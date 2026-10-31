"""Database connectivity, schema migrations and run-level locking."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources

import psycopg

from edp.logging_setup import get_logger

log = get_logger(__name__)

# Arbitrary but fixed keys for PostgreSQL advisory locks.
PIPELINE_LOCK_KEY = 0x45445031  # "EDP1"
MIGRATION_LOCK_KEY = 0x45445032  # "EDP2"


class MigrationError(RuntimeError):
    """An applied migration no longer matches its file on disk."""


class PipelineLockedError(RuntimeError):
    """Another pipeline run currently holds the run lock."""


def connect(dsn: str, *, autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=autocommit, connect_timeout=10)


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def load_migrations() -> list[Migration]:
    folder = resources.files("edp").joinpath("sql", "migrations")
    migrations = []
    for entry in sorted(folder.iterdir(), key=lambda e: e.name):
        if not entry.name.endswith(".sql"):
            continue
        version, _, rest = entry.name.partition("_")
        migrations.append(
            Migration(version=version, name=rest.removesuffix(".sql"), sql=entry.read_text("utf-8"))
        )
    return migrations


def apply_migrations(dsn: str) -> list[str]:
    """Apply pending migrations, each in its own transaction.

    Returns the versions applied. Raises ``MigrationError`` if a migration that
    was already applied has since been edited, because silently diverging
    schemas are a common source of production incidents.
    """
    applied_now: list[str] = []
    with connect(dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        try:
            conn.execute(
                """
                CREATE SCHEMA IF NOT EXISTS ops;
                CREATE TABLE IF NOT EXISTS ops.schema_migrations (
                    version     text PRIMARY KEY,
                    name        text NOT NULL,
                    checksum    char(64) NOT NULL,
                    applied_at  timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            rows = conn.execute("SELECT version, checksum FROM ops.schema_migrations").fetchall()
            existing: dict[str, str] = dict(rows)
            for migration in load_migrations():
                if migration.version in existing:
                    if existing[migration.version] != migration.checksum:
                        raise MigrationError(
                            f"migration {migration.version}_{migration.name} was modified "
                            "after being applied; add a new migration instead"
                        )
                    continue
                with conn.transaction():
                    conn.execute(migration.sql)
                    conn.execute(
                        "INSERT INTO ops.schema_migrations (version, name, checksum) "
                        "VALUES (%s, %s, %s)",
                        (migration.version, migration.name, migration.checksum),
                    )
                applied_now.append(migration.version)
                log.info(
                    "migration_applied",
                    extra={"fields": {"version": migration.version, "name": migration.name}},
                )
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
    return applied_now


@contextmanager
def pipeline_lock(dsn: str) -> Iterator[psycopg.Connection]:
    """Hold a session-level advisory lock for the duration of a run.

    The lock is released automatically if the process dies, because it is tied
    to the database session, so a crashed run never blocks future runs.
    """
    conn = connect(dsn, autocommit=True)
    try:
        row = conn.execute("SELECT pg_try_advisory_lock(%s)", (PIPELINE_LOCK_KEY,)).fetchone()
        if not row or not row[0]:
            raise PipelineLockedError("another pipeline run is in progress")
        try:
            yield conn
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (PIPELINE_LOCK_KEY,))
    finally:
        conn.close()
