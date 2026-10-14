from functools import partial

from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, DetailView, FormView, ListView, TemplateView, UpdateView

from apps.core.exceptions import DomainError
from apps.core.money import ZERO
from apps.core.params import choice_param, int_param, text_param
from apps.core.views import flash_domain_error
from apps.audit import services as audit
from apps.audit.domain import AuditAction
from apps.tenants.mixins import ManageMixin, OrganizationMixin, WriteMixin

from . import coretax, coretax_sync, services
from .coretax_api import ConnectorName
from .forms import CoretaxConnectionForm, InvoiceForm, InvoiceLineFormSet, ItemForm, PaymentForm, ReturnForm, TaxInvoiceNumberForm, VoidForm, line_inputs
from .models import CoretaxConnection, CoretaxSubmission, Invoice, InvoiceKind, InvoiceStatusFilter
from .selectors import invoice_detail, invoices_for, items_for, outstanding_summary, payments_for, returns_for

# Complete phrases rather than fragments, so each language can word them naturally.
KIND_LABELS = {
    InvoiceKind.SALES: {
        "title": _("Sales invoice"),
        "plural": _("Sales invoices"),
        "contact": _("Customer"),
        "new": _("New sales invoice"),
        "edit": _("Edit draft sales invoice"),
        "create": _("New sale"),
        "outstanding": _("Unpaid receivables"),
        "aging": _("View receivables aging report"),
    },
    InvoiceKind.PURCHASE: {
        "title": _("Purchase bill"),
        "plural": _("Purchase bills"),
        "contact": _("Supplier"),
        "new": _("New purchase bill"),
        "edit": _("Edit draft purchase bill"),
        "create": _("New purchase"),
        "outstanding": _("Unpaid payables"),
        "aging": _("View payables aging report"),
    },
}


class InvoiceKindMixin:
    """Resolve the ``kind`` URL segment and expose labels to templates."""

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.kind = kwargs["kind"]

    def get_context_data(self, **kwargs):
        return super().get_context_data(kind=self.kind, labels=KIND_LABELS[self.kind], **kwargs)

    def invoice_queryset(self):
        return Invoice.objects.for_organization(self.organization).filter(kind=self.kind)


class InvoiceListView(InvoiceKindMixin, OrganizationMixin, ListView):
    template_name = "invoicing/invoice_list.html"
    paginate_by = 25

    def get_queryset(self):
        params = self.request.GET
        return invoices_for(
            self.organization, self.kind,
            query=text_param(params, "q"),
            status=choice_param(params, "status", InvoiceStatusFilter.values),
        )

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            statuses=InvoiceStatusFilter.choices, summary=outstanding_summary(self.organization, self.kind), **kwargs
        )


class InvoiceFormView(InvoiceKindMixin, WriteMixin, TemplateView):
    template_name = "invoicing/invoice_form.html"

    def get_invoice(self):
        return None

    def build_forms(self, invoice, data=None):
        initial_lines = None
        if invoice is not None and data is None:
            initial_lines = [
                {
                    "item": l.item, "description": l.description, "account": l.account, "quantity": l.quantity,
                    "unit_price": l.unit_price, "discount_percent": l.discount_percent, "tax_rate": l.tax_rate,
                }
                for l in invoice.lines.all()
            ]
        form = InvoiceForm(
            data, instance=invoice, kind=self.kind, organization=self.organization,
            initial=None if invoice else {"date": timezone.localdate(), "contact": int_param(self.request.GET, "contact", minimum=1)},
        )
        formset = InvoiceLineFormSet(
            data, initial=initial_lines, form_kwargs={"organization": self.organization, "kind": self.kind}
        )
        return form, formset

    def render_forms(self, invoice, form, formset):
        items = {
            item.pk: {
                "description": item.name,
                "price": str(item.sale_price if self.kind == InvoiceKind.SALES else item.purchase_price),
                "account": (
                    item.income_account_id if self.kind == InvoiceKind.SALES
                    else item.inventory_account_id if item.track_inventory else item.expense_account_id
                ),
                "tax": item.tax_rate_id,
            }
            for item in items_for(self.organization, active_only=True)
        }
        return self.render_to_response(
            self.get_context_data(form=form, formset=formset, invoice=invoice, items=items)
        )

    def get(self, request, *args, **kwargs):
        invoice = self.get_invoice()
        return self.render_forms(invoice, *self.build_forms(invoice))

    def post(self, request, *args, **kwargs):
        invoice = self.get_invoice()
        form, formset = self.build_forms(invoice, request.POST)
        if form.is_valid() and formset.is_valid():
            issue = "issue" in request.POST
            try:
                with transaction.atomic():  # saving and issuing succeed or fail together
                    saved = self.save(invoice, form.cleaned_data, line_inputs(formset))
                    if issue:
                        saved = services.issue_invoice(invoice=saved, issued_by=request.user)
                if issue:
                    messages.success(
                        request,
                        _("%(document)s %(number)s issued.") % {"document": KIND_LABELS[self.kind]["title"], "number": saved.number},
                    )
                else:
                    messages.success(request, _("Draft saved."))
                return redirect("invoicing:detail", kind=self.kind, pk=saved.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return self.render_forms(invoice, form, formset)


class InvoiceCreateView(InvoiceFormView):
    def save(self, invoice, header, lines):
        return services.create_invoice(
            organization=self.organization, kind=self.kind, lines=lines, created_by=self.request.user, **header
        )


class InvoiceUpdateView(InvoiceFormView):
    def get_invoice(self):
        return get_object_or_404(self.invoice_queryset(), pk=self.kwargs["pk"], status=Invoice.Status.DRAFT)

    def save(self, invoice, header, lines):
        return services.update_draft_invoice(invoice=invoice, lines=lines, **header)


class InvoiceDetailView(InvoiceKindMixin, OrganizationMixin, DetailView):
    template_name = "invoicing/invoice_detail.html"
    context_object_name = "invoice"

    def get_object(self, queryset=None):
        return get_object_or_404(invoice_detail(self.organization, self.kwargs["pk"]).filter(kind=self.kind))

    def get_context_data(self, **kwargs):
        invoice = self.object
        today = timezone.localdate()
        returns = list(invoice.returns.all())
        show_coretax = invoice.is_sales and invoice.organization.is_pkp and invoice.status in (
            Invoice.Status.ISSUED, Invoice.Status.PARTIAL, Invoice.Status.PAID,
        )
        return super().get_context_data(
            coretax_problem=coretax.invoice_problem(invoice) if show_coretax else None,
            show_coretax=show_coretax,
            coretax_connected=show_coretax and CoretaxConnection.objects.filter(organization=self.organization, is_active=True).exists(),
            coretax_submission=invoice.coretax_submissions.first() if show_coretax else None,
            payment_form=PaymentForm(
                organization=self.organization, invoice=invoice,
                initial={
                    "date": max(today, invoice.date), "amount": invoice.balance_due,
                    "vat_withheld": max(min(invoice.vat_still_withheld, invoice.balance_due), ZERO),
                },
            ),
            void_form=VoidForm(initial={"date": today}),
            returns=returns,
            has_posted_returns=any(r.status == r.Status.POSTED for r in returns),
            **kwargs,
        )


class InvoicePrintView(InvoiceDetailView):
    """The document sent to the customer/supplier, in *their* language, not the viewer's."""

    template_name = "invoicing/invoice_print.html"

    def render_to_response(self, context, **response_kwargs):
        response = super().render_to_response(context, **response_kwargs)
        language = self.object.contact.document_language
        with translation.override(language):
            response.render()
        response.headers["Content-Language"] = language
        return response


class InvoiceActionView(InvoiceKindMixin, WriteMixin, View):
    def post(self, request, kind, pk):
        invoice = get_object_or_404(self.invoice_queryset(), pk=pk)
        try:
            return redirect(self.act(invoice))
        except DomainError as exc:
            flash_domain_error(request, exc)
            return redirect("invoicing:detail", kind=self.kind, pk=invoice.pk)

    def detail_url(self, invoice):
        return reverse("invoicing:detail", kwargs={"kind": self.kind, "pk": invoice.pk})


class InvoiceIssueView(InvoiceActionView):
    def act(self, invoice):
        invoice = services.issue_invoice(invoice=invoice, issued_by=self.request.user)
        messages.success(self.request, _("Invoice %(number)s issued and posted to the ledger.") % {"number": invoice.number})
        return self.detail_url(invoice)


class InvoiceVoidView(InvoiceActionView):
    def act(self, invoice):
        form = VoidForm(self.request.POST)
        on = form.cleaned_data["date"] if form.is_valid() else timezone.localdate()
        services.void_invoice(invoice=invoice, on=on, voided_by=self.request.user)
        messages.success(self.request, _("Invoice %(number)s voided; its journal entry was reversed.") % {"number": invoice.number})
        return self.detail_url(invoice)


class InvoiceDeleteView(InvoiceActionView):
    def act(self, invoice):
        services.delete_draft_invoice(invoice=invoice)
        messages.success(self.request, _("Draft deleted."))
        return reverse("invoicing:list", kwargs={"kind": self.kind})


class PaymentCreateView(InvoiceActionView):
    def act(self, invoice):
        form = PaymentForm(self.request.POST, organization=self.organization, invoice=invoice)
        if not form.is_valid():
            errors = "; ".join(e for errs in form.errors.values() for e in errs)
            raise DomainError(_("Invalid payment data: %(errors)s") % {"errors": errors})
        payment = services.record_payment(invoice=invoice, created_by=self.request.user, **form.service_kwargs())
        messages.success(self.request, _("Payment %(number)s recorded.") % {"number": payment.number})
        return self.detail_url(invoice)


# --- Payments -----------------------------------------------------------------


class PaymentListView(OrganizationMixin, ListView):
    template_name = "invoicing/payment_list.html"
    paginate_by = 25

    def get_queryset(self):
        params = self.request.GET
        return payments_for(
            self.organization, kind=choice_param(params, "kind", InvoiceKind.values), query=text_param(params, "q")
        )

    def get_context_data(self, **kwargs):
        return super().get_context_data(kinds=[(kind, kind.payment_label) for kind in InvoiceKind], **kwargs)


class PaymentVoidView(WriteMixin, View):
    def post(self, request, pk):
        payment = get_object_or_404(payments_for(self.organization), pk=pk)
        try:
            services.void_payment(payment=payment, on=timezone.localdate(), voided_by=request.user)
            messages.success(request, _("Payment %(number)s voided.") % {"number": payment.number})
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect("invoicing:detail", kind=payment.invoice.kind, pk=payment.invoice_id)


class WithholdingSlipView(WriteMixin, View):
    def post(self, request, pk):
        payment = get_object_or_404(payments_for(self.organization), pk=pk)
        try:
            services.record_withholding_slip(payment=payment, number=text_param(request.POST, "number"))
            messages.success(request, _("Bukti potong number recorded."))
        except DomainError as exc:
            flash_domain_error(request, exc)
        if choice_param(request.POST, "next", ["withholding"]):
            return redirect("taxes:withholding_month", payment.date.year, payment.date.month)
        return redirect("invoicing:detail", kind=payment.invoice.kind, pk=payment.invoice_id)


# --- Items --------------------------------------------------------------------


class ItemListView(OrganizationMixin, ListView):
    template_name = "invoicing/item_list.html"
    paginate_by = 25

    def get_queryset(self):
        return items_for(self.organization, query=text_param(self.request.GET, "q")).select_related("tax_rate")


class ItemFormMixin(WriteMixin):
    form_class = ItemForm
    template_name = "invoicing/item_form.html"
    success_url = reverse_lazy("invoicing:items")

    def get_queryset(self):
        return items_for(self.organization)

    def form_valid(self, form):
        form.instance.organization = self.organization
        messages.success(self.request, _("Item saved."))
        return super().form_valid(form)


class ItemCreateView(ItemFormMixin, CreateView):
    pass


class ItemUpdateView(ItemFormMixin, UpdateView):
    pass




class ReturnCreateView(InvoiceKindMixin, WriteMixin, FormView):
    """Record a sales return or purchase return (nota retur) of an issued invoice."""

    template_name = "invoicing/return_form.html"
    form_class = ReturnForm

    def dispatch(self, request, *args, **kwargs):
        self.invoice = None
        return super().dispatch(request, *args, **kwargs)

    def get_invoice(self):
        if self.invoice is None:
            self.invoice = get_object_or_404(invoice_detail(self.organization, self.kwargs["pk"]).filter(kind=self.kind))
        return self.invoice

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "organization": self.organization, "invoice": self.get_invoice()}

    def get_initial(self):
        return {"date": max(timezone.localdate(), self.get_invoice().date)}

    def get_context_data(self, **kwargs):
        return super().get_context_data(invoice=self.get_invoice(), **kwargs)

    def form_valid(self, form):
        try:
            note = services.create_return(invoice=self.get_invoice(), created_by=self.request.user, **form.to_input())
        except DomainError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, _("Return %(number)s recorded and posted to the ledger.") % {"number": note.number})
        return redirect("invoicing:return_detail", note.pk)


class ReturnDetailView(OrganizationMixin, DetailView):
    template_name = "invoicing/return_detail.html"
    context_object_name = "note"

    def get_queryset(self):
        return returns_for(self.organization).prefetch_related("lines__invoice_line")

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            void_form=VoidForm(initial={"date": timezone.localdate()}), coretax_problem=coretax.exportable_problem(self.object), **kwargs
        )


class ReturnVoidView(WriteMixin, View):
    def post(self, request, pk):
        note = get_object_or_404(returns_for(self.organization), pk=pk)
        form = VoidForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Invalid void date."))
        else:
            try:
                services.void_return(note=note, on=form.cleaned_data["date"], voided_by=request.user)
                messages.success(request, _("Return voided: its journal entry and stock were reversed."))
            except DomainError as exc:
                flash_domain_error(request, exc)
        return redirect("invoicing:return_detail", note.pk)


def _xml_download(content: bytes, name: str) -> HttpResponse:
    response = HttpResponse(content, content_type="application/xml; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


class ReturnCoretaxView(OrganizationMixin, View):
    """A purchase return as a Coretax "Retur Pajak Masukan" import file."""

    def get(self, request, pk):
        note = get_object_or_404(returns_for(self.organization), pk=pk)
        problem = coretax.exportable_problem(note)
        if problem:
            messages.error(request, problem)
            return redirect("invoicing:return_detail", note.pk)
        return _xml_download(coretax.build_xml(self.organization, [note]), coretax.filename(self.organization, note.number.replace("/", "-")))


class MonthCoretaxView(OrganizationMixin, View):
    """A month's purchase returns in one Coretax import file; returns that can't be reported are left out."""

    def get(self, request, year, month):
        if not 1 <= month <= 12:
            raise Http404
        notes = [n for n in coretax.month_returns(self.organization, year, month) if not coretax.exportable_problem(n)]
        if not notes:
            messages.info(request, _("No purchase return of %(period)s can be reported to Coretax.") % {"period": f"{month:02d}/{year}"})
            return redirect(f"{reverse('reports:vat')}?year={year}")
        return _xml_download(coretax.build_xml(self.organization, notes), coretax.filename(self.organization, f"{year}-{month:02d}"))


class InvoiceCoretaxView(InvoiceKindMixin, OrganizationMixin, View):
    """A sales invoice as a Coretax "Faktur Pajak Keluaran" import file."""

    def get(self, request, kind, pk):
        invoice = get_object_or_404(invoice_detail(self.organization, pk).filter(kind=self.kind))
        problem = coretax.invoice_problem(invoice)
        if problem:
            messages.error(request, problem)
            return redirect("invoicing:detail", self.kind, invoice.pk)
        name = coretax.filename(self.organization, invoice.number.replace("/", "-"), kind="faktur-pajak-keluaran")
        return _xml_download(coretax.build_invoices_xml(self.organization, [invoice]), name)


class MonthInvoicesCoretaxView(OrganizationMixin, View):
    """A month's sales invoices in one Coretax import file; invoices that can't be reported are left out."""

    def get(self, request, year, month):
        if not 1 <= month <= 12:
            raise Http404
        invoices = [i for i in coretax.month_invoices(self.organization, year, month) if not coretax.invoice_problem(i)]
        if not invoices:
            messages.info(request, _("No sales invoice of %(period)s can be reported to Coretax.") % {"period": f"{month:02d}/{year}"})
            return redirect(f"{reverse('reports:vat')}?year={year}")
        name = coretax.filename(self.organization, f"{year}-{month:02d}", kind="faktur-pajak-keluaran")
        return _xml_download(coretax.build_invoices_xml(self.organization, invoices), name)


class MonthRetailCoretaxView(OrganizationMixin, View):
    """A retailer's sales to anonymous consumers of a month, for the SPT Masa PPN (digunggung:
    1.A5, 1.A9 and 1.B), in DJP's retail import format."""

    def get(self, request, year, month):
        if not 1 <= month <= 12:
            raise Http404
        rows, _returns = coretax.month_retail(self.organization, year, month)
        if not rows or len(coretax.tin(self.organization.npwp)) != 16:
            messages.info(request, _("There are no retail sales of %(period)s to report in total, or the organization has no NPWP.")
                          % {"period": f"{month:02d}/{year}"})
            return redirect(f"{reverse('reports:vat')}?year={year}")
        name = coretax.filename(self.organization, f"{year}-{month:02d}", kind="ppn-digunggung")
        return _xml_download(coretax.build_retail_xml(self.organization, year, month, rows), name)


class TaxInvoiceNumberView(InvoiceKindMixin, WriteMixin, View):
    def post(self, request, kind, pk):
        invoice = get_object_or_404(invoice_detail(self.organization, pk).filter(kind=self.kind))
        form = TaxInvoiceNumberForm(request.POST)
        if form.is_valid():
            try:
                services.record_tax_invoice_number(invoice=invoice, number=form.cleaned_data["number"])
                messages.success(request, _("Faktur Pajak number recorded."))
            except DomainError as exc:
                flash_domain_error(request, exc)
        else:
            messages.error(request, _("Enter the Faktur Pajak number."))
        return redirect("invoicing:detail", self.kind, invoice.pk)


# --- Coretax connection (PJAP) -------------------------------------------------------


class CoretaxConnectionView(ManageMixin, FormView):
    template_name = "invoicing/coretax_connection.html"
    form_class = CoretaxConnectionForm

    def get_form_kwargs(self):
        return {key: value for key, value in super().get_form_kwargs().items() if key != "organization"}

    def get_initial(self):
        connection = CoretaxConnection.objects.filter(organization=self.organization).first()
        if connection is None:
            return {"connector": ConnectorName.HTTP, "is_active": True}
        return {"connector": connection.connector, "api_url": connection.api_url, "is_active": connection.is_active}

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            connection=CoretaxConnection.objects.filter(organization=self.organization).first(),
            submissions=CoretaxSubmission.objects.filter(organization=self.organization)[:30], **kwargs,
        )

    def form_valid(self, form):
        try:
            coretax_sync.save_connection(organization=self.organization, **form.cleaned_data)
        except DomainError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        audit.record(AuditAction.CORETAX_CONNECTION_CHANGED, request=self.request, organization=self.organization,
                     connector=form.cleaned_data["connector"], active=form.cleaned_data["is_active"])
        messages.success(self.request, _("Coretax connection saved."))
        return redirect("invoicing:coretax_connection")


class CoretaxSendView(WriteMixin, View):
    """Send one invoice, or a month's invoices or purchase returns, through the connection."""

    def post(self, request, **kwargs):
        organization = self.organization
        if "pk" in kwargs:
            invoice = get_object_or_404(invoice_detail(organization, kwargs["pk"]).filter(kind=InvoiceKind.SALES))
            target = reverse("invoicing:detail", args=[InvoiceKind.SALES, invoice.pk])
            send = partial(coretax_sync.submit_invoices, organization=organization, invoices=[invoice], label=invoice.number)
        else:
            year, month = kwargs["year"], kwargs["month"]
            if not 1 <= month <= 12:
                raise Http404
            target, label = f"{reverse('reports:vat')}?year={year}", f"{year}-{month:02d}"
            if kwargs.get("returns"):
                send = partial(coretax_sync.submit_returns, organization=organization, notes=coretax.month_returns(organization, year, month), label=label)
            else:
                send = partial(coretax_sync.submit_invoices, organization=organization, invoices=coretax.month_invoices(organization, year, month), label=label)
        try:
            submission = send(created_by=request.user)
        except DomainError as exc:
            flash_domain_error(request, exc)
            return redirect(target)
        messages.success(request, _("Sent to Coretax: %(status)s.") % {"status": submission.get_status_display()})
        return redirect(target)


class CoretaxRefreshView(WriteMixin, View):
    def post(self, request, pk):
        submission = get_object_or_404(CoretaxSubmission, organization=self.organization, pk=pk)
        try:
            coretax_sync.refresh_submission(submission)
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect("invoicing:coretax_connection")
