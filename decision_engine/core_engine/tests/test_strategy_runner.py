"""Test strategy_runner.py: chạy MỘT chiến lược cho một cặp (bảng nến vào, bảng tín hiệu ra; có hoặc không bảng xu hướng) và ghi tín hiệu theo List–Hash (RawSignalWriter) — hermetic."""

from __future__ import annotations

import json
import logging
from typing import Any

import pandas as pd
import pytest
from engine_support import RecordingRedis, decode_eval, make_bars, trend_config

from core_engine.src import (
    configuration,
    redis_connector,
    strategy_runner,
    trend_filtering,
)
from core_engine.src.configuration import get_strategy
from core_engine.src.indicator import merge_trend_reference
from core_engine.src.rules.trend_tf import apply_signal_filters, build_trend_frame
from core_engine.src.strategy_runner import Outcome, RawSignalWriter


@pytest.mark.parametrize(("strategy", "timeframe", "freq", "columns"), [
    ("ma_cross", "M30", "30min", {"fast_ma", "slow_ma", "macd_h", "atr"}),
    ("combo", "H1", "h", {"atr"}),
])
def test_run_strategy_chay_du_pipeline_va_khong_sua_nen_dau_vao(strategy: str, timeframe: str, freq: str, columns: set[str]) -> None:
    candles = make_bars(600, freq=freq)
    before = candles.copy()
    frame = strategy_runner.run_strategy(strategy, symbol="US30", tf=timeframe, bars=candles)
    pd.testing.assert_frame_equal(candles, before)
    assert columns | {"raw_signal", "signal", "entry_price"} <= set(frame.columns) and len(frame) == len(candles)
    pd.testing.assert_series_equal(frame["signal"].astype(int), frame["raw_signal"].astype(int), check_names=False)  # không có trend: tín hiệu cuối = tín hiệu thô


def test_run_strategy_khung_ngoai_khung_ho_tro_thi_raise() -> None:
    with pytest.raises(ValueError, match="supports only these timeframes"):
        strategy_runner.run_strategy("ma_cross", symbol="US30", tf="D1", bars=make_bars(600))


def test_run_strategy_chien_luoc_la_thi_raise() -> None:
    with pytest.raises(KeyError, match="Unknown strategy"):
        strategy_runner.run_strategy("khong_co", symbol="US30", tf="H1", bars=make_bars(600))


def test_strategy_runner_khong_tu_tinh_xu_huong_chi_nhan_bang_do_nguoi_goi_dua_vao() -> None:
    owned_elsewhere = ("trend_filtering", "compute_timeline", "build_trend_frame", "TrendOutput", "TrendReader")
    assert not [name for name in owned_elsewhere if hasattr(strategy_runner, name)]  # tính và đọc xu hướng thuộc trend_filtering; ở đây chỉ ghép bảng được đưa vào


def test_run_strategy_khong_co_bang_xu_huong_thi_khong_co_cot_xu_huong() -> None:
    frame = strategy_runner.run_strategy("ma_cross", symbol="US30", tf="M30", bars=make_bars(600, "30min"))
    assert not {"trend_bias", "trend_close_time"} & set(frame.columns)


# =============================================================================
# Nhánh có lọc trend: bảng xu hướng được ghép sau bước chỉ báo, lọc và dời tín hiệu do code thật (rules/trend_tf, rules/shift) làm.
# Chiến lược giả chỉ để điều khiển từng nến: cột ``setup`` (+1 mua, -1 bán, 0 không) là điều kiện vào lệnh.
# =============================================================================


class _FakeSpec:
    key = "fake"
    supported_timeframes = ()

    def normalize_params(self, overrides, symbol, tf):
        params = {"ALTERNATING_FILTER_ENABLED": True, "TREND_FILTER_ENABLED": False, "TREND_METHODS": ("breakout",), **(overrides or {})}
        if isinstance(params["TREND_METHODS"], str):
            params["TREND_METHODS"] = (params["TREND_METHODS"],)
        return params

    def add_indicators(self, bars, params):
        return bars.copy()

    def detect_signals(self, df, symbol=None, params=None):
        return apply_signal_filters(df, params, buy_setup=df["setup"].eq(1), sell_setup=df["setup"].eq(-1), buy_reason="mua", sell_reason="ban")

    def add_entries(self, df, params, symbol):
        return df.assign(entry_price=df["close"], atr=1.0)


FILTER = {"TREND_METHODS": "breakout", "TREND_FILTER_ENABLED": True}  # khối có chính sách dời tín hiệu


def _entries(setup: list[int]) -> pd.DataFrame:
    """Nến vào lệnh H2 mở lúc 00:00, 02:00, 04:00... với điều kiện vào lệnh ``setup`` từng nến."""
    opens = pd.date_range("2026-11-02 00:00", periods=len(setup), freq="2h").astype("datetime64[us]")
    return pd.DataFrame({"bartime": opens, "close": 100.0, "setup": setup})


def _marks(bias: list[int]) -> pd.DataFrame:
    """Bảng xu hướng cùng khung H2: mốc i là của nến mở 2 giờ x i, đóng sau 2 giờ."""
    opens = pd.date_range("2026-11-02 00:00", periods=len(bias), freq="2h").astype("datetime64[us]")
    return pd.DataFrame({"bartime": opens, "trend_bias": bias, "trend_close_time": opens + pd.Timedelta(hours=2)})


def _run_fake(monkeypatch: pytest.MonkeyPatch, setup: list[int], bias: list[int], upto: int) -> pd.DataFrame:
    monkeypatch.setattr(strategy_runner, "get_strategy", lambda key: _FakeSpec())
    return strategy_runner.run_strategy("fake", symbol="US30", tf="H2", bars=_entries(setup)[:upto], overrides=FILTER, trend=_marks(bias))


def test_run_strategy_xu_huong_doi_dung_nen_trigger_thi_bi_loai_o_nen_do_va_phat_o_nen_ke_tiep(monkeypatch: pytest.MonkeyPatch) -> None:
    setup, bias = [0, 1, 1, 1, 0, 0], [-1, 1, 1, 1, 1, 1]  # nến 02:00 có buy; chính nến đó làm xu hướng đổi sang tăng (mốc đóng lúc 04:00)
    at_trigger = _run_fake(monkeypatch, setup, bias, upto=2).iloc[-1]
    assert (at_trigger["raw_signal"], at_trigger["signal"], at_trigger["trend_bias"]) == (1, 0, -1)  # nến trigger chỉ thấy xu hướng đã đóng trước nó: giảm, nên buy bị loại
    next_candle = _run_fake(monkeypatch, setup, bias, upto=3).iloc[-1]
    assert (next_candle["raw_signal"], next_candle["signal"], next_candle["trend_bias"]) == (0, 1, 1)  # nến kế tiếp: xu hướng mới, điều kiện buy còn -> phát (dời)
    assert next_candle["entry_price"] == 100.0 and next_candle["atr"] == 1.0  # giá vào, atr tính theo nến phát chứ không phải nến trigger
    assert next_candle["signal_reason"].endswith("; aligned with trend reference")


def test_run_strategy_dieu_kien_buy_het_o_nen_ke_tiep_thi_khong_phat(monkeypatch: pytest.MonkeyPatch) -> None:
    last = _run_fake(monkeypatch, [0, 1, 0, 0, 0, 0], [-1, 1, 1, 1, 1, 1], upto=3).iloc[-1]
    assert (last["signal"], last["trend_bias"]) == (0, 1)


def test_run_strategy_moc_cua_chinh_nen_vua_dong_khong_duoc_dung_cho_nen_do(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cùng khung: mốc của nến vừa đóng được tiến trình trend ghi gần như cùng lúc với lần đánh giá nên có thể có hoặc chưa có trong bảng; kết quả KHÔNG được phụ thuộc vào điều đó."""
    setup, bias = [0, 1, 1, 1, 0, 0], [-1, 1, 1, 1, 1, 1]
    for upto in (2, 3, 4):
        monkeypatch.setattr(strategy_runner, "get_strategy", lambda key: _FakeSpec())
        bars = _entries(setup)[:upto]
        without_own_mark = strategy_runner.run_strategy("fake", symbol="US30", tf="H2", bars=bars, overrides=FILTER, trend=_marks(bias)[: upto - 1])  # chỉ các mốc đã đóng trước nến cuối
        with_own_mark = strategy_runner.run_strategy("fake", symbol="US30", tf="H2", bars=bars, overrides=FILTER, trend=_marks(bias)[:upto])  # thêm mốc của chính nến cuối
        with_future_marks = strategy_runner.run_strategy("fake", symbol="US30", tf="H2", bars=bars, overrides=FILTER, trend=_marks(bias))
        pd.testing.assert_frame_equal(without_own_mark, with_own_mark)
        pd.testing.assert_frame_equal(without_own_mark, with_future_marks)


def test_run_strategy_bat_loc_trend_ma_khong_co_bang_xu_huong_thi_bao_loi_ro_rang(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(strategy_runner, "get_strategy", lambda key: _FakeSpec())
    with pytest.raises(ValueError, match="requires a trend reference frame"):
        strategy_runner.run_strategy("fake", symbol="US30", tf="H2", bars=_entries([0, 1, 1]), overrides=FILTER)


def test_run_strategy_khong_sua_bang_nen_va_bang_xu_huong_dau_vao(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(strategy_runner, "get_strategy", lambda key: _FakeSpec())
    bars, marks = _entries([0, 1, 1, 1]), _marks([-1, 1, 1, 1])
    bars_before, marks_before = bars.copy(), marks.copy()
    strategy_runner.run_strategy("fake", symbol="US30", tf="H2", bars=bars, overrides=FILTER, trend=marks)
    pd.testing.assert_frame_equal(bars, bars_before)
    pd.testing.assert_frame_equal(marks, marks_before)


@pytest.mark.parametrize(("strategy", "timeframe", "method", "has_shifted"), [
    ("ma_cross", "M30", "tstat", True),
    ("ma_cross", "M10", "breakout", True),
    ("combo", "H3", "cloud", True),
    ("macd_break", "M30", "tstat", True),
    ("ma_cross", "M20", "knn", False),
])
def test_duong_moi_doc_xu_huong_tu_redis_cho_ket_qua_giong_het_duong_tinh_xu_huong_tai_cho(strategy: str, timeframe: str, method: str, has_shifted: bool) -> None:
    """Đường mới: tiến trình trend ghi dòng thời gian vào db trend (TrendOutput), tiến trình chiến lược đọc lại số mốc ``cell.marks`` (TrendReader) rồi chạy ``run_strategy(trend=...)``.
    Đường cũ: tính xu hướng tại chỗ (build_trend_frame) rồi ghép và chạy từng bước theo StrategySpec. Hai đường phải ra CÙNG từng nến, gồm cả tín hiệu dời (nến không có tín hiệu thô)."""
    minutes = {"M10": 10, "M20": 20, "M30": 30, "H1": 60, "H2": 120, "H3": 180, "H4": 240}
    job = configuration.build_lane_job(trend_config((strategy,), (method,)), strategy)
    cell = job.trend_cells[("US30", timeframe)][0]
    trend_bars = make_bars(600, f"{minutes[cell.trend_tf]}min")
    entry = make_bars(500, f"{minutes[timeframe]}min")
    entry["bartime"] = entry["bartime"] + pd.Timedelta(minutes=600 * minutes[cell.trend_tf] - 500 * minutes[timeframe])  # cửa sổ nến vào lệnh kết thúc cùng lúc với dòng thời gian trend

    spec = get_strategy(strategy)
    params = spec.normalize_params(cell.overrides, "US30", timeframe)
    expected = merge_trend_reference(spec.add_indicators(entry, params), build_trend_frame(trend_bars, params))
    expected = spec.add_entries(spec.detect_signals(expected, symbol="US30", params=params), params, "US30")

    client = RecordingRedis(emulate=True)
    connection = {"host": "127.0.0.1", "port": 6380, "password": "", "db": 1, "key_prefix": "L_TREND", "client": client}
    trend_filtering.TrendOutput(**connection, retention_seconds=604800, retention_max_entries=1500).write(
        cell.method, "US30", cell.trend_tf, trend_filtering.compute_timeline(trend_bars, params),
    )
    table = trend_filtering.TrendReader(**connection).read(cell.method, "US30", cell.trend_tf, cell.marks)
    result = strategy_runner.run_strategy(strategy, symbol="US30", tf=timeframe, bars=entry, overrides=cell.overrides, trend=table)

    columns = ["bartime", "raw_signal", "signal", "entry_price", "signal_reason", "trend_filter_status", "trend_bias"]
    pd.testing.assert_frame_equal(result[columns], expected[columns])
    assert (result["raw_signal"] != 0).any() and (result["signal"] != 0).any()  # so sánh có nội dung, không phải hai bảng trống
    shifted = (result["signal"] != 0) & (result["raw_signal"] == 0)
    assert shifted.any() == has_shifted


# =============================================================================
# Đầu ra: ghi tín hiệu RAW (RawSignalWriter) -- giá trị mong đợi là bản ghi tín hiệu THẬT của ma_cross HK50 H1, nến mở 2026-10-16 16:00, SELL.
# =============================================================================

VALID_FROM = pd.Timestamp("2026-10-16 17:00:00", tz="UTC")
VALID_UNTIL = pd.Timestamp("2026-10-16 17:10:00", tz="UTC")
RULE = {"mode": "seconds_after_bar_close", "seconds": 600}
ROW = pd.Series({
    "bartime": pd.Timestamp("2026-10-16 16:00:00"), "signal": -1, "raw_signal": -1, "entry_price": 24137.7, "atr": 66.475406,
    "signal_reason": "fast SMA below slow SMA; MACD histogram < 0",
})
EXPECTED_FIELDS = {
    "symbol": "HK50", "timeframe": "H1", "strategy": "ma_cross", "bartime": "2026-10-16T16:00:00Z", "tradeSide": "SELL", "orderType": "MARKET",
    "expirationTimestamp": "1792170600", "clientOrderId": "ma_cross:H1:HK50:20261016T160000Z",
    "comment": "fast SMA below slow SMA; MACD histogram < 0", "atr": "66.48", "valid_from": "2026-10-16T17:00:00Z",
}


def _writer(client: RecordingRedis) -> RawSignalWriter:
    return RawSignalWriter(
        host="127.0.0.1", port=6380, password="", db=0, key_prefix="L_DE", channel_prefix="L_CHANNEL",
        retention_seconds=604800, retention_max_entries=500, client=client,
    )


def _latest(**changes: Any) -> pd.Series:
    row = ROW.copy()
    for key, value in changes.items():
        row[key] = value
    return row


def _publish(writer: RawSignalWriter, latest: pd.Series, now: str) -> Outcome:
    return writer.publish_latest(strategy="ma_cross", order_type="MARKET", validity=RULE, timeframe="H1", symbol="HK50", latest=latest, now=pd.Timestamp(now, tz="UTC"))


def test_publish_latest_ghi_list_hash_va_kenh_dung_hop_dong_voi_oe(caplog: pytest.LogCaptureFixture) -> None:
    client = RecordingRedis()
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        outcome = _publish(_writer(client), ROW, "2026-10-16 17:00:10")
    assert outcome == Outcome("published", 1) and len(client.calls) == 1
    call = decode_eval(client.calls[0])
    assert call["hash_key"] == "L_DE_MACROSS_HK50_H1:2026-10-16 16:00:00" and call["list_key"] == "L_DE_MACROSS_HK50_H1"
    assert call["stamp"] == "2026-10-16 16:00:00" and call["ttl"] == "604800" and call["max"] == "500"
    assert call["channel"] == "L_CHANNEL_MACROSS_HK50_H1" and call["fields"] == EXPECTED_FIELDS
    assert json.loads(call["message"]) == EXPECTED_FIELDS  # message kênh mang đủ tín hiệu
    assert [r.getMessage() for r in caplog.records] == [
        "event=signal_published strategy=ma_cross symbol=HK50 timeframe=H1 side=SELL signal_id=ma_cross:H1:HK50:20261016T160000Z source=raw"
    ]


def test_nguon_loc_ghi_vao_tien_to_rieng_cung_cau_truc_tin_hieu_va_gan_nhan_nguon_vao_log(caplog: pytest.LogCaptureFixture) -> None:
    client = RecordingRedis()
    writer = RawSignalWriter(
        host="127.0.0.1", port=6380, password="", db=2, key_prefix="L_DE_TSTAT", channel_prefix="L_CHANNEL_TSTAT",
        retention_seconds=604800, retention_max_entries=500, source="tstat", client=client,
    )
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        outcome = _publish(writer, ROW, "2026-10-16 17:00:10")
    call = decode_eval(client.calls[0])
    assert outcome == Outcome("published", 1)
    assert call["list_key"] == "L_DE_TSTAT_MACROSS_HK50_H1" and call["channel"] == "L_CHANNEL_TSTAT_MACROSS_HK50_H1"  # khoá và kênh theo tiền tố của khối
    assert call["fields"] == EXPECTED_FIELDS  # cùng cấu trúc tín hiệu với nguồn thô
    assert caplog.records[0].getMessage().endswith("signal_id=ma_cross:H1:HK50:20261016T160000Z source=tstat")


def test_price_decimals_lam_tron_theo_symbol_con_symbol_khong_khai_giu_2_so_le() -> None:
    client = RecordingRedis()
    writer = RawSignalWriter(
        host="127.0.0.1", port=6380, password="", db=0, key_prefix="L_DE", channel_prefix="L_CHANNEL",
        retention_seconds=604800, retention_max_entries=500, price_decimals={"EURUSD": 5}, client=client,
    )
    now = pd.Timestamp("2026-10-16 20:00:10", tz="UTC")
    validity = {"mode": "next_bar", "valid_bars": 3}
    for symbol in ("EURUSD", "US30"):
        row = _latest(signal=1, entry_price=1.082346 if symbol == "EURUSD" else 42000.567, atr=0.000412 if symbol == "EURUSD" else 66.475406)
        outcome = writer.publish_latest(strategy="combo", order_type="STOP", validity=validity, timeframe="H4", symbol=symbol, latest=row, now=now)
        assert outcome == Outcome("published", 1)
    forex, index = (decode_eval(call)["fields"] for call in client.calls)
    assert (forex["stopPrice"], forex["atr"]) == ("1.08235", "0.00041")  # symbol khai 5 số lẻ
    assert (index["stopPrice"], index["atr"]) == ("42000.57", "66.48")  # không khai: 2 số lẻ như trước


def test_publish_latest_lenh_stop_co_stop_price_bang_entry() -> None:
    client = RecordingRedis()
    row = _latest(signal=1, entry_price=42000.5)
    writer = _writer(client)
    outcome = writer.publish_latest(
        strategy="combo", order_type="STOP", validity={"mode": "next_bar", "valid_bars": 3}, timeframe="H4", symbol="US30", latest=row,
        now=pd.Timestamp("2026-10-16 20:00:10", tz="UTC"),
    )
    assert outcome == Outcome("published", 1)
    fields = decode_eval(client.calls[0])["fields"]
    assert fields["orderType"] == "STOP" and fields["stopPrice"] == "42000.5" and fields["tradeSide"] == "BUY"


def test_publish_latest_bao_trung_khi_khoa_da_co(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        outcome = _publish(_writer(RecordingRedis(created=0)), ROW, "2026-10-16 17:00:10")
    assert outcome == Outcome("duplicate", 0)
    assert caplog.records[0].getMessage() == "event=signal_duplicate strategy=ma_cross symbol=HK50 timeframe=H1 signal_id=ma_cross:H1:HK50:20261016T160000Z source=raw"


def test_publish_latest_khong_tin_hieu_thi_khong_ghi_va_chi_ghi_debug(caplog: pytest.LogCaptureFixture) -> None:
    client = RecordingRedis()
    with caplog.at_level(logging.DEBUG, logger="core_engine.src"):
        outcome = _publish(_writer(client), _latest(signal=0, raw_signal=0), "2026-10-16 17:00:10")
    assert outcome == Outcome("no_signal", 0) and client.calls == []
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [(logging.DEBUG, "event=no_signal strategy=ma_cross symbol=HK50 timeframe=H1 source=raw")]


def test_publish_latest_het_han_thi_khong_ghi_va_ghi_info(caplog: pytest.LogCaptureFixture) -> None:
    client = RecordingRedis()
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        outcome = _publish(_writer(client), ROW, "2026-10-16 17:10:00")  # đúng hạn chót: đã hết hạn (nửa mở)
    assert outcome == Outcome("expired", 0) and client.calls == []
    assert caplog.records[0].getMessage() == "event=signal_expired strategy=ma_cross symbol=HK50 timeframe=H1 source=raw"


def test_publish_latest_ghi_khi_con_trong_han() -> None:
    writer = _writer(RecordingRedis())
    assert _publish(writer, ROW, "2026-10-16 17:00:00").state == "published"  # đúng giờ nến đóng
    assert _publish(writer, ROW, "2026-10-16 17:09:59").state == "published"  # sát hạn chót (hạn chót nửa mở)


def test_raw_signal_writer_ping_close_va_noi_redis_qua_redis_connector(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RecordingRedis()
    writer = _writer(client)
    writer.ping()
    writer.close()
    assert client.pinged == 1 and client.closed
    seen: list[dict] = []
    monkeypatch.setattr(redis_connector.redis, "Redis", lambda **kwargs: seen.append(kwargs) or RecordingRedis())
    RawSignalWriter(
        host="10.0.0.5", port="6380", password="mat-khau-dau-ra", db="0", key_prefix="L_DE", channel_prefix="L_CHANNEL",
        retention_seconds=604800, retention_max_entries=500, socket_timeout=4, connect_timeout=3, healthcheck_interval=20,
    )
    assert seen == [{
        "host": "10.0.0.5", "port": 6380, "db": 0, "password": "mat-khau-dau-ra", "decode_responses": True,
        "socket_timeout": 4.0, "socket_connect_timeout": 3.0, "health_check_interval": 20,
    }]
