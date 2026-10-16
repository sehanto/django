"""Fixed asset register: writes. Depreciation and disposals post through the ledger."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.core.money import ZERO, quantize
from apps.core.sequences import next_document_number
from apps.core.periods import add_months, month_end
from apps.core.validators import ensure_business_date
from apps.ledger.domain import AccountCategory
from apps.ledger.models import JournalEntry
from apps.ledger.services import JournalLineInput, create_journal_entry

from .domain import DepreciationMethod, months_between
from .models import AssetRevision, DepreciationPosting, FixedAsset
from .selectors import accumulated_depreciation, due_depreciation, posted_amounts

NUMBER_PREFIX = "AT"
# Fixed assets (Bab 11) and separately acquired intangible assets (Bab 12), amortised alike.
REGISTER_CATEGORIES = (AccountCategory.FIXED_ASSET, AccountCategory.INTANGIBLE_ASSET)
EDITABLE_ONCE_DEPRECIATED = ("name", "description")


def _belongs(account, organization) -> bool:
    return account is not None and account.organization_id == organization.pk and account.is_active


def _validate(organization, data: dict) -> None:
    ensure_business_date(data["acquisition_date"])
    asset_account = data["asset_account"]
    if not _belongs(asset_account, organization) or asset_account.category not in REGISTER_CATEGORIES or asset_account.is_contra:
        raise ValidationFailed(_("Choose a fixed asset or intangible asset account (not accumulated depreciation)."))
    if quantize(data["cost"]) <= 0:
        raise ValidationFailed(_("The cost must be positive."))
    if quantize(data.get("residual_value") or ZERO):
        raise ValidationFailed(_("SAK EMKM depreciates and amortises without a residual value: leave it at 0."))
    if not data["is_depreciable"]:
        if asset_account.category == AccountCategory.INTANGIBLE_ASSET:
            raise ValidationFailed(_("An intangible asset has a limited useful life and is amortised (SAK EMKM 12.10)."))
        return
    if not data.get("useful_life_months"):
        raise ValidationFailed(_("Enter the useful life of a depreciable asset."))
    accumulated, expense = data.get("accumulated_depreciation_account"), data.get("depreciation_expense_account")
    if not _belongs(accumulated, organization) or accumulated.category != asset_account.category or not accumulated.is_contra:
        raise ValidationFailed(_("Choose an accumulated depreciation (or, for an intangible asset, amortisation) account of the same kind as the asset account."))
    if not _belongs(expense, organization) or expense.category not in (AccountCategory.OPERATING_EXPENSE, AccountCategory.COST_OF_SALES):
        raise ValidationFailed(_("Choose an expense account for the depreciation."))
    until = data.get("depreciated_before_until")
    if until is not None and until < data["acquisition_date"]:
        raise ValidationFailed(_("Depreciation already recorded can't end before the acquisition date."))


def _normalized(data: dict) -> dict:
    data = {**data}
    if not data["is_depreciable"]:  # land: nothing to depreciate
        data.update(useful_life_months=None, accumulated_depreciation_account=None, depreciation_expense_account=None,
                    depreciated_before_until=None, residual_value=ZERO)
    elif data.get("depreciated_before_until"):  # whole months
        until = data["depreciated_before_until"]
        data["depreciated_before_until"] = month_end(until.year, until.month)
    data["residual_value"] = data.get("residual_value") or ZERO
    return data


@transaction.atomic
def register_asset(*, organization, created_by=None, **data) -> FixedAsset:
    """Add an asset already in the books; its depreciation method is the organization's policy."""
    _validate(organization, data)
    return FixedAsset.objects.create(
        organization=organization,
        number=next_document_number(organization, NUMBER_PREFIX, data["acquisition_date"]),
        method=organization.depreciation_method,
        created_by=created_by,
        **_normalized(data),
    )


def is_locked(asset: FixedAsset) -> bool:
    """Figures are fixed once depreciation is posted or the asset is disposed (assets migration 0002)."""
    return asset.status != FixedAsset.Status.ACTIVE or asset.postings.exists()


@transaction.atomic
def update_asset(*, asset: FixedAsset, **data) -> FixedAsset:
    asset = FixedAsset.objects.select_for_update().get(pk=asset.pk)
    if is_locked(asset):
        changes = {name: data[name] for name in EDITABLE_ONCE_DEPRECIATED if name in data}
    else:
        _validate(asset.organization, data)
        changes = _normalized(data)
    for name, value in changes.items():
        setattr(asset, name, value)
    asset.save()
    return asset


@transaction.atomic
def delete_asset(*, asset: FixedAsset) -> None:
    if is_locked(asset):
        raise InvalidStateTransition(_("This asset has posted depreciation or has been disposed, so it stays in the register."))
    asset.delete()


def _period(year: int, month: int) -> str:
    return f"{month:02d}/{year}"


@transaction.atomic
def post_depreciation(*, organization, through: date, created_by=None) -> list[JournalEntry]:
    """Post every month of depreciation that has ended by ``through`` and isn't posted yet:
    one journal entry per month, dated its last day (Beban Penyusutan / Akumulasi Penyusutan).

    Raises ``PeriodLocked`` when a month due falls on or before the lock date; nothing is
    posted then, so the register never skips a month.
    """
    entries = []
    for (year, month), items in due_depreciation(organization, through).items():
        lines = []
        for asset, scheduled in items:
            label = f"{asset.number} {asset.name}"
            lines.append(JournalLineInput(asset.depreciation_expense_account, debit=scheduled.amount, description=label))
            lines.append(JournalLineInput(asset.accumulated_depreciation_account, credit=scheduled.amount, description=label))
        # Journal descriptions are stored business records and stay in Indonesian.
        intangible = any(asset.asset_account.category == AccountCategory.INTANGIBLE_ASSET for asset, _scheduled in items)
        tangible = any(asset.asset_account.category != AccountCategory.INTANGIBLE_ASSET for asset, _scheduled in items)
        what = {(True, False): "Penyusutan aset tetap", (False, True): "Amortisasi aset takberwujud"}.get(
            (tangible, intangible), "Penyusutan aset tetap dan amortisasi aset takberwujud")
        entry = create_journal_entry(
            organization=organization, date=month_end(year, month), description=f"{what} masa {_period(year, month)}",
            source=JournalEntry.Source.FIXED_ASSET, created_by=created_by, lines=lines,
        )
        DepreciationPosting.objects.bulk_create(
            DepreciationPosting(organization=organization, asset=asset, year=year, month=month, amount=scheduled.amount, journal_entry=entry)
            for asset, scheduled in items
        )
        entries.append(entry)
    return entries


@dataclass(frozen=True)
class InvoicedSale:
    """Sell an asset on a sales invoice to ``customer``: a receivable instead of cash. For a
    PKP with a PPN rate, it is a delivery of an asset not originally for sale (Art. 16D UU PPN,
    Faktur Pajak code 09)."""

    customer: object  # contacts.Contact
    tax_rate: object = None  # taxes.TaxRate | None


def _check_disposal(asset: FixedAsset, on: date, proceeds: Decimal, cash_account, sale) -> None:
    if asset.status != FixedAsset.Status.ACTIVE:
        raise InvalidStateTransition(_("This asset has already been disposed."))
    ensure_business_date(on)
    if on < asset.acquisition_date:
        raise ValidationFailed(_("The disposal date can't be before the acquisition date."))
    if proceeds < 0:
        raise ValidationFailed(_("The sale proceeds can't be negative."))
    if sale is not None and not proceeds:
        raise ValidationFailed(_("Enter the sale price to invoice."))
    if proceeds and sale is None and (
        cash_account is None or not cash_account.is_cash or cash_account.organization_id != asset.organization_id
    ):
        raise ValidationFailed(_("Choose the cash or bank account that received the proceeds."))


def _is_gain_loss_account(account, organization) -> bool:
    return (
        account is not None and account.organization_id == organization.pk and account.is_active
        and account.category in (AccountCategory.OTHER_INCOME, AccountCategory.OTHER_EXPENSE)
    )


def _signed_line(account, amount: Decimal, description: str) -> JournalLineInput:
    """A debit of ``amount``, or a credit when it is negative."""
    if amount >= 0:
        return JournalLineInput(account, debit=amount, description=description)
    return JournalLineInput(account, credit=-amount, description=description)


@transaction.atomic
def dispose_asset(
    *, asset: FixedAsset, on: date, proceeds: Decimal = ZERO, cash_account=None, gain_loss_account=None,
    sale: InvoicedSale | None = None, created_by=None,
) -> FixedAsset:
    """Sell or write off an asset. Depreciation runs to the end of the month before the
    disposal (posted first if it is still due); the disposal entry removes the cost and the
    accumulated depreciation, records the proceeds and books the difference as a gain or loss.

    With ``sale`` the proceeds are invoiced: the sales invoice (issued on the disposal date)
    credits them to the asset account, and the disposal entry removes the rest of the cost."""
    asset = FixedAsset.objects.select_for_update().get(pk=asset.pk)
    organization = asset.organization
    proceeds = quantize(proceeds or ZERO)
    _check_disposal(asset, on, proceeds, cash_account, sale)

    previous_month_end = month_end(*add_months(on.year, on.month, -1))
    post_depreciation(organization=organization, through=previous_month_end, created_by=created_by)
    later = [key for key in posted_amounts(asset) if key >= (on.year, on.month)]
    if later:
        raise ValidationFailed(
            _("Depreciation is already posted for %(period)s. Choose a disposal date after that month.") % {"period": _period(*max(later))}
        )
    accumulated = accumulated_depreciation(asset)
    difference = asset.cost - accumulated - proceeds  # positive: loss, negative: gain
    if difference and not _is_gain_loss_account(gain_loss_account, organization):
        raise ValidationFailed(_("Choose an other income or other expense account for the gain or loss."))

    label = f"{asset.number} {asset.name}"
    invoice = _invoice_sale(asset, on, proceeds, sale, created_by) if sale is not None else None
    # An invoiced sale already credited the proceeds to the asset account.
    asset_credit = asset.cost - proceeds if invoice is not None else asset.cost
    lines = [
        JournalLineInput(cash_account, debit=proceeds if invoice is None else ZERO, description=label),
        JournalLineInput(asset.accumulated_depreciation_account, debit=accumulated, description=label),
        _signed_line(gain_loss_account, difference, label),  # a loss is debited
        _signed_line(asset.asset_account, -asset_credit, label),  # the cost left is credited
    ]
    entry = create_journal_entry(
        organization=organization, date=on, description=f"Pelepasan aset tetap {label}",
        source=JournalEntry.Source.FIXED_ASSET, created_by=created_by, lines=[line for line in lines if line.debit or line.credit],
    )
    asset.status = FixedAsset.Status.DISPOSED
    asset.disposed_on = on
    asset.disposal_proceeds = proceeds
    asset.disposal_entry = entry
    asset.disposal_invoice = invoice
    asset.save(update_fields=["status", "disposed_on", "disposal_proceeds", "disposal_entry", "disposal_invoice", "updated_at"])
    return asset


def _invoice_sale(asset: FixedAsset, on: date, proceeds: Decimal, sale: InvoicedSale, created_by):
    from apps.invoicing import services as invoicing  # invoicing doesn't depend on assets
    from apps.invoicing.models import DeliveryKind, InvoiceKind

    organization = asset.organization
    taxed = organization.is_pkp and sale.tax_rate is not None
    if sale.tax_rate is not None and sale.tax_rate.organization_id != organization.pk:
        raise ValidationFailed(_("Choose one of your tax rates."))
    invoice = invoicing.create_invoice(
        organization=organization, kind=InvoiceKind.SALES, contact=sale.customer, date=on, created_by=created_by,
        delivery_kind=DeliveryKind.ASSET if taxed else DeliveryKind.NORMAL,
        lines=[invoicing.InvoiceLineInput(asset.name, asset.asset_account, Decimal("1"), proceeds, tax_rate=sale.tax_rate if taxed else None)],
        notes=f"Penjualan aset tetap {asset.number}",  # stored business records stay Indonesian
    )
    return invoicing.issue_invoice(invoice=invoice, issued_by=created_by)


def first_revisable_month(asset: FixedAsset) -> date:
    """The first month a change in estimate can apply to: after the posted and opening-balance months."""
    candidates = [date(asset.acquisition_date.year, asset.acquisition_date.month, 1)]
    if asset.depreciated_before_until:
        candidates.append(date(*add_months(asset.depreciated_before_until.year, asset.depreciated_before_until.month, 1), 1))
    posted = posted_amounts(asset)
    if posted:
        candidates.append(date(*add_months(*max(posted), 1), 1))
    candidates += [r.effective_from for r in asset.estimates]
    return max(candidates)


@transaction.atomic
def revise_estimate(
    *, asset: FixedAsset, effective_from: date, useful_life_months: int, reason: str, method: str | None = None, created_by=None,
) -> AssetRevision:
    """Change the useful life or depreciation method from a month on (a change in accounting
    estimate, applied prospectively): depreciation already posted stays, and from that month the
    remaining book value is depreciated over the remaining life with the (new) method, without a
    residual value (SAK EMKM 11.14)."""
    asset = FixedAsset.objects.select_for_update().get(pk=asset.pk)
    if asset.status != FixedAsset.Status.ACTIVE or not asset.is_depreciable:
        raise InvalidStateTransition(_("Only a depreciable asset in use can be revised."))
    if not is_locked(asset):
        raise InvalidStateTransition(_("No depreciation has been posted for this asset yet, so edit it instead."))
    if not reason.strip():
        raise ValidationFailed(_("Give the reason for the change."))
    ensure_business_date(effective_from)
    starts = date(effective_from.year, effective_from.month, 1)
    earliest = first_revisable_month(asset)
    if starts < earliest:
        raise ValidationFailed(
            _("Depreciation up to %(period)s is already recorded. The change can apply from %(earliest)s.")
            % {"period": _period(*add_months(earliest.year, earliest.month, -1)), "earliest": _period(earliest.year, earliest.month)}
        )
    elapsed = months_between((asset.acquisition_date.year, asset.acquisition_date.month), (starts.year, starts.month))
    if useful_life_months <= elapsed:
        raise ValidationFailed(ngettext(
            "The useful life must run past %(period)s: at least %(months)d month from acquisition.",
            "The useful life must run past %(period)s: at least %(months)d months from acquisition.",
            elapsed + 1,
        ) % {"period": _period(starts.year, starts.month), "months": elapsed + 1})
    method = DepreciationMethod(method or asset.current_method)
    if (useful_life_months, method) == (asset.current_useful_life_months, asset.current_method):
        raise ValidationFailed(_("Change the useful life or the depreciation method."))
    return AssetRevision.objects.create(
        organization=asset.organization, asset=asset, effective_from=starts, useful_life_months=useful_life_months,
        previous_useful_life_months=asset.current_useful_life_months, method=method, previous_method=asset.current_method,
        reason=reason.strip(), created_by=created_by,
    )
