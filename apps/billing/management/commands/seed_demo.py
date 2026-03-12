"""Seed demo data for the FactPulse Billing App."""

import uuid as uuid_lib

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from apps.billing.constants import AFNOR_SCHEME_ID, VAT_ACCOUNTING_CODE
from apps.billing.models import (
    Customer,
    Invoice,
    NumberingSequence,
    Product,
    Supplier,
)
from apps.core.models import Organization, OrganizationMembership


class Command(BaseCommand):
    help = "Seed demo data: organization, supplier, customers, products, invoices, user"

    def handle(self, *args, **options):
        self.stdout.write("Seeding demo data...")

        # Organization — fixed factpulse_client_uid to avoid auto-provisioning API call
        DEMO_CLIENT_UID = uuid_lib.UUID("00000000-0000-4000-a000-000000000001")
        org, _ = Organization.objects.get_or_create(
            slug="factpulse-demo",
            defaults={
                "name": "FactPulse Demo",
                "factpulse_client_uid": DEMO_CLIENT_UID,
            },
        )
        # Backfill for existing orgs created before this field existed
        if not org.factpulse_client_uid:
            org.factpulse_client_uid = DEMO_CLIENT_UID
            org.save(update_fields=["factpulse_client_uid"])
        self.stdout.write(f"  Organization: {org.name}")

        # Demo user
        user, created = User.objects.get_or_create(
            username="demo@factpulse.local",
            defaults={
                "email": "demo@factpulse.local",
                "first_name": "Demo",
                "last_name": "User",
                "is_active": True,
            },
        )
        if created:
            user.set_password("demo-factpulse")
            user.save()
        self.stdout.write(f"  User: {user.email} (password: demo-factpulse)")

        # Membership
        OrganizationMembership.objects.get_or_create(
            user=user,
            organization=org,
            defaults={"role": "owner"},
        )

        # Supplier
        supplier, _ = Supplier.objects.get_or_create(
            organization=org,
            siren="920195229",
            defaults={
                "name": "FactPulse SAS",
                "siret": "92019522900010",
                "vat_number": "FR12920195229",
                "email": "facturation@factpulse.com",
                "address": {
                    "lineOne": "42 rue de la Tech",
                    "postalCode": "75011",
                    "city": "Paris",
                    "countryCode": "FR",
                },
                "contact": {
                    "name": "Service Facturation",
                    "email": "facturation@factpulse.com",
                },
                "electronic_address": {
                    "schemeId": AFNOR_SCHEME_ID,
                    "identifier": "920195229",
                },
                "iban": "FR7630006000011234567890189",
                "legal_description": "SAS au capital de 10 000 EUR - RCS Paris",
                "is_default": True,
                "primary_color": "#1a73e8",
                "pdf_legal_mentions": (
                    "FactPulse SAS - SIREN 920 195 229 - "
                    "TVA FR12920195229 - "
                    "En cas de retard de paiement, une pénalité de 3 fois le taux d'intérêt légal sera appliquée."
                ),
            },
        )
        self.stdout.write(f"  Supplier: {supplier.name}")

        # Numbering sequence
        NumberingSequence.objects.get_or_create(
            supplier=supplier,
            defaults={
                "prefix_template": "FACT-{{ issue_date|date:'Y' }}-",
                "padding": 3,
            },
        )

        # Customers
        customers_data = [
            {
                "name": "Dupont & Fils SAS",
                "siren": "123456789",
                "siret": "12345678900010",
                "vat_number": "FR32123456789",
                "email": "comptabilite@dupont-fils.fr",
                "external_id": "cust_dupont",
                "address": {
                    "lineOne": "15 avenue des Champs",
                    "postalCode": "75008",
                    "city": "Paris",
                    "countryCode": "FR",
                },
                "electronic_address": {
                    "schemeId": AFNOR_SCHEME_ID,
                    "identifier": "123456789",
                },
            },
            {
                "name": "TechCorp SARL",
                "siren": "987654321",
                "siret": "98765432100015",
                "vat_number": "FR65987654321",
                "email": "invoices@techcorp.io",
                "external_id": "cust_techcorp",
                "address": {
                    "lineOne": "8 rue de l'Innovation",
                    "postalCode": "69001",
                    "city": "Lyon",
                    "countryCode": "FR",
                },
                "electronic_address": {
                    "schemeId": AFNOR_SCHEME_ID,
                    "identifier": "987654321",
                },
            },
            {
                "name": "GreenLeaf Bio",
                "siren": "456789123",
                "email": "admin@greenleaf.bio",
                "external_id": "cust_greenleaf",
                "address": {
                    "lineOne": "3 chemin des Vignes",
                    "postalCode": "33000",
                    "city": "Bordeaux",
                    "countryCode": "FR",
                },
                "electronic_address": {
                    "schemeId": AFNOR_SCHEME_ID,
                    "identifier": "456789123",
                },
            },
        ]

        customers = []
        for cdata in customers_data:
            customer, _ = Customer.objects.get_or_create(
                organization=org,
                external_id=cdata.pop("external_id"),
                defaults=cdata,
            )
            customers.append(customer)
            self.stdout.write(f"  Customer: {customer.name}")

        # Products
        products_data = [
            {
                "name": "Prestation conseil",
                "reference": "PREST-001",
                "external_id": "prod_conseil",
                "default_unit_price": "800.00",
                "default_vat_rate": "20.00",
                "default_vat_category": "S",
                "default_unit": "C62",
                "description": "Prestation de conseil en conformité facturation",
            },
            {
                "name": "Abonnement API Standard",
                "reference": "ABO-STD",
                "external_id": "prod_abo_std",
                "default_unit_price": "49.00",
                "default_vat_rate": "20.00",
                "default_vat_category": "S",
                "default_unit": "MON",
                "description": "Abonnement mensuel API FactPulse - Standard",
            },
            {
                "name": "Abonnement API Pro",
                "reference": "ABO-PRO",
                "external_id": "prod_abo_pro",
                "default_unit_price": "149.00",
                "default_vat_rate": "20.00",
                "default_vat_category": "S",
                "default_unit": "MON",
                "description": "Abonnement mensuel API FactPulse - Pro",
            },
            {
                "name": "Formation Factur-X",
                "reference": "FORM-FX",
                "external_id": "prod_formation",
                "default_unit_price": "1500.00",
                "default_vat_rate": "20.00",
                "default_vat_category": "S",
                "default_unit": "C62",
                "description": "Formation à la facturation électronique Factur-X (1 jour)",
            },
            {
                "name": "Support premium",
                "reference": "SUP-PREM",
                "external_id": "prod_support",
                "default_unit_price": "200.00",
                "default_vat_rate": "20.00",
                "default_vat_category": "S",
                "default_unit": "MON",
                "description": "Support technique prioritaire mensuel",
            },
        ]

        for pdata in products_data:
            product, _ = Product.objects.get_or_create(
                organization=org,
                external_id=pdata.pop("external_id"),
                defaults=pdata,
            )
            self.stdout.write(f"  Product: {product.name}")

        # Sample invoices
        if not Invoice.objects.filter(organization=org).exists():
            invoice1 = Invoice.objects.create(
                organization=org,
                supplier=supplier,
                customer=customers[0],
                en16931_data={
                    "supplier": {
                        "name": "FactPulse SAS",
                        "siren": "920195229",
                        "vatNumber": "FR12920195229",
                        "postalAddress": {
                            "lineOne": "42 rue de la Tech",
                            "postalCode": "75011",
                            "city": "Paris",
                            "countryCode": "FR",
                        },
                    },
                    "recipient": {
                        "name": "Dupont & Fils SAS",
                        "siren": "123456789",
                        "vatNumber": "FR32123456789",
                        "postalAddress": {
                            "lineOne": "15 avenue des Champs",
                            "postalCode": "75008",
                            "city": "Paris",
                            "countryCode": "FR",
                        },
                    },
                    "invoiceDate": "2026-01-15",
                    "paymentDueDate": "2026-02-14",
                    "invoiceLines": [
                        {
                            "lineNumber": 1,
                            "itemName": "Prestation conseil",
                            "quantity": "5",
                            "unitNetPrice": "800.00",
                            "manualVatRate": "20.00",
                            "vatCategory": "S",
                            "unit": "PIECE",
                            "lineNetAmount": "4000.00",
                        },
                    ],
                    "vatLines": [
                        {
                            "category": "S",
                            "manualRate": "20.00",
                            "taxableAmount": "4000.00",
                            "vatAmount": "800.00",
                        },
                    ],
                    "totals": {
                        "totalNetAmount": "4000.00",
                        "vatAmount": "800.00",
                        "totalGrossAmount": "4800.00",
                        "amountDue": "4800.00",
                    },
                    "references": {
                        "invoiceType": "FACTURE",
                        "invoiceCurrency": "EUR",
                        "vatAccountingCode": VAT_ACCOUNTING_CODE,
                        "issueDate": "2026-01-15",
                        "dueDate": "2026-02-14",
                        "paymentMeans": "VIREMENT",
                    },
                },
            )
            self.stdout.write(f"  Invoice 1: {invoice1.uuid} (draft)")

            invoice2 = Invoice.objects.create(
                organization=org,
                supplier=supplier,
                customer=customers[1],
                en16931_data={
                    "supplier": {
                        "name": "FactPulse SAS",
                        "siren": "920195229",
                        "vatNumber": "FR12920195229",
                        "postalAddress": {
                            "lineOne": "42 rue de la Tech",
                            "postalCode": "75011",
                            "city": "Paris",
                            "countryCode": "FR",
                        },
                    },
                    "recipient": {
                        "name": "TechCorp SARL",
                        "siren": "987654321",
                        "vatNumber": "FR65987654321",
                        "postalAddress": {
                            "lineOne": "8 rue de l'Innovation",
                            "postalCode": "69001",
                            "city": "Lyon",
                            "countryCode": "FR",
                        },
                    },
                    "invoiceDate": "2026-02-01",
                    "paymentDueDate": "2026-03-03",
                    "invoiceLines": [
                        {
                            "lineNumber": 1,
                            "itemName": "Abonnement API Pro",
                            "quantity": "1",
                            "unitNetPrice": "149.00",
                            "manualVatRate": "20.00",
                            "vatCategory": "S",
                            "unit": "PIECE",
                            "lineNetAmount": "149.00",
                        },
                        {
                            "lineNumber": 2,
                            "itemName": "Support premium",
                            "quantity": "1",
                            "unitNetPrice": "200.00",
                            "manualVatRate": "20.00",
                            "vatCategory": "S",
                            "unit": "PIECE",
                            "lineNetAmount": "200.00",
                        },
                    ],
                    "vatLines": [
                        {
                            "category": "S",
                            "manualRate": "20.00",
                            "taxableAmount": "349.00",
                            "vatAmount": "69.80",
                        },
                    ],
                    "totals": {
                        "totalNetAmount": "349.00",
                        "vatAmount": "69.80",
                        "totalGrossAmount": "418.80",
                        "amountDue": "418.80",
                    },
                    "references": {
                        "invoiceType": "FACTURE",
                        "invoiceCurrency": "EUR",
                        "vatAccountingCode": VAT_ACCOUNTING_CODE,
                        "issueDate": "2026-02-01",
                        "dueDate": "2026-03-03",
                        "paymentMeans": "VIREMENT",
                    },
                },
            )
            self.stdout.write(f"  Invoice 2: {invoice2.uuid} (draft)")

        self.stdout.write(self.style.SUCCESS("\nDemo data seeded successfully!"))
        self.stdout.write(
            "\nQuick start:\n"
            "  UI:  http://localhost:8000 (demo@factpulse.local / demo-factpulse)\n"
            "  API: POST /api/v1/auth/token/ with "
            '{"email": "demo@factpulse.local", "password": "demo-factpulse"}\n'
            "       then use Authorization: Bearer <access_token>\n"
        )
