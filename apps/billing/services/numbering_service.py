"""Numbering service — sequential invoice numbering.

Assigns invoice numbers at the draft → validated transition using
Django template prefix rendering and atomic counter increment.
"""

from datetime import date

from django.db import transaction
from django.template import Context, Template

from apps.billing.models import NumberingCounter, NumberingSequence


def assign_number(invoice):
    """Assign a sequential number to the invoice.

    Must be called within a transaction. Uses SELECT FOR UPDATE on the counter
    to ensure atomicity.

    Returns the assigned number string.
    """
    try:
        sequence = NumberingSequence.objects.get(supplier=invoice.supplier)
    except NumberingSequence.DoesNotExist:
        # Auto-create a default sequence
        sequence = NumberingSequence.objects.create(supplier=invoice.supplier)

    # Determine the issue_date for prefix rendering
    issue_date = invoice.issue_date or date.today()

    # Render the prefix template
    resolved_prefix = _render_prefix(sequence.prefix_template, issue_date)

    # Atomic counter increment
    with transaction.atomic():
        counter, _ = NumberingCounter.objects.select_for_update().get_or_create(
            sequence=sequence,
            resolved_prefix=resolved_prefix,
            defaults={"last_number": 0},
        )
        counter.last_number += 1
        counter.save(update_fields=["last_number"])

        number = f"{resolved_prefix}{str(counter.last_number).zfill(sequence.padding)}"

    return number


def _render_prefix(template_str, issue_date):
    """Render a Django template string with issue_date context."""
    template = Template(template_str)
    context = Context({"issue_date": issue_date})
    return template.render(context)
