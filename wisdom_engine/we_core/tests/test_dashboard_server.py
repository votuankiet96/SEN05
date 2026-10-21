"""Tests for the signal_display live dashboard server without a real SQL Server."""

from __future__ import annotations

import gzip

import pytest
from wisdom_engine.we_core.src import db_connector
from wisdom_engine.we_core.src.signal_display import cli, server

from tests.fixtures import make_ohlcv


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
    assert payload["meta"]["trendMethods"] == "knn"
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
    assert "sma_trend" not in body and "SMA Trend" not in body   # đã xoá khỏi we_core 2026-10-16
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

    response = client.get("/api/scan?strategy=combo&symbol=US30&tf=H2&bars=300&trend_mode=trend_filter&trend_tf=H1")

    assert response.status_code == 400
    assert "must not be smaller than the entry timeframe H2" in response.json["error"]
    export = client.get("/api/export?strategy=combo&symbol=US30&tf=H2&bars=300&trend_mode=trend_filter&trend_tf=H1")
    assert export.status_code == 400


def test_run_scan_cho_phep_khung_trend_bang_khung_tin_hieu(monkeypatch):
    monkeypatch.setattr(server, "load", lambda symbol, tf, bars, *, before=None: make_ohlcv(bars, freq_minutes=60, seed=42))

    payload = server.run_scan(strategy="combo", symbol="US30", tf="H1", bars=300, trend_mode="trend_filter", trend_tf="H1")

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
        payload = server.run_scan(strategy="combo", symbol="US30", tf=entry_tf, bars=300, trend_mode="trend_filter")
        assert payload["meta"]["trendTf"] == "H4"


def test_run_scan_bao_loi_ro_khi_khong_co_o_khung_trend_va_khong_chon_tay(monkeypatch):
    """Không còn khung trend mặc định chung: symbol/khung vào ngoài bảng mà không chọn Trend TF -> lỗi rõ TRƯỚC khi tải SQL."""
    from wisdom_engine.we_core.src import configuration

    monkeypatch.setattr(configuration, "COMBO_TREND_TF_BY_SYMBOL", {})

    def must_not_load(*args, **kwargs):
        raise AssertionError("không được tải dữ liệu khi chưa có khung trend")

    monkeypatch.setattr(server, "load", must_not_load)
    with pytest.raises(ValueError, match="No trend timeframe configured for US30 H1; choose Trend TF"):
        server.run_scan(strategy="combo", symbol="US30", tf="H1", bars=300, trend_mode="trend_filter")
    response = server.create_app().test_client().get("/api/scan?strategy=combo&symbol=US30&tf=H1&bars=300&trend_mode=trend_filter")
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
    combo_tuned = body.split('"combo"', 1)[1].split('"tunedSymbols":', 1)[1].split("]", 1)[0]
    assert all(symbol in combo_tuned for symbol in ("EURUSD", "USDJPY", "GBPUSD", "USDCAD"))


def test_trang_dashboard_dat_dinh_dang_gia_theo_so_chu_so_cua_symbol():
    html = server.create_app().test_client().get("/").get_data(as_text=True)

    assert "priceDigits" in html and "applyOptions({ priceFormat: priceFormat() })" in html
    assert html.count("priceFormat: priceFormat()") >= 2        # cả hai nhóm đường overlay (giá và trend)
