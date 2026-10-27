"""Payment service — orchestrates checkout creation and webhook handling."""

import logging
from datetime import date
from decimal import Decimal

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
        "api_key": stripe_api_key(provider_config)
        if provider_config.provider == ProviderConfig.Provider.STRIPE
        else provider_config.api_key,
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
    elif event.event_type == StripeEvent.CREDIT_NOTE_CREATED:
        _handle_credit_note_created(event, provider_config)
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
                source="online_payment",
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


def stripe_api_key(provider_config):
    """Key for API calls on the merchant's Stripe account.

    The key entered in the payment settings or, for an organization connected
    through the Stripe App (optional private module), its OAuth access token.
    """
    if provider_config.api_key:
        return provider_config.api_key

    from django.apps import apps

    if apps.is_installed("apps.connect"):
        from apps.connect.services import access_token_for_organization

        return access_token_for_organization(provider_config.organization)
    return ""


def import_stripe_invoice(organization, stripe_invoice, provider_config, notify=True):
    """Import a Stripe invoice as a draft FactPulse invoice.

    Used by both the webhook handler and the UI manual import. The invoice is
    left in 'draft' status (no number assigned) for manual review and
    validation — Stripe-imported invoices must not be auto-emitted.
    Returns (invoice, created) where created=False if already imported.
    notify=False skips the "pending manual validation" e-mail (automatic mode).
    """
    from apps.billing.models import Invoice

    stripe_invoice_id = stripe_invoice.get("id", "")

    # Idempotence
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
        return existing, False

    payload = _build_stripe_invoice_payload(
        organization, stripe_invoice, provider_config
    )

    invoice, warnings = invoice_service.create_invoice(organization, payload)
    if warnings:
        logger.info("Invoice created with warnings: %s", warnings)

    logger.info(
        "Auto-created draft invoice %s from Stripe invoice %s (pending manual validation)",
        invoice.uuid,
        stripe_invoice_id,
    )

    if notify:
        _notify_invoice_created(invoice, organization)

    return invoice, True


def import_stripe_credit_note(organization, stripe_credit_note, notify=True):
    """Import a Stripe credit note as a draft FactPulse credit note.

    The credited Stripe invoice must already be in FactPulse (BT-25/BT-26 refer
    to it); otherwise the credit note is skipped. Returns (credit_note, created),
    credit_note being None when skipped.
    notify=False skips the "pending manual validation" e-mail (automatic mode).
    """
    from apps.payments.providers.stripe.mapper import stripe_credit_note_to_payload

    credit_note_id = stripe_credit_note.get("id", "")

    # Idempotence
    existing = Invoice.objects.filter(
        organization=organization,
        external_id=credit_note_id,
        deleted_at__isnull=True,
    ).first()
    if existing:
        logger.info(
            "Credit note already exists for Stripe credit note %s (uuid=%s), skipping.",
            credit_note_id,
            existing.uuid,
        )
        return existing, False

    invoice_field = stripe_credit_note.get("invoice")
    stripe_invoice_id = (
        invoice_field.get("id")
        if isinstance(invoice_field, dict)
        else (invoice_field or "")
    )
    original = Invoice.objects.filter(
        organization=organization,
        external_id=stripe_invoice_id,
        deleted_at__isnull=True,
    ).first()
    if not stripe_invoice_id or not original:
        logger.warning(
            "Stripe credit note %s skipped: credited invoice %s not in FactPulse.",
            credit_note_id,
            stripe_invoice_id or "?",
        )
        return None, False

    payload = stripe_credit_note_to_payload(
        stripe_credit_note, original_invoice=original
    )
    credit_note, warnings = invoice_service.create_invoice(organization, payload)
    if warnings:
        logger.info("Credit note created with warnings: %s", warnings)
    credit_note.preceding_invoice = original
    credit_note.save(update_fields=["preceding_invoice", "updated_at"])

    logger.info(
        "Auto-created draft credit note %s from Stripe credit note %s "
        "(pending manual validation)",
        credit_note.uuid,
        credit_note_id,
    )

    if notify:
        _notify_invoice_created(credit_note, organization)

    return credit_note, True


def _build_stripe_invoice_payload(organization, stripe_invoice, provider_config):
    """Build the create_invoice payload of a Stripe invoice, linking its customer."""
    from apps.billing.models import Customer
    from apps.payments.providers.stripe.mapper import stripe_invoice_to_payload
    from apps.payments.providers.stripe.sync import import_customer_from_invoice

    # Resolve customer — always link one, create if needed
    customer_field = stripe_invoice.get("customer")
    stripe_customer_id = (
        customer_field.get("id")
        if isinstance(customer_field, dict)
        else (customer_field or "")
    )
    customer = None
    if stripe_customer_id:
        customer = Customer.objects.filter(
            organization=organization, external_id=stripe_customer_id
        ).first()
        if not customer:
            customer = import_customer_from_invoice(organization, stripe_invoice)

    # Build product map for line enrichment
    product_map = _build_stripe_product_map(organization, stripe_invoice)

    # Build payload
    payload = stripe_invoice_to_payload(
        stripe_invoice,
        provider_config=provider_config,
        product_map=product_map,
    )

    # Link customer: the FactPulse Customer record is the source of truth for the
    # recipient (same result as saving the draft from the edit form). Stripe's
    # invoice-time data only seeds the Customer when it is first created.
    if customer:
        payload.get("en16931_data", {}).pop("recipient", None)
        payload["customer_id"] = str(customer.uuid)

    return payload


def resync_stripe_invoice(invoice, provider_config, user=None):
    """Rebuild a draft imported from Stripe from the current Stripe invoice.

    The invoice is re-read through the API, so its data follows the API version
    pinned by the SDK. Only drafts can be re-synced. Returns the invoice.
    """
    import stripe

    if not (invoice.external_id or "").startswith("in_"):
        raise ValueError("Cette facture ne provient pas de Stripe.")

    stripe_invoice = stripe.Invoice.retrieve(
        invoice.external_id,
        api_key=stripe_api_key(provider_config),
        expand=["customer"],
    )
    payload = _build_stripe_invoice_payload(
        invoice.organization, dict(stripe_invoice), provider_config
    )
    invoice, warnings = invoice_service.replace_draft_data(invoice, payload, user=user)
    if warnings:
        logger.info("Invoice re-synced with warnings: %s", warnings)
    return invoice


def _notify_invoice_created(invoice, organization):
    """Send an email notification to org owners when an invoice is auto-created."""
    try:
        from django.conf import settings
        from django.core.mail import send_mail
        from django.template.loader import render_to_string
        from django.urls import reverse

        from apps.core.models import OrganizationMembership

        owners = OrganizationMembership.objects.filter(
            organization=organization,
            role=OrganizationMembership.Role.OWNER,
        ).select_related("user")
        recipients = [m.user.email for m in owners if m.user.email]
        if not recipients:
            return

        from apps.billing.constants import CREDIT_NOTE_TYPE_CODE

        kind = (
            "Avoir" if invoice.invoice_type_code == CREDIT_NOTE_TYPE_CODE else "Facture"
        )
        site_url = settings.SITE_URL.rstrip("/")
        invoice_path = reverse("ui:invoice_detail", kwargs={"uuid": invoice.uuid})

        recipient_name = ""
        if invoice.customer:
            recipient_name = invoice.customer.name
        else:
            recipient_name = (
                invoice.en16931_data.get("recipient", {}).get("name") or "—"
            )

        body = render_to_string(
            "emails/invoice_created.txt",
            {
                "org_name": organization.name,
                "invoice_number": invoice.number or str(invoice.uuid)[:8],
                "customer_name": recipient_name,
                "amount": invoice.total_incl_tax or "—",
                "currency": invoice.currency_code,
                "status": invoice.get_status_display(),
                "invoice_url": f"{site_url}{invoice_path}",
            },
        )

        send_mail(
            subject=f"[FactPulse] {kind} Stripe à valider — {organization.name}",
            message=body,
            from_email=None,
            recipient_list=recipients,
        )
    except Exception:
        logger.warning("Failed to send invoice created notification", exc_info=True)


def _build_stripe_product_map(organization, stripe_invoice):
    """Return {stripe_product_id: Product} for products found in FactPulse."""
    from apps.billing.models import Product

    stripe_product_ids = set()
    for line in (stripe_invoice.get("lines") or {}).get("data", []):
        price = line.get("price") or {}
        pid = price.get("product") if isinstance(price, dict) else None
        if pid and isinstance(pid, str):
            stripe_product_ids.add(pid)

    if not stripe_product_ids:
        return {}

    products = Product.objects.filter(
        organization=organization,
        external_id__in=stripe_product_ids,
        archived=False,
    )
    return {p.external_id: p for p in products}


def _handle_invoice_finalized(event, provider, provider_config):
    """Create a draft invoice from a Stripe subscription invoice.

    Flow: Stripe invoice.finalized → import_stripe_invoice() (draft, unnumbered)
    """
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

    stripe_invoice = event.raw_data.get("data", {}).get("object", {})
    organization = provider_config.organization

    import_stripe_invoice(organization, stripe_invoice, provider_config)


def _handle_credit_note_created(event, provider_config):
    """Create a draft credit note from a Stripe credit note (refund, cancellation)."""
    if not provider_config:
        logger.warning("No provider_config for credit_note.created event, skipping.")
        return

    stripe_credit_note = event.raw_data.get("data", {}).get("object", {})
    import_stripe_credit_note(provider_config.organization, stripe_credit_note)


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

    # Invoice not yet validated/numbered — record the payment info on the
    # draft so it's visible in the UI, but don't transition the status.
    # mark_paid() will pick these pre-filled values up once the invoice is
    # manually validated and transmitted.
    if invoice.status in (S.DRAFT, S.PROCESSING):
        invoice.payment_date = date.today()
        invoice.payment_reference = f"{provider}:{stripe_invoice_id}"
        invoice.payment_amount = Decimal(
            str((stripe_invoice.get("amount_paid") or 0) / 100)
        )
        invoice.save(
            update_fields=[
                "payment_date",
                "payment_reference",
                "payment_amount",
                "updated_at",
            ]
        )
        logger.info(
            "Invoice %s still in %s state — recorded payment info from Stripe "
            "invoice %s, status unchanged.",
            invoice.uuid,
            invoice.status,
            stripe_invoice_id,
        )
        return

    try:
        today = date.today()
        invoice_service.mark_paid(
            invoice,
            source="online_payment",
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
