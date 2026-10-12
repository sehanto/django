from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from apps.core.exceptions import DomainError
from apps.core.views import flash_domain_error
from apps.tenants.mixins import OrganizationMixin, WriteMixin

from . import services
from .domain import AccountCategory
from .forms import AccountForm, JournalEntryForm, JournalLineFormSet, ReverseForm, line_inputs
from .models import JournalEntry
from .selectors import account_balances, accounts_for, journal_entries_for, search_journal_entries


# --- Chart of accounts -------------------------------------------------------


class AccountListView(OrganizationMixin, TemplateView):
    template_name = "ledger/account_list.html"

    def get_context_data(self, **kwargs):
        balances = {b.account.pk: b.balance for b in account_balances(self.organization)}
        groups = []
        accounts = list(accounts_for(self.organization))
        for category in AccountCategory:
            rows = [(a, balances.get(a.pk)) for a in accounts if a.category == category]
            if rows:
                groups.append((category.label, rows))
        return super().get_context_data(groups=groups, **kwargs)


class AccountFormMixin(WriteMixin):
    form_class = AccountForm
    template_name = "ledger/account_form.html"
    success_url = reverse_lazy("ledger:accounts")

    def get_queryset(self):
        return accounts_for(self.organization)

    def form_valid(self, form):
        try:
            self.save(form.cleaned_data)
        except DomainError as exc:
            form.add_error(None, exc.message)
            return self.form_invalid(form)
        messages.success(self.request, _("Account saved."))
        return redirect(self.success_url)


class AccountCreateView(AccountFormMixin, CreateView):
    def save(self, data):
        services.create_account(organization=self.organization, **data)


class AccountUpdateView(AccountFormMixin, UpdateView):
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # Work on a fresh copy so a rejected change is not reflected in the instance.
        kwargs["instance"] = self.get_queryset().get(pk=self.object.pk)
        return kwargs

    def save(self, data):
        services.update_account(account=self.object, **data)


class AccountDeleteView(WriteMixin, View):
    def post(self, request, pk):
        account = get_object_or_404(accounts_for(self.organization), pk=pk)
        try:
            services.delete_account(account=account)
            messages.success(request, _("Account deleted."))
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect("ledger:accounts")


# --- Journal entries ---------------------------------------------------------


class JournalListView(OrganizationMixin, ListView):
    template_name = "ledger/journal_list.html"
    paginate_by = 25

    def get_queryset(self):
        params = self.request.GET
        return search_journal_entries(
            self.organization,
            query=params.get("q", ""),
            status=params.get("status", ""),
            source=params.get("source", ""),
        )

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            statuses=JournalEntry.Status.choices, sources=JournalEntry.Source.choices, **kwargs
        )


class JournalDetailView(OrganizationMixin, DetailView):
    template_name = "ledger/journal_detail.html"
    context_object_name = "entry"

    def get_queryset(self):
        return journal_entries_for(self.organization).select_related("reversal_of", "created_by")

    def get_context_data(self, **kwargs):
        return super().get_context_data(reverse_form=ReverseForm(initial={"date": timezone.localdate()}), **kwargs)


class JournalFormView(WriteMixin, TemplateView):
    """Shared create/edit screen for manual journal entries (header + lines)."""

    template_name = "ledger/journal_form.html"

    def get_entry(self):
        return None

    def build_forms(self, entry, data=None):
        initial_lines = None
        if entry is not None and data is None:
            initial_lines = [
                {"account": l.account, "description": l.description, "debit": l.debit or None, "credit": l.credit or None}
                for l in entry.lines.all()
            ]
        form = JournalEntryForm(
            data, instance=entry, organization=self.organization,
            initial=None if entry else {"date": timezone.localdate()},
        )
        formset = JournalLineFormSet(data, initial=initial_lines, form_kwargs={"organization": self.organization})
        return form, formset

    def get(self, request, *args, **kwargs):
        entry = self.get_entry()
        form, formset = self.build_forms(entry)
        return self.render_to_response(self.get_context_data(form=form, formset=formset, entry=entry))

    def post(self, request, *args, **kwargs):
        entry = self.get_entry()
        form, formset = self.build_forms(entry, request.POST)
        if form.is_valid() and formset.is_valid():
            try:
                saved = self.save(entry, form.cleaned_data, line_inputs(formset), post="post" in request.POST)
                messages.success(request, _("Journal entry %(number)s saved.") % {"number": saved.number})
                return redirect("ledger:journal_detail", pk=saved.pk)
            except DomainError as exc:
                form.add_error(None, exc.message)
        return self.render_to_response(self.get_context_data(form=form, formset=formset, entry=entry))


class JournalCreateView(JournalFormView):
    def save(self, entry, header, lines, post):
        return services.create_journal_entry(
            organization=self.organization, lines=lines, created_by=self.request.user, post=post, **header
        )


class JournalUpdateView(JournalFormView):
    def get_entry(self):
        return get_object_or_404(
            journal_entries_for(self.organization),
            pk=self.kwargs["pk"], status=JournalEntry.Status.DRAFT, source=JournalEntry.Source.MANUAL,
        )

    def save(self, entry, header, lines, post):
        entry = services.update_draft_journal_entry(entry=entry, lines=lines, **header)
        if post:
            entry = services.post_journal_entry(entry=entry)
        return entry


class JournalActionView(WriteMixin, View):
    """POST-only state transitions on a journal entry."""

    def post(self, request, pk):
        entry = get_object_or_404(journal_entries_for(self.organization), pk=pk)
        try:
            target = self.act(entry)
        except DomainError as exc:
            flash_domain_error(request, exc)
            return redirect("ledger:journal_detail", pk=entry.pk)
        return redirect(target)


class JournalPostView(JournalActionView):
    def act(self, entry):
        services.post_journal_entry(entry=entry)
        messages.success(self.request, _("Journal entry %(number)s posted.") % {"number": entry.number})
        return reverse("ledger:journal_detail", args=[entry.pk])


class JournalDeleteView(JournalActionView):
    def act(self, entry):
        services.delete_draft_journal_entry(entry=entry)
        messages.success(self.request, _("Draft journal entry deleted."))
        return reverse("ledger:journals")


class JournalReverseView(JournalActionView):
    def act(self, entry):
        form = ReverseForm(self.request.POST)
        if not form.is_valid():
            raise DomainError(_("Invalid reversal date."))
        reversal = services.reverse_manual_journal_entry(
            entry=entry, date=form.cleaned_data["date"], created_by=self.request.user
        )
        messages.success(self.request, _("Journal entry reversed by %(number)s.") % {"number": reversal.number})
        return reverse("ledger:journal_detail", args=[reversal.pk])
