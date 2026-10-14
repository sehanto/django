"""Account use cases: email verification and email changes."""

import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.core.exceptions import ValidationFailed

from apps.audit.domain import AuditAction

from . import throttle

logger = logging.getLogger(__name__)
User = get_user_model()
SALT = "accounts.email-verification"


def verification_address(user) -> str:
    """Where the confirmation link goes: a corrected address waiting for confirmation, or the account's."""
    return user.pending_email or user.email


def make_verification_token(user) -> str:
    """Signed, timestamped token naming the user *and* the address being verified,
    so it stops working if the email changes."""
    return signing.dumps({"user": user.pk, "email": verification_address(user)}, salt=SALT)


def _other_account(email: str, user=None):
    accounts = User.objects.filter(email__iexact=email)
    return (accounts.exclude(pk=user.pk) if user is not None else accounts).first()


def send_account_exists_notice(account) -> bool:
    """Tell an account holder that someone tried to sign up with, or switch to, their address
    (instead of telling that someone the address is taken). Throttled per address."""
    if not throttle.allow(throttle.ThrottleScope.ACCOUNT_NOTICE, account.email.lower(),
                          limit=settings.ACCOUNT_NOTICE_EMAILS_PER_ADDRESS_PER_HOUR, window_seconds=3600):
        logger.warning("Account notice throttled for user %s", account.pk)
        return False
    site = settings.SITE_URL.rstrip("/")
    context = {"user": account, "site_name": settings.SITE_NAME, "login_url": site + reverse("accounts:login"),
               "reset_url": site + reverse("accounts:password_reset")}
    with translation.override(account.language):
        subject = "".join(render_to_string("accounts/emails/account_exists_subject.txt", context).splitlines())
        body = render_to_string("accounts/emails/account_exists.txt", context)
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [account.email])
    return True


def send_verification_email(user) -> bool:
    """Email a verification link (in the user's language). Returns False when throttled."""
    if not throttle.allow(throttle.ThrottleScope.EMAIL_VERIFICATION, str(user.pk), limit=settings.EMAIL_VERIFICATION_EMAILS_PER_HOUR, window_seconds=3600):
        logger.warning("Verification email throttled for user %s", user.pk)
        return False
    address = verification_address(user)
    if address != user.email and (owner := _other_account(address, user)) is not None:
        send_account_exists_notice(owner)  # the address is taken: its owner hears of it, nobody else
        return True
    link = settings.SITE_URL.rstrip("/") + reverse("accounts:verify_email", args=[make_verification_token(user)])
    context = {
        "user": user, "link": link, "site_name": settings.SITE_NAME,
        "lifetime": timedelta(seconds=settings.EMAIL_VERIFICATION_TIMEOUT),
    }
    with translation.override(user.language):
        subject = "".join(render_to_string("accounts/emails/verify_email_subject.txt", context).splitlines())
        body = render_to_string("accounts/emails/verify_email.txt", context)
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [address])
    return True


class InvalidVerificationLink(ValidationFailed):
    pass


@transaction.atomic
def verify_email(token: str) -> tuple:
    """Mark the address in ``token`` as verified; a corrected address replaces the old one
    now. Returns (user, the previous address when it changed, else "")."""
    try:
        data = signing.loads(token, salt=SALT, max_age=settings.EMAIL_VERIFICATION_TIMEOUT)
    except signing.SignatureExpired as exc:
        raise InvalidVerificationLink(_("This confirmation link has expired. Request a new one.")) from exc
    except signing.BadSignature as exc:
        raise InvalidVerificationLink(_("This confirmation link is not valid.")) from exc
    user = User.objects.select_for_update().filter(pk=data.get("user"), is_active=True).first()
    address = data.get("email") or ""
    # Only the address waiting for confirmation: after a correction, the link sent to the typo is dead.
    if user is None or not address or address != verification_address(user):
        raise InvalidVerificationLink(_("This confirmation link is not valid."))
    previous = ""
    if address != user.email:  # the corrected address: it may have been taken since
        if _other_account(address, user) is not None:
            raise InvalidVerificationLink(_("This confirmation link is not valid."))
        previous, user.email = user.email, address
    user.pending_email = ""
    user.email_verified_at = user.email_verified_at or timezone.now()
    user.save(update_fields=["email", "pending_email", "email_verified_at"])
    return user, previous


def mark_email_verified(user) -> None:
    """For flows that already prove control of the mailbox (e.g. password reset)."""
    if not user.is_email_verified:
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at"])


@transaction.atomic
def change_unverified_email(*, user, new_email: str):
    """Fix a mistyped address before it was ever verified: the link goes to the new address,
    which replaces the old one when it is opened. Whether another account has the address is
    never said here; if one does, its owner is told instead (``send_verification_email``)."""
    new_email = new_email.strip().lower()
    if user.is_email_verified:
        raise ValidationFailed(_("A confirmed email address can't be changed here."))
    user.pending_email = "" if new_email == user.email.lower() else new_email
    user.save(update_fields=["pending_email"])
    transaction.on_commit(lambda: send_verification_email(user))
    return user


SECURITY_EVENTS = {  # emailed to the account holder; also recorded in the audit log
    AuditAction.TWO_FACTOR_ENABLED: gettext_lazy("Two-factor authentication was turned on for your account."),
    AuditAction.TWO_FACTOR_DISABLED: gettext_lazy("Two-factor authentication was turned off for your account."),
    AuditAction.BACKUP_CODES_REGENERATED: gettext_lazy("New two-factor backup codes were created for your account. The old codes no longer work."),
    AuditAction.DEVICE_REMEMBERED: gettext_lazy(
        "A device was remembered for two-factor sign-in and won't be asked for a code for %(days)s days: %(device)s."
    ),
}


def send_security_notice(user, event: AuditAction, **params) -> None:
    """Tell the account holder about a security change, so an attacker can't make one silently."""
    with translation.override(user.language):
        event_text = str(SECURITY_EVENTS[event]) % params if params else str(SECURITY_EVENTS[event])
    context = {"user": user, "site_name": settings.SITE_NAME, "event": event_text,
               "when": timezone.localtime(), "security_url": settings.SITE_URL.rstrip("/") + reverse("accounts:security")}
    with translation.override(user.language):
        subject = "".join(render_to_string("accounts/emails/security_notice_subject.txt", context).splitlines())
        body = render_to_string("accounts/emails/security_notice.txt", context)
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email])
