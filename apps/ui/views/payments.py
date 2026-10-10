"""UI views for payment provider settings."""

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render

from apps.core.models import OrganizationMembership


@login_required
def payment_settings(request):
    org = request.organization
    if not org:
        return redirect("ui:dashboard")

    membership = OrganizationMembership.objects.filter(
        user=request.user, organization=org
    ).first()
    if not membership or membership.role != OrganizationMembership.Role.OWNER:
        return HttpResponseForbidden(
            "Accès réservé aux propriétaires de l'organisation."
        )

    if not apps.is_installed("apps.payments"):
        return render(request, "ui/payment_settings.html", {"payments_disabled": True})

    from apps.billing.models import Supplier
    from apps.payments.models import ProviderConfig

    suppliers = Supplier.objects.filter(organization=org, archived=False).order_by(
        "name"
    )

    configs = {c.provider: c for c in ProviderConfig.objects.filter(organization=org)}

    if request.method == "POST":
        provider = request.POST.get("provider", "stripe")
        api_key = request.POST.get("api_key", "").strip()
        webhook_secret = request.POST.get("webhook_secret", "").strip()
        default_supplier_uuid = request.POST.get("default_supplier", "").strip()

        if not api_key:
            messages.error(request, "La clé API est obligatoire.")
            return redirect("ui:payment_settings")

        default_supplier = None
        if default_supplier_uuid:
            default_supplier = Supplier.objects.filter(
                uuid=default_supplier_uuid, organization=org
            ).first()

        existing = configs.get(provider)
        if existing:
            existing.api_key = api_key
            if webhook_secret:
                existing.webhook_secret = webhook_secret
            existing.default_supplier = default_supplier
            existing.is_active = True
            existing.save(
                update_fields=[
                    "api_key",
                    "webhook_secret",
                    "default_supplier_id",
                    "is_active",
                    "updated_at",
                ]
            )
        else:
            ProviderConfig.objects.create(
                organization=org,
                provider=provider,
                api_key=api_key,
                webhook_secret=webhook_secret,
                default_supplier=default_supplier,
                is_active=True,
            )

        messages.success(request, "Configuration paiement enregistrée.")
        return redirect("ui:payment_settings")

    stripe_config = configs.get("stripe")

    webhook_url = request.build_absolute_uri("/api/v1/payments/webhooks/stripe/")

    return render(
        request,
        "ui/payment_settings.html",
        {
            "stripe_config": stripe_config,
            "suppliers": suppliers,
            "webhook_url": webhook_url,
        },
    )
