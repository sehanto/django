from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class DeferralsConfig(AppConfig):
    name = "apps.deferrals"
    verbose_name = _("Prepayments")
