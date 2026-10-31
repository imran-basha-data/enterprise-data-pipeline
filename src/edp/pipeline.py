"""Pipeline orchestration: one run = discover -> ingest -> model -> check."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import psycopg

from edp.config import Settings
from edp.db import connect, pipeline_lock
from edp.ingest import Checkpoint, FileResult, ingest_file
from edp.logging_setup import get_logger
from edp.quality import CheckResult, QualityGateError, record_checks, run_dataset_checks
from edp.readers import LandedFile, discover_files
from edp.retry import retry_call
from edp.transform import ModelResult, refresh_models

log = get_logger(__name__)


@dataclass
class RunSummary:
    run_id: str
    status: str = "running"
    files_discovered: int = 0
    files: list[FileResult] = field(default_factory=list)
    ignored_files: list[str] = field(default_factory=list)
    models: list[ModelResult] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    error: str | None = None

    def count(self, status: str) -> int:
        return sum(1 for f in self.files if f.status == status)

    @property
    def records_loaded(self) -> int:
        return sum(f.rows_loaded for f in self.files)

    @property
    def records_quarantined(self) -> int:
        return sum(f.rows_quarantined for f in self.files)

    @property
    def quarantine_rate(self) -> float:
        read = sum(f.rows_read for f in self.files if f.status != "skipped")
        return self.records_quarantined / read if read else 0.0


def run_pipeline(
    settings: Settings,
    *,
    now: datetime | None = None,
    checkpoint: Checkpoint | None = None,
    sleep: Callable[[float], None] = time.sleep,
    full_refresh: bool = False,
) -> RunSummary:
    """Execute one pipeline run and return its summary.

    Raises ``PipelineLockedError`` if another run holds the lock. Any other
    failure is recorded against the run and reflected in ``status``; files
    committed before the failure stay committed, and the next run resumes
    from the first file that did not complete. Marts are only published when
    every blocking data-quality check passes.
    """
    now = now or datetime.now(UTC)
    with pipeline_lock(settings.database_url) as lock_conn:
        _mark_abandoned_runs(lock_conn)
        summary = RunSummary(run_id=str(uuid.uuid4()))
        lock_conn.execute("INSERT INTO ops.pipeline_runs (run_id) VALUES (%s)", (summary.run_id,))
        log.info("run_started", extra={"fields": {"run_id": summary.run_id}})
        try:
            discovery = discover_files(settings.landing_dir)
            summary.files_discovered = len(discovery.files)
            summary.ignored_files = [p.name for p in discovery.ignored]
            for path in discovery.ignored:
                log.warning("file_ignored", extra={"fields": {"file": path.name}})
            for landed in discovery.files:
                summary.files.append(
                    retry_call(
                        lambda landed=landed: _load_file(  # type: ignore[misc]
                            settings, landed, summary.run_id, now, checkpoint
                        ),
                        attempts=settings.max_retries,
                        base_delay_s=settings.retry_base_delay_s,
                        sleep=sleep,
                        description=f"ingest {landed.name}",
                    )
                )
            summary.models, summary.checks = retry_call(
                lambda: _publish_models(settings, summary, full_refresh),
                attempts=settings.max_retries,
                base_delay_s=settings.retry_base_delay_s,
                sleep=sleep,
                description="refresh models",
            )
            summary.status = "succeeded"
        except QualityGateError as exc:
            summary.checks = exc.results
            summary.status = "failed"
            summary.error = str(exc)
            log.error(
                "quality_gate_failed",
                extra={
                    "fields": {
                        "run_id": summary.run_id,
                        "failed_checks": [c.name for c in exc.failures],
                    }
                },
            )
        except Exception as exc:
            summary.status = "failed"
            summary.error = f"{type(exc).__name__}: {exc}"
            log.exception("run_failed", extra={"fields": {"run_id": summary.run_id}})
        finally:
            if summary.checks:
                record_checks(lock_conn, summary.run_id, summary.checks)
            _finish_run(lock_conn, summary)
    log.info(
        "run_finished",
        extra={
            "fields": {
                "run_id": summary.run_id,
                "status": summary.status,
                "files_loaded": summary.count("loaded"),
                "files_skipped": summary.count("skipped"),
                "files_rejected": summary.count("rejected"),
                "records_loaded": summary.records_loaded,
                "records_quarantined": summary.records_quarantined,
            }
        },
    )
    return summary


def _load_file(
    settings: Settings,
    landed: LandedFile,
    run_id: str,
    now: datetime,
    checkpoint: Checkpoint | None,
) -> FileResult:
    # A fresh connection per attempt means a retry never reuses a broken one.
    with connect(settings.database_url) as conn, conn.transaction():
        return ingest_file(conn, landed, run_id, settings, now, checkpoint)


def _publish_models(
    settings: Settings, summary: RunSummary, full_refresh: bool
) -> tuple[list[ModelResult], list[CheckResult]]:
    """Refresh marts and run the quality gate in one transaction."""
    with connect(settings.database_url) as conn, conn.transaction():
        models = refresh_models(conn, settings.source_timezone, full_refresh=full_refresh)
        checks = run_dataset_checks(conn, summary.quarantine_rate)
        if any(c.blocking_failure for c in checks):
            raise QualityGateError(checks)  # rolls back the model refresh
    for check in checks:
        if not check.passed:
            log.warning(
                "dq_check_warning",
                extra={"fields": {"check": check.name, "observed": check.observed}},
            )
    return models, checks


def _mark_abandoned_runs(conn: psycopg.Connection) -> None:
    """Any run still 'running' while we hold the lock must have crashed."""
    cur = conn.execute(
        "UPDATE ops.pipeline_runs SET status = 'abandoned', finished_at = now(), "
        "error_message = 'process exited before the run finished' WHERE status = 'running'"
    )
    if cur.rowcount:
        log.warning("abandoned_runs_marked", extra={"fields": {"count": cur.rowcount}})


def _finish_run(conn: psycopg.Connection, summary: RunSummary) -> None:
    conn.execute(
        """
        UPDATE ops.pipeline_runs SET
            finished_at = now(), status = %s, files_discovered = %s, files_loaded = %s,
            files_skipped = %s, files_rejected = %s, records_loaded = %s,
            records_quarantined = %s, error_message = %s
        WHERE run_id = %s
        """,
        (
            summary.status,
            summary.files_discovered,
            summary.count("loaded"),
            summary.count("skipped"),
            summary.count("rejected"),
            summary.records_loaded,
            summary.records_quarantined,
            summary.error,
            summary.run_id,
        ),
    )
