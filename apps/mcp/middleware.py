"""ASGI middleware for authentication on the MCP endpoint.

Supports two auth methods:
- API Key: Bearer fp_... (Claude Code, scripts, integrations)
- OAuth:   Bearer <opaque> (Claude Desktop, via django-oauth-toolkit)
"""

import logging

from asgiref.sync import sync_to_async
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from apps.core.models import APIKey, OrganizationMembership
from apps.mcp.server import current_org

logger = logging.getLogger(__name__)


def _capture_sentry_exception():
    """Send exception to Sentry if the SDK is active. No-op otherwise."""
    try:
        import sentry_sdk

        sentry_sdk.capture_exception()
    except ImportError:
        pass


_WWW_AUTHENTICATE = 'Bearer resource_metadata="/.well-known/oauth-protected-resource"'


class MCPAuthMiddleware:
    """Validate Bearer token (API key or OAuth), resolve org, set contextvar."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        auth_header = request.headers.get("authorization", "")

        if not auth_header.startswith("Bearer "):
            response = JSONResponse(
                {"error": "Authorization header required (Bearer <token>)"},
                status_code=401,
                headers={"WWW-Authenticate": _WWW_AUTHENTICATE},
            )
            await response(scope, receive, send)
            return

        token_str = auth_header[7:]

        # Try API key first (starts with fp_), then OAuth token
        if token_str.startswith(APIKey.PREFIX):
            org = await self._auth_api_key(token_str)
        else:
            org = await self._auth_oauth_token(token_str)

        if not org:
            response = JSONResponse(
                {"error": "Invalid or expired token"},
                status_code=401,
                headers={"WWW-Authenticate": _WWW_AUTHENTICATE},
            )
            await response(scope, receive, send)
            return

        ctx_token = current_org.set(org)
        try:
            await self.app(scope, receive, send)
        except Exception:
            _capture_sentry_exception()
            raise
        finally:
            current_org.reset(ctx_token)

    @staticmethod
    @sync_to_async
    def _auth_api_key(raw_key: str):
        result = APIKey.authenticate(raw_key)
        if result is None:
            return None
        _user, org = result
        return org

    @staticmethod
    @sync_to_async
    def _auth_oauth_token(token_str: str):
        """Validate an OAuth 2.1 access token (django-oauth-toolkit)."""
        from django.utils import timezone
        from oauth2_provider.models import AccessToken as OAuthAccessToken

        try:
            token = OAuthAccessToken.objects.select_related("user").get(
                token=token_str,
                expires__gt=timezone.now(),
            )
        except OAuthAccessToken.DoesNotExist:
            return None

        if not token.user:
            return None

        membership = (
            OrganizationMembership.objects.select_related("organization")
            .filter(user=token.user)
            .first()
        )
        return membership.organization if membership else None
