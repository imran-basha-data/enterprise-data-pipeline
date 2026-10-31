from __future__ import annotations

from pathlib import Path

import pytest

from edp.cli import EXIT_CONFIG, EXIT_LOCKED, EXIT_OK, main
from edp.db import pipeline_lock

from conftest import scalar

pytestmark = pytest.mark.integration


def test_generate_migrate_run(db: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    landing = tmp_path / "landing"
    monkeypatch.setenv("EDP_DATABASE_URL", db)
    monkeypatch.setenv("EDP_LANDING_DIR", str(landing))
    assert main(["generate", "--batches", "1", "--days-per-batch", "3"]) == EXIT_OK
    assert main(["migrate"]) == EXIT_OK
    assert main(["run"]) == EXIT_OK
    assert scalar(db, "SELECT status FROM ops.pipeline_runs") == "succeeded"
    assert scalar(db, "SELECT count(*) FROM staging.production_orders") > 0  # type: ignore[operator]


def test_missing_configuration_exits_with_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EDP_DATABASE_URL", raising=False)
    assert main(["run"]) == EXIT_CONFIG


def test_locked_run_exits_with_lock_code(
    db: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EDP_DATABASE_URL", db)
    monkeypatch.setenv("EDP_LANDING_DIR", str(tmp_path))
    with pipeline_lock(db):
        assert main(["run"]) == EXIT_LOCKED
