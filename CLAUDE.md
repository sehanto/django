# BukuUMKM

Django 5.2 + PostgreSQL multi-tenant accounting SaaS for Indonesian MSMEs. See README.md for architecture.

- Run tests: `.venv/bin/pytest` (settings: `config.settings.test`; uses `--reuse-db`, add `--create-db` after model changes)
- Dev server: `.venv/bin/python manage.py runserver`; demo data: `manage.py seed_demo`
- PostgreSQL 18; `msme_user` owns `msme_db` (required to create tables in `public`) and needs CREATEDB for the test DB.

Conventions:
- Business rules in `domain.py` (no ORM), writes in `services.py` (atomic, raise `apps.core.exceptions.DomainError`), reads in `selectors.py`. Views stay thin.
- Every tenant query goes through `Model.objects.for_organization(org)`; views get the org from `OrganizationMixin`/`WriteMixin`/`ManageMixin`.
- All ledger postings go through `ledger.services.create_journal_entry`. Never edit/delete posted entries; reverse them.
- Language policy is in README "Languages"; follow it. URLs are English and language-neutral; never put the language in the URL.
- Interface text: English source strings, always marked for translation (`gettext_lazy` for module-level/labels/choices, `gettext` for runtime messages, `{% translate %}`/`{% blocktranslate trimmed %}` in templates). Use `%(name)s` placeholders, whole sentences, `ngettext`/`{% blocktranslate count %}` for anything counted, `pgettext` for ambiguous short words. Then `makemessages -l id`, translate in `locale/id/LC_MESSAGES/django.po`, `compilemessages`, commit .po and .mo (a test enforces this).
- Fixed sets of values are enumerations, never bare strings: `models.TextChoices` for anything stored, shown or filtered (nested on the model, or in `domain.py` when rules need it, e.g. `InvoiceStatus`, `AgingBucket`), `StrEnum` for internal/external codes (`PaymentStatus`, `ThrottleScope`, Xendit `SessionStatus`). Validate request values with `choice_param(..., Enum.values)`. Templates compare against members (`invoice.status == invoice.Status.PAID`, `invoice_kinds.SALES` from the invoicing context processor, or an enum passed in the view context), never quoted values (a test enforces it).
- Money: `|rupiah` in templates, `apps.core.formatting.localized_rupiah` in messages (separators follow the active language). Customer-facing documents render in `Contact.document_language`.
- Stored business data (chart of accounts, journal descriptions, tax-rate/plan names) stays Indonesian (UU KUP Art. 28(4)) and is never translated.
- Plan changes only happen through `apps.billing` (payment confirmed by re-fetching the gateway, never by trusting a webhook body or redirect). `tenants.services.set_subscription` is the single writer of `Organization.plan`/`plan_paid_until`. Dev/tests use `BILLING_GATEWAY=fake`; schedule `manage.py process_billing` daily.
- Input handling: forms inherit `apps.core.forms.BaseForm`/`BaseModelForm` (sanitizes text; a test enforces it); passwords use `strip=False`. Put format rules on model fields (`apps.core.validators`), use `BusinessDateField` for transaction dates and `DigitsField` for NPWP/NIB-style identifiers. Read request parameters only via `apps.core.params` (`text_param`, `choice_param`, `int_param`, `year_param`). Never use `|safe`/`mark_safe`/raw SQL; pass CSV cells through `csv_safe`.
- Login is rate limited by django-axes (email+IP, see README). Tests must log users in with `client.force_login`, not `client.login()` (axes needs a request). `AXES_USERNAME_FORM_FIELD` must stay `"username"` (what Django's login form passes to `authenticate`).
- Password reset uses Django's views with project templates; links are built from `SITE_URL` (never the Host header). Throttles use `apps.accounts.throttle` on the shared cache. Emails to a user are rendered in `user.language`.
- Email verification is mandatory: `EmailVerificationMiddleware` redirects unverified users to `accounts:verify_email_pending`. Test users must be created with `email_verified_at=timezone.now()` (the shared fixtures do this). New views an unverified user must reach go into `EXEMPT_ACCOUNT_VIEWS`.
- 2FA uses django-otp (`apps/accounts/two_factor.py`); the login view stops after the password when 2FA is on (`two_factor.begin_login` → `accounts:login_otp`). The admin is an `OTPAdminSite`. In tests, compute codes with `django_otp.oath.TOTP` and let back-off pass by patching `django_otp.models.timezone.now`.
- `Organization.require_two_factor` is enforced by `apps.tenants.middleware.TwoFactorRequirementMiddleware` (checks `request.user.is_verified()`); views a member must reach before completing 2FA go in its `EXEMPT` set.
- Remembered devices (`apps/accounts/trusted_devices.py`) skip only the code step and are bound to `user.get_session_auth_hash()`. In tests sign out via the logout view, not `client.logout()` (which also wipes the remember cookie).
- 2FA devices are `accounts.AuthenticatorDevice` (Fernet-encrypted key) and `BackupCodeDevice`/`BackupCode` (keyed hashes); never use django-otp's `TOTPDevice`/`StaticDevice` (plaintext). Encryption/hashing goes through `apps.core.crypto` with `FIELD_ENCRYPTION_KEYS`.
- Plan switches are prorated in `apps.billing` (`switch_credit`, `switch_quote`): PPN on the net amount, free switches when credit covers the price, `superseded_on` prevents reusing credit. Billing tests that pay then switch must `refresh_from_db()` the organization.
- Tests must not rely on data migrations (transactional tests flush the DB); create needed rows in fixtures.
