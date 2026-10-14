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


class RecordChange(models.Model):
    """One row inserted, changed or deleted in a tenant table: the audit trail of the books.

    Written only by the database (``audit_record_change()``, a trigger on every tenant table,
    migration 0009), never by the application: the app role may read this table but not
    write it. Each organization's changes form a hash chain like the posted ledger
    (``sequence``, ``previous_hash``, ``change_hash``), checked by ``manage.py
    verify_audit_trail``. Rows are never updated or deleted, except when the schema owner
    deletes the whole organization."""

    class Operation(models.TextChoices):
        INSERT = "I", _("Created")
        UPDATE = "U", _("Changed")
        DELETE = "D", _("Deleted")

    organization = models.ForeignKey(
        "tenants.Organization", on_delete=models.DO_NOTHING, db_constraint=False, related_name="+",
        verbose_name=_("organization"),
    )
    sequence = models.BigIntegerField(_("sequence"), editable=False)
    table_name = models.CharField(_("table"), max_length=63, editable=False)
    record_id = models.CharField(_("record"), max_length=64, editable=False)
    operation = models.CharField(_("operation"), max_length=1, choices=Operation.choices, editable=False)
    # INSERT: the new row; DELETE: the old row; UPDATE: {column: [old, new]} for the columns that changed.
    changes = models.JSONField(_("changes"), editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.DO_NOTHING, db_constraint=False, related_name="+",
        verbose_name=_("user"),
    )
    actor_label = models.CharField(_("changed by"), max_length=254, blank=True, editable=False)
    db_role = models.CharField(_("database role"), max_length=63, editable=False)
    transaction_id = models.BigIntegerField(_("transaction"), editable=False)
    changed_at = models.DateTimeField(_("time"), editable=False)
    previous_hash = models.CharField(max_length=64, editable=False)
    change_hash = models.CharField(max_length=64, editable=False)

    class Meta:
        ordering = ["-sequence"]
        verbose_name = _("record change")
        verbose_name_plural = _("record changes")
        constraints = [models.UniqueConstraint(fields=["organization", "sequence"], name="unique_record_change_sequence")]
        indexes = [models.Index(fields=["organization", "table_name", "record_id"], name="audit_change_record")]

    def __str__(self):
        return f"{self.table_name} {self.record_id} {self.operation}"
