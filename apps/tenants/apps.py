from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TenantsConfig(AppConfig):
    name = "apps.tenants"
    verbose_name = _("Organizations")
