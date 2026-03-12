"""Tests for the numbering service."""

from datetime import date
from unittest.mock import patch

import pytest

from apps.billing.factories import (
    InvoiceFactory,
    NumberingSequenceFactory,
    SupplierFactory,
)
from apps.billing.models import NumberingCounter, NumberingSequence
from apps.billing.services.numbering_service import assign_number


@pytest.mark.django_db
class TestAssignNumber:
    def test_basic_number_format(self):
        supplier = SupplierFactory()
        NumberingSequenceFactory(
            supplier=supplier, prefix_template="FACT-2026-", padding=3
        )
        invoice = InvoiceFactory(supplier=supplier, issue_date=date(2026, 1, 15))

        number = assign_number(invoice)

        assert number == "FACT-2026-001"

    def test_auto_creates_sequence_when_absent(self):
        supplier = SupplierFactory()
        invoice = InvoiceFactory(supplier=supplier, issue_date=date(2026, 3, 1))

        assert not NumberingSequence.objects.filter(supplier=supplier).exists()
        number = assign_number(invoice)

        assert NumberingSequence.objects.filter(supplier=supplier).exists()
        assert number  # Should produce a valid number

    def test_sequential_counter(self):
        supplier = SupplierFactory()
        NumberingSequenceFactory(supplier=supplier, prefix_template="INV-", padding=3)
        inv1 = InvoiceFactory(supplier=supplier, issue_date=date(2026, 1, 1))
        inv2 = InvoiceFactory(supplier=supplier, issue_date=date(2026, 1, 2))

        num1 = assign_number(inv1)
        num2 = assign_number(inv2)

        assert num1 == "INV-001"
        assert num2 == "INV-002"

    def test_different_prefix_per_year(self):
        supplier = SupplierFactory()
        NumberingSequenceFactory(
            supplier=supplier,
            prefix_template="FACT-{{ issue_date|date:'Y' }}-",
            padding=3,
        )
        inv_2025 = InvoiceFactory(supplier=supplier, issue_date=date(2025, 12, 1))
        inv_2026 = InvoiceFactory(supplier=supplier, issue_date=date(2026, 1, 1))

        num_2025 = assign_number(inv_2025)
        num_2026 = assign_number(inv_2026)

        assert num_2025 == "FACT-2025-001"
        assert num_2026 == "FACT-2026-001"
        # Each year prefix has its own counter
        assert NumberingCounter.objects.count() == 2

    def test_custom_padding(self):
        supplier = SupplierFactory()
        NumberingSequenceFactory(supplier=supplier, prefix_template="F-", padding=5)
        invoice = InvoiceFactory(supplier=supplier, issue_date=date(2026, 1, 1))

        number = assign_number(invoice)

        assert number == "F-00001"

    def test_uses_today_when_no_issue_date(self):
        supplier = SupplierFactory()
        NumberingSequenceFactory(
            supplier=supplier,
            prefix_template="FACT-{{ issue_date|date:'Y' }}-",
            padding=3,
        )
        invoice = InvoiceFactory(supplier=supplier, issue_date=None)

        with patch("apps.billing.services.numbering_service.date") as mock_date:
            mock_date.today.return_value = date(2026, 6, 15)
            mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
            number = assign_number(invoice)

        assert "2026" in number

    def test_counter_persists_across_calls(self):
        supplier = SupplierFactory()
        NumberingSequenceFactory(supplier=supplier, prefix_template="X-", padding=2)
        for _ in range(5):
            invoice = InvoiceFactory(supplier=supplier, issue_date=date(2026, 1, 1))
            num = assign_number(invoice)

        assert num == "X-05"
        counter = NumberingCounter.objects.get(resolved_prefix="X-")
        assert counter.last_number == 5
