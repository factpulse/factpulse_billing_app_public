from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.webhooks.views import WebhookEndpointViewSet

router = DefaultRouter()
router.register("", WebhookEndpointViewSet, basename="webhook")

urlpatterns = [
    path("", include(router.urls)),
]
