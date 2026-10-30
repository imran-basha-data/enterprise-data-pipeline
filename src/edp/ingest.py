"""File ingestion: raw landing, validation, de-duplication and staging upserts.

``ingest_file`` must run inside a single database transaction. Every write for
a file -- manifest row, raw records, staging upserts, quarantine rows and
data-quality results -- commits together or not at all. Combined with the
manifest's unique checksum, this makes a file load both atomic and idempotent:
a crash at any point leaves no trace, and a successful load is never repeated.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from edp.config import Settings
from edp.contracts import CONTRACTS, EntityContract, FieldType, Issue, Reason
from edp.logging_setup import get_logger
from edp.readers import (
    MALFORMED_KEY,
    LandedFile,
    UnreadableFileError,
    check_schema,
    read_file,
    sha256_file,
)
from edp.validation import ValidatedRecord, record_hash, validate_record

log = get_logger(__name__)

Checkpoint = Callable[[str], None]
LINEAGE_COLUMNS = ("_record_hash", "_file_id", "_run_id")


@dataclass
class FileResult:
    file_name: str
    entity: str
    status: str  # loaded | rejected | skipped
    file_id: int | None = None
    rows_read: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_unchanged: int = 0
    rows_stale: int = 0
    rows_duplicate: int = 0
    rows_quarantined: int = 0
    rejection_reason: str | None = None
    schema_drift: dict[str, list[str]] | None = None
    quarantine_by_reason: dict[str, int] = field(default_factory=dict)

    @property
    def rows_loaded(self) -> int:
        return self.rows_inserted + self.rows_updated


@dataclass
class _Candidate:
    record: ValidatedRecord
    key: str
    hash: str
    version: datetime | None


def ingest_file(
    conn: psycopg.Connection,
    landed: LandedFile,
    run_id: str,
    settings: Settings,
    now: datetime,
    checkpoint: Checkpoint | None = None,
) -> FileResult:
    contract = landed.contract
    hit = checkpoint or (lambda _stage: None)
    checksum = sha256_file(landed.path)
    result = FileResult(file_name=landed.name, entity=contract.name, status="loaded")

    existing = conn.execute(
        "SELECT file_id FROM ops.ingested_files WHERE entity = %s AND checksum_sha256 = %s",
        (contract.name, checksum),
    ).fetchone()
    if existing is not None:
        result.status = "skipped"
        result.file_id = existing[0]
        return result

    file_id = _insert_manifest(conn, landed, run_id, checksum)
    result.file_id = file_id

    try:
        parsed = read_file(landed)
    except (UnreadableFileError, UnicodeDecodeError) as exc:
        return _reject(conn, result, run_id, f"unreadable_file: {exc}")

    result.rows_read = len(parsed.rows)
    schema = check_schema(contract, parsed.columns)
    if schema.has_drift:
        result.schema_drift = schema.as_dict()
    _record_dq(
        conn,
        run_id,
        file_id,
        contract.name,
        "schema_contract",
        passed=not schema.has_drift,
        severity="error" if not schema.is_compatible else "warning",
        observed=len(schema.missing_required) + len(schema.unexpected),
        details=schema.as_dict(),
    )
    if not schema.is_compatible:
        return _reject(
            conn, result, run_id, f"missing_required_columns: {', '.join(schema.missing_required)}"
        )
    if schema.unexpected and settings.schema_drift_policy == "reject":
        return _reject(conn, result, run_id, f"unexpected_columns: {', '.join(schema.unexpected)}")

    _copy_raw(conn, file_id, parsed.rows)
    hit("after_raw")

    tz = ZoneInfo(settings.source_timezone)
    records = [_validate(contract, row, n, tz, now) for n, row in enumerate(parsed.rows, start=1)]
    _check_foreign_keys(conn, contract, [r for r in records if r.is_valid])

    rejected = [r for r in records if not r.is_valid]
    to_write, conflicts = _resolve_duplicates(conn, contract, records, result)
    rejected.extend(conflicts)

    reject_rate = len(rejected) / result.rows_read if result.rows_read else 0.0
    _record_dq(
        conn,
        run_id,
        file_id,
        contract.name,
        "reject_rate",
        passed=reject_rate <= settings.max_reject_rate,
        severity="error",
        observed=round(reject_rate, 4),
        threshold=settings.max_reject_rate,
    )

    _copy_quarantine(conn, contract, run_id, file_id, rejected)
    result.rows_quarantined = len(rejected)
    result.quarantine_by_reason = _count_reasons(rejected)
    for reason in Reason:
        count = result.quarantine_by_reason.get(reason.value, 0)
        _record_dq(
            conn,
            run_id,
            file_id,
            contract.name,
            reason.value,
            passed=count == 0,
            severity="warning",
            observed=count,
        )
    _record_dq(
        conn,
        run_id,
        file_id,
        contract.name,
        "exact_duplicates",
        passed=result.rows_duplicate == 0,
        severity="info",
        observed=result.rows_duplicate,
    )

    if reject_rate > settings.max_reject_rate:
        # Too many bad rows usually means a systemic upstream problem
        # (wrong extract, broken export). Load nothing and keep evidence.
        return _reject(
            conn,
            result,
            run_id,
            f"reject_rate_exceeded: {reject_rate:.1%} > {settings.max_reject_rate:.0%}",
            keep_counts=True,
        )

    inserted, updated = _upsert_staging(conn, contract, to_write, file_id, run_id)
    result.rows_inserted, result.rows_updated = inserted, updated
    # Rows filtered out by the version guard inside the upsert are stale.
    result.rows_stale += len(to_write) - inserted - updated
    hit("after_staging")

    _finalise_manifest(conn, result)
    log.info(
        "file_loaded",
        extra={
            "fields": {
                "run_id": run_id,
                "file": landed.name,
                "entity": contract.name,
                "rows_read": result.rows_read,
                "inserted": result.rows_inserted,
                "updated": result.rows_updated,
                "unchanged": result.rows_unchanged,
                "stale": result.rows_stale,
                "duplicate": result.rows_duplicate,
                "quarantined": result.rows_quarantined,
                "schema_drift": result.schema_drift,
            }
        },
    )
    return result


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate(
    contract: EntityContract,
    row: dict[str, Any],
    row_number: int,
    tz: ZoneInfo,
    now: datetime,
) -> ValidatedRecord:
    if MALFORMED_KEY in row:
        record = ValidatedRecord(row_number=row_number, raw=row)
        record.issues.append(Issue(None, Reason.MALFORMED_RECORD, str(row[MALFORMED_KEY])))
        return record
    return validate_record(contract, row, row_number, tz, now)


def _check_foreign_keys(
    conn: psycopg.Connection, contract: EntityContract, records: Sequence[ValidatedRecord]
) -> None:
    for fk in contract.foreign_keys:
        ref = CONTRACTS[fk.ref_entity]
        wanted = sorted({r.values[fk.field] for r in records if r.values.get(fk.field)})
        if not wanted:
            continue
        query = sql.SQL("SELECT {key} FROM staging.{table} WHERE {key} = ANY(%s)").format(
            key=sql.Identifier(ref.key), table=sql.Identifier(ref.staging_table)
        )
        found = {row[0] for row in conn.execute(query, (wanted,))}
        for record in records:
            value = record.values.get(fk.field)
            if value is not None and value not in found:
                record.issues.append(
                    Issue(
                        fk.field,
                        Reason.ORPHAN_REFERENCE,
                        f"{fk.field} '{value}' not found in {ref.name}",
                    )
                )


def _resolve_duplicates(
    conn: psycopg.Connection,
    contract: EntityContract,
    records: Sequence[ValidatedRecord],
    result: FileResult,
) -> tuple[list[_Candidate], list[ValidatedRecord]]:
    """Pick one winning version per key and classify everything else.

    Within the file: identical copies are exact duplicates; same key and
    version with different content is a conflict; older versions are stale.
    Against staging: identical content is unchanged; an older version is
    stale; the same version with different content is a conflict.
    """
    groups: dict[str, list[_Candidate]] = defaultdict(list)
    for record in records:
        if not record.is_valid:
            continue
        key = str(record.values[contract.key])
        version = record.values.get(contract.version_field) if contract.version_field else None
        groups[key].append(_Candidate(record, key, record_hash(record.values), version))

    winners: list[_Candidate] = []
    conflicts: list[ValidatedRecord] = []
    for candidates in groups.values():
        if contract.immutable:
            ordered = sorted(candidates, key=lambda c: c.record.row_number)
        else:
            ordered = sorted(
                candidates,
                key=lambda c: (-c.version.timestamp(), c.record.row_number),  # type: ignore[union-attr]
            )
        winner = ordered[0]
        winners.append(winner)
        for other in ordered[1:]:
            if other.hash == winner.hash:
                result.rows_duplicate += 1
            elif contract.immutable or other.version == winner.version:
                other.record.issues.append(
                    Issue(
                        contract.key,
                        Reason.DUPLICATE_CONFLICT,
                        f"{contract.key} '{other.key}' appears again in row "
                        f"{winner.record.row_number} with different values",
                    )
                )
                conflicts.append(other.record)
            else:
                result.rows_stale += 1

    existing = _fetch_existing(conn, contract, [w.key for w in winners])
    to_write: list[_Candidate] = []
    for winner in winners:
        current = existing.get(winner.key)
        if current is None:
            to_write.append(winner)
            continue
        current_version, current_hash = current
        if current_hash == winner.hash:
            result.rows_unchanged += 1
        elif contract.immutable or winner.version == current_version:
            winner.record.issues.append(
                Issue(
                    contract.key,
                    Reason.DUPLICATE_CONFLICT,
                    f"{contract.key} '{winner.key}' was already loaded with different values "
                    "and the same version",
                )
            )
            conflicts.append(winner.record)
        elif (
            winner.version is not None
            and current_version is not None
            and (winner.version < current_version)
        ):
            result.rows_stale += 1
        else:
            to_write.append(winner)
    return to_write, conflicts


def _fetch_existing(
    conn: psycopg.Connection, contract: EntityContract, keys: list[str]
) -> dict[str, tuple[datetime | None, str]]:
    if not keys:
        return {}
    key_type = "uuid" if contract.spec(contract.key).type is FieldType.UUID else "text"
    version = (
        sql.Identifier(contract.version_field)
        if contract.version_field
        else sql.SQL("NULL::timestamptz")
    )
    query = sql.SQL(
        "SELECT {key}::text, {version}, _record_hash FROM staging.{table} "
        "WHERE {key} = ANY(%s::{key_type}[])"
    ).format(
        key=sql.Identifier(contract.key),
        version=version,
        table=sql.Identifier(contract.staging_table),
        key_type=sql.SQL(key_type),
    )
    return {row[0]: (row[1], row[2]) for row in conn.execute(query, (keys,))}


def _count_reasons(records: Sequence[ValidatedRecord]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for record in records:
        for code in record.reason_codes:
            counts[code] += 1
    return dict(counts)


# ---------------------------------------------------------------------------
# Database writes
# ---------------------------------------------------------------------------


def _insert_manifest(
    conn: psycopg.Connection, landed: LandedFile, run_id: str, checksum: str
) -> int:
    row = conn.execute(
        """
        INSERT INTO ops.ingested_files
            (run_id, entity, file_name, checksum_sha256, file_size_bytes, status)
        VALUES (%s, %s, %s, %s, %s, 'loaded')
        RETURNING file_id
        """,
        (run_id, landed.contract.name, landed.name, checksum, landed.path.stat().st_size),
    ).fetchone()
    if row is None:  # pragma: no cover - INSERT ... RETURNING always yields a row
        raise RuntimeError("manifest insert returned no file_id")
    return int(row[0])


def _finalise_manifest(conn: psycopg.Connection, result: FileResult) -> None:
    conn.execute(
        """
        UPDATE ops.ingested_files SET
            status = %s, rejection_reason = %s, schema_drift = %s,
            rows_read = %s, rows_inserted = %s, rows_updated = %s, rows_unchanged = %s,
            rows_stale = %s, rows_duplicate = %s, rows_quarantined = %s
        WHERE file_id = %s
        """,
        (
            result.status,
            result.rejection_reason,
            Jsonb(result.schema_drift) if result.schema_drift else None,
            result.rows_read,
            result.rows_inserted,
            result.rows_updated,
            result.rows_unchanged,
            result.rows_stale,
            result.rows_duplicate,
            result.rows_quarantined,
            result.file_id,
        ),
    )


def _reject(
    conn: psycopg.Connection,
    result: FileResult,
    run_id: str,
    reason: str,
    *,
    keep_counts: bool = False,
) -> FileResult:
    """Mark the file rejected. Nothing reaches staging; evidence is kept.

    ``keep_counts`` preserves the quarantine and duplicate counts when the
    file was rejected after validation (reject-rate breach).
    """
    result.status = "rejected"
    result.rejection_reason = reason
    result.rows_inserted = result.rows_updated = result.rows_unchanged = result.rows_stale = 0
    if not keep_counts:
        result.rows_quarantined = result.rows_duplicate = 0
    _finalise_manifest(conn, result)
    log.warning(
        "file_rejected",
        extra={
            "fields": {
                "run_id": run_id,
                "file": result.file_name,
                "entity": result.entity,
                "reason": reason,
            }
        },
    )
    return result


def _copy_raw(conn: psycopg.Connection, file_id: int, rows: Sequence[dict[str, Any]]) -> None:
    with (
        conn.cursor() as cur,
        cur.copy("COPY raw.landed_records (file_id, row_number, payload) FROM STDIN") as copy,
    ):
        for row_number, row in enumerate(rows, start=1):
            copy.write_row((file_id, row_number, Jsonb(row)))


def _copy_quarantine(
    conn: psycopg.Connection,
    contract: EntityContract,
    run_id: str,
    file_id: int,
    records: Sequence[ValidatedRecord],
) -> None:
    if not records:
        return
    with (
        conn.cursor() as cur,
        cur.copy(
            "COPY ops.quarantine (run_id, file_id, entity, row_number, record_key, "
            "reason_codes, issues, payload) FROM STDIN"
        ) as copy,
    ):
        for record in records:
            copy.write_row(
                (
                    run_id,
                    file_id,
                    contract.name,
                    record.row_number,
                    record.key(contract),
                    record.reason_codes,
                    Jsonb([i.as_dict() for i in record.issues]),
                    Jsonb(dict(record.raw)),
                )
            )


def _upsert_staging(
    conn: psycopg.Connection,
    contract: EntityContract,
    candidates: Sequence[_Candidate],
    file_id: int,
    run_id: str,
) -> tuple[int, int]:
    """Bulk upsert via COPY into a temp table, then one set-based INSERT.

    Mutable entities only overwrite when the incoming version is newer, so
    replaying an old file or receiving records out of order cannot regress
    data. Immutable events are insert-only.
    """
    if not candidates:
        return 0, 0
    columns = contract.staging_columns + LINEAGE_COLUMNS
    table = sql.Identifier("staging", contract.staging_table)
    col_list = sql.SQL(", ").join(sql.Identifier(c) for c in columns)

    conn.execute(
        sql.SQL(
            "CREATE TEMP TABLE tmp_upsert ON COMMIT DROP AS SELECT {} FROM {} WITH NO DATA"
        ).format(col_list, table)
    )
    with (
        conn.cursor() as cur,
        cur.copy(sql.SQL("COPY tmp_upsert ({}) FROM STDIN").format(col_list)) as copy,
    ):
        for candidate in candidates:
            values = dict(candidate.record.values)
            if contract.derive is not None:
                values.update(contract.derive(values))
            copy.write_row(
                [values.get(c) for c in contract.staging_columns]
                + [candidate.hash, file_id, run_id]
            )

    key = sql.Identifier(contract.key)
    conflict_action: sql.Composable
    if contract.immutable:
        conflict_action = sql.SQL("DO NOTHING")
    else:
        assignments = sql.SQL(", ").join(
            sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c))
            for c in columns
            if c != contract.key
        )
        version = sql.Identifier(contract.version_field)  # type: ignore[arg-type]
        conflict_action = sql.SQL(
            "DO UPDATE SET {assignments}, _change_seq = nextval('staging.change_seq'), "
            "_loaded_at = now() WHERE {table}.{version} < EXCLUDED.{version}"
        ).format(assignments=assignments, table=table, version=version)

    query = sql.SQL(
        "INSERT INTO {table} ({cols}) SELECT {cols} FROM tmp_upsert "
        "ON CONFLICT ({key}) {action} RETURNING (xmax = 0) AS inserted"
    ).format(table=table, cols=col_list, key=key, action=conflict_action)
    flags = [row[0] for row in conn.execute(query)]
    conn.execute("DROP TABLE tmp_upsert")
    inserted = sum(1 for f in flags if f)
    return inserted, len(flags) - inserted


def _record_dq(
    conn: psycopg.Connection,
    run_id: str,
    file_id: int | None,
    entity: str | None,
    check_name: str,
    *,
    passed: bool,
    severity: str,
    observed: float | int | None = None,
    threshold: float | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO ops.dq_results
            (run_id, file_id, check_name, entity, severity, passed,
             observed_value, threshold, details)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            run_id,
            file_id,
            check_name,
            entity,
            severity,
            passed,
            observed,
            threshold,
            Jsonb(details) if details else None,
        ),
    )
