"""Tests for billing API viewsets."""

from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.billing.factories import (
    CustomerFactory,
    InvoiceFactory,
    NumberingSequenceFactory,
    ProductFactory,
    SupplierFactory,
)
from apps.billing.models import Invoice
from apps.core.models import Organization, OrganizationMembership

# --- Helpers ---


def _auth_client(user, org):
    from rest_framework_simplejwt.tokens import RefreshToken

    client = APIClient()
    refresh = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


# --- Supplier ViewSet ---


@pytest.mark.django_db
class TestSupplierViewSet:
    url = "/api/v1/suppliers/"

    def test_list(self, auth_api_client, org):
        SupplierFactory(organization=org)
        SupplierFactory(organization=org)
        resp = auth_api_client.get(self.url)
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 2

    def test_create(self, auth_api_client):
        resp = auth_api_client.post(
            self.url, {"name": "New Supplier", "siren": "111222333"}
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "New Supplier"

    def test_retrieve(self, auth_api_client, org):
        supplier = SupplierFactory(organization=org)
        resp = auth_api_client.get(f"{self.url}{supplier.uuid}/")
        assert resp.status_code == 200
        assert resp.json()["name"] == supplier.name

    def test_update(self, auth_api_client, org):
        supplier = SupplierFactory(organization=org)
        resp = auth_api_client.patch(
            f"{self.url}{supplier.uuid}/", {"name": "Updated"}, format="json"
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"

    def test_delete(self, auth_api_client, org):
        supplier = SupplierFactory(organization=org)
        resp = auth_api_client.delete(f"{self.url}{supplier.uuid}/")
        assert resp.status_code == 204

    def test_search(self, auth_api_client, org):
        SupplierFactory(organization=org, name="ACME Corp")
        SupplierFactory(organization=org, name="Beta Inc")
        resp = auth_api_client.get(self.url, {"search": "ACME"})
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 1

    def test_org_isolation(self, auth_api_client, org):
        other_org = Organization.objects.create(name="Other", slug="other")
        SupplierFactory(organization=other_org)
        resp = auth_api_client.get(self.url)
        assert len(resp.json()["results"]) == 0


# --- Customer ViewSet ---


@pytest.mark.django_db
class TestCustomerViewSet:
    url = "/api/v1/customers/"

    def test_list(self, auth_api_client, org):
        CustomerFactory(organization=org)
        resp = auth_api_client.get(self.url)
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 1

    def test_create(self, auth_api_client):
        resp = auth_api_client.post(self.url, {"name": "New Customer"})
        assert resp.status_code == 201

    def test_retrieve(self, auth_api_client, org):
        customer = CustomerFactory(organization=org)
        resp = auth_api_client.get(f"{self.url}{customer.uuid}/")
        assert resp.status_code == 200

    def test_update(self, auth_api_client, org):
        customer = CustomerFactory(organization=org)
        resp = auth_api_client.patch(
            f"{self.url}{customer.uuid}/", {"name": "Updated"}, format="json"
        )
        assert resp.status_code == 200

    def test_delete(self, auth_api_client, org):
        customer = CustomerFactory(organization=org)
        resp = auth_api_client.delete(f"{self.url}{customer.uuid}/")
        assert resp.status_code == 204

    def test_invite_owner_only(self, auth_api_client, org):
        customer = CustomerFactory(organization=org)
        resp = auth_api_client.post(
            f"{self.url}{customer.uuid}/invite/", {"email": "invite@test.com"}
        )
        assert resp.status_code == 201
        assert resp.json()["email"] == "invite@test.com"
        assert OrganizationMembership.objects.filter(
            role="customer_access", organization=org
        ).exists()

    def test_invite_member_forbidden(self, org, member_user):
        customer = CustomerFactory(organization=org)
        client = _auth_client(member_user, org)
        resp = client.post(
            f"{self.url}{customer.uuid}/invite/", {"email": "x@test.com"}
        )
        assert resp.status_code == 403


# --- Product ViewSet ---


@pytest.mark.django_db
class TestProductViewSet:
    url = "/api/v1/products/"

    def test_list(self, auth_api_client, org):
        ProductFactory(organization=org)
        resp = auth_api_client.get(self.url)
        assert resp.status_code == 200

    def test_create(self, auth_api_client):
        resp = auth_api_client.post(
            self.url,
            {
                "name": "Widget",
                "default_unit_price": "49.99",
                "default_vat_rate": "20.00",
            },
            format="json",
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "Widget"

    def test_retrieve(self, auth_api_client, org):
        product = ProductFactory(organization=org)
        resp = auth_api_client.get(f"{self.url}{product.uuid}/")
        assert resp.status_code == 200

    def test_update(self, auth_api_client, org):
        product = ProductFactory(organization=org)
        resp = auth_api_client.patch(
            f"{self.url}{product.uuid}/", {"name": "Updated"}, format="json"
        )
        assert resp.status_code == 200


# --- Invoice ViewSet ---


@pytest.mark.django_db
class TestInvoiceViewSet:
    url = "/api/v1/invoices/"

    def test_create_with_supplier_id(self, auth_api_client, org, supplier):
        resp = auth_api_client.post(
            self.url,
            {"supplier_id": str(supplier.uuid), "en16931_data": {}},
            format="json",
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "draft"

    def test_idempotency_key(self, auth_api_client, org, supplier):
        payload = {"supplier_id": str(supplier.uuid), "en16931_data": {}}
        resp1 = auth_api_client.post(
            self.url,
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="idem-123",
        )
        resp2 = auth_api_client.post(
            self.url,
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="idem-123",
        )
        assert resp1.status_code == 201
        assert resp2.status_code == 201
        assert resp1.json()["uuid"] == resp2.json()["uuid"]
        assert Invoice.objects.count() == 1

    def test_patch_with_version(self, auth_api_client, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, organization=org)
        resp = auth_api_client.patch(
            f"{self.url}{invoice.uuid}/",
            {"version": 1, "en16931_data": {"notes": "patched"}},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == 2

    def test_delete_soft(self, auth_api_client, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, organization=org)
        resp = auth_api_client.delete(f"{self.url}{invoice.uuid}/")
        assert resp.status_code == 204

        # Should not appear in list
        list_resp = auth_api_client.get(self.url)
        uuids = [r["uuid"] for r in list_resp.json()["results"]]
        assert str(invoice.uuid) not in uuids

    @patch("apps.factpulse.tasks.generate_and_validate_invoice.delay")
    def test_validate_action(self, mock_delay, auth_api_client, org, supplier):
        NumberingSequenceFactory(supplier=supplier)
        invoice = InvoiceFactory(supplier=supplier, organization=org)
        resp = auth_api_client.post(f"{self.url}{invoice.uuid}/validate/")
        assert resp.status_code == 200
        assert resp.json()["status"] == "processing"
        mock_delay.assert_called_once()

    @patch("apps.factpulse.tasks.transmit_invoice.delay")
    def test_transmit_action(self, mock_delay, auth_api_client, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier, organization=org, status="validated"
        )
        resp = auth_api_client.post(f"{self.url}{invoice.uuid}/transmit/")
        assert resp.status_code == 200
        assert resp.json()["status"] == "transmitting"

    def test_mark_paid_action(self, auth_api_client, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier,
            organization=org,
            status="transmitted",
            en16931_data={"totals": {"totalGrossAmount": "500.00"}},
        )
        resp = auth_api_client.post(
            f"{self.url}{invoice.uuid}/mark-paid/",
            {},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "paid"

    def test_cancel_action(self, auth_api_client, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier,
            organization=org,
            status="validated",
            en16931_data={"references": {"invoiceType": "380"}},
        )
        resp = auth_api_client.post(f"{self.url}{invoice.uuid}/cancel/")
        assert resp.status_code == 201
        data = resp.json()
        assert data["en16931_data"]["references"]["invoiceType"] == "381"

    def test_pdf_draft_no_file_returns_202(self, auth_api_client, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, organization=org)
        with patch("apps.factpulse.tasks.generate_source_pdf.delay"):
            resp = auth_api_client.get(f"{self.url}{invoice.uuid}/pdf/")
        assert resp.status_code == 202

    def test_audit_log(self, auth_api_client, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, organization=org)
        # Create an audit log entry by creating via service
        resp = auth_api_client.get(f"{self.url}{invoice.uuid}/audit-log/")
        assert resp.status_code == 200

    def test_viewer_can_list(self, org, viewer_user, supplier):
        InvoiceFactory(supplier=supplier, organization=org)
        client = _auth_client(viewer_user, org)
        resp = client.get(self.url)
        assert resp.status_code == 200

    def test_viewer_cannot_create(self, org, viewer_user, supplier):
        client = _auth_client(viewer_user, org)
        resp = client.post(
            self.url,
            {"supplier_id": str(supplier.uuid), "en16931_data": {}},
            format="json",
        )
        assert resp.status_code == 403

    def test_list_excludes_deleted(self, auth_api_client, org, supplier):
        inv1 = InvoiceFactory(supplier=supplier, organization=org)
        inv2 = InvoiceFactory(supplier=supplier, organization=org)
        from django.utils import timezone

        inv2.deleted_at = timezone.now()
        inv2.save(update_fields=["deleted_at"])

        resp = auth_api_client.get(self.url)
        uuids = [r["uuid"] for r in resp.json()["results"]]
        assert str(inv1.uuid) in uuids
        assert str(inv2.uuid) not in uuids

    def test_create_missing_supplier_returns_error(self, auth_api_client):
        resp = auth_api_client.post(self.url, {"en16931_data": {}}, format="json")
        assert resp.status_code == 400
