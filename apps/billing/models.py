import uuid as uuid_lib
from decimal import Decimal, InvalidOperation

from django.core.validators import RegexValidator
from django.db import models

from apps.billing.validators import (
    validate_image_extension,
    validate_image_size,
    validate_pdf_extension,
    validate_pdf_size,
)
from apps.core.models import Organization


class Supplier(models.Model):
    """Entité légale qui émet les factures (le vendeur sur la facture)."""

    uuid = models.UUIDField(default=uuid_lib.uuid4, unique=True, editable=False)
    organization = models.ForeignKey(
        Organization, related_name="suppliers", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=255)  # BT-27
    siren = models.CharField(max_length=9, blank=True)  # BT-30
    siret = models.CharField(max_length=14, blank=True)  # BT-29
    vat_number = models.CharField(max_length=20, blank=True)  # BT-31
    iban = models.CharField(max_length=34, blank=True)  # BT-84
    bic = models.CharField(max_length=11, blank=True)  # BT-86
    email = models.EmailField(blank=True)
    address = models.JSONField(default=dict, blank=True)
    contact = models.JSONField(default=dict, blank=True)
    electronic_address = models.JSONField(default=dict, blank=True)
    legal_description = models.CharField(max_length=500, blank=True)  # BT-33
    external_id = models.CharField(  # noqa: DJ001 - null needed for unique-with-org constraint
        max_length=255, null=True, blank=True, db_index=True
    )
    is_default = models.BooleanField(default=False)

    # PDF customization
    logo = models.ImageField(
        upload_to="suppliers/logos/",
        blank=True,
        validators=[validate_image_extension, validate_image_size],
    )
    primary_color = models.CharField(
        max_length=7,
        blank=True,
        validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", "Invalid hex color.")],
    )
    pdf_legal_mentions = models.TextField(blank=True)

    # Mentions obligatoires (BR-FR-05)
    note_pmt = models.TextField(
        blank=True,
        default="Indemnité forfaitaire pour frais de recouvrement : 40 €",
    )
    note_pmd = models.TextField(
        blank=True,
        default="Pénalités de retard : 3 fois le taux d'intérêt légal",
    )
    note_aab = models.TextField(
        blank=True,
        default="Pas d'escompte",
    )

    # Régime TVA — détermine la périodicité e-reporting (DGFiP v3.1, Tableau 12)
    class VatRegime(models.TextChoices):
        REEL_NORMAL_MENSUEL = "reel_normal_mensuel", "Réel normal mensuel"
        REEL_NORMAL_TRIMESTRIEL = "reel_normal_trimestriel", "Réel normal trimestriel"
        SIMPLIFIE = "simplifie", "Simplifié"
        FRANCHISE = "franchise", "Franchise en base"

    vat_regime = models.CharField(
        max_length=30, choices=VatRegime, default=VatRegime.REEL_NORMAL_MENSUEL
    )

    # Conditions de paiement
    payment_terms_days = models.PositiveIntegerField(null=True, blank=True)
    payment_terms_end_of_month = models.BooleanField(default=False)

    archived = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "external_id"],
                condition=models.Q(external_id__isnull=False),
                name="unique_supplier_external_id_per_org",
            )
        ]

    def __str__(self):
        return self.name


class Customer(models.Model):
    """Fiche client vivante — optionnelle, confort pour l'UI et les clients récurrents."""

    class CustomerType(models.TextChoices):
        ASSUJETTI_FR = "assujetti_fr", "Assujetti TVA France"
        INTRA_UE = "intra_ue", "Intra-communautaire UE"
        EXTRA_UE = "extra_ue", "Extra-UE"
        PARTICULIER = "particulier", "Particulier (B2C)"
        PUBLIC = "public", "Secteur public (B2G)"

    uuid = models.UUIDField(default=uuid_lib.uuid4, unique=True, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="customers"
    )
    external_id = models.CharField(  # noqa: DJ001 - null needed for unique-with-org constraint
        max_length=255, null=True, blank=True, db_index=True
    )
    name = models.CharField(max_length=255)  # BT-44
    siren = models.CharField(max_length=9, blank=True)  # BT-47
    siret = models.CharField(max_length=14, blank=True)  # BT-46
    vat_number = models.CharField(max_length=20, blank=True)  # BT-48
    customer_type = models.CharField(
        max_length=20,
        choices=CustomerType,
        default=CustomerType.ASSUJETTI_FR,
    )
    email = models.EmailField(blank=True)
    address = models.JSONField(default=dict, blank=True)
    contact = models.JSONField(default=dict, blank=True)
    electronic_address = models.JSONField(default=dict, blank=True)  # BT-49

    archived = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "external_id"],
                condition=models.Q(external_id__isnull=False),
                name="unique_customer_external_id_per_org",
            )
        ]

    def __str__(self):
        return self.name


class Product(models.Model):
    """Catalogue produit basique — confort pour l'UI et les factures récurrentes."""

    uuid = models.UUIDField(default=uuid_lib.uuid4, unique=True, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="products"
    )
    external_id = models.CharField(  # noqa: DJ001 - null needed for unique-with-org constraint
        max_length=255, null=True, blank=True, db_index=True
    )
    name = models.CharField(max_length=255)  # BT-153
    description = models.TextField(blank=True)  # BT-154
    reference = models.CharField(max_length=255, blank=True)  # BT-155
    default_unit_price = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True
    )
    default_vat_rate = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    default_vat_category = models.CharField(max_length=5, default="S")
    default_unit = models.CharField(max_length=10, default="C62")  # UN/ECE Rec 20

    archived = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "external_id"],
                condition=models.Q(external_id__isnull=False),
                name="unique_product_external_id_per_org",
            )
        ]

    def __str__(self):
        return self.name


class NumberingSequence(models.Model):
    """Séquence de numérotation par supplier.
    Le prefix_template est un template Django rendu avec le contexte de la facture."""

    supplier = models.OneToOneField(
        Supplier, on_delete=models.CASCADE, related_name="numbering_sequence"
    )
    prefix_template = models.CharField(
        max_length=100, default="FACT-{{ issue_date|date:'Y' }}-"
    )
    padding = models.PositiveIntegerField(default=3)

    def __str__(self):
        return f"Numbering for {self.supplier.name}"


class NumberingCounter(models.Model):
    """Compteur continu par préfixe résolu."""

    sequence = models.ForeignKey(
        NumberingSequence, on_delete=models.CASCADE, related_name="counters"
    )
    resolved_prefix = models.CharField(max_length=100, db_index=True)
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = [("sequence", "resolved_prefix")]

    def __str__(self):
        return f"{self.resolved_prefix}{self.last_number}"


class Invoice(models.Model):
    """Facture — modèle Django lean, conformité EN16931 déléguée à l'API FactPulse."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Brouillon"
        PROCESSING = "processing", "En cours de génération"
        VALIDATED = "validated", "Validée"
        TRANSMITTING = "transmitting", "Transmission en cours"
        TRANSMITTED = "transmitted", "Transmise"
        ACCEPTED = "accepted", "Acceptée"
        REJECTED = "rejected", "Rejetée"
        REFUSED = "refused", "Refusée"
        PAID = "paid", "Payée"
        CANCELLED = "cancelled", "Annulée"

    uuid = models.UUIDField(default=uuid_lib.uuid4, unique=True, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="invoices"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.PROTECT, related_name="invoices"
    )
    customer = models.ForeignKey(
        Customer,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invoices",
    )

    # Indexed fields for SQL queries
    number = models.CharField(max_length=50, blank=True)
    invoice_type_code = models.CharField(max_length=10, default="380")  # BT-3
    currency_code = models.CharField(max_length=3, default="EUR")  # BT-5
    issue_date = models.DateField(null=True, blank=True)  # BT-2
    due_date = models.DateField(null=True, blank=True)  # BT-9

    status = models.CharField(
        max_length=20, choices=Status, default=Status.DRAFT, db_index=True
    )

    # Denormalized totals (BG-22)
    total_excl_tax = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )  # BT-109
    total_tax = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )  # BT-110
    total_incl_tax = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )  # BT-112

    # EN16931 snapshot — source of truth
    en16931_data = models.JSONField(default=dict)

    # Flow detection — conformité 2026 (ref BR-FR-20, XP Z12-012)
    class Flow(models.TextChoices):
        B2B_DOMESTIC = "b2b_domestic", "B2B domestique (e-facturation)"
        B2C = "b2c", "B2C (e-reporting)"
        B2B_INTRA_EU = "b2b_intra_eu", "B2B intra-UE (e-reporting)"
        B2B_EXTRA_EU = "b2b_extra_eu", "B2B extra-UE (e-reporting)"
        B2G = "b2g", "B2G (Chorus Pro)"

    class OperationCategory(models.TextChoices):
        TPS1 = "TPS1", "Prestation de services"
        TLB1 = "TLB1", "Livraison de biens"
        TNT1 = "TNT1", "Opérations non taxées"
        TMA1 = "TMA1", "Mixte"

    detected_flow = models.CharField(
        max_length=20, choices=Flow, blank=True, db_index=True
    )
    operation_category = models.CharField(
        max_length=4, choices=OperationCategory, default=OperationCategory.TPS1
    )

    # E-reporting status (for non-B2B flows: B2C, intra/extra-UE)
    class EreportingStatus(models.TextChoices):
        NONE = "", "—"
        PENDING = "pending", "En attente"
        SUBMITTED = "submitted", "Soumis"
        ACCEPTED = "accepted", "Accepté"
        ERROR = "error", "Erreur"

    ereporting_status = models.CharField(
        max_length=20,
        choices=EreportingStatus,
        blank=True,
        default=EreportingStatus.NONE,
    )
    ereporting_error = models.JSONField(null=True, blank=True)

    # Conformité 2026
    facturx_status = models.CharField(max_length=20, blank=True)
    pdp_transmission_id = models.CharField(max_length=100, blank=True)
    pdp_status = models.CharField(max_length=50, blank=True)

    # FactPulse error
    factpulse_error = models.JSONField(null=True, blank=True)

    # External system link
    external_id = models.CharField(  # noqa: DJ001 - null needed for unique-with-org constraint
        max_length=255, null=True, blank=True, db_index=True
    )
    pdf_file = models.FileField(
        upload_to="invoices/pdf/",
        blank=True,
        validators=[validate_pdf_extension, validate_pdf_size],
    )

    # Credit note link
    preceding_invoice = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="credit_notes",
    )
    is_internal = models.BooleanField(default=False)

    # Payment fields (set via mark-paid, outside en16931_data snapshot)
    payment_date = models.DateField(null=True, blank=True)
    payment_reference = models.CharField(max_length=255, blank=True)
    payment_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    # Soft delete
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # Optimistic locking
    version = models.PositiveIntegerField(default=1)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["supplier", "number"],
                condition=~models.Q(number=""),
                name="unique_invoice_number_per_supplier",
            ),
            models.UniqueConstraint(
                fields=["organization", "external_id"],
                condition=models.Q(external_id__isnull=False),
                name="unique_invoice_external_id_per_org",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "number"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return self.number or f"Draft {self.uuid}"

    def save(self, *args, **kwargs):
        self._sync_denormalized_fields()
        super().save(*args, **kwargs)

    def _sync_denormalized_fields(self):
        """Synchronise denormalized fields from en16931_data."""
        data = self.en16931_data or {}

        # Totals
        totals = data.get("totals", {})
        self.total_excl_tax = self._to_decimal(totals.get("totalNetAmount"))
        self.total_tax = self._to_decimal(totals.get("vatAmount"))
        self.total_incl_tax = self._to_decimal(totals.get("totalGrossAmount"))

        # Dates
        references = data.get("references", {})
        if references.get("issueDate"):
            self.issue_date = references["issueDate"]
        if references.get("dueDate"):
            self.due_date = references["dueDate"]

        # Currency and invoice type
        if references.get("invoiceCurrency"):
            self.currency_code = references["invoiceCurrency"]
        if references.get("invoiceType"):
            # API uses enum names or codes: "380"=INVOICE, "381"=CREDIT_NOTE
            self.invoice_type_code = references["invoiceType"]

    @staticmethod
    def _to_decimal(value):
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None


class InvoiceAuditLog(models.Model):
    """Traces status transitions and important modifications."""

    class Action(models.TextChoices):
        CREATED = "created", "Créé"
        STATUS_CHANGE = "status_change", "Changement de statut"
        DELETE = "delete", "Suppression"
        FLOW_SUBMITTED = "flow_submitted", "Flux soumis"
        CDAR_EVENT = "cdar_event", "Événement CDAR"
        CDAR_PAID_SUBMITTED = "cdar_paid_submitted", "CDAR payé soumis"
        CDAR_PAID_SKIPPED = "cdar_paid_skipped", "CDAR payé ignoré"
        CDAR_PAID_ERROR = "cdar_paid_error", "CDAR payé erreur"
        DATA_UPDATE = "data_update", "Mise à jour des données"
        EREPORTING_SUBMITTED = "ereporting_submitted", "E-reporting soumis"
        EREPORTING_ERROR = "ereporting_error", "E-reporting erreur"

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="audit_logs"
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(
        "auth.User", null=True, blank=True, on_delete=models.SET_NULL
    )
    action = models.CharField(max_length=50, choices=Action)
    old_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20, blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.invoice} - {self.action} at {self.timestamp}"


class IdempotencyKey(models.Model):
    """Idempotency key for POST /invoices/ (Stripe pattern, TTL 24h)."""

    key = models.CharField(max_length=255, db_index=True)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE)
    response_data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("organization", "key")]

    def __str__(self):
        return self.key
