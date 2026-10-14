import secrets
import time
from base64 import b32encode
from urllib.parse import quote, urlencode

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_otp.models import Device, ThrottlingMixin, TimestampMixin
from django_otp.oath import TOTP

from apps.core import crypto
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
    # A corrected address waiting for its link to be opened: it replaces ``email`` only then, so
    # choosing an address never tells whether another account has it.
    pending_email = models.EmailField(_("new email, not yet confirmed"), blank=True)

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


# --- Two-factor devices with secrets protected at rest -------------------------------------
# Same behaviour as django-otp's TOTPDevice / StaticDevice (which store secrets in plain
# text), built on django-otp's Device API so match_token, throttling, session
# verification and OTPAdminSite work unchanged.


class AuthenticatorDevice(TimestampMixin, ThrottlingMixin, Device):
    """Authenticator app (TOTP, RFC 6238). The shared secret is stored Fernet-encrypted."""

    encrypted_key = models.TextField()
    step = models.PositiveSmallIntegerField(default=30)
    t0 = models.BigIntegerField(default=0)
    digits = models.PositiveSmallIntegerField(default=6)
    tolerance = models.PositiveSmallIntegerField(default=1)
    drift = models.SmallIntegerField(default=0)
    last_t = models.BigIntegerField(default=-1)  # replay protection: next code must be newer

    class Meta(Device.Meta):
        verbose_name = _("authenticator app")
        verbose_name_plural = _("authenticator apps")

    @classmethod
    def new(cls, user, **fields) -> "AuthenticatorDevice":
        device = cls(user=user, **fields)
        device.set_key(secrets.token_bytes(20))  # 160-bit secret, as recommended by RFC 4226
        return device

    def set_key(self, key: bytes) -> None:
        self.encrypted_key = crypto.encrypt(key)

    @property
    def bin_key(self) -> bytes:
        return crypto.decrypt(self.encrypted_key)

    def verify_token(self, token) -> bool:
        verify_allowed, _ = self.verify_is_allowed()
        if not verify_allowed:
            return False
        try:
            token = int(token)
        except (TypeError, ValueError):
            verified = False
        else:
            totp = TOTP(self.bin_key, self.step, self.t0, self.digits, self.drift)
            totp.time = time.time()
            verified = totp.verify(token, self.tolerance, self.last_t + 1)
            if verified:
                self.last_t = totp.t()
                if getattr(settings, "OTP_TOTP_SYNC", True):
                    self.drift = totp.drift
                self.throttle_reset(commit=False)
                self.set_last_used_timestamp(commit=False)
                self.save()
        if not verified:
            self.throttle_increment(commit=True)
        return verified

    def get_throttle_factor(self):
        return getattr(settings, "OTP_TOTP_THROTTLE_FACTOR", 1)

    @property
    def config_url(self) -> str:
        """otpauth:// URL for the QR code (Key Uri Format)."""
        issuer = settings.OTP_TOTP_ISSUER.replace(":", "")
        label = f"{issuer}:{self.user.get_username()}"
        params = urlencode({"secret": b32encode(self.bin_key), "algorithm": "SHA1", "digits": self.digits, "period": self.step})
        return f"otpauth://totp/{quote(label)}?{params}&issuer={quote(issuer)}"


class BackupCodeDevice(TimestampMixin, ThrottlingMixin, Device):
    """Single-use backup codes, stored only as keyed hashes (HMAC-SHA256 with a secret key)."""

    class Meta(Device.Meta):
        verbose_name = _("backup codes")
        verbose_name_plural = _("backup codes")

    def verify_token(self, token) -> bool:
        verify_allowed, _ = self.verify_is_allowed()
        if not verify_allowed:
            return False
        match = self.codes.filter(code_hash__in=crypto.keyed_hash_candidates(str(token))).first()
        if match is None:
            self.throttle_increment()
            return False
        match.delete()  # single use
        self.throttle_reset(commit=False)
        self.set_last_used_timestamp(commit=False)
        self.save()
        return True

    def get_throttle_factor(self):
        return getattr(settings, "OTP_STATIC_THROTTLE_FACTOR", 1)


class BackupCode(models.Model):
    device = models.ForeignKey(BackupCodeDevice, on_delete=models.CASCADE, related_name="codes")
    code_hash = models.CharField(max_length=64, db_index=True)
