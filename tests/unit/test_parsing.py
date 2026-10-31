from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from edp.parsing import (
    ParseError,
    check_timestamp_bounds,
    clean_string,
    normalise_code,
    parse_bool,
    parse_date,
    parse_decimal,
    parse_int,
    parse_timestamp,
)

LONDON = ZoneInfo("Europe/London")
NOW = datetime(2026, 6, 1, tzinfo=UTC)


@pytest.mark.parametrize("raw", [None, "", "   ", "NULL", "n/a", "N/A", "None", "-", "nan"])
def test_clean_string_maps_null_tokens_to_none(raw: object) -> None:
    assert clean_string(raw) is None


def test_clean_string_trims_whitespace() -> None:
    assert clean_string("  Hydraulic Pump ") == "Hydraulic Pump"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("12", 12), (" 7 ", 7), ("12.0", 12), ("1,200", 1200), (-3, -3), ("", None)],
)
def test_parse_int_accepts_whole_numbers(raw: object, expected: int | None) -> None:
    assert parse_int(raw) == expected


@pytest.mark.parametrize("raw", ["twelve", "12.5", "1e400", "2147483648", 2**40, True])
def test_parse_int_rejects_non_integers(raw: object) -> None:
    with pytest.raises(ParseError):
        parse_int(raw)


def test_parse_decimal_preserves_precision() -> None:
    assert parse_decimal("100.10") == Decimal("100.10")
    assert parse_decimal(0.1) == Decimal("0.1")
    with pytest.raises(ParseError):
        parse_decimal("abc")
    with pytest.raises(ParseError):
        parse_decimal("Infinity")


@pytest.mark.parametrize(
    ("raw", "expected"), [("true", True), ("Y", True), ("0", False), ("no", False)]
)
def test_parse_bool(raw: str, expected: bool) -> None:
    assert parse_bool(raw) is expected


def test_parse_bool_rejects_unknown_token() -> None:
    with pytest.raises(ParseError):
        parse_bool("maybe")


def test_parse_date_supports_iso_and_uk_formats() -> None:
    assert parse_date("2026-03-04") == date(2026, 3, 4)
    assert parse_date("04/03/2026") == date(2026, 3, 4)  # DD/MM/YYYY
    assert parse_date("2026-03-04T10:00:00Z") == date(2026, 3, 4)


@pytest.mark.parametrize("raw", ["2026-02-30", "31/13/2026", "2026-01-05garbage", "yesterday"])
def test_parse_date_rejects_impossible_dates(raw: str) -> None:
    with pytest.raises(ParseError):
        parse_date(raw)


def test_parse_timestamp_converts_offsets_to_utc() -> None:
    assert parse_timestamp("2026-01-05T10:00:00Z", LONDON) == datetime(2026, 1, 5, 10, tzinfo=UTC)
    assert parse_timestamp("2026-01-05T10:00:00+05:30", LONDON) == datetime(
        2026, 1, 5, 4, 30, tzinfo=UTC
    )


def test_naive_timestamps_use_source_timezone_including_daylight_saving() -> None:
    # Winter: London is UTC+0. Summer (BST): London is UTC+1.
    assert parse_timestamp("2026-01-05 08:00:00", LONDON) == datetime(2026, 1, 5, 8, tzinfo=UTC)
    assert parse_timestamp("2026-07-05 08:00:00", LONDON) == datetime(2026, 7, 5, 7, tzinfo=UTC)


def test_parse_timestamp_accepts_epoch_seconds_and_milliseconds() -> None:
    expected = datetime(2026, 1, 5, 10, tzinfo=UTC)
    seconds = int(expected.timestamp())
    assert parse_timestamp(seconds, LONDON) == expected
    assert parse_timestamp(seconds * 1000, LONDON) == expected
    assert parse_timestamp(str(seconds * 1000), LONDON) == expected


@pytest.mark.parametrize("raw", ["not-a-timestamp", "2026-13-01T00:00:00", True])
def test_parse_timestamp_rejects_invalid_values(raw: object) -> None:
    with pytest.raises(ParseError):
        parse_timestamp(raw, LONDON)


def test_timestamp_bounds() -> None:
    assert check_timestamp_bounds(datetime(1999, 12, 31, tzinfo=UTC), NOW) is not None
    assert check_timestamp_bounds(datetime(2026, 6, 10, tzinfo=UTC), NOW) is not None
    assert check_timestamp_bounds(datetime(2026, 6, 10, tzinfo=UTC), NOW, allow_future=True) is None
    assert check_timestamp_bounds(datetime(2026, 5, 31, tzinfo=UTC), NOW) is None


def test_normalise_code() -> None:
    assert normalise_code(" In Progress ") == "in_progress"
    assert normalise_code("in-transit") == "in_transit"
