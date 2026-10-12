from .base import *  # noqa: F403

DEBUG = True
STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
}
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
MIDDLEWARE.remove("whitenoise.middleware.WhiteNoiseMiddleware")  # noqa: F405 - runserver serves static
