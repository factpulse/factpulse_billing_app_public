import logging

from django.conf import settings
from django.db import connection
from django.http import JsonResponse

logger = logging.getLogger(__name__)


def healthz(request):
    checks = {}
    healthy = True

    # Database check
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception:
        logger.exception("Healthcheck: database unreachable")
        checks["database"] = "error"
        healthy = False

    # Redis check (via Celery broker URL)
    broker_url = getattr(settings, "CELERY_BROKER_URL", "")
    if "redis" in broker_url:
        try:
            import redis

            r = redis.Redis.from_url(broker_url, socket_connect_timeout=3)
            r.ping()
            checks["redis"] = "ok"
        except Exception:
            logger.exception("Healthcheck: redis unreachable")
            checks["redis"] = "error"
            healthy = False
    else:
        checks["redis"] = "skipped"

    status_code = 200 if healthy else 503
    return JsonResponse(
        {"status": "healthy" if healthy else "unhealthy", "checks": checks},
        status=status_code,
    )
