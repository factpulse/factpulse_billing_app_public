"""Payment API views."""

import logging

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.models import Invoice
from apps.core.exceptions import UnprocessableError
from apps.core.permissions import HasOrganization, IsMember, IsOwner
from apps.payments import services as payment_service
from apps.payments.models import PaymentTransaction, ProviderConfig
from apps.payments.serializers import (
    CheckoutRequestSerializer,
    PaymentTransactionSerializer,
    ProviderConfigCreateSerializer,
    ProviderConfigSerializer,
)

logger = logging.getLogger(__name__)


class CheckoutView(APIView):
    """POST /api/v1/payments/invoices/{uuid}/checkout/

    Generate a checkout URL for an invoice.
    """

    permission_classes = [HasOrganization, IsMember]

    def post(self, request, uuid):
        invoice = Invoice.objects.filter(
            uuid=uuid,
            organization=request.organization,
            deleted_at__isnull=True,
        ).first()
        if not invoice:
            return Response(
                {"error": {"code": "not_found", "message": "Invoice not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = CheckoutRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Build default URLs from request
        base_url = request.build_absolute_uri("/").rstrip("/")
        default_success = f"{base_url}/invoices/{uuid}/?payment=success"
        default_cancel = f"{base_url}/invoices/{uuid}/"

        txn = payment_service.create_checkout(
            organization=request.organization,
            invoice=invoice,
            success_url=serializer.validated_data.get("success_url", default_success),
            cancel_url=serializer.validated_data.get("cancel_url", default_cancel),
            provider=serializer.validated_data["provider"],
        )

        return Response(
            {
                "checkout_url": txn.checkout_url,
                "transaction": PaymentTransactionSerializer(txn).data,
            },
            status=status.HTTP_201_CREATED,
        )


class PaymentStatusView(APIView):
    """GET /api/v1/payments/invoices/{uuid}/status/

    Get payment status for an invoice.
    """

    permission_classes = [HasOrganization, IsMember]

    def get(self, request, uuid):
        transactions = PaymentTransaction.objects.filter(
            invoice__uuid=uuid,
            organization=request.organization,
        ).order_by("-created_at")

        return Response(
            PaymentTransactionSerializer(transactions, many=True).data,
        )


class ProviderConfigView(APIView):
    """GET/POST /api/v1/payments/providers/"""

    permission_classes = [HasOrganization, IsOwner]

    def get(self, request):
        configs = ProviderConfig.objects.filter(organization=request.organization)
        return Response(ProviderConfigSerializer(configs, many=True).data)

    def post(self, request):
        serializer = ProviderConfigCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Resolve default_supplier if provided
        default_supplier = None
        if data.get("default_supplier"):
            from apps.billing.models import Supplier

            default_supplier = Supplier.objects.filter(
                uuid=data["default_supplier"],
                organization=request.organization,
            ).first()

        config, created = ProviderConfig.objects.update_or_create(
            organization=request.organization,
            provider=data["provider"],
            defaults={
                "api_key": data["api_key"],
                "webhook_secret": data.get("webhook_secret", ""),
                "default_supplier": default_supplier,
                "config": data.get("config", {}),
                "is_active": True,
            },
        )

        return Response(
            ProviderConfigSerializer(config).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


def _handle_provider_webhook(request, provider_name, signature_header):
    """Generic webhook handler for any provider."""
    configs = ProviderConfig.objects.filter(
        provider=provider_name,
        is_active=True,
    ).exclude(webhook_secret="")  # nosec B106

    body = request.body
    headers = {
        signature_header: request.META.get(
            f"HTTP_{signature_header.upper().replace('-', '_')}", ""
        )
    }

    for config in configs:
        try:
            processed = payment_service.handle_webhook(
                provider=provider_name,
                provider_config=config,
                headers=headers,
                body=body,
            )
            return Response(
                {"received": True, "processed": processed},
                status=status.HTTP_200_OK,
            )
        except UnprocessableError:
            continue

    logger.warning(
        "No valid %s webhook secret found for incoming event.", provider_name
    )
    return Response(
        {"error": {"code": "invalid_signature", "message": "Invalid signature."}},
        status=status.HTTP_400_BAD_REQUEST,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def stripe_webhook(request):
    """POST /api/v1/payments/webhooks/stripe/

    Inbound Stripe webhook. No JWT auth — verified via HMAC signature.
    """
    return _handle_provider_webhook(request, "stripe", "Stripe-Signature")


@api_view(["POST"])
@permission_classes([AllowAny])
def gocardless_webhook(request):
    """POST /api/v1/payments/webhooks/gocardless/

    Inbound GoCardless webhook. Verified via HMAC-SHA256 signature.
    """
    return _handle_provider_webhook(request, "gocardless", "Webhook-Signature")


@api_view(["POST"])
@permission_classes([AllowAny])
def fintecture_webhook(request):
    """POST /api/v1/payments/webhooks/fintecture/

    Inbound Fintecture webhook. Verified via HMAC signature.
    """
    return _handle_provider_webhook(request, "fintecture", "Signature")
