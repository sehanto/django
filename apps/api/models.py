from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class ApiKey(TimeStampedModel):
    """A key for the REST API (``/api/v1/``), scoped to one organization.

    Only the key's keyed hash is stored; it is shown once when created. Requests act as the
    member who created it, with that member's current role: removing the member (or
    revoking the key) ends its access."""

    organization = models.ForeignKey("tenants.Organization", on_delete=models.CASCADE, related_name="api_keys")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys", verbose_name=_("created by"))
    name = models.CharField(_("name"), max_length=100, help_text=_("What it is for, e.g. the online shop."))
    prefix = models.CharField(_("key starts with"), max_length=12, editable=False)
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    last_used_at = models.DateTimeField(_("last used"), null=True, blank=True)
    revoked_at = models.DateTimeField(_("revoked"), null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("API key")
        verbose_name_plural = _("API keys")

    def __str__(self):
        return f"{self.name} ({self.prefix}…)"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
