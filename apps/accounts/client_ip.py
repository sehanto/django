"""Client IP resolution for login rate limiting (django-axes ``AXES_CLIENT_IP_CALLABLE``).

Behind reverse proxies, ``REMOTE_ADDR`` is the proxy, so every visitor would share
one IP. Each trusted proxy appends the address it received the request from to
``X-Forwarded-For``; with N trusted proxies the real client is therefore the N-th
entry from the right. Entries further left come from the client and can be forged,
so they are never used. Set ``TRUSTED_PROXY_COUNT`` to the number of proxies you
operate (0 when Django is reached directly).
"""

import ipaddress

from django.conf import settings


def _valid_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def login_username(request, credentials) -> str:
    """The identity axes counts failures for: emails are case-insensitive here."""
    value = (credentials or {}).get(settings.AXES_USERNAME_FORM_FIELD) or request.POST.get(settings.AXES_USERNAME_FORM_FIELD, "")
    return str(value).strip().lower()


def client_ip(request) -> str | None:
    remote_addr = _valid_ip(request.META.get("REMOTE_ADDR", "") or "")
    proxies = settings.TRUSTED_PROXY_COUNT
    if proxies <= 0:
        return remote_addr
    forwarded = [part for part in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if part.strip()]
    if len(forwarded) < proxies:
        return remote_addr  # request did not pass through all our proxies
    return _valid_ip(forwarded[-proxies]) or remote_addr
