"""Retry with exponential backoff for transient failures.

Only operations that are safe to repeat should be retried. In this pipeline the
unit of retry is a whole file load, which runs in a single database transaction
and is idempotent, so a retry can never produce partial or duplicate writes.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

import psycopg

from edp.logging_setup import get_logger

T = TypeVar("T")
log = get_logger(__name__)

# Connection drops, server restarts, serialization failures and deadlocks are all
# subclasses of OperationalError in psycopg 3.
TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (psycopg.OperationalError,)


def retry_call(
    fn: Callable[[], T],
    *,
    attempts: int,
    base_delay_s: float,
    max_delay_s: float = 10.0,
    retry_on: tuple[type[BaseException], ...] = TRANSIENT_ERRORS,
    sleep: Callable[[float], None] = time.sleep,
    description: str = "operation",
) -> T:
    """Call ``fn`` until it succeeds or ``attempts`` is exhausted.

    Non-transient exceptions propagate immediately. The final transient
    exception is re-raised once all attempts are used.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except retry_on as exc:
            if attempt == attempts:
                log.error(
                    "retry_exhausted",
                    extra={"fields": {"operation": description, "attempts": attempt}},
                )
                raise
            delay = min(max_delay_s, base_delay_s * (2 ** (attempt - 1)))
            log.warning(
                "transient_failure_retrying",
                extra={
                    "fields": {
                        "operation": description,
                        "attempt": attempt,
                        "next_delay_s": delay,
                        "error": type(exc).__name__,
                    }
                },
            )
            sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover
