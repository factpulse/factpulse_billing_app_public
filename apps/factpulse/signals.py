"""Auto-provision FactPulse clients for new Organizations."""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.core.models import Organization

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Organization)
def auto_provision_factpulse_client(sender, instance, created, **kwargs):
    """Create a FactPulse client when a new Organization is created."""
    if not created:
        return

    if instance.factpulse_client_uid:
        return

    from apps.factpulse.client import FactPulseError, client  # lazy: mocked in tests

    if not client.is_configured:
        return

    try:
        result = client.create_client(name=instance.name)
        instance.factpulse_client_uid = result["uid"]
        instance.save(update_fields=["factpulse_client_uid"])
        logger.info(
            "Auto-provisioned FactPulse client %s for org %s",
            result["uid"],
            instance.name,
        )
    except FactPulseError:
        logger.warning(
            "Failed to auto-provision FactPulse client for org %s",
            instance.name,
            exc_info=True,
        )
