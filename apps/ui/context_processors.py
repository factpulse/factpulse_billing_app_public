from django.conf import settings

from apps.core.models import OrganizationMembership


def ui_context(request):
    """Global context for UI templates."""
    context = {}

    if request.user.is_authenticated:
        memberships = OrganizationMembership.objects.select_related(
            "organization"
        ).filter(user=request.user)
        context["organizations"] = [m.organization for m in memberships]

        # Current org role
        org = getattr(request, "organization", None)
        if org:
            current = next(
                (m for m in memberships if m.organization_id == org.id), None
            )
            context["user_role"] = current.role if current else None
            context["is_owner"] = (
                current.role == OrganizationMembership.Role.OWNER if current else False
            )

    # FactPulse status
    configured = bool(
        settings.FACTPULSE_API_URL
        and settings.FACTPULSE_EMAIL
        and settings.FACTPULSE_PASSWORD
    )
    context["factpulse_degraded"] = not configured
    context["factpulse_sandbox"] = (
        configured and "sandbox" in settings.FACTPULSE_API_URL
    )

    # Payments
    context["stripe_enabled"] = getattr(settings, "STRIPE_ENABLED", False)

    return context
