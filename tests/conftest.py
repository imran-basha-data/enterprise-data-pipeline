from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from edp.config import Settings
from edp.db import apply_migrations

sys.path.insert(0, str(Path(__file__).parent))

# A fixed "now" keeps date-bound validation deterministic.
FIXED_NOW = datetime(2026, 6, 1, tzinfo=UTC)


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("EDP_TEST_DATABASE_URL")
    if not url:
        if os.environ.get("EDP_REQUIRE_DB") == "1":
            pytest.fail("EDP_REQUIRE_DB=1 but EDP_TEST_DATABASE_URL is not set")
        pytest.skip("EDP_TEST_DATABASE_URL not set; skipping database tests")
    return url


def reset_database(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_unlock_all()")
        for schema in ("mart", "staging", "raw", "ops"):
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    apply_migrations(url)


@pytest.fixture
def db(database_url: str) -> Iterator[str]:
    """A freshly migrated, empty database for each test."""
    reset_database(database_url)
    yield database_url


@pytest.fixture
def landing(tmp_path: Path) -> Path:
    path = tmp_path / "landing"
    path.mkdir()
    return path


@pytest.fixture
def settings(db: str, landing: Path, tmp_path: Path) -> Settings:
    return Settings(
        database_url=db,
        landing_dir=landing,
        report_dir=tmp_path / "reports",
        retry_base_delay_s=0.0,
    )


def query(url: str, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    with psycopg.connect(url) as conn:
        return conn.execute(sql, params).fetchall()  # type: ignore[arg-type]


def scalar(url: str, sql: str, params: tuple[object, ...] = ()) -> object:
    rows = query(url, sql, params)
    return rows[0][0] if rows else None
