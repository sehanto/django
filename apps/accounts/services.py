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

from . import throttle

logger = logging.getLogger(__name__)
User = get_user_model()
SALT = "accounts.email-verification"


def make_verification_token(user) -> str:
    """Signed, timestamped token naming the user *and* the address being verified,
    so it stops working if the email changes."""
    return signing.dumps({"user": user.pk, "email": user.email}, salt=SALT)


def send_verification_email(user) -> bool:
    """Email a verification link (in the user's language). Returns False when throttled."""
    if not throttle.allow("email-verification", str(user.pk), limit=settings.EMAIL_VERIFICATION_EMAILS_PER_HOUR, window_seconds=3600):
        logger.warning("Verification email throttled for user %s", user.pk)
        return False
    link = settings.SITE_URL.rstrip("/") + reverse("accounts:verify_email", args=[make_verification_token(user)])
    context = {
        "user": user, "link": link, "site_name": settings.SITE_NAME,
        "lifetime": timedelta(seconds=settings.EMAIL_VERIFICATION_TIMEOUT),
    }
    with translation.override(user.language):
        subject = "".join(render_to_string("accounts/emails/verify_email_subject.txt", context).splitlines())
        body = render_to_string("accounts/emails/verify_email.txt", context)
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email])
    return True


class InvalidVerificationLink(ValidationFailed):
    pass


@transaction.atomic
def verify_email(token: str):
    """Mark the address in ``token`` as verified and return the user."""
    try:
        data = signing.loads(token, salt=SALT, max_age=settings.EMAIL_VERIFICATION_TIMEOUT)
    except signing.SignatureExpired as exc:
        raise InvalidVerificationLink(_("This confirmation link has expired. Request a new one.")) from exc
    except signing.BadSignature as exc:
        raise InvalidVerificationLink(_("This confirmation link is not valid.")) from exc
    user = User.objects.select_for_update().filter(pk=data.get("user"), is_active=True).first()
    if user is None or user.email != data.get("email"):
        raise InvalidVerificationLink(_("This confirmation link is not valid."))
    if not user.is_email_verified:
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at"])
    return user


def mark_email_verified(user) -> None:
    """For flows that already prove control of the mailbox (e.g. password reset)."""
    if not user.is_email_verified:
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at"])


@transaction.atomic
def change_unverified_email(*, user, new_email: str):
    """Fix a mistyped address before it was ever verified, then send a new link."""
    new_email = new_email.strip().lower()
    if user.is_email_verified:
        raise ValidationFailed(_("A confirmed email address can't be changed here."))
    if User.objects.filter(email__iexact=new_email).exclude(pk=user.pk).exists():
        raise ValidationFailed(_("This email is already registered."))
    user.email = new_email
    user.save(update_fields=["email"])
    transaction.on_commit(lambda: send_verification_email(user))
    return user


SECURITY_EVENTS = {
    "two_factor_enabled": gettext_lazy("Two-factor authentication was turned on for your account."),
    "two_factor_disabled": gettext_lazy("Two-factor authentication was turned off for your account."),
    "backup_codes_regenerated": gettext_lazy("New two-factor backup codes were created for your account. The old codes no longer work."),
    "device_remembered": gettext_lazy(
        "A device was remembered for two-factor sign-in and won't be asked for a code for %(days)s days: %(device)s."
    ),
}


def send_security_notice(user, event: str, **params) -> None:
    """Tell the account holder about a security change, so an attacker can't make one silently."""
    with translation.override(user.language):
        event_text = str(SECURITY_EVENTS[event]) % params if params else str(SECURITY_EVENTS[event])
    context = {"user": user, "site_name": settings.SITE_NAME, "event": event_text,
               "when": timezone.localtime(), "security_url": settings.SITE_URL.rstrip("/") + reverse("accounts:security")}
    with translation.override(user.language):
        subject = "".join(render_to_string("accounts/emails/security_notice_subject.txt", context).splitlines())
        body = render_to_string("accounts/emails/security_notice.txt", context)
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email])
