"""Unit tests for source-agnostic and live helper functions (no Redis, no DB)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from og_core.signals import build_signal_id
from og_live.common import pipeline
from og_live.common.candle_snapshot import parse_snapshot_event, parse_state_snapshot, snapshot_matches_event
from og_live.common.settings import WatchedItem
from og_live.pubsub_mechanism import settings as pubsub_settings
from og_live.pubsub_mechanism.app import _decode_message
from og_live.stream_mechanism import settings as stream_settings
from og_live.stream_mechanism.healthcheck import CheckResult, _report, _summarize_snapshot_json


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


def test_candle_snapshot_state_parser_returns_standard_frame():
    raw_snapshot = json.dumps(
        {
            "schema_version": 1,
            "tv_symbol": "us30",
            "tf_code": "h1",
            "bars_count": 2,
            "latest_bar_time": "2026-01-05T11:00:00",
            "snapshot_version": "US30:H1:2026-01-05T11:00:00",
            "bars": [
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
            ],
        }
    )

    snapshot = parse_state_snapshot(raw_snapshot)
    df = snapshot.bars

    assert snapshot.symbol == "US30"
    assert snapshot.tf == "H1"
    assert df.columns.tolist() == ["bartime", "open", "high", "low", "close", "volume"]
    assert df["bartime"].astype(str).tolist() == ["2026-01-05 10:00:00", "2026-01-05 11:00:00"]
    assert df["close"].tolist() == [100.5, 102.5]


def test_candle_snapshot_event_matches_state_snapshot():
    fields = {
        "schema_version": "1",
        "event_type": "snapshot_updated",
        "tv_symbol": "US30",
        "tf_code": "H1",
        "bar_time": "2026-01-05T11:00:00",
        "state_key": "dp:candle_snapshot:latest:US30:H1",
        "bars_count": "500",
        "published_at_utc": "2026-01-05T11:00:03",
        "snapshot_version": "US30:H1:2026-01-05T11:00:00",
    }
    event = parse_snapshot_event(fields)
    snapshot = parse_state_snapshot(
        json.dumps(
            {
                "schema_version": 1,
                "tv_symbol": "US30",
                "tf_code": "H1",
                "bars_count": 1,
                "latest_bar_time": "2026-01-05T11:00:00",
                "snapshot_version": "US30:H1:2026-01-05T11:00:00",
                "bars": [
                    {
                        "bar_time": "2026-01-05T11:00:00",
                        "open": 100,
                        "high": 101,
                        "low": 99,
                        "close": 100.5,
                        "volume": 0,
                    }
                ],
            }
        )
    )

    assert event.symbol == "US30"
    assert event.tf == "H1"
    assert snapshot_matches_event(snapshot, event)


def test_default_live_watchlist_matches_live_fetching_universe(monkeypatch):
    monkeypatch.delenv("OG_STREAM_WATCHED_JSON", raising=False)
    monkeypatch.delenv("OG_STREAM_STRATEGY", raising=False)
    monkeypatch.delenv("OG_STREAM_ASSET_TYPES", raising=False)
    monkeypatch.delenv("OG_STREAM_SYMBOLS", raising=False)
    monkeypatch.delenv("OG_STREAM_TIMEFRAMES", raising=False)
    monkeypatch.delenv("OG_STREAM_BARS", raising=False)
    monkeypatch.delenv("OG_STREAM_LATEST_ONLY", raising=False)

    watched = stream_settings.load_watched_items()

    assert len(watched) == len(stream_settings.LIVE_FETCHING_TIMEFRAMES)
    assert {item.tf for item in watched} == set(stream_settings.LIVE_FETCHING_TIMEFRAMES)
    assert sum(len(item.symbols) for item in watched) == 165
    assert all(item.strategy == "combo" for item in watched)
    assert all(item.symbols == stream_settings.LIVE_FETCHING_SYMBOLS for item in watched)
    assert all(item.bars == 500 for item in watched)
    assert all(item.latest_only for item in watched)


def test_live_watchlist_can_filter_by_asset_and_timeframe(monkeypatch):
    monkeypatch.delenv("OG_STREAM_WATCHED_JSON", raising=False)
    monkeypatch.delenv("OG_STREAM_SYMBOLS", raising=False)
    monkeypatch.setenv("OG_STREAM_STRATEGY", "combo")
    monkeypatch.setenv("OG_STREAM_ASSET_TYPES", "index")
    monkeypatch.setenv("OG_STREAM_TIMEFRAMES", "H4")

    watched = stream_settings.load_watched_items()

    assert len(watched) == 1
    assert watched[0].strategy == "combo"
    assert watched[0].tf == "H4"
    assert watched[0].symbols == ("DE40", "FR40", "HK50", "J225", "SP35", "UK100", "US100", "US30", "US500")
    assert stream_settings.watched_summary()["pairs"] == 9


def test_live_watched_bars_are_clamped(monkeypatch):
    monkeypatch.setenv(
        "OG_STREAM_WATCHED_JSON",
        json.dumps([{"strategy": "combo", "symbols": ["us30"], "tf": "h1", "bars": 0}]),
    )

    watched = stream_settings.load_watched_items()

    assert watched[0].bars == 1


def test_healthcheck_warn_does_not_fail_without_strict_mode():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    report = _report(now, [CheckResult("coverage", "warn", "short history")], fail_on_warn=False)

    assert report["status"] == "warn"
    assert report["exit_status"] == "ok"


def test_healthcheck_warn_can_fail_in_strict_mode():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    report = _report(now, [CheckResult("coverage", "warn", "short history")], fail_on_warn=True)

    assert report["status"] == "warn"
    assert report["exit_status"] == "fail"


def test_healthcheck_summarizes_state_snapshot_without_leaking_payload():
    summary = _summarize_snapshot_json(
        json.dumps(
            {
                "tv_symbol": "US30",
                "tf_code": "M5",
                "bars_count": 2,
                "latest_bar_time": "2026-01-01T00:05:00",
                "snapshot_version": "US30:M5:2026-01-01T00:05:00",
                "bars": [
                    {"bar_time": "2026-01-01T00:00:00", "open": 1, "high": 2, "low": 0, "close": 1, "volume": 0},
                    {"bar_time": "2026-01-01T00:05:00", "open": 2, "high": 3, "low": 1, "close": 2, "volume": 0},
                ],
            }
        )
    )

    assert summary["valid"] is True
    assert summary["count"] == 2
    assert summary["first_bar_time"] == "2026-01-01T00:00:00"
    assert summary["last_bar_time"] == "2026-01-01T00:05:00"
    assert "bars" not in summary


def test_stream_signal_output_defaults_to_routed_strategy_symbol_timeframe():
    keys = stream_settings.signal_stream_keys("combo", {"symbol": "HK50", "timeframe": "H4"})

    assert keys == ("og:stream:signals:combo:HK50:H4",)


def test_pubsub_signal_output_uses_pubsub_namespace():
    keys = pubsub_settings.signal_stream_keys("combo", {"symbol": "HK50", "timeframe": "H4"})

    assert keys == ("og:pubsub:signals:combo:HK50:H4",)


def test_pubsub_message_decoder_accepts_dp_contract_json():
    fields = _decode_message(
        json.dumps(
            {
                "schema_version": 1,
                "event_type": "snapshot_updated",
                "symbol_id": 8,
                "tv_symbol": "US500",
                "tf_code": "M5",
                "bar_time": "2026-07-14T09:00:00",
                "state_key": "dp:candle_snapshot:latest:US500:M5",
                "snapshot_version": "US500:M5:2026-07-14T09:00:00",
                "bars_count": 500,
                "published_at_utc": "2026-07-14T09:05:12Z",
            }
        )
    )

    event = parse_snapshot_event(fields)

    assert event.symbol == "US500"
    assert event.tf == "M5"
    assert event.bars_count == 500


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
