from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AssetsConfig(AppConfig):
    name = "apps.assets"
    verbose_name = _("Fixed assets")
