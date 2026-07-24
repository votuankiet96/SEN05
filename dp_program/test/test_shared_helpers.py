"""Behavior locks for small helpers shared during refactor v2."""

from __future__ import annotations

from datetime import timezone

import pytest

from core_engine.shared.freshness import stale_after_minutes
from core_engine.shared.time import parse_utc_time


@pytest.mark.parametrize(
    ("timeframe", "overnight", "expected"),
    [
        (5, 0, 15),
        (60, 0, 180),
        (5, 480, 485),
        (60, 480, 540),
    ],
)
def test_stale_after_minutes_preserves_historical_and_live_formula(timeframe, overnight, expected):
    assert stale_after_minutes(timeframe, overnight) == expected


@pytest.mark.parametrize(
    "value",
    ["2026-07-22T05:30:00Z", "2026-07-22T05:30:00+00:00", "2026-07-22T05:30:00"],
)
def test_parse_utc_time_normalizes_supported_iso_shapes(value):
    parsed = parse_utc_time(value)
    assert parsed is not None
    assert parsed.tzinfo == timezone.utc
    assert parsed.isoformat() == "2026-07-22T05:30:00+00:00"


@pytest.mark.parametrize("value", [None, "", "not-a-time"])
def test_parse_utc_time_rejects_missing_or_invalid_values(value):
    assert parse_utc_time(value) is None
