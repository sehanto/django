"""Xendit adapter against a recording HTTP double (no network)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
import requests

from apps.billing.gateways.base import CheckoutRequest, GatewayError, PaymentStatus
from apps.billing.gateways.xendit import XenditGateway


class FakeResponse:
    def __init__(self, status_code, data):
        self.status_code, self._data, self.text = status_code, data, str(data)

    def json(self):
        return self._data


class RecordingHttp:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def gateway(*responses):
    http = RecordingHttp(*responses)
    return XenditGateway(secret_key="xnd_development_secret", callback_token="tok-123", http=http), http


def checkout_request(return_url="https://app.example.com/billing/invoices/1/"):
    return CheckoutRequest(
        reference="SUB-202610-000001", amount=Decimal("109890.00"), description="Usaha", locale="id",
        expires_at=datetime(2026, 10, 13, 10, 0, tzinfo=UTC), return_url=return_url, metadata={"invoice": "SUB-202610-000001"},
    )


def test_create_checkout_calls_payment_sessions_api():
    gw, http = gateway(FakeResponse(201, {
        "payment_session_id": "ps-661f87c614802d6c402cd82d", "payment_link_url": "https://xen.to/abc",
        "expires_at": "2026-10-13T10:00:00Z", "status": "ACTIVE",
    }))
    checkout = gw.create_checkout(checkout_request())
    method, url, kwargs = http.calls[0]
    assert (method, url) == ("POST", "https://api.xendit.co/sessions")
    assert kwargs["auth"] == ("xnd_development_secret", "")  # secret key as username, empty password
    body = kwargs["json"]
    assert body["session_type"] == "PAY" and body["mode"] == "PAYMENT_LINK"
    assert (body["amount"], body["currency"], body["country"]) == (109890, "IDR", "ID")
    assert body["reference_id"] == "SUB-202610-000001"
    assert body["success_return_url"] == body["cancel_return_url"] == "https://app.example.com/billing/invoices/1/"
    assert checkout.gateway_reference == "ps-661f87c614802d6c402cd82d"
    assert checkout.payment_url == "https://xen.to/abc"


def test_http_return_urls_are_omitted_because_xendit_requires_https():
    gw, http = gateway(FakeResponse(201, {"payment_session_id": "ps-x", "payment_link_url": "https://xen.to/x"}))
    gw.create_checkout(checkout_request(return_url="http://localhost:8000/billing/invoices/1/"))
    assert "success_return_url" not in http.calls[0][2]["json"]


@pytest.mark.parametrize(("api_status", "expected"), [
    ("ACTIVE", PaymentStatus.PENDING), ("COMPLETED", PaymentStatus.PAID),
    ("EXPIRED", PaymentStatus.EXPIRED), ("CANCELED", PaymentStatus.CANCELED),
])
def test_fetch_payment_maps_session_status(api_status, expected):
    gw, http = gateway(FakeResponse(200, {"status": api_status, "amount": 109890, "payment_id": "py-1"}))
    payment = gw.fetch_payment("ps-661f87c614802d6c402cd82d")
    assert http.calls[0][:2] == ("GET", "https://api.xendit.co/sessions/ps-661f87c614802d6c402cd82d")
    assert payment.status == expected and payment.amount == Decimal("109890")


@pytest.mark.parametrize("failure", [FakeResponse(401, {"error_code": "INVALID_API_KEY"}), requests.ConnectionError("down")])
def test_errors_become_gateway_errors(failure):
    gw, _ = gateway(failure)
    with pytest.raises(GatewayError):
        gw.fetch_payment("ps-x")


def test_webhook_token_and_references():
    gw, _ = gateway()
    assert gw.verify_webhook({"x-callback-token": "tok-123"})
    assert not gw.verify_webhook({"x-callback-token": "wrong"})
    assert not gw.verify_webhook({})
    payload = {"event": "payment_session.completed", "data": {"payment_session_id": "ps-1", "reference_id": "SUB-1"}}
    assert set(gw.webhook_references(payload)) == {"ps-1", "SUB-1"}


def test_missing_configuration_fails_fast():
    with pytest.raises(ValueError):
        XenditGateway(secret_key="", callback_token="")
