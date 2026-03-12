"""URL helper — generates UI links from view names, no new views needed."""

import os

from django.urls import reverse

_SITE_URL = os.environ.get("SITE_URL", "http://localhost:8000").rstrip("/")


def entity_url(view_name: str, uuid=None) -> str:
    path = reverse(view_name, kwargs={"uuid": uuid}) if uuid else reverse(view_name)
    return f"{_SITE_URL}{path}"


# Mapping: tool_name -> (view_name, uuid_field_in_result)
TOOL_URL_MAP = {
    "list_invoices": ("ui:invoice_detail", "uuid"),
    "get_invoice": ("ui:invoice_detail", "uuid"),
    "create_draft_invoice": ("ui:invoice_detail", "uuid"),
    "validate_invoice": ("ui:invoice_detail", "uuid"),
    "cancel_invoice": ("ui:invoice_detail", "uuid"),
    "mark_paid": ("ui:invoice_detail", "uuid"),
    "list_customers": ("ui:customer_edit", "uuid"),
    "get_customer": ("ui:customer_edit", "uuid"),
    "create_customer": ("ui:customer_edit", "uuid"),
    "list_products": ("ui:product_edit", "uuid"),
    "get_product": ("ui:product_edit", "uuid"),
    "create_product": ("ui:product_edit", "uuid"),
    "list_suppliers": ("ui:supplier_edit", "uuid"),
    "get_supplier": ("ui:supplier_edit", "uuid"),
    "update_draft_invoice": ("ui:invoice_detail", "uuid"),
    "archive_customer": ("ui:customer_edit", "uuid"),
    "create_supplier": ("ui:supplier_edit", "uuid"),
    "archive_supplier": ("ui:supplier_edit", "uuid"),
    "archive_product": ("ui:product_edit", "uuid"),
    "transmit_invoice": ("ui:invoice_detail", "uuid"),
    "download_pdf": ("ui:invoice_detail", "uuid"),
}


def enrich_result(tool_name: str, result):
    """Add UI URLs to tool results (single dict or list of dicts)."""
    if tool_name not in TOOL_URL_MAP:
        return result
    view_name, uuid_key = TOOL_URL_MAP[tool_name]
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict) and uuid_key in item:
                item["url"] = entity_url(view_name, item[uuid_key])
    elif isinstance(result, dict) and uuid_key in result:
        result["url"] = entity_url(view_name, result[uuid_key])
    return result
