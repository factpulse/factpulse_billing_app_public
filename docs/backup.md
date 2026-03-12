# Backup & Restore

This guide covers what to back up and how to restore the FactPulse Billing App.

## What to back up

| Data | Storage | Critical | Notes |
|------|---------|----------|-------|
| PostgreSQL database | `postgres_data` Docker volume or host DB | **Yes** | All invoices, customers, suppliers, users, webhook config, numbering sequences |
| Uploaded files (PDFs, logos) | MinIO (`minio_data` volume), AWS S3, or local `media/` | **Yes** | Factur-X PDFs, supplier logos |
| `.env` file | Host filesystem | **Yes** | Contains `SECRET_KEY`, database password, API credentials |

### What you do NOT need to back up

- **Redis** — ephemeral task queue, rebuilt automatically on restart
- **Static files** — regenerated via `collectstatic`
- **Python dependencies** — reinstalled from `uv.lock`

## PostgreSQL

### Docker deployment

```bash
# Dump (custom format, compressed)
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  exec db pg_dump -U factpulse -Fc factpulse_billing \
  > backup_$(date +%Y%m%d_%H%M%S).dump

# Plain SQL alternative (human-readable, larger)
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  exec db pg_dump -U factpulse factpulse_billing \
  > backup_$(date +%Y%m%d_%H%M%S).sql
```

### Manual deployment

```bash
pg_dump -U factpulse -Fc factpulse_billing > backup_$(date +%Y%m%d_%H%M%S).dump
```

### Automation (cron)

```bash
# /etc/cron.d/factpulse-backup
0 2 * * * root docker compose -f /path/to/docker-compose.yml -f /path/to/docker-compose.prod.yml exec -T db pg_dump -U factpulse -Fc factpulse_billing > /backups/factpulse_$(date +\%Y\%m\%d).dump 2>&1
```

> The `-T` flag disables pseudo-TTY allocation (required for non-interactive cron execution).

## Uploaded files

### MinIO (Docker default)

```bash
# Install mc (MinIO Client) on the host, then:
mc alias set local http://localhost:9000 minioadmin minioadmin
mc mirror local/factpulse-billing /backups/minio/factpulse-billing/
```

Or back up the Docker volume directly:

```bash
docker run --rm -v factpulse_billing_app_minio_data:/data -v /backups:/backup \
  alpine tar czf /backup/minio_$(date +%Y%m%d).tar.gz -C /data .
```

### AWS S3

If you use AWS S3 instead of MinIO, your files are already on a managed service. Enable [S3 versioning](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Versioning.html) on your bucket for built-in protection against accidental deletions.

### Local filesystem

If using local storage (`media/` directory):

```bash
tar czf /backups/media_$(date +%Y%m%d).tar.gz media/
```

## .env file

Store your `.env` file separately from database backups, ideally in a secrets manager (Vault, AWS Secrets Manager, 1Password) or an encrypted backup.

```bash
# Simple encrypted copy
gpg --symmetric --cipher-algo AES256 -o /backups/env_$(date +%Y%m%d).gpg .env
```

> Never commit `.env` to git. Never store it unencrypted alongside database dumps.

## Restore

### 1. Restore PostgreSQL

```bash
# Docker
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  exec -T db pg_restore -U factpulse -d factpulse_billing --clean --if-exists \
  < backup.dump

# Manual
pg_restore -U factpulse -d factpulse_billing --clean --if-exists backup.dump
```

After restoring, run migrations in case the backup is from an older version:

```bash
# Docker
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec web python manage.py migrate

# Manual
python manage.py migrate
```

### 2. Restore uploaded files

```bash
# MinIO (via mc)
mc mirror /backups/minio/factpulse-billing/ local/factpulse-billing/

# MinIO (via Docker volume)
docker run --rm -v factpulse_billing_app_minio_data:/data -v /backups:/backup \
  alpine tar xzf /backup/minio_YYYYMMDD.tar.gz -C /data

# Local filesystem
tar xzf /backups/media_YYYYMMDD.tar.gz
```

### 3. Restore .env

```bash
gpg -d /backups/env_YYYYMMDD.gpg > .env
```

### 4. Restart services

```bash
make prod
# or: docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

## Recommendations

- **Frequency**: daily database dumps at minimum. For high-volume usage, consider PostgreSQL continuous archiving (WAL).
- **Retention**: keep at least 7 daily + 4 weekly + 3 monthly backups.
- **Offsite storage**: copy backups to a separate location (different server, cloud bucket, etc.).
- **Test restores regularly**: a backup that has never been tested is not a backup.
- **Monitor failures**: make sure your cron job alerts you on failure (email, webhook, monitoring tool).
