import json
import logging

import psycopg
import pytest

from edp.config import ConfigError, Settings, redact_dsn
from edp.logging_setup import JsonFormatter, TextFormatter
from edp.retry import retry_call

DSN = "postgresql://edp:s3cret@db:5432/edp"


def test_settings_require_database_url() -> None:
    with pytest.raises(ConfigError, match="EDP_DATABASE_URL"):
        Settings.from_env({})


def test_settings_defaults_and_overrides() -> None:
    settings = Settings.from_env(
        {"EDP_DATABASE_URL": DSN, "EDP_MAX_RETRIES": "5", "EDP_LOG_LEVEL": "debug"}
    )
    assert settings.max_retries == 5
    assert settings.log_level == "DEBUG"
    assert settings.source_timezone == "Europe/London"


@pytest.mark.parametrize(
    "env",
    [
        {"EDP_DATABASE_URL": "mysql://x"},
        {"EDP_DATABASE_URL": DSN, "EDP_MAX_RETRIES": "abc"},
        {"EDP_DATABASE_URL": DSN, "EDP_MAX_RETRIES": "0"},
        {"EDP_DATABASE_URL": DSN, "EDP_MAX_REJECT_RATE": "1.5"},
        {"EDP_DATABASE_URL": DSN, "EDP_SOURCE_TIMEZONE": "Mars/Olympus"},
        {"EDP_DATABASE_URL": DSN, "EDP_SCHEMA_DRIFT_POLICY": "ignore"},
        {"EDP_DATABASE_URL": DSN, "EDP_LOG_FORMAT": "xml"},
    ],
)
def test_invalid_settings_are_rejected(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(env)


def test_password_is_redacted() -> None:
    assert redact_dsn(DSN) == "postgresql://edp:***@db:5432/edp"
    assert "s3cret" not in Settings(database_url=DSN).redacted_database_url
    assert redact_dsn("postgresql://db/edp") == "postgresql://db/edp"


def test_retry_recovers_from_transient_errors() -> None:
    calls: list[int] = []
    delays: list[float] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise psycopg.OperationalError("connection reset")
        return "ok"

    assert retry_call(flaky, attempts=3, base_delay_s=0.5, sleep=delays.append) == "ok"
    assert len(calls) == 3
    assert delays == [0.5, 1.0]


def test_retry_gives_up_after_max_attempts() -> None:
    delays: list[float] = []

    def always_down() -> None:
        raise psycopg.OperationalError("down")

    with pytest.raises(psycopg.OperationalError):
        retry_call(always_down, attempts=4, base_delay_s=4, max_delay_s=10, sleep=delays.append)
    assert delays == [4, 8, 10]


def test_retry_does_not_retry_permanent_errors() -> None:
    calls: list[int] = []

    def broken() -> None:
        calls.append(1)
        raise ValueError("bad data")

    with pytest.raises(ValueError):
        retry_call(broken, attempts=5, base_delay_s=0, sleep=lambda _s: None)
    assert len(calls) == 1


def _record(**fields: object) -> logging.LogRecord:
    record = logging.LogRecord("edp.test", logging.INFO, __file__, 1, "file_loaded", (), None)
    record.fields = fields
    return record


def test_json_formatter_emits_structured_fields() -> None:
    payload = json.loads(JsonFormatter().format(_record(file="a.csv", rows=3)))
    assert payload["event"] == "file_loaded"
    assert payload["level"] == "INFO"
    assert payload["file"] == "a.csv"
    assert payload["rows"] == 3


def test_text_formatter_is_human_readable() -> None:
    assert TextFormatter().format(_record(rows=3)) == "INFO    edp.test: file_loaded rows=3"
