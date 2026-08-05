"""Posting-date parsing and precision model.

The Career Engine contract states that posting dates are never fabricated.
This module encodes the allowed precision ladder:

- ``exact`` - an ISO timestamp with a time component (or a millisecond epoch).
- ``day`` - a date-only value (``YYYY-MM-DD``).
- ``month`` - a year-month value (``YYYY-MM``).
- ``unknown`` - no date is available; the value is ``None``.

The canonical value is always a plain date string (``YYYY-MM-DD``) for
``exact``/``day``, ``YYYY-MM`` for ``month``, and ``None`` for ``unknown``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

PRECISION_EXACT = "exact"
PRECISION_DAY = "day"
PRECISION_MONTH = "month"
PRECISION_UNKNOWN = "unknown"
PRECISIONS = (PRECISION_EXACT, PRECISION_DAY, PRECISION_MONTH, PRECISION_UNKNOWN)

_ISO_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?"
)
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_ISO_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_DAY_RANGE = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
              21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31}
_MONTH_RANGE = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}


def _valid_calendar_day(value: str) -> bool:
    match = _ISO_DATE_RE.match(value)
    if not match:
        return False
    month, day = int(match.group(2)), int(match.group(3))
    return month in _MONTH_RANGE and day in _DAY_RANGE


def _valid_calendar_month(value: str) -> bool:
    match = _ISO_MONTH_RE.match(value)
    return bool(match) and int(match.group(2)) in _MONTH_RANGE


@dataclass(frozen=True, slots=True)
class PostingDate:
    """A posting date with its precision and extraction source.

    ``value`` is ``YYYY-MM-DD`` (exact/day), ``YYYY-MM`` (month) or ``None``
    (unknown). The ``source`` field records which upstream field produced it,
    so precision and provenance are always traceable.
    """

    value: str | None
    precision: str
    source: str

    def __post_init__(self) -> None:
        if self.precision not in PRECISIONS:
            raise ValueError(f"Unsupported precision: {self.precision!r}")
        if self.value is None:
            if self.precision != PRECISION_UNKNOWN:
                raise ValueError("value may be None only for precision 'unknown'")
            return
        if self.precision in (PRECISION_EXACT, PRECISION_DAY) and not (
            _ISO_DATE_RE.match(self.value) and _valid_calendar_day(self.value)
        ):
            raise ValueError(f"exact/day value must be a valid YYYY-MM-DD, got {self.value!r}")
        if self.precision == PRECISION_MONTH and not (
            _ISO_MONTH_RE.match(self.value) and _valid_calendar_month(self.value)
        ):
            raise ValueError(f"month value must be a valid YYYY-MM, got {self.value!r}")

    def to_data(self) -> dict[str, Any]:
        return asdict(self)


def parse_iso(value: Any, source: str) -> PostingDate:
    """Parse an ISO-8601 datetime/date string into a PostingDate.

    A timestamp resolves to an exact day (precision ``exact``); a bare date
    resolves to ``day``. Anything unparseable resolves to ``unknown`` rather
    than raising, because a malformed upstream field must never fabricate a
    date or fail an entire probe.
    """
    if value is None or not str(value).strip():
        return PostingDate(None, PRECISION_UNKNOWN, source)
    text = str(value).strip()
    if _ISO_DATETIME_RE.match(text):
        return PostingDate(text[:10], PRECISION_EXACT, source)
    match = _ISO_DATE_RE.match(text)
    if match:
        return PostingDate(text, PRECISION_DAY, source)
    match = _ISO_MONTH_RE.match(text)
    if match:
        return PostingDate(text, PRECISION_MONTH, source)
    return PostingDate(None, PRECISION_UNKNOWN, source)


def parse_ms_epoch(value: Any, source: str) -> PostingDate:
    """Parse a millisecond epoch timestamp into an exact-day PostingDate."""
    if value is None:
        return PostingDate(None, PRECISION_UNKNOWN, source)
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return PostingDate(None, PRECISION_UNKNOWN, source)
    if number <= 0:
        return PostingDate(None, PRECISION_UNKNOWN, source)
    try:
        stamp = datetime.fromtimestamp(number / 1000.0, tz=_utc())
    except (OverflowError, OSError, ValueError):
        return PostingDate(None, PRECISION_UNKNOWN, source)
    return PostingDate(stamp.strftime("%Y-%m-%d"), PRECISION_EXACT, source)


def parse_date(value: Any, source: str) -> PostingDate:
    """Parse a flexible date value (ISO datetime, date or month)."""
    if value is None or not str(value).strip():
        return PostingDate(None, PRECISION_UNKNOWN, source)
    text = str(value).strip()
    if _ISO_DATETIME_RE.match(text) or _ISO_DATE_RE.match(text):
        return parse_iso(text, source)
    if _ISO_MONTH_RE.match(text):
        return PostingDate(text, PRECISION_MONTH, source)
    return PostingDate(None, PRECISION_UNKNOWN, source)


def unknown(source: str) -> PostingDate:
    """An explicitly-unknown posting date (never fabricated)."""
    return PostingDate(None, PRECISION_UNKNOWN, source)


def to_tracker_text(posted: PostingDate) -> str:
    """Format a posting date for the shared tracker ``posting_date`` field.

    The tracker stores a plain string; precision and extraction source are
    encoded inline so they survive round-trips without a second authority.
    """
    if posted.precision == PRECISION_UNKNOWN:
        return "unknown"
    return f"{posted.value} ({posted.precision}, from {posted.source})"


def _utc() -> Any:
    from datetime import timezone

    return timezone.utc
