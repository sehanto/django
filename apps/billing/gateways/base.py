"""Payment gateway port. Billing services depend only on this interface."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.exceptions import DomainError


class GatewayName(models.TextChoices):
    """How a subscription invoice is settled (``SubscriptionInvoice.gateway``, ``BILLING_GATEWAY``)."""

    XENDIT = "xendit", "Xendit"
    FAKE = "fake", _("Simulated (development)")
    CREDIT = "credit", _("Plan credit")  # fully covered by proration credit; no gateway involved


class PaymentStatus(StrEnum):
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
    name: GatewayName

    def create_checkout(self, request: CheckoutRequest) -> Checkout: ...

    def fetch_payment(self, gateway_reference: str) -> GatewayPayment: ...

    def verify_webhook(self, headers) -> bool: ...

    def webhook_references(self, payload: dict) -> list[str]:
        """Identifiers in a webhook payload that may match ``gateway_reference`` or our invoice number."""
        ...
