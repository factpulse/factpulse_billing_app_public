"""Periodic Stripe tasks."""

from celery import shared_task

from apps.payments.catchup import catch_up_stripe


@shared_task
def catch_up_stripe_documents():
    """Import the Stripe invoices and credit notes the webhooks missed."""
    return catch_up_stripe()
