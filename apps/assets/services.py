"""Fixed asset register: writes. Depreciation and disposals post through the ledger."""

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils.translation import gettext as _

from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.core.money import ZERO, quantize
from apps.core.sequences import next_document_number
from apps.core.validators import ensure_business_date
from apps.ledger.domain import AccountCategory
from apps.ledger.models import JournalEntry
from apps.ledger.services import JournalLineInput, create_journal_entry

from .domain import add_months, month_end
from .models import DepreciationPosting, FixedAsset
from .selectors import accumulated_depreciation, due_depreciation, posted_amounts

NUMBER_PREFIX = "AT"
EDITABLE_ONCE_DEPRECIATED = ("name", "description")


def _belongs(account, organization) -> bool:
    return account is not None and account.organization_id == organization.pk and account.is_active


def _validate(organization, data: dict) -> None:
    ensure_business_date(data["acquisition_date"])
    asset_account = data["asset_account"]
    if not _belongs(asset_account, organization) or asset_account.category != AccountCategory.FIXED_ASSET or asset_account.is_contra:
        raise ValidationFailed(_("Choose a fixed asset account (not accumulated depreciation)."))
    cost, residual = quantize(data["cost"]), quantize(data.get("residual_value") or ZERO)
    if cost <= 0 or residual < 0 or residual >= cost:
        raise ValidationFailed(_("The cost must be positive and the residual value lower than the cost."))
    if not data["is_depreciable"]:
        return
    if not data.get("useful_life_months"):
        raise ValidationFailed(_("Enter the useful life of a depreciable asset."))
    accumulated, expense = data.get("accumulated_depreciation_account"), data.get("depreciation_expense_account")
    if not _belongs(accumulated, organization) or accumulated.category != AccountCategory.FIXED_ASSET or not accumulated.is_contra:
        raise ValidationFailed(_("Choose an accumulated depreciation account (a contra fixed asset account)."))
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
        entry = create_journal_entry(
            organization=organization, date=month_end(year, month), description=f"Penyusutan aset tetap masa {_period(year, month)}",
            source=JournalEntry.Source.FIXED_ASSET, created_by=created_by, lines=lines,
        )
        DepreciationPosting.objects.bulk_create(
            DepreciationPosting(organization=organization, asset=asset, year=year, month=month, amount=scheduled.amount, journal_entry=entry)
            for asset, scheduled in items
        )
        entries.append(entry)
    return entries


@transaction.atomic
def dispose_asset(
    *, asset: FixedAsset, on: date, proceeds: Decimal = ZERO, cash_account=None, gain_loss_account=None, created_by=None
) -> FixedAsset:
    """Sell or write off an asset. Depreciation runs to the end of the month before the
    disposal (posted first if it is still due); the disposal entry removes the cost and the
    accumulated depreciation, records the proceeds and books the difference as a gain or loss."""
    asset = FixedAsset.objects.select_for_update().get(pk=asset.pk)
    organization = asset.organization
    if asset.status != FixedAsset.Status.ACTIVE:
        raise InvalidStateTransition(_("This asset has already been disposed."))
    ensure_business_date(on)
    if on < asset.acquisition_date:
        raise ValidationFailed(_("The disposal date can't be before the acquisition date."))
    proceeds = quantize(proceeds or ZERO)
    if proceeds < 0:
        raise ValidationFailed(_("The sale proceeds can't be negative."))
    if proceeds and (cash_account is None or not cash_account.is_cash or cash_account.organization_id != organization.pk):
        raise ValidationFailed(_("Choose the cash or bank account that received the proceeds."))

    previous_month_end = month_end(*add_months(on.year, on.month, -1))
    post_depreciation(organization=organization, through=previous_month_end, created_by=created_by)
    later = [key for key in posted_amounts(asset) if key >= (on.year, on.month)]
    if later:
        raise ValidationFailed(
            _("Depreciation is already posted for %(period)s. Choose a disposal date after that month.") % {"period": _period(*max(later))}
        )
    accumulated = accumulated_depreciation(asset)
    difference = asset.cost - accumulated - proceeds  # positive: loss, negative: gain
    if difference and (
        gain_loss_account is None or gain_loss_account.organization_id != organization.pk or not gain_loss_account.is_active
        or gain_loss_account.category not in (AccountCategory.OTHER_INCOME, AccountCategory.OTHER_EXPENSE)
    ):
        raise ValidationFailed(_("Choose an other income or other expense account for the gain or loss."))

    label = f"{asset.number} {asset.name}"
    lines = []
    if proceeds:
        lines.append(JournalLineInput(cash_account, debit=proceeds, description=label))
    if accumulated:
        lines.append(JournalLineInput(asset.accumulated_depreciation_account, debit=accumulated, description=label))
    if difference > 0:
        lines.append(JournalLineInput(gain_loss_account, debit=difference, description=label))
    lines.append(JournalLineInput(asset.asset_account, credit=asset.cost, description=label))
    if difference < 0:
        lines.append(JournalLineInput(gain_loss_account, credit=-difference, description=label))
    entry = create_journal_entry(
        organization=organization, date=on, description=f"Pelepasan aset tetap {label}",
        source=JournalEntry.Source.FIXED_ASSET, created_by=created_by, lines=lines,
    )
    asset.status = FixedAsset.Status.DISPOSED
    asset.disposed_on = on
    asset.disposal_proceeds = proceeds
    asset.disposal_entry = entry
    asset.save(update_fields=["status", "disposed_on", "disposal_proceeds", "disposal_entry", "updated_at"])
    return asset
