import json
from pathlib import Path

import pytest

from edp.contracts import PRODUCTION_ORDERS, SHIPMENTS
from edp.readers import (
    MALFORMED_KEY,
    UnreadableFileError,
    check_schema,
    discover_files,
    read_file,
    sha256_file,
)

import factories as f


def test_discovery_orders_by_dependency_and_ignores_unknown(tmp_path: Path) -> None:
    for name in [
        "shipments__batch_001.json",
        "products__batch_002.csv",
        "products__batch_001.csv",
        "suppliers__batch_001.csv",
        "_generation_manifest.json",
        "notes.txt",
        "customers__batch_001.csv",
    ]:
        (tmp_path / name).write_text("x")
    result = discover_files(tmp_path)
    assert [lf.name for lf in result.files] == [
        "suppliers__batch_001.csv",
        "products__batch_001.csv",
        "products__batch_002.csv",
        "shipments__batch_001.json",
    ]
    assert sorted(p.name for p in result.ignored) == ["customers__batch_001.csv", "notes.txt"]


def test_discovery_fails_for_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        discover_files(tmp_path / "missing")


def _landed(path: Path):  # type: ignore[no-untyped-def]
    return discover_files(path.parent).files[0]


def test_csv_with_bom_and_ragged_rows(tmp_path: Path) -> None:
    path = tmp_path / "production_orders__b1.csv"
    header = ",".join(PRODUCTION_ORDERS.column_names)
    good = ",".join(str(v) for v in f.production_order().values())
    path.write_text(
        "\ufeff" + header + "\n" + good + "\n" + good + ",EXTRA\nMO-1,short\n", encoding="utf-8"
    )
    parsed = read_file(_landed(path))
    assert parsed.columns[0] == "production_order_id"
    assert MALFORMED_KEY not in parsed.rows[0]
    assert MALFORMED_KEY in parsed.rows[1]
    assert MALFORMED_KEY in parsed.rows[2]


def test_jsonl_keeps_malformed_lines_as_records(tmp_path: Path) -> None:
    path = tmp_path / "inventory_movements__b1.jsonl"
    path.write_text(json.dumps(f.movement()) + '\n{"movement_id": "abc\n[1,2]\n\n')
    parsed = read_file(_landed(path))
    assert len(parsed.rows) == 3
    assert MALFORMED_KEY not in parsed.rows[0]
    assert parsed.rows[1][MALFORMED_KEY] == "invalid JSON"
    assert "not a JSON object" in parsed.rows[2][MALFORMED_KEY]
    assert "movement_id" in parsed.columns


def test_json_array_and_wrapped_records(tmp_path: Path) -> None:
    path = tmp_path / "shipments__b1.json"
    path.write_text(
        json.dumps({"records": [f.shipment(), f.shipment(shipment_id="SHP-2026-000002")]})
    )
    parsed = read_file(_landed(path))
    assert len(parsed.rows) == 2


@pytest.mark.parametrize("content", ['{"truncated": ', '{"not": "a list"}'])
def test_unreadable_json_file(tmp_path: Path, content: str) -> None:
    path = tmp_path / "shipments__b1.json"
    path.write_text(content)
    with pytest.raises(UnreadableFileError):
        read_file(_landed(path))


def test_schema_check_detects_drift() -> None:
    columns = [c for c in SHIPMENTS.column_names if c not in {"supplier_id", "carrier"}] + ["eta"]
    report = check_schema(SHIPMENTS, columns)
    assert report.missing_required == ("supplier_id",)
    assert report.missing_optional == ("carrier",)
    assert report.unexpected == ("eta",)
    assert not report.is_compatible
    assert report.has_drift


def test_schema_check_passes_exact_columns() -> None:
    report = check_schema(SHIPMENTS, SHIPMENTS.column_names)
    assert report.is_compatible and not report.has_drift


def test_checksum_depends_only_on_content(tmp_path: Path) -> None:
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("same")
    b.write_text("same")
    assert sha256_file(a) == sha256_file(b)
    b.write_text("different")
    assert sha256_file(a) != sha256_file(b)
