import uuid as uuid_lib

from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.core.models import OrganizationMembership


class OrganizationJWTAuthentication(JWTAuthentication):
    """JWT authentication that also resolves the user's organization.

    The middleware runs before DRF authentication, so request.organization
    is not set yet for JWT requests. This authenticator resolves the
    organization from the user's membership after JWT validation.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, token = result
        # Resolve organization (middleware couldn't because user was anonymous)
        django_request = request._request if hasattr(request, "_request") else request
        if not getattr(django_request, "organization", None):
            django_request.organization = self._resolve_organization(
                user, django_request
            )
        return (user, token)

    @staticmethod
    def _resolve_organization(user, request):
        # 1. Try X-Organization header
        x_org = request.META.get("HTTP_X_ORGANIZATION")
        if x_org:
            try:
                org_uuid = uuid_lib.UUID(x_org)
                lookup = {"organization__uuid": org_uuid}
            except ValueError:
                lookup = {"organization__slug": x_org}
            try:
                membership = OrganizationMembership.objects.select_related(
                    "organization"
                ).get(user=user, **lookup)
                return membership.organization
            except OrganizationMembership.DoesNotExist:
                return None

        # 2. Fallback to first membership
        membership = (
            OrganizationMembership.objects.select_related("organization")
            .filter(user=user)
            .first()
        )
        return membership.organization if membership else None
