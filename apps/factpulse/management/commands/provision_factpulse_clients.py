"""Provision FactPulse clients for Organizations that don't have one yet."""

from django.core.management.base import BaseCommand

from apps.core.models import Organization
from apps.factpulse.client import FactPulseError, client


class Command(BaseCommand):
    help = (
        "Provision FactPulse clients for organizations without a factpulse_client_uid"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List organizations that would be provisioned without making API calls.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if not client.is_configured:
            self.stderr.write(
                self.style.ERROR(
                    "FactPulse API is not configured. "
                    "Set FACTPULSE_API_URL, FACTPULSE_EMAIL, and FACTPULSE_PASSWORD."
                )
            )
            return

        orgs = Organization.objects.filter(factpulse_client_uid__isnull=True)

        if not orgs.exists():
            self.stdout.write(
                self.style.SUCCESS("All organizations already have a FactPulse client.")
            )
            return

        self.stdout.write(
            f"Found {orgs.count()} organization(s) without FactPulse client."
        )

        if dry_run:
            for org in orgs:
                self.stdout.write(
                    f"  [DRY-RUN] Would provision: {org.name} (slug={org.slug})"
                )
            return

        provisioned = 0
        errors = 0

        for org in orgs:
            try:
                result = client.create_client(name=org.name)
                org.factpulse_client_uid = result["uid"]
                org.save(update_fields=["factpulse_client_uid"])
                provisioned += 1
                self.stdout.write(f"  Provisioned: {org.name} -> {result['uid']}")
            except FactPulseError as e:
                errors += 1
                self.stderr.write(f"  ERROR: {org.name} -> {e}")

        self.stdout.write(
            self.style.SUCCESS(f"\nDone: {provisioned} provisioned, {errors} error(s).")
        )
