"""The uploads, by kind (``domain.UploadKind``)."""

from django.utils.translation import gettext_lazy as _

from ..domain import UploadKind
from .bank import BankStatementImporter
from .base import Context, Importer, apply, check
from .coretax import InputFakturImporter, OutputFakturImporter, WithholdingSlipImporter
from .master import AccountImporter, ContactImporter, ItemImporter
from .opening import FixedAssetImporter, OpeningBalancesImporter

IMPORTERS: dict[str, Importer] = {
    importer.kind: importer
    for importer in (
        ContactImporter(), ItemImporter(), AccountImporter(), OpeningBalancesImporter(), FixedAssetImporter(),
        OutputFakturImporter(), InputFakturImporter(), WithholdingSlipImporter(), BankStatementImporter(),
    )
}
assert set(IMPORTERS) == set(UploadKind.values), "every upload kind has its importer"

GROUPS = (
    ("master", _("Master data"), _("Start from the lists you already keep in a spreadsheet.")),
    ("opening", _("Opening balances"), _("Bring over the balances of the earlier books on the day they end.")),
    ("coretax", _("Coretax results"), _("Record what Coretax gave back for the files uploaded to it.")),
    ("bank", _("Bank"), _("Reconcile the bank accounts with the statements from internet banking.")),
)

__all__ = ["GROUPS", "IMPORTERS", "Context", "Importer", "apply", "check"]
