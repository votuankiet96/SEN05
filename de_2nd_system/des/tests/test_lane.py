"""Test configuration.py phần ĐIỀU PHỐI TIẾN TRÌNH (xử lý một việc, vòng lặp sự kiện, khung chạy chung, nạp lại config không restart) và main.py (đọc tham số dòng lệnh, giao việc) — hermetic.

Redis giả: RecordingRedis (đầu ra db0), FakeSnapshots (engine giả trả bảng đã nạp), LoopEngine + FakeClock (vòng lặp). Không có Redis thật, không có file log thật.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest
import redis
from engine_support import (
    FakeClock,
    FakeSnapshots,
    LoopEngine,
    RecordingRedis,
    local_config,
    make_bars,
    plan,
    shared_config,
    stamp_of,
    with_break20,
    with_combo,
)
from test_signal_publisher import _candles, _signals, _trend

from des.src import configuration, main
from des.src.data_engine import Pair
from des.src.de_module import combo_strategy
from des.src.de_module import trend as trend_module
from des.src.util import ModuleWriter, SignalWriter

T = pd.Timestamp


# =============================================================================
# 1. Một việc của runner: nến -> module -> db0.
# =============================================================================


def _module_job(module: str):
    return configuration.build_module_job(plan(), local_config(), module, "default")


def _writer(job) -> tuple[ModuleWriter, RecordingRedis]:
    client = RecordingRedis(emulate=True)
    return ModuleWriter(**job.sink, client=client), client


def test_process_module_ma_cross_chi_ghi_nen_co_tin_hieu_trong_cua_so_sau_warm_up() -> None:
    job = _module_job("ma_cross")
    writer, client = _writer(job)
    bars = make_bars(800, freq="10min")
    written = configuration.process_module(job, FakeSnapshots({Pair("BTCUSD", "M10"): bars}), writer, Pair("BTCUSD", "M10"))
    fast, slow = bars["close"].rolling(13).mean(), bars["close"].rolling(34).mean()
    ema = lambda series, period: series.ewm(span=period, adjust=False).mean()
    macd = ema(bars["close"], 5) - ema(bars["close"], 25)
    hist = macd - ema(macd, 5)
    up, down = (fast.shift(1) <= slow.shift(1)) & (fast > slow) & (hist > 0), (fast.shift(1) >= slow.shift(1)) & (fast < slow) & (hist < 0)
    window = bars.iloc[300:]  # 300 nến đầu chỉ để chỉ báo kịp ấm, không ghi
    expected = [stamp_of(t) for t, u, d in zip(window["bartime"], up.iloc[300:], down.iloc[300:], strict=True) if u or d]
    assert written == len(expected) > 0
    assert client.lists["L_MODSTR_MACROSS_BTCUSD_M10"] == expected
    first = client.hashes[f"L_MODSTR_MACROSS_BTCUSD_M10:{expected[0]}"]
    assert first["close_time"] == stamp_of(T(expected[0]) + pd.Timedelta(minutes=10)) and first["signal"] in ("1", "-1") and first["reason"].startswith("fast SMA crossed")
    assert client.trims[-1] == ("L_MODSTR_MACROSS_BTCUSD_M10", stamp_of(window["bartime"].iloc[0]))  # bỏ các mốc cũ hơn đầu cửa sổ ghi
    assert client.calls[0][3] == "600000"  # hạn lưu HASH = 2 x 500 nến x 600 giây


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


@pytest.mark.parametrize("name", ["macd", "sma20"])
def test_process_module_yeu_to_trend_ghi_moi_nen_cua_cua_so_voi_huong_dung_va_chay_lai_khong_ghi_trung(name: str) -> None:
    job = _module_job(name)
    writer, client = _writer(job)
    bars = make_bars(800, freq="30min")
    engine = FakeSnapshots({Pair("BTCUSD", "M30"): bars})
    assert configuration.process_module(job, engine, writer, Pair("BTCUSD", "M30")) == 500
    key = f"L_MODTRE_{name.upper()}_BTCUSD_M30"
    assert client.lists[key] == [stamp_of(t) for t in bars["bartime"].iloc[300:]]
    close = bars["close"]
    macd = _ema(close, 5) - _ema(close, 25)
    signal_line = macd - _ema(macd, 5) if name == "macd" else close - close.rolling(20).mean()  # macd: Histogram; sma20: close trừ SMA20 (cùng dấu với so sánh close với SMA20)
    expected = np.where(signal_line > 0, 1, np.where(signal_line < 0, -1, 0))
    assert [int(client.hashes[f"{key}:{stamp}"]["trend_bias"]) for stamp in client.lists[key]] == expected[300:].tolist()
    assert client.hashes[f"{key}:{client.lists[key][0]}"]["close_time"] == stamp_of(bars["bartime"].iloc[300] + pd.Timedelta(minutes=30))
    assert configuration.process_module(job, engine, writer, Pair("BTCUSD", "M30")) == 0  # nến không đổi: mỗi mốc đúng một lần


def test_process_module_break20_va_combo_chay_o_khung_rieng_cua_chung_theo_dang_ky_trong_config() -> None:
    combo_plan = configuration.parse_plan(with_break20(with_combo(shared_config())), configuration_servers())
    break_job = configuration.build_module_job(combo_plan, local_config(), "break20", "default")
    writer, client = _writer(break_job)
    bars = make_bars(800, freq="45min")
    assert configuration.process_module(break_job, FakeSnapshots({Pair("BTCUSD", "M45"): bars}), writer, Pair("BTCUSD", "M45")) == 500
    key = "L_MODTRE_BREAK20_BTCUSD_M45"
    assert [int(client.hashes[f"{key}:{stamp}"]["trend_bias"]) for stamp in client.lists[key]] == trend_module.bias_break(bars, lookback=20)[300:].tolist()
    combo_job = configuration.build_module_job(combo_plan, local_config(), "combo", "default")
    combo_writer, combo_client = _writer(combo_job)
    combo_bars = make_bars(800, freq="30min")
    written = configuration.process_module(combo_job, FakeSnapshots({Pair("BTCUSD", "M30"): combo_bars}), combo_writer, Pair("BTCUSD", "M30"))
    signals = combo_strategy.detect(combo_bars, **combo_job.params)["signal"].iloc[300:]
    expected = [stamp_of(t) for t, s in zip(combo_bars["bartime"].iloc[300:], signals, strict=True) if s != 0]
    assert written == len(expected) > 0 and combo_client.lists["L_MODSTR_COMBO_BTCUSD_M30"] == expected
    assert combo_client.hashes[f"L_MODSTR_COMBO_BTCUSD_M30:{expected[0]}"]["reason"].startswith(("bullish candle", "bearish candle"))


def test_process_module_chua_co_nen_hoac_chua_du_warm_up_thi_khong_ghi_gi(caplog: pytest.LogCaptureFixture) -> None:
    job = _module_job("macd")
    writer, client = _writer(job)
    assert configuration.process_module(job, FakeSnapshots({}), writer, Pair("BTCUSD", "M30")) == 0
    with caplog.at_level(logging.WARNING):
        assert configuration.process_module(job, FakeSnapshots({Pair("BTCUSD", "M30"): make_bars(300, freq="30min")}), writer, Pair("BTCUSD", "M30")) == 0
    assert "event=warmup_incomplete" in caplog.text and client.calls == []


# =============================================================================
# 2. Một việc của publisher: kết quả module + nến -> tín hiệu cuối.
# =============================================================================


def _decision(*, signals=True, trend=True, candles=True):
    job = configuration.build_decision_job(plan(), local_config(), "default")
    registration = next(r for r in job.registrations if r.symbol == "BTCUSD")
    strategies = FakeSnapshots({Pair("BTCUSD", "M10", "MACROSS"): _signals(("10:20", 1))} if signals else {})
    trends = FakeSnapshots(
        {Pair("BTCUSD", "M30", "MACD"): _trend(("09:30", 1), ("10:00", 1)), Pair("BTCUSD", "M30", "SMA20"): _trend(("09:30", 1), ("10:00", 1))} if trend else {}
    )
    client = RecordingRedis(emulate=True)
    return job, registration, strategies, trends, FakeSnapshots({Pair("BTCUSD", "M10"): _candles("10:20")} if candles else {}), SignalWriter(**job.sink, client=client), client


NOW = T("2026-11-16 10:30:14", tz="UTC")


def test_process_decision_doc_ba_thu_roi_ghi_tin_hieu_cuoi_va_chay_lai_khong_ghi_trung() -> None:
    job, registration, strategies, trends, candles, writer, client = _decision()
    assert configuration.process_decision(job, strategies, trends, candles, writer, registration, NOW) == 1
    assert strategies.asked == [Pair("BTCUSD", "M10", "MACROSS")] and trends.asked == [Pair("BTCUSD", "M30", "MACD"), Pair("BTCUSD", "M30", "SMA20")] and candles.asked == [Pair("BTCUSD", "M10")]
    assert client.lists["L_DE_BTCUSD_M10"] == ["2026-11-16 10:20:00"]
    assert client.hashes["L_DE_BTCUSD_M10:2026-11-16 10:20:00"]["tradeSide"] == "BUY"
    assert configuration.process_decision(job, strategies, trends, candles, writer, registration, NOW) == 0  # đánh giá lại khi có sự kiện khác: bản trùng không ghi và không báo kênh lại


@pytest.mark.parametrize("missing", ["signals", "candles"])
def test_process_decision_thieu_tin_hieu_ma_cross_hoac_nen_thi_chua_co_gi_de_quyet(missing: str) -> None:
    job, registration, strategies, trends, candles, writer, client = _decision(**{missing: False})
    assert configuration.process_decision(job, strategies, trends, candles, writer, registration, NOW) == 0 and client.calls == []


def test_process_decision_chua_co_nen_trend_nao_thi_cho_khong_ghi_gi() -> None:
    job, registration, strategies, trends, candles, writer, client = _decision(trend=False)
    assert configuration.process_decision(job, strategies, trends, candles, writer, registration, NOW) == 0 and client.calls == []


def test_process_decision_qua_han_hieu_luc_thi_bo() -> None:
    job, registration, strategies, trends, candles, writer, client = _decision()
    assert configuration.process_decision(job, strategies, trends, candles, writer, registration, T("2026-11-16 10:40:00", tz="UTC")) == 0 and client.calls == []


# =============================================================================
# 3. Vòng lặp sự kiện.
# =============================================================================


def _loop(engines, keys, step, **kwargs) -> None:
    clock = kwargs.pop("clock", FakeClock())
    configuration._run_loop(
        engines=engines, keys=keys, route=kwargs.pop("route", lambda pair: (pair,)), step=step, changed=kwargs.pop("changed", lambda: False),
        process_on_startup=kwargs.pop("process_on_startup", False), wait_seconds=kwargs.pop("wait_seconds", 1.0), clock=clock, **kwargs,
    )


def test_quet_khoi_dong_xu_ly_moi_viec_mot_lan_theo_thu_tu_va_ghi_log_bat_dau_ket_thuc(caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock()
    engine, done = LoopEngine(clock, ["A", "B", "C"]), []
    with caplog.at_level(logging.INFO):
        _loop([engine], ["A", "B", "C"], lambda key: done.append(key) or 1, process_on_startup=True, clock=clock, should_stop=lambda: len(done) == 3)
    assert done == ["A", "B", "C"]
    assert "event=reconcile_start keys=3" in caplog.text and "event=reconcile_end keys=3 written=3" in caplog.text


def test_khong_quet_khoi_dong_thi_chi_xu_ly_viec_co_su_kien_va_gop_su_kien_trung() -> None:
    clock = FakeClock()
    engine, done = LoopEngine(clock, ["A", "B"]), []
    engine.batches = [["A", "B", "A"]]  # cặp A ghi hai lần trong một đợt: gộp thành một lần xử lý
    _loop([engine], ["A", "B"], lambda key: done.append(key) or 1, clock=clock, should_stop=lambda: engine.polls >= 4)
    assert done == ["A", "B"]


def test_het_viec_thi_cho_dung_thoi_gian_va_con_viec_dang_ban_thi_khong_cho() -> None:
    clock = FakeClock()
    engine = LoopEngine(clock, ["A"])
    _loop([engine], ["A"], lambda key: 1, clock=clock, wait_seconds=1.0, should_stop=lambda: engine.polls >= 3)
    assert clock.now == 1000.0 + 3 * 1.0  # không có việc nào: mỗi vòng chờ đủ wait_seconds
    clock2 = FakeClock()
    busy = LoopEngine(clock2, ["A", "B"])
    busy.batches = [["A", "B"]]
    _loop([busy], ["A", "B"], lambda key: 1, clock=clock2, wait_seconds=1.0, should_stop=lambda: busy.polls >= 2)
    assert clock2.now == 1000.0 + 1.0  # vòng đầu chờ cả giây rồi nhận hai việc; vòng hai còn việc đang bẩn nên không chờ


def test_nhieu_engine_chia_deu_thoi_gian_cho_va_cac_su_kien_deu_duoc_dinh_tuyen() -> None:
    clock = FakeClock()
    first, second, done = LoopEngine(clock, ["A"]), LoopEngine(clock, ["B"]), []
    second.batches = [["B"]]
    _loop([first, second], ["A", "B"], lambda key: done.append(key) or 1, clock=clock, wait_seconds=0.1, should_stop=lambda: len(done) == 1)
    assert done == ["B"] and first.polls == 1 and second.polls == 1 and clock.now == pytest.approx(1000.1)  # mỗi engine chờ 0,05 giây


def test_route_doi_mot_cap_thanh_nhieu_viec_va_cap_la_thi_bo() -> None:
    clock = FakeClock()
    engine, done = LoopEngine(clock, ["X"]), []
    engine.batches = [["trend", "la"]]
    routes = {"trend": ("A", "B")}
    _loop([engine], ["A", "B"], lambda key: done.append(key) or 1, route=lambda pair: routes.get(pair, ()), clock=clock, should_stop=lambda: len(done) == 2)
    assert done == ["A", "B"]


def test_co_phien_ban_config_moi_thi_vong_lap_raise_reload() -> None:
    clock = FakeClock()
    with pytest.raises(configuration._Reload):
        _loop([LoopEngine(clock, ["A"])], ["A"], lambda key: 1, changed=lambda: True, clock=clock)


def test_loi_cua_mot_viec_duoc_co_lap_con_loi_redis_noi_len_cho_vong_noi_lai(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR):
        assert configuration._guarded(lambda: 1 / 0, Pair("US30", "M10")) == 0
    assert "event=process_failed symbol=US30 timeframe=M10" in caplog.text
    assert configuration._guarded(lambda: 7, "k") == 7

    def lost() -> int:
        raise redis.exceptions.ConnectionError("mat ket noi")

    with pytest.raises(redis.exceptions.ConnectionError):
        configuration._guarded(lost, "k")


class _Watcher:
    """Kết nối nghe phiên bản config giả: ``poll`` trả các message đã xếp hàng (mỗi lần gọi một lượt)."""

    def __init__(self, *batches: list) -> None:
        self.batches = list(batches)

    def poll(self, timeout: float) -> list:
        return self.batches.pop(0) if self.batches else []


def _module_job_two_frames(*, on_startup: bool):
    config = with_combo(shared_config())
    # macd chạy ở hai khung: M30 (ma_cross) và H1 (combo)
    config["redis"]["candles"]["process_on_startup"] = on_startup
    return configuration.build_module_job(configuration.parse_plan(config, configuration_servers()), local_config(), "macd", "default")


def test_run_module_lane_quet_khoi_dong_khung_lon_truoc_roi_nap_lai_khi_co_config_moi(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _module_job_two_frames(on_startup=True)
    seen: list[Pair] = []
    monkeypatch.setattr(configuration, "process_module", lambda job, engine, writer, pair: seen.append(pair) or 1)
    clock = FakeClock()
    watcher = _Watcher([], [], [], [], [{"type": "pmessage"}])  # lần hỏi thứ 5 báo có phiên bản config mới
    with pytest.raises(configuration._Reload):
        configuration.run_module_lane(job, LoopEngine(clock, job.pairs), object(), watcher, clock=clock)
    assert seen == [Pair("BTCUSD", "H1"), Pair("US30", "H1"), Pair("BTCUSD", "M30"), Pair("US30", "M30")]  # khung lớn trước, cùng khung theo tên symbol


def test_run_module_lane_chi_xu_ly_cap_nen_co_su_kien_khi_khong_quet_khoi_dong(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _module_job_two_frames(on_startup=False)
    seen: list[Pair] = []
    monkeypatch.setattr(configuration, "process_module", lambda job, engine, writer, pair: seen.append(pair) or 1)
    clock = FakeClock()
    engine = LoopEngine(clock, job.pairs)
    engine.batches = [[Pair("US30", "M30")], [], [Pair("BTCUSD", "H1")]]
    with pytest.raises(configuration._Reload):
        configuration.run_module_lane(job, engine, object(), _Watcher([], [], [], [], [{"type": "pmessage"}]), clock=clock)
    assert seen == [Pair("US30", "M30"), Pair("BTCUSD", "H1")]


def configuration_servers() -> dict:
    from engine_support import SERVERS

    return SERVERS


def test_run_decision_lane_quet_khoi_dong_theo_thu_tu_dang_ky(monkeypatch: pytest.MonkeyPatch) -> None:
    job = configuration.build_decision_job(plan(), local_config(), "default")
    seen: list[str] = []
    monkeypatch.setattr(configuration, "process_decision", lambda job, strategies, trends, candles, writer, registration, now: seen.append(registration.symbol) or 1)
    clock = FakeClock()
    with pytest.raises(configuration._Reload):
        configuration.run_decision_lane(
            job, LoopEngine(clock, job.strategy_pairs), LoopEngine(clock, job.trend_pairs), LoopEngine(clock, job.candle_pairs), object(), _Watcher([], [], [{"type": "pmessage"}]),
            now=lambda: NOW, clock=clock
        )
    assert seen == ["US30", "BTCUSD"]


def test_run_decision_lane_ba_loai_su_kien_deu_danh_dau_o_quyet_dinh_cua_symbol_do(monkeypatch: pytest.MonkeyPatch) -> None:
    config = shared_config()
    config["redis"]["candles"]["process_on_startup"] = False
    job = configuration.build_decision_job(configuration.parse_plan(config, configuration_servers()), local_config(), "default")
    seen: list[str] = []
    monkeypatch.setattr(configuration, "process_decision", lambda job, strategies, trends, candles, writer, registration, now: seen.append(registration.symbol) or 1)
    clock = FakeClock()
    strategies, trends, candles = LoopEngine(clock, job.strategy_pairs), LoopEngine(clock, job.trend_pairs), LoopEngine(clock, job.candle_pairs)
    trends.batches = [[Pair("BTCUSD", "M30", "SMA20")]]  # nến của một yếu tố trend của BTCUSD về
    strategies.batches = [[], [Pair("US30", "M10", "MACROSS")]]  # MA Cross của US30 có tín hiệu
    candles.batches = [[], [], [Pair("BTCUSD", "M10")]]  # nến khung trigger của BTCUSD về
    with pytest.raises(configuration._Reload):
        configuration.run_decision_lane(job, strategies, trends, candles, object(), _Watcher(*([[]] * 5), [{"type": "pmessage"}]), now=lambda: NOW, clock=clock)
    assert seen == ["BTCUSD", "US30", "BTCUSD"]


# =============================================================================
# 4. Khung chạy chung: nạp config lên Redis khi khởi động, đọc từ Redis, nạp lại không restart.
# =============================================================================


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch):
    """Giả mọi thứ chạm đĩa/Redis của ``_serve``: log, bootstrap, nạp và đọc config, ngủ. Trả về dict ghi lại các lời gọi."""
    from des.src import log

    calls: dict[str, list] = {"upload": [], "fetch": [], "sleep": []}
    monkeypatch.setattr(log, "configure_logging", lambda level, name=None: calls.setdefault("log", []).append(name))
    monkeypatch.setattr(log, "set_log_level", lambda level: None)
    monkeypatch.setattr(configuration, "load_bootstrap", local_config)
    monkeypatch.setattr(configuration, "upload_config", lambda: calls["upload"].append(1) or (len(calls["upload"]), True))
    monkeypatch.setattr(configuration.time, "sleep", lambda seconds: calls["sleep"].append(seconds))
    monkeypatch.setattr(configuration, "fetch_plan", lambda local: calls["fetch"].append(1) or (len(calls["fetch"]), plan()))
    return calls


def test_serve_nap_config_mot_lan_roi_chay_va_co_phien_ban_moi_thi_dung_lai_viec_khong_restart(serve) -> None:
    runs: list[object] = []

    def forever(job: object) -> None:
        runs.append(job)
        raise configuration._Reload if len(runs) == 1 else SystemExit(0)

    assert configuration._serve("runner_test", lambda plan, local: ("job", plan.strategies["ma_cross"].params["fast_ma"]), forever) == 0
    assert len(serve["upload"]) == 1 and len(serve["fetch"]) == 2 and runs == [("job", 13), ("job", 13)]  # nạp một lần, đọc lại và dựng lại việc sau khi có phiên bản mới
    assert serve["log"] == ["runner_test"]


def test_serve_redis_tat_trong_config_thi_thoat_em_khong_dung_viec(serve) -> None:
    def make_job(*_: object) -> object:
        raise AssertionError("không được dựng việc khi redis.enabled=false")

    serve_plan = configuration.parse_plan(shared_config(redis={**shared_config()["redis"], "enabled": False}), configuration_servers())
    configuration.fetch_plan = lambda local: (1, serve_plan)
    assert configuration._serve("x", make_job, lambda job: None) == 0


def test_serve_config_sai_thi_ma_thoat_1_va_ghi_config_error(serve, caplog: pytest.LogCaptureFixture) -> None:
    def make_job(*_: object) -> object:
        raise ValueError("config hong")

    with caplog.at_level(logging.CRITICAL):
        assert configuration._serve("x", make_job, lambda job: None) == 1
    assert "event=config_error error=config hong" in caplog.text


def test_serve_mat_redis_luc_nap_hoac_doc_config_thi_doi_roi_thu_lai(monkeypatch: pytest.MonkeyPatch, serve) -> None:
    attempts = {"upload": 0, "fetch": 0}

    def upload() -> tuple[int, bool]:
        attempts["upload"] += 1
        if attempts["upload"] == 1:
            raise redis.exceptions.ConnectionError("tat")
        return 1, True

    def fetch(local: dict) -> tuple[int, object]:
        attempts["fetch"] += 1
        if attempts["fetch"] == 1:
            raise redis.exceptions.ConnectionError("tat")
        return 1, plan()

    monkeypatch.setattr(configuration, "upload_config", upload)
    monkeypatch.setattr(configuration, "fetch_plan", fetch)

    def forever(job: object) -> None:
        raise SystemExit(0)

    assert configuration._serve("x", lambda plan, local: "job", forever) == 0
    assert attempts == {"upload": 2, "fetch": 2} and serve["sleep"] == [3, 3]  # nạp lỗi thì nạp lại, rồi đọc lỗi thì đọc lại


def test_run_load_config_ma_thoat_theo_ket_qua(monkeypatch: pytest.MonkeyPatch, serve) -> None:
    monkeypatch.setattr(configuration, "upload_config", lambda: (3, True))
    assert configuration.run_load_config() == 0

    def broken() -> tuple[int, bool]:
        raise ValueError("fast_ma sai")

    monkeypatch.setattr(configuration, "upload_config", broken)
    assert configuration.run_load_config() == 1


# =============================================================================
# 5. main.py: tham số dòng lệnh.
# =============================================================================


def test_runner_lane_la_module_cham_nguon_va_nguon_default_thanh_rong() -> None:
    args = main.parse_args(["--runner", "--lane", "ma_cross.og8", "--symbols", "us30,btcusd"])
    assert (args.runner, args.lane, args.source, args.symbols) == (True, "ma_cross", "og8", ("US30", "BTCUSD"))
    assert main.parse_args(["--runner", "--lane", "Trend.default"]).source == "" and main.parse_args(["--runner", "--lane", "trend.default"]).lane == "trend"


def test_publisher_lane_la_nguon_va_bo_di_thi_la_nguon_default() -> None:
    assert main.parse_args(["--publisher", "--lane", "og8"]).source == "og8"
    assert main.parse_args(["--publisher"]).source == "" and main.parse_args(["--publisher", "--lane", "default"]).source == ""


@pytest.mark.parametrize(
    "argv",
    [
        [], ["--runner"], ["--runner", "--lane", "ma_cross"], ["--runner", "--lane", "ma_cross.og-8"], ["--publisher", "--lane", "ma_cross.og8"],
        ["--load-config", "--lane", "og8"], ["--load-config", "--symbols", "US30"], ["--runner", "--publisher"], ["--runner", "--lane", "trend.default", "--symbols", "US30,US30"],
    ],
)
def test_tham_so_sai_thi_argparse_thoat_ma_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main.parse_args(argv)
    assert excinfo.value.code == 2


def test_main_giao_dung_viec_cho_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    import signal

    monkeypatch.setattr(signal, "signal", lambda *_: None)
    monkeypatch.setattr(configuration, "run_runner", lambda **kwargs: ("runner", kwargs) and 11)
    monkeypatch.setattr(configuration, "run_publisher", lambda **kwargs: 12)
    monkeypatch.setattr(configuration, "run_load_config", lambda: 13)
    assert main.main(["--runner", "--lane", "trend.og8"]) == 11 and main.main(["--publisher", "--lane", "og8"]) == 12 and main.main(["--load-config"]) == 13
