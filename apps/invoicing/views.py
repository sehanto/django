from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone, translation
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from apps.core.exceptions import DomainError
from apps.core.params import choice_param, int_param, text_param
from apps.core.views import flash_domain_error
from apps.tenants.mixins import OrganizationMixin, WriteMixin

from . import services
from .forms import InvoiceForm, InvoiceLineFormSet, ItemForm, PaymentForm, VoidForm, line_inputs
from .models import Invoice, InvoiceKind, InvoiceStatusFilter
from .selectors import invoice_detail, invoices_for, items_for, outstanding_summary, payments_for

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
        return super().get_context_data(
            payment_form=PaymentForm(
                organization=self.organization, initial={"date": max(today, invoice.date), "amount": invoice.balance_due}
            ),
            void_form=VoidForm(initial={"date": today}),
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
        form = PaymentForm(self.request.POST, organization=self.organization)
        if not form.is_valid():
            errors = "; ".join(e for errs in form.errors.values() for e in errs)
            raise DomainError(_("Invalid payment data: %(errors)s") % {"errors": errors})
        payment = services.record_payment(invoice=invoice, created_by=self.request.user, **form.cleaned_data)
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


