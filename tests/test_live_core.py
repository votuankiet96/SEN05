"""Unit tests for source-agnostic and live helper functions (no Redis, no DB)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd

from og_core.signals import build_signal_id
from og_live import pipeline
from og_live.settings import WatchedItem
from og_live.sources.candle_snapshot import parse_snapshot_entry, snapshot_symbol_tf


def test_signal_id_is_deterministic():
    args = ("combo", "US30", "H1", "2026-01-05 10:00:00", 1)
    assert build_signal_id(*args) == build_signal_id(*args)


def test_signal_id_accepts_equivalent_timestamp_forms():
    a = build_signal_id("combo", "US30", "H1", "2026-01-05 10:00:00", 1)
    b = build_signal_id("combo", "US30", "H1", pd.Timestamp("2026-01-05 10:00:00"), 1)
    assert a == b


def test_signal_id_differs_by_direction():
    buy = build_signal_id("combo", "US30", "H1", "2026-01-05 10:00:00", 1)
    sell = build_signal_id("combo", "US30", "H1", "2026-01-05 10:00:00", -1)
    assert buy != sell


def test_signal_id_differs_by_symbol():
    a = build_signal_id("combo", "US30", "H1", "2026-01-05 10:00:00", 1)
    b = build_signal_id("combo", "DE40", "H1", "2026-01-05 10:00:00", 1)
    assert a != b


def test_signal_id_differs_by_bar_time():
    a = build_signal_id("combo", "US30", "H1", "2026-01-05 10:00:00", 1)
    b = build_signal_id("combo", "US30", "H1", "2026-01-05 11:00:00", 1)
    assert a != b


def test_candle_snapshot_parser_returns_standard_frame():
    fields = {
        "tv_symbol": "us30",
        "tf_code": "h1",
        "bars": json.dumps(
            [
                {
                    "bar_time": "2026-01-05T11:00:00",
                    "open": "102",
                    "high": "103",
                    "low": "101",
                    "close": "102.5",
                    "volume": "200",
                },
                {
                    "bar_time": "2026-01-05T10:00:00",
                    "open": "100",
                    "high": "101",
                    "low": "99",
                    "close": "100.5",
                    "volume": "100",
                },
            ]
        ),
    }

    assert snapshot_symbol_tf(fields) == ("US30", "H1")
    df = parse_snapshot_entry(fields)

    assert df.columns.tolist() == ["bartime", "open", "high", "low", "close", "volume"]
    assert df["bartime"].astype(str).tolist() == ["2026-01-05 10:00:00", "2026-01-05 11:00:00"]
    assert df["close"].tolist() == [100.5, 102.5]


def test_live_pipeline_latest_only_does_not_replay_old_signals(monkeypatch):
    enriched = pd.DataFrame(
        {
            "bartime": pd.to_datetime(["2026-01-05 10:00:00", "2026-01-05 11:00:00"]),
            "close": [100.0, 101.0],
            "signal": [1, 0],
        }
    )

    def fake_run_strategy_on_bars(*_args, **_kwargs):
        return SimpleNamespace(strategy="combo", symbol="US30", tf="H1", enriched=enriched)

    monkeypatch.setattr(pipeline, "run_strategy_on_bars", fake_run_strategy_on_bars)
    item = WatchedItem(strategy="combo", symbols=("US30",), tf="H1", latest_only=True)

    rows = pipeline.signal_payloads_from_bars(item, "US30", "H1", pd.DataFrame({"bartime": [pd.Timestamp("now")]}))

    assert rows == []


def test_live_pipeline_can_replay_when_explicitly_enabled(monkeypatch):
    enriched = pd.DataFrame(
        {
            "bartime": pd.to_datetime(["2026-01-05 10:00:00", "2026-01-05 11:00:00"]),
            "close": [100.0, 101.0],
            "signal": [1, 0],
        }
    )

    def fake_run_strategy_on_bars(*_args, **_kwargs):
        return SimpleNamespace(strategy="combo", symbol="US30", tf="H1", enriched=enriched)

    monkeypatch.setattr(pipeline, "run_strategy_on_bars", fake_run_strategy_on_bars)
    item = WatchedItem(strategy="combo", symbols=("US30",), tf="H1", latest_only=False)

    rows = pipeline.signal_payloads_from_bars(item, "US30", "H1", pd.DataFrame({"bartime": [pd.Timestamp("now")]}))

    assert len(rows) == 1
    assert rows[0]["side"] == "BUY"
