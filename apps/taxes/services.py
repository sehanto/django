from datetime import date

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from decimal import Decimal

from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.core.formatting import localized_rupiah
from apps.core.money import ZERO, round_down_rupiah
from apps.core.validators import ensure_business_date
from apps.core.periods import month_end
from apps.ledger.domain import AccountCategory, SystemRole
from apps.ledger.models import JournalEntry
from apps.ledger.services import JournalLineInput, create_journal_entry, get_system_account, reverse_journal_entry

from .domain import PAYER_WITHHOLDING_NAMES, Month, PayerWithholding
from . import tax_objects
from .models import ImportDeclaration, PphFinalAccrual, PphFinalPayment, SelfDeposit, TaxRate, WithholdingDeposit
from .selectors import pph_final_schedule, pph_final_withheld, withholding_due

DEFAULT_TAX_RATES = [
    # (name, rate, dpp_numerator, dpp_denominator)
    ("PPN 12% (DPP 11/12) - Umum", 12, 11, 12),
    ("PPN 12% - Barang Mewah", 12, 1, 1),
]


def seed_tax_rates(organization) -> None:
    TaxRate.objects.bulk_create(
        [
            TaxRate(organization=organization, name=name, rate=rate, dpp_numerator=num, dpp_denominator=den)
            for name, rate, num, den in DEFAULT_TAX_RATES
        ]
    )


def _period(month: Month, year: int) -> dict:
    return {"period": f"{month.label} {year}"}


def _ensure_uses_pph_final(organization) -> None:
    if not organization.uses_pph_final:
        raise ValidationFailed(_("This organization does not use the MSME PPh Final scheme."))


def posted_accrual(organization, year: int, month: Month) -> PphFinalAccrual | None:
    return (
        PphFinalAccrual.objects.for_organization(organization).select_for_update()
        .filter(year=year, month=month, status=PphFinalAccrual.Status.POSTED).first()
    )


@transaction.atomic
def accrue_pph_final(*, organization, year: int, month: Month, created_by=None) -> PphFinalAccrual:
    """Recognise the month's PPh Final as an expense on its last day (accrual basis, SAK EMKM):
    Beban PPh Final against Utang PPh Final, which the deposit settles."""
    month = Month(month)
    _ensure_uses_pph_final(organization)
    if month_end(year, month) > timezone.localdate():
        raise ValidationFailed(_("%(period)s has not ended yet. Record its PPh Final expense from the last day of the month.") % _period(month, year))
    if posted_accrual(organization, year, month):
        raise ValidationFailed(_("The PPh Final expense for %(period)s has already been recorded.") % _period(month, year))
    if PphFinalPayment.objects.for_organization(organization).filter(year=year, month=month, status=PphFinalPayment.Status.POSTED).exists():
        raise ValidationFailed(_("PPh Final for %(period)s has already been recorded.") % _period(month, year))
    period = pph_final_schedule(organization, year)[month - 1]
    if period.tax <= 0:
        raise ValidationFailed(_("No PPh Final is due for this period."))

    # Journal descriptions are stored business records and stay in Indonesian.
    entry = create_journal_entry(
        organization=organization,
        date=month_end(year, month),
        description=f"Beban PPh Final UMKM masa {month.tax_period(year)}",
        source=JournalEntry.Source.TAX,
        created_by=created_by,
        lines=[
            JournalLineInput(get_system_account(organization, SystemRole.PPH_FINAL_EXPENSE), debit=period.tax),
            JournalLineInput(get_system_account(organization, SystemRole.PPH_FINAL_PAYABLE), credit=period.tax),
        ],
    )
    return PphFinalAccrual.objects.create(
        organization=organization,
        year=year,
        month=month,
        gross_turnover=period.gross_turnover,
        taxable_turnover=period.taxable_turnover,
        tax_amount=period.tax,
        journal_entry=entry,
        created_by=created_by,
    )


@transaction.atomic
def cancel_pph_final_accrual(*, accrual: PphFinalAccrual, on: date, created_by=None) -> PphFinalAccrual:
    accrual = PphFinalAccrual.objects.select_for_update().get(pk=accrual.pk)
    if accrual.status != PphFinalAccrual.Status.POSTED:
        raise InvalidStateTransition(_("This PPh Final expense has already been cancelled."))
    if accrual.payments.filter(status=PphFinalPayment.Status.POSTED).exists():
        raise InvalidStateTransition(_("Cancel the payment of this PPh Final first."))
    reverse_journal_entry(
        entry=accrual.journal_entry, date=on, created_by=created_by,
        description=f"Pembatalan beban PPh Final masa {Month(accrual.month).tax_period(accrual.year)}",
    )
    accrual.status = PphFinalAccrual.Status.VOID  # kept as history: never deleted
    accrual.save(update_fields=["status", "updated_at"])
    return accrual


@transaction.atomic
def record_pph_final_payment(
    *, organization, year: int, month: Month, paid_on: date, cash_account, ntpn: str = "", created_by=None
) -> PphFinalPayment:
    """Deposit (setoran) of a month's PPh Final. It settles the month's recorded expense,
    which is recorded first when it is missing, so the expense always falls in its own month.
    PPh Final that buyers withheld from the month's receipts already settled part of it (the
    buyers deposit it), so only the rest is paid."""
    month = Month(month)
    _ensure_uses_pph_final(organization)
    if not cash_account.is_cash or cash_account.organization_id != organization.pk:
        raise ValidationFailed(_("Choose a valid cash or bank account."))
    if PphFinalPayment.objects.for_organization(organization).filter(year=year, month=month, status=PphFinalPayment.Status.POSTED).exists():
        raise ValidationFailed(_("PPh Final for %(period)s has already been recorded.") % _period(month, year))
    accrual = posted_accrual(organization, year, month) or accrue_pph_final(
        organization=organization, year=year, month=month, created_by=created_by
    )
    withheld = pph_final_withheld(organization, year).get(month, ZERO)
    if withheld >= accrual.tax_amount:
        raise ValidationFailed(
            _("Buyers withheld %(withheld)s of PPh Final in %(period)s, which covers the %(due)s due: there is nothing to deposit.")
            % {"withheld": localized_rupiah(withheld), "due": localized_rupiah(accrual.tax_amount), **_period(month, year)}
        )

    entry = create_journal_entry(
        organization=organization,
        date=paid_on,
        description=f"Setoran PPh Final UMKM masa {month.tax_period(year)}",
        reference=ntpn,
        source=JournalEntry.Source.TAX,
        created_by=created_by,
        lines=[
            JournalLineInput(get_system_account(organization, SystemRole.PPH_FINAL_PAYABLE), debit=accrual.tax_amount - withheld),
            JournalLineInput(cash_account, credit=accrual.tax_amount - withheld),
        ],
    )
    return PphFinalPayment.objects.create(
        organization=organization,
        year=year,
        month=month,
        gross_turnover=accrual.gross_turnover,
        taxable_turnover=accrual.taxable_turnover,
        tax_amount=accrual.tax_amount,
        withheld=withheld,
        paid_on=paid_on,
        ntpn=ntpn,
        cash_account=cash_account,
        journal_entry=entry,
        accrual=accrual,
        created_by=created_by,
    )


@transaction.atomic
def cancel_pph_final_payment(*, payment: PphFinalPayment, on: date, created_by=None) -> PphFinalPayment:
    payment = PphFinalPayment.objects.select_for_update().get(pk=payment.pk)
    if payment.status != PphFinalPayment.Status.POSTED:
        raise InvalidStateTransition(_("This PPh Final payment has already been cancelled."))
    reverse_journal_entry(
        entry=payment.journal_entry, date=on, created_by=created_by,
        description=f"Pembatalan setoran PPh Final masa {Month(payment.month).tax_period(payment.year)}",
    )
    payment.status = PphFinalPayment.Status.VOID  # kept as history: never deleted
    payment.save(update_fields=["status", "updated_at"])
    return payment


PAYER_ROLES = {
    PayerWithholding.PPH_23: SystemRole.PPH_23_PAYABLE, PayerWithholding.PPH_4_2: SystemRole.PPH_4_2_PAYABLE,
    PayerWithholding.PPH_21: SystemRole.PPH_21_PAYABLE, PayerWithholding.PPH_26: SystemRole.PPH_26_PAYABLE,
}


@transaction.atomic
def record_withholding_deposit(
    *, organization, kind: str, year: int, month: Month, paid_on: date, cash_account, ntpn: str = "", created_by=None,
) -> WithholdingDeposit:
    """Deposit (setoran) of the PPh 23, 4(2), 21 or 26 withheld in a month: it settles the
    withheld tax payable. Due by the 15th of the next month."""
    month = Month(month)
    kind = PayerWithholding(kind)
    if not cash_account.is_cash or cash_account.organization_id != organization.pk:
        raise ValidationFailed(_("Choose a valid cash or bank account."))
    if WithholdingDeposit.objects.for_organization(organization).filter(kind=kind, year=year, month=month, status=WithholdingDeposit.Status.POSTED).exists():
        raise ValidationFailed(_("The %(tax)s for %(period)s has already been deposited.") % {"tax": kind.label, **_period(month, year)})
    amount = withholding_due(organization, year).get((kind, month), ZERO)
    if amount <= 0:
        raise ValidationFailed(_("No %(tax)s was withheld in %(period)s.") % {"tax": kind.label, **_period(month, year)})
    tax = PAYER_WITHHOLDING_NAMES[kind]
    entry = create_journal_entry(
        organization=organization, date=paid_on, description=f"Setoran {tax} masa {month.tax_period(year)}", reference=ntpn,
        source=JournalEntry.Source.TAX, created_by=created_by,
        lines=[
            JournalLineInput(get_system_account(organization, PAYER_ROLES[kind]), debit=amount),
            JournalLineInput(cash_account, credit=amount),
        ],
    )
    return WithholdingDeposit.objects.create(
        organization=organization, kind=kind, year=year, month=month, amount=amount, paid_on=paid_on, ntpn=ntpn,
        cash_account=cash_account, journal_entry=entry, created_by=created_by,
    )


@transaction.atomic
def cancel_withholding_deposit(*, deposit: WithholdingDeposit, on: date, created_by=None) -> WithholdingDeposit:
    deposit = WithholdingDeposit.objects.select_for_update().get(pk=deposit.pk)
    if deposit.status != WithholdingDeposit.Status.POSTED:
        raise InvalidStateTransition(_("This deposit has already been cancelled."))
    reverse_journal_entry(
        entry=deposit.journal_entry, date=max(on, deposit.paid_on), created_by=created_by,
        description=f"Pembatalan setoran {deposit.get_kind_display()} masa {Month(deposit.month).tax_period(deposit.year)}"[:255],
    )
    deposit.status = WithholdingDeposit.Status.VOID  # kept as history: never deleted
    deposit.save(update_fields=["status", "updated_at"])
    return deposit


# --- Imports (PIB) and self-deposited income tax ------------------------------------------

IMPORT_COST_CATEGORIES = (
    AccountCategory.COST_OF_SALES, AccountCategory.OPERATING_EXPENSE, AccountCategory.OTHER_EXPENSE, AccountCategory.INVENTORY,
    AccountCategory.OTHER_CURRENT_ASSET, AccountCategory.FIXED_ASSET,
)


def _own_cash(organization, account) -> None:
    if account is None or account.organization_id != organization.pk or not account.is_cash:
        raise ValidationFailed(_("Choose a valid cash or bank account."))


def _check_import_accounts(organization, cost_account, cash_account, bill, *, prepaid_tax_account, pays_pph22: bool) -> None:
    from apps.invoicing.models import InvoiceKind, Item  # invoicing depends on taxes; tracked stock is valued from its movements

    _own_cash(organization, cash_account)
    if (cost_account.organization_id != organization.pk or not cost_account.is_active
            or cost_account.category not in IMPORT_COST_CATEGORIES or cost_account.system_role not in (None, SystemRole.PURCHASE_EXPENSE)):
        raise ValidationFailed(_("Choose an expense, inventory or asset account for the import costs."))
    if Item.objects.filter(organization=organization, track_inventory=True, inventory_account=cost_account).exists():
        raise ValidationFailed(_("Import costs of tracked stock belong on the supplier's bill (or as their own bill): choose another account."))
    if pays_pph22 and (
        prepaid_tax_account is None or prepaid_tax_account.organization_id != organization.pk
        or prepaid_tax_account.category != AccountCategory.OTHER_CURRENT_ASSET or prepaid_tax_account.system_role
    ):
        raise ValidationFailed(_("Choose the prepaid tax account for the PPh 22 impor."))
    if bill is not None and (bill.organization_id != organization.pk or bill.kind != InvoiceKind.PURCHASE):
        raise ValidationFailed(_("Choose one of your bills."))


@transaction.atomic
def record_import_declaration(
    *, organization, number: str, on: date, supplier_name: str, customs_value: Decimal, import_duty: Decimal, vat: Decimal,
    luxury_tax: Decimal, pph22: Decimal, cost_account, cash_account, prepaid_tax_account=None, bill=None, ntpn: str = "",
    created_by=None,
) -> ImportDeclaration:
    """The taxes paid at customs on an import (PIB): PPN impor is input PPN for a PKP (a cost
    otherwise), PPh 22 impor a prepaid income tax, import duty and PPnBM part of the cost."""
    ensure_business_date(on)
    number = number.strip()
    if not number or not supplier_name.strip():
        raise ValidationFailed(_("Enter the PIB number and the foreign supplier."))
    if min(customs_value, import_duty, vat, luxury_tax, pph22) < 0 or customs_value <= 0:
        raise ValidationFailed(_("Enter the import value and the taxes paid; none can be negative."))
    if vat + luxury_tax + pph22 + import_duty <= 0:
        raise ValidationFailed(_("Enter the taxes paid at customs."))
    if ImportDeclaration.objects.for_organization(organization).filter(number=number, status=ImportDeclaration.Status.POSTED).exists():
        raise ValidationFailed(_("PIB %(number)s has already been recorded.") % {"number": number})
    _check_import_accounts(organization, cost_account, cash_account, bill, prepaid_tax_account=prepaid_tax_account if pph22 else None,
                           pays_pph22=bool(pph22))
    creditable = organization.is_pkp
    cost = import_duty + luxury_tax + (ZERO if creditable else vat)
    lines = [
        JournalLineInput(cost_account, debit=cost),
        JournalLineInput(get_system_account(organization, SystemRole.INPUT_VAT), debit=vat if creditable else ZERO),
        JournalLineInput(prepaid_tax_account, debit=pph22) if pph22 else None,
        JournalLineInput(cash_account, credit=import_duty + vat + luxury_tax + pph22),
    ]
    entry = create_journal_entry(
        organization=organization, date=on, description=f"Pajak dalam rangka impor PIB {number}", reference=ntpn,
        source=JournalEntry.Source.TAX, created_by=created_by, lines=[l for l in lines if l is not None and (l.debit or l.credit)],
    )
    return ImportDeclaration.objects.create(
        organization=organization, number=number, date=on, supplier_name=supplier_name.strip(), bill=bill,
        customs_value=customs_value, import_duty=import_duty, vat=vat, luxury_tax=luxury_tax, pph22=pph22,
        cost_account=cost_account, prepaid_tax_account=prepaid_tax_account if pph22 else None, cash_account=cash_account,
        ntpn=ntpn, journal_entry=entry, created_by=created_by,
    )


@transaction.atomic
def cancel_import_declaration(*, declaration: ImportDeclaration, on: date, created_by=None) -> ImportDeclaration:
    declaration = ImportDeclaration.objects.select_for_update().get(pk=declaration.pk)
    if declaration.status != ImportDeclaration.Status.POSTED:
        raise InvalidStateTransition(_("This import declaration has already been cancelled."))
    reverse_journal_entry(entry=declaration.journal_entry, date=max(on, declaration.date), created_by=created_by,
                          description=f"Pembatalan PIB {declaration.number}")
    declaration.status = ImportDeclaration.Status.VOID
    declaration.save(update_fields=["status", "updated_at"])
    return declaration


@transaction.atomic
def record_self_deposit(
    *, organization, tax_object: str, tax_base: Decimal, document_number: str, document_date: date, paid_on: date, cash_account,
    invoice=None, ntpn: str = "", created_by=None,
) -> SelfDeposit:
    """Final income tax deposited on one's own income (BPSP): the expense in the month of the
    income (accrual), and its deposit, which settles it."""
    from apps.invoicing.models import InvoiceKind  # invoicing depends on taxes

    rate_table = tax_objects.SELF_DEPOSIT
    if tax_object not in rate_table:
        raise ValidationFailed(_("Choose the tax object code."))
    if tax_base <= 0:
        raise ValidationFailed(_("Enter the income the tax is on."))
    ensure_business_date(document_date)
    ensure_business_date(paid_on)
    if paid_on < document_date:
        raise ValidationFailed(_("The deposit can't be before the income."))
    document_number = document_number.strip()
    if not document_number:
        raise ValidationFailed(_("Enter the document the income is on (e.g. the invoice number)."))
    if invoice is not None and (invoice.organization_id != organization.pk or invoice.kind != InvoiceKind.SALES):
        raise ValidationFailed(_("Choose one of your sales invoices."))
    _own_cash(organization, cash_account)
    rate = rate_table[tax_object][1]
    amount = round_down_rupiah(tax_base * rate / Decimal("100"))
    if amount <= 0:
        raise ValidationFailed(_("This income bears no tax to deposit."))
    payable = get_system_account(organization, SystemRole.PPH_4_2_PAYABLE)
    period = Month(document_date.month).tax_period(document_date.year)
    expense = create_journal_entry(
        organization=organization, date=document_date, description=f"Beban PPh final disetor sendiri {tax_object} masa {period}",
        source=JournalEntry.Source.TAX, created_by=created_by, lines=[
            JournalLineInput(get_system_account(organization, SystemRole.PPH_FINAL_EXPENSE), debit=amount),
            JournalLineInput(payable, credit=amount),
        ],
    )
    payment = create_journal_entry(
        organization=organization, date=paid_on, description=f"Setoran sendiri PPh final {tax_object} masa {period}", reference=ntpn,
        source=JournalEntry.Source.TAX, created_by=created_by, lines=[
            JournalLineInput(payable, debit=amount), JournalLineInput(cash_account, credit=amount),
        ],
    )
    return SelfDeposit.objects.create(
        organization=organization, tax_object=tax_object, year=document_date.year, month=document_date.month, tax_base=tax_base,
        rate=rate, amount=amount, invoice=invoice, document_number=document_number, document_date=document_date, paid_on=paid_on,
        ntpn=ntpn, cash_account=cash_account, expense_entry=expense, payment_entry=payment, created_by=created_by,
    )


@transaction.atomic
def cancel_self_deposit(*, deposit: SelfDeposit, on: date, created_by=None) -> SelfDeposit:
    deposit = SelfDeposit.objects.select_for_update().get(pk=deposit.pk)
    if deposit.status != SelfDeposit.Status.POSTED:
        raise InvalidStateTransition(_("This self-deposit has already been cancelled."))
    for entry in (deposit.payment_entry, deposit.expense_entry):
        reverse_journal_entry(entry=entry, date=max(on, entry.date), created_by=created_by,
                              description=f"Pembatalan setoran sendiri {deposit.tax_object}")
    deposit.status = SelfDeposit.Status.VOID
    deposit.save(update_fields=["status", "updated_at"])
    return deposit


@transaction.atomic
def record_self_deposit_slip(*, deposit: SelfDeposit, number: str) -> SelfDeposit:
    deposit = SelfDeposit.objects.select_for_update().get(pk=deposit.pk)
    if deposit.slip_number or deposit.status != SelfDeposit.Status.POSTED:
        raise InvalidStateTransition(_("This self-deposit already has its BPSP number."))
    number = number.strip()
    if not number or len(number) > 50:
        raise ValidationFailed(_("Enter the BPSP number."))
    deposit.slip_number = number
    deposit.save(update_fields=["slip_number", "updated_at"])
    return deposit
