from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class UploadsConfig(AppConfig):
    name = "apps.uploads"
    verbose_name = _("File uploads")
