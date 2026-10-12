from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.validators import phone_validator


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("email_verified_at", timezone.now())  # created by a trusted operator
        return self._create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(_("email"), unique=True)
    full_name = models.CharField(_("full name"), max_length=150)
    phone = models.CharField(_("phone number"), max_length=30, blank=True, validators=[phone_validator])
    language = models.CharField(
        _("interface language"), max_length=10, choices=settings.LANGUAGES, default=settings.LANGUAGE_CODE
    )
    is_active = models.BooleanField(_("active"), default=True)
    is_staff = models.BooleanField(_("staff status"), default=False)
    date_joined = models.DateTimeField(_("date joined"), default=timezone.now)
    email_verified_at = models.DateTimeField(_("email verified at"), null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")

    def __str__(self):
        return self.full_name or self.email

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else self.email


class TrustedDevice(models.Model):
    """A browser where the user chose "don't ask for a code again" after 2FA.

    Only a SHA-256 hash of the cookie secret is stored. ``password_fingerprint``
    ties the record to the current password, so any password change forgets it.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="trusted_devices")
    token_hash = models.CharField(max_length=64, unique=True)
    password_fingerprint = models.CharField(max_length=128)
    label = models.CharField(_("device"), max_length=100)
    created_at = models.DateTimeField(_("remembered on"), auto_now_add=True)
    last_used_at = models.DateTimeField(_("last used"), null=True, blank=True)
    expires_at = models.DateTimeField(_("expires"))

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("remembered device")
        verbose_name_plural = _("remembered devices")

    def __str__(self):
        return f"{self.user} · {self.label}"
