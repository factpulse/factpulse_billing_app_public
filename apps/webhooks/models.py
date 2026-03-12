import uuid as uuid_lib

from django.db import models

from apps.core.fields import EncryptedCharField
from apps.core.models import Organization


class WebhookEndpoint(models.Model):
    """Outbound webhook endpoint configurable per Organization."""

    uuid = models.UUIDField(default=uuid_lib.uuid4, unique=True, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="webhook_endpoints"
    )
    url = models.URLField()
    secret = EncryptedCharField(max_length=512)
    events = models.JSONField(default=list)  # Empty list = all events
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.url} ({self.organization})"


class WebhookDelivery(models.Model):
    """Traces each webhook delivery attempt."""

    uuid = models.UUIDField(default=uuid_lib.uuid4, unique=True, editable=False)
    endpoint = models.ForeignKey(
        WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries"
    )
    event = models.CharField(max_length=50)
    payload = models.JSONField()
    http_status = models.IntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True)
    success = models.BooleanField(default=False)
    attempt = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.event} -> {self.endpoint.url} (attempt {self.attempt})"
