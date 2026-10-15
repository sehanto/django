from datetime import date
from decimal import Decimal

import pytest

from apps.core.exceptions import InvalidStateTransition, PeriodLocked, ValidationFailed
from apps.ledger import services
from apps.ledger.chart_template import chart_for
from apps.ledger.domain import AccountCategory, SystemRole
from apps.ledger.models import Account, JournalEntry
from apps.ledger.selectors import account_balance, account_ledger
from apps.ledger.services import JournalLineInput

D = Decimal


def capital_injection(organization, account, amount=D("10000000"), on=date(2026, 1, 2), post=True):
    return services.create_journal_entry(
        organization=organization,
        date=on,
        description="Setoran modal awal",
        lines=[JournalLineInput(account("1-1100"), debit=amount), JournalLineInput(account("3-1000"), credit=amount)],
        post=post,
    )


def test_onboarding_seeds_full_chart_with_system_roles(organization):
    accounts = Account.objects.for_organization(organization)
    assert accounts.count() == len(chart_for("perorangan"))
    assert set(accounts.exclude(system_role=None).values_list("system_role", flat=True)) == set(SystemRole.values)


def test_posted_entry_updates_balances(organization, account):
    entry = capital_injection(organization, account)
    assert entry.number == "JU/2026/01/0001"
    assert entry.is_posted
    assert account_balance(organization, account("1-1100")) == D("10000000")
    assert account_balance(organization, account("3-1000")) == D("10000000")


def test_draft_does_not_affect_balances_until_posted(organization, account):
    entry = capital_injection(organization, account, post=False)
    assert account_balance(organization, account("1-1100")) == 0
    services.post_journal_entry(entry=entry)
    assert account_balance(organization, account("1-1100")) == D("10000000")
    with pytest.raises(InvalidStateTransition):
        services.post_journal_entry(entry=entry)


def test_unbalanced_entry_is_rejected(organization, account):
    with pytest.raises(ValidationFailed):
        services.create_journal_entry(
            organization=organization,
            date=date(2026, 1, 2),
            description="Salah",
            lines=[JournalLineInput(account("1-1000"), debit=D("100")), JournalLineInput(account("3-1000"), credit=D("99"))],
        )
    assert not JournalEntry.objects.exists()


def test_accounts_from_another_tenant_are_rejected(organization, other_organization, account):
    foreign = Account.objects.get(organization=other_organization, code="3-1000")
    with pytest.raises(ValidationFailed):
        services.create_journal_entry(
            organization=organization,
            date=date(2026, 1, 2),
            description="Lintas tenant",
            lines=[JournalLineInput(account("1-1000"), debit=D("100")), JournalLineInput(foreign, credit=D("100"))],
        )


def test_locked_period_rejects_posting(organization, account):
    organization.lock_date = date(2026, 1, 31)
    organization.save()
    with pytest.raises(PeriodLocked):
        capital_injection(organization, account, on=date(2026, 1, 15))
    capital_injection(organization, account, on=date(2026, 2, 1))


def test_reversal_nets_to_zero_and_cannot_repeat(organization, account, user):
    entry = capital_injection(organization, account)
    reversal = services.reverse_manual_journal_entry(entry=entry, date=date(2026, 1, 5), created_by=user)
    assert reversal.reversal_of == entry
    assert reversal.source == JournalEntry.Source.REVERSAL
    assert account_balance(organization, account("1-1100")) == 0
    with pytest.raises(InvalidStateTransition):
        services.reverse_journal_entry(entry=entry, date=date(2026, 1, 6))
    with pytest.raises(InvalidStateTransition):
        services.reverse_journal_entry(entry=reversal, date=date(2026, 1, 6))


def test_draft_can_be_edited_and_deleted_but_posted_cannot(organization, account):
    draft = capital_injection(organization, account, post=False)
    services.update_draft_journal_entry(
        entry=draft, date=date(2026, 1, 3), description="Revisi", reference="",
        lines=[JournalLineInput(account("1-1000"), debit=D("5")), JournalLineInput(account("3-1000"), credit=D("5"))],
    )
    assert draft.lines.count() == 2 and draft.description == "Revisi"
    services.delete_draft_journal_entry(entry=draft)

    posted = capital_injection(organization, account)
    with pytest.raises(InvalidStateTransition):
        services.delete_draft_journal_entry(entry=posted)


def test_account_rules(organization, account):
    capital_injection(organization, account)
    with pytest.raises(ValidationFailed):
        services.delete_account(account=account("1-1100"))  # has postings
    with pytest.raises(ValidationFailed):
        services.delete_account(account=account("1-1200"))  # system account
    with pytest.raises(ValidationFailed):
        services.update_account(account=account("1-1100"), category=AccountCategory.INVENTORY)
    with pytest.raises(ValidationFailed):
        services.create_account(organization=organization, code="1-1000", name="Duplikat", category=AccountCategory.CASH_BANK)
    new = services.create_account(organization=organization, code="1-1010", name="Kas Kecil", category=AccountCategory.CASH_BANK)
    assert new.is_cash
    services.delete_account(account=new)


def test_ledger_running_balance(organization, account):
    capital_injection(organization, account, amount=D("1000"), on=date(2025, 12, 31))
    capital_injection(organization, account, amount=D("500"), on=date(2026, 1, 10))
    ledger = account_ledger(organization, account("1-1100"), date_from=date(2026, 1, 1), date_to=date(2026, 1, 31))
    assert ledger.opening_balance == D("1000")
    assert [r.balance for r in ledger.rows] == [D("1500")]
    assert ledger.closing_balance == D("1500")
