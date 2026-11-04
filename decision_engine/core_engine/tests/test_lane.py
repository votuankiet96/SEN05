"""Test điều phối một tiến trình chiến lược (configuration.py: build_lane_job, process_pair, run_lane, run) và lối vào main.py — hermetic.

Vòng lặp sự kiện được kiểm bằng engine/publisher giả, đồng hồ giả (ĐỒNG HỒ chỉ tiến khi vòng lặp "chờ") và ``process_pair`` giả ghi lại thứ tự xử lý: điều cần chứng minh là CÁCH ĐIỀU PHỐI
(gộp sự kiện theo cặp, quét khởi động, thử lại nến chưa đóng, cô lập lỗi), không phải chiến lược (đã có test riêng và A/B với bản cũ).
"""

from __future__ import annotations

import copy
import dataclasses
import logging
import math
import signal
from typing import ClassVar

import pandas as pd
import pytest
import redis
from engine_support import (
    GROUP_SYMBOLS,
    GROUP_TIMEFRAMES,
    FakeClock,
    LoopEngine,
    lane_config,
    source_config,
    trend_config,
)

from core_engine.src import (
    configuration,
    data_engine,
    main,
    strategy_runner,
    trend_filtering,
)
from core_engine.src.data_engine import Pair
from core_engine.src.indicator import timeframe_minutes
from core_engine.src.strategy_runner import Outcome


def _job(strategy: str = "combo", **changes):
    return dataclasses.replace(configuration.build_lane_job(lane_config(("combo", "ma_cross")), strategy), **changes)


# =============================================================================
# 1. build_lane_job: dựng và kiểm cấu hình trước khi chạm Redis.
# =============================================================================


def test_build_lane_job_dung_viec_cua_mot_chien_luoc() -> None:
    job = configuration.build_lane_job(lane_config(), "combo")
    assert job.strategy == "combo" and job.order_type == "STOP" and job.validity == {"mode": "next_bar", "valid_bars": 3}
    assert len(job.pairs) == len(GROUP_SYMBOLS) * len(GROUP_TIMEFRAMES) and set(job.pairs) == {(s, t) for s in GROUP_SYMBOLS for t in GROUP_TIMEFRAMES}
    assert job.source == {
        "host": "127.0.0.1", "port": 6379, "password": "", "db": 0, "key_prefix": "L_CANDLE", "snapshot_bars": 500,
        "socket_timeout": 5.0, "connect_timeout": 5.0, "healthcheck_interval": 30,
    }
    assert job.sink == {
        "host": "127.0.0.1", "port": 6380, "password": "", "db": 0, "key_prefix": "L_DE", "channel_prefix": "L_CHANNEL",
        "retention_seconds": 604800, "retention_max_entries": 500, "price_decimals": {}, "socket_timeout": 5.0, "connect_timeout": 5.0, "healthcheck_interval": 30,
    }
    assert job.process_on_startup is True and job.reconnect_delay_seconds == 3.0 and job.log_level == "INFO"
    assert configuration.build_lane_job(lane_config(), "ma_cross").order_type == "MARKET"


def test_cap_cua_lane_la_khung_live_cua_chien_luoc_giao_nhom_khai_trong_config() -> None:
    config = lane_config()
    config["redis"]["input"]["symbols"] = ["us30", "HK50"]
    config["redis"]["input"]["timeframes"] = ["H4", "h1"]
    job = configuration.build_lane_job(config, "combo")
    assert job.pairs == (("US30", "H1"), ("HK50", "H1"), ("US30", "H4"), ("HK50", "H4"))  # khung theo thứ tự khung live của chiến lược, symbol theo thứ tự nhóm, chữ hoa


@pytest.mark.parametrize(
    ("mutate", "error", "match"),
    [
        (lambda c: c["live"].update(enabled_strategies=["combo"]), ValueError, "not in live.enabled_strategies"),
        (lambda c: c["redis"].update(trend_outputs={"ema": {"key_prefix": "L_DE_EMA", "channel_prefix": "L_CHANNEL_EMA"}}), ValueError, "'redis.trend_outputs.ema' is not a trend source"),
        (lambda c: c["redis"].update(trend_outputs={"raw": {"key_prefix": "L_X", "channel_prefix": "L_Y"}}), ValueError, "'raw' is the unfiltered output"),
        (lambda c: c["redis"].update(trend_outputs={"knn": {"key_prefix": "L_DE", "channel_prefix": "L_CHANNEL_KNN"}}), ValueError, "share the same key_prefix 'L_DE'"),
        (lambda c: c["redis"].update(trend_outputs={"knn": {"key_prefix": "L_DE_KNN", "channel_prefix": "L_CHANNEL"}}), ValueError, "share the same channel_prefix 'L_CHANNEL'"),
        (lambda c: c["redis"].update(trend_outputs={"knn": {"key_prefix": "L_DE_KNN"}}), KeyError, "redis.trend_outputs.knn.channel_prefix"),
        (lambda c: c["redis"].update(trend_outputs=["knn"]), TypeError, "must map source name"),
        (lambda c: c["redis"]["output"].update(port=6379, db=0), ValueError, "different databases"),  # cùng Redis, cùng db với đầu vào
        (lambda c: c["redis"]["trend_output"].update(db=0), ValueError, "trend_output.db must differ from redis.output.db"),
        (lambda c: c["redis"]["trend_output"].pop("db"), KeyError, "redis.trend_output.db"),
        (lambda c: c["redis"].pop("trend_output"), KeyError, "redis.trend_output"),
        (lambda c: c["redis"].pop("filtered_output"), KeyError, "redis.filtered_output.db"),
        (lambda c: c["redis"]["filtered_output"].pop("db"), KeyError, "redis.filtered_output.db"),
        (lambda c: c["redis"]["filtered_output"].update(db=0), ValueError, "filtered_output.db must differ from redis.output.db and redis.trend_output.db"),
        (lambda c: c["redis"]["filtered_output"].update(db=1), ValueError, "filtered_output.db must differ from redis.output.db and redis.trend_output.db"),
        (
            lambda c: (c["redis"]["output"].update(port=6379, db=5), c["redis"]["trend_output"].update(db=6), c["redis"]["filtered_output"].update(db=0)),
            ValueError,
            "input and filtered output must use different databases",
        ),  # db lọc trùng db nến của Core (cùng Redis): sẽ ghi tín hiệu lẫn vào nến
        (lambda c: c["redis"]["input"].pop("symbols"), KeyError, "redis.input.symbols"),
        (lambda c: c["redis"]["output"].pop("port"), KeyError, "redis.output.port"),
        (lambda c: c["redis"].pop("host"), KeyError, "redis.host"),
        (lambda c: c["live"].pop("log_level"), KeyError, "live.log_level"),
        (lambda c: c["live"]["signal_validity"].pop("ma_cross"), KeyError, "live.signal_validity.ma_cross"),
        (lambda c: c["redis"]["input"].update(timeframes=["X5"]), ValueError, "Unsupported timeframe 'X5'"),
        (lambda c: c["redis"]["input"].update(symbols=[]), TypeError, "non-empty list"),
        (lambda c: c["redis"]["input"].update(symbols=["US30", "us30"]), ValueError, "lists a name twice: US30"),
        (lambda c: c["redis"]["input"].update(timeframes=["H1", " "]), ValueError, "empty name"),
        (lambda c: c["redis"]["input"].update(snapshot_bars=0), ValueError, "snapshot_bars must be positive"),
        (lambda c: c["redis"]["output"].update(key_prefix=" "), ValueError, "key_prefix must be configured"),
        (lambda c: c["redis"]["output"].update(channel_prefix=""), ValueError, "channel_prefix must be configured"),
        (lambda c: c["redis"]["output"].update(retention_seconds=0), ValueError, "retention_seconds must be positive"),
        (lambda c: c["redis"]["output"].update(retention_max_entries=0), ValueError, "retention_max_entries must be positive"),
        (lambda c: c["live"]["signal_validity"].update(ma_cross={"mode": "weekly"}), ValueError, "Unsupported validity mode"),
    ],
)
def test_build_lane_job_cau_hinh_sai_thi_dung_ngay_voi_loi_ro_rang(mutate, error, match) -> None:
    config = lane_config()
    mutate(config)
    with pytest.raises(error, match=match):
        configuration.build_lane_job(config, "ma_cross")


def test_main_giao_tien_trinh_trend_cho_configuration_run_trend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.signal, "signal", lambda signum, handler: None)
    called = []
    monkeypatch.setattr(configuration, "run_trend", lambda method, input_name="": called.append((method, input_name)) or 3)
    monkeypatch.setattr(configuration, "run", lambda strategy, input_name="": pytest.fail("không chạy tiến trình chiến lược khi truyền --trend"))
    assert main.main(["--trend", "zigzag"]) == 3 and called == [("zigzag", "")]
    assert main.main(["--trend", "knn.og8"]) == 3 and called[-1] == ("knn", "og8")  # hậu tố .<nguồn> chọn nguồn nến thêm


def test_build_lane_job_chap_nhan_trend_outputs_khai_rong() -> None:
    for empty in (None, {}):
        config = lane_config()
        config["redis"]["trend_outputs"] = empty  # nguồn trend đang tắt (khối chú thích hết) là cấu hình hợp lệ
        job = configuration.build_lane_job(config, "ma_cross")
        assert job.strategy == "ma_cross" and job.trend_cells == {} and job.trend_source is None and job.filtered_sinks == {}  # chỉ còn nhánh 1


# =============================================================================
# 1b. build_lane_job: bảng "ô -> khối trend", nguồn xu hướng và nguồn lọc.
# =============================================================================


def _trend_job(strategy: str = "ma_cross", sources: tuple[str, ...] = ("knn", "breakout", "tstat"), **changes):
    return dataclasses.replace(configuration.build_lane_job(trend_config((strategy,), sources), strategy), **changes)


def test_build_lane_job_dung_bang_o_khoi_trend_theo_dung_ham_cua_phia_trend() -> None:
    job = _trend_job()
    assert job.trend_cells and set(job.trend_cells) == {(symbol, tf) for symbol, tf in job.pairs if configuration.trend_enabled_for("ma_cross", tf)}
    cells = job.trend_cells[("US30", "M30")]
    assert [cell.method for cell in cells] == ["knn", "breakout", "tstat"]  # đúng thứ tự khai trong redis.trend_outputs
    for cell in cells:
        source = configuration.trend_source_for("ma_cross", "US30", "M30", cell.method)
        assert (cell.trend_tf, cell.shifts_signals) == (source.trend_tf, source.shifts_signals)
        assert cell.overrides == {"TREND_METHODS": cell.method, "TREND_FILTER_ENABLED": True, "TREND_TF": cell.trend_tf}
        assert cell.marks == math.ceil(500 * 30 / timeframe_minutes(cell.trend_tf)) + 2  # phủ cả cửa sổ 500 nến vào lệnh, dư 2 mốc
    assert {cell.method: cell.shifts_signals for cell in cells} == {"knn": False, "breakout": True, "tstat": True}


def test_build_lane_job_nguon_xu_huong_va_nguon_loc_dung_db_port_va_tien_to() -> None:
    config = trend_config(("ma_cross",), ("knn", "tstat"))
    config["redis"]["output"].update(port=6380, password="mat-khau-dau-ra")
    job = configuration.build_lane_job(config, "ma_cross")
    assert job.outputs == (("raw", "L_DE", "L_CHANNEL"), ("knn", "L_DE_KNN", "L_CHANNEL_KNN"), ("tstat", "L_DE_TSTAT", "L_CHANNEL_TSTAT"))
    assert job.trend_source == {
        "host": "127.0.0.1", "port": 6380, "password": "mat-khau-dau-ra", "db": 1, "key_prefix": "L_TREND",
        "socket_timeout": 5.0, "connect_timeout": 5.0, "healthcheck_interval": 30,
    }
    assert set(job.filtered_sinks) == {"knn", "tstat"} and job.sink["db"] == 0  # nhánh 1 vẫn ở db0
    assert job.filtered_sinks["tstat"] == {
        "host": "127.0.0.1", "port": 6380, "password": "mat-khau-dau-ra", "db": 2, "key_prefix": "L_DE_TSTAT", "channel_prefix": "L_CHANNEL_TSTAT",
        "retention_seconds": 604800, "retention_max_entries": 500, "source": "tstat", "price_decimals": {}, "socket_timeout": 5.0, "connect_timeout": 5.0, "healthcheck_interval": 30,
    }


def test_build_lane_job_chien_luoc_khong_co_nhanh_loc_thi_chi_con_nhanh_mot() -> None:
    job = _trend_job("ichimoku", ("knn", "tstat"))  # ichimoku tắt trend ở mọi khung
    assert job.trend_cells == {} and job.trend_source is None and job.filtered_sinks == {} and job.outputs == (("raw", "L_DE", "L_CHANNEL"),)
    job = _trend_job("macd_break", ("knn", "breakout"))  # macd_break loại khối breakout
    assert job.trend_cells and all(cell.method != "breakout" for cells in job.trend_cells.values() for cell in cells)
    assert set(job.filtered_sinks) == {"knn"} and [name for name, _, _ in job.outputs] == ["raw", "knn"]


def test_build_lane_job_nguon_nen_them_doc_nen_tu_redis_rieng_nhung_ghi_dau_ra_o_redis_cua_de() -> None:
    config = source_config(("ma_cross",), ("knn",))
    default = configuration.build_lane_job(config, "ma_cross")
    extra = configuration.build_lane_job(config, "ma_cross", "og8")
    assert default.input_name == "" and extra.input_name == "og8"
    assert {s for s, _ in default.pairs} == set(GROUP_SYMBOLS) and {s for s, _ in extra.pairs} == {"EURUSD", "FR40"}  # mỗi nhóm chỉ chạy symbol của nguồn mình
    assert {s for s, _ in extra.trend_cells} == {"EURUSD", "FR40"}
    assert extra.source == {
        "host": "10.11.12.8", "port": 6380, "password": "mat-khau-og8", "db": 0, "key_prefix": "L_CANDLE", "snapshot_bars": 500,
        "socket_timeout": 5.0, "connect_timeout": 5.0, "healthcheck_interval": 30,
    }
    # Đầu ra không đổi theo nguồn nến: vẫn ghi/đọc ở Redis của DE (cùng host với nhóm mặc định), nên mọi nhóm dùng chung một bố cục khoá
    assert extra.sink == default.sink and extra.trend_source == default.trend_source and extra.filtered_sinks == default.filtered_sinks
    assert extra.sink["host"] == "127.0.0.1" and extra.process_on_startup == default.process_on_startup


@pytest.mark.parametrize(
    ("mutate", "error", "match"),
    [
        (lambda c: c["redis"]["input_sources"]["og8"].pop("password"), KeyError, "redis.input_sources.og8.password"),
        (lambda c: c["redis"]["input_sources"]["og8"].pop("symbols"), KeyError, "redis.input_sources.og8.symbols"),
        (lambda c: c["redis"]["input_sources"]["og8"].update(snapshot_bars=100), ValueError, "unknown keys: snapshot_bars"),  # kế thừa redis.input, không đè được
        (lambda c: c["redis"]["input_sources"]["og8"].update(host="127.0.0.1"), ValueError, "input and output must use different databases"),  # cùng Redis, cùng db với đầu ra
        (lambda c: c["redis"]["input_sources"]["og8"].update(symbols=["EURUSD", "eurusd"]), ValueError, "lists a name twice: EURUSD"),
        (lambda c: c["redis"].update(input_sources=["og8"]), TypeError, "must map source name"),
        (lambda c: c["redis"].update(input_sources={"OG-8": {}}), ValueError, "lowercase letters and digits only"),
    ],
)
def test_build_lane_job_nguon_nen_them_sai_thi_dung_ngay_voi_loi_ro_rang(mutate, error, match) -> None:
    config = source_config(("ma_cross",), ("knn",))
    mutate(config)
    with pytest.raises(error, match=match):
        configuration.build_lane_job(config, "ma_cross", "og8")


def test_build_lane_job_nguon_nen_la_hoac_hong_khong_chan_nhom_mac_dinh() -> None:
    config = source_config(("ma_cross",), ("knn",))
    with pytest.raises(ValueError, match="input source 'khac' is not declared in redis.input_sources \\(declared: og8\\)"):
        configuration.build_lane_job(config, "ma_cross", "khac")
    config["redis"]["input_sources"]["og8"].pop("password")  # khối og8 hỏng: nhóm mặc định không đọc nó nên vẫn dựng được (mỗi nhóm độc lập)
    assert configuration.build_lane_job(config, "ma_cross").input_name == ""


def test_all_symbols_gop_symbol_cac_nguon_va_chan_symbol_thuoc_hai_nguon() -> None:
    config = source_config()
    assert configuration._all_symbols(config["redis"]) == (*GROUP_SYMBOLS, "EURUSD", "FR40")
    config["redis"]["input_sources"]["og8"]["symbols"] = ["EURUSD", "US30"]  # US30 đã thuộc redis.input
    with pytest.raises(ValueError, match="symbol US30 is listed by both 'redis.input' and 'redis.input_sources.og8'"):
        configuration._all_symbols(config["redis"])


def test_price_decimals_doc_tu_live_va_di_vao_ca_nguon_tho_lan_nguon_loc() -> None:
    config = trend_config(("ma_cross",), ("knn",))
    config["live"]["price_decimals"] = {"eurusd": 5, "USDJPY": 3}
    job = configuration.build_lane_job(config, "ma_cross")
    expected = {"EURUSD": 5, "USDJPY": 3}
    assert job.sink["price_decimals"] == expected and job.filtered_sinks["knn"]["price_decimals"] == expected


@pytest.mark.parametrize(
    ("value", "error", "match"),
    [
        ({"AUDUSD": 5}, ValueError, "'live.price_decimals.AUDUSD' is not an operating symbol"),  # gõ sai/không vận hành: không âm thầm bỏ qua
        ({"EURUSD": 5, "eurusd": 4}, ValueError, "lists symbol EURUSD twice"),
        ({"EURUSD": 9}, ValueError, "integer between 0 and 8"),
        ({"EURUSD": -1}, ValueError, "integer between 0 and 8"),
        ({"EURUSD": True}, ValueError, "integer between 0 and 8"),
        ({"EURUSD": "5"}, ValueError, "integer between 0 and 8"),
        (["EURUSD"], TypeError, "must map symbol"),
    ],
)
def test_price_decimals_sai_thi_dung_ngay_voi_loi_ro_rang(value, error, match) -> None:
    config = lane_config()
    config["live"]["price_decimals"] = value
    with pytest.raises(error, match=match):
        configuration.build_lane_job(config, "ma_cross")


# =============================================================================
# 2. process_pair: luồng của một cặp.
# =============================================================================


class _Engine:
    def __init__(self, frame) -> None:
        self.frame = frame
        self.asked: list[Pair] = []

    def snapshot(self, pair):
        self.asked.append(pair)
        return self.frame


class _Publisher:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def publish_latest(self, **kwargs):
        self.calls.append(kwargs)
        return Outcome("published", 1)


def test_process_pair_nen_roi_chien_luoc_roi_ghi(monkeypatch: pytest.MonkeyPatch) -> None:
    candles = pd.DataFrame({"bartime": pd.to_datetime(["2026-01-01"]), "close": [1.0]})
    strategy_frame = pd.DataFrame({"bartime": pd.to_datetime(["2026-01-01", "2026-01-02"]), "signal": [0, 1], "raw_signal": [0, 1]})
    seen = {}
    monkeypatch.setattr(strategy_runner, "run_strategy", lambda strategy, *, symbol, tf, bars: seen.update(strategy=strategy, symbol=symbol, timeframe=tf, candles=bars) or strategy_frame)
    publisher = _Publisher()
    now = pd.Timestamp("2026-01-02 10:00", tz="UTC")
    outcome = configuration.process_pair(_job("ma_cross"), _Engine(candles), publisher, Pair("US30", "H1"), now=now)
    assert outcome == Outcome("published", 1)
    assert seen["strategy"] == "ma_cross" and seen["symbol"] == "US30" and seen["timeframe"] == "H1" and seen["candles"] is candles
    call = publisher.calls[0]
    assert call["strategy"] == "ma_cross" and call["order_type"] == "MARKET" and call["validity"] == {"mode": "seconds_after_bar_close", "seconds": 600}
    assert call["timeframe"] == "H1" and call["symbol"] == "US30" and call["now"] == now
    assert call["latest"].equals(strategy_frame.iloc[-1])  # chỉ dòng cuối (nến mới nhất) được xét


def test_process_pair_cap_chua_co_nen_thi_khong_chay_gi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(strategy_runner, "run_strategy", lambda *a, **k: pytest.fail("không chạy chiến lược khi chưa có nến"))
    publisher = _Publisher()
    for frame in (None, pd.DataFrame(columns=data_engine.OHLCV_COLUMNS)):
        assert configuration.process_pair(_job(), _Engine(frame), publisher, Pair("US30", "H1")) is None
    assert publisher.calls == []


# =============================================================================
# 2b. process_pair, nhánh 2: tín hiệu đã lọc trend của từng khối (xu hướng đọc từ db trend, ghi vào db lọc).
# =============================================================================


class _TrendReader:
    def __init__(self, table: pd.DataFrame, events: list[str]) -> None:
        self.table, self.events = table, events
        self.calls: list[tuple] = []

    def read(self, method, symbol, trend_tf, count):
        self.events.append(f"read:{method}")
        self.calls.append((method, symbol, trend_tf, count))
        return self.table


class _Writer:
    def __init__(self, name: str, events: list[str], outcome: Outcome) -> None:
        self.name, self.events, self.outcome = name, events, outcome
        self.calls: list[dict] = []

    def publish_latest(self, **kwargs):
        self.events.append(f"write:{self.name}")
        self.calls.append(kwargs)
        return self.outcome


TREND_TABLE = pd.DataFrame({
    "bartime": pd.to_datetime(["2026-01-01 00:00"]).astype("datetime64[us]"), "trend_bias": [1], "trend_close_time": pd.to_datetime(["2026-01-01 01:00"]).astype("datetime64[us]"),
})


def _patch_two_runs(monkeypatch: pytest.MonkeyPatch, events: list[str], captured: list, raw_signal: int, fail_for: tuple[str, ...] = ()) -> None:
    """Chiến lược giả: lần không đè tham số (nhánh 1) trả bảng có tín hiệu thô ``raw_signal`` ở dòng cuối; lần có đè (nhánh 2) trả bảng có tín hiệu lọc ở dòng cuối, gắn tên khối."""
    bartime = pd.to_datetime(["2026-01-01", "2026-01-02"])

    def fake(strategy, *, symbol, tf, bars, overrides=None, trend=None):
        if overrides is None:
            events.append("run:raw")
            return pd.DataFrame({"bartime": bartime, "signal": [0, raw_signal], "raw_signal": [0, raw_signal]})
        method = overrides["TREND_METHODS"]
        events.append(f"run:{method}")
        captured.append((method, overrides, trend))
        if method in fail_for:
            raise ValueError("khối hỏng")
        return pd.DataFrame({"bartime": bartime, "signal": [0, 1], "raw_signal": [0, 0], "tag": method})

    monkeypatch.setattr(strategy_runner, "run_strategy", fake)


def _branch2_setup(raw_outcome: Outcome, table: pd.DataFrame = TREND_TABLE):
    job = _trend_job("ma_cross", ("knn", "tstat"))
    pair = Pair(*next(iter(job.trend_cells)))
    events: list[str] = []
    raw = _Writer("raw", events, raw_outcome)
    writers = {name: _Writer(name, events, Outcome("published", 1)) for name in ("knn", "tstat")}
    return job, pair, events, raw, writers, _TrendReader(table, events)


NOW = pd.Timestamp("2026-01-02 10:00", tz="UTC")
CANDLES = pd.DataFrame({"bartime": pd.to_datetime(["2026-01-01"]), "close": [1.0]})


def test_process_pair_nhanh_hai_ghi_sau_nhanh_mot_khoi_khong_doi_chi_chay_khi_co_tin_hieu_tho_khoi_doi_luon_chay(monkeypatch: pytest.MonkeyPatch) -> None:
    for raw_signal, raw_outcome, expected_events, expected in (
        (0, Outcome("no_signal", 0), ["run:raw", "write:raw", "read:tstat", "run:tstat", "write:tstat"], Outcome("no_signal", 1)),
        (
            1, Outcome("published", 1),
            ["run:raw", "write:raw", "read:knn", "run:knn", "write:knn", "read:tstat", "run:tstat", "write:tstat"], Outcome("published", 3),
        ),
    ):
        job, pair, events, raw, writers, reader = _branch2_setup(raw_outcome)
        captured: list = []
        _patch_two_runs(monkeypatch, events, captured, raw_signal)
        outcome = configuration.process_pair(job, _Engine(CANDLES), raw, pair, now=NOW, trend_reader=reader, filtered=writers)
        assert events == expected_events and outcome == expected
        cells = {cell.method: cell for cell in job.trend_cells[(pair.symbol, pair.timeframe)]}
        for method, overrides, trend in captured:
            assert overrides == cells[method].overrides and trend is reader.table  # chạy đúng bộ tham số của khối, đưa đúng bảng xu hướng vừa đọc
        assert reader.calls == [(c.method, pair.symbol, c.trend_tf, c.marks) for c in cells.values() if raw_signal or c.shifts_signals]
        for method, writer in writers.items():
            for call in writer.calls:
                assert call["strategy"] == "ma_cross" and call["latest"]["tag"] == method and call["now"] == NOW  # ghi dòng cuối của bảng ĐÃ LỌC


def test_process_pair_o_khong_co_nhanh_hai_thi_tra_nguyen_ket_qua_nhanh_mot(monkeypatch: pytest.MonkeyPatch) -> None:
    job, _, events, raw, writers, reader = _branch2_setup(Outcome("published", 1))
    pair = next(Pair(symbol, tf) for symbol, tf in job.pairs if (symbol, tf) not in job.trend_cells)  # ma_cross tắt trend ở H1..H4
    _patch_two_runs(monkeypatch, events, [], raw_signal=1)
    outcome = configuration.process_pair(job, _Engine(CANDLES), raw, pair, now=NOW, trend_reader=reader, filtered=writers)
    assert outcome == Outcome("published", 1) and events == ["run:raw", "write:raw"] and reader.calls == []


def test_process_pair_loi_cua_mot_khoi_khong_chan_khoi_khac_va_khong_mat_nhanh_mot(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    job, pair, events, raw, writers, reader = _branch2_setup(Outcome("published", 1))
    _patch_two_runs(monkeypatch, events, [], raw_signal=1, fail_for=("knn",))
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        outcome = configuration.process_pair(job, _Engine(CANDLES), raw, pair, now=NOW, trend_reader=reader, filtered=writers)
    assert outcome == Outcome("published", 2)  # nhánh 1 cộng khối tstat; khối knn hỏng bị bỏ
    assert events == ["run:raw", "write:raw", "read:knn", "run:knn", "read:tstat", "run:tstat", "write:tstat"]
    assert [m for m in _messages(caplog) if m.startswith("event=process_failed")] == [f"event=process_failed symbol={pair.symbol} timeframe={pair.timeframe} trend=knn"]


def test_process_pair_mat_redis_o_nhanh_hai_noi_len_cho_vong_noi_lai(monkeypatch: pytest.MonkeyPatch) -> None:
    job, pair, events, raw, writers, reader = _branch2_setup(Outcome("published", 1))
    _patch_two_runs(monkeypatch, events, [], raw_signal=1)

    def broken(method, symbol, trend_tf, count):
        raise redis.ConnectionError("mất kết nối")

    reader.read = broken
    with pytest.raises(redis.ConnectionError):
        configuration.process_pair(job, _Engine(CANDLES), raw, pair, now=NOW, trend_reader=reader, filtered=writers)
    assert events == ["run:raw", "write:raw"]  # nhánh 1 đã ghi xong trước đó


def test_process_pair_chua_co_xu_huong_thi_canh_bao_va_bo_khoi_do_chu_khong_dung_gi_thay_the(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    empty = pd.DataFrame(columns=trend_filtering.TIMELINE_COLUMNS)
    job, pair, events, raw, writers, reader = _branch2_setup(Outcome("published", 1), table=empty)
    _patch_two_runs(monkeypatch, events, [], raw_signal=1)
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        outcome = configuration.process_pair(job, _Engine(CANDLES), raw, pair, now=NOW, trend_reader=reader, filtered=writers)
    assert outcome == Outcome("published", 1) and events == ["run:raw", "write:raw", "read:knn", "read:tstat"]  # không chạy chiến lược lọc, không ghi
    missing = [m for m in _messages(caplog) if m.startswith("event=trend_missing")]
    cells = {cell.method: cell for cell in job.trend_cells[(pair.symbol, pair.timeframe)]}
    assert missing == [
        f"event=trend_missing strategy=ma_cross symbol={pair.symbol} timeframe={pair.timeframe} trend={m} trend_timeframe={cells[m].trend_tf}" for m in ("knn", "tstat")
    ]


# =============================================================================
# 3. run_lane: vòng lặp sự kiện.
# =============================================================================


P1, P2, P3 = Pair("US30", "H1"), Pair("US30", "H2"), Pair("US30", "H4")


def _patch_process(monkeypatch: pytest.MonkeyPatch, outcomes: dict | None = None, raises: dict | None = None) -> list[Pair]:
    seen: list[Pair] = []
    outcomes = {pair: list(queue) for pair, queue in (outcomes or {}).items()}

    def fake(job, engine, publisher, pair, now=None, **_branch2):
        seen.append(pair)
        if pair in (raises or {}):
            raise raises[pair]
        queue = outcomes.get(pair)
        if queue is None:
            return Outcome("published", 1)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(configuration, "process_pair", fake)
    return seen


def _run(job, engine, clock, stop, max_iterations: int = 400) -> None:
    iterations = {"n": 0}

    def should_stop() -> bool:
        iterations["n"] += 1
        return stop() or iterations["n"] > max_iterations

    configuration.run_lane(job, engine, object(), clock=clock, should_stop=should_stop)
    assert iterations["n"] <= max_iterations, "vòng lặp không dừng như mong đợi"


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name.startswith("core_engine.src.")]


def test_khoi_dong_quet_moi_cap_mot_lan_va_ghi_reconcile(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock()
    engine = LoopEngine(clock, [P1, P2, P3])
    seen = _patch_process(monkeypatch)
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        _run(_job(), engine, clock, lambda: len(seen) >= 3)
    assert seen == [P1, P2, P3]
    messages = _messages(caplog)
    assert messages[0] == "event=reconcile_start db=0 pairs=3"
    assert messages[1].startswith("event=reconcile_end pairs=3 published=3 duration_s=")


def test_khong_quet_khi_process_on_startup_tat(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock()
    engine = LoopEngine(clock, [P1, P2, P3])
    engine.batches = [[P2]]
    seen = _patch_process(monkeypatch)
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        _run(_job(process_on_startup=False), engine, clock, lambda: engine.polls >= 2)
    assert seen == [P2] and _messages(caplog) == []


def test_nhieu_su_kien_cua_cung_mot_cap_gop_thanh_mot_lan_xu_ly(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    engine = LoopEngine(clock, [P1, P2])
    engine.batches = [[P1, P2], [P2, P2]]  # P2 báo lại trong lúc còn chờ xử lý
    seen = _patch_process(monkeypatch)
    _run(_job(process_on_startup=False), engine, clock, lambda: engine.polls >= 3)
    assert seen == [P1, P2]


def test_su_kien_den_sau_khi_da_xu_ly_thi_xu_ly_lai(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    engine = LoopEngine(clock, [P1])
    engine.batches = [[P1], [], [P1]]
    seen = _patch_process(monkeypatch)
    _run(_job(process_on_startup=False), engine, clock, lambda: engine.polls >= 4)
    assert seen == [P1, P1]


def test_loi_cua_mot_cap_khong_chan_cap_khac(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock()
    engine = LoopEngine(clock, [P1, P2, P3])
    seen = _patch_process(monkeypatch, raises={P2: ValueError("nến hỏng")})
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        _run(_job(), engine, clock, lambda: len(seen) >= 3)
    assert seen == [P1, P2, P3]
    assert [m for m in _messages(caplog) if m.startswith("event=process_failed")] == ["event=process_failed symbol=US30 timeframe=H2"]
    assert next(m for m in _messages(caplog) if m.startswith("event=reconcile_end")).startswith("event=reconcile_end pairs=3 published=2")


def test_loi_redis_noi_len_cho_vong_noi_lai(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    engine = LoopEngine(clock, [P1])
    _patch_process(monkeypatch, raises={P1: redis.ConnectionError("mất kết nối")})
    with pytest.raises(redis.ConnectionError):
        _run(_job(), engine, clock, lambda: False)


def test_cap_chua_co_nen_tinh_la_da_xong_va_khong_canh_bao(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock()
    engine = LoopEngine(clock, [P1])
    seen: list[Pair] = []
    monkeypatch.setattr(configuration, "process_pair", lambda job, _engine, _publisher, pair, now=None, **_branch2: seen.append(pair))  # trả None: db0 chưa có nến của cặp
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        _run(_job(), engine, clock, lambda: len(seen) >= 1)
    assert next(m for m in _messages(caplog) if m.startswith("event=reconcile_end")).startswith("event=reconcile_end pairs=1 published=0")
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# =============================================================================
# 4. Kết nối lại và vòng đời.
# =============================================================================


class _FakePart:
    instances: ClassVar[list[_FakePart]] = []
    fail_subscribe_once = False

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.log: list[str] = []
        self.closed = False
        type(self).instances.append(self)

    def ping(self) -> None:
        self.log.append("ping")

    def check_keyspace_flags(self) -> str:
        self.log.append("flags")
        return "hK"

    def subscribe(self) -> int:
        self.log.append("subscribe")
        if _FakePart.fail_subscribe_once:
            _FakePart.fail_subscribe_once = False
            raise redis.ConnectionError("rớt lúc đăng ký")
        return len(self.kwargs["pairs"])

    def close(self) -> None:
        self.closed = True


class _FakeEngine(_FakePart):
    instances: ClassVar[list[_FakePart]] = []


class _FakePublisher(_FakePart):
    instances: ClassVar[list[_FakePart]] = []


class _FakeReader(_FakePart):
    instances: ClassVar[list[_FakePart]] = []


def _patch_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeEngine.instances, _FakePublisher.instances, _FakeReader.instances, _FakePart.fail_subscribe_once = [], [], [], False
    monkeypatch.setattr(data_engine, "DataEngine", _FakeEngine)
    monkeypatch.setattr(strategy_runner, "RawSignalWriter", _FakePublisher)
    monkeypatch.setattr(trend_filtering, "TrendReader", _FakeReader)


def test_run_forever_noi_kiem_co_dang_ky_roi_chay_vong_lap_va_dong_ket_noi(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _patch_parts(monkeypatch)
    ran = []

    def fake_run_lane(job, engine, publisher, **kwargs):
        ran.append((engine, publisher))
        raise SystemExit(0)

    monkeypatch.setattr(configuration, "run_lane", fake_run_lane)
    job = _job()
    with caplog.at_level(logging.INFO, logger="core_engine.src"), pytest.raises(SystemExit):
        configuration._run_forever(job)
    engine, publisher = _FakeEngine.instances[0], _FakePublisher.instances[0]
    assert engine.log == ["ping", "flags", "subscribe"] and publisher.log == ["ping"] and ran == [(engine, publisher)]
    assert engine.kwargs["pairs"] == job.pairs and engine.kwargs["db"] == 0 and publisher.kwargs["db"] == 0 and publisher.kwargs["port"] == 6380 and engine.kwargs["port"] == 6379  # mỗi module nhận đúng phần tham số của mình
    assert engine.closed and publisher.closed  # tắt (SystemExit) vẫn đóng kết nối
    assert _messages(caplog) == [
        "event=redis_connected input_db=0 output_db=0",
        "event=subscribed db=0 key_prefix=L_CANDLE notify_flags=hK pairs=64",
    ]


def test_run_forever_mat_redis_thi_cho_roi_noi_lai(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _patch_parts(monkeypatch)
    _FakePart.fail_subscribe_once = True
    slept: list[float] = []
    monkeypatch.setattr(configuration.time, "sleep", slept.append)

    def fake_run_lane(job, engine, publisher, **kwargs):
        raise SystemExit(0)

    monkeypatch.setattr(configuration, "run_lane", fake_run_lane)
    with caplog.at_level(logging.INFO, logger="core_engine.src"), pytest.raises(SystemExit):
        configuration._run_forever(_job())
    assert slept == [3.0] and len(_FakeEngine.instances) == 2 and len(_FakePublisher.instances) == 2
    assert all(part.closed for part in (*_FakeEngine.instances, *_FakePublisher.instances))
    assert any(m.startswith("event=redis_connection_lost error=rớt lúc đăng ký retry_in_s=3.0") for m in _messages(caplog))


def test_run_forever_co_nhanh_loc_thi_noi_them_doc_xu_huong_va_ghi_loc_roi_dong_het(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _patch_parts(monkeypatch)
    got: dict = {}

    def fake_run_lane(job, engine, publisher, **kwargs):
        got.update(kwargs, engine=engine, publisher=publisher)
        raise SystemExit(0)

    monkeypatch.setattr(configuration, "run_lane", fake_run_lane)
    job = _trend_job("ma_cross", ("knn", "tstat"))
    with caplog.at_level(logging.INFO, logger="core_engine.src"), pytest.raises(SystemExit):
        configuration._run_forever(job)
    reader, filtered = _FakeReader.instances[0], got["filtered"]
    assert got["trend_reader"] is reader and set(filtered) == {"knn", "tstat"} and got["publisher"] is _FakePublisher.instances[0]
    assert (reader.kwargs["port"], reader.kwargs["db"], reader.kwargs["key_prefix"]) == (6380, 1, "L_TREND")  # xu hướng ở db trend của Redis đầu ra
    assert (got["publisher"].kwargs["port"], got["publisher"].kwargs["db"]) == (6380, 0)  # nhánh 1 vẫn ở db0
    for method, prefix in (("knn", "L_DE_KNN"), ("tstat", "L_DE_TSTAT")):
        assert (filtered[method].kwargs["port"], filtered[method].kwargs["db"], filtered[method].kwargs["key_prefix"], filtered[method].kwargs["source"]) == (6380, 2, prefix, method)
    assert reader.log == ["ping"] and all(writer.log == ["ping"] for writer in filtered.values())
    assert reader.closed and all(writer.closed for writer in filtered.values()) and got["engine"].closed and got["publisher"].closed
    assert _messages(caplog)[0] == "event=redis_connected input_db=0 output_db=0 trend_db=1 filtered_db=2"


def test_run_forever_khong_co_nhanh_loc_thi_khong_noi_doc_xu_huong(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_parts(monkeypatch)
    got: dict = {}

    def fake_run_lane(job, engine, publisher, **kwargs):
        got.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr(configuration, "run_lane", fake_run_lane)
    with pytest.raises(SystemExit):
        configuration._run_forever(_job())
    assert got == {"trend_reader": None, "filtered": {}} and _FakeReader.instances == [] and len(_FakePublisher.instances) == 1


def _patch_run_environment(monkeypatch: pytest.MonkeyPatch, config: dict) -> list[str]:
    """Cho ``run`` chạy không đụng file log thật, không bật nhớ đệm thật, với cấu hình thử."""
    from core_engine.src import log

    events: list[str] = []
    monkeypatch.setattr(configuration, "CONFIG", config)
    monkeypatch.setattr(log, "configure_logging", lambda level, name=None: events.append(f"logging:{level}:{name}"))
    monkeypatch.setattr(log, "set_log_level", lambda level: events.append(f"level:{level}"))
    monkeypatch.setattr(strategy_runner, "enable_memo", lambda maxsize=256: events.append(f"memo:{maxsize}"))
    return events


def test_run_cau_hinh_hop_le_ghi_log_khoi_dong_roi_chay(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    events = _patch_run_environment(monkeypatch, lane_config())
    ran = []
    monkeypatch.setattr(configuration, "_run_forever", ran.append)
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        assert configuration.run("ma_cross") == 0
    assert events == ["logging:INFO:ma_cross", "level:INFO", "memo:256"] and ran[0].strategy == "ma_cross"
    messages = _messages(caplog)
    assert messages[0] == "event=startup strategies=ma_cross log_level=INFO redis_input_db=0 redis_output_db=0 output_sources=raw lane=ma_cross"
    assert messages[1] == f"event=input_source name=default host=127.0.0.1 port=6379 symbols={len(GROUP_SYMBOLS)}"
    assert messages[2] == "event=output_source source=raw key_prefix=L_DE channel_prefix=L_CHANNEL"
    assert "event=alternating_filter strategy=ma_cross enabled=True" in messages and "event=memo enabled=true maxsize=256" in messages
    assert not any(m.startswith("event=trend_filter") for m in messages)  # tiến trình chiến lược không còn lọc trend


def test_run_co_nhanh_loc_ghi_nguon_va_so_o_trend_vao_log_khoi_dong(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    config = trend_config(("ma_cross",), ("knn", "tstat"))
    _patch_run_environment(monkeypatch, config)
    monkeypatch.setattr(configuration, "_run_forever", lambda job: None)
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        assert configuration.run("ma_cross") == 0
    messages = _messages(caplog)
    assert messages[0] == "event=startup strategies=ma_cross log_level=INFO redis_input_db=0 redis_output_db=0 output_sources=raw,knn,tstat lane=ma_cross"
    assert "event=output_source source=knn key_prefix=L_DE_KNN channel_prefix=L_CHANNEL_KNN" in messages
    assert "event=output_source source=tstat key_prefix=L_DE_TSTAT channel_prefix=L_CHANNEL_TSTAT" in messages
    cells = configuration.build_lane_job(config, "ma_cross").trend_cells
    assert f"event=trend_cells cells={len(cells)} trend_db=1 filtered_db=2" in messages


def test_run_voi_nguon_nen_them_dat_ten_log_rieng_va_ghi_nguon_vao_log(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    events = _patch_run_environment(monkeypatch, source_config(("ma_cross",), ("knn",)))
    ran = []
    monkeypatch.setattr(configuration, "_run_forever", ran.append)
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        assert configuration.run("ma_cross", "og8") == 0
    assert events[0] == "logging:INFO:ma_cross_og8"  # file log riêng: de_run_ma_cross_og8.log, không đè log của nhóm mặc định
    assert ran[0].input_name == "og8" and {s for s, _ in ran[0].pairs} == {"EURUSD", "FR40"}
    messages = _messages(caplog)
    assert messages[0].endswith("lane=ma_cross_og8") and messages[1] == "event=input_source name=og8 host=10.11.12.8 port=6380 symbols=2"


def test_run_tat_bang_cong_tac_thi_ghi_log_roi_thoat_em(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    config = lane_config()
    config["redis"]["enabled"] = False
    _patch_run_environment(monkeypatch, config)
    monkeypatch.setattr(configuration, "_run_forever", lambda job: pytest.fail("không chạy khi redis.enabled=false"))
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        assert configuration.run("combo") == 0
    assert "event=disabled reason=redis_disabled_by_config" in _messages(caplog)


def test_run_redis_enabled_sai_kieu_la_config_error(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    config = lane_config()
    config["redis"]["enabled"] = "yes"  # phải là true/false thật
    _patch_run_environment(monkeypatch, config)
    monkeypatch.setattr(configuration, "_run_forever", lambda job: pytest.fail("không chạy khi cấu hình sai"))
    with caplog.at_level(logging.CRITICAL, logger="core_engine.src"):
        assert configuration.run("combo") == 1
    assert any("event=config_error" in m and "redis.enabled must be true or false" in m for m in _messages(caplog))


def test_run_cau_hinh_sai_thi_ghi_config_error_va_tra_1(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    config = copy.deepcopy(lane_config())
    config["redis"]["input"].pop("snapshot_bars")
    _patch_run_environment(monkeypatch, config)
    with caplog.at_level(logging.CRITICAL, logger="core_engine.src"):
        assert configuration.run("combo") == 1
    assert any("event=config_error" in m and "redis.input.snapshot_bars" in m for m in _messages(caplog))


def test_run_thieu_co_keyspace_la_config_error(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _patch_run_environment(monkeypatch, lane_config())

    def broken(job):
        raise RuntimeError("Redis notify-keyspace-events thiếu lớp ['h']")

    monkeypatch.setattr(configuration, "_run_forever", broken)
    with caplog.at_level(logging.CRITICAL, logger="core_engine.src"):
        assert configuration.run("combo") == 1
    assert any("event=config_error" in m and "notify-keyspace-events" in m for m in _messages(caplog))


def test_run_dung_em_khi_nhan_sigterm_hoac_ctrl_c(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _patch_run_environment(monkeypatch, lane_config())
    for stop in (SystemExit(0), KeyboardInterrupt()):
        def stopping(job, stop=stop):
            raise stop

        monkeypatch.setattr(configuration, "_run_forever", stopping)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="core_engine.src"):
            assert configuration.run("combo") == 0
        assert "event=stopped" in _messages(caplog)


# =============================================================================
# 5. main.py
# =============================================================================


def test_main_parse_args_chuan_hoa_ten_va_bat_buoc_strategy() -> None:
    assert main.parse_args(["--strategy", " MA_Cross "]).strategy == "ma_cross"
    assert main.parse_args(["--trend", " KNN "]).trend == "knn" and main.parse_args(["--trend", "knn"]).strategy is None
    for bad in ([], ["--strategy", "ma-cross"], ["--strategy", "../x"], ["--strategy", ""], ["--trend", "k-n"], ["--strategy", "combo", "--trend", "knn"]):
        with pytest.raises(SystemExit):
            main.parse_args(bad)


def test_main_bat_sigterm_roi_giao_cho_configuration_run(monkeypatch: pytest.MonkeyPatch) -> None:
    handlers = {}
    monkeypatch.setattr(main.signal, "signal", lambda signum, handler: handlers.update({signum: handler}))
    called = []
    monkeypatch.setattr(configuration, "run", lambda strategy, input_name="": called.append((strategy, input_name)) or 7)
    assert main.main(["--strategy", "ema_reach"]) == 7 and called == [("ema_reach", "")]
    assert main.main(["--strategy", "ma_cross.og8"]) == 7 and called[-1] == ("ma_cross", "og8")  # tên chiến lược có gạch dưới vẫn tách đúng ở dấu chấm
    assert handlers[signal.SIGTERM] is main._handle_shutdown_signal


def test_main_sigterm_handler_raise_system_exit_0_va_ghi_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="core_engine.src"), pytest.raises(SystemExit) as raised:
        main._handle_shutdown_signal(signal.SIGTERM, None)
    assert raised.value.code == 0 and "event=shutdown_signal signal=SIGTERM" in _messages(caplog)
