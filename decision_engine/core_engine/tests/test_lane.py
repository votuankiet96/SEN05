"""Test điều phối hai loại tiến trình của DE (configuration.py: process_frame / run_runner_lane / run_runner của runner; process_cell / run_publisher_lane / run_publisher của publisher) và lối vào main.py — hermetic.

Có hai nhóm test. (1) ĐƯỜNG ĐI: runner ghi cấu phần vào db0/db1 giả, publisher đọc lại bằng DataEngine thật (chế độ cấu phần, hai db) và quyết định theo ô; kết quả phải khớp đúng pipeline một-tiến-trình cũ
(bản thô và bản lọc trend, kể cả tín hiệu DỜI), và đúng luật nến đóng + chờ + đọc CONTEXT. (2) VÒNG LẶP SỰ KIỆN và vòng đời: engine / bộ ghi giả, đồng hồ giả (ĐỒNG HỒ chỉ tiến khi vòng lặp "chờ"):
điều cần chứng minh là CÁCH ĐIỀU PHỐI (gộp sự kiện, quét khởi động theo thứ tự, cô lập lỗi, nối lại), không phải chiến lược (đã có test riêng).
"""

from __future__ import annotations

import copy
import dataclasses
import logging
import signal
from typing import ClassVar

import numpy as np
import pandas as pd
import pytest
import redis
from engine_support import (
    FakeClock,
    FakeSnapshots,
    LoopEngine,
    RecordingRedis,
    cell_config,
    decode_eval,
    make_bars,
    run_strategy,
)

from core_engine.src import (
    configuration,
    data_engine,
    main,
    signal_publisher,
    strategy_runner,
)
from core_engine.src.configuration import Cell
from core_engine.src.data_engine import DataEngine, Pair
from core_engine.src.rules.trend_tf import build_trend_frame
from core_engine.src.signal_publisher import Outcome, RawSignalWriter

SYMBOL = "HK50"


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name.startswith("core_engine.src.")]


# =============================================================================
# 1. Đường đi: runner ghi db0/db1 -> publisher đọc, ghép, quyết định, ghi db2.
# =============================================================================


def _resample(base: pd.DataFrame, rule: str) -> pd.DataFrame:
    grouped = base.set_index("bartime").resample(rule, label="left", closed="left")
    out = grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
    out["volume"] = np.nan
    out["bartime"] = out["bartime"].astype("datetime64[us]")
    return out


def _run_runner(config: dict, candles: dict[str, pd.DataFrame]) -> tuple[RecordingRedis, RecordingRedis]:
    """Chạy ``process_frame`` cho mọi khung trong ``candles`` (khung lớn trước, như quét khởi động) với bộ ghi thật trên hai db giả: db0 (TRIGGER, CONTEXT) và db1 (TREND)."""
    job = configuration.build_runner_job(config)
    db0, db1 = RecordingRedis(emulate=True), RecordingRedis(emulate=True)
    writer = strategy_runner.ComponentWriter(**job.sink, client=db0)
    inputs_writer = strategy_runner.ComponentWriter(**job.inputs_sink, client=db1)
    engine = FakeSnapshots({Pair(SYMBOL, tf): bars for tf, bars in candles.items()})
    for pair in sorted(engine.frames, key=lambda p: -data_engine_minutes(p.timeframe)):
        configuration.process_frame(job, engine, writer, inputs_writer, pair)
    return db0, db1


def data_engine_minutes(timeframe: str) -> int:
    from core_engine.src.indicator import timeframe_minutes

    return timeframe_minutes(timeframe)


def _de_publish(key: str, tf: str, candles: dict[str, pd.DataFrame], now: pd.Timestamp, config: dict | None = None):
    """Toàn bộ đường mới cho MỘT ô: runner ghi db0/db1 -> publisher đọc (DataEngine hai db) -> ``process_cell``. Trả về (outcome, các lệnh ghi bản thô, {khối: các lệnh ghi bản lọc})."""
    config = config or cell_config()
    db0, db1 = _run_runner(config, candles)
    job = configuration.build_publisher_job(config, [SYMBOL])
    engine = DataEngine(**job.source, pairs=job.pairs, client=db0, clients={1: db1}, events_client=db0)
    raw_out = RecordingRedis(emulate=True)
    filtered_out = {method: RecordingRedis(emulate=True) for method in job.filtered_sinks}
    publisher = RawSignalWriter(**job.sink, client=raw_out)
    filtered = {method: RawSignalWriter(**params, client=filtered_out[method]) for method, params in job.filtered_sinks.items()}
    outcome = configuration.process_cell(job, engine, publisher, Cell(SYMBOL, key, tf), now, filtered=filtered)
    return outcome, [decode_eval(c) for c in raw_out.calls], {m: [decode_eval(c) for c in r.calls] for m, r in filtered_out.items()}


def _expected(job: configuration.PublisherJob, source: str, key: str, tf: str, latest: pd.Series, now: pd.Timestamp) -> list[dict]:
    """Bản ghi mà pipeline THAM CHIẾU (một bảng, trend gộp tại chỗ) sẽ ghi cho dòng ``latest``, dùng đúng bộ ghi tín hiệu."""
    out = RecordingRedis(emulate=True)
    sink = job.sink if source == "raw" else job.filtered_sinks[source]
    spec = configuration.get_strategy(key)
    RawSignalWriter(**sink, client=out).publish_latest(
        strategy=key, order_type=spec.order_type, validity=job.cells[Cell(SYMBOL, key, tf)].validity, timeframe=tf, symbol=SYMBOL, latest=latest, now=now,
    )
    return [decode_eval(c) for c in out.calls]


def _close_plus(bartime: pd.Timestamp, tf: str, seconds: int = 60) -> pd.Timestamp:
    return (pd.Timestamp(bartime) + pd.Timedelta(minutes=data_engine_minutes(tf)) + pd.Timedelta(seconds=seconds)).tz_localize("UTC")


@pytest.mark.parametrize(
    ("key", "tf", "freq"),
    [("combo", "H1", "h"), ("ma_cross", "M30", "30min"), ("macd_break", "H1", "h"), ("ema_reach", "M30", "30min"), ("ichimoku", "H1", "h")],
)
def test_ban_tho_cua_o_giong_het_pipeline_tham_chieu_mot_bang(key: str, tf: str, freq: str) -> None:
    """Runner ghi TRIGGER (tín hiệu thô sau lọc trùng) rồi publisher chỉ đọc lại: bản thô ghi ra phải GIỐNG HỆT lần ghi dòng cuối của pipeline cũ (cắt nến tới một nến có tín hiệu để so cả bản ghi)."""
    bars = make_bars(900, freq)
    reference = run_strategy(key, symbol=SYMBOL, tf=tf, bars=bars)
    index = int(reference.index[reference["signal"] != 0][-1])
    now = _close_plus(bars["bartime"].iloc[index], tf)
    outcome, raw, filtered = _de_publish(key, tf, {tf: bars.iloc[: index + 1].reset_index(drop=True)}, now)
    job = configuration.build_publisher_job(cell_config(), [SYMBOL])
    assert len(raw) == 1 and outcome == Outcome("published", 1 + sum(len(calls) for calls in filtered.values()))
    assert raw == _expected(job, "raw", key, tf, reference.iloc[index], now)  # key LIST, mốc, kênh, message và mọi trường HASH
    if key != "ichimoku":
        assert all(not calls or calls[0]["list_key"].startswith("L_DE_") for calls in filtered.values())


@pytest.mark.parametrize(
    ("key", "tf", "method", "trend_tf", "compare_comment"),
    [
        ("ma_cross", "M30", "cloud", "H1", True),  # khối DỜI: khung trend H1 lớn hơn khung vào lệnh
        ("ema_reach", "M30", "breakout", "M30", True),  # khối DỜI cùng khung vào lệnh
        ("combo", "H1", "cloud", "H1", False),  # combo dời mượn lý do riêng của nó (ở pipeline cũ), chỉ so các trường còn lại
        ("ma_cross", "M30", "knn", "H1", True),  # khối KHÔNG dời
    ],
)
def test_ban_loc_trend_cua_o_giong_het_pipeline_tham_chieu_ke_ca_tin_hieu_doi(key: str, tf: str, method: str, trend_tf: str, compare_comment: bool) -> None:
    base = make_bars(3600, "10min")
    rule = {"M30": "30min", "H1": "1h"}
    bars, trend_bars = _resample(base, rule[tf]), _resample(base, rule[trend_tf])
    spec = configuration.get_strategy(key)
    overrides = {"TREND_METHODS": method, "TREND_FILTER_ENABLED": True}
    if not configuration.uses_entry_timeframe((method,)):
        overrides["TREND_TF"] = trend_tf
    params = spec.normalize_params(overrides, SYMBOL, tf)
    reference = run_strategy(key, symbol=SYMBOL, tf=tf, bars=bars, overrides=overrides, trend=build_trend_frame(trend_bars, params))
    shifted = (reference["signal"] != 0) & (reference["raw_signal"] == 0)
    fired = reference.index[reference["signal"] != 0]
    index = int((shifted[shifted].index if shifted.any() and method != "knn" else fired)[-1])
    if method != "knn":
        assert shifted.any(), "dữ liệu thử phải có tín hiệu dời để bài test có nghĩa"
    now = _close_plus(bars["bartime"].iloc[index], tf)
    candles = {tf: bars.iloc[: index + 1].reset_index(drop=True)}
    if trend_tf != tf:
        candles[trend_tf] = trend_bars  # khung trend lớn hơn: giữ đủ nến (nến trend sau giờ đóng của nến vào lệnh bị luật nến đóng bỏ qua)
    outcome, _raw, filtered = _de_publish(key, tf, candles, now)
    job = configuration.build_publisher_job(cell_config(), [SYMBOL])
    expected = _expected(job, method, key, tf, reference.iloc[index], now)
    assert len(filtered[method]) == 1 and outcome.published >= 1
    got = filtered[method][0]
    if not compare_comment:
        got, expected = [{**item, "fields": {k: v for k, v in item["fields"].items() if k != "comment"}, "message": ""} for item in (got, expected[0])], None
        assert got[0] == got[1]
    else:
        assert filtered[method] == expected
    if method != "knn":
        assert filtered[method][0]["fields"]["comment"].endswith("; aligned with trend reference")


def _hourly(n: int, start: str = "2026-11-09 00:00") -> pd.DataFrame:
    bartime = pd.date_range(start, periods=n, freq="h").astype("datetime64[us]")
    return pd.DataFrame({"bartime": bartime, "close_time": (bartime + pd.Timedelta(hours=1)).astype("datetime64[us]")})


def _trigger(signals: list[int], setups: list[int] | None = None, start: str = "2026-11-09 00:00") -> pd.DataFrame:
    frame = _hourly(len(signals), start)
    n = len(signals)
    frame["signal"] = [float(s) for s in signals]
    frame["setup"] = [float(s) for s in (setups if setups is not None else signals)]
    frame["open"] = np.arange(n) + 100.0
    frame["high"] = np.arange(n) + 102.0
    frame["low"] = np.arange(n) + 99.0
    frame["close"] = np.arange(n) + 101.0
    frame["atr"] = np.arange(n) + 5.0
    frame["reason"] = ["fast SMA below slow SMA; MACD histogram < 0" if s else "" for s in signals]
    return frame


def _context(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"bartime": frame["bartime"], "close_time": frame["close_time"], "macd_h": np.arange(len(frame)) - 0.5, "ma": np.arange(len(frame)) + 1000.0})


def _trend(bias: list[int], start: str = "2026-11-09 00:00", hours: int = 1) -> pd.DataFrame:
    bartime = pd.date_range(start, periods=len(bias), freq=f"{hours}h").astype("datetime64[us]")
    return pd.DataFrame({"bartime": bartime, "close_time": (bartime + pd.Timedelta(hours=hours)).astype("datetime64[us]"), "trend_bias": [float(b) for b in bias]})


class _Sink:
    """Bộ ghi tín hiệu thay thế: ghi lại mọi lần ``publish_latest`` (không Redis)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def publish_latest(self, **kwargs):
        self.calls.append(kwargs)
        return Outcome("published", 1) if int(kwargs["latest"]["signal"]) != 0 else Outcome("no_signal", 0)  # như RawSignalWriter: dòng không có tín hiệu thì không ghi

    @property
    def fired(self) -> list[dict]:
        """Các lần gọi mà dòng cuối CÓ tín hiệu (bộ ghi thật chỉ ghi những lần này)."""
        return [call for call in self.calls if int(call["latest"]["signal"]) != 0]


def _cell_world(key: str = "ma_cross", tf: str = "H1", config: dict | None = None):
    job = configuration.build_publisher_job(config or cell_config(), [SYMBOL])
    return job, job.cells[Cell(SYMBOL, key, tf)]


def _process(job, cell_job, frames: dict[Pair, pd.DataFrame], now: str):
    engine = FakeSnapshots(frames)
    publisher, filtered = _Sink(), {method: _Sink() for method in job.filtered_sinks}
    outcome = configuration.process_cell(job, engine, publisher, cell_job.plan.cell, pd.Timestamp(now, tz="UTC"), filtered=filtered)
    return outcome, publisher, filtered, engine


def _split(trigger: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Bảng từng nến (kiểu cũ: signal, setup, giá, atr, reason ở MỌI nến) -> hai lát cắt runner ghi: SETUP (mọi nến) và TRIGGER (chỉ nến có tín hiệu; không có nến nào thì không có LIST)."""
    parts = {"setup": strategy_runner.setup_view(trigger)}
    events = strategy_runner.trigger_events(trigger)
    if not events.empty:
        parts["trigger"] = events
    return parts


def _bars(cell_job, trigger: pd.DataFrame) -> dict[Pair, pd.DataFrame]:
    """Các bảng db0 của ô cho một bảng từng nến: SETUP và (nếu có nến nào có tín hiệu) TRIGGER."""
    parts = _split(trigger)
    frames = {cell_job.setup: parts["setup"]}
    if "trigger" in parts:
        frames[cell_job.trigger] = parts["trigger"]
    return frames


def _frames(cell_job, trigger: pd.DataFrame, trends: dict[str, pd.DataFrame] | None = None, context: pd.DataFrame | None = None) -> dict[Pair, pd.DataFrame]:
    frames = {**_bars(cell_job, trigger), cell_job.context: context if context is not None else _context(trigger)}
    for leg in cell_job.legs:
        if trends and leg.method in trends:
            frames[leg.pair] = trends[leg.method]
    return frames


def test_o_cat_doc_ca_trigger_context_va_trend_cua_tung_khoi_dung_cap_cua_o() -> None:
    job, cell_job = _cell_world()
    trigger = _trigger([0, 0, -1])
    _, _, _, engine = _process(job, cell_job, _frames(cell_job, trigger, {leg.method: _trend([1, 1, 1, 1, 1, 1], "2026-11-08 18:00") for leg in cell_job.legs}), "2026-11-09 03:01")
    assert engine.asked[0] == cell_job.setup and {cell_job.trigger, cell_job.context} <= set(engine.asked) and {leg.pair for leg in cell_job.legs} <= set(engine.asked)
    assert cell_job.context == Pair(SYMBOL, "H1", "CONTEXT")  # context mặc định: chính khung trigger


def test_ban_tho_ghi_khi_nen_cuoi_co_tin_hieu_va_ban_loc_khong_doi_chi_ghi_khi_cung_chieu_trend() -> None:
    job, cell_job = _cell_world()
    knn = next(leg for leg in cell_job.legs if leg.method == "knn")
    trigger = _trigger([0, 0, -1])  # nến cuối (mở 02:00, đóng 03:00) có tín hiệu thô SELL
    for bias, expect in ((-1, 1), (1, 0), (0, 0)):
        trend = _trend([bias] * 8, "2026-11-07 00:00", hours=knn.length // pd.Timedelta(hours=1))
        outcome, publisher, filtered, _ = _process(job, cell_job, _frames(cell_job, trigger, {"knn": trend}), "2026-11-09 03:01")
        assert len(publisher.fired) == 1 and len(filtered["knn"].fired) == expect, bias  # khối knn không dời: chỉ giữ khi cùng chiều
        assert outcome is not None and outcome.published == 1 + expect


def test_chua_co_context_cua_nen_trigger_moi_nhat_hoac_chua_co_nen_nao_thi_chua_quyet_dinh() -> None:
    job, cell_job = _cell_world()
    trigger = _trigger([0, 0, -1])
    short_context = _context(trigger).iloc[:2]  # CONTEXT chưa tới nến cuối
    for frames in (
        {**_bars(cell_job, trigger), cell_job.context: short_context},
        _bars(cell_job, trigger),  # chưa có CONTEXT nào
        {cell_job.context: _context(trigger)},  # chưa có SETUP nào (nến mới nhất lấy từ SETUP)
        {cell_job.setup: _split(trigger)["setup"].iloc[0:0], cell_job.context: _context(trigger)},
    ):
        outcome, publisher, filtered, _ = _process(job, cell_job, frames, "2026-11-09 03:01")
        assert outcome is None and publisher.calls == [] and all(sink.calls == [] for sink in filtered.values())


def test_khung_context_lon_hon_duoc_doc_va_cho_khi_nen_context_dong_dung_luc_chua_ve() -> None:
    """context_tf đổi sang khung lớn hơn: ô đọc ĐÚNG CONTEXT khung đó, ghép theo nến đóng, chờ nếu nến context phải đóng đúng lúc mà chưa về (chưa áp điều kiện nào lên context)."""
    config = cell_config()
    config["strategies"]["macd_break"]["context_tf_by_symbol"][SYMBOL]["M10"] = "H1"
    job, cell_job = _cell_world("macd_break", "M10", config)
    assert cell_job.context == Pair(SYMBOL, "H1", "CONTEXT") and cell_job.context_length == pd.Timedelta(hours=1)
    bartime = pd.date_range("2026-11-09 00:00", periods=12, freq="10min").astype("datetime64[us]")
    trigger = _trigger([0] * 11 + [-1])[["signal", "setup", "open", "high", "low", "close", "atr", "reason"]].assign(
        bartime=bartime, close_time=(bartime + pd.Timedelta(minutes=10)).astype("datetime64[us]")
    )  # nến cuối mở 01:50, đóng 02:00: trùng giờ đóng nến H1 mở 01:00
    h1 = _hourly(1, "2026-11-09 00:00").assign(macd_h=[3.0], ma=[1000.0])  # mới có nến H1 đóng 01:00, còn thiếu nến đóng 02:00
    outcome, publisher, _, engine = _process(job, cell_job, {**_bars(cell_job, trigger), cell_job.context: h1}, "2026-11-09 02:01")
    assert outcome is None and publisher.calls == [] and cell_job.context in engine.asked  # chờ
    h1_full = pd.concat([h1, _hourly(1, "2026-11-09 01:00").assign(macd_h=[4.0], ma=[1001.0])], ignore_index=True)
    outcome, publisher, _, _ = _process(job, cell_job, {**_bars(cell_job, trigger), cell_job.context: h1_full}, "2026-11-09 02:01")
    assert outcome is not None and len(publisher.calls) == 1  # đủ: quyết định và ghi bản thô
    stale = _hourly(1, "2026-11-08 20:00").assign(macd_h=[1.0], ma=[1.0])  # nến context cũ, không rơi đúng lưới đóng (nghỉ phiên): dùng luôn, không chờ
    off_grid = trigger.assign(bartime=trigger["bartime"] + pd.Timedelta(minutes=5), close_time=trigger["close_time"] + pd.Timedelta(minutes=5))
    assert configuration.process_cell(job, FakeSnapshots({**_bars(cell_job, off_grid), cell_job.context: stale}), _Sink(), cell_job.plan.cell, pd.Timestamp("2026-11-09 02:06", tz="UTC"), filtered={}) is not None


def test_nen_trend_dong_dung_luc_chua_ve_thi_chi_hoan_ban_loc_con_ban_tho_van_ghi_roi_ban_loc_ghi_khi_nen_ve() -> None:
    job, cell_job = _cell_world("ma_cross", "H1")
    assert next(leg for leg in cell_job.legs if leg.method == "cloud").pair.timeframe == "H1"  # khung trend H1 = khung trigger: nến trend đóng cùng lúc nến trigger
    trigger = _trigger([0, 0, -1])
    late = _trend([0, 0], "2026-11-09 00:00")  # nến trend mở 02:00 (đóng 03:00) chưa về
    outcome, publisher, filtered, _ = _process(job, cell_job, _frames(cell_job, trigger, {"cloud": late}), "2026-11-09 03:01")
    assert len(publisher.fired) == 1 and filtered["cloud"].calls == [] and outcome is not None  # bản thô không phụ thuộc trend
    arrived = _trend([0, 0, -1], "2026-11-09 00:00")
    _, _, filtered, _ = _process(job, cell_job, _frames(cell_job, trigger, {"cloud": arrived}), "2026-11-09 03:01")
    assert len(filtered["cloud"].fired) == 1  # nến trend về thì bản lọc ghi


def test_nen_trend_cu_khong_roi_dung_luoi_dong_thi_dung_luon_khong_cho() -> None:
    job, cell_job = _cell_world("ma_cross", "H1")
    knn = next(leg for leg in cell_job.legs if leg.method == "knn")  # khung trend lớn: thứ Sáu đóng, cuối tuần không có nến
    trigger = _trigger([0, 0, -1], start="2026-11-09 00:00")
    stale = _trend([-1], "2026-11-06 20:00", hours=knn.length // pd.Timedelta(hours=1))
    _, publisher, filtered, _ = _process(job, cell_job, _frames(cell_job, trigger, {"knn": stale}), "2026-11-09 03:01")
    assert len(publisher.fired) == 1 and len(filtered["knn"].fired) == 1  # (03:00 - lúc đóng) không phải bội của độ dài khung -> không có nến nào đang đến hạn


def test_khoi_khong_doi_chi_xet_ban_loc_khi_nen_cuoi_co_tin_hieu_tho_khoi_doi_luon_xet() -> None:
    job, cell_job = _cell_world("ma_cross", "H1")
    # đợt SELL mở ở nến 0 (trend trung tính), setup còn -1 ở nến 1 và 2; trend cloud đổi -1 ở nến 2 -> tín hiệu DỜI ở nến cuối; knn không dời nên nến cuối (signal 0) không xét
    trigger = _trigger([-1, 0, 0], setups=[-1, -1, -1])
    trends = {method: _trend([0, 0, -1], "2026-11-09 00:00") for method in ("cloud", "breakout", "tstat", "zigzag")}
    trends["knn"] = _trend([-1, -1, -1], "2026-11-09 00:00")
    outcome, publisher, filtered, _ = _process(job, cell_job, _frames(cell_job, trigger, trends), "2026-11-09 03:01")
    assert publisher.fired == []  # bản thô: nến cuối không có tín hiệu thô
    assert filtered["knn"].calls == [] and filtered["zigzag"].calls == []  # không dời -> không xét
    shifted = filtered["cloud"].fired
    assert len(shifted) == 1 and int(shifted[0]["latest"]["signal"]) == -1 and shifted[0]["latest"]["bartime"] == pd.Timestamp("2026-11-09 02:00")
    latest = shifted[0]["latest"]
    assert latest["entry_price"] == latest["close"] == 103.0 and latest["atr"] == 7.0  # entry và atr của đúng nến bắn (nến dời), không phải nến thô
    assert latest["signal_reason"] == "fast SMA below slow SMA; MACD histogram < 0; aligned with trend reference"  # lý do mượn từ nến thô mở đợt
    assert outcome is not None and outcome.published == 3  # cloud, breakout và tstat cùng dời; knn, zigzag không


def test_o_khong_co_trend_ichimoku_chi_co_ban_tho() -> None:
    job, cell_job = _cell_world("ichimoku", "H1")
    assert cell_job.legs == ()
    outcome, publisher, filtered, _ = _process(job, cell_job, _frames(cell_job, _trigger([0, 0, 1])), "2026-11-09 03:01")
    assert len(publisher.fired) == 1 and all(sink.calls == [] for sink in filtered.values()) and outcome.published == 1


def test_cho_qua_han_hieu_luc_thi_canh_bao_va_con_trong_han_thi_chi_debug(caplog: pytest.LogCaptureFixture) -> None:
    job, cell_job = _cell_world("ma_cross", "H1")
    trigger = _trigger([0, 0, -1])  # nến cuối đóng 03:00; ma_cross còn hiệu lực 600 giây
    late = _trend([0, 0], "2026-11-09 00:00")  # nến trend H1 (cloud) đóng 03:00 chưa về
    with caplog.at_level(logging.DEBUG, logger="core_engine.src"):
        _process(job, cell_job, _frames(cell_job, trigger, {"cloud": late}), "2026-11-09 03:05")
        assert [r.levelno for r in caplog.records if "waiting_for=trend_cloud" in r.getMessage()] == [logging.DEBUG]
        caplog.clear()
        _process(job, cell_job, _frames(cell_job, trigger, {"cloud": late}), "2026-11-09 03:11")
    expired = [r for r in caplog.records if "reference_wait_expired" in r.getMessage() and "waiting_for=trend_cloud" in r.getMessage()]
    assert len(expired) == 1 and expired[0].levelno == logging.WARNING and f"symbol={SYMBOL} strategy=ma_cross timeframe=H1" in expired[0].getMessage()


# --- process_frame: một cặp ở runner -----------------------------------------------------------------------------------


def _pair_job(**changes) -> configuration.RunnerJob:
    return dataclasses.replace(configuration.build_runner_job(cell_config()), **{"trend_needed": None, **changes})  # None = tính mọi khối ở mọi khung (test riêng cho trend_needed)


def _frame_world(job: configuration.RunnerJob, bars: pd.DataFrame, tf: str = "H1"):
    db0, db1 = RecordingRedis(emulate=True), RecordingRedis(emulate=True)
    writer = strategy_runner.ComponentWriter(**job.sink, client=db0)
    inputs_writer = strategy_runner.ComponentWriter(**job.inputs_sink, client=db1)
    pair = Pair(SYMBOL, tf)
    return db0, db1, writer, inputs_writer, FakeSnapshots({pair: bars}), pair


def _rows(redis_fake: RecordingRedis) -> dict[str, dict[str, dict[str, str]]]:
    rows: dict[str, dict[str, dict[str, str]]] = {}
    for call in redis_fake.calls:
        item = decode_eval(call)
        rows.setdefault(item["list_key"], {})[item["stamp"]] = item["fields"]
    return rows


def _events_of(job: configuration.RunnerJob, bars: pd.DataFrame, key: str) -> pd.DataFrame:
    """Các nến CÓ tín hiệu thô của một chiến lược trên bảng nến ``bars`` (khung H1): thứ TRIGGER phải chứa, tính độc lập với đường ghi."""
    spec = configuration.get_strategy(key)
    table = strategy_runner.trigger_table(spec, bars, spec.normalize_params(None, SYMBOL, "H1"), SYMBOL, "H1")
    return strategy_runner.trigger_events(table)


def test_process_frame_ghi_setup_o_db0_va_trigger_context_trend_o_db1_cho_moi_chien_luoc_va_khoi() -> None:
    job = _pair_job()
    bars = make_bars(300, "h")
    db0, db1, writer, inputs_writer, engine, pair = _frame_world(job, bars)
    order: list[tuple[str, str]] = []  # thứ tự ghi qua CẢ HAI db: (vai trò, định danh)
    for target in (writer, inputs_writer):
        real = target.write

        def recording(symbol, role, timeframe, table, qualifier="", real=real):
            order.append((role, qualifier))
            return real(symbol, role, timeframe, table, qualifier=qualifier)

        target.write = recording
    outcome = configuration.process_frame(job, engine, writer, inputs_writer, pair)
    tokens = {key: signal_publisher.strategy_token(key) for key in job.strategies}
    events = {key: _events_of(job, bars, key) for key in job.strategies}
    assert sum(len(frame) for frame in events.values()) > 0  # dữ liệu thử phải có tín hiệu để bài test có nghĩa
    rows0, rows1 = _rows(db0), _rows(db1)
    assert set(rows0) == {f"{SYMBOL}_SETUP_{token}_H1" for token in tokens.values()}  # db0 chỉ có SETUP, mọi nến
    with_events = {f"{SYMBOL}_TRIGGER_{tokens[key]}_H1" for key, frame in events.items() if not frame.empty}
    trend_lists = {f"{SYMBOL}_TREND_{m.upper()}_H1" for m in job.methods}
    assert set(rows1) == trend_lists | {f"{SYMBOL}_CONTEXT_H1"} | with_events  # db1: TREND, CONTEXT (mọi nến) và TRIGGER (chỉ list có tín hiệu: nến không có tín hiệu không có dòng)
    for key, frame in events.items():
        got = rows1.get(f"{SYMBOL}_TRIGGER_{tokens[key]}_H1", {})
        assert sorted(got) == sorted(pd.to_datetime(frame["bartime"]).dt.strftime("%Y-%m-%d %H:%M:%S")) and all(row["signal"] in ("1", "-1") for row in got.values())
        assert len(rows0[f"{SYMBOL}_SETUP_{tokens[key]}_H1"]) == 300
    roles = [role for role, _ in order]
    assert roles.index("CONTEXT") < min(i for i, role in enumerate(roles) if role in ("TRIGGER", "SETUP"))  # TREND, CONTEXT trước mọi TRIGGER/SETUP
    for key, token in tokens.items():  # với từng chiến lược, SETUP (có dòng cho mọi nến, ở db0) ghi SAU TRIGGER (db1): là dấu chốt, thấy nến mới ở SETUP thì sự kiện của nến đó đã có
        assert order.index(("SETUP", token)) > (order.index(("TRIGGER", token)) if not events[key].empty else -1)
    assert order[-1][0] == "SETUP"  # lần ghi cuối cùng của lượt luôn là một SETUP
    expected = 300 * (len(job.methods) + 1 + len(job.strategies)) + sum(len(frame) for frame in events.values())
    assert outcome == Outcome("written", expected)
    some = next(iter(rows1[next(iter(with_events))].values()))
    assert set(some) == {"bartime", "close_time", "signal", "setup", "open", "high", "low", "close", "atr", "reason"}
    assert set(next(iter(rows0[f"{SYMBOL}_SETUP_COMBO_H1"].values()))) == {"bartime", "close_time", "setup", "open", "high", "low", "close", "atr"}  # trạng thái từng nến: không có signal/reason
    assert set(next(iter(rows1[f"{SYMBOL}_CONTEXT_H1"].values()))) == {"bartime", "close_time", "macd_h", "ma"}
    assert set(next(iter(rows1[f"{SYMBOL}_TREND_KNN_H1"].values()))) == {"bartime", "close_time", "trend_bias"}


def _lane_jobs(config: dict) -> dict[str, configuration.RunnerJob]:
    """Làn nào chạy ở bản chia vai trò: mỗi chiến lược một làn, cộng làn trend; ``""`` là tiến trình trọn mọi vai trò để so."""
    return {lane: configuration.build_runner_job(config, lane=lane) for lane in ("", configuration.TREND_LANE, *configuration.STRATEGIES)}


def test_cac_lan_runner_gop_lai_ghi_dung_tung_dong_nhu_mot_tien_trinh_tron_vai_tro_va_moi_lan_chi_ghi_phan_cua_minh() -> None:
    """Chia vai trò chỉ là chia VIỆC chứ không đổi KẾT QUẢ: làn trend + 5 làn chiến lược cùng ghi vào một cặp db giả phải ra đúng từng danh sách, từng dòng, từng trường của một tiến trình làm trọn mọi vai trò;
    còn mỗi làn đứng riêng thì chỉ ghi vai trò của nó (chiến lược: TRIGGER của chiến lược đó ở db1 và SETUP của nó ở db0; trend: CONTEXT và TREND ở db1, không có SETUP/TRIGGER)."""
    config = cell_config()
    jobs = _lane_jobs(config)
    for tf in ("H1", "M30"):  # H1: đủ mọi khối trend; M30: chỉ breakout (xem trend_needed)
        bars = make_bars(300, "h" if tf == "H1" else "30min")
        pair = Pair(SYMBOL, tf)

        def run(lane_jobs: list[configuration.RunnerJob], pair: Pair = pair, bars: pd.DataFrame = bars) -> tuple[RecordingRedis, RecordingRedis]:
            db0, db1 = RecordingRedis(emulate=True), RecordingRedis(emulate=True)
            for job in lane_jobs:
                configuration.process_frame(
                    job, FakeSnapshots({pair: bars}), strategy_runner.ComponentWriter(**job.sink, client=db0), strategy_runner.ComponentWriter(**job.inputs_sink, client=db1), pair
                )
            return db0, db1

        whole = run([jobs[""]])
        lanes = run([jobs[lane] for lane in jobs if lane])
        assert _rows(lanes[0]) == _rows(whole[0]) and _rows(lanes[1]) == _rows(whole[1]), tf
        assert _rows(whole[0]) and _rows(whole[1])  # có dữ liệu để so
        for key in configuration.STRATEGIES:
            token = signal_publisher.strategy_token(key)
            rows0, rows1 = (_rows(db) for db in run([jobs[key]]))
            assert set(rows0) == {f"{SYMBOL}_SETUP_{token}_{tf}"} and set(rows1) <= {f"{SYMBOL}_TRIGGER_{token}_{tf}"}, (key, tf)
        rows0, rows1 = (_rows(db) for db in run([jobs[configuration.TREND_LANE]]))
        assert rows0 == {} and {key.split("_")[1] for key in rows1} == {"CONTEXT", "TREND"}, tf
        assert f"{SYMBOL}_CONTEXT_{tf}" in rows1 and all("_TRIGGER_" not in key for key in rows1)


def test_process_frame_chi_ghi_write_bars_hang_moi_nhat() -> None:
    job = _pair_job(write_bars=100)
    bars = make_bars(300, "h")
    db0, db1, writer, inputs_writer, engine, pair = _frame_world(job, bars)
    configuration.process_frame(job, engine, writer, inputs_writer, pair)
    rows0, rows1 = _rows(db0), _rows(db1)
    first = pd.Timestamp(bars["bartime"].iloc[200]).strftime("%Y-%m-%d %H:%M:%S")
    assert {len(rows) for rows in rows0.values()} == {100} and {len(rows) for key, rows in rows1.items() if "_TRIGGER_" not in key} == {100}  # mọi danh sách theo nến: đúng 100 hàng
    assert min(rows1[f"{SYMBOL}_CONTEXT_H1"]) == first
    assert all(len(rows) <= 100 and min(rows) >= first for key, rows in rows1.items() if "_TRIGGER_" in key)  # sự kiện chỉ nằm trong 100 nến gần nhất


def test_process_frame_moi_bang_chi_tinh_tren_cua_so_cua_no_zigzag_1200_con_lai_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bảng nến đọc 1.200 nến: CONTEXT, 5 chiến lược và các khối trend thường chỉ được tính trên 400 nến cuối (ghi 100 + warm-up 300), zigzag trên cả 1.200; mọi bảng kết thúc ở cùng nến mới nhất,
    và vẫn chỉ ghi 100 hàng cuối mỗi danh sách theo nến."""
    job = _pair_job(write_bars=100, warmup_bars=300, window_bars=400, trend_window_bars={"knn": 400, "breakout": 1200, "zigzag": 1200, "tstat": 400, "cloud": 400})
    bars = make_bars(1200, "h")
    seen: dict[str, tuple[int, pd.Timestamp]] = {}
    real = {name: getattr(strategy_runner, name) for name in ("trend_table", "context_table", "trigger_table")}

    def spy_trend(candles, method, timeframe):
        seen[f"trend_{method}"] = (len(candles), candles["bartime"].iloc[-1])
        return real["trend_table"](candles, method, timeframe)

    def spy_context(candles, params, timeframe):
        seen["context"] = (len(candles), candles["bartime"].iloc[-1])
        return real["context_table"](candles, params, timeframe)

    def spy_trigger(spec, candles, params, symbol, timeframe):
        seen[f"trigger_{spec.key}"] = (len(candles), candles["bartime"].iloc[-1])
        return real["trigger_table"](spec, candles, params, symbol, timeframe)

    monkeypatch.setattr(strategy_runner, "trend_table", spy_trend)
    monkeypatch.setattr(strategy_runner, "context_table", spy_context)
    monkeypatch.setattr(strategy_runner, "trigger_table", spy_trigger)
    db0, db1, writer, inputs_writer, engine, pair = _frame_world(job, bars)
    configuration.process_frame(job, engine, writer, inputs_writer, pair)
    newest = bars["bartime"].iloc[-1]
    expected = {"trend_zigzag": 1200, "trend_breakout": 1200, **{f"trend_{m}": 400 for m in ("knn", "tstat", "cloud")}, "context": 400, **{f"trigger_{k}": 400 for k in job.strategies}}
    assert {name: size for name, (size, _) in seen.items()} == expected
    assert {last for _, last in seen.values()} == {newest}
    first = pd.Timestamp(bars["bartime"].iloc[1100]).strftime("%Y-%m-%d %H:%M:%S")
    rows0, rows1 = _rows(db0), _rows(db1)
    assert {len(rows) for rows in rows0.values()} == {100} and min(rows1[f"{SYMBOL}_CONTEXT_H1"]) == first
    assert {len(rows) for key, rows in rows1.items() if "_TRIGGER_" not in key} == {100}


def test_trend_needed_knn_zigzag_tstat_cloud_chi_o_khung_h_con_breakout_o_moi_khung() -> None:
    """Quy tắc chủ hệ chốt 10/11/2026 ("trend chỉ dùng khung H"): theo bảng khung của config, knn, zigzag, tstat, cloud chỉ có ô đọc ở H1-H4 còn breakout (cùng khung vào lệnh) ở cả 8 khung; M10-M45 chỉ cần breakout."""
    needed = configuration.build_runner_job(cell_config()).trend_needed
    assert needed is not None
    frames: dict[str, set[str]] = {}
    for _symbol, method, frame in needed:
        frames.setdefault(method, set()).add(frame)
    assert frames["breakout"] == {"M10", "M20", "M30", "M45", "H1", "H2", "H3", "H4"}
    for method in ("knn", "zigzag", "tstat", "cloud"):
        assert frames[method] <= {"H1", "H2", "H3", "H4"} and frames[method], method
    assert {method for _symbol, method, frame in needed if frame.startswith("M")} == {"breakout"}


def test_process_frame_bo_qua_bang_trend_khong_o_nao_doc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ở khung M, runner chỉ tính và ghi TREND breakout; không gọi trend_table cho knn, zigzag, tstat, cloud và không có danh sách TREND của chúng. Ở khung H vẫn tính đủ các khối có ô đọc."""
    job = configuration.build_runner_job(cell_config())
    called: list[tuple[str, str]] = []
    real = strategy_runner.trend_table

    def spy(candles, method, timeframe):
        called.append((method, timeframe))
        return real(candles, method, timeframe)

    monkeypatch.setattr(strategy_runner, "trend_table", spy)
    for tf in ("M30", "H4"):
        _db0, db1, writer, inputs_writer, engine, pair = _frame_world(job, make_bars(300, "h"), tf)
        configuration.process_frame(job, engine, writer, inputs_writer, pair)
        lists = {key for key in _rows(db1) if "_TREND_" in key}
        expected = {method for (symbol, method, frame) in job.trend_needed if symbol == SYMBOL and frame == tf}
        assert {key.split("_")[2].lower() for key in lists} == expected, tf
        assert {method for method, frame in called if frame == tf} == expected, tf
    assert {method for method, frame in called if frame == "M30"} == {"breakout"}


def test_process_frame_cap_chua_co_nen_thi_khong_ghi_gi() -> None:
    job = _pair_job()
    db0, db1, writer, inputs_writer, _, pair = _frame_world(job, make_bars(10, "h"))
    for frame in (None, pd.DataFrame(columns=data_engine.OHLCV_COLUMNS)):
        assert configuration.process_frame(job, FakeSnapshots({pair: frame}), writer, inputs_writer, pair) is None
    assert db0.calls == [] and db1.calls == []


def test_process_frame_mot_khoi_hong_khong_chan_cac_bang_con_lai(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    job = _pair_job()
    db0, db1, writer, inputs_writer, engine, pair = _frame_world(job, make_bars(300, "h"))
    real = strategy_runner.trend_table

    def broken(candles, method, timeframe):
        if method == "zigzag":
            raise ValueError("khối hỏng")
        return real(candles, method, timeframe)

    monkeypatch.setattr(strategy_runner, "trend_table", broken)
    with caplog.at_level(logging.ERROR, logger="core_engine.src"):
        outcome = configuration.process_frame(job, engine, writer, inputs_writer, pair)
    assert f"{SYMBOL}_TREND_ZIGZAG_H1" not in _rows(db1) and f"{SYMBOL}_TREND_KNN_H1" in _rows(db1) and f"{SYMBOL}_SETUP_COMBO_H1" in _rows(db0)
    assert outcome is not None and [m for m in _messages(caplog) if m.startswith("event=table_failed")] == ["event=table_failed table=trend_zigzag symbol=HK50 timeframe=H1"]


def test_process_frame_warmup_chi_ghi_tu_nen_thu_warmup_cong_mot_va_hang_ghi_ra_da_am() -> None:
    """Bảng nến 300 nến, warm-up 100: cấu phần tính trên cả 300 nhưng chỉ ghi 200 hàng cuối, giá trị từng hàng ghi ra đúng bằng giá trị lúc không warm-up (cùng bảng nến), và không hàng CONTEXT nào còn NaN."""
    bars = make_bars(300, "h")
    plain_job, warm_job = _pair_job(), _pair_job(warmup_bars=100)
    results = []
    for job in (plain_job, warm_job):
        db0, db1, writer, inputs_writer, engine, pair = _frame_world(job, bars)
        configuration.process_frame(job, engine, writer, inputs_writer, pair)
        results.append((_rows(db0), _rows(db1)))
    (plain0, plain1), (warm0, warm1) = results
    first = pd.Timestamp(bars["bartime"].iloc[100]).strftime("%Y-%m-%d %H:%M:%S")
    for plain, warm in ((plain0, warm0), (plain1, warm1)):
        assert set(plain) == set(warm)
        for key, rows in warm.items():
            if "_TRIGGER_" not in key:  # danh sách theo nến: nến thứ 101 là hàng đầu tiên được ghi
                assert len(plain[key]) == 300 and len(rows) == 200 and min(rows) == first
            assert rows == {stamp: fields for stamp, fields in plain[key].items() if stamp >= first}  # cắt đầu, không đổi giá trị nào (TRIGGER: chỉ các sự kiện từ nến đó trở đi)
    assert any("nan" in fields.values() for fields in plain1[f"{SYMBOL}_CONTEXT_H1"].values())  # không warm-up: đầu bảng còn NaN
    assert not any("nan" in fields.values() for fields in warm1[f"{SYMBOL}_CONTEXT_H1"].values())  # có warm-up: hàng nào cũng đã ấm


def test_process_frame_warmup_cua_so_truot_nen_moi_vao_chi_ghi_dung_mot_hang_moi_moi_bang() -> None:
    job = _pair_job(warmup_bars=100)
    db0, db1, writer, inputs_writer, _, pair = _frame_world(job, make_bars(10, "h"))
    bars = make_bars(301, "h")
    first = configuration.process_frame(job, FakeSnapshots({pair: bars.iloc[:300].reset_index(drop=True)}), writer, inputs_writer, pair)
    per_pair = len(job.methods) + 1 + len(job.strategies)  # một hàng theo nến cho mỗi danh sách
    first_events = sum(1 for call in db1.calls if "_TRIGGER_" in decode_eval(call)["list_key"])
    assert first == Outcome("written", 200 * per_pair + first_events)
    db0.calls.clear()
    db1.calls.clear()
    slid = bars.iloc[1:].reset_index(drop=True)  # cửa sổ vẫn 300 nến: nến cũ nhất rơi ra, nến mới vào
    again = configuration.process_frame(job, FakeSnapshots({pair: slid}), writer, inputs_writer, pair)
    events = sum(1 for call in db1.calls if "_TRIGGER_" in decode_eval(call)["list_key"])
    assert again == Outcome("written", per_pair + events) and events <= len(job.strategies)  # mỗi danh sách theo nến thêm đúng một hàng; sự kiện chỉ khi nến mới có tín hiệu
    newest = pd.Timestamp(bars["bartime"].iloc[300]).strftime("%Y-%m-%d %H:%M:%S")
    assert {decode_eval(c)["stamp"] for c in (*db0.calls, *db1.calls)} == {newest}


def test_trigger_chi_giu_su_kien_trong_100_nen_gan_nhat_su_kien_troi_ra_ngoai_thi_bi_bo_ca_hash() -> None:
    """Cửa sổ trượt theo NẾN chứ không theo số dòng: runner nhảy cửa sổ tới sau thì sự kiện cũ hơn nến đầu cửa sổ bị bỏ khỏi LIST TRIGGER và HASH của nó bị xoá; các danh sách theo nến chỉ còn 100 hàng."""
    job = dataclasses.replace(configuration.build_runner_job(cell_config(rows=100)), warmup_bars=0)
    assert job.write_bars == 100 and job.sink["retention_max_entries"] == 100
    _db0, db1, writer, inputs_writer, _, pair = _frame_world(job, make_bars(10, "h"))
    bars = make_bars(400, "h")
    configuration.process_frame(job, FakeSnapshots({pair: bars.iloc[:300].reset_index(drop=True)}), writer, inputs_writer, pair)
    before = {key: list(stamps) for key, stamps in db1.lists.items() if "_TRIGGER_" in key}
    assert before and all(stamps == sorted(stamps) for stamps in before.values())
    window_start = pd.Timestamp(bars["bartime"].iloc[250]).strftime("%Y-%m-%d %H:%M:%S")  # cửa sổ mới: nến 250..349
    outside = [(key, stamp) for key, stamps in before.items() for stamp in stamps if stamp < window_start]
    assert outside, "dữ liệu thử phải có sự kiện sẽ trôi ra ngoài cửa sổ"
    db1.trims.clear()
    configuration.process_frame(job, FakeSnapshots({pair: bars.iloc[50:350].reset_index(drop=True)}), writer, inputs_writer, pair)
    for key, stamps in {**db1.lists, **_db0.lists}.items():
        if "_TRIGGER_" in key:
            assert all(stamp >= window_start for stamp in stamps)  # chỉ còn sự kiện trong cửa sổ
        else:
            assert len(stamps) == 100 and stamps[0] == window_start  # danh sách theo nến: đúng 100 nến, nến cũ nhất ra khi nến mới vào
    assert all(f"{key}:{stamp}" not in db1.hashes for key, stamp in outside)  # HASH của sự kiện đã bỏ cũng bị xoá
    assert {key for key, _ in db1.trims} == {f"{SYMBOL}_TRIGGER_{signal_publisher.strategy_token(k)}_H1" for k in job.strategies}  # mọi LIST TRIGGER được dọn ở mỗi lượt (kể cả LIST chưa có), với mốc = nến đầu cửa sổ
    assert {cutoff for _, cutoff in db1.trims} == {window_start}


def test_process_frame_chua_qua_warmup_bars_nen_thi_khong_ghi_gi_va_canh_bao(caplog: pytest.LogCaptureFixture) -> None:
    job = _pair_job(warmup_bars=100)
    db0, db1, writer, inputs_writer, _, pair = _frame_world(job, make_bars(10, "h"))
    with caplog.at_level(logging.WARNING, logger="core_engine.src"):
        assert configuration.process_frame(job, FakeSnapshots({pair: make_bars(100, "h")}), writer, inputs_writer, pair) is None  # đúng 100 nến: toàn nến ấm, chưa có hàng nào để ghi
    assert db0.calls == [] and db1.calls == []
    assert [m for m in _messages(caplog) if "event=warmup_incomplete" in m] == ["event=warmup_incomplete symbol=HK50 timeframe=H1 candles=100 warmup_bars=100"]
    outcome = configuration.process_frame(job, FakeSnapshots({pair: make_bars(101, "h")}), writer, inputs_writer, pair)
    assert outcome == Outcome("written", len(job.methods) + 1 + len(job.strategies))  # nến thứ 101: một hàng mỗi bảng


# =============================================================================
# 2. Vòng lặp sự kiện (dùng chung runner và publisher).
# =============================================================================

P1, P2, P3 = Pair("US30", "H1"), Pair("US30", "H2"), Pair("US30", "H4")


def _runner_job(**changes) -> configuration.RunnerJob:
    return dataclasses.replace(configuration.build_runner_job(cell_config()), **changes)


def _patch_frame(monkeypatch: pytest.MonkeyPatch, outcomes: dict | None = None, raises: dict | None = None) -> list[Pair]:
    seen: list[Pair] = []
    outcomes = {pair: list(queue) for pair, queue in (outcomes or {}).items()}

    def fake(job, engine, writer, inputs_writer, pair):
        seen.append(pair)
        if pair in (raises or {}):
            raise raises[pair]
        queue = outcomes.get(pair)
        if queue is None:
            return Outcome("written", 1)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(configuration, "process_frame", fake)
    return seen


def _run(job, engine, clock, stop, max_iterations: int = 400) -> None:
    iterations = {"n": 0}

    def should_stop() -> bool:
        iterations["n"] += 1
        return stop() or iterations["n"] > max_iterations

    configuration.run_runner_lane(job, engine, object(), object(), clock=clock, should_stop=should_stop)
    assert iterations["n"] <= max_iterations, "vòng lặp không dừng như mong đợi"


def test_khoi_dong_quet_moi_cap_mot_lan_theo_thu_tu_cua_viec_va_ghi_reconcile(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock()
    engine = LoopEngine(clock, [P1, P2, P3])
    seen = _patch_frame(monkeypatch)
    job = _runner_job(pairs=(P3, P2, P1))  # thứ tự quét là của việc (khung lớn trước), không phải thứ tự engine trả
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        _run(job, engine, clock, lambda: len(seen) >= 3)
    assert seen == [P3, P2, P1]
    messages = _messages(caplog)
    assert messages[0] == "event=reconcile_start db=0 pairs=3"
    assert messages[1].startswith("event=reconcile_end pairs=3 published=3 duration_s=")


def test_khong_quet_khi_process_on_startup_tat(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock()
    engine = LoopEngine(clock, [P1, P2, P3])
    engine.batches = [[P2]]
    seen = _patch_frame(monkeypatch)
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        _run(_runner_job(process_on_startup=False, pairs=(P1, P2, P3)), engine, clock, lambda: engine.polls >= 2)
    assert seen == [P2] and _messages(caplog) == []


def test_nhieu_su_kien_cua_cung_mot_cap_gop_thanh_mot_lan_xu_ly(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    engine = LoopEngine(clock, [P1, P2])
    engine.batches = [[P1, P2], [P2, P2]]  # P2 báo lại trong lúc còn chờ xử lý
    seen = _patch_frame(monkeypatch)
    _run(_runner_job(process_on_startup=False, pairs=(P1, P2)), engine, clock, lambda: engine.polls >= 3)
    assert seen == [P1, P2]


def test_su_kien_den_sau_khi_da_xu_ly_thi_xu_ly_lai(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    engine = LoopEngine(clock, [P1])
    engine.batches = [[P1], [], [P1]]
    seen = _patch_frame(monkeypatch)
    _run(_runner_job(process_on_startup=False, pairs=(P1,)), engine, clock, lambda: engine.polls >= 4)
    assert seen == [P1, P1]


def test_loi_cua_mot_cap_khong_chan_cap_khac_va_log_dung_cap(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock()
    engine = LoopEngine(clock, [P1, P2, P3])
    seen = _patch_frame(monkeypatch, raises={P2: ValueError("nến hỏng")})
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        _run(_runner_job(pairs=(P1, P2, P3)), engine, clock, lambda: len(seen) >= 3)
    assert seen == [P1, P2, P3]
    assert [m for m in _messages(caplog) if m.startswith("event=process_failed")] == ["event=process_failed symbol=US30 timeframe=H2"]
    assert next(m for m in _messages(caplog) if m.startswith("event=reconcile_end")).startswith("event=reconcile_end pairs=3 published=2")


def test_loi_redis_noi_len_cho_vong_noi_lai(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    engine = LoopEngine(clock, [P1])
    _patch_frame(monkeypatch, raises={P1: redis.ConnectionError("mất kết nối")})
    with pytest.raises(redis.ConnectionError):
        _run(_runner_job(pairs=(P1,)), engine, clock, lambda: False)


def test_cap_chua_co_nen_tinh_la_da_xong_va_khong_canh_bao(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock()
    engine = LoopEngine(clock, [P1])
    seen: list[Pair] = []
    monkeypatch.setattr(configuration, "process_frame", lambda job, _engine, _writer, _trend_writer, pair: seen.append(pair))  # trả None: nguồn chưa có nến của cặp
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        _run(_runner_job(pairs=(P1,)), engine, clock, lambda: len(seen) >= 1)
    assert next(m for m in _messages(caplog) if m.startswith("event=reconcile_end")).startswith("event=reconcile_end pairs=1 published=0")
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_publisher_moi_cap_cau_phan_danh_dau_dung_cac_o_dung_no_va_gop_thanh_mot_viec_moi_o(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Một lượt ghi của runner bắn các sự kiện (TREND, CONTEXT, TRIGGER) của cùng một ô: publisher chỉ quyết định MỘT lần cho ô đó. Sự kiện TREND đánh dấu mọi ô dùng nó; quét khởi động mỗi ô một lần."""
    job = configuration.build_publisher_job(cell_config(), [SYMBOL])
    events = [Pair(SYMBOL, "H4", "TREND", "KNN"), Pair(SYMBOL, "H1", "CONTEXT"), Pair(SYMBOL, "H1", "TRIGGER", "COMBO"), Pair(SYMBOL, "H1", "TRIGGER", "COMBO")]
    expected = {cell for pair in set(events) for cell in job.routes[pair]}
    clock = FakeClock()
    engine = LoopEngine(clock, list(job.pairs))
    engine.batches = [[], [], events]
    seen: list[Cell] = []
    monkeypatch.setattr(configuration, "process_cell", lambda job, engine, publisher, cell, now=None, **_kw: seen.append(cell) or Outcome("published", 0))
    iterations = {"n": 0}

    def should_stop() -> bool:
        iterations["n"] += 1
        return len(seen) >= len(expected) or iterations["n"] > 4000

    configuration.run_publisher_lane(dataclasses.replace(job, process_on_startup=False), engine, object(), clock=clock, should_stop=should_stop)
    assert len(seen) == len(set(seen)) and set(seen) == expected  # mỗi ô đúng một lần dù nhiều sự kiện cùng đánh dấu nó
    assert Cell(SYMBOL, "combo", "H1") in expected and Cell(SYMBOL, "ichimoku", "H1") in expected  # CONTEXT H1 là của mọi chiến lược cùng khung
    assert Cell(SYMBOL, "combo", "M10") in expected  # combo M10 cũng dùng KNN H4
    assert Cell(SYMBOL, "ichimoku", "H2") not in expected and Cell(SYMBOL, "ma_cross", "H1") in expected and Cell(SYMBOL, "ma_cross", "H2") not in expected
    scanned: list[Cell] = []
    monkeypatch.setattr(configuration, "process_cell", lambda job, engine, publisher, cell, now=None, **_kw: scanned.append(cell) or Outcome("published", 0))
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        configuration.run_publisher_lane(job, LoopEngine(FakeClock(), list(job.pairs)), object(), clock=FakeClock(), should_stop=lambda: len(scanned) >= len(job.cells))
    assert scanned == list(job.cells)  # quét khởi động theo thứ tự ô của việc, mỗi ô một lần
    assert next(m for m in _messages(caplog) if m.startswith("event=reconcile_start")) == f"event=reconcile_start db=0 pairs={len(job.cells)}"


def test_loi_cua_mot_o_o_publisher_khong_chan_o_khac(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    job = configuration.build_publisher_job(cell_config(), [SYMBOL])
    clock = FakeClock()
    engine = LoopEngine(clock, list(job.pairs))
    seen: list[Cell] = []
    first = next(iter(job.cells))

    def fake(job, engine, publisher, cell, now=None, **_kw):
        seen.append(cell)
        if cell == first:
            raise ValueError("bảng hỏng")
        return Outcome("published", 1)

    monkeypatch.setattr(configuration, "process_cell", fake)
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        configuration.run_publisher_lane(job, engine, object(), clock=clock, should_stop=lambda: len(seen) >= 2)
    assert seen[:2] == list(job.cells)[:2]
    assert [m for m in _messages(caplog) if m.startswith("event=process_failed")] == [f"event=process_failed symbol={first.symbol} strategy={first.strategy} timeframe={first.entry_tf}"]


# =============================================================================
# 3. Kết nối lại và vòng đời.
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


class _FakeWriter(_FakePart):
    instances: ClassVar[list[_FakePart]] = []


class _FakePublisher(_FakePart):
    instances: ClassVar[list[_FakePart]] = []


def _patch_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeEngine.instances, _FakeWriter.instances, _FakePublisher.instances, _FakePart.fail_subscribe_once = [], [], [], False
    monkeypatch.setattr(data_engine, "DataEngine", _FakeEngine)
    monkeypatch.setattr(strategy_runner, "ComponentWriter", _FakeWriter)
    monkeypatch.setattr(signal_publisher, "RawSignalWriter", _FakePublisher)


def test_run_runner_forever_noi_kiem_co_dang_ky_roi_chay_vong_lap_va_dong_ket_noi(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _patch_parts(monkeypatch)
    ran = []

    def fake_lane(job, engine, writer, inputs_writer, **kwargs):
        ran.append((engine, writer, inputs_writer))
        raise SystemExit(0)

    monkeypatch.setattr(configuration, "run_runner_lane", fake_lane)
    job = _runner_job()
    with caplog.at_level(logging.INFO, logger="core_engine.src"), pytest.raises(SystemExit):
        configuration._run_runner_forever(job)
    engine, writer, inputs_writer = _FakeEngine.instances[0], _FakeWriter.instances[0], _FakeWriter.instances[1]
    assert engine.log == ["ping", "flags", "subscribe"] and writer.log == ["ping"] and inputs_writer.log == ["ping"] and ran == [(engine, writer, inputs_writer)]
    assert engine.kwargs["pairs"] == job.pairs and (engine.kwargs["db"], engine.kwargs["port"]) == (0, 6379)  # nến ở 6379
    assert (writer.kwargs["db"], writer.kwargs["port"]) == (0, 6380) and (inputs_writer.kwargs["db"], inputs_writer.kwargs["port"]) == (1, 6380)  # TRIGGER/CONTEXT db0, TREND db1
    assert engine.closed and writer.closed and inputs_writer.closed  # tắt (SystemExit) vẫn đóng kết nối
    assert _messages(caplog) == ["event=redis_connected input_db=0 components_db=0 trend_db=1", f"event=subscribed db=0 key_prefix=L_CANDLE notify_flags=hK pairs={len(job.pairs)}"]


def test_run_runner_forever_mat_redis_thi_cho_roi_noi_lai(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _patch_parts(monkeypatch)
    _FakePart.fail_subscribe_once = True
    slept: list[float] = []
    monkeypatch.setattr(configuration.time, "sleep", slept.append)
    monkeypatch.setattr(configuration, "run_runner_lane", lambda job, engine, writer, inputs_writer, **kwargs: (_ for _ in ()).throw(SystemExit(0)))
    with caplog.at_level(logging.INFO, logger="core_engine.src"), pytest.raises(SystemExit):
        configuration._run_runner_forever(_runner_job())
    assert slept == [3.0] and len(_FakeEngine.instances) == 2 and len(_FakeWriter.instances) == 4
    assert all(part.closed for part in (*_FakeEngine.instances, *_FakeWriter.instances))
    assert any(m.startswith("event=redis_connection_lost error=rớt lúc đăng ký retry_in_s=3.0") for m in _messages(caplog))


def test_run_publisher_forever_noi_db0_db1_doc_va_db2_ghi_ca_ban_tho_lan_ban_loc_roi_dong_het(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _patch_parts(monkeypatch)
    got: dict = {}

    def fake_lane(job, engine, publisher, **kwargs):
        got.update(kwargs, engine=engine, publisher=publisher)
        raise SystemExit(0)

    monkeypatch.setattr(configuration, "run_publisher_lane", fake_lane)
    job = configuration.build_publisher_job(cell_config(), [SYMBOL])
    with caplog.at_level(logging.INFO, logger="core_engine.src"), pytest.raises(SystemExit):
        configuration._run_publisher_forever(job)
    engine, publisher, filtered = got["engine"], got["publisher"], got["filtered"]
    assert engine.log == ["ping", "flags", "subscribe"] and engine.kwargs["pairs"] == job.pairs and engine.kwargs["components"] is True and engine.kwargs["role_db"] == {"TRIGGER": 1, "CONTEXT": 1, "TREND": 1}
    assert (engine.kwargs["port"], engine.kwargs["db"]) == (6380, 0) and (publisher.kwargs["port"], publisher.kwargs["db"]) == (6380, 2)  # đọc SETUP ở db0 (TRIGGER/CONTEXT/TREND ở db1), ghi tín hiệu db2, cùng Redis đầu ra
    assert set(filtered) == {"knn", "breakout", "zigzag", "tstat", "cloud"} and (filtered["knn"].kwargs["db"], filtered["knn"].kwargs["key_prefix"], filtered["knn"].kwargs["source"]) == (2, "L_DE_KNN", "knn")
    assert publisher.log == ["ping"] and all(writer.log == ["ping"] for writer in filtered.values())
    assert engine.closed and publisher.closed and all(writer.closed for writer in filtered.values())
    assert _messages(caplog)[0] == "event=redis_connected components_db=0 trend_db=1 signals_db=2"
    assert _messages(caplog)[1] == f"event=subscribed db=0 key_prefix='' notify_flags=hK pairs={len(job.pairs)}"


def _patch_run_environment(monkeypatch: pytest.MonkeyPatch, config: dict) -> list[str]:
    """Cho ``run_*`` chạy không đụng file log thật, không bật nhớ đệm thật, với cấu hình thử."""
    from core_engine.src import log

    events: list[str] = []
    monkeypatch.setattr(configuration, "CONFIG", config)
    monkeypatch.setattr(log, "configure_logging", lambda level, name=None: events.append(f"logging:{level}:{name}"))
    monkeypatch.setattr(log, "set_log_level", lambda level: events.append(f"level:{level}"))
    monkeypatch.setattr(strategy_runner, "enable_memo", lambda maxsize=256: events.append(f"memo:{maxsize}"))
    return events


def test_run_runner_cau_hinh_hop_le_ghi_log_khoi_dong_roi_chay(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    events = _patch_run_environment(monkeypatch, cell_config())
    ran = []
    monkeypatch.setattr(configuration, "_run_runner_forever", ran.append)
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        assert configuration.run_runner() == 0
    assert events == ["logging:INFO:runner", "level:INFO", "memo:256"] and len(ran[0].pairs) == 64
    messages = _messages(caplog)
    assert messages[0] == "event=startup role=runner log_level=INFO input_db=0 components_db=0 trend_db=1 pairs=64 strategies=5 trend_methods=5 warmup_bars=0 write_bars=500 lane=runner"
    assert messages[1] == "event=input_source name=default host=127.0.0.1 port=6379 symbols=8"
    assert "event=memo enabled=true maxsize=256" in messages


def test_run_runner_voi_nguon_nen_them_va_gioi_han_symbol_dat_ten_log_rieng(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    events = _patch_run_environment(monkeypatch, cell_config(og8=True))
    ran = []
    monkeypatch.setattr(configuration, "_run_runner_forever", ran.append)
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        assert configuration.run_runner("og8") == 0
        assert configuration.run_runner("og8", ["fr40"]) == 0
    assert events[0] == "logging:INFO:runner_og8" and events[3] == "logging:INFO:runner_og8_fr40"  # file log riêng cho từng nhóm / phần việc
    assert ran[0].input_name == "og8" and {pair.symbol for pair in ran[0].pairs} == {"EURUSD", "FR40"} and {pair.symbol for pair in ran[1].pairs} == {"FR40"}
    messages = _messages(caplog)
    assert messages[0].endswith("lane=runner_og8") and messages[1] == "event=input_source name=og8 host=10.11.12.8 port=6380 symbols=2"


def test_run_publisher_ghi_log_khoi_dong_tung_chien_luoc_roi_chay(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    events = _patch_run_environment(monkeypatch, cell_config())
    ran = []
    monkeypatch.setattr(configuration, "_run_publisher_forever", ran.append)
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        assert configuration.run_publisher() == 0
    assert events == ["logging:INFO:publisher", "level:INFO"] and len(ran[0].cells) == 600
    messages = _messages(caplog)
    assert messages[0] == "event=startup role=publisher log_level=INFO components_db=0 trend_db=1 signals_db=2 cells=600 pairs=1680 max_shift_bars=999 lane=publisher"
    assert "event=strategy strategy=combo cells=120 legs=600 alternating_filter=True" in messages
    assert "event=strategy strategy=macd_break cells=120 legs=480 alternating_filter=True" in messages
    assert "event=strategy strategy=ichimoku cells=120 legs=0 alternating_filter=True" in messages


def test_lane_name_chi_chu_thuong_so_va_gach_duoi() -> None:
    assert configuration._lane_name("runner", "", None) == "runner"
    assert configuration._lane_name("runner", "og8", ["fr40", "EURUSD"]) == "runner_og8_eurusd_fr40"
    assert configuration._lane_name("publisher", "", ["HK50"]) == "publisher_hk50"
    assert configuration._lane_name("runner", "", None, "combo") == "runner_combo"  # làn đứng trước nguồn: nguồn mặc định bị bỏ như trước
    assert configuration._lane_name("runner", "og8", None, "trend") == "runner_trend_og8"
    assert configuration._lane_name("publisher", "", ["HK50"], "ma_cross") == "publisher_ma_cross_hk50"


def test_run_runner_theo_lan_dat_ten_log_theo_lan_va_chi_giu_phan_viec_cua_lan(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    events = _patch_run_environment(monkeypatch, cell_config(og8=True))
    ran = []
    monkeypatch.setattr(configuration, "_run_runner_forever", ran.append)
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        assert configuration.run_runner("og8", lane="combo") == 0
        assert configuration.run_runner("", lane="trend") == 0
        assert configuration.run_runner("", lane="khong_co") == 1  # làn lạ: config_error, không chạy
    assert events[0] == "logging:INFO:runner_combo_og8" and events[3] == "logging:INFO:runner_trend"
    assert [job.strategies for job in ran] == [("combo",), ()] and [job.methods == () for job in ran] == [True, False] and [job.with_context for job in ran] == [False, True]
    assert any(message.startswith("event=config_error") and "khong_co" in message for message in _messages(caplog))


def test_run_publisher_theo_chien_luoc_chi_giu_o_cua_chien_luoc_do(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    events = _patch_run_environment(monkeypatch, cell_config())
    ran = []
    monkeypatch.setattr(configuration, "_run_publisher_forever", ran.append)
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        assert configuration.run_publisher(strategy="ma_cross") == 0
    assert events == ["logging:INFO:publisher_ma_cross", "level:INFO"] and len(ran[0].cells) == 120 and {cell.strategy for cell in ran[0].cells} == {"ma_cross"}
    messages = _messages(caplog)
    assert messages[0].endswith(f"cells=120 pairs={len(ran[0].pairs)} max_shift_bars=999 lane=publisher_ma_cross")
    legs = sum(len(cell_job.legs) for cell_job in ran[0].cells.values())
    assert [m for m in messages if m.startswith("event=strategy ")] == [f"event=strategy strategy=ma_cross cells=120 legs={legs} alternating_filter={configuration.alternating_filter_for('ma_cross')}"]


@pytest.mark.parametrize("role", ["runner", "publisher"])
def test_run_tat_bang_cong_tac_thi_ghi_log_roi_thoat_em(role: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    config = cell_config()
    config["redis"]["enabled"] = False
    _patch_run_environment(monkeypatch, config)
    for name in ("_run_runner_forever", "_run_publisher_forever"):
        monkeypatch.setattr(configuration, name, lambda job: pytest.fail("không chạy khi redis.enabled=false"))
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        assert (configuration.run_runner() if role == "runner" else configuration.run_publisher()) == 0
    assert "event=disabled reason=redis_disabled_by_config" in _messages(caplog)


def test_run_redis_enabled_sai_kieu_la_config_error(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    config = cell_config()
    config["redis"]["enabled"] = "yes"  # phải là true/false thật
    _patch_run_environment(monkeypatch, config)
    monkeypatch.setattr(configuration, "_run_runner_forever", lambda job: pytest.fail("không chạy khi cấu hình sai"))
    with caplog.at_level(logging.CRITICAL, logger="core_engine.src"):
        assert configuration.run_runner() == 1
    assert any("event=config_error" in m and "redis.enabled must be true or false" in m for m in _messages(caplog))


@pytest.mark.parametrize(
    ("role", "key"), [("runner", "snapshot_bars"), ("publisher", "snapshot_bars"), ("runner", "warmup_bars"), ("publisher", "warmup_bars")]
)
def test_run_cau_hinh_sai_thi_ghi_config_error_va_tra_1(role: str, key: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    config = copy.deepcopy(cell_config())
    config["redis"]["input"].pop(key)
    _patch_run_environment(monkeypatch, config)
    with caplog.at_level(logging.CRITICAL, logger="core_engine.src"):
        assert (configuration.run_runner() if role == "runner" else configuration.run_publisher()) == 1
    assert any("event=config_error" in m and f"redis.input.{key}" in m for m in _messages(caplog))


def test_run_bang_khung_trend_sai_thi_publisher_config_error_ngay(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    config = cell_config()
    del config["strategies"]["combo"]["trend_tf_by_symbol"]["US30"]
    _patch_run_environment(monkeypatch, config)
    with caplog.at_level(logging.CRITICAL, logger="core_engine.src"):
        assert configuration.run_publisher() == 1
    assert any("event=config_error" in m and "trend_tf_by_symbol.US30" in m for m in _messages(caplog))


def test_run_thieu_co_keyspace_la_config_error(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _patch_run_environment(monkeypatch, cell_config())

    def broken(job):
        raise RuntimeError("Redis notify-keyspace-events thiếu lớp ['h']")

    monkeypatch.setattr(configuration, "_run_publisher_forever", broken)
    with caplog.at_level(logging.CRITICAL, logger="core_engine.src"):
        assert configuration.run_publisher() == 1
    assert any("event=config_error" in m and "notify-keyspace-events" in m for m in _messages(caplog))


@pytest.mark.parametrize("role", ["runner", "publisher"])
def test_run_dung_em_khi_nhan_sigterm_hoac_ctrl_c(role: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _patch_run_environment(monkeypatch, cell_config())
    for stop in (SystemExit(0), KeyboardInterrupt()):
        def stopping(job, stop=stop):
            raise stop

        monkeypatch.setattr(configuration, "_run_runner_forever", stopping)
        monkeypatch.setattr(configuration, "_run_publisher_forever", stopping)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="core_engine.src"):
            assert (configuration.run_runner() if role == "runner" else configuration.run_publisher()) == 0
        assert "event=stopped" in _messages(caplog)


# =============================================================================
# 4. main.py
# =============================================================================


def test_main_parse_args_chon_vai_tro_nguon_va_symbol() -> None:
    runner = main.parse_args(["--runner"])
    assert (runner.runner, runner.publisher, runner.source, runner.symbols) == (True, False, "", ())
    assert main.parse_args(["--runner", "--source", " OG8 "]).source == "og8" and main.parse_args(["--runner", "--source", "default"]).source == ""
    assert main.parse_args(["--publisher", "--symbols", "hk50, us30"]).symbols == ("HK50", "US30")
    for bad in (
        [], ["--runner", "--publisher"], ["--source", "og8"], ["--publisher", "--source", "og8"], ["--runner", "--source", "o_g8"], ["--runner", "--source", "../x"],
        ["--runner", "--symbols", ""], ["--runner", "--symbols", "HK50,hk50"], ["--runner", "--symbols", "H K"], ["--strategy", "combo"], ["--trend", "knn"],
    ):
        with pytest.raises(SystemExit):
            main.parse_args(bad)


def test_main_bat_sigterm_roi_giao_cho_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    handlers = {}
    monkeypatch.setattr(main.signal, "signal", lambda signum, handler: handlers.update({signum: handler}))
    called = []
    monkeypatch.setattr(configuration, "run_runner", lambda input_name="", symbols=None, lane="": called.append(("runner", input_name, tuple(symbols or ()), lane)) or 7)
    monkeypatch.setattr(configuration, "run_publisher", lambda symbols=None, strategy="": called.append(("publisher", tuple(symbols or ()), strategy)) or 8)
    assert main.main(["--runner"]) == 7 and called == [("runner", "", (), "")]
    assert main.main(["--runner", "--source", "og8", "--symbols", "EURUSD"]) == 7 and called[-1] == ("runner", "og8", ("EURUSD",), "")
    assert main.main(["--publisher", "--symbols", "HK50,US30"]) == 8 and called[-1] == ("publisher", ("HK50", "US30"), "")
    assert main.main(["--runner", "--lane", "combo.og8"]) == 7 and called[-1] == ("runner", "og8", (), "combo")  # nguồn nằm sau dấu chấm của làn
    assert main.main(["--runner", "--lane", "trend.default"]) == 7 and called[-1] == ("runner", "", (), "trend")  # default = redis.input
    assert main.main(["--publisher", "--lane", "combo"]) == 8 and called[-1] == ("publisher", (), "combo")
    assert handlers[signal.SIGTERM] is main._handle_shutdown_signal


@pytest.mark.parametrize(
    "argv",
    [
        ["--runner", "--lane", "combo"],  # làn runner phải kèm nguồn: <làn>.<nguồn>
        ["--runner", "--lane", "combo.og8", "--source", "og8"],  # nguồn khai hai lần
        ["--runner", "--lane", "co mbo.og8"],
        ["--runner", "--lane", "combo.og8.x"],
        ["--publisher", "--lane", "combo.og8"],  # publisher không có nguồn nến
        ["--publisher", "--lane", "co-mbo"],
    ],
)
def test_main_tu_choi_lan_sai_hinh_dang_hoac_di_chung_source(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main.parse_args(argv)
    assert raised.value.code == 2 and "lane" in capsys.readouterr().err


def test_main_sigterm_handler_raise_system_exit_0_va_ghi_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="core_engine.src"), pytest.raises(SystemExit) as raised:
        main._handle_shutdown_signal(signal.SIGTERM, None)
    assert raised.value.code == 0 and "event=shutdown_signal signal=SIGTERM" in _messages(caplog)


@pytest.mark.parametrize(("limit", "fires"), [(999, True), (2, True), (1, False), (0, False)])
def test_max_shift_bars_cua_publisher_gioi_han_tin_hieu_doi_theo_so_nen_ke_tu_nen_mo_dot(limit: int, fires: bool) -> None:
    """Đợt SELL mở ở nến 0, trend cloud đổi -1 ở nến 2 (tuổi 2): tín hiệu dời chỉ bắn khi live.max_shift_bars >= 2."""
    job, cell_job = _cell_world("ma_cross", "H1", cell_config(max_shift_bars=limit))
    assert job.max_shift_bars == limit
    trigger = _trigger([-1, 0, 0], setups=[-1, -1, -1])
    trends = {method: _trend([0, 0, -1], "2026-11-09 00:00") for method in ("cloud", "breakout", "tstat", "zigzag")}
    trends["knn"] = _trend([-1, -1, -1], "2026-11-09 00:00")
    _, publisher, filtered, _ = _process(job, cell_job, _frames(cell_job, trigger, trends), "2026-11-09 03:01")
    assert len(filtered["cloud"].fired) == (1 if fires else 0)  # khối dời chịu giới hạn
    assert filtered["knn"].calls == [] and publisher.fired == []  # khối không dời và bản thô không đổi
