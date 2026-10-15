from django.apps import AppConfig
from django.db.models.signals import post_migrate
from django.utils.translation import gettext_lazy as _


def _grant_app_privileges(sender, using, **kwargs):
    from .db_roles import grant_app_privileges

    grant_app_privileges(using)


class CoreConfig(AppConfig):
    name = "apps.core"
    verbose_name = _("Core")

    def ready(self):
        from django.urls import register_converter

        from . import checks  # noqa: F401 - registers system checks
        from .converters import MonthConverter, YearConverter

        register_converter(YearConverter, "year")
        register_converter(MonthConverter, "month")

        post_migrate.connect(_grant_app_privileges, sender=self, dispatch_uid="core.grant_app_privileges")
