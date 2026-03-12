"""Tests that reproduce the examples in docs/api-guide.md.

If a test here breaks, the corresponding documentation example is outdated.
"""

import pytest
from rest_framework.test import APIClient


@pytest.mark.django_db
class TestDocsRegisterAndTokens:
    """Reproduces: Authentication section of api-guide.md."""

    def setup_method(self):
        from django.core.cache import cache

        cache.clear()

    def test_register_sends_verification(self):
        client = APIClient()

        # Register (api-guide: Register a new account)
        resp = client.post(
            "/api/v1/auth/register/",
            {
                "email": "docuser@example.com",
                "password": "securepassword123",
                "org_name": "My Company",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        # No JWT — email verification required first
        assert "detail" in data
        assert "access" not in data

    def test_token_obtain_after_verification(self):
        from django.contrib.auth.models import User

        client = APIClient()

        # First register
        client.post(
            "/api/v1/auth/register/",
            {
                "email": "login@example.com",
                "password": "securepassword123",
                "org_name": "Login Corp",
            },
        )

        # Manually verify email (simulates clicking the link)
        user = User.objects.get(email="login@example.com")
        user.profile.email_verified = True
        user.profile.save(update_fields=["email_verified"])

        # Then login (api-guide: Obtain tokens)
        resp = client.post(
            "/api/v1/auth/token/",
            {
                "email": "login@example.com",
                "password": "securepassword123",
            },
        )
        assert resp.status_code == 200
        assert "access" in resp.json()
        assert "refresh" in resp.json()


@pytest.mark.django_db
class TestDocsSupplierCRUD:
    """Reproduces: Suppliers section of api-guide.md."""

    def test_supplier_crud(self, auth_api_client, org):
        # Create (api-guide: Create a supplier)
        resp = auth_api_client.post(
            "/api/v1/suppliers/",
            {
                "name": "ACME Corp",
                "siren": "123456789",
                "email": "contact@acme.com",
            },
        )
        assert resp.status_code == 201
        supplier = resp.json()
        supplier_uuid = supplier["uuid"]
        assert supplier["name"] == "ACME Corp"

        # Update (api-guide: Update a supplier)
        resp = auth_api_client.patch(
            f"/api/v1/suppliers/{supplier_uuid}/",
            {"email": "new-email@acme.com"},
            format="json",
        )
        assert resp.status_code == 200

        # Delete (api-guide: Delete a supplier)
        resp = auth_api_client.delete(f"/api/v1/suppliers/{supplier_uuid}/")
        assert resp.status_code == 204


@pytest.mark.django_db
class TestDocsInvoiceCreateWithSupplierId:
    """Reproduces: Create an invoice (referenced supplier) from api-guide.md."""

    def test_create_invoice_with_supplier_id_and_idempotency(
        self, auth_api_client, org, supplier
    ):
        # Create invoice (api-guide: Create an invoice (referenced supplier))
        payload = {
            "supplier_id": str(supplier.uuid),
            "en16931_data": {
                "references": {
                    "issueDate": "2026-01-15",
                    "dueDate": "2026-02-15",
                    "currencyCode": "EUR",
                },
                "invoiceLines": [
                    {"itemName": "Service", "quantity": "1", "unitNetPrice": "100.00"}
                ],
                "totals": {
                    "totalNetAmount": "100.00",
                    "vatAmount": "20.00",
                    "totalGrossAmount": "120.00",
                },
            },
        }

        resp = auth_api_client.post(
            "/api/v1/invoices/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="doc-test-key-1",
        )
        assert resp.status_code == 201
        invoice = resp.json()
        assert invoice["status"] == "draft"

        # Idempotency (api-guide: Idempotency)
        resp2 = auth_api_client.post(
            "/api/v1/invoices/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="doc-test-key-1",
        )
        assert resp2.status_code == 201
        assert resp2.json()["uuid"] == invoice["uuid"]


@pytest.mark.django_db
class TestDocsWebhookEndpoint:
    """Reproduces: Webhooks section of api-guide.md."""

    def test_create_webhook_endpoint(self, auth_api_client, org):
        # Create (api-guide: Create a webhook endpoint)
        resp = auth_api_client.post(
            "/api/v1/webhooks/",
            {
                "url": "https://your-app.example.com/webhook",
                "secret": "your-webhook-secret",
                "events": ["invoice.validated", "invoice.transmitted"],
            },
            format="json",
        )
        assert resp.status_code == 201
        endpoint = resp.json()
        assert "secret" not in endpoint  # write-only


@pytest.mark.django_db
class TestDocsHmacVerification:
    """Reproduces: Verifying HMAC-SHA256 signatures from api-guide.md."""

    def test_hmac_verification_function(self):
        import hashlib
        import hmac

        # This is the exact function from the docs
        def verify_webhook(request_body: bytes, signature: str, secret: str) -> bool:
            expected = hmac.new(
                secret.encode("utf-8"),
                request_body,
                hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(expected, signature)

        body = b'{"event": "invoice.validated", "data": {}}'
        secret = "test-secret"
        sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

        assert verify_webhook(body, sig, secret) is True
        assert verify_webhook(body, "wrong-sig", secret) is False
