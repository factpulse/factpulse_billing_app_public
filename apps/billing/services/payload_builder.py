"""Payload enrichment — fills missing EN16931 fields with sensible defaults.

Called by invoice_service.create_invoice() to ensure all required fields
are present regardless of entry point (API, UI form, or MCP).

Only fills fields that are MISSING — never overwrites explicitly provided values.
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal


def enrich_en16931_data(en16931_data, supplier=None):
    """Fill missing fields in en16931_data with defaults.

    Mutates en16931_data in place and returns it.
    """
    _enrich_lines(en16931_data)
    _enrich_totals(en16931_data)
    _enrich_dates(en16931_data, supplier)
    _enrich_references(en16931_data)
    _enrich_notes(en16931_data, supplier)
    return en16931_data


def _enrich_lines(en16931_data):
    """Ensure each invoice line has lineNumber, unit, unitNetPrice, vatCategory."""
    lines = en16931_data.get("invoiceLines", [])
    for i, line in enumerate(lines):
        if "lineNumber" not in line:
            line["lineNumber"] = i + 1
        if "unit" not in line:
            line["unit"] = "PIECE"
        # unitNetPrice = unitPrice if not set
        if "unitNetPrice" not in line and "unitPrice" in line:
            line["unitNetPrice"] = line["unitPrice"]
        # Default VAT rate
        if "manualVatRate" not in line:
            vat_rate = line.get("vatRate", "20")
            line["manualVatRate"] = str(vat_rate)
        # VAT category
        if "vatCategory" not in line:
            rate = Decimal(str(line.get("manualVatRate", "20")))
            line["vatCategory"] = "S" if rate > 0 else "E"
        # lineNetAmount
        if "lineNetAmount" not in line:
            qty = Decimal(str(line.get("quantity", "1")))
            price = Decimal(str(line.get("unitNetPrice", line.get("unitPrice", "0"))))
            line["lineNetAmount"] = str(
                (qty * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            )


def _enrich_totals(en16931_data):
    """Compute totals and vatLines if not provided."""
    if "totals" in en16931_data:
        return

    lines = en16931_data.get("invoiceLines", [])
    total_net = Decimal("0")
    vat_buckets = {}

    for line in lines:
        line_net = Decimal(str(line.get("lineNetAmount", "0")))
        total_net += line_net

        rate_str = str(line.get("manualVatRate", "20"))
        category = line.get("vatCategory", "S")
        key = (rate_str, category)
        bucket = vat_buckets.setdefault(key, {"base": Decimal("0"), "rate": rate_str})
        bucket["base"] += line_net

    total_vat = Decimal("0")
    vat_lines = []
    for (rate_str, category), bucket in vat_buckets.items():
        rate = Decimal(rate_str)
        vat_amount = (bucket["base"] * rate / 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        total_vat += vat_amount
        vat_lines.append(
            {
                "taxableAmount": str(bucket["base"]),
                "manualRate": rate_str,
                "vatAmount": str(vat_amount),
                "category": category,
            }
        )

    total_gross = total_net + total_vat
    en16931_data["totals"] = {
        "totalNetAmount": str(total_net),
        "vatAmount": str(total_vat),
        "totalGrossAmount": str(total_gross),
        "amountDue": str(total_gross),
    }

    if "vatLines" not in en16931_data:
        en16931_data["vatLines"] = vat_lines


def _enrich_dates(en16931_data, supplier):
    """Fill invoiceDate and paymentDueDate if missing."""
    if "invoiceDate" not in en16931_data:
        en16931_data["invoiceDate"] = date.today().isoformat()

    if "paymentDueDate" not in en16931_data:
        issue = date.fromisoformat(en16931_data["invoiceDate"])
        en16931_data["paymentDueDate"] = _compute_due_date(issue, supplier).isoformat()


def _enrich_references(en16931_data):
    """Fill references block with defaults for missing fields."""
    from apps.billing.constants import INVOICE_TYPE_CODE, VAT_ACCOUNTING_CODE

    refs = en16931_data.setdefault("references", {})
    refs.setdefault("invoiceType", INVOICE_TYPE_CODE)
    refs.setdefault("vatAccountingCode", VAT_ACCOUNTING_CODE)
    refs.setdefault("invoiceCurrency", "EUR")
    refs.setdefault("paymentMeans", "VIREMENT")

    # Copy dates into references if missing
    if "issueDate" not in refs and "invoiceDate" in en16931_data:
        refs["issueDate"] = en16931_data["invoiceDate"]
    if "dueDate" not in refs and "paymentDueDate" in en16931_data:
        refs["dueDate"] = en16931_data["paymentDueDate"]


def _enrich_notes(en16931_data, supplier):
    """Add BR-FR-05 mandatory notes if missing."""
    if "notes" in en16931_data:
        return
    if not supplier:
        return

    notes = []
    if supplier.note_pmt and supplier.note_pmt.strip():
        notes.append({"subjectCode": "PMT", "content": supplier.note_pmt.strip()})
    if supplier.note_pmd and supplier.note_pmd.strip():
        notes.append({"subjectCode": "PMD", "content": supplier.note_pmd.strip()})
    if supplier.note_aab and supplier.note_aab.strip():
        notes.append({"subjectCode": "AAB", "content": supplier.note_aab.strip()})
    if notes:
        en16931_data["notes"] = notes


def _compute_due_date(issue_date, supplier):
    """Compute due date from supplier payment terms."""
    days = 30
    end_of_month = False
    if supplier:
        if supplier.payment_terms_days:
            days = supplier.payment_terms_days
        end_of_month = supplier.payment_terms_end_of_month

    due = issue_date + timedelta(days=days)
    if end_of_month:
        if due.month == 12:
            due = due.replace(year=due.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            due = due.replace(month=due.month + 1, day=1) - timedelta(days=1)
    return due
