"""Invoice tools — read and write operations on invoices."""

from datetime import date

from apps.assistant.tools.registry import ParamType, ToolParam, is_uuid, tool


def _mcp_lines_to_en16931(lines):
    """Convert MCP-format lines to EN16931 invoiceLines."""
    result = []
    for line in lines:
        il = {}
        if "product_uuid" in line:
            il["product_id"] = line["product_uuid"]
        if "item_name" in line:
            il["itemName"] = line["item_name"]
        il["quantity"] = line.get("quantity", 1)
        il["unitPrice"] = str(line.get("unit_price", "0"))
        if "vat_rate" in line:
            il["vatRate"] = str(line["vat_rate"])
        result.append(il)
    return result


def _serialize_invoice(inv):
    result = {
        "uuid": str(inv.uuid),
        "number": inv.number or "(pas encore numérotée)",
        "status": inv.status,
        "customer": inv.customer.name if inv.customer else "(aucun client)",
        "customer_uuid": str(inv.customer.uuid) if inv.customer else None,
        "supplier": inv.supplier.name,
        "supplier_uuid": str(inv.supplier.uuid),
        "total_excl_tax": str(inv.total_excl_tax or 0),
        "total_tax": str(inv.total_tax or 0),
        "total_incl_tax": str(inv.total_incl_tax or 0),
        "currency": inv.currency_code,
        "issue_date": str(inv.issue_date) if inv.issue_date else None,
        "due_date": str(inv.due_date) if inv.due_date else None,
    }
    if inv.factpulse_error:
        result["error"] = inv.factpulse_error
    return result


@tool(
    name="list_invoices",
    description=(
        "Liste les factures avec filtres optionnels. "
        "Retourne les factures les plus récentes en premier."
    ),
    params=[
        ToolParam(
            "status",
            ParamType.STRING,
            "Filtrer par statut",
            required=False,
            enum=["draft", "validated", "transmitted", "accepted", "paid", "cancelled"],
        ),
        ToolParam(
            "customer_name",
            ParamType.STRING,
            "Filtrer par nom de client (recherche partielle)",
            required=False,
        ),
        ToolParam(
            "overdue",
            ParamType.BOOLEAN,
            "Si true, ne retourner que les factures échues impayées",
            required=False,
        ),
        ToolParam(
            "has_error",
            ParamType.BOOLEAN,
            "Si true, ne retourner que les factures en erreur (factpulse_error non vide)",
            required=False,
        ),
        ToolParam(
            "limit",
            ParamType.INTEGER,
            "Nombre max de résultats (défaut 20, max 50)",
            required=False,
        ),
    ],
)
def list_invoices(
    org, status=None, customer_name=None, overdue=False, has_error=False, limit=20, **kw
):
    from apps.billing.models import Invoice

    qs = (
        Invoice.objects.filter(
            organization=org,
            deleted_at__isnull=True,
        )
        .select_related("supplier", "customer")
        .order_by("-created_at")
    )

    if status:
        qs = qs.filter(status=status)
    if customer_name:
        qs = qs.filter(customer__name__icontains=customer_name)
    if overdue:
        qs = qs.filter(
            due_date__lt=date.today(),
            status__in=["validated", "transmitted", "accepted"],
        )
    if has_error:
        qs = qs.filter(factpulse_error__isnull=False)

    limit = min(int(limit or 20), 50)
    invoices = list(qs[:limit])
    return [_serialize_invoice(inv) for inv in invoices]


@tool(
    name="get_invoice",
    description="Récupère le détail d'une facture par son UUID ou son numéro.",
    params=[
        ToolParam(
            "identifier",
            ParamType.STRING,
            "UUID ou numéro de la facture (ex: FA-2026-001)",
        ),
    ],
)
def get_invoice(org, identifier, **kw):
    from apps.billing.models import Invoice

    qs = Invoice.objects.filter(organization=org, deleted_at__isnull=True)
    qs = qs.select_related("supplier", "customer")
    inv = None
    if is_uuid(identifier):
        inv = qs.filter(uuid=identifier).first()
    if inv is None:
        inv = qs.filter(number=identifier).first()
    if inv is None:
        return {"error": f"Facture '{identifier}' introuvable."}

    result = _serialize_invoice(inv)
    lines = inv.en16931_data.get("invoiceLines", [])
    result["lines"] = [
        {
            "item_name": line.get("itemName", ""),
            "description": line.get("itemDescription", ""),
            "quantity": line.get("quantity", ""),
            "unit_price": line.get("unitPrice", ""),
            "vat_rate": line.get("vatRate", ""),
            "line_total": line.get("lineNetAmount", ""),
        }
        for line in lines
    ]
    # Include EN16931 details only when there's an error (for diagnostics)
    if inv.factpulse_error:
        en16931 = inv.en16931_data
        if en16931.get("supplier"):
            result["supplier_details"] = en16931["supplier"]
        if en16931.get("customer"):
            result["customer_details"] = en16931["customer"]
    return result


@tool(
    name="create_draft_invoice",
    description=(
        "Crée un brouillon de facture. Nécessite un client (UUID) et des lignes. "
        "Le client DOIT exister dans la base (utilise list_customers ou create_customer d'abord). "
        "Chaque ligne a un product_uuid OU un item_name, plus quantity et unit_price. "
        "Si supplier_uuid n'est pas fourni, le fournisseur par défaut est utilisé."
    ),
    params=[
        ToolParam(
            "customer_uuid",
            ParamType.STRING,
            "UUID du client (doit exister dans la base)",
        ),
        ToolParam(
            "lines",
            ParamType.ARRAY,
            (
                "Lignes de facture. Chaque ligne: "
                "{product_uuid?, item_name?, quantity, unit_price, vat_rate?}"
            ),
        ),
        ToolParam(
            "supplier_uuid",
            ParamType.STRING,
            "UUID du fournisseur émetteur (défaut: fournisseur par défaut)",
            required=False,
        ),
    ],
    confirm=True,
    read_only=False,
)
def create_draft_invoice(org, customer_uuid, lines, supplier_uuid=None, **kw):
    from apps.billing.services import invoice_service

    payload = {
        "customer_id": customer_uuid,
        "en16931_data": {"invoiceLines": _mcp_lines_to_en16931(lines)},
        "supplier_id": supplier_uuid or "default",
    }

    user = kw.get("user")
    invoice, warnings = invoice_service.create_invoice(org, payload, user=user)
    result = _serialize_invoice(invoice)
    if warnings:
        result["warnings"] = warnings
    return result


@tool(
    name="update_draft_invoice",
    description=(
        "Modifie un brouillon de facture : changer le client, le fournisseur, "
        "les lignes, ou une combinaison. Seuls les brouillons sont modifiables."
    ),
    params=[
        ToolParam("invoice_uuid", ParamType.STRING, "UUID de la facture brouillon"),
        ToolParam(
            "customer_uuid",
            ParamType.STRING,
            "Nouveau client (UUID)",
            required=False,
        ),
        ToolParam(
            "supplier_uuid",
            ParamType.STRING,
            "Nouveau fournisseur émetteur (UUID)",
            required=False,
        ),
        ToolParam(
            "lines",
            ParamType.ARRAY,
            (
                "Nouvelles lignes (remplace les existantes). Chaque ligne: "
                "{product_uuid?, item_name?, quantity, unit_price, vat_rate?}"
            ),
            required=False,
        ),
    ],
    confirm=True,
    read_only=False,
)
def update_draft_invoice(
    org, invoice_uuid, customer_uuid=None, supplier_uuid=None, lines=None, **kw
):
    from apps.billing.models import Invoice
    from apps.billing.services import invoice_service

    try:
        inv = Invoice.objects.get(
            uuid=invoice_uuid,
            organization=org,
            deleted_at__isnull=True,
        )
    except Invoice.DoesNotExist:
        return {"error": "Facture introuvable."}

    if inv.status != "draft":
        return {"error": "Seuls les brouillons peuvent être modifiés."}

    payload = {"version": inv.version}

    if customer_uuid:
        payload["customer_id"] = customer_uuid

    if supplier_uuid:
        payload["supplier_id"] = supplier_uuid

    if lines:
        payload["en16931_data"] = {"invoiceLines": _mcp_lines_to_en16931(lines)}

    if len(payload) <= 1:
        return {"error": "Aucun champ à mettre à jour."}

    user = kw.get("user")
    inv, warnings = invoice_service.update_invoice(inv, payload, user=user)
    result = _serialize_invoice(inv)
    if warnings:
        result["warnings"] = warnings
    return result


@tool(
    name="validate_invoice",
    description="Valide un brouillon de facture (génère le numéro et le PDF Factur-X).",
    params=[
        ToolParam("invoice_uuid", ParamType.STRING, "UUID de la facture à valider"),
    ],
    confirm=True,
    read_only=False,
)
def validate_invoice(org, invoice_uuid, **kw):
    from apps.billing.models import Invoice
    from apps.billing.services import invoice_service

    try:
        inv = Invoice.objects.get(
            uuid=invoice_uuid,
            organization=org,
            deleted_at__isnull=True,
        )
    except Invoice.DoesNotExist:
        return {"error": "Facture introuvable."}

    user = kw.get("user")
    inv = invoice_service.validate_invoice(inv, user=user)
    return _serialize_invoice(inv)


@tool(
    name="cancel_invoice",
    description="Annule une facture en créant un avoir (credit note) à 100%.",
    params=[
        ToolParam("invoice_uuid", ParamType.STRING, "UUID de la facture à annuler"),
    ],
    confirm=True,
    read_only=False,
)
def cancel_invoice(org, invoice_uuid, **kw):
    from apps.billing.models import Invoice
    from apps.billing.services import invoice_service

    try:
        inv = Invoice.objects.get(
            uuid=invoice_uuid,
            organization=org,
            deleted_at__isnull=True,
        )
    except Invoice.DoesNotExist:
        return {"error": "Facture introuvable."}

    user = kw.get("user")
    credit_note = invoice_service.cancel_invoice(inv, user=user)
    result = _serialize_invoice(credit_note)
    result["note"] = "Avoir créé. La facture originale sera annulée automatiquement."
    return result


@tool(
    name="mark_paid",
    description="Marque une facture comme payée.",
    params=[
        ToolParam("invoice_uuid", ParamType.STRING, "UUID de la facture"),
        ToolParam(
            "payment_date",
            ParamType.STRING,
            "Date du paiement (YYYY-MM-DD, défaut: aujourd'hui)",
            required=False,
        ),
        ToolParam(
            "payment_reference",
            ParamType.STRING,
            "Référence du paiement (ex: numéro de virement)",
            required=False,
        ),
    ],
    confirm=True,
    read_only=False,
)
def mark_paid(org, invoice_uuid, payment_date=None, payment_reference=None, **kw):
    from apps.billing.models import Invoice
    from apps.billing.services import invoice_service

    try:
        inv = Invoice.objects.get(
            uuid=invoice_uuid,
            organization=org,
            deleted_at__isnull=True,
        )
    except Invoice.DoesNotExist:
        return {"error": "Facture introuvable."}

    payment_data = {}
    if payment_date:
        payment_data["payment_date"] = payment_date
    if payment_reference:
        payment_data["payment_reference"] = payment_reference

    user = kw.get("user")
    inv = invoice_service.mark_paid(inv, payment_data=payment_data or None, user=user)
    return _serialize_invoice(inv)


@tool(
    name="transmit_invoice",
    description=(
        "Transmet une facture validée à la plateforme agréée (PA). "
        "La facture doit être au statut 'validated'. Action irréversible."
    ),
    params=[
        ToolParam("invoice_uuid", ParamType.STRING, "UUID de la facture à transmettre"),
    ],
    confirm=True,
    read_only=False,
)
def transmit_invoice(org, invoice_uuid, **kw):
    from apps.billing.models import Invoice
    from apps.billing.services import invoice_service

    try:
        inv = Invoice.objects.get(
            uuid=invoice_uuid,
            organization=org,
            deleted_at__isnull=True,
        )
    except Invoice.DoesNotExist:
        return {"error": "Facture introuvable."}

    user = kw.get("user")
    inv = invoice_service.transmit_invoice(inv, user=user)
    return _serialize_invoice(inv)


@tool(
    name="download_pdf",
    description=(
        "Télécharge le PDF d'une facture. "
        "Pour les brouillons, déclenche la génération si nécessaire. "
        "Retourne l'URL de téléchargement du PDF."
    ),
    params=[
        ToolParam("invoice_uuid", ParamType.STRING, "UUID de la facture"),
    ],
)
def download_pdf(org, invoice_uuid, **kw):
    import os

    from apps.billing.models import Invoice
    from apps.factpulse.tasks import generate_source_pdf

    site_url = os.environ.get("SITE_URL", "http://localhost:8000").rstrip("/")

    try:
        inv = Invoice.objects.get(
            uuid=invoice_uuid,
            organization=org,
            deleted_at__isnull=True,
        )
    except Invoice.DoesNotExist:
        return {"error": "Facture introuvable."}

    pdf_url = f"{site_url}/api/v1/invoices/{inv.uuid}/pdf/"

    if inv.pdf_file:
        pdf_type = "facturx" if inv.status != Invoice.Status.DRAFT else "source"
        return {
            "uuid": str(inv.uuid),
            "pdf_url": pdf_url,
            "pdf_type": pdf_type,
            "filename": f"{inv.number or inv.uuid}.pdf",
        }

    if inv.status == Invoice.Status.DRAFT:
        generate_source_pdf.delay(str(inv.uuid))
        return {
            "uuid": str(inv.uuid),
            "pdf_url": pdf_url,
            "status": "generating",
            "message": "PDF en cours de génération. Réessayez dans quelques secondes.",
        }

    return {"error": "PDF pas encore disponible pour cette facture."}
