"""State machine — invoice status transitions.

Defines the allowed transition matrix and enforces it.
"""

from apps.billing.models import Invoice
from apps.core.exceptions import ConflictError

S = Invoice.Status

# Allowed transitions: from_status -> [to_status, ...]
TRANSITIONS = {
    S.DRAFT: [S.PROCESSING],
    S.PROCESSING: [S.VALIDATED, S.DRAFT],  # draft = error fallback
    S.VALIDATED: [S.TRANSMITTING, S.PAID, S.CANCELLED],
    S.TRANSMITTING: [
        S.TRANSMITTED,
        S.REJECTED,
        S.VALIDATED,
    ],  # validated = error fallback
    S.TRANSMITTED: [S.ACCEPTED, S.REJECTED, S.REFUSED, S.PAID],
    S.ACCEPTED: [S.REFUSED, S.PAID],
    S.REJECTED: [],
    S.REFUSED: [],
    S.PAID: [S.CANCELLED],
    S.CANCELLED: [],
}

# Transitions requiring specific endpoints (not direct PATCH)
ENDPOINT_TRANSITIONS = {
    (S.DRAFT, S.PROCESSING): "validate",
    (S.VALIDATED, S.TRANSMITTING): "transmit",
    (S.VALIDATED, S.PAID): "mark_paid",
    (S.TRANSMITTED, S.PAID): "mark_paid",
    (S.ACCEPTED, S.PAID): "mark_paid",
}


def can_transition(from_status, to_status):
    """Check if a transition is allowed."""
    allowed = TRANSITIONS.get(from_status, [])
    return to_status in allowed


def validate_transition(invoice, to_status):
    """Validate and raise ConflictError if transition is not allowed."""
    if not can_transition(invoice.status, to_status):
        raise ConflictError(
            f"Transition from '{invoice.status}' to '{to_status}' is not allowed."
        )

    # Block modifications while processing
    if invoice.status == S.PROCESSING and to_status not in (S.VALIDATED, S.DRAFT):
        raise ConflictError("Invoice is currently being processed. Please wait.")


def is_editable(invoice):
    """Check if an invoice can be edited (PATCH)."""
    return invoice.status == S.DRAFT


def is_deletable(invoice):
    """Check if an invoice can be soft-deleted."""
    return invoice.status == S.DRAFT and not invoice.number
