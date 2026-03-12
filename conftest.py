"""Shared fixtures for all tests."""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from apps.billing.factories import (
    CustomerFactory,
    InvoiceFactory,
    ProductFactory,
    SupplierFactory,
)
from apps.core.models import Organization, OrganizationMembership


@pytest.fixture(autouse=True)
def _no_factpulse_api():
    """Prevent auto-provisioning signal from hitting the FactPulse API."""
    with patch("apps.factpulse.client.client") as mock_client:
        mock_client.is_configured = False
        yield mock_client


@pytest.fixture
def org(db):
    return Organization.objects.create(name="Test Org", slug="test-org")


@pytest.fixture
def owner_user(db, org):
    user = User.objects.create_user(
        username="owner@test.local",
        email="owner@test.local",
        password="testpass123",  # nosec B106
    )
    OrganizationMembership.objects.create(user=user, organization=org, role="owner")
    return user


@pytest.fixture
def member_user(db, org):
    user = User.objects.create_user(
        username="member@test.local",
        email="member@test.local",
        password="testpass123",  # nosec B106
    )
    OrganizationMembership.objects.create(user=user, organization=org, role="member")
    return user


@pytest.fixture
def viewer_user(db, org):
    user = User.objects.create_user(
        username="viewer@test.local",
        email="viewer@test.local",
        password="testpass123",  # nosec B106
    )
    OrganizationMembership.objects.create(user=user, organization=org, role="viewer")
    return user


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def supplier(db, org):
    return SupplierFactory(organization=org)


@pytest.fixture
def customer(db, org):
    return CustomerFactory(organization=org)


@pytest.fixture
def product(db, org):
    return ProductFactory(organization=org)


@pytest.fixture
def draft_invoice(db, org, supplier):
    return InvoiceFactory(organization=org, supplier=supplier)


@pytest.fixture
def auth_api_client(api_client, owner_user, org):
    """APIClient authenticated via JWT token."""
    from rest_framework_simplejwt.tokens import RefreshToken

    refresh = RefreshToken.for_user(owner_user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return api_client
