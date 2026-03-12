"""Tests for the URL enrichment helper."""

import pytest

from apps.assistant.tools.urls import enrich_result, entity_url


@pytest.mark.django_db
class TestEntityUrl:
    def test_with_uuid(self):
        url = entity_url("ui:invoice_detail", "550e8400-e29b-41d4-a716-446655440000")
        assert "/invoices/550e8400-e29b-41d4-a716-446655440000/" in url

    def test_without_uuid(self):
        url = entity_url("ui:invoice_list")
        assert "/invoices/" in url


@pytest.mark.django_db
class TestEnrichResult:
    def test_single_dict(self):
        result = {"uuid": "550e8400-e29b-41d4-a716-446655440000", "number": "FA-001"}
        enriched = enrich_result("get_invoice", result)
        assert "url" in enriched
        assert "/invoices/" in enriched["url"]

    def test_list_of_dicts(self):
        results = [
            {"uuid": "550e8400-e29b-41d4-a716-446655440000"},
            {"uuid": "660e8400-e29b-41d4-a716-446655440001"},
        ]
        enriched = enrich_result("list_invoices", results)
        for item in enriched:
            assert "url" in item

    def test_unknown_tool(self):
        result = {"uuid": "abc"}
        enriched = enrich_result("unknown_tool", result)
        assert "url" not in enriched

    def test_no_uuid_in_result(self):
        result = {"name": "test"}
        enriched = enrich_result("get_invoice", result)
        assert "url" not in enriched
