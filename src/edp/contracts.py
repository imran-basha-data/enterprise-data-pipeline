"""Data contracts for every source entity.

A contract is the single source of truth for an entity's columns, types,
required fields, natural key, version column, foreign keys and cross-field
business rules. Ingestion, validation and staging loads are all driven from
these definitions, so adding a column is a one-line change here plus a
migration.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any

from edp.parsing import normalise_code


class FieldType(Enum):
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMP = "timestamp"
    UUID = "uuid"


class Reason(StrEnum):
    """Data-quality reason codes recorded in ``ops.quarantine``."""

    MISSING_REQUIRED = "missing_required"
    INVALID_DATE = "invalid_date"
    INVALID_QUANTITY = "invalid_quantity"
    INVALID_VALUE = "invalid_value"
    ORPHAN_REFERENCE = "orphan_reference"
    DUPLICATE_CONFLICT = "duplicate_conflict"
    MALFORMED_RECORD = "malformed_record"


@dataclass(frozen=True)
class Issue:
    field: str | None
    reason: Reason
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"field": self.field, "reason": self.reason.value, "detail": self.detail}


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: FieldType
    required: bool = False
    is_quantity: bool = False
    min_value: Decimal | int | None = None
    max_value: Decimal | int | None = None
    max_length: int | None = None
    pattern: str | None = None
    allowed_values: frozenset[str] | None = None
    aliases: Mapping[str, str] = field(default_factory=dict)
    normalise: Callable[[str], str] | None = None
    default: Any = None
    allow_future: bool = False

    @property
    def numeric_reason(self) -> Reason:
        return Reason.INVALID_QUANTITY if self.is_quantity else Reason.INVALID_VALUE


@dataclass(frozen=True)
class ForeignKey:
    field: str
    ref_entity: str


RowRule = Callable[[Mapping[str, Any]], Issue | None]


@dataclass(frozen=True)
class EntityContract:
    name: str
    key: str
    fields: tuple[FieldSpec, ...]
    load_order: int
    version_field: str | None = None
    foreign_keys: tuple[ForeignKey, ...] = ()
    rules: tuple[RowRule, ...] = ()
    # Python-side transformations that add derived staging columns.
    derive: Callable[[Mapping[str, Any]], dict[str, Any]] | None = None
    derived_columns: tuple[str, ...] = ()

    @property
    def immutable(self) -> bool:
        """Events are append-only: a key can never change once loaded."""
        return self.version_field is None

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)

    @property
    def staging_columns(self) -> tuple[str, ...]:
        return self.column_names + self.derived_columns

    @property
    def required_columns(self) -> frozenset[str]:
        return frozenset(f.name for f in self.fields if f.required)

    @property
    def staging_table(self) -> str:
        return self.name

    def spec(self, name: str) -> FieldSpec:
        for spec in self.fields:
            if spec.name == name:
                return spec
        raise KeyError(name)


# --------------------------------------------------------------------------
# Cross-field rules. Each rule only runs when every field passed type checks,
# so values are already typed (datetime, int, ...) or None.
# --------------------------------------------------------------------------


def _not_before(later: str, earlier: str) -> RowRule:
    def rule(row: Mapping[str, Any]) -> Issue | None:
        a, b = row.get(later), row.get(earlier)
        if a is None or b is None:
            return None
        a_cmp = a.date() if isinstance(a, datetime) and not isinstance(b, datetime) else a
        if a_cmp < b:
            return Issue(later, Reason.INVALID_DATE, f"{later} is before {earlier}")
        return None

    rule.__name__ = f"{later}_not_before_{earlier}"
    return rule


def _requires_when_status(status: str, *required: str) -> RowRule:
    def rule(row: Mapping[str, Any]) -> Issue | None:
        if row.get("status") != status:
            return None
        missing = [f for f in required if row.get(f) is None]
        if missing:
            return Issue(
                missing[0],
                Reason.MISSING_REQUIRED,
                f"status '{status}' requires {', '.join(missing)}",
            )
        return None

    rule.__name__ = f"{status}_requires_{'_'.join(required)}"
    return rule


def _requires_field(field_name: str, dependency: str) -> RowRule:
    def rule(row: Mapping[str, Any]) -> Issue | None:
        if row.get(field_name) is not None and row.get(dependency) is None:
            return Issue(
                dependency, Reason.INVALID_DATE, f"{field_name} is set but {dependency} is empty"
            )
        return None

    return rule


def _max_ratio(numerator: str, denominator: str, ratio: Decimal) -> RowRule:
    def rule(row: Mapping[str, Any]) -> Issue | None:
        num, den = row.get(numerator), row.get(denominator)
        if num is None or den is None:
            return None
        if Decimal(num) > Decimal(den) * ratio:
            return Issue(
                numerator,
                Reason.INVALID_QUANTITY,
                f"{numerator} ({num}) exceeds {ratio:.0%} of {denominator} ({den})",
            )
        return None

    return rule


def _not_greater_than(smaller: str, larger: str) -> RowRule:
    def rule(row: Mapping[str, Any]) -> Issue | None:
        a, b = row.get(smaller), row.get(larger)
        if a is None or b is None:
            return None
        if a > b:
            return Issue(
                smaller, Reason.INVALID_QUANTITY, f"{smaller} ({a}) exceeds {larger} ({b})"
            )
        return None

    return rule


def _movement_quantity_sign(row: Mapping[str, Any]) -> Issue | None:
    quantity, movement_type = row.get("quantity"), row.get("movement_type")
    if quantity is None or movement_type is None:
        return None
    if quantity == 0:
        return Issue("quantity", Reason.INVALID_QUANTITY, "quantity must not be zero")
    if quantity < 0 and movement_type != "adjustment":
        return Issue(
            "quantity",
            Reason.INVALID_QUANTITY,
            f"negative quantity is only allowed for adjustments, not '{movement_type}'",
        )
    return None


def _signed_quantity(row: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a movement into its signed effect on stock on hand."""
    quantity: int = row["quantity"]
    if row["movement_type"] in OUTBOUND_MOVEMENTS:
        return {"quantity_delta": -abs(quantity)}
    return {"quantity_delta": quantity}


def _upper(value: str) -> str:
    return value.strip().upper()


def _title(value: str) -> str:
    return " ".join(w.capitalize() for w in value.strip().split())


# --------------------------------------------------------------------------
# Contracts
# --------------------------------------------------------------------------

F = FieldSpec
T = FieldType

SUPPLIERS = EntityContract(
    name="suppliers",
    key="supplier_id",
    version_field="updated_at",
    load_order=1,
    fields=(
        F("supplier_id", T.STRING, required=True, pattern=r"^SUP-\d{3,}$", normalise=_upper),
        F("supplier_name", T.STRING, required=True, max_length=200),
        F("country_code", T.STRING, required=True, pattern=r"^[A-Z]{2}$", normalise=_upper),
        F(
            "contracted_lead_time_days",
            T.INTEGER,
            required=True,
            is_quantity=True,
            min_value=0,
            max_value=365,
        ),
        F("is_active", T.BOOLEAN, default=True),
        F("updated_at", T.TIMESTAMP, required=True),
    ),
)

WAREHOUSES = EntityContract(
    name="warehouses",
    key="warehouse_id",
    version_field="updated_at",
    load_order=1,
    fields=(
        F("warehouse_id", T.STRING, required=True, pattern=r"^WH-[A-Z]{3}$", normalise=_upper),
        F("warehouse_name", T.STRING, required=True, max_length=200),
        F("city", T.STRING, required=True, normalise=_title),
        F("region", T.STRING, normalise=_title),
        F("country_code", T.STRING, required=True, pattern=r"^[A-Z]{2}$", normalise=_upper),
        F("capacity_units", T.INTEGER, is_quantity=True, min_value=0),
        F("is_active", T.BOOLEAN, default=True),
        F("updated_at", T.TIMESTAMP, required=True),
    ),
)

PRODUCTS = EntityContract(
    name="products",
    key="product_id",
    version_field="updated_at",
    load_order=2,
    foreign_keys=(ForeignKey("primary_supplier_id", "suppliers"),),
    fields=(
        F("product_id", T.STRING, required=True, pattern=r"^PRD-\d{4}$", normalise=_upper),
        F("sku", T.STRING, required=True, max_length=40, normalise=_upper),
        F("product_name", T.STRING, required=True, max_length=200),
        F("category", T.STRING, required=True, normalise=_title),
        F(
            "unit_of_measure",
            T.STRING,
            required=True,
            allowed_values=frozenset({"EA", "KG", "M", "L", "BOX"}),
            normalise=_upper,
        ),
        F(
            "unit_cost",
            T.DECIMAL,
            required=True,
            min_value=Decimal("0.01"),
            max_value=Decimal("9999999999.99"),
        ),
        F("reorder_point", T.INTEGER, is_quantity=True, min_value=0, default=0),
        F("primary_supplier_id", T.STRING, required=True, normalise=_upper),
        F("is_active", T.BOOLEAN, default=True),
        F("updated_at", T.TIMESTAMP, required=True),
    ),
)

PRODUCTION_ORDER_STATUSES = frozenset({"planned", "in_progress", "completed", "cancelled"})

PRODUCTION_ORDERS = EntityContract(
    name="production_orders",
    key="production_order_id",
    version_field="updated_at",
    load_order=3,
    foreign_keys=(
        ForeignKey("product_id", "products"),
        ForeignKey("warehouse_id", "warehouses"),
    ),
    fields=(
        F(
            "production_order_id",
            T.STRING,
            required=True,
            pattern=r"^MO-\d{4}-\d{6}$",
            normalise=_upper,
        ),
        F("product_id", T.STRING, required=True, normalise=_upper),
        F("warehouse_id", T.STRING, required=True, normalise=_upper),
        F(
            "status",
            T.STRING,
            required=True,
            allowed_values=PRODUCTION_ORDER_STATUSES,
            aliases={"complete": "completed", "done": "completed", "canceled": "cancelled"},
            normalise=normalise_code,
        ),
        F("planned_quantity", T.INTEGER, required=True, is_quantity=True, min_value=1),
        F("produced_quantity", T.INTEGER, is_quantity=True, min_value=0),
        F("scrap_quantity", T.INTEGER, is_quantity=True, min_value=0, default=0),
        F("planned_start", T.TIMESTAMP, required=True, allow_future=True),
        F("planned_end", T.TIMESTAMP, required=True, allow_future=True),
        F("actual_start", T.TIMESTAMP),
        F("actual_end", T.TIMESTAMP),
        F("updated_at", T.TIMESTAMP, required=True),
    ),
    rules=(
        _not_before("planned_end", "planned_start"),
        _requires_field("actual_end", "actual_start"),
        _not_before("actual_end", "actual_start"),
        _requires_when_status("completed", "actual_end", "produced_quantity"),
        _max_ratio("produced_quantity", "planned_quantity", Decimal("1.5")),
    ),
)

MOVEMENT_TYPES = frozenset(
    {"receipt", "issue", "production_output", "adjustment", "transfer_in", "transfer_out"}
)
INBOUND_MOVEMENTS = frozenset({"receipt", "production_output", "transfer_in"})
OUTBOUND_MOVEMENTS = frozenset({"issue", "transfer_out"})

INVENTORY_MOVEMENTS = EntityContract(
    name="inventory_movements",
    key="movement_id",
    version_field=None,
    load_order=3,
    foreign_keys=(
        ForeignKey("product_id", "products"),
        ForeignKey("warehouse_id", "warehouses"),
    ),
    fields=(
        F("movement_id", T.UUID, required=True),
        F("product_id", T.STRING, required=True, normalise=_upper),
        F("warehouse_id", T.STRING, required=True, normalise=_upper),
        F(
            "movement_type",
            T.STRING,
            required=True,
            allowed_values=MOVEMENT_TYPES,
            normalise=normalise_code,
        ),
        F("quantity", T.INTEGER, required=True, is_quantity=True),
        F("reference_id", T.STRING, max_length=64),
        F("event_timestamp", T.TIMESTAMP, required=True),
    ),
    rules=(_movement_quantity_sign,),
    derive=_signed_quantity,
    derived_columns=("quantity_delta",),
)

SHIPMENT_STATUSES = frozenset({"ordered", "in_transit", "delivered", "cancelled"})

SHIPMENTS = EntityContract(
    name="shipments",
    key="shipment_id",
    version_field="updated_at",
    load_order=3,
    foreign_keys=(
        ForeignKey("supplier_id", "suppliers"),
        ForeignKey("product_id", "products"),
        ForeignKey("warehouse_id", "warehouses"),
    ),
    fields=(
        F("shipment_id", T.STRING, required=True, pattern=r"^SHP-\d{4}-\d{6}$", normalise=_upper),
        F("supplier_id", T.STRING, required=True, normalise=_upper),
        F("product_id", T.STRING, required=True, normalise=_upper),
        F("warehouse_id", T.STRING, required=True, normalise=_upper),
        F(
            "status",
            T.STRING,
            required=True,
            allowed_values=SHIPMENT_STATUSES,
            aliases={"in_transport": "in_transit", "canceled": "cancelled"},
            normalise=normalise_code,
        ),
        F("carrier", T.STRING, max_length=100),
        F("quantity_ordered", T.INTEGER, required=True, is_quantity=True, min_value=1),
        F("quantity_received", T.INTEGER, is_quantity=True, min_value=0),
        F("quantity_rejected", T.INTEGER, is_quantity=True, min_value=0, default=0),
        F("order_date", T.DATE, required=True),
        F("promised_date", T.DATE, required=True, allow_future=True),
        F("shipped_at", T.TIMESTAMP),
        F("delivered_at", T.TIMESTAMP),
        F("updated_at", T.TIMESTAMP, required=True),
    ),
    rules=(
        _not_before("promised_date", "order_date"),
        _not_before("shipped_at", "order_date"),
        _requires_field("delivered_at", "shipped_at"),
        _not_before("delivered_at", "shipped_at"),
        _requires_when_status("delivered", "delivered_at", "quantity_received"),
        _not_greater_than("quantity_rejected", "quantity_received"),
        _max_ratio("quantity_received", "quantity_ordered", Decimal("1.1")),
    ),
)

CONTRACTS: dict[str, EntityContract] = {
    c.name: c
    for c in (SUPPLIERS, WAREHOUSES, PRODUCTS, PRODUCTION_ORDERS, INVENTORY_MOVEMENTS, SHIPMENTS)
}

_ENTITY_FILE = re.compile(
    r"^(?P<entity>[a-z_]+?)__(?P<batch>[A-Za-z0-9_\-]+)\.(?P<ext>csv|json|jsonl)$"
)


def match_landing_file(file_name: str) -> tuple[EntityContract, str] | None:
    """Map ``<entity>__<batch>.<ext>`` to its contract, or ``None`` if unknown."""
    match = _ENTITY_FILE.match(file_name)
    if not match:
        return None
    contract = CONTRACTS.get(match.group("entity"))
    if contract is None:
        return None
    return contract, match.group("ext")
