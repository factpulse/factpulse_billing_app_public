from django.contrib import admin

from .models import (
    Customer,
    IdempotencyKey,
    Invoice,
    InvoiceAuditLog,
    NumberingCounter,
    NumberingSequence,
    Product,
    Supplier,
)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "siren", "organization", "is_default", "created_at")
    list_filter = ("organization", "is_default")
    search_fields = ("name", "siren", "external_id")


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "siren", "organization", "created_at")
    list_filter = ("organization",)
    search_fields = ("name", "siren", "external_id")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "reference", "organization", "default_unit_price")
    list_filter = ("organization",)
    search_fields = ("name", "reference", "external_id")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "number",
        "status",
        "supplier",
        "customer",
        "total_incl_tax",
        "issue_date",
        "created_at",
    )
    list_filter = ("status", "organization", "supplier")
    search_fields = ("number", "external_id", "uuid")
    readonly_fields = ("uuid", "version", "created_at", "updated_at")


@admin.register(InvoiceAuditLog)
class InvoiceAuditLogAdmin(admin.ModelAdmin):
    list_display = ("invoice", "action", "old_status", "new_status", "timestamp")
    list_filter = ("action",)


@admin.register(NumberingSequence)
class NumberingSequenceAdmin(admin.ModelAdmin):
    list_display = ("supplier", "prefix_template", "padding")


@admin.register(NumberingCounter)
class NumberingCounterAdmin(admin.ModelAdmin):
    list_display = ("sequence", "resolved_prefix", "last_number")


@admin.register(IdempotencyKey)
class IdempotencyKeyAdmin(admin.ModelAdmin):
    list_display = ("key", "organization", "invoice", "created_at")
