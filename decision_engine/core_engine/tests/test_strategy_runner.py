"""Test strategy_runner.py: tính ba loại cấu phần THÔ (TRIGGER: đủ điều kiện của một chiến lược tại một khung; CONTEXT: macd_h và ma; TREND: hướng trend của một khối) từ nến và ghi vào db0/db1 của DE theo
List–Hash (ComponentWriter) — hermetic.

Việc ráp hai nửa (runner ghi, publisher đọc và quyết định) và so với đường một tiến trình cũ nằm ở test_lane.py.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest
from engine_support import (
    ALL_STRATEGIES,
    RecordingRedis,
    decode_eval,
    make_bars,
    run_strategy,
)

from core_engine.src import redis_connector, strategy_runner
from core_engine.src.configuration import get_strategy
from core_engine.src.indicator import macd_hist, sma

CONTEXT = {"macd_fast": 5, "macd_slow": 25, "macd_signal": 5, "ma_period": 20}
TREND_METHODS = ("knn", "breakout", "zigzag", "tstat", "cloud")


# =============================================================================
# 1. Ba loại bảng cấu phần.
# =============================================================================


@pytest.mark.parametrize("method", TREND_METHODS)
def test_trend_table_ba_cot_moi_nen_mot_dong_va_khong_sua_nen_dau_vao(method: str) -> None:
    bars = make_bars(600, "h")
    before = bars.copy()
    table = strategy_runner.trend_table(bars, method, "H1")
    pd.testing.assert_frame_equal(bars, before)
    assert list(table.columns) == ["bartime", "close_time", "trend_bias"] and len(table) == len(bars)
    assert (table["close_time"] - table["bartime"] == pd.Timedelta(hours=1)).all()
    assert set(table["trend_bias"].astype(int)) <= {-1, 0, 1} and table["bartime"].dtype == "datetime64[us]" and table["close_time"].dtype == "datetime64[us]"


def test_trend_table_tinh_tren_chinh_khung_cua_nen_nen_gio_dong_theo_khung_do() -> None:
    table = strategy_runner.trend_table(make_bars(600, "4h"), "knn", "H4")
    assert (table["close_time"] - table["bartime"] == pd.Timedelta(hours=4)).all()
    assert table["trend_bias"].astype(int).abs().sum() > 0  # nến đủ dài thì KNN có hướng rõ ràng


@pytest.mark.parametrize("key", ALL_STRATEGIES)
def test_trigger_table_la_tin_hieu_tho_cua_chien_luoc_tren_chinh_khung_va_khop_pipeline_tham_chieu(key: str) -> None:
    bars = make_bars(900, "h")
    before = bars.copy()
    spec = get_strategy(key)
    params = spec.normalize_params(None, "HK50", "H1")
    table = strategy_runner.trigger_table(spec, bars, params, "HK50", "H1")
    pd.testing.assert_frame_equal(bars, before)
    assert list(table.columns) == ["bartime", "close_time", "signal", "setup", "open", "high", "low", "close", "atr", "reason"] and len(table) == len(bars)
    reference = run_strategy(key, symbol="HK50", tf="H1", bars=bars)
    assert table["signal"].tolist() == reference["raw_signal"].tolist() and (table["signal"] != 0).any()  # signal = raw_signal, sau lọc trùng
    for column in ("open", "high", "low", "close"):
        assert table[column].tolist() == bars[column].tolist()
    assert (table["close_time"] - table["bartime"] == pd.Timedelta(hours=1)).all()
    fired = table["signal"] != 0
    assert (table.loc[fired, "setup"] == table.loc[fired, "signal"]).all()  # nến bắn luôn nằm trong mặt nạ cùng chiều
    assert set(table["setup"]) <= {-1, 0, 1} and (table["reason"] != "").tolist() == fired.tolist()  # lý do chỉ có ở nến có tín hiệu


def test_trigger_table_setup_cho_biet_nen_con_du_dieu_kien_ca_khi_nen_do_khong_phai_nen_bat_dau_cua_dot() -> None:
    bars = make_bars(900, "h")
    spec = get_strategy("ma_cross")
    table = strategy_runner.trigger_table(spec, bars, spec.normalize_params(None, "HK50", "H1"), "HK50", "H1")
    assert ((table["setup"] != 0) & (table["signal"] == 0)).any()  # trạng thái kéo dài: lọc trùng chỉ giữ nến đầu, setup còn ở các nến sau
    assert (table["setup"] != 0).sum() > (table["signal"] != 0).sum()


def test_context_table_macd_va_ma_cua_chinh_khung_theo_tham_so_chuan() -> None:
    bars = make_bars(300, "h")
    before = bars.copy()
    table = strategy_runner.context_table(bars, CONTEXT, "H1")
    pd.testing.assert_frame_equal(bars, before)
    assert list(table.columns) == ["bartime", "close_time", "macd_h", "ma"] and len(table) == len(bars)
    pd.testing.assert_series_equal(table["macd_h"], macd_hist(bars["close"], fast=5, slow=25, signal=5), check_names=False)
    pd.testing.assert_series_equal(table["ma"], sma(bars["close"], 20), check_names=False)
    assert table["ma"].iloc[:19].isna().all() and table["ma"].iloc[19:].notna().all()
    assert (table["close_time"] - table["bartime"] == pd.Timedelta(hours=1)).all()
    other = strategy_runner.context_table(bars, {**CONTEXT, "ma_period": 50}, "H1")
    assert other["ma"].notna().sum() < table["ma"].notna().sum()  # tham số do người gọi truyền vào
    pd.testing.assert_series_equal(other["macd_h"], table["macd_h"])


# =============================================================================
# =============================================================================
# 2. Ghi vào db0 của DE: ComponentWriter.
# =============================================================================


def _context(n: int = 5, start: str = "2026-11-03 00:00") -> pd.DataFrame:
    bartime = pd.date_range(start, periods=n, freq="h").astype("datetime64[us]")
    return pd.DataFrame(
        {"bartime": bartime, "close_time": bartime + pd.Timedelta(hours=1), "macd_h": [0.1, -2.5, math.nan, 4.0, 1 / 3][:n], "atr": [6.949058123456789] * n}
    )


def _trend(n: int = 5, start: str = "2026-11-03 00:00") -> pd.DataFrame:
    bartime = pd.date_range(start, periods=n, freq="h").astype("datetime64[us]")
    return pd.DataFrame({"bartime": bartime, "close_time": bartime + pd.Timedelta(hours=1), "trend_bias": [1, 1, 0, -1, 1][:n]})


def _writer(client: RecordingRedis, **changes) -> strategy_runner.ComponentWriter:
    kwargs = {"host": "127.0.0.1", "port": 6380, "password": "", "db": 0, "key_prefix": "", "retention_max_entries": 1000, "client": client}
    return strategy_runner.ComponentWriter(**{**kwargs, **changes})


@pytest.mark.parametrize("key", ALL_STRATEGIES)
def test_trigger_events_chi_giu_nen_co_tin_hieu_con_setup_view_giu_moi_nen_khong_co_tin_hieu(key: str) -> None:
    bars = make_bars(900, "h")
    spec = get_strategy(key)
    table = strategy_runner.trigger_table(spec, bars, spec.normalize_params(None, "HK50", "H1"), "HK50", "H1")
    events, setup = strategy_runner.trigger_events(table), strategy_runner.setup_view(table)
    assert len(events) > 0 and (events["signal"] != 0).all() and set(events["signal"].astype(int)) <= {-1, 1}  # chỉ nến bắn tín hiệu
    assert len(events) == int((table["signal"] != 0).sum()) < len(table) // 4  # sự kiện hiếm, không phải mọi nến
    assert list(events.columns) == list(table.columns) and (events["reason"] != "").all()  # đủ cột: giá, atr, lý do của chính nến sự kiện
    assert list(setup.columns) == ["bartime", "close_time", "setup", "open", "high", "low", "close", "atr"] and len(setup) == len(table)  # mọi nến, không có signal/reason
    assert setup["bartime"].tolist() == table["bartime"].tolist()


def test_component_writer_drop_before_bo_su_kien_cu_hon_nen_dau_cua_so_theo_gio_nen() -> None:
    client = RecordingRedis(emulate=True)
    writer = _writer(client)
    events = _trend(5).rename(columns={"trend_bias": "signal"})  # bốn cột đủ để ghi: giờ nến + một cột số
    assert writer.write("HK50", "TRIGGER", "H1", events, qualifier="combo") == 5
    assert writer.drop_before("HK50", "TRIGGER", "H1", pd.Timestamp("2026-11-03 02:00"), qualifier="combo") == 2  # bỏ 00:00 và 01:00
    assert client.trims == [("HK50_TRIGGER_COMBO_H1", "2026-11-03 02:00:00")]
    assert client.lists["HK50_TRIGGER_COMBO_H1"] == ["2026-11-03 02:00:00", "2026-11-03 03:00:00", "2026-11-03 04:00:00"]
    assert "HK50_TRIGGER_COMBO_H1:2026-11-03 00:00:00" not in client.hashes
    assert writer.drop_before("HK50", "TRIGGER", "H1", pd.Timestamp("2026-11-03 02:00"), qualifier="combo") == 0  # lần sau không còn gì để bỏ
    assert writer.drop_before("HK50", "TRIGGER", "H4", pd.Timestamp("2026-11-03 02:00"), qualifier="combo") == 0  # LIST chưa có: không lỗi


def test_component_writer_bang_su_kien_rong_thi_khong_ghi_gi() -> None:
    client = RecordingRedis(emulate=True)
    assert _writer(client).write("HK50", "TRIGGER", "H1", _trend(5).rename(columns={"trend_bias": "signal"}).iloc[0:0], qualifier="combo") == 0 and client.calls == []


def test_component_writer_lan_dau_ghi_ca_bang_theo_list_hash_dung_ten_va_truong() -> None:
    client = RecordingRedis(emulate=True)
    assert _writer(client).write("hk50", "TREND", "h3", _trend(), qualifier="knn") == 5
    calls = [decode_eval(c) for c in client.calls]
    assert [c["stamp"] for c in calls] == [f"2026-11-03 0{i}:00:00" for i in range(5)]  # cũ đứng trước
    first = calls[0]
    assert first["list_key"] == "HK50_TREND_KNN_H3" and first["hash_key"] == "HK50_TREND_KNN_H3:2026-11-03 00:00:00"  # định danh (phương pháp) nằm giữa vai trò và khung
    assert first["ttl"] == str(2 * 1000 * 180 * 60) and first["max"] == "1000" and first["channel"] == "" and first["message"] == ""  # không kênh Pub/Sub; hết hạn gấp đôi số mốc × độ dài khung
    assert first["fields"] == {"bartime": "2026-11-03 00:00:00", "close_time": "2026-11-03 01:00:00", "trend_bias": "1"}
    assert [c["fields"]["trend_bias"] for c in calls] == ["1", "1", "0", "-1", "1"]


def test_component_writer_so_thuc_ghi_bang_repr_doc_lai_dung_tung_bit_va_nan_la_chu_nan() -> None:
    client = RecordingRedis(emulate=True)
    _writer(client).write("HK50", "CONTEXT", "H1", _context())
    fields = [decode_eval(c)["fields"] for c in client.calls]
    assert fields[0] == {"bartime": "2026-11-03 00:00:00", "close_time": "2026-11-03 01:00:00", "macd_h": "0.1", "atr": "6.949058123456789"}
    assert fields[2]["macd_h"] == "nan" and fields[4]["macd_h"] == repr(1 / 3) and float(fields[4]["macd_h"]) == 1 / 3


def test_component_writer_tien_to_khoa_va_moi_vai_tro_khung_symbol_mot_list_rieng() -> None:
    client = RecordingRedis(emulate=True)
    writer = _writer(client, key_prefix="DE")
    writer.write("HK50", "CONTEXT", "H1", _context(2))
    writer.write("HK50", "CONTEXT", "H2", _context(2))  # khung khác
    writer.write("HK50", "TREND", "H1", _trend(2), qualifier="KNN")  # vai trò khác
    writer.write("HK50", "TREND", "H1", _trend(2), qualifier="ZIGZAG")  # định danh khác = list khác, mốc đã ghi của list này không bị coi là của list kia
    writer.write("US30", "CONTEXT", "H1", _context(2))  # symbol khác
    assert {decode_eval(c)["list_key"] for c in client.calls} == {
        "DE_HK50_CONTEXT_H1", "DE_HK50_CONTEXT_H2", "DE_HK50_TREND_KNN_H1", "DE_HK50_TREND_ZIGZAG_H1", "DE_US30_CONTEXT_H1",
    }


def test_component_writer_lan_sau_chi_gui_mot_moi() -> None:
    client = RecordingRedis(emulate=True)
    writer = _writer(client)
    writer.write("HK50", "CONTEXT", "H1", _context(4))
    client.calls.clear()
    assert writer.write("HK50", "CONTEXT", "H1", _context(4)) == 0 and client.calls == []  # không có mốc mới: không gửi gì
    assert writer.write("HK50", "CONTEXT", "H1", _context(5)) == 1
    assert [decode_eval(c)["stamp"] for c in client.calls] == ["2026-11-03 04:00:00"]  # chỉ mốc thứ 5


def test_component_writer_khoi_dong_lai_gui_lai_ca_bang_nhung_redis_bo_qua_mot_da_co() -> None:
    client = RecordingRedis(emulate=True)
    _writer(client).write("HK50", "CONTEXT", "H1", _context(5))
    restarted = _writer(client)  # tiến trình mới: chưa nhớ gì
    assert restarted.write("HK50", "CONTEXT", "H1", _context(5)) == 0  # gửi lại cả bảng, Redis báo 0 mốc mới
    assert len(client.calls) == 10


def test_component_writer_bang_rong_va_tham_so_ket_noi(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RecordingRedis(emulate=True)
    assert _writer(client).write("HK50", "CONTEXT", "H1", _context(0)) == 0 and client.calls == []
    seen: list[dict] = []
    monkeypatch.setattr(redis_connector.redis, "Redis", lambda **kwargs: seen.append(kwargs) or RecordingRedis())
    strategy_runner.ComponentWriter(
        host="10.0.0.5", port="6380", password="pw", db="0", key_prefix="", retention_max_entries=1000, socket_timeout=4, connect_timeout=3, healthcheck_interval=20,
    )
    assert seen == [{
        "host": "10.0.0.5", "port": 6380, "db": 0, "password": "pw", "decode_responses": True,
        "socket_timeout": 4.0, "socket_connect_timeout": 3.0, "health_check_interval": 20,
    }]


def test_component_writer_ping_va_close() -> None:
    client = RecordingRedis()
    writer = _writer(client)
    writer.ping()
    writer.close()
    assert client.pinged == 1 and client.closed
