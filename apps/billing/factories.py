"""Factory-boy factories for billing models."""

import factory

from apps.billing.models import (
    Customer,
    Invoice,
    NumberingSequence,
    Product,
    Supplier,
)
from apps.core.models import Organization


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Organization

    name = factory.Sequence(lambda n: f"Org {n}")
    slug = factory.Sequence(lambda n: f"org-{n}")


class SupplierFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Supplier

    organization = factory.SubFactory(OrganizationFactory)
    name = factory.Sequence(lambda n: f"Supplier {n}")
    siren = factory.Sequence(lambda n: f"{100000000 + n}")
    iban = "FR7630006000011234567890189"


class CustomerFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Customer

    organization = factory.SubFactory(OrganizationFactory)
    name = factory.Sequence(lambda n: f"Customer {n}")
    siren = factory.Sequence(lambda n: f"{200000000 + n}")


class ProductFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Product

    organization = factory.SubFactory(OrganizationFactory)
    name = factory.Sequence(lambda n: f"Product {n}")
    default_unit_price = factory.LazyFunction(
        lambda: __import__("decimal").Decimal("100.00")
    )
    default_vat_rate = factory.LazyFunction(
        lambda: __import__("decimal").Decimal("20.00")
    )


class NumberingSequenceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = NumberingSequence

    supplier = factory.SubFactory(SupplierFactory)
    prefix_template = "FACT-{{ issue_date|date:'Y' }}-"
    padding = 3


class InvoiceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Invoice

    organization = factory.LazyAttribute(lambda o: o.supplier.organization)
    supplier = factory.SubFactory(SupplierFactory)
    en16931_data = factory.LazyFunction(dict)
