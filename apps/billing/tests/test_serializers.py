"""Tests for billing serializers (serializers.py)."""

import pytest
from rest_framework import serializers  # noqa: F401

from apps.billing.serializers import (
    InvoiceCreateSerializer,
    InvoiceUpdateSerializer,
    validate_external_id,
)

# --- validate_external_id ---


class TestValidateExternalId:
    def test_valid_starts_with_letter(self):
        assert validate_external_id("abc-123") == "abc-123"

    def test_valid_starts_with_underscore(self):
        assert validate_external_id("_my_id") == "_my_id"

    def test_none_returns_none(self):
        assert validate_external_id(None) is None

    def test_uuid_raises(self):
        with pytest.raises(serializers.ValidationError, match="UUID"):
            validate_external_id("550e8400-e29b-41d4-a716-446655440000")

    def test_starts_with_digit_raises(self):
        with pytest.raises(serializers.ValidationError, match="letter or underscore"):
            validate_external_id("1abc")

    def test_starts_with_hyphen_raises(self):
        with pytest.raises(serializers.ValidationError, match="letter or underscore"):
            validate_external_id("-abc")

    def test_valid_uppercase(self):
        assert validate_external_id("ABC_123") == "ABC_123"


# --- InvoiceCreateSerializer.validate ---


class TestInvoiceCreateSerializerValidate:
    def _validate(self, data):
        s = InvoiceCreateSerializer(data=data)
        s.is_valid(raise_exception=True)
        return s.validated_data

    def test_no_supplier_raises(self):
        with pytest.raises(serializers.ValidationError) as exc_info:
            self._validate({"en16931_data": {}})
        # Should mention supplier_id
        assert "supplier_id" in str(exc_info.value.detail)

    def test_supplier_id_only(self):
        data = self._validate({"supplier_id": "sup-001"})
        assert data["supplier_id"] == "sup-001"

    def test_supplier_inline_only(self):
        data = self._validate({"supplier": {"name": "Inline Supplier"}})
        assert data["supplier"]["name"] == "Inline Supplier"

    def test_both_supplier_id_and_supplier_raises(self):
        with pytest.raises(
            serializers.ValidationError, match="both supplier_id and supplier"
        ):
            self._validate(
                {
                    "supplier_id": "sup-001",
                    "supplier": {"name": "Inline"},
                }
            )

    def test_both_customer_id_and_recipient_raises(self):
        with pytest.raises(
            serializers.ValidationError, match="both customer_id and recipient"
        ):
            self._validate(
                {
                    "supplier_id": "sup-001",
                    "customer_id": "cust-001",
                    "recipient": {"name": "Inline Customer"},
                }
            )

    def test_supplier_override_with_inline_supplier_raises(self):
        with pytest.raises(
            serializers.ValidationError, match="supplier_override with inline"
        ):
            self._validate(
                {
                    "supplier": {"name": "Inline"},
                    "supplier_override": {"name": "Override"},
                }
            )

    def test_customer_override_with_inline_recipient_raises(self):
        with pytest.raises(
            serializers.ValidationError, match="customer_override with inline"
        ):
            self._validate(
                {
                    "supplier_id": "sup-001",
                    "recipient": {"name": "Inline"},
                    "customer_override": {"name": "Override"},
                }
            )

    def test_supplier_id_with_override_ok(self):
        """supplier_override is fine when using supplier_id (not inline supplier)."""
        data = self._validate(
            {
                "supplier_id": "sup-001",
                "supplier_override": {"name": "Override"},
            }
        )
        assert data["supplier_id"] == "sup-001"
        assert data["supplier_override"]["name"] == "Override"

    def test_customer_id_with_override_ok(self):
        """customer_override is fine when using customer_id (not inline recipient)."""
        data = self._validate(
            {
                "supplier_id": "sup-001",
                "customer_id": "cust-001",
                "customer_override": {"name": "Override"},
            }
        )
        assert data["customer_id"] == "cust-001"


# --- InvoiceUpdateSerializer.validate ---


class TestInvoiceUpdateSerializerValidate:
    def _validate(self, data):
        s = InvoiceUpdateSerializer(data=data)
        s.is_valid(raise_exception=True)
        return s.validated_data

    def test_both_supplier_id_and_supplier_raises(self):
        with pytest.raises(
            serializers.ValidationError, match="both supplier_id and supplier"
        ):
            self._validate(
                {
                    "version": 1,
                    "supplier_id": "sup-001",
                    "supplier": {"name": "Inline"},
                }
            )

    def test_both_customer_id_and_recipient_raises(self):
        with pytest.raises(
            serializers.ValidationError, match="both customer_id and recipient"
        ):
            self._validate(
                {
                    "version": 1,
                    "customer_id": "cust-001",
                    "recipient": {"name": "Inline"},
                }
            )

    def test_version_required(self):
        with pytest.raises(serializers.ValidationError):
            self._validate({"supplier_id": "sup-001"})

    def test_valid_update(self):
        data = self._validate(
            {
                "version": 1,
                "supplier_id": "sup-001",
                "en16931_data": {"issue_date": "2025-01-01"},
            }
        )
        assert data["version"] == 1
        assert data["supplier_id"] == "sup-001"

    def test_empty_update_with_version(self):
        """Only version is required; no other fields needed for update."""
        data = self._validate({"version": 3})
        assert data["version"] == 3
