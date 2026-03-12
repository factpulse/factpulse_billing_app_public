"""Tests for core app — Organization model, services, API register, middleware, permissions."""

import uuid as uuid_lib

import pytest
from django.contrib.auth.models import User
from django.test import RequestFactory
from rest_framework.test import APIClient

from apps.core.middleware import APIVersionMiddleware, OrganizationMiddleware
from apps.core.models import Organization, OrganizationMembership
from apps.core.permissions import (
    HasOrganization,
    IsCustomerAccess,
    IsMember,
    IsOwner,
    IsViewer,
)
from apps.core.services import create_account

# --- Organization model ---


@pytest.mark.django_db
class TestOrganizationModel:
    def test_factpulse_client_uid_nullable(self):
        org = Organization.objects.create(name="No UID", slug="no-uid")
        assert org.factpulse_client_uid is None

    def test_factpulse_client_uid_set(self):
        uid = uuid_lib.uuid4()
        org = Organization.objects.create(
            name="With UID", slug="with-uid", factpulse_client_uid=uid
        )
        org.refresh_from_db()
        assert org.factpulse_client_uid == uid


# --- create_account service ---


@pytest.mark.django_db
class TestCreateAccount:
    def test_success(self):
        user, org = create_account("new@test.com", "securepass1", "My Company")
        assert user.email == "new@test.com"
        assert user.username == "new@test.com"
        assert org.name == "My Company"
        assert org.slug == "my-company"
        membership = OrganizationMembership.objects.get(user=user, organization=org)
        assert membership.role == "owner"

    def test_email_normalized(self):
        user, org = create_account("  TEST@Example.COM  ", "securepass1", "Org")
        assert user.email == "test@example.com"
        assert user.username == "test@example.com"

    def test_duplicate_email_raises(self):
        create_account("dupe@test.com", "securepass1", "Org 1")
        with pytest.raises(ValueError, match="existe déjà"):
            create_account("dupe@test.com", "securepass1", "Org 2")

    def test_empty_email_raises(self):
        with pytest.raises(ValueError, match="email"):
            create_account("", "securepass1", "Org")

    def test_empty_password_raises(self):
        with pytest.raises(ValueError, match="mot de passe"):
            create_account("a@b.com", "", "Org")

    def test_empty_org_name_raises(self):
        with pytest.raises(ValueError, match="organisation"):
            create_account("a@b.com", "securepass1", "")

    def test_slug_uniqueness(self):
        create_account("a@test.com", "securepass1", "Same Name")
        _, org2 = create_account("b@test.com", "securepass1", "Same Name")
        assert org2.slug == "same-name-1"

    def test_atomic_rollback_on_duplicate(self):
        """If user creation fails, org should not be created."""
        create_account("atomic@test.com", "securepass1", "Atomic Org")
        with pytest.raises(ValueError):
            create_account("atomic@test.com", "securepass1", "Another Org")
        assert Organization.objects.filter(name="Another Org").count() == 0


# --- API Register endpoint ---


@pytest.mark.django_db
class TestRegisterApi:
    def setup_method(self):
        self.client = APIClient()
        self.url = "/api/v1/auth/register/"

    def test_register_success(self):
        response = self.client.post(
            self.url,
            {
                "email": "api@test.com",
                "password": "securepass123",
                "org_name": "API Corp",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert "detail" in data
        # No JWT tokens — must verify email first
        assert "access" not in data
        # Verify user was created
        user = User.objects.get(email="api@test.com")
        assert user.check_password("securepass123")
        assert not user.profile.email_verified

    def test_register_creates_unverified_user(self):
        response = self.client.post(
            self.url,
            {
                "email": "jwt@test.com",
                "password": "securepass123",
                "org_name": "JWT Corp",
            },
        )
        assert response.status_code == 201
        user = User.objects.get(email="jwt@test.com")
        assert user.check_password("securepass123")
        assert not user.profile.email_verified

    def test_register_duplicate_email(self):
        self.client.post(
            self.url,
            {
                "email": "dupe-api@test.com",
                "password": "securepass123",
                "org_name": "Org 1",
            },
        )
        response = self.client.post(
            self.url,
            {
                "email": "dupe-api@test.com",
                "password": "securepass123",
                "org_name": "Org 2",
            },
        )
        assert response.status_code == 400
        assert "existe déjà" in response.json()["detail"]

    def test_register_missing_fields(self):
        response = self.client.post(self.url, {"email": "no-pass@test.com"})
        assert response.status_code == 400

    def test_register_password_too_short(self):
        response = self.client.post(
            self.url,
            {
                "email": "short@test.com",
                "password": "short",
                "org_name": "Short Corp",
            },
        )
        assert response.status_code == 400

    def test_register_invalid_email(self):
        response = self.client.post(
            self.url,
            {
                "email": "not-an-email",
                "password": "securepass123",
                "org_name": "Bad Email Corp",
            },
        )
        assert response.status_code == 400

    def test_register_no_auth_required(self):
        """Register endpoint should be accessible without authentication."""
        response = self.client.post(
            self.url,
            {
                "email": "anon@test.com",
                "password": "securepass123",
                "org_name": "Anon Corp",
            },
        )
        assert response.status_code == 201


# --- OrganizationMiddleware ---


def _make_get_response(status_code=200):
    """Create a simple get_response callable for middleware."""
    from django.http import HttpResponse

    def get_response(request):
        return HttpResponse(status=status_code)

    return get_response


@pytest.mark.django_db
class TestOrganizationMiddleware:
    def _make_request(self, user=None, path="/api/v1/test/", **meta):
        factory = RequestFactory()
        request = factory.get(path, **meta)
        if user:
            request.user = user
        else:
            from django.contrib.auth.models import AnonymousUser

            request.user = AnonymousUser()
        request.session = {}
        return request

    def test_jwt_with_x_organization_uuid(self, org, owner_user):
        middleware = OrganizationMiddleware(_make_get_response())
        request = self._make_request(
            user=owner_user,
            HTTP_X_ORGANIZATION=str(org.uuid),
        )

        middleware(request)

        assert request.organization == org

    def test_jwt_with_x_organization_slug(self, org, owner_user):
        middleware = OrganizationMiddleware(_make_get_response())
        request = self._make_request(
            user=owner_user,
            HTTP_X_ORGANIZATION=org.slug,
        )

        middleware(request)

        assert request.organization == org

    def test_session_org_id(self, org, owner_user):
        middleware = OrganizationMiddleware(_make_get_response())
        request = self._make_request(user=owner_user)
        request.session["organization_id"] = org.pk

        middleware(request)

        assert request.organization == org

    def test_fallback_first_membership(self, org, owner_user):
        middleware = OrganizationMiddleware(_make_get_response())
        request = self._make_request(user=owner_user)

        middleware(request)

        assert request.organization == org

    def test_no_membership_returns_none(self):
        user = User.objects.create_user(
            username="lonely@test.local", email="lonely@test.local", password="pass123"
        )
        middleware = OrganizationMiddleware(_make_get_response())
        request = self._make_request(user=user)

        middleware(request)

        assert request.organization is None

    def test_anonymous_user_returns_none(self):
        middleware = OrganizationMiddleware(_make_get_response())
        request = self._make_request()

        middleware(request)

        assert request.organization is None

    def test_x_organization_wrong_org_returns_none(self, org, owner_user):
        """User doesn't have membership in the requested org."""
        other_org = Organization.objects.create(name="Other", slug="mw-other")
        middleware = OrganizationMiddleware(_make_get_response())
        request = self._make_request(
            user=owner_user,
            HTTP_X_ORGANIZATION=str(other_org.uuid),
        )

        middleware(request)

        # Falls back to first membership
        assert request.organization == org


# --- APIVersionMiddleware ---


@pytest.mark.django_db
class TestAPIVersionMiddleware:
    def test_api_path_gets_header(self):
        factory = RequestFactory()
        request = factory.get("/api/v1/invoices/")

        middleware = APIVersionMiddleware(_make_get_response())
        response = middleware(request)

        assert response["X-API-Version"] == "v1"

    def test_non_api_path_no_header(self):
        factory = RequestFactory()
        request = factory.get("/dashboard/")

        middleware = APIVersionMiddleware(_make_get_response())
        response = middleware(request)

        assert "X-API-Version" not in response


# --- Permissions ---


@pytest.mark.django_db
class TestPermissions:
    def _make_request(self, user=None, organization=None):
        factory = RequestFactory()
        request = factory.get("/")
        request.user = user
        request.organization = organization
        return request

    # --- HasOrganization ---

    def test_has_organization_pass(self, org, owner_user):
        request = self._make_request(user=owner_user, organization=org)
        assert HasOrganization().has_permission(request, None) is True

    def test_has_organization_fail(self, owner_user):
        request = self._make_request(user=owner_user, organization=None)
        assert HasOrganization().has_permission(request, None) is False

    # --- IsOwner ---

    def test_is_owner_with_owner(self, org, owner_user):
        request = self._make_request(user=owner_user, organization=org)
        assert IsOwner().has_permission(request, None) is True

    def test_is_owner_with_member(self, org, member_user):
        request = self._make_request(user=member_user, organization=org)
        assert IsOwner().has_permission(request, None) is False

    def test_is_owner_no_org(self, owner_user):
        request = self._make_request(user=owner_user, organization=None)
        assert IsOwner().has_permission(request, None) is False

    # --- IsMember ---

    def test_is_member_with_owner(self, org, owner_user):
        request = self._make_request(user=owner_user, organization=org)
        assert IsMember().has_permission(request, None) is True

    def test_is_member_with_member(self, org, member_user):
        request = self._make_request(user=member_user, organization=org)
        assert IsMember().has_permission(request, None) is True

    def test_is_member_with_viewer(self, org, viewer_user):
        request = self._make_request(user=viewer_user, organization=org)
        assert IsMember().has_permission(request, None) is False

    # --- IsViewer ---

    def test_is_viewer_with_owner(self, org, owner_user):
        request = self._make_request(user=owner_user, organization=org)
        assert IsViewer().has_permission(request, None) is True

    def test_is_viewer_with_member(self, org, member_user):
        request = self._make_request(user=member_user, organization=org)
        assert IsViewer().has_permission(request, None) is True

    def test_is_viewer_with_viewer(self, org, viewer_user):
        request = self._make_request(user=viewer_user, organization=org)
        assert IsViewer().has_permission(request, None) is True

    def test_is_viewer_no_org(self, owner_user):
        request = self._make_request(user=owner_user, organization=None)
        assert IsViewer().has_permission(request, None) is False

    # --- IsCustomerAccess ---

    def test_is_customer_access(self, org):
        from apps.billing.factories import CustomerFactory

        customer = CustomerFactory(organization=org)
        user = User.objects.create_user(
            username="cust@test.local", email="cust@test.local", password="pass123"
        )
        OrganizationMembership.objects.create(
            user=user, organization=org, role="customer_access", customer=customer
        )

        request = self._make_request(user=user, organization=org)
        perm = IsCustomerAccess()
        assert perm.has_permission(request, None) is True
        assert perm.get_customer(request) == customer

    def test_is_customer_access_with_owner(self, org, owner_user):
        request = self._make_request(user=owner_user, organization=org)
        assert IsCustomerAccess().has_permission(request, None) is False

    def test_is_customer_access_get_customer_not_found(self, org, owner_user):
        """get_customer returns None for non-customer_access users."""
        request = self._make_request(user=owner_user, organization=org)
        assert IsCustomerAccess().get_customer(request) is None

    # --- Unauthenticated user with org ---

    def test_is_owner_unauthenticated(self, org):
        from django.contrib.auth.models import AnonymousUser

        request = self._make_request(user=AnonymousUser(), organization=org)
        assert IsOwner().has_permission(request, None) is False

    def test_is_member_unauthenticated(self, org):
        from django.contrib.auth.models import AnonymousUser

        request = self._make_request(user=AnonymousUser(), organization=org)
        assert IsMember().has_permission(request, None) is False

    def test_is_viewer_unauthenticated(self, org):
        from django.contrib.auth.models import AnonymousUser

        request = self._make_request(user=AnonymousUser(), organization=org)
        assert IsViewer().has_permission(request, None) is False

    def test_is_customer_access_unauthenticated(self, org):
        from django.contrib.auth.models import AnonymousUser

        request = self._make_request(user=AnonymousUser(), organization=org)
        assert IsCustomerAccess().has_permission(request, None) is False

    # --- No org ---

    def test_is_member_no_org(self, member_user):
        request = self._make_request(user=member_user, organization=None)
        assert IsMember().has_permission(request, None) is False

    def test_is_customer_access_no_org(self, org):
        from apps.billing.factories import CustomerFactory

        customer = CustomerFactory(organization=org)
        user = User.objects.create_user(
            username="cust2@test.local", email="cust2@test.local", password="pass123"
        )
        OrganizationMembership.objects.create(
            user=user, organization=org, role="customer_access", customer=customer
        )
        request = self._make_request(user=user, organization=None)
        assert IsCustomerAccess().has_permission(request, None) is False


# --- EmailBackend ---


@pytest.mark.django_db
class TestEmailBackend:
    def setup_method(self):
        from apps.core.backends import EmailBackend

        self.backend = EmailBackend()
        self.user = User.objects.create_user(
            username="auth@test.local",
            email="auth@test.local",
            password="correctpass",
        )

    def test_authenticate_valid_email_password(self):
        result = self.backend.authenticate(
            request=None, email="auth@test.local", password="correctpass"
        )
        assert result == self.user

    def test_authenticate_wrong_password(self):
        result = self.backend.authenticate(
            request=None, email="auth@test.local", password="wrongpass"
        )
        assert result is None

    def test_authenticate_nonexistent_email(self):
        result = self.backend.authenticate(
            request=None, email="nobody@test.local", password="whatever"
        )
        assert result is None

    def test_authenticate_email_none(self):
        result = self.backend.authenticate(
            request=None, email=None, password="whatever"
        )
        assert result is None

    def test_authenticate_password_none(self):
        result = self.backend.authenticate(
            request=None, email="auth@test.local", password=None
        )
        assert result is None

    def test_authenticate_via_username_kwarg(self):
        """Falls back to kwargs['username'] when email is None."""
        result = self.backend.authenticate(
            request=None, username="auth@test.local", password="correctpass"
        )
        assert result == self.user

    def test_authenticate_inactive_user(self):
        self.user.is_active = False
        self.user.save()
        result = self.backend.authenticate(
            request=None, email="auth@test.local", password="correctpass"
        )
        assert result is None


# --- OrganizationJWTAuthentication ---


@pytest.mark.django_db
class TestOrganizationJWTAuthentication:
    def test_authenticate_sets_jwt_user(self, owner_user):
        from unittest.mock import MagicMock, patch

        from apps.core.authentication import OrganizationJWTAuthentication

        auth = OrganizationJWTAuthentication()
        mock_token = MagicMock()
        request = RequestFactory().get("/api/v1/test/")

        with patch.object(
            auth.__class__.__bases__[0],
            "authenticate",
            return_value=(owner_user, mock_token),
        ):
            result = auth.authenticate(request)

        assert result is not None
        user, token = result
        assert user == owner_user
        assert token == mock_token
        assert request.organization is not None

    def test_authenticate_returns_none_no_token(self):
        from unittest.mock import patch

        from apps.core.authentication import OrganizationJWTAuthentication

        auth = OrganizationJWTAuthentication()
        request = RequestFactory().get("/api/v1/test/")

        with patch.object(
            auth.__class__.__bases__[0],
            "authenticate",
            return_value=None,
        ):
            result = auth.authenticate(request)

        assert result is None
        assert not hasattr(request, "_jwt_user")


# --- custom_exception_handler ---


@pytest.mark.django_db
class TestCustomExceptionHandler:
    def setup_method(self):
        from apps.core.exceptions import custom_exception_handler

        self.handler = custom_exception_handler

    def _make_context(self):
        request = RequestFactory().get("/api/v1/test/")
        return {"request": request, "view": None}

    def test_validation_error_field_errors(self):
        from rest_framework.exceptions import ValidationError

        exc = ValidationError({"name": ["This field is required."]})
        response = self.handler(exc, self._make_context())

        assert response.status_code == 400
        body = response.data
        assert body["error"]["details"][0]["field"] == "name"
        assert body["error"]["details"][0]["message"] == "This field is required."

    def test_non_field_errors_single(self):
        from rest_framework.exceptions import ValidationError

        exc = ValidationError({"non_field_errors": ["Something went wrong."]})
        response = self.handler(exc, self._make_context())

        body = response.data
        # Single non_field_error is lifted to top-level message, details is empty
        assert body["error"]["message"] == "Something went wrong."
        assert body["error"]["details"] == []

    def test_non_field_errors_multiple(self):
        from rest_framework.exceptions import ValidationError

        exc = ValidationError({"non_field_errors": ["Error one.", "Error two."]})
        response = self.handler(exc, self._make_context())

        body = response.data
        assert body["error"]["message"] == "The payload contains validation errors."
        assert len(body["error"]["details"]) == 2
        assert body["error"]["details"][0]["field"] is None
        assert body["error"]["details"][1]["field"] is None

    def test_nested_dict_errors(self):
        from rest_framework.exceptions import ValidationError

        exc = ValidationError({"address": {"city": ["Required."]}})
        response = self.handler(exc, self._make_context())

        body = response.data
        assert body["error"]["details"][0]["field"] == "address.city"
        assert body["error"]["details"][0]["message"] == "Required."

    def test_single_detail_no_details_array(self):
        from rest_framework.exceptions import NotFound

        exc = NotFound("Not found.")
        response = self.handler(exc, self._make_context())

        body = response.data
        assert body["error"]["message"] == "Not found."
        assert body["error"]["details"] == []

    def test_string_errors_in_detail_list(self):
        from rest_framework.exceptions import ValidationError

        exc = ValidationError({"detail": ["First error.", "Second error."]})
        response = self.handler(exc, self._make_context())

        body = response.data
        # detail list items should appear with field=None in non_field_errors style
        # but field="detail" since it's the key "detail" (not "non_field_errors")
        assert len(body["error"]["details"]) == 2
        assert body["error"]["details"][0]["message"] == "First error."

    def test_none_response_passthrough(self):
        """Non-DRF exceptions return None."""
        exc = RuntimeError("unexpected")
        result = self.handler(exc, self._make_context())
        assert result is None

    def test_multiple_field_errors(self):
        from rest_framework.exceptions import ValidationError

        exc = ValidationError(
            {
                "name": ["Required."],
                "email": ["Invalid format."],
            }
        )
        response = self.handler(exc, self._make_context())

        body = response.data
        assert body["error"]["message"] == "The payload contains validation errors."
        assert len(body["error"]["details"]) == 2

    def test_permission_denied(self):
        from rest_framework.exceptions import PermissionDenied

        exc = PermissionDenied("No access.")
        response = self.handler(exc, self._make_context())

        body = response.data
        assert response.status_code == 403
        assert body["error"]["message"] == "No access."
        assert body["error"]["details"] == []
