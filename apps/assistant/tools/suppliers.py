"""Supplier tools — CRUD operations on suppliers."""

from apps.assistant.tools.registry import ParamType, ToolParam, is_uuid, tool

_ADDR_MAPPING = {
    "address_line1": "streetName",
    "address_postcode": "postalZone",
    "address_city": "cityName",
    "address_country": "countryCode",
}

_ADDR_PARAMS = [
    ToolParam("address_line1", ParamType.STRING, "Adresse ligne 1", required=False),
    ToolParam("address_postcode", ParamType.STRING, "Code postal", required=False),
    ToolParam("address_city", ParamType.STRING, "Ville", required=False),
    ToolParam(
        "address_country",
        ParamType.STRING,
        "Code pays ISO (défaut: FR)",
        required=False,
    ),
]


def _build_address(kw, existing=None):
    """Build address dict from keyword args. Returns dict or None."""
    addr_update = {}
    for param, json_key in _ADDR_MAPPING.items():
        if param in kw and kw[param] is not None:
            addr_update[json_key] = kw[param]
    if not addr_update:
        return None
    addr = dict(existing or {})
    addr.update(addr_update)
    return addr


def _serialize_supplier(s):
    return {
        "uuid": str(s.uuid),
        "name": s.name,
        "siren": s.siren,
        "siret": s.siret,
        "vat_number": s.vat_number,
        "email": s.email,
        "iban": s.iban,
        "is_default": s.is_default,
        "address": s.address,
    }


@tool(
    name="list_suppliers",
    description="Liste les fournisseurs (émetteurs de factures) de l'organisation.",
    params=[
        ToolParam(
            "limit",
            ParamType.INTEGER,
            "Nombre max de résultats (défaut 20)",
            required=False,
        ),
    ],
)
def list_suppliers(org, limit=20, **kw):
    from apps.billing.models import Supplier

    qs = Supplier.objects.filter(
        organization=org,
        archived=False,
    ).order_by("name")
    limit = min(int(limit or 20), 50)
    return [_serialize_supplier(s) for s in qs[:limit]]


@tool(
    name="get_supplier",
    description="Récupère le détail d'un fournisseur par son UUID ou son nom.",
    params=[
        ToolParam("identifier", ParamType.STRING, "UUID ou nom du fournisseur"),
    ],
)
def get_supplier(org, identifier, **kw):
    from apps.billing.models import Supplier

    qs = Supplier.objects.filter(organization=org, archived=False)
    s = None
    if is_uuid(identifier):
        s = qs.filter(uuid=identifier).first()
    if s is None:
        s = qs.filter(name__iexact=identifier).first()
    if s is None:
        return {"error": f"Fournisseur '{identifier}' introuvable."}
    return _serialize_supplier(s)


@tool(
    name="create_supplier",
    description=(
        "Crée un nouveau fournisseur (émetteur de factures). "
        "Utilise lookup_sirene d'abord pour pré-remplir les infos."
    ),
    params=[
        ToolParam("name", ParamType.STRING, "Nom du fournisseur (raison sociale)"),
        ToolParam("email", ParamType.STRING, "Email", required=False),
        ToolParam(
            "siren", ParamType.STRING, "Numéro SIREN (9 chiffres)", required=False
        ),
        ToolParam(
            "siret", ParamType.STRING, "Numéro SIRET (14 chiffres)", required=False
        ),
        ToolParam(
            "vat_number",
            ParamType.STRING,
            "Numéro de TVA intracommunautaire",
            required=False,
        ),
        ToolParam("iban", ParamType.STRING, "IBAN", required=False),
        ToolParam("bic", ParamType.STRING, "BIC", required=False),
        *_ADDR_PARAMS,
    ],
    confirm=True,
    read_only=False,
)
def create_supplier(org, name, **kw):
    from apps.billing.models import Supplier

    data = {"name": name, "organization": org}
    for field in ("email", "siren", "siret", "vat_number", "iban", "bic"):
        if kw.get(field):
            data[field] = kw[field]

    addr = _build_address(kw)
    if addr:
        data["address"] = addr

    s = Supplier.objects.create(**data)
    return _serialize_supplier(s)


@tool(
    name="update_supplier",
    description="Met à jour les informations d'un fournisseur.",
    params=[
        ToolParam("supplier_uuid", ParamType.STRING, "UUID du fournisseur"),
        ToolParam("name", ParamType.STRING, "Nouveau nom", required=False),
        ToolParam("email", ParamType.STRING, "Nouvel email", required=False),
        ToolParam(
            "siren", ParamType.STRING, "Numéro SIREN (9 chiffres)", required=False
        ),
        ToolParam(
            "siret", ParamType.STRING, "Numéro SIRET (14 chiffres)", required=False
        ),
        ToolParam(
            "vat_number",
            ParamType.STRING,
            "Numéro de TVA intracommunautaire",
            required=False,
        ),
        ToolParam("iban", ParamType.STRING, "Nouvel IBAN", required=False),
        ToolParam("bic", ParamType.STRING, "Nouveau BIC", required=False),
        *_ADDR_PARAMS,
    ],
    confirm=True,
    read_only=False,
)
def update_supplier(org, supplier_uuid, **kw):
    from apps.billing.models import Supplier

    try:
        s = Supplier.objects.get(uuid=supplier_uuid, organization=org, archived=False)
    except Supplier.DoesNotExist:
        return {"error": "Fournisseur introuvable."}

    scalar_fields = ["name", "email", "siren", "siret", "vat_number", "iban", "bic"]
    changed = []
    for field in scalar_fields:
        if field in kw and kw[field] is not None:
            setattr(s, field, kw[field])
            changed.append(field)

    addr = _build_address(kw, existing=s.address)
    if addr is not None:
        s.address = addr
        changed.append("address")

    if not changed:
        return {"error": "Aucun champ à mettre à jour."}

    s.save(update_fields=changed + ["updated_at"])
    return _serialize_supplier(s)


@tool(
    name="archive_supplier",
    description="Archive un fournisseur (le masque des listes sans le supprimer).",
    params=[
        ToolParam("supplier_uuid", ParamType.STRING, "UUID du fournisseur à archiver"),
    ],
    confirm=True,
    read_only=False,
)
def archive_supplier(org, supplier_uuid, **kw):
    from apps.billing.models import Supplier

    try:
        s = Supplier.objects.get(uuid=supplier_uuid, organization=org, archived=False)
    except Supplier.DoesNotExist:
        return {"error": "Fournisseur introuvable ou déjà archivé."}

    s.archived = True
    s.save(update_fields=["archived", "updated_at"])
    result = _serialize_supplier(s)
    result["archived"] = True
    return result
