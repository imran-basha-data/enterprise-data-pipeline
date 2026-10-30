"""End-to-end check against generated data with known, injected defects.

The generator records exactly how many defects of each kind it injected. The
pipeline must quarantine every one of them under the right reason code and
nothing else; a mismatch in either direction fails the test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from edp.config import Settings
from edp.generator import GeneratorConfig, generate
from edp.pipeline import run_pipeline

from conftest import FIXED_NOW, query, scalar
from test_ingestion import staging_fingerprint

pytestmark = pytest.mark.integration

CONFIG = GeneratorConfig(
    seed=11, batches=3, days_per_batch=8, defect_rate=0.03, duplicate_rate=0.01
)


def test_pipeline_catches_exactly_the_injected_defects(settings: Settings, landing: Path) -> None:
    manifest = generate(landing, CONFIG)

    summary = run_pipeline(settings, now=FIXED_NOW)

    assert summary.status == "succeeded", summary.error
    assert summary.count("rejected") == 0
    observed = dict(
        query(
            settings.database_url,
            "SELECT code, count(*) FROM ops.quarantine, unnest(reason_codes) AS code GROUP BY code",
        )
    )
    assert observed == manifest.expected_quarantine()
    assert sum(r.rows_duplicate for r in summary.files) == manifest.expected_exact_duplicates()
    assert scalar(settings.database_url, "SELECT count(*) FROM ops.quarantine") == sum(
        manifest.expected_quarantine().values()
    )


def test_generated_dataset_incremental_batches_update_existing_rows(
    settings: Settings, landing: Path
) -> None:
    generate(landing, CONFIG)
    summary = run_pipeline(settings, now=FIXED_NOW)
    updated_shipments = sum(r.rows_updated for r in summary.files if r.entity == "shipments")
    assert updated_shipments > 0  # in-transit shipments delivered in later batches
    drift = [r for r in summary.files if r.schema_drift]
    assert [r.file_name for r in drift] == ["shipments__batch_003.json"]


def test_generated_dataset_full_rerun_is_a_no_op(settings: Settings, landing: Path) -> None:
    generate(landing, CONFIG)
    run_pipeline(settings, now=FIXED_NOW)
    before = staging_fingerprint(settings.database_url)
    second = run_pipeline(settings, now=FIXED_NOW)
    assert second.status == "succeeded"
    assert second.count("loaded") == 0
    assert staging_fingerprint(settings.database_url) == before
