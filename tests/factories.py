"""Builders for small, hand-written landing files used across tests."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from edp.contracts import CONTRACTS


def supplier(**overrides: Any) -> dict[str, Any]:
    row = {
        "supplier_id": "SUP-001",
        "supplier_name": "Test Castings",
        "country_code": "GB",
        "contracted_lead_time_days": "7",
        "is_active": "true",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    return {**row, **overrides}


def warehouse(**overrides: Any) -> dict[str, Any]:
    row = {
        "warehouse_id": "WH-BHM",
        "warehouse_name": "Birmingham Plant",
        "city": "Birmingham",
        "region": "West Midlands",
        "country_code": "GB",
        "capacity_units": "1000",
        "is_active": "true",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    return {**row, **overrides}


def product(**overrides: Any) -> dict[str, Any]:
    row = {
        "product_id": "PRD-0001",
        "sku": "HYD-HP-100",
        "product_name": "Hydraulic Pump HP-100",
        "category": "Hydraulic Components",
        "unit_of_measure": "EA",
        "unit_cost": "100.00",
        "reorder_point": "50",
        "primary_supplier_id": "SUP-001",
        "is_active": "true",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    return {**row, **overrides}


def production_order(**overrides: Any) -> dict[str, Any]:
    row = {
        "production_order_id": "MO-2026-000001",
        "product_id": "PRD-0001",
        "warehouse_id": "WH-BHM",
        "status": "completed",
        "planned_quantity": "100",
        "produced_quantity": "95",
        "scrap_quantity": "3",
        "planned_start": "2026-01-05 08:00:00",
        "planned_end": "2026-01-05 16:00:00",
        "actual_start": "2026-01-05 08:30:00",
        "actual_end": "2026-01-05 17:00:00",
        "updated_at": "2026-01-05 17:10:00",
    }
    return {**row, **overrides}


def movement(**overrides: Any) -> dict[str, Any]:
    row = {
        "movement_id": "6f1c1d4e-0d0b-4b8e-9a43-3f1f1e0f2a01",
        "product_id": "PRD-0001",
        "warehouse_id": "WH-BHM",
        "movement_type": "receipt",
        "quantity": 40,
        "reference_id": "SHP-2026-000001",
        "event_timestamp": "2026-01-06T10:00:00Z",
    }
    return {**row, **overrides}


def shipment(**overrides: Any) -> dict[str, Any]:
    row = {
        "shipment_id": "SHP-2026-000001",
        "supplier_id": "SUP-001",
        "product_id": "PRD-0001",
        "warehouse_id": "WH-BHM",
        "status": "delivered",
        "carrier": "DPD",
        "quantity_ordered": 100,
        "quantity_received": 100,
        "quantity_rejected": 2,
        "order_date": "2026-01-01",
        "promised_date": "2026-01-08",
        "shipped_at": "2026-01-05T09:00:00Z",
        "delivered_at": "2026-01-07T14:00:00Z",
        "updated_at": "2026-01-07T14:30:00Z",
    }
    return {**row, **overrides}


def write_csv(landing: Path, entity: str, batch: str, rows: list[dict[str, Any]]) -> Path:
    path = landing / f"{entity}__{batch}.csv"
    columns = list(rows[0].keys()) if rows else list(CONTRACTS[entity].column_names)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_jsonl(landing: Path, entity: str, batch: str, rows: list[dict[str, Any]]) -> Path:
    path = landing / f"{entity}__{batch}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def write_json(landing: Path, entity: str, batch: str, rows: list[dict[str, Any]]) -> Path:
    path = landing / f"{entity}__{batch}.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def write_reference_data(landing: Path, batch: str = "batch_001") -> None:
    """Minimal valid suppliers, warehouses and products."""
    write_csv(
        landing,
        "suppliers",
        batch,
        [supplier(), supplier(supplier_id="SUP-002", supplier_name="Second Supplier")],
    )
    write_csv(
        landing,
        "warehouses",
        batch,
        [
            warehouse(),
            warehouse(warehouse_id="WH-MAN", warehouse_name="Manchester", city="Manchester"),
        ],
    )
    write_csv(
        landing,
        "products",
        batch,
        [
            product(),
            product(
                product_id="PRD-0002",
                sku="SNS-PS-100",
                product_name="Proximity Sensor",
                category="Sensors",
                unit_cost="20.00",
                primary_supplier_id="SUP-002",
            ),
        ],
    )
