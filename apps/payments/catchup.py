"""Catch-up of Stripe invoices and credit notes the webhooks did not deliver.

Lists the finalized invoices and the credit notes of the last days on each
connected Stripe account and imports those unknown to FactPulse, exactly as the
webhook would have (draft, or automatic processing for the Stripe App).
Documents already imported — including soft-deleted drafts — are left alone.
"""

import logging
from datetime import timedelta

import stripe
from django.apps import apps
from django.utils import timezone

from apps.billing.models import Invoice
from apps.payments.models import ProviderConfig
from apps.payments.services import (
    import_stripe_credit_note,
    import_stripe_invoice,
    stripe_api_key,
)

logger = logging.getLogger(__name__)

CATCHUP_DAYS = 3
# Finalized invoices: a draft has no legal number yet, a void one is cancelled
FINALIZED_STATUSES = {"open", "paid", "uncollectible"}


def catch_up_stripe(days=CATCHUP_DAYS):
    """Catch up every active Stripe connection. Returns the number of imports."""
    imported = 0
    configs = ProviderConfig.objects.filter(
        provider=ProviderConfig.Provider.STRIPE, is_active=True
    ).select_related("organization", "default_supplier")
    for config in configs:
        try:
            imported += catch_up_organization(config, days)
        except Exception:  # one failing account must not block the others
            logger.exception("Stripe catch-up failed for %s", config.organization.slug)
    return imported


def catch_up_organization(config, days=CATCHUP_DAYS):
    """Import the recent Stripe documents missing from one organization."""
    handlers = _handlers(config)
    if not handlers:
        return 0
    started_at, import_invoice, import_credit_note = handlers

    api_key = stripe_api_key(config)
    if not api_key:
        return 0
    since = max(timezone.now() - timedelta(days=days), started_at)

    invoices = [
        inv
        for inv in _recent(stripe.Invoice, api_key, since, expand=["data.customer"])
        if inv.get("status") in FINALIZED_STATUSES
    ]
    credit_notes = list(_recent(stripe.CreditNote, api_key, since))

    imported = 0
    # Invoices first: a credit note refers to the invoice it credits
    for stripe_invoice in _unknown(config.organization, invoices):
        if import_invoice(stripe_invoice):
            imported += 1
    for stripe_credit_note in _unknown(config.organization, credit_notes):
        if import_credit_note(stripe_credit_note):
            imported += 1

    if imported:
        logger.info(
            "Stripe catch-up: %d document(s) imported for %s",
            imported,
            config.organization.slug,
        )
    return imported


def _handlers(config):
    """(start date, invoice importer, credit note importer), or None to skip.

    Organizations connected through the Stripe App (optional private module)
    follow its processing (onboarding, automatic mode); the others follow the
    manual webhook, which needs a default supplier for invoices.
    """
    organization = config.organization
    if apps.is_installed("apps.connect"):
        from apps.connect.processing import catch_up_handlers

        handlers = catch_up_handlers(organization)
        if handlers:
            return handlers

    if not config.default_supplier:
        return None
    return (
        config.created_at,
        lambda inv: import_stripe_invoice(organization, dict(inv), config)[1],
        lambda cn: import_stripe_credit_note(organization, dict(cn))[1],
    )


def _recent(resource, api_key, since, **params):
    """Objects created since `since`, newest first (Stripe list order).

    Stops at the first older object instead of filtering on `created`, which
    the credit note list does not accept in every API version.
    """
    threshold = int(since.timestamp())
    for obj in resource.list(api_key=api_key, limit=100, **params).auto_paging_iter():
        if (obj.get("created") or 0) < threshold:
            return
        yield obj


def _unknown(organization, stripe_objects):
    """Stripe objects whose ID matches no FactPulse invoice, deleted ones included."""
    ids = [obj["id"] for obj in stripe_objects]
    known = set(
        Invoice.objects.filter(
            organization=organization, external_id__in=ids
        ).values_list("external_id", flat=True)
    )
    # Oldest first, in the order the webhooks would have delivered them
    return [obj for obj in reversed(stripe_objects) if obj["id"] not in known]
