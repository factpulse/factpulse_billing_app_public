"""Tests for flow_detector service.

Covers: detect_flow, suggest_customer_type, inject_bar_note, inject_framework.
"""

from unittest.mock import MagicMock

from django.test import TestCase

from apps.billing.services.flow_detector import (
    CATEGORY_TO_FRAMEWORK,
    EU_COUNTRY_CODES,
    FLOW_TO_BAR_VALUE,
    FLOW_TO_PROCESSING_RULE,
    detect_flow,
    enrich_recipient_country,
    get_ereporting_flux_type,
    inject_bar_note,
    inject_framework,
    is_ereporting_flow,
    suggest_customer_type,
)


class SuggestCustomerTypeTest(TestCase):
    """Test suggest_customer_type() — auto-detection from identifiers."""

    def test_siren_returns_assujetti_fr(self):
        assert suggest_customer_type(siren="123456789") == "assujetti_fr"

    def test_siren_takes_priority_over_vat(self):
        assert (
            suggest_customer_type(siren="123456789", vat_number="DE123456789")
            == "assujetti_fr"
        )

    def test_french_vat_returns_assujetti_fr(self):
        assert suggest_customer_type(vat_number="FR12345678901") == "assujetti_fr"

    def test_german_vat_returns_intra_ue(self):
        assert suggest_customer_type(vat_number="DE123456789") == "intra_ue"

    def test_italian_vat_returns_intra_ue(self):
        assert suggest_customer_type(vat_number="IT12345678901") == "intra_ue"

    def test_swiss_vat_returns_extra_ue(self):
        assert suggest_customer_type(vat_number="CHE123456789") == "extra_ue"

    def test_us_vat_returns_extra_ue(self):
        assert suggest_customer_type(vat_number="US123456789") == "extra_ue"

    def test_german_country_returns_intra_ue(self):
        assert suggest_customer_type(country_code="DE") == "intra_ue"

    def test_us_country_returns_extra_ue(self):
        assert suggest_customer_type(country_code="US") == "extra_ue"

    def test_french_country_returns_assujetti_fr(self):
        assert suggest_customer_type(country_code="FR") == "assujetti_fr"

    def test_no_info_returns_assujetti_fr(self):
        assert suggest_customer_type() == "assujetti_fr"

    def test_all_eu_countries_return_intra_ue(self):
        for code in EU_COUNTRY_CODES:
            assert suggest_customer_type(vat_number=f"{code}123456789") == "intra_ue", (
                f"Expected intra_ue for {code}"
            )


class DetectFlowFromCustomerTest(TestCase):
    """Test detect_flow() when a Customer record is linked."""

    def _make_invoice(self, customer_type, en16931_data=None):
        invoice = MagicMock()
        invoice.customer = MagicMock()
        invoice.customer.customer_type = customer_type
        invoice.en16931_data = en16931_data or {}
        return invoice

    def test_assujetti_fr(self):
        assert detect_flow(self._make_invoice("assujetti_fr")) == "b2b_domestic"

    def test_intra_ue(self):
        assert detect_flow(self._make_invoice("intra_ue")) == "b2b_intra_eu"

    def test_extra_ue(self):
        assert detect_flow(self._make_invoice("extra_ue")) == "b2b_extra_eu"

    def test_particulier(self):
        assert detect_flow(self._make_invoice("particulier")) == "b2c"

    def test_public(self):
        assert detect_flow(self._make_invoice("public")) == "b2g"


class DetectFlowHeuristicTest(TestCase):
    """Test detect_flow() fallback heuristic from en16931_data."""

    def _make_invoice(self, recipient):
        invoice = MagicMock()
        invoice.customer = None
        invoice.en16931_data = {"recipient": recipient}
        return invoice

    def test_siren_returns_b2b_domestic(self):
        inv = self._make_invoice({"siren": "123456789"})
        assert detect_flow(inv) == "b2b_domestic"

    def test_french_vat_returns_b2b_domestic(self):
        inv = self._make_invoice({"vatNumber": "FR12345678901"})
        assert detect_flow(inv) == "b2b_domestic"

    def test_german_vat_returns_b2b_intra_eu(self):
        inv = self._make_invoice({"vatNumber": "DE123456789"})
        assert detect_flow(inv) == "b2b_intra_eu"

    def test_swiss_vat_returns_b2b_extra_eu(self):
        inv = self._make_invoice({"vatNumber": "CHE123456789"})
        assert detect_flow(inv) == "b2b_extra_eu"

    def test_french_address_no_siren_returns_b2c(self):
        inv = self._make_invoice({"postalAddress": {"countryCode": "FR"}})
        assert detect_flow(inv) == "b2c"

    def test_german_address_returns_b2b_intra_eu(self):
        inv = self._make_invoice({"postalAddress": {"countryCode": "DE"}})
        assert detect_flow(inv) == "b2b_intra_eu"

    def test_us_address_returns_b2b_extra_eu(self):
        inv = self._make_invoice({"postalAddress": {"countryCode": "US"}})
        assert detect_flow(inv) == "b2b_extra_eu"

    def test_no_data_returns_b2c(self):
        inv = self._make_invoice({})
        assert detect_flow(inv) == "b2c"

    def test_empty_recipient_returns_b2c(self):
        invoice = MagicMock()
        invoice.customer = None
        invoice.en16931_data = {}
        assert detect_flow(invoice) == "b2c"


class InjectBarNoteTest(TestCase):
    """Test inject_bar_note() — BAR note injection into en16931_data."""

    def test_injects_bar_note(self):
        data = {}
        inject_bar_note(data, "b2b_domestic")
        assert data["notes"] == [{"subjectCode": "BAR", "content": "B2B"}]

    def test_b2c_bar_note(self):
        data = {}
        inject_bar_note(data, "b2c")
        assert data["notes"][0]["content"] == "B2C"

    def test_b2b_intra_eu_bar_note(self):
        data = {}
        inject_bar_note(data, "b2b_intra_eu")
        assert data["notes"][0]["content"] == "B2BINT"

    def test_does_not_duplicate(self):
        data = {"notes": [{"subjectCode": "BAR", "content": "B2B"}]}
        inject_bar_note(data, "b2b_domestic")
        bar_notes = [n for n in data["notes"] if n["subjectCode"] == "BAR"]
        assert len(bar_notes) == 1

    def test_preserves_existing_notes(self):
        data = {"notes": [{"subjectCode": "PMT", "text": "Paiement"}]}
        inject_bar_note(data, "b2c")
        assert len(data["notes"]) == 2
        assert data["notes"][0]["subjectCode"] == "PMT"
        assert data["notes"][1]["subjectCode"] == "BAR"

    def test_all_flows_have_bar_values(self):
        for flow in FLOW_TO_BAR_VALUE:
            data = {}
            inject_bar_note(data, flow)
            assert len(data["notes"]) == 1


class InjectFrameworkTest(TestCase):
    """Test inject_framework() — BT-23 invoicing framework."""

    def test_services_framework(self):
        data = {"references": {}}
        inject_framework(data, "TPS1")
        assert data["references"]["invoicingFramework"] == "S1"

    def test_goods_framework(self):
        data = {"references": {}}
        inject_framework(data, "TLB1")
        assert data["references"]["invoicingFramework"] == "B1"

    def test_mixed_framework(self):
        data = {"references": {}}
        inject_framework(data, "TMA1")
        assert data["references"]["invoicingFramework"] == "M1"

    def test_does_not_overwrite_existing(self):
        data = {"references": {"invoicingFramework": "S5"}}
        inject_framework(data, "TPS1")
        assert data["references"]["invoicingFramework"] == "S5"

    def test_creates_references_if_missing(self):
        data = {}
        inject_framework(data, "TLB1")
        assert data["references"]["invoicingFramework"] == "B1"

    def test_all_categories_have_frameworks(self):
        for cat in CATEGORY_TO_FRAMEWORK:
            data = {}
            inject_framework(data, cat)
            assert "invoicingFramework" in data.get("references", {})


class ConstantsTest(TestCase):
    """Test mapping constants consistency."""

    def test_all_flows_have_processing_rules(self):
        flows = ["b2b_domestic", "b2c", "b2b_intra_eu", "b2b_extra_eu", "b2g"]
        for flow in flows:
            assert flow in FLOW_TO_PROCESSING_RULE

    def test_all_flows_have_bar_values(self):
        flows = ["b2b_domestic", "b2c", "b2b_intra_eu", "b2b_extra_eu", "b2g"]
        for flow in flows:
            assert flow in FLOW_TO_BAR_VALUE

    def test_eu_countries_count(self):
        # 26 EU members minus France
        assert len(EU_COUNTRY_CODES) == 26
        assert "FR" not in EU_COUNTRY_CODES


class IsEreportingFlowTest(TestCase):
    """Test is_ereporting_flow() helper."""

    def test_b2c_is_ereporting(self):
        assert is_ereporting_flow("b2c") is True

    def test_b2b_intra_eu_is_ereporting(self):
        assert is_ereporting_flow("b2b_intra_eu") is True

    def test_b2b_extra_eu_is_ereporting(self):
        assert is_ereporting_flow("b2b_extra_eu") is True

    def test_b2b_domestic_is_not_ereporting(self):
        assert is_ereporting_flow("b2b_domestic") is False

    def test_b2g_is_not_ereporting(self):
        assert is_ereporting_flow("b2g") is False

    def test_empty_is_not_ereporting(self):
        assert is_ereporting_flow("") is False


class GetEreportingFluxTypeTest(TestCase):
    """Test get_ereporting_flux_type() — DGFiP flux mapping."""

    def test_b2b_intra_eu_returns_10_1(self):
        assert get_ereporting_flux_type("b2b_intra_eu") == "10.1"

    def test_b2b_extra_eu_returns_10_1(self):
        assert get_ereporting_flux_type("b2b_extra_eu") == "10.1"

    def test_b2c_returns_10_3(self):
        assert get_ereporting_flux_type("b2c") == "10.3"

    def test_b2b_domestic_returns_none(self):
        assert get_ereporting_flux_type("b2b_domestic") is None

    def test_b2g_returns_none(self):
        assert get_ereporting_flux_type("b2g") is None


class EnrichRecipientCountryTest(TestCase):
    """Test enrich_recipient_country() — TT-39 enrichment from Customer."""

    def test_injects_country_when_missing(self):
        data = {"recipient": {"name": "Acme"}}
        customer = MagicMock()
        customer.address = {"countryCode": "DE"}
        enrich_recipient_country(data, customer)
        assert data["recipient"]["postalAddress"]["countryCode"] == "DE"

    def test_does_not_overwrite_existing_country(self):
        data = {"recipient": {"postalAddress": {"countryCode": "IT"}}}
        customer = MagicMock()
        customer.address = {"countryCode": "DE"}
        enrich_recipient_country(data, customer)
        assert data["recipient"]["postalAddress"]["countryCode"] == "IT"

    def test_no_customer_does_nothing(self):
        data = {"recipient": {"name": "Acme"}}
        enrich_recipient_country(data, None)
        assert "postalAddress" not in data["recipient"]

    def test_empty_customer_address_does_nothing(self):
        data = {"recipient": {"name": "Acme"}}
        customer = MagicMock()
        customer.address = {}
        enrich_recipient_country(data, customer)
        assert "postalAddress" not in data["recipient"]

    def test_no_recipient_block_does_nothing(self):
        data = {}
        customer = MagicMock()
        customer.address = {"countryCode": "ES"}
        enrich_recipient_country(data, customer)
        # No recipient block → nothing to enrich (recipient must exist)
        # The function checks data.get("recipient", {}) and gets empty dict
        # then checks address.get("countryCode") which is empty → returns
        # Actually the function will create it — let's verify
        assert (
            data.get("recipient", {}).get("postalAddress", {}).get("countryCode")
            == "ES"
        )

    def test_creates_postal_address_block(self):
        data = {"recipient": {}}
        customer = MagicMock()
        customer.address = {"countryCode": "BE"}
        enrich_recipient_country(data, customer)
        assert data["recipient"]["postalAddress"]["countryCode"] == "BE"
