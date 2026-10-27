"""Tests for the Stripe catch-up of invoices and credit notes missed by webhooks."""

import time
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from apps.billing.factories import InvoiceFactory, SupplierFactory
from apps.billing.models import Invoice
from apps.payments.catchup import catch_up_stripe
from apps.payments.models import ProviderConfig

pytestmark = pytest.mark.django_db

NOW = int(time.time())
HOUR = 3600


def _page(*objects):
    page = MagicMock()
    page.auto_paging_iter.return_value = iter(objects)
    return page


def _invoice(id_, status="paid", created=NOW - HOUR):
    return {
        "id": id_,
        "number": f"N-{id_}",
        "status": status,
        "created": created,
        "currency": "eur",
        "customer": "cus_1",
        "customer_name": "Client SAS",
        "total": 2900,
        "subtotal": 2900,
        "lines": {"data": [{"description": "Starter", "quantity": 1, "amount": 2900}]},
    }


def _credit_note(id_, invoice_id, created=NOW - HOUR):
    return {
        "id": id_,
        "number": f"N-{invoice_id}-CN-01",
        "invoice": invoice_id,
        "created": created,
        "currency": "eur",
        "total": 2900,
        "lines": {"data": [{"description": "Starter", "quantity": 1, "amount": 2900}]},
    }


@pytest.fixture
def supplier(org):
    return SupplierFactory(organization=org)


@pytest.fixture
def config(org, supplier):
    config = ProviderConfig.objects.create(
        organization=org,
        provider="stripe",
        api_key="sk_test",
        default_supplier=supplier,
    )
    ProviderConfig.objects.filter(pk=config.pk).update(
        created_at=timezone.now() - timezone.timedelta(days=30)
    )
    return config


def _run(invoices=(), credit_notes=()):
    with (
        patch("stripe.Invoice.list", return_value=_page(*invoices)) as inv_list,
        patch("stripe.CreditNote.list", return_value=_page(*credit_notes)),
    ):
        imported = catch_up_stripe()
    return imported, inv_list


def test_imports_missing_invoice_then_its_credit_note(config, org):
    imported, _ = _run(
        invoices=[_invoice("in_missed")],
        credit_notes=[_credit_note("cn_missed", "in_missed")],
    )

    assert imported == 2
    invoice = Invoice.objects.get(external_id="in_missed")
    credit_note = Invoice.objects.get(external_id="cn_missed")
    assert invoice.status == "draft"
    assert credit_note.preceding_invoice == invoice
    assert credit_note.invoice_type_code == "381"


def test_known_documents_are_left_alone(config, org, supplier):
    InvoiceFactory(organization=org, supplier=supplier, external_id="in_known")
    InvoiceFactory(
        organization=org,
        supplier=supplier,
        external_id="in_deleted",
        deleted_at=timezone.now(),
    )

    imported, _ = _run(invoices=[_invoice("in_known"), _invoice("in_deleted")])

    assert imported == 0
    assert Invoice.objects.filter(external_id="in_deleted").count() == 1


def test_draft_and_void_invoices_skipped(config):
    imported, _ = _run(
        invoices=[_invoice("in_draft", "draft"), _invoice("in_void", "void")]
    )

    assert imported == 0
    assert not Invoice.objects.exists()


def test_stops_at_documents_older_than_window(config):
    imported, _ = _run(
        invoices=[_invoice("in_recent"), _invoice("in_old", created=NOW - 5 * 86400)]
    )

    assert imported == 1
    assert not Invoice.objects.filter(external_id="in_old").exists()


def test_nothing_before_the_stripe_connection(config):
    ProviderConfig.objects.filter(pk=config.pk).update(created_at=timezone.now())

    imported, _ = _run(invoices=[_invoice("in_before")])

    assert imported == 0


def test_without_default_supplier_no_api_call(config):
    config.default_supplier = None
    config.save()

    imported, inv_list = _run(invoices=[_invoice("in_x")])

    assert imported == 0
    inv_list.assert_not_called()


def test_failing_account_does_not_block_others(config, owner_user):
    from apps.core.models import Organization

    other_org = Organization.objects.create(name="Other", slug="other")
    ProviderConfig.objects.create(
        organization=other_org,
        provider="stripe",
        api_key="sk_other",
        default_supplier=SupplierFactory(organization=other_org),
    )
    calls = []

    def fake_list(api_key, **params):
        calls.append(api_key)
        if api_key == "sk_test":
            raise RuntimeError("Stripe down")
        return _page()

    with (
        patch("stripe.Invoice.list", side_effect=fake_list),
        patch("stripe.CreditNote.list", return_value=_page()),
    ):
        catch_up_stripe()

    assert set(calls) == {"sk_test", "sk_other"}
