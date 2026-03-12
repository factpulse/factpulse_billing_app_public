from celery import shared_task

from apps.webhooks.services import deliver_webhook


@shared_task(bind=True, max_retries=0)
def send_webhook(self, endpoint_id, payload, attempt=1):
    """Celery task to deliver a webhook."""
    deliver_webhook(endpoint_id, payload, attempt=attempt)
