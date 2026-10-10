"""Copy uploaded files from an S3-compatible bucket (e.g. MinIO) to MEDIA_ROOT.

One-shot migration from S3 storage to the local `media_data` volume. Objects are
written under MEDIA_ROOT with their key as path, which is the FileField name, so
existing database references keep working once storage switches to the
filesystem. Files already present with the same size are skipped; nothing is
deleted from the bucket.

Connection settings default to the AWS_* settings (.env), so run it BEFORE
removing them from .env:

    # Preview, change nothing
    python manage.py copy_media_from_s3 --dry-run

    # Copy, then check every Invoice PDF / Supplier logo is present locally
    python manage.py copy_media_from_s3
"""

from pathlib import Path

import boto3
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.billing.models import Invoice, Supplier


class Command(BaseCommand):
    help = "Copy uploaded files from an S3/MinIO bucket to MEDIA_ROOT."

    def add_arguments(self, parser):
        parser.add_argument("--bucket", default=settings.AWS_STORAGE_BUCKET_NAME)
        parser.add_argument("--endpoint", default=settings.AWS_S3_ENDPOINT_URL)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        media_root = Path(settings.MEDIA_ROOT).resolve()
        dry_run = options["dry_run"]
        s3 = boto3.client(
            "s3",
            endpoint_url=options["endpoint"] or None,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )

        copied = skipped = 0
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=options["bucket"]):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                target = (media_root / key).resolve()
                if not target.is_relative_to(media_root):
                    raise CommandError(f"Unsafe object key: {key}")
                if target.exists() and target.stat().st_size == obj["Size"]:
                    skipped += 1
                    continue
                if not dry_run:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    s3.download_file(options["bucket"], key, str(target))
                copied += 1
                self.stdout.write(f"{'would copy' if dry_run else 'copied'} {key}")

        self.stdout.write(f"{copied} copied, {skipped} already present.")
        if dry_run:
            return

        missing = [
            name
            for name in [
                *Invoice.objects.exclude(pdf_file="").values_list(
                    "pdf_file", flat=True
                ),
                *Supplier.objects.exclude(logo="").values_list("logo", flat=True),
            ]
            if name and not (media_root / name).exists()
        ]
        for name in missing:
            self.stdout.write(self.style.WARNING(f"missing locally: {name}"))
        if missing:
            raise CommandError(f"{len(missing)} referenced file(s) missing locally.")
        self.stdout.write(self.style.SUCCESS("All referenced files are present."))
