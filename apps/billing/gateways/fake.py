"""In-process stand-in for a real gateway: used by tests and by local development
until Xendit keys are configured. Payments are simulated on our own checkout page."""

from decimal import Decimal

from django.core.cache import cache
from django.urls import reverse

from .base import Checkout, CheckoutRequest, GatewayName, GatewayPayment, PaymentStatus

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

    # Test/dev helper: what the customer would do on the hosted page.
    def simulate(self, gateway_reference: str, status: PaymentStatus, amount: Decimal | None = None) -> None:
        state = cache.get(CACHE_PREFIX + gateway_reference)
        if state is None:
            return
        state["status"] = status.value
        if amount is not None:
            state["amount"] = str(amount)
        cache.set(CACHE_PREFIX + gateway_reference, state, None)
