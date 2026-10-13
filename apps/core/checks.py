from django.conf import settings
from django.core.checks import Tags, Warning, register
from django.db import DatabaseError, connection

from .db_roles import connected_role_can_alter_schema


@register(Tags.security, deploy=True)
def check_database_role(app_configs, **kwargs):
    """The app must not connect as a role that could disable the ledger triggers."""
    if connection.vendor != "postgresql":
        return []
    try:
        privileged = connected_role_can_alter_schema()
    except DatabaseError:
        return []  # database unreachable: other checks/commands will say so
    if not privileged:
        return []
    return [
        Warning(
            "The application connects to the database as a superuser or as the owner of the tables, "
            "so it could disable the ledger's immutability triggers.",
            hint="Point DATABASE_URL at a data-only role and DATABASE_OWNER_URL at the owner "
            "(README, 'Database roles'). "
            f"Connected as {settings.DATABASES['default']['USER']!r}.",
            id="core.W001",
        )
    ]


# Cloudflare's public test keys (always pass / always fail / forced challenge).
TURNSTILE_TEST_KEY_PREFIXES = ("1x0000", "2x0000", "3x0000")


@register(Tags.security, deploy=True)
def check_captcha_keys(app_configs, **kwargs):
    """Cloudflare's test keys accept every token, so the login CAPTCHA would stop nothing."""
    if settings.CAPTCHA_BACKEND != "turnstile":
        return [Warning("The login CAPTCHA is not using Turnstile.", hint="Set CAPTCHA_BACKEND=turnstile.", id="core.W002")]
    keys = (settings.TURNSTILE_SITE_KEY or "", settings.TURNSTILE_SECRET_KEY or "")
    if any(key.startswith(TURNSTILE_TEST_KEY_PREFIXES) for key in keys):
        return [
            Warning(
                "The login CAPTCHA uses Cloudflare's Turnstile test keys, which accept any visitor.",
                hint="Set TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY to the keys of your Turnstile widget.",
                id="core.W002",
            )
        ]
    return []
