import logging
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

_DEFAULT_SECRET_KEY = "insecure-dev-key-change-me"  # nosec B105 — sentinel value, never used as actual secret
SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY or SECRET_KEY == _DEFAULT_SECRET_KEY:
    from django.core.management.utils import get_random_secret_key

    SECRET_KEY = get_random_secret_key()
    logging.warning(
        "SECRET_KEY is not set — using an auto-generated ephemeral key. "
        "Set SECRET_KEY in your environment for stable sessions across restarts."
    )

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_filters",
    "django_celery_beat",
    "storages",
    "drf_spectacular",
    "oauth2_provider",
    # Local apps
    "apps.core",
    "apps.billing",
    "apps.webhooks",
    "apps.factpulse",
    "apps.ui",
    "apps.assistant",
    "apps.oauth",
]

# Payments (optional — zero footprint if disabled)
STRIPE_ENABLED = os.environ.get("STRIPE_ENABLED", "false").lower() in ("true", "1")

if STRIPE_ENABLED:
    INSTALLED_APPS.append("apps.payments")

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.OrganizationMiddleware",
    "apps.core.middleware.APIVersionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.ui.context_processors.ui_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "factpulse"),
        "USER": os.environ.get("POSTGRES_USER", "factpulse"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "factpulse"),
        "HOST": os.environ.get("POSTGRES_HOST", "db"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

AUTHENTICATION_BACKENDS = [
    "apps.core.backends.EmailBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]

LANGUAGE_CODE = "fr"
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_L10N = True
USE_TZ = True

LANGUAGES = [
    ("fr", "Français"),
    ("en", "English"),
]

LOCALE_PATHS = [
    BASE_DIR / "locale",
]

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Cookie security
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"

# Celery
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "Europe/Paris"
CELERY_BEAT_SCHEDULE = {
    "poll-cdar-events": {
        "task": "apps.factpulse.tasks.poll_cdar_events",
        "schedule": int(os.environ.get("CDAR_POLL_INTERVAL_SECONDS", "900")),
    },
}

# DRF
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.core.authentication.OrganizationJWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.CursorPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "apps.core.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": "100/min",
        "auth": "10/min",
    },
    "EXCEPTION_HANDLER": "apps.core.exceptions.custom_exception_handler",
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.URLPathVersioning",
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ["v1"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

# drf-spectacular
_SERVERS = [
    {"url": s.strip(), "description": d}
    for s, d in zip(
        os.environ.get("API_SERVERS", "http://localhost:8000").split(","),
        os.environ.get("API_SERVER_DESCRIPTIONS", "Local dev").split(","),
        strict=False,
    )
    if s.strip()
]

SPECTACULAR_SETTINGS = {
    "TITLE": "FactPulse Billing API",
    "DESCRIPTION": (
        "B2B invoicing API compliant with Factur-X (EN 16931), "
        "connected to the FactPulse platform for electronic invoice generation "
        "and transmission via an accredited service provider.\n\n"
        "## Authentication\n\n"
        "The API uses **JWT Bearer** tokens. "
        "Obtain a token via `POST /api/v1/auth/token/` with your credentials "
        "(email + password), then pass it in the header:\n\n"
        "```\nAuthorization: Bearer <access_token>\n```\n\n"
        "The access token expires after 1 hour. "
        "Use `POST /api/v1/auth/token/refresh/` with the refresh token "
        "to obtain a new one."
    ),
    "VERSION": "1.0.0",
    "SERVERS": _SERVERS,
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/v[0-9]+",
    "COMPONENT_SPLIT_REQUEST": True,
    "TAGS": [
        {"name": "auth", "description": "JWT authentication"},
        {"name": "invoices", "description": "Invoice management"},
        {"name": "suppliers", "description": "Supplier management"},
        {"name": "customers", "description": "Customer management"},
        {"name": "products", "description": "Product catalog"},
        {"name": "webhooks", "description": "Outbound webhooks"},
        {"name": "payments", "description": "Payment integration (Stripe)"},
    ],
}

# CORS
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = os.environ.get(
    "CORS_ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
).split(",")

# S3 / django-storages
DEFAULT_FILE_STORAGE = "storages.backends.s3boto3.S3Boto3Storage"
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "minioadmin")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "minioadmin")
AWS_STORAGE_BUCKET_NAME = os.environ.get("AWS_STORAGE_BUCKET_NAME", "factpulse")
AWS_S3_ENDPOINT_URL = os.environ.get("AWS_S3_ENDPOINT_URL", "http://minio:9000")
AWS_S3_FILE_OVERWRITE = False
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = True

# FactPulse API
FACTPULSE_API_URL = os.environ.get("FACTPULSE_API_URL", "")
FACTPULSE_EMAIL = os.environ.get("FACTPULSE_EMAIL", "")
FACTPULSE_PASSWORD = os.environ.get("FACTPULSE_PASSWORD", "")

# Email
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True").lower() in ("true", "1")
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "False").lower() in ("true", "1")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@factpulse.local")

# Login
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

# Simple JWT
from datetime import timedelta  # noqa: E402

# OAuth 2.1 (django-oauth-toolkit) — for Claude Desktop MCP connector
OAUTH2_PROVIDER = {  # nosec B105 — these are config keys, not passwords
    "PKCE_REQUIRED": True,
    "ALLOWED_REDIRECT_URI_SCHEMES": ["http", "https"],
    "ACCESS_TOKEN_EXPIRE_SECONDS": 3600,
    "REFRESH_TOKEN_EXPIRE_SECONDS": 604800,
    "ROTATE_REFRESH_TOKEN": True,
    "SCOPES": {"mcp": "Access MCP tools"},
    "DEFAULT_SCOPES": ["mcp"],
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}
