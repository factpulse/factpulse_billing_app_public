"""Tests des vues UI de synchronisation Stripe (clients, produits, factures).

L'API Stripe est mockée : on vérifie l'accès (owner + Stripe configuré),
le croisement avec les objets FactPulse et les imports unitaires / en masse.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import Client

from apps.billing.factories import CustomerFactory, InvoiceFactory, ProductFactory
from apps.billing.models import Customer, Product
from apps.core.models import OrganizationMembership
from apps.payments.models import ProviderConfig

SYNC = "apps.payments.providers.stripe.sync"

CUSTOMERS_URL = "/settings/payments/stripe/customers/"
PRODUCTS_URL = "/settings/payments/stripe/products/"
INVOICES_URL = "/settings/payments/stripe/invoices/"

STRIPE_CUSTOMER = {
    "id": "cus_1",
    "name": "Client Stripe",
    "email": "client@stripe.test",
    "address": {"line1": "1 rue de Paris", "postal_code": "75001", "city": "Paris"},
    "tax_ids": {"data": [{"value": "FR12345678901"}]},
}
STRIPE_PRODUCT = {
    "id": "prod_1",
    "name": "Abonnement",
    "description": "Mensuel",
    "default_price": {"unit_amount": 2900, "currency": "eur"},
}
STRIPE_INVOICE = {
    "id": "in_1",
    "number": "INV-0001",
    "customer": {"id": "cus_1", "name": "Client Stripe"},
    "amount_due": 3480,
    "currency": "eur",
    "status": "open",
    "created": 1760000000,
}


def _messages(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


def _login(org, role="owner"):
    user = User.objects.create_user(
        username=f"{role}@stripe.test",
        email=f"{role}@stripe.test",
        password="testpass123",  # nosec B106
    )
    OrganizationMembership.objects.create(user=user, organization=org, role=role)
    client = Client()
    client.login(username=f"{role}@stripe.test", password="testpass123")  # nosec B106
    return client


@pytest.fixture
def stripe_config(org, supplier):
    return ProviderConfig.objects.create(  # nosec B106
        organization=org,
        provider="stripe",
        api_key="sk_test_xxx",
        webhook_secret="whsec_xxx",
        default_supplier=supplier,
    )


@pytest.fixture
def owner_client(org):
    return _login(org)


# --- Accès ---


@pytest.mark.django_db
class TestAccess:
    @pytest.mark.parametrize("url", [CUSTOMERS_URL, PRODUCTS_URL, INVOICES_URL])
    def test_member_is_redirected_to_dashboard(self, org, stripe_config, url):
        resp = _login(org, role="member").get(url)

        assert resp.status_code == 302
        assert resp.url == "/"

    @pytest.mark.parametrize("url", [CUSTOMERS_URL, PRODUCTS_URL, INVOICES_URL])
    def test_without_stripe_config_redirects_to_settings(self, owner_client, url):
        resp = owner_client.get(url)

        assert resp.status_code == 302
        assert resp.url == "/settings/payments/"
        assert "Stripe n'est pas configuré" in _messages(resp)[0]

    def test_inactive_config_is_ignored(self, owner_client, stripe_config):
        stripe_config.is_active = False
        stripe_config.save()

        resp = owner_client.get(CUSTOMERS_URL)

        assert resp.url == "/settings/payments/"


# --- Clients ---


@pytest.mark.django_db
class TestStripeCustomers:
    def test_list_cross_references_factpulse(self, owner_client, org, stripe_config):
        mapped = CustomerFactory(organization=org, external_id="cus_1")
        raw = [STRIPE_CUSTOMER, {"id": "cus_2", "name": None, "email": None}]

        with patch(f"{SYNC}.list_customers", return_value=raw) as list_mock:
            resp = owner_client.get(CUSTOMERS_URL)

        list_mock.assert_called_once_with("sk_test_xxx")
        assert resp.status_code == 200
        assert resp.context["unmapped_count"] == 1
        assert resp.context["stripe_list"] == [
            {
                "id": "cus_1",
                "name": "Client Stripe",
                "email": "client@stripe.test",
                "is_mapped": True,
                "factpulse_uuid": str(mapped.uuid),
            },
            {
                "id": "cus_2",
                "name": "—",
                "email": "—",
                "is_mapped": False,
                "factpulse_uuid": None,
            },
        ]

    def test_list_shows_api_error(self, owner_client, stripe_config):
        with patch(f"{SYNC}.list_customers", side_effect=Exception("boom")):
            resp = owner_client.get(CUSTOMERS_URL)

        assert resp.status_code == 200
        assert resp.context["api_error"] == "boom"
        assert resp.context["stripe_list"] == []

    def test_import_one_creates_then_updates(self, owner_client, org, stripe_config):
        with patch("stripe.Customer.retrieve", return_value=STRIPE_CUSTOMER):
            resp = owner_client.post(
                CUSTOMERS_URL, {"action": "import", "stripe_id": "cus_1"}
            )
            assert resp.url == CUSTOMERS_URL
            assert "importé" in _messages(resp)[0]

            resp = owner_client.post(
                CUSTOMERS_URL, {"action": "import", "stripe_id": "cus_1"}
            )
            assert "déjà présent" in _messages(resp)[-1]

        customer = Customer.objects.get(organization=org, external_id="cus_1")
        assert customer.name == "Client Stripe"
        assert customer.vat_number == "FR12345678901"
        assert customer.address["city"] == "Paris"

    def test_import_one_stripe_error(self, owner_client, stripe_config):
        with patch("stripe.Customer.retrieve", side_effect=Exception("No such")):
            resp = owner_client.post(
                CUSTOMERS_URL, {"action": "import", "stripe_id": "cus_x"}
            )

        assert _messages(resp) == ["Erreur Stripe : No such"]

    def test_import_all(self, owner_client, org, stripe_config):
        CustomerFactory(organization=org, external_id="cus_1")
        raw = [STRIPE_CUSTOMER, {"id": "cus_2", "name": "Autre"}]

        with patch(f"{SYNC}.list_customers", return_value=raw):
            resp = owner_client.post(CUSTOMERS_URL, {"action": "import_all"})

        assert _messages(resp) == ["1 client(s) importé(s), 1 mis à jour."]
        assert Customer.objects.filter(organization=org).count() == 2

    def test_import_all_stripe_error(self, owner_client, stripe_config):
        with patch(f"{SYNC}.list_customers", side_effect=Exception("down")):
            resp = owner_client.post(CUSTOMERS_URL, {"action": "import_all"})

        assert _messages(resp) == ["Erreur Stripe : down"]


# --- Produits ---


@pytest.mark.django_db
class TestStripeProducts:
    def test_list_formats_price(self, owner_client, org, stripe_config):
        ProductFactory(organization=org, external_id="prod_1")
        raw = [
            STRIPE_PRODUCT,
            {"id": "prod_2", "name": "Sans prix", "default_price": None},
            {"id": "prod_3", "name": "Prix au compteur", "default_price": {}},
        ]

        with patch(f"{SYNC}.list_products", return_value=raw):
            resp = owner_client.get(PRODUCTS_URL)

        assert resp.status_code == 200
        prices = [p["default_price"] for p in resp.context["stripe_list"]]
        assert prices == ["29.00 EUR", "", ""]
        assert resp.context["unmapped_count"] == 2

    def test_list_shows_api_error(self, owner_client, stripe_config):
        with patch(f"{SYNC}.list_products", side_effect=Exception("boom")):
            resp = owner_client.get(PRODUCTS_URL)

        assert resp.context["api_error"] == "boom"

    def test_import_one_creates_then_updates(self, owner_client, org, stripe_config):
        with patch("stripe.Product.retrieve", return_value=STRIPE_PRODUCT) as ret:
            resp = owner_client.post(
                PRODUCTS_URL, {"action": "import", "stripe_id": "prod_1"}
            )
            assert "importé" in _messages(resp)[0]
            resp = owner_client.post(
                PRODUCTS_URL, {"action": "import", "stripe_id": "prod_1"}
            )
            assert "déjà présent" in _messages(resp)[-1]

        assert ret.call_args.kwargs["expand"] == ["default_price"]
        product = Product.objects.get(organization=org, external_id="prod_1")
        assert product.default_unit_price == Decimal("29.00")

    def test_import_one_stripe_error(self, owner_client, stripe_config):
        with patch("stripe.Product.retrieve", side_effect=Exception("No such")):
            resp = owner_client.post(
                PRODUCTS_URL, {"action": "import", "stripe_id": "prod_x"}
            )

        assert _messages(resp) == ["Erreur Stripe : No such"]

    def test_import_all(self, owner_client, org, stripe_config):
        with patch(f"{SYNC}.list_products", return_value=[STRIPE_PRODUCT]):
            resp = owner_client.post(PRODUCTS_URL, {"action": "import_all"})

        assert _messages(resp) == ["1 produit(s) importé(s), 0 mis à jour."]

    def test_import_all_stripe_error(self, owner_client, stripe_config):
        with patch(f"{SYNC}.list_products", side_effect=Exception("down")):
            resp = owner_client.post(PRODUCTS_URL, {"action": "import_all"})

        assert _messages(resp) == ["Erreur Stripe : down"]


# --- Factures ---


@pytest.mark.django_db
class TestStripeInvoices:
    def test_list_with_pagination(self, owner_client, org, supplier, stripe_config):
        imported = InvoiceFactory(organization=org, supplier=supplier)
        imported.external_id = "in_1"
        imported.save()
        raw = [
            STRIPE_INVOICE,
            {
                "id": "in_2",
                "customer": "cus_9",
                "customer_email": "x@stripe.test",
                "amount_due": None,
                "currency": None,
            },
        ]

        with patch(f"{SYNC}.list_invoices", return_value=(raw, True)) as list_mock:
            resp = owner_client.get(f"{INVOICES_URL}?cursor=in_0")

        list_mock.assert_called_once_with("sk_test_xxx", starting_after="in_0")
        ctx = resp.context
        assert ctx["has_more"] is True
        assert ctx["next_cursor"] == "in_2"
        assert ctx["has_supplier"] is True
        first, second = ctx["stripe_list"]
        assert first["is_imported"] is True
        assert first["factpulse_uuid"] == str(imported.uuid)
        assert first["customer_name"] == "Client Stripe"
        assert first["amount"] == "34.80 EUR"
        assert second["number"] == "in_2"
        assert second["customer_name"] == "x@stripe.test"
        assert second["amount"] == "0.00 EUR"
        assert second["status"] == "—"

    def test_list_shows_api_error(self, owner_client, stripe_config):
        with patch(f"{SYNC}.list_invoices", side_effect=Exception("boom")):
            resp = owner_client.get(INVOICES_URL)

        assert resp.context["api_error"] == "boom"

    def test_import_requires_default_supplier(self, owner_client, stripe_config):
        stripe_config.default_supplier = None
        stripe_config.save()

        resp = owner_client.post(
            INVOICES_URL, {"action": "import", "stripe_invoice_id": "in_1"}
        )

        assert resp.url == INVOICES_URL
        assert "fournisseur par défaut" in _messages(resp)[0]

    @pytest.mark.parametrize(
        "created, expected", [(True, "Facture importée (F-1)."), (False, None)]
    )
    def test_import_one(self, owner_client, stripe_config, created, expected):
        invoice = MagicMock(number="F-1")

        with (
            patch("stripe.Invoice.retrieve", return_value=STRIPE_INVOICE),
            patch(
                "apps.payments.services.import_stripe_invoice",
                return_value=(invoice, created),
            ) as import_mock,
        ):
            resp = owner_client.post(
                INVOICES_URL,
                {"action": "import", "stripe_invoice_id": "in_1", "cursor": "in_0"},
            )

        assert resp.url == f"{INVOICES_URL}?cursor=in_0"
        assert import_mock.call_args.args[1] == STRIPE_INVOICE
        assert _messages(resp) == [expected or "Facture déjà importée."]

    def test_import_one_error(self, owner_client, stripe_config):
        with patch("stripe.Invoice.retrieve", side_effect=Exception("No such")):
            resp = owner_client.post(
                INVOICES_URL, {"action": "import", "stripe_invoice_id": "in_x"}
            )

        assert _messages(resp) == ["Erreur : No such"]


@pytest.mark.django_db
class TestStripeInvoiceResync:
    def _url(self, invoice):
        return f"/invoices/{invoice.uuid}/stripe-resync/"

    def test_get_redirects_without_resync(self, owner_client, draft_invoice):
        with patch("apps.payments.services.resync_stripe_invoice") as resync:
            resp = owner_client.get(self._url(draft_invoice))

        assert resp.url == f"/invoices/{draft_invoice.uuid}/"
        resync.assert_not_called()

    def test_post_resyncs(self, owner_client, draft_invoice, stripe_config):
        with patch("apps.payments.services.resync_stripe_invoice") as resync:
            resp = owner_client.post(self._url(draft_invoice))

        resync.assert_called_once()
        assert resync.call_args.args[0] == draft_invoice
        assert _messages(resp) == ["Brouillon resynchronisé depuis Stripe."]

    def test_post_error(self, owner_client, draft_invoice, stripe_config):
        with patch(
            "apps.payments.services.resync_stripe_invoice",
            side_effect=Exception("not a draft"),
        ):
            resp = owner_client.post(self._url(draft_invoice))

        assert resp.url == f"/invoices/{draft_invoice.uuid}/"
        assert _messages(resp) == ["Erreur : not a draft"]


# --- Appels SDK Stripe ---


class TestStripeListHelpers:
    def test_list_customers_auto_paginates(self):
        from apps.payments.providers.stripe.sync import list_customers

        page = MagicMock()
        page.auto_paging_iter.return_value = iter([STRIPE_CUSTOMER])
        with patch("stripe.Customer.list", return_value=page) as list_mock:
            assert list_customers("sk") == [STRIPE_CUSTOMER]

        list_mock.assert_called_once_with(api_key="sk")

    def test_list_products_expands_default_price(self):
        from apps.payments.providers.stripe.sync import list_products

        page = MagicMock()
        page.auto_paging_iter.return_value = iter([STRIPE_PRODUCT])
        with patch("stripe.Product.list", return_value=page) as list_mock:
            assert list_products("sk") == [STRIPE_PRODUCT]

        list_mock.assert_called_once_with(
            api_key="sk", active=True, expand=["data.default_price"]
        )

    @pytest.mark.parametrize("cursor", [None, "in_0"])
    def test_list_invoices_page(self, cursor):
        from apps.payments.providers.stripe.sync import list_invoices

        page = MagicMock(data=[STRIPE_INVOICE], has_more=False)
        with patch("stripe.Invoice.list", return_value=page) as list_mock:
            assert list_invoices("sk", starting_after=cursor) == (
                [STRIPE_INVOICE],
                False,
            )

        kwargs = list_mock.call_args.kwargs
        assert kwargs["expand"] == ["data.customer"]
        assert kwargs.get("starting_after") == cursor
