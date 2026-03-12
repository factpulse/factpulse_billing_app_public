import django_filters
from django.db import models

from apps.billing.models import Customer, Invoice, Product, Supplier


class InvoiceFilter(django_filters.FilterSet):
    status = django_filters.CharFilter(field_name="status")
    supplier = django_filters.CharFilter(method="filter_supplier")
    customer = django_filters.CharFilter(method="filter_customer")
    date_from = django_filters.DateFilter(field_name="issue_date", lookup_expr="gte")
    date_to = django_filters.DateFilter(field_name="issue_date", lookup_expr="lte")

    class Meta:
        model = Invoice
        fields = ["status"]

    def filter_supplier(self, queryset, name, value):
        """Filter by supplier UUID or external_id."""
        return queryset.filter(
            models.Q(supplier__uuid=value) | models.Q(supplier__external_id=value)
        )

    def filter_customer(self, queryset, name, value):
        """Filter by customer UUID or external_id."""
        return queryset.filter(
            models.Q(customer__uuid=value) | models.Q(customer__external_id=value)
        )


class SupplierFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = Supplier
        fields = ["siren"]

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            models.Q(name__icontains=value)
            | models.Q(siren__icontains=value)
            | models.Q(external_id__icontains=value)
        )


class CustomerFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = Customer
        fields = ["siren"]

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            models.Q(name__icontains=value)
            | models.Q(siren__icontains=value)
            | models.Q(external_id__icontains=value)
        )


class ProductFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search")

    class Meta:
        model = Product
        fields = []

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            models.Q(name__icontains=value)
            | models.Q(reference__icontains=value)
            | models.Q(external_id__icontains=value)
        )
