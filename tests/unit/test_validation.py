from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from edp.contracts import (
    INVENTORY_MOVEMENTS,
    PRODUCTION_ORDERS,
    PRODUCTS,
    SHIPMENTS,
    Reason,
)
from edp.validation import record_hash, validate_record

import factories as f

LONDON = ZoneInfo("Europe/London")
NOW = datetime(2026, 6, 1, tzinfo=UTC)


def validate(contract, row):  # type: ignore[no-untyped-def]
    return validate_record(contract, row, 1, LONDON, NOW)


def test_valid_production_order_is_typed_and_standardised() -> None:
    record = validate(PRODUCTION_ORDERS, f.production_order(status=" COMPLETE "))
    assert record.is_valid, record.issues
    assert record.values["status"] == "completed"
    assert record.values["planned_quantity"] == 100
    assert record.values["actual_end"] == datetime(2026, 1, 5, 17, tzinfo=UTC)


def test_identifiers_are_normalised_to_upper_case() -> None:
    record = validate(
        INVENTORY_MOVEMENTS, f.movement(warehouse_id="wh-bhm", product_id=" prd-0001 ")
    )
    assert record.is_valid
    assert record.values["warehouse_id"] == "WH-BHM"
    assert record.values["product_id"] == "PRD-0001"


def test_missing_required_field_is_reported() -> None:
    record = validate(PRODUCTION_ORDERS, f.production_order(product_id=""))
    assert record.reason_codes == [Reason.MISSING_REQUIRED.value]
    assert record.issues[0].field == "product_id"


def test_optional_fields_receive_defaults() -> None:
    record = validate(PRODUCTS, f.product(reorder_point="", is_active=""))
    assert record.is_valid
    assert record.values["reorder_point"] == 0
    assert record.values["is_active"] is True
    assert record.values["unit_cost"] == Decimal("100.00")


def test_invalid_enum_and_pattern_values() -> None:
    record = validate(INVENTORY_MOVEMENTS, f.movement(movement_type="teleport"))
    assert record.reason_codes == [Reason.INVALID_VALUE.value]
    record = validate(PRODUCTS, f.product(product_id="P1"))
    assert record.reason_codes == [Reason.INVALID_VALUE.value]


def test_invalid_quantities() -> None:
    assert validate(PRODUCTION_ORDERS, f.production_order(planned_quantity="-5")).reason_codes == [
        "invalid_quantity"
    ]
    assert validate(
        PRODUCTION_ORDERS, f.production_order(planned_quantity="fifty")
    ).reason_codes == ["invalid_quantity"]
    assert validate(PRODUCTS, f.product(unit_cost="0")).reason_codes == ["invalid_value"]


def test_end_before_start_is_an_invalid_date() -> None:
    record = validate(PRODUCTION_ORDERS, f.production_order(actual_end="2026-01-05 07:00:00"))
    assert record.reason_codes == [Reason.INVALID_DATE.value]
    assert "actual_end is before actual_start" in record.issues[0].detail


def test_future_event_timestamp_is_rejected_but_future_plan_is_allowed() -> None:
    record = validate(INVENTORY_MOVEMENTS, f.movement(event_timestamp="2027-01-01T00:00:00Z"))
    assert record.reason_codes == ["invalid_date"]
    planned = validate(
        PRODUCTION_ORDERS,
        f.production_order(
            status="planned",
            planned_start="2026-12-01 08:00:00",
            planned_end="2026-12-01 16:00:00",
            actual_start="",
            actual_end="",
            produced_quantity="",
        ),
    )
    assert planned.is_valid, planned.issues


def test_status_dependent_required_fields() -> None:
    record = validate(PRODUCTION_ORDERS, f.production_order(actual_end="", produced_quantity=""))
    assert record.reason_codes == ["missing_required"]
    record = validate(SHIPMENTS, f.shipment(delivered_at=None))
    assert record.reason_codes == ["missing_required"]


def test_over_production_and_over_receipt_rules() -> None:
    assert validate(
        PRODUCTION_ORDERS, f.production_order(produced_quantity="151")
    ).reason_codes == ["invalid_quantity"]
    assert validate(SHIPMENTS, f.shipment(quantity_received=111)).reason_codes == [
        "invalid_quantity"
    ]
    assert validate(SHIPMENTS, f.shipment(quantity_rejected=101)).reason_codes == [
        "invalid_quantity"
    ]


def test_movement_sign_rules() -> None:
    assert validate(INVENTORY_MOVEMENTS, f.movement(quantity=0)).reason_codes == [
        "invalid_quantity"
    ]
    assert validate(INVENTORY_MOVEMENTS, f.movement(quantity=-5)).reason_codes == [
        "invalid_quantity"
    ]
    adjustment = validate(INVENTORY_MOVEMENTS, f.movement(movement_type="adjustment", quantity=-5))
    assert adjustment.is_valid


def test_derived_quantity_delta_is_signed_by_movement_type() -> None:
    derive = INVENTORY_MOVEMENTS.derive
    assert derive is not None
    assert derive({"movement_type": "issue", "quantity": 7}) == {"quantity_delta": -7}
    assert derive({"movement_type": "receipt", "quantity": 7}) == {"quantity_delta": 7}
    assert derive({"movement_type": "adjustment", "quantity": -2}) == {"quantity_delta": -2}


def test_cross_field_rules_do_not_cascade_after_field_errors() -> None:
    # An unparseable start must produce one root-cause issue, not extra rule failures.
    record = validate(PRODUCTION_ORDERS, f.production_order(actual_start="2026-02-30 08:00:00"))
    assert len(record.issues) == 1
    assert record.issues[0].field == "actual_start"


def test_multiple_field_errors_are_all_reported() -> None:
    record = validate(SHIPMENTS, f.shipment(supplier_id=None, promised_date="31/13/2026"))
    assert set(record.reason_codes) == {"missing_required", "invalid_date"}


def test_record_hash_ignores_cosmetic_differences() -> None:
    a = validate(PRODUCTION_ORDERS, f.production_order())
    b = validate(
        PRODUCTION_ORDERS,
        f.production_order(
            status="Completed", product_id="prd-0001", updated_at="2026-01-05T17:10:00Z"
        ),
    )
    assert record_hash(a.values) == record_hash(b.values)
    c = validate(PRODUCTION_ORDERS, f.production_order(produced_quantity="96"))
    assert record_hash(a.values) != record_hash(c.values)
