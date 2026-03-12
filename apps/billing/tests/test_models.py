"""Tests for billing models."""

from decimal import Decimal

import pytest
from django.db import IntegrityError

from apps.billing.factories import InvoiceFactory, SupplierFactory
from apps.billing.models import IdempotencyKey, Invoice


@pytest.mark.django_db
class TestInvoiceSyncDenormalizedFields:
    def test_totals_synced(self, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier,
            organization=org,
            en16931_data={
                "totals": {
                    "totalNetAmount": "1000.00",
                    "vatAmount": "200.00",
                    "totalGrossAmount": "1200.00",
                }
            },
        )

        assert invoice.total_excl_tax == Decimal("1000.00")
        assert invoice.total_tax == Decimal("200.00")
        assert invoice.total_incl_tax == Decimal("1200.00")

    def test_dates_synced(self, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier,
            organization=org,
            en16931_data={
                "references": {
                    "issueDate": "2026-01-15",
                    "dueDate": "2026-02-15",
                }
            },
        )

        assert str(invoice.issue_date) == "2026-01-15"
        assert str(invoice.due_date) == "2026-02-15"

    def test_currency_synced(self, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier,
            organization=org,
            en16931_data={"references": {"invoiceCurrency": "USD"}},
        )

        assert invoice.currency_code == "USD"

    def test_invoice_type_code_synced(self, org, supplier):
        invoice = InvoiceFactory(
            supplier=supplier,
            organization=org,
            en16931_data={"references": {"invoiceType": "381"}},
        )

        assert invoice.invoice_type_code == "381"

    def test_empty_data(self, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, organization=org, en16931_data={})

        assert invoice.total_excl_tax is None
        assert invoice.total_tax is None
        assert invoice.total_incl_tax is None


@pytest.mark.django_db
class TestToDecimal:
    def test_none_returns_none(self):
        assert Invoice._to_decimal(None) is None

    def test_valid_string(self):
        assert Invoice._to_decimal("123.45") == Decimal("123.45")

    def test_invalid_string(self):
        assert Invoice._to_decimal("not-a-number") is None

    def test_integer(self):
        assert Invoice._to_decimal(42) == Decimal("42")


@pytest.mark.django_db
class TestUniqueConstraints:
    def test_unique_invoice_number_per_supplier(self, org, supplier):
        InvoiceFactory(supplier=supplier, organization=org, number="INV-001")

        with pytest.raises(IntegrityError):
            InvoiceFactory(supplier=supplier, organization=org, number="INV-001")

    def test_blank_number_allowed_duplicate(self, org, supplier):
        InvoiceFactory(supplier=supplier, organization=org, number="")
        InvoiceFactory(supplier=supplier, organization=org, number="")  # no error

    def test_unique_supplier_external_id_per_org(self, org):
        SupplierFactory(organization=org, external_id="ext_1")

        with pytest.raises(IntegrityError):
            SupplierFactory(organization=org, external_id="ext_1")

    def test_null_external_id_allowed_duplicate(self, org):
        SupplierFactory(organization=org, external_id=None)
        SupplierFactory(organization=org, external_id=None)  # no error


@pytest.mark.django_db
class TestIdempotencyKey:
    def test_unique_together(self, org, supplier):
        invoice = InvoiceFactory(supplier=supplier, organization=org)
        IdempotencyKey.objects.create(
            key="key-1", organization=org, invoice=invoice, response_data={}
        )

        with pytest.raises(IntegrityError):
            IdempotencyKey.objects.create(
                key="key-1", organization=org, invoice=invoice, response_data={}
            )
