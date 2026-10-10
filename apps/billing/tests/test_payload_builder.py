"""Tests for payload_builder.enrich_en16931_data — default VAT exemption handling."""

import pytest

from apps.billing.factories import SupplierFactory
from apps.billing.services.payload_builder import enrich_en16931_data


@pytest.mark.django_db
class TestDefaultVatExemption:
    def test_zero_rate_line_without_supplier_default_is_unchanged(self, org):
        supplier = SupplierFactory(organization=org)
        en16931_data = {
            "invoiceLines": [
                {
                    "itemName": "Item",
                    "quantity": "1",
                    "unitNetPrice": "100",
                    "manualVatRate": "0",
                }
            ]
        }

        enrich_en16931_data(en16931_data, supplier=supplier)

        line = en16931_data["invoiceLines"][0]
        assert line["vatCategory"] == "E"
        assert "exemptionReason" not in line
        assert "vatexCode" not in line

    def test_applies_default_exemption_reason_to_zero_rate_line(self, org):
        supplier = SupplierFactory(
            organization=org,
            default_vat_exemption_reason="TVA non applicable, art. 293 B du CGI",
        )
        en16931_data = {
            "invoiceLines": [
                {
                    "itemName": "Item",
                    "quantity": "1",
                    "unitNetPrice": "100",
                    "lineNetAmount": "100.00",
                    "manualVatRate": "0",
                    "vatCategory": "Z",
                }
            ]
        }

        enrich_en16931_data(en16931_data, supplier=supplier)

        line = en16931_data["invoiceLines"][0]
        assert line["vatCategory"] == "E"
        assert line["exemptionReason"] == "TVA non applicable, art. 293 B du CGI"
        assert "vatexCode" not in line

        vat_line = en16931_data["vatLines"][0]
        assert vat_line["category"] == "E"
        assert vat_line["exemptionReason"] == "TVA non applicable, art. 293 B du CGI"
        # vatexCode key must always be present (even empty string)
        assert vat_line["vatexCode"] == ""

    def test_applies_default_exemption_code(self, org):
        supplier = SupplierFactory(
            organization=org, default_vat_exemption_code="VATEX-FR-FRANCHISE"
        )
        en16931_data = {
            "invoiceLines": [
                {
                    "itemName": "Item",
                    "quantity": "1",
                    "unitNetPrice": "100",
                    "lineNetAmount": "100.00",
                    "manualVatRate": "0",
                }
            ]
        }

        enrich_en16931_data(en16931_data, supplier=supplier)

        line = en16931_data["invoiceLines"][0]
        assert line["vatCategory"] == "E"
        assert line["vatexCode"] == "VATEX-FR-FRANCHISE"

        vat_line = en16931_data["vatLines"][0]
        assert vat_line["vatexCode"] == "VATEX-FR-FRANCHISE"

    def test_does_not_override_existing_exemption_reason(self, org):
        supplier = SupplierFactory(
            organization=org,
            default_vat_exemption_reason="TVA non applicable, art. 293 B du CGI",
        )
        en16931_data = {
            "invoiceLines": [
                {
                    "itemName": "Item",
                    "quantity": "1",
                    "unitNetPrice": "100",
                    "lineNetAmount": "100.00",
                    "manualVatRate": "0",
                    "vatCategory": "AE",
                    "exemptionReason": "Autoliquidation — Art. 283-1 CGI",
                }
            ]
        }

        enrich_en16931_data(en16931_data, supplier=supplier)

        line = en16931_data["invoiceLines"][0]
        assert line["vatCategory"] == "AE"
        assert line["exemptionReason"] == "Autoliquidation — Art. 283-1 CGI"

    def test_nonzero_rate_line_unaffected(self, org):
        supplier = SupplierFactory(
            organization=org,
            default_vat_exemption_reason="TVA non applicable, art. 293 B du CGI",
        )
        en16931_data = {
            "invoiceLines": [
                {
                    "itemName": "Item",
                    "quantity": "1",
                    "unitNetPrice": "100",
                    "lineNetAmount": "100.00",
                    "manualVatRate": "20",
                }
            ]
        }

        enrich_en16931_data(en16931_data, supplier=supplier)

        line = en16931_data["invoiceLines"][0]
        assert line["vatCategory"] == "S"
        assert "exemptionReason" not in line

    def test_builds_vat_lines_when_totals_already_present(self, org):
        """Stripe-imported invoices already carry a `totals` block — vatLines
        (BG-23) must still be derived from invoiceLines so BR-E-10 is satisfiable.
        """
        supplier = SupplierFactory(
            organization=org,
            default_vat_exemption_reason="TVA non applicable, art. 293 B du CGI",
        )
        en16931_data = {
            "invoiceLines": [
                {
                    "itemName": "Item",
                    "quantity": "1",
                    "unitNetPrice": "100",
                    "lineNetAmount": "100.00",
                    "manualVatRate": "0",
                    "vatCategory": "Z",
                }
            ],
            "totals": {
                "totalNetAmount": "100.00",
                "vatAmount": "0.00",
                "totalGrossAmount": "100.00",
                "amountDue": "100.00",
            },
        }

        enrich_en16931_data(en16931_data, supplier=supplier)

        assert en16931_data["totals"]["totalNetAmount"] == "100.00"
        assert "vatLines" in en16931_data
        vat_line = en16931_data["vatLines"][0]
        assert vat_line["category"] == "E"
        assert vat_line["exemptionReason"] == "TVA non applicable, art. 293 B du CGI"
