from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle


class UserRateThrottle(SimpleRateThrottle):
    """Throttle by authenticated user."""

    scope = "user"

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            return self.cache_format % {
                "scope": self.scope,
                "ident": f"user_{request.user.pk}",
            }
        return self.get_ident(request)


class AuthRateThrottle(AnonRateThrottle):
    """Stricter throttle for authentication endpoints."""

    scope = "auth"
