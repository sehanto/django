from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantOwnedModel

from .domain import UploadKind


class Upload(TenantOwnedModel):
    """A file whose rows were applied (append-only, uploads 0002): who uploaded what, when, and what
    it did. The same file can't be applied twice (its SHA-256), so a second click or a re-sent
    file changes nothing. The records it created carry their own history (audit trail)."""

    Kind = UploadKind

    kind = models.CharField(_("type"), max_length=30, choices=UploadKind.choices)
    file_name = models.CharField(_("file"), max_length=255)
    sha256 = models.CharField(_("SHA-256"), max_length=64)
    rows = models.PositiveIntegerField(_("rows"))
    applied = models.PositiveIntegerField(_("rows applied"))
    skipped = models.PositiveIntegerField(_("rows skipped"))
    summary = models.CharField(_("summary"), max_length=500, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("file upload")
        verbose_name_plural = _("file uploads")
        constraints = [models.UniqueConstraint(fields=["organization", "kind", "sha256"], name="upload_applied_once")]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.file_name}"
