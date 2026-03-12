"""OAuth 2.1 views for MCP integration with Claude Desktop.

Provides:
- Well-known metadata endpoints (RFC 9728 + RFC 8414)
- Dynamic Client Registration (RFC 7591)
- Custom AuthorizationView with post-consent success page
"""

import json
import uuid

from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from oauth2_provider.models import get_application_model
from oauth2_provider.views import AuthorizationView as BaseAuthorizationView

Application = get_application_model()


class AuthorizationView(BaseAuthorizationView):
    """Override to show a success page after the user grants consent.

    Instead of a raw HTTP redirect to the client's redirect_uri (which
    leaves the consent page visible in the browser tab), we render a
    "Autorisation accordée" page that auto-redirects via JS.
    """

    def form_valid(self, form):
        from django.shortcuts import render

        response = super().form_valid(form)
        # super().form_valid() returns a redirect response on success
        if hasattr(response, "url"):
            redirect_uri = response.url
        elif hasattr(response, "headers") and "Location" in response.headers:
            redirect_uri = response.headers["Location"]
        else:
            return response

        client_id = form.cleaned_data.get("client_id")
        application = Application.objects.filter(client_id=client_id).first()

        return render(
            self.request,
            "oauth2_provider/authorized.html",
            {"redirect_uri": redirect_uri, "application": application},
        )


def _base_url(request):
    """Build the base URL from the request (scheme + host)."""
    return f"{request.scheme}://{request.get_host()}"


@require_GET
def protected_resource_metadata(request):
    """RFC 9728 — OAuth Protected Resource Metadata.

    Tells the client (Claude Desktop) where to find the authorization server.
    """
    base = _base_url(request)
    return JsonResponse(
        {
            "resource": f"{base}/mcp/mcp",
            "authorization_servers": [f"{base}/"],
            "bearer_methods_supported": ["header"],
        },
        headers={"Access-Control-Allow-Origin": "*"},
    )


@require_GET
def authorization_server_metadata(request):
    """RFC 8414 — OAuth Authorization Server Metadata.

    Describes all OAuth endpoints so the client can auto-discover them.
    """
    base = _base_url(request)
    return JsonResponse(
        {
            "issuer": f"{base}/",
            "authorization_endpoint": f"{base}/oauth/authorize/",
            "token_endpoint": f"{base}/oauth/token/",
            "registration_endpoint": f"{base}/oauth/register/",
            "revocation_endpoint": f"{base}/oauth/revoke_token/",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": ["mcp"],
        },
        headers={"Access-Control-Allow-Origin": "*"},
    )


class DynamicClientRegistrationView(View):
    """RFC 7591 — Dynamic Client Registration.

    Claude Desktop calls this to register itself before starting the OAuth flow.
    No authentication required (per spec).
    """

    def post(self, request):
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "invalid_client_metadata"}, status=400)

        client_name = data.get("client_name", "MCP Client")
        redirect_uris = data.get("redirect_uris", [])
        grant_types = data.get("grant_types", ["authorization_code", "refresh_token"])
        token_endpoint_auth_method = data.get("token_endpoint_auth_method", "none")

        if not redirect_uris:
            return JsonResponse(
                {
                    "error": "invalid_redirect_uri",
                    "error_description": "At least one redirect_uri is required.",
                },
                status=400,
            )

        client_id = uuid.uuid4().hex

        # Public client (PKCE) — no client_secret
        app = Application.objects.create(
            name=client_name,
            client_id=client_id,
            client_secret="",  # nosec B106 — public client (PKCE), no secret by design
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris=" ".join(redirect_uris),
            skip_authorization=False,
        )

        return JsonResponse(
            {
                "client_id": app.client_id,
                "client_name": app.name,
                "redirect_uris": redirect_uris,
                "grant_types": grant_types,
                "response_types": ["code"],
                "token_endpoint_auth_method": token_endpoint_auth_method,
            },
            status=201,
        )

    def dispatch(self, request, *args, **kwargs):
        # CORS preflight + no CSRF for this endpoint
        if request.method == "OPTIONS":
            response = JsonResponse({})
            response["Access-Control-Allow-Origin"] = "*"
            response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            response["Access-Control-Allow-Headers"] = "Content-Type"
            return response
        return super().dispatch(request, *args, **kwargs)


dynamic_client_registration = csrf_exempt(DynamicClientRegistrationView.as_view())
