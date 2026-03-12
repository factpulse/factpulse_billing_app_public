"""Tests for the resolution service (supplier, customer, product line resolution)."""

import uuid as uuid_lib

import pytest

from apps.billing.factories import CustomerFactory, ProductFactory, SupplierFactory
from apps.billing.models import Customer, Supplier
from apps.billing.services.resolution_service import (
    _validate_external_id,
    deep_merge,
    resolve_customer,
    resolve_product_lines,
    resolve_supplier,
)
from apps.core.models import Organization


@pytest.fixture
def org2(db):
    return Organization.objects.create(name="Other Org", slug="other-org")


# --- resolve_supplier ---


@pytest.mark.django_db
class TestResolveSupplier:
    def test_referenced_by_uuid(self, org):
        supplier = SupplierFactory(organization=org, name="ACME")
        payload = {"supplier_id": str(supplier.uuid)}

        result_supplier, data, warnings = resolve_supplier(org, payload)

        assert result_supplier == supplier
        assert data["name"] == "ACME"
        assert warnings == []

    def test_referenced_by_external_id(self, org):
        supplier = SupplierFactory(organization=org, external_id="ext_sup_1")
        payload = {"supplier_id": "ext_sup_1"}

        result_supplier, data, warnings = resolve_supplier(org, payload)

        assert result_supplier == supplier

    def test_referenced_by_default(self, org):
        supplier = SupplierFactory(organization=org, is_default=True)
        payload = {"supplier_id": "default"}

        result_supplier, data, warnings = resolve_supplier(org, payload)

        assert result_supplier == supplier

    def test_referenced_not_found_raises(self, org):
        payload = {"supplier_id": str(uuid_lib.uuid4())}
        with pytest.raises(ValueError, match="not found"):
            resolve_supplier(org, payload)

    def test_inline_match_by_siren(self, org):
        supplier = SupplierFactory(organization=org, siren="123456789")
        payload = {"supplier": {"name": "ACME Inline", "siren": "123456789"}}

        result_supplier, data, warnings = resolve_supplier(org, payload)

        assert result_supplier == supplier

    def test_inline_auto_create_by_siren(self, org):
        payload = {"supplier": {"name": "New Corp", "siren": "999888777"}}

        result_supplier, data, warnings = resolve_supplier(org, payload)

        assert result_supplier.siren == "999888777"
        assert result_supplier.name == "New Corp"
        assert Supplier.objects.filter(organization=org, siren="999888777").exists()

    def test_supplier_overridedeep_merge(self, org):
        supplier = SupplierFactory(organization=org, name="Base", email="base@test.com")
        payload = {
            "supplier_id": str(supplier.uuid),
            "supplier_override": {"email": "override@test.com"},
        }

        _, data, _ = resolve_supplier(org, payload)

        assert data["name"] == "Base"
        assert data["email"] == "override@test.com"

    def test_both_supplier_id_and_inline_raises(self, org):
        supplier = SupplierFactory(organization=org)
        payload = {
            "supplier_id": str(supplier.uuid),
            "supplier": {"name": "Inline", "siren": "111222333"},
        }

        with pytest.raises(ValueError, match="both"):
            resolve_supplier(org, payload)

    def test_inline_and_override_raises(self, org):
        payload = {
            "supplier": {"name": "Inline", "siren": "111222333"},
            "supplier_override": {"email": "x@y.com"},
        }

        with pytest.raises(ValueError, match="supplier_override"):
            resolve_supplier(org, payload)

    def test_neither_supplier_id_nor_inline_raises(self, org):
        with pytest.raises(ValueError, match="must be provided"):
            resolve_supplier(org, {})

    def test_org_isolation(self, org, org2):
        supplier = SupplierFactory(organization=org)
        payload = {"supplier_id": str(supplier.uuid)}

        with pytest.raises(ValueError, match="not found"):
            resolve_supplier(org2, payload)

    def test_inline_match_by_uuid(self, org):
        supplier = SupplierFactory(organization=org)
        payload = {"supplier": {"uuid": str(supplier.uuid), "name": "X"}}

        result_supplier, _, _ = resolve_supplier(org, payload)
        assert result_supplier == supplier

    def test_inline_match_by_external_id(self, org):
        supplier = SupplierFactory(organization=org, external_id="ext_match")
        payload = {"supplier": {"external_id": "ext_match", "name": "X"}}

        result_supplier, _, _ = resolve_supplier(org, payload)
        assert result_supplier == supplier

    def test_inline_data_mismatch_warning(self, org):
        SupplierFactory(organization=org, name="Real Name", siren="123456789")
        payload = {"supplier": {"name": "Wrong Name", "siren": "123456789"}}

        _, _, warnings = resolve_supplier(org, payload)

        assert len(warnings) > 0
        assert warnings[0]["code"] == "supplier_data_mismatch"


# --- resolve_customer ---


@pytest.mark.django_db
class TestResolveCustomer:
    def test_referenced_by_uuid(self, org):
        customer = CustomerFactory(organization=org, name="Client A")
        payload = {"customer_id": str(customer.uuid)}

        result_customer, data, warnings = resolve_customer(org, payload)

        assert result_customer == customer
        assert data["name"] == "Client A"

    def test_referenced_by_external_id(self, org):
        customer = CustomerFactory(organization=org, external_id="cust_ext_1")
        payload = {"customer_id": "cust_ext_1"}

        result_customer, _, _ = resolve_customer(org, payload)
        assert result_customer == customer

    def test_no_recipient_returns_none(self, org):
        customer, data, warnings = resolve_customer(org, {})

        assert customer is None
        assert data is None
        assert warnings == []

    def test_inline_match_by_siren(self, org):
        customer = CustomerFactory(organization=org, siren="987654321")
        payload = {"recipient": {"name": "Inline", "siren": "987654321"}}

        result_customer, _, _ = resolve_customer(org, payload)
        assert result_customer == customer

    def test_inline_auto_create_by_siren(self, org):
        payload = {"recipient": {"name": "New Client", "siren": "555666777"}}

        result_customer, _, _ = resolve_customer(org, payload)
        assert result_customer.name == "New Client"
        assert Customer.objects.filter(organization=org, siren="555666777").exists()

    def test_inline_auto_create_by_external_id(self, org):
        payload = {"recipient": {"name": "ExtClient", "external_id": "ext_new"}}

        result_customer, _, _ = resolve_customer(org, payload)
        assert result_customer.external_id == "ext_new"

    def test_both_customer_id_and_recipient_raises(self, org):
        customer = CustomerFactory(organization=org)
        payload = {
            "customer_id": str(customer.uuid),
            "recipient": {"name": "Inline", "siren": "111222333"},
        }

        with pytest.raises(ValueError, match="both"):
            resolve_customer(org, payload)

    def test_customer_overridedeep_merge(self, org):
        customer = CustomerFactory(organization=org, name="Client", email="a@b.com")
        payload = {
            "customer_id": str(customer.uuid),
            "customer_override": {"email": "new@test.com"},
        }

        _, data, _ = resolve_customer(org, payload)

        assert data["name"] == "Client"
        assert data["email"] == "new@test.com"

    def test_customer_not_found_raises(self, org):
        payload = {"customer_id": str(uuid_lib.uuid4())}
        with pytest.raises(ValueError, match="not found"):
            resolve_customer(org, payload)


# --- resolve_product_lines ---


@pytest.mark.django_db
class TestResolveProductLines:
    def test_defaults_injected_from_product(self, org):
        product = ProductFactory(
            organization=org, name="Widget", default_unit_price="50.00"
        )
        product.refresh_from_db()
        en16931_data = {
            "invoiceLines": [{"product_id": str(product.uuid), "quantity": "2"}]
        }

        resolve_product_lines(org, en16931_data)

        line = en16931_data["invoiceLines"][0]
        assert line["itemName"] == "Widget"
        assert line["unitNetPrice"] == str(product.default_unit_price)
        assert line["quantity"] == "2"
        assert "product_id" not in line

    def test_explicit_override_product_defaults(self, org):
        product = ProductFactory(
            organization=org, name="Widget", default_unit_price="50.00"
        )
        en16931_data = {
            "invoiceLines": [
                {
                    "product_id": str(product.uuid),
                    "itemName": "Custom Name",
                    "quantity": "1",
                }
            ]
        }

        resolve_product_lines(org, en16931_data)

        line = en16931_data["invoiceLines"][0]
        assert line["itemName"] == "Custom Name"  # explicit overrides product default

    def test_no_product_id_leaves_line_unchanged(self, org):
        en16931_data = {
            "invoiceLines": [
                {"itemName": "Manual", "quantity": "1", "unitNetPrice": "10.00"}
            ]
        }

        resolve_product_lines(org, en16931_data)

        line = en16931_data["invoiceLines"][0]
        assert line["itemName"] == "Manual"

    def test_product_not_found_raises(self, org):
        en16931_data = {"invoiceLines": [{"product_id": str(uuid_lib.uuid4())}]}

        with pytest.raises(ValueError, match="not found"):
            resolve_product_lines(org, en16931_data)

    def test_product_by_external_id(self, org):
        ProductFactory(organization=org, external_id="prod_ext_1", name="ExtProd")
        en16931_data = {"invoiceLines": [{"product_id": "prod_ext_1", "quantity": "3"}]}

        resolve_product_lines(org, en16931_data)

        line = en16931_data["invoiceLines"][0]
        assert line["itemName"] == "ExtProd"

    def test_empty_lines_no_error(self, org):
        en16931_data = {"invoiceLines": []}
        resolve_product_lines(org, en16931_data)
        assert en16931_data["invoiceLines"] == []

    def test_no_lines_key_no_error(self, org):
        en16931_data = {}
        resolve_product_lines(org, en16931_data)


# --- _validate_external_id ---


class TestValidateExternalId:
    def test_uuid_rejected(self):
        valid, error = _validate_external_id(str(uuid_lib.uuid4()))
        assert valid is False
        assert "UUID" in error

    def test_valid_format(self):
        valid, error = _validate_external_id("my_ext_id")
        assert valid is True
        assert error is None

    def test_starts_with_number_rejected(self):
        valid, error = _validate_external_id("123abc")
        assert valid is False

    def test_starts_with_underscore_accepted(self):
        valid, error = _validate_external_id("_id")
        assert valid is True


# --- deep_merge ---


class TestDeepMerge:
    def test_nested_dicts_merged(self):
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        override = {"a": {"y": 99, "z": 100}}

        result = deep_merge(base, override)

        assert result == {"a": {"x": 1, "y": 99, "z": 100}, "b": 3}

    def test_arrays_replaced(self):
        base = {"items": [1, 2, 3]}
        override = {"items": [4, 5]}

        result = deep_merge(base, override)

        assert result == {"items": [4, 5]}

    def test_does_not_mutate_base(self):
        base = {"a": {"x": 1}}
        override = {"a": {"x": 2}}

        deep_merge(base, override)

        assert base == {"a": {"x": 1}}

    def test_new_keys_added(self):
        base = {"a": 1}
        override = {"b": 2}

        result = deep_merge(base, override)

        assert result == {"a": 1, "b": 2}
