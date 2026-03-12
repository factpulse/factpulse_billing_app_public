"""Celery tasks for FactPulse integration — PDF generation, validation, transmission."""

import base64
import hashlib
import logging
from datetime import date as date_type

from celery import shared_task
from django.core.files.base import ContentFile
from django.template.loader import render_to_string

from apps.billing.models import Invoice, InvoiceAuditLog
from apps.billing.services import invoice_service
from apps.billing.services.flow_detector import (
    FLOW_TO_PROCESSING_RULE,
    get_ereporting_flux_type,
    is_ereporting_flow,
)
from apps.billing.services.state_machine import can_transition
from apps.core.models import Organization
from apps.factpulse.client import (
    FactPulseError,
    FactPulseUnavailableError,
    client,
)
from apps.webhooks.events import InvoiceEvent

logger = logging.getLogger(__name__)

S = Invoice.Status
A = InvoiceAuditLog.Action
ES = Invoice.EreportingStatus


def _get_client_uid(organization):
    """Extract FactPulse client UID from an Organization."""
    return (
        str(organization.factpulse_client_uid)
        if organization.factpulse_client_uid
        else None
    )


def _get_invoice_or_log(uuid, log_prefix="", select_related_fields=None):
    """Fetch an Invoice by UUID, or log and return None."""
    qs = Invoice.objects
    if select_related_fields:
        qs = qs.select_related(*select_related_fields)
    try:
        return qs.get(uuid=uuid)
    except Invoice.DoesNotExist:
        logger.error(
            "%sInvoice %s not found", f"{log_prefix}: " if log_prefix else "", uuid
        )
        return None


def _build_error_dict(error, error_type):
    """Build a factpulse_error dict from a FactPulse exception."""
    raw_details = getattr(error, "details", None)
    details = raw_details if isinstance(raw_details, dict) else {}
    return {
        "error_type": error_type,
        "message": str(error),
        "error_code": details.get("errorCode", ""),
        "errors": details.get("details", []),
    }


@shared_task
def generate_and_validate_invoice(invoice_uuid):
    """Generate source PDF via WeasyPrint, send to FactPulse, store Factur-X.

    Flow:
    1. WeasyPrint → source PDF
    2. POST to FactPulse /generate-invoice
    3. Store Factur-X PDF
    4. Transition processing → validated (or → draft on error)
    """
    invoice = _get_invoice_or_log(invoice_uuid, select_related_fields=("organization",))
    if not invoice:
        return

    if invoice.status != S.PROCESSING:
        logger.warning(
            "Invoice %s is not in processing state (status: %s)",
            invoice_uuid,
            invoice.status,
        )
        return

    client_uid = _get_client_uid(invoice.organization)
    if not client_uid:
        _handle_factpulse_error(
            invoice,
            FactPulseError("Organization has no FactPulse client configured."),
            "not_provisioned",
        )
        return

    try:
        # Step 1: Generate source PDF
        source_pdf = _generate_source_pdf(invoice)

        # Step 2: Call FactPulse API
        facturx_pdf = client.generate_invoice(
            invoice_data=invoice.en16931_data,
            source_pdf=source_pdf,
            client_uid=client_uid,
        )

        # Step 3: Store Factur-X PDF
        filename = f"{invoice.number or invoice.uuid}.pdf"
        invoice.pdf_file.save(filename, ContentFile(facturx_pdf), save=False)
        invoice.facturx_status = "generated"
        invoice.factpulse_error = None

        # Step 4: Transition to validated
        old_status = invoice.status
        invoice.status = S.VALIDATED
        invoice.save()

        InvoiceAuditLog.objects.create(
            invoice=invoice,
            action=A.STATUS_CHANGE,
            old_status=old_status,
            new_status=S.VALIDATED,
            details={"source": "celery_task"},
        )

        # Check auto-cancel for credit notes
        invoice_service.check_auto_cancel(invoice)

        # Emit webhook
        from apps.webhooks.services import emit_webhook  # lazy: mocked in tests

        emit_webhook(
            invoice.organization,
            InvoiceEvent.VALIDATED,
            {
                "uuid": str(invoice.uuid),
                "number": invoice.number,
                "status": S.VALIDATED,
                "total_incl_tax": str(invoice.total_incl_tax)
                if invoice.total_incl_tax
                else None,
                "external_id": invoice.external_id,
                "pdf_url": f"/api/v1/invoices/{invoice.uuid}/pdf/",
            },
        )

        # Auto-submit e-reporting for non-B2B flows (transparent to user)
        if is_ereporting_flow(invoice.detected_flow):
            submit_ereporting_for_invoice.delay(str(invoice.uuid))

    except FactPulseError as e:
        _handle_factpulse_error(invoice, e, "validation_rejected")

    except FactPulseUnavailableError as e:
        _handle_factpulse_error(invoice, e, "unavailable")

    except Exception as e:
        logger.exception("Unexpected error processing invoice %s", invoice_uuid)
        _handle_factpulse_error(invoice, e, "timeout")


@shared_task
def generate_source_pdf(invoice_uuid):
    """Generate a source PDF for draft preview (no FactPulse call)."""
    invoice = _get_invoice_or_log(invoice_uuid)
    if not invoice:
        return

    source_pdf = _generate_source_pdf(invoice)
    filename = f"source_{invoice.uuid}.pdf"
    invoice.pdf_file.save(filename, ContentFile(source_pdf), save=True)


@shared_task
def transmit_invoice(invoice_uuid):
    """Submit a Factur-X invoice to the PA via AFNOR Flow Service (POST /flows).

    On 202: stays transmitting (flowId stored, poll_cdar_events handles confirmation).
    On error: reverts to validated so user can retry.
    """
    invoice = _get_invoice_or_log(invoice_uuid, select_related_fields=("organization",))
    if not invoice:
        return

    if invoice.status != S.TRANSMITTING:
        return

    client_uid = _get_client_uid(invoice.organization)

    try:
        if not invoice.pdf_file:
            raise FactPulseError("No Factur-X PDF attached to invoice.")
        facturx_pdf = invoice.pdf_file.read()

        # Build AFNOR FlowInfo (XP Z12-013 §FlowInfo)
        processing_rule = FLOW_TO_PROCESSING_RULE.get(invoice.detected_flow, "B2B")
        flow_info = {
            "flowSyntax": "Factur-X",
            "flowProfile": "CIUS",
            "trackingId": str(invoice.uuid),
            "name": invoice.number or str(invoice.uuid),
            "processingRule": processing_rule,
            "sha256": hashlib.sha256(facturx_pdf).hexdigest(),
        }

        filename = f"{invoice.number or invoice.uuid}.pdf"
        result = client.submit_flow(
            flow_info=flow_info,
            file_bytes=facturx_pdf,
            filename=filename,
            client_uid=client_uid,
        )

        # 202 received — flow submitted, awaiting PA confirmation.
        # Stay in "transmitting" until poll_cdar_events confirms via CDAR or AFNOR flow.
        invoice.pdp_transmission_id = result.get("flowId", "")
        invoice.pdp_status = "submitted"
        invoice.factpulse_error = None
        invoice.save(
            update_fields=[
                "pdp_transmission_id",
                "pdp_status",
                "factpulse_error",
                "updated_at",
            ]
        )

        InvoiceAuditLog.objects.create(
            invoice=invoice,
            action=A.FLOW_SUBMITTED,
            old_status=S.TRANSMITTING,
            new_status=S.TRANSMITTING,
            details={
                "source": "celery_task",
                "flow_id": result.get("flowId", ""),
            },
        )

    except (FactPulseError, FactPulseUnavailableError) as e:
        logger.error("Failed to transmit invoice %s: %s", invoice_uuid, e)
        # Revert to validated so the user can retry
        old_status = invoice.status
        invoice.status = S.VALIDATED
        invoice.factpulse_error = _build_error_dict(e, "transmission_failed")
        invoice.save(update_fields=["status", "factpulse_error", "updated_at"])

        InvoiceAuditLog.objects.create(
            invoice=invoice,
            action=A.STATUS_CHANGE,
            old_status=old_status,
            new_status=S.VALIDATED,
            details={"error": str(e), "error_type": "transmission_failed"},
        )


@shared_task
def submit_cdar_paid(invoice_uuid):
    """Submit CDAR paid status (212) to the PA via FactPulse.

    Best-effort: errors are logged but do NOT revert the paid status.
    """
    invoice = _get_invoice_or_log(
        invoice_uuid,
        log_prefix="CDAR",
        select_related_fields=("organization", "supplier"),
    )
    if not invoice:
        return

    if invoice.status != S.PAID:
        logger.warning(
            "CDAR: Invoice %s is not paid (status: %s)", invoice_uuid, invoice.status
        )
        return

    client_uid = _get_client_uid(invoice.organization)
    if not client_uid:
        logger.info(
            "CDAR: No FactPulse client for org %s, skipping", invoice.organization_id
        )
        return

    # Extract buyer data from en16931_data
    recipient = invoice.en16931_data.get("recipient", {})
    buyer_siren = recipient.get("siren", "")
    ea = recipient.get("electronicAddress")
    if isinstance(ea, dict):
        ea_string = ea.get("value", "")
    elif isinstance(ea, str):
        ea_string = ea
    else:
        ea_string = ""

    if not buyer_siren and not ea_string:
        logger.info(
            "CDAR: No buyer siren or electronic address for invoice %s, skipping",
            invoice_uuid,
        )
        InvoiceAuditLog.objects.create(
            invoice=invoice,
            action=A.CDAR_PAID_SKIPPED,
            details={"reason": "missing buyer siren and electronic address"},
        )
        return

    # Build CDAR payload
    supplier_siren = invoice.supplier.siren if invoice.supplier else ""
    if not supplier_siren:
        logger.info(
            "CDAR: No supplier siren for invoice %s, skipping",
            invoice_uuid,
        )
        InvoiceAuditLog.objects.create(
            invoice=invoice,
            action=A.CDAR_PAID_SKIPPED,
            details={"reason": "missing supplier siren"},
        )
        return

    amount = invoice.payment_amount or invoice.total_incl_tax
    payload = {
        "invoiceId": invoice.number,
        "invoiceIssueDate": invoice.issue_date.isoformat()
        if invoice.issue_date
        else "",
        "invoiceBuyerSiren": buyer_siren,
        "invoiceBuyerElectronicAddress": ea_string,
        "amount": str(amount) if amount else "0",
        "currency": invoice.currency_code or "EUR",
        "senderSiren": supplier_siren,
        "flowType": "CustomerInvoiceLC",
    }

    try:
        result = client.submit_paid_status(payload, client_uid=client_uid)

        InvoiceAuditLog.objects.create(
            invoice=invoice,
            action=A.CDAR_PAID_SUBMITTED,
            details={
                "flow_id": result.get("flowId", ""),
                "document_id": result.get("documentId", ""),
                "status": result.get("status", ""),
            },
        )

    except Exception as e:
        logger.warning(
            "CDAR: Failed to submit paid status for invoice %s: %s", invoice_uuid, e
        )
        InvoiceAuditLog.objects.create(
            invoice=invoice,
            action=A.CDAR_PAID_ERROR,
            details={"error": str(e)},
        )


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def submit_ereporting_for_invoice(self, invoice_uuid):
    """Submit e-reporting data to the PA via FactPulse API.

    For invoices with non-B2B flows (B2C, intra/extra-UE).
    Ref: DGFiP v3.1 flux 10.1 (B2B international) / 10.3 (B2C).

    Auto-triggered after validation. Transparent to user.
    """
    invoice, client_uid, flux_type = _ereporting_preconditions(invoice_uuid)
    if not invoice:
        return

    payload = _build_ereporting_payload(invoice, flux_type)

    try:
        result = client.submit_ereporting(payload, client_uid=client_uid)
        _ereporting_success(invoice, flux_type, result)
    except (FactPulseError, FactPulseUnavailableError) as e:
        _ereporting_failure(invoice, invoice_uuid, e)
        if isinstance(e, FactPulseUnavailableError):
            raise self.retry(exc=e) from e


def _ereporting_preconditions(invoice_uuid):
    """Check all preconditions for e-reporting. Returns (invoice, client_uid, flux_type) or (None, None, None)."""
    invoice = _get_invoice_or_log(
        invoice_uuid,
        log_prefix="E-reporting",
        select_related_fields=("organization", "supplier", "customer"),
    )
    if not invoice:
        return None, None, None

    if invoice.status != S.VALIDATED:
        logger.info(
            "E-reporting: Invoice %s not validated (status: %s), skipping",
            invoice_uuid,
            invoice.status,
        )
        return None, None, None

    if not is_ereporting_flow(invoice.detected_flow):
        logger.info(
            "E-reporting: Invoice %s flow is %s, not e-reporting",
            invoice_uuid,
            invoice.detected_flow,
        )
        return None, None, None

    if invoice.ereporting_status == ES.SUBMITTED:
        return None, None, None

    client_uid = _get_client_uid(invoice.organization)
    if not client_uid:
        logger.info(
            "E-reporting: No FactPulse client for org %s", invoice.organization_id
        )
        return None, None, None

    if not invoice.supplier:
        logger.info("E-reporting: Invoice %s has no supplier, skipping", invoice_uuid)
        return None, None, None

    flux_type = get_ereporting_flux_type(invoice.detected_flow)
    return invoice, client_uid, flux_type


def _build_ereporting_payload(invoice, flux_type):
    """Build the DGFiP v3.1 §10 e-reporting payload."""
    supplier = invoice.supplier
    recipient = invoice.en16931_data.get("recipient", {})
    recipient_address = recipient.get("postalAddress", {})
    issue_date = invoice.issue_date.isoformat() if invoice.issue_date else ""

    return {
        "fluxType": flux_type,
        "sender": {
            "siren": supplier.siren,
            "name": supplier.name,
            "vatNumber": supplier.vat_number,
        },
        "period": {"startDate": issue_date, "endDate": issue_date},
        "invoices": [
            {
                "invoiceNumber": invoice.number,
                "invoiceDate": issue_date,
                "invoiceTypeCode": invoice.invoice_type_code,
                "currencyCode": invoice.currency_code or "EUR",
                "buyerCountry": recipient_address.get("countryCode", ""),
                "buyerVatNumber": recipient.get("vatNumber", ""),
                "totalNetAmount": str(invoice.total_excl_tax or 0),
                "vatAmount": str(invoice.total_tax or 0),
                "totalGrossAmount": str(invoice.total_incl_tax or 0),
                "operationCategory": invoice.operation_category
                or Invoice.OperationCategory.TPS1,
            }
        ],
    }


def _ereporting_success(invoice, flux_type, result):
    """Handle successful e-reporting submission."""
    invoice.ereporting_status = ES.SUBMITTED
    invoice.ereporting_error = None
    invoice.save(update_fields=["ereporting_status", "ereporting_error", "updated_at"])
    InvoiceAuditLog.objects.create(
        invoice=invoice,
        action=A.EREPORTING_SUBMITTED,
        details={
            "flux_type": flux_type,
            "flow_id": result.get("flowId", ""),
            "status": result.get("status", ""),
        },
    )


def _ereporting_failure(invoice, invoice_uuid, error):
    """Handle failed e-reporting submission."""
    logger.warning("E-reporting: Failed for invoice %s: %s", invoice_uuid, error)
    invoice.ereporting_status = ES.ERROR
    invoice.ereporting_error = {
        "message": str(error),
        "details": getattr(error, "details", {}),
    }
    invoice.save(update_fields=["ereporting_status", "ereporting_error", "updated_at"])
    InvoiceAuditLog.objects.create(
        invoice=invoice,
        action=A.EREPORTING_ERROR,
        details={"error": str(error)},
    )


@shared_task
def poll_cdar_events(invoice_number=None, days=7):
    """Celery Beat task — poll CDAR lifecycle events, then check AFNOR flow fallback.

    Status transitions from CDAR codes (BR-FR-CDV-CL-06):
        200 (Déposée)         → transmitting → transmitted
        201 (Émise par la PF) → transmitted  → accepted
        210 (Refusée)         → transmitting/transmitted → rejected
        213 (Rejetée)         → transmitting/transmitted → rejected
    """
    orgs = Organization.objects.filter(factpulse_client_uid__isnull=False)

    for org in orgs:
        client_uid = _get_client_uid(org)

        try:
            data = client.get_cdar_lifecycle(
                days=days,
                invoice_id=invoice_number,
                client_uid=client_uid,
            )
        except Exception as e:
            logger.warning("CDAR: Failed to poll lifecycle for org %s: %s", org.slug, e)
            continue

        invoices_with_cdar = _process_cdar_lifecycle(org, data)
        _check_afnor_flow_fallback(org, client_uid, invoice_number, invoices_with_cdar)


# CDAR status code → target invoice status
# CDAR lifecycle status codes → invoice status mapping.
# Ref: AFNOR XP Z12-012 §7.3, BR-FR-CDV-CL-06 (Compte-Rendu d'Acheminement).
#   200 = "Déposée"         (deposited on the platform)
#   201 = "Émise par la PF" (issued by the platform to recipient)
#   210 = "Refusée"         (refused by recipient)
#   213 = "Rejetée"         (rejected by the platform — format/validation error)
CDAR_STATUS_MAP = {
    "200": S.TRANSMITTED,
    "201": S.ACCEPTED,
    "210": S.REFUSED,
    "213": S.REJECTED,
}


def _process_cdar_lifecycle(org, data):
    """Process CDAR lifecycle events. Returns set of invoice numbers that got events."""
    invoices_with_cdar = set()

    api_invoice_numbers = [
        inv_data.get("invoiceId", "")
        for inv_data in data.get("invoices", [])
        if inv_data.get("invoiceId")
    ]

    # Batch fetch matching local invoices
    local_invoices = {}
    if api_invoice_numbers:
        for inv in Invoice.objects.filter(
            organization=org,
            number__in=api_invoice_numbers,
            deleted_at__isnull=True,
        ).select_related("organization", "supplier"):
            local_invoices[(inv.supplier.siren, inv.number)] = inv
            local_invoices.setdefault(("", inv.number), inv)

    # Batch prefetch existing cdar_event audit logs for dedup
    dedup_cache = {}
    if local_invoices:
        invoice_pks = {inv.pk for inv in local_invoices.values()}
        for log in InvoiceAuditLog.objects.filter(
            invoice_id__in=invoice_pks,
            action=A.CDAR_EVENT,
        ):
            details = log.details or {}
            dedup_cache.setdefault(log.invoice_id, set()).add(
                (details.get("status_code", ""), details.get("at", ""))
            )

    for inv_data in data.get("invoices", []):
        seller_id = inv_data.get("sellerId", "")
        invoice_id = inv_data.get("invoiceId", "")
        if not invoice_id:
            continue

        invoices_with_cdar.add(invoice_id)

        invoice = local_invoices.get((seller_id, invoice_id))
        if not invoice:
            invoice = local_invoices.get(("", invoice_id))
        if not invoice:
            logger.debug(
                "CDAR: No local invoice for sellerId=%s invoiceId=%s",
                seller_id,
                invoice_id,
            )
            continue

        existing_keys = dedup_cache.get(invoice.pk, set())
        for event in inv_data.get("events", []):
            _process_single_cdar_event(
                invoice, seller_id, invoice_id, event, existing_keys
            )

    return invoices_with_cdar


def _process_single_cdar_event(invoice, seller_id, invoice_id, event, existing_keys):
    """Process a single CDAR lifecycle event for an invoice."""
    status_code = event.get("statusCode", "")
    at = event.get("at", "")
    if (status_code, at) in existing_keys:
        return

    InvoiceAuditLog.objects.create(
        invoice=invoice,
        action=A.CDAR_EVENT,
        details={
            "seller_id": seller_id,
            "invoice_id": invoice_id,
            "status_code": status_code,
            "status_description": event.get("statusDescription", ""),
            "at": at,
            "amount": event.get("amount"),
            "issuer_siren": event.get("issuerSiren", ""),
            "issuer_role": event.get("issuerRole", ""),
            "reason_code": event.get("reasonCode", ""),
        },
    )

    new_status = CDAR_STATUS_MAP.get(status_code)
    if new_status and can_transition(invoice.status, new_status):
        old_status = invoice.status
        invoice.status = new_status
        invoice.save(update_fields=["status", "updated_at"])

        InvoiceAuditLog.objects.create(
            invoice=invoice,
            action=A.STATUS_CHANGE,
            old_status=old_status,
            new_status=new_status,
            details={
                "source": "cdar_poll",
                "cdar_status_code": status_code,
            },
        )

        from apps.webhooks.services import emit_webhook

        emit_webhook(
            invoice.organization,
            f"invoice.{new_status}",
            {
                "uuid": str(invoice.uuid),
                "number": invoice.number,
                "status": new_status,
                "external_id": invoice.external_id,
            },
        )


def _check_afnor_flow_fallback(org, client_uid, invoice_number, invoices_with_cdar):
    """Check AFNOR flow status for transmitting invoices with no CDAR events."""
    stuck_invoices = Invoice.objects.filter(
        organization=org,
        status=S.TRANSMITTING,
        pdp_transmission_id__gt="",
    ).select_related("organization")

    if invoice_number:
        stuck_invoices = stuck_invoices.filter(number=invoice_number)

    for invoice in stuck_invoices:
        if invoice.number in invoices_with_cdar:
            continue

        try:
            flow = client.get_flow_status(
                invoice.pdp_transmission_id,
                client_uid=client_uid,
            )
            ack = flow.get("acknowledgement", {})
            ack_status = ack.get("status")  # Pending / Ok / Error

            if ack_status == "Ok":
                _apply_flow_fallback(invoice, S.TRANSMITTED, "Ok", flow)
            elif ack_status == "Error":
                _apply_flow_fallback(
                    invoice,
                    S.VALIDATED,
                    "Error",
                    flow,
                    error={
                        "error_type": "pa_submission_failed",
                        "message": "Soumission rejetée par la plateforme agréée.",
                        "errors": [
                            {
                                "item": d.get("item", ""),
                                "reason": d.get(
                                    "reasonMessage", d.get("reasonCode", "")
                                ),
                            }
                            for d in ack.get("details", [])
                        ],
                    },
                )

        except Exception as e:
            logger.warning(
                "CDAR: Failed to check AFNOR flow for invoice %s: %s",
                invoice.uuid,
                e,
            )


def _apply_flow_fallback(invoice, new_status, pdp_status, flow, error=None):
    """Apply a status transition from AFNOR flow fallback."""
    old_status = invoice.status
    invoice.status = new_status
    invoice.pdp_status = pdp_status
    update_fields = ["status", "pdp_status", "updated_at"]
    if error:
        invoice.factpulse_error = error
        update_fields.append("factpulse_error")
    invoice.save(update_fields=update_fields)

    InvoiceAuditLog.objects.create(
        invoice=invoice,
        action=A.STATUS_CHANGE,
        old_status=old_status,
        new_status=new_status,
        details={"source": "afnor_flow_fallback", "afnor_flow": flow},
    )


def _generate_source_pdf(invoice):
    """Generate a source PDF from the invoice data using WeasyPrint.

    PRINCIPE PASSTHROUGH — cette fonction ne doit JAMAIS :
    - Calculer des valeurs métier (totaux, dates, notes, TVA...)
    - Lire des données métier depuis le modèle Supplier/Customer Django
    - Modifier ou enrichir le contenu de en16931_data

    Toutes les données de la facture viennent de en16931_data (le snapshot).
    Les calculs sont faits en amont : côté front (invoice-calculator.js)
    ou côté intégrateur (API), et vérifiés par FactPulse à la validation.

    Les SEULES données lues depuis le modèle Django sont les paramètres
    de rendu PDF (logo, pdf_legal_mentions) qui ne font pas partie du
    standard EN16931.
    """
    from weasyprint import HTML  # heavy import — keep lazy

    # --- Paramètres de rendu PDF (hors EN16931, lus depuis le modèle Django) ---
    logo_data_uri = ""
    if invoice.supplier and invoice.supplier.logo:
        try:
            logo_file = invoice.supplier.logo
            logo_file.open("rb")
            logo_bytes = logo_file.read()
            logo_file.close()
            ext = logo_file.name.rsplit(".", 1)[-1].lower()
            mime = {
                "png": "image/png",
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "svg": "image/svg+xml",
            }.get(ext, "image/png")
            logo_data_uri = (
                f"data:{mime};base64,{base64.b64encode(logo_bytes).decode()}"
            )
        except Exception:
            logger.warning(
                "Could not read supplier logo for invoice %s",
                invoice.uuid,
                exc_info=True,
            )

    pdf_legal_mentions = ""
    primary_color = ""
    if invoice.supplier:
        pdf_legal_mentions = invoice.supplier.pdf_legal_mentions
        primary_color = invoice.supplier.primary_color

    # --- Données EN16931 : lues depuis en16931_data uniquement ---
    # Conversion des dates ISO string → objets date pour le filtre |date:"d/m/Y"
    # Fallback sur les champs dénormalisés du modèle Invoice (même source,
    # extraits de en16931_data au save()).
    references = dict(invoice.en16931_data.get("references", {}))
    for date_key, model_field in (("issueDate", "issue_date"), ("dueDate", "due_date")):
        val = references.get(date_key)
        if val and isinstance(val, str):
            try:
                references[date_key] = date_type.fromisoformat(val)
            except ValueError:
                pass
        elif not val:
            # Fallback : champ dénormalisé du modèle (même donnée, extraite au save)
            references[date_key] = getattr(invoice, model_field, None)

    html_content = render_to_string(
        "pdf/invoice.html",
        {
            # Données EN16931 (snapshot passthrough)
            "invoice": invoice,
            "data": invoice.en16931_data,
            "supplier": invoice.en16931_data.get("supplier", {}),
            "recipient": invoice.en16931_data.get("recipient", {}),
            "lines": invoice.en16931_data.get("invoiceLines", []),
            "totals": invoice.en16931_data.get("totals", {}),
            "vat_lines": invoice.en16931_data.get("vatLines", []),
            "notes": invoice.en16931_data.get("notes", []),
            "references": references,
            # Paramètres de rendu PDF (hors EN16931)
            "logo_data_uri": logo_data_uri,
            "pdf_legal_mentions": pdf_legal_mentions,
            "primary_color": primary_color,
        },
    )

    pdf_bytes = HTML(string=html_content).write_pdf()
    return pdf_bytes


def _handle_factpulse_error(invoice, error, error_type):
    """Handle FactPulse error — revert to draft and store error."""
    old_status = invoice.status
    invoice.status = S.DRAFT

    invoice.factpulse_error = _build_error_dict(error, error_type)
    invoice.save()

    InvoiceAuditLog.objects.create(
        invoice=invoice,
        action=A.STATUS_CHANGE,
        old_status=old_status,
        new_status=S.DRAFT,
        details={"error": str(error), "error_type": error_type},
    )

    # Emit error webhook
    from apps.webhooks.services import emit_webhook

    emit_webhook(
        invoice.organization,
        InvoiceEvent.ERROR,
        {
            "uuid": str(invoice.uuid),
            "status": S.DRAFT,
            "external_id": invoice.external_id,
            "error_type": error_type,
            "details": getattr(error, "details", {}),
        },
    )
