from django.conf import settings

from .base import PaymentGateway


def get_gateway() -> PaymentGateway:
    """The configured gateway (``BILLING_GATEWAY``): ``"xendit"`` in production, ``"fake"`` for dev/tests."""
    if settings.BILLING_GATEWAY == "xendit":
        from .xendit import XenditGateway

        return XenditGateway(
            secret_key=settings.XENDIT_SECRET_KEY,
            callback_token=settings.XENDIT_CALLBACK_TOKEN,
            api_url=settings.XENDIT_API_URL,
        )
    if settings.BILLING_GATEWAY == "fake":
        from .fake import FakeGateway

        return FakeGateway()
    raise ValueError(f"Unknown BILLING_GATEWAY: {settings.BILLING_GATEWAY!r}")
