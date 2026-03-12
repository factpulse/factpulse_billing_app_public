"""Shared form helpers for UI views."""

from apps.billing.constants import AFNOR_SCHEME_ID


def build_address_from_post(post_data):
    """Build an address dict from POST form data."""
    return {
        "lineOne": post_data.get("address_line1", ""),
        "postalCode": post_data.get("address_postcode", ""),
        "city": post_data.get("address_city", ""),
        "countryCode": post_data.get("address_country", "FR"),
    }


def build_electronic_address(post_data):
    """Build an electronic address dict from POST form data."""
    ea_raw = post_data.get("electronic_address", "").strip()
    return {"identifier": ea_raw, "schemeId": AFNOR_SCHEME_ID} if ea_raw else {}
