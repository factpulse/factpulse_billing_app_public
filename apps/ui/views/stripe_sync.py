"""UI views for Stripe customer/product/invoice sync."""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.core.models import OrganizationMembership

logger = logging.getLogger(__name__)


def _require_owner_and_stripe(request):
    """Return (org, provider_config) or redirect response on failure."""
    org = request.organization
    if not org:
        return None, None, redirect("ui:dashboard")

    membership = OrganizationMembership.objects.filter(
        user=request.user, organization=org
    ).first()
    if not membership or membership.role != OrganizationMembership.Role.OWNER:
        return None, None, redirect("ui:dashboard")

    from apps.payments.models import ProviderConfig

    config = (
        ProviderConfig.objects.filter(
            organization=org, provider="stripe", is_active=True
        )
        .exclude(api_key="")
        .first()
    )
    if not config:
        messages.error(request, "Stripe n'est pas configuré pour cette organisation.")
        return None, None, redirect("ui:payment_settings")

    return org, config, None


@login_required
def stripe_customers(request):
    org, config, err = _require_owner_and_stripe(request)
    if err:
        return err

    from apps.billing.models import Customer
    from apps.payments.providers.stripe.sync import import_customer, list_customers

    if request.method == "POST":
        action = request.POST.get("action")
        stripe_id = request.POST.get("stripe_id", "").strip()

        if action == "import" and stripe_id:
            try:
                import stripe as stripe_sdk

                sc = stripe_sdk.Customer.retrieve(stripe_id, api_key=config.api_key)
                _, created = import_customer(org, sc)
                if created:
                    messages.success(
                        request, f"Client « {sc.get('name') or stripe_id} » importé."
                    )
                else:
                    messages.info(
                        request,
                        f"Client « {sc.get('name') or stripe_id} » déjà présent, mis à jour.",
                    )
            except Exception as e:
                messages.error(request, f"Erreur Stripe : {e}")

        elif action == "import_all":
            try:
                stripe_customers_list = list_customers(config.api_key)
                created_count = updated_count = 0
                for sc in stripe_customers_list:
                    _, created = import_customer(org, sc)
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1
                messages.success(
                    request,
                    f"{created_count} client(s) importé(s), {updated_count} mis à jour.",
                )
            except Exception as e:
                messages.error(request, f"Erreur Stripe : {e}")

        return redirect("ui:stripe_customers")

    # Fetch Stripe customers + cross-reference with FactPulse
    stripe_list = []
    api_error = None
    try:
        raw = list_customers(config.api_key)
        stripe_ids = [sc["id"] for sc in raw]
        mapped = {
            c.external_id: str(c.uuid)
            for c in Customer.objects.filter(
                organization=org, external_id__in=stripe_ids
            )
        }
        stripe_list = [
            {
                "id": sc["id"],
                "name": sc.get("name") or "—",
                "email": sc.get("email") or "—",
                "is_mapped": sc["id"] in mapped,
                "factpulse_uuid": mapped.get(sc["id"]),
            }
            for sc in raw
        ]
    except Exception as e:
        api_error = str(e)

    return render(
        request,
        "ui/stripe_customers.html",
        {
            "stripe_list": stripe_list,
            "api_error": api_error,
            "unmapped_count": sum(1 for s in stripe_list if not s["is_mapped"]),
        },
    )


@login_required
def stripe_products(request):
    org, config, err = _require_owner_and_stripe(request)
    if err:
        return err

    from apps.billing.models import Product
    from apps.payments.providers.stripe.sync import import_product, list_products

    if request.method == "POST":
        action = request.POST.get("action")
        stripe_id = request.POST.get("stripe_id", "").strip()

        if action == "import" and stripe_id:
            try:
                import stripe as stripe_sdk

                sp = stripe_sdk.Product.retrieve(
                    stripe_id,
                    api_key=config.api_key,
                    expand=["default_price"],
                )
                _, created = import_product(org, sp)
                if created:
                    messages.success(
                        request, f"Produit « {sp.get('name') or stripe_id} » importé."
                    )
                else:
                    messages.info(
                        request,
                        f"Produit « {sp.get('name') or stripe_id} » déjà présent, mis à jour.",
                    )
            except Exception as e:
                messages.error(request, f"Erreur Stripe : {e}")

        elif action == "import_all":
            try:
                stripe_products_list = list_products(config.api_key)
                created_count = updated_count = 0
                for sp in stripe_products_list:
                    _, created = import_product(org, sp)
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1
                messages.success(
                    request,
                    f"{created_count} produit(s) importé(s), {updated_count} mis à jour.",
                )
            except Exception as e:
                messages.error(request, f"Erreur Stripe : {e}")

        return redirect("ui:stripe_products")

    stripe_list = []
    api_error = None
    try:
        raw = list_products(config.api_key)
        stripe_ids = [sp["id"] for sp in raw]
        mapped = {
            p.external_id: str(p.uuid)
            for p in Product.objects.filter(
                organization=org, external_id__in=stripe_ids
            )
        }
        stripe_list = [
            {
                "id": sp["id"],
                "name": sp.get("name") or "—",
                "description": sp.get("description") or "",
                "default_price": _format_price(sp.get("default_price")),
                "is_mapped": sp["id"] in mapped,
                "factpulse_uuid": mapped.get(sp["id"]),
            }
            for sp in raw
        ]
    except Exception as e:
        api_error = str(e)

    return render(
        request,
        "ui/stripe_products.html",
        {
            "stripe_list": stripe_list,
            "api_error": api_error,
            "unmapped_count": sum(1 for s in stripe_list if not s["is_mapped"]),
        },
    )


@login_required
def stripe_invoices(request):
    org, config, err = _require_owner_and_stripe(request)
    if err:
        return err

    from apps.billing.models import Invoice
    from apps.payments.providers.stripe.sync import list_invoices
    from apps.payments.services import import_stripe_invoice

    if request.method == "POST":
        action = request.POST.get("action")
        stripe_invoice_id = request.POST.get("stripe_invoice_id", "").strip()

        if action == "import" and stripe_invoice_id:
            if not config.default_supplier:
                messages.error(
                    request,
                    "Configurez un fournisseur par défaut dans Paiements avant d'importer des factures.",
                )
            else:
                try:
                    import stripe as stripe_sdk

                    si = stripe_sdk.Invoice.retrieve(
                        stripe_invoice_id,
                        api_key=config.api_key,
                        expand=["customer"],
                    )
                    invoice, created = import_stripe_invoice(org, dict(si), config)
                    if created:
                        messages.success(
                            request,
                            f"Facture importée ({invoice.number or str(invoice.uuid)[:8]}).",
                        )
                    else:
                        messages.info(request, "Facture déjà importée.")
                except Exception as e:
                    messages.error(request, f"Erreur : {e}")

        cursor = request.POST.get("cursor", "")
        return redirect(f"{request.path}?cursor={cursor}" if cursor else request.path)

    cursor = request.GET.get("cursor", "") or None
    stripe_list = []
    has_more = False
    next_cursor = None
    api_error = None

    try:
        raw, has_more = list_invoices(config.api_key, starting_after=cursor)
        stripe_ids = [si["id"] for si in raw]
        imported = {
            inv.external_id: str(inv.uuid)
            for inv in Invoice.objects.filter(
                organization=org,
                external_id__in=stripe_ids,
                deleted_at__isnull=True,
            )
        }
        stripe_list = [_invoice_display(si, imported) for si in raw]
        if raw and has_more:
            next_cursor = raw[-1]["id"]
    except Exception as e:
        api_error = str(e)

    return render(
        request,
        "ui/stripe_invoices.html",
        {
            "stripe_list": stripe_list,
            "api_error": api_error,
            "has_more": has_more,
            "next_cursor": next_cursor,
            "current_cursor": cursor,
            "has_supplier": bool(config.default_supplier),
        },
    )


def _format_price(default_price):
    """Return a human-readable price string from a Stripe default_price object."""
    if not default_price or not isinstance(default_price, dict):
        return ""
    unit_amount = default_price.get("unit_amount")
    currency = (default_price.get("currency") or "eur").upper()
    if unit_amount is None:
        return ""
    return f"{unit_amount / 100:.2f} {currency}"


def _invoice_display(si, imported_map):
    """Build a display dict from a Stripe Invoice object."""
    customer = si.get("customer")
    if isinstance(customer, dict):
        customer_name = (
            customer.get("name") or customer.get("email") or customer.get("id") or "—"
        )
    else:
        customer_name = (
            si.get("customer_name") or si.get("customer_email") or str(customer or "—")
        )

    amount = (si.get("amount_due") or 0) / 100
    currency = (si.get("currency") or "eur").upper()

    return {
        "id": si["id"],
        "number": si.get("number") or si["id"],
        "customer_name": customer_name,
        "amount": f"{amount:.2f} {currency}",
        "status": si.get("status") or "—",
        "created": si.get("created"),
        "is_imported": si["id"] in imported_map,
        "factpulse_uuid": imported_map.get(si["id"]),
    }


@login_required
def stripe_invoice_resync(request, uuid):
    """Re-read a Stripe-imported draft from Stripe and rebuild it (POST only)."""
    from django.shortcuts import get_object_or_404

    from apps.billing.models import Invoice
    from apps.payments.services import resync_stripe_invoice

    if request.method != "POST":
        return redirect("ui:invoice_detail", uuid=uuid)

    org, config, err = _require_owner_and_stripe(request)
    if err:
        return err

    invoice = get_object_or_404(
        Invoice, uuid=uuid, organization=org, deleted_at__isnull=True
    )
    try:
        resync_stripe_invoice(invoice, config, user=request.user)
        messages.success(request, "Brouillon resynchronisé depuis Stripe.")
    except Exception as e:
        logger.exception("Stripe re-sync failed for invoice %s", invoice.uuid)
        messages.error(request, f"Erreur : {e}")
    return redirect("ui:invoice_detail", uuid=uuid)
