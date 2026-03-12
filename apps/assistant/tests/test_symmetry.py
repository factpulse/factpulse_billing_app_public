"""Symmetry test — ensures UI, REST API, and MCP tools stay in sync.

Each billing action must be available in all three interfaces.
Exceptions (UI-only or API-only actions) are explicitly listed.
"""

import pytest

from apps.assistant.tools import TOOL_REGISTRY

# Canonical list of billing actions with their expected presence.
# Format: (action_name, has_ui, has_api, has_mcp)
INVOICE_ACTIONS = [
    # CRUD
    ("list_invoices", True, True, True),
    ("get_invoice", True, True, True),
    ("create_draft_invoice", True, True, True),
    ("update_draft_invoice", True, True, True),
    ("delete_draft_invoice", True, True, False),  # MCP: intentionally excluded
    # Lifecycle
    ("validate_invoice", True, True, True),
    ("transmit_invoice", True, True, True),
    ("cancel_invoice", True, True, True),
    ("mark_paid", True, True, True),
    # Resources
    ("download_pdf", True, True, True),
    ("audit_log", True, True, False),  # MCP: read-only, low value for LLM
]

CUSTOMER_ACTIONS = [
    ("list_customers", True, True, True),
    ("get_customer", True, True, True),
    ("create_customer", True, True, True),
    ("update_customer", True, True, True),
    ("archive_customer", True, True, True),
]

PRODUCT_ACTIONS = [
    ("list_products", True, True, True),
    ("get_product", True, True, True),
    ("create_product", True, True, True),
    ("update_product", True, True, True),
    ("archive_product", True, True, True),
]

SUPPLIER_ACTIONS = [
    ("list_suppliers", True, True, True),
    ("get_supplier", True, True, True),
    ("create_supplier", True, True, True),
    ("update_supplier", True, True, True),
    ("archive_supplier", True, True, True),
]

OTHER_ACTIONS = [
    ("lookup_sirene", True, True, True),
    ("get_dashboard_stats", True, True, True),
]

ALL_ACTIONS = (
    INVOICE_ACTIONS
    + CUSTOMER_ACTIONS
    + PRODUCT_ACTIONS
    + SUPPLIER_ACTIONS
    + OTHER_ACTIONS
)


class TestMCPSymmetry:
    """Verify that every action expected in MCP is registered."""

    @pytest.mark.parametrize(
        "action_name",
        [name for name, _, _, has_mcp in ALL_ACTIONS if has_mcp],
    )
    def test_mcp_tool_exists(self, action_name):
        assert action_name in TOOL_REGISTRY, (
            f"MCP tool '{action_name}' is missing from TOOL_REGISTRY. "
            f"Add it in apps/assistant/tools/ or update the symmetry table."
        )

    def test_no_unexpected_mcp_tools(self):
        """All registered MCP tools should appear in the symmetry table."""
        known = {name for name, *_ in ALL_ACTIONS}
        unexpected = set(TOOL_REGISTRY.keys()) - known
        assert not unexpected, (
            f"MCP tools not listed in symmetry table: {unexpected}. "
            f"Add them to ALL_ACTIONS in test_symmetry.py."
        )
