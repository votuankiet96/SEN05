from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd

import core_python.notify.signal_watcher as watcher
from core_python.notify.formatter import format_combo_raw_signal_message
from core_python.notify.signal_watcher import (
    _LAST_STALE_LOGGED,
    _drop_open_bar,
    _scan_fingerprint_error,
    _warn_if_stale_bar,
    check_ai_trend_once,
    check_once,
    scan_fingerprint,
)
from core_python.notify.state import SignalState, signal_key
from core_python.strategies.ai_trend.alerts import (
    H3_TREND_CHANGE,
    M45_ENTRY_SIGNAL,
    AiTrendAlert,
    ai_trend_alert_key,
    extract_m45_entry_alerts,
    format_ai_trend_alert,
)
from core_python.strategies.ai_trend.signals import merge_trend_into_entry


class _FakeNotifier:
    dry_run = False

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.calls: list[dict] = []
        self.discord_webhook = "https://discord.example/default"
        self.combo_discord_webhook = "https://discord.example/webhook"

    def send(
        self,
        message: str,
        chat_id: str | None = None,
        *,
        backend: str | None = None,
        discord_webhook: str | None = None,
    ) -> SimpleNamespace:
        self.messages.append(message)
        self.calls.append(
            {
                "message": message,
                "chat_id": chat_id,
                "backend": backend,
                "discord_webhook": discord_webhook,
            }
        )
        return SimpleNamespace(sent=True, backend=backend or "fake", detail="")


def test_scan_fingerprint_is_canonical_for_ordering() -> None:
    groups_a = [
        {
            "strategy": "AI_TREND",
            "event_type": "M45_ENTRY_SIGNAL",
            "symbols": ["eurusd", "GOLD"],
            "tf": "m45",
            "bars": "1000",
            "overrides": {"ENTRY_BARS": 1000, "TREND_BARS": 400},
        },
        {
            "strategy": "combo",
            "symbols": ["US30"],
            "tf": "H1",
            "bars": 500,
            "overrides": {},
        },
    ]
    groups_b = [
        {
            "strategy": "combo",
            "symbols": ["us30"],
            "tf": "h1",
            "bars": 500,
            "overrides": {},
        },
        {
            "strategy": "ai_trend",
            "event_type": "m45_entry_signal",
            "symbols": ["GOLD", "EURUSD"],
            "tf": "M45",
            "bars": 1000,
            "overrides": {"TREND_BARS": 400, "ENTRY_BARS": 1000},
        },
    ]

    assert scan_fingerprint(groups_a, closed_only=True) == scan_fingerprint(groups_b, closed_only=True)
    assert scan_fingerprint(groups_a, closed_only=True) != scan_fingerprint(groups_b, closed_only=False)


def test_scan_fingerprint_error_detects_missing_and_mismatch(tmp_path) -> None:
    group = [{"strategy": "combo", "symbols": ["US30"], "tf": "H1", "bars": 500, "overrides": {}}]
    expected = scan_fingerprint(group, closed_only=True)

    missing_state = SignalState(tmp_path / "missing.json")
    assert "no scan fingerprint" in str(_scan_fingerprint_error(missing_state, expected))

    state = SignalState(tmp_path / "state.json")
    state.set_scan_fingerprint(expected)
    assert _scan_fingerprint_error(state, expected) is None
    assert "scan config changed" in str(_scan_fingerprint_error(state, "different"))


def test_merge_trend_into_entry_uses_only_closed_h3_rows() -> None:
    entry = pd.DataFrame(
        {
            "bartime": pd.to_datetime(
                [
                    "2026-05-06 08:15:00",
                    "2026-05-06 09:00:00",
                    "2026-05-06 11:45:00",
                    "2026-05-06 12:00:00",
                ]
            )
        }
    )
    trend = pd.DataFrame(
        {
            "h3_close_time": pd.to_datetime(["2026-05-06 09:00:00", "2026-05-06 12:00:00"]),
            "ai_knn": [1.0, 2.0],
            "ai_avg": [0.5, 1.5],
            "ai_direction": [1, -1],
            "h3_bias": [1, -1],
            "h3_bias_segment": [10, 11],
        }
    )

    merged = merge_trend_into_entry(entry, trend)

    assert pd.isna(merged.loc[0, "h3_bias"])
    assert int(merged.loc[1, "h3_bias"]) == 1
    assert int(merged.loc[2, "h3_bias"]) == 1
    assert int(merged.loc[3, "h3_bias"]) == -1
    valid = merged["h3_close_time"].notna()
    assert (merged.loc[valid, "bartime"] >= merged.loc[valid, "h3_close_time"]).all()


def test_drop_open_bar_handles_timezone_aware_bartime() -> None:
    now = pd.Timestamp.now("UTC")
    closed_bar = now - pd.Timedelta(hours=2)
    open_bar = now - pd.Timedelta(minutes=30)
    frame = pd.DataFrame({"bartime": [closed_bar, open_bar]})

    out = _drop_open_bar(frame, "H1")

    assert len(out) == 1
    assert pd.Timestamp(out.loc[0, "bartime"]) == closed_bar


def test_signal_state_prunes_ttl_and_preserves_fingerprint(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "sent": {
                    "old": "2000-01-01T00:00:00+00:00",
                    "recent": pd.Timestamp.now("UTC").isoformat(),
                },
                "meta": {"scan_fingerprint": "abc123"},
            }
        ),
        encoding="utf-8",
    )

    state = SignalState(path)

    assert not state.has("old")
    assert state.has("recent")
    assert state.get_scan_fingerprint() == "abc123"


def test_signal_key_format_stability() -> None:
    key = signal_key("combo", "us30", "h1", pd.Timestamp("2026-05-06 12:00:00"), 1)
    utc_key = signal_key("combo", "us30", "h1", pd.Timestamp("2026-05-06 12:00:00", tz="UTC"), 1)
    bangkok_key = signal_key(
        "combo",
        "us30",
        "h1",
        pd.Timestamp("2026-05-06 19:00:00", tz="Asia/Bangkok"),
        1,
    )

    assert key == "combo|US30|H1|2026-05-06 12:00:00|1"
    assert utc_key == key
    assert bangkok_key == key


def test_combo_raw_formatter_excludes_execution_levels() -> None:
    row = pd.Series(
        {
            "bartime": pd.Timestamp("2026-05-06 12:00:00"),
            "signal": 1,
            "open": 100.0,
            "high": 110.0,
            "low": 95.0,
            "close": 108.0,
            "ma": 101.5,
            "macd_h": 2.25,
            "atr": 4.5,
            "rr": 3.0,
            "entry_price": 111.0,
            "sl_price": 94.0,
            "tp_price": 123.0,
            "signal_reason": "close above MA, bullish candle, MACD histogram > 0",
        }
    )

    message = format_combo_raw_signal_message(row, strategy_label="Combo", symbol="US30", tf="H1")

    assert "Combo RAW SIGNAL | US30 H1 | BUY" in message
    assert "OHLC:" in message
    assert "Reason:" in message
    assert "Entry" not in message
    assert "SL" not in message
    assert "TP" not in message
    assert "RR_RAW" not in message


def test_stale_check_uses_latest_close_not_bar_open() -> None:
    _LAST_STALE_LOGGED.clear()
    now = pd.Timestamp("2026-05-06 12:10:00", tz="UTC")

    assert not _warn_if_stale_bar("EURUSD", "H3", "2026-05-06 09:00:00", now_utc=now)
    assert _warn_if_stale_bar("EURUSD", "H3", "2026-05-06 06:00:00", now_utc=now)


def test_check_once_skips_and_marks_historical_combo_signal(tmp_path, monkeypatch) -> None:
    frame = pd.DataFrame({"bartime": [pd.Timestamp("2026-01-01 00:00:00")], "signal": [1]})

    def fake_run_strategy_frame(**_kwargs):
        return frame, SimpleNamespace(label="Combo"), {}

    monkeypatch.setattr(watcher, "run_strategy_frame", fake_run_strategy_frame)
    state = SignalState(tmp_path / "state.json")
    notifier = _FakeNotifier()

    events = check_once(
        strategy="combo",
        symbols=["US30"],
        tf="H1",
        bars=500,
        state=state,
        notifier=notifier,  # type: ignore[arg-type]
        export_on_signal=False,
        max_alert_age_minutes=120,
    )

    key = signal_key("combo", "US30", "H1", pd.Timestamp("2026-01-01 00:00:00"), 1)
    assert state.has(key)
    assert notifier.messages == []
    assert any("skipped historical" in event for event in events)


def test_check_once_routes_combo_signal_to_discord_raw_message(tmp_path, monkeypatch) -> None:
    bartime = pd.Timestamp.now("UTC").floor("h") - pd.Timedelta(hours=1)
    frame = pd.DataFrame(
        {
            "bartime": [bartime.tz_localize(None)],
            "signal": [1],
            "open": [100.0],
            "high": [110.0],
            "low": [95.0],
            "close": [108.0],
            "ma": [101.5],
            "macd_h": [2.25],
            "atr": [4.5],
            "rr": [3.0],
            "entry_price": [111.0],
            "sl_price": [94.0],
            "tp_price": [123.0],
            "signal_reason": ["close above MA, bullish candle, MACD histogram > 0"],
        }
    )

    def fake_run_strategy_frame(**_kwargs):
        return frame, SimpleNamespace(label="Combo"), {}

    monkeypatch.setattr(watcher, "run_strategy_frame", fake_run_strategy_frame)
    state = SignalState(tmp_path / "state.json")
    notifier = _FakeNotifier()

    events = check_once(
        strategy="combo",
        symbols=["US30"],
        tf="H1",
        bars=500,
        state=state,
        notifier=notifier,  # type: ignore[arg-type]
        export_on_signal=False,
        max_alert_age_minutes=240,
    )

    assert notifier.calls[0]["backend"] == "discord"
    assert notifier.calls[0]["discord_webhook"] == notifier.combo_discord_webhook
    assert "Combo RAW SIGNAL | US30 H1 | BUY" in notifier.messages[0]
    assert "Entry" not in notifier.messages[0]
    assert "SL" not in notifier.messages[0]
    assert "TP" not in notifier.messages[0]
    assert "RR_RAW" not in notifier.messages[0]
    assert any("alerted via discord" in event for event in events)


def test_check_once_routes_non_ai_strategy_away_from_telegram(tmp_path, monkeypatch) -> None:
    bartime = pd.Timestamp.now("UTC").floor("h") - pd.Timedelta(hours=1)
    frame = pd.DataFrame(
        {
            "bartime": [bartime.tz_localize(None)],
            "signal": [1],
            "entry_price": [111.0],
            "sl_price": [94.0],
            "tp_price": [123.0],
            "signal_reason": ["test signal"],
        }
    )

    def fake_run_strategy_frame(**_kwargs):
        return frame, SimpleNamespace(label="MA Cross"), {}

    monkeypatch.setattr(watcher, "run_strategy_frame", fake_run_strategy_frame)
    state = SignalState(tmp_path / "state.json")
    notifier = _FakeNotifier()

    events = check_once(
        strategy="ma_cross",
        symbols=["US30"],
        tf="H1",
        bars=500,
        state=state,
        notifier=notifier,  # type: ignore[arg-type]
        export_on_signal=False,
        max_alert_age_minutes=240,
    )

    assert notifier.calls[0]["backend"] == "discord"
    assert notifier.calls[0]["discord_webhook"] == notifier.discord_webhook
    assert "MA Cross" in notifier.messages[0]
    assert any("alerted via discord" in event for event in events)


def test_check_once_logs_csv_export_failure_and_still_sends(tmp_path, monkeypatch, caplog) -> None:
    bartime = pd.Timestamp.now("UTC").floor("h") - pd.Timedelta(hours=1)
    frame = pd.DataFrame(
        {
            "bartime": [bartime.tz_localize(None)],
            "signal": [1],
            "entry_price": [111.0],
            "sl_price": [94.0],
            "tp_price": [123.0],
            "signal_reason": ["test signal"],
        }
    )

    def fake_run_strategy_frame(**_kwargs):
        return frame, SimpleNamespace(label="MA Cross"), {}

    def fake_export_signals(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(watcher, "run_strategy_frame", fake_run_strategy_frame)
    monkeypatch.setattr(watcher, "export_signals", fake_export_signals)
    state = SignalState(tmp_path / "state.json")
    notifier = _FakeNotifier()
    caplog.set_level("WARNING", logger=watcher.__name__)

    events = check_once(
        strategy="ma_cross",
        symbols=["US30"],
        tf="H1",
        bars=500,
        state=state,
        notifier=notifier,  # type: ignore[arg-type]
        export_on_signal=True,
        output_dir=tmp_path,
        max_alert_age_minutes=240,
    )

    assert notifier.messages
    assert any("alerted via discord" in event for event in events)
    assert "CSV export failed for ma_cross US30 H1: disk full" in caplog.text


def test_check_ai_trend_once_skips_and_marks_historical_alert(tmp_path, monkeypatch) -> None:
    alert = AiTrendAlert(
        kind=H3_TREND_CHANGE,
        symbol="AUDUSD",
        tf="H3",
        direction=1,
        bar_time=pd.Timestamp("2026-01-01 00:00:00"),
        event_time=pd.Timestamp("2026-01-01 03:00:00"),
    )

    def fake_run_ai_trend_alerts(**_kwargs):
        return [alert], pd.Timestamp("2026-01-01 00:00:00")

    monkeypatch.setattr(watcher, "run_ai_trend_alerts", fake_run_ai_trend_alerts)
    state = SignalState(tmp_path / "state.json")
    notifier = _FakeNotifier()

    events = check_ai_trend_once(
        symbols=["AUDUSD"],
        tf="H3",
        bars=400,
        state=state,
        notifier=notifier,  # type: ignore[arg-type]
        event_type=H3_TREND_CHANGE,
        max_alert_age_minutes=120,
    )

    assert state.has(ai_trend_alert_key(alert))
    assert notifier.messages == []
    assert any("skipped historical AI Trend" in event for event in events)


def test_m45_alert_report_includes_h3_bias_context() -> None:
    entry_frame = pd.DataFrame(
        {
            "bartime": [pd.Timestamp("2026-05-06 12:00:00")],
            "m45_close_time": [pd.Timestamp("2026-05-06 12:45:00")],
            "signal": [1],
            "h3_bias": [1],
            "h3_bias_segment": [12],
            "h3_close_time": [pd.Timestamp("2026-05-06 12:00:00")],
            "h3_bias_started_at": [pd.Timestamp("2026-05-06 09:00:00")],
            "h3_bias_started_close_time": [pd.Timestamp("2026-05-06 12:00:00")],
            "h3_ai_knn": [2.4],
            "h3_ai_avg": [1.7],
            "ema_fast": [1.12],
            "ema_slow": [1.1],
            "close": [1.13],
            "entry_time": [pd.Timestamp("2026-05-06 12:45:00")],
            "entry_price": [1.131],
            "sl_price": [1.121],
            "tp_price": [1.146],
            "risk_reward": [1.5],
            "signal_reason": ["First M45 bar after H3 close in bullish segment"],
        }
    )

    alerts = extract_m45_entry_alerts(entry_frame, "EURUSD")
    assert len(alerts) == 1
    alert = alerts[0]

    assert alert.kind == M45_ENTRY_SIGNAL
    assert alert.h3_bias == 1
    assert alert.h3_segment == 12
    assert alert.h3_bias_started_at == pd.Timestamp("2026-05-06 09:00:00")
    assert alert.entry_price == 1.131
    assert alert.sl_price == 1.121
    assert alert.tp_price == 1.146
    assert alert.risk_reward == 1.5

    message = format_ai_trend_alert(alert)

    assert "🟩 <b>AI Trend M45 LONG</b>" in message
    assert "H3 bias:   🟢 <b>BULLISH</b>" in message
    assert "Segment:   <code>12</code>" in message
    assert "Bias turn: <code>2026-05-06 09:00 UTC</code> H3 bartime" in message
    assert "Entry:     <code>1.131</code>" in message
    assert "Stoploss:  <code>1.121</code>" in message
    assert "Takeprofit:<code>1.146</code>" in message
    assert "R:R:       <code>1.5</code>" in message
    assert "Turn close:" not in message
    assert "H3 close:" not in message


def test_h3_alert_uses_round_bias_icon() -> None:
    alert = AiTrendAlert(
        kind=H3_TREND_CHANGE,
        symbol="BTCUSD",
        tf="H3",
        direction=-1,
        bar_time=pd.Timestamp("2026-05-06 09:00:00"),
        event_time=pd.Timestamp("2026-05-06 12:00:00"),
    )

    message = format_ai_trend_alert(alert)

    assert "H3 bias:  🔴 <b>BEARISH</b>" in message
    assert "🟥 <b>AI Trend M45 SHORT</b>" not in message


def test_run_ai_trend_alerts_applies_m45_levels_before_extract(monkeypatch) -> None:
    trend_raw = pd.DataFrame({"bartime": [pd.Timestamp("2026-05-06 09:00:00")]})
    entry_with_signal = pd.DataFrame(
        {
            "bartime": [pd.Timestamp("2026-05-06 12:00:00")],
            "m45_close_time": [pd.Timestamp("2026-05-06 12:45:00")],
            "signal": [1],
            "h3_bias": [1],
            "h3_bias_segment": [7],
            "h3_close_time": [pd.Timestamp("2026-05-06 12:00:00")],
            "h3_ai_knn": [2.4],
            "h3_ai_avg": [1.7],
            "ema_fast": [1.12],
            "ema_slow": [1.1],
            "close": [1.13],
        }
    )

    class FakeSpec:
        def normalize_params(self, _overrides, symbol):
            return {
                "SYMBOL": symbol,
                "TREND_TF": "H3",
                "ENTRY_TF": "M45",
                "TREND_BARS": 400,
                "ENTRY_BARS": 1000,
            }

        def add_levels(self, frame, _params, _symbol):
            enriched = frame.copy()
            enriched["entry_price"] = 1.131
            enriched["sl_price"] = 1.121
            enriched["tp_price"] = 1.146
            enriched["risk_reward"] = 1.5
            return enriched

    def fake_load(symbol, tf, bars):
        _ = symbol, bars
        return trend_raw if tf == "H3" else entry_with_signal

    monkeypatch.setattr(watcher, "get_strategy", lambda _name: FakeSpec())
    monkeypatch.setattr(watcher, "_load_ohlcv", fake_load)
    monkeypatch.setattr(watcher, "_drop_open_bar", lambda frame, _tf: frame)
    monkeypatch.setattr(watcher, "_warn_if_stale_bar", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(watcher, "build_ai_trend_frames", lambda _trend, _entry, _params: (_trend, entry_with_signal))

    alerts, _latest = watcher.run_ai_trend_alerts(
        symbol="EURUSD",
        tf="M45",
        bars=1000,
        event_type=M45_ENTRY_SIGNAL,
    )

    assert len(alerts) == 1
    assert alerts[0].entry_price == 1.131
    assert alerts[0].sl_price == 1.121
    assert alerts[0].tp_price == 1.146
    assert alerts[0].risk_reward == 1.5


def test_check_ai_trend_once_skips_m45_that_does_not_match_h3_bias(tmp_path, monkeypatch) -> None:
    now = pd.Timestamp.now("UTC").floor("min")
    alert = AiTrendAlert(
        kind=M45_ENTRY_SIGNAL,
        symbol="EURUSD",
        tf="M45",
        direction=1,
        bar_time=now - pd.Timedelta(minutes=45),
        event_time=now,
        h3_bias=-1,
    )

    def fake_run_ai_trend_alerts(**_kwargs):
        return [alert], alert.bar_time

    monkeypatch.setattr(watcher, "run_ai_trend_alerts", fake_run_ai_trend_alerts)
    state = SignalState(tmp_path / "state.json")
    notifier = _FakeNotifier()

    events = check_ai_trend_once(
        symbols=["EURUSD"],
        tf="M45",
        bars=1000,
        state=state,
        notifier=notifier,  # type: ignore[arg-type]
        event_type=M45_ENTRY_SIGNAL,
    )

    assert state.has(ai_trend_alert_key(alert))
    assert notifier.messages == []
    assert any("skipped AI Trend M45" in event and "H3 bias is -1" in event for event in events)


def test_hourly_summary_includes_only_ai_trend_m45_entries() -> None:
    now = pd.Timestamp.now("UTC")
    events = [
        watcher.SentSignalEvent(
            strategy="ai_trend",
            symbol="EURUSD",
            tf="H3",
            side="BULLISH",
            event_time=now,
            sent_at=now,
            kind=H3_TREND_CHANGE,
        ),
        watcher.SentSignalEvent(
            strategy="ai_trend",
            symbol="EURUSD",
            tf="M45",
            side="BUY",
            event_time=now,
            sent_at=now,
            kind=M45_ENTRY_SIGNAL,
        ),
    ]
    groups = [{"symbols": ["EURUSD"]}]

    summary = watcher._format_hourly_symbol_summary(events, groups, minutes=60)

    assert "Signals: <b>1</b>" in summary
    assert "AI Trend M45 BUY" in summary
    assert "AI Trend H3" not in summary
