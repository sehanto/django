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


class IdempotentRequest(models.Model):
    """A successful API write, kept for ``idempotency.WINDOW`` under the client's ``Idempotency-Key``:
    a retry of the same request gets this response again instead of creating the document twice.

    Only successes are kept (a failed request changed nothing and may be retried). The row is
    inserted in the request's transaction before the work, so a concurrent retry waits for it."""

    api_key = models.ForeignKey(ApiKey, on_delete=models.CASCADE, related_name="idempotent_requests")
    key = models.CharField(max_length=100)
    fingerprint = models.CharField(max_length=64)  # SHA-256 of method, path and body
    status = models.PositiveSmallIntegerField(default=0)
    body = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["api_key", "key"], name="unique_idempotency_key_per_api_key")]
