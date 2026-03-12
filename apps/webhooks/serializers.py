from rest_framework import serializers

from apps.webhooks.models import WebhookDelivery, WebhookEndpoint


class WebhookEndpointSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookEndpoint
        fields = [
            "uuid",
            "url",
            "secret",
            "events",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["uuid", "created_at", "updated_at"]
        extra_kwargs = {"secret": {"write_only": True}}

    def create(self, validated_data):
        validated_data["organization"] = self.context["request"].organization
        return super().create(validated_data)


class WebhookDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookDelivery
        fields = [
            "uuid",
            "event",
            "payload",
            "http_status",
            "response_body",
            "success",
            "attempt",
            "created_at",
        ]
        read_only_fields = fields
