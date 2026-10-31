"""Type coercion and value cleaning for source records.

Every parser accepts the raw value exactly as read from CSV or JSON and either
returns a typed Python value, returns ``None`` for an empty value, or raises
``ParseError`` with a human-readable reason.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal, InvalidOperation
from typing import Any

NULL_TOKENS = frozenset({"", "null", "none", "n/a", "na", "nan", "-"})
MIN_VALID_TIMESTAMP = datetime(2000, 1, 1, tzinfo=UTC)
FUTURE_TOLERANCE = timedelta(days=1)
_TRUE = frozenset({"true", "t", "yes", "y", "1"})
_FALSE = frozenset({"false", "f", "no", "n", "0"})
_EPOCH_MS_THRESHOLD = 10**11  # values above this are treated as milliseconds
INT32_MAX = 2_147_483_647  # PostgreSQL ``integer`` range
_UK_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


class ParseError(ValueError):
    """The value could not be converted to the expected type."""


def clean_string(value: Any) -> str | None:
    """Trim whitespace and map null-like tokens to ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in NULL_TOKENS:
        return None
    return text


def is_missing(value: Any) -> bool:
    return clean_string(value) is None


def parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        raise ParseError(f"expected an integer, got boolean {value!r}")
    if isinstance(value, int):
        return _int32(value)
    text = clean_string(value)
    if text is None:
        return None
    try:
        number = Decimal(text.replace(",", ""))
    except InvalidOperation as exc:
        raise ParseError(f"'{text}' is not a number") from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise ParseError(f"'{text}' is not a whole number")
    return _int32(int(number))


def _int32(value: int) -> int:
    if abs(value) > INT32_MAX:
        raise ParseError(f"{value} is outside the supported integer range")
    return value


def parse_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        raise ParseError(f"expected a number, got boolean {value!r}")
    if isinstance(value, int | float):
        return Decimal(str(value))
    text = clean_string(value)
    if text is None:
        return None
    try:
        number = Decimal(text.replace(",", ""))
    except InvalidOperation as exc:
        raise ParseError(f"'{text}' is not a number") from exc
    if not number.is_finite():
        raise ParseError(f"'{text}' is not a finite number")
    return number


def parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = clean_string(value)
    if text is None:
        return None
    lowered = text.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ParseError(f"'{text}' is not a boolean")


def parse_date(value: Any) -> date | None:
    """Parse ISO ``YYYY-MM-DD`` or UK ``DD/MM/YYYY`` dates."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = clean_string(value)
    if text is None:
        return None
    match = _UK_DATE.match(text)
    try:
        if match:
            day, month, year = (int(g) for g in match.groups())
            return date(year, month, day)
        if len(text) > 10:
            return datetime.fromisoformat(text).date()
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ParseError(f"'{text}' is not a valid date") from exc


def parse_timestamp(value: Any, source_tz: tzinfo) -> datetime | None:
    """Parse a timestamp and standardise it to timezone-aware UTC.

    Accepted inputs:
    * ISO 8601 strings with an offset or ``Z`` suffix.
    * Naive ISO strings (``2026-01-05 08:00:00``), interpreted in ``source_tz``.
    * Unix epoch numbers in seconds or milliseconds.
    """
    if isinstance(value, bool):
        raise ParseError(f"expected a timestamp, got boolean {value!r}")
    if isinstance(value, int | float):
        seconds = value / 1000 if abs(value) >= _EPOCH_MS_THRESHOLD else value
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise ParseError(f"epoch value {value!r} is out of range") from exc
    if isinstance(value, datetime):
        parsed = value
    else:
        text = clean_string(value)
        if text is None:
            return None
        if re.fullmatch(r"-?\d+(\.\d+)?", text):
            return parse_timestamp(float(text) if "." in text else int(text), source_tz)
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ParseError(f"'{text}' is not a valid timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=source_tz)
    return parsed.astimezone(UTC)


def check_timestamp_bounds(
    ts: datetime, now: datetime, *, allow_future: bool = False
) -> str | None:
    """Return an error message when a timestamp is implausible."""
    if ts < MIN_VALID_TIMESTAMP:
        return f"{ts.isoformat()} is before {MIN_VALID_TIMESTAMP.date().isoformat()}"
    if not allow_future and ts > now + FUTURE_TOLERANCE:
        return f"{ts.isoformat()} is in the future"
    return None


def normalise_code(value: str) -> str:
    """Lower-case snake form used for status and type codes."""
    return re.sub(r"[\s\-]+", "_", value.strip().lower())
