from django.contrib.auth.signals import user_logged_in, user_login_failed

from . import captcha


def on_login_failed(sender, credentials, request=None, **kwargs):
    captcha.record_failure(request, (credentials or {}).get("username"))


def on_login(sender, request, user, **kwargs):
    captcha.record_success(user.email)


def connect():
    user_login_failed.connect(on_login_failed, dispatch_uid="accounts.captcha_failure")
    user_logged_in.connect(on_login, dispatch_uid="accounts.captcha_success")
