"""Stripe payment adapter."""

import logging

import stripe

from apps.payments.adapters import PaymentProviderAdapter, PaymentResult, WebhookEvent
from apps.webhooks.events import PaymentEvent

logger = logging.getLogger(__name__)

# Mapping Stripe checkout.session events → normalized status
_SESSION_STATUS_MAP = {
    "complete": "confirmed",
    "expired": "failed",
    "open": "pending",
}

# Mapping Stripe event types → normalized event types
_EVENT_TYPE_MAP = {
    # Checkout (Phase 1 — pay-by-link)
    "checkout.session.completed": PaymentEvent.CONFIRMED,
    "checkout.session.async_payment_succeeded": PaymentEvent.CONFIRMED,
    "checkout.session.async_payment_failed": PaymentEvent.FAILED,
    "checkout.session.expired": PaymentEvent.FAILED,
    # Stripe Billing subscriptions (Phase 2)
    "invoice.finalized": "invoice.finalized",
    "invoice.payment_succeeded": "invoice.paid",
    "invoice.paid": "invoice.paid",
    "invoice.payment_failed": PaymentEvent.FAILED,
}


class StripeAdapter(PaymentProviderAdapter):
    """Stripe Checkout Session adapter."""

    def __init__(self, api_key, webhook_secret=""):  # nosec B107
        self.api_key = api_key
        self.webhook_secret = webhook_secret

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
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[
                {
                    "price_data": {
                        "currency": currency.lower(),
                        "product_data": {"name": f"Facture {invoice_number}"},
                        "unit_amount": int(amount * 100),  # euros → centimes
                    },
                    "quantity": 1,
                }
            ],
            customer_email=customer_email,
            metadata={
                "invoice_uuid": str(invoice_uuid),
                **(metadata or {}),
            },
            success_url=success_url,
            cancel_url=cancel_url,
            api_key=self.api_key,
        )

        return PaymentResult(
            provider_payment_id=session.id,
            status="created",
            checkout_url=session.url,
            provider_data={"session_id": session.id},
        )

    def get_payment_status(self, provider_payment_id) -> PaymentResult:
        session = stripe.checkout.Session.retrieve(
            provider_payment_id,
            api_key=self.api_key,
        )
        return PaymentResult(
            provider_payment_id=session.id,
            status=_SESSION_STATUS_MAP.get(session.status, "pending"),
            payment_method=session.payment_method_types[0]
            if session.payment_method_types
            else "",
            provider_data={"session_id": session.id, "status": session.status},
        )

    def verify_webhook(self, headers, body) -> bool:
        try:
            sig = headers.get("Stripe-Signature", "")
            stripe.Webhook.construct_event(body, sig, self.webhook_secret)
            return True
        except (stripe.SignatureVerificationError, ValueError):
            return False

    def parse_webhook(self, headers, body) -> WebhookEvent:
        sig = headers.get("Stripe-Signature", "")
        event = stripe.Webhook.construct_event(body, sig, self.webhook_secret)

        event_data = event["data"]["object"]
        metadata = event_data.get("metadata", {})

        # Determine normalized event type
        stripe_type = event["type"]
        norm_type = _EVENT_TYPE_MAP.get(stripe_type, stripe_type)

        # Extract payment method if available
        payment_method = ""
        pmt = event_data.get("payment_method_types")
        if pmt:
            payment_method = pmt[0]

        return WebhookEvent(
            provider_event_id=event["id"],
            event_type=norm_type,
            provider_payment_id=event_data.get("id", ""),
            metadata={
                **metadata,
                "payment_method": payment_method,
                "amount_total": event_data.get("amount_total"),
                "currency": event_data.get("currency"),
            },
            raw_data=dict(event),
        )
