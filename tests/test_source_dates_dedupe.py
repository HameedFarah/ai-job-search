"""Posting-date precision and dedupe tests.

The posting-date contract is central to the Career Engine: dates are never
fabricated, every value carries a precision, and unknown stays unknown.
"""

from __future__ import annotations

import pytest

from career_engine.sources.dates import (
    PRECISION_DAY,
    PRECISION_EXACT,
    PRECISION_MONTH,
    PRECISION_UNKNOWN,
    PostingDate,
    parse_date,
    parse_iso,
    parse_ms_epoch,
    to_tracker_text,
    unknown,
)
from career_engine.sources.dedupe import DedupeStore, dedupe_key, normalize_key_text


def test_iso_timestamp_is_exact_day() -> None:
    parsed = parse_iso("2026-06-16T08:14:18-04:00", "Greenhouse first_published")
    assert parsed.precision == PRECISION_EXACT
    assert parsed.value == "2026-06-16"
    assert parsed.source == "Greenhouse first_published"


def test_bare_date_is_day_precision() -> None:
    parsed = parse_iso("2026-07-08", "Workable published_on")
    assert parsed.precision == PRECISION_DAY
    assert parsed.value == "2026-07-08"


def test_ms_epoch_is_exact_day() -> None:
    parsed = parse_ms_epoch(1750110000000, "Lever createdAt")
    assert parsed.precision == PRECISION_EXACT
    assert parsed.value == "2025-06-16"  # 1750110000000 ms == 2025-06-16


def test_garbage_never_fabricates_a_date() -> None:
    assert parse_iso("soon", "board") == PostingDate(None, PRECISION_UNKNOWN, "board")
    assert parse_ms_epoch("n/a", "board") == PostingDate(None, PRECISION_UNKNOWN, "board")
    assert parse_date(None, "board").precision == PRECISION_UNKNOWN
    assert unknown("board").precision == PRECISION_UNKNOWN
    assert unknown("board").value is None


def test_month_precision() -> None:
    parsed = parse_iso("2026-08", "some source")
    assert parsed.precision == PRECISION_MONTH
    assert parsed.value == "2026-08"


def test_postind_date_rejects_invalid_precision() -> None:
    with pytest.raises(ValueError):
        PostingDate("2026-08-01", "not-a-precision", "x")
    with pytest.raises(ValueError):
        PostingDate("2026-13-40", PRECISION_EXACT, "x")


def test_tracker_text_encodes_precision_and_source() -> None:
    parsed = parse_iso("2026-06-16T08:14:18-04:00", "Greenhouse first_published")
    assert to_tracker_text(parsed) == "2026-06-16 (exact, from Greenhouse first_published)"
    assert to_tracker_text(unknown("board")) == "unknown"


def test_dedupe_key_prefers_external_id() -> None:
    a = dedupe_key(source_id="greenhouse", external_job_id="1001", source_url="https://x/1001")
    b = dedupe_key(source_id="greenhouse", external_job_id="1001", source_url="https://x/1001")
    c = dedupe_key(source_id="greenhouse", external_job_id="1002", source_url="https://x/1002")
    assert a == b
    assert a != c


def test_dedupe_key_falls_back_to_triple() -> None:
    a = dedupe_key(source_id="jsonld", company="ACME", role="Design Manager", location="Riyadh")
    b = dedupe_key(source_id="jsonld", company="  Acme ", role=" design manager ", location="riyadh")
    assert a == b


def test_dedupe_store_counts_new_and_duplicate() -> None:
    store = DedupeStore()
    key = dedupe_key(source_id="test", external_job_id="1")
    assert store.add(key) is True
    assert store.add(key) is False
    assert store.is_duplicate(key) is True
    assert len(store) == 1


def test_normalize_key_text_collapses_whitespace() -> None:
    assert normalize_key_text("  Senior   Design  ") == "senior design"
