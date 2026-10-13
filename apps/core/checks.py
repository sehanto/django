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


@register(Tags.security, deploy=True)
def check_captcha_keys(app_configs, **kwargs):
    """Cloudflare's test keys accept every token, so the login CAPTCHA would stop nothing."""
    if settings.CAPTCHA_BACKEND != "turnstile":
        return [Warning("The login CAPTCHA is not using Turnstile.", hint="Set CAPTCHA_BACKEND=turnstile.", id="core.W002")]
    keys = (settings.TURNSTILE_SITE_KEY or "", settings.TURNSTILE_SECRET_KEY or "")
    if any(key.startswith(settings.TURNSTILE_TEST_KEY_PREFIXES) for key in keys):
        return [
            Warning(
                "The login CAPTCHA uses Cloudflare's Turnstile test keys, which accept any visitor.",
                hint="Set TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY to the keys of your Turnstile widget.",
                id="core.W002",
            )
        ]
    return []


PLACEHOLDER_WORDS = ("contoh", "example", "placeholder", "dummy", "change_me", "changeme")


def _seller_problems() -> list[str]:
    from apps.contacts.validators import normalize_npwp  # checks load early; import at use

    problems = []
    name = (settings.BILLING_SELLER_NAME or "").strip()
    npwp = normalize_npwp(settings.BILLING_SELLER_NPWP)
    address = (settings.BILLING_SELLER_ADDRESS or "").strip()
    if not name:
        problems.append("BILLING_SELLER_NAME is empty")
    if len(npwp) not in (15, 16):
        problems.append("BILLING_SELLER_NPWP is not a 15- or 16-digit NPWP")
    elif len(set(npwp)) == 1:
        problems.append(f"BILLING_SELLER_NPWP looks like a placeholder ({npwp})")
    if not address:
        problems.append("BILLING_SELLER_ADDRESS is empty")
    elif any(word in address.lower() for word in PLACEHOLDER_WORDS):
        problems.append("BILLING_SELLER_ADDRESS looks like a placeholder")
    return problems


@register(Tags.security, deploy=True)
def check_billing_seller(app_configs, **kwargs):
    """Subscription receipts print the seller's identity; it must be the company's real one."""
    problems = _seller_problems()
    if not problems:
        return []
    return [
        Warning(
            "Subscription receipts would show missing or placeholder seller details: " + "; ".join(problems) + ".",
            hint="Set BILLING_SELLER_NAME, BILLING_SELLER_NPWP and BILLING_SELLER_ADDRESS to the company's "
            "registered name, NPWP and address.",
            id="core.W003",
        )
    ]


@register(Tags.caches, Tags.security, deploy=True)
def check_cache_answers(app_configs, **kwargs):
    """Rate limits and CAPTCHA triggers live in the cache: it must actually store values."""
    import secrets

    from django.core.cache import cache

    probe = secrets.token_hex(8)
    try:
        cache.set("bukuumkm:deploy-check", probe, 30)
        works = cache.get("bukuumkm:deploy-check") == probe
        cache.delete("bukuumkm:deploy-check")
    except Exception as exc:  # unreachable server, missing table, ...
        works, reason = False, f"{type(exc).__name__}: {exc}"
    else:
        reason = "it did not return a value it just stored"
    if works:
        return []
    return [
        Warning(
            f"The cache does not work ({reason}), so sign-in rate limits and CAPTCHA triggers can't count.",
            hint="Check CACHE_URL; for the database cache run manage.py createcachetable (as the owner).",
            id="core.W004",
        )
    ]
