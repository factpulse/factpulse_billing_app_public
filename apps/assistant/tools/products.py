"""Product tools — CRUD operations on product catalog."""

from apps.assistant.tools.registry import ParamType, ToolParam, is_uuid, tool


def _serialize_product(p):
    return {
        "uuid": str(p.uuid),
        "name": p.name,
        "description": p.description,
        "reference": p.reference,
        "unit_price": str(p.default_unit_price) if p.default_unit_price else None,
        "vat_rate": str(p.default_vat_rate) if p.default_vat_rate else None,
        "unit": p.default_unit,
    }


@tool(
    name="list_products",
    description="Liste les produits du catalogue, avec recherche optionnelle par nom.",
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
def list_products(org, search=None, limit=20, **kw):
    from apps.billing.models import Product

    qs = Product.objects.filter(
        organization=org,
        archived=False,
    ).order_by("name")
    if search:
        qs = qs.filter(name__icontains=search)
    limit = min(int(limit or 20), 50)
    return [_serialize_product(p) for p in qs[:limit]]


@tool(
    name="get_product",
    description="Récupère le détail d'un produit par son UUID ou son nom.",
    params=[
        ToolParam("identifier", ParamType.STRING, "UUID ou nom du produit"),
    ],
)
def get_product(org, identifier, **kw):
    from apps.billing.models import Product

    qs = Product.objects.filter(organization=org, archived=False)
    p = None
    if is_uuid(identifier):
        p = qs.filter(uuid=identifier).first()
    if p is None:
        p = qs.filter(name__iexact=identifier).first()
    if p is None:
        return {"error": f"Produit '{identifier}' introuvable."}
    return _serialize_product(p)


@tool(
    name="create_product",
    description="Crée un nouveau produit dans le catalogue.",
    params=[
        ToolParam("name", ParamType.STRING, "Nom du produit"),
        ToolParam(
            "unit_price",
            ParamType.NUMBER,
            "Prix unitaire HT par défaut",
            required=False,
        ),
        ToolParam(
            "vat_rate",
            ParamType.NUMBER,
            "Taux de TVA par défaut (ex: 20 pour 20%)",
            required=False,
        ),
        ToolParam("description", ParamType.STRING, "Description", required=False),
        ToolParam("reference", ParamType.STRING, "Référence produit", required=False),
    ],
    confirm=True,
    read_only=False,
)
def create_product(
    org,
    name,
    unit_price=None,
    vat_rate=None,
    description="",
    reference="",
    **kw,
):
    from decimal import Decimal

    from apps.billing.models import Product

    p = Product.objects.create(
        organization=org,
        name=name,
        description=description,
        reference=reference,
        default_unit_price=Decimal(str(unit_price)) if unit_price is not None else None,
        default_vat_rate=Decimal(str(vat_rate)) if vat_rate is not None else None,
    )
    return _serialize_product(p)


@tool(
    name="update_product",
    description="Met à jour un produit existant.",
    params=[
        ToolParam("product_uuid", ParamType.STRING, "UUID du produit à modifier"),
        ToolParam("name", ParamType.STRING, "Nouveau nom", required=False),
        ToolParam(
            "unit_price", ParamType.NUMBER, "Nouveau prix unitaire HT", required=False
        ),
        ToolParam("vat_rate", ParamType.NUMBER, "Nouveau taux de TVA", required=False),
        ToolParam(
            "description", ParamType.STRING, "Nouvelle description", required=False
        ),
    ],
    confirm=True,
    read_only=False,
)
def update_product(org, product_uuid, **kw):
    from decimal import Decimal

    from apps.billing.models import Product

    try:
        p = Product.objects.get(uuid=product_uuid, organization=org, archived=False)
    except Product.DoesNotExist:
        return {"error": "Produit introuvable."}

    changed = []
    field_map = {
        "name": "name",
        "description": "description",
        "unit_price": "default_unit_price",
        "vat_rate": "default_vat_rate",
    }
    for param, model_field in field_map.items():
        if param in kw and kw[param] is not None:
            value = kw[param]
            if param in ("unit_price", "vat_rate"):
                value = Decimal(str(value))
            setattr(p, model_field, value)
            changed.append(model_field)

    if not changed:
        return {"error": "Aucun champ à mettre à jour."}

    p.save(update_fields=changed + ["updated_at"])
    return _serialize_product(p)


@tool(
    name="archive_product",
    description="Archive un produit (le masque des listes sans le supprimer).",
    params=[
        ToolParam("product_uuid", ParamType.STRING, "UUID du produit à archiver"),
    ],
    confirm=True,
    read_only=False,
)
def archive_product(org, product_uuid, **kw):
    from apps.billing.models import Product

    try:
        p = Product.objects.get(uuid=product_uuid, organization=org, archived=False)
    except Product.DoesNotExist:
        return {"error": "Produit introuvable ou déjà archivé."}

    p.archived = True
    p.save(update_fields=["archived", "updated_at"])
    result = _serialize_product(p)
    result["archived"] = True
    return result
