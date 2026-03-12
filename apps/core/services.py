"""Core services — account creation & email verification."""

import logging

from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.text import slugify

from apps.core.tokens import email_verification_token

logger = logging.getLogger(__name__)


def _provision_factpulse_client(org):
    """Provision a FactPulse client for the organisation (best-effort)."""
    if org.factpulse_client_uid:
        return

    from apps.factpulse.client import FactPulseError, client  # lazy: mocked in tests

    if not client.is_configured:
        return

    try:
        result = client.create_client(name=org.name)
        org.factpulse_client_uid = result["uid"]
        org.save(update_fields=["factpulse_client_uid"])
        logger.info("FactPulse client provisioned for %s: %s", org.slug, result["uid"])
    except FactPulseError:
        logger.warning(
            "Failed to provision FactPulse client for %s — "
            "run provision_factpulse_clients later.",
            org.slug,
            exc_info=True,
        )


def create_account(email, password, org_name):
    """Create a new user account with an organization.

    Returns:
        (user, organization) tuple

    Raises:
        ValueError: if email already taken or org_name invalid.
    """
    email = email.strip().lower()
    org_name = org_name.strip()

    if not email:
        raise ValueError("L'adresse email est requise.")
    if not password:
        raise ValueError("Le mot de passe est requis.")
    if not org_name:
        raise ValueError("Le nom de l'organisation est requis.")

    # Import here to avoid circular imports
    from apps.core.models import Organization, OrganizationMembership, UserProfile

    slug = _generate_unique_slug(org_name)

    try:
        with transaction.atomic():
            user = User.objects.create_user(
                username=email,
                email=email,
                password=password,
            )

            UserProfile.objects.create(user=user, email_verified=False)

            org = Organization.objects.create(
                name=org_name,
                slug=slug,
            )

            OrganizationMembership.objects.create(
                user=user,
                organization=org,
                role=OrganizationMembership.Role.OWNER,
            )

    except IntegrityError:
        raise ValueError("Un compte avec cet email existe déjà.") from None

    # Best-effort: provision FactPulse client outside the transaction
    _provision_factpulse_client(org)

    return user, org


def send_verification_email(user, request):
    """Send an email-verification link to *user*."""
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = email_verification_token.make_token(user)
    protocol = "https" if request.is_secure() else "http"
    domain = request.get_host()

    body = render_to_string(
        "registration/email_verification.html",
        {
            "user": user,
            "protocol": protocol,
            "domain": domain,
            "uid": uid,
            "token": token,
        },
    )

    send_mail(
        subject="Vérifiez votre adresse email — FactPulse Billing",
        message=body,
        from_email=None,  # uses DEFAULT_FROM_EMAIL
        recipient_list=[user.email],
    )


def invite_customer_user(organization, customer, email):
    """Invite a user with customer_access role.

    Returns:
        (user, membership, created) — created is False if user already had access.
    """
    from apps.core.models import OrganizationMembership

    user, _ = User.objects.get_or_create(
        email=email,
        defaults={"username": email, "is_active": False},
    )

    membership, created = OrganizationMembership.objects.get_or_create(
        user=user,
        organization=organization,
        defaults={
            "role": OrganizationMembership.Role.CUSTOMER_ACCESS,
            "customer": customer,
        },
    )

    if created:
        try:
            send_mail(
                subject=f"Invitation à consulter vos factures - {organization.name}",
                message=(
                    "Vous avez été invité à consulter vos factures.\n"
                    "Connectez-vous à l'application pour y accéder."
                ),
                from_email=None,  # uses DEFAULT_FROM_EMAIL
                recipient_list=[email],
            )
        except Exception:
            logger.warning(
                "Failed to send invitation email to %s", email, exc_info=True
            )

    return user, membership, created


def _generate_unique_slug(name):
    """Generate a unique slug from org name, appending a suffix if needed."""
    from apps.core.models import Organization

    base = slugify(name) or "org"
    slug = base
    counter = 1
    while Organization.objects.filter(slug=slug).exists():
        slug = f"{base}-{counter}"
        counter += 1
    return slug
