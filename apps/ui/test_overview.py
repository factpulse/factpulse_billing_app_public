"""Tests for Réglages > Vue d'ensemble."""

import uuid as uuid_lib
from unittest.mock import patch

import pytest
from django.test import Client

from apps.billing.factories import SupplierFactory
from apps.factpulse.client import FactPulseError

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner_client(owner_user):
    client = Client()
    client.force_login(owner_user)
    return client


@pytest.fixture
def provisioned(org):
    org.factpulse_client_uid = uuid_lib.uuid4()
    org.save()
    return org


def _get(client, superpdp=None, pdp=None, error=None):
    with patch("apps.factpulse.client.client") as mock_client:
        mock_client.is_configured = True
        mock_client.get_superpdp_status.return_value = superpdp or {}
        mock_client.get_pdp_config.return_value = pdp or {}
        if error:
            mock_client.get_superpdp_status.side_effect = error
        response = client.get("/settings/")
    assert response.status_code == 200
    return response


def test_login_required():
    response = Client().get("/settings/")
    assert response.status_code == 302
    assert "login" in response.url


def test_everything_ready(owner_client, org, provisioned):
    SupplierFactory(
        organization=org,
        name="ACME",
        siret="12345678900011",
        vat_number="FR00123456789",
        is_default=True,
    )
    response = _get(owner_client, superpdp={"connected": True, "modeSandbox": False})
    content = response.content.decode()
    assert "Tout est en ordre" in content
    assert "ACME" in content
    assert "SuperPDP connecté." in content
    assert "FR76 •••• 0189" in content  # IBAN masked
    assert "Saisie et API" in content


def test_lists_what_is_missing(owner_client, org, provisioned):
    response = _get(owner_client)
    content = response.content.decode()
    assert "2 points à régler" in content
    assert "Aucun fournisseur par défaut" in content
    assert "Aucune plateforme agréée" in content


def test_sandbox_and_franchise(owner_client, org, provisioned):
    SupplierFactory(
        organization=org,
        siret="12345678900011",
        vat_regime="franchise",
        is_default=True,
    )
    content = _get(
        owner_client, superpdp={"connected": True, "modeSandbox": True}
    ).content.decode()
    assert "Franchise en base" in content
    assert "bac à sable" in content
    assert "1 point à régler" in content


def test_other_pa_shows_host(owner_client, org, provisioned):
    content = _get(
        owner_client,
        pdp={
            "isConfigured": True,
            "isActive": True,
            "flowServiceUrl": "https://api.pa.example/flow/v1",
        },
    ).content.decode()
    assert "Connectée à api.pa.example." in content


def test_pa_api_error_does_not_break_page(owner_client, org, provisioned):
    content = _get(owner_client, error=FactPulseError("boom")).content.decode()
    assert "Statut indisponible" in content


def test_stripe_classic_source(owner_client, org, provisioned):
    from django.apps import apps

    if not apps.is_installed("apps.payments"):
        pytest.skip("payments app not installed")
    from apps.payments.models import ProviderConfig

    ProviderConfig.objects.create(
        organization=org,
        provider="stripe",
        api_key="sk_live_x",  # nosec B106
        webhook_secret="",
    )
    content = _get(owner_client).content.decode()
    assert "Stripe (clé API)" in content
    assert "Import manuel (webhook non configuré)" in content


def test_member_sees_page_without_owner_links(member_user, org, provisioned):
    client = Client()
    client.force_login(member_user)
    content = _get(client).content.decode()
    assert "/settings/pdp/" not in content.split("Vue d'ensemble</h1>")[1]
