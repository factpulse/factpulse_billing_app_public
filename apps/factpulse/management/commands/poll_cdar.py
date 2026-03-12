"""Poll CDAR lifecycle events — manual trigger with verbose output."""

import logging

from django.core.management.base import BaseCommand

from apps.billing.models import InvoiceAuditLog
from apps.factpulse.client import client
from apps.factpulse.tasks import poll_cdar_events


class Command(BaseCommand):
    help = "Poll CDAR lifecycle events via GET /cdar/lifecycle"

    def add_arguments(self, parser):
        parser.add_argument(
            "--invoice",
            type=str,
            help="Filter by invoice number (e.g. FA-2026-001).",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="Lookback window in days (default 7).",
        )

    def handle(self, *args, **options):
        invoice_number = options.get("invoice")
        days = options["days"]

        if not client.is_configured:
            self.stderr.write(
                self.style.ERROR(
                    "FactPulse API is not configured. "
                    "Set FACTPULSE_API_URL, FACTPULSE_EMAIL, and FACTPULSE_PASSWORD."
                )
            )
            return

        # Enable factpulse logger so task output is visible
        logging.getLogger("apps.factpulse").setLevel(logging.DEBUG)

        count_before = InvoiceAuditLog.objects.filter(action="cdar_event").count()

        self.stdout.write(
            f"Polling CDAR lifecycle (days={days}, invoice={invoice_number or 'all'})..."
        )

        poll_cdar_events(invoice_number=invoice_number, days=days)

        count_after = InvoiceAuditLog.objects.filter(action="cdar_event").count()
        new_events = count_after - count_before

        self.stdout.write(
            self.style.SUCCESS(f"Done. {new_events} new CDAR event(s) logged.")
        )
