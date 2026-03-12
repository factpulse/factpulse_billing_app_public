"""Flow detector — determines the e-invoicing / e-reporting flow for an invoice.

Ref: XP Z12-012 §6.3.2 (BR-FR-20), XP Z12-014 §3.2

Based on the 2026 French e-invoicing reform (art. 289 bis CGI):
- B2B domestic → e-facturation via PA (processingRule "B2B")
- B2C → e-reporting flux 10.3/10.4 (processingRule "B2C")
- B2B intra-EU → e-reporting flux 10.1/10.2 (processingRule "B2Bint")
- B2B extra-EU → e-reporting flux 10.1/10.2 (processingRule "B2Bint")
- B2G → Chorus Pro (processingRule "B2G")
"""

# EU member states (ISO 3166-1 alpha-2), excluding FR
EU_COUNTRY_CODES = frozenset(
    {
        "AT",
        "BE",
        "BG",
        "CY",
        "CZ",
        "DE",
        "DK",
        "EE",
        "ES",
        "FI",
        "GR",
        "HR",
        "HU",
        "IE",
        "IT",
        "LT",
        "LU",
        "LV",
        "MT",
        "NL",
        "PL",
        "PT",
        "RO",
        "SE",
        "SI",
        "SK",
    }
)

# detected_flow → AFNOR processingRule (ref XP Z12-013 §FlowInfo)
FLOW_TO_PROCESSING_RULE = {
    "b2b_domestic": "B2B",
    "b2c": "B2C",
    "b2b_intra_eu": "B2Bint",
    "b2b_extra_eu": "B2Bint",
    "b2g": "B2G",
}

# detected_flow → BAR note value (ref XP Z12-012 §BR-FR-20)
FLOW_TO_BAR_VALUE = {
    "b2b_domestic": "B2B",
    "b2c": "B2C",
    "b2b_intra_eu": "B2BINT",
    "b2b_extra_eu": "B2BINT",
    "b2g": "B2B",  # B2G uses B2B flow via PA routing
}

# operation_category → invoicing framework BT-23 (ref XP Z12-012 §6.3.1)
CATEGORY_TO_FRAMEWORK = {
    "TPS1": "S1",  # Services
    "TLB1": "B1",  # Goods
    "TMA1": "M1",  # Mixed
    "TNT1": "S1",  # Non-taxed defaults to services framework
}


def detect_flow(invoice):
    """Determine the applicable flow for an invoice.

    Priority:
    1. Customer.customer_type if a Customer record is linked
    2. Fallback: heuristic from en16931_data recipient fields
    """
    if invoice.customer and invoice.customer.customer_type:
        return _flow_from_customer_type(invoice.customer.customer_type)

    recipient = invoice.en16931_data.get("recipient", {})
    return _detect_from_recipient(recipient)


def suggest_customer_type(*, siren="", vat_number="", country_code=""):
    """Suggest a customer_type based on identifiers.

    Called at customer creation/update for auto-suggestion.
    """
    if siren:
        return "assujetti_fr"

    if vat_number:
        prefix = vat_number[:2].upper()
        if prefix == "FR":
            return "assujetti_fr"
        if prefix in EU_COUNTRY_CODES:
            return "intra_ue"
        return "extra_ue"

    country = (country_code or "").upper()
    if country and country != "FR":
        if country in EU_COUNTRY_CODES:
            return "intra_ue"
        return "extra_ue"

    # Default: French company (most common case for FactPulse users)
    return "assujetti_fr"


def inject_bar_note(en16931_data, detected_flow):
    """Inject the BAR note (BT-21/BT-22) into en16931_data.

    Ref: XP Z12-012 §BR-FR-20 — the BAR note tells the PA which flow applies.
    """
    bar_value = FLOW_TO_BAR_VALUE.get(detected_flow)
    if not bar_value:
        return

    notes = en16931_data.get("notes", [])

    # Don't duplicate if already present
    for note in notes:
        if note.get("subjectCode") == "BAR":
            return

    notes.append({"subjectCode": "BAR", "content": bar_value})
    en16931_data["notes"] = notes


def inject_framework(en16931_data, operation_category):
    """Inject the invoicing framework BT-23 into en16931_data.

    Ref: XP Z12-012 §6.3.1 — required for e-reporting.
    """
    framework = CATEGORY_TO_FRAMEWORK.get(operation_category)
    if not framework:
        return

    references = en16931_data.get("references", {})
    if not references.get("invoicingFramework"):
        references["invoicingFramework"] = framework
        en16931_data["references"] = references


# detected_flow → DGFiP e-reporting flux type (ref DGFiP v3.1, §10)
FLOW_TO_EREPORTING_FLUX = {
    "b2b_intra_eu": "10.1",  # Factures B2B international
    "b2b_extra_eu": "10.1",
    "b2c": "10.3",  # Transactions B2C agrégées
}


def is_ereporting_flow(detected_flow):
    """Return True if this flow requires e-reporting (not e-facturation)."""
    return detected_flow in FLOW_TO_EREPORTING_FLUX


def get_ereporting_flux_type(detected_flow):
    """Return the DGFiP flux type for e-reporting, or None."""
    return FLOW_TO_EREPORTING_FLUX.get(detected_flow)


def enrich_recipient_country(en16931_data, customer):
    """Inject recipient country (TT-39) from Customer if missing in en16931_data.

    Ref: DGFiP v3.1 — TT-39 is required for e-reporting.
    """
    if not customer or not customer.address:
        return

    recipient = en16931_data.get("recipient", {})
    address = recipient.get("postalAddress", {})
    if address.get("countryCode"):
        return

    customer_country = (customer.address.get("countryCode") or "").strip()
    if not customer_country:
        return

    if "postalAddress" not in recipient:
        recipient["postalAddress"] = {}
    recipient["postalAddress"]["countryCode"] = customer_country
    en16931_data["recipient"] = recipient


def _flow_from_customer_type(customer_type):
    """Map customer_type to detected_flow."""
    mapping = {
        "assujetti_fr": "b2b_domestic",
        "intra_ue": "b2b_intra_eu",
        "extra_ue": "b2b_extra_eu",
        "particulier": "b2c",
        "public": "b2g",
    }
    return mapping.get(customer_type, "b2c")


def _detect_from_recipient(recipient):
    """Heuristic flow detection from en16931_data recipient block."""
    siren = recipient.get("siren", "")
    vat_number = recipient.get("vatNumber", "")
    address = recipient.get("postalAddress", {})
    country = (address.get("countryCode") or "").upper()

    if siren:
        return "b2b_domestic"

    if vat_number:
        prefix = vat_number[:2].upper()
        if prefix == "FR":
            return "b2b_domestic"
        if prefix in EU_COUNTRY_CODES:
            return "b2b_intra_eu"
        return "b2b_extra_eu"

    if country:
        if country == "FR":
            return "b2c"
        if country in EU_COUNTRY_CODES:
            return "b2b_intra_eu"
        return "b2b_extra_eu"

    return "b2c"
