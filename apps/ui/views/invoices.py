"""Invoice views — list, create, edit, detail, actions."""

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.billing.constants import INVOICE_TYPE_CODE, VAT_ACCOUNTING_CODE
from apps.billing.models import Invoice
from apps.billing.services import invoice_service
from apps.core.exceptions import ConflictError

logger = logging.getLogger(__name__)

S = Invoice.Status


@login_required
def invoice_list(request):
    org = request.organization
    if not org:
        return redirect("ui:dashboard")

    invoices = (
        Invoice.objects.filter(organization=org, deleted_at__isnull=True)
        .select_related("supplier", "customer")
        .order_by("-created_at")
    )

    # Filters
    status_filter = request.GET.get("status")
    if status_filter == "pending":
        invoices = invoices.filter(status__in=[S.VALIDATED, S.TRANSMITTED, S.ACCEPTED])
    elif status_filter:
        invoices = invoices.filter(status=status_filter)

    search = request.GET.get("search")
    if search:
        invoices = invoices.filter(
            Q(number__icontains=search) | Q(customer__name__icontains=search)
        )

    paginator = Paginator(invoices, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    if request.headers.get("HX-Request"):
        return render(
            request,
            "ui/partials/invoice_table.html",
            {"invoices": page_obj, "page_obj": page_obj},
        )

    return render(
        request,
        "ui/invoice_list.html",
        {
            "invoices": page_obj,
            "page_obj": page_obj,
            "status_choices": Invoice.Status.choices,
            "current_status": status_filter or "",
        },
    )


@login_required
def invoice_create(request):
    org = request.organization
    if not org:
        return redirect("ui:dashboard")

    from apps.billing.models import Customer, Product, Supplier

    suppliers = Supplier.objects.filter(organization=org, archived=False)
    customers = Customer.objects.filter(organization=org, archived=False)
    products = Product.objects.filter(organization=org, archived=False)

    if request.method == "POST":
        payload = _build_invoice_payload(request.POST)
        try:
            invoice, warnings = invoice_service.create_invoice(
                organization=org,
                payload=payload,
                user=request.user,
            )
            return redirect("ui:invoice_detail", uuid=invoice.uuid)
        except ValueError as e:
            return render(
                request,
                "ui/invoice_form.html",
                {
                    "suppliers": suppliers,
                    "customers": customers,
                    "products": products,
                    "error": str(e),
                    "form_data": request.POST,
                },
            )

    return render(
        request,
        "ui/invoice_form.html",
        {
            "suppliers": suppliers,
            "customers": customers,
            "products": products,
        },
    )


@login_required
def invoice_edit(request, uuid):
    org = request.organization
    invoice = get_object_or_404(
        Invoice, uuid=uuid, organization=org, deleted_at__isnull=True
    )

    if invoice.status != S.DRAFT:
        return redirect("ui:invoice_detail", uuid=uuid)

    from apps.billing.models import Customer, Product, Supplier

    suppliers = Supplier.objects.filter(organization=org, archived=False)
    customers = Customer.objects.filter(organization=org, archived=False)
    products = Product.objects.filter(organization=org, archived=False)

    if request.method == "POST":
        payload = _build_invoice_payload(request.POST)
        payload["version"] = invoice.version
        try:
            invoice, warnings = invoice_service.update_invoice(
                invoice=invoice,
                payload=payload,
                user=request.user,
            )
            return redirect("ui:invoice_detail", uuid=invoice.uuid)
        except (ValueError, ConflictError) as e:
            return render(
                request,
                "ui/invoice_form.html",
                {
                    "invoice": invoice,
                    "suppliers": suppliers,
                    "customers": customers,
                    "products": products,
                    "error": str(e),
                },
            )

    return render(
        request,
        "ui/invoice_form.html",
        {
            "invoice": invoice,
            "suppliers": suppliers,
            "customers": customers,
            "products": products,
        },
    )


@login_required
def invoice_detail(request, uuid):
    org = request.organization
    invoice = get_object_or_404(
        Invoice, uuid=uuid, organization=org, deleted_at__isnull=True
    )
    audit_logs = invoice.audit_logs.all()[:20]

    return render(
        request,
        "ui/invoice_detail.html",
        {
            "invoice": invoice,
            "audit_logs": audit_logs,
            "data": invoice.en16931_data,
        },
    )


def _invoice_action(request, uuid, action_fn, error_msg, redirect_to_result=False):
    """Shared handler for POST-only invoice actions (validate, transmit, mark_paid, cancel)."""
    org = request.organization
    invoice = get_object_or_404(Invoice, uuid=uuid, organization=org)

    if request.method == "POST":
        try:
            result = action_fn(invoice, user=request.user)
            if redirect_to_result and result:
                return redirect("ui:invoice_detail", uuid=result.uuid)
        except (ConflictError, ValueError):
            logger.exception("%s %s", error_msg, uuid)
            messages.error(request, error_msg)
    return redirect("ui:invoice_detail", uuid=uuid)


@login_required
def invoice_validate(request, uuid):
    return _invoice_action(
        request,
        uuid,
        invoice_service.validate_invoice,
        "Impossible de valider la facture.",
    )


@login_required
def invoice_transmit(request, uuid):
    return _invoice_action(
        request,
        uuid,
        invoice_service.transmit_invoice,
        "Impossible de transmettre la facture.",
    )


@login_required
def invoice_mark_paid(request, uuid):
    return _invoice_action(
        request,
        uuid,
        invoice_service.mark_paid,
        "Impossible de marquer la facture comme payée.",
    )


@login_required
def invoice_cancel(request, uuid):
    return _invoice_action(
        request,
        uuid,
        invoice_service.cancel_invoice,
        "Impossible d'annuler la facture.",
        redirect_to_result=True,
    )


@login_required
def invoice_delete(request, uuid):
    org = request.organization
    invoice = get_object_or_404(Invoice, uuid=uuid, organization=org)

    if request.method == "POST":
        try:
            invoice_service.soft_delete(invoice, user=request.user)
        except (ConflictError, ValueError):
            return redirect("ui:invoice_detail", uuid=uuid)
    return redirect("ui:invoice_list")


# --- Invoice form helpers ---


def _parse_invoice_lines(post_data):
    """Parse invoice lines from form POST data."""
    lines = []
    i = 0
    while f"line_{i}_item_name" in post_data:
        line = {
            "lineNumber": i + 1,
            "itemName": post_data.get(f"line_{i}_item_name", ""),
            "quantity": post_data.get(f"line_{i}_quantity", "1"),
            "unitNetPrice": post_data.get(f"line_{i}_unit_price", "1"),
            "manualVatRate": post_data.get(f"line_{i}_vat_rate", "20.00"),
            "vatCategory": post_data.get(f"line_{i}_vat_category", "S"),
            "unit": "PIECE",
        }
        exemption_reason = post_data.get(f"line_{i}_exemption_reason", "")
        if exemption_reason:
            line["exemptionReason"] = exemption_reason
        line_net = post_data.get(f"line_{i}_net_amount")
        if line_net:
            line["lineNetAmount"] = line_net
        product_id = post_data.get(f"line_{i}_product_id")
        if product_id:
            line["product_id"] = product_id
        lines.append(line)
        i += 1
    return lines


def _merge_exemption_reasons(lines, vat_lines):
    """Merge exemptionReason from invoice lines into VAT lines.

    Server-side fix: JS vatLines may miss exemptionReason due to Alpine timing.
    """
    if not vat_lines or not lines:
        return
    reason_by_cat = {}
    for line in lines:
        cat = line.get("vatCategory", "S")
        reason = line.get("exemptionReason", "")
        if reason and cat not in reason_by_cat:
            reason_by_cat[cat] = reason
    for vl in vat_lines:
        if not vl.get("exemptionReason"):
            cat = vl.get("category", "")
            if cat in reason_by_cat:
                vl["exemptionReason"] = reason_by_cat[cat]


def _build_references(post_data):
    """Build EN16931 references block from POST data."""
    references = {}
    if post_data.get("issue_date"):
        references["issueDate"] = post_data["issue_date"]
    if post_data.get("due_date"):
        references["dueDate"] = post_data["due_date"]
    if post_data.get("payment_means"):
        references["paymentMeans"] = post_data["payment_means"]
    if post_data.get("note_pmd"):
        references["paymentTerms"] = post_data["note_pmd"]
    references["invoiceType"] = post_data.get("invoice_type_code", INVOICE_TYPE_CODE)
    references["vatAccountingCode"] = VAT_ACCOUNTING_CODE
    references["invoiceCurrency"] = "EUR"
    return references


def _build_notes(post_data):
    """Build EN16931 notes list from POST data (BR-FR-05)."""
    notes = []
    for code, field in (("PMT", "note_pmt"), ("PMD", "note_pmd"), ("AAB", "note_aab")):
        if post_data.get(field):
            notes.append({"subjectCode": code, "content": post_data[field]})
    if post_data.get("notes_extra"):
        notes.append({"content": post_data["notes_extra"]})
    return notes


def _build_invoice_payload(post_data):
    """Build an invoice service payload from form POST data."""
    lines = _parse_invoice_lines(post_data)

    en16931_data = {"invoiceLines": lines}

    # Totals (pre-computed by JS)
    total_net = post_data.get("total_net_amount") or "0.00"
    total_vat = post_data.get("total_vat_amount") or "0.00"
    total_with_vat = post_data.get("total_with_vat") or "0.00"
    en16931_data["totals"] = {
        "totalNetAmount": total_net,
        "vatAmount": total_vat,
        "totalGrossAmount": total_with_vat,
        "amountDue": total_with_vat,
    }

    # VAT lines (pre-computed by JS)
    vat_lines_json = post_data.get("vat_lines_json")
    if vat_lines_json:
        try:
            en16931_data["vatLines"] = json.loads(vat_lines_json)
        except json.JSONDecodeError:
            logger.warning("Invalid vat_lines_json from form, ignoring.")

    _merge_exemption_reasons(lines, en16931_data.get("vatLines", []))

    # Top-level dates (required by FactPulse)
    if post_data.get("issue_date"):
        en16931_data["invoiceDate"] = post_data["issue_date"]
    if post_data.get("due_date"):
        en16931_data["paymentDueDate"] = post_data["due_date"]

    en16931_data["references"] = _build_references(post_data)

    notes = _build_notes(post_data)
    if notes:
        en16931_data["notes"] = notes

    payload = {"en16931_data": en16931_data}
    for key in ("supplier_id", "customer_id"):
        if post_data.get(key):
            payload[key] = post_data[key]

    return payload
