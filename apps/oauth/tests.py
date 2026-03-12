"""Tests for OAuth 2.1 provider — metadata, Dynamic Client Registration, flows."""

import json

import pytest
from django.contrib.auth.models import User
from oauth2_provider.models import get_application_model

from apps.core.models import Organization, OrganizationMembership

Application = get_application_model()


# ── Well-known metadata endpoints ────────────────────────────────────


@pytest.mark.django_db
class TestProtectedResourceMetadata:
    """RFC 9728 — OAuth Protected Resource Metadata."""

    def test_returns_resource_metadata(self, client):
        resp = client.get("/.well-known/oauth-protected-resource")
        assert resp.status_code == 200
        data = resp.json()
        assert "/mcp/mcp" in data["resource"]
        assert len(data["authorization_servers"]) >= 1
        assert "header" in data["bearer_methods_supported"]

    def test_cors_header_present(self, client):
        resp = client.get("/.well-known/oauth-protected-resource")
        assert resp["Access-Control-Allow-Origin"] == "*"


@pytest.mark.django_db
class TestAuthorizationServerMetadata:
    """RFC 8414 — OAuth Authorization Server Metadata."""

    def test_returns_server_metadata(self, client):
        resp = client.get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        data = resp.json()
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data
        assert "registration_endpoint" in data
        assert "revocation_endpoint" in data
        assert "code" in data["response_types_supported"]
        assert "authorization_code" in data["grant_types_supported"]
        assert "S256" in data["code_challenge_methods_supported"]

    def test_cors_header_present(self, client):
        resp = client.get("/.well-known/oauth-authorization-server")
        assert resp["Access-Control-Allow-Origin"] == "*"


# ── Dynamic Client Registration (RFC 7591) ───────────────────────────


@pytest.mark.django_db
class TestDynamicClientRegistration:
    """RFC 7591 — Dynamic Client Registration endpoint."""

    def test_register_public_client(self, client):
        resp = client.post(
            "/oauth/register/",
            data=json.dumps(
                {
                    "client_name": "Claude Desktop",
                    "redirect_uris": ["http://localhost:3000/callback"],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "token_endpoint_auth_method": "none",
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "client_id" in data
        assert data["client_name"] == "Claude Desktop"
        assert "http://localhost:3000/callback" in data["redirect_uris"]

        # Verify app was created in DB
        app = Application.objects.get(client_id=data["client_id"])
        assert app.client_type == Application.CLIENT_PUBLIC
        assert app.authorization_grant_type == Application.GRANT_AUTHORIZATION_CODE

    def test_register_without_redirect_uri_fails(self, client):
        resp = client.post(
            "/oauth/register/",
            data=json.dumps({"client_name": "Bad Client", "redirect_uris": []}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "redirect_uri" in resp.json()["error"]

    def test_register_with_invalid_json_fails(self, client):
        resp = client.post(
            "/oauth/register/",
            data="not json",
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_register_multiple_redirect_uris(self, client):
        resp = client.post(
            "/oauth/register/",
            data=json.dumps(
                {
                    "client_name": "Multi-URI Client",
                    "redirect_uris": [
                        "http://localhost:3000/cb1",
                        "http://localhost:3000/cb2",
                    ],
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 201
        app = Application.objects.get(client_id=resp.json()["client_id"])
        assert "cb1" in app.redirect_uris
        assert "cb2" in app.redirect_uris

    def test_options_preflight(self, client):
        resp = client.options("/oauth/register/")
        assert resp.status_code == 200
        assert resp["Access-Control-Allow-Origin"] == "*"
        assert "POST" in resp["Access-Control-Allow-Methods"]

    def test_default_client_name(self, client):
        resp = client.post(
            "/oauth/register/",
            data=json.dumps({"redirect_uris": ["http://localhost/callback"]}),
            content_type="application/json",
        )
        assert resp.status_code == 201
        assert resp.json()["client_name"] == "MCP Client"


# ── Authorization endpoint ───────────────────────────────────────────


@pytest.mark.django_db
class TestAuthorizationEndpoint:
    """Test the authorization endpoint requires login."""

    def test_unauthenticated_redirects_to_login(self, client):
        app = Application.objects.create(
            name="test-app",
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris="http://localhost:3000/callback",
        )
        resp = client.get(
            "/oauth/authorize/",
            {
                "response_type": "code",
                "client_id": app.client_id,
                "redirect_uri": "http://localhost:3000/callback",
                "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
                "code_challenge_method": "S256",
                "scope": "mcp",
            },
        )
        # Should redirect to login page
        assert resp.status_code == 302
        assert "/login" in resp.url or "/accounts/login" in resp.url


# ── Token endpoint ───────────────────────────────────────────────────


@pytest.mark.django_db
class TestTokenEndpoint:
    """Test the token exchange endpoint."""

    def test_invalid_grant_returns_error(self, client):
        resp = client.post(
            "/oauth/token/",
            {
                "grant_type": "authorization_code",
                "code": "invalid_code",
                "redirect_uri": "http://localhost/callback",
                "client_id": "nonexistent",
            },
        )
        # DOT returns 400 or 401 for invalid grants
        assert resp.status_code in (400, 401)

    def test_missing_grant_type_fails(self, client):
        resp = client.post("/oauth/token/", {})
        assert resp.status_code in (400, 401)


# ── Token revocation ─────────────────────────────────────────────────


@pytest.mark.django_db
class TestTokenRevocation:
    """Test the token revocation endpoint."""

    def test_revoke_valid_token(self, client):
        from datetime import timedelta

        from django.utils import timezone

        org = Organization.objects.create(name="Rev Org", slug="rev-org")
        user = User.objects.create_user(
            username="rev@test.local", password="testpass123"
        )
        OrganizationMembership.objects.create(user=user, organization=org, role="owner")
        app = Application.objects.create(
            name="rev-app",
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris="http://localhost/callback",
        )
        from oauth2_provider.models import AccessToken

        AccessToken.objects.create(
            user=user,
            token="token_to_revoke",
            application=app,
            expires=timezone.now() + timedelta(hours=1),
            scope="mcp",
        )
        resp = client.post(
            "/oauth/revoke_token/",
            {"token": "token_to_revoke", "client_id": app.client_id},
        )
        # RFC 7009: always return 200 (even if token not found)
        assert resp.status_code == 200

    def test_revoke_nonexistent_token_with_client(self, client):
        """RFC 7009: revocation of an unknown token should still succeed."""
        app = Application.objects.create(
            name="rev-app2",
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris="http://localhost/callback",
        )
        resp = client.post(
            "/oauth/revoke_token/",
            {"token": "nonexistent_token", "client_id": app.client_id},
        )
        assert resp.status_code == 200
