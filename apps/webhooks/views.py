from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import HasOrganization, IsOwner
from apps.webhooks.models import WebhookEndpoint
from apps.webhooks.serializers import (
    WebhookDeliverySerializer,
    WebhookEndpointSerializer,
)


class WebhookEndpointViewSet(viewsets.ModelViewSet):
    queryset = WebhookEndpoint.objects.all()
    serializer_class = WebhookEndpointSerializer
    permission_classes = [HasOrganization, IsOwner]
    lookup_field = "uuid"
    ordering = ["-created_at"]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.organization:
            qs = qs.filter(organization=self.request.organization)
        return qs

    @action(detail=True, methods=["get"])
    def deliveries(self, request, uuid=None):
        endpoint = self.get_object()
        deliveries = endpoint.deliveries.all()[:50]
        serializer = WebhookDeliverySerializer(deliveries, many=True)
        return Response(serializer.data)
