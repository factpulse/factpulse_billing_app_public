"""GoCardless payment adapter — SEPA Direct Debit (Core + B2B)."""

import hashlib
import hmac
import json
import logging

from apps.payments.adapters import PaymentProviderAdapter, PaymentResult, WebhookEvent
from apps.webhooks.events import PaymentEvent

logger = logging.getLogger(__name__)

# GoCardless payment status → normalized status
_STATUS_MAP = {
    "pending_submission": "pending",
    "submitted": "pending",
    "confirmed": "confirmed",
    "paid_out": "confirmed",
    "failed": "failed",
    "cancelled": "failed",
    "charged_back": "refunded",
}


class GoCardlessAdapter(PaymentProviderAdapter):
    """GoCardless adapter for SEPA Direct Debit payments."""

    def __init__(self, api_key, webhook_secret="", environment="live"):  # nosec B107
        self.api_key = api_key
        self.webhook_secret = webhook_secret
        self.environment = environment
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import gocardless_pro

            self._client = gocardless_pro.Client(
                access_token=self.api_key,
                environment=self.environment,
            )
        return self._client

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
        """Create a GoCardless Billing Request → checkout redirect."""
        amount_cents = int(amount * 100)

        billing_request = self.client.billing_requests.create(
            params={
                "payment_request": {
                    "description": f"Facture {invoice_number}",
                    "amount": amount_cents,
                    "currency": currency.upper(),
                    "metadata": {
                        "invoice_uuid": str(invoice_uuid),
                        **(metadata or {}),
                    },
                },
            }
        )

        # Create a billing request flow for hosted checkout
        flow = self.client.billing_request_flows.create(
            params={
                "redirect_uri": success_url,
                "exit_uri": cancel_url,
                "links": {"billing_request": billing_request.id},
                "prefilled_customer": {"email": customer_email}
                if customer_email
                else {},
            }
        )

        return PaymentResult(
            provider_payment_id=billing_request.id,
            status="created",
            checkout_url=flow.authorisation_url,
            payment_method="sepa_debit",
            provider_data={
                "billing_request_id": billing_request.id,
                "flow_id": flow.id,
            },
        )

    def get_payment_status(self, provider_payment_id) -> PaymentResult:
        """Get payment status from GoCardless."""
        payment = self.client.payments.get(provider_payment_id)
        return PaymentResult(
            provider_payment_id=payment.id,
            status=_STATUS_MAP.get(payment.status, "pending"),
            payment_method="sepa_debit",
            provider_data={"status": payment.status, "id": payment.id},
        )

    def verify_webhook(self, headers, body) -> bool:
        """Verify GoCardless webhook HMAC-SHA256 signature."""
        if not self.webhook_secret:
            return False
        signature = headers.get("Webhook-Signature", "")
        expected = hmac.new(
            self.webhook_secret.encode("utf-8"),
            body if isinstance(body, bytes) else body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_webhook(self, headers, body) -> WebhookEvent:
        """Parse GoCardless webhook into normalized event."""
        payload = json.loads(body) if isinstance(body, bytes) else body
        events = payload.get("events", [])

        if not events:
            return WebhookEvent(
                provider_event_id="empty",
                event_type="unknown",
                raw_data=payload,
            )

        # Process first payment-related event
        event = events[0]
        event_id = event.get("id", "")
        resource_type = event.get("resource_type", "")
        action = event.get("action", "")
        links = event.get("links", {})

        # Determine normalized event type
        norm_type = "unknown"
        provider_payment_id = ""

        if resource_type == "payments":
            provider_payment_id = links.get("payment", "")
            if action in ("confirmed", "paid_out"):
                norm_type = PaymentEvent.CONFIRMED
            elif action in ("failed", "cancelled"):
                norm_type = PaymentEvent.FAILED
        elif resource_type == "mandates":
            provider_payment_id = links.get("mandate", "")
            if action == "active":
                norm_type = "mandate.active"
            elif action in ("failed", "cancelled", "expired"):
                norm_type = "mandate.cancelled"

        return WebhookEvent(
            provider_event_id=event_id,
            event_type=norm_type,
            provider_payment_id=provider_payment_id,
            metadata={
                "resource_type": resource_type,
                "action": action,
                "payment_method": "sepa_debit",
            },
            raw_data=payload,
        )
