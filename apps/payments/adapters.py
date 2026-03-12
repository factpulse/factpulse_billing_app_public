"""Payment provider adapter base class."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class PaymentResult:
    """Normalized result from a payment provider."""

    provider_payment_id: str = ""
    status: str = "created"  # created, pending, confirmed, failed, refunded
    checkout_url: str = ""
    payment_method: str = ""
    provider_data: dict = field(default_factory=dict)


@dataclass
class WebhookEvent:
    """Normalized inbound webhook event."""

    provider_event_id: str = ""
    event_type: str = ""  # payment.confirmed, payment.failed, etc.
    provider_payment_id: str = ""
    metadata: dict = field(default_factory=dict)
    raw_data: dict = field(default_factory=dict)


class PaymentProviderAdapter(ABC):
    """Interface commune pour tous les providers de paiement."""

    @abstractmethod
    def create_checkout(
        self,
        *,
        amount,
        currency,
        invoice_uuid,
        invoice_number,
        customer_email,
        success_url,
        cancel_url,
        metadata=None,
    ) -> PaymentResult:
        """Create a checkout session / payment link."""

    @abstractmethod
    def get_payment_status(self, provider_payment_id) -> PaymentResult:
        """Get current payment status from provider."""

    @abstractmethod
    def verify_webhook(self, headers, body) -> bool:
        """Verify webhook signature."""

    @abstractmethod
    def parse_webhook(self, headers, body) -> WebhookEvent:
        """Parse a webhook payload into a normalized event."""
