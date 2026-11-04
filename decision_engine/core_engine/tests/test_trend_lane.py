"""Test điều phối của tiến trình TREND (configuration.py: build_trend_job, process_trend_pair, run_trend_lane, run_trend) — hermetic.

Tiến trình trend nối db0 (nghe nến khung trend) và db trend của Redis đầu ra: tính hướng trend (trend_filtering) rồi ghi List–Hash (TrendOutput). Kết quả được tiến trình chiến lược đọc lại bằng TrendReader (test_trend_filtering.py, test_lane.py).
"""

from __future__ import annotations

import logging
from typing import ClassVar

import pandas as pd
import pytest
import redis
from engine_support import (
    ALL_STRATEGIES,
    GROUP_SYMBOLS,
    TREND_SOURCES,
    FakeClock,
    FakeSnapshots,
    LoopEngine,
    lane_config,
    make_bars,
    source_config,
    trend_config,
)

from core_engine.src import (
    configuration,
    data_engine,
    strategy_runner,
    trend_filtering,
)
from core_engine.src.data_engine import Pair


def _trend(method: str, sources: tuple[str, ...] = TREND_SOURCES, enabled: tuple[str, ...] = ALL_STRATEGIES):
    return configuration.build_trend_job(trend_config(enabled, sources), method)


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name.startswith("core_engine.src.")]


# =============================================================================
# 1. build_trend_job.
# =============================================================================


def test_trend_job_dung_viec_cua_mot_khoi() -> None:
    job = _trend("knn")
    assert job.method == "knn" and job.pairs == tuple(sorted(job.pairs)) and job.pairs
    assert {timeframe for _, timeframe in job.pairs} <= {"H1", "H2", "H3", "H4"}
    assert job.source["snapshot_bars"] == 500 and job.source["db"] == 0 and job.source["key_prefix"] == "L_CANDLE"
    assert job.sink == {
        "host": "127.0.0.1", "port": 6380, "password": "", "db": 1, "key_prefix": "L_TREND", "retention_seconds": 604800, "retention_max_entries": 1500,
        "socket_timeout": 5.0, "connect_timeout": 5.0, "healthcheck_interval": 30,
    }
    assert set(job.frame_params) == set(job.pairs)
    assert job.process_on_startup is True and job.reconnect_delay_seconds == 3.0 and job.log_level == "INFO"
    assert {symbol for symbol, _ in job.pairs} == set(GROUP_SYMBOLS)
    for (_symbol, trend_tf), params in job.frame_params.items():
        assert tuple(params["TREND_METHODS"]) == ("knn",) and params["TREND_TF"] == trend_tf


def test_trend_job_nguon_nen_them_chi_tinh_symbol_cua_nguon_va_ghi_trend_o_redis_cua_de() -> None:
    config = source_config()
    default = configuration.build_trend_job(config, "knn")
    extra = configuration.build_trend_job(config, "knn", "og8")
    assert default.input_name == "" and extra.input_name == "og8"
    assert {symbol for symbol, _ in default.pairs} == set(GROUP_SYMBOLS) and {symbol for symbol, _ in extra.pairs} == {"EURUSD", "FR40"}
    assert extra.source["host"] == "10.11.12.8" and extra.source["port"] == 6380 and extra.source["password"] == "mat-khau-og8" and extra.source["snapshot_bars"] == 500
    assert extra.sink == default.sink and extra.sink["host"] == "127.0.0.1" and extra.sink["db"] == 1  # kết quả trend vẫn ghi db trend của DE
    assert {symbol for symbol, _ in extra.frame_params} == {"EURUSD", "FR40"}


def test_trend_job_so_nen_tinh_theo_khoi_va_bi_chan_boi_nen_nguon_giu() -> None:
    zigzag = _trend("zigzag")
    assert zigzag.source["snapshot_bars"] == 1200  # zigzag đòi 3000 nhưng nguồn chỉ giữ 1200 nến
    config = trend_config()
    config["redis"]["input"]["source_max_bars"] = 900
    assert configuration.build_trend_job(config, "zigzag").source["snapshot_bars"] == 900
    config["redis"]["input"]["source_max_bars"] = 100  # không bao giờ ít hơn số nến chuẩn (snapshot_bars)
    assert configuration.build_trend_job(config, "zigzag").source["snapshot_bars"] == 500
    assert _trend("knn").source["snapshot_bars"] == 500


def test_trend_job_chuan_hoa_ten_khoi() -> None:
    assert configuration.build_trend_job(trend_config(), " KNN ").method == "knn"


@pytest.mark.parametrize(
    ("build", "match"),
    [
        (lambda: configuration.build_trend_job(trend_config(sources=("knn",)), "tstat"), "trend source 'tstat' is not declared in redis.trend_outputs"),
        (lambda: configuration.build_trend_job(trend_config(sources=("knn",)), "ema"), "trend source 'ema' is not declared in redis.trend_outputs"),
        (lambda: configuration.build_trend_job(lane_config(), "knn"), "trend source 'knn' is not declared in redis.trend_outputs"),
    ],
)
def test_trend_job_khoi_chua_khai_o_trend_outputs_thi_dung_ngay(build, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        build()


def test_trend_job_khong_chien_luoc_nao_dang_bat_dung_khoi_thi_dung_ngay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(configuration, "trend_method_applies", lambda key, method: False)
    with pytest.raises(ValueError, match="no enabled strategy uses trend source 'knn'"):
        _trend("knn")


def test_trend_job_hai_chien_luoc_doi_tinh_mot_khung_hai_cach_khac_nhau_thi_dung_ngay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(configuration, "_method_params_repr", lambda method, params: repr(sorted(params)))  # tham số tính của combo và ma_cross khác nhau
    with pytest.raises(ValueError, match="would be computed two different ways"):
        _trend("knn", enabled=("combo", "ma_cross"))


def test_trend_job_cac_khung_doi_so_nen_khac_nhau_thi_dung_ngay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(configuration, "_trend_bars", lambda base, trend: 500 if trend.trend_tf == "H4" else 600)
    with pytest.raises(ValueError, match="needs different candle counts for different frames"):
        _trend("knn")


# =============================================================================
# 2. process_trend_pair.
# =============================================================================


class _Output:
    """Đầu ra trend giả: ghi lại các lần ``write`` và trả số nến mới đã định sẵn."""

    def __init__(self, created: int = 3) -> None:
        self.created = created
        self.writes: list[tuple] = []

    def write(self, method: str, symbol: str, trend_tf: str, timeline: pd.DataFrame) -> int:
        self.writes.append((method, symbol, trend_tf, timeline))
        return self.created


def test_process_trend_pair_tinh_roi_ghi_dung_thong_so_va_dung_khoa(monkeypatch: pytest.MonkeyPatch) -> None:
    job, pair, candles = _trend("knn"), Pair("US30", "H1"), make_bars(600, "h")
    seen: list = []
    real = trend_filtering.compute_timeline
    monkeypatch.setattr(trend_filtering, "compute_timeline", lambda frame, params: seen.append((frame, params)) or real(frame, params))
    engine, output = FakeSnapshots({pair: candles}), _Output(created=3)
    assert configuration.process_trend_pair(job, engine, output, pair) == strategy_runner.Outcome("written", 3)  # số nến ghi mới do đầu ra báo
    assert engine.asked == [pair] and len(seen) == 1 and seen[0][0] is candles and seen[0][1] == job.frame_params[("US30", "H1")]
    ((method, symbol, trend_tf, timeline),) = output.writes
    assert (method, symbol, trend_tf) == ("knn", "US30", "H1")
    pd.testing.assert_frame_equal(timeline, real(candles, job.frame_params[("US30", "H1")]))  # ghi đúng bảng mà compute_timeline ra


def test_process_trend_pair_khung_chua_co_nen_thi_khong_tinh_va_khong_ghi(monkeypatch: pytest.MonkeyPatch) -> None:
    job, pair, output = _trend("knn"), Pair("US30", "H1"), _Output()
    monkeypatch.setattr(trend_filtering, "compute_timeline", lambda *a, **k: pytest.fail("không tính khi chưa có nến"))
    for frame in (None, pd.DataFrame(columns=data_engine.OHLCV_COLUMNS)):
        assert configuration.process_trend_pair(job, FakeSnapshots({pair: frame}), output, pair) is None
    assert output.writes == []


def test_method_params_repr_chi_co_voi_khoi_co_tham_so() -> None:
    frames = {"knn": "H1", "tstat": "H1", "zigzag": "H1", "breakout": "M30", "cloud": "H1"}

    def params(source: str) -> dict:
        return configuration._trend_params(configuration.get_strategy("ma_cross"), source, "US30", "M30", frames[source])

    for source in ("knn", "tstat", "zigzag"):
        text = configuration._method_params_repr(source, params(source))
        assert text.startswith("[(") and text == configuration._method_params_repr(source, params(source))
    for source in ("breakout", "cloud"):
        assert configuration._method_params_repr(source, params(source)) == ""  # khối không tham số
    assert configuration._method_params_repr("knn", params("knn")) != configuration._method_params_repr("tstat", params("tstat"))


# =============================================================================
# 3. Vòng lặp của tiến trình trend.
# =============================================================================

P1, P2, P3 = Pair("US30", "H1"), Pair("US30", "H2"), Pair("US30", "H4")


def test_vong_lap_trend_xu_ly_tung_khung_roi_gop_ket_qua_quet(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    clock, seen = FakeClock(), []
    engine = LoopEngine(clock, [P1, P2, P3])
    monkeypatch.setattr(configuration, "process_trend_pair", lambda job, _engine, _output, pair: seen.append(pair) or strategy_runner.Outcome("written", 1))
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        configuration.run_trend_lane(_trend("knn"), engine, _Output(), clock=clock, should_stop=lambda: len(seen) >= 3)
    assert seen == [P1, P2, P3]
    messages = _messages(caplog)
    assert messages[0] == "event=reconcile_start db=0 pairs=3" and messages[1].startswith("event=reconcile_end pairs=3 published=3 duration_s=")


def test_vong_lap_trend_loi_cua_mot_khung_khong_chan_khung_khac(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    clock, seen = FakeClock(), []

    def fake(job, _engine, _output, pair):
        seen.append(pair)
        if pair == P2:
            raise ValueError("nến hỏng")
        return strategy_runner.Outcome("written", 1)

    monkeypatch.setattr(configuration, "process_trend_pair", fake)
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        configuration.run_trend_lane(_trend("knn"), LoopEngine(clock, [P1, P2, P3]), _Output(), clock=clock, should_stop=lambda: len(seen) >= 3)
    assert seen == [P1, P2, P3]
    assert [m for m in _messages(caplog) if m.startswith("event=process_failed")] == ["event=process_failed symbol=US30 timeframe=H2"]
    assert next(m for m in _messages(caplog) if m.startswith("event=reconcile_end")).startswith("event=reconcile_end pairs=3 published=2")


def test_vong_lap_trend_loi_redis_noi_len_cho_vong_noi_lai(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()

    def broken(job, _engine, _output, pair):
        raise redis.ConnectionError("mất kết nối")

    monkeypatch.setattr(configuration, "process_trend_pair", broken)
    with pytest.raises(redis.ConnectionError):
        configuration.run_trend_lane(_trend("knn"), LoopEngine(clock, [P1]), _Output(), clock=clock, should_stop=lambda: False)


# =============================================================================
# 4. Kết nối lại và vòng đời của tiến trình.
# =============================================================================


class _Part:
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
        if _Part.fail_subscribe_once:
            _Part.fail_subscribe_once = False
            raise redis.ConnectionError("rớt lúc đăng ký")
        return len(self.kwargs["pairs"])

    def close(self) -> None:
        self.closed = True


class _FakeEngine(_Part):
    instances: ClassVar[list[_Part]] = []


class _FakeOutput(_Part):
    instances: ClassVar[list[_Part]] = []


def _patch_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeEngine.instances, _FakeOutput.instances, _Part.fail_subscribe_once = [], [], False
    monkeypatch.setattr(data_engine, "DataEngine", _FakeEngine)
    monkeypatch.setattr(trend_filtering, "TrendOutput", _FakeOutput)


def test_run_trend_forever_noi_db0_va_db_trend_roi_chay_vong_lap_va_dong_ket_noi(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _patch_parts(monkeypatch)
    ran = []

    def fake_run_trend_lane(job, engine, output, **kwargs):
        ran.append((engine, output))
        raise SystemExit(0)

    monkeypatch.setattr(configuration, "run_trend_lane", fake_run_trend_lane)
    job = _trend("knn")
    with caplog.at_level(logging.INFO, logger="core_engine.src"), pytest.raises(SystemExit):
        configuration._run_trend_forever(job)
    engine, output = _FakeEngine.instances[0], _FakeOutput.instances[0]
    assert ran == [(engine, output)]
    assert engine.kwargs["pairs"] == job.pairs and engine.kwargs["snapshot_bars"] == job.source["snapshot_bars"] and output.kwargs == job.sink
    assert engine.log == ["ping", "flags", "subscribe"] and output.log == ["ping"] and engine.closed and output.closed
    assert _messages(caplog) == [
        "event=redis_connected input_db=0 output_db=1",
        f"event=subscribed db=0 key_prefix=L_CANDLE notify_flags=hK pairs={len(job.pairs)}",
    ]


def test_run_trend_forever_mat_redis_thi_cho_roi_noi_lai(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _patch_parts(monkeypatch)
    _Part.fail_subscribe_once = True
    slept: list[float] = []
    monkeypatch.setattr(configuration.time, "sleep", slept.append)
    monkeypatch.setattr(configuration, "run_trend_lane", lambda job, engine, output, **kwargs: (_ for _ in ()).throw(SystemExit(0)))
    with caplog.at_level(logging.INFO, logger="core_engine.src"), pytest.raises(SystemExit):
        configuration._run_trend_forever(_trend("knn"))
    assert slept == [3.0] and len(_FakeEngine.instances) == 2 and len(_FakeOutput.instances) == 2
    assert all(part.closed for part in (*_FakeEngine.instances, *_FakeOutput.instances))
    assert any(m.startswith("event=redis_connection_lost error=rớt lúc đăng ký retry_in_s=3.0") for m in _messages(caplog))


def _patch_run_environment(monkeypatch: pytest.MonkeyPatch, config: dict) -> list[str]:
    from core_engine.src import log

    events: list[str] = []
    monkeypatch.setattr(configuration, "CONFIG", config)
    monkeypatch.setattr(log, "configure_logging", lambda level, name=None: events.append(f"logging:{level}:{name}"))
    monkeypatch.setattr(log, "set_log_level", lambda level: events.append(f"level:{level}"))
    return events


def test_run_trend_cau_hinh_hop_le_ghi_log_khoi_dong_roi_chay(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    events = _patch_run_environment(monkeypatch, trend_config())
    ran = []
    monkeypatch.setattr(configuration, "_run_trend_forever", ran.append)
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        assert configuration.run_trend(" KNN ") == 0
    assert events == ["logging:INFO:trend_knn", "level:INFO"] and ran[0].method == "knn"  # không bật nhớ đệm chỉ báo: tiến trình trend không chạy chiến lược
    messages = _messages(caplog)
    assert messages[0].startswith("event=startup trend=knn log_level=INFO redis_input_db=0 redis_output_db=1 frames=") and messages[0].endswith("bars=500 lane=trend_knn")
    assert messages[1].startswith("event=input_source name=default host=127.0.0.1 port=6379 symbols=") and messages[2] == "event=trend_output key_prefix=L_TREND"


def test_run_trend_voi_nguon_nen_them_dat_ten_log_rieng(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    events = _patch_run_environment(monkeypatch, source_config())
    ran = []
    monkeypatch.setattr(configuration, "_run_trend_forever", ran.append)
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        assert configuration.run_trend("knn", "og8") == 0
    assert events[0] == "logging:INFO:trend_knn_og8" and ran[0].input_name == "og8"  # file log riêng: de_run_trend_knn_og8.log
    messages = _messages(caplog)
    assert messages[0].endswith("lane=trend_knn_og8") and messages[1] == "event=input_source name=og8 host=10.11.12.8 port=6380 symbols=2"


def test_run_trend_tat_bang_cong_tac_thi_ghi_log_roi_thoat_em(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    config = trend_config()
    config["redis"]["enabled"] = False
    _patch_run_environment(monkeypatch, config)
    monkeypatch.setattr(configuration, "_run_trend_forever", lambda job: pytest.fail("không chạy khi redis.enabled=false"))
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        assert configuration.run_trend("knn") == 0
    assert "event=disabled reason=redis_disabled_by_config" in _messages(caplog)


def test_run_trend_khoi_chua_khai_thi_ghi_config_error_va_tra_1(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _patch_run_environment(monkeypatch, trend_config(sources=("knn",)))
    monkeypatch.setattr(configuration, "_run_trend_forever", lambda job: pytest.fail("không chạy khi cấu hình sai"))
    with caplog.at_level(logging.CRITICAL, logger="core_engine.src"):
        assert configuration.run_trend("tstat") == 1
    assert any("event=config_error" in m and "not declared in redis.trend_outputs" in m for m in _messages(caplog))


def test_run_trend_thieu_co_keyspace_la_config_error(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _patch_run_environment(monkeypatch, trend_config())

    def broken(job):
        raise RuntimeError("Redis notify-keyspace-events thiếu lớp ['h']")

    monkeypatch.setattr(configuration, "_run_trend_forever", broken)
    with caplog.at_level(logging.CRITICAL, logger="core_engine.src"):
        assert configuration.run_trend("knn") == 1
    assert any("event=config_error" in m and "notify-keyspace-events" in m for m in _messages(caplog))


def test_run_trend_dung_em_khi_nhan_sigterm_hoac_ctrl_c(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _patch_run_environment(monkeypatch, trend_config())
    for stop in (SystemExit(0), KeyboardInterrupt()):
        def stopping(job, stop=stop):
            raise stop

        monkeypatch.setattr(configuration, "_run_trend_forever", stopping)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="core_engine.src"):
            assert configuration.run_trend("knn") == 0
        assert "event=stopped" in _messages(caplog)
