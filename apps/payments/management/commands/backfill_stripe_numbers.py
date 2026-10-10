"""Backfill Stripe invoice numbers (and issue dates) onto imported invoices.

Older imports either left FactPulse-imported Stripe invoices without their Stripe
number, or let FactPulse generate its own number at validation — both break
Stripe's gapless legal sequence. This command re-fetches each imported invoice
from Stripe (matched by external_id "in_...") and copies its number + issue
(finalization) date back onto the FactPulse invoice.

Safety model — an invoice number that has been legally emitted must never be
silently rewritten:

* DRAFT invoices are (re)numbered freely: their number was never transmitted to
  the plateforme agréée, so replacing "FACT--023" with the Stripe number is safe.
* validated / transmitted / accepted / paid / ... invoices are NEVER touched by
  default (their number is legally emitted). They are only reported. The
  --force-emitted flag lifts that guard, for exceptional manual repairs.
* An invoice that already carries the exact Stripe number is left untouched.

    # Preview everything, change nothing (always start here)
    python manage.py backfill_stripe_numbers --dry-run

    # Apply on drafts, single organization
    python manage.py backfill_stripe_numbers --org <org_uuid>
"""

import stripe
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.billing.models import Invoice
from apps.payments.models import ProviderConfig
from apps.payments.providers.stripe.mapper import _resolve_issue_date


class Command(BaseCommand):
    help = "Copy Stripe invoice numbers + issue dates onto imported FactPulse invoices."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org",
            dest="org_uuid",
            help="Limit to a single organization (UUID). Default: all orgs.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without writing anything.",
        )
        parser.add_argument(
            "--force-emitted",
            action="store_true",
            help="Also rewrite the number of non-draft (legally emitted) invoices. "
            "DANGEROUS — only for exceptional manual repairs. Off by default.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force_emitted = options["force_emitted"]

        configs = ProviderConfig.objects.filter(
            provider=ProviderConfig.Provider.STRIPE, is_active=True
        )
        if options.get("org_uuid"):
            configs = configs.filter(organization__uuid=options["org_uuid"])

        if not configs.exists():
            self.stdout.write(
                self.style.WARNING("No active Stripe provider config found.")
            )
            return

        totals = {
            "updated": 0,
            "already_ok": 0,
            "protected": 0,
            "no_number": 0,
            "missing": 0,
        }

        for config in configs.select_related("organization"):
            org = config.organization
            self.stdout.write(self.style.MIGRATE_HEADING(f"\nOrganization: {org}"))

            # Imported-from-Stripe invoices carry the Stripe invoice id ("in_...").
            invoices = Invoice.objects.filter(
                organization=org,
                external_id__startswith="in_",
                deleted_at__isnull=True,
            ).order_by("created_at")

            for invoice in invoices:
                try:
                    si = stripe.Invoice.retrieve(
                        invoice.external_id, api_key=config.api_key
                    )
                except stripe.error.StripeError as exc:
                    totals["missing"] += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"  ! {invoice.external_id}: not retrievable ({exc})"
                        )
                    )
                    continue

                number = si.get("number") or ""
                if not number:
                    totals["no_number"] += 1
                    self.stdout.write(
                        f"  · {invoice.external_id}: no Stripe number "
                        "(unfinalized), skipped"
                    )
                    continue

                # Already carries the right number — nothing to do.
                if invoice.number == number:
                    totals["already_ok"] += 1
                    continue

                is_draft = invoice.status == Invoice.Status.DRAFT
                if not is_draft and not force_emitted:
                    # Legally-emitted number: protect it, only report.
                    totals["protected"] += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"  ~ {invoice.uuid}: status '{invoice.status}', "
                            f"number '{invoice.number}' ≠ Stripe '{number}' "
                            "— PROTECTED, left as-is (use --force-emitted to override)"
                        )
                    )
                    continue

                issue_date = _resolve_issue_date(si)
                flag = "" if is_draft else " [FORCED non-draft]"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  → {invoice.uuid}: '{invoice.number or '∅'}' → '{number}', "
                        f"issue_date {issue_date}{flag}"
                    )
                )
                if not dry_run:
                    self._apply(invoice, number, issue_date)
                totals["updated"] += 1

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"\nDone{' (dry-run)' if dry_run else ''}: "
                f"{totals['updated']} updated, "
                f"{totals['already_ok']} already correct, "
                f"{totals['protected']} protected (non-draft), "
                f"{totals['no_number']} without Stripe number, "
                f"{totals['missing']} not retrievable."
            )
        )

    @transaction.atomic
    def _apply(self, invoice, number, issue_date):
        invoice.number = number
        data = invoice.en16931_data or {}
        data["invoiceNumber"] = number
        if issue_date:
            references = data.get("references", {})
            references["issueDate"] = issue_date.isoformat()
            data["references"] = references
        invoice.en16931_data = data
        # issue_date is re-synced from en16931_data by Invoice.save().
        invoice.save()
