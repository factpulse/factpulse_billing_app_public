"""Tests for FactPulse integration — client, signals, management command."""

import uuid as uuid_lib
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.core.management import call_command

from apps.core.models import Organization
from apps.factpulse.client import (
    FactPulseClient,
    FactPulseError,
    FactPulseUnavailableError,
    _TokenEntry,
)

# --- FactPulseClient token cache ---


class TestTokenCache:
    def setup_method(self):
        with patch.object(FactPulseClient, "__init__", lambda self: None):
            self.client = FactPulseClient()
        import threading

        self.client.base_url = "http://fake"
        self.client.email = "test@test.com"
        self.client.password = "pass"
        self.client.timeout = 5
        self.client._tokens = {}
        self.client._lock = threading.Lock()

    def test_separate_token_entries(self):
        """Account-level and client-level tokens are cached separately."""
        self.client._tokens[None] = _TokenEntry(
            access="account-token", refresh=None, expires_at=9999999999
        )
        self.client._tokens["client-uid"] = _TokenEntry(
            access="client-token", refresh=None, expires_at=9999999999
        )

        headers_account = self.client._headers(client_uid=None)
        headers_client = self.client._headers(client_uid="client-uid")

        assert "account-token" in headers_account["Authorization"]
        assert "client-token" in headers_client["Authorization"]

    def test_is_configured(self):
        assert self.client.is_configured is True

    def test_not_configured_empty_url(self):
        self.client.base_url = ""
        assert self.client.is_configured is False


# --- FactPulseClient API methods ---


class TestClientApiMethods:
    def setup_method(self):
        with patch.object(FactPulseClient, "__init__", lambda self: None):
            self.client = FactPulseClient()
        import threading

        self.client.base_url = "http://fake"
        self.client.email = "test@test.com"
        self.client.password = "pass"
        self.client.timeout = 5
        self.client._tokens = {
            None: _TokenEntry(access="tok", refresh=None, expires_at=9999999999),
            "uid-1": _TokenEntry(access="tok-1", refresh=None, expires_at=9999999999),
        }
        self.client._lock = threading.Lock()

    @patch("apps.factpulse.client.requests.request")
    def test_create_client(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"uid": "new-uid", "name": "Test"}
        mock_request.return_value = mock_response

        result = self.client.create_client("Test")
        assert result["uid"] == "new-uid"

        call_kwargs = mock_request.call_args
        assert call_kwargs[1]["json"] == {"name": "Test"}

    @patch("apps.factpulse.client.requests.request")
    def test_create_client_with_siret(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"uid": "uid", "name": "Test"}
        mock_request.return_value = mock_response

        self.client.create_client("Test", siret="12345678900010")
        call_kwargs = mock_request.call_args
        assert call_kwargs[1]["json"]["siret"] == "12345678900010"

    @patch("apps.factpulse.client.requests.request")
    def test_get_pdp_config(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"isConfigured": True, "isActive": True}
        mock_request.return_value = mock_response

        result = self.client.get_pdp_config("uid-1")
        assert result["isConfigured"] is True
        assert "/clients/uid-1/pdp-config" in mock_request.call_args[0][1]

    @patch("apps.factpulse.client.requests.request")
    def test_push_pdp_config(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"isConfigured": True}
        mock_request.return_value = mock_response

        config = {"flowServiceUrl": "https://pdp.example.com", "oauthClientId": "id"}
        result = self.client.push_pdp_config("uid-1", config)
        assert result["isConfigured"] is True

    @patch("apps.factpulse.client.requests.request")
    def test_generate_invoice_with_client_uid(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"pdf-bytes"
        mock_request.return_value = mock_response

        result = self.client.generate_invoice({"data": "test"}, client_uid="uid-1")
        assert result == b"pdf-bytes"
        # Verify auth header uses client-level token
        headers = mock_request.call_args[1]["headers"]
        assert "tok-1" in headers["Authorization"]

    @patch("apps.factpulse.client.requests.request")
    def test_submit_flow_with_client_uid(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {
            "flowId": "flow-1",
            "submittedAt": "2025-01-01T00:00:00Z",
        }
        mock_request.return_value = mock_response

        flow_info = {"flowSyntax": "Factur-X", "name": "INV-001"}
        result = self.client.submit_flow(flow_info, b"pdf-bytes", client_uid="uid-1")
        assert result["flowId"] == "flow-1"

    @patch("apps.factpulse.client.requests.request")
    def test_get_flow_status_with_client_uid(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "flowId": "flow-1",
            "acknowledgement": {"status": "Ok"},
        }
        mock_request.return_value = mock_response

        result = self.client.get_flow_status("flow-1", client_uid="uid-1")
        assert result["acknowledgement"]["status"] == "Ok"

    def test_not_configured_raises(self):
        self.client.base_url = ""
        with pytest.raises(FactPulseUnavailableError):
            self.client.create_client("Test")
        with pytest.raises(FactPulseUnavailableError):
            self.client.get_pdp_config("uid")
        with pytest.raises(FactPulseUnavailableError):
            self.client.generate_invoice({})

    @patch("apps.factpulse.client.requests.request")
    def test_handle_error_on_failure(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.json.return_value = {"message": "Validation failed"}
        mock_request.return_value = mock_response

        with pytest.raises(FactPulseError, match="Validation failed"):
            self.client.create_client("Test")

    @patch("apps.factpulse.client.requests.request")
    def test_submit_paid_status(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "flowId": "cdar-flow-1",
            "documentId": "doc-1",
            "status": "accepted",
            "invoiceId": "INV-001",
            "message": "ok",
        }
        mock_request.return_value = mock_response

        result = self.client.submit_paid_status(
            {"invoiceId": "INV-001", "amount": 100.0}, client_uid="uid-1"
        )
        assert result["flowId"] == "cdar-flow-1"
        assert "/cdar/encaissee" in mock_request.call_args[0][1]

    @patch("apps.factpulse.client.requests.request")
    def test_get_cdar_lifecycle(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "invoices": [
                {
                    "sellerId": "123456789",
                    "invoiceId": "FA-2026-001",
                    "events": [
                        {
                            "statusCode": "200",
                            "statusDescription": "Deposee",
                            "at": "2026-02-01T10:00:00Z",
                        },
                    ],
                    "totalEvents": 1,
                },
            ],
            "totalInvoices": 1,
            "cutoffDays": 7,
        }
        mock_request.return_value = mock_response

        result = self.client.get_cdar_lifecycle(days=7, client_uid="uid-1")
        assert result["totalInvoices"] == 1
        assert result["invoices"][0]["sellerId"] == "123456789"
        url = mock_request.call_args[0][1]
        assert "/cdar/lifecycle" in url

    @patch("apps.factpulse.client.requests.request")
    def test_get_cdar_lifecycle_with_invoice_id(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "invoices": [],
            "totalInvoices": 0,
            "cutoffDays": 7,
        }
        mock_request.return_value = mock_response

        self.client.get_cdar_lifecycle(
            days=30, invoice_id="FA-2026-001", client_uid="uid-1"
        )
        call_kwargs = mock_request.call_args[1]
        assert call_kwargs["params"] == {"days": 30, "invoiceId": "FA-2026-001"}


# --- Auto-provisioning signal ---


@pytest.mark.django_db
class TestAutoProvisioningSignal:
    @patch("apps.factpulse.client.client")
    def test_new_org_provisioned(self, mock_client):
        uid = str(uuid_lib.uuid4())
        mock_client.is_configured = True
        mock_client.create_client.return_value = {"uid": uid}

        org = Organization.objects.create(name="Signal Org", slug="signal-org")
        org.refresh_from_db()
        assert str(org.factpulse_client_uid) == uid
        mock_client.create_client.assert_called_once_with(name="Signal Org")

    @patch("apps.factpulse.client.client")
    def test_skip_if_not_configured(self, mock_client):
        mock_client.is_configured = False

        org = Organization.objects.create(name="No API", slug="no-api")
        assert org.factpulse_client_uid is None
        mock_client.create_client.assert_not_called()

    @patch("apps.factpulse.client.client")
    def test_skip_if_already_has_uid(self, mock_client):
        mock_client.is_configured = True
        uid = uuid_lib.uuid4()

        org = Organization.objects.create(
            name="Has UID", slug="has-uid", factpulse_client_uid=uid
        )
        assert org.factpulse_client_uid == uid
        mock_client.create_client.assert_not_called()

    @patch("apps.factpulse.client.client")
    def test_skip_on_update(self, mock_client):
        mock_client.is_configured = True
        mock_client.create_client.return_value = {"uid": str(uuid_lib.uuid4())}

        org = Organization.objects.create(name="Update Test", slug="update-test")
        mock_client.create_client.reset_mock()

        org.name = "Updated Name"
        org.save()
        mock_client.create_client.assert_not_called()

    @patch("apps.factpulse.client.client")
    def test_api_error_silent(self, mock_client):
        mock_client.is_configured = True
        mock_client.create_client.side_effect = FactPulseError("API down")

        org = Organization.objects.create(name="Error Org", slug="error-org")
        assert org.factpulse_client_uid is None


# --- Management command ---


COMMAND_CLIENT = "apps.factpulse.management.commands.provision_factpulse_clients.client"


@pytest.mark.django_db
class TestProvisionCommand:
    def _create_unprovisioned_org(self, name, slug):
        """Create an org that bypasses the signal (set uid then clear it)."""
        org = Organization.objects.create(
            name=name, slug=slug, factpulse_client_uid=uuid_lib.uuid4()
        )
        Organization.objects.filter(pk=org.pk).update(factpulse_client_uid=None)
        return org

    @patch("apps.factpulse.client.client")
    @patch(COMMAND_CLIENT)
    def test_provision_unprovisioned_orgs(self, mock_cmd_client, mock_signal_client):
        mock_signal_client.is_configured = True
        mock_signal_client.create_client.return_value = {"uid": str(uuid_lib.uuid4())}

        uid = str(uuid_lib.uuid4())
        mock_cmd_client.is_configured = True
        mock_cmd_client.create_client.return_value = {"uid": uid}

        org = self._create_unprovisioned_org("Unprov", "unprov")

        out = StringIO()
        call_command("provision_factpulse_clients", stdout=out)

        org.refresh_from_db()
        assert str(org.factpulse_client_uid) == uid
        assert "1 provisioned" in out.getvalue()

    @patch("apps.factpulse.client.client")
    @patch(COMMAND_CLIENT)
    def test_dry_run(self, mock_cmd_client, mock_signal_client):
        mock_signal_client.is_configured = True
        mock_signal_client.create_client.return_value = {"uid": str(uuid_lib.uuid4())}

        mock_cmd_client.is_configured = True

        org = self._create_unprovisioned_org("Dry", "dry")

        out = StringIO()
        call_command("provision_factpulse_clients", "--dry-run", stdout=out)

        org.refresh_from_db()
        assert org.factpulse_client_uid is None
        mock_cmd_client.create_client.assert_not_called()
        assert "DRY-RUN" in out.getvalue()

    @patch("apps.factpulse.client.client")
    @patch(COMMAND_CLIENT)
    def test_all_provisioned(self, mock_cmd_client, mock_signal_client):
        mock_signal_client.is_configured = True
        mock_signal_client.create_client.return_value = {"uid": str(uuid_lib.uuid4())}

        mock_cmd_client.is_configured = True

        Organization.objects.create(
            name="Done", slug="done", factpulse_client_uid=uuid_lib.uuid4()
        )

        out = StringIO()
        call_command("provision_factpulse_clients", stdout=out)
        assert "already have" in out.getvalue()

    @patch("apps.factpulse.client.client")
    @patch(COMMAND_CLIENT)
    def test_not_configured(self, mock_cmd_client, mock_signal_client):
        mock_signal_client.is_configured = False
        mock_cmd_client.is_configured = False

        self._create_unprovisioned_org("Needs It", "needs-it")

        err = StringIO()
        call_command("provision_factpulse_clients", stderr=err)
        assert "not configured" in err.getvalue()


# --- CDAR tasks ---


@pytest.mark.django_db
class TestSubmitCdarPaid:
    def _make_paid_invoice(self, org, supplier):
        from apps.billing.factories import InvoiceFactory

        return InvoiceFactory(
            organization=org,
            supplier=supplier,
            status="paid",
            en16931_data={
                "totals": {"totalGrossAmount": "240.00"},
                "recipient": {
                    "siren": "200000001",
                    "electronicAddress": {"value": "0009:200000001"},
                },
            },
        )

    @patch("apps.factpulse.tasks.client")
    def test_submits_paid_status(self, mock_client, org, supplier):
        from apps.billing.models import InvoiceAuditLog
        from apps.factpulse.tasks import submit_cdar_paid

        org.factpulse_client_uid = uuid_lib.uuid4()
        org.save(update_fields=["factpulse_client_uid"])
        supplier.siren = "100000001"
        supplier.save(update_fields=["siren"])

        invoice = self._make_paid_invoice(org, supplier)

        mock_client.submit_paid_status.return_value = {
            "flowId": "cdar-1",
            "documentId": "doc-1",
            "status": "accepted",
        }

        submit_cdar_paid(str(invoice.uuid))

        mock_client.submit_paid_status.assert_called_once()
        payload = mock_client.submit_paid_status.call_args[0][0]
        assert payload["invoiceId"] == invoice.number
        assert payload["senderSiren"] == "100000001"
        assert payload["amount"] == "240.00"

        log = InvoiceAuditLog.objects.filter(
            invoice=invoice, action="cdar_paid_submitted"
        )
        assert log.exists()

    @patch("apps.factpulse.tasks.client")
    def test_skips_missing_buyer_data(self, mock_client, org, supplier):
        from apps.billing.factories import InvoiceFactory
        from apps.billing.models import InvoiceAuditLog
        from apps.factpulse.tasks import submit_cdar_paid

        org.factpulse_client_uid = uuid_lib.uuid4()
        org.save(update_fields=["factpulse_client_uid"])
        supplier.siren = "100000001"
        supplier.save(update_fields=["siren"])

        invoice = InvoiceFactory(
            organization=org,
            supplier=supplier,
            status="paid",
            en16931_data={"recipient": {}},
        )

        submit_cdar_paid(str(invoice.uuid))

        mock_client.submit_paid_status.assert_not_called()
        log = InvoiceAuditLog.objects.filter(
            invoice=invoice, action="cdar_paid_skipped"
        )
        assert log.exists()

    @patch("apps.factpulse.tasks.client")
    def test_skips_missing_supplier_siren(self, mock_client, org, supplier):
        from apps.billing.models import InvoiceAuditLog
        from apps.factpulse.tasks import submit_cdar_paid

        org.factpulse_client_uid = uuid_lib.uuid4()
        org.save(update_fields=["factpulse_client_uid"])
        supplier.siren = ""
        supplier.save(update_fields=["siren"])

        invoice = self._make_paid_invoice(org, supplier)

        submit_cdar_paid(str(invoice.uuid))

        mock_client.submit_paid_status.assert_not_called()
        log = InvoiceAuditLog.objects.filter(
            invoice=invoice, action="cdar_paid_skipped"
        )
        assert log.exists()
        assert "supplier siren" in log.first().details["reason"]

    @patch("apps.factpulse.tasks.client")
    def test_api_error_does_not_revert_paid(self, mock_client, org, supplier):
        from apps.billing.models import InvoiceAuditLog
        from apps.factpulse.tasks import submit_cdar_paid

        org.factpulse_client_uid = uuid_lib.uuid4()
        org.save(update_fields=["factpulse_client_uid"])
        supplier.siren = "100000001"
        supplier.save(update_fields=["siren"])

        invoice = self._make_paid_invoice(org, supplier)
        mock_client.submit_paid_status.side_effect = FactPulseError("API down")

        submit_cdar_paid(str(invoice.uuid))

        invoice.refresh_from_db()
        assert invoice.status == "paid"  # NOT reverted

        log = InvoiceAuditLog.objects.filter(invoice=invoice, action="cdar_paid_error")
        assert log.exists()


@pytest.mark.django_db
class TestPollCdarEvents:
    @patch("apps.factpulse.tasks.client")
    def test_creates_audit_logs_for_new_events(self, mock_client, org, supplier):
        from apps.billing.factories import InvoiceFactory
        from apps.billing.models import InvoiceAuditLog
        from apps.factpulse.tasks import poll_cdar_events

        org.factpulse_client_uid = uuid_lib.uuid4()
        org.save(update_fields=["factpulse_client_uid"])

        invoice = InvoiceFactory(
            organization=org,
            supplier=supplier,
            status="transmitting",
            number="FA-2026-001",
            pdp_transmission_id="flow-1",
        )

        mock_client.get_cdar_lifecycle.return_value = {
            "invoices": [
                {
                    "sellerId": supplier.siren,
                    "invoiceId": "FA-2026-001",
                    "events": [
                        {
                            "statusCode": "200",
                            "statusDescription": "Déposée",
                            "at": "2026-02-17T10:00:00Z",
                        },
                    ],
                    "totalEvents": 1,
                }
            ],
            "totalInvoices": 1,
            "cutoffDays": 7,
        }

        poll_cdar_events()

        events = InvoiceAuditLog.objects.filter(invoice=invoice, action="cdar_event")
        assert events.count() == 1
        assert events.first().details["status_code"] == "200"

    @patch("apps.factpulse.tasks.client")
    def test_deduplicates_events(self, mock_client, org, supplier):
        from apps.billing.factories import InvoiceFactory
        from apps.billing.models import InvoiceAuditLog
        from apps.factpulse.tasks import poll_cdar_events

        org.factpulse_client_uid = uuid_lib.uuid4()
        org.save(update_fields=["factpulse_client_uid"])

        invoice = InvoiceFactory(
            organization=org,
            supplier=supplier,
            status="transmitted",
            number="FA-2026-002",
            pdp_transmission_id="flow-2",
        )

        # Pre-existing audit log
        InvoiceAuditLog.objects.create(
            invoice=invoice,
            action="cdar_event",
            details={"status_code": "200", "at": "2026-02-17T10:00:00Z"},
        )

        mock_client.get_cdar_lifecycle.return_value = {
            "invoices": [
                {
                    "sellerId": supplier.siren,
                    "invoiceId": "FA-2026-002",
                    "events": [
                        {
                            "statusCode": "200",
                            "statusDescription": "Déposée",
                            "at": "2026-02-17T10:00:00Z",
                        },
                        {
                            "statusCode": "201",
                            "statusDescription": "Émise",
                            "at": "2026-02-17T10:05:00Z",
                        },
                    ],
                    "totalEvents": 2,
                }
            ],
        }

        poll_cdar_events()

        events = InvoiceAuditLog.objects.filter(invoice=invoice, action="cdar_event")
        assert events.count() == 2  # 1 existing + 1 new (201), not 3

    @patch("apps.factpulse.tasks.client")
    def test_transitions_status_from_cdar(self, mock_client, org, supplier):
        from apps.billing.factories import InvoiceFactory
        from apps.factpulse.tasks import poll_cdar_events

        org.factpulse_client_uid = uuid_lib.uuid4()
        org.save(update_fields=["factpulse_client_uid"])

        invoice = InvoiceFactory(
            organization=org,
            supplier=supplier,
            status="transmitting",
            number="FA-2026-003",
            pdp_transmission_id="flow-3",
        )

        mock_client.get_cdar_lifecycle.return_value = {
            "invoices": [
                {
                    "sellerId": supplier.siren,
                    "invoiceId": "FA-2026-003",
                    "events": [
                        {
                            "statusCode": "200",
                            "statusDescription": "Déposée",
                            "at": "2026-02-17T10:00:00Z",
                        },
                    ],
                    "totalEvents": 1,
                }
            ],
        }

        poll_cdar_events()

        invoice.refresh_from_db()
        assert invoice.status == "transmitted"

    @patch("apps.factpulse.tasks.client")
    def test_afnor_fallback_for_stuck_transmitting(self, mock_client, org, supplier):
        from apps.billing.factories import InvoiceFactory
        from apps.factpulse.tasks import poll_cdar_events

        org.factpulse_client_uid = uuid_lib.uuid4()
        org.save(update_fields=["factpulse_client_uid"])

        invoice = InvoiceFactory(
            organization=org,
            supplier=supplier,
            status="transmitting",
            number="FA-2026-004",
            pdp_transmission_id="flow-4",
        )

        # No CDAR events returned
        mock_client.get_cdar_lifecycle.return_value = {
            "invoices": [],
            "totalInvoices": 0,
        }
        # But AFNOR flow says Error
        mock_client.get_flow_status.return_value = {
            "flowId": "flow-4",
            "acknowledgement": {
                "status": "Error",
                "details": [{"item": "syntax", "reasonMessage": "Invalid PDF"}],
            },
        }

        poll_cdar_events()

        invoice.refresh_from_db()
        assert invoice.status == "validated"  # Reverted
        assert invoice.factpulse_error["error_type"] == "pa_submission_failed"

    @patch("apps.factpulse.tasks.client")
    def test_refused_status_from_cdar_210(self, mock_client, org, supplier):
        from apps.billing.factories import InvoiceFactory
        from apps.factpulse.tasks import poll_cdar_events

        org.factpulse_client_uid = uuid_lib.uuid4()
        org.save(update_fields=["factpulse_client_uid"])

        invoice = InvoiceFactory(
            organization=org,
            supplier=supplier,
            status="transmitted",
            number="FA-2026-005",
            pdp_transmission_id="flow-5",
        )

        mock_client.get_cdar_lifecycle.return_value = {
            "invoices": [
                {
                    "sellerId": supplier.siren,
                    "invoiceId": "FA-2026-005",
                    "events": [
                        {
                            "statusCode": "210",
                            "statusDescription": "Refusée",
                            "at": "2026-02-17T12:00:00Z",
                        },
                    ],
                    "totalEvents": 1,
                }
            ],
        }

        poll_cdar_events()

        invoice.refresh_from_db()
        assert invoice.status == "refused"


# =====================================================================
# Additional coverage tests — client.py, tasks.py, poll_cdar.py
# =====================================================================


# --- _obtain_tokens ---


class TestObtainTokens:
    def setup_method(self):
        with patch.object(FactPulseClient, "__init__", lambda self: None):
            self.client = FactPulseClient()
        import threading

        self.client.base_url = "http://fake"
        self.client.email = "test@test.com"
        self.client.password = "pass"
        self.client.timeout = 5
        self.client._tokens = {}
        self.client._lock = threading.Lock()

    @patch("apps.factpulse.client.requests.post")
    def test_obtain_tokens_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access": "new-access",
            "refresh": "new-refresh",
            "access_lifetime": 3600,
        }
        mock_post.return_value = mock_response

        self.client._obtain_tokens(client_uid=None)

        entry = self.client._tokens[None]
        assert entry.access == "new-access"
        assert entry.refresh == "new-refresh"
        payload = mock_post.call_args[1]["json"]
        assert payload["username"] == "test@test.com"
        assert payload["password"] == "pass"

    @patch("apps.factpulse.client.requests.post")
    def test_obtain_tokens_with_client_uid(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access": "client-access",
            "refresh": "client-refresh",
            "access_lifetime": 1800,
        }
        mock_post.return_value = mock_response

        self.client._obtain_tokens(client_uid="uid-abc")

        entry = self.client._tokens["uid-abc"]
        assert entry.access == "client-access"
        payload = mock_post.call_args[1]["json"]
        assert payload["client_uid"] == "uid-abc"

    @patch("apps.factpulse.client.requests.post")
    def test_obtain_tokens_connection_error(self, mock_post):
        mock_post.side_effect = requests.ConnectionError("refused")

        with pytest.raises(FactPulseUnavailableError, match="Cannot connect"):
            self.client._obtain_tokens()

    @patch("apps.factpulse.client.requests.post")
    def test_obtain_tokens_timeout(self, mock_post):
        mock_post.side_effect = requests.Timeout("timed out")

        with pytest.raises(FactPulseUnavailableError, match="timed out"):
            self.client._obtain_tokens()

    @patch("apps.factpulse.client.requests.post")
    def test_obtain_tokens_auth_failure(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_post.return_value = mock_response

        with pytest.raises(FactPulseError, match="authentication failed"):
            self.client._obtain_tokens()

    @patch("apps.factpulse.client.requests.post")
    def test_obtain_tokens_default_lifetime(self, mock_post):
        """When access_lifetime is missing, default to 3600."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access": "tok",
            "refresh": None,
        }
        mock_post.return_value = mock_response

        self.client._obtain_tokens()

        entry = self.client._tokens[None]
        assert entry.access == "tok"
        assert entry.refresh is None


# --- _refresh_access_token ---


class TestRefreshAccessToken:
    def setup_method(self):
        with patch.object(FactPulseClient, "__init__", lambda self: None):
            self.client = FactPulseClient()
        import threading

        self.client.base_url = "http://fake"
        self.client.email = "test@test.com"
        self.client.password = "pass"
        self.client.timeout = 5
        self.client._tokens = {}
        self.client._lock = threading.Lock()

    @patch("apps.factpulse.client.requests.post")
    def test_refresh_success(self, mock_post):
        self.client._tokens[None] = _TokenEntry(
            access="old", refresh="ref-tok", expires_at=0
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access": "refreshed-access",
            "access_lifetime": 7200,
        }
        mock_post.return_value = mock_response

        self.client._refresh_access_token(client_uid=None)

        assert self.client._tokens[None].access == "refreshed-access"
        # Verify it called the refresh endpoint
        url = mock_post.call_args[0][0]
        assert "/api/token/refresh/" in url
        assert mock_post.call_args[1]["json"]["refresh"] == "ref-tok"

    @patch.object(FactPulseClient, "_obtain_tokens")
    def test_refresh_no_entry_falls_back_to_obtain(self, mock_obtain):
        """No token entry => falls back to _obtain_tokens."""
        self.client._refresh_access_token(client_uid=None)
        mock_obtain.assert_called_once_with(None)

    @patch.object(FactPulseClient, "_obtain_tokens")
    def test_refresh_no_refresh_token_falls_back(self, mock_obtain):
        """Token entry exists but has no refresh token => falls back."""
        self.client._tokens[None] = _TokenEntry(
            access="old", refresh=None, expires_at=0
        )
        self.client._refresh_access_token(client_uid=None)
        mock_obtain.assert_called_once_with(None)

    @patch.object(FactPulseClient, "_obtain_tokens")
    @patch("apps.factpulse.client.requests.post")
    def test_refresh_request_exception_falls_back(self, mock_post, mock_obtain):
        """RequestException during refresh => falls back to _obtain_tokens."""
        self.client._tokens[None] = _TokenEntry(
            access="old", refresh="ref-tok", expires_at=0
        )
        mock_post.side_effect = requests.RequestException("network error")

        self.client._refresh_access_token(client_uid=None)
        mock_obtain.assert_called_once_with(None)

    @patch.object(FactPulseClient, "_obtain_tokens")
    @patch("apps.factpulse.client.requests.post")
    def test_refresh_non_200_falls_back(self, mock_post, mock_obtain):
        """Non-200 refresh response => falls back to _obtain_tokens."""
        self.client._tokens[None] = _TokenEntry(
            access="old", refresh="ref-tok", expires_at=0
        )
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_post.return_value = mock_response

        self.client._refresh_access_token(client_uid=None)
        mock_obtain.assert_called_once_with(None)


# --- _request (ConnectionError, Timeout, 401 retry) ---


class TestRequest:
    def setup_method(self):
        with patch.object(FactPulseClient, "__init__", lambda self: None):
            self.client = FactPulseClient()
        import threading

        self.client.base_url = "http://fake"
        self.client.email = "test@test.com"
        self.client.password = "pass"
        self.client.timeout = 5
        self.client._tokens = {
            None: _TokenEntry(access="tok", refresh="ref", expires_at=9999999999),
        }
        self.client._lock = threading.Lock()

    @patch("apps.factpulse.client.requests.request")
    def test_request_connection_error(self, mock_request):
        mock_request.side_effect = requests.ConnectionError("refused")

        with pytest.raises(FactPulseUnavailableError, match="Cannot connect"):
            self.client._request("GET", "http://fake/api/test")

    @patch("apps.factpulse.client.requests.request")
    def test_request_timeout(self, mock_request):
        mock_request.side_effect = requests.Timeout("timed out")

        with pytest.raises(FactPulseUnavailableError, match="timed out"):
            self.client._request("GET", "http://fake/api/test")

    @patch.object(FactPulseClient, "_obtain_tokens")
    @patch("apps.factpulse.client.requests.request")
    def test_request_401_retry_success(self, mock_request, mock_obtain):
        """On 401, re-authenticate and retry the request."""
        first_response = MagicMock()
        first_response.status_code = 401
        first_response.content = b"unauthorized"

        second_response = MagicMock()
        second_response.status_code = 200
        second_response.json.return_value = {"ok": True}

        mock_request.side_effect = [first_response, second_response]

        result = self.client._request("GET", "http://fake/api/test")
        assert result.status_code == 200
        assert mock_request.call_count == 2
        mock_obtain.assert_called_once_with(None)

    @patch.object(FactPulseClient, "_obtain_tokens")
    @patch("apps.factpulse.client.requests.request")
    def test_request_401_retry_network_error(self, mock_request, mock_obtain):
        """On 401 retry, if network fails, raise FactPulseUnavailableError."""
        first_response = MagicMock()
        first_response.status_code = 401
        first_response.content = b"unauthorized"

        mock_request.side_effect = [first_response, requests.ConnectionError("down")]

        with pytest.raises(FactPulseUnavailableError, match="Cannot connect"):
            self.client._request("GET", "http://fake/api/test")


# --- _handle_error (various error formats) ---


class TestHandleError:
    def setup_method(self):
        with patch.object(FactPulseClient, "__init__", lambda self: None):
            self.client = FactPulseClient()

    def test_error_message_format(self):
        """errorMessage field is used as the message."""
        response = MagicMock()
        response.status_code = 422
        response.json.return_value = {
            "errorCode": "VALIDATION_ERR",
            "errorMessage": "Field X is required",
        }

        with pytest.raises(FactPulseError, match="Field X is required"):
            self.client._handle_error(response)

    def test_error_code_only_format(self):
        """errorCode without errorMessage uses errorCode + status_code."""
        response = MagicMock()
        response.status_code = 400
        response.json.return_value = {"errorCode": "BAD_REQUEST"}

        with pytest.raises(FactPulseError, match="BAD_REQUEST.*400"):
            self.client._handle_error(response)

    def test_detail_as_dict_format(self):
        """detail as dict with 'error' key."""
        response = MagicMock()
        response.status_code = 500
        response.json.return_value = {
            "detail": {"error": "Internal failure", "code": 42}
        }

        with pytest.raises(FactPulseError, match="Internal failure"):
            self.client._handle_error(response)

    def test_detail_as_dict_without_error_key(self):
        """detail as dict without 'error' key stringifies the dict."""
        response = MagicMock()
        response.status_code = 500
        response.json.return_value = {"detail": {"code": 42, "info": "something"}}

        with pytest.raises(FactPulseError) as exc_info:
            self.client._handle_error(response)
        # Falls back to str(detail)
        assert "code" in str(exc_info.value)

    def test_detail_as_str_format(self):
        """detail as a plain string."""
        response = MagicMock()
        response.status_code = 403
        response.json.return_value = {"detail": "Forbidden: no access."}

        with pytest.raises(FactPulseError, match="Forbidden: no access"):
            self.client._handle_error(response)

    def test_fallback_message_format(self):
        """No recognized error fields => fallback message."""
        response = MagicMock()
        response.status_code = 418
        response.json.return_value = {"unknown_field": True}

        with pytest.raises(FactPulseError, match="FactPulse API error.*418"):
            self.client._handle_error(response)

    def test_json_parse_error(self):
        """When response is not valid JSON, use response.text."""
        response = MagicMock()
        response.status_code = 500
        response.json.side_effect = ValueError("not JSON")
        response.text = "Internal Server Error"

        with pytest.raises(FactPulseError, match="Internal Server Error"):
            self.client._handle_error(response)

    def test_message_field_format(self):
        """Fallback to 'message' when no other known fields."""
        response = MagicMock()
        response.status_code = 422
        response.json.return_value = {"message": "Validation failed"}

        with pytest.raises(FactPulseError, match="Validation failed"):
            self.client._handle_error(response)

    def test_error_has_status_code_and_details(self):
        """Verify the FactPulseError carries status_code and details."""
        response = MagicMock()
        response.status_code = 422
        response.json.return_value = {
            "errorMessage": "Bad data",
            "details": [{"field": "name"}],
        }

        with pytest.raises(FactPulseError) as exc_info:
            self.client._handle_error(response)

        assert exc_info.value.status_code == 422
        assert exc_info.value.details["details"] == [{"field": "name"}]


# --- _poll_task_result ---


class TestPollTaskResult:
    def setup_method(self):
        with patch.object(FactPulseClient, "__init__", lambda self: None):
            self.client = FactPulseClient()
        import threading

        self.client.base_url = "http://fake"
        self.client.email = "test@test.com"
        self.client.password = "pass"
        self.client.timeout = 5
        self.client._tokens = {
            None: _TokenEntry(access="tok", refresh="ref", expires_at=9999999999),
        }
        self.client._lock = threading.Lock()

    @patch("apps.factpulse.client.time.sleep")
    @patch("apps.factpulse.client.requests.request")
    def test_poll_completed_with_pdf(self, mock_request, mock_sleep):
        """Completed task with content_b64 returns decoded PDF bytes."""
        import base64

        pdf_b64 = base64.b64encode(b"fake-pdf-content").decode()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "completed",
            "result": {"content_b64": pdf_b64},
        }
        mock_request.return_value = mock_response

        result = self.client._poll_task_result("task-1")
        assert result == b"fake-pdf-content"

    @patch("apps.factpulse.client.time.sleep")
    @patch("apps.factpulse.client.requests.request")
    def test_poll_completed_with_pdf_base64_key(self, mock_request, mock_sleep):
        """Completed task with pdf_base64 key returns decoded PDF bytes."""
        import base64

        pdf_b64 = base64.b64encode(b"pdf-data").decode()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "SUCCESS",
            "result": {"pdf_base64": pdf_b64},
        }
        mock_request.return_value = mock_response

        result = self.client._poll_task_result("task-1")
        assert result == b"pdf-data"

    @patch("apps.factpulse.client.time.sleep")
    @patch("apps.factpulse.client.requests.request")
    def test_poll_completed_with_pdf_key(self, mock_request, mock_sleep):
        """Completed task with 'pdf' key returns decoded PDF bytes."""
        import base64

        pdf_b64 = base64.b64encode(b"pdf-via-pdf-key").decode()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "completed",
            "result": {"pdf": pdf_b64},
        }
        mock_request.return_value = mock_response

        result = self.client._poll_task_result("task-1")
        assert result == b"pdf-via-pdf-key"

    @patch("apps.factpulse.client.time.sleep")
    @patch("apps.factpulse.client.requests.request")
    def test_poll_completed_with_error_inside(self, mock_request, mock_sleep):
        """Task completed but result contains errorCode/errorMessage."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "completed",
            "result": {
                "errorCode": "SCHEMA_INVALID",
                "errorMessage": "Factur-X schema validation failed",
            },
        }
        mock_request.return_value = mock_response

        with pytest.raises(FactPulseError, match="schema validation failed"):
            self.client._poll_task_result("task-1")

    @patch("apps.factpulse.client.time.sleep")
    @patch("apps.factpulse.client.requests.request")
    def test_poll_completed_no_pdf(self, mock_request, mock_sleep):
        """Task completed but no PDF in result."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "completed",
            "result": {"otherData": "value"},
        }
        mock_request.return_value = mock_response

        with pytest.raises(FactPulseError, match="no PDF in response"):
            self.client._poll_task_result("task-1")

    @patch("apps.factpulse.client.time.sleep")
    @patch("apps.factpulse.client.requests.request")
    def test_poll_failed_status(self, mock_request, mock_sleep):
        """Task with 'failed' status raises FactPulseError."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "failed",
            "result": {"errorMessage": "Processing failed"},
        }
        mock_request.return_value = mock_response

        with pytest.raises(FactPulseError, match="Processing failed"):
            self.client._poll_task_result("task-1")

    @patch("apps.factpulse.client.time.sleep")
    @patch("apps.factpulse.client.requests.request")
    def test_poll_failure_status(self, mock_request, mock_sleep):
        """Task with 'FAILURE' status raises FactPulseError."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "FAILURE",
            "result": {},
        }
        mock_request.return_value = mock_response

        with pytest.raises(FactPulseError, match="Task failed"):
            self.client._poll_task_result("task-1")

    @patch("apps.factpulse.client.time.sleep")
    @patch("apps.factpulse.client.requests.request")
    def test_poll_timeout(self, mock_request, mock_sleep):
        """Task does not complete within max_attempts."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "pending"}
        mock_request.return_value = mock_response

        with pytest.raises(FactPulseError, match="did not complete"):
            self.client._poll_task_result("task-1", max_attempts=3, interval=0)

        assert mock_sleep.call_count == 3

    @patch("apps.factpulse.client.time.sleep")
    @patch("apps.factpulse.client.requests.request")
    def test_poll_pending_then_completed(self, mock_request, mock_sleep):
        """Task polls pending first, then completed."""
        import base64

        pdf_b64 = base64.b64encode(b"finally-done").decode()

        pending = MagicMock()
        pending.status_code = 200
        pending.json.return_value = {"status": "pending"}

        completed = MagicMock()
        completed.status_code = 200
        completed.json.return_value = {
            "status": "completed",
            "result": {"content_b64": pdf_b64},
        }

        mock_request.side_effect = [pending, pending, completed]

        result = self.client._poll_task_result("task-1", max_attempts=5, interval=0)
        assert result == b"finally-done"

    @patch("apps.factpulse.client.time.sleep")
    @patch("apps.factpulse.client.requests.request")
    def test_poll_non_200_calls_handle_error(self, mock_request, mock_sleep):
        """Non-200 status code during polling calls _handle_error."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"message": "Server error"}
        mock_request.return_value = mock_response

        with pytest.raises(FactPulseError, match="Server error"):
            self.client._poll_task_result("task-1")


# --- generate_invoice (202 paths) ---


class TestGenerateInvoice202:
    def setup_method(self):
        with patch.object(FactPulseClient, "__init__", lambda self: None):
            self.client = FactPulseClient()
        import threading

        self.client.base_url = "http://fake"
        self.client.email = "test@test.com"
        self.client.password = "pass"
        self.client.timeout = 5
        self.client._tokens = {
            None: _TokenEntry(access="tok", refresh="ref", expires_at=9999999999),
        }
        self.client._lock = threading.Lock()

    @patch.object(FactPulseClient, "_poll_task_result", return_value=b"polled-pdf")
    @patch("apps.factpulse.client.requests.request")
    def test_202_with_task_id(self, mock_request, mock_poll):
        """202 with taskId delegates to _poll_task_result."""
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {"taskId": "async-task-42"}
        mock_request.return_value = mock_response

        result = self.client.generate_invoice({"data": "test"})
        assert result == b"polled-pdf"
        mock_poll.assert_called_once_with("async-task-42", client_uid=None)

    @patch("apps.factpulse.client.requests.request")
    def test_202_without_task_id(self, mock_request):
        """202 without taskId raises FactPulseError."""
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {}
        mock_request.return_value = mock_response

        with pytest.raises(FactPulseError, match="no taskId"):
            self.client.generate_invoice({"data": "test"})

    @patch("apps.factpulse.client.requests.request")
    def test_generate_invoice_error_status(self, mock_request):
        """Non-200/202 status calls _handle_error."""
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.json.return_value = {"errorMessage": "Invalid invoice data"}
        mock_request.return_value = mock_response

        with pytest.raises(FactPulseError, match="Invalid invoice data"):
            self.client.generate_invoice({"data": "test"})

    @patch("apps.factpulse.client.requests.request")
    def test_generate_invoice_with_source_pdf(self, mock_request):
        """Source PDF is passed as multipart file."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"output-pdf"
        mock_request.return_value = mock_response

        result = self.client.generate_invoice(
            {"data": "test"}, source_pdf=b"source-pdf-bytes"
        )
        assert result == b"output-pdf"
        # Verify files were passed
        call_kwargs = mock_request.call_args[1]
        assert call_kwargs["files"] is not None


# --- delete_pdp_config ---


class TestDeletePdpConfig:
    def setup_method(self):
        with patch.object(FactPulseClient, "__init__", lambda self: None):
            self.client = FactPulseClient()
        import threading

        self.client.base_url = "http://fake"
        self.client.email = "test@test.com"
        self.client.password = "pass"
        self.client.timeout = 5
        self.client._tokens = {
            None: _TokenEntry(access="tok", refresh="ref", expires_at=9999999999),
            "uid-1": _TokenEntry(access="tok-1", refresh="ref", expires_at=9999999999),
        }
        self.client._lock = threading.Lock()

    @patch("apps.factpulse.client.requests.request")
    def test_delete_pdp_config_success(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_request.return_value = mock_response

        result = self.client.delete_pdp_config("uid-1")
        assert result is True
        assert mock_request.call_args[0][0] == "DELETE"

    @patch("apps.factpulse.client.requests.request")
    def test_delete_pdp_config_200(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_request.return_value = mock_response

        result = self.client.delete_pdp_config("uid-1")
        assert result is True

    @patch("apps.factpulse.client.requests.request")
    def test_delete_pdp_config_error(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.json.return_value = {"message": "Not found"}
        mock_request.return_value = mock_response

        with pytest.raises(FactPulseError, match="Not found"):
            self.client.delete_pdp_config("uid-1")


# --- list_clients ---


class TestListClients:
    def setup_method(self):
        with patch.object(FactPulseClient, "__init__", lambda self: None):
            self.client = FactPulseClient()
        import threading

        self.client.base_url = "http://fake"
        self.client.email = "test@test.com"
        self.client.password = "pass"
        self.client.timeout = 5
        self.client._tokens = {
            None: _TokenEntry(access="tok", refresh="ref", expires_at=9999999999),
        }
        self.client._lock = threading.Lock()

    @patch("apps.factpulse.client.requests.request")
    def test_list_clients_success(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"uid": "uid-1", "name": "Client A"},
            {"uid": "uid-2", "name": "Client B"},
        ]
        mock_request.return_value = mock_response

        result = self.client.list_clients()
        assert len(result) == 2
        assert result[0]["uid"] == "uid-1"

    @patch("apps.factpulse.client.requests.request")
    def test_list_clients_error(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"message": "Internal error"}
        mock_request.return_value = mock_response

        with pytest.raises(FactPulseError, match="Internal error"):
            self.client.list_clients()

    def test_list_clients_not_configured(self):
        self.client.base_url = ""
        with pytest.raises(FactPulseUnavailableError):
            self.client.list_clients()


# --- search_directory_lines ---


class TestSearchDirectoryLines:
    def setup_method(self):
        with patch.object(FactPulseClient, "__init__", lambda self: None):
            self.client = FactPulseClient()
        import threading

        self.client.base_url = "http://fake"
        self.client.email = "test@test.com"
        self.client.password = "pass"
        self.client.timeout = 5
        self.client._tokens = {
            None: _TokenEntry(access="tok", refresh="ref", expires_at=9999999999),
        }
        self.client._lock = threading.Lock()

    @patch("apps.factpulse.client.requests.request")
    def test_search_directory_lines_success(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"results": []}'
        mock_response.json.return_value = {
            "results": [
                {"addressingIdentifier": "0009:123456789", "siren": "123456789"}
            ],
            "total": 1,
        }
        mock_request.return_value = mock_response

        result = self.client.search_directory_lines("123456789")
        assert result["total"] == 1
        assert result["results"][0]["siren"] == "123456789"
        # Verify the POST body contains the siren filter
        call_kwargs = mock_request.call_args[1]
        assert call_kwargs["json"]["filters"]["siren"]["value"] == "123456789"

    @patch("apps.factpulse.client.requests.request")
    def test_search_directory_lines_error(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"errorMessage": "Invalid siren"}'
        mock_response.json.return_value = {"errorMessage": "Invalid siren"}
        mock_request.return_value = mock_response

        with pytest.raises(FactPulseError, match="Invalid siren"):
            self.client.search_directory_lines("bad")

    def test_search_directory_lines_not_configured(self):
        self.client.base_url = ""
        with pytest.raises(FactPulseUnavailableError):
            self.client.search_directory_lines("123456789")


# --- get_directory_siren ---


class TestGetDirectorySiren:
    def setup_method(self):
        with patch.object(FactPulseClient, "__init__", lambda self: None):
            self.client = FactPulseClient()
        import threading

        self.client.base_url = "http://fake"
        self.client.email = "test@test.com"
        self.client.password = "pass"
        self.client.timeout = 5
        self.client._tokens = {
            None: _TokenEntry(access="tok", refresh="ref", expires_at=9999999999),
            "uid-1": _TokenEntry(access="tok-1", refresh="ref", expires_at=9999999999),
        }
        self.client._lock = threading.Lock()

    @patch("apps.factpulse.client.requests.request")
    def test_get_directory_siren_success(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "siren": "123456789",
            "companyName": "ACME Corp",
        }
        mock_request.return_value = mock_response

        result = self.client.get_directory_siren("123456789", client_uid="uid-1")
        assert result["siren"] == "123456789"
        assert "/code-insee:123456789" in mock_request.call_args[0][1]

    @patch("apps.factpulse.client.requests.request")
    def test_get_directory_siren_error(self, mock_request):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.json.return_value = {"message": "SIREN not found"}
        mock_request.return_value = mock_response

        with pytest.raises(FactPulseError, match="SIREN not found"):
            self.client.get_directory_siren("999999999")

    def test_get_directory_siren_not_configured(self):
        self.client.base_url = ""
        with pytest.raises(FactPulseUnavailableError):
            self.client.get_directory_siren("123456789")


# =====================================================================
# tasks.py — generate_and_validate_invoice
# =====================================================================


@pytest.mark.django_db
class TestGenerateAndValidateInvoice:
    def _make_processing_invoice(self, org, supplier):
        from apps.billing.factories import InvoiceFactory

        return InvoiceFactory(
            organization=org,
            supplier=supplier,
            status="processing",
            en16931_data={"invoiceLines": [], "totals": {"totalGrossAmount": "100.00"}},
        )

    @patch("apps.webhooks.services.emit_webhook")
    @patch("apps.billing.services.invoice_service.check_auto_cancel")
    @patch("apps.factpulse.tasks.client")
    @patch("apps.factpulse.tasks._generate_source_pdf", return_value=b"source-pdf")
    def test_full_success_flow(
        self, mock_gen_pdf, mock_client, mock_auto_cancel, mock_webhook, org, supplier
    ):
        from apps.billing.models import InvoiceAuditLog
        from apps.factpulse.tasks import generate_and_validate_invoice

        org.factpulse_client_uid = uuid_lib.uuid4()
        org.save(update_fields=["factpulse_client_uid"])

        invoice = self._make_processing_invoice(org, supplier)
        mock_client.generate_invoice.return_value = b"facturx-pdf-bytes"

        generate_and_validate_invoice(str(invoice.uuid))

        invoice.refresh_from_db()
        assert invoice.status == "validated"
        assert invoice.facturx_status == "generated"
        assert invoice.factpulse_error is None
        assert invoice.pdf_file  # PDF was saved

        mock_gen_pdf.assert_called_once_with(invoice)
        mock_client.generate_invoice.assert_called_once()
        mock_auto_cancel.assert_called_once_with(invoice)
        mock_webhook.assert_called_once()

        log = InvoiceAuditLog.objects.filter(
            invoice=invoice, action="status_change", new_status="validated"
        )
        assert log.exists()

    @patch("apps.factpulse.tasks._generate_source_pdf", return_value=b"source-pdf")
    def test_invoice_not_found(self, mock_gen_pdf):
        from apps.factpulse.tasks import generate_and_validate_invoice

        # Should not raise, just return
        generate_and_validate_invoice(str(uuid_lib.uuid4()))
        mock_gen_pdf.assert_not_called()

    @patch("apps.factpulse.tasks._generate_source_pdf", return_value=b"source-pdf")
    def test_wrong_status(self, mock_gen_pdf, org, supplier):
        from apps.billing.factories import InvoiceFactory
        from apps.factpulse.tasks import generate_and_validate_invoice

        invoice = InvoiceFactory(organization=org, supplier=supplier, status="draft")

        generate_and_validate_invoice(str(invoice.uuid))

        invoice.refresh_from_db()
        assert invoice.status == "draft"
        mock_gen_pdf.assert_not_called()

    @patch("apps.webhooks.services.emit_webhook")
    @patch("apps.factpulse.tasks._generate_source_pdf", return_value=b"source-pdf")
    def test_no_client_uid(self, mock_gen_pdf, mock_webhook, org, supplier):
        from apps.billing.models import InvoiceAuditLog
        from apps.factpulse.tasks import generate_and_validate_invoice

        # org has no factpulse_client_uid
        invoice = self._make_processing_invoice(org, supplier)

        generate_and_validate_invoice(str(invoice.uuid))

        invoice.refresh_from_db()
        assert invoice.status == "draft"
        assert invoice.factpulse_error["error_type"] == "not_provisioned"

        log = InvoiceAuditLog.objects.filter(
            invoice=invoice, action="status_change", new_status="draft"
        )
        assert log.exists()

    @patch("apps.webhooks.services.emit_webhook")
    @patch("apps.factpulse.tasks.client")
    @patch("apps.factpulse.tasks._generate_source_pdf", return_value=b"source-pdf")
    def test_factpulse_error(
        self, mock_gen_pdf, mock_client, mock_webhook, org, supplier
    ):
        from apps.factpulse.tasks import generate_and_validate_invoice

        org.factpulse_client_uid = uuid_lib.uuid4()
        org.save(update_fields=["factpulse_client_uid"])

        invoice = self._make_processing_invoice(org, supplier)
        mock_client.generate_invoice.side_effect = FactPulseError(
            "Schema invalid", status_code=422, details={"errorCode": "SCHEMA_ERR"}
        )

        generate_and_validate_invoice(str(invoice.uuid))

        invoice.refresh_from_db()
        assert invoice.status == "draft"
        assert invoice.factpulse_error["error_type"] == "validation_rejected"
        assert "Schema invalid" in invoice.factpulse_error["message"]

    @patch("apps.webhooks.services.emit_webhook")
    @patch("apps.factpulse.tasks.client")
    @patch("apps.factpulse.tasks._generate_source_pdf", return_value=b"source-pdf")
    def test_factpulse_unavailable_error(
        self, mock_gen_pdf, mock_client, mock_webhook, org, supplier
    ):
        from apps.factpulse.tasks import generate_and_validate_invoice

        org.factpulse_client_uid = uuid_lib.uuid4()
        org.save(update_fields=["factpulse_client_uid"])

        invoice = self._make_processing_invoice(org, supplier)
        mock_client.generate_invoice.side_effect = FactPulseUnavailableError(
            "Cannot connect"
        )

        generate_and_validate_invoice(str(invoice.uuid))

        invoice.refresh_from_db()
        assert invoice.status == "draft"
        # FactPulseUnavailableError is a subclass of FactPulseError,
        # so it is caught by the first except block in the task
        assert invoice.factpulse_error["error_type"] == "validation_rejected"

    @patch("apps.webhooks.services.emit_webhook")
    @patch("apps.factpulse.tasks.client")
    @patch("apps.factpulse.tasks._generate_source_pdf")
    def test_unexpected_error(
        self, mock_gen_pdf, mock_client, mock_webhook, org, supplier
    ):
        from apps.factpulse.tasks import generate_and_validate_invoice

        org.factpulse_client_uid = uuid_lib.uuid4()
        org.save(update_fields=["factpulse_client_uid"])

        invoice = self._make_processing_invoice(org, supplier)
        mock_gen_pdf.side_effect = RuntimeError("WeasyPrint crashed")

        generate_and_validate_invoice(str(invoice.uuid))

        invoice.refresh_from_db()
        assert invoice.status == "draft"
        assert invoice.factpulse_error["error_type"] == "timeout"


# =====================================================================
# tasks.py — generate_source_pdf (the task, not the helper)
# =====================================================================


@pytest.mark.django_db
class TestGenerateSourcePdfTask:
    @patch("apps.factpulse.tasks._generate_source_pdf", return_value=b"preview-pdf")
    def test_generates_and_saves_pdf(self, mock_gen_pdf, org, supplier):
        from apps.billing.factories import InvoiceFactory
        from apps.factpulse.tasks import generate_source_pdf

        invoice = InvoiceFactory(organization=org, supplier=supplier, status="draft")

        generate_source_pdf(str(invoice.uuid))

        invoice.refresh_from_db()
        assert invoice.pdf_file
        mock_gen_pdf.assert_called_once()

    @patch("apps.factpulse.tasks._generate_source_pdf")
    def test_invoice_not_found(self, mock_gen_pdf):
        from apps.factpulse.tasks import generate_source_pdf

        generate_source_pdf(str(uuid_lib.uuid4()))
        mock_gen_pdf.assert_not_called()


# =====================================================================
# tasks.py — transmit_invoice
# =====================================================================


@pytest.mark.django_db
class TestTransmitInvoice:
    def _make_transmitting_invoice(self, org, supplier):
        from django.core.files.base import ContentFile

        from apps.billing.factories import InvoiceFactory

        invoice = InvoiceFactory(
            organization=org,
            supplier=supplier,
            status="transmitting",
            facturx_status="generated",
            number="FA-2026-100",
        )
        invoice.pdf_file.save("test.pdf", ContentFile(b"facturx-pdf"), save=True)
        return invoice

    @patch("apps.factpulse.tasks.client")
    def test_full_success_flow(self, mock_client, org, supplier):
        from apps.billing.models import InvoiceAuditLog
        from apps.factpulse.tasks import transmit_invoice

        org.factpulse_client_uid = uuid_lib.uuid4()
        org.save(update_fields=["factpulse_client_uid"])

        invoice = self._make_transmitting_invoice(org, supplier)
        mock_client.submit_flow.return_value = {
            "flowId": "flow-99",
            "submittedAt": "2026-02-20T10:00:00Z",
        }

        transmit_invoice(str(invoice.uuid))

        invoice.refresh_from_db()
        assert invoice.status == "transmitting"  # stays transmitting
        assert invoice.pdp_transmission_id == "flow-99"
        assert invoice.pdp_status == "submitted"
        assert invoice.factpulse_error is None

        log = InvoiceAuditLog.objects.filter(invoice=invoice, action="flow_submitted")
        assert log.exists()
        assert log.first().details["flow_id"] == "flow-99"

    def test_invoice_not_found(self):
        from apps.factpulse.tasks import transmit_invoice

        # Should not raise
        transmit_invoice(str(uuid_lib.uuid4()))

    def test_wrong_status(self, org, supplier):
        from apps.billing.factories import InvoiceFactory
        from apps.factpulse.tasks import transmit_invoice

        invoice = InvoiceFactory(
            organization=org, supplier=supplier, status="validated"
        )

        transmit_invoice(str(invoice.uuid))

        invoice.refresh_from_db()
        assert invoice.status == "validated"

    @patch("apps.factpulse.tasks.client")
    def test_no_pdf_file(self, mock_client, org, supplier):
        from apps.billing.factories import InvoiceFactory
        from apps.factpulse.tasks import transmit_invoice

        org.factpulse_client_uid = uuid_lib.uuid4()
        org.save(update_fields=["factpulse_client_uid"])

        invoice = InvoiceFactory(
            organization=org,
            supplier=supplier,
            status="transmitting",
        )
        # No pdf_file set

        transmit_invoice(str(invoice.uuid))

        invoice.refresh_from_db()
        assert invoice.status == "validated"  # reverted
        assert invoice.factpulse_error["error_type"] == "transmission_failed"
        assert "No Factur-X PDF" in invoice.factpulse_error["message"]

    @patch("apps.factpulse.tasks.client")
    def test_factpulse_error_reverts_to_validated(self, mock_client, org, supplier):
        from apps.billing.models import InvoiceAuditLog
        from apps.factpulse.tasks import transmit_invoice

        org.factpulse_client_uid = uuid_lib.uuid4()
        org.save(update_fields=["factpulse_client_uid"])

        invoice = self._make_transmitting_invoice(org, supplier)
        mock_client.submit_flow.side_effect = FactPulseError(
            "Flow rejected",
            details={"errorCode": "FLOW_ERR", "details": [{"item": "syntax"}]},
        )

        transmit_invoice(str(invoice.uuid))

        invoice.refresh_from_db()
        assert invoice.status == "validated"
        assert invoice.factpulse_error["error_type"] == "transmission_failed"
        assert invoice.factpulse_error["error_code"] == "FLOW_ERR"

        log = InvoiceAuditLog.objects.filter(
            invoice=invoice,
            action="status_change",
            new_status="validated",
        )
        assert log.exists()

    @patch("apps.factpulse.tasks.client")
    def test_unavailable_error_reverts_to_validated(self, mock_client, org, supplier):
        from apps.factpulse.tasks import transmit_invoice

        org.factpulse_client_uid = uuid_lib.uuid4()
        org.save(update_fields=["factpulse_client_uid"])

        invoice = self._make_transmitting_invoice(org, supplier)
        mock_client.submit_flow.side_effect = FactPulseUnavailableError(
            "API unreachable"
        )

        transmit_invoice(str(invoice.uuid))

        invoice.refresh_from_db()
        assert invoice.status == "validated"
        assert invoice.factpulse_error["error_type"] == "transmission_failed"


# =====================================================================
# tasks.py — _handle_factpulse_error (direct test)
# =====================================================================


@pytest.mark.django_db
class TestHandleFactpulseError:
    @patch("apps.webhooks.services.emit_webhook")
    def test_reverts_to_draft_and_stores_error(self, mock_webhook, org, supplier):
        from apps.billing.factories import InvoiceFactory
        from apps.billing.models import InvoiceAuditLog
        from apps.factpulse.tasks import _handle_factpulse_error

        invoice = InvoiceFactory(
            organization=org, supplier=supplier, status="processing"
        )

        error = FactPulseError(
            "Validation failed",
            status_code=422,
            details={"errorCode": "VAL_ERR", "details": [{"field": "lines"}]},
        )

        _handle_factpulse_error(invoice, error, "validation_rejected")

        invoice.refresh_from_db()
        assert invoice.status == "draft"
        assert invoice.factpulse_error["error_type"] == "validation_rejected"
        assert invoice.factpulse_error["message"] == "Validation failed"
        assert invoice.factpulse_error["error_code"] == "VAL_ERR"
        assert invoice.factpulse_error["errors"] == [{"field": "lines"}]

        log = InvoiceAuditLog.objects.filter(
            invoice=invoice, action="status_change", new_status="draft"
        )
        assert log.exists()

        mock_webhook.assert_called_once()
        call_args = mock_webhook.call_args
        assert call_args[0][1] == "invoice.error"
        assert call_args[0][2]["error_type"] == "validation_rejected"

    @patch("apps.webhooks.services.emit_webhook")
    def test_handles_non_dict_details(self, mock_webhook, org, supplier):
        """When error.details is not a dict (e.g. a string), handle gracefully."""
        from apps.billing.factories import InvoiceFactory
        from apps.factpulse.tasks import _handle_factpulse_error

        invoice = InvoiceFactory(
            organization=org, supplier=supplier, status="processing"
        )

        error = RuntimeError("Something unexpected")

        _handle_factpulse_error(invoice, error, "timeout")

        invoice.refresh_from_db()
        assert invoice.status == "draft"
        assert invoice.factpulse_error["error_type"] == "timeout"
        assert invoice.factpulse_error["error_code"] == ""
        assert invoice.factpulse_error["errors"] == []


# =====================================================================
# poll_cdar management command
# =====================================================================


POLL_CMD_CLIENT = "apps.factpulse.management.commands.poll_cdar.client"


@pytest.mark.django_db
class TestPollCdarCommand:
    @patch(POLL_CMD_CLIENT)
    @patch("apps.factpulse.tasks.client")
    def test_not_configured(self, mock_task_client, mock_cmd_client):
        mock_cmd_client.is_configured = False

        err = StringIO()
        call_command("poll_cdar", stderr=err)
        assert "not configured" in err.getvalue()

    @patch("apps.factpulse.management.commands.poll_cdar.poll_cdar_events")
    @patch(POLL_CMD_CLIENT)
    @patch("apps.factpulse.tasks.client")
    def test_configured_runs_poll(
        self, mock_task_client, mock_cmd_client, mock_poll_fn
    ):
        mock_cmd_client.is_configured = True

        out = StringIO()
        call_command("poll_cdar", stdout=out)

        mock_poll_fn.assert_called_once_with(invoice_number=None, days=7)
        output = out.getvalue()
        assert "Polling CDAR lifecycle" in output
        assert "Done" in output

    @patch("apps.factpulse.management.commands.poll_cdar.poll_cdar_events")
    @patch(POLL_CMD_CLIENT)
    @patch("apps.factpulse.tasks.client")
    def test_with_invoice_flag(self, mock_task_client, mock_cmd_client, mock_poll_fn):
        mock_cmd_client.is_configured = True

        out = StringIO()
        call_command("poll_cdar", "--invoice", "FA-2026-001", stdout=out)

        mock_poll_fn.assert_called_once_with(invoice_number="FA-2026-001", days=7)
        assert "FA-2026-001" in out.getvalue()

    @patch("apps.factpulse.management.commands.poll_cdar.poll_cdar_events")
    @patch(POLL_CMD_CLIENT)
    @patch("apps.factpulse.tasks.client")
    def test_with_days_flag(self, mock_task_client, mock_cmd_client, mock_poll_fn):
        mock_cmd_client.is_configured = True

        out = StringIO()
        call_command("poll_cdar", "--days", "30", stdout=out)

        mock_poll_fn.assert_called_once_with(invoice_number=None, days=30)
        assert "days=30" in out.getvalue()

    @patch("apps.factpulse.management.commands.poll_cdar.poll_cdar_events")
    @patch(POLL_CMD_CLIENT)
    @patch("apps.factpulse.tasks.client")
    def test_with_both_flags(self, mock_task_client, mock_cmd_client, mock_poll_fn):
        mock_cmd_client.is_configured = True

        out = StringIO()
        call_command(
            "poll_cdar", "--invoice", "FA-2026-010", "--days", "14", stdout=out
        )

        mock_poll_fn.assert_called_once_with(invoice_number="FA-2026-010", days=14)

    @patch(POLL_CMD_CLIENT)
    @patch("apps.factpulse.tasks.client")
    def test_reports_new_events_count(
        self, mock_task_client, mock_cmd_client, org, supplier
    ):
        """Integration: verifies the command counts new cdar_event audit logs."""
        from apps.billing.factories import InvoiceFactory

        mock_cmd_client.is_configured = True

        org.factpulse_client_uid = uuid_lib.uuid4()
        org.save(update_fields=["factpulse_client_uid"])

        InvoiceFactory(
            organization=org,
            supplier=supplier,
            status="transmitting",
            number="FA-CMD-001",
            pdp_transmission_id="flow-cmd",
        )

        mock_task_client.get_cdar_lifecycle.return_value = {
            "invoices": [
                {
                    "sellerId": supplier.siren,
                    "invoiceId": "FA-CMD-001",
                    "events": [
                        {
                            "statusCode": "200",
                            "statusDescription": "Deposee",
                            "at": "2026-02-20T10:00:00Z",
                        },
                    ],
                    "totalEvents": 1,
                }
            ],
        }
        mock_task_client.get_flow_status.return_value = {
            "acknowledgement": {"status": "Pending"},
        }

        out = StringIO()
        call_command("poll_cdar", stdout=out)

        output = out.getvalue()
        assert "1 new CDAR event(s)" in output
