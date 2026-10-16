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
    PERIOD_REOPENED = "period_reopened", _("Locked period reopened")
    BUSINESS_PLACE_SAVED = "business_place_saved", _("Place of business saved")
    MEMBER_ADDED = "member_added", _("Member added")
    MEMBER_REMOVED = "member_removed", _("Member removed")
    MEMBER_INVITED = "member_invited", _("Member invited")
    INVITATION_REVOKED = "invitation_revoked", _("Invitation revoked")
    API_KEY_CREATED = "api_key_created", _("API key created")
    API_KEY_REVOKED = "api_key_revoked", _("API key revoked")
    CORETAX_CONNECTION_CHANGED = "coretax_connection_changed", _("Coretax connection changed")
    BOOKS_EXPORTED = "books_exported", _("Books exported")
    FILE_UPLOADED = "file_uploaded", _("File uploaded into the books")
    ACCOUNT_CREATED = "account_created", _("Account added to the chart of accounts")
    ACCOUNT_CHANGED = "account_changed", _("Chart of accounts entry changed")
    ACCOUNT_DELETED = "account_deleted", _("Account removed from the chart of accounts")
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
        [A.ORGANIZATION_CREATED, A.ORGANIZATION_SETTINGS_CHANGED, A.PERIOD_REOPENED, A.BUSINESS_PLACE_SAVED, A.MEMBER_ADDED, A.MEMBER_REMOVED, A.MEMBER_INVITED, A.INVITATION_REVOKED, A.API_KEY_CREATED, A.API_KEY_REVOKED, A.CORETAX_CONNECTION_CHANGED,
         A.BOOKS_EXPORTED, A.FILE_UPLOADED, A.ORGANIZATION_DELETED, A.ACCOUNT_CREATED, A.ACCOUNT_CHANGED, A.ACCOUNT_DELETED],
        AuditCategory.ORGANIZATION,
    ),
    **dict.fromkeys([A.PLAN_CHANGED, A.PAYMENT_CONFIRMED_MANUALLY], AuditCategory.BILLING),
    A.ENCRYPTION_KEYS_ROTATED: AuditCategory.SYSTEM,
}


# Kept for ORGANIZATION_LOG_RETENTION_DAYS: they document changes to the books (kept 10
# years) or to how their data is protected (key rotation). Account security events follow
# SECURITY_LOG_RETENTION_DAYS.
KEPT_LONGER = frozenset({AuditCategory.ORGANIZATION, AuditCategory.BILLING, AuditCategory.SYSTEM})


def actions_in(category: AuditCategory) -> list[AuditAction]:
    return [action for action, action_category in ACTION_CATEGORY.items() if action_category == category]
