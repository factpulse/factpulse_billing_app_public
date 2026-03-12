"""Invoice service — orchestrates invoice creation, update, and lifecycle.

This is the shared service layer used by both DRF viewsets and UI views.
"""

import copy
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.billing.constants import CREDIT_NOTE_TYPE_CODE
from apps.billing.models import Invoice, InvoiceAuditLog
from apps.billing.services import (
    flow_detector,
    numbering_service,
    resolution_service,
    state_machine,
)
from apps.core.exceptions import ConflictError

S = Invoice.Status
A = InvoiceAuditLog.Action
ES = Invoice.EreportingStatus


def create_invoice(organization, payload, user=None):
    """Create a new invoice from the API/UI payload.

    Returns (invoice, warnings).
    """
    warnings = []

    # Resolve supplier
    supplier, supplier_data, sup_warnings = resolution_service.resolve_supplier(
        organization, payload
    )
    warnings.extend(sup_warnings)

    # Resolve customer/recipient
    customer, recipient_data, cust_warnings = resolution_service.resolve_customer(
        organization, payload
    )
    warnings.extend(cust_warnings)

    # Build en16931_data
    en16931_data = copy.deepcopy(payload.get("en16931_data", {}))

    # Inject resolved supplier/recipient data
    if supplier_data:
        en16931_data["supplier"] = supplier_data
    if recipient_data:
        en16931_data["recipient"] = recipient_data

    # Resolve product references in lines
    resolution_service.resolve_product_lines(organization, en16931_data)

    # Enrich missing fields (totals, references, notes, dates)
    from apps.billing.services.payload_builder import enrich_en16931_data

    enrich_en16931_data(en16931_data, supplier=supplier)

    # Create the invoice
    invoice = Invoice(
        organization=organization,
        supplier=supplier,
        customer=customer,
        en16931_data=en16931_data,
        external_id=payload.get("external_id"),
        is_internal=payload.get("is_internal", False),
    )
    invoice.save()

    # Audit log
    _audit_log(invoice, A.CREATED, user=user)

    return invoice, warnings


def update_invoice(invoice, payload, user=None):
    """Update a draft invoice (PATCH with optimistic locking).

    Returns (invoice, warnings).
    """
    _validate_editable(invoice, payload)
    warnings = []

    warnings.extend(_update_supplier(invoice, payload))
    warnings.extend(_update_customer(invoice, payload))
    _update_en16931_data(invoice, payload)
    _update_scalar_fields(invoice, payload)

    # Re-enrich after any en16931_data change (lineNumber, unit, totals, etc.)
    if "en16931_data" in payload:
        from apps.billing.services.payload_builder import enrich_en16931_data

        # Clear computed fields so they get recalculated from new lines
        if "invoiceLines" in payload.get("en16931_data", {}):
            invoice.en16931_data.pop("totals", None)
            invoice.en16931_data.pop("vatLines", None)
        enrich_en16931_data(invoice.en16931_data, supplier=invoice.supplier)

    invoice.version += 1
    invoice.factpulse_error = None  # Clear error on edit
    if invoice.pdf_file:
        invoice.pdf_file.delete(save=False)
    invoice.save()

    _audit_log(invoice, A.DATA_UPDATE, user=user)

    return invoice, warnings


def _validate_editable(invoice, payload):
    """Check invoice is editable and validate optimistic locking."""
    if not state_machine.is_editable(invoice):
        raise ConflictError("Only draft invoices can be edited.")
    expected_version = payload.get("version")
    if expected_version is None:
        raise ConflictError("The 'version' field is required for updates.")
    if int(expected_version) != invoice.version:
        raise ConflictError("Invoice has been modified. Reload and try again.")


def _update_supplier(invoice, payload):
    """Re-resolve supplier if provided in payload. Returns warnings."""
    warnings = []
    if "supplier_id" in payload or "supplier" in payload:
        supplier, supplier_data, sup_warnings = resolution_service.resolve_supplier(
            invoice.organization, payload
        )
        warnings.extend(sup_warnings)
        invoice.supplier = supplier
        invoice.en16931_data["supplier"] = supplier_data
    if "supplier_override" in payload and "supplier_id" in payload:
        invoice.en16931_data["supplier"] = resolution_service.deep_merge(
            invoice.en16931_data.get("supplier", {}), payload["supplier_override"]
        )
    return warnings


def _update_customer(invoice, payload):
    """Re-resolve customer if provided in payload. Returns warnings."""
    warnings = []
    if "customer_id" in payload or "recipient" in payload:
        customer, recipient_data, cust_warnings = resolution_service.resolve_customer(
            invoice.organization, payload
        )
        warnings.extend(cust_warnings)
        invoice.customer = customer
        if recipient_data:
            invoice.en16931_data["recipient"] = recipient_data
    if "customer_override" in payload and "customer_id" in payload:
        invoice.en16931_data["recipient"] = resolution_service.deep_merge(
            invoice.en16931_data.get("recipient", {}), payload["customer_override"]
        )
    return warnings


def _update_en16931_data(invoice, payload):
    """Merge en16931_data updates from payload into invoice."""
    if "en16931_data" not in payload:
        return
    new_data = payload["en16931_data"]

    # Preserve BT-25/BT-26 when references are replaced (credit notes)
    if "references" in new_data and invoice.preceding_invoice:
        old_refs = invoice.en16931_data.get("references", {})
        for key in ("precedingInvoiceReference", "precedingInvoiceDate"):
            if key in old_refs and key not in new_data["references"]:
                new_data["references"][key] = old_refs[key]

    for key, value in new_data.items():
        invoice.en16931_data[key] = value

    if "invoiceLines" in new_data:
        resolution_service.resolve_product_lines(
            invoice.organization, invoice.en16931_data
        )


def _update_scalar_fields(invoice, payload):
    """Update simple scalar fields from payload."""
    if "external_id" in payload:
        invoice.external_id = payload["external_id"]
    if "is_internal" in payload:
        invoice.is_internal = payload["is_internal"]


def soft_delete(invoice, user=None):
    """Soft-delete a draft invoice."""
    if not state_machine.is_deletable(invoice):
        if invoice.number:
            raise ConflictError("Cannot delete an invoice that has a number.")
        raise ConflictError("Only draft invoices can be deleted.")

    if invoice.status == S.PROCESSING:
        raise ConflictError("Cannot delete an invoice that is being processed.")

    invoice.deleted_at = timezone.now()
    invoice.save(update_fields=["deleted_at", "updated_at"])

    _audit_log(invoice, A.DELETE, user=user)
    return invoice


def validate_invoice(invoice, user=None):
    """Transition draft → processing and launch Celery task.

    Returns the invoice in 'processing' state.
    """
    state_machine.validate_transition(invoice, S.PROCESSING)

    # Validate supplier has IBAN (required for Factur-X BR-CO-27)
    if invoice.supplier and not invoice.supplier.iban:
        raise ValueError(
            f"Le fournisseur « {invoice.supplier.name} » n'a pas d'IBAN renseigné. "
            "L'IBAN est obligatoire pour générer une facture Factur-X conforme (BT-84)."
        )

    with transaction.atomic():
        # Assign number if not already assigned
        if not invoice.number:
            invoice.number = numbering_service.assign_number(invoice)
            # Set issue_date if not set
            if not invoice.issue_date:
                issue_date = date.today()
                invoice.issue_date = issue_date
                references = invoice.en16931_data.get("references", {})
                references["issueDate"] = issue_date.isoformat()
                invoice.en16931_data["references"] = references

        # Inject invoiceNumber into en16931_data (required by FactPulse)
        invoice.en16931_data["invoiceNumber"] = invoice.number

        # Flow detection (ref BR-FR-20, XP Z12-012)
        if not invoice.detected_flow:
            invoice.detected_flow = flow_detector.detect_flow(invoice)
        flow_detector.inject_bar_note(invoice.en16931_data, invoice.detected_flow)
        flow_detector.inject_framework(invoice.en16931_data, invoice.operation_category)

        # Enrich recipient country from Customer (TT-39, required for e-reporting)
        flow_detector.enrich_recipient_country(invoice.en16931_data, invoice.customer)

        # Mark e-reporting pending for non-B2B flows
        if flow_detector.is_ereporting_flow(invoice.detected_flow):
            invoice.ereporting_status = ES.PENDING

        old_status = invoice.status
        invoice.status = S.PROCESSING
        invoice.factpulse_error = None
        invoice.save()

    _audit_log(
        invoice,
        A.STATUS_CHANGE,
        user=user,
        old_status=old_status,
        new_status=S.PROCESSING,
    )

    # Launch Celery task (imported here to avoid circular imports)
    from apps.factpulse.tasks import generate_and_validate_invoice

    generate_and_validate_invoice.delay(str(invoice.uuid))

    return invoice


def transmit_invoice(invoice, user=None):
    """Transition validated → transmitting (async submission to PA)."""
    state_machine.validate_transition(invoice, S.TRANSMITTING)

    old_status = invoice.status
    invoice.status = S.TRANSMITTING
    invoice.save(update_fields=["status", "updated_at"])

    _audit_log(
        invoice,
        A.STATUS_CHANGE,
        user=user,
        old_status=old_status,
        new_status=S.TRANSMITTING,
    )

    # Launch Celery task — will transition to "transmitted" on 202 from PA
    from apps.factpulse.tasks import transmit_invoice as transmit_task

    transmit_task.delay(str(invoice.uuid))

    return invoice


def mark_paid(invoice, payment_data=None, user=None):
    """Transition to paid."""
    state_machine.validate_transition(invoice, S.PAID)

    old_status = invoice.status
    invoice.status = S.PAID

    payment_data = payment_data or {}
    invoice.payment_date = payment_data.get("payment_date", date.today())
    invoice.payment_reference = payment_data.get("payment_reference", "")
    if payment_data.get("amount"):
        invoice.payment_amount = Decimal(str(payment_data["amount"]))
    elif invoice.total_incl_tax:
        invoice.payment_amount = invoice.total_incl_tax

    invoice.save()

    _audit_log(
        invoice,
        A.STATUS_CHANGE,
        user=user,
        old_status=old_status,
        new_status=S.PAID,
        details=payment_data,
    )

    # Launch CDAR paid status submission (best-effort, async)
    from apps.factpulse.tasks import submit_cdar_paid

    submit_cdar_paid.delay(str(invoice.uuid))

    return invoice


def cancel_invoice(invoice, user=None):
    """Create a credit note (avoir) linked to the invoice.

    Returns the new credit note invoice in draft status.
    """
    if invoice.status not in (S.VALIDATED, S.TRANSMITTED, S.ACCEPTED, S.PAID):
        raise ConflictError(
            "Can only cancel validated, transmitted, accepted, or paid invoices."
        )

    # Create credit note as draft
    en16931_data = copy.deepcopy(invoice.en16931_data)
    references = en16931_data.get("references", {})
    references["invoiceType"] = CREDIT_NOTE_TYPE_CODE

    # BT-25 / BT-26 — required by BR-FR-CO-05 for credit notes
    if invoice.number:
        references["precedingInvoiceReference"] = invoice.number
    if invoice.issue_date:
        issue_date = invoice.issue_date
        references["precedingInvoiceDate"] = (
            issue_date.isoformat()
            if hasattr(issue_date, "isoformat")
            else str(issue_date)
        )

    en16931_data["references"] = references

    credit_note = Invoice(
        organization=invoice.organization,
        supplier=invoice.supplier,
        customer=invoice.customer,
        en16931_data=en16931_data,
        preceding_invoice=invoice,
        invoice_type_code=CREDIT_NOTE_TYPE_CODE,
    )
    credit_note.save()

    _audit_log(
        credit_note,
        A.CREATED,
        user=user,
        details={
            "reason": "cancel",
            "preceding_invoice": str(invoice.uuid),
        },
    )

    return credit_note


def check_auto_cancel(credit_note):
    """Check if a validated credit note should auto-cancel the original invoice.

    Called after a credit note transitions to 'validated'.
    If the credit note total matches the original invoice total exactly,
    the original is cancelled.
    """
    if not credit_note.preceding_invoice:
        return

    original = credit_note.preceding_invoice
    if original.status not in (S.VALIDATED, S.PAID):
        return

    # Compare totals
    if (
        credit_note.total_incl_tax is not None
        and original.total_incl_tax is not None
        and credit_note.total_incl_tax == original.total_incl_tax
    ):
        old_status = original.status
        original.status = S.CANCELLED
        original.save(update_fields=["status", "updated_at"])

        _audit_log(
            original,
            A.STATUS_CHANGE,
            old_status=old_status,
            new_status=S.CANCELLED,
            details={
                "reason": "credit_note_validated",
                "credit_note": str(credit_note.uuid),
            },
        )


def _audit_log(invoice, action, user=None, old_status="", new_status="", details=None):
    """Create an audit log entry."""
    InvoiceAuditLog.objects.create(
        invoice=invoice,
        user=user,
        action=action,
        old_status=old_status,
        new_status=new_status,
        details=details or {},
    )
