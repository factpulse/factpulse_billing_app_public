import uuid as uuid_lib

from django.db import models

from apps.core.fields import EncryptedCharField
from apps.core.models import Organization


class ProviderConfig(models.Model):
    """Configuration d'un provider de paiement par organisation."""

    class Provider(models.TextChoices):
        STRIPE = "stripe", "Stripe"
        GOCARDLESS = "gocardless", "GoCardless"
        FINTECTURE = "fintecture", "Fintecture"

    uuid = models.UUIDField(default=uuid_lib.uuid4, unique=True, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="payment_providers"
    )
    provider = models.CharField(max_length=50, choices=Provider)
    api_key = EncryptedCharField(max_length=512)
    webhook_secret = EncryptedCharField(max_length=512, blank=True)
    is_active = models.BooleanField(default=True)
    default_supplier = models.ForeignKey(
        "billing.Supplier",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("organization", "provider")]

    def __str__(self):
        return f"{self.provider} ({self.organization})"


class PaymentTransaction(models.Model):
    """Transaction de paiement, indépendante du provider."""

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    uuid = models.UUIDField(default=uuid_lib.uuid4, unique=True, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="payment_transactions"
    )
    invoice = models.ForeignKey(
        "billing.Invoice", on_delete=models.CASCADE, related_name="payments"
    )
    provider = models.CharField(max_length=50)
    provider_payment_id = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="EUR")
    payment_method = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=50, choices=Status, default=Status.CREATED)
    checkout_url = models.URLField(max_length=2048, blank=True)
    provider_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.provider}:{self.provider_payment_id} ({self.status})"


class PaymentEventLog(models.Model):
    """Log d'événements webhooks entrants (idempotence + audit)."""

    provider = models.CharField(max_length=50)
    provider_event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=100)
    payload = models.JSONField()
    processed = models.BooleanField(default=False)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.provider}:{self.event_type} ({self.provider_event_id})"
