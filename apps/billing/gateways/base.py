"""Payment gateway port. Billing services depend only on this interface."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Protocol

from apps.core.exceptions import DomainError


class PaymentStatus(Enum):
    PENDING = "pending"
    PAID = "paid"
    EXPIRED = "expired"
    CANCELED = "canceled"


@dataclass(frozen=True)
class CheckoutRequest:
    reference: str  # our invoice number; unique per payment attempt
    amount: Decimal  # total including PPN, whole rupiah
    description: str
    expires_at: datetime
    locale: str
    return_url: str
    metadata: dict


@dataclass(frozen=True)
class Checkout:
    gateway_reference: str
    payment_url: str
    expires_at: datetime | None


@dataclass(frozen=True)
class GatewayPayment:
    status: PaymentStatus
    amount: Decimal
    payment_id: str = ""


class GatewayError(DomainError):
    """The payment provider could not be reached or rejected the request."""


class PaymentGateway(Protocol):
    name: str

    def create_checkout(self, request: CheckoutRequest) -> Checkout: ...

    def fetch_payment(self, gateway_reference: str) -> GatewayPayment: ...

    def verify_webhook(self, headers) -> bool: ...

    def webhook_references(self, payload: dict) -> list[str]:
        """Identifiers in a webhook payload that may match ``gateway_reference`` or our invoice number."""
        ...
