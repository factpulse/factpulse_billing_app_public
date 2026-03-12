"""Tests for the payments app."""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.billing.factories import InvoiceFactory, SupplierFactory
from apps.core.exceptions import ConflictError, UnprocessableError
from apps.payments.adapters import PaymentProviderAdapter, PaymentResult, WebhookEvent
from apps.payments.models import PaymentEventLog, PaymentTransaction, ProviderConfig
from apps.payments.services import (
    create_checkout,
    get_adapter,
    get_provider_config,
    handle_webhook,
)


def _payments_available():
    """Check if payment extras are installed and enabled."""
    if not __import__("django.conf", fromlist=["settings"]).settings.STRIPE_ENABLED:
        return False
    try:
        import stripe  # noqa: F401

        return True
    except ImportError:
        return False


_skip = pytest.mark.skipif(
    not _payments_available(),
    reason="STRIPE_ENABLED is false or payment SDKs not installed",
)


@_skip
class AdapterBaseTest(TestCase):
    def test_payment_result_defaults(self):
        result = PaymentResult()
        assert result.provider_payment_id == ""
        assert result.status == "created"
        assert result.checkout_url == ""
        assert result.payment_method == ""
        assert result.provider_data == {}

    def test_webhook_event_defaults(self):
        event = WebhookEvent()
        assert event.provider_event_id == ""
        assert event.event_type == ""
        assert event.provider_payment_id == ""
        assert event.metadata == {}
        assert event.raw_data == {}

    def test_adapter_is_abstract(self):
        with pytest.raises(TypeError):
            PaymentProviderAdapter()


@_skip
class GetAdapterTest(TestCase):
    def setUp(self):
        self.supplier = SupplierFactory()
        self.org = self.supplier.organization

    def test_get_adapter_stripe(self):
        config = ProviderConfig.objects.create(
            organization=self.org,
            provider="stripe",
            api_key="sk_test_xxx",
            webhook_secret="whsec_xxx",
        )
        adapter = get_adapter(config)
        from apps.payments.providers.stripe.adapter import StripeAdapter

        assert isinstance(adapter, StripeAdapter)

    def test_get_adapter_unknown_provider(self):
        config = ProviderConfig.objects.create(
            organization=self.org,
            provider="unknown",
            api_key="xxx",
        )
        with pytest.raises(UnprocessableError, match="Unknown payment provider"):
            get_adapter(config)


@_skip
class GetProviderConfigTest(TestCase):
    def setUp(self):
        self.supplier = SupplierFactory()
        self.org = self.supplier.organization

    def test_get_active_config(self):
        config = ProviderConfig.objects.create(
            organization=self.org,
            provider="stripe",
            api_key="sk_test_xxx",
            is_active=True,
        )
        result = get_provider_config(self.org, "stripe")
        assert result.pk == config.pk

    def test_get_config_not_found(self):
        with pytest.raises(UnprocessableError, match="not configured"):
            get_provider_config(self.org, "stripe")

    def test_get_inactive_config_not_found(self):
        ProviderConfig.objects.create(
            organization=self.org,
            provider="stripe",
            api_key="sk_test_xxx",
            is_active=False,
        )
        with pytest.raises(UnprocessableError, match="not configured"):
            get_provider_config(self.org, "stripe")


def _make_mock_adapter():
    """Return a fresh mock adapter that simulates a successful checkout."""
    adapter = MagicMock()
    adapter.create_checkout.return_value = PaymentResult(
        provider_payment_id="cs_mock",
        status="created",
        checkout_url="https://checkout.stripe.com/pay/cs_mock",
        payment_method="card",
    )
    return adapter


@_skip
class CheckoutServiceTest(TestCase):
    def setUp(self):
        self.supplier = SupplierFactory()
        self.org = self.supplier.organization
        self.invoice = InvoiceFactory(
            supplier=self.supplier,
            status="validated",
            number="FACT-2026-001",
            total_incl_tax=Decimal("1200.00"),
            currency_code="EUR",
            en16931_data={
                "recipient": {"email": "client@example.com"},
                "totals": {
                    "totalNetAmount": "1000.00",
                    "vatAmount": "200.00",
                    "totalGrossAmount": "1200.00",
                },
            },
        )
        self.provider_config = ProviderConfig.objects.create(
            organization=self.org,
            provider="stripe",
            api_key="sk_test_xxx",
            webhook_secret="whsec_xxx",
        )

    @patch(
        "apps.payments.services.get_adapter", side_effect=lambda _: _make_mock_adapter()
    )
    def test_create_checkout_returns_transaction(self, _mock):
        txn = create_checkout(
            organization=self.org,
            invoice=self.invoice,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )

        assert txn.checkout_url == "https://checkout.stripe.com/pay/cs_mock"
        assert txn.status == "created"
        assert txn.provider == "stripe"
        assert txn.amount == Decimal("1200.00")
        assert txn.invoice == self.invoice
        assert txn.organization == self.org
        assert txn.currency == "EUR"

    @patch(
        "apps.payments.services.get_adapter", side_effect=lambda _: _make_mock_adapter()
    )
    def test_create_checkout_returns_existing_pending(self, _mock):
        existing = PaymentTransaction.objects.create(
            organization=self.org,
            invoice=self.invoice,
            provider="stripe",
            provider_payment_id="cs_existing",
            amount=Decimal("1200.00"),
            status="created",
            checkout_url="https://checkout.stripe.com/pay/cs_existing",
        )

        txn = create_checkout(
            organization=self.org,
            invoice=self.invoice,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )

        assert txn.pk == existing.pk

    def test_create_checkout_draft_invoice_fails(self):
        self.invoice.status = "draft"
        self.invoice.save()

        with pytest.raises(ConflictError):
            create_checkout(
                organization=self.org,
                invoice=self.invoice,
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
            )

    def test_create_checkout_paid_invoice_fails(self):
        self.invoice.status = "paid"
        self.invoice.save()

        with pytest.raises(ConflictError):
            create_checkout(
                organization=self.org,
                invoice=self.invoice,
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
            )

    def test_create_checkout_cancelled_invoice_fails(self):
        self.invoice.status = "cancelled"
        self.invoice.save()

        with pytest.raises(ConflictError):
            create_checkout(
                organization=self.org,
                invoice=self.invoice,
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
            )

    @patch(
        "apps.payments.services.get_adapter", side_effect=lambda _: _make_mock_adapter()
    )
    def test_create_checkout_transmitted_invoice_ok(self, _mock):
        self.invoice.status = "transmitted"
        self.invoice.save()

        txn = create_checkout(
            organization=self.org,
            invoice=self.invoice,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )
        assert txn.status == "created"

    @patch(
        "apps.payments.services.get_adapter", side_effect=lambda _: _make_mock_adapter()
    )
    def test_create_checkout_accepted_invoice_ok(self, _mock):
        self.invoice.status = "accepted"
        self.invoice.save()

        txn = create_checkout(
            organization=self.org,
            invoice=self.invoice,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )
        assert txn.status == "created"

    @patch(
        "apps.payments.services.get_adapter", side_effect=lambda _: _make_mock_adapter()
    )
    def test_create_checkout_no_recipient_email(self, _mock):
        self.invoice.en16931_data = {
            "totals": {
                "totalNetAmount": "1000.00",
                "vatAmount": "200.00",
                "totalGrossAmount": "1200.00",
            },
        }
        self.invoice.save()

        txn = create_checkout(
            organization=self.org,
            invoice=self.invoice,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )
        assert txn.checkout_url

    def test_create_checkout_no_provider_config(self):
        self.provider_config.delete()

        with pytest.raises(UnprocessableError, match="not configured"):
            create_checkout(
                organization=self.org,
                invoice=self.invoice,
                success_url="https://example.com/success",
                cancel_url="https://example.com/cancel",
            )


@_skip
class WebhookServiceTest(TestCase):
    def setUp(self):
        self.supplier = SupplierFactory()
        self.org = self.supplier.organization
        self.invoice = InvoiceFactory(
            supplier=self.supplier,
            status="validated",
            number="FACT-2026-002",
            total_incl_tax=Decimal("500.00"),
            en16931_data={
                "totals": {
                    "totalNetAmount": "416.67",
                    "vatAmount": "83.33",
                    "totalGrossAmount": "500.00",
                },
            },
        )
        self.provider_config = ProviderConfig.objects.create(
            organization=self.org,
            provider="stripe",
            api_key="sk_test_xxx",
            webhook_secret="whsec_xxx",
        )
        self.txn = PaymentTransaction.objects.create(
            organization=self.org,
            invoice=self.invoice,
            provider="stripe",
            provider_payment_id="cs_test_456",
            amount=Decimal("500.00"),
            status="created",
        )

    @patch("apps.payments.services.get_adapter")
    def test_handle_webhook_confirms_payment(self, mock_get_adapter):
        mock_adapter = MagicMock()
        mock_adapter.verify_webhook.return_value = True
        mock_adapter.parse_webhook.return_value = WebhookEvent(
            provider_event_id="evt_123",
            event_type="payment.confirmed",
            provider_payment_id="cs_test_456",
            metadata={"payment_method": "card"},
            raw_data={"id": "evt_123", "type": "checkout.session.completed"},
        )
        mock_get_adapter.return_value = mock_adapter

        result = handle_webhook(
            provider="stripe",
            provider_config=self.provider_config,
            headers={"Stripe-Signature": "sig"},
            body=b"{}",
        )

        assert result is True

        self.txn.refresh_from_db()
        assert self.txn.status == "confirmed"
        assert self.txn.payment_method == "card"

        self.invoice.refresh_from_db()
        assert self.invoice.status == "paid"
        assert self.invoice.payment_reference == "stripe:cs_test_456"

        event_log = PaymentEventLog.objects.get(provider_event_id="evt_123")
        assert event_log.processed is True
        assert event_log.event_type == "payment.confirmed"

    @patch("apps.payments.services.get_adapter")
    def test_handle_webhook_duplicate_skipped(self, mock_get_adapter):
        mock_adapter = MagicMock()
        mock_adapter.verify_webhook.return_value = True
        mock_adapter.parse_webhook.return_value = WebhookEvent(
            provider_event_id="evt_dup",
            event_type="payment.confirmed",
            provider_payment_id="cs_test_456",
            metadata={},
            raw_data={},
        )
        mock_get_adapter.return_value = mock_adapter

        handle_webhook("stripe", self.provider_config, {}, b"{}")

        result = handle_webhook("stripe", self.provider_config, {}, b"{}")
        assert result is False
        assert PaymentEventLog.objects.filter(provider_event_id="evt_dup").count() == 1

    @patch("apps.payments.services.get_adapter")
    def test_handle_webhook_invalid_signature(self, mock_get_adapter):
        mock_adapter = MagicMock()
        mock_adapter.verify_webhook.return_value = False
        mock_get_adapter.return_value = mock_adapter

        with pytest.raises(UnprocessableError, match="Invalid webhook signature"):
            handle_webhook("stripe", self.provider_config, {}, b"{}")

    @patch("apps.payments.services.get_adapter")
    def test_handle_webhook_payment_failed(self, mock_get_adapter):
        mock_adapter = MagicMock()
        mock_adapter.verify_webhook.return_value = True
        mock_adapter.parse_webhook.return_value = WebhookEvent(
            provider_event_id="evt_fail",
            event_type="payment.failed",
            provider_payment_id="cs_test_456",
            metadata={},
            raw_data={"id": "evt_fail"},
        )
        mock_get_adapter.return_value = mock_adapter

        result = handle_webhook("stripe", self.provider_config, {}, b"{}")

        assert result is True
        self.txn.refresh_from_db()
        assert self.txn.status == "failed"
        self.invoice.refresh_from_db()
        assert self.invoice.status == "validated"

    @patch("apps.payments.services.get_adapter")
    def test_handle_webhook_no_matching_transaction(self, mock_get_adapter):
        mock_adapter = MagicMock()
        mock_adapter.verify_webhook.return_value = True
        mock_adapter.parse_webhook.return_value = WebhookEvent(
            provider_event_id="evt_unknown",
            event_type="payment.confirmed",
            provider_payment_id="cs_nonexistent",
            metadata={},
            raw_data={},
        )
        mock_get_adapter.return_value = mock_adapter

        result = handle_webhook("stripe", self.provider_config, {}, b"{}")
        assert result is True
        assert PaymentEventLog.objects.filter(provider_event_id="evt_unknown").exists()

    @patch("apps.payments.services.get_adapter")
    def test_handle_webhook_already_paid_invoice(self, mock_get_adapter):
        self.invoice.status = "paid"
        self.invoice.save()

        mock_adapter = MagicMock()
        mock_adapter.verify_webhook.return_value = True
        mock_adapter.parse_webhook.return_value = WebhookEvent(
            provider_event_id="evt_already_paid",
            event_type="payment.confirmed",
            provider_payment_id="cs_test_456",
            metadata={},
            raw_data={},
        )
        mock_get_adapter.return_value = mock_adapter

        result = handle_webhook("stripe", self.provider_config, {}, b"{}")
        assert result is True
        self.invoice.refresh_from_db()
        assert self.invoice.status == "paid"

    @patch("apps.payments.services.get_adapter")
    def test_handle_webhook_unhandled_event_type(self, mock_get_adapter):
        mock_adapter = MagicMock()
        mock_adapter.verify_webhook.return_value = True
        mock_adapter.parse_webhook.return_value = WebhookEvent(
            provider_event_id="evt_other",
            event_type="customer.created",
            provider_payment_id="",
            metadata={},
            raw_data={},
        )
        mock_get_adapter.return_value = mock_adapter

        result = handle_webhook("stripe", self.provider_config, {}, b"{}")
        assert result is True
        event_log = PaymentEventLog.objects.get(provider_event_id="evt_other")
        assert event_log.processed is True


@_skip
class StripeAdapterTest(TestCase):
    def setUp(self):
        from apps.payments.providers.stripe.adapter import StripeAdapter

        self.adapter = StripeAdapter(
            api_key="sk_test_xxx",
            webhook_secret="whsec_test",
        )

    @patch("apps.payments.providers.stripe.adapter.stripe.checkout.Session.create")
    def test_create_checkout(self, mock_create):
        mock_create.return_value = MagicMock(
            id="cs_test_adapt",
            url="https://checkout.stripe.com/pay/cs_test_adapt",
            status="open",
            payment_method_types=["card"],
        )

        result = self.adapter.create_checkout(
            amount=Decimal("100.00"),
            currency="EUR",
            invoice_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            invoice_number="FACT-001",
            customer_email="test@example.com",
            success_url="https://example.com/ok",
            cancel_url="https://example.com/cancel",
        )

        assert result.checkout_url == "https://checkout.stripe.com/pay/cs_test_adapt"
        assert result.provider_payment_id == "cs_test_adapt"
        assert result.status == "created"

        call_args = mock_create.call_args
        assert call_args.kwargs["line_items"][0]["price_data"]["unit_amount"] == 10000

    @patch("apps.payments.providers.stripe.adapter.stripe.checkout.Session.retrieve")
    def test_get_payment_status_complete(self, mock_retrieve):
        mock_retrieve.return_value = MagicMock(
            id="cs_test_status",
            status="complete",
            payment_method_types=["card"],
        )

        result = self.adapter.get_payment_status("cs_test_status")
        assert result.status == "confirmed"
        assert result.payment_method == "card"

    @patch("apps.payments.providers.stripe.adapter.stripe.checkout.Session.retrieve")
    def test_get_payment_status_expired(self, mock_retrieve):
        mock_retrieve.return_value = MagicMock(
            id="cs_expired",
            status="expired",
            payment_method_types=[],
        )

        result = self.adapter.get_payment_status("cs_expired")
        assert result.status == "failed"
        assert result.payment_method == ""

    @patch("apps.payments.providers.stripe.adapter.stripe.checkout.Session.retrieve")
    def test_get_payment_status_open(self, mock_retrieve):
        mock_retrieve.return_value = MagicMock(
            id="cs_open",
            status="open",
            payment_method_types=["card"],
        )

        result = self.adapter.get_payment_status("cs_open")
        assert result.status == "pending"

    @patch("apps.payments.providers.stripe.adapter.stripe.Webhook.construct_event")
    def test_verify_webhook_valid(self, mock_construct):
        mock_construct.return_value = {"id": "evt_1"}
        assert self.adapter.verify_webhook({"Stripe-Signature": "sig"}, b"body") is True

    @patch("apps.payments.providers.stripe.adapter.stripe.Webhook.construct_event")
    def test_verify_webhook_invalid(self, mock_construct):
        import stripe as stripe_lib

        mock_construct.side_effect = stripe_lib.SignatureVerificationError(
            "bad", sig_header="sig"
        )
        assert (
            self.adapter.verify_webhook({"Stripe-Signature": "bad"}, b"body") is False
        )

    @patch("apps.payments.providers.stripe.adapter.stripe.Webhook.construct_event")
    def test_parse_webhook_checkout_completed(self, mock_construct):
        mock_construct.return_value = {
            "id": "evt_parse",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_parsed",
                    "metadata": {"invoice_uuid": "abc"},
                    "payment_method_types": ["card"],
                    "amount_total": 10000,
                    "currency": "eur",
                }
            },
        }

        event = self.adapter.parse_webhook({"Stripe-Signature": "sig"}, b"body")
        assert event.event_type == "payment.confirmed"
        assert event.provider_event_id == "evt_parse"
        assert event.provider_payment_id == "cs_parsed"
        assert event.metadata["payment_method"] == "card"
        assert event.metadata["amount_total"] == 10000

    @patch("apps.payments.providers.stripe.adapter.stripe.Webhook.construct_event")
    def test_parse_webhook_async_payment_succeeded(self, mock_construct):
        mock_construct.return_value = {
            "id": "evt_async_ok",
            "type": "checkout.session.async_payment_succeeded",
            "data": {
                "object": {
                    "id": "cs_async",
                    "metadata": {},
                    "payment_method_types": ["sepa_debit"],
                    "amount_total": 5000,
                    "currency": "eur",
                }
            },
        }

        event = self.adapter.parse_webhook({"Stripe-Signature": "sig"}, b"body")
        assert event.event_type == "payment.confirmed"

    @patch("apps.payments.providers.stripe.adapter.stripe.Webhook.construct_event")
    def test_parse_webhook_async_payment_failed(self, mock_construct):
        mock_construct.return_value = {
            "id": "evt_async_fail",
            "type": "checkout.session.async_payment_failed",
            "data": {
                "object": {
                    "id": "cs_fail",
                    "metadata": {},
                    "payment_method_types": ["sepa_debit"],
                    "amount_total": 5000,
                    "currency": "eur",
                }
            },
        }

        event = self.adapter.parse_webhook({"Stripe-Signature": "sig"}, b"body")
        assert event.event_type == "payment.failed"

    @patch("apps.payments.providers.stripe.adapter.stripe.Webhook.construct_event")
    def test_parse_webhook_session_expired(self, mock_construct):
        mock_construct.return_value = {
            "id": "evt_expired",
            "type": "checkout.session.expired",
            "data": {
                "object": {
                    "id": "cs_exp",
                    "metadata": {},
                    "payment_method_types": [],
                    "amount_total": 0,
                    "currency": "eur",
                }
            },
        }

        event = self.adapter.parse_webhook({"Stripe-Signature": "sig"}, b"body")
        assert event.event_type == "payment.failed"

    @patch("apps.payments.providers.stripe.adapter.stripe.Webhook.construct_event")
    def test_parse_webhook_unknown_event_type(self, mock_construct):
        mock_construct.return_value = {
            "id": "evt_unknown_type",
            "type": "charge.refunded",
            "data": {
                "object": {
                    "id": "ch_ref",
                    "metadata": {},
                }
            },
        }

        event = self.adapter.parse_webhook({"Stripe-Signature": "sig"}, b"body")
        assert event.event_type == "charge.refunded"


@_skip
class ModelTest(TestCase):
    def setUp(self):
        self.supplier = SupplierFactory()
        self.org = self.supplier.organization

    def test_provider_config_str(self):
        config = ProviderConfig.objects.create(
            organization=self.org,
            provider="stripe",
            api_key="sk_test",
        )
        assert "stripe" in str(config)
        assert str(self.org) in str(config)

    def test_provider_config_unique_together(self):
        ProviderConfig.objects.create(
            organization=self.org, provider="stripe", api_key="sk_1"
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ProviderConfig.objects.create(
                    organization=self.org, provider="stripe", api_key="sk_2"
                )

    def test_payment_transaction_str(self):
        invoice = InvoiceFactory(supplier=self.supplier, status="validated")
        txn = PaymentTransaction.objects.create(
            organization=self.org,
            invoice=invoice,
            provider="stripe",
            provider_payment_id="cs_123",
            amount=Decimal("100.00"),
            status="created",
        )
        assert "stripe" in str(txn)
        assert "cs_123" in str(txn)
        assert "created" in str(txn)

    def test_payment_event_log_str(self):
        log = PaymentEventLog.objects.create(
            provider="stripe",
            provider_event_id="evt_str",
            event_type="payment.confirmed",
            payload={},
        )
        assert "stripe" in str(log)
        assert "payment.confirmed" in str(log)

    def test_payment_event_log_ordering(self):
        PaymentEventLog.objects.create(
            provider="stripe",
            provider_event_id="evt_a",
            event_type="payment.confirmed",
            payload={},
        )
        PaymentEventLog.objects.create(
            provider="stripe",
            provider_event_id="evt_b",
            event_type="payment.failed",
            payload={},
        )
        logs = list(PaymentEventLog.objects.all())
        assert logs[0].provider_event_id == "evt_b"

    def test_api_key_encrypted(self):
        config = ProviderConfig.objects.create(
            organization=self.org,
            provider="stripe",
            api_key="sk_live_secret_key_123",
        )
        config.refresh_from_db()
        assert config.api_key == "sk_live_secret_key_123"

    def test_transaction_default_values(self):
        invoice = InvoiceFactory(supplier=self.supplier, status="validated")
        txn = PaymentTransaction.objects.create(
            organization=self.org,
            invoice=invoice,
            provider="stripe",
            amount=Decimal("50.00"),
        )
        assert txn.status == "created"
        assert txn.currency == "EUR"
        assert txn.provider_payment_id == ""
        assert txn.payment_method == ""
        assert txn.checkout_url == ""
        assert txn.provider_data == {}


# --- API View Tests ---


@_skip
@pytest.mark.django_db
class TestCheckoutView:
    url_tpl = "/api/v1/payments/invoices/{uuid}/checkout/"

    @patch(
        "apps.payments.services.get_adapter",
        side_effect=lambda _: _make_mock_adapter(),
    )
    def test_create_checkout_ok(self, _mock, auth_api_client, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier,
            status="validated",
            number="FACT-V-001",
            total_incl_tax=Decimal("100.00"),
            en16931_data={
                "totals": {
                    "totalNetAmount": "83.33",
                    "vatAmount": "16.67",
                    "totalGrossAmount": "100.00",
                },
            },
        )
        ProviderConfig.objects.create(
            organization=org,
            provider="stripe",
            api_key="sk_test",
            webhook_secret="whsec_test",
        )
        resp = auth_api_client.post(
            self.url_tpl.format(uuid=invoice.uuid), {}, format="json"
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "checkout_url" in data
        assert "transaction" in data
        assert data["transaction"]["status"] == "created"

    def test_checkout_invoice_not_found(self, auth_api_client):
        import uuid as _uuid

        resp = auth_api_client.post(
            self.url_tpl.format(uuid=_uuid.uuid4()), {}, format="json"
        )
        assert resp.status_code == 404

    def test_checkout_unauthenticated(self, api_client, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, status="validated")
        resp = api_client.post(
            self.url_tpl.format(uuid=invoice.uuid), {}, format="json"
        )
        assert resp.status_code == 401


@_skip
@pytest.mark.django_db
class TestPaymentStatusView:
    url_tpl = "/api/v1/payments/invoices/{uuid}/status/"

    def test_get_status(self, auth_api_client, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, status="validated")
        PaymentTransaction.objects.create(
            organization=org,
            invoice=invoice,
            provider="stripe",
            provider_payment_id="cs_1",
            amount=Decimal("100.00"),
            status="created",
        )
        resp = auth_api_client.get(self.url_tpl.format(uuid=invoice.uuid))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "created"

    def test_get_status_empty(self, auth_api_client, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, status="validated")
        resp = auth_api_client.get(self.url_tpl.format(uuid=invoice.uuid))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_status_unauthenticated(self, api_client, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, status="validated")
        resp = api_client.get(self.url_tpl.format(uuid=invoice.uuid))
        assert resp.status_code == 401


@_skip
@pytest.mark.django_db
class TestProviderConfigView:
    url = "/api/v1/payments/providers/"

    def test_list_configs(self, auth_api_client, org):
        ProviderConfig.objects.create(
            organization=org, provider="stripe", api_key="sk_test"
        )
        resp = auth_api_client.get(self.url)
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_create_config(self, auth_api_client, org):
        resp = auth_api_client.post(
            self.url,
            {
                "provider": "stripe",
                "api_key": "sk_test_new",
                "webhook_secret": "whsec_new",
            },
            format="json",
        )
        assert resp.status_code == 201
        assert ProviderConfig.objects.filter(
            organization=org, provider="stripe"
        ).exists()

    def test_update_existing_config(self, auth_api_client, org):
        ProviderConfig.objects.create(
            organization=org, provider="stripe", api_key="sk_old"
        )
        resp = auth_api_client.post(
            self.url,
            {"provider": "stripe", "api_key": "sk_new"},
            format="json",
        )
        assert resp.status_code == 200
        config = ProviderConfig.objects.get(organization=org, provider="stripe")
        assert config.api_key == "sk_new"

    def test_create_config_member_forbidden(self, api_client, member_user, org):
        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(member_user)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        resp = api_client.post(
            self.url,
            {"provider": "stripe", "api_key": "sk_test"},
            format="json",
        )
        assert resp.status_code == 403


@_skip
@pytest.mark.django_db
class TestStripeWebhookView:
    url = "/api/v1/payments/webhooks/stripe/"

    @patch("apps.payments.services.handle_webhook")
    def test_webhook_processed(self, mock_handle, api_client, org):
        ProviderConfig.objects.create(
            organization=org,
            provider="stripe",
            api_key="sk_test",
            webhook_secret="whsec_test",
        )
        mock_handle.return_value = True
        resp = api_client.post(
            self.url,
            data=b'{"type": "checkout.session.completed"}',
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig_test",
        )
        assert resp.status_code == 200
        assert resp.json()["processed"] is True

    def test_webhook_no_valid_config(self, api_client):
        resp = api_client.post(
            self.url,
            data=b'{"type": "test"}',
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="bad_sig",
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_signature"

    @patch("apps.payments.services.handle_webhook")
    def test_webhook_tries_each_config(self, mock_handle, api_client, org):
        ProviderConfig.objects.create(
            organization=org,
            provider="stripe",
            api_key="sk_1",
            webhook_secret="whsec_bad",
        )
        from apps.core.exceptions import UnprocessableError as UE

        mock_handle.side_effect = UE("Invalid webhook signature.")
        resp = api_client.post(
            self.url,
            data=b"{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )
        assert resp.status_code == 400


@_skip
@pytest.mark.django_db
class TestSerializers:
    def test_checkout_request_defaults(self):
        from apps.payments.serializers import CheckoutRequestSerializer

        s = CheckoutRequestSerializer(data={})
        assert s.is_valid()
        assert s.validated_data["provider"] == "stripe"

    def test_checkout_request_custom_provider(self):
        from apps.payments.serializers import CheckoutRequestSerializer

        s = CheckoutRequestSerializer(data={"provider": "paypal"})
        assert s.is_valid()
        assert s.validated_data["provider"] == "paypal"

    def test_provider_config_create_serializer(self):
        from apps.payments.serializers import ProviderConfigCreateSerializer

        s = ProviderConfigCreateSerializer(
            data={"provider": "stripe", "api_key": "sk_test"}
        )
        assert s.is_valid()
        assert s.validated_data["webhook_secret"] == ""

    def test_transaction_serializer(self, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, status="validated")
        txn = PaymentTransaction.objects.create(
            organization=org,
            invoice=invoice,
            provider="stripe",
            provider_payment_id="cs_1",
            amount=Decimal("100.00"),
        )
        from apps.payments.serializers import PaymentTransactionSerializer

        data = PaymentTransactionSerializer(txn).data
        assert data["invoice_uuid"] == str(invoice.uuid)
        assert data["provider"] == "stripe"
        assert data["status"] == "created"

    def test_provider_config_serializer(self, org):
        config = ProviderConfig.objects.create(
            organization=org, provider="stripe", api_key="sk_test"
        )
        from apps.payments.serializers import ProviderConfigSerializer

        data = ProviderConfigSerializer(config).data
        assert data["provider"] == "stripe"
        assert "api_key" not in data


# --- Phase 2: Stripe subscription mapper tests ---


@_skip
class StripeMapperTest(TestCase):
    def setUp(self):
        self.supplier = SupplierFactory()
        self.org = self.supplier.organization
        self.provider_config = ProviderConfig.objects.create(
            organization=self.org,
            provider="stripe",
            api_key="sk_test",
            default_supplier=self.supplier,
        )

    def test_basic_mapping(self):
        from apps.payments.providers.stripe.mapper import stripe_invoice_to_payload

        stripe_inv = {
            "id": "in_abc123",
            "total": 12000,  # 120.00 EUR
            "subtotal": 10000,
            "tax": 2000,
            "currency": "eur",
            "created": 1709640000,  # 2024-03-05
            "due_date": 1710244800,
            "customer_name": "Client SA",
            "customer_email": "client@example.com",
            "customer_address": {
                "line1": "123 Rue de la Paix",
                "postal_code": "75001",
                "city": "Paris",
                "country": "FR",
            },
            "lines": {
                "data": [
                    {
                        "description": "Pro Plan (Mar 2024)",
                        "quantity": 1,
                        "amount": 10000,
                        "tax_amounts": [{"tax_rate": {"percentage": 20.0}}],
                    }
                ]
            },
        }

        payload = stripe_invoice_to_payload(
            stripe_inv, provider_config=self.provider_config
        )

        assert payload["external_id"] == "in_abc123"
        assert payload["supplier_id"] == str(self.supplier.uuid)
        assert payload["is_internal"] is True

        data = payload["en16931_data"]
        assert data["totals"]["totalGrossAmount"] == "120.00"
        assert data["totals"]["totalNetAmount"] == "100.00"
        assert data["totals"]["vatAmount"] == "20.00"
        assert data["recipient"]["name"] == "Client SA"
        assert data["recipient"]["email"] == "client@example.com"
        assert data["recipient"]["postalAddress"]["city"] == "Paris"
        assert len(data["invoiceLines"]) == 1
        assert data["invoiceLines"][0]["itemName"] == "Pro Plan (Mar 2024)"
        assert data["invoiceLines"][0]["unitNetPrice"] == "100.00"
        assert data["invoiceLines"][0]["manualVatRate"] == "20.0"

    def test_mapping_no_tax(self):
        from apps.payments.providers.stripe.mapper import stripe_invoice_to_payload

        stripe_inv = {
            "id": "in_notax",
            "total": 5000,
            "subtotal": 5000,
            "tax": None,
            "currency": "eur",
            "created": 1709640000,
            "due_date": None,
            "customer_name": "Client B",
            "customer_email": "",
            "lines": {"data": []},
        }

        payload = stripe_invoice_to_payload(
            stripe_inv, provider_config=self.provider_config
        )
        data = payload["en16931_data"]
        assert data["totals"]["vatAmount"] == "0.00"
        assert "dueDate" not in data["references"]
        assert data["invoiceLines"] == []

    def test_mapping_with_tax_id(self):
        from apps.payments.providers.stripe.mapper import stripe_invoice_to_payload

        stripe_inv = {
            "id": "in_vat",
            "total": 12000,
            "subtotal": 10000,
            "tax": 2000,
            "currency": "eur",
            "created": 1709640000,
            "customer_name": "GmbH DE",
            "customer_email": "de@example.com",
            "customer_tax_ids": [{"type": "eu_vat", "value": "DE123456789"}],
            "lines": {"data": []},
        }

        payload = stripe_invoice_to_payload(
            stripe_inv, provider_config=self.provider_config
        )
        assert payload["en16931_data"]["recipient"]["vatNumber"] == "DE123456789"

    def test_mapping_multiple_lines(self):
        from apps.payments.providers.stripe.mapper import stripe_invoice_to_payload

        stripe_inv = {
            "id": "in_multi",
            "total": 30000,
            "subtotal": 25000,
            "tax": 5000,
            "currency": "eur",
            "created": 1709640000,
            "customer_name": "Multi Client",
            "customer_email": "multi@test.com",
            "lines": {
                "data": [
                    {
                        "description": "Item A",
                        "quantity": 2,
                        "amount": 15000,
                        "tax_amounts": [{"tax_rate": {"percentage": 20.0}}],
                    },
                    {
                        "description": "Item B",
                        "quantity": 1,
                        "amount": 10000,
                        "tax_amounts": [],
                    },
                ]
            },
        }

        payload = stripe_invoice_to_payload(
            stripe_inv, provider_config=self.provider_config
        )
        lines = payload["en16931_data"]["invoiceLines"]
        assert len(lines) == 2
        assert lines[0]["quantity"] == "2"
        assert lines[0]["unitNetPrice"] == "75.00"
        assert lines[0]["lineNetAmount"] == "150.00"
        assert lines[1]["lineNumber"] == 2
        assert lines[1]["manualVatRate"] == "0.00"
        assert lines[1]["vatCategory"] == "Z"

    def test_mapping_without_default_supplier(self):
        from apps.payments.providers.stripe.mapper import stripe_invoice_to_payload

        self.provider_config.default_supplier = None
        self.provider_config.save()

        stripe_inv = {
            "id": "in_nosup",
            "total": 1000,
            "subtotal": 1000,
            "tax": 0,
            "currency": "eur",
            "created": 1709640000,
            "customer_name": "Test",
            "customer_email": "t@t.com",
            "lines": {"data": []},
        }

        payload = stripe_invoice_to_payload(
            stripe_inv, provider_config=self.provider_config
        )
        assert "supplier_id" not in payload


# --- Phase 2: invoice.finalized / invoice.paid webhook tests ---


@_skip
class InvoiceFinalizedTest(TestCase):
    def setUp(self):
        self.supplier = SupplierFactory()
        self.org = self.supplier.organization
        self.provider_config = ProviderConfig.objects.create(
            organization=self.org,
            provider="stripe",
            api_key="sk_test",
            webhook_secret="whsec_test",
            default_supplier=self.supplier,
        )

    @patch("apps.payments.services.get_adapter")
    @patch("apps.billing.services.invoice_service.validate_invoice")
    def test_invoice_finalized_creates_invoice(self, mock_validate, mock_get_adapter):
        mock_adapter = MagicMock()
        mock_adapter.verify_webhook.return_value = True
        mock_adapter.parse_webhook.return_value = WebhookEvent(
            provider_event_id="evt_inv_fin",
            event_type="invoice.finalized",
            provider_payment_id="in_stripe_123",
            metadata={},
            raw_data={
                "data": {
                    "object": {
                        "id": "in_stripe_123",
                        "total": 12000,
                        "subtotal": 10000,
                        "tax": 2000,
                        "currency": "eur",
                        "created": 1709640000,
                        "customer_name": "Client Auto",
                        "customer_email": "auto@test.com",
                        "lines": {
                            "data": [
                                {
                                    "description": "SaaS Pro",
                                    "quantity": 1,
                                    "amount": 10000,
                                    "tax_amounts": [{"tax_rate": {"percentage": 20.0}}],
                                }
                            ]
                        },
                    }
                }
            },
        )
        mock_get_adapter.return_value = mock_adapter

        result = handle_webhook(
            provider="stripe",
            provider_config=self.provider_config,
            headers={"Stripe-Signature": "sig"},
            body=b"{}",
        )

        assert result is True
        mock_validate.assert_called_once()

        from apps.billing.models import Invoice

        invoice = Invoice.objects.get(external_id="in_stripe_123")
        assert invoice.organization == self.org
        assert invoice.supplier == self.supplier

    @patch("apps.payments.services.get_adapter")
    def test_invoice_finalized_no_default_supplier_skips(self, mock_get_adapter):
        self.provider_config.default_supplier = None
        self.provider_config.save()

        mock_adapter = MagicMock()
        mock_adapter.verify_webhook.return_value = True
        mock_adapter.parse_webhook.return_value = WebhookEvent(
            provider_event_id="evt_nosup",
            event_type="invoice.finalized",
            provider_payment_id="in_nosup",
            metadata={},
            raw_data={"data": {"object": {"id": "in_nosup"}}},
        )
        mock_get_adapter.return_value = mock_adapter

        result = handle_webhook(
            provider="stripe",
            provider_config=self.provider_config,
            headers={},
            body=b"{}",
        )
        assert result is True

        from apps.billing.models import Invoice

        assert not Invoice.objects.filter(external_id="in_nosup").exists()

    @patch("apps.payments.services.get_adapter")
    @patch("apps.billing.services.invoice_service.validate_invoice")
    def test_invoice_finalized_duplicate_skips(self, mock_validate, mock_get_adapter):
        # Create an existing invoice with this external_id
        InvoiceFactory(
            supplier=self.supplier,
            status="validated",
            external_id="in_existing",
        )

        mock_adapter = MagicMock()
        mock_adapter.verify_webhook.return_value = True
        mock_adapter.parse_webhook.return_value = WebhookEvent(
            provider_event_id="evt_dup_inv",
            event_type="invoice.finalized",
            provider_payment_id="in_existing",
            metadata={},
            raw_data={"data": {"object": {"id": "in_existing"}}},
        )
        mock_get_adapter.return_value = mock_adapter

        handle_webhook("stripe", self.provider_config, {}, b"{}")
        mock_validate.assert_not_called()


@_skip
class InvoicePaidTest(TestCase):
    def setUp(self):
        self.supplier = SupplierFactory()
        self.org = self.supplier.organization
        self.provider_config = ProviderConfig.objects.create(
            organization=self.org,
            provider="stripe",
            api_key="sk_test",
            webhook_secret="whsec_test",
        )
        self.invoice = InvoiceFactory(
            supplier=self.supplier,
            status="validated",
            number="FACT-SUB-001",
            external_id="in_stripe_paid",
            total_incl_tax=Decimal("120.00"),
            en16931_data={
                "totals": {
                    "totalNetAmount": "100.00",
                    "vatAmount": "20.00",
                    "totalGrossAmount": "120.00",
                },
            },
        )

    @patch("apps.payments.services.get_adapter")
    def test_invoice_paid_marks_paid(self, mock_get_adapter):
        mock_adapter = MagicMock()
        mock_adapter.verify_webhook.return_value = True
        mock_adapter.parse_webhook.return_value = WebhookEvent(
            provider_event_id="evt_inv_paid",
            event_type="invoice.paid",
            provider_payment_id="in_stripe_paid",
            metadata={},
            raw_data={
                "data": {
                    "object": {
                        "id": "in_stripe_paid",
                        "amount_paid": 12000,
                    }
                }
            },
        )
        mock_get_adapter.return_value = mock_adapter

        result = handle_webhook("stripe", self.provider_config, {}, b"{}")
        assert result is True

        self.invoice.refresh_from_db()
        assert self.invoice.status == "paid"
        assert "stripe:in_stripe_paid" in self.invoice.payment_reference

    @patch("apps.payments.services.get_adapter")
    def test_invoice_paid_no_matching_invoice(self, mock_get_adapter):
        mock_adapter = MagicMock()
        mock_adapter.verify_webhook.return_value = True
        mock_adapter.parse_webhook.return_value = WebhookEvent(
            provider_event_id="evt_nomatch",
            event_type="invoice.paid",
            provider_payment_id="in_nonexistent",
            metadata={},
            raw_data={
                "data": {"object": {"id": "in_nonexistent", "amount_paid": 5000}}
            },
        )
        mock_get_adapter.return_value = mock_adapter

        result = handle_webhook("stripe", self.provider_config, {}, b"{}")
        assert result is True  # processed without error

    @patch("apps.payments.services.get_adapter")
    def test_invoice_paid_already_paid_skips(self, mock_get_adapter):
        self.invoice.status = "paid"
        self.invoice.save()

        mock_adapter = MagicMock()
        mock_adapter.verify_webhook.return_value = True
        mock_adapter.parse_webhook.return_value = WebhookEvent(
            provider_event_id="evt_already_paid2",
            event_type="invoice.paid",
            provider_payment_id="in_stripe_paid",
            metadata={},
            raw_data={
                "data": {"object": {"id": "in_stripe_paid", "amount_paid": 12000}}
            },
        )
        mock_get_adapter.return_value = mock_adapter

        result = handle_webhook("stripe", self.provider_config, {}, b"{}")
        assert result is True

    @patch("apps.payments.services.get_adapter")
    def test_invoice_paid_still_processing_skips(self, mock_get_adapter):
        self.invoice.status = "processing"
        self.invoice.save()

        mock_adapter = MagicMock()
        mock_adapter.verify_webhook.return_value = True
        mock_adapter.parse_webhook.return_value = WebhookEvent(
            provider_event_id="evt_processing",
            event_type="invoice.paid",
            provider_payment_id="in_stripe_paid",
            metadata={},
            raw_data={
                "data": {"object": {"id": "in_stripe_paid", "amount_paid": 12000}}
            },
        )
        mock_get_adapter.return_value = mock_adapter

        result = handle_webhook("stripe", self.provider_config, {}, b"{}")
        assert result is True
        self.invoice.refresh_from_db()
        assert self.invoice.status == "processing"


# --- Phase 2: Stripe adapter event type mapping ---


@_skip
class StripeAdapterPhase2Test(TestCase):
    def setUp(self):
        from apps.payments.providers.stripe.adapter import StripeAdapter

        self.adapter = StripeAdapter(api_key="sk_test", webhook_secret="whsec_test")

    @patch("apps.payments.providers.stripe.adapter.stripe.Webhook.construct_event")
    def test_parse_invoice_finalized(self, mock_construct):
        mock_construct.return_value = {
            "id": "evt_inv_fin",
            "type": "invoice.finalized",
            "data": {
                "object": {
                    "id": "in_123",
                    "metadata": {},
                    "amount_total": 12000,
                    "currency": "eur",
                }
            },
        }

        event = self.adapter.parse_webhook({"Stripe-Signature": "sig"}, b"body")
        assert event.event_type == "invoice.finalized"
        assert event.provider_payment_id == "in_123"

    @patch("apps.payments.providers.stripe.adapter.stripe.Webhook.construct_event")
    def test_parse_invoice_payment_succeeded(self, mock_construct):
        mock_construct.return_value = {
            "id": "evt_inv_paid",
            "type": "invoice.payment_succeeded",
            "data": {
                "object": {
                    "id": "in_456",
                    "metadata": {},
                    "amount_paid": 12000,
                    "currency": "eur",
                }
            },
        }

        event = self.adapter.parse_webhook({"Stripe-Signature": "sig"}, b"body")
        assert event.event_type == "invoice.paid"

    @patch("apps.payments.providers.stripe.adapter.stripe.Webhook.construct_event")
    def test_parse_invoice_payment_failed(self, mock_construct):
        mock_construct.return_value = {
            "id": "evt_inv_fail",
            "type": "invoice.payment_failed",
            "data": {
                "object": {
                    "id": "in_789",
                    "metadata": {},
                }
            },
        }

        event = self.adapter.parse_webhook({"Stripe-Signature": "sig"}, b"body")
        assert event.event_type == "payment.failed"


# --- Phase 3: get_adapter for new providers ---


@_skip
class GetAdapterMultiProviderTest(TestCase):
    def setUp(self):
        self.supplier = SupplierFactory()
        self.org = self.supplier.organization

    def test_get_adapter_gocardless(self):
        config = ProviderConfig.objects.create(
            organization=self.org,
            provider="gocardless",
            api_key="sandbox_token",
            webhook_secret="gc_secret",
            config={"environment": "sandbox"},
        )
        adapter = get_adapter(config)
        from apps.payments.providers.gocardless.adapter import GoCardlessAdapter

        assert isinstance(adapter, GoCardlessAdapter)
        assert adapter.environment == "sandbox"

    def test_get_adapter_fintecture(self):
        config = ProviderConfig.objects.create(
            organization=self.org,
            provider="fintecture",
            api_key="app_id_test",
            webhook_secret="fint_secret",
            config={"app_secret": "secret123"},
        )
        adapter = get_adapter(config)
        from apps.payments.providers.fintecture.adapter import FintectureAdapter

        assert isinstance(adapter, FintectureAdapter)
        assert adapter.app_secret == "secret123"


# --- Phase 3: GoCardless adapter tests ---


@_skip
class GoCardlessAdapterTest(TestCase):
    def setUp(self):
        from apps.payments.providers.gocardless.adapter import GoCardlessAdapter

        self.adapter = GoCardlessAdapter(
            api_key="sandbox_token",
            webhook_secret="gc_webhook_secret",
            environment="sandbox",
        )

    def test_verify_webhook_valid(self):
        import hashlib
        import hmac

        body = b'{"events": []}'
        sig = hmac.new(b"gc_webhook_secret", body, hashlib.sha256).hexdigest()
        assert self.adapter.verify_webhook({"Webhook-Signature": sig}, body) is True

    def test_verify_webhook_invalid(self):
        assert (
            self.adapter.verify_webhook({"Webhook-Signature": "bad"}, b"body") is False
        )

    def test_verify_webhook_no_secret(self):
        from apps.payments.providers.gocardless.adapter import GoCardlessAdapter

        adapter = GoCardlessAdapter(api_key="t", webhook_secret="")
        assert adapter.verify_webhook({}, b"body") is False

    def test_parse_webhook_payment_confirmed(self):
        import json

        body = json.dumps(
            {
                "events": [
                    {
                        "id": "EV001",
                        "resource_type": "payments",
                        "action": "confirmed",
                        "links": {"payment": "PM001"},
                    }
                ]
            }
        ).encode()

        event = self.adapter.parse_webhook({}, body)
        assert event.event_type == "payment.confirmed"
        assert event.provider_event_id == "EV001"
        assert event.provider_payment_id == "PM001"
        assert event.metadata["payment_method"] == "sepa_debit"

    def test_parse_webhook_payment_failed(self):
        import json

        body = json.dumps(
            {
                "events": [
                    {
                        "id": "EV002",
                        "resource_type": "payments",
                        "action": "failed",
                        "links": {"payment": "PM002"},
                    }
                ]
            }
        ).encode()

        event = self.adapter.parse_webhook({}, body)
        assert event.event_type == "payment.failed"

    def test_parse_webhook_mandate_active(self):
        import json

        body = json.dumps(
            {
                "events": [
                    {
                        "id": "EV003",
                        "resource_type": "mandates",
                        "action": "active",
                        "links": {"mandate": "MD001"},
                    }
                ]
            }
        ).encode()

        event = self.adapter.parse_webhook({}, body)
        assert event.event_type == "mandate.active"
        assert event.provider_payment_id == "MD001"

    def test_parse_webhook_empty_events(self):
        import json

        body = json.dumps({"events": []}).encode()
        event = self.adapter.parse_webhook({}, body)
        assert event.event_type == "unknown"


# --- Phase 3: Fintecture adapter tests ---


@_skip
class FintectureAdapterTest(TestCase):
    def setUp(self):
        from apps.payments.providers.fintecture.adapter import FintectureAdapter

        self.adapter = FintectureAdapter(
            api_key="app_id",
            webhook_secret="fint_secret",
            app_secret="app_secret",
        )

    def test_verify_webhook_valid(self):
        import hashlib
        import hmac

        body = b'{"meta": {"status": "payment_successful"}}'
        sig = hmac.new(b"fint_secret", body, hashlib.sha256).hexdigest()
        assert self.adapter.verify_webhook({"Signature": sig}, body) is True

    def test_verify_webhook_invalid(self):
        assert self.adapter.verify_webhook({"Signature": "bad"}, b"body") is False

    def test_parse_webhook_payment_successful(self):
        import json

        body = json.dumps(
            {
                "meta": {
                    "session_id": "sess_123",
                    "status": "payment_successful",
                    "event_id": "evt_fint_1",
                }
            }
        ).encode()

        event = self.adapter.parse_webhook({}, body)
        assert event.event_type == "payment.confirmed"
        assert event.provider_payment_id == "sess_123"
        assert event.provider_event_id == "evt_fint_1"
        assert event.metadata["payment_method"] == "bank_transfer"

    def test_parse_webhook_payment_unsuccessful(self):
        import json

        body = json.dumps(
            {
                "meta": {
                    "session_id": "sess_fail",
                    "status": "payment_unsuccessful",
                }
            }
        ).encode()

        event = self.adapter.parse_webhook({}, body)
        assert event.event_type == "payment.failed"

    def test_parse_webhook_payment_pending(self):
        import json

        body = json.dumps(
            {
                "meta": {
                    "session_id": "sess_pend",
                    "status": "payment_pending",
                }
            }
        ).encode()

        event = self.adapter.parse_webhook({}, body)
        assert event.event_type == "payment_pending"  # not mapped to normalized


# --- Phase 3: webhook view tests ---


@_skip
@pytest.mark.django_db
class TestGoCardlessWebhookView:
    url = "/api/v1/payments/webhooks/gocardless/"

    @patch("apps.payments.services.handle_webhook")
    def test_webhook_processed(self, mock_handle, api_client, org):
        ProviderConfig.objects.create(
            organization=org,
            provider="gocardless",
            api_key="gc_token",
            webhook_secret="gc_secret",
        )
        mock_handle.return_value = True
        resp = api_client.post(
            self.url,
            data=b'{"events": []}',
            content_type="application/json",
            HTTP_WEBHOOK_SIGNATURE="sig_test",
        )
        assert resp.status_code == 200

    def test_webhook_no_config(self, api_client):
        resp = api_client.post(
            self.url,
            data=b"{}",
            content_type="application/json",
            HTTP_WEBHOOK_SIGNATURE="sig",
        )
        assert resp.status_code == 400


@_skip
@pytest.mark.django_db
class TestFintectureWebhookView:
    url = "/api/v1/payments/webhooks/fintecture/"

    @patch("apps.payments.services.handle_webhook")
    def test_webhook_processed(self, mock_handle, api_client, org):
        ProviderConfig.objects.create(
            organization=org,
            provider="fintecture",
            api_key="fint_app",
            webhook_secret="fint_secret",
        )
        mock_handle.return_value = True
        resp = api_client.post(
            self.url,
            data=b'{"meta": {}}',
            content_type="application/json",
            HTTP_SIGNATURE="sig_test",
        )
        assert resp.status_code == 200

    def test_webhook_no_config(self, api_client):
        resp = api_client.post(
            self.url,
            data=b"{}",
            content_type="application/json",
            HTTP_SIGNATURE="sig",
        )
        assert resp.status_code == 400
