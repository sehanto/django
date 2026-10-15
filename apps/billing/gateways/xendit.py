"""Xendit Payment Sessions adapter (hosted checkout, ``mode=PAYMENT_LINK``).

API: https://docs.xendit.co/apidocs/create-session and /apidocs/get-session.
Webhooks are treated as triggers only: after verifying ``x-callback-token`` we
re-fetch the session and trust its status and amount, never the webhook body.
"""

import hmac
import logging
from decimal import Decimal
from enum import StrEnum

import requests
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _

from .base import (
    ChargeRequest,
    Checkout,
    CheckoutRequest,
    GatewayError,
    GatewayName,
    GatewayPayment,
    PaymentStatus,
    SaveRequest,
)

logger = logging.getLogger(__name__)


class SessionStatus(StrEnum):
    """Payment Session statuses as Xendit reports them."""

    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    CANCELED = "CANCELED"


STATUS_MAP = {
    SessionStatus.ACTIVE: PaymentStatus.PENDING,
    SessionStatus.COMPLETED: PaymentStatus.PAID,
    SessionStatus.EXPIRED: PaymentStatus.EXPIRED,
    SessionStatus.CANCELED: PaymentStatus.CANCELED,
}
TIMEOUT_SECONDS = 15


class PaymentRequestStatus(StrEnum):
    """Payment Request (v3) statuses, for charges of a saved payment token."""

    PENDING = "PENDING"
    ACCEPTING_PAYMENTS = "ACCEPTING_PAYMENTS"
    REQUIRES_ACTION = "REQUIRES_ACTION"
    AUTHORIZED = "AUTHORIZED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"


REQUEST_STATUS_MAP = {
    PaymentRequestStatus.PENDING: PaymentStatus.PENDING,
    PaymentRequestStatus.ACCEPTING_PAYMENTS: PaymentStatus.PENDING,
    PaymentRequestStatus.REQUIRES_ACTION: PaymentStatus.PENDING,
    PaymentRequestStatus.AUTHORIZED: PaymentStatus.PENDING,
    PaymentRequestStatus.SUCCEEDED: PaymentStatus.PAID,
    PaymentRequestStatus.FAILED: PaymentStatus.FAILED,
    PaymentRequestStatus.CANCELED: PaymentStatus.CANCELED,
    PaymentRequestStatus.EXPIRED: PaymentStatus.EXPIRED,
}
PAYMENT_REQUEST_PREFIX = "pr-"


class XenditGateway:
    name = GatewayName.XENDIT

    def __init__(self, *, secret_key: str, callback_token: str, api_url: str = "https://api.xendit.co", http=None,
                 payment_request_api_version: str = "2024-11-11"):
        if not secret_key or not callback_token:
            raise ValueError("XENDIT_SECRET_KEY and XENDIT_CALLBACK_TOKEN must be set.")
        self._callback_token = callback_token
        self._api_url = api_url.rstrip("/")
        self._http = http or requests.Session()
        self._auth = (secret_key, "")  # Xendit: secret key as Basic-auth username, empty password
        self._request_headers = {"api-version": payment_request_api_version}

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
        if gateway_reference.startswith(PAYMENT_REQUEST_PREFIX):  # a charge of a saved token
            data = self._call("GET", f"/v3/payment_requests/{gateway_reference}", headers=self._request_headers)
            status = REQUEST_STATUS_MAP.get(data.get("status"))
            if status is None:
                raise GatewayError(_("Unknown payment status from the payment provider."))
            amount = data.get("request_amount", data.get("amount", 0))
            return GatewayPayment(status=status, amount=Decimal(str(amount)), payment_id=data.get("latest_payment_id") or gateway_reference)
        data = self._call("GET", f"/sessions/{gateway_reference}")
        status = STATUS_MAP.get(data.get("status"))
        if status is None:
            raise GatewayError(_("Unknown payment status from the payment provider."))
        return GatewayPayment(status=status, amount=Decimal(str(data.get("amount", 0))), payment_id=data.get("payment_id") or "")

    def create_save_session(self, request: SaveRequest) -> Checkout:
        """A Payment Session in SAVE mode: the customer saves a card or e-wallet for recurring
        charges; nothing is charged now (docs: payment sessions, "Save payment method")."""
        body = {
            "reference_id": request.reference,
            "session_type": "SAVE",
            "mode": "PAYMENT_LINK",
            "amount": 0,
            "currency": "IDR",
            "country": "ID",
            "description": request.description,
            "expires_at": request.expires_at.isoformat(),
            "locale": request.locale,
            "customer": {
                "reference_id": request.customer_reference, "type": "INDIVIDUAL", "email": request.customer_email,
                "individual_detail": {"given_names": request.customer_name or request.customer_email},
            },
            "channel_properties": {"cards": {"card_on_file_type": "RECURRING"}},
        }
        if request.return_url.startswith("https://"):
            body["success_return_url"] = body["cancel_return_url"] = request.return_url
        data = self._call("POST", "/sessions", json=body)
        if not data.get("payment_link_url"):
            raise GatewayError(_("The payment provider did not return a payment link."))
        return Checkout(
            gateway_reference=data["payment_session_id"], payment_url=data["payment_link_url"],
            expires_at=parse_datetime(data["expires_at"]) if data.get("expires_at") else None,
        )

    def fetch_saved_token(self, gateway_reference: str) -> str:
        data = self._call("GET", f"/sessions/{gateway_reference}")
        if data.get("status") != SessionStatus.COMPLETED:
            return ""
        return str(data.get("payment_token_id") or "")

    def charge(self, request: ChargeRequest) -> Checkout:
        """A merchant-initiated Payment Request with the saved token (docs: "Pay with tokens")."""
        data = self._call("POST", "/v3/payment_requests", headers=self._request_headers, json={
            "reference_id": request.reference,
            "type": "PAY",
            "country": "ID",
            "currency": "IDR",
            "request_amount": int(request.amount),
            "capture_method": "AUTOMATIC",
            "payment_token_id": request.payment_token,
            "card_on_file_type": "RECURRING",
            "description": request.description,
            "metadata": request.metadata,
        })
        if not str(data.get("payment_request_id", "")).startswith(PAYMENT_REQUEST_PREFIX):
            raise GatewayError(_("The payment provider did not accept the charge."))
        return Checkout(gateway_reference=data["payment_request_id"], payment_url="", expires_at=None)

    def verify_webhook(self, headers) -> bool:
        token = headers.get("x-callback-token", "")
        return hmac.compare_digest(token.encode(), self._callback_token.encode())

    def webhook_references(self, payload: dict) -> list[str]:
        # The session webhook payload shape is not fully documented, so look in the likely places.
        candidates = [payload, payload.get("data") or {}]
        keys = ("payment_session_id", "payment_request_id", "reference_id", "id")
        return [str(c[k]) for c in candidates if isinstance(c, dict) for k in keys if c.get(k)]
