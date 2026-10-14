from .base import *  # noqa: F403

DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
}
MIDDLEWARE.remove("whitenoise.middleware.WhiteNoiseMiddleware")  # noqa: F405 - runserver serves static
BILLING_GATEWAY = "fake"
CAPTCHA_BACKEND = "fake"  # accepts only apps.accounts.captcha.FAKE_PASSING_TOKEN
ACCOUNT_RESPONSE_MIN_SECONDS = 0.0
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# The test runner creates and migrates its own database, which needs the owner role.
if DATABASE_OWNER:  # noqa: F405
    DATABASES["default"].update({key: DATABASE_OWNER[key] for key in ("USER", "PASSWORD", "HOST", "PORT")})  # noqa: F405
    DATABASE_OWNER = None
    DATABASE_APP_ROLE = None
