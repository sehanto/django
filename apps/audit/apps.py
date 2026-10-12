from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AuditConfig(AppConfig):
    name = "apps.audit"
    verbose_name = _("Audit log")

    def ready(self):
        from . import signals

        signals.connect()
