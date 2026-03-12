"""Tests for MCP server — auth middleware, org resolution, context vars."""

import json
from datetime import timedelta

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import User
from django.utils import timezone

from apps.core.models import APIKey, Organization, OrganizationMembership

# ── Helpers ──────────────────────────────────────────────────────────


def _build_scope(path="/mcp/mcp", method="POST", headers=None):
    """Build an ASGI HTTP scope dict."""
    raw_headers = []
    for key, val in (headers or {}).items():
        raw_headers.append((key.lower().encode(), val.encode()))
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": raw_headers,
        "server": ("localhost", 8000),
    }


async def _get_response(app, scope):
    """Send a minimal request through an ASGI app and return status + body."""
    body_parts = []
    status_code = None
    response_headers = {}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        nonlocal status_code, response_headers
        if message["type"] == "http.response.start":
            status_code = message["status"]
            response_headers = {
                k.decode(): v.decode() for k, v in message.get("headers", [])
            }
        elif message["type"] == "http.response.body":
            body_parts.append(message.get("body", b""))

    await app(scope, receive, send)
    body = b"".join(body_parts)
    return status_code, response_headers, body


def _make_dummy_app():
    """A dummy ASGI app that echoes the current org from the context var."""
    from apps.mcp.server import current_org

    async def dummy_app(scope, receive, send):
        org = current_org.get(None)
        body = json.dumps({"org_name": org.name if org else None}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return dummy_app


# ── MCPAuthMiddleware tests ──────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestMCPAuthMiddleware:
    """Test the auth middleware that protects the MCP endpoint."""

    @pytest.fixture
    def mcp_org(self):
        return Organization.objects.create(name="MCP Org", slug="mcp-org")

    @pytest.fixture
    def mcp_user(self, mcp_org):
        user = User.objects.create_user(
            username="mcp@test.local", email="mcp@test.local", password="testpass123"
        )
        OrganizationMembership.objects.create(
            user=user, organization=mcp_org, role="owner"
        )
        return user

    @pytest.fixture
    def api_key(self, mcp_user, mcp_org):
        return APIKey.generate(name="test-key", user=mcp_user, organization=mcp_org)

    @pytest.fixture
    def middleware(self):
        from apps.mcp.middleware import MCPAuthMiddleware

        return MCPAuthMiddleware(_make_dummy_app())

    @pytest.mark.asyncio
    async def test_no_auth_header_returns_401(self, middleware):
        scope = _build_scope()
        status, headers, body = await _get_response(middleware, scope)
        assert status == 401
        data = json.loads(body)
        assert "Authorization" in data["error"]

    @pytest.mark.asyncio
    async def test_invalid_bearer_format_returns_401(self, middleware):
        scope = _build_scope(headers={"authorization": "Basic abc123"})
        status, _, body = await _get_response(middleware, scope)
        assert status == 401

    @pytest.mark.asyncio
    async def test_invalid_api_key_returns_401(self, middleware):
        scope = _build_scope(headers={"authorization": "Bearer fp_invalid_key"})
        status, _, body = await _get_response(middleware, scope)
        assert status == 401

    @pytest.mark.asyncio
    async def test_valid_api_key_sets_org(self, middleware, api_key, mcp_org):
        _instance, raw_key = api_key
        scope = _build_scope(headers={"authorization": f"Bearer {raw_key}"})
        status, _, body = await _get_response(middleware, scope)
        assert status == 200
        data = json.loads(body)
        assert data["org_name"] == mcp_org.name

    @pytest.mark.asyncio
    async def test_expired_api_key_returns_401(self, middleware, api_key):
        instance, raw_key = api_key
        instance.expires_at = timezone.now() - timedelta(hours=1)
        await sync_to_async(instance.save)()
        scope = _build_scope(headers={"authorization": f"Bearer {raw_key}"})
        status, _, _ = await _get_response(middleware, scope)
        assert status == 401

    @pytest.mark.asyncio
    async def test_revoked_api_key_returns_401(self, middleware, api_key):
        instance, raw_key = api_key
        instance.is_active = False
        await sync_to_async(instance.save)()
        scope = _build_scope(headers={"authorization": f"Bearer {raw_key}"})
        status, _, _ = await _get_response(middleware, scope)
        assert status == 401

    @pytest.mark.asyncio
    async def test_valid_oauth_token_sets_org(self, middleware, mcp_user, mcp_org):
        from oauth2_provider.models import AccessToken, get_application_model

        Application = get_application_model()
        app = await sync_to_async(Application.objects.create)(
            name="test-app",
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
        )
        await sync_to_async(AccessToken.objects.create)(
            user=mcp_user,
            token="test_oauth_token_abc",
            application=app,
            expires=timezone.now() + timedelta(hours=1),
            scope="mcp",
        )
        scope = _build_scope(headers={"authorization": "Bearer test_oauth_token_abc"})
        status, _, body = await _get_response(middleware, scope)
        assert status == 200
        data = json.loads(body)
        assert data["org_name"] == mcp_org.name

    @pytest.mark.asyncio
    async def test_expired_oauth_token_returns_401(self, middleware, mcp_user):
        from oauth2_provider.models import AccessToken, get_application_model

        Application = get_application_model()
        app = await sync_to_async(Application.objects.create)(
            name="test-app-expired",
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
        )
        await sync_to_async(AccessToken.objects.create)(
            user=mcp_user,
            token="expired_oauth_token",
            application=app,
            expires=timezone.now() - timedelta(hours=1),
            scope="mcp",
        )
        scope = _build_scope(headers={"authorization": "Bearer expired_oauth_token"})
        status, _, _ = await _get_response(middleware, scope)
        assert status == 401

    @pytest.mark.asyncio
    async def test_oauth_user_without_membership_returns_401(self, middleware):
        from oauth2_provider.models import AccessToken, get_application_model

        orphan_user = await sync_to_async(User.objects.create_user)(
            username="orphan@test.local", password="testpass123"
        )
        Application = get_application_model()
        app = await sync_to_async(Application.objects.create)(
            name="test-app-orphan",
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
        )
        await sync_to_async(AccessToken.objects.create)(
            user=orphan_user,
            token="orphan_token",
            application=app,
            expires=timezone.now() + timedelta(hours=1),
            scope="mcp",
        )
        scope = _build_scope(headers={"authorization": "Bearer orphan_token"})
        status, _, _ = await _get_response(middleware, scope)
        assert status == 401

    @pytest.mark.asyncio
    async def test_non_http_scope_passes_through(self):
        from apps.mcp.middleware import MCPAuthMiddleware

        called = False

        async def inner_app(scope, receive, send):
            nonlocal called
            called = True

        mw = MCPAuthMiddleware(inner_app)
        scope = {"type": "websocket"}
        await mw(scope, lambda: None, lambda msg: None)
        assert called

    @pytest.mark.asyncio
    async def test_www_authenticate_header_present(self, middleware):
        scope = _build_scope()
        status, headers, _ = await _get_response(middleware, scope)
        assert status == 401
        assert "www-authenticate" in headers


# ── Multi-tenant isolation ───────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestMCPMultiTenantIsolation:
    """Ensure each API key resolves to its own org."""

    @pytest.mark.asyncio
    async def test_different_keys_different_orgs(self):
        from apps.mcp.middleware import MCPAuthMiddleware

        org_a = await sync_to_async(Organization.objects.create)(
            name="Org A", slug="org-a"
        )
        org_b = await sync_to_async(Organization.objects.create)(
            name="Org B", slug="org-b"
        )
        user_a = await sync_to_async(User.objects.create_user)(
            username="a@test.local", password="pass"
        )
        user_b = await sync_to_async(User.objects.create_user)(
            username="b@test.local", password="pass"
        )
        await sync_to_async(OrganizationMembership.objects.create)(
            user=user_a, organization=org_a, role="owner"
        )
        await sync_to_async(OrganizationMembership.objects.create)(
            user=user_b, organization=org_b, role="owner"
        )
        _, key_a = await sync_to_async(APIKey.generate)(
            name="key-a", user=user_a, organization=org_a
        )
        _, key_b = await sync_to_async(APIKey.generate)(
            name="key-b", user=user_b, organization=org_b
        )

        mw = MCPAuthMiddleware(_make_dummy_app())

        # Request with key A
        scope_a = _build_scope(headers={"authorization": f"Bearer {key_a}"})
        status_a, _, body_a = await _get_response(mw, scope_a)
        assert status_a == 200
        assert json.loads(body_a)["org_name"] == "Org A"

        # Request with key B
        scope_b = _build_scope(headers={"authorization": f"Bearer {key_b}"})
        status_b, _, body_b = await _get_response(mw, scope_b)
        assert status_b == 200
        assert json.loads(body_b)["org_name"] == "Org B"


# ── Server tool registration ─────────────────────────────────────────


class TestMCPToolRegistration:
    """Verify all assistant tools are registered in the MCP server."""

    def test_all_tools_registered(self):
        from apps.assistant.tools import TOOL_REGISTRY
        from apps.mcp.server import mcp

        registry_names = set(TOOL_REGISTRY.keys())
        # FastMCP._tool_manager stores registered tools
        assert len(mcp._tool_manager._tools) >= len(registry_names)

    def test_tool_has_description(self):
        from apps.mcp.server import mcp

        for name, tool in mcp._tool_manager._tools.items():
            assert tool.description, f"Tool {name} has no description"


# ── Context var isolation ────────────────────────────────────────────


class TestCurrentOrgContextVar:
    """Verify the context var is properly isolated."""

    def test_default_raises_lookup_error(self):
        from apps.mcp.server import current_org

        # Without a set(), get() should raise LookupError
        with pytest.raises(LookupError):
            current_org.get()

    def test_get_with_default(self):
        from apps.mcp.server import current_org

        assert current_org.get(None) is None

    def test_set_and_reset(self):
        from apps.mcp.server import current_org

        token = current_org.set("test_org")
        assert current_org.get() == "test_org"
        current_org.reset(token)
        assert current_org.get(None) is None
