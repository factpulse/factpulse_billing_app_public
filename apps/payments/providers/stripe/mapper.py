"""Stripe Invoice → en16931_data mapper.

Maps a Stripe Invoice object (from webhook) to the en16931_data JSON structure
used by create_invoice().
"""

import logging
from datetime import date, datetime
from decimal import Decimal

logger = logging.getLogger(__name__)


def stripe_invoice_to_payload(stripe_invoice, *, provider_config):
    """Convert a Stripe Invoice dict to a create_invoice payload.

    Args:
        stripe_invoice: dict from Stripe webhook data.object (invoice)
        provider_config: ProviderConfig with default_supplier

    Returns:
        dict: payload suitable for invoice_service.create_invoice()
    """
    # Amounts: Stripe uses cents
    currency = (stripe_invoice.get("currency") or "eur").upper()
    total = _cents_to_decimal(stripe_invoice.get("total", 0))
    subtotal = _cents_to_decimal(stripe_invoice.get("subtotal", 0))
    tax = _cents_to_decimal(stripe_invoice.get("tax") or 0)

    # Dates
    issue_date = _ts_to_date(stripe_invoice.get("created"))
    due_date = _ts_to_date(stripe_invoice.get("due_date"))

    # Customer info
    customer_name = stripe_invoice.get("customer_name") or ""
    customer_email = stripe_invoice.get("customer_email") or ""
    customer_address = stripe_invoice.get("customer_address") or {}
    customer_tax_id = _extract_tax_id(stripe_invoice)

    # Build invoice lines from Stripe line items
    invoice_lines = _map_line_items(stripe_invoice.get("lines", {}).get("data", []))

    # Build en16931_data
    en16931_data = {
        "references": {
            "invoiceType": "FACTURE",
            "issueDate": issue_date.isoformat()
            if issue_date
            else date.today().isoformat(),
            "invoiceCurrency": currency,
        },
        "recipient": {
            "name": customer_name,
            "email": customer_email,
        },
        "invoiceLines": invoice_lines,
        "totals": {
            "totalNetAmount": str(subtotal),
            "vatAmount": str(tax),
            "totalGrossAmount": str(total),
            "amountDue": str(total),
        },
    }

    if due_date:
        en16931_data["references"]["dueDate"] = due_date.isoformat()

    # Recipient address
    if customer_address:
        en16931_data["recipient"]["postalAddress"] = {
            "lineOne": customer_address.get("line1") or "",
            "postalCode": customer_address.get("postal_code") or "",
            "city": customer_address.get("city") or "",
            "countryCode": customer_address.get("country") or "",
        }

    if customer_tax_id:
        en16931_data["recipient"]["vatNumber"] = customer_tax_id

    # Build payload
    payload = {
        "en16931_data": en16931_data,
        "external_id": stripe_invoice.get("id", ""),
        "is_internal": True,
    }

    # Reference supplier from provider config
    if provider_config.default_supplier:
        payload["supplier_id"] = str(provider_config.default_supplier.uuid)

    return payload


def _map_line_items(stripe_lines):
    """Convert Stripe line items to en16931 invoiceLines."""
    lines = []
    for i, item in enumerate(stripe_lines, start=1):
        description = item.get("description") or ""
        quantity = item.get("quantity") or 1
        amount = _cents_to_decimal(item.get("amount", 0))

        # Stripe doesn't always provide unit price separately
        unit_price = amount / Decimal(str(quantity)) if quantity else amount

        # Tax info
        tax_amounts = item.get("tax_amounts") or []
        vat_rate = "0.00"
        if tax_amounts:
            rate = tax_amounts[0].get("tax_rate", {})
            if isinstance(rate, dict):
                vat_rate = str(Decimal(str(rate.get("percentage", 0))))
            elif isinstance(rate, str):
                # rate is a tax_rate ID, percentage not available inline
                vat_rate = "20.00"  # default fallback

        lines.append(
            {
                "lineNumber": i,
                "itemName": description or f"Stripe subscription item #{i}",
                "quantity": str(quantity),
                "unitNetPrice": str(unit_price),
                "lineNetAmount": str(amount),
                "manualVatRate": vat_rate,
                "vatCategory": "S" if Decimal(vat_rate) > 0 else "Z",
            }
        )
    return lines


def _cents_to_decimal(cents):
    """Convert Stripe amount (cents) to Decimal."""
    if cents is None:
        return Decimal("0.00")
    return (Decimal(str(cents)) / 100).quantize(Decimal("0.01"))


def _ts_to_date(timestamp):
    """Convert Unix timestamp to date, or None."""
    if not timestamp:
        return None
    return datetime.fromtimestamp(int(timestamp)).date()


def _extract_tax_id(stripe_invoice):
    """Extract customer tax ID (VAT number) from Stripe invoice."""
    # customer_tax_ids is a list of {type, value}
    tax_ids = stripe_invoice.get("customer_tax_ids") or []
    for tid in tax_ids:
        if isinstance(tid, dict) and tid.get("value"):
            return tid["value"]
    return ""
