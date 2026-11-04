"""Test trend_filtering.py: tính dòng thời gian hướng trend từ nến khung trend (không config), ghi List–Hash vào db trend (TrendOutput) và đọc lại (TrendReader) — hermetic."""

from __future__ import annotations

import logging

import pandas as pd
import pytest
from engine_support import RecordingRedis, decode_eval, make_bars

from core_engine.src import configuration, redis_connector, trend_filtering
from core_engine.src.configuration import get_strategy

# (khối, khung trend) cho ô ma_cross M30: breakout dùng chính khung vào lệnh (M30), bốn khối còn lại dùng H1.
CASES = [("knn", "H1"), ("breakout", "M30"), ("zigzag", "H1"), ("tstat", "H1"), ("cloud", "H1")]
FREQ = {"H1": "h", "M30": "30min"}
MINUTES = {"H1": 60, "M30": 30}


def _params(source: str, trend_tf: str) -> dict:
    return configuration._trend_params(get_strategy("ma_cross"), source, "US30", "M30", trend_tf)


@pytest.mark.parametrize(("source", "trend_tf"), CASES)
def test_compute_timeline_ra_ba_cot_moi_nen_mot_dong_va_khong_sua_nen_dau_vao(source: str, trend_tf: str) -> None:
    candles = make_bars(600, FREQ[trend_tf])
    before = candles.copy()
    timeline = trend_filtering.compute_timeline(candles, _params(source, trend_tf))
    pd.testing.assert_frame_equal(candles, before)
    assert list(timeline.columns) == trend_filtering.TIMELINE_COLUMNS and len(timeline) == 600
    assert isinstance(timeline.index, pd.RangeIndex) and timeline["bartime"].equals(candles["bartime"])
    assert (timeline["trend_close_time"] - timeline["bartime"] == pd.Timedelta(minutes=MINUTES[trend_tf])).all()  # giờ đóng = giờ mở + độ dài khung trend
    assert timeline["trend_bias"].dtype == "int64" and set(timeline["trend_bias"].unique()) <= {-1, 0, 1}


def test_compute_timeline_knn_co_nen_co_huong_ro_rang() -> None:
    timeline = trend_filtering.compute_timeline(make_bars(600, "h"), _params("knn", "H1"))
    assert (timeline["trend_bias"] != 0).any()


# =============================================================================
# Đầu ra: TrendOutput ghi List–Hash vào Redis (db trend).
# =============================================================================


def _timeline(n: int = 5, start: str = "2026-11-03 00:00") -> pd.DataFrame:
    bartime = pd.date_range(start, periods=n, freq="h").astype("datetime64[us]")
    return pd.DataFrame({"bartime": bartime, "trend_bias": [1, 1, 0, -1, 1][:n] if n <= 5 else [1] * n, "trend_close_time": bartime + pd.Timedelta(hours=1)})


def _output(client: RecordingRedis, **changes) -> trend_filtering.TrendOutput:
    kwargs = {"host": "127.0.0.1", "port": 6380, "password": "", "db": 1, "key_prefix": "L_TREND", "retention_seconds": 604800, "retention_max_entries": 1500, "client": client}
    return trend_filtering.TrendOutput(**{**kwargs, **changes})


def test_trend_output_lan_dau_ghi_ca_bang_theo_list_hash_dung_ten_va_truong() -> None:
    client = RecordingRedis(emulate=True)
    assert _output(client).write("knn", "us30", "h1", _timeline()) == 5
    calls = [decode_eval(c) for c in client.calls]
    assert [c["stamp"] for c in calls] == [f"2026-11-03 0{i}:00:00" for i in range(5)]  # cũ đứng trước
    first = calls[0]
    assert first["list_key"] == "L_TREND_KNN_US30_H1" and first["hash_key"] == "L_TREND_KNN_US30_H1:2026-11-03 00:00:00"
    assert first["ttl"] == "604800" and first["max"] == "1500" and first["channel"] == "" and first["message"] == ""  # trend không có kênh Pub/Sub
    assert first["fields"] == {
        "method": "knn", "symbol": "US30", "timeframe": "H1", "bartime": "2026-11-03 00:00:00", "trend_bias": "1", "close_time": "2026-11-03 01:00:00",
    }
    assert [c["fields"]["trend_bias"] for c in calls] == ["1", "1", "0", "-1", "1"]


def test_trend_output_lan_sau_chi_gui_nen_moi() -> None:
    client = RecordingRedis(emulate=True)
    output = _output(client)
    output.write("knn", "US30", "H1", _timeline(4))
    client.calls.clear()
    assert output.write("knn", "US30", "H1", _timeline(4)) == 0 and client.calls == []  # không có nến mới: không gửi gì
    assert output.write("knn", "US30", "H1", _timeline(5)) == 1
    assert [decode_eval(c)["stamp"] for c in client.calls] == ["2026-11-03 04:00:00"]  # chỉ nến thứ 5


def test_trend_output_moi_cap_mot_moc_nho_rieng() -> None:
    client = RecordingRedis(emulate=True)
    output = _output(client)
    assert output.write("knn", "US30", "H1", _timeline(2)) == 2
    assert output.write("tstat", "US30", "H1", _timeline(2)) == 2  # khối khác: LIST khác
    assert output.write("knn", "HK50", "H1", _timeline(2)) == 2  # symbol khác
    assert {decode_eval(c)["list_key"] for c in client.calls} == {"L_TREND_KNN_US30_H1", "L_TREND_TSTAT_US30_H1", "L_TREND_KNN_HK50_H1"}


def test_trend_output_khoi_dong_lai_gui_lai_ca_bang_nhung_redis_bo_qua_nen_da_co() -> None:
    client = RecordingRedis(emulate=True)
    _output(client).write("knn", "US30", "H1", _timeline(5))
    restarted = _output(client)  # tiến trình mới: chưa nhớ gì
    assert restarted.write("knn", "US30", "H1", _timeline(5)) == 0  # gửi lại cả bảng, Redis báo 0 mốc mới
    assert len(client.calls) == 10


def test_trend_output_chuoi_rong_va_tham_so_ket_noi(monkeypatch: pytest.MonkeyPatch) -> None:
    for blank in ("", "  "):
        with pytest.raises(ValueError, match="key_prefix must be configured"):
            _output(RecordingRedis(), key_prefix=blank)
    seen: list[dict] = []
    monkeypatch.setattr(redis_connector.redis, "Redis", lambda **kwargs: seen.append(kwargs) or RecordingRedis())
    trend_filtering.TrendOutput(
        host="10.0.0.5", port="6380", password="pw", db="1", key_prefix="L_TREND", retention_seconds=1, retention_max_entries=1,
        socket_timeout=4, connect_timeout=3, healthcheck_interval=20,
    )
    assert seen == [{
        "host": "10.0.0.5", "port": 6380, "db": 1, "password": "pw", "decode_responses": True,
        "socket_timeout": 4.0, "socket_connect_timeout": 3.0, "health_check_interval": 20,
    }]


def test_trend_output_ping_va_close() -> None:
    client = RecordingRedis()
    output = _output(client)
    output.ping()
    output.close()
    assert client.pinged == 1 and client.closed


# =============================================================================
# Đầu vào: TrendReader đọc lại đúng cái TrendOutput đã ghi (cùng tên khoá, cùng định dạng).
# =============================================================================


def _reader(client: RecordingRedis, **changes) -> trend_filtering.TrendReader:
    kwargs = {"host": "127.0.0.1", "port": 6380, "password": "", "db": 1, "key_prefix": "L_TREND", "client": client}
    return trend_filtering.TrendReader(**{**kwargs, **changes})


def test_trend_reader_doc_lai_dung_bang_da_ghi_tang_dan_cung_kieu_thoi_gian_voi_bang_nen() -> None:
    client = RecordingRedis(emulate=True)
    written = _timeline(5)
    _output(client).write("knn", "US30", "H1", written)
    table = _reader(client).read("knn", "us30", "h1", 100)  # tên chữ thường vẫn ra cùng khoá
    assert list(table.columns) == trend_filtering.TIMELINE_COLUMNS
    pd.testing.assert_frame_equal(table, written[trend_filtering.TIMELINE_COLUMNS])
    assert table["bartime"].dtype == table["trend_close_time"].dtype == make_bars(2)["bartime"].dtype  # datetime64[us], cùng kiểu cột bartime của bảng nến
    assert table["trend_bias"].dtype == "int64"


def test_trend_reader_chi_lay_so_moc_moi_nhat_xin_va_chi_doc_khong_ghi() -> None:
    client = RecordingRedis(emulate=True)
    _output(client).write("knn", "US30", "H1", _timeline(5))
    writes = len(client.calls)
    table = _reader(client).read("knn", "US30", "H1", 3)
    assert table["bartime"].dt.strftime("%H:%M").tolist() == ["02:00", "03:00", "04:00"]  # 3 mốc mới nhất, cũ đứng trước
    assert len(client.calls) == writes  # đọc không gửi lệnh ghi nào


def test_trend_reader_dung_chung_ten_khoa_voi_trend_output() -> None:
    client = RecordingRedis()
    assert _reader(client).list_key("tstat", "hk50", "h4") == _output(client).list_key("tstat", "hk50", "h4") == "L_TREND_TSTAT_HK50_H4"
    assert trend_filtering.trend_list_key("L_TREND", "knn", "us30", "h1") == "L_TREND_KNN_US30_H1"


def test_trend_reader_chua_co_khoa_thi_tra_bang_rong_dung_cot_va_kieu() -> None:
    table = _reader(RecordingRedis(emulate=True)).read("knn", "US30", "H1", 10)
    assert table.empty and list(table.columns) == trend_filtering.TIMELINE_COLUMNS
    assert str(table["bartime"].dtype) == "datetime64[us]" and str(table["trend_bias"].dtype) == "int64"


def test_trend_reader_moc_da_het_han_bi_bo_va_canh_bao(caplog: pytest.LogCaptureFixture) -> None:
    client = RecordingRedis(emulate=True)
    _output(client).write("knn", "US30", "H1", _timeline(5))
    del client.hashes["L_TREND_KNN_US30_H1:2026-11-03 01:00:00"]  # HASH của một mốc đã hết hạn nhưng mốc vẫn còn trong LIST
    with caplog.at_level(logging.WARNING, logger="core_engine.src"):
        table = _reader(client).read("knn", "US30", "H1", 100)
    assert table["bartime"].dt.strftime("%H:%M").tolist() == ["00:00", "02:00", "03:00", "04:00"]
    assert [r.getMessage() for r in caplog.records] == ["event=trend_marks_missing trend=knn symbol=US30 timeframe=H1 missing=1 of=5"]
    client.hashes.clear()  # mọi HASH hết hạn: coi như chưa có xu hướng
    assert _reader(client).read("knn", "US30", "H1", 100).empty


def test_trend_reader_chuoi_rong_tham_so_ket_noi_ping_va_close(monkeypatch: pytest.MonkeyPatch) -> None:
    for blank in ("", "  "):
        with pytest.raises(ValueError, match="key_prefix must be configured"):
            _reader(RecordingRedis(), key_prefix=blank)
    client = RecordingRedis()
    reader = _reader(client)
    reader.ping()
    reader.close()
    assert client.pinged == 1 and client.closed
    seen: list[dict] = []
    monkeypatch.setattr(redis_connector.redis, "Redis", lambda **kwargs: seen.append(kwargs) or RecordingRedis())
    trend_filtering.TrendReader(
        host="10.0.0.5", port="6380", password="pw", db="1", key_prefix="L_TREND", socket_timeout=4, connect_timeout=3, healthcheck_interval=20,
    )
    assert seen == [{
        "host": "10.0.0.5", "port": 6380, "db": 1, "password": "pw", "decode_responses": True,
        "socket_timeout": 4.0, "socket_connect_timeout": 3.0, "health_check_interval": 20,
    }]
