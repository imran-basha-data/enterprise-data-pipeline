"""Database tests for file ingestion, incremental loads and data quality."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from edp.config import Settings
from edp.pipeline import RunSummary, run_pipeline

import factories as f
from conftest import FIXED_NOW, query, scalar

pytestmark = pytest.mark.integration


def run(settings: Settings) -> RunSummary:
    summary = run_pipeline(settings, now=FIXED_NOW)
    assert summary.status == "succeeded", summary.error
    return summary


def file_result(summary: RunSummary, name: str):  # type: ignore[no-untyped-def]
    return next(r for r in summary.files if r.file_name == name)


def staging_fingerprint(url: str) -> dict[str, object]:
    """Row counts plus a content hash of every staging table."""
    tables = [
        "suppliers",
        "warehouses",
        "products",
        "production_orders",
        "inventory_movements",
        "shipments",
    ]
    return {
        t: query(
            url,
            "SELECT count(*), "
            "md5(coalesce(string_agg(_record_hash, ',' ORDER BY _record_hash), '')) "
            f"FROM staging.{t}",
        )[0]
        for t in tables
    }


def test_reference_and_transactional_data_load_with_types(
    settings: Settings, landing: Path
) -> None:
    f.write_reference_data(landing)
    f.write_csv(landing, "production_orders", "batch_001", [f.production_order()])
    f.write_jsonl(
        landing,
        "inventory_movements",
        "batch_001",
        [
            f.movement(),
            f.movement(
                movement_id="6f1c1d4e-0d0b-4b8e-9a43-3f1f1e0f2a02",
                movement_type="issue",
                quantity=15,
            ),
        ],
    )
    f.write_json(landing, "shipments", "batch_001", [f.shipment()])

    summary = run(settings)

    assert summary.count("loaded") == 6
    assert scalar(settings.database_url, "SELECT count(*) FROM staging.products") == 2
    start = scalar(
        settings.database_url,
        "SELECT actual_start FROM staging.production_orders "
        "WHERE production_order_id = 'MO-2026-000001'",
    )
    assert start == datetime(2026, 1, 5, 8, 30, tzinfo=UTC)  # London winter time == UTC
    deltas = query(
        settings.database_url,
        "SELECT movement_type, quantity_delta FROM staging.inventory_movements "
        "ORDER BY movement_type",
    )
    assert deltas == [("issue", -15), ("receipt", 40)]
    raw_rows = scalar(settings.database_url, "SELECT count(*) FROM raw.landed_records")
    assert raw_rows == 2 + 2 + 2 + 1 + 2 + 1


def test_rerun_is_idempotent(settings: Settings, landing: Path) -> None:
    f.write_reference_data(landing)
    f.write_csv(landing, "production_orders", "batch_001", [f.production_order()])
    run(settings)
    before = staging_fingerprint(settings.database_url)
    raw_before = scalar(settings.database_url, "SELECT count(*) FROM raw.landed_records")

    second = run(settings)

    assert second.count("skipped") == 4 and second.count("loaded") == 0
    assert staging_fingerprint(settings.database_url) == before
    assert scalar(settings.database_url, "SELECT count(*) FROM raw.landed_records") == raw_before


def test_same_content_under_a_new_name_is_not_reloaded(settings: Settings, landing: Path) -> None:
    f.write_reference_data(landing)
    run(settings)
    shutil.copy(landing / "products__batch_001.csv", landing / "products__batch_001_resend.csv")
    summary = run(settings)
    assert file_result(summary, "products__batch_001_resend.csv").status == "skipped"


def test_incremental_update_advances_change_sequence(settings: Settings, landing: Path) -> None:
    f.write_reference_data(landing)
    f.write_csv(
        landing,
        "production_orders",
        "batch_001",
        [
            f.production_order(
                status="in_progress",
                actual_end="",
                produced_quantity="",
                scrap_quantity="0",
                updated_at="2026-01-05 08:40:00",
            ),
        ],
    )
    run(settings)
    seq_before = scalar(settings.database_url, "SELECT _change_seq FROM staging.production_orders")

    f.write_csv(landing, "production_orders", "batch_002", [f.production_order()])
    summary = run(settings)

    result = file_result(summary, "production_orders__batch_002.csv")
    assert (result.rows_inserted, result.rows_updated) == (0, 1)
    status, produced, seq_after = query(
        settings.database_url,
        "SELECT status, produced_quantity, _change_seq FROM staging.production_orders",
    )[0]
    assert (status, produced) == ("completed", 95)
    assert seq_after > seq_before  # type: ignore[operator]


def test_out_of_order_delivery_never_regresses_state(settings: Settings, landing: Path) -> None:
    f.write_reference_data(landing)
    # The newer version arrives first (batch_001), the older one later (batch_002).
    f.write_csv(landing, "production_orders", "batch_001", [f.production_order()])
    f.write_csv(
        landing,
        "production_orders",
        "batch_002",
        [
            f.production_order(
                status="in_progress",
                actual_end="",
                produced_quantity="",
                updated_at="2026-01-05 08:40:00",
            ),
        ],
    )
    summary = run(settings)
    assert file_result(summary, "production_orders__batch_002.csv").rows_stale == 1
    assert (
        scalar(settings.database_url, "SELECT status FROM staging.production_orders") == "completed"
    )


def test_in_file_duplicates_and_conflicts(settings: Settings, landing: Path) -> None:
    f.write_reference_data(landing)
    original = f.production_order()
    older = f.production_order(
        status="in_progress", actual_end="", produced_quantity="", updated_at="2026-01-05 08:40:00"
    )
    conflicting = f.production_order(planned_quantity="120")  # same version, new content
    f.write_csv(landing, "production_orders", "batch_001", [original, original, older, conflicting])

    result = file_result(run(settings), "production_orders__batch_001.csv")

    assert (
        result.rows_inserted,
        result.rows_duplicate,
        result.rows_stale,
        result.rows_quarantined,
    ) == (1, 1, 1, 1)
    reasons = query(settings.database_url, "SELECT reason_codes, row_number FROM ops.quarantine")
    assert reasons == [(["duplicate_conflict"], 4)]


def test_conflicting_event_across_files_is_quarantined(settings: Settings, landing: Path) -> None:
    f.write_reference_data(landing)
    f.write_jsonl(landing, "inventory_movements", "batch_001", [f.movement()])
    run(settings)
    f.write_jsonl(landing, "inventory_movements", "batch_002", [f.movement(quantity=41)])
    f.write_jsonl(landing, "inventory_movements", "batch_003", [f.movement(warehouse_id="wh-bhm")])
    summary = run(settings)

    assert file_result(summary, "inventory_movements__batch_002.jsonl").rows_quarantined == 1
    assert file_result(summary, "inventory_movements__batch_003.jsonl").rows_unchanged == 1
    assert scalar(settings.database_url, "SELECT quantity FROM staging.inventory_movements") == 40


def test_orphan_references_are_quarantined(settings: Settings, landing: Path) -> None:
    f.write_reference_data(landing)
    valid = [f.shipment(shipment_id=f"SHP-2026-{i:06d}") for i in range(10, 18)]
    f.write_json(
        landing,
        "shipments",
        "batch_001",
        [
            *valid,
            f.shipment(shipment_id="SHP-2026-000002", warehouse_id="WH-XXX"),
            f.shipment(shipment_id="SHP-2026-000003", supplier_id="SUP-404"),
        ],
    )
    run(settings)  # 2 of 10 bad = 20%, below the 25% reject-rate threshold
    rows = query(
        settings.database_url,
        "SELECT record_key, issues->0->>'field' FROM ops.quarantine ORDER BY record_key",
    )
    assert rows == [("SHP-2026-000002", "warehouse_id"), ("SHP-2026-000003", "supplier_id")]
    assert scalar(settings.database_url, "SELECT count(*) FROM staging.shipments") == 8


def test_every_file_accounts_for_every_row(settings: Settings, landing: Path) -> None:
    f.write_reference_data(landing)
    rows = [f.production_order(production_order_id=f"MO-2026-{i:06d}") for i in range(1, 11)]
    rows += [
        rows[0],
        f.production_order(production_order_id="MO-2026-000099", planned_quantity="-1"),
    ]
    f.write_csv(landing, "production_orders", "batch_001", rows)
    summary = run(settings)
    for r in summary.files:
        assert r.rows_read == (
            r.rows_inserted
            + r.rows_updated
            + r.rows_unchanged
            + r.rows_stale
            + r.rows_duplicate
            + r.rows_quarantined
        ), r.file_name


def test_unexpected_column_is_loaded_with_a_warning(settings: Settings, landing: Path) -> None:
    f.write_reference_data(landing)
    f.write_json(landing, "shipments", "batch_001", [{**f.shipment(), "eta_confidence": "high"}])
    summary = run(settings)
    assert file_result(summary, "shipments__batch_001.json").status == "loaded"
    check = query(
        settings.database_url,
        "SELECT passed, severity, details->'unexpected' FROM ops.dq_results "
        "WHERE check_name = 'schema_contract' AND entity = 'shipments'",
    )
    assert check == [(False, "warning", ["eta_confidence"])]


def test_unexpected_column_rejected_under_strict_policy(settings: Settings, landing: Path) -> None:
    f.write_reference_data(landing)
    f.write_json(landing, "shipments", "batch_001", [{**f.shipment(), "eta_confidence": "high"}])
    summary = run(replace(settings, schema_drift_policy="reject"))
    result = file_result(summary, "shipments__batch_001.json")
    assert result.status == "rejected"
    assert result.rejection_reason == "unexpected_columns: eta_confidence"
    assert scalar(settings.database_url, "SELECT count(*) FROM staging.shipments") == 0


def test_missing_required_column_rejects_file(settings: Settings, landing: Path) -> None:
    f.write_reference_data(landing)
    row = f.production_order()
    del row["product_id"]
    f.write_csv(landing, "production_orders", "batch_001", [row])
    summary = run(settings)
    result = file_result(summary, "production_orders__batch_001.csv")
    assert result.status == "rejected"
    assert "product_id" in (result.rejection_reason or "")
    status = scalar(
        settings.database_url,
        "SELECT status FROM ops.ingested_files WHERE entity = 'production_orders'",
    )
    assert status == "rejected"
    # A rejected file is remembered and not retried until its content changes.
    assert file_result(run(settings), "production_orders__batch_001.csv").status == "skipped"


def test_unreadable_file_is_rejected_and_run_continues(settings: Settings, landing: Path) -> None:
    f.write_reference_data(landing)
    (landing / "shipments__batch_001.json").write_text('[{"shipment_id": ')
    f.write_csv(landing, "production_orders", "batch_001", [f.production_order()])
    summary = run(settings)
    assert file_result(summary, "shipments__batch_001.json").status == "rejected"
    assert file_result(summary, "production_orders__batch_001.csv").status == "loaded"


def test_reject_rate_breach_loads_nothing_but_keeps_evidence(
    settings: Settings, landing: Path
) -> None:
    f.write_reference_data(landing)
    rows = [
        f.production_order(production_order_id=f"MO-2026-{i:06d}", product_id="PRD-9999")
        for i in range(1, 5)
    ]
    rows.append(f.production_order(production_order_id="MO-2026-000005"))
    f.write_csv(landing, "production_orders", "batch_001", rows)

    result = file_result(run(settings), "production_orders__batch_001.csv")

    assert result.status == "rejected"
    assert result.rejection_reason is not None and "reject_rate_exceeded" in result.rejection_reason
    assert scalar(settings.database_url, "SELECT count(*) FROM staging.production_orders") == 0
    assert scalar(settings.database_url, "SELECT count(*) FROM ops.quarantine") == 4
    breach = scalar(
        settings.database_url,
        "SELECT passed FROM ops.dq_results WHERE check_name = 'reject_rate'"
        " AND entity = 'production_orders'",
    )
    assert breach is False


def test_quarantine_keeps_original_payload(settings: Settings, landing: Path) -> None:
    f.write_reference_data(landing)
    bad = f.movement(event_timestamp="not-a-timestamp")
    f.write_jsonl(landing, "inventory_movements", "batch_001", [bad])
    run(settings)
    payload, issues = query(settings.database_url, "SELECT payload, issues FROM ops.quarantine")[0]
    assert payload == json.loads(json.dumps(bad))
    assert issues[0]["reason"] == "invalid_date"  # type: ignore[index]
