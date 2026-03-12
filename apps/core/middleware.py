import uuid as uuid_lib

from apps.core.models import OrganizationMembership


class OrganizationMiddleware:
    """Injects request.organization from authenticated user.

    - JWT auth: organization is resolved from the user's membership.
      API clients can specify org via X-Organization header (UUID or slug).
    - Session auth: organization is read from session, falling back to
      the user's first membership.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.organization = None

        if request.user and request.user.is_authenticated:
            # 1. Try X-Organization header (useful for API / JWT clients)
            x_org = request.META.get("HTTP_X_ORGANIZATION")
            if x_org:
                request.organization = self._resolve_from_header(request.user, x_org)

            # 2. Try session (UI clients)
            if not request.organization:
                org_id = request.session.get("organization_id")
                if org_id:
                    try:
                        membership = OrganizationMembership.objects.select_related(
                            "organization"
                        ).get(user=request.user, organization_id=org_id)
                        request.organization = membership.organization
                    except OrganizationMembership.DoesNotExist:
                        pass

            # 3. Fallback to first membership
            if not request.organization:
                membership = (
                    OrganizationMembership.objects.select_related("organization")
                    .filter(user=request.user)
                    .first()
                )
                if membership:
                    request.organization = membership.organization
                    request.session["organization_id"] = membership.organization_id

        return self.get_response(request)

    @staticmethod
    def _resolve_from_header(user, value):
        """Resolve organization from X-Organization header (UUID or slug).
        Returns the Organization only if the user has a membership."""
        try:
            org_uuid = uuid_lib.UUID(value)
            lookup = {"organization__uuid": org_uuid}
        except ValueError:
            lookup = {"organization__slug": value}

        try:
            membership = OrganizationMembership.objects.select_related(
                "organization"
            ).get(user=user, **lookup)
            return membership.organization
        except OrganizationMembership.DoesNotExist:
            return None


class APIVersionMiddleware:
    """Adds X-API-Version header to all API responses."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/api/"):
            response["X-API-Version"] = "v1"
        return response


class SecurityHeadersMiddleware:
    """Adds Content-Security-Policy and Permissions-Policy headers."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; "
                "font-src 'self'; "
                "connect-src 'self' https://api-adresse.data.gouv.fr; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self'"
            )
        if "X-Content-Type-Options" not in response:
            response["X-Content-Type-Options"] = "nosniff"
        if "Permissions-Policy" not in response:
            response["Permissions-Policy"] = (
                "camera=(), microphone=(), geolocation=(), "
                "payment=(), usb=(), magnetometer=(), gyroscope=(), accelerometer=()"
            )
        return response
