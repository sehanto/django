from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    verbose_name = _("Users")

    def ready(self):
        from . import signals

        signals.connect()
