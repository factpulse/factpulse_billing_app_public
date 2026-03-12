"""Tests for the invoice state machine."""

import pytest

from apps.billing.services.state_machine import (
    TRANSITIONS,
    can_transition,
    is_deletable,
    is_editable,
    validate_transition,
)
from apps.core.exceptions import ConflictError


class _FakeInvoice:
    def __init__(self, status, number=""):
        self.status = status
        self.number = number


class TestCanTransition:
    """Test can_transition for all valid and invalid transitions."""

    @pytest.mark.parametrize(
        "from_status,to_status",
        [
            ("draft", "processing"),
            ("processing", "validated"),
            ("processing", "draft"),
            ("validated", "transmitting"),
            ("validated", "cancelled"),
            ("transmitting", "transmitted"),
            ("transmitting", "validated"),
            ("transmitted", "accepted"),
            ("transmitted", "rejected"),
            ("transmitted", "paid"),
            ("accepted", "paid"),
        ],
    )
    def test_valid_transitions(self, from_status, to_status):
        assert can_transition(from_status, to_status) is True

    @pytest.mark.parametrize(
        "from_status,to_status",
        [
            ("draft", "validated"),
            ("draft", "paid"),
            ("draft", "cancelled"),
            ("paid", "draft"),
            ("paid", "validated"),
            ("cancelled", "draft"),
            ("cancelled", "paid"),
            ("rejected", "paid"),
            ("rejected", "draft"),
            ("accepted", "draft"),
        ],
    )
    def test_invalid_transitions(self, from_status, to_status):
        assert can_transition(from_status, to_status) is False

    def test_unknown_status_returns_false(self):
        assert can_transition("unknown", "draft") is False


class TestValidateTransition:
    def test_valid_transition_does_not_raise(self):
        invoice = _FakeInvoice("draft")
        validate_transition(invoice, "processing")

    def test_invalid_transition_raises_conflict(self):
        invoice = _FakeInvoice("draft")
        with pytest.raises(ConflictError, match="not allowed"):
            validate_transition(invoice, "validated")

    def test_processing_to_cancelled_blocked(self):
        invoice = _FakeInvoice("processing")
        with pytest.raises(ConflictError, match="not allowed"):
            validate_transition(invoice, "cancelled")

    def test_processing_to_validated_allowed(self):
        invoice = _FakeInvoice("processing")
        validate_transition(invoice, "validated")

    def test_processing_to_draft_allowed(self):
        """Processing → draft is the error fallback."""
        invoice = _FakeInvoice("processing")
        validate_transition(invoice, "draft")


class TestIsEditable:
    def test_draft_is_editable(self):
        assert is_editable(_FakeInvoice("draft")) is True

    @pytest.mark.parametrize(
        "status",
        [
            "processing",
            "validated",
            "transmitting",
            "transmitted",
            "accepted",
            "rejected",
            "paid",
            "cancelled",
        ],
    )
    def test_non_draft_not_editable(self, status):
        assert is_editable(_FakeInvoice(status)) is False


class TestIsDeletable:
    def test_draft_without_number_is_deletable(self):
        assert is_deletable(_FakeInvoice("draft")) is True

    def test_draft_with_number_not_deletable(self):
        assert is_deletable(_FakeInvoice("draft", number="F-2026-001")) is False

    @pytest.mark.parametrize(
        "status",
        [
            "processing",
            "validated",
            "transmitting",
            "transmitted",
            "accepted",
            "rejected",
            "paid",
            "cancelled",
        ],
    )
    def test_non_draft_not_deletable(self, status):
        assert is_deletable(_FakeInvoice(status)) is False


class TestTransitionsCompleteness:
    def test_all_statuses_have_entry(self):
        """Every status should appear as a key in TRANSITIONS."""
        expected = {
            "draft",
            "processing",
            "validated",
            "transmitting",
            "transmitted",
            "accepted",
            "rejected",
            "refused",
            "paid",
            "cancelled",
        }
        assert set(TRANSITIONS.keys()) == expected
