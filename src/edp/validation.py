"""Record-level validation and cleaning driven by entity contracts."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo
from decimal import Decimal
from typing import Any

from edp.contracts import EntityContract, FieldSpec, FieldType, Issue, Reason
from edp.parsing import (
    ParseError,
    check_timestamp_bounds,
    clean_string,
    parse_bool,
    parse_date,
    parse_decimal,
    parse_int,
    parse_timestamp,
)


@dataclass
class ValidatedRecord:
    row_number: int
    raw: Mapping[str, Any]
    values: dict[str, Any] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.issues

    @property
    def reason_codes(self) -> list[str]:
        seen: dict[str, None] = {}
        for issue in self.issues:
            seen.setdefault(issue.reason.value, None)
        return list(seen)

    def key(self, contract: EntityContract) -> str | None:
        value = self.values.get(contract.key)
        if value is None:
            raw = clean_string(self.raw.get(contract.key))
            return raw
        return str(value)


def validate_record(
    contract: EntityContract,
    raw: Mapping[str, Any],
    row_number: int,
    source_tz: tzinfo,
    now: datetime,
) -> ValidatedRecord:
    """Clean and type-check one source record against its contract.

    Field-level checks run first. Cross-field rules only run when every field
    passed, so each defect is reported once with its root cause instead of a
    cascade of secondary failures.
    """
    record = ValidatedRecord(row_number=row_number, raw=raw)
    for spec in contract.fields:
        try:
            value = _coerce(spec, raw.get(spec.name), source_tz, now)
        except _FieldError as exc:
            record.issues.append(Issue(spec.name, exc.reason, exc.detail))
            continue
        if value is None:
            if spec.required:
                record.issues.append(
                    Issue(spec.name, Reason.MISSING_REQUIRED, f"{spec.name} is required")
                )
                continue
            value = spec.default
        record.values[spec.name] = value

    if record.is_valid:
        for rule in contract.rules:
            issue = rule(record.values)
            if issue is not None:
                record.issues.append(issue)
    return record


class _FieldError(Exception):
    def __init__(self, reason: Reason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def _coerce(spec: FieldSpec, raw_value: Any, source_tz: tzinfo, now: datetime) -> Any:
    try:
        if spec.type is FieldType.STRING:
            return _coerce_string(spec, raw_value)
        if spec.type is FieldType.INTEGER:
            return _check_range(spec, parse_int(raw_value))
        if spec.type is FieldType.DECIMAL:
            return _check_range(spec, parse_decimal(raw_value))
        if spec.type is FieldType.BOOLEAN:
            return parse_bool(raw_value)
        if spec.type is FieldType.DATE:
            parsed_date = parse_date(raw_value)
            if parsed_date is not None:
                as_ts = datetime(
                    parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=now.tzinfo
                )
                _check_date_bounds(spec, as_ts, now)
            return parsed_date
        if spec.type is FieldType.TIMESTAMP:
            ts = parse_timestamp(raw_value, source_tz)
            if ts is not None:
                _check_date_bounds(spec, ts, now)
            return ts
        if spec.type is FieldType.UUID:
            text = clean_string(raw_value)
            if text is None:
                return None
            try:
                return str(uuid.UUID(text))
            except ValueError as exc:
                raise _FieldError(Reason.INVALID_VALUE, f"'{text}' is not a UUID") from exc
    except ParseError as exc:
        reason = (
            Reason.INVALID_DATE
            if spec.type in (FieldType.DATE, FieldType.TIMESTAMP)
            else spec.numeric_reason
            if spec.type in (FieldType.INTEGER, FieldType.DECIMAL)
            else Reason.INVALID_VALUE
        )
        raise _FieldError(reason, f"{spec.name}: {exc}") from exc
    raise AssertionError(f"unhandled field type {spec.type}")  # pragma: no cover


def _coerce_string(spec: FieldSpec, raw_value: Any) -> str | None:
    text = clean_string(raw_value)
    if text is None:
        return None
    if spec.normalise is not None:
        text = spec.normalise(text)
    text = spec.aliases.get(text, text)
    if spec.max_length is not None and len(text) > spec.max_length:
        raise _FieldError(Reason.INVALID_VALUE, f"{spec.name} exceeds {spec.max_length} characters")
    if spec.pattern is not None and not re.fullmatch(spec.pattern, text):
        raise _FieldError(Reason.INVALID_VALUE, f"{spec.name} '{text}' has an invalid format")
    if spec.allowed_values is not None and text not in spec.allowed_values:
        raise _FieldError(
            Reason.INVALID_VALUE,
            f"{spec.name} '{text}' is not one of {sorted(spec.allowed_values)}",
        )
    return text


def _check_range(spec: FieldSpec, value: int | Decimal | None) -> int | Decimal | None:
    if value is None:
        return None
    if spec.min_value is not None and value < spec.min_value:
        raise _FieldError(spec.numeric_reason, f"{spec.name} {value} is below {spec.min_value}")
    if spec.max_value is not None and value > spec.max_value:
        raise _FieldError(spec.numeric_reason, f"{spec.name} {value} is above {spec.max_value}")
    return value


def _check_date_bounds(spec: FieldSpec, ts: datetime, now: datetime) -> None:
    problem = check_timestamp_bounds(ts, now, allow_future=spec.allow_future)
    if problem is not None:
        raise _FieldError(Reason.INVALID_DATE, f"{spec.name}: {problem}")


def record_hash(values: Mapping[str, Any]) -> str:
    """Stable content hash of cleaned values, used for duplicate detection.

    Hashing the cleaned values (not the raw text) means cosmetic differences
    such as whitespace, casing or timestamp offsets do not create false
    conflicts.
    """
    canonical = json.dumps(
        {k: _canonical(v) for k, v in sorted(values.items())},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return value
