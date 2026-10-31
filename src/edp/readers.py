"""Landing-zone discovery, file readers and schema-contract checks."""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from edp.contracts import EntityContract, match_landing_file

MALFORMED_KEY = "_malformed"


class UnreadableFileError(Exception):
    """The file cannot be parsed at all (e.g. truncated JSON array)."""


@dataclass(frozen=True)
class LandedFile:
    path: Path
    contract: EntityContract
    file_format: str

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class DiscoveryResult:
    files: list[LandedFile] = field(default_factory=list)
    ignored: list[Path] = field(default_factory=list)


def discover_files(landing_dir: Path) -> DiscoveryResult:
    """Find loadable files and order them by entity dependency, then name.

    Reference data (suppliers, warehouses) loads before products, and products
    before the transactional entities that reference them. Within an entity,
    files load in lexical order, so batch names should sort chronologically.
    Files whose names start with ``_`` or ``.`` are ignored.
    """
    result = DiscoveryResult()
    if not landing_dir.is_dir():
        raise FileNotFoundError(f"landing directory '{landing_dir}' does not exist")
    for path in sorted(p for p in landing_dir.iterdir() if p.is_file()):
        if path.name.startswith(("_", ".")):
            continue
        matched = match_landing_file(path.name)
        if matched is None:
            result.ignored.append(path)
            continue
        contract, ext = matched
        result.files.append(LandedFile(path=path, contract=contract, file_format=ext))
    result.files.sort(key=lambda f: (f.contract.load_order, f.contract.name, f.name))
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class ParsedFile:
    columns: list[str]
    rows: list[dict[str, Any]]


def read_file(landed: LandedFile) -> ParsedFile:
    if landed.file_format == "csv":
        return _read_csv(landed.path)
    if landed.file_format == "jsonl":
        return _read_jsonl(landed.path)
    return _read_json(landed.path)


def _read_csv(path: Path) -> ParsedFile:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = [c.strip() for c in (reader.fieldnames or [])]
        if not columns:
            raise UnreadableFileError(f"{path.name} has no header row")
        rows: list[dict[str, Any]] = []
        for raw in reader:
            extra = raw.pop(None, None)
            row = {(k or "").strip(): v for k, v in raw.items()}
            if extra is not None or any(v is None for v in raw.values()):
                row[MALFORMED_KEY] = "row has a different number of fields than the header"
            rows.append(row)
    return ParsedFile(columns=columns, rows=rows)


def _read_json(path: Path) -> ParsedFile:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise UnreadableFileError(f"{path.name} is not valid JSON: {exc}") from exc
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        payload = payload["records"]
    if not isinstance(payload, list):
        raise UnreadableFileError(f"{path.name} must contain a JSON array of objects")
    rows = [_as_row(item) for item in payload]
    return ParsedFile(columns=_union_columns(rows), rows=rows)


def _read_jsonl(path: Path) -> ParsedFile:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append(_as_row(json.loads(stripped)))
            except json.JSONDecodeError:
                rows.append({MALFORMED_KEY: "invalid JSON", "_raw_line": stripped[:2000]})
    return ParsedFile(columns=_union_columns(rows), rows=rows)


def _as_row(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    return {MALFORMED_KEY: "record is not a JSON object", "_raw_value": item}


def _union_columns(rows: Iterable[dict[str, Any]]) -> list[str]:
    seen: dict[str, None] = {}
    for row in rows:
        if MALFORMED_KEY in row:
            continue
        for key in row:
            seen.setdefault(key, None)
    return list(seen)


@dataclass(frozen=True)
class SchemaReport:
    missing_required: tuple[str, ...]
    missing_optional: tuple[str, ...]
    unexpected: tuple[str, ...]

    @property
    def is_compatible(self) -> bool:
        return not self.missing_required

    @property
    def has_drift(self) -> bool:
        return bool(self.missing_required or self.missing_optional or self.unexpected)

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "missing_required": list(self.missing_required),
            "missing_optional": list(self.missing_optional),
            "unexpected": list(self.unexpected),
        }


def check_schema(contract: EntityContract, columns: Iterable[str]) -> SchemaReport:
    present = set(columns)
    expected = set(contract.column_names)
    required = contract.required_columns
    return SchemaReport(
        missing_required=tuple(sorted(required - present)),
        missing_optional=tuple(sorted((expected - required) - present)),
        unexpected=tuple(sorted(present - expected)),
    )
