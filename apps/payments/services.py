"""Payment service — orchestrates checkout creation and webhook handling."""

import logging
from datetime import date

from django.db import IntegrityError, transaction

from apps.billing.models import Invoice
from apps.billing.services import invoice_service
from apps.core.exceptions import ConflictError, UnprocessableError
from apps.payments.adapters import PaymentProviderAdapter
from apps.payments.models import PaymentEventLog, PaymentTransaction, ProviderConfig
from apps.webhooks.events import PaymentEvent, StripeEvent

logger = logging.getLogger(__name__)

S = Invoice.Status
TS = PaymentTransaction.Status


def _load_adapter_registry():
    """Lazily build the adapter registry.

    Each provider is imported only when accessed, so a missing optional
    dependency (e.g. ``stripe``) won't break the entire registry.
    """
    registry = {}

    try:
        from apps.payments.providers.stripe.adapter import StripeAdapter

        registry["stripe"] = (StripeAdapter, {})
    except ImportError:
        logger.debug("stripe SDK not installed — Stripe adapter unavailable")

    try:
        from apps.payments.providers.gocardless.adapter import GoCardlessAdapter

        registry["gocardless"] = (
            GoCardlessAdapter,
            {"config_keys": {"environment": "live"}},
        )
    except ImportError:
        logger.debug("gocardless SDK not installed — GoCardless adapter unavailable")

    try:
        from apps.payments.providers.fintecture.adapter import FintectureAdapter

        registry["fintecture"] = (
            FintectureAdapter,
            {"config_keys": {"app_secret": ""}},  # nosec B105 default fallback
        )
    except ImportError:
        logger.debug("fintecture SDK not installed — Fintecture adapter unavailable")

    return registry


_adapter_registry = None


def get_adapter(provider_config: ProviderConfig) -> PaymentProviderAdapter:
    """Return the adapter instance for a provider config."""
    global _adapter_registry
    if _adapter_registry is None:
        _adapter_registry = _load_adapter_registry()

    entry = _adapter_registry.get(provider_config.provider)
    if not entry:
        raise UnprocessableError(
            f"Unknown payment provider: {provider_config.provider}"
        )

    adapter_cls, opts = entry
    kwargs = {
        "api_key": provider_config.api_key,
        "webhook_secret": provider_config.webhook_secret,
    }
    for key, default in opts.get("config_keys", {}).items():
        kwargs[key] = provider_config.config.get(key, default)
    return adapter_cls(**kwargs)


def get_provider_config(organization, provider="stripe"):
    """Get the active provider config for an organization."""
    try:
        return ProviderConfig.objects.get(
            organization=organization,
            provider=provider,
            is_active=True,
        )
    except ProviderConfig.DoesNotExist as exc:
        raise UnprocessableError(
            f"Payment provider '{provider}' is not configured. "
            f"Configure it in Settings > Payments."
        ) from exc


def create_checkout(
    organization, invoice, *, success_url, cancel_url, provider="stripe"
):
    """Create a checkout session for an invoice.

    Returns the PaymentTransaction with checkout_url set.
    """
    # Only validated+ invoices can have payment links
    if invoice.status not in (S.VALIDATED, S.TRANSMITTED, S.ACCEPTED):
        raise ConflictError(
            "Payment links can only be created for validated, transmitted, "
            "or accepted invoices."
        )

    # Check for existing pending transaction
    existing = PaymentTransaction.objects.filter(
        invoice=invoice,
        provider=provider,
        status__in=(TS.CREATED, TS.PENDING),
    ).first()
    if existing and existing.checkout_url:
        return existing

    provider_config = get_provider_config(organization, provider)
    adapter = get_adapter(provider_config)

    # Extract customer email from en16931_data
    recipient = invoice.en16931_data.get("recipient", {})
    customer_email = recipient.get("email", "")

    result = adapter.create_checkout(
        amount=invoice.total_incl_tax,
        currency=invoice.currency_code,
        invoice_uuid=invoice.uuid,
        invoice_number=invoice.number or str(invoice.uuid)[:8],
        customer_email=customer_email,
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"organization_uuid": str(organization.uuid)},
    )

    txn = PaymentTransaction.objects.create(
        organization=organization,
        invoice=invoice,
        provider=provider,
        provider_payment_id=result.provider_payment_id,
        amount=invoice.total_incl_tax,
        currency=invoice.currency_code,
        payment_method=result.payment_method,
        status=result.status,
        checkout_url=result.checkout_url,
        provider_data=result.provider_data,
    )

    return txn


def handle_webhook(provider, provider_config, headers, body):
    """Handle an inbound webhook from a payment provider.

    Returns True if the event was processed, False if skipped (duplicate).
    """
    adapter = get_adapter(provider_config)

    # Verify signature
    if not adapter.verify_webhook(headers, body):
        raise UnprocessableError("Invalid webhook signature.")

    # Parse event
    event = adapter.parse_webhook(headers, body)

    # Idempotence: skip if already processed
    try:
        with transaction.atomic():
            event_log = PaymentEventLog.objects.create(
                provider=provider,
                provider_event_id=event.provider_event_id,
                event_type=event.event_type,
                payload=event.raw_data,
            )
    except IntegrityError:
        logger.info("Duplicate webhook event %s, skipping.", event.provider_event_id)
        return False

    try:
        _process_event(event, provider, provider_config)
        event_log.processed = True
        event_log.save(update_fields=["processed"])
    except Exception as e:
        event_log.error = str(e)
        event_log.save(update_fields=["error"])
        logger.exception("Error processing webhook event %s", event.provider_event_id)
        raise

    return True


def _process_event(event, provider, provider_config=None):
    """Process a normalized webhook event."""
    if event.event_type == PaymentEvent.CONFIRMED:
        _handle_payment_confirmed(event, provider)
    elif event.event_type == PaymentEvent.FAILED:
        _handle_payment_failed(event, provider)
    elif event.event_type == StripeEvent.INVOICE_FINALIZED:
        _handle_invoice_finalized(event, provider, provider_config)
    elif event.event_type == StripeEvent.INVOICE_PAID:
        _handle_invoice_paid(event, provider)
    else:
        logger.info("Unhandled event type: %s", event.event_type)


def _handle_payment_confirmed(event, provider):
    """Mark transaction as confirmed and invoice as paid."""
    txn = PaymentTransaction.objects.filter(
        provider=provider,
        provider_payment_id=event.provider_payment_id,
    ).first()

    if not txn:
        logger.warning(
            "No transaction found for %s:%s",
            provider,
            event.provider_payment_id,
        )
        return

    txn.status = TS.CONFIRMED
    txn.payment_method = event.metadata.get("payment_method", txn.payment_method)
    txn.save(update_fields=["status", "payment_method", "updated_at"])

    # Mark invoice as paid (idempotent — mark_paid validates transition)
    invoice = txn.invoice
    if invoice.status != S.PAID:
        try:
            today = date.today()
            invoice_service.mark_paid(
                invoice,
                payment_data={
                    "payment_date": today.isoformat(),
                    "payment_reference": f"{provider}:{txn.provider_payment_id}",
                    "amount": float(txn.amount),
                },
            )
        except ConflictError:
            logger.info(
                "Invoice %s already in non-payable state (%s), skipping.",
                invoice.uuid,
                invoice.status,
            )


def _handle_payment_failed(event, provider):
    """Mark transaction as failed."""
    txn = PaymentTransaction.objects.filter(
        provider=provider,
        provider_payment_id=event.provider_payment_id,
    ).first()

    if txn:
        txn.status = TS.FAILED
        txn.save(update_fields=["status", "updated_at"])


def _handle_invoice_finalized(event, provider, provider_config):
    """Create a Factur-X invoice from a Stripe subscription invoice.

    Flow: Stripe invoice.finalized → create_invoice() + validate_invoice()
    """
    from apps.billing.models import Invoice

    stripe_invoice = event.raw_data.get("data", {}).get("object", {})
    stripe_invoice_id = stripe_invoice.get("id", "")

    if not provider_config:
        logger.warning("No provider_config for invoice.finalized event, skipping.")
        return

    if not provider_config.default_supplier:
        logger.warning(
            "No default_supplier configured for provider %s, "
            "cannot auto-create invoice from subscription.",
            provider,
        )
        return

    organization = provider_config.organization

    # Check if we already created an invoice for this Stripe invoice
    existing = Invoice.objects.filter(
        organization=organization,
        external_id=stripe_invoice_id,
        deleted_at__isnull=True,
    ).first()
    if existing:
        logger.info(
            "Invoice already exists for Stripe invoice %s (uuid=%s), skipping.",
            stripe_invoice_id,
            existing.uuid,
        )
        return

    # Map Stripe invoice → create_invoice payload
    from apps.payments.providers.stripe.mapper import stripe_invoice_to_payload

    payload = stripe_invoice_to_payload(stripe_invoice, provider_config=provider_config)

    # Create draft invoice
    invoice, warnings = invoice_service.create_invoice(organization, payload)
    if warnings:
        logger.info("Invoice created with warnings: %s", warnings)

    # Auto-validate (draft → processing → validated via Celery)
    invoice_service.validate_invoice(invoice)

    logger.info(
        "Auto-created invoice %s from Stripe invoice %s",
        invoice.uuid,
        stripe_invoice_id,
    )


def _handle_invoice_paid(event, provider):
    """Mark the matching invoice as paid (Stripe subscription invoice.paid).

    Looks up by external_id (Stripe invoice ID).
    """
    from apps.billing.models import Invoice

    stripe_invoice = event.raw_data.get("data", {}).get("object", {})
    stripe_invoice_id = stripe_invoice.get("id", "")

    if not stripe_invoice_id:
        logger.warning("No Stripe invoice ID in invoice.paid event, skipping.")
        return

    invoice = Invoice.objects.filter(
        external_id=stripe_invoice_id,
        deleted_at__isnull=True,
    ).first()

    if not invoice:
        logger.warning(
            "No matching invoice for Stripe invoice %s, skipping.",
            stripe_invoice_id,
        )
        return

    if invoice.status == S.PAID:
        logger.info("Invoice %s already paid, skipping.", invoice.uuid)
        return

    # Wait for validation to complete — if still processing, skip
    if invoice.status in (S.DRAFT, S.PROCESSING):
        logger.info(
            "Invoice %s still in %s state, cannot mark paid yet.",
            invoice.uuid,
            invoice.status,
        )
        return

    try:
        today = date.today()
        invoice_service.mark_paid(
            invoice,
            payment_data={
                "payment_date": today.isoformat(),
                "payment_reference": f"{provider}:{stripe_invoice_id}",
                # Stripe amounts are in cents
                "amount": float((stripe_invoice.get("amount_paid") or 0) / 100),
            },
        )
        logger.info(
            "Marked invoice %s as paid from Stripe invoice %s",
            invoice.uuid,
            stripe_invoice_id,
        )
    except ConflictError:
        logger.info(
            "Invoice %s in non-payable state (%s), skipping.",
            invoice.uuid,
            invoice.status,
        )
