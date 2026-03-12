from django.contrib import admin

from .models import WebhookDelivery, WebhookEndpoint


@admin.register(WebhookEndpoint)
class WebhookEndpointAdmin(admin.ModelAdmin):
    list_display = ("url", "organization", "is_active", "created_at")
    list_filter = ("is_active", "organization")


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "event",
        "endpoint",
        "http_status",
        "success",
        "attempt",
        "created_at",
    )
    list_filter = ("success", "event")
