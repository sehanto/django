"""Sign-in events come from Django's and django-axes' signals, so every login path is covered
(the site, the admin, the two-factor step)."""

from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_login_failed

from .domain import AuditAction
from .services import record


def _user_with_email(email) -> object | None:
    if not email:
        return None
    return get_user_model().objects.filter(email__iexact=str(email).strip()).first()


def on_login(sender, request, user, **kwargs):
    record(AuditAction.LOGIN_SUCCEEDED, request=request, actor=user)


def on_login_failed(sender, credentials, request=None, **kwargs):
    email = (credentials or {}).get("username", "")  # Django strips the password from credentials
    record(AuditAction.LOGIN_FAILED, request=request, actor=_user_with_email(email), target=email, step="password")


def on_locked_out(sender, request=None, username=None, ip_address=None, **kwargs):
    record(AuditAction.ACCOUNT_LOCKED, request=request, actor=_user_with_email(username), target=username or "")


def connect():
    from axes.signals import user_locked_out

    user_logged_in.connect(on_login, dispatch_uid="audit.login")
    user_login_failed.connect(on_login_failed, dispatch_uid="audit.login_failed")
    user_locked_out.connect(on_locked_out, dispatch_uid="audit.locked_out")
