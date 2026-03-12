"""Tests for the invoice service (create, update, validate, transmit, cancel, etc.)."""

from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.billing.factories import (
    CustomerFactory,
    InvoiceFactory,
    NumberingSequenceFactory,
    SupplierFactory,
)
from apps.billing.models import InvoiceAuditLog
from apps.billing.services import invoice_service
from apps.core.exceptions import ConflictError


@pytest.mark.django_db
class TestCreateInvoice:
    def test_creates_invoice_with_supplier_id(self, org, supplier):
        payload = {
            "supplier_id": str(supplier.uuid),
            "en16931_data": {
                "totals": {
                    "totalNetAmount": "100.00",
                    "vatAmount": "20.00",
                    "totalGrossAmount": "120.00",
                },
            },
        }

        invoice, warnings = invoice_service.create_invoice(org, payload)

        assert invoice.pk is not None
        assert invoice.supplier == supplier
        assert invoice.status == "draft"
        assert invoice.organization == org
        assert invoice.total_incl_tax == Decimal("120.00")

    def test_creates_audit_log(self, org, supplier):
        payload = {"supplier_id": str(supplier.uuid)}
        invoice, _ = invoice_service.create_invoice(org, payload)

        logs = InvoiceAuditLog.objects.filter(invoice=invoice)
        assert logs.count() == 1
        assert logs.first().action == "created"

    def test_returns_warnings_on_data_mismatch(self, org):
        SupplierFactory(organization=org, name="Real", siren="123456789")
        payload = {
            "supplier": {"name": "Different", "siren": "123456789"},
        }

        _, warnings = invoice_service.create_invoice(org, payload)

        assert len(warnings) > 0

    def test_creates_with_customer(self, org, supplier, customer):
        payload = {
            "supplier_id": str(supplier.uuid),
            "customer_id": str(customer.uuid),
        }

        invoice, _ = invoice_service.create_invoice(org, payload)

        assert invoice.customer == customer

    def test_creates_with_external_id(self, org, supplier):
        payload = {
            "supplier_id": str(supplier.uuid),
            "external_id": "ext_inv_1",
        }

        invoice, _ = invoice_service.create_invoice(org, payload)

        assert invoice.external_id == "ext_inv_1"


@pytest.mark.django_db
class TestUpdateInvoice:
    def test_version_increment(self, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, organization=org)
        assert invoice.version == 1

        payload = {"version": 1, "en16931_data": {"notes": "updated"}}
        updated, _ = invoice_service.update_invoice(invoice, payload)

        assert updated.version == 2

    def test_conflict_if_not_draft(self, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier, organization=org, status="validated"
        )

        with pytest.raises(ConflictError, match="draft"):
            invoice_service.update_invoice(invoice, {"version": 1})

    def test_conflict_if_version_mismatch(self, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, organization=org)

        with pytest.raises(ConflictError, match="modified"):
            invoice_service.update_invoice(invoice, {"version": 999})

    def test_version_required(self, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, organization=org)

        with pytest.raises(ConflictError, match="version"):
            invoice_service.update_invoice(invoice, {})

    def test_re_resolve_supplier(self, org):
        sup1 = SupplierFactory(organization=org, name="Sup 1")
        sup2 = SupplierFactory(organization=org, name="Sup 2")
        invoice = InvoiceFactory(supplier=sup1, organization=org)

        payload = {"version": 1, "supplier_id": str(sup2.uuid)}
        updated, _ = invoice_service.update_invoice(invoice, payload)

        assert updated.supplier == sup2

    def test_clears_factpulse_error(self, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier,
            organization=org,
            factpulse_error={"message": "some error"},
        )

        payload = {"version": 1, "en16931_data": {"notes": "fix"}}
        updated, _ = invoice_service.update_invoice(invoice, payload)

        assert updated.factpulse_error is None

    def test_update_en16931_data_partial(self, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier,
            organization=org,
            en16931_data={"supplier": {"name": "A"}, "notes": "old"},
        )

        payload = {"version": 1, "en16931_data": {"notes": "new"}}
        updated, _ = invoice_service.update_invoice(invoice, payload)

        assert updated.en16931_data["notes"] == "new"
        assert updated.en16931_data["supplier"]["name"] == "A"  # unchanged


@pytest.mark.django_db
class TestSoftDelete:
    def test_soft_delete_draft(self, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, organization=org)
        result = invoice_service.soft_delete(invoice)

        assert result.deleted_at is not None

    def test_soft_delete_non_draft_raises(self, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier, organization=org, status="validated"
        )

        with pytest.raises(ConflictError, match="draft"):
            invoice_service.soft_delete(invoice)

    def test_soft_delete_with_number_raises(self, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier, organization=org, number="F-2026-001"
        )

        with pytest.raises(ConflictError, match="number"):
            invoice_service.soft_delete(invoice)

    def test_soft_delete_creates_audit_log(self, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, organization=org)
        invoice_service.soft_delete(invoice)

        log = InvoiceAuditLog.objects.filter(invoice=invoice, action="delete")
        assert log.exists()


@pytest.mark.django_db
class TestValidateInvoice:
    @patch("apps.factpulse.tasks.generate_and_validate_invoice.delay")
    def test_transitions_to_processing(self, mock_delay, org, supplier):
        NumberingSequenceFactory(supplier=supplier)
        invoice = InvoiceFactory(supplier=supplier, organization=org)

        result = invoice_service.validate_invoice(invoice)

        assert result.status == "processing"
        assert result.number  # number should be assigned
        mock_delay.assert_called_once_with(str(invoice.uuid))

    @patch("apps.factpulse.tasks.generate_and_validate_invoice.delay")
    def test_assigns_number(self, mock_delay, org, supplier):
        NumberingSequenceFactory(supplier=supplier, prefix_template="INV-", padding=3)
        invoice = InvoiceFactory(supplier=supplier, organization=org)

        result = invoice_service.validate_invoice(invoice)

        assert result.number == "INV-001"

    @patch("apps.factpulse.tasks.generate_and_validate_invoice.delay")
    def test_sets_issue_date_if_not_set(self, mock_delay, org, supplier):
        NumberingSequenceFactory(supplier=supplier)
        invoice = InvoiceFactory(supplier=supplier, organization=org, issue_date=None)

        result = invoice_service.validate_invoice(invoice)

        assert result.issue_date is not None

    @patch("apps.factpulse.tasks.generate_and_validate_invoice.delay")
    def test_creates_status_change_audit_log(self, mock_delay, org, supplier):
        NumberingSequenceFactory(supplier=supplier)
        invoice = InvoiceFactory(supplier=supplier, organization=org)

        invoice_service.validate_invoice(invoice)

        log = InvoiceAuditLog.objects.filter(
            invoice=invoice, action="status_change", new_status="processing"
        )
        assert log.exists()

    def test_non_draft_raises(self, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, organization=org, status="paid")

        with pytest.raises(ConflictError):
            invoice_service.validate_invoice(invoice)

    @patch("apps.factpulse.tasks.generate_and_validate_invoice.delay")
    def test_detects_flow_b2b_domestic(self, mock_delay, org, supplier):
        from apps.billing.factories import CustomerFactory

        customer = CustomerFactory(
            organization=org, customer_type="assujetti_fr", siren="123456789"
        )
        NumberingSequenceFactory(supplier=supplier)
        invoice = InvoiceFactory(supplier=supplier, organization=org, customer=customer)

        result = invoice_service.validate_invoice(invoice)

        assert result.detected_flow == "b2b_domestic"
        notes = result.en16931_data.get("notes", [])
        bar_notes = [n for n in notes if n.get("subjectCode") == "BAR"]
        assert len(bar_notes) == 1
        assert bar_notes[0]["content"] == "B2B"

    @patch("apps.factpulse.tasks.generate_and_validate_invoice.delay")
    def test_detects_flow_b2c(self, mock_delay, org, supplier):
        from apps.billing.factories import CustomerFactory

        customer = CustomerFactory(organization=org, customer_type="particulier")
        NumberingSequenceFactory(supplier=supplier)
        invoice = InvoiceFactory(supplier=supplier, organization=org, customer=customer)

        result = invoice_service.validate_invoice(invoice)

        assert result.detected_flow == "b2c"
        notes = result.en16931_data.get("notes", [])
        bar_notes = [n for n in notes if n.get("subjectCode") == "BAR"]
        assert bar_notes[0]["content"] == "B2C"

    @patch("apps.factpulse.tasks.generate_and_validate_invoice.delay")
    def test_detects_flow_injects_framework(self, mock_delay, org, supplier):
        NumberingSequenceFactory(supplier=supplier)
        invoice = InvoiceFactory(
            supplier=supplier, organization=org, operation_category="TLB1"
        )

        result = invoice_service.validate_invoice(invoice)

        refs = result.en16931_data.get("references", {})
        assert refs.get("invoicingFramework") == "B1"

    @patch("apps.factpulse.tasks.generate_and_validate_invoice.delay")
    def test_enriches_recipient_country_from_customer(self, mock_delay, org, supplier):
        NumberingSequenceFactory(supplier=supplier)
        customer = CustomerFactory(
            organization=org,
            customer_type="intra_ue",
            address={"countryCode": "DE", "lineOne": "Berlin"},
        )
        invoice = InvoiceFactory(
            supplier=supplier,
            organization=org,
            customer=customer,
            en16931_data={"recipient": {"name": "Acme GmbH"}},
        )

        result = invoice_service.validate_invoice(invoice)

        country = (
            result.en16931_data.get("recipient", {})
            .get("postalAddress", {})
            .get("countryCode")
        )
        assert country == "DE"

    @patch("apps.factpulse.tasks.generate_and_validate_invoice.delay")
    def test_b2c_flow_sets_ereporting_pending(self, mock_delay, org, supplier):
        NumberingSequenceFactory(supplier=supplier)
        customer = CustomerFactory(
            organization=org,
            customer_type="particulier",
        )
        invoice = InvoiceFactory(
            supplier=supplier,
            organization=org,
            customer=customer,
        )

        result = invoice_service.validate_invoice(invoice)

        assert result.detected_flow == "b2c"
        assert result.ereporting_status == "pending"

    @patch("apps.factpulse.tasks.generate_and_validate_invoice.delay")
    def test_b2b_domestic_no_ereporting(self, mock_delay, org, supplier):
        NumberingSequenceFactory(supplier=supplier)
        customer = CustomerFactory(
            organization=org,
            customer_type="assujetti_fr",
        )
        invoice = InvoiceFactory(
            supplier=supplier,
            organization=org,
            customer=customer,
        )

        result = invoice_service.validate_invoice(invoice)

        assert result.detected_flow == "b2b_domestic"
        assert result.ereporting_status == ""


@pytest.mark.django_db
class TestTransmitInvoice:
    @patch("apps.factpulse.tasks.transmit_invoice.delay")
    def test_transitions_to_transmitting(self, mock_delay, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier, organization=org, status="validated"
        )

        result = invoice_service.transmit_invoice(invoice)

        assert result.status == "transmitting"
        mock_delay.assert_called_once_with(str(invoice.uuid))

    def test_non_validated_raises(self, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, organization=org, status="draft")

        with pytest.raises(ConflictError):
            invoice_service.transmit_invoice(invoice)


@pytest.mark.django_db
class TestMarkPaid:
    def test_mark_paid_from_transmitted(self, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier,
            organization=org,
            status="transmitted",
            en16931_data={"totals": {"totalGrossAmount": "500.00"}},
        )

        result = invoice_service.mark_paid(invoice, {"payment_date": "2026-01-15"})

        assert result.status == "paid"
        assert str(result.payment_date) == "2026-01-15"

    def test_mark_paid_sets_amount_from_total(self, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier,
            organization=org,
            status="transmitted",
            en16931_data={"totals": {"totalGrossAmount": "500.00"}},
        )

        result = invoice_service.mark_paid(invoice)

        assert result.payment_amount == Decimal("500.00")

    def test_mark_paid_with_explicit_amount(self, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier,
            organization=org,
            status="accepted",
            en16931_data={"totals": {"totalGrossAmount": "500.00"}},
        )

        result = invoice_service.mark_paid(invoice, {"amount": "300.00"})

        assert result.payment_amount == Decimal("300.00")

    def test_mark_paid_from_draft_raises(self, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, organization=org, status="draft")

        with pytest.raises(ConflictError):
            invoice_service.mark_paid(invoice)


@pytest.mark.django_db
class TestCancelInvoice:
    def test_creates_credit_note(self, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier,
            organization=org,
            status="validated",
            en16931_data={"references": {"invoiceType": "380"}},
        )

        credit_note = invoice_service.cancel_invoice(invoice)

        assert credit_note.invoice_type_code == "381"
        assert credit_note.preceding_invoice == invoice
        assert credit_note.status == "draft"
        assert credit_note.en16931_data["references"]["invoiceType"] == "381"

    def test_credit_note_has_preceding_invoice_reference(self, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier,
            organization=org,
            status="validated",
            number="FA-2026-001",
            issue_date="2026-01-15",
            en16931_data={"references": {"invoiceType": "380"}},
        )

        credit_note = invoice_service.cancel_invoice(invoice)

        refs = credit_note.en16931_data["references"]
        assert refs["precedingInvoiceReference"] == "FA-2026-001"
        assert refs["precedingInvoiceDate"] == "2026-01-15"

    def test_cancel_draft_raises(self, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, organization=org, status="draft")

        with pytest.raises(ConflictError, match="cancel"):
            invoice_service.cancel_invoice(invoice)

    def test_cancel_paid_allowed(self, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, organization=org, status="paid")

        credit_note = invoice_service.cancel_invoice(invoice)
        assert credit_note.preceding_invoice == invoice


@pytest.mark.django_db
class TestCheckAutoCancel:
    def test_auto_cancel_matching_totals(self, org, supplier):
        original = InvoiceFactory(
            supplier=supplier,
            organization=org,
            status="validated",
            en16931_data={"totals": {"totalGrossAmount": "100.00"}},
        )
        credit_note = InvoiceFactory(
            supplier=supplier,
            organization=org,
            status="validated",
            preceding_invoice=original,
            en16931_data={"totals": {"totalGrossAmount": "100.00"}},
        )

        invoice_service.check_auto_cancel(credit_note)

        original.refresh_from_db()
        assert original.status == "cancelled"

    def test_no_auto_cancel_different_totals(self, org, supplier):
        original = InvoiceFactory(
            supplier=supplier,
            organization=org,
            status="validated",
            en16931_data={"totals": {"totalGrossAmount": "100.00"}},
        )
        credit_note = InvoiceFactory(
            supplier=supplier,
            organization=org,
            status="validated",
            preceding_invoice=original,
            en16931_data={"totals": {"totalGrossAmount": "50.00"}},
        )

        invoice_service.check_auto_cancel(credit_note)

        original.refresh_from_db()
        assert original.status == "validated"

    def test_no_auto_cancel_without_preceding(self, org, supplier):
        credit_note = InvoiceFactory(
            supplier=supplier,
            organization=org,
            status="validated",
            en16931_data={"totals": {"totalGrossAmount": "100.00"}},
        )

        # Should not raise
        invoice_service.check_auto_cancel(credit_note)


@pytest.mark.django_db
class TestMarkPaidCDAR:
    @patch("apps.factpulse.tasks.submit_cdar_paid.delay")
    def test_mark_paid_triggers_cdar_task(self, mock_delay, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier,
            organization=org,
            status="transmitted",
            en16931_data={"totals": {"totalGrossAmount": "500.00"}},
        )

        invoice_service.mark_paid(invoice)

        mock_delay.assert_called_once_with(str(invoice.uuid))

    @patch("apps.factpulse.tasks.submit_cdar_paid.delay")
    def test_mark_paid_returns_before_cdar(self, mock_delay, org, supplier):
        """The invoice status is 'paid' before the CDAR task runs."""
        invoice = InvoiceFactory(
            supplier=supplier,
            organization=org,
            status="accepted",
            en16931_data={"totals": {"totalGrossAmount": "200.00"}},
        )

        result = invoice_service.mark_paid(invoice)

        assert result.status == "paid"
        # The task was called (async), but the status was already set
        mock_delay.assert_called_once()
