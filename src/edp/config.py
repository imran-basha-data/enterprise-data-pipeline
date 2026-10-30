"""Runtime configuration loaded from environment variables.

All settings have an ``EDP_`` prefix. Only the database URL is mandatory; every
other value has a safe default suitable for local development.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(ValueError):
    """Raised when configuration is missing or invalid."""


_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
_LOG_FORMATS = {"json", "text"}
_DRIFT_POLICIES = {"warn", "reject"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    landing_dir: Path = Path("data/landing")
    report_dir: Path = Path("reports")
    source_timezone: str = "Europe/London"
    log_level: str = "INFO"
    log_format: str = "json"
    schema_drift_policy: str = "warn"
    max_reject_rate: float = 0.25
    max_retries: int = 3
    retry_base_delay_s: float = 0.5

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        database_url = env.get("EDP_DATABASE_URL", "").strip()
        if not database_url:
            raise ConfigError(
                "EDP_DATABASE_URL is not set. Copy .env.example to .env and set it, "
                "e.g. postgresql://edp:<password>@localhost:5432/edp"
            )
        if urlsplit(database_url).scheme not in {"postgresql", "postgres"}:
            raise ConfigError("EDP_DATABASE_URL must start with postgresql://")

        settings = cls(
            database_url=database_url,
            landing_dir=Path(env.get("EDP_LANDING_DIR", "data/landing")),
            report_dir=Path(env.get("EDP_REPORT_DIR", "reports")),
            source_timezone=env.get("EDP_SOURCE_TIMEZONE", "Europe/London"),
            log_level=env.get("EDP_LOG_LEVEL", "INFO").upper(),
            log_format=env.get("EDP_LOG_FORMAT", "json").lower(),
            schema_drift_policy=env.get("EDP_SCHEMA_DRIFT_POLICY", "warn").lower(),
            max_reject_rate=_parse_float(env, "EDP_MAX_REJECT_RATE", 0.25),
            max_retries=_parse_int(env, "EDP_MAX_RETRIES", 3),
            retry_base_delay_s=_parse_float(env, "EDP_RETRY_BASE_DELAY_S", 0.5),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.log_level not in _LOG_LEVELS:
            raise ConfigError(f"EDP_LOG_LEVEL must be one of {sorted(_LOG_LEVELS)}")
        if self.log_format not in _LOG_FORMATS:
            raise ConfigError(f"EDP_LOG_FORMAT must be one of {sorted(_LOG_FORMATS)}")
        if self.schema_drift_policy not in _DRIFT_POLICIES:
            raise ConfigError(f"EDP_SCHEMA_DRIFT_POLICY must be one of {sorted(_DRIFT_POLICIES)}")
        if not 0.0 < self.max_reject_rate <= 1.0:
            raise ConfigError("EDP_MAX_REJECT_RATE must be in the range (0, 1]")
        if not 1 <= self.max_retries <= 10:
            raise ConfigError("EDP_MAX_RETRIES must be between 1 and 10")
        if self.retry_base_delay_s < 0:
            raise ConfigError("EDP_RETRY_BASE_DELAY_S must not be negative")
        try:
            ZoneInfo(self.source_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigError(
                f"EDP_SOURCE_TIMEZONE '{self.source_timezone}' is not a valid IANA zone"
            ) from exc

    @property
    def redacted_database_url(self) -> str:
        return redact_dsn(self.database_url)


def redact_dsn(dsn: str) -> str:
    """Return the DSN with any password replaced, safe for logs."""
    parts = urlsplit(dsn)
    if parts.password is None:
        return dsn
    netloc = parts.netloc.replace(f":{parts.password}@", ":***@", 1)
    return urlunsplit(parts._replace(netloc=netloc))


def _parse_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got '{raw}'") from exc


def _parse_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got '{raw}'") from exc
