from django.apps import apps
from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.views import TokenRefreshView

from apps.core.views import (
    APIKeyListCreateView,
    APIKeyRevokeView,
    EmailTokenObtainPairView,
    LogoutView,
    RegisterView,
)
from apps.oauth.views import authorization_server_metadata, protected_resource_metadata
from config.health import healthz
from config.views import scalar_docs_view

urlpatterns = [
    # OAuth 2.1 well-known endpoints (RFC 9728 + RFC 8414)
    path(
        ".well-known/oauth-protected-resource",
        protected_resource_metadata,
        name="oauth-protected-resource",
    ),
    path(
        ".well-known/oauth-authorization-server",
        authorization_server_metadata,
        name="oauth-authorization-server",
    ),
    # OAuth 2.1 endpoints (authorize, token, register, revoke)
    path("oauth/", include("apps.oauth.urls")),
    # Health check (no auth, used by load balancers / Docker)
    path("healthz/", healthz, name="healthz"),
    # JWT auth endpoints
    path(
        "api/v1/auth/token/",
        EmailTokenObtainPairView.as_view(),
        name="token_obtain_pair",
    ),
    path(
        "api/v1/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"
    ),
    path("api/v1/auth/register/", RegisterView.as_view(), name="register"),
    path("api/v1/auth/logout/", LogoutView.as_view(), name="logout_api"),
    # API keys
    path(
        "api/v1/auth/api-keys/",
        APIKeyListCreateView.as_view(),
        name="api_key_list_create",
    ),
    path(
        "api/v1/auth/api-keys/<uuid:uuid>/",
        APIKeyRevokeView.as_view(),
        name="api_key_revoke",
    ),
    # API endpoints (no i18n prefix — language-agnostic)
    path("api/v1/", include("apps.billing.urls")),
    path("api/v1/webhooks/", include("apps.webhooks.urls")),
    # OpenAPI schema + docs
    path(
        "api/v1/schema/",
        SpectacularAPIView.as_view(permission_classes=[IsAuthenticated]),
        name="schema",
    ),
    path("api/v1/docs/", scalar_docs_view, name="scalar-docs"),
    # Language switch
    path("i18n/", include("django.conf.urls.i18n")),
]

# Payments (optional — only registered if STRIPE_ENABLED=true)
if apps.is_installed("apps.payments"):
    urlpatterns.append(
        path("api/v1/payments/", include("apps.payments.urls")),
    )

# UI routes with i18n prefix
urlpatterns += i18n_patterns(
    path("admin/", admin.site.urls),
    path("", include("apps.ui.urls")),
    prefix_default_language=False,
)

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
