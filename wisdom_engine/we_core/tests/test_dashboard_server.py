"""Tests for the signal_display live dashboard server without a real SQL Server."""

from __future__ import annotations

import gzip

import pandas as pd
import pytest
from wisdom_engine.we_core.src import db_connector
from wisdom_engine.we_core.src.signal_display import cli, server

from tests.fixtures import make_ohlcv


def _fake_load_h1_h4(symbol, tf, bars, *, before=None):
    """Như loader thật: N nến CUỐI của mỗi khung, hai khung kết thúc cùng một mốc (nến trend còn dư warm-up phía trước)."""
    _ = symbol, before
    minutes = {"H1": 60, "H4": 240}[tf]
    seed = 42 if tf == "H1" else 7
    start = pd.Timestamp("2026-03-01") - pd.Timedelta(minutes=minutes * bars)
    return make_ohlcv(bars, freq_minutes=minutes, seed=seed, start=start)


def test_run_scan_builds_full_payload_shape(monkeypatch):
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: make_ohlcv(300))

    payload = server.run_scan(strategy="combo", symbol="US30", tf="H1", bars=300)

    assert set(payload) == {"meta", "trendChart", "candles", "overlays", "panels", "markers", "signals", "stats"}
    assert payload["meta"] == {
        "strategy": "combo",
        "strategyLabel": "Combo",
        "symbol": "US30",
        "priceDigits": 2,
        "tf": "H1",
        "bars": 300,
        "hasMore": True,
        "trendMode": "no_trend",
        "trendMethods": "",
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
        "ATR_PERIOD": "",
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
        trend_methods="knn",  # mặc định của config là breakout (khung của tín hiệu); khung tham chiếu H4 chỉ có với knn
        trend_tf="H4",
    )

    assert payload["meta"]["trendMode"] == "trend_filter"
    assert payload["meta"]["trendMethods"] == "knn"
    assert payload["meta"]["trendTf"] == "H4"
    assert payload["trendChart"]["tf"] == "H4"
    assert payload["trendChart"]["candles"]
    assert {item["key"] for item in payload["trendChart"]["overlays"]} == {
        "trend_ai_knn",
        "trend_ai_avg",
    }


def test_run_scan_with_breakout_trend_uses_the_entry_timeframe_and_keeps_its_own_trend_pane(monkeypatch):
    monkeypatch.setattr(server, "load", _fake_load_h1_h4)

    payload = server.run_scan(strategy="combo", symbol="US30", tf="H1", bars=300, trend_mode="trend_filter", trend_methods="breakout")

    assert payload["meta"]["trendMethods"] == "breakout"
    assert payload["meta"]["trendTf"] == "H1"  # khung trend = khung vào lệnh, không có khung tham chiếu riêng
    pane = payload["trendChart"]
    assert pane is not None and pane["tf"] == "H1" and "BREAKOUT" in pane["label"]  # pane trend RIÊNG, tách khỏi chart vào lệnh
    assert "sameTimeframe" not in pane
    assert [item["key"] for item in pane["overlays"]] == ["trend_breakout_high", "trend_breakout_low"]
    assert [item["label"] for item in pane["overlays"]] == ["Break High", "Break Low"]
    assert all(item["data"] for item in pane["overlays"])
    assert all("color" not in point for item in pane["overlays"] for point in item["data"])  # màu cố định theo đường
    assert isinstance(pane["markers"], list)  # dấu TREND UP/DOWN khi hướng đổi, như pane KNN
    assert {item["key"] for item in payload["overlays"]} == {"ma"}  # chart vào lệnh chỉ còn chỉ báo của chiến lược
    assert len(pane["candles"]) >= len(payload["candles"])
    assert [c["time"] for c in pane["candles"]][-len(payload["candles"]):] == [c["time"] for c in payload["candles"]]  # cùng nến, cùng khung


def test_run_scan_draws_the_trend_pane_with_heikin_ashi_candles_and_keeps_regular_candles_on_the_entry_chart(monkeypatch):
    monkeypatch.setattr(server, "load", _fake_load_h1_h4)
    seen = []
    real = server.heikin_ashi_points
    monkeypatch.setattr(server, "heikin_ashi_points", lambda df, **kw: (seen.append(df), real(df, **kw))[1])

    plain = server.run_scan(strategy="combo", symbol="US30", tf="H1", bars=300)
    payload = server.run_scan(strategy="combo", symbol="US30", tf="H1", bars=300, trend_mode="trend_filter", trend_methods="zigzag", trend_tf="H4")

    pane = payload["trendChart"]
    assert pane["candleStyle"] == "heikin_ashi" and len(seen) == 1  # chỉ pane trend dùng Heikin Ashi
    assert payload["candles"] == plain["candles"]  # chart vào lệnh: nến thường, giống hệt khi không bật trend
    regular = server.candlestick_points(seen[0], digits=2)  # nến thường của đúng khung trend đã vẽ
    assert [c["time"] for c in pane["candles"]] == [c["time"] for c in regular]  # cùng nến, cùng thời điểm
    assert pane["candles"] != regular and pane["candles"] == real(seen[0], digits=2)  # nhưng giá trị là nến Heikin Ashi
    # đường xu hướng vẫn tính trên giá thật: đỉnh/đáy thật nằm trong râu nến Heikin Ashi
    assert all(c["high"] >= r["high"] and c["low"] <= r["low"] for c, r in zip(pane["candles"], regular, strict=True))


def test_live_page_shows_the_heikin_ashi_marker_in_the_trend_pane_title():
    body = server.create_app().test_client().get("/").get_data(as_text=True)
    assert 'trendPayload.candleStyle === "heikin_ashi"' in body and "Heikin Ashi" in body


def test_run_scan_with_breakout_trend_on_ma_cross_m30_uses_m30(monkeypatch):
    seen = []

    def fake_load(symbol, tf, bars, *, before=None):
        _ = symbol, before
        seen.append(tf)
        return make_ohlcv(bars, freq_minutes=30, start="2026-01-01")

    monkeypatch.setattr(server, "load", fake_load)

    payload = server.run_scan(strategy="ma_cross", symbol="US30", tf="M30", bars=300, trend_mode="trend_filter", trend_methods="breakout")

    assert payload["meta"]["trendTf"] == "M30" and payload["trendChart"]["tf"] == "M30"
    assert set(seen) == {"M30"}  # chỉ tải đúng khung M30, không khung H1–H4 nào
    assert {item["key"] for item in payload["overlays"]} == {"fast_ma", "slow_ma"}  # chart vào lệnh chỉ còn hai SMA
    assert {item["key"] for item in payload["trendChart"]["overlays"]} == {"trend_breakout_high", "trend_breakout_low"}  # hai đường break ở pane trend


def test_run_scan_rejects_a_reference_timeframe_for_the_breakout_trend(monkeypatch):
    monkeypatch.setattr(server, "load", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("không được tải dữ liệu")))

    with pytest.raises(ValueError, match="always use the entry timeframe H1; TREND_TF H4 is not allowed"):
        server.run_scan(strategy="combo", symbol="US30", tf="H1", bars=300, trend_mode="trend_filter", trend_methods="breakout", trend_tf="H4")


def test_api_scan_route_accepts_the_trend_methods_param(monkeypatch):
    monkeypatch.setattr(server, "load", _fake_load_h1_h4)
    client = server.create_app().test_client()
    base = "/api/scan?strategy=combo&symbol=US30&tf=H1&bars=300&trend_mode=trend_filter"

    breakout_response = client.get(base + "&trend_methods=breakout")
    knn_response = client.get(base + "&trend_methods=knn&trend_tf=H4")
    mismatch = client.get(base + "&trend_methods=breakout&trend_tf=H4")
    unknown = client.get(base + "&trend_methods=nope&trend_tf=H4")

    assert breakout_response.status_code == 200
    assert breakout_response.json["meta"]["trendMethods"] == "breakout"
    assert breakout_response.json["trendChart"]["tf"] == "H1"  # pane trend riêng, cùng khung với tín hiệu
    assert {"trend_breakout_high", "trend_breakout_low"} <= {item["key"] for item in breakout_response.json["trendChart"]["overlays"]}
    assert {"trend_breakout_high", "trend_breakout_low"}.isdisjoint(item["key"] for item in breakout_response.json["overlays"])  # không vẽ lên chart vào lệnh
    assert knn_response.status_code == 200 and knn_response.json["trendChart"]["tf"] == "H4"  # knn vẫn có pane khung lớn
    assert mismatch.status_code == 400
    assert "entry timeframe" in mismatch.get_json()["error"]
    assert unknown.status_code == 400
    assert "unknown trend method" in unknown.get_json()["error"]


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
        "/api/scan?strategy=combo&symbol=US30&tf=H1&bars=300&trend_mode=trend_filter&trend_methods=knn&trend_tf=H4"
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
    assert "sma_trend" not in body and "SMA Trend" not in body   # đã xoá khỏi we_core 2026-10-16
    assert "LightweightCharts" in body
    assert "tunedSymbols" not in body          # khái niệm "symbol tune X riêng" đã bỏ cùng X (2026-10-21)
    assert '"recommendedTimeframes": ["H1", "H2", "H3", "H4"]' in body
    assert '"trendTimeframes": ["H1", "H2", "H3", "H4"]' in body
    assert 'id="trend-mode"' in body
    assert 'id="trend-method"' in body
    assert '"trendMethods": ["knn", "breakout", "zigzag", "tstat"]' in body  # các khối đã đăng ký, cho ô chọn phương pháp trend
    assert '"sameTimeframeTrendMethods": ["breakout"]' in body  # khối tính trên chính khung của tín hiệu: không có ô Trend TF
    assert 'params.set("trend_methods", el.trendMethod.value' in body
    assert 'if (!usesEntryTimeframe()) params.set("trend_tf"' in body
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


def _fake_symbols(*names):
    return {name: {"symbol_id": index, "label": name, "asset_type": "x"} for index, name in enumerate(names)}


def test_index_chi_liet_ke_symbol_van_hanh_khong_co_cap_forex_ngoai_vu_tru(monkeypatch):
    # DWH có cả forex nhưng worker/des chỉ chạy các symbol vận hành (OPERATING_SYMBOLS).
    monkeypatch.setattr(server, "OPERATING_SYMBOLS", ("US30", "BTCUSD"))
    monkeypatch.setattr(db_connector, "_symbols_cache", _fake_symbols("US30", "BTCUSD", "AUDCAD", "AUDJPY"))
    client = server.create_app().test_client()

    body = client.get("/").get_data(as_text=True)

    # Ô chọn symbol (khoá "symbols") chỉ liệt kê symbol vận hành (OPERATING_SYMBOLS); chuỗi EURUSD vẫn có thể
    # xuất hiện ở chỗ khác của trang (bảng khung trend theo symbol), nên chỉ kiểm danh sách.
    assert '"symbols": ["BTCUSD", "US30"]' in body


def test_api_scan_van_nhan_symbol_ngoai_vu_tru_van_hanh(monkeypatch):
    # Chỉ danh sách trên trang bị giới hạn; gọi thẳng bằng URL vẫn chạy như trước.
    monkeypatch.setattr(server, "OPERATING_SYMBOLS", ("US30",))
    monkeypatch.setattr(db_connector, "_symbols_cache", _fake_symbols("US30", "AUDCAD"))
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: make_ohlcv(300))
    client = server.create_app().test_client()

    response = client.get("/api/scan?strategy=macd_break&symbol=AUDCAD&tf=H1&bars=300")

    assert response.status_code == 200
    assert response.json["meta"]["symbol"] == "AUDCAD"
    assert response.json["meta"]["priceDigits"] == 2          # symbol ngoài vũ trụ vận hành: 2 chữ số như cũ


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


_SCAN_URL = "/api/scan?strategy=combo&symbol=US30&tf=H1&bars=300"


def test_api_scan_nen_gzip_khi_trinh_duyet_chap_nhan(monkeypatch):
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: make_ohlcv(300))
    client = server.create_app().test_client()

    plain = client.get(_SCAN_URL)
    packed = client.get(_SCAN_URL, headers={"Accept-Encoding": "gzip, deflate, br"})

    assert "Content-Encoding" not in plain.headers
    assert packed.headers["Content-Encoding"] == "gzip"
    assert packed.headers["Content-Length"] == str(len(packed.data))
    assert len(packed.data) < len(plain.data) / 3
    assert gzip.decompress(packed.data) == plain.data
    assert packed.mimetype == "application/json"


@pytest.mark.parametrize("accept_encoding", [None, "identity", "gzip;q=0", "deflate, br"])
def test_api_scan_khong_nen_khi_trinh_duyet_khong_nhan_gzip(monkeypatch, accept_encoding):
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: make_ohlcv(300))
    client = server.create_app().test_client()

    response = client.get(_SCAN_URL, headers={"Accept-Encoding": accept_encoding} if accept_encoding else {})

    assert "Content-Encoding" not in response.headers
    assert response.json["meta"]["symbol"] == "US30"
    # Bản thô và bản nén cùng URL nên cache trung gian phải phân biệt theo Accept-Encoding.
    assert "Accept-Encoding" in response.headers["Vary"]


def test_phan_hoi_nho_va_phan_hoi_loi_khong_nen_gzip():
    client = server.create_app().test_client()
    accepts_gzip = {"Accept-Encoding": "gzip"}

    health = client.get("/health", headers=accepts_gzip)
    bad_symbol = client.get("/api/scan?strategy=combo&symbol=NOPE&tf=H1", headers=accepts_gzip)

    assert "Content-Encoding" not in health.headers
    assert health.json == {"status": "ok"}
    assert bad_symbol.status_code == 400
    assert "Content-Encoding" not in bad_symbol.headers
    assert "error" in bad_symbol.json


def test_trang_chu_nen_gzip_con_csv_tai_ve_thi_khong(monkeypatch):
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: make_ohlcv(300))
    client = server.create_app().test_client()
    accepts_gzip = {"Accept-Encoding": "gzip"}

    index = client.get("/", headers=accepts_gzip)
    export = client.get("/api/export?strategy=combo&symbol=US30&tf=H1&bars=300", headers=accepts_gzip)

    assert index.headers["Content-Encoding"] == "gzip"
    assert "LightweightCharts" in gzip.decompress(index.data).decode()
    assert export.mimetype == "text/csv"
    assert "Content-Encoding" not in export.headers


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


def test_run_scan_nhan_co_alternating_tu_override_cua_dashboard(monkeypatch):
    """Ô "Alternating Filter" của form dashboard gửi ALTERNATING_FILTER_ENABLED=false: phải ra nhiều tín hiệu
    hơn mặc định (cờ trong config = bật) và đúng số của combo khi chưa có bộ lọc (31 trên dữ liệu mẫu)."""
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: make_ohlcv(300))

    default = server.run_scan(strategy="combo", symbol="US30", tf="H1", bars=300)
    off = server.run_scan(
        strategy="combo", symbol="US30", tf="H1", bars=300, overrides={"ALTERNATING_FILTER_ENABLED": "false"}
    )

    assert default["stats"]["total"] == 23
    assert off["stats"]["total"] == 31
    assert off["stats"]["total"] > default["stats"]["total"]


def test_worker_maps_each_timeframe_to_every_strategy_that_owns_it():
    from wisdom_engine.we_core.src.we_signal.redis_io.worker import (
        _strategies_for_timeframe,
    )

    assert _strategies_for_timeframe("H1") == ["combo", "macd_break"]
    assert _strategies_for_timeframe("M30") == ["ma_cross", "macd_break"]
    assert _strategies_for_timeframe("W") == []


def test_bootstrap_ghi_co_alternating_cua_tung_chien_luoc_vao_log(monkeypatch, caplog):
    """Đổi cờ làm đổi nội dung DB0/DB1 mà tên key không đổi: log bootstrap_start phải cho biết cờ đang áp dụng."""
    from wisdom_engine.we_core.src.we_signal.redis_io import worker

    monkeypatch.setattr(worker, "_safe_refresh_pair", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        worker, "_bootstrap_pairs", lambda: [("combo", "US30", "H1"), ("ma_cross", "US30", "M30"), ("macd_break", "US30", "H1")]
    )

    with caplog.at_level("INFO"):
        worker.bootstrap_all(None, key_prefix="x", trend_client=None, trend_key_prefix="y")

    start = next(record for record in caplog.records if getattr(record, "event_name", "") == "bootstrap_start")
    assert start.fields["alternating_combo"] is True
    assert start.fields["alternating_macd_break"] is True
    assert start.fields["alternating_ma_cross"] is True


# --- Quy tắc khung trend >= khung tín hiệu (chốt 2026-10-16) --------------------------------------


def test_api_scan_tra_400_khi_khung_trend_nho_hon_khung_tin_hieu_va_khong_tai_du_lieu(monkeypatch):
    def must_not_load(*args, **kwargs):
        raise AssertionError("không được tải dữ liệu khi khung trend không hợp lệ")

    monkeypatch.setattr(server, "load", must_not_load)
    monkeypatch.setattr(server, "load_range_with_warmup", must_not_load)
    client = server.create_app().test_client()

    response = client.get("/api/scan?strategy=combo&symbol=US30&tf=H2&bars=300&trend_mode=trend_filter&trend_methods=knn&trend_tf=H1")

    assert response.status_code == 400
    assert "must not be smaller than the entry timeframe H2" in response.json["error"]
    export = client.get("/api/export?strategy=combo&symbol=US30&tf=H2&bars=300&trend_mode=trend_filter&trend_methods=knn&trend_tf=H1")
    assert export.status_code == 400


def test_run_scan_cho_phep_khung_trend_bang_khung_tin_hieu(monkeypatch):
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars, *, before=None: make_ohlcv(bars, freq_minutes=60, seed=42))

    payload = server.run_scan(strategy="combo", symbol="US30", tf="H1", bars=300, trend_mode="trend_filter", trend_methods="knn", trend_tf="H1")

    assert payload["meta"]["trendTf"] == "H1"
    assert payload["trendChart"]["tf"] == "H1"


def test_run_scan_dung_khung_trend_theo_o_cua_combo_va_chay_voi_moi_khung_vao_lenh(monkeypatch):
    from wisdom_engine.we_core.src import configuration

    monkeypatch.setattr(configuration, "COMBO_TREND_TF_BY_SYMBOL", {"US30": {tf: "H4" for tf in ("H1", "H2", "H3", "H4")}})

    def fake_load(symbol, tf, bars, *, before=None):
        _ = symbol, before
        minutes = {"H1": 60, "H2": 120, "H3": 180, "H4": 240}[tf]
        return make_ohlcv(bars, freq_minutes=minutes, seed=minutes, start="2026-01-01")

    monkeypatch.setattr(server, "load", fake_load)

    for entry_tf in ("H1", "H2", "H3", "H4"):
        payload = server.run_scan(strategy="combo", symbol="US30", tf=entry_tf, bars=300, trend_mode="trend_filter", trend_methods="knn")
        assert payload["meta"]["trendTf"] == "H4"


def test_run_scan_bao_loi_ro_khi_khong_co_o_khung_trend_va_khong_chon_tay(monkeypatch):
    """Không còn khung trend mặc định chung: symbol/khung vào ngoài bảng mà không chọn Trend TF -> lỗi rõ TRƯỚC khi tải SQL."""
    from wisdom_engine.we_core.src import configuration

    monkeypatch.setattr(configuration, "COMBO_TREND_TF_BY_SYMBOL", {})

    def must_not_load(*args, **kwargs):
        raise AssertionError("không được tải dữ liệu khi chưa có khung trend")

    monkeypatch.setattr(server, "load", must_not_load)
    with pytest.raises(ValueError, match="No trend timeframe configured for US30 H1; choose Trend TF"):
        server.run_scan(strategy="combo", symbol="US30", tf="H1", bars=300, trend_mode="trend_filter", trend_methods="knn")
    response = server.create_app().test_client().get("/api/scan?strategy=combo&symbol=US30&tf=H1&bars=300&trend_mode=trend_filter&trend_methods=knn")
    assert response.status_code == 400
    assert "No trend timeframe configured" in response.get_json()["error"]


def test_trang_chu_chi_liet_ke_khung_trend_lon_hon_hoac_bang_khung_tin_hieu():
    """Phần JS không chạy được trong pytest: khoá nội dung trang. Đã kiểm hành vi bằng Chrome thật (CLAUDE.md, ghi chú 2026-10-16)."""
    body = server.create_app().test_client().get("/").get_data(as_text=True)

    assert "const entrySeconds = timeframeSeconds(el.tf.value);" in body
    assert "configured.filter((tf) => timeframeSeconds(tf) >= entrySeconds)" in body
    assert 'el.tf.addEventListener("change", () => { populateTrendControls(); scan(); });' in body
    assert "defaultTrendTf" not in body      # đã xoá khung trend mặc định chung (2026-10-16)


def test_worker_kiem_tra_khung_trend_mac_dinh_truoc_khi_chay_va_pass_voi_config_hien_tai():
    from wisdom_engine.we_core.src.we_signal.redis_io import worker

    assert worker.check_trend_timeframes() is None


def test_worker_kiem_tra_khung_trend_liet_ke_moi_cap_sai(monkeypatch):
    """Cấu hình sai (combo mặc định H1 nhưng worker chạy H1-H4) phải dừng worker trước khi chạm vào Redis."""
    from wisdom_engine.we_core.src import configuration
    from wisdom_engine.we_core.src.we_signal.redis_io import worker

    real_get_strategy = configuration.get_strategy

    class _CfgSai:
        def __init__(self, spec):
            self._spec = spec

        def normalize_params(self, overrides, symbol, tf):
            return {**self._spec.normalize_params(overrides, symbol, tf), "TREND_TF": "H1"}

    monkeypatch.setattr(worker, "get_strategy", lambda key: _CfgSai(real_get_strategy(key)) if key == "combo" else real_get_strategy(key))

    with pytest.raises(ValueError) as excinfo:
        worker.check_trend_timeframes()

    message = str(excinfo.value)
    assert "combo H2" in message and "combo H3" in message and "combo H4" in message
    assert "combo H1" not in message          # bằng nhau thì hợp lệ
    assert "ma_cross" not in message and "macd_break" not in message
    assert "BTCUSD" in message and "US30" in message          # liệt kê cả các symbol sai


def test_worker_main_dung_o_loi_cau_hinh_khung_trend_truoc_khi_xoa_redis(monkeypatch):
    from wisdom_engine.we_core.src.we_signal.redis_io import worker

    monkeypatch.setattr(worker.signal, "signal", lambda *args, **kwargs: None)   # main() không được đổi handler SIGTERM của pytest
    monkeypatch.setattr(worker, "configure_logging", lambda: ".")
    monkeypatch.setattr(worker, "load_config", lambda: {"redis": {"past_signal": {"db": 0, "key_prefix": "x"}}})
    monkeypatch.setattr(worker, "check_trend_timeframes", lambda: (_ for _ in ()).throw(ValueError("cấu hình khung trend sai")))

    def must_not_run(config):
        raise AssertionError("run_forever (xoá Redis + bootstrap) không được chạy khi cấu hình sai")

    monkeypatch.setattr(worker, "run_forever", must_not_run)

    assert worker.main() == 1


def test_worker_kiem_tra_khung_trend_chi_ra_dung_symbol_va_khung_sai_theo_o(monkeypatch):
    """Sai ở một ô trend_tf_by_symbol (chỉ BTCUSD vào H2) phải bị chỉ đích danh, các symbol còn lại không bị nêu."""
    from wisdom_engine.we_core.src import configuration
    from wisdom_engine.we_core.src.we_signal.redis_io import worker

    real_get_strategy = configuration.get_strategy

    class _OSai:
        def __init__(self, spec):
            self._spec = spec

        def normalize_params(self, overrides, symbol, tf):
            params = self._spec.normalize_params(overrides, symbol, tf)
            return {**params, "TREND_TF": "H1"} if (symbol, tf) == ("BTCUSD", "H2") else params

    monkeypatch.setattr(worker, "get_strategy", lambda key: _OSai(real_get_strategy(key)) if key == "combo" else real_get_strategy(key))

    with pytest.raises(ValueError) as excinfo:
        worker.check_trend_timeframes()

    message = str(excinfo.value)
    assert "combo H2" in message and "[BTCUSD]" in message
    assert "combo H3" not in message and "US30" not in message


def test_trang_chu_nhung_bang_khung_trend_theo_o_va_js_tra_khung_mac_dinh_theo_symbol_va_khung_vao_lenh(monkeypatch):
    monkeypatch.setitem(server.STRATEGIES["combo"].trend_tf_by_symbol, "ZZTEST", {"H2": "H3"})
    # symbol thử không nằm trong config nên thêm trực tiếp vào dict mà trang nhúng (chỉ để kiểm nhúng JSON)
    body = server.create_app().test_client().get("/").get_data(as_text=True)

    assert '"trendTfBySymbol"' in body
    assert '"ZZTEST": {"H2": "H3"}' in body
    assert "function configuredTrendTf(spec)" in body
    assert 'cells[el.tf.value] || ""' in body
    assert "defaultTrendTf" not in body
    assert 'el.symbol.addEventListener("change", () => { populateTrendControls(); scan(); });' in body


# --- Restart có chọn phạm vi (2026-10-20): xoá + tính lại chỉ các cặp được chọn --------------------------------


class _KeyspaceRedis:
    """Redis giả chỉ có những gì clear_pair_signals cần: scan_iter (glob) và delete."""

    def __init__(self, keys):
        self.keys = set(keys)

    def scan_iter(self, match, count=None):
        import fnmatch

        return iter([key for key in sorted(self.keys) if fnmatch.fnmatchcase(key, match)])

    def delete(self, *keys):
        found = [key for key in keys if key in self.keys]
        self.keys.difference_update(found)
        return len(found)


def test_clear_pair_signals_xoa_dung_mot_cap_khong_lan_sang_cap_giong_ten():
    from wisdom_engine.we_core.src.we_signal.redis_io.publisher import (
        clear_pair_signals,
    )

    mine = {"L_PastSignal_US30_H1_COMBO", "L_PastSignal_US30_H1_COMBO:2026-01-01 10:00:00", "L_PastSignal_US30_H1_COMBO:2026-01-01 11:00:00"}
    others = {
        "L_PastSignal_US300_H1_COMBO", "L_PastSignal_US300_H1_COMBO:2026-01-01 10:00:00",      # symbol bắt đầu giống
        "L_PastSignal_US30_H1_MACD_BREAK", "L_PastSignal_US30_H1_MACD_BREAK:2026-01-01 10:00:00",   # chiến lược khác
        "L_PastSignal_US30_H10_COMBO", "L_PastSignal_US30_H2_COMBO",                            # khung khác
        "L_PastSignal_Trend_US30_H1_COMBO",                                                     # tiền tố khác (DB1)
    }
    client = _KeyspaceRedis(mine | others)

    deleted = clear_pair_signals(client, key_prefix="L_PastSignal", symbol="us30", timeframe="h1", strategy="combo")

    assert deleted == 3
    assert client.keys == others


def test_clear_pair_signals_cap_chua_co_du_lieu_xoa_0_key():
    from wisdom_engine.we_core.src.we_signal.redis_io.publisher import (
        clear_pair_signals,
    )

    client = _KeyspaceRedis({"L_PastSignal_US30_H1_COMBO"})

    assert clear_pair_signals(client, key_prefix="L_PastSignal", symbol="EURUSD", timeframe="H1", strategy="combo") == 0
    assert client.keys == {"L_PastSignal_US30_H1_COMBO"}


def test_select_scope_pairs_khong_chon_gi_la_full_con_chon_thi_loc_dung_cap():
    from wisdom_engine.we_core.src.we_signal.redis_io import worker

    assert worker.select_scope_pairs() is None
    assert worker.select_scope_pairs([], [], []) is None
    assert worker.select_scope_pairs(["  "], None, None) is None

    one = worker.select_scope_pairs(["combo"], ["eurusd"], ["h1"])                  # không phân biệt hoa thường
    assert one == [("combo", "EURUSD", "H1")]

    forex = worker.select_scope_pairs(symbols=["EURUSD", "USDJPY", "GBPUSD", "USDCAD"])
    assert len(forex) == 4 * (4 + 4 + 8) and {symbol for _, symbol, _ in forex} == {"EURUSD", "USDJPY", "GBPUSD", "USDCAD"}
    assert {strategy for strategy, _, _ in forex} == {"combo", "ma_cross", "macd_break"}

    h1 = worker.select_scope_pairs(timeframes=["H1"])                                # H1: chỉ combo và macd_break chạy
    assert {strategy for strategy, _, _ in h1} == {"combo", "macd_break"} and len(h1) == 2 * len(worker.OPERATING_SYMBOLS)


def test_select_scope_pairs_gia_tri_la_va_to_hop_rong_bao_loi_ro():
    from wisdom_engine.we_core.src.we_signal.redis_io import worker

    with pytest.raises(ValueError, match=r"unknown strategy 'foo'.*unknown symbol 'XYZ'.*unknown timeframe 'W'"):
        worker.select_scope_pairs(["foo"], ["XYZ"], ["W"])
    with pytest.raises(ValueError, match="selects no pair"):
        worker.select_scope_pairs(strategies=["combo"], timeframes=["M10"])        # combo không chạy khung M10


def test_parse_scope_doc_tham_so_va_bien_moi_truong_tham_so_thang():
    from wisdom_engine.we_core.src.we_signal.redis_io import worker

    assert worker.parse_scope([], {}) is None
    assert worker.parse_scope([], {"WE_SCOPE_SYMBOL": "EURUSD", "WE_SCOPE_STRATEGY": "combo", "WE_SCOPE_TF": "H1,H2"}) == [
        ("combo", "EURUSD", "H1"), ("combo", "EURUSD", "H2"),
    ]
    assert worker.parse_scope(["--symbol", "USDJPY ; GBPUSD", "--strategy", "ma_cross", "--tf", "M10"], {"WE_SCOPE_SYMBOL": "EURUSD"}) == [
        ("ma_cross", "GBPUSD", "M10"), ("ma_cross", "USDJPY", "M10"),
    ]


def _fake_run_forever_setup(monkeypatch):
    """Dựng run_forever với Redis/listener giả; trả về (config, nhật ký các lần gọi)."""
    import threading

    from wisdom_engine.we_core.src.we_signal.redis_io import worker

    calls = {"clear_all": [], "clear_pair": [], "bootstrap": [], "listen": 0}

    class _Client:
        def ping(self):
            return True

        def close(self):
            pass

    monkeypatch.setattr(worker, "_SHUTDOWN", threading.Event())
    monkeypatch.setattr(worker, "_CLEARED_THIS_PROCESS", False)
    monkeypatch.setattr(worker, "create_client", lambda redis_config, db: _Client())
    monkeypatch.setattr(worker, "clear_all_signals", lambda client, *, key_prefix: calls["clear_all"].append(key_prefix) or 0)
    monkeypatch.setattr(
        worker, "clear_pair_signals",
        lambda client, *, key_prefix, symbol, timeframe, strategy: calls["clear_pair"].append((key_prefix, strategy, symbol, timeframe)) or 1,
    )
    monkeypatch.setattr(worker, "bootstrap_all", lambda *args, pairs=None, **kwargs: calls["bootstrap"].append(pairs))
    config = {
        "redis": {
            "reconnect_delay_seconds": 0,
            "backfill_event": {"channel": "c", "reconcile_interval_seconds": 1},
            "past_signal": {"db": 0, "key_prefix": "P"},
            "past_signal_trend": {"db": 1, "key_prefix": "PT"},
        }
    }
    return worker, config, calls


def test_run_forever_full_giu_nguyen_xoa_sach_ca_hai_db_va_bootstrap_moi_cap(monkeypatch):
    worker, config, calls = _fake_run_forever_setup(monkeypatch)
    monkeypatch.setattr(worker, "listen_for_backfill_events", lambda *args, **kwargs: worker._SHUTDOWN.set())

    worker.run_forever(config)

    assert calls["clear_all"] == ["P", "PT"] and calls["clear_pair"] == []
    assert calls["bootstrap"] == [None]


def test_run_forever_co_pham_vi_chi_xoa_va_bootstrap_cap_duoc_chon_roi_noi_lai_redis_thi_tinh_lai_tat_ca(monkeypatch):
    import redis

    worker, config, calls = _fake_run_forever_setup(monkeypatch)
    listens = []

    def listen(*args, **kwargs):
        listens.append(1)
        if len(listens) == 1:
            raise redis.ConnectionError("rớt kết nối")   # lần nối lại: phạm vi hết hiệu lực
        worker._SHUTDOWN.set()

    monkeypatch.setattr(worker, "listen_for_backfill_events", listen)
    scope = [("combo", "EURUSD", "H1"), ("ma_cross", "EURUSD", "M10")]

    worker.run_forever(config, scope)

    assert calls["clear_all"] == []                                    # không xoá full
    assert calls["clear_pair"] == [                                    # DB0 (P) và DB1 (PT) của từng cặp được chọn, và chỉ một lần
        ("P", "combo", "EURUSD", "H1"), ("PT", "combo", "EURUSD", "H1"),
        ("P", "ma_cross", "EURUSD", "M10"), ("PT", "ma_cross", "EURUSD", "M10"),
    ]
    assert calls["bootstrap"] == [scope, None]                         # lượt đầu theo phạm vi, sau khi nối lại: mọi cặp


def test_worker_main_pham_vi_sai_dung_truoc_khi_chay_va_pham_vi_dung_duoc_truyen_vao_run_forever(monkeypatch):
    from wisdom_engine.we_core.src.we_signal.redis_io import worker

    monkeypatch.setattr(worker.signal, "signal", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "configure_logging", lambda: ".")
    monkeypatch.setattr(worker, "load_config", lambda: {"redis": {"past_signal": {"db": 0, "key_prefix": "x"}}})
    monkeypatch.setattr(worker, "check_trend_timeframes", lambda: None)
    for name in worker.SCOPE_ENV_VARS.values():
        monkeypatch.delenv(name, raising=False)
    runs = []
    monkeypatch.setattr(worker, "run_forever", lambda config, scope_pairs=None: runs.append(scope_pairs))

    assert worker.main(["--symbol", "NOPE"]) == 1 and runs == []        # lỗi cấu hình: không chạm Redis
    assert worker.main([]) == 0 and runs == [None]                      # không chọn: FULL như cũ
    assert worker.main(["--symbol", "EURUSD", "--strategy", "combo", "--tf", "H1"]) == 0
    assert runs[-1] == [("combo", "EURUSD", "H1")]


# --- Đầu ra trend THÊM (redis.extra_trend_outputs, 2026-10-23): db2 dùng breakout song song DB0/DB1 ------------------


_REDIS_BASE = {
    "backfill_event": {"channel": "c"},
    "past_signal": {"db": 0, "key_prefix": "P"},
    "past_signal_trend": {"db": 1, "key_prefix": "PT"},
}


def test_extra_trend_outputs_mac_dinh_rong_va_khong_dong_toi_db0_db1():
    from wisdom_engine.we_core.src.we_signal.redis_io.client import (
        normalize_redis_config,
    )

    for raw in (None, []):
        redis_cfg = dict(_REDIS_BASE) if raw is None else {**_REDIS_BASE, "extra_trend_outputs": raw}
        out = normalize_redis_config(redis_cfg)
        assert out["extra_trend_outputs"] == []
        assert out["past_signal"] == {"db": 0, "key_prefix": "P"} and out["past_signal_trend"] == {"db": 1, "key_prefix": "PT"}


def test_extra_trend_outputs_chuan_hoa_va_chuan_hoa_lai_van_ra_dung_no():
    from wisdom_engine.we_core.src.we_signal.redis_io.client import (
        normalize_redis_config,
    )

    raw = [{"db": 2, "key_prefix": " PX ", "trend_methods": "BreakOut"}, {"db": 3, "key_prefix": "PK", "trend_methods": ["knn"]}]
    out = normalize_redis_config({**_REDIS_BASE, "extra_trend_outputs": raw})["extra_trend_outputs"]

    assert out == [{"db": 2, "key_prefix": "PX", "trend_methods": ("breakout",)}, {"db": 3, "key_prefix": "PK", "trend_methods": ("knn",)}]
    assert normalize_redis_config(normalize_redis_config({**_REDIS_BASE, "extra_trend_outputs": raw}))["extra_trend_outputs"] == out  # create_client chuẩn hoá lần hai


def test_extra_trend_outputs_bao_loi_ro_rang_kem_duong_dan_config():
    import pytest
    from wisdom_engine.we_core.src.we_signal.redis_io.client import (
        normalize_redis_config,
    )

    def run(extra):
        return normalize_redis_config({**_REDIS_BASE, "extra_trend_outputs": extra})

    good = {"db": 2, "key_prefix": "PX", "trend_methods": ["breakout"]}
    with pytest.raises(TypeError, match="must be a list"):
        run({"db": 2})
    with pytest.raises(TypeError, match=r"extra_trend_outputs\[0\] must be a mapping"):
        run(["db2"])
    for key in ("db", "key_prefix", "trend_methods"):
        with pytest.raises(KeyError, match=rf"extra_trend_outputs\[1\]\.{key}"):
            run([good, {k: v for k, v in good.items() if k != key}])
    for bad_db in (-1, "2", 2.0, True):
        with pytest.raises(ValueError, match=r"extra_trend_outputs\[0\]\.db must be a non-negative integer"):
            run([{**good, "db": bad_db}])
    with pytest.raises(ValueError, match=r"key_prefix must not be empty"):
        run([{**good, "key_prefix": "  "}])
    for taken in (0, 1):  # trùng DB0 hoặc DB1
        with pytest.raises(ValueError, match=rf"db {taken} is already used by another WE output"):
            run([{**good, "db": taken}])
    with pytest.raises(ValueError, match=r"extra_trend_outputs\[1\]\.db 2 is already used"):  # trùng đầu ra thêm trước đó
        run([good, {**good, "key_prefix": "PY"}])
    with pytest.raises(ValueError, match=r"extra_trend_outputs\[0\]\.trend_methods has unknown trend method\(s\): ema"):
        run([{**good, "trend_methods": ["ema"]}])
    with pytest.raises(ValueError, match="at least one trend method"):
        run([{**good, "trend_methods": []}])


def _spy_refresh_pair(monkeypatch, *, trend_bars_marker="trend-bars"):
    """refresh_pair với SQL/tính toán/ghi Redis giả; trả về (worker, nhật ký). Mỗi lần gọi ghi lại đủ để so DB0/DB1/đầu ra thêm."""
    import pandas as pd
    from wisdom_engine.we_core.src.we_signal.redis_io import worker

    raw = pd.DataFrame({"bartime": pd.date_range("2026-01-05", periods=3, freq="60min"), "close": [1.0, 2.0, 3.0]})
    log = {"raw": raw, "loads": [], "warmups": [], "run_strategy": [], "run_trend": [], "publish": []}

    def fake_load(symbol, timeframe, date_from, date_to, warmup):
        log["loads"].append((symbol, timeframe))
        log["warmups"].append((symbol, timeframe, warmup))
        return raw if timeframe == "H1" else pd.DataFrame({"bartime": [pd.Timestamp("2026-01-01")], "close": [trend_bars_marker]})

    def fake_run_strategy(key, *, symbol, tf, bars, overrides=None):
        log["run_strategy"].append((key, symbol, tf, bars is raw, overrides))
        return "db0-frame"

    def fake_run_trend(key, *, symbol, entry_tf, entry_bars, trend_tf, trend_bars, overrides=None):
        log["run_trend"].append((key, symbol, entry_tf, entry_bars is raw, trend_tf, trend_bars is raw, overrides))
        return f"trend-frame:{trend_tf}:{(overrides or {}).get('TREND_METHODS', 'default')}"

    def fake_publish(client, *, key_prefix, symbol, timeframe, strategy, enriched, effective_from, delta_mode, window_to):
        log["publish"].append((client, key_prefix, strategy, enriched, delta_mode, window_to))
        return 7

    monkeypatch.setattr(worker, "load_range_with_warmup", fake_load)
    monkeypatch.setattr(worker, "run_strategy", fake_run_strategy)
    monkeypatch.setattr(worker, "run_strategy_with_trend_reference", fake_run_trend)
    monkeypatch.setattr(worker, "_compute_and_publish", fake_publish)
    return worker, log


def test_refresh_pair_ghi_them_dau_ra_trend_rieng_va_db0_db1_giu_nguyen(monkeypatch):
    worker, log = _spy_refresh_pair(monkeypatch)
    c0, c1, c2 = "client0", "client1", "client2"
    common = {"symbol": "US30", "timeframe": "H1", "key_prefix": "P", "trend_client": c1, "trend_key_prefix": "PT", "strategy_keys": ["combo"]}

    without = worker.refresh_pair(c0, **common)
    publish_without, loads_without = list(log["publish"]), list(log["loads"])
    log["publish"].clear(), log["loads"].clear(), log["run_trend"].clear()
    extra = worker.TrendOutput(client=c2, key_prefix="PX", trend_methods=("breakout",), label="db2:breakout")
    with_extra = worker.refresh_pair(c0, extra_outputs=[extra], **common)

    assert without == 14 and with_extra == 21                                  # DB0 + DB1 như cũ, cộng thêm đầu ra thêm
    assert log["publish"][:2] == publish_without                               # DB0 và DB1: cùng client, tiền tố, dữ liệu như khi không có đầu ra thêm
    assert [item[:2] for item in log["publish"]] == [(c0, "P"), (c1, "PT"), (c2, "PX")]
    assert log["publish"][2][3] == "trend-frame:H1:breakout"                    # đầu ra thêm chạy breakout, ghi vào client/tiền tố của nó
    default_call, extra_call = log["run_trend"]
    assert default_call[-1] is None and default_call[4] != "" and extra_call[-1] == {"TREND_METHODS": "breakout"}   # DB1 giữ khối theo config, không bị đè
    # breakout cùng khung với tín hiệu: dùng lại chính nến vào lệnh, KHÔNG tải thêm nến trend
    assert extra_call[3] is True and extra_call[5] is True and extra_call[4] == "H1"
    assert len(log["loads"]) == len(loads_without)


def test_refresh_pair_dau_ra_them_dung_khung_tham_chieu_rieng_khi_khoi_can_khung_lon_hon(monkeypatch):
    worker, log = _spy_refresh_pair(monkeypatch)
    extra = worker.TrendOutput(client="c2", key_prefix="PX", trend_methods=("knn",), label="db2:knn")

    worker.refresh_pair("c0", symbol="US30", timeframe="H1", key_prefix="P", trend_client="c1", trend_key_prefix="PT", strategy_keys=["combo"], extra_outputs=[extra])

    extra_call = log["run_trend"][-1]
    assert extra_call[-1] == {"TREND_METHODS": "knn"} and extra_call[4] in ("H1", "H2", "H3", "H4")   # khung tra theo trend_tf_by_symbol
    if extra_call[4] != "H1":
        assert extra_call[5] is False and ("US30", extra_call[4]) in log["loads"]                       # tải nến khung đó, không dùng nến vào lệnh


def test_refresh_pair_nen_trend_dung_warmup_rieng_cua_khoi_va_nen_vao_lenh_giu_warmup_chung(monkeypatch):
    from wisdom_engine.we_core.src.configuration import WARMUP_BARS
    from wisdom_engine.we_core.src.rules import trend_tf

    # Mặc định (knn, breakout không khai báo warm-up riêng): mọi lần tải đều dùng warm-up chung -- hành vi y như trước.
    worker, log = _spy_refresh_pair(monkeypatch)
    extra = worker.TrendOutput(client="c2", key_prefix="PX", trend_methods=("knn",), label="db2:knn")
    worker.refresh_pair("c0", symbol="US30", timeframe="H1", key_prefix="P", trend_client="c1", trend_key_prefix="PT", strategy_keys=["combo"], extra_outputs=[extra])
    assert {warmup for _, _, warmup in log["warmups"]} == {WARMUP_BARS}

    # Khối đòi warm-up dài hơn (ô POLICY.trend_warmup_bars): CHỈ nến trend của đầu ra dùng khối đó tải dài hơn; nến vào lệnh vẫn WARMUP_BARS.
    long_warmup = WARMUP_BARS * 5
    monkeypatch.setitem(trend_tf.POLICY, "knn", trend_tf.MethodPolicy(trend_warmup_bars=long_warmup))
    worker, log = _spy_refresh_pair(monkeypatch)
    worker.refresh_pair("c0", symbol="US30", timeframe="H1", key_prefix="P", trend_client="c1", trend_key_prefix="PT", strategy_keys=["combo"])   # DB1 theo config = knn
    entry_loads = [item for item in log["warmups"] if item[1] == "H1"]
    trend_loads = [item for item in log["warmups"] if item[1] != "H1"]
    assert entry_loads == [("US30", "H1", WARMUP_BARS)]
    assert trend_loads and all(item[2] == long_warmup for item in trend_loads)


def test_refresh_pair_dau_ra_them_khong_dung_lai_nen_vao_lenh_khi_khoi_cung_khung_dong_warmup_dai(monkeypatch):
    from wisdom_engine.we_core.src.configuration import WARMUP_BARS
    from wisdom_engine.we_core.src.rules import trend_tf

    long_warmup = WARMUP_BARS * 5
    extra_args = {"symbol": "US30", "timeframe": "H1", "key_prefix": "P", "trend_client": "c1", "trend_key_prefix": "PT", "strategy_keys": ["combo"]}
    # breakout cùng khung với tín hiệu: bình thường dùng lại `raw`, không tải nến trend
    worker, log = _spy_refresh_pair(monkeypatch)
    extra = worker.TrendOutput(client="c2", key_prefix="PX", trend_methods=("breakout",), label="db2:breakout")
    worker.refresh_pair("c0", extra_outputs=[extra], **extra_args)
    assert log["run_trend"][-1][5] is True and log["warmups"].count(("US30", "H1", WARMUP_BARS)) == 1
    # nếu khối đòi warm-up dài hơn cái `raw` đã có thì phải tải riêng nến trend với warm-up đó, dù cùng khung
    monkeypatch.setitem(trend_tf.POLICY, "breakout", trend_tf.MethodPolicy(same_timeframe=True, first_aligned_per_run=True, trend_warmup_bars=long_warmup))
    worker, log = _spy_refresh_pair(monkeypatch)
    worker.refresh_pair("c0", extra_outputs=[extra], **extra_args)
    # (loader giả trả lại `raw` cho khung H1 nên không so `is raw`; điều cần khoá là CÓ một lần tải riêng với warm-up dài)
    assert ("US30", "H1", long_warmup) in log["warmups"] and log["warmups"].count(("US30", "H1", WARMUP_BARS)) == 1


def test_refresh_pair_dau_ra_them_cung_che_do_delta_voi_db0_db1(monkeypatch):
    worker, log = _spy_refresh_pair(monkeypatch)
    extra = worker.TrendOutput(client="c2", key_prefix="PX", trend_methods=("breakout",), label="db2:breakout")

    worker.refresh_pair(
        "c0", symbol="US30", timeframe="H1", key_prefix="P", trend_client="c1", trend_key_prefix="PT", strategy_keys=["macd_break"],
        date_from="2026-01-05 00:00:00", date_to="2026-01-05 02:00:00", extra_outputs=[extra],
    )

    assert [item[4] for item in log["publish"]] == [True, True, True]          # cả ba đều ghép delta, không có đầu ra nào full-recompute
    assert {item[5] for item in log["publish"]} == {log["publish"][0][5]} and log["publish"][0][5] is not None


def test_bootstrap_all_va_on_update_truyen_extra_outputs_xuong_refresh(monkeypatch):
    from wisdom_engine.we_core.src.we_signal.redis_io import worker

    seen = []
    monkeypatch.setattr(worker, "_safe_refresh_pair", lambda *args, **kwargs: seen.append(kwargs))
    extra = worker.TrendOutput(client=None, key_prefix="PX", trend_methods=("breakout",), label="db2:breakout")

    worker.bootstrap_all(None, key_prefix="x", trend_client=None, trend_key_prefix="y", pairs=[("combo", "US30", "H1")], extra_outputs=[extra])
    worker._make_on_update(None, key_prefix="x", trend_client=None, trend_key_prefix="y", extra_outputs=[extra])("H1", "US30", None, None)
    worker.bootstrap_all(None, key_prefix="x", trend_client=None, trend_key_prefix="y", pairs=[("combo", "US30", "H1")])

    assert seen[0]["extra_outputs"] == [extra] and seen[1]["extra_outputs"] == [extra]
    assert seen[2]["extra_outputs"] == ()                                      # không khai thì rỗng: đường DB0/DB1 y như cũ


def test_run_forever_xoa_va_bootstrap_ca_dau_ra_trend_them(monkeypatch):
    import redis

    worker, config, calls = _fake_run_forever_setup(monkeypatch)
    config["redis"]["extra_trend_outputs"] = [{"db": 2, "key_prefix": "PX", "trend_methods": ("breakout",)}]
    boots = []
    monkeypatch.setattr(worker, "bootstrap_all", lambda *args, pairs=None, **kwargs: boots.append((pairs, [o.label for o in kwargs["extra_outputs"]])))
    listens = []

    def listen(*args, **kwargs):
        listens.append(1)
        if len(listens) == 1:
            raise redis.ConnectionError("rớt kết nối")
        worker._SHUTDOWN.set()

    monkeypatch.setattr(worker, "listen_for_backfill_events", listen)

    worker.run_forever(config)                                                  # restart FULL
    assert calls["clear_all"] == ["P", "PT", "PX"] and calls["clear_pair"] == []   # xoá sạch cả ba DB, đúng một lần dù nối lại Redis
    assert boots == [(None, ["db2:breakout"]), (None, ["db2:breakout"])]

    worker, config, calls = _fake_run_forever_setup(monkeypatch)
    config["redis"]["extra_trend_outputs"] = [{"db": 2, "key_prefix": "PX", "trend_methods": ("breakout",)}]
    monkeypatch.setattr(worker, "listen_for_backfill_events", lambda *args, **kwargs: worker._SHUTDOWN.set())
    scope = [("combo", "EURUSD", "H1")]
    worker.run_forever(config, scope)                                           # restart có phạm vi
    assert calls["clear_all"] == []
    assert sorted(calls["clear_pair"]) == sorted([("P", "combo", "EURUSD", "H1"), ("PT", "combo", "EURUSD", "H1"), ("PX", "combo", "EURUSD", "H1")])


def test_run_forever_ping_va_dong_client_cua_dau_ra_trend_them(monkeypatch):
    worker, config, _ = _fake_run_forever_setup(monkeypatch)
    config["redis"]["extra_trend_outputs"] = [{"db": 2, "key_prefix": "PX", "trend_methods": ("breakout",)}]
    made: dict[int, list] = {}

    class _Recorder:
        def __init__(self):
            self.pinged = self.closed = 0

        def ping(self):
            self.pinged += 1
            return True

        def close(self):
            self.closed += 1

    def make(redis_config, db):
        made.setdefault(db, []).append(_Recorder())
        return made[db][-1]

    monkeypatch.setattr(worker, "create_client", make)
    monkeypatch.setattr(worker, "listen_for_backfill_events", lambda *args, **kwargs: worker._SHUTDOWN.set())

    worker.run_forever(config)

    assert sorted(made) == [0, 1, 2] and len(made[2]) == 1
    assert all(client.pinged == 1 and client.closed == 1 for clients in made.values() for client in clients)   # kể cả client của đầu ra thêm


def test_check_extra_trend_outputs_chap_nhan_breakout_va_knn_va_gom_moi_loi(monkeypatch):
    import pytest
    from wisdom_engine.we_core.src.we_signal.redis_io import worker

    worker.check_extra_trend_outputs({})                                         # không khai: không làm gì
    worker.check_extra_trend_outputs({"extra_trend_outputs": []})
    for methods in (("breakout",), ("knn",)):
        worker.check_extra_trend_outputs({"extra_trend_outputs": [{"db": 2, "key_prefix": "PX", "trend_methods": methods}]})
    real = worker.check_trend_timeframe

    def picky(entry_tf, trend_tf):
        if entry_tf in ("H1", "M10"):
            raise ValueError("khung trend nhỏ hơn khung vào lệnh")
        return real(entry_tf, trend_tf)

    monkeypatch.setattr(worker, "check_trend_timeframe", picky)
    with pytest.raises(ValueError) as caught:
        worker.check_extra_trend_outputs({"extra_trend_outputs": [{"db": 2, "key_prefix": "PX", "trend_methods": ("breakout",)}]})
    message = str(caught.value)
    assert "db2:breakout" in message and "combo H1" in message and "macd_break M10" in message and "khung trend nhỏ hơn" in message


def test_worker_main_dung_truoc_khi_xoa_gi_khi_dau_ra_trend_them_khai_sai(monkeypatch):
    from wisdom_engine.we_core.src.we_signal.redis_io import worker

    monkeypatch.setattr(worker.signal, "signal", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "configure_logging", lambda: ".")
    monkeypatch.setattr(worker, "load_config", lambda: {"redis": {"past_signal": {"db": 0, "key_prefix": "x"}}})
    monkeypatch.setattr(worker, "check_trend_timeframes", lambda: None)
    for name in worker.SCOPE_ENV_VARS.values():
        monkeypatch.delenv(name, raising=False)

    def broken(redis_config):
        raise ValueError("redis.extra_trend_outputs cannot run")

    monkeypatch.setattr(worker, "check_extra_trend_outputs", broken)
    runs = []
    monkeypatch.setattr(worker, "run_forever", lambda config, scope_pairs=None: runs.append(scope_pairs))

    assert worker.main([]) == 1 and runs == []                                  # lỗi cấu hình: dừng khi Redis còn nguyên


def test_config_mau_khai_bao_dau_ra_trend_them_db2_breakout_db3_tstat_va_db4_zigzag():
    from pathlib import Path

    import yaml
    from wisdom_engine.we_core.src.we_signal.redis_io import worker
    from wisdom_engine.we_core.src.we_signal.redis_io.client import (
        normalize_redis_config,
    )

    example = yaml.safe_load((Path(__file__).resolve().parents[1] / "we_config.example.yaml").read_text(encoding="utf-8"))
    redis_cfg = normalize_redis_config(example["redis"])

    assert redis_cfg["extra_trend_outputs"] == [
        {"db": 2, "key_prefix": "L_PastSignal_Breakout", "trend_methods": ("breakout",)},
        {"db": 3, "key_prefix": "L_PastSignal_Tstat", "trend_methods": ("tstat",)},
        {"db": 4, "key_prefix": "L_PastSignal_Zigzag", "trend_methods": ("zigzag",)},
    ]
    worker.check_extra_trend_outputs(redis_cfg)                                # chạy được với mọi (chiến lược, symbol, khung vào lệnh) của worker


# --- Forex trên dashboard (2026-10-20): số chữ số theo symbol + ô chọn symbol ------------------------------------


def _forex_bars(n=300, base=1.1600, seed=3):
    """Nến giá ~1,16 có biên độ vài pip -- làm tròn 2 chữ số sẽ ra đường phẳng."""
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    close = base + np.cumsum(rng.normal(0, 0.0008, n))
    open_ = np.concatenate([[base], close[:-1]])
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.0003, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.0003, n))
    return pd.DataFrame({"bartime": pd.date_range("2026-01-01", periods=n, freq="60min"),
                         "open": open_, "high": high, "low": low, "close": close, "volume": 1.0})


def test_api_scan_forex_giu_5_chu_so_chart_khong_phang_con_us30_van_2(monkeypatch):
    monkeypatch.setattr(db_connector, "_symbols_cache", _fake_symbols("US30", "EURUSD", "USDJPY"))
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: _forex_bars() if symbol == "EURUSD" else make_ohlcv(300))
    client = server.create_app().test_client()

    eur = client.get("/api/scan?strategy=combo&symbol=EURUSD&tf=H1&bars=300").json
    us30 = client.get("/api/scan?strategy=combo&symbol=US30&tf=H1&bars=300").json

    assert eur["meta"]["priceDigits"] == 5 and us30["meta"]["priceDigits"] == 2
    closes = [c["close"] for c in eur["candles"]]
    assert len(set(closes)) > 250                                           # 2 chữ số sẽ chỉ còn vài giá trị khác nhau
    assert all(abs(c["close"] * 1e5 - round(c["close"] * 1e5)) < 1e-6 for c in eur["candles"])    # tối đa 5 chữ số
    assert any(len(str(c["close"]).split(".")[1]) > 2 for c in eur["candles"])
    assert all(len(str(c["close"]).split(".")[1]) <= 2 for c in us30["candles"])
    ma = next(o for o in eur["overlays"] if o["key"] == "ma")["data"]
    assert len({p["value"] for p in ma}) > 250                              # đường MA cũng giữ 5 chữ số
    macd = eur["panels"][0]["data"]
    assert any(abs(p["value"]) > 0 for p in macd)                           # MACD ~1e-4: 2 chữ số sẽ ra toàn 0


def test_api_scan_forex_bang_tin_hieu_va_csv_dung_5_chu_so_con_jpy_3(monkeypatch):
    monkeypatch.setattr(db_connector, "_symbols_cache", _fake_symbols("US30", "EURUSD", "USDJPY"))
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars: _forex_bars())
    client = server.create_app().test_client()

    scan = client.get("/api/scan?strategy=combo&symbol=EURUSD&tf=H1&bars=300").json
    csv = client.get("/api/export?strategy=combo&symbol=EURUSD&tf=H1&bars=300")

    assert scan["signals"], "dữ liệu thử phải có tín hiệu combo"
    assert any(len(str(row["entry"]).split(".")[1]) > 2 for row in scan["signals"])
    header, *rows = csv.get_data(as_text=True).strip().splitlines()
    assert header == "bartime,atr,entry,signal"
    assert any(len(row.split(",")[2].split(".")[1]) > 2 for row in rows)
    jpy = client.get("/api/scan?strategy=combo&symbol=USDJPY&tf=H1&bars=300").json
    assert jpy["meta"]["priceDigits"] == 3


def test_o_chon_symbol_gom_ca_4_cap_forex_van_hanh_cho_ca_ba_chien_luoc(monkeypatch):
    monkeypatch.setattr(db_connector, "_symbols_cache", _fake_symbols("US30", "EURUSD", "USDJPY", "GBPUSD", "USDCAD", "AUDCAD"))
    client = server.create_app().test_client()

    body = client.get("/").get_data(as_text=True)

    assert '"symbols": ["EURUSD", "GBPUSD", "US30", "USDCAD", "USDJPY"]' in body      # AUDCAD (không vận hành) bị loại
    assert "const allowed = CONFIG.symbols;" in body                                  # ô chọn mọi chiến lược dùng đúng danh sách này


def test_trang_dashboard_dat_dinh_dang_gia_theo_so_chu_so_cua_symbol():
    html = server.create_app().test_client().get("/").get_data(as_text=True)

    assert "priceDigits" in html and "applyOptions({ priceFormat: priceFormat() })" in html
    assert html.count("priceFormat: priceFormat()") >= 2        # cả hai nhóm đường overlay (giá và trend)


def test_run_scan_with_tstat_trend_uses_the_reference_timeframe_and_draws_the_fit_line(monkeypatch):
    monkeypatch.setattr(server, "load", _fake_load_h1_h4)

    payload = server.run_scan(strategy="combo", symbol="US30", tf="H1", bars=300, trend_mode="trend_filter", trend_methods="tstat", trend_tf="H4")

    assert payload["meta"]["trendMethods"] == "tstat" and payload["meta"]["trendTf"] == "H4"  # khung tham chiếu riêng như knn/zigzag
    pane = payload["trendChart"]
    assert pane is not None and pane["tf"] == "H4" and "TSTAT" in pane["label"].upper()
    assert [item["key"] for item in pane["overlays"]] == ["trend_tstat_fit"]
    line = pane["overlays"][0]
    assert line["label"] == "T-stat Trend Fit" and line["data"]
    assert any("color" in point for point in line["data"])  # tô màu theo hướng của chính khối
    assert {item["key"] for item in payload["overlays"]} == {"ma"}  # chart vào lệnh chỉ còn chỉ báo của chiến lược


def test_live_page_lists_the_tstat_block_in_the_trend_method_choices():
    body = server.create_app().test_client().get("/").get_data(as_text=True)
    assert '"tstat"' in body and "tstat" in body.split('"trendMethods":')[1].split("]")[0]
