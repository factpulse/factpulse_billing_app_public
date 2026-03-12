#!/bin/bash
set -e

echo "==> Waiting for database..."
retries=30
until uv run python -c "
import django
django.setup()
from django.db import connection
connection.ensure_connection()
" 2>&1; do
    retries=$((retries - 1))
    if [ "$retries" -le 0 ]; then
        echo "ERROR: database not reachable after 60s"
        exit 1
    fi
    echo "    Database not ready, retrying in 2s... ($retries attempts left)"
    sleep 2
done
echo "==> Database is ready"

# Only the web container runs migrations, collectstatic and superuser creation.
# Celery workers/beat set SKIP_MIGRATE=1 to avoid race conditions on fresh databases.
if [ "${SKIP_MIGRATE:-0}" != "1" ]; then
    echo "==> Running migrations..."
    uv run python manage.py migrate --noinput

    echo "==> Collecting static files..."
    uv run python manage.py collectstatic --noinput || true

    echo "==> Compiling translations..."
    uv run python manage.py compilemessages || true

    # Create superuser if env vars are set
    if [ -n "$DJANGO_SUPERUSER_EMAIL" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
        echo "==> Creating superuser (if not exists)..."
        uv run python manage.py createsuperuser \
            --noinput \
            --email "$DJANGO_SUPERUSER_EMAIL" \
            2>/dev/null || true
    fi
else
    echo "==> Skipping migrations (SKIP_MIGRATE=1)"
fi

echo "==> Starting: $@"
exec "$@"
