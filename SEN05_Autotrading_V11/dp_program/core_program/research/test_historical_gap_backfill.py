"""Isolated safety tests for the research historical repair module."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest


RESEARCH_ROOT = Path(__file__).resolve().parent
if str(RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(RESEARCH_ROOT))

import historical_gap_backfill as historical


UTC = timezone.utc


def _candle(timestamp: datetime, close: str = "100") -> dict:
    value = Decimal(close)
    return {
        "symbol_id": 1,
        "symbol": "BTCUSD",
        "exchange": "CAPITALCOM",
        "timeframe": "M30",
        "timestamp": timestamp,
        "open": value,
        "high": value,
        "low": value,
        "close": value,
        "volume": Decimal("1"),
    }


def _pair() -> tuple[dict, dict]:
    return (
        {
            "symbol_id": 1,
            "symbol": "BTCUSD",
            "exchange": "CAPITALCOM",
            "asset_type": "Crypto",
        },
        {
            "code": "M30",
            "minutes": 30,
            "interval": "30",
            "staging_table": "SEN.TF_M30",
        },
    )


def test_date_only_end_includes_the_complete_day() -> None:
    assert historical.parse_boundary(
        "2024-12-31", inclusive_date_end=True
    ) == datetime(2025, 1, 1, tzinfo=UTC)


def test_gap_detection_includes_leading_internal_and_trailing() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=5)
    timestamps = [
        start + timedelta(hours=1),
        start + timedelta(hours=1, minutes=30),
        start + timedelta(hours=3),
    ]
    gaps = historical.detect_gap_candidates(timestamps, start, end, 30)
    assert [item.kind for item in gaps] == ["leading", "internal", "trailing"]


def test_provider_observed_comparison_ignores_calendar_absence() -> None:
    friday = datetime(2024, 1, 5, 21, tzinfo=UTC)
    monday = datetime(2024, 1, 8, 0, tzinfo=UTC)
    candles = [_candle(friday), _candle(monday)]
    provider = {item["timestamp"]: item for item in candles}
    existing = {
        item["timestamp"].replace(tzinfo=None): historical.candle_signature(item)
        for item in candles
    }
    missing, changed = historical.compare_provider_to_fact(provider, existing)
    assert missing == []
    assert changed == []


def test_provider_observed_missing_bar_is_detected() -> None:
    first = datetime(2024, 1, 1, tzinfo=UTC)
    candles = [_candle(first), _candle(first + timedelta(minutes=30))]
    provider = {item["timestamp"]: item for item in candles}
    existing = {
        first.replace(tzinfo=None): historical.candle_signature(candles[0])
    }
    missing, changed = historical.compare_provider_to_fact(provider, existing)
    assert missing == [first + timedelta(minutes=30)]
    assert changed == []


def test_repair_windows_cluster_adjacent_missing_bars() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    provider = [start + timedelta(minutes=30 * index) for index in range(7)]
    missing = provider[2:4] + [provider[6]]
    windows = historical.build_repair_windows(provider, missing)
    assert windows[0].start == provider[1]
    assert windows[0].end == provider[4]
    assert windows[1].start == provider[5]
    assert windows[1].end == provider[6]


def test_unreachable_probe_never_calls_tradingview(monkeypatch: pytest.MonkeyPatch) -> None:
    called = []
    monkeypatch.setattr(
        historical,
        "fetch_candles_batch",
        lambda *_args, **_kwargs: called.append(True),
    )
    start = datetime(2017, 1, 1, tzinfo=UTC)
    end = datetime(2025, 1, 1, tzinfo=UTC)
    config = {
        "backfill": {"overlap_bars": 3, "max_bars_per_request": 20_000},
        "live": {"closed_candles_only": True},
    }
    result, provider, missing, changed = historical.probe_pair(
        config,
        _pair(),
        start,
        end,
        {},
        [historical.GapCandidate("empty", start, end)],
        now=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert result["status"] == "UNREACHABLE_WITH_CURRENT_TRANSPORT"
    assert result["writes"] == 0
    assert provider == missing == changed == []
    assert called == []


def test_historical_apply_is_blocked_before_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        historical,
        "fetch_and_store",
        lambda *_args, **_kwargs: pytest.fail("historical write must be blocked"),
    )
    old = datetime(2017, 1, 1, tzinfo=UTC)
    provider = [_candle(old), _candle(old + timedelta(minutes=30))]
    result = historical.apply_repair(
        {"backfill": {"lookback_days": 60}},
        _pair(),
        provider,
        [old + timedelta(minutes=30)],
        [],
        now=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert result["status"] == "HISTORICAL_WRITE_SCOPE_BLOCKED"
    assert result["writes"] == 0


def test_changed_existing_rows_are_always_blocked() -> None:
    result = historical.apply_repair(
        {"backfill": {"lookback_days": 60}},
        _pair(),
        [],
        [datetime(2026, 8, 31, tzinfo=UTC)],
        [datetime(2026, 8, 30, tzinfo=UTC)],
        now=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert result == {"status": "CHANGED_EXISTING_ROWS_BLOCKED", "writes": 0}
