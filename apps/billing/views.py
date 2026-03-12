import logging
from datetime import timedelta

from django.http import FileResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.billing.filters import (
    CustomerFilter,
    InvoiceFilter,
    ProductFilter,
    SupplierFilter,
)
from apps.billing.models import (
    Customer,
    IdempotencyKey,
    Invoice,
    Product,
    Supplier,
)
from apps.billing.serializers import (
    CustomerSerializer,
    InvoiceAuditLogSerializer,
    InvoiceCreateSerializer,
    InvoiceReadSerializer,
    InvoiceUpdateSerializer,
    MarkPaidSerializer,
    ProductSerializer,
    SupplierSerializer,
)
from apps.billing.services import invoice_service
from apps.core.exceptions import ConflictError, UnprocessableError
from apps.core.permissions import HasOrganization, IsMember, IsOwner, IsViewer
from apps.core.services import invite_customer_user
from apps.factpulse.tasks import generate_source_pdf

logger = logging.getLogger(__name__)


class SupplierViewSet(viewsets.ModelViewSet):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    permission_classes = [HasOrganization, IsMember]
    filterset_class = SupplierFilter
    search_fields = ["name", "siren", "external_id"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]
    lookup_field = "uuid"

    def get_queryset(self):
        return super().get_queryset().filter(organization=self.request.organization)


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    permission_classes = [HasOrganization, IsMember]
    filterset_class = CustomerFilter
    search_fields = ["name", "siren", "external_id"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]
    lookup_field = "uuid"

    def get_queryset(self):
        return super().get_queryset().filter(organization=self.request.organization)

    @action(
        detail=True, methods=["post"], permission_classes=[HasOrganization, IsOwner]
    )
    def invite(self, request, uuid=None):
        """Invite a customer_access user by email."""
        customer = self.get_object()
        email = request.data.get("email")
        if not email:
            return Response(
                {
                    "error": {
                        "code": "validation_error",
                        "message": "Email is required.",
                        "details": [],
                    }
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        user, membership, created = invite_customer_user(
            organization=request.organization,
            customer=customer,
            email=email,
        )

        if not created:
            return Response(
                {"message": "User already has access to this organization."},
                status=status.HTTP_200_OK,
            )

        return Response(
            {"message": "Invitation sent.", "email": email},
            status=status.HTTP_201_CREATED,
        )


class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [HasOrganization, IsMember]
    filterset_class = ProductFilter
    search_fields = ["name", "reference", "external_id"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]
    lookup_field = "uuid"

    def get_queryset(self):
        return super().get_queryset().filter(organization=self.request.organization)


class InvoiceViewSet(viewsets.ModelViewSet):
    queryset = Invoice.objects.filter(deleted_at__isnull=True)
    permission_classes = [HasOrganization, IsViewer]
    filterset_class = InvoiceFilter
    search_fields = ["number", "customer__name"]
    ordering_fields = ["created_at", "issue_date", "total_incl_tax"]
    ordering = ["-created_at"]
    lookup_field = "uuid"

    def get_queryset(self):
        return super().get_queryset().filter(organization=self.request.organization)

    def get_serializer_class(self):
        if self.action == "create":
            return InvoiceCreateSerializer
        if self.action in ("partial_update", "update"):
            return InvoiceUpdateSerializer
        if self.action == "mark_paid":
            return MarkPaidSerializer
        return InvoiceReadSerializer

    def get_permissions(self):
        write_actions = (
            "create",
            "partial_update",
            "update",
            "destroy",
            "validate",
            "transmit",
            "mark_paid",
            "cancel",
        )
        if self.action in write_actions:
            return [HasOrganization(), IsMember()]
        return super().get_permissions()

    def create(self, request, *args, **kwargs):
        # Idempotency check
        idempotency_key = request.headers.get("Idempotency-Key")
        if idempotency_key:
            try:
                existing = IdempotencyKey.objects.get(
                    organization=request.organization,
                    key=idempotency_key,
                )
                # Check TTL (24h)
                if existing.created_at > timezone.now() - timedelta(hours=24):
                    return Response(
                        existing.response_data,
                        status=status.HTTP_201_CREATED,
                    )
                else:
                    existing.delete()
            except IdempotencyKey.DoesNotExist:
                pass

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            invoice, warnings = invoice_service.create_invoice(
                organization=request.organization,
                payload=serializer.validated_data,
                user=request.user,
            )
        except ValueError as e:
            raise UnprocessableError(detail=str(e)) from None

        response_data = InvoiceReadSerializer(invoice).data
        if warnings:
            response_data["warnings"] = warnings

        # Store idempotency key
        if idempotency_key:
            IdempotencyKey.objects.create(
                key=idempotency_key,
                organization=request.organization,
                invoice=invoice,
                response_data=response_data,
            )

        return Response(response_data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        invoice = self.get_object()

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            invoice, warnings = invoice_service.update_invoice(
                invoice=invoice,
                payload=serializer.validated_data,
                user=request.user,
            )
        except ValueError as e:
            raise UnprocessableError(detail=str(e)) from None

        response_data = InvoiceReadSerializer(invoice).data
        if warnings:
            response_data["warnings"] = warnings

        return Response(response_data)

    def destroy(self, request, *args, **kwargs):
        invoice = self.get_object()
        try:
            invoice_service.soft_delete(
                invoice,
                user=request.user,
            )
        except ConflictError:
            raise
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="validate")
    def validate_invoice(self, request, uuid=None):
        invoice = self.get_object()
        try:
            invoice = invoice_service.validate_invoice(
                invoice,
                user=request.user,
            )
        except ConflictError:
            raise
        return Response(InvoiceReadSerializer(invoice).data)

    @action(detail=True, methods=["post"])
    def transmit(self, request, uuid=None):
        invoice = self.get_object()
        try:
            invoice = invoice_service.transmit_invoice(
                invoice,
                user=request.user,
            )
        except ConflictError:
            raise
        return Response(InvoiceReadSerializer(invoice).data)

    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, uuid=None):
        invoice = self.get_object()
        serializer = MarkPaidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            invoice = invoice_service.mark_paid(
                invoice,
                payment_data=serializer.validated_data,
                user=request.user,
            )
        except ConflictError:
            raise
        return Response(InvoiceReadSerializer(invoice).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, uuid=None):
        invoice = self.get_object()
        try:
            credit_note = invoice_service.cancel_invoice(
                invoice,
                user=request.user,
            )
        except ConflictError:
            raise
        return Response(
            InvoiceReadSerializer(credit_note).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"])
    def pdf(self, request, uuid=None):
        """Serve the invoice PDF.

        - validated+: returns Factur-X PDF
        - draft: returns source PDF (WeasyPrint) or 202 if not yet generated
        """
        invoice = self.get_object()

        if invoice.pdf_file:
            response = FileResponse(
                invoice.pdf_file.open("rb"),
                content_type="application/pdf",
            )
            pdf_type = "facturx" if invoice.status != Invoice.Status.DRAFT else "source"
            response["X-PDF-Type"] = pdf_type
            response["Content-Disposition"] = (
                f'attachment; filename="{invoice.number or invoice.uuid}.pdf"'
            )
            return response

        if invoice.status == Invoice.Status.DRAFT:
            generate_source_pdf.delay(str(invoice.uuid))
            return Response(
                {"message": "PDF is being generated."},
                status=status.HTTP_202_ACCEPTED,
                headers={"Location": request.build_absolute_uri()},
            )

        return Response(
            {
                "error": {
                    "code": "not_found",
                    "message": "PDF not available yet.",
                    "details": [],
                }
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    @action(detail=True, methods=["get"], url_path="audit-log")
    def audit_log(self, request, uuid=None):
        invoice = self.get_object()
        logs = invoice.audit_logs.all()
        serializer = InvoiceAuditLogSerializer(logs, many=True)
        return Response(serializer.data)
