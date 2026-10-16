from enum import StrEnum

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import TemplateView

from apps.core.exceptions import DomainError
from apps.core.params import choice_param, int_param
from apps.core.views import flash_domain_error
from apps.tenants.mixins import OrganizationMixin, WriteMixin

from . import services
from .domain import StatementLineStatus
from .forms import AsOfForm, PostLineForm
from .models import StatementLine
from .selectors import (
    bank_accounts,
    candidates,
    journal_lines_for,
    reconciliation,
    statement_lines,
    unreconciled_counts,
)

SUGGESTIONS = 3


class LineAction(StrEnum):
    MATCH = "match"
    POST = "post"
    IGNORE = "ignore"
    UNDO = "undo"


LineAction.do_not_call_in_templates = True  # templates read its members (an Enum class is callable)


class BankAccountListView(OrganizationMixin, TemplateView):
    template_name = "banking/account_list.html"

    def get_context_data(self, **kwargs):
        counts = unreconciled_counts(self.organization)
        accounts = [(account, counts.get(account.pk, 0)) for account in bank_accounts(self.organization)]
        return super().get_context_data(accounts=accounts, **kwargs)


class ReconciliationView(OrganizationMixin, TemplateView):
    template_name = "banking/reconciliation.html"
    paginate_by = 100

    def get_context_data(self, **kwargs):
        account = get_object_or_404(bank_accounts(self.organization), pk=self.kwargs["pk"])
        status = choice_param(self.request.GET, "status", StatementLineStatus.values, default=StatementLineStatus.UNMATCHED)
        form = AsOfForm(self.request.GET or None)
        as_of = (form.cleaned_data.get("as_of") if form.is_valid() else None) or timezone.localdate()
        lines = list(statement_lines(self.organization, account, status=status).order_by("-date", "-id")[: self.paginate_by])
        rows = [(line, list(candidates(line)[:SUGGESTIONS]) if line.status == StatementLineStatus.UNMATCHED else []) for line in lines]
        return super().get_context_data(
            account=account, rows=rows, status=status, statuses=StatementLineStatus, as_of_form=form, as_of=as_of,
            summary=reconciliation(self.organization, account, as_of), post_form=PostLineForm(organization=self.organization),
            actions=LineAction, **kwargs,
        )


class StatementLineActionView(WriteMixin, View):
    def post(self, request, pk, action):
        if action not in LineAction:
            raise Http404
        line = get_object_or_404(StatementLine.objects.for_organization(self.organization), pk=pk)
        back = f"{reverse('banking:reconciliation', args=[line.account_id])}?status={StatementLineStatus.UNMATCHED}"
        try:
            if action == LineAction.MATCH:
                journal_line = journal_lines_for(self.organization).filter(pk=int_param(request.POST, "journal_line", default=0)).first()
                if journal_line is None:
                    raise Http404
                services.match_line(line=line, journal_line=journal_line, matched_by=request.user)
                messages.success(request, _("Statement line reconciled."))
            elif action == LineAction.POST:
                form = PostLineForm(request.POST, organization=self.organization)
                if not form.is_valid():
                    messages.error(request, _("Choose the other account of the entry."))
                    return redirect(back)
                services.post_and_match(line=line, counter_account=form.cleaned_data["counter_account"],
                                        description=form.cleaned_data["description"], created_by=request.user)
                messages.success(request, _("Entry posted and reconciled."))
            elif action == LineAction.IGNORE:
                services.ignore_line(line=line, ignored_by=request.user)
                messages.success(request, _("Statement line set aside."))
            else:
                services.undo_reconciliation(line=line)
                messages.success(request, _("Reconciliation undone."))
        except DomainError as exc:
            flash_domain_error(request, exc)
        return redirect(back)
