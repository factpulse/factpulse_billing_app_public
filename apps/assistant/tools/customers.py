"""Customer tools — CRUD operations on customer records."""

from apps.assistant.tools.registry import ParamType, ToolParam, is_uuid, tool


def _format_address(addr):
    if not addr or not isinstance(addr, dict):
        return ""
    parts = [
        addr.get("streetName", ""),
        " ".join(filter(None, [addr.get("postalZone", ""), addr.get("cityName", "")])),
        addr.get("countryCode", ""),
    ]
    return ", ".join(p for p in parts if p)


def _serialize_customer(c):
    return {
        "uuid": str(c.uuid),
        "name": c.name,
        "email": c.email,
        "siren": c.siren,
        "siret": c.siret,
        "vat_number": c.vat_number,
        "customer_type": c.customer_type,
        "address": _format_address(c.address),
    }


@tool(
    name="list_customers",
    description="Liste les clients de l'organisation, avec recherche optionnelle par nom.",
    params=[
        ToolParam(
            "search",
            ParamType.STRING,
            "Recherche par nom (partielle)",
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
def list_customers(org, search=None, limit=20, **kw):
    from apps.billing.models import Customer

    qs = Customer.objects.filter(
        organization=org,
        archived=False,
    ).order_by("name")
    if search:
        qs = qs.filter(name__icontains=search)
    limit = min(int(limit or 20), 50)
    return [_serialize_customer(c) for c in qs[:limit]]


@tool(
    name="get_customer",
    description="Récupère le détail d'un client par son UUID ou son nom exact.",
    params=[
        ToolParam("identifier", ParamType.STRING, "UUID ou nom du client"),
    ],
)
def get_customer(org, identifier, **kw):
    from apps.billing.models import Customer

    qs = Customer.objects.filter(organization=org, archived=False)
    c = None
    if is_uuid(identifier):
        c = qs.filter(uuid=identifier).first()
    if c is None:
        c = qs.filter(name__iexact=identifier).first()
    if c is None:
        return {"error": f"Client '{identifier}' introuvable."}
    return _serialize_customer(c)


@tool(
    name="create_customer",
    description=(
        "Crée un nouveau client. Le type est auto-détecté si non fourni "
        "(à partir du SIREN, TVA, pays). Utilise lookup_sirene d'abord "
        "pour pré-remplir les infos."
    ),
    params=[
        ToolParam("name", ParamType.STRING, "Nom du client (raison sociale)"),
        ToolParam("email", ParamType.STRING, "Email du client", required=False),
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
        ToolParam(
            "customer_type",
            ParamType.STRING,
            "Type de client (auto-détecté si omis)",
            required=False,
            enum=["assujetti_fr", "intra_ue", "extra_ue", "particulier", "public"],
        ),
        ToolParam("address_line1", ParamType.STRING, "Adresse ligne 1", required=False),
        ToolParam("address_postcode", ParamType.STRING, "Code postal", required=False),
        ToolParam("address_city", ParamType.STRING, "Ville", required=False),
        ToolParam(
            "address_country",
            ParamType.STRING,
            "Code pays ISO (défaut: FR)",
            required=False,
        ),
    ],
    confirm=True,
    read_only=False,
)
def create_customer(org, name, **kw):
    from apps.billing.models import Customer
    from apps.billing.services.customer_service import enrich_customer_data

    data = {"name": name}
    for field in (
        "email",
        "siren",
        "siret",
        "vat_number",
        "customer_type",
        "address_line1",
        "address_postcode",
        "address_city",
        "address_country",
    ):
        if kw.get(field):
            data[field] = kw[field]

    enrich_customer_data(data)
    c = Customer.objects.create(organization=org, **data)
    return _serialize_customer(c)


@tool(
    name="update_customer",
    description=(
        "Met à jour les informations d'un client existant. "
        "Le type est re-détecté automatiquement si non fourni."
    ),
    params=[
        ToolParam("customer_uuid", ParamType.STRING, "UUID du client à modifier"),
        ToolParam("name", ParamType.STRING, "Nouveau nom", required=False),
        ToolParam("email", ParamType.STRING, "Nouvel email", required=False),
        ToolParam("siren", ParamType.STRING, "Nouveau SIREN", required=False),
        ToolParam("siret", ParamType.STRING, "Nouveau SIRET", required=False),
        ToolParam(
            "vat_number", ParamType.STRING, "Nouveau numéro de TVA", required=False
        ),
        ToolParam(
            "customer_type",
            ParamType.STRING,
            "Type de client (auto-détecté si omis)",
            required=False,
            enum=["assujetti_fr", "intra_ue", "extra_ue", "particulier", "public"],
        ),
        ToolParam("address_line1", ParamType.STRING, "Adresse ligne 1", required=False),
        ToolParam("address_postcode", ParamType.STRING, "Code postal", required=False),
        ToolParam("address_city", ParamType.STRING, "Ville", required=False),
        ToolParam(
            "address_country",
            ParamType.STRING,
            "Code pays ISO (défaut: FR)",
            required=False,
        ),
    ],
    confirm=True,
    read_only=False,
)
def update_customer(org, customer_uuid, **kw):
    from apps.billing.models import Customer
    from apps.billing.services.customer_service import enrich_customer_data

    try:
        c = Customer.objects.get(uuid=customer_uuid, organization=org, archived=False)
    except Customer.DoesNotExist:
        return {"error": "Client introuvable."}

    data = {}
    for field in (
        "name",
        "email",
        "siren",
        "siret",
        "vat_number",
        "customer_type",
        "address_line1",
        "address_postcode",
        "address_city",
        "address_country",
    ):
        if kw.get(field) is not None:
            data[field] = kw[field]

    if not data:
        return {"error": "Aucun champ à mettre à jour."}

    enrich_customer_data(data)
    for key, value in data.items():
        setattr(c, key, value)
    c.save()
    return _serialize_customer(c)


@tool(
    name="archive_customer",
    description="Archive un client (le masque des listes sans le supprimer).",
    params=[
        ToolParam("customer_uuid", ParamType.STRING, "UUID du client à archiver"),
    ],
    confirm=True,
    read_only=False,
)
def archive_customer(org, customer_uuid, **kw):
    from apps.billing.models import Customer

    try:
        c = Customer.objects.get(uuid=customer_uuid, organization=org, archived=False)
    except Customer.DoesNotExist:
        return {"error": "Client introuvable ou déjà archivé."}

    c.archived = True
    c.save(update_fields=["archived", "updated_at"])
    result = _serialize_customer(c)
    result["archived"] = True
    return result
