import hashlib
import secrets
import uuid as uuid_lib

from django.conf import settings
from django.db import models


class Organization(models.Model):
    """Tenant principal — un éditeur, une entreprise, ou FactPulse SAS elle-même."""

    uuid = models.UUIDField(default=uuid_lib.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    factpulse_client_uid = models.UUIDField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class OrganizationMembership(models.Model):
    """Lien utilisateur ↔ organisation avec rôle."""

    class Role(models.TextChoices):
        OWNER = "owner", "Propriétaire"
        MEMBER = "member", "Membre"
        VIEWER = "viewer", "Lecteur"
        CUSTOMER_ACCESS = "customer_access", "Accès client"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=20, choices=Role, default=Role.MEMBER)
    # For customer_access role: links to the Customer they can view
    customer = models.ForeignKey(
        "billing.Customer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="access_memberships",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("user", "organization")]

    def __str__(self):
        return f"{self.user} - {self.organization} ({self.role})"


class UserProfile(models.Model):
    """Extra per-user data (email verification, etc.)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    email_verified = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user} (verified={self.email_verified})"


class APIKey(models.Model):
    """Clé API longue durée pour les intégrations (MCP, webhooks, etc.).

    La clé complète n'est visible qu'à la création. Seuls le préfixe
    (pour identification) et le hash SHA-256 sont stockés.
    """

    PREFIX = "fp_"
    KEY_LENGTH = 40  # 40 random bytes → 80 hex chars

    uuid = models.UUIDField(default=uuid_lib.uuid4, unique=True, editable=False)
    name = models.CharField(
        max_length=255, help_text="Nom descriptif (ex: « Claude Desktop »)"
    )
    prefix = models.CharField(max_length=12, editable=False, db_index=True)
    key_hash = models.CharField(max_length=64, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys"
    )
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="api_keys"
    )
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.prefix}…)"

    @classmethod
    def generate(cls, *, name, user, organization):
        """Create a new API key. Returns (api_key_instance, raw_key).

        The raw key is only available at creation time — store or display it
        immediately, it cannot be recovered.
        """
        raw_secret = secrets.token_hex(cls.KEY_LENGTH)
        raw_key = f"{cls.PREFIX}{raw_secret}"
        prefix = raw_key[: len(cls.PREFIX) + 8]  # fp_ + 8 hex chars
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        instance = cls.objects.create(
            name=name,
            prefix=prefix,
            key_hash=key_hash,
            user=user,
            organization=organization,
        )
        return instance, raw_key

    @classmethod
    def authenticate(cls, raw_key):
        """Validate a raw API key. Returns (user, organization) or None."""
        if not raw_key or not raw_key.startswith(cls.PREFIX):
            return None

        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        prefix = raw_key[: len(cls.PREFIX) + 8]

        from django.utils import timezone

        try:
            api_key = cls.objects.select_related("user", "organization").get(
                prefix=prefix,
                key_hash=key_hash,
                is_active=True,
            )
        except cls.DoesNotExist:
            return None

        if api_key.expires_at and api_key.expires_at < timezone.now():
            return None

        # Update last_used timestamp (fire-and-forget)
        cls.objects.filter(pk=api_key.pk).update(last_used_at=timezone.now())

        return api_key.user, api_key.organization
