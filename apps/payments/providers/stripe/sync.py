"""Stripe data sync — list and import customers, products, invoices into FactPulse."""

import logging
from decimal import Decimal

import stripe

from apps.billing.models import Customer, Product
from apps.billing.services.customer_service import enrich_customer_data

logger = logging.getLogger(__name__)


def list_customers(api_key):
    """Fetch all Stripe customers (auto-paginated)."""
    return list(stripe.Customer.list(api_key=api_key).auto_paging_iter())


def list_products(api_key):
    """Fetch all active Stripe products (auto-paginated, with default_price expanded)."""
    return list(
        stripe.Product.list(
            api_key=api_key,
            active=True,
            expand=["data.default_price"],
        ).auto_paging_iter()
    )


def list_invoices(api_key, limit=25, starting_after=None):
    """Fetch one page of Stripe invoices. Returns (invoices, has_more)."""
    params = {"limit": limit, "expand": ["data.customer"]}
    if starting_after:
        params["starting_after"] = starting_after
    page = stripe.Invoice.list(api_key=api_key, **params)
    return list(page.data), page.has_more


def import_customer(org, stripe_customer):
    """Upsert a FactPulse Customer from a Stripe Customer object/dict.

    Returns (customer, created).
    """
    sid = stripe_customer["id"]
    address_raw = stripe_customer.get("address") or {}
    tax_ids_data = (stripe_customer.get("tax_ids") or {}).get("data") or []

    data = {
        "name": stripe_customer.get("name") or sid,
        "email": stripe_customer.get("email") or "",
        "external_id": sid,
    }
    if address_raw:
        data["address"] = {
            "lineOne": address_raw.get("line1") or "",
            "postalCode": address_raw.get("postal_code") or "",
            "city": address_raw.get("city") or "",
            "countryCode": address_raw.get("country") or "FR",
        }
    for tid in tax_ids_data:
        val = tid.get("value") if isinstance(tid, dict) else getattr(tid, "value", "")
        if val:
            data["vat_number"] = val
            break

    enrich_customer_data(data)

    customer, created = Customer.objects.update_or_create(
        organization=org,
        external_id=sid,
        defaults={
            "name": data["name"],
            "email": data.get("email", ""),
            "address": data.get("address", {}),
            "vat_number": data.get("vat_number", ""),
            "customer_type": data.get(
                "customer_type", Customer.CustomerType.ASSUJETTI_FR
            ),
        },
    )
    return customer, created


def import_customer_from_invoice(org, stripe_invoice):
    """Create or update a Customer from inline data embedded in a Stripe invoice.

    Handles both expanded customer objects and flat customer_* fields.
    Returns the Customer instance, or None if no customer ID found.
    """
    customer_field = stripe_invoice.get("customer")

    # Expanded customer object
    if isinstance(customer_field, dict) and customer_field.get("id"):
        customer, _ = import_customer(org, customer_field)
        return customer

    sid = customer_field if isinstance(customer_field, str) else ""
    if not sid:
        return None

    address_raw = stripe_invoice.get("customer_address") or {}
    tax_ids = stripe_invoice.get("customer_tax_ids") or []

    data = {
        "name": stripe_invoice.get("customer_name") or sid,
        "email": stripe_invoice.get("customer_email") or "",
        "external_id": sid,
    }
    if address_raw:
        data["address"] = {
            "lineOne": address_raw.get("line1") or "",
            "postalCode": address_raw.get("postal_code") or "",
            "city": address_raw.get("city") or "",
            "countryCode": address_raw.get("country") or "FR",
        }
    for tid in tax_ids:
        val = tid.get("value") if isinstance(tid, dict) else getattr(tid, "value", "")
        if val:
            data["vat_number"] = val
            break

    enrich_customer_data(data)

    customer, _ = Customer.objects.update_or_create(
        organization=org,
        external_id=sid,
        defaults={
            "name": data["name"],
            "email": data.get("email", ""),
            "address": data.get("address", {}),
            "vat_number": data.get("vat_number", ""),
            "customer_type": data.get(
                "customer_type", Customer.CustomerType.ASSUJETTI_FR
            ),
        },
    )
    return customer


def import_product(org, stripe_product):
    """Upsert a FactPulse Product from a Stripe Product object/dict.

    Returns (product, created).
    """
    sid = stripe_product["id"]

    default_unit_price = None
    default_price = stripe_product.get("default_price")
    if isinstance(default_price, dict):
        unit_amount = default_price.get("unit_amount")
        if unit_amount is not None:
            default_unit_price = Decimal(str(unit_amount)) / 100

    defaults = {
        "name": stripe_product.get("name") or sid,
        "description": stripe_product.get("description") or "",
    }
    if default_unit_price is not None:
        defaults["default_unit_price"] = default_unit_price

    product, created = Product.objects.update_or_create(
        organization=org,
        external_id=sid,
        defaults=defaults,
    )
    return product, created
