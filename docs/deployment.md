# Deployment Guide

This guide covers deploying the FactPulse Billing App as a standalone application.

## Prerequisites

- **Docker** (recommended): Docker Engine 20+ and Docker Compose v2
- **Manual**: Python 3.12+, PostgreSQL 15+, Redis 7+, [uv](https://docs.astral.sh/uv/)

## Quick Start with Docker

```bash
# Clone and configure
git clone https://github.com/factpulse/factpulse_billing_app.git
cd factpulse_billing_app
cp .env.example .env
# Edit .env with your settings (see Environment Variables below)

# Development (hot-reload, PostgreSQL)
make dev

# The app is available at http://localhost:8000
```

## Production Deployment (Docker)

### 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set all required variables (see [Environment Variables](#environment-variables) below). At minimum:

```env
SECRET_KEY=<generate-a-strong-secret-key>
ALLOWED_HOSTS=billing.example.com
POSTGRES_PASSWORD=<strong-password>
CSRF_TRUSTED_ORIGINS=https://billing.example.com
CORS_ALLOWED_ORIGINS=https://billing.example.com
```

Generate a secret key:

```bash
python3 -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

> **Note**: If `SECRET_KEY` is not set, the app auto-generates an ephemeral key at startup with a warning. This is fine for quick testing but sessions will be lost on restart.

### 2. Start services

```bash
make prod
```

This starts gunicorn + WhiteNoise (static files), PostgreSQL, Redis, MinIO, Celery worker, and Celery Beat.

The entrypoint script automatically runs migrations, collects static files, and compiles translations on each startup.

### 3. Create admin account

**Option A** — Via environment variables (automatic on startup):

```env
DJANGO_SUPERUSER_EMAIL=admin@example.com
DJANGO_SUPERUSER_PASSWORD=your-strong-password
```

**Option B** — Interactively:

```bash
make createsuperuser
```

### 4. Configure reverse proxy

The application listens on `127.0.0.1:8100`. Place a reverse proxy (nginx, Caddy, Traefik) in front to handle SSL.

An example nginx configuration is provided in `nginx/example.conf`:

```bash
sudo cp nginx/example.conf /etc/nginx/sites-available/factpulse-billing
# Edit the file: replace YOUR_DOMAIN with your actual domain
sudo ln -s /etc/nginx/sites-available/factpulse-billing /etc/nginx/sites-enabled/
sudo certbot --nginx -d billing.example.com
sudo nginx -t && sudo systemctl reload nginx
```

## Self-Hosted Deployment (all-in-one with Caddy)

For self-hosted deployments without an existing reverse proxy, a Caddy-based stack is provided with automatic SSL (Let's Encrypt).

### 1. Configure

```bash
cp .env.example .env
```

Set at minimum:

```env
DOMAIN=billing.example.com
SECRET_KEY=<generate-a-strong-secret-key>
POSTGRES_PASSWORD=<strong-password>
DJANGO_SUPERUSER_EMAIL=admin@example.com
DJANGO_SUPERUSER_PASSWORD=<strong-password>
```

### 2. Start

```bash
make selfhost
```

This starts all services (PostgreSQL, Redis, MinIO, web, Celery worker, Celery beat) plus a Caddy reverse proxy that automatically provisions SSL certificates via Let's Encrypt.

`SECURE_SSL_REDIRECT` is set to `false` automatically — Caddy handles SSL termination.

### 3. Verify

```bash
curl https://billing.example.com/healthz/
# → {"status": "healthy", "checks": {"database": "ok", "redis": "ok"}}
```

### Management

```bash
make selfhost-logs   # Tail logs
make selfhost-stop   # Stop all services
```

## One-Click Cloud Deploy

Platform-specific configuration files are included:

| Platform | File | Notes |
|----------|------|-------|
| **Railway** | `railway.toml` | Dockerfile builder, healthcheck on `/healthz/` |
| **Render** | `render.yaml` | Web + worker + managed DB + Redis, auto-generated `SECRET_KEY` |
| **Heroku / DigitalOcean** | `app.json` | Addons for Postgres + Redis, auto-generated `SECRET_KEY` |

Each platform config uses the Docker entrypoint which handles migrations, static files, and superuser creation automatically.

## Manual Deployment (without Docker)

### 1. Install dependencies

```bash
uv sync --no-dev
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env: set DJANGO_SETTINGS_MODULE=config.settings.prod
# Configure PostgreSQL and Redis connection strings
```

### 3. Set up PostgreSQL

```bash
sudo -u postgres createdb factpulse_billing
sudo -u postgres createuser factpulse
sudo -u postgres psql -c "ALTER USER factpulse WITH PASSWORD 'your-password';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE factpulse_billing TO factpulse;"
```

### 4. Run migrations and collect static files

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

### 5. Start the application

```bash
# Web server
uv run gunicorn config.wsgi:application --bind 127.0.0.1:8100 --workers 4

# Celery worker (separate terminal/service)
uv run celery -A config.celery worker --loglevel=info

# Celery Beat (separate terminal/service)
uv run celery -A config.celery beat --loglevel=info
```

Use systemd or supervisor to manage these processes in production.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SECRET_KEY` | Recommended | auto-generated | Django secret key (generate a unique one for stable sessions) |
| `DEBUG` | No | `False` | Set to `True` only for development |
| `ALLOWED_HOSTS` | Yes | - | Comma-separated hostnames |
| `DOMAIN` | No | `localhost` | Domain for the self-hosted Caddy stack |
| `SECURE_SSL_REDIRECT` | No | `true` | Set to `false` when SSL is handled by Caddy or a cloud platform |
| `POSTGRES_DB` | Yes | `factpulse_billing` | Database name |
| `POSTGRES_USER` | Yes | `factpulse` | Database user |
| `POSTGRES_PASSWORD` | Yes | - | Database password |
| `POSTGRES_HOST` | No | `db` | Database host |
| `POSTGRES_PORT` | No | `5432` | Database port |
| `CELERY_BROKER_URL` | Yes | `redis://redis:6379/0` | Redis URL for Celery |
| `DJANGO_SUPERUSER_EMAIL` | No | - | Auto-create superuser on startup |
| `DJANGO_SUPERUSER_PASSWORD` | No | - | Password for auto-created superuser |
| `AWS_ACCESS_KEY_ID` | No | - | S3/MinIO access key |
| `AWS_SECRET_ACCESS_KEY` | No | - | S3/MinIO secret key |
| `AWS_STORAGE_BUCKET_NAME` | No | `factpulse-billing` | S3/MinIO bucket name |
| `AWS_S3_ENDPOINT_URL` | No | - | MinIO endpoint (omit for AWS S3) |
| `CORS_ALLOWED_ORIGINS` | No | - | CORS origins (comma-separated) |
| `CSRF_TRUSTED_ORIGINS` | No | - | CSRF trusted origins |
| `EMAIL_BACKEND` | No | `console` | Email backend class |
| `DEFAULT_FROM_EMAIL` | No | - | Default sender address |

## FactPulse Connection (optional)

To enable Factur-X PDF generation and transmission via plateforme agréée, configure:

```env
FACTPULSE_API_URL=https://your-factpulse-instance.example.com
FACTPULSE_EMAIL=billing@example.com
FACTPULSE_PASSWORD=your-password
```

**Degraded mode**: Without these variables, the application works fully except for:
- Factur-X PDF generation (requires FactPulse API)
- Transmission via plateforme agréée (requires FactPulse API)

All other features (invoices, suppliers, customers, products, API, webhooks, portal) remain operational.

### Stripe Payments (optional)

Enable payment link generation and automatic payment reconciliation via Stripe:

```env
STRIPE_ENABLED=true
```

Install the Stripe dependency:

```bash
uv sync --extra stripe
```

Then configure your Stripe API key and webhook secret via the API or portal UI (Settings > Payments). Your Stripe Dashboard must be configured to send webhooks to `https://your-domain.com/api/v1/payments/webhooks/stripe/`.

**Zero footprint**: When `STRIPE_ENABLED` is not set or `false`, no payment routes, models, or migrations are loaded.

#### GoCardless (SEPA Direct Debit)

Install the GoCardless dependency:

```bash
uv sync --extra gocardless
```

Configure via the API or portal (Settings > Payments) with your GoCardless access token and webhook secret. Set `config.environment` to `"sandbox"` for testing.

Webhook URL: `https://your-domain.com/api/v1/payments/webhooks/gocardless/`

#### Fintecture (Open Banking)

No additional dependency required (uses `requests`). Configure via the API with your Fintecture `app_id`, `webhook_secret`, and `config.app_secret`.

Webhook URL: `https://your-domain.com/api/v1/payments/webhooks/fintecture/`

#### All payment providers at once

```bash
uv sync --extra payments  # installs stripe + gocardless-pro
```

## Storage Backends

### MinIO (default for Docker)

The Docker Compose setup includes a MinIO instance for S3-compatible storage. PDFs and uploaded files are stored there.

```env
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin
AWS_STORAGE_BUCKET_NAME=factpulse-billing
AWS_S3_ENDPOINT_URL=http://minio:9000
```

### AWS S3

For AWS S3, remove `AWS_S3_ENDPOINT_URL` and configure:

```env
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
AWS_STORAGE_BUCKET_NAME=your-bucket-name
AWS_S3_REGION_NAME=eu-west-1
```

### Local filesystem

For development without object storage, the application falls back to local file storage in the `media/` directory.

## Architecture

### Production (behind your own reverse proxy)

```
                     Reverse Proxy (nginx/Caddy)
                            |
                     :8100  |
         +------------------+-------------------+
         |      Docker Compose                  |
         |  +--------+  +--------+  +--------+  |
         |  |  web   |  | celery |  |  beat  |  |
         |  |gunicorn|  | worker |  |scheduler| |
         |  +---+----+  +---+----+  +---+----+  |
         |      |            |           |       |
         |  +---+--+  +-----+--+  +-----+--+   |
         |  |Postgres| |  Redis |  |  MinIO |   |
         |  +--------+ +--------+  +--------+   |
         +--------------------------------------+
```

### Self-hosted (all-in-one with Caddy)

```
         Internet (:80/:443)
               |
         +-----+--------------------------------------+
         |     Caddy (auto SSL)                       |
         |     |                                      |
         |  +--+-----+  +--------+  +--------+       |
         |  |  web   |  | celery |  |  beat  |       |
         |  |gunicorn|  | worker |  |scheduler|      |
         |  +---+----+  +---+----+  +---+----+       |
         |      |            |           |            |
         |  +---+--+  +-----+--+  +-----+--+        |
         |  |Postgres| |  Redis |  |  MinIO |        |
         |  +--------+ +--------+  +--------+        |
         +--------------------------------------------+
```

## Monitoring

- **Health check**: `GET /healthz/` returns `{"status": "healthy", "checks": {"database": "ok", "redis": "ok"}}` (no authentication required)
- **Logs**: `make prod-logs` or `make selfhost-logs`
- **Celery**: Monitor with `celery -A config.celery inspect active`
