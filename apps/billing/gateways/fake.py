"""In-process stand-in for a real gateway: used by tests and by local development
until Xendit keys are configured. Payments are simulated on our own checkout page."""

from decimal import Decimal

from django.core.cache import cache
from django.urls import reverse

from .base import ChargeRequest, Checkout, CheckoutRequest, GatewayName, GatewayPayment, PaymentStatus, SaveRequest

DECLINED_TOKEN = "fake-token-declined"  # charging it fails, for tests

CACHE_PREFIX = "fake-gateway:"


class FakeGateway:
    name = GatewayName.FAKE

    def create_checkout(self, request: CheckoutRequest) -> Checkout:
        reference = f"fake-{request.reference}"
        cache.set(CACHE_PREFIX + reference, {"status": PaymentStatus.PENDING.value, "amount": str(request.amount)}, None)
        return Checkout(
            gateway_reference=reference,
            payment_url=reverse("billing:fake_checkout", args=[reference]),
            expires_at=request.expires_at,
        )

    def fetch_payment(self, gateway_reference: str) -> GatewayPayment:
        state = cache.get(CACHE_PREFIX + gateway_reference) or {"status": PaymentStatus.EXPIRED.value, "amount": "0"}
        status = PaymentStatus(state["status"])
        return GatewayPayment(
            status=status,
            amount=Decimal(state["amount"]),
            payment_id=f"pay-{gateway_reference}" if status == PaymentStatus.PAID else "",
        )

    def verify_webhook(self, headers) -> bool:
        return True

    def webhook_references(self, payload: dict) -> list[str]:
        return [payload["reference"]] if payload.get("reference") else []

    def create_save_session(self, request: SaveRequest) -> Checkout:
        reference = f"fake-save-{request.reference}"
        cache.set(CACHE_PREFIX + reference, {"token": ""}, None)
        return Checkout(gateway_reference=reference, payment_url=reverse("billing:fake_save", args=[reference]), expires_at=request.expires_at)

    def fetch_saved_token(self, gateway_reference: str) -> str:
        return (cache.get(CACHE_PREFIX + gateway_reference) or {}).get("token", "")

    def charge(self, request: ChargeRequest) -> Checkout:
        reference = f"fake-charge-{request.reference}"
        status = PaymentStatus.FAILED if request.payment_token == DECLINED_TOKEN else PaymentStatus.PAID
        cache.set(CACHE_PREFIX + reference, {"status": status.value, "amount": str(request.amount)}, None)
        return Checkout(gateway_reference=reference, payment_url="", expires_at=None)

    def save_method(self, gateway_reference: str, token: str = "fake-token-ok") -> None:
        """Test/dev helper: the customer saved a payment method on the hosted page."""
        if cache.get(CACHE_PREFIX + gateway_reference) is not None:
            cache.set(CACHE_PREFIX + gateway_reference, {"token": token}, None)

    # Test/dev helper: what the customer would do on the hosted page.
    def simulate(self, gateway_reference: str, status: PaymentStatus, amount: Decimal | None = None) -> None:
        state = cache.get(CACHE_PREFIX + gateway_reference)
        if state is None:
            return
        state["status"] = status.value
        if amount is not None:
            state["amount"] = str(amount)
        cache.set(CACHE_PREFIX + gateway_reference, state, None)
