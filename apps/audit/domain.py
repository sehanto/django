"""What the audit log records: security-relevant and governance actions.

Bookkeeping itself is audited by the ledger (immutable entries with their
author); this log covers who signed in, changed security settings, managed
members, exported or deleted data, and changed the plan.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _


class AuditCategory(models.TextChoices):
    ACCOUNT = "account", _("Account security")
    ORGANIZATION = "organization", _("Organization")
    BILLING = "billing", _("Subscription")
    SYSTEM = "system", _("System")


class AuditAction(models.TextChoices):
    LOGIN_SUCCEEDED = "login_succeeded", _("Signed in")
    LOGIN_FAILED = "login_failed", _("Failed sign-in attempt")
    ACCOUNT_LOCKED = "account_locked", _("Sign-in locked after repeated failures")
    PASSWORD_CHANGED = "password_changed", _("Password changed")
    PASSWORD_RESET = "password_reset", _("Password reset by email")
    EMAIL_CHANGED = "email_changed", _("Email address changed")
    TWO_FACTOR_ENABLED = "two_factor_enabled", _("Two-factor authentication turned on")
    TWO_FACTOR_DISABLED = "two_factor_disabled", _("Two-factor authentication turned off")
    BACKUP_CODES_REGENERATED = "backup_codes_regenerated", _("New backup codes created")
    BACKUP_CODE_USED = "backup_code_used", _("Signed in with a backup code")
    DEVICE_REMEMBERED = "device_remembered", _("Device remembered for two-factor sign-in")
    DEVICES_FORGOTTEN = "devices_forgotten", _("Remembered devices forgotten")

    ORGANIZATION_CREATED = "organization_created", _("Organization created")
    ORGANIZATION_SETTINGS_CHANGED = "organization_settings_changed", _("Organization settings changed")
    MEMBER_ADDED = "member_added", _("Member added")
    MEMBER_REMOVED = "member_removed", _("Member removed")
    BOOKS_EXPORTED = "books_exported", _("Books exported")
    ORGANIZATION_DELETED = "organization_deleted", _("Organization deleted")

    PLAN_CHANGED = "plan_changed", _("Subscription plan changed")
    PAYMENT_CONFIRMED_MANUALLY = "payment_confirmed_manually", _("Payment confirmed manually")

    ENCRYPTION_KEYS_ROTATED = "encryption_keys_rotated", _("Encryption keys rotated")

    @property
    def category(self) -> AuditCategory:
        return ACTION_CATEGORY[self]


A = AuditAction
ACTION_CATEGORY = {
    **dict.fromkeys(
        [A.LOGIN_SUCCEEDED, A.LOGIN_FAILED, A.ACCOUNT_LOCKED, A.PASSWORD_CHANGED, A.PASSWORD_RESET, A.EMAIL_CHANGED,
         A.TWO_FACTOR_ENABLED, A.TWO_FACTOR_DISABLED, A.BACKUP_CODES_REGENERATED, A.BACKUP_CODE_USED,
         A.DEVICE_REMEMBERED, A.DEVICES_FORGOTTEN],
        AuditCategory.ACCOUNT,
    ),
    **dict.fromkeys(
        [A.ORGANIZATION_CREATED, A.ORGANIZATION_SETTINGS_CHANGED, A.MEMBER_ADDED, A.MEMBER_REMOVED,
         A.BOOKS_EXPORTED, A.ORGANIZATION_DELETED],
        AuditCategory.ORGANIZATION,
    ),
    **dict.fromkeys([A.PLAN_CHANGED, A.PAYMENT_CONFIRMED_MANUALLY], AuditCategory.BILLING),
    A.ENCRYPTION_KEYS_ROTATED: AuditCategory.SYSTEM,
}


# Kept for ORGANIZATION_LOG_RETENTION_DAYS (they document changes to the books, which are
# kept 10 years); every other category for SECURITY_LOG_RETENTION_DAYS.
KEPT_LONGER = frozenset({AuditCategory.ORGANIZATION, AuditCategory.BILLING})


def actions_in(category: AuditCategory) -> list[AuditAction]:
    return [action for action, action_category in ACTION_CATEGORY.items() if action_category == category]
