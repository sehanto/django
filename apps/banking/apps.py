from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class BankingConfig(AppConfig):
    name = "apps.banking"
    verbose_name = _("Bank reconciliation")
