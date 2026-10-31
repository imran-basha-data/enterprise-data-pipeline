import csv
import json
from pathlib import Path

import pytest

from edp.generator import MANIFEST_NAME, GeneratorConfig, generate

SMALL = GeneratorConfig(seed=7, batches=2, days_per_batch=5, orders_per_day=6, shipments_per_day=4)


def _contents(folder: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())}


def test_generation_is_deterministic(tmp_path: Path) -> None:
    generate(tmp_path / "a", SMALL)
    generate(tmp_path / "b", SMALL)
    assert _contents(tmp_path / "a") == _contents(tmp_path / "b")


def test_different_seed_produces_different_data(tmp_path: Path) -> None:
    generate(tmp_path / "a", SMALL)
    generate(tmp_path / "b", GeneratorConfig(**{**SMALL.__dict__, "seed": 8}))
    assert _contents(tmp_path / "a") != _contents(tmp_path / "b")


def test_manifest_matches_written_files(tmp_path: Path) -> None:
    manifest = generate(tmp_path, SMALL)
    on_disk = json.loads((tmp_path / MANIFEST_NAME).read_text())
    assert on_disk["expected_quarantine_by_reason"] == manifest.expected_quarantine()
    written = {p.name for p in tmp_path.iterdir() if not p.name.startswith("_")}
    assert written == {f.file for f in manifest.files}
    orders = tmp_path / "production_orders__batch_001.csv"
    with orders.open() as handle:
        rows = list(csv.DictReader(handle))
    entry = next(f for f in manifest.files if f.file == orders.name)
    assert entry.rows_written == len(rows)


def test_reference_data_only_in_expected_batches(tmp_path: Path) -> None:
    generate(tmp_path, SMALL)
    names = {p.name for p in tmp_path.iterdir()}
    assert "warehouses__batch_001.csv" in names
    assert "warehouses__batch_002.csv" not in names
    assert "suppliers__batch_002.csv" in names  # lead-time change delta


def test_schema_drift_column_only_in_configured_batch(tmp_path: Path) -> None:
    generate(tmp_path, GeneratorConfig(**{**SMALL.__dict__, "schema_drift_batch": 2}))
    first = json.loads((tmp_path / "shipments__batch_001.json").read_text())
    second = json.loads((tmp_path / "shipments__batch_002.json").read_text())
    assert all("carrier_service_level" not in r for r in first)
    assert all("carrier_service_level" in r for r in second)


def test_zero_defect_rate_injects_nothing(tmp_path: Path) -> None:
    manifest = generate(
        tmp_path, GeneratorConfig(**{**SMALL.__dict__, "defect_rate": 0, "duplicate_rate": 0})
    )
    assert manifest.expected_quarantine() == {}
    assert manifest.expected_exact_duplicates() == 0


@pytest.mark.parametrize(
    "overrides", [{"batches": 0}, {"defect_rate": 0.9}, {"duplicate_rate": -0.1}]
)
def test_invalid_generator_config(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        GeneratorConfig(**overrides)  # type: ignore[arg-type]
