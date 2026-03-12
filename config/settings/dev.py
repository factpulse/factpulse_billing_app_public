import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env for local dev (Docker Compose does this automatically)
_env_file = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_file, override=False)

from .base import *  # noqa: E402,F401,F403

DEBUG = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "loggers": {
        "apps.factpulse": {
            "handlers": ["console"],
            "level": "DEBUG",
        },
    },
}

CORS_ALLOW_ALL_ORIGINS = True

# Mode local sans Docker : pas de POSTGRES_HOST = pas de Redis ni MinIO non plus
_local_mode = os.environ.get("USE_SQLITE", "") or not os.environ.get("POSTGRES_HOST")

if _local_mode:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
        }
    }
    # Celery synchrone (pas de Redis en local)
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True
    # Filesystem (pas de MinIO en local)
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
else:
    # Docker : utiliser les services configurés dans .env
    if not os.environ.get("CELERY_BROKER_URL"):
        CELERY_TASK_ALWAYS_EAGER = True
        CELERY_TASK_EAGER_PROPAGATES = True
    if os.environ.get("AWS_S3_ENDPOINT_URL"):
        STORAGES = {
            "default": {
                "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
            },
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
            },
        }
