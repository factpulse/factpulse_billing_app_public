from rest_framework import serializers

from apps.payments.models import PaymentTransaction, ProviderConfig


class ProviderConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProviderConfig
        fields = [
            "uuid",
            "provider",
            "is_active",
            "default_supplier",
            "config",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["uuid", "created_at", "updated_at"]


class ProviderConfigCreateSerializer(serializers.Serializer):
    provider = serializers.CharField(max_length=50)
    api_key = serializers.CharField(max_length=512)
    webhook_secret = serializers.CharField(max_length=512, required=False, default="")
    default_supplier = serializers.UUIDField(required=False, allow_null=True)
    config = serializers.JSONField(required=False, default=dict)


class CheckoutRequestSerializer(serializers.Serializer):
    provider = serializers.CharField(max_length=50, default="stripe")
    success_url = serializers.URLField(required=False)
    cancel_url = serializers.URLField(required=False)


class PaymentTransactionSerializer(serializers.ModelSerializer):
    invoice_uuid = serializers.UUIDField(source="invoice.uuid", read_only=True)

    class Meta:
        model = PaymentTransaction
        fields = [
            "uuid",
            "invoice_uuid",
            "provider",
            "amount",
            "currency",
            "payment_method",
            "status",
            "checkout_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
