"""Tests for individual tool handlers."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from apps.assistant.tools.customers import (
    create_customer,
    get_customer,
    list_customers,
    update_customer,
)
from apps.assistant.tools.dashboard import get_dashboard_stats
from apps.assistant.tools.invoices import (
    create_draft_invoice,
    download_pdf,
    get_invoice,
    list_invoices,
    transmit_invoice,
    validate_invoice,
)
from apps.assistant.tools.products import (
    create_product,
    get_product,
    list_products,
    update_product,
)
from apps.assistant.tools.suppliers import get_supplier, list_suppliers, update_supplier
from apps.billing.factories import (
    CustomerFactory,
    InvoiceFactory,
    NumberingSequenceFactory,
    ProductFactory,
)
from apps.billing.models import Invoice

# ── Invoice tools ──


@pytest.mark.django_db
class TestListInvoices:
    def test_returns_all(self, org, supplier):
        InvoiceFactory.create_batch(3, organization=org, supplier=supplier)
        result = list_invoices(org=org)
        assert len(result) == 3

    def test_filter_by_status(self, org, supplier):
        InvoiceFactory(organization=org, supplier=supplier, status="draft")
        InvoiceFactory(organization=org, supplier=supplier, status="paid")
        result = list_invoices(org=org, status="draft")
        assert len(result) == 1
        assert result[0]["status"] == "draft"

    def test_filter_by_customer_name(self, org, supplier, customer):
        InvoiceFactory(organization=org, supplier=supplier, customer=customer)
        InvoiceFactory(organization=org, supplier=supplier)
        result = list_invoices(org=org, customer_name=customer.name[:5])
        assert len(result) == 1

    def test_overdue_filter(self, org, supplier):
        today = date.today()
        InvoiceFactory(
            organization=org,
            supplier=supplier,
            status="validated",
            en16931_data={
                "references": {
                    "issueDate": str(today),
                    "dueDate": str(today - timedelta(days=5)),
                },
            },
        )
        InvoiceFactory(
            organization=org,
            supplier=supplier,
            status="validated",
            en16931_data={
                "references": {
                    "issueDate": str(today),
                    "dueDate": str(today + timedelta(days=5)),
                },
            },
        )
        result = list_invoices(org=org, overdue=True)
        assert len(result) == 1

    def test_limit(self, org, supplier):
        InvoiceFactory.create_batch(5, organization=org, supplier=supplier)
        result = list_invoices(org=org, limit=2)
        assert len(result) == 2

    def test_excludes_deleted(self, org, supplier):
        from django.utils import timezone

        InvoiceFactory(organization=org, supplier=supplier, deleted_at=timezone.now())
        result = list_invoices(org=org)
        assert len(result) == 0


@pytest.mark.django_db
class TestGetInvoice:
    def test_by_uuid(self, org, supplier):
        inv = InvoiceFactory(organization=org, supplier=supplier)
        result = get_invoice(org=org, identifier=str(inv.uuid))
        assert result["uuid"] == str(inv.uuid)
        assert "lines" in result

    def test_by_number(self, org, supplier):
        inv = InvoiceFactory(
            organization=org,
            supplier=supplier,
            number="FA-2026-001",
        )
        result = get_invoice(org=org, identifier="FA-2026-001")
        assert result["uuid"] == str(inv.uuid)

    def test_not_found(self, org):
        result = get_invoice(org=org, identifier="nope")
        assert "error" in result


@pytest.mark.django_db
class TestCreateDraftInvoice:
    def test_creates_draft(self, org, supplier, customer, product):
        supplier.is_default = True
        supplier.save()
        lines = [
            {
                "product_uuid": str(product.uuid),
                "quantity": 3,
                "unit_price": "150.00",
            }
        ]
        result = create_draft_invoice(
            org=org,
            customer_uuid=str(customer.uuid),
            lines=lines,
        )
        assert "uuid" in result
        assert result["status"] == "draft"
        inv = Invoice.objects.get(uuid=result["uuid"])
        assert inv.customer == customer


@pytest.mark.django_db
class TestValidateInvoice:
    def test_validates(self, org, supplier):
        NumberingSequenceFactory(supplier=supplier)
        inv = InvoiceFactory(
            organization=org,
            supplier=supplier,
            en16931_data={
                "invoiceLines": [
                    {
                        "itemName": "Test",
                        "quantity": 1,
                        "unitPrice": "100.00",
                        "vatRate": "20.00",
                        "vatCategory": "S",
                        "lineNetAmount": "100.00",
                    }
                ],
            },
        )
        result = validate_invoice(org=org, invoice_uuid=str(inv.uuid))
        assert result["status"] == "processing"

    def test_not_found(self, org):
        result = validate_invoice(
            org=org, invoice_uuid="00000000-0000-0000-0000-000000000000"
        )
        assert "error" in result


@pytest.mark.django_db
class TestTransmitInvoice:
    def test_transmits(self, org, supplier):
        inv = InvoiceFactory(
            organization=org,
            supplier=supplier,
            status="validated",
        )
        result = transmit_invoice(org=org, invoice_uuid=str(inv.uuid))
        assert result["status"] == "transmitting"

    def test_not_found(self, org):
        result = transmit_invoice(
            org=org, invoice_uuid="00000000-0000-0000-0000-000000000000"
        )
        assert "error" in result


@pytest.mark.django_db
class TestDownloadPdf:
    def test_with_pdf_file(self, org, supplier):
        from django.core.files.base import ContentFile

        inv = InvoiceFactory(
            organization=org,
            supplier=supplier,
            status="validated",
            number="FA-2026-099",
        )
        inv.pdf_file.save("test.pdf", ContentFile(b"%PDF-1.4 test"))
        result = download_pdf(org=org, invoice_uuid=str(inv.uuid))
        assert "pdf_url" in result
        assert result["pdf_type"] == "facturx"
        assert result["filename"] == "FA-2026-099.pdf"

    def test_draft_triggers_generation(self, org, supplier):
        inv = InvoiceFactory(organization=org, supplier=supplier, status="draft")
        result = download_pdf(org=org, invoice_uuid=str(inv.uuid))
        assert result["status"] == "generating"

    def test_not_found(self, org):
        result = download_pdf(
            org=org, invoice_uuid="00000000-0000-0000-0000-000000000000"
        )
        assert "error" in result

    def test_no_pdf_non_draft(self, org, supplier):
        inv = InvoiceFactory(organization=org, supplier=supplier, status="validated")
        result = download_pdf(org=org, invoice_uuid=str(inv.uuid))
        assert "error" in result


# ── Customer tools ──


@pytest.mark.django_db
class TestCustomerTools:
    def test_list(self, org):
        CustomerFactory.create_batch(3, organization=org)
        result = list_customers(org=org)
        assert len(result) == 3

    def test_list_search(self, org):
        CustomerFactory(organization=org, name="Dupont SARL")
        CustomerFactory(organization=org, name="Martin SAS")
        result = list_customers(org=org, search="Dupont")
        assert len(result) == 1

    def test_get_by_uuid(self, org, customer):
        result = get_customer(org=org, identifier=str(customer.uuid))
        assert result["name"] == customer.name

    def test_get_by_name(self, org):
        c = CustomerFactory(organization=org, name="Exact Name Corp")
        result = get_customer(org=org, identifier="Exact Name Corp")
        assert result["uuid"] == str(c.uuid)

    def test_create(self, org):
        result = create_customer(org=org, name="New Client", email="new@test.fr")
        assert result["name"] == "New Client"
        assert result["email"] == "new@test.fr"

    def test_update(self, org, customer):
        result = update_customer(
            org=org,
            customer_uuid=str(customer.uuid),
            name="Updated",
        )
        assert result["name"] == "Updated"


# ── Product tools ──


@pytest.mark.django_db
class TestProductTools:
    def test_list(self, org):
        ProductFactory.create_batch(2, organization=org)
        result = list_products(org=org)
        assert len(result) == 2

    def test_get_by_uuid(self, org, product):
        result = get_product(org=org, identifier=str(product.uuid))
        assert result["name"] == product.name

    def test_create(self, org):
        result = create_product(
            org=org,
            name="Consulting",
            unit_price=200,
            vat_rate=20,
        )
        assert result["name"] == "Consulting"
        assert result["unit_price"] == "200"

    def test_update(self, org, product):
        result = update_product(
            org=org,
            product_uuid=str(product.uuid),
            name="Renamed",
        )
        assert result["name"] == "Renamed"


# ── Supplier tools ──


@pytest.mark.django_db
class TestSupplierTools:
    def test_list(self, org, supplier):
        result = list_suppliers(org=org)
        assert len(result) == 1
        assert result[0]["name"] == supplier.name

    def test_get_by_uuid(self, org, supplier):
        result = get_supplier(org=org, identifier=str(supplier.uuid))
        assert result["name"] == supplier.name

    def test_get_by_name(self, org, supplier):
        result = get_supplier(org=org, identifier=supplier.name)
        assert result["uuid"] == str(supplier.uuid)

    def test_update(self, org, supplier):
        result = update_supplier(
            org=org,
            supplier_uuid=str(supplier.uuid),
            email="new@supplier.fr",
        )
        assert result["email"] == "new@supplier.fr"


# ── Dashboard tools ──


@pytest.mark.django_db
class TestDashboardStats:
    def _en16931(self, total, issue_date, due_date=None):
        """Build minimal en16931_data with totals and dates."""
        data = {
            "totals": {"totalGrossAmount": str(total)},
            "references": {"issueDate": str(issue_date)},
        }
        if due_date:
            data["references"]["dueDate"] = str(due_date)
        return data

    def test_returns_stats(self, org, supplier):
        today = date.today()
        InvoiceFactory(
            organization=org,
            supplier=supplier,
            status="paid",
            en16931_data=self._en16931(1200, today),
        )
        InvoiceFactory(
            organization=org,
            supplier=supplier,
            status="validated",
            en16931_data=self._en16931(500, today, today - timedelta(days=10)),
        )
        result = get_dashboard_stats(org=org, period="month")
        assert result["total_invoices"] == 2
        assert result["by_status"]["paid"] == 1
        assert result["by_status"]["validated"] == 1
        assert result["overdue_count"] == 1
        assert Decimal(result["total_revenue"]) == Decimal("1200")
        assert Decimal(result["pending_amount"]) == Decimal("500")
        assert Decimal(result["overdue_amount"]) == Decimal("500")

    def test_period_all(self, org, supplier):
        InvoiceFactory(
            organization=org,
            supplier=supplier,
            status="paid",
            en16931_data=self._en16931(100, date(2020, 1, 1)),
        )
        result = get_dashboard_stats(org=org, period="all")
        assert result["total_invoices"] == 1
