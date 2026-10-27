"""Stripe Invoice → en16931_data mapper.

Maps a Stripe Invoice object (from webhook) to the en16931_data JSON structure
used by create_invoice().
"""

import logging
from datetime import date, datetime
from decimal import Decimal

logger = logging.getLogger(__name__)


def stripe_invoice_to_payload(stripe_invoice, *, provider_config, product_map=None):
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

    # Dates — use Stripe's issue (finalization) date, never "today"
    issue_date = _resolve_issue_date(stripe_invoice)
    due_date = _ts_to_date(stripe_invoice.get("due_date"))

    # Customer info
    customer_name = stripe_invoice.get("customer_name") or ""
    customer_email = stripe_invoice.get("customer_email") or ""
    customer_address = stripe_invoice.get("customer_address") or {}
    customer_tax_id = _extract_tax_id(stripe_invoice)

    # Build invoice lines from Stripe line items
    invoice_lines = _map_line_items(
        stripe_invoice.get("lines", {}).get("data", []),
        product_map=product_map,
    )

    # Build en16931_data
    en16931_data = {
        "references": {
            "invoiceType": "FACTURE",
            "issueDate": issue_date.isoformat()
            if issue_date
            else date.today().isoformat(),
            "invoiceCurrency": currency,
            "paymentMeans": _resolve_payment_means(stripe_invoice),
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
        # Stripe is the numbering authority: keep its gapless legal number
        # (e.g. "INV-0001"). create_invoice() will not re-number it.
        "number": stripe_invoice.get("number") or "",
        "is_internal": True,
    }

    # Reference supplier from provider config
    if provider_config.default_supplier:
        payload["supplier_id"] = str(provider_config.default_supplier.uuid)

    return payload


def stripe_credit_note_to_payload(stripe_credit_note, *, original_invoice):
    """Convert a Stripe Credit Note dict to a create_invoice payload.

    The credit note (BT-3 = 381) refers to the FactPulse invoice imported from
    the credited Stripe invoice (BT-25/BT-26), and takes its supplier, customer
    and payment means. Amounts stay positive, as for FactPulse credit notes.
    Tax amounts are read from both API formats: ``tax_amounts`` (before
    2025-03-31) and ``taxes``/``total_taxes`` (after).
    """
    from apps.billing.constants import CREDIT_NOTE_TYPE_CODE

    original_data = original_invoice.en16931_data or {}
    original_refs = original_data.get("references", {})
    currency = (stripe_credit_note.get("currency") or "eur").upper()
    issue_date = _ts_to_date(
        stripe_credit_note.get("effective_at") or stripe_credit_note.get("created")
    )

    total = _cents_to_decimal(stripe_credit_note.get("total", 0))
    tax = _cents_to_decimal(
        _sum_tax_amounts(
            stripe_credit_note.get("total_taxes")
            or stripe_credit_note.get("tax_amounts")
        )
    )

    references = {
        "invoiceType": CREDIT_NOTE_TYPE_CODE,
        "issueDate": (issue_date or date.today()).isoformat(),
        "invoiceCurrency": currency,
        "paymentMeans": original_refs.get("paymentMeans", "VIREMENT"),
    }
    # BT-25 / BT-26 — required by BR-FR-CO-05 for credit notes
    if original_invoice.number:
        references["precedingInvoiceReference"] = original_invoice.number
    if original_invoice.issue_date:
        references["precedingInvoiceDate"] = str(original_invoice.issue_date)

    en16931_data = {
        "references": references,
        "invoiceLines": _map_credit_note_lines(
            (stripe_credit_note.get("lines") or {}).get("data", []),
            original_lines=original_data.get("invoiceLines", []),
        ),
        "totals": {
            "totalNetAmount": str(total - tax),
            "vatAmount": str(tax),
            "totalGrossAmount": str(total),
            "amountDue": str(total),
        },
    }

    payload = {
        "en16931_data": en16931_data,
        "external_id": stripe_credit_note.get("id", ""),
        # Stripe numbers credit notes itself (e.g. "INV-0001-CN-01")
        "number": stripe_credit_note.get("number") or "",
        "is_internal": True,
    }
    if original_invoice.supplier_id:
        payload["supplier_id"] = str(original_invoice.supplier.uuid)
    if original_invoice.customer_id:
        payload["customer_id"] = str(original_invoice.customer.uuid)
    elif original_data.get("recipient"):
        en16931_data["recipient"] = original_data["recipient"]
    return payload


def _map_credit_note_lines(stripe_lines, original_lines=()):
    """Convert Stripe credit note lines to en16931 invoiceLines.

    The VAT rate is derived from the line tax amounts. When the credited
    invoice has a line at the same rate, its VAT category and exemption are
    reused so the credit note mirrors the invoice.
    """
    lines = []
    for i, item in enumerate(stripe_lines, start=1):
        quantity = item.get("quantity") or 1
        taxes = item.get("taxes") or item.get("tax_amounts") or []
        amount = _cents_to_decimal(
            (item.get("amount") or 0)
            - _sum_discounts(item)
            - _sum_tax_amounts(taxes, inclusive_only=True)
        )
        vat_rate = _credit_note_line_rate(item, taxes)

        line = {
            "lineNumber": i,
            "itemName": item.get("description") or f"Stripe item #{i}",
            "quantity": str(quantity),
            "unitNetPrice": str(amount / Decimal(str(quantity))),
            "lineNetAmount": str(amount),
            "manualVatRate": vat_rate,
            "vatCategory": "S" if Decimal(vat_rate) > 0 else "Z",
        }
        for original in original_lines:
            if Decimal(str(original.get("manualVatRate") or 0)) == Decimal(vat_rate):
                for key in ("vatCategory", "exemptionReason", "vatexCode"):
                    if original.get(key):
                        line[key] = original[key]
                break
        lines.append(line)
    return lines


def _credit_note_line_rate(item, taxes):
    """VAT rate of a credit note line, as a "20.00" string."""
    for tax_rate in item.get("tax_rates") or []:
        if isinstance(tax_rate, dict) and tax_rate.get("percentage") is not None:
            return str(Decimal(str(tax_rate["percentage"])).quantize(Decimal("0.01")))
    tax = _sum_tax_amounts(taxes)
    taxable = sum((t.get("taxable_amount") or 0) for t in taxes)
    if not tax or not taxable:
        return "0.00"
    return str((Decimal(tax) * 100 / Decimal(taxable)).quantize(Decimal("0.01")))


def _sum_tax_amounts(taxes, inclusive_only=False):
    """Sum Stripe tax amounts (cents), old ``inclusive`` or new ``tax_behavior``."""
    total = 0
    for tax in taxes or []:
        inclusive = tax.get("inclusive") or tax.get("tax_behavior") == "inclusive"
        if inclusive_only and not inclusive:
            continue
        total += tax.get("amount") or 0
    return total


def _sum_discounts(item):
    return sum((d.get("amount") or 0) for d in item.get("discount_amounts") or [])


def _map_line_items(stripe_lines, product_map=None):
    """Convert Stripe line items to en16931 invoiceLines.

    product_map: optional dict of stripe_product_id → FactPulse Product instance.
    When provided, FactPulse product data (VAT rate, unit) takes precedence over
    Stripe's inline tax data.
    """
    lines = []
    for i, item in enumerate(stripe_lines, start=1):
        description = item.get("description") or ""
        quantity = item.get("quantity") or 1
        amount = _cents_to_decimal(item.get("amount", 0))

        unit_price = amount / Decimal(str(quantity)) if quantity else amount

        # Resolve FactPulse product if available
        price = item.get("price") or {}
        stripe_product_id = price.get("product") if isinstance(price, dict) else None
        product = (
            (product_map or {}).get(stripe_product_id) if stripe_product_id else None
        )

        # VAT rate: prefer FactPulse product, fall back to Stripe tax data
        if product and product.default_vat_rate is not None:
            vat_rate = str(product.default_vat_rate)
        else:
            tax_amounts = item.get("tax_amounts") or []
            vat_rate = "0.00"
            if tax_amounts:
                rate = tax_amounts[0].get("tax_rate", {})
                if isinstance(rate, dict):
                    vat_rate = str(Decimal(str(rate.get("percentage", 0))))
                elif isinstance(rate, str):
                    vat_rate = "20.00"  # tax_rate ID, percentage not available inline

        line = {
            "lineNumber": i,
            "itemName": description
            or (product.name if product else f"Stripe item #{i}"),
            "quantity": str(quantity),
            "unitNetPrice": str(unit_price),
            "lineNetAmount": str(amount),
            "manualVatRate": vat_rate,
            "vatCategory": "S" if Decimal(vat_rate) > 0 else "Z",
        }

        if product:
            if product.default_unit:
                line["quantityUnit"] = product.default_unit
            if product.reference:
                line["itemReference"] = product.reference

        lines.append(line)
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


def _resolve_issue_date(stripe_invoice):
    """Stripe issue date = finalization date, with sensible fallbacks.

    A finalized Stripe invoice carries its legal issue date in
    ``status_transitions.finalized_at``. We fall back to ``effective_at`` and
    finally ``created`` so a date is always available, but we never substitute
    "today" — the issued date must match what the customer received.
    """
    transitions = stripe_invoice.get("status_transitions") or {}
    ts = (
        transitions.get("finalized_at")
        or stripe_invoice.get("effective_at")
        or stripe_invoice.get("created")
    )
    return _ts_to_date(ts)


def _resolve_payment_means(stripe_invoice):
    """Map how the Stripe invoice is collected to a FactPulse paymentMeans code.

    ``payment_settings.payment_method_types`` is null when the account defaults
    apply; an automatically charged invoice is then paid with the customer's
    saved card ("CARTE" = UNTDID 4461 code 48, no IBAN required).
    """
    payment_settings = stripe_invoice.get("payment_settings") or {}
    types = payment_settings.get("payment_method_types") or []
    if "card" in types:
        return "CARTE"
    if "sepa_debit" in types:
        return "PRELEVEMENT"
    if types:
        return "VIREMENT"
    if stripe_invoice.get("collection_method") == "charge_automatically":
        return "CARTE"
    return "VIREMENT"


def _extract_tax_id(stripe_invoice):
    """Extract customer tax ID (VAT number) from Stripe invoice."""
    # customer_tax_ids is a list of {type, value}
    tax_ids = stripe_invoice.get("customer_tax_ids") or []
    for tid in tax_ids:
        if isinstance(tid, dict) and tid.get("value"):
            return tid["value"]
    return ""
