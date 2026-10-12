from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .domain import AuditAction


class AuditEvent(models.Model):
    """One sensitive action. Append-only: the database rejects edits and deletes
    (migration 0002), except retention pruning by the schema owner and clearing the
    user/organization links when those rows are deleted (snapshots keep the names)."""

    created_at = models.DateTimeField(_("time"), default=timezone.now, editable=False, db_index=True)
    action = models.CharField(_("action"), max_length=40, choices=AuditAction.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name=_("user")
    )
    actor_email = models.CharField(_("user email"), max_length=254, blank=True)
    organization = models.ForeignKey(
        "tenants.Organization", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        verbose_name=_("organization"),
    )
    organization_name = models.CharField(_("organization name"), max_length=200, blank=True)
    ip_address = models.GenericIPAddressField(_("IP address"), null=True, blank=True)
    user_agent = models.CharField(_("browser"), max_length=255, blank=True)
    target = models.CharField(_("target"), max_length=255, blank=True)
    details = models.JSONField(_("details"), default=dict, blank=True, encoder=DjangoJSONEncoder)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("audit event")
        verbose_name_plural = _("audit log")
        indexes = [
            models.Index(fields=["organization", "-created_at"], name="audit_org_time"),
            models.Index(fields=["actor", "-created_at"], name="audit_actor_time"),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.actor_email}"
