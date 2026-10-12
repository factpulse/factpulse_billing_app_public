"""Configuration overview: one read-only page summing up every setting."""

from urllib.parse import urlsplit

from django.apps import apps
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.billing.models import Invoice, Supplier
from apps.core.models import APIKey
from apps.factpulse.client import FactPulseError

OK, WARN, OFF = "ok", "warn", "off"


def _card(key, title, state, rows, action=None, summary=""):
    return {
        "key": key,
        "title": title,
        "state": state,
        "summary": summary,
        "rows": rows,
        "action": action,
    }


def _mask_iban(iban):
    iban = (iban or "").replace(" ", "")
    return f"{iban[:4]} •••• {iban[-4:]}" if len(iban) > 8 else ""


def _company_card(org):
    supplier = (
        Supplier.objects.filter(organization=org, archived=False, is_default=True)
        .order_by("-updated_at")
        .first()
    )
    if not supplier:
        others = Supplier.objects.filter(organization=org, archived=False).exists()
        return _card(
            "company",
            "Votre entreprise",
            WARN,
            [],
            (
                reverse("ui:supplier_list")
                if others
                else reverse("ui:supplier_create"),
                "Choisir le fournisseur par défaut" if others else "Ajouter",
            ),
            summary="Aucun fournisseur par défaut : c'est lui qui émet vos factures.",
        )

    franchise = supplier.vat_regime == Supplier.VatRegime.FRANCHISE
    rows = [
        ("Raison sociale", supplier.name),
        ("SIRET", supplier.siret or supplier.siren or "—"),
        (
            "TVA",
            "Franchise en base (art. 293 B)"
            if franchise
            else supplier.vat_number or "—",
        ),
        ("Régime", supplier.get_vat_regime_display()),
        ("IBAN", _mask_iban(supplier.iban) or "—"),
    ]
    missing = []
    if not (supplier.siret or supplier.siren):
        missing.append("SIRET")
    if not franchise and not supplier.vat_number:
        missing.append("numéro de TVA")
    return _card(
        "company",
        "Votre entreprise",
        WARN if missing else OK,
        rows,
        (reverse("ui:supplier_edit", args=[supplier.uuid]), "Modifier"),
        summary=f"À compléter : {', '.join(missing)}." if missing else supplier.name,
    )


def _pa_card(org, is_owner):
    from apps.factpulse.client import client  # lazy: mocked in tests

    action = (reverse("ui:pdp_settings"), "Configurer") if is_owner else None
    if not org.factpulse_client_uid or not client.is_configured:
        return _card(
            "pa",
            "Plateforme agréée",
            WARN,
            [],
            action,
            summary="Compte FactPulse non provisionné.",
        )
    try:
        superpdp = client.get_superpdp_status(org.factpulse_client_uid) or {}
        pdp = client.get_pdp_config(org.factpulse_client_uid) or {}
    except FactPulseError:
        return _card(
            "pa",
            "Plateforme agréée",
            WARN,
            [],
            action,
            summary="Statut indisponible pour le moment.",
        )

    last = (
        Invoice.objects.filter(organization=org, deleted_at__isnull=True)
        .exclude(pdp_transmission_id="")
        .order_by("-updated_at")
        .values_list("updated_at", flat=True)
        .first()
    )
    last_row = ("Dernière transmission", last.strftime("%d/%m/%Y") if last else "—")

    if superpdp.get("connected"):
        sandbox = superpdp.get("modeSandbox")
        rows = [
            ("Plateforme", "SuperPDP"),
            ("Environnement", "Bac à sable (tests)" if sandbox else "Production"),
            last_row,
        ]
        if superpdp.get("lastError"):
            rows.append(("Dernière erreur", superpdp["lastError"]))
        return _card(
            "pa",
            "Plateforme agréée",
            WARN if sandbox or superpdp.get("lastError") else OK,
            rows,
            action,
            summary=(
                "SuperPDP en bac à sable : les factures ne partent pas pour de vrai."
                if sandbox
                else "SuperPDP connecté."
            ),
        )

    if pdp.get("isConfigured"):
        host = urlsplit(pdp.get("flowServiceUrl") or "").hostname or "—"
        active = pdp.get("isActive")
        rows = [
            ("Plateforme", host),
            (
                "Environnement",
                "Bac à sable (tests)" if pdp.get("modeSandbox") else "Production",
            ),
            ("Connexion", "Active" if active else "Désactivée"),
            last_row,
        ]
        if pdp.get("lastTestAt"):
            rows.append(
                ("Dernier test", "Réussi" if pdp.get("lastTestSuccess") else "Échec")
            )
        return _card(
            "pa",
            "Plateforme agréée",
            OK if active and pdp.get("lastTestSuccess", True) else WARN,
            rows,
            action,
            summary=f"Connectée à {host}." if active else "Connexion désactivée.",
        )

    return _card(
        "pa",
        "Plateforme agréée",
        WARN,
        [],
        action,
        summary="Aucune plateforme agréée : vos factures ne peuvent pas être transmises.",
    )


def _sources(org, is_owner):
    """Where invoices come from, and what happens to each of them."""
    sources = [
        {
            "name": "Saisie et API",
            "detail": "Factures créées ici ou par l'API",
            "arrival": "Brouillon",
            "validation": "Par vous",
            "transmission": "Par vous",
            "state": OK,
            "action": None,
        }
    ]

    if apps.is_installed("apps.payments"):
        from apps.payments.models import ProviderConfig

        configs = {
            c.provider: c
            for c in ProviderConfig.objects.filter(organization=org, is_active=True)
        }
        stripe = configs.get(ProviderConfig.Provider.STRIPE)
        if stripe and stripe.api_key:
            last = (
                Invoice.objects.filter(organization=org, external_id__startswith="in_")
                .order_by("-created_at")
                .values_list("created_at", flat=True)
                .first()
            )
            sources.append(
                {
                    "name": "Stripe (clé API)",
                    "detail": (
                        f"Dernière facture reçue le {last.strftime('%d/%m/%Y')}"
                        if last
                        else "Aucune facture reçue"
                    ),
                    "arrival": (
                        "Brouillon, dès la finalisation"
                        if stripe.webhook_secret
                        else "Import manuel (webhook non configuré)"
                    ),
                    "validation": "Par vous",
                    "transmission": "Par vous",
                    "state": OK if stripe.webhook_secret else WARN,
                    "action": (reverse("ui:payment_settings"), "Modifier")
                    if is_owner
                    else None,
                }
            )
        gocardless = configs.get(ProviderConfig.Provider.GOCARDLESS)
        if gocardless:
            sources.append(
                {
                    "name": "GoCardless",
                    "detail": "Paiements reportés sur vos factures",
                    "arrival": "—",
                    "validation": "—",
                    "transmission": "—",
                    "state": OK,
                    "action": (reverse("ui:payment_settings"), "Modifier")
                    if is_owner
                    else None,
                }
            )

    if apps.is_installed("apps.connect"):
        from apps.connect.overview import stripe_app_source

        source = stripe_app_source(org)
        if source:
            sources.append(source)
    return sources


def _integrations(org):
    from apps.webhooks.models import WebhookEndpoint

    keys = APIKey.objects.filter(organization=org, is_active=True).count()
    hooks = WebhookEndpoint.objects.filter(organization=org, is_active=True).count()
    return [
        ("Clés API actives", keys, reverse("ui:api_key_list")),
        ("Webhooks sortants", hooks, None),
    ]


@login_required
def settings_overview(request):
    org = request.organization
    if not org:
        return redirect("ui:dashboard")

    from apps.core.models import OrganizationMembership

    is_owner = OrganizationMembership.objects.filter(
        user=request.user, organization=org, role=OrganizationMembership.Role.OWNER
    ).exists()

    cards = [_company_card(org), _pa_card(org, is_owner)]
    sources = _sources(org, is_owner)
    todo = [c for c in cards if c["state"] == WARN] + [
        {"title": s["name"], "summary": s["arrival"], "action": s["action"]}
        for s in sources
        if s["state"] == WARN
    ]

    return render(
        request,
        "ui/settings_overview.html",
        {
            "cards": cards,
            "sources": sources,
            "todo": todo,
            "integrations": _integrations(org),
        },
    )
