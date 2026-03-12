"""Resolution service — resolves the 3 integration patterns (inline, referenced, hybrid).

Resolves supplier, customer/recipient, and product references from the API payload
into concrete data injected into en16931_data.
"""

import copy
import re
import uuid as uuid_lib

from apps.billing.constants import AFNOR_SCHEME_ID
from apps.billing.models import Customer, Product, Supplier


def _is_uuid(value):
    """Check if a string is a valid UUID."""
    try:
        uuid_lib.UUID(str(value))
        return True
    except (ValueError, AttributeError):
        return False


def _validate_external_id(value):
    """Validate external_id format: must start with letter or underscore, UUID forbidden."""
    if _is_uuid(value):
        return False, "external_id cannot be a UUID format."
    if not re.match(r"^[a-zA-Z_]", str(value)):
        return False, "external_id must start with a letter or underscore."
    return True, None


def deep_merge(base, override):
    """Recursively merge override into base. Arrays are replaced entirely."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _compare_data(record_data, payload_data, field_prefix):
    """Compare record data vs payload data and return warnings for mismatches."""
    warnings = []
    for key, payload_val in payload_data.items():
        if key in ("uuid", "external_id", "id"):
            continue
        record_val = record_data.get(key)
        if (
            record_val is not None
            and payload_val is not None
            and str(record_val) != str(payload_val)
        ):
            warnings.append(
                {
                    "code": f"{field_prefix}_data_mismatch",
                    "message": (
                        f'Le {field_prefix} "{key}" dans le payload '
                        f'("{payload_val}") diffère de la fiche en base ("{record_val}").'
                    ),
                    "field": f"{field_prefix}.{key}",
                }
            )
    return warnings


def _lookup_by_identifier(model_class, organization, identifier, label):
    """Generic lookup by UUID, external_id, or (for suppliers) 'default'.

    Args:
        model_class: Django model (Supplier, Customer, Product).
        organization: Organization instance.
        identifier: UUID string, external_id string, or 'default'.
        label: Human-readable name for error messages (e.g. 'Supplier').
    """
    if identifier == "default" and hasattr(model_class, "is_default"):
        try:
            return model_class.objects.get(organization=organization, is_default=True)
        except model_class.DoesNotExist:
            raise ValueError(
                f"No default {label.lower()} found for this organization."
            ) from None

    if _is_uuid(identifier):
        try:
            return model_class.objects.get(organization=organization, uuid=identifier)
        except model_class.DoesNotExist:
            raise ValueError(f"{label} with UUID '{identifier}' not found.") from None

    valid, error = _validate_external_id(identifier)
    if valid:
        try:
            return model_class.objects.get(
                organization=organization, external_id=identifier
            )
        except model_class.DoesNotExist:
            raise ValueError(
                f"{label} with external_id '{identifier}' not found."
            ) from None

    raise ValueError(f"Invalid {label.lower()} identifier: '{identifier}'.")


def _resolve_inline_entity(
    model_class,
    organization,
    inline_data,
    to_en16931_fn,
    label,
    match_fields,
    create_fn,
):
    """Generic inline resolution: match by UUID/external_id/match_fields, or auto-create.

    Args:
        model_class: Django model (Supplier, Customer).
        organization: Organization instance.
        inline_data: Dict from the payload.
        to_en16931_fn: Callable(instance) → dict for comparison.
        label: Human-readable name for warnings (e.g. 'supplier', 'recipient').
        match_fields: List of (data_key, model_field) tuples for fallback matching.
        create_fn: Callable(organization, inline_data) → model instance for auto-creation.
    """
    warnings = []

    # Try UUID match
    if inline_data.get("uuid") and _is_uuid(inline_data["uuid"]):
        try:
            entity = model_class.objects.get(
                organization=organization, uuid=inline_data["uuid"]
            )
            warnings.extend(_compare_data(to_en16931_fn(entity), inline_data, label))
            return entity, False, warnings
        except model_class.DoesNotExist:
            pass

    # Try external_id match
    if inline_data.get("external_id"):
        try:
            entity = model_class.objects.get(
                organization=organization, external_id=inline_data["external_id"]
            )
            warnings.extend(_compare_data(to_en16931_fn(entity), inline_data, label))
            return entity, False, warnings
        except model_class.DoesNotExist:
            # Auto-create if external_id is the match key (for Customer)
            if ("external_id", "external_id") in match_fields:
                return create_fn(organization, inline_data), True, warnings

    # Try additional match fields (siren, etc.)
    for data_key, model_field in match_fields:
        if data_key == "external_id":
            continue  # Already handled above
        if inline_data.get(data_key):
            try:
                entity = model_class.objects.get(
                    organization=organization, **{model_field: inline_data[data_key]}
                )
                warnings.extend(
                    _compare_data(to_en16931_fn(entity), inline_data, label)
                )
                return entity, False, warnings
            except model_class.DoesNotExist:
                return create_fn(organization, inline_data), True, warnings

    raise ValueError(
        f"Inline {label} must have a uuid, external_id, or siren for matching."
    )


def _create_supplier(organization, data):
    """Auto-create a Supplier from inline data."""
    return Supplier.objects.create(
        organization=organization,
        name=data.get("name", ""),
        siren=data.get("siren", ""),
        siret=data.get("siret", ""),
        vat_number=data.get("vatNumber", ""),
        email=data.get("email", ""),
        address=data.get("postalAddress", {}),
        contact=data.get("contact", {}),
        electronic_address=data.get("electronicAddress", {}),
        external_id=data.get("external_id"),
    )


def _create_customer(organization, data):
    """Auto-create a Customer from inline data."""
    return Customer.objects.create(
        organization=organization,
        name=data.get("name", ""),
        external_id=data.get("external_id"),
        siren=data.get("siren", ""),
        siret=data.get("siret", ""),
        vat_number=data.get("vatNumber", ""),
        email=data.get("email", ""),
        address=data.get("postalAddress", {}),
        contact=data.get("contact", {}),
        electronic_address=data.get("electronicAddress", {}),
    )


def _entity_to_en16931(entity, extra_fields=None):
    """Convert a Supplier or Customer model instance to EN16931 data.

    Shared fields: name, siren, siret, vatNumber, email, postalAddress, contact,
    electronicAddress (with SIREN fallback).
    extra_fields: list of (model_attr, en16931_key) for model-specific fields.
    """
    data = {"name": entity.name}
    for attr, key in (
        ("siren", "siren"),
        ("siret", "siret"),
        ("vat_number", "vatNumber"),
        ("email", "email"),
    ):
        value = getattr(entity, attr, None)
        if value:
            data[key] = value
    if entity.address:
        data["postalAddress"] = dict(entity.address)
    if entity.contact:
        data["contact"] = entity.contact

    # electronicAddress (BT-49) — use stored value, fallback SIREN, then VAT number
    ea = entity.electronic_address or {}
    if ea.get("identifier"):
        data["electronicAddress"] = {
            "identifier": ea["identifier"],
            "schemeId": ea.get("schemeId", AFNOR_SCHEME_ID),
        }
    elif entity.siren:
        data["electronicAddress"] = {
            "identifier": entity.siren,
            "schemeId": AFNOR_SCHEME_ID,
        }
    elif getattr(entity, "vat_number", None):
        data["electronicAddress"] = {
            "identifier": entity.vat_number,
            "schemeId": "0088",
        }

    for attr, key in extra_fields or []:
        value = getattr(entity, attr, None)
        if value:
            data[key] = value

    return data


def _supplier_to_en16931(supplier):
    """Convert a Supplier model instance to EN16931 supplier data."""
    return _entity_to_en16931(
        supplier,
        extra_fields=[
            ("iban", "iban"),
            ("bic", "bic"),
            ("legal_description", "legalDescription"),
        ],
    )


def _customer_to_en16931(customer):
    """Convert a Customer model instance to EN16931 recipient data."""
    return _entity_to_en16931(customer)


def resolve_supplier(organization, payload):
    """Resolve supplier from payload. Returns (Supplier, en16931_supplier_data, warnings).

    Raises ValueError on validation errors.
    """
    supplier_id = payload.get("supplier_id")
    supplier_inline = payload.get("supplier")
    supplier_override = payload.get("supplier_override")
    warnings = []

    # Exclusivity check
    if supplier_id and supplier_inline:
        raise ValueError(
            "Cannot provide both supplier_id and supplier in the same payload."
        )
    if supplier_inline and supplier_override:
        raise ValueError("Cannot provide supplier_override with inline supplier data.")

    if supplier_id:
        # Referenced pattern
        supplier = _lookup_supplier(organization, supplier_id)
        supplier_data = _supplier_to_en16931(supplier)
        if supplier_override:
            supplier_data = deep_merge(supplier_data, supplier_override)
        return supplier, supplier_data, warnings

    elif supplier_inline:
        # Inline pattern
        supplier, created, match_warnings = _resolve_inline_supplier(
            organization, supplier_inline
        )
        warnings.extend(match_warnings)
        # In inline mode, the payload data is used as-is for en16931_data
        return supplier, supplier_inline, warnings

    else:
        raise ValueError("Either supplier_id or supplier must be provided.")


def _lookup_supplier(organization, identifier):
    """Look up supplier by UUID, external_id, or 'default' keyword."""
    return _lookup_by_identifier(Supplier, organization, identifier, "Supplier")


def _resolve_inline_supplier(organization, supplier_data):
    """Match or auto-create a Supplier from inline data."""
    return _resolve_inline_entity(
        Supplier,
        organization,
        supplier_data,
        _supplier_to_en16931,
        "supplier",
        match_fields=[("siren", "siren")],
        create_fn=_create_supplier,
    )


def resolve_customer(organization, payload):
    """Resolve customer/recipient from payload. Returns (Customer|None, en16931_recipient_data, warnings).

    Raises ValueError on validation errors.
    """
    customer_id = payload.get("customer_id")
    recipient_inline = payload.get("recipient")
    customer_override = payload.get("customer_override")
    warnings = []

    # Exclusivity check
    if customer_id and recipient_inline:
        raise ValueError(
            "Cannot provide both customer_id and recipient in the same payload."
        )
    if recipient_inline and customer_override:
        raise ValueError("Cannot provide customer_override with inline recipient data.")

    if customer_id:
        # Referenced pattern
        customer = _lookup_customer(organization, customer_id)
        recipient_data = _customer_to_en16931(customer)
        if customer_override:
            recipient_data = deep_merge(recipient_data, customer_override)
        return customer, recipient_data, warnings

    elif recipient_inline:
        # Inline pattern
        customer, created, match_warnings = _resolve_inline_customer(
            organization, recipient_inline
        )
        warnings.extend(match_warnings)
        return customer, recipient_inline, warnings

    else:
        # No customer/recipient — allowed (minimal payload)
        return None, None, warnings


def _lookup_customer(organization, identifier):
    """Look up customer by UUID or external_id."""
    return _lookup_by_identifier(Customer, organization, identifier, "Customer")


def _resolve_inline_customer(organization, recipient_data):
    """Match or auto-create a Customer from inline recipient data."""
    return _resolve_inline_entity(
        Customer,
        organization,
        recipient_data,
        _customer_to_en16931,
        "recipient",
        match_fields=[("external_id", "external_id"), ("siren", "siren")],
        create_fn=_create_customer,
    )


def resolve_product_lines(organization, en16931_data):
    """Resolve product_id references in invoiceLines.

    Modifies en16931_data in place, injecting product defaults for lines
    that contain a product_id.
    """
    lines = en16931_data.get("invoiceLines", [])
    for i, line in enumerate(lines):
        product_id = line.pop("product_id", None)
        if not product_id:
            continue

        product = _lookup_product(organization, product_id)

        # Inject defaults — explicit fields in the line override product defaults
        defaults = {}
        if product.name:
            defaults["itemName"] = product.name
        if product.default_unit_price is not None:
            defaults["unitNetPrice"] = str(product.default_unit_price)
        if product.default_vat_rate is not None:
            defaults["manualVatRate"] = str(product.default_vat_rate)
        if product.default_vat_category:
            defaults["vatCategory"] = product.default_vat_category
        if product.default_unit:
            defaults["quantityUnit"] = product.default_unit
        if product.reference:
            defaults["itemReference"] = product.reference
        if product.description:
            defaults["itemDescription"] = product.description

        # Merge: explicit line fields override product defaults
        merged = {**defaults, **line}
        lines[i] = merged


def _lookup_product(organization, identifier):
    """Look up product by UUID or external_id."""
    return _lookup_by_identifier(Product, organization, identifier, "Product")
