"""Tests for the tool registry."""

from apps.assistant.tools import TOOL_REGISTRY
from apps.assistant.tools.registry import ParamType


class TestToolRegistry:
    def test_all_tools_registered(self):
        expected = {
            "list_invoices",
            "get_invoice",
            "create_draft_invoice",
            "update_draft_invoice",
            "validate_invoice",
            "cancel_invoice",
            "mark_paid",
            "list_customers",
            "get_customer",
            "create_customer",
            "update_customer",
            "archive_customer",
            "list_products",
            "get_product",
            "create_product",
            "update_product",
            "archive_product",
            "list_suppliers",
            "get_supplier",
            "create_supplier",
            "update_supplier",
            "archive_supplier",
            "transmit_invoice",
            "download_pdf",
            "get_dashboard_stats",
            "lookup_sirene",
        }
        assert set(TOOL_REGISTRY.keys()) == expected

    def test_write_tools_require_confirmation(self):
        write_tools = [
            "create_draft_invoice",
            "update_draft_invoice",
            "validate_invoice",
            "cancel_invoice",
            "mark_paid",
            "transmit_invoice",
            "create_customer",
            "update_customer",
            "archive_customer",
            "create_product",
            "update_product",
            "archive_product",
            "create_supplier",
            "update_supplier",
            "archive_supplier",
        ]
        for name in write_tools:
            tool = TOOL_REGISTRY[name]
            assert tool.confirm is True, f"{name} should require confirmation"
            assert tool.read_only is False, f"{name} should not be read_only"

    def test_read_tools_no_confirmation(self):
        read_tools = [
            "list_invoices",
            "get_invoice",
            "list_customers",
            "get_customer",
            "list_products",
            "get_product",
            "list_suppliers",
            "get_supplier",
            "download_pdf",
            "get_dashboard_stats",
        ]
        for name in read_tools:
            tool = TOOL_REGISTRY[name]
            assert tool.confirm is False, f"{name} should not require confirmation"
            assert tool.read_only is True, f"{name} should be read_only"

    def test_all_tools_have_handler(self):
        for name, tool in TOOL_REGISTRY.items():
            assert callable(tool.handler), f"{name} handler is not callable"

    def test_all_tools_have_description(self):
        for name, tool in TOOL_REGISTRY.items():
            assert tool.description, f"{name} has no description"

    def test_param_types_are_valid(self):
        for name, tool in TOOL_REGISTRY.items():
            for param in tool.params:
                assert isinstance(param.type, ParamType), (
                    f"{name}.{param.name} has invalid type"
                )
