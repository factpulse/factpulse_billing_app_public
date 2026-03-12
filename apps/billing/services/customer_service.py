"""Customer service — enrichment and lifecycle operations.

Same pattern as payload_builder.py: fills missing fields with sensible
defaults so that API, UI, and MCP all get the same behaviour with
minimal payloads.
"""

from apps.billing.constants import AFNOR_SCHEME_ID


def enrich_customer_data(data):
    """Fill missing customer fields in place.

    Handles:
    - customer_type auto-detection from siren / vat_number / country
    - address dict from flat fields (address_line1, address_city, …)
    - electronic_address from a bare identifier string

    Never overwrites fields that are already set.
    """
    _enrich_customer_type(data)
    _enrich_address(data)
    _enrich_electronic_address(data)
    return data


def _enrich_customer_type(data):
    """Auto-detect customer_type when not explicitly provided."""
    if data.get("customer_type"):
        return

    from apps.billing.services.flow_detector import suggest_customer_type

    # Extract country from address dict or flat field
    address = data.get("address")
    country = ""
    if isinstance(address, dict):
        country = address.get("countryCode", "")
    if not country:
        country = data.pop("address_country", "") or "FR"

    data["customer_type"] = suggest_customer_type(
        siren=data.get("siren", ""),
        vat_number=data.get("vat_number", ""),
        country_code=country,
    )


def _enrich_address(data):
    """Build address dict from flat fields if no address dict provided."""
    if data.get("address"):
        return

    flat_keys = ("address_line1", "address_postcode", "address_city", "address_country")
    if not any(data.get(k) for k in flat_keys):
        return

    data["address"] = {
        "lineOne": data.pop("address_line1", ""),
        "postalCode": data.pop("address_postcode", ""),
        "city": data.pop("address_city", ""),
        "countryCode": data.pop("address_country", "FR"),
    }


def _enrich_electronic_address(data):
    """Build electronic_address dict from a bare identifier string."""
    if data.get("electronic_address"):
        return

    ea_id = data.pop("electronic_address_id", "")
    if ea_id:
        data["electronic_address"] = {"identifier": ea_id, "schemeId": AFNOR_SCHEME_ID}
