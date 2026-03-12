"""Tests for UI views — PDP settings, signup."""

import json
import uuid as uuid_lib
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client

from apps.billing.factories import (
    CustomerFactory,
    InvoiceFactory,
    ProductFactory,
    SupplierFactory,
)
from apps.billing.models import Customer, Product, Supplier
from apps.core.exceptions import ConflictError
from apps.core.models import Organization, OrganizationMembership
from apps.ui.views import _build_invoice_payload

# --- PDP Settings view ---


@pytest.mark.django_db
class TestPdpSettingsView:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(
            name="PDP Org",
            slug="pdp-org",
            factpulse_client_uid=uuid_lib.uuid4(),
        )
        self.owner = User.objects.create_user(
            username="pdp-owner@test.com",
            email="pdp-owner@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.owner, organization=self.org, role="owner"
        )

    def test_login_required(self):
        response = self.client.get("/settings/pdp/")
        assert response.status_code == 302
        assert "login" in response.url

    def test_owner_can_access(self):
        self.client.login(username="pdp-owner@test.com", password="testpass123")
        with patch("apps.factpulse.client.client") as mock_client:
            mock_client.is_configured = True
            mock_client.get_pdp_config.return_value = {
                "isConfigured": False,
                "isActive": False,
            }
            response = self.client.get("/settings/pdp/")
        assert response.status_code == 200
        assert b"Configuration plateforme agr" in response.content

    def test_member_forbidden(self):
        member = User.objects.create_user(
            username="pdp-member@test.com",
            email="pdp-member@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=member, organization=self.org, role="member"
        )
        self.client.login(username="pdp-member@test.com", password="testpass123")
        response = self.client.get("/settings/pdp/")
        assert response.status_code == 403

    def test_not_provisioned_shows_banner(self):
        self.org.factpulse_client_uid = None
        self.org.save(update_fields=["factpulse_client_uid"])
        self.client.login(username="pdp-owner@test.com", password="testpass123")
        response = self.client.get("/settings/pdp/")
        assert response.status_code == 200
        assert "non provisionnée" in response.content.decode()

    def test_post_pushes_config(self):
        self.client.login(username="pdp-owner@test.com", password="testpass123")
        with patch("apps.factpulse.client.client") as mock_client:
            mock_client.is_configured = True
            mock_client.get_pdp_config.return_value = {}
            mock_client.push_pdp_config.return_value = {"isConfigured": True}

            response = self.client.post(
                "/settings/pdp/",
                {
                    "flowServiceUrl": "https://pdp.example.com/flow",
                    "tokenUrl": "https://pdp.example.com/token",
                    "oauthClientId": "my-client-id",
                    "clientSecret": "my-secret",
                },
            )

        assert response.status_code == 302
        mock_client.push_pdp_config.assert_called_once()
        config = mock_client.push_pdp_config.call_args[0][1]
        assert config["flowServiceUrl"] == "https://pdp.example.com/flow"
        assert config["clientSecret"] == "my-secret"
        assert config["encryptionMode"] == "fernet"

    def test_post_missing_fields_shows_error(self):
        self.client.login(username="pdp-owner@test.com", password="testpass123")
        with patch("apps.factpulse.client.client") as mock_client:
            mock_client.is_configured = True
            mock_client.get_pdp_config.return_value = {}

            response = self.client.post(
                "/settings/pdp/",
                {
                    "flowServiceUrl": "https://pdp.example.com/flow",
                    "tokenUrl": "",
                    "oauthClientId": "",
                    "clientSecret": "",
                },
            )

        assert response.status_code == 200
        assert "requis" in response.content.decode()

    def test_api_error_shown(self):
        self.client.login(username="pdp-owner@test.com", password="testpass123")
        from apps.factpulse.client import FactPulseError

        with patch("apps.factpulse.client.client") as mock_client:
            mock_client.is_configured = True
            mock_client.get_pdp_config.side_effect = FactPulseError("Auth failed")

            response = self.client.get("/settings/pdp/")

        assert response.status_code == 200
        assert "Auth failed" in response.content.decode()


# --- Signup view ---


@pytest.mark.django_db
class TestSignupView:
    def setup_method(self):
        self.client = Client()

    def test_get_signup_page(self):
        response = self.client.get("/signup/")
        assert response.status_code == 200
        assert "Créer un compte" in response.content.decode()

    def test_signup_success(self):
        response = self.client.post(
            "/signup/",
            {
                "email": "new@test.com",
                "password": "securepass123",
                "password_confirm": "securepass123",
                "org_name": "New Corp",
            },
        )
        assert response.status_code == 302
        assert User.objects.filter(email="new@test.com").exists()
        org = Organization.objects.get(slug="new-corp")
        membership = OrganizationMembership.objects.get(
            user__email="new@test.com", organization=org
        )
        assert membership.role == "owner"

    def test_signup_redirects_to_verify_email(self):
        response = self.client.post(
            "/signup/",
            {
                "email": "autologin@test.com",
                "password": "securepass123",
                "password_confirm": "securepass123",
                "org_name": "Auto Org",
            },
        )
        # After signup, user is NOT logged in — redirected to verify-email page
        assert response.status_code == 302
        assert "/verify-email/sent/" in response.url

    def test_password_mismatch(self):
        response = self.client.post(
            "/signup/",
            {
                "email": "mismatch@test.com",
                "password": "securepass123",
                "password_confirm": "differentpass",
                "org_name": "Mismatch Org",
            },
        )
        assert response.status_code == 200
        assert "ne correspondent pas" in response.content.decode()
        assert not User.objects.filter(email="mismatch@test.com").exists()

    def test_password_too_short(self):
        response = self.client.post(
            "/signup/",
            {
                "email": "short@test.com",
                "password": "short",
                "password_confirm": "short",
                "org_name": "Short Org",
            },
        )
        assert response.status_code == 200
        assert "8 caractères" in response.content.decode()

    def test_duplicate_email(self):
        User.objects.create_user(
            username="dupe@test.com", email="dupe@test.com", password="pass"
        )
        response = self.client.post(
            "/signup/",
            {
                "email": "dupe@test.com",
                "password": "securepass123",
                "password_confirm": "securepass123",
                "org_name": "Dupe Org",
            },
        )
        assert response.status_code == 200
        assert "existe déjà" in response.content.decode()

    def test_authenticated_user_redirected(self):
        User.objects.create_user(
            username="already@test.com", email="already@test.com", password="pass"
        )
        self.client.login(username="already@test.com", password="pass")
        response = self.client.get("/signup/")
        assert response.status_code == 302

    def test_login_page_has_signup_link(self):
        response = self.client.get("/login/")
        assert "signup" in response.content.decode()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _logged_in_client(user, password="testpass123"):
    """Return a Django test Client logged in as *user*."""
    c = Client()
    c.login(username=user.username, password=password)
    return c


# ---------------------------------------------------------------------------
# 1. login_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLoginView:
    def setup_method(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="loginuser@test.com",
            email="loginuser@test.com",
            password="testpass123",
        )
        self.org = Organization.objects.create(name="Login Org", slug="login-org")
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )

    def test_get_renders_form(self):
        response = self.client.get("/login/")
        assert response.status_code == 200

    def test_post_valid_login(self):
        response = self.client.post(
            "/login/",
            {"username": "loginuser@test.com", "password": "testpass123"},
        )
        assert response.status_code == 302
        assert response.url == "/"

    def test_post_invalid_login(self):
        response = self.client.post(
            "/login/",
            {"username": "loginuser@test.com", "password": "wrong"},
        )
        assert response.status_code == 200  # re-renders form

    def test_authenticated_user_redirected(self):
        self.client.login(username="loginuser@test.com", password="testpass123")
        response = self.client.get("/login/")
        assert response.status_code == 302
        assert response.url == "/"


# ---------------------------------------------------------------------------
# 2. logout_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLogoutView:
    def setup_method(self):
        self.user = User.objects.create_user(
            username="logoutuser@test.com",
            email="logoutuser@test.com",
            password="testpass123",
        )
        self.client = Client()
        self.client.login(username="logoutuser@test.com", password="testpass123")

    def test_logout_redirects_to_login(self):
        response = self.client.get("/logout/")
        assert response.status_code == 302
        assert "login" in response.url

    def test_after_logout_requires_login(self):
        self.client.get("/logout/")
        response = self.client.get("/")
        assert response.status_code == 302
        assert "login" in response.url


# ---------------------------------------------------------------------------
# 3. switch_org
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSwitchOrg:
    def setup_method(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="switchuser@test.com",
            email="switchuser@test.com",
            password="testpass123",
        )
        self.org1 = Organization.objects.create(name="Org1", slug="org1")
        self.org2 = Organization.objects.create(name="Org2", slug="org2")
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org1, role="owner"
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org2, role="member"
        )
        self.client.login(username="switchuser@test.com", password="testpass123")

    def test_post_switches_org(self):
        response = self.client.post("/switch-org/", {"organization_id": self.org2.id})
        assert response.status_code == 302
        # Verify session was updated
        assert self.client.session["organization_id"] == self.org2.id

    def test_invalid_org_ignored(self):
        self.client.post("/switch-org/", {"organization_id": self.org1.id})
        # Try switching to an org the user is NOT a member of
        other_org = Organization.objects.create(name="Other", slug="other")
        self.client.post("/switch-org/", {"organization_id": other_org.id})
        # Should still be on org1
        assert self.client.session["organization_id"] == self.org1.id

    def test_login_required(self):
        c = Client()
        response = c.post("/switch-org/", {"organization_id": self.org1.id})
        assert response.status_code == 302
        assert "login" in response.url


# ---------------------------------------------------------------------------
# 4. dashboard
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDashboard:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="Dash Org", slug="dash-org")
        self.user = User.objects.create_user(
            username="dashuser@test.com",
            email="dashuser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="dashuser@test.com", password="testpass123")

    def test_renders_with_stats(self):
        supplier = SupplierFactory(organization=self.org)
        InvoiceFactory(organization=self.org, supplier=supplier)
        response = self.client.get("/")
        assert response.status_code == 200
        assert "stats" in response.context

    def test_no_org_shows_no_org(self):
        # Create user with no memberships
        User.objects.create_user(
            username="noorg@test.com",
            email="noorg@test.com",
            password="testpass123",
        )
        c = Client()
        c.login(username="noorg@test.com", password="testpass123")
        response = c.get("/")
        assert response.status_code == 200
        assert "no_org" in response.templates[0].name


# ---------------------------------------------------------------------------
# 5. invoice_list
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceList:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="Inv Org", slug="inv-org")
        self.user = User.objects.create_user(
            username="invuser@test.com",
            email="invuser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="invuser@test.com", password="testpass123")
        self.supplier = SupplierFactory(organization=self.org)

    def test_full_page_render(self):
        InvoiceFactory(organization=self.org, supplier=self.supplier)
        response = self.client.get("/invoices/")
        assert response.status_code == 200
        assert "invoice_list" in response.templates[0].name

    def test_hx_request_returns_partial(self):
        InvoiceFactory(organization=self.org, supplier=self.supplier)
        response = self.client.get("/invoices/", HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert "partials/invoice_table" in response.templates[0].name

    def test_status_filter(self):
        InvoiceFactory(organization=self.org, supplier=self.supplier, status="draft")
        InvoiceFactory(
            organization=self.org, supplier=self.supplier, status="validated"
        )
        response = self.client.get("/invoices/?status=draft")
        assert response.status_code == 200
        invoices = list(response.context["invoices"])
        assert all(inv.status == "draft" for inv in invoices)

    def test_search_filter(self):
        customer = CustomerFactory(organization=self.org, name="Acme Corp")
        InvoiceFactory(organization=self.org, supplier=self.supplier, customer=customer)
        InvoiceFactory(organization=self.org, supplier=self.supplier)
        response = self.client.get("/invoices/?search=Acme")
        assert response.status_code == 200

    def test_pending_meta_filter(self):
        InvoiceFactory(organization=self.org, supplier=self.supplier, status="draft")
        InvoiceFactory(
            organization=self.org, supplier=self.supplier, status="validated"
        )
        InvoiceFactory(
            organization=self.org, supplier=self.supplier, status="transmitted"
        )
        response = self.client.get("/invoices/?status=pending")
        assert response.status_code == 200
        invoices = list(response.context["invoices"])
        assert all(
            inv.status in ("validated", "transmitted", "accepted") for inv in invoices
        )


# ---------------------------------------------------------------------------
# 6. invoice_create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceCreate:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="Create Org", slug="create-org")
        self.user = User.objects.create_user(
            username="createuser@test.com",
            email="createuser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="createuser@test.com", password="testpass123")
        self.supplier = SupplierFactory(organization=self.org)
        self.customer = CustomerFactory(organization=self.org)

    def test_get_form(self):
        response = self.client.get("/invoices/new/")
        assert response.status_code == 200
        assert "suppliers" in response.context

    @patch("apps.ui.views.invoices.invoice_service.create_invoice")
    def test_post_success(self, mock_create):
        invoice = InvoiceFactory.build(
            organization=self.org, supplier=self.supplier, uuid=uuid_lib.uuid4()
        )
        mock_create.return_value = (invoice, [])
        response = self.client.post(
            "/invoices/new/",
            {
                "supplier_id": str(self.supplier.id),
                "customer_id": str(self.customer.id),
                "line_0_item_name": "Item 1",
                "line_0_quantity": "1",
                "line_0_unit_price": "100.00",
                "line_0_vat_rate": "20.00",
            },
        )
        assert response.status_code == 302
        assert str(invoice.uuid) in response.url
        mock_create.assert_called_once()

    @patch("apps.ui.views.invoices.invoice_service.create_invoice")
    def test_post_error(self, mock_create):
        mock_create.side_effect = ValueError("Missing supplier")
        response = self.client.post(
            "/invoices/new/",
            {
                "line_0_item_name": "Item 1",
                "line_0_quantity": "1",
                "line_0_unit_price": "100.00",
            },
        )
        assert response.status_code == 200
        assert "Missing supplier" in response.content.decode()


# ---------------------------------------------------------------------------
# 7. invoice_edit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceEdit:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="Edit Org", slug="edit-org")
        self.user = User.objects.create_user(
            username="edituser@test.com",
            email="edituser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="edituser@test.com", password="testpass123")
        self.supplier = SupplierFactory(organization=self.org)
        self.invoice = InvoiceFactory(
            organization=self.org, supplier=self.supplier, status="draft"
        )

    def test_get_form(self):
        response = self.client.get(f"/invoices/{self.invoice.uuid}/edit/")
        assert response.status_code == 200
        assert "invoice" in response.context

    @patch("apps.ui.views.invoices.invoice_service.update_invoice")
    def test_post_success(self, mock_update):
        mock_update.return_value = (self.invoice, [])
        response = self.client.post(
            f"/invoices/{self.invoice.uuid}/edit/",
            {
                "supplier_id": str(self.supplier.id),
                "line_0_item_name": "Updated Item",
                "line_0_quantity": "2",
                "line_0_unit_price": "50.00",
                "line_0_vat_rate": "20.00",
            },
        )
        assert response.status_code == 302
        mock_update.assert_called_once()

    @patch("apps.ui.views.invoices.invoice_service.update_invoice")
    def test_post_error(self, mock_update):
        mock_update.side_effect = ValueError("Conflict")
        response = self.client.post(
            f"/invoices/{self.invoice.uuid}/edit/",
            {
                "line_0_item_name": "Item",
                "line_0_quantity": "1",
                "line_0_unit_price": "10.00",
            },
        )
        assert response.status_code == 200
        assert "Conflict" in response.content.decode()

    def test_non_draft_redirects(self):
        self.invoice.status = "validated"
        self.invoice.save()
        response = self.client.get(f"/invoices/{self.invoice.uuid}/edit/")
        assert response.status_code == 302
        assert f"/invoices/{self.invoice.uuid}/" in response.url


# ---------------------------------------------------------------------------
# 8. invoice_detail
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceDetail:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="Detail Org", slug="detail-org")
        self.user = User.objects.create_user(
            username="detailuser@test.com",
            email="detailuser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="detailuser@test.com", password="testpass123")
        self.supplier = SupplierFactory(organization=self.org)
        self.invoice = InvoiceFactory(organization=self.org, supplier=self.supplier)

    def test_renders_invoice_detail(self):
        response = self.client.get(f"/invoices/{self.invoice.uuid}/")
        assert response.status_code == 200
        assert response.context["invoice"] == self.invoice

    def test_includes_audit_logs(self):
        from apps.billing.models import InvoiceAuditLog

        InvoiceAuditLog.objects.create(
            invoice=self.invoice, action="created", user=self.user
        )
        response = self.client.get(f"/invoices/{self.invoice.uuid}/")
        assert response.status_code == 200
        assert len(response.context["audit_logs"]) >= 1


# ---------------------------------------------------------------------------
# 9. invoice_validate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceValidate:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="Val Org", slug="val-org")
        self.user = User.objects.create_user(
            username="valuser@test.com",
            email="valuser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="valuser@test.com", password="testpass123")
        self.supplier = SupplierFactory(organization=self.org)
        self.invoice = InvoiceFactory(
            organization=self.org, supplier=self.supplier, status="draft"
        )

    @patch("apps.ui.views.invoices.invoice_service.validate_invoice")
    def test_post_success(self, mock_validate):
        mock_validate.return_value = self.invoice
        response = self.client.post(f"/invoices/{self.invoice.uuid}/validate/")
        assert response.status_code == 302
        mock_validate.assert_called_once()

    @patch("apps.ui.views.invoices.invoice_service.validate_invoice")
    def test_post_exception_shows_error(self, mock_validate):
        mock_validate.side_effect = ConflictError("Validation failed")
        response = self.client.post(f"/invoices/{self.invoice.uuid}/validate/")
        assert response.status_code == 302
        # Follow redirect to check the message
        detail_response = self.client.get(f"/invoices/{self.invoice.uuid}/")
        assert detail_response.status_code == 200


# ---------------------------------------------------------------------------
# 10. invoice_transmit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceTransmit:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="Trans Org", slug="trans-org")
        self.user = User.objects.create_user(
            username="transuser@test.com",
            email="transuser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="transuser@test.com", password="testpass123")
        self.supplier = SupplierFactory(organization=self.org)
        self.invoice = InvoiceFactory(
            organization=self.org, supplier=self.supplier, status="validated"
        )

    @patch("apps.ui.views.invoices.invoice_service.transmit_invoice")
    def test_post_success(self, mock_transmit):
        mock_transmit.return_value = self.invoice
        response = self.client.post(f"/invoices/{self.invoice.uuid}/transmit/")
        assert response.status_code == 302
        mock_transmit.assert_called_once()

    @patch("apps.ui.views.invoices.invoice_service.transmit_invoice")
    def test_post_exception_shows_error(self, mock_transmit):
        mock_transmit.side_effect = ConflictError("Transmit failed")
        response = self.client.post(f"/invoices/{self.invoice.uuid}/transmit/")
        assert response.status_code == 302


# ---------------------------------------------------------------------------
# 11. invoice_mark_paid
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceMarkPaid:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="Paid Org", slug="paid-org")
        self.user = User.objects.create_user(
            username="paiduser@test.com",
            email="paiduser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="paiduser@test.com", password="testpass123")
        self.supplier = SupplierFactory(organization=self.org)
        self.invoice = InvoiceFactory(
            organization=self.org, supplier=self.supplier, status="transmitted"
        )

    @patch("apps.ui.views.invoices.invoice_service.mark_paid")
    def test_post_success(self, mock_mark_paid):
        mock_mark_paid.return_value = self.invoice
        response = self.client.post(f"/invoices/{self.invoice.uuid}/mark-paid/")
        assert response.status_code == 302
        mock_mark_paid.assert_called_once()

    @patch("apps.ui.views.invoices.invoice_service.mark_paid")
    def test_post_exception_shows_error(self, mock_mark_paid):
        mock_mark_paid.side_effect = ConflictError("Payment failed")
        response = self.client.post(f"/invoices/{self.invoice.uuid}/mark-paid/")
        assert response.status_code == 302


# ---------------------------------------------------------------------------
# 12. invoice_cancel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceCancel:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="Cancel Org", slug="cancel-org")
        self.user = User.objects.create_user(
            username="canceluser@test.com",
            email="canceluser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="canceluser@test.com", password="testpass123")
        self.supplier = SupplierFactory(organization=self.org)
        self.invoice = InvoiceFactory(
            organization=self.org, supplier=self.supplier, status="validated"
        )

    @patch("apps.ui.views.invoices.invoice_service.cancel_invoice")
    def test_post_returns_credit_note_redirect(self, mock_cancel):
        credit_note = InvoiceFactory.build(
            organization=self.org, supplier=self.supplier, uuid=uuid_lib.uuid4()
        )
        mock_cancel.return_value = credit_note
        response = self.client.post(f"/invoices/{self.invoice.uuid}/cancel/")
        assert response.status_code == 302
        assert str(credit_note.uuid) in response.url

    @patch("apps.ui.views.invoices.invoice_service.cancel_invoice")
    def test_post_exception_shows_error(self, mock_cancel):
        mock_cancel.side_effect = ConflictError("Cancel failed")
        response = self.client.post(f"/invoices/{self.invoice.uuid}/cancel/")
        assert response.status_code == 302
        assert str(self.invoice.uuid) in response.url


# ---------------------------------------------------------------------------
# 13. invoice_delete
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestInvoiceDelete:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="Del Org", slug="del-org")
        self.user = User.objects.create_user(
            username="deluser@test.com",
            email="deluser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="deluser@test.com", password="testpass123")
        self.supplier = SupplierFactory(organization=self.org)
        self.invoice = InvoiceFactory(
            organization=self.org, supplier=self.supplier, status="draft"
        )

    @patch("apps.ui.views.invoices.invoice_service.soft_delete")
    def test_post_redirects_to_list(self, mock_delete):
        mock_delete.return_value = self.invoice
        response = self.client.post(f"/invoices/{self.invoice.uuid}/delete/")
        assert response.status_code == 302
        assert "/invoices/" in response.url
        mock_delete.assert_called_once()

    @patch("apps.ui.views.invoices.invoice_service.soft_delete")
    def test_post_exception_redirects_to_detail(self, mock_delete):
        mock_delete.side_effect = ConflictError("Cannot delete")
        response = self.client.post(f"/invoices/{self.invoice.uuid}/delete/")
        assert response.status_code == 302
        assert str(self.invoice.uuid) in response.url


# ---------------------------------------------------------------------------
# 14. customer_list
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCustomerList:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="CL Org", slug="cl-org")
        self.user = User.objects.create_user(
            username="cluser@test.com",
            email="cluser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="cluser@test.com", password="testpass123")

    def test_full_page(self):
        CustomerFactory(organization=self.org, name="Alpha")
        response = self.client.get("/customers/")
        assert response.status_code == 200
        assert "customer_list" in response.templates[0].name

    def test_hx_request_partial(self):
        CustomerFactory(organization=self.org)
        response = self.client.get("/customers/", HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert "partials/customer_table" in response.templates[0].name

    def test_search_filter(self):
        CustomerFactory(organization=self.org, name="Zebra Inc")
        CustomerFactory(organization=self.org, name="Alpha Inc")
        response = self.client.get("/customers/?search=Zebra")
        assert response.status_code == 200
        customers = list(response.context["customers"])
        assert len(customers) == 1
        assert customers[0].name == "Zebra Inc"

    def test_archived_filter(self):
        CustomerFactory(organization=self.org, name="Active", archived=False)
        CustomerFactory(organization=self.org, name="Archived", archived=True)

        # Without archived flag: only active
        response = self.client.get("/customers/")
        customers = list(response.context["customers"])
        assert all(not c.archived for c in customers)

        # With archived flag: all
        response = self.client.get("/customers/?archived=1")
        customers = list(response.context["customers"])
        names = [c.name for c in customers]
        assert "Archived" in names


# ---------------------------------------------------------------------------
# 15. customer_create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCustomerCreate:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="CC Org", slug="cc-org")
        self.user = User.objects.create_user(
            username="ccuser@test.com",
            email="ccuser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="ccuser@test.com", password="testpass123")

    def test_get_form(self):
        response = self.client.get("/customers/new/")
        assert response.status_code == 200

    def test_post_creates_customer(self):
        response = self.client.post(
            "/customers/new/",
            {
                "name": "New Customer",
                "siren": "123456789",
                "siret": "12345678901234",
                "vat_number": "FR12345678901",
                "email": "cust@test.com",
                "address_line1": "1 rue de la Paix",
                "address_postcode": "75001",
                "address_city": "Paris",
                "address_country": "FR",
            },
        )
        assert response.status_code == 302
        assert Customer.objects.filter(
            organization=self.org, name="New Customer"
        ).exists()


# ---------------------------------------------------------------------------
# 16. customer_edit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCustomerEdit:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="CE Org", slug="ce-org")
        self.user = User.objects.create_user(
            username="ceuser@test.com",
            email="ceuser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="ceuser@test.com", password="testpass123")
        self.customer = CustomerFactory(organization=self.org, name="Old Name")

    def test_get_form_with_customer(self):
        response = self.client.get(f"/customers/{self.customer.uuid}/edit/")
        assert response.status_code == 200
        assert response.context["customer"] == self.customer

    def test_post_updates_customer(self):
        response = self.client.post(
            f"/customers/{self.customer.uuid}/edit/",
            {
                "name": "New Name",
                "siren": "999999999",
                "address_line1": "2 rue neuve",
                "address_postcode": "69001",
                "address_city": "Lyon",
                "address_country": "FR",
            },
        )
        assert response.status_code == 302
        self.customer.refresh_from_db()
        assert self.customer.name == "New Name"
        assert self.customer.siren == "999999999"


# ---------------------------------------------------------------------------
# 17. customer_archive
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCustomerArchive:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="CA Org", slug="ca-org")
        self.user = User.objects.create_user(
            username="causer@test.com",
            email="causer@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="causer@test.com", password="testpass123")
        self.customer = CustomerFactory(organization=self.org, archived=False)

    def test_post_toggles_archived(self):
        response = self.client.post(f"/customers/{self.customer.uuid}/archive/")
        assert response.status_code == 302
        self.customer.refresh_from_db()
        assert self.customer.archived is True

        # Toggle back
        response = self.client.post(f"/customers/{self.customer.uuid}/archive/")
        self.customer.refresh_from_db()
        assert self.customer.archived is False


# ---------------------------------------------------------------------------
# 18. product_list
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProductList:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="PL Org", slug="pl-org")
        self.user = User.objects.create_user(
            username="pluser@test.com",
            email="pluser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="pluser@test.com", password="testpass123")

    def test_full_page(self):
        ProductFactory(organization=self.org)
        response = self.client.get("/products/")
        assert response.status_code == 200
        assert "product_list" in response.templates[0].name

    def test_hx_request_partial(self):
        ProductFactory(organization=self.org)
        response = self.client.get("/products/", HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert "partials/product_table" in response.templates[0].name

    def test_search_filter(self):
        ProductFactory(organization=self.org, name="Widget", reference="W001")
        ProductFactory(organization=self.org, name="Gadget", reference="G001")
        response = self.client.get("/products/?search=Widget")
        products = list(response.context["products"])
        assert len(products) == 1
        assert products[0].name == "Widget"

    def test_archived_filter(self):
        ProductFactory(organization=self.org, name="Active Prod", archived=False)
        ProductFactory(organization=self.org, name="Archived Prod", archived=True)

        response = self.client.get("/products/")
        products = list(response.context["products"])
        assert all(not p.archived for p in products)

        response = self.client.get("/products/?archived=1")
        products = list(response.context["products"])
        names = [p.name for p in products]
        assert "Archived Prod" in names


# ---------------------------------------------------------------------------
# 19. product_create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProductCreate:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="PC Org", slug="pc-org")
        self.user = User.objects.create_user(
            username="pcuser@test.com",
            email="pcuser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="pcuser@test.com", password="testpass123")

    def test_get_form(self):
        response = self.client.get("/products/new/")
        assert response.status_code == 200

    def test_post_creates_product(self):
        response = self.client.post(
            "/products/new/",
            {
                "name": "New Product",
                "description": "A test product",
                "reference": "NP001",
                "default_unit_price": "49.99",
                "default_vat_rate": "20.00",
                "default_vat_category": "S",
                "default_unit": "C62",
            },
        )
        assert response.status_code == 302
        assert Product.objects.filter(
            organization=self.org, name="New Product"
        ).exists()


# ---------------------------------------------------------------------------
# 20. product_edit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProductEdit:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="PE Org", slug="pe-org")
        self.user = User.objects.create_user(
            username="peuser@test.com",
            email="peuser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="peuser@test.com", password="testpass123")
        self.product = ProductFactory(organization=self.org, name="Old Product")

    def test_get_form_with_product(self):
        response = self.client.get(f"/products/{self.product.uuid}/edit/")
        assert response.status_code == 200
        assert response.context["product"] == self.product

    def test_post_updates_product(self):
        response = self.client.post(
            f"/products/{self.product.uuid}/edit/",
            {
                "name": "Updated Product",
                "description": "Updated description",
                "reference": "UP001",
                "default_unit_price": "79.99",
                "default_vat_rate": "10.00",
                "default_vat_category": "S",
                "default_unit": "KGM",
            },
        )
        assert response.status_code == 302
        self.product.refresh_from_db()
        assert self.product.name == "Updated Product"


# ---------------------------------------------------------------------------
# 21. product_archive
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProductArchive:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="PA Org", slug="pa-org")
        self.user = User.objects.create_user(
            username="pauser@test.com",
            email="pauser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="pauser@test.com", password="testpass123")
        self.product = ProductFactory(organization=self.org, archived=False)

    def test_post_toggles_archived(self):
        response = self.client.post(f"/products/{self.product.uuid}/archive/")
        assert response.status_code == 302
        self.product.refresh_from_db()
        assert self.product.archived is True

        response = self.client.post(f"/products/{self.product.uuid}/archive/")
        self.product.refresh_from_db()
        assert self.product.archived is False


# ---------------------------------------------------------------------------
# 22. supplier_list
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSupplierList:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="SL Org", slug="sl-org")
        self.user = User.objects.create_user(
            username="sluser@test.com",
            email="sluser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="sluser@test.com", password="testpass123")

    def test_full_page(self):
        SupplierFactory(organization=self.org)
        response = self.client.get("/suppliers/")
        assert response.status_code == 200
        assert "supplier_list" in response.templates[0].name

    def test_hx_request_partial(self):
        SupplierFactory(organization=self.org)
        response = self.client.get("/suppliers/", HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert "partials/supplier_table" in response.templates[0].name

    def test_archived_filter(self):
        SupplierFactory(organization=self.org, name="Active Sup", archived=False)
        SupplierFactory(organization=self.org, name="Archived Sup", archived=True)

        response = self.client.get("/suppliers/")
        suppliers = list(response.context["suppliers"])
        assert all(not s.archived for s in suppliers)

        response = self.client.get("/suppliers/?archived=1")
        suppliers = list(response.context["suppliers"])
        names = [s.name for s in suppliers]
        assert "Archived Sup" in names


# ---------------------------------------------------------------------------
# 23. supplier_create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSupplierCreate:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="SC Org", slug="sc-org")
        self.user = User.objects.create_user(
            username="scuser@test.com",
            email="scuser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="scuser@test.com", password="testpass123")

    def test_get_form(self):
        response = self.client.get("/suppliers/new/")
        assert response.status_code == 200

    def test_post_creates_supplier(self):
        response = self.client.post(
            "/suppliers/new/",
            {
                "name": "New Supplier",
                "siren": "111222333",
                "siret": "11122233344444",
                "vat_number": "FR11122233344",
                "email": "supplier@test.com",
                "address_line1": "10 avenue des Champs",
                "address_postcode": "75008",
                "address_city": "Paris",
                "address_country": "FR",
            },
        )
        assert response.status_code == 302
        assert Supplier.objects.filter(
            organization=self.org, name="New Supplier"
        ).exists()


# ---------------------------------------------------------------------------
# 24. supplier_edit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSupplierEdit:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="SE Org", slug="se-org")
        self.user = User.objects.create_user(
            username="seuser@test.com",
            email="seuser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="seuser@test.com", password="testpass123")
        self.supplier = SupplierFactory(organization=self.org, name="Old Supplier")

    def test_get_form_with_supplier(self):
        response = self.client.get(f"/suppliers/{self.supplier.uuid}/edit/")
        assert response.status_code == 200
        assert response.context["supplier"] == self.supplier

    def test_post_updates_supplier(self):
        response = self.client.post(
            f"/suppliers/{self.supplier.uuid}/edit/",
            {
                "name": "Updated Supplier",
                "siren": "888777666",
                "address_line1": "5 boulevard Haussmann",
                "address_postcode": "75009",
                "address_city": "Paris",
                "address_country": "FR",
            },
        )
        assert response.status_code == 302
        self.supplier.refresh_from_db()
        assert self.supplier.name == "Updated Supplier"


# ---------------------------------------------------------------------------
# 25. supplier_archive
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSupplierArchive:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="SA Org", slug="sa-org")
        self.user = User.objects.create_user(
            username="sauser@test.com",
            email="sauser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="sauser@test.com", password="testpass123")
        self.supplier = SupplierFactory(organization=self.org, archived=False)

    def test_post_toggles_archived(self):
        response = self.client.post(f"/suppliers/{self.supplier.uuid}/archive/")
        assert response.status_code == 302
        self.supplier.refresh_from_db()
        assert self.supplier.archived is True

        response = self.client.post(f"/suppliers/{self.supplier.uuid}/archive/")
        self.supplier.refresh_from_db()
        assert self.supplier.archived is False


# ---------------------------------------------------------------------------
# 26. supplier_settings
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSupplierSettings:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="SS Org", slug="ss-org")
        self.user = User.objects.create_user(
            username="ssuser@test.com",
            email="ssuser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="ssuser@test.com", password="testpass123")
        self.supplier = SupplierFactory(organization=self.org)

    def test_get_form(self):
        response = self.client.get(f"/suppliers/{self.supplier.uuid}/settings/")
        assert response.status_code == 200
        assert response.context["supplier"] == self.supplier

    def test_post_updates_settings(self):
        response = self.client.post(
            f"/suppliers/{self.supplier.uuid}/settings/",
            {
                "note_pmt": "Custom PMT note",
                "note_pmd": "Custom PMD note",
                "note_aab": "Custom AAB note",
                "pdf_legal_mentions": "Legal text",
                "primary_color": "#FF0000",
                "iban": "FR7630006000011234567890189",
                "bic": "BNPAFRPP",
                "payment_terms_days": "30",
                "payment_terms_end_of_month": "on",
            },
        )
        assert response.status_code == 302
        self.supplier.refresh_from_db()
        assert self.supplier.note_pmt == "Custom PMT note"
        assert self.supplier.iban == "FR7630006000011234567890189"
        assert self.supplier.payment_terms_days == 30
        assert self.supplier.payment_terms_end_of_month is True


# ---------------------------------------------------------------------------
# 27. supplier_defaults
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSupplierDefaults:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="SD Org", slug="sd-org")
        self.user = User.objects.create_user(
            username="sduser@test.com",
            email="sduser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="sduser@test.com", password="testpass123")
        self.supplier = SupplierFactory(
            organization=self.org,
            note_pmt="PMT note",
            note_pmd="PMD note",
            note_aab="AAB note",
            payment_terms_days=45,
            payment_terms_end_of_month=True,
        )

    def test_get_returns_json(self):
        response = self.client.get(f"/suppliers/{self.supplier.uuid}/defaults/")
        assert response.status_code == 200
        data = response.json()
        assert data["note_pmt"] == "PMT note"
        assert data["note_pmd"] == "PMD note"
        assert data["note_aab"] == "AAB note"
        assert data["payment_terms_days"] == 45
        assert data["payment_terms_end_of_month"] is True


# ---------------------------------------------------------------------------
# 28. sirene_lookup
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSireneLookup:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="SIR Org", slug="sir-org")
        self.user = User.objects.create_user(
            username="siruser@test.com",
            email="siruser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="siruser@test.com", password="testpass123")

    def test_no_query_returns_400(self):
        response = self.client.get("/sirene-lookup/")
        assert response.status_code == 400
        data = response.json()
        assert "error" in data

    @patch("apps.ui.views.lookups.sirene_lookup_fn")
    def test_valid_query(self, mock_lookup):
        mock_lookup.return_value = {
            "name": "Test Corp",
            "siren": "123456789",
            "siret": "12345678901234",
        }
        response = self.client.get("/sirene-lookup/?q=123456789")
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["name"] == "Test Corp"

    @patch("apps.ui.views.lookups.sirene_lookup_fn")
    def test_sirene_error_returns_400(self, mock_lookup):
        from apps.billing.services.sirene_client import SireneError

        mock_lookup.side_effect = SireneError("Not found")
        response = self.client.get("/sirene-lookup/?q=000000000")
        assert response.status_code == 400
        data = response.json()
        assert "Not found" in data["error"]


# ---------------------------------------------------------------------------
# 29. directory_lookup
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDirectoryLookup:
    def setup_method(self):
        self.client = Client()
        self.org = Organization.objects.create(name="Dir Org", slug="dir-org")
        self.user = User.objects.create_user(
            username="diruser@test.com",
            email="diruser@test.com",
            password="testpass123",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role="owner"
        )
        self.client.login(username="diruser@test.com", password="testpass123")

    def test_invalid_siren_returns_400(self):
        response = self.client.get("/directory-lookup/?siren=123")
        assert response.status_code == 400
        data = response.json()
        assert "SIREN valide" in data["error"]

    def test_missing_siren_returns_400(self):
        response = self.client.get("/directory-lookup/")
        assert response.status_code == 400

    @patch("apps.factpulse.client.client")
    def test_not_configured_returns_503(self, mock_client):
        mock_client.is_configured = False
        response = self.client.get("/directory-lookup/?siren=123456789")
        assert response.status_code == 503
        data = response.json()
        assert "pas configuré" in data["error"]

    @patch("apps.factpulse.client.client")
    def test_success(self, mock_client):
        mock_client.is_configured = True
        mock_client.search_directory_lines.return_value = [
            {"siren": "123456789", "address": "test@piste.gouv.fr"}
        ]
        response = self.client.get("/directory-lookup/?siren=123456789")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 1

    @patch("apps.factpulse.client.client")
    def test_factpulse_error_returns_400(self, mock_client):
        from apps.factpulse.client import FactPulseError

        mock_client.is_configured = True
        mock_client.search_directory_lines.side_effect = FactPulseError("API error")
        response = self.client.get("/directory-lookup/?siren=123456789")
        assert response.status_code == 400
        data = response.json()
        assert "API error" in data["error"]

    @patch("apps.factpulse.client.client")
    def test_factpulse_unavailable_returns_503(self, mock_client):
        from apps.factpulse.client import FactPulseUnavailableError

        mock_client.is_configured = True
        mock_client.search_directory_lines.side_effect = FactPulseUnavailableError(
            "Service down"
        )
        response = self.client.get("/directory-lookup/?siren=123456789")
        assert response.status_code == 503
        data = response.json()
        assert "indisponible" in data["error"]


# ---------------------------------------------------------------------------
# 30. _build_invoice_payload
# ---------------------------------------------------------------------------


class TestBuildInvoicePayload:
    """Tests for the _build_invoice_payload helper (no DB needed)."""

    def test_basic_single_line(self):
        post = {
            "supplier_id": "42",
            "customer_id": "7",
            "line_0_item_name": "Widget",
            "line_0_quantity": "3",
            "line_0_unit_price": "10.00",
            "line_0_vat_rate": "20.00",
            "line_0_vat_category": "S",
        }
        payload = _build_invoice_payload(post)
        assert payload["supplier_id"] == "42"
        assert payload["customer_id"] == "7"
        lines = payload["en16931_data"]["invoiceLines"]
        assert len(lines) == 1
        assert lines[0]["itemName"] == "Widget"
        assert lines[0]["quantity"] == "3"
        assert lines[0]["unitNetPrice"] == "10.00"

    def test_multiple_lines(self):
        post = {
            "line_0_item_name": "A",
            "line_0_quantity": "1",
            "line_0_unit_price": "10",
            "line_1_item_name": "B",
            "line_1_quantity": "2",
            "line_1_unit_price": "20",
        }
        payload = _build_invoice_payload(post)
        lines = payload["en16931_data"]["invoiceLines"]
        assert len(lines) == 2
        assert lines[0]["lineNumber"] == 1
        assert lines[1]["lineNumber"] == 2

    def test_totals(self):
        post = {
            "total_net_amount": "100.00",
            "total_vat_amount": "20.00",
            "total_with_vat": "120.00",
        }
        payload = _build_invoice_payload(post)
        totals = payload["en16931_data"]["totals"]
        assert totals["totalNetAmount"] == "100.00"
        assert totals["vatAmount"] == "20.00"
        assert totals["totalGrossAmount"] == "120.00"
        assert totals["amountDue"] == "120.00"

    def test_dates(self):
        post = {
            "issue_date": "2026-01-15",
            "due_date": "2026-02-15",
        }
        payload = _build_invoice_payload(post)
        data = payload["en16931_data"]
        assert data["invoiceDate"] == "2026-01-15"
        assert data["paymentDueDate"] == "2026-02-15"
        refs = data["references"]
        assert refs["issueDate"] == "2026-01-15"
        assert refs["dueDate"] == "2026-02-15"

    def test_notes(self):
        post = {
            "note_pmt": "PMT content",
            "note_pmd": "PMD content",
            "note_aab": "AAB content",
            "notes_extra": "Extra note",
        }
        payload = _build_invoice_payload(post)
        notes = payload["en16931_data"]["notes"]
        assert len(notes) == 4
        subjects = [n.get("subjectCode") for n in notes]
        assert "PMT" in subjects
        assert "PMD" in subjects
        assert "AAB" in subjects

    def test_vat_lines_json(self):
        vat_lines = [
            {
                "category": "S",
                "rate": "20.00",
                "taxableAmount": "100.00",
                "taxAmount": "20.00",
            }
        ]
        post = {"vat_lines_json": json.dumps(vat_lines)}
        payload = _build_invoice_payload(post)
        assert payload["en16931_data"]["vatLines"] == vat_lines

    def test_invalid_vat_lines_json_ignored(self):
        post = {"vat_lines_json": "not valid json{{{"}
        payload = _build_invoice_payload(post)
        assert "vatLines" not in payload["en16931_data"]

    def test_exemption_reason_merged_into_vat_lines(self):
        vat_lines = [{"category": "E", "rate": "0.00", "taxableAmount": "100.00"}]
        post = {
            "line_0_item_name": "Exempt item",
            "line_0_quantity": "1",
            "line_0_unit_price": "100",
            "line_0_vat_rate": "0.00",
            "line_0_vat_category": "E",
            "line_0_exemption_reason": "Exempt per Article 261",
            "vat_lines_json": json.dumps(vat_lines),
        }
        payload = _build_invoice_payload(post)
        vl = payload["en16931_data"]["vatLines"]
        assert vl[0]["exemptionReason"] == "Exempt per Article 261"

    def test_product_id_included(self):
        post = {
            "line_0_item_name": "Item",
            "line_0_quantity": "1",
            "line_0_unit_price": "10",
            "line_0_product_id": "99",
        }
        payload = _build_invoice_payload(post)
        assert payload["en16931_data"]["invoiceLines"][0]["product_id"] == "99"

    def test_line_net_amount_included(self):
        post = {
            "line_0_item_name": "Item",
            "line_0_quantity": "2",
            "line_0_unit_price": "50",
            "line_0_net_amount": "100.00",
        }
        payload = _build_invoice_payload(post)
        assert payload["en16931_data"]["invoiceLines"][0]["lineNetAmount"] == "100.00"

    def test_payment_means(self):
        post = {"payment_means": "30"}
        payload = _build_invoice_payload(post)
        assert payload["en16931_data"]["references"]["paymentMeans"] == "30"

    def test_invoice_type_code(self):
        post = {"invoice_type_code": "381"}
        payload = _build_invoice_payload(post)
        assert payload["en16931_data"]["references"]["invoiceType"] == "381"

    def test_no_supplier_no_customer(self):
        post = {}
        payload = _build_invoice_payload(post)
        assert "supplier_id" not in payload
        assert "customer_id" not in payload

    def test_defaults_when_empty(self):
        post = {}
        payload = _build_invoice_payload(post)
        totals = payload["en16931_data"]["totals"]
        assert totals["totalNetAmount"] == "0.00"
        assert totals["vatAmount"] == "0.00"
        refs = payload["en16931_data"]["references"]
        assert refs["invoiceType"] == "380"
        assert refs["invoiceCurrency"] == "EUR"
