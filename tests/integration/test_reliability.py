"""Failure recovery, retries, locking and migration safety."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from edp.config import Settings
from edp.db import MigrationError, PipelineLockedError, apply_migrations, pipeline_lock
from edp.pipeline import run_pipeline

import factories as f
from conftest import FIXED_NOW, scalar
from test_ingestion import staging_fingerprint

pytestmark = pytest.mark.integration


def _seed(landing: Path) -> None:
    f.write_reference_data(landing)
    f.write_csv(landing, "production_orders", "batch_001", [f.production_order()])
    f.write_json(landing, "shipments", "batch_001", [f.shipment()])


def test_crash_mid_file_rolls_back_that_file_and_rerun_recovers(
    settings: Settings, landing: Path, tmp_path: Path, db: str
) -> None:
    _seed(landing)
    calls = {"n": 0}

    def crash_on_orders(stage: str) -> None:
        # Fail after staging writes for the 4th file (production orders).
        if stage == "after_staging":
            calls["n"] += 1
            if calls["n"] == 4:
                raise RuntimeError("simulated crash")

    failed = run_pipeline(settings, now=FIXED_NOW, checkpoint=crash_on_orders)

    assert failed.status == "failed"
    assert "simulated crash" in (failed.error or "")
    # Files before the crash are committed; the crashing file left no trace.
    assert scalar(db, "SELECT count(*) FROM staging.products") == 2
    assert scalar(db, "SELECT count(*) FROM staging.production_orders") == 0
    assert (
        scalar(db, "SELECT count(*) FROM ops.ingested_files WHERE entity = 'production_orders'")
        == 0
    )
    assert (
        scalar(db, "SELECT status FROM ops.pipeline_runs WHERE run_id = %s", (failed.run_id,))
        == "failed"
    )

    recovered = run_pipeline(settings, now=FIXED_NOW)
    assert recovered.status == "succeeded"
    assert recovered.count("skipped") == 3 and recovered.count("loaded") == 2
    after_recovery = staging_fingerprint(db)

    # The recovered state is identical to a clean single run.
    from conftest import reset_database

    reset_database(db)
    assert run_pipeline(settings, now=FIXED_NOW).status == "succeeded"
    assert staging_fingerprint(db) == after_recovery


def test_transient_error_is_retried_without_duplicate_writes(
    settings: Settings, landing: Path, db: str
) -> None:
    _seed(landing)
    failures = {"left": 2}

    def flaky(stage: str) -> None:
        if stage == "after_staging" and failures["left"] > 0:
            failures["left"] -= 1
            raise psycopg.OperationalError("server closed the connection unexpectedly")

    summary = run_pipeline(settings, now=FIXED_NOW, checkpoint=flaky)

    assert summary.status == "succeeded"
    assert failures["left"] == 0
    assert scalar(db, "SELECT count(*) FROM ops.ingested_files") == 5
    assert (
        scalar(
            db,
            "SELECT count(*) FROM raw.landed_records WHERE file_id IN "
            "(SELECT file_id FROM ops.ingested_files WHERE entity = 'suppliers')",
        )
        == 2
    )


def test_run_fails_when_retries_are_exhausted(settings: Settings, landing: Path, db: str) -> None:
    _seed(landing)

    def always_down(stage: str) -> None:
        if stage == "after_raw":
            raise psycopg.OperationalError("database unavailable")

    summary = run_pipeline(settings, now=FIXED_NOW, checkpoint=always_down)
    assert summary.status == "failed"
    assert "OperationalError" in (summary.error or "")
    assert scalar(db, "SELECT count(*) FROM ops.ingested_files") == 0


def test_concurrent_runs_are_prevented(settings: Settings, landing: Path) -> None:
    _seed(landing)
    with pipeline_lock(settings.database_url), pytest.raises(PipelineLockedError):
        run_pipeline(settings, now=FIXED_NOW)
    # The lock is released afterwards and a normal run proceeds.
    assert run_pipeline(settings, now=FIXED_NOW).status == "succeeded"


def test_crashed_runs_are_marked_abandoned(settings: Settings, landing: Path, db: str) -> None:
    _seed(landing)
    with psycopg.connect(db) as conn:
        conn.execute(
            "INSERT INTO ops.pipeline_runs (run_id) VALUES ('00000000-0000-0000-0000-000000000001')"
        )
    run_pipeline(settings, now=FIXED_NOW)
    assert (
        scalar(
            db,
            "SELECT status FROM ops.pipeline_runs "
            "WHERE run_id = '00000000-0000-0000-0000-000000000001'",
        )
        == "abandoned"
    )


def test_migrations_are_idempotent_and_tamper_evident(db: str) -> None:
    assert apply_migrations(db) == []
    with psycopg.connect(db) as conn:
        conn.execute(
            "UPDATE ops.schema_migrations SET checksum = repeat('0', 64) WHERE version = '001'"
        )
    with pytest.raises(MigrationError, match="modified"):
        apply_migrations(db)


def test_missing_landing_directory_fails_the_run(settings: Settings, tmp_path: Path) -> None:
    from dataclasses import replace

    summary = run_pipeline(replace(settings, landing_dir=tmp_path / "nope"), now=FIXED_NOW)
    assert summary.status == "failed"
    assert "does not exist" in (summary.error or "")
