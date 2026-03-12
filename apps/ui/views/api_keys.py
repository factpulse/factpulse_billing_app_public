"""UI views for API key management."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.core.models import APIKey


@login_required
def api_key_list(request):
    org = request.organization
    if not org:
        return redirect("ui:dashboard")

    keys = APIKey.objects.filter(user=request.user, organization=org)

    # Handle creation
    new_key = None
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create":
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Le nom est obligatoire.")
            else:
                api_key, raw_key = APIKey.generate(
                    name=name, user=request.user, organization=org
                )
                new_key = raw_key
                messages.success(
                    request,
                    "Clé API créée. Copiez-la maintenant, elle ne sera plus visible.",
                )

        elif action == "revoke":
            key_uuid = request.POST.get("key_uuid")
            try:
                api_key = APIKey.objects.get(
                    uuid=key_uuid, user=request.user, organization=org
                )
                api_key.is_active = False
                api_key.save(update_fields=["is_active"])
                messages.success(request, f"Clé « {api_key.name} » révoquée.")
            except APIKey.DoesNotExist:
                messages.error(request, "Clé introuvable.")

            return redirect("ui:api_key_list")

    return render(
        request,
        "ui/api_key_list.html",
        {"keys": keys, "new_key": new_key},
    )
