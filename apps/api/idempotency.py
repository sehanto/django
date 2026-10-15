"""API writes are all or nothing, and repeatable with an ``Idempotency-Key``.

A client that times out doesn't know whether its invoice, payment or journal entry was recorded.
With an ``Idempotency-Key`` header (any 1-100 of ``A-Z a-z 0-9 _ . : -``, e.g. a UUID), the first
successful response is kept for ``WINDOW``; the same request again gets that response back (with
``Idempotent-Replayed: true``) and records nothing, and the same key with another request is a 422.
Every write runs in one transaction that an error response rolls back, so a failed request leaves
nothing behind (e.g. no draft when issuing the invoice fails) and may simply be sent again.
"""

import hashlib
import re
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone

from .models import IdempotentRequest

WINDOW = timedelta(hours=24)
HEADER = "Idempotency-Key"
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")


def _error(message: str, status: int) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def fingerprint(request) -> str:
    return hashlib.sha256(f"{request.method} {request.path}\n".encode() + request.body).hexdigest()


def write(request, api_key, run) -> HttpResponse:
    """Run the write ``run()`` in one transaction, once per ``Idempotency-Key``."""
    key = request.headers.get(HEADER)
    if key is not None and not KEY_PATTERN.match(key):
        return _error(f"'{HEADER}' must be 1 to 100 letters, digits or _ . : - (e.g. a UUID).", 400)
    with transaction.atomic():
        if key is not None and (earlier := _claim(api_key, key, fingerprint(request))) is not None:
            return earlier
        response = run()
        if response.status_code >= 400:
            transaction.set_rollback(True)  # nothing of a failed request is kept, its key neither
        elif key is not None:
            IdempotentRequest.objects.filter(api_key=api_key, key=key).update(status=response.status_code, body=response.content.decode())
        return response


def _claim(api_key, key: str, request_fingerprint: str) -> HttpResponse | None:
    """Reserve the key for this request (None), or answer with the request that already used it."""
    IdempotentRequest.objects.filter(api_key=api_key, created_at__lt=timezone.now() - WINDOW).delete()
    try:
        with transaction.atomic():  # waits while a concurrent request holds the key, then fails if it succeeded
            IdempotentRequest.objects.create(api_key=api_key, key=key, fingerprint=request_fingerprint)
        return None
    except IntegrityError:
        pass
    earlier = IdempotentRequest.objects.get(api_key=api_key, key=key)
    if earlier.fingerprint != request_fingerprint:
        return _error(f"This {HEADER} was already used for a different request.", 422)
    replay = HttpResponse(earlier.body, status=earlier.status, content_type="application/json")
    replay["Idempotent-Replayed"] = "true"
    return replay
