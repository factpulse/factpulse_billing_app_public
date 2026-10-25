"""PDP settings, SIRENE lookup, and directory lookup views."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.billing.services.sirene_client import SireneError
from apps.billing.services.sirene_client import lookup as sirene_lookup_fn
from apps.core.models import OrganizationMembership
from apps.core.redirects import ExternalRedirect
from apps.factpulse.client import FactPulseError, FactPulseUnavailableError


@login_required
def pdp_settings(request):
    org = request.organization
    if not org:
        return redirect("ui:dashboard")

    # Owner-only access
    membership = OrganizationMembership.objects.filter(
        user=request.user, organization=org
    ).first()
    if not membership or membership.role != OrganizationMembership.Role.OWNER:
        return HttpResponseForbidden(
            "Accès réservé aux propriétaires de l'organisation."
        )

    from apps.factpulse.client import client  # lazy: mocked in tests

    client_uid = str(org.factpulse_client_uid) if org.factpulse_client_uid else None
    pdp_config = {}
    pdp_status_loaded = False
    api_error = None

    superpdp = {}
    if client_uid and client.is_configured:
        try:
            pdp_config = client.get_pdp_config(client_uid) or {}
            pdp_status_loaded = True
            superpdp = client.get_superpdp_status(client_uid) or {}
        except FactPulseError as e:
            api_error = str(e)

    connected = request.GET.get("superpdp_connected")
    if connected == "true":
        messages.success(request, "SuperPDP connecté.")
    elif connected == "false":
        messages.error(request, "Connexion SuperPDP non aboutie. Réessayez.")

    if request.method == "POST":
        if not client_uid:
            messages.error(request, "Organisation non provisionnée sur FactPulse.")
            return redirect("ui:pdp_settings")

        action = request.POST.get("action", "save")

        if action == "superpdp_connect":
            return_url = request.build_absolute_uri(reverse("ui:pdp_settings"))
            try:
                return ExternalRedirect(
                    client.superpdp_connect_url(
                        client_uid,
                        mode_sandbox=request.POST.get("mode_sandbox") == "on",
                        return_url=return_url,
                    )
                )
            except FactPulseError as e:
                messages.error(request, f"Erreur FactPulse : {e}")
                return redirect("ui:pdp_settings")

        if action == "superpdp_disconnect":
            try:
                client.disconnect_superpdp(client_uid)
                messages.success(request, "SuperPDP déconnecté.")
            except FactPulseError as e:
                messages.error(request, f"Erreur FactPulse : {e}")
            return redirect("ui:pdp_settings")

        if action == "toggle_active":
            if not pdp_status_loaded or not pdp_config.get("isConfigured"):
                messages.error(
                    request, "Aucune configuration plateforme agréée à activer."
                )
                return redirect("ui:pdp_settings")

            new_active = not pdp_config.get("isActive")
            try:
                client.push_pdp_config(client_uid, {"isActive": new_active})
                if new_active:
                    messages.success(request, "Connexion plateforme agréée réactivée.")
                else:
                    messages.success(request, "Connexion plateforme agréée désactivée.")
            except FactPulseError as e:
                messages.error(request, f"Erreur FactPulse : {e}")

            return redirect("ui:pdp_settings")

        flow_service_url = request.POST.get("flowServiceUrl", "").strip()
        token_url = request.POST.get("tokenUrl", "").strip()
        oauth_client_id = request.POST.get("oauthClientId", "").strip()
        client_secret = request.POST.get("clientSecret", "").strip()

        if not all([flow_service_url, token_url, oauth_client_id, client_secret]):
            messages.error(request, "Tous les champs sont requis.")
            # Re-populate form values from POST so user doesn't lose input
            pdp_config.update(
                {
                    "flowServiceUrl": flow_service_url,
                    "tokenUrl": token_url,
                    "oauthClientId": oauth_client_id,
                }
            )
            return render(
                request,
                "ui/pdp_settings.html",
                {
                    "client_uid": client_uid,
                    "pdp_config": pdp_config,
                    "pdp_status_loaded": pdp_status_loaded,
                    "superpdp": superpdp,
                },
            )

        config = {
            "flowServiceUrl": flow_service_url,
            "tokenUrl": token_url,
            "oauthClientId": oauth_client_id,
            "clientSecret": client_secret,
            "encryptionMode": "fernet",
            "isActive": True,
            "modeSandbox": False,
        }

        try:
            client.push_pdp_config(client_uid, config)
            messages.success(
                request, "Configuration plateforme agréée enregistrée avec succès."
            )
        except FactPulseError as e:
            messages.error(request, f"Erreur FactPulse : {e}")

        return redirect("ui:pdp_settings")

    return render(
        request,
        "ui/pdp_settings.html",
        {
            "client_uid": client_uid,
            "pdp_config": pdp_config,
            "pdp_status_loaded": pdp_status_loaded,
            "api_error": api_error,
            "superpdp": superpdp,
        },
    )


@login_required
def sirene_lookup(request):
    query = request.GET.get("q", "").strip()
    if not query:
        return JsonResponse(
            {"error": "Veuillez saisir un nom ou un numéro SIREN/SIRET."}, status=400
        )
    try:
        data = sirene_lookup_fn(query)
        return JsonResponse({"data": data})
    except SireneError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@login_required
def directory_lookup(request):
    """Lookup AFNOR directory by SIREN — returns raw directory data."""
    siren = request.GET.get("siren", "").strip()
    if not siren or len(siren) != 9 or not siren.isdigit():
        return JsonResponse(
            {"error": "Veuillez fournir un numéro SIREN valide (9 chiffres)."},
            status=400,
        )

    from apps.factpulse.client import client  # lazy: mocked in tests

    org = request.organization
    client_uid = (
        str(org.factpulse_client_uid) if org and org.factpulse_client_uid else None
    )

    if not client.is_configured:
        return JsonResponse(
            {"error": "Le service annuaire n'est pas configuré."}, status=503
        )

    try:
        data = client.search_directory_lines(siren, client_uid=client_uid)
    except FactPulseUnavailableError:
        return JsonResponse({"error": "Service annuaire indisponible."}, status=503)
    except FactPulseError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse({"data": data})
