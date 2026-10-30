"""Star-schema models, KPI definitions, the quality gate and reporting."""

from __future__ import annotations

import csv
import shutil
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from edp.config import Settings
from edp.generator import GeneratorConfig, generate
from edp.pipeline import run_pipeline
from edp.reporting import SECTIONS, generate_report

import factories as f
from conftest import FIXED_NOW, query, scalar

pytestmark = pytest.mark.integration


def ok(settings: Settings, **kwargs: object) -> None:
    summary = run_pipeline(settings, now=FIXED_NOW, **kwargs)  # type: ignore[arg-type]
    assert summary.status == "succeeded", summary.error


def write_golden_dataset(landing: Path) -> None:
    """A tiny dataset whose KPIs are worked out by hand in the assertions."""
    f.write_reference_data(landing)
    f.write_csv(
        landing,
        "production_orders",
        "batch_001",
        [
            # Finished 1h after planned end: late. 95 produced of 100, 3 scrap.
            f.production_order(),
            # Finished 1h early: on time. 50 of 50, no scrap.
            f.production_order(
                production_order_id="MO-2026-000002",
                planned_quantity="50",
                produced_quantity="50",
                scrap_quantity="0",
                planned_start="2026-01-06 08:00:00",
                planned_end="2026-01-06 16:00:00",
                actual_start="2026-01-06 08:00:00",
                actual_end="2026-01-06 15:00:00",
                updated_at="2026-01-06 15:10:00",
            ),
            # Still running: excluded from completion KPIs.
            f.production_order(
                production_order_id="MO-2026-000003",
                status="in_progress",
                actual_end="",
                produced_quantity="",
                scrap_quantity="0",
                updated_at="2026-01-05 09:00:00",
            ),
        ],
    )
    f.write_json(
        landing,
        "shipments",
        "batch_001",
        [
            # SUP-001: on time (day early), accepted 98/100 = in full. Lead 6 days.
            f.shipment(),
            # SUP-001: 3 days late, accepted 90/100 = not in full. Lead 10 days.
            f.shipment(
                shipment_id="SHP-2026-000002",
                quantity_received=90,
                quantity_rejected=0,
                order_date="2026-01-02",
                promised_date="2026-01-09",
                shipped_at="2026-01-10T09:00:00Z",
                delivered_at="2026-01-12T10:00:00Z",
                updated_at="2026-01-12T10:30:00Z",
            ),
            # SUP-002: on time, accepted 45/50 = not in full. 10% defects.
            f.shipment(
                shipment_id="SHP-2026-000003",
                supplier_id="SUP-002",
                product_id="PRD-0002",
                quantity_ordered=50,
                quantity_received=50,
                quantity_rejected=5,
                order_date="2026-01-03",
                promised_date="2026-01-08",
                shipped_at="2026-01-06T09:00:00Z",
                delivered_at="2026-01-08T11:00:00Z",
                updated_at="2026-01-08T11:30:00Z",
            ),
            # SUP-002: still in transit and past its promised date.
            f.shipment(
                shipment_id="SHP-2026-000004",
                supplier_id="SUP-002",
                product_id="PRD-0002",
                status="in_transit",
                quantity_received=None,
                quantity_rejected=0,
                order_date="2026-01-04",
                promised_date="2026-01-09",
                shipped_at="2026-01-08T09:00:00Z",
                delivered_at=None,
                updated_at="2026-01-08T09:05:00Z",
            ),
        ],
    )
    ids = [f"6f1c1d4e-0d0b-4b8e-9a43-3f1f1e0f2a{n:02d}" for n in range(1, 5)]
    f.write_jsonl(
        landing,
        "inventory_movements",
        "batch_001",
        [
            f.movement(movement_id=ids[0], quantity=40),
            f.movement(movement_id=ids[1], movement_type="issue", quantity=15),
            f.movement(movement_id=ids[2], movement_type="adjustment", quantity=-2),
            f.movement(
                movement_id=ids[3],
                product_id="PRD-0002",
                movement_type="receipt",
                quantity=500,
                event_timestamp="2026-01-12T12:00:00Z",
            ),
        ],
    )


def test_kpis_match_hand_calculated_values(settings: Settings, landing: Path) -> None:
    write_golden_dataset(landing)
    ok(settings)
    url = settings.database_url

    perf = query(
        url,
        "SELECT orders_completed, planned_units, produced_units, plan_attainment_pct, "
        "scrap_rate_pct, on_time_completion_pct FROM mart.v_production_performance",
    )
    # 145 / 150 = 96.67%; scrap 3 / 148 = 2.03%; 1 of 2 on time.
    assert perf == [(2, 150, 145, Decimal("96.67"), Decimal("2.03"), Decimal("50.00"))]

    scorecard = query(
        url,
        "SELECT supplier_id, deliveries, on_time_pct, in_full_pct, otif_pct, avg_lead_time_days, "
        "avg_days_late_when_late, defect_rate_pct, open_shipments, open_overdue "
        "FROM mart.v_supplier_scorecard ORDER BY supplier_id",
    )
    assert scorecard == [
        # Lead (6 + 10) / 2 = 8; defects 2 / 190 = 1.05%.
        ("SUP-001", 2, Decimal("50.00"), Decimal("50.00"), Decimal("50.00"), Decimal("8.00"),
         Decimal("3.00"), Decimal("1.05"), 0, 0),
        # As-of date is 2026-01-12, after SHP-4's promised date of 2026-01-09.
        ("SUP-002", 1, Decimal("100.00"), Decimal("0.00"), Decimal("0.00"), Decimal("5.00"),
         None, Decimal("10.00"), 1, 1),
    ]  # fmt: skip

    position = query(
        url,
        "SELECT product_id, on_hand, stock_value, is_stockout, is_below_reorder_point "
        "FROM mart.v_inventory_position ORDER BY product_id",
    )
    # PRD-0001: +40 -15 -2 = 23 units at 100.00 each; below its reorder point of 50.
    assert position == [
        ("PRD-0001", 23, Decimal("2300.00"), False, True),
        ("PRD-0002", 500, Decimal("10000.00"), False, False),
    ]

    delays = query(url, "SELECT delay_bucket, shipments FROM mart.v_shipment_delays ORDER BY 1")
    assert delays == [("3-5 days late", 1), ("On time or early", 2)]

    summary = query(
        url,
        "SELECT as_of_date::text, units_produced, supplier_otif_pct, inventory_value "
        "FROM mart.v_operational_kpis",
    )
    # OTIF across all delivered shipments: only SHP-1 of 3.
    assert summary == [("2026-01-12", 145, Decimal("33.33"), Decimal("12300.00"))]


def test_business_dates_use_local_timezone(settings: Settings, landing: Path) -> None:
    f.write_reference_data(landing)
    # 23:30 UTC on 30 May is 00:30 on 31 May in London (BST, UTC+1).
    f.write_jsonl(
        landing,
        "inventory_movements",
        "batch_001",
        [f.movement(event_timestamp="2026-05-30T23:30:00Z")],
    )
    ok(settings)
    assert scalar(settings.database_url, "SELECT date_key FROM mart.fact_inventory_movement") == (
        20260531
    )


def test_dimension_updates_keep_surrogate_keys(settings: Settings, landing: Path) -> None:
    f.write_reference_data(landing)
    f.write_csv(landing, "production_orders", "batch_001", [f.production_order()])
    ok(settings)
    key_before = scalar(
        settings.database_url,
        "SELECT product_key FROM mart.dim_product WHERE product_id='PRD-0001'",
    )
    f.write_csv(
        landing,
        "products",
        "batch_002",
        [f.product(unit_cost="110.00", updated_at="2026-02-01T00:00:00Z")],
    )
    ok(settings)
    rows = query(
        settings.database_url,
        "SELECT product_key, unit_cost FROM mart.dim_product WHERE product_id = 'PRD-0001'",
    )
    assert rows == [(key_before, Decimal("110.00"))]
    fact_key = scalar(settings.database_url, "SELECT product_key FROM mart.fact_production_order")
    assert fact_key == key_before


def test_accumulating_snapshot_updates_in_place(settings: Settings, landing: Path) -> None:
    f.write_reference_data(landing)
    in_transit = f.shipment(
        status="in_transit",
        quantity_received=None,
        quantity_rejected=0,
        delivered_at=None,
        updated_at="2026-01-05T09:05:00Z",
    )
    f.write_json(landing, "shipments", "batch_001", [in_transit])
    ok(settings)
    assert query(settings.database_url, "SELECT status, is_on_time FROM mart.fact_shipment") == [
        ("in_transit", None)
    ]
    f.write_json(landing, "shipments", "batch_002", [f.shipment()])
    ok(settings)
    assert query(
        settings.database_url, "SELECT status, is_on_time, lead_time_days FROM mart.fact_shipment"
    ) == [("delivered", True, 6)]


def test_incremental_refresh_only_processes_new_changes(settings: Settings, landing: Path) -> None:
    write_golden_dataset(landing)
    ok(settings)
    ok(settings)  # nothing new
    rows = dict(
        query(settings.database_url, "SELECT model_name, rows_affected FROM ops.model_watermarks")
    )
    assert set(rows.values()) == {0}


def mart_snapshot(url: str) -> dict[str, list[tuple[object, ...]]]:
    """Mart contents keyed by natural key, ignoring surrogate key numbering."""
    return {
        "orders": query(
            url,
            "SELECT f.production_order_id, p.product_id, f.status, f.produced_quantity, "
            "f.is_on_time, f.completed_date_key FROM mart.fact_production_order f "
            "JOIN mart.dim_product p USING (product_key) ORDER BY 1",
        ),
        "shipments": query(
            url,
            "SELECT f.shipment_id, s.supplier_id, f.status, f.delay_days, f.is_in_full "
            "FROM mart.fact_shipment f JOIN mart.dim_supplier s USING (supplier_key) ORDER BY 1",
        ),
        "positions": query(url, "SELECT * FROM mart.v_inventory_position ORDER BY 1, 4"),
        "suppliers": query(url, "SELECT * FROM mart.v_supplier_scorecard ORDER BY 1"),
        "products": query(
            url, "SELECT product_id, unit_cost, source_updated_at FROM mart.dim_product ORDER BY 1"
        ),
    }


def test_incremental_batches_equal_full_refresh(
    settings: Settings, landing: Path, tmp_path: Path
) -> None:
    source = tmp_path / "generated"
    generate(source, GeneratorConfig(seed=5, batches=3, days_per_batch=6))
    for batch in ("batch_001", "batch_002", "batch_003"):
        for path in source.glob(f"*__{batch}.*"):
            shutil.copy(path, landing / path.name)
        ok(settings)
    incremental = mart_snapshot(settings.database_url)

    ok(settings, full_refresh=True)

    assert mart_snapshot(settings.database_url) == incremental
    assert len(incremental["orders"]) > 0 and len(incremental["shipments"]) > 0


def test_quality_gate_blocks_inconsistent_marts(settings: Settings, landing: Path) -> None:
    write_golden_dataset(landing)
    ok(settings)
    url = settings.database_url
    before = mart_snapshot(url)

    # Simulate a defect in model logic: a watermark that skips new changes.
    with psycopg.connect(url) as conn:
        conn.execute(
            "UPDATE ops.model_watermarks SET last_change_seq = 1000000 "
            "WHERE model_name = 'fact_shipment'"
        )
    f.write_json(landing, "shipments", "batch_002", [f.shipment(shipment_id="SHP-2026-000099")])
    f.write_csv(
        landing,
        "products",
        "batch_002",
        [f.product(unit_cost="150.00", updated_at="2026-02-01T00:00:00Z")],
    )
    summary = run_pipeline(settings, now=FIXED_NOW)

    assert summary.status == "failed"
    assert "reconcile_shipments" in (summary.error or "")
    # The whole refresh rolled back: even the valid product change is not published.
    assert mart_snapshot(url) == before
    failed = query(
        url,
        "SELECT check_name FROM ops.dq_results WHERE run_id = %s AND NOT passed "
        "AND severity = 'error'",
        (summary.run_id,),
    )
    assert failed == [("reconcile_shipments",)]


def test_report_writes_markdown_and_csv(settings: Settings, landing: Path, tmp_path: Path) -> None:
    write_golden_dataset(landing)
    ok(settings)
    out = tmp_path / "report"
    report = generate_report(settings.database_url, out)

    text = report.read_text()
    for section in SECTIONS:
        assert f"## {section.title}" in text
        assert (out / f"{section.slug}.csv").exists()
    assert "Data as of **2026-01-12**" in text
    with (out / "supplier_scorecard.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert [r["supplier_id"] for r in rows] == ["SUP-002", "SUP-001"]  # worst OTIF first


def test_negative_stock_raises_warning_not_failure(settings: Settings, landing: Path) -> None:
    f.write_reference_data(landing)
    f.write_jsonl(
        landing, "inventory_movements", "batch_001", [f.movement(movement_type="issue", quantity=5)]
    )
    summary = run_pipeline(settings, now=FIXED_NOW)
    assert summary.status == "succeeded"
    warning = next(c for c in summary.checks if c.name == "negative_stock_positions")
    assert (warning.passed, warning.observed) == (False, 1)
