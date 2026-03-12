"""Webhook delivery service — sends webhooks with HMAC-SHA256 signatures."""

import hashlib
import hmac
import json
import logging

import requests
from django.utils import timezone

from apps.webhooks.models import WebhookDelivery, WebhookEndpoint

logger = logging.getLogger(__name__)

WEBHOOK_MAX_ATTEMPTS = 3
WEBHOOK_BACKOFF_SECONDS = {1: 10, 2: 60}  # attempt → delay in seconds
WEBHOOK_DEFAULT_BACKOFF = 300


def emit_webhook(organization, event, data):
    """Emit a webhook event to all active endpoints for the organization.

    Dispatches Celery tasks for async delivery.
    """
    endpoints = WebhookEndpoint.objects.filter(
        organization=organization,
        is_active=True,
    )

    for endpoint in endpoints:
        # Filter by event if the endpoint has a filter
        if endpoint.events and event not in endpoint.events:
            continue

        payload = {
            "event": event,
            "timestamp": timezone.now().isoformat(),
            "data": data,
        }

        from apps.webhooks.tasks import send_webhook  # avoid circular import

        send_webhook.delay(endpoint.pk, payload)


def deliver_webhook(endpoint_id, payload, attempt=1):
    """Actually deliver a webhook (called from Celery task)."""
    try:
        endpoint = WebhookEndpoint.objects.get(pk=endpoint_id)
    except WebhookEndpoint.DoesNotExist:
        return

    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    signature = hmac.new(
        endpoint.secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    delivery = WebhookDelivery(
        endpoint=endpoint,
        event=payload.get("event", ""),
        payload=payload,
        attempt=attempt,
    )

    try:
        response = requests.post(
            endpoint.url,
            data=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": signature,
            },
            timeout=10,
        )
        delivery.http_status = response.status_code
        delivery.response_body = response.text[:1000]
        delivery.success = 200 <= response.status_code < 300
    except requests.RequestException as e:
        delivery.http_status = None
        delivery.response_body = str(e)[:1000]
        delivery.success = False

    delivery.save()

    if not delivery.success and attempt < WEBHOOK_MAX_ATTEMPTS:
        from apps.webhooks.tasks import send_webhook  # avoid circular import

        send_webhook.apply_async(
            args=[endpoint_id, payload],
            kwargs={"attempt": attempt + 1},
            countdown=WEBHOOK_BACKOFF_SECONDS.get(attempt, WEBHOOK_DEFAULT_BACKOFF),
        )
    elif not delivery.success and attempt >= WEBHOOK_MAX_ATTEMPTS:
        endpoint.is_active = False
        endpoint.save(update_fields=["is_active"])
        logger.warning(
            "Webhook endpoint %s disabled after %d failures",
            endpoint.url,
            WEBHOOK_MAX_ATTEMPTS,
        )
