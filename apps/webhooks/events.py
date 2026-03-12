"""Webhook event type constants."""


class InvoiceEvent:
    VALIDATED = "invoice.validated"
    TRANSMITTED = "invoice.transmitted"
    ACCEPTED = "invoice.accepted"
    REJECTED = "invoice.rejected"
    REFUSED = "invoice.refused"
    ERROR = "invoice.error"


class PaymentEvent:
    CONFIRMED = "payment.confirmed"
    FAILED = "payment.failed"


class StripeEvent:
    """Stripe-specific webhook event types (not mapped to our webhook events)."""

    INVOICE_FINALIZED = "invoice.finalized"
    INVOICE_PAID = "invoice.paid"
