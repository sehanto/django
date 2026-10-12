from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class LedgerConfig(AppConfig):
    name = "apps.ledger"
    verbose_name = _("General Ledger")
