from django.urls import path

from apps.payments.views import (
    CheckoutView,
    PaymentStatusView,
    ProviderConfigView,
    fintecture_webhook,
    gocardless_webhook,
    stripe_webhook,
)

urlpatterns = [
    # Checkout & status
    path(
        "invoices/<uuid:uuid>/checkout/",
        CheckoutView.as_view(),
        name="payment-checkout",
    ),
    path(
        "invoices/<uuid:uuid>/status/",
        PaymentStatusView.as_view(),
        name="payment-status",
    ),
    # Provider configuration
    path("providers/", ProviderConfigView.as_view(), name="payment-providers"),
    # Inbound webhooks (no JWT — signature verified)
    path("webhooks/stripe/", stripe_webhook, name="stripe-webhook"),
    path("webhooks/gocardless/", gocardless_webhook, name="gocardless-webhook"),
    path("webhooks/fintecture/", fintecture_webhook, name="fintecture-webhook"),
]
