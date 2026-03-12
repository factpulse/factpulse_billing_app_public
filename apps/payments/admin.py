from django.contrib import admin

from apps.payments.models import PaymentEventLog, PaymentTransaction, ProviderConfig


@admin.register(ProviderConfig)
class ProviderConfigAdmin(admin.ModelAdmin):
    list_display = ["provider", "organization", "is_active", "created_at"]
    list_filter = ["provider", "is_active"]
    readonly_fields = ["uuid"]


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = [
        "provider",
        "invoice",
        "amount",
        "currency",
        "status",
        "created_at",
    ]
    list_filter = ["provider", "status"]
    readonly_fields = ["uuid"]
    search_fields = ["provider_payment_id", "invoice__number"]


@admin.register(PaymentEventLog)
class PaymentEventLogAdmin(admin.ModelAdmin):
    list_display = [
        "provider",
        "event_type",
        "provider_event_id",
        "processed",
        "created_at",
    ]
    list_filter = ["provider", "event_type", "processed"]
    readonly_fields = ["provider_event_id"]
