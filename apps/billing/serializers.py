import re
import uuid

from rest_framework import serializers

from apps.billing.models import (
    Customer,
    Invoice,
    InvoiceAuditLog,
    Product,
    Supplier,
)


def validate_external_id(value):
    """Validate external_id format: must start with letter or underscore, UUID forbidden."""
    if value is None:
        return value
    # Check UUID format
    try:
        uuid.UUID(str(value))
        raise serializers.ValidationError("external_id cannot be in UUID format.")
    except ValueError:
        pass
    if not re.match(r"^[a-zA-Z_]", str(value)):
        raise serializers.ValidationError(
            "external_id must start with a letter or underscore."
        )
    return value


# --- Supplier ---


class OrganizationCreateMixin:
    """Mixin that injects organization from request context on create."""

    def create(self, validated_data):
        validated_data["organization"] = self.context["request"].organization
        return super().create(validated_data)


class SupplierSerializer(OrganizationCreateMixin, serializers.ModelSerializer):
    external_id = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        validators=[validate_external_id],
    )

    class Meta:
        model = Supplier
        fields = [
            "uuid",
            "name",
            "siren",
            "siret",
            "vat_number",
            "iban",
            "bic",
            "email",
            "address",
            "contact",
            "electronic_address",
            "legal_description",
            "external_id",
            "is_default",
            "logo",
            "primary_color",
            "pdf_legal_mentions",
            "note_pmt",
            "note_pmd",
            "note_aab",
            "vat_regime",
            "payment_terms_days",
            "payment_terms_end_of_month",
            "archived",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["uuid", "created_at", "updated_at"]


# --- Customer ---


class CustomerSerializer(OrganizationCreateMixin, serializers.ModelSerializer):
    external_id = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        validators=[validate_external_id],
    )

    class Meta:
        model = Customer
        fields = [
            "uuid",
            "name",
            "siren",
            "siret",
            "vat_number",
            "customer_type",
            "email",
            "address",
            "contact",
            "electronic_address",
            "external_id",
            "archived",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["uuid", "created_at", "updated_at"]

    def create(self, validated_data):
        from apps.billing.services.customer_service import enrich_customer_data

        enrich_customer_data(validated_data)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        from apps.billing.services.customer_service import enrich_customer_data

        enrich_customer_data(validated_data)
        return super().update(instance, validated_data)


# --- Product ---


class ProductSerializer(OrganizationCreateMixin, serializers.ModelSerializer):
    external_id = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        validators=[validate_external_id],
    )

    class Meta:
        model = Product
        fields = [
            "uuid",
            "name",
            "description",
            "reference",
            "external_id",
            "default_unit_price",
            "default_vat_rate",
            "default_vat_category",
            "default_unit",
            "archived",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["uuid", "created_at", "updated_at"]


# --- Invoice ---


class InvoiceCreateSerializer(serializers.Serializer):
    """Envelope serializer for invoice creation."""

    # Resolution fields (top-level)
    supplier_id = serializers.CharField(required=False)
    supplier = serializers.JSONField(required=False)
    supplier_override = serializers.JSONField(required=False)
    customer_id = serializers.CharField(required=False)
    recipient = serializers.JSONField(required=False)
    customer_override = serializers.JSONField(required=False)

    # EN16931 data
    en16931_data = serializers.JSONField(
        required=False,
        default=dict,
        help_text=(
            "Factur-X / EN 16931 invoice data. "
            "See full model: "
            "https://factpulse.fr/api/facturation/scalar#model/facturxinvoice"
        ),
    )

    # Extra fields
    external_id = serializers.CharField(
        required=False,
        allow_null=True,
        validators=[validate_external_id],
    )
    is_internal = serializers.BooleanField(required=False, default=False)

    def validate(self, data):
        # At least supplier_id or supplier must be provided
        if not data.get("supplier_id") and not data.get("supplier"):
            raise serializers.ValidationError(
                {"supplier_id": "Either supplier_id or supplier is required."}
            )

        # Exclusivity checks
        if data.get("supplier_id") and data.get("supplier"):
            raise serializers.ValidationError(
                "Cannot provide both supplier_id and supplier."
            )
        if data.get("customer_id") and data.get("recipient"):
            raise serializers.ValidationError(
                "Cannot provide both customer_id and recipient."
            )
        if data.get("supplier") and data.get("supplier_override"):
            raise serializers.ValidationError(
                "Cannot provide supplier_override with inline supplier."
            )
        if data.get("recipient") and data.get("customer_override"):
            raise serializers.ValidationError(
                "Cannot provide customer_override with inline recipient."
            )

        return data


class InvoiceUpdateSerializer(serializers.Serializer):
    """Envelope serializer for invoice update (PATCH)."""

    version = serializers.IntegerField(required=True)

    # Same resolution fields as create
    supplier_id = serializers.CharField(required=False)
    supplier = serializers.JSONField(required=False)
    supplier_override = serializers.JSONField(required=False)
    customer_id = serializers.CharField(required=False)
    recipient = serializers.JSONField(required=False)
    customer_override = serializers.JSONField(required=False)

    # EN16931 data (partial update)
    en16931_data = serializers.JSONField(
        required=False,
        help_text=(
            "Factur-X / EN 16931 data (partial update: provided keys "
            "replace existing ones). "
            "See full model: "
            "https://factpulse.fr/api/facturation/scalar#model/facturxinvoice"
        ),
    )

    external_id = serializers.CharField(
        required=False,
        allow_null=True,
        validators=[validate_external_id],
    )
    is_internal = serializers.BooleanField(required=False)

    def validate(self, data):
        if data.get("supplier_id") and data.get("supplier"):
            raise serializers.ValidationError(
                "Cannot provide both supplier_id and supplier."
            )
        if data.get("customer_id") and data.get("recipient"):
            raise serializers.ValidationError(
                "Cannot provide both customer_id and recipient."
            )
        return data


class InvoiceReadSerializer(serializers.ModelSerializer):
    """Output serializer for invoice detail/list."""

    supplier_uuid = serializers.UUIDField(source="supplier.uuid", read_only=True)
    customer_uuid = serializers.UUIDField(
        source="customer.uuid", read_only=True, allow_null=True
    )
    preceding_invoice_uuid = serializers.UUIDField(
        source="preceding_invoice.uuid", read_only=True, allow_null=True
    )

    class Meta:
        model = Invoice
        fields = [
            "uuid",
            "number",
            "status",
            "version",
            "invoice_type_code",
            "currency_code",
            "issue_date",
            "due_date",
            "total_excl_tax",
            "total_tax",
            "total_incl_tax",
            "en16931_data",
            "supplier_uuid",
            "customer_uuid",
            "detected_flow",
            "operation_category",
            "ereporting_status",
            "facturx_status",
            "pdp_transmission_id",
            "pdp_status",
            "factpulse_error",
            "external_id",
            "preceding_invoice_uuid",
            "is_internal",
            "payment_date",
            "payment_reference",
            "payment_amount",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class MarkPaidSerializer(serializers.Serializer):
    """Payload for mark-paid endpoint."""

    payment_date = serializers.DateField(required=False)
    payment_reference = serializers.CharField(required=False, allow_blank=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)


class InvoiceAuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceAuditLog
        fields = [
            "id",
            "action",
            "old_status",
            "new_status",
            "details",
            "timestamp",
        ]
        read_only_fields = fields
