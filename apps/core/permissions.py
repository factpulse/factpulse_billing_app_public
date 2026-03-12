from rest_framework import permissions

from apps.core.models import OrganizationMembership

R = OrganizationMembership.Role


def _has_role(request, allowed_roles):
    """Check if the authenticated user has one of the allowed roles in the current org."""
    if not request.organization:
        return False
    if not request.user or not request.user.is_authenticated:
        return False
    return OrganizationMembership.objects.filter(
        user=request.user,
        organization=request.organization,
        role__in=allowed_roles,
    ).exists()


class HasOrganization(permissions.BasePermission):
    """Requires request.organization to be set."""

    message = "No organization context. Authenticate and ensure you belong to an organization."

    def has_permission(self, request, view):
        return request.organization is not None


class IsOwner(permissions.BasePermission):
    """Requires owner role in the current organization."""

    def has_permission(self, request, view):
        return _has_role(request, [R.OWNER])


class IsMember(permissions.BasePermission):
    """Requires member+ role (owner or member) in the current organization."""

    def has_permission(self, request, view):
        return _has_role(request, [R.OWNER, R.MEMBER])


class IsViewer(permissions.BasePermission):
    """Requires at least viewer role (owner, member, or viewer) in the current organization."""

    def has_permission(self, request, view):
        return _has_role(request, [R.OWNER, R.MEMBER, R.VIEWER])


class IsCustomerAccess(permissions.BasePermission):
    """For customer_access users — can only view their own invoices."""

    def has_permission(self, request, view):
        return _has_role(request, [R.CUSTOMER_ACCESS])

    def get_customer(self, request):
        """Return the Customer linked to this customer_access user."""
        try:
            membership = OrganizationMembership.objects.get(
                user=request.user,
                organization=request.organization,
                role=R.CUSTOMER_ACCESS,
            )
            return membership.customer
        except OrganizationMembership.DoesNotExist:
            return None
