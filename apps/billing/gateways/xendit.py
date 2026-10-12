"""Xendit Payment Sessions adapter (hosted checkout, ``mode=PAYMENT_LINK``).

API: https://docs.xendit.co/apidocs/create-session and /apidocs/get-session.
Webhooks are treated as triggers only: after verifying ``x-callback-token`` we
re-fetch the session and trust its status and amount, never the webhook body.
"""

import hmac
import logging
from decimal import Decimal

import requests
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _

from .base import Checkout, CheckoutRequest, GatewayError, GatewayPayment, PaymentStatus

logger = logging.getLogger(__name__)

STATUS_MAP = {
    "ACTIVE": PaymentStatus.PENDING,
    "COMPLETED": PaymentStatus.PAID,
    "EXPIRED": PaymentStatus.EXPIRED,
    "CANCELED": PaymentStatus.CANCELED,
}
TIMEOUT_SECONDS = 15


class XenditGateway:
    name = "xendit"

    def __init__(self, *, secret_key: str, callback_token: str, api_url: str = "https://api.xendit.co", http=None):
        if not secret_key or not callback_token:
            raise ValueError("XENDIT_SECRET_KEY and XENDIT_CALLBACK_TOKEN must be set.")
        self._callback_token = callback_token
        self._api_url = api_url.rstrip("/")
        self._http = http or requests.Session()
        self._auth = (secret_key, "")  # Xendit: secret key as Basic-auth username, empty password

    def _call(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self._http.request(method, self._api_url + path, auth=self._auth, timeout=TIMEOUT_SECONDS, **kwargs)
        except requests.RequestException as exc:
            logger.warning("Xendit %s %s failed: %s", method, path, exc)
            raise GatewayError(_("The payment provider is not reachable right now. Please try again.")) from exc
        if response.status_code >= 400:
            logger.error("Xendit %s %s returned %s: %s", method, path, response.status_code, response.text[:500])
            raise GatewayError(_("The payment provider rejected the request. Please try again later."))
        return response.json()

    def create_checkout(self, request: CheckoutRequest) -> Checkout:
        body = {
            "reference_id": request.reference,
            "session_type": "PAY",
            "mode": "PAYMENT_LINK",
            "amount": int(request.amount),  # IDR has no minor unit
            "currency": "IDR",
            "country": "ID",
            "description": request.description,
            "expires_at": request.expires_at.isoformat(),
            "locale": request.locale,
            "metadata": request.metadata,
        }
        if request.return_url.startswith("https://"):  # Xendit only accepts HTTPS return URLs
            body["success_return_url"] = body["cancel_return_url"] = request.return_url
        data = self._call("POST", "/sessions", json=body)
        if not data.get("payment_link_url"):
            raise GatewayError(_("The payment provider did not return a payment link."))
        return Checkout(
            gateway_reference=data["payment_session_id"],
            payment_url=data["payment_link_url"],
            expires_at=parse_datetime(data["expires_at"]) if data.get("expires_at") else None,
        )

    def fetch_payment(self, gateway_reference: str) -> GatewayPayment:
        data = self._call("GET", f"/sessions/{gateway_reference}")
        status = STATUS_MAP.get(data.get("status"))
        if status is None:
            raise GatewayError(_("Unknown payment status from the payment provider."))
        return GatewayPayment(status=status, amount=Decimal(str(data.get("amount", 0))), payment_id=data.get("payment_id") or "")

    def verify_webhook(self, headers) -> bool:
        token = headers.get("x-callback-token", "")
        return hmac.compare_digest(token.encode(), self._callback_token.encode())

    def webhook_references(self, payload: dict) -> list[str]:
        # The session webhook payload shape is not fully documented, so look in the likely places.
        candidates = [payload, payload.get("data") or {}]
        keys = ("payment_session_id", "reference_id", "id")
        return [str(c[k]) for c in candidates if isinstance(c, dict) for k in keys if c.get(k)]
