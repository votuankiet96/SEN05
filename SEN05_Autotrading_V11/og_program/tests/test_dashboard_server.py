"""Tests for the signal_display live dashboard server without a real SQL Server."""

from __future__ import annotations

import pytest

from core_python import db_connector
from core_python.signal_display import cli, server
from tests.fixtures import make_ohlcv


def test_run_scan_builds_full_payload_shape(monkeypatch):
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: make_ohlcv(300))

    payload = server.run_scan(strategy="combo", symbol="US30", tf="H1", bars=300)

    assert set(payload) == {"meta", "trendChart", "candles", "overlays", "panels", "markers", "signals", "stats"}
    assert payload["meta"] == {
        "strategy": "combo",
        "strategyLabel": "Combo",
        "symbol": "US30",
        "tf": "H1",
        "bars": 300,
        "hasMore": True,
        "trendMode": "no_trend",
        "trendType": "",
        "trendTf": "",
        "timeMode": "latest",
        "fromTime": "",
        "toTime": "",
    }
    assert payload["trendChart"] is None
    assert payload["overlays"][0]["key"] == "ma"
    assert payload["overlays"][0]["label"] == "MA 20"
    assert {p["key"] for p in payload["panels"]} == {"macd"}


def test_run_scan_ma_cross_overlays_have_fast_and_slow_keys(monkeypatch):
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: make_ohlcv(300))

    payload = server.run_scan(strategy="ma_cross", symbol="US30", tf="M30", bars=300)

    keys = [o["key"] for o in payload["overlays"]]
    assert keys == ["fast_ma", "slow_ma"]
    assert payload["overlays"][0]["label"] == "Fast 13"
    assert payload["overlays"][1]["label"] == "Slow 34"


def test_run_scan_ema_cross_overlays_have_fast_and_slow_ema_keys(monkeypatch):
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: make_ohlcv(300))

    payload = server.run_scan(strategy="ema_cross", symbol="US30", tf="M45", bars=300)

    keys = [o["key"] for o in payload["overlays"]]
    assert keys == ["ema_fast", "ema_slow"]
    assert payload["overlays"][0]["label"] == "Fast EMA 13"
    assert payload["overlays"][1]["label"] == "Slow EMA 34"


def test_run_scan_uses_strategy_default_timeframe_when_blank(monkeypatch):
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: make_ohlcv(300))

    payload = server.run_scan(strategy="ma_cross", symbol="US30", tf="", bars=300)

    assert payload["meta"]["tf"] == "M30"


def test_run_scan_rejects_unknown_symbol(monkeypatch):
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: make_ohlcv(50))

    with pytest.raises(KeyError):
        server.run_scan(strategy="combo", symbol="NOPE", tf="H1", bars=50)


def test_run_scan_rejects_timeframe_outside_strategy_contract(monkeypatch):
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: make_ohlcv(50))

    with pytest.raises(ValueError, match="supports only"):
        server.run_scan(strategy="ma_cross", symbol="US30", tf="H1", bars=50)


def test_run_scan_applies_param_overrides(monkeypatch):
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: make_ohlcv(300))

    default_payload = server.run_scan(strategy="combo", symbol="US30", tf="H1", bars=300)
    overridden_payload = server.run_scan(
        strategy="combo", symbol="US30", tf="H1", bars=300, overrides={"MA_PERIOD": "25"}
    )

    assert default_payload["overlays"][0]["label"] == "MA 20"
    assert overridden_payload["overlays"][0]["label"] == "MA 25"


def test_extract_overrides_drops_request_keys_and_blanks():
    args = {
        "strategy": "combo",
        "symbol": "US30",
        "tf": "H1",
        "bars": "300",
        "trend_mode": "trend_filter",
        "trend_tf": "H4",
        "TREND_FILTER_ENABLED": "true",
        "from_time": "2026-01-01T00:00",
        "to_time": "2026-01-02T00:00",
        "time_range": "2026-01-01 00:00 -> 2026-01-02 00:00",
        "MA_PERIOD": "25",
        "X": "",
    }

    assert server._extract_overrides(args) == {"MA_PERIOD": "25"}


def test_run_scan_with_trend_filter_adds_trend_chart(monkeypatch):
    def fake_load(symbol, tf, bars, *, before=None):
        _ = symbol, before
        minutes = {"H1": 60, "H4": 240}[tf]
        seed = 42 if tf == "H1" else 7
        return make_ohlcv(bars, freq_minutes=minutes, seed=seed, start="2026-01-01")

    monkeypatch.setattr(server, "load", fake_load)

    payload = server.run_scan(
        strategy="combo",
        symbol="US30",
        tf="H1",
        bars=300,
        trend_mode="trend_filter",
        trend_tf="H4",
    )

    assert payload["meta"]["trendMode"] == "trend_filter"
    assert payload["meta"]["trendType"] == "knn"
    assert payload["meta"]["trendTf"] == "H4"
    assert payload["trendChart"]["tf"] == "H4"
    assert payload["trendChart"]["candles"]
    assert {item["key"] for item in payload["trendChart"]["overlays"]} == {
        "trend_ai_knn",
        "trend_ai_avg",
    }


def test_parse_trend_mode_accepts_short_aliases_and_rejects_unknown():
    assert server._parse_trend_mode(None) == "no_trend"
    assert server._parse_trend_mode("nt") == "no_trend"
    assert server._parse_trend_mode("tf") == "trend_filter"

    with pytest.raises(ValueError, match="trend_mode"):
        server._parse_trend_mode("maybe")


def test_parse_time_window_requires_pair_and_orders_bounds():
    assert server._parse_time_window("", "") == (None, None)
    assert server._parse_time_window("2026-01-01T00:00", "2026-01-02T12:30") == (
        server.pd.Timestamp("2026-01-01 00:00"),
        server.pd.Timestamp("2026-01-02 12:30"),
    )
    assert server._parse_time_window("", "", "2026-01-01 00:00 -> 2026-01-02 12:30") == (
        server.pd.Timestamp("2026-01-01 00:00"),
        server.pd.Timestamp("2026-01-02 12:30"),
    )

    with pytest.raises(ValueError, match="set together"):
        server._parse_time_window("2026-01-01T00:00", "")
    with pytest.raises(ValueError, match="earlier"):
        server._parse_time_window("2026-01-03T00:00", "2026-01-02T00:00")
    with pytest.raises(ValueError, match="cannot be combined"):
        server._parse_time_window("2026-01-01T00:00", "2026-01-02T00:00", "2026-01-01 -> 2026-01-02")
    with pytest.raises(ValueError, match="time_range"):
        server._parse_time_window("", "", "2026-01-01 00:00")


def test_run_scan_time_range_loads_warmup_and_trims_payload(monkeypatch):
    calls = []

    def fake_load(symbol, tf, bars, *, before=None):
        calls.append(("load", symbol, tf, bars, before))
        start = server.pd.Timestamp(before) - server.pd.Timedelta(hours=bars)
        return make_ohlcv(
            bars,
            freq_minutes=60,
            start=str(start),
            seed=1,
        )

    def fake_load_range(symbol, tf, date_from=None, date_to=None):
        calls.append(("load_range", symbol, tf, date_from, date_to))
        return make_ohlcv(
            5,
            freq_minutes=60,
            start="2026-01-01 00:00",
            seed=2,
        )

    monkeypatch.setattr(db_connector, "load", fake_load)
    monkeypatch.setattr(db_connector, "load_range", fake_load_range)
    from_time = server.pd.Timestamp("2026-01-01 00:00")
    to_time = server.pd.Timestamp("2026-01-01 04:00")

    payload = server.run_scan(
        strategy="combo",
        symbol="US30",
        tf="H1",
        bars=300,
        from_time=from_time,
        to_time=to_time,
    )

    assert calls[0] == ("load", "US30", "H1", server.WARMUP_BARS, from_time)
    assert calls[1] == ("load_range", "US30", "H1", from_time.to_pydatetime(), to_time.to_pydatetime())
    assert payload["meta"]["timeMode"] == "range"
    assert payload["meta"]["hasMore"] is False
    assert payload["meta"]["fromTime"] == "2026-01-01 00:00"
    assert payload["meta"]["toTime"] == "2026-01-01 04:00"
    assert len(payload["candles"]) == 5
    assert payload["candles"][0]["time"] == int(from_time.timestamp())


def test_export_csv_for_combo_includes_pending_entry(monkeypatch):
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: make_ohlcv(300))

    csv_text, filename = server.export_csv(strategy="combo", symbol="US30", tf="H1", bars=300)
    lines = csv_text.splitlines()

    assert filename == "combo_US30_H1_latest_300bars_signals_nt.csv"
    assert lines[0] == "bartime,atr,entry,signal"
    # cTrader ProtoOATradeSide encoding: BUY=1, SELL=2 (not OG's internal 1/-1).
    assert lines[1].split(",")[-1] in {"1", "2"}


def test_export_csv_time_range_uses_range_filename(monkeypatch):
    monkeypatch.setattr(db_connector, "load", lambda symbol, tf, bars, *, before=None: make_ohlcv(bars))
    monkeypatch.setattr(
        db_connector,
        "load_range",
        lambda symbol, tf, date_from=None, date_to=None: make_ohlcv(
            300,
            freq_minutes=60,
            start="2026-01-01",
        ),
    )

    _csv_text, filename = server.export_csv(
        strategy="combo",
        symbol="US30",
        tf="H1",
        bars=300,
        from_time=server.pd.Timestamp("2026-01-01 00:00"),
        to_time=server.pd.Timestamp("2026-01-02 00:00"),
    )

    assert filename == "combo_US30_H1_20260101_0000_20260102_0000_signals_nt.csv"


def test_export_csv_for_ma_cross_keeps_three_columns(monkeypatch):
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: make_ohlcv(300))

    csv_text, filename = server.export_csv(
        strategy="ma_cross",
        symbol="US30",
        tf="M30",
        bars=300,
    )

    assert filename == "ma_cross_US30_M30_latest_300bars_signals_nt.csv"
    assert csv_text.splitlines()[0] == "bartime,atr,signal"


def test_export_csv_applies_param_overrides(monkeypatch):
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: make_ohlcv(300))

    default_csv, _ = server.export_csv(strategy="combo", symbol="US30", tf="H1", bars=300)
    overridden_csv, _ = server.export_csv(
        strategy="combo", symbol="US30", tf="H1", bars=300, overrides={"MA_PERIOD": "25"}
    )

    assert default_csv != overridden_csv


def test_parse_bars_defaults_and_clamps():
    assert server._parse_bars(None) == server.N_BARS
    assert server._parse_bars("not-a-number") == server.N_BARS
    assert server._parse_bars("10") == cli.BARS_MIN
    assert server._parse_bars("999999") == cli.BARS_MAX
    assert server._parse_bars("500") == 500


def test_parse_before_accepts_unix_utc_and_rejects_invalid_values():
    assert server._parse_before("1767225600") == server.pd.Timestamp("2026-01-01")

    with pytest.raises(ValueError, match="positive Unix timestamp"):
        server._parse_before("invalid")


def test_db_load_applies_strict_before_cursor(monkeypatch):
    captured = {}

    def fake_query(query, params, **kwargs):
        captured.update(query=query, params=params, kwargs=kwargs)
        return make_ohlcv(1)

    monkeypatch.setattr(db_connector, "_run_ohlcv_query", fake_query)
    cutoff = server.pd.Timestamp("2026-01-10 12:00")

    db_connector.load("US30", "H1", 25, before=cutoff)

    assert "f.BarTime < ?" in captured["query"]
    assert captured["params"][:3] == (25, 10, "H1")
    assert captured["params"][-1] == cutoff.to_pydatetime()


def test_run_scan_history_uses_warmup_and_returns_nearest_batch(monkeypatch):
    source = make_ohlcv(100, start="2025-12-01")
    calls = {}

    def fake_load(symbol, tf, bars, *, before=None):
        calls.update(symbol=symbol, tf=tf, bars=bars, before=before)
        return source

    monkeypatch.setattr(server, "load", fake_load)
    cutoff = server.pd.Timestamp("2026-01-01")

    payload = server.run_scan(
        strategy="combo",
        symbol="US30",
        tf="H1",
        bars=50,
        before=cutoff,
    )

    assert calls == {"symbol": "US30", "tf": "H1", "bars": 100, "before": cutoff}
    assert len(payload["candles"]) == 50
    assert payload["candles"][0]["time"] == int(source.iloc[50]["bartime"].timestamp())
    assert payload["meta"]["hasMore"] is True


def test_run_scan_history_returns_oldest_remainder(monkeypatch):
    source = make_ohlcv(40, start="2025-01-01")
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars, *, before=None: source)

    payload = server.run_scan(
        strategy="combo",
        symbol="US30",
        tf="H1",
        bars=50,
        before=server.pd.Timestamp("2026-01-01"),
    )

    assert len(payload["candles"]) == 40
    assert payload["meta"]["hasMore"] is False


def test_api_scan_route_returns_200_json(monkeypatch):
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: make_ohlcv(300))
    client = server.create_app().test_client()

    response = client.get("/api/scan?strategy=combo&symbol=US30&tf=H1&bars=300")

    assert response.status_code == 200
    assert response.json["meta"]["symbol"] == "US30"


def test_api_scan_route_returns_trend_payload(monkeypatch):
    def fake_load(symbol, tf, bars, *, before=None):
        _ = symbol, before
        minutes = {"H1": 60, "H4": 240}[tf]
        seed = 42 if tf == "H1" else 7
        return make_ohlcv(bars, freq_minutes=minutes, seed=seed, start="2026-01-01")

    monkeypatch.setattr(server, "load", fake_load)
    client = server.create_app().test_client()

    response = client.get(
        "/api/scan?strategy=combo&symbol=US30&tf=H1&bars=300&trend_mode=trend_filter&trend_tf=H4"
    )

    assert response.status_code == 200
    assert response.json["trendChart"]["tf"] == "H4"


def test_api_scan_route_accepts_from_to_time_range(monkeypatch):
    monkeypatch.setattr(db_connector, "load", lambda symbol, tf, bars, *, before=None: make_ohlcv(bars))
    monkeypatch.setattr(
        db_connector,
        "load_range",
        lambda symbol, tf, date_from=None, date_to=None: make_ohlcv(3, freq_minutes=60, start="2026-01-01"),
    )
    client = server.create_app().test_client()

    response = client.get(
        "/api/scan?strategy=combo&symbol=US30&tf=H1&bars=300"
        "&from_time=2026-01-01%2000:00&to_time=2026-01-01%2002:00"
    )

    assert response.status_code == 200
    assert response.json["meta"]["timeMode"] == "range"
    assert len(response.json["candles"]) == 3


def test_api_scan_route_returns_400_on_bad_symbol(monkeypatch):
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: make_ohlcv(50))
    client = server.create_app().test_client()

    response = client.get("/api/scan?strategy=combo&symbol=NOPE&tf=H1")

    assert response.status_code == 400
    assert "error" in response.json


def test_api_export_route_returns_csv_attachment(monkeypatch):
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: make_ohlcv(300))
    client = server.create_app().test_client()

    response = client.get("/api/export?strategy=combo&symbol=US30&tf=H1&bars=300&MA_PERIOD=25")

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "attachment" in response.headers["Content-Disposition"]


def test_index_route_serves_html_with_embedded_strategy_config():
    client = server.create_app().test_client()

    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "combo" in body
    assert "ma_cross" in body
    assert "LightweightCharts" in body
    assert '"tunedSymbols": ["US30", "US500"' in body
    assert '"recommendedTimeframes": ["H1", "H2", "H3", "H4"]' in body
    assert '"trendTimeframes": ["H1", "H2", "H3", "H4"]' in body
    assert 'id="trend-mode"' in body
    assert 'id="trend-tf"' in body
    assert 'id="trend-chart"' in body
    assert 'id="signal-table"' not in body
    assert 'id="signal-detail"' in body
    assert "subscribeClick(showSignalDetail)" in body
    assert "subscribeVisibleTimeRangeChange(syncEntryToTrendTimeRange)" in body
    assert "subscribeVisibleTimeRangeChange(syncTrendToEntryTimeRange)" in body
    assert "subscribeClick(selectTrendRangePoint)" in body
    assert "trendSelectionRange(trendRangeStartTime, selectedTime)" in body
    assert "setEntryMarkers(entryMarkersForTrendSelection(selectedTime, null))" in body
    assert "setEntryMarkers(entryMarkersForTrendSelection(trendRangeStartTime, selectedTime))" in body
    assert "barsInLogicalRange" in body
    assert 'id="from-time" type="text"' in body
    assert 'id="to-time" type="text"' in body
    assert 'id="time-range"' not in body
    assert 'params.set("from_time", el.fromTime.value.trim())' in body
    assert 'params.set("to_time", el.toTime.value.trim())' in body
    assert 'if (chartData.meta.timeMode === "range") return;' in body
    assert 'params.set("before", String(oldest))' in body


def test_api_scan_rejects_invalid_history_cursor():
    client = server.create_app().test_client()

    response = client.get("/api/scan?strategy=combo&symbol=US30&tf=H1&before=invalid")

    assert response.status_code == 400
    assert "before" in response.json["error"]


def test_health_route_returns_ok():
    client = server.create_app().test_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json == {"status": "ok"}


def test_main_parses_host_port_and_starts_app(monkeypatch):
    calls = {}

    class FakeApp:
        def run(self, **kwargs):
            calls.update(kwargs)

    monkeypatch.setattr(server, "create_app", lambda: FakeApp())

    rc = server.main(["--host", "0.0.0.0", "--port", "9000"])

    assert rc == 0
    assert calls["host"] == "0.0.0.0"
    assert calls["port"] == 9000
