"""Test data_engine.py (cửa vào duy nhất của dữ liệu đọc vào): đọc nến, đọc cấu phần db0 của DE, bộ đệm gia tăng, nghe keyspace theo cặp — hermetic, Redis giả (engine_support.FakeCore cho nến,
RecordingRedis cho cấu phần).

Nguyên tắc kiểm: bảng nến mà bộ đệm trả về phải GIỐNG HỆT đọc đầy đủ ở mọi bước (``assert_frame_equal``, kể cả dtype), và các lệnh Redis đã gọi cho thấy đúng đường nào được dùng
(gia tăng chỉ hỏi cửa sổ đầu LIST và HASH của nến mới; bất thường thì đọc đầy đủ).
"""

from __future__ import annotations

import logging
import math

import pandas as pd
import pytest
import redis
from engine_support import (
    PREFIX,
    FakeCore,
    RecordingRedis,
    bar_row,
    make_bars,
    stamp_of,
)

from core_engine.src import data_engine, redis_connector, strategy_runner
from core_engine.src.data_engine import (
    HEAD_WINDOW,
    DataEngine,
    Pair,
    pair_list_key,
    parse_pair_key,
    read_candles,
    read_components,
)

US30_H1 = Pair("US30", "H1")


def _engine(core: FakeCore, pairs=(("US30", "H1"),), bars: int = 500) -> DataEngine:
    return DataEngine(host="127.0.0.1", port=6379, password="", db=0, key_prefix=PREFIX, snapshot_bars=bars, pairs=pairs, client=core, events_client=core)


def _core_with(n: int, symbol: str = "US30", timeframe: str = "H1") -> tuple[FakeCore, pd.DataFrame]:
    core = FakeCore()
    bars = make_bars(n)
    core.fill(symbol, timeframe, bars)
    return core, bars


def _push_next(core: FakeCore, bars: pd.DataFrame, index: int, **kwargs) -> None:
    bar = bars.iloc[index]
    core.push("US30", "H1", stamp_of(bar["bartime"]), bar_row(bar), **kwargs)


def _full(core: FakeCore, bars: int = 500) -> pd.DataFrame:
    return read_candles(core, symbol="US30", timeframe="H1", key_prefix=PREFIX, snapshot_bars=bars)


# =============================================================================
# 1. Tên key, đọc đầy đủ.
# =============================================================================


def test_parse_pair_key_chi_nhan_key_list_cua_cap() -> None:
    assert parse_pair_key("L_CANDLE_US30_M5", PREFIX) == Pair("US30", "M5")
    assert parse_pair_key("L_CANDLE_btcusd_h4", PREFIX) == Pair("BTCUSD", "H4")
    for bad in ("L_CANDLE_US30_M5:2026-01-01 00:00:00", "OTHER_US30_M5", "L_CANDLE_US30", "L_CANDLE__M5", "L_CANDLE_US30_"):
        assert parse_pair_key(bad, PREFIX) is None


def test_read_candles_tra_dung_n_nen_moi_nhat_tang_dan_dung_dtype() -> None:
    core, bars = _core_with(700)
    frame = read_candles(core, symbol="US30", timeframe="H1", key_prefix=PREFIX, snapshot_bars=500)
    pd.testing.assert_frame_equal(frame, bars.tail(500).reset_index(drop=True))
    assert str(frame["bartime"].dtype) == "datetime64[us]"
    assert core.calls[0] == ("lrange", "L_CANDLE_US30_H1", 0, 499)  # hai lượt: LRANGE rồi pipeline HMGET


def test_read_candles_list_ngan_hon_thi_tra_it_nen_va_list_rong_thi_tra_bang_rong() -> None:
    core, bars = _core_with(30)
    pd.testing.assert_frame_equal(_full(core), bars)
    empty = read_candles(core, symbol="XAUUSD", timeframe="M10", key_prefix=PREFIX, snapshot_bars=500)
    assert empty.empty and list(empty.columns) == data_engine.OHLCV_COLUMNS


def test_read_candles_bo_nen_thieu_hash_va_canh_bao(caplog: pytest.LogCaptureFixture) -> None:
    core, bars = _core_with(40)
    _push_next(core, make_bars(41), 40, with_hash=False)  # LIST đã có mốc mới nhưng HASH chưa tới (Core ghi bằng các lệnh rời)
    with caplog.at_level(logging.WARNING, logger="core_engine.src.data_engine"):
        frame = _full(core)
    pd.testing.assert_frame_equal(frame, bars)
    assert any(r.getMessage().startswith("event=candle_stamp_missing symbol=US30 timeframe=H1 count=1") for r in caplog.records)


def test_read_candles_gia_tri_sai_thi_raise_va_ghi_log(caplog: pytest.LogCaptureFixture) -> None:
    core, _ = _core_with(40)
    core.push("US30", "H1", "2026-12-31 00:00:00", {"open": "1", "high": "2", "low": "0", "close": "not-a-number"})
    with caplog.at_level(logging.ERROR, logger="core_engine.src.data_engine"), pytest.raises(ValueError, match="contains invalid values"):
        _full(core)
    assert any("event=candle_snapshot_invalid" in r.getMessage() for r in caplog.records)


def test_read_candles_mot_moc_lpush_hai_lan_chi_giu_mot_dong(caplog: pytest.LogCaptureFixture) -> None:
    core, _ = _core_with(40)
    _push_next(core, make_bars(41), 40, duplicate=True)
    with caplog.at_level(logging.WARNING, logger="core_engine.src.data_engine"):
        frame = _full(core)
    pd.testing.assert_frame_equal(frame, make_bars(41))
    assert any(r.getMessage().startswith("event=candle_stamp_duplicate") for r in caplog.records)


# =============================================================================
# 2. Bộ đệm gia tăng: kết quả giống hệt đọc đầy đủ, nhưng chỉ đọc cái mới.
# =============================================================================


def test_snapshot_lan_dau_doc_day_du_roi_giu_bo_dem() -> None:
    core, bars = _core_with(700)
    engine = _engine(core)
    frame = engine.snapshot(US30_H1)
    pd.testing.assert_frame_equal(frame, bars.tail(500).reset_index(drop=True))
    assert sum(1 for c in core.calls if c[0] == "hmget") == 500


def test_snapshot_khong_co_nen_moi_tra_cung_doi_tuong_va_chi_hoi_cua_so_dau_list() -> None:
    core, _ = _core_with(700)
    engine = _engine(core)
    first = engine.snapshot(US30_H1)
    core.calls.clear()
    assert engine.snapshot(US30_H1) is first
    assert core.calls == [("lrange", "L_CANDLE_US30_H1", 0, HEAD_WINDOW - 1)]


def test_snapshot_nen_moi_chi_doc_nen_moi_va_giong_het_doc_day_du() -> None:
    bars = make_bars(800)
    core = FakeCore()
    core.fill("US30", "H1", bars.head(700))
    engine = _engine(core)
    engine.snapshot(US30_H1)
    core.calls.clear()
    _push_next(core, bars, 700)
    frame = engine.snapshot(US30_H1)
    assert core.calls == [("lrange", "L_CANDLE_US30_H1", 0, HEAD_WINDOW - 1), ("hmget", f"L_CANDLE_US30_H1:{stamp_of(bars.iloc[700]['bartime'])}")]
    pd.testing.assert_frame_equal(frame, _full(core))


def test_snapshot_nhieu_nen_moi_lien_nhau_doc_dung_cac_nen_do() -> None:
    bars = make_bars(800)
    core = FakeCore()
    core.fill("US30", "H1", bars.head(700))
    engine = _engine(core)
    engine.snapshot(US30_H1)
    core.calls.clear()
    for index in (700, 701, 702):
        _push_next(core, bars, index)
    frame = engine.snapshot(US30_H1)
    assert sum(1 for c in core.calls if c[0] == "hmget") == 3
    pd.testing.assert_frame_equal(frame, _full(core))


def test_snapshot_nhay_qua_cua_so_dau_list_thi_doc_lai_day_du() -> None:
    bars = make_bars(800)
    core = FakeCore()
    core.fill("US30", "H1", bars.head(700))
    engine = _engine(core)
    engine.snapshot(US30_H1)
    core.calls.clear()
    for index in range(700, 700 + HEAD_WINDOW + 4):
        _push_next(core, bars, index)
    frame = engine.snapshot(US30_H1)
    assert sum(1 for c in core.calls if c[0] == "hmget") == 500  # mốc đang giữ không còn nằm trong cửa sổ đầu LIST: đọc lại đầy đủ
    pd.testing.assert_frame_equal(frame, _full(core))


def test_snapshot_cat_dung_snapshot_bars_sau_nhieu_lan_cap_nhat() -> None:
    bars = make_bars(260)
    core = FakeCore()
    core.fill("US30", "H1", bars.head(100))
    engine = _engine(core, bars=50)
    engine.snapshot(US30_H1)
    for index in range(100, 260):
        _push_next(core, bars, index)
        frame = engine.snapshot(US30_H1)
    assert len(frame) == 50
    pd.testing.assert_frame_equal(frame, _full(core, bars=50))
    pd.testing.assert_frame_equal(frame, bars.tail(50).reset_index(drop=True))


def test_snapshot_mot_moc_lpush_hai_lan_o_dau_list_chi_them_mot_nen() -> None:
    bars = make_bars(800)
    core = FakeCore()
    core.fill("US30", "H1", bars.head(700))
    engine = _engine(core)
    engine.snapshot(US30_H1)
    _push_next(core, bars, 700, duplicate=True)
    frame = engine.snapshot(US30_H1)
    pd.testing.assert_frame_equal(frame, bars.head(701).tail(500).reset_index(drop=True))


def test_snapshot_thieu_hash_cua_nen_moi_doc_lai_day_du_roi_nhan_nen_khi_hash_toi(caplog: pytest.LogCaptureFixture) -> None:
    bars = make_bars(800)
    core = FakeCore()
    core.fill("US30", "H1", bars.head(700))
    engine = _engine(core)
    before = engine.snapshot(US30_H1)
    _push_next(core, bars, 700, with_hash=False)  # LIST đi trước HASH
    with caplog.at_level(logging.WARNING, logger="core_engine.src.data_engine"):
        frame = engine.snapshot(US30_H1)
    pd.testing.assert_frame_equal(frame, bars.head(700).tail(499).reset_index(drop=True))  # đúng như đọc đầy đủ cũ: 500 mốc trong LIST nhưng một mốc chưa có HASH nên còn 499 nến
    assert before is not frame
    assert any(r.getMessage().startswith("event=candle_stamp_missing") for r in caplog.records)
    core.hashes[f"L_CANDLE_US30_H1:{stamp_of(bars.iloc[700]['bartime'])}"] = bar_row(bars.iloc[700])  # HASH tới sau
    pd.testing.assert_frame_equal(engine.snapshot(US30_H1), bars.head(701).tail(500).reset_index(drop=True))


def test_snapshot_gia_tri_sai_o_nen_moi_thi_raise_va_khong_lam_hong_bo_dem() -> None:
    bars = make_bars(800)
    core = FakeCore()
    core.fill("US30", "H1", bars.head(700))
    engine = _engine(core)
    good = engine.snapshot(US30_H1)
    core.push("US30", "H1", stamp_of(bars.iloc[700]["bartime"]), {"open": "1", "high": "2", "low": "0", "close": "nan"})
    with pytest.raises(ValueError, match="contains invalid values"):
        engine.snapshot(US30_H1)
    core.lists["L_CANDLE_US30_H1"].pop(0)  # nến lỗi bị Core gỡ đi
    assert engine.snapshot(US30_H1) is good


def test_snapshot_cap_chua_co_nen_tra_none_im_lang(caplog: pytest.LogCaptureFixture) -> None:
    core = FakeCore()
    engine = _engine(core, pairs=(("US30", "H1"), ("XAUUSD", "M10")))
    core.fill("US30", "H1", make_bars(50))
    with caplog.at_level(logging.DEBUG):
        assert engine.snapshot(Pair("XAUUSD", "M10")) is None
    assert caplog.records == []  # coi như không có: không cảnh báo, không log
    assert engine.snapshot(US30_H1) is not None


def test_snapshot_list_bi_xoa_giua_chung_thi_bo_bo_dem_va_tra_none() -> None:
    core, _ = _core_with(100)
    engine = _engine(core)
    engine.snapshot(US30_H1)
    core.lists["L_CANDLE_US30_H1"].clear()
    assert engine.snapshot(US30_H1) is None


# =============================================================================
# 3. Nghe keyspace: đúng cặp, gộp trùng, nến cũ buộc đọc lại.
# =============================================================================


def test_subscribe_dang_ky_dung_tung_cap_va_cho_xac_nhan_du() -> None:
    core = FakeCore()
    engine = _engine(core, pairs=(("US30", "H1"), ("HK50", "M10")))
    assert engine.subscribe() == 2
    assert sorted(core.pubsubs[0].patterns) == [
        "__keyspace@0__:L_CANDLE_HK50_M10:*",
        "__keyspace@0__:L_CANDLE_US30_H1:*",
    ]


def test_subscribe_khong_du_xac_nhan_trong_han_thi_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    core = FakeCore()
    core.confirm_subscribe = False
    monkeypatch.setattr(redis_connector, "SUBSCRIBE_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(redis.exceptions.TimeoutError, match="0/1 patterns"):
        _engine(core).subscribe()


def test_pattern_thoat_ky_tu_glob_cua_ten_symbol() -> None:
    assert data_engine._glob_escape("US[30]*") == "US\\[30\\]\\*"
    assert data_engine._glob_escape("US30") == "US30"


def test_poll_events_chi_nhan_cap_da_dang_ky_hset_va_gop_trung_giu_thu_tu() -> None:
    core = FakeCore()
    engine = _engine(core, pairs=(("US30", "H1"), ("HK50", "M10")))
    engine.subscribe()
    pubsub = core.pubsubs[0]
    core.emit_hset("HK50", "M10", "2026-01-01 10:00:00")
    core.emit_hset("US30", "H1", "2026-01-01 10:00:00")
    core.emit_hset("HK50", "M10", "2026-01-01 10:10:00")  # trùng cặp: gộp
    core.emit_hset("US30", "H1", "2026-01-01 11:00:00", data="del")  # không phải hset: bỏ
    pubsub.queue.append({"type": "pmessage", "pattern": "", "channel": "__keyspace@0__:L_CANDLE_EURUSD_H4:2026-01-01 10:00:00", "data": "hset"})  # cặp ngoài danh sách
    pubsub.queue.append({"type": "pmessage", "pattern": "", "channel": "__keyspace@5__:L_CANDLE_US30_H1:2026-01-01 10:00:00", "data": "hset"})  # db khác
    assert engine.poll_events(0.0) == [Pair("HK50", "M10"), Pair("US30", "H1")]
    assert engine.poll_events(0.0) == []


def test_poll_events_truoc_subscribe_la_loi() -> None:
    with pytest.raises(RuntimeError, match="before subscribe"):
        _engine(FakeCore()).poll_events(0.0)


def test_event_ghi_mot_moc_khong_moi_hon_nen_moi_nhat_thi_buoc_doc_lai_day_du() -> None:
    core, bars = _core_with(700)
    engine = _engine(core)
    engine.subscribe()
    engine.snapshot(US30_H1)
    core.calls.clear()
    core.emit_hset("US30", "H1", stamp_of(bars.iloc[300]["bartime"]))  # ghi bù một nến cũ
    assert engine.poll_events(0.0) == [US30_H1]
    engine.snapshot(US30_H1)
    assert sum(1 for c in core.calls if c[0] == "hmget") == 500


def test_event_cua_nen_moi_hon_thi_van_di_duong_gia_tang() -> None:
    bars = make_bars(800)
    core = FakeCore()
    core.fill("US30", "H1", bars.head(700))
    engine = _engine(core)
    engine.subscribe()
    engine.snapshot(US30_H1)
    _push_next(core, bars, 700)
    core.emit_hset("US30", "H1", stamp_of(bars.iloc[700]["bartime"]))
    assert engine.poll_events(0.0) == [US30_H1]
    core.calls.clear()
    frame = engine.snapshot(US30_H1)
    assert sum(1 for c in core.calls if c[0] == "hmget") == 1  # nến mới hơn nến đang giữ: không phải đọc lại cả cửa sổ
    pd.testing.assert_frame_equal(frame, _full(core))


def test_check_keyspace_flags() -> None:
    core = FakeCore()
    assert _engine(core).check_keyspace_flags() == "hK"
    core.flags = "K"
    with pytest.raises(RuntimeError, match=r"thiếu lớp \['h'\]"):
        _engine(core).check_keyspace_flags()
    core.flags = ""
    with pytest.raises(RuntimeError, match=r"thiếu lớp \['K', 'h'\]"):
        _engine(core).check_keyspace_flags()


# =============================================================================
# 4. Kết nối.
# =============================================================================


def test_close_dong_ca_hai_ket_noi_va_pubsub() -> None:
    reader, events = FakeCore(), FakeCore()
    engine = DataEngine(host="h", port=1, password="", db=0, key_prefix=PREFIX, snapshot_bars=10, pairs=[("US30", "H1")], client=reader, events_client=events)
    engine.subscribe()
    engine.close()
    assert reader.closed and events.closed and events.pubsubs[0].closed


def test_cap_duoc_chuan_hoa_chu_hoa_sap_xep_va_khong_trung() -> None:
    engine = _engine(FakeCore(), pairs=(("us30", "h1"), ("HK50", "M10"), ("US30", "H1")))
    assert engine.pairs == (Pair("HK50", "M10"), Pair("US30", "H1"))


def test_snapshot_bars_phai_duong() -> None:
    with pytest.raises(ValueError, match="snapshot_bars must be positive"):
        _engine(FakeCore(), bars=0)


def test_ket_noi_db0_dung_tham_so_va_hai_ket_noi_rieng_cho_doc_va_nghe_event(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict] = []
    monkeypatch.setattr(redis_connector.redis, "Redis", lambda **kwargs: created.append(kwargs) or FakeCore())
    engine = DataEngine(
        host="10.0.0.5", port="6379", password="mat-khau-nguon", db="0", key_prefix="L_CANDLE", snapshot_bars=500, pairs=[("US30", "H1")],
        socket_timeout=4, connect_timeout=3, healthcheck_interval=20,
    )
    assert len(created) == 1  # kết nối nghe event chỉ tạo khi cần
    engine.ping()
    expected = {
        "host": "10.0.0.5", "port": 6379, "db": 0, "password": "mat-khau-nguon", "decode_responses": True,
        "socket_timeout": 4.0, "socket_connect_timeout": 3.0, "health_check_interval": 20,
    }
    assert len(created) == 2 and created == [expected, expected]  # hai kết nối riêng: đọc nến và nghe event
    created.clear()
    DataEngine(host="h", port=6379, password="", db=0, key_prefix="L_CANDLE", snapshot_bars=10, pairs=[("US30", "H1")])
    assert created[0]["password"] is None  # mật khẩu rỗng = không xác thực


# =============================================================================
# 7. Chế độ cấu phần: publisher đọc db0 của DE (LIST mới nhất ĐỨNG CUỐI, HASH đọc bằng HGETALL, khoá không tiền tố).
# =============================================================================


class _Spy(RecordingRedis):
    """Redis đầu ra giả ghi lại cả lệnh ĐỌC (lrange, hgetall), để kiểm đường gia tăng chỉ hỏi cái cần hỏi."""

    def __init__(self) -> None:
        super().__init__(emulate=True)
        self.reads: list[tuple] = []

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        self.reads.append(("lrange", key, start, end))
        return super().lrange(key, start, end)

    def hgetall(self, key: str) -> dict[str, str]:
        self.reads.append(("hgetall", key))
        return super().hgetall(key)


def _table(n: int, start: str = "2026-11-03 00:00", **columns: list) -> pd.DataFrame:
    bartime = pd.date_range(start, periods=n, freq="h").astype("datetime64[us]")
    return pd.DataFrame({"bartime": bartime, "close_time": bartime + pd.Timedelta(hours=1), **columns})


def _write(client: RecordingRedis, role: str, table: pd.DataFrame, symbol: str = "HK50", timeframe: str = "H1", prefix: str = "") -> None:
    strategy_runner.ComponentWriter(host="x", port=1, password="", db=0, key_prefix=prefix, retention_max_entries=1000, client=client).write(symbol, role, timeframe, table)


def _component_engine(client: RecordingRedis, pairs=(("HK50", "H1", "CONTEXT"),), bars=500, prefix: str = "") -> DataEngine:
    return DataEngine(
        host="127.0.0.1", port=6380, password="", db=0, key_prefix=prefix, snapshot_bars=bars, pairs=pairs, components=True, client=client, events_client=FakeCore(),
    )


HK_CONTEXT = Pair("HK50", "H1", "CONTEXT")


def test_ten_key_cau_phan_va_phan_tich_nguoc() -> None:
    assert pair_list_key("", "hk50", "h1", "trigger") == "HK50_TRIGGER_H1" and pair_list_key("DE", "hk50", "h1", "trigger") == "DE_HK50_TRIGGER_H1"
    assert pair_list_key(PREFIX, "us30", "m5") == "L_CANDLE_US30_M5"  # nến: không đổi
    assert parse_pair_key("HK50_TRIGGER_H1", "", components=True) == Pair("HK50", "H1", "TRIGGER")
    assert parse_pair_key("DE_hk50_trend_h3", "DE", components=True) == Pair("HK50", "H3", "TREND")
    for bad in ("HK50_TRIGGER_H1:2026-11-03 00:00:00", "HK50_FOO_H1", "HK50_TRIGGER", "HK50_TRIGGER_H1_X", "_TRIGGER_H1", "HK50__H1", "OTHER_HK50_TRIGGER_H1"):
        assert parse_pair_key(bad, "DE", components=True) is None, bad
    assert parse_pair_key("HK50_TRIGGER_H1", "DE", components=True) is None  # thiếu tiền tố đã cấu hình


def test_read_components_tra_dung_n_moc_moi_nhat_tang_dan_dung_dtype_va_doc_dung_tung_bit() -> None:
    client = _Spy()
    values = [1 / 3, 6.949058123456789, 0.1, math.nan, -2.5, 1e-9, 1234567.891011, 2 / 7]
    _write(client, "CONTEXT", _table(8, macd_h=values, atr=[v * 3 for v in values]))
    frame = read_components(client, symbol="HK50", timeframe="H1", role="CONTEXT", key_prefix="", snapshot_bars=6)
    assert list(frame.columns) == ["bartime", "close_time", "macd_h", "atr"] and len(frame) == 6
    assert frame["bartime"].tolist() == pd.date_range("2026-11-03 02:00", periods=6, freq="h").tolist() and frame["bartime"].is_monotonic_increasing
    assert frame["bartime"].dtype == frame["close_time"].dtype == "datetime64[us]" and frame["macd_h"].dtype == "float64"
    assert (frame["close_time"] - frame["bartime"] == pd.Timedelta(hours=1)).all()
    expected = values[2:]
    assert [v for v in frame["macd_h"] if not math.isnan(v)] == [v for v in expected if not math.isnan(v)]  # đúng từng bit (to_numeric của pandas lệch 1 ulp ở ~35% giá trị)
    assert math.isnan(frame["macd_h"].iloc[1]) and frame["atr"].tolist()[0] == expected[0] * 3


def test_read_components_giu_cot_chu_va_xu_ly_list_chua_co() -> None:
    client = _Spy()
    _write(client, "TREND", _table(4, method="knn", trend_bias=[1, -1, 0, 1]), timeframe="H3")
    frame = read_components(client, symbol="HK50", timeframe="H3", role="TREND", key_prefix="", snapshot_bars=10)
    assert frame["method"].tolist() == ["knn"] * 4 and frame["trend_bias"].tolist() == [1.0, -1.0, 0.0, 1.0]
    empty = read_components(client, symbol="HK50", timeframe="H1", role="TREND", key_prefix="", snapshot_bars=10)
    assert empty.empty and list(empty.columns) == ["bartime", "close_time"] and empty["bartime"].dtype == "datetime64[us]"


def test_read_components_bo_moc_het_han_hash_va_canh_bao(caplog: pytest.LogCaptureFixture) -> None:
    client = _Spy()
    _write(client, "CONTEXT", _table(5, atr=[1.0] * 5))
    del client.hashes["HK50_CONTEXT_H1:2026-11-03 02:00:00"]  # HASH đã hết hạn nhưng mốc còn trong LIST
    with caplog.at_level(logging.WARNING, logger="core_engine.src"):
        frame = read_components(client, symbol="HK50", timeframe="H1", role="CONTEXT", key_prefix="", snapshot_bars=10)
    assert len(frame) == 4 and pd.Timestamp("2026-11-03 02:00") not in frame["bartime"].tolist()
    assert [r.getMessage() for r in caplog.records] == ["event=component_stamp_missing symbol=HK50 timeframe=H1 role=CONTEXT count=1 sample=['2026-11-03 02:00:00']"]


def test_read_components_dong_hong_bi_bo_va_ghi_log_loi_chu_khong_chan_ca_cua_so(caplog: pytest.LogCaptureFixture) -> None:
    client = _Spy()
    _write(client, "TRIGGER", _table(4, open=[1.0] * 4, high=[2.0] * 4, low=[0.5] * 4, close=[1.5] * 4))
    client.hashes["HK50_TRIGGER_H1:2026-11-03 01:00:00"]["close"] = "nan"  # giá không hữu hạn: TRIGGER hỏng
    client.hashes["HK50_TRIGGER_H1:2026-11-03 02:00:00"]["close_time"] = "hỏng"  # giờ đóng hỏng
    with caplog.at_level(logging.ERROR, logger="core_engine.src"):
        frame = read_components(client, symbol="HK50", timeframe="H1", role="TRIGGER", key_prefix="", snapshot_bars=10)
    assert frame["bartime"].tolist() == [pd.Timestamp("2026-11-03 00:00"), pd.Timestamp("2026-11-03 03:00")]
    assert len(caplog.records) == 1 and caplog.records[0].getMessage().startswith("event=component_rows_invalid symbol=HK50 timeframe=H1 role=TRIGGER invalid_rows=2 total_rows=4")
    _write(client, "SETUP", _table(3, setup=[1.0] * 3, open=[1.0] * 3, high=[2.0] * 3, low=[0.5] * 3, close=[1.5] * 3, atr=[1.0] * 3))
    client.hashes["HK50_SETUP_H1:2026-11-03 01:00:00"]["high"] = "inf"  # SETUP cũng mang giá nến: giá không hữu hạn thì dòng bị bỏ như TRIGGER
    assert read_components(client, symbol="HK50", timeframe="H1", role="SETUP", key_prefix="", snapshot_bars=10)["bartime"].tolist() == [pd.Timestamp("2026-11-03 00:00"), pd.Timestamp("2026-11-03 02:00")]
    _write(client, "CONTEXT", _table(2, atr=[1.0, 2.0]))
    client.hashes["HK50_CONTEXT_H1:2026-11-03 00:00:00"]["atr"] = "không phải số"  # CONTEXT: số hỏng thành NaN, mốc vẫn còn
    context = read_components(client, symbol="HK50", timeframe="H1", role="CONTEXT", key_prefix="", snapshot_bars=10)
    assert len(context) == 2 and math.isnan(context["atr"].iloc[0]) and context["atr"].iloc[1] == 2.0


def test_snapshot_cau_phan_lan_dau_doc_day_du_roi_chi_hoi_cua_so_cuoi_list() -> None:
    client = _Spy()
    _write(client, "CONTEXT", _table(20, atr=[float(i) for i in range(20)]))
    engine = _component_engine(client)
    first = engine.snapshot(HK_CONTEXT)
    assert len(first) == 20 and sum(1 for r in client.reads if r[0] == "hgetall") == 20
    client.reads.clear()
    assert engine.snapshot(HK_CONTEXT) is first  # không có mốc mới: cùng đối tượng
    assert client.reads == [("lrange", "HK50_CONTEXT_H1", -HEAD_WINDOW, -1)]  # LIST mới nhất đứng CUỐI: hỏi cửa sổ cuối


def test_snapshot_cau_phan_mot_moi_chi_doc_mot_moi_va_giong_het_doc_day_du() -> None:
    client = _Spy()
    table = _table(25, atr=[float(i) for i in range(25)])
    _write(client, "CONTEXT", table.head(20))
    engine = _component_engine(client)
    engine.snapshot(HK_CONTEXT)
    client.reads.clear()
    _write(client, "CONTEXT", table.head(22))  # hai mốc mới
    frame = engine.snapshot(HK_CONTEXT)
    assert client.reads == [("lrange", "HK50_CONTEXT_H1", -HEAD_WINDOW, -1), ("hgetall", "HK50_CONTEXT_H1:2026-11-03 21:00:00"), ("hgetall", "HK50_CONTEXT_H1:2026-11-03 20:00:00")]
    pd.testing.assert_frame_equal(frame, read_components(client, symbol="HK50", timeframe="H1", role="CONTEXT", key_prefix="", snapshot_bars=500))


def test_snapshot_cau_phan_cat_dung_so_moc_cua_tung_cap_va_nhay_xa_thi_doc_lai() -> None:
    client = _Spy()
    _write(client, "CONTEXT", _table(30, atr=[1.0] * 30))
    _write(client, "TREND", _table(30, method="knn", trend_bias=[1] * 30))
    trend = Pair("HK50", "H1", "TREND")
    engine = _component_engine(client, pairs=(HK_CONTEXT, trend), bars={HK_CONTEXT: 10, trend: 12})  # mỗi cặp một số mốc
    assert len(engine.snapshot(HK_CONTEXT)) == 10 and len(engine.snapshot(trend)) == 12
    _write(client, "CONTEXT", _table(35, atr=[1.0] * 35))
    assert len(engine.snapshot(HK_CONTEXT)) == 10  # sau cập nhật gia tăng vẫn cắt đúng 10
    _write(client, "CONTEXT", _table(80, atr=[1.0] * 80))  # 45 mốc mới, vượt cửa sổ cuối 16: đọc lại đầy đủ
    client.reads.clear()
    frame = engine.snapshot(HK_CONTEXT)
    assert len(frame) == 10 and frame["bartime"].iloc[-1] == pd.Timestamp("2026-11-03 00:00") + pd.Timedelta(hours=79) and sum(1 for r in client.reads if r[0] == "hgetall") == 10
    with pytest.raises(ValueError, match="snapshot_bars must be positive"):
        _component_engine(client, bars={HK_CONTEXT: 0})


def test_snapshot_cau_phan_chua_co_gi_tra_none_im_lang(caplog: pytest.LogCaptureFixture) -> None:
    engine = _component_engine(_Spy())
    with caplog.at_level(logging.INFO, logger="core_engine.src"):
        assert engine.snapshot(HK_CONTEXT) is None
    assert caplog.records == []


def test_nghe_cau_phan_dang_ky_dung_mau_va_chi_nhan_cap_da_dang_ky_o_dung_db() -> None:
    client = _Spy()
    events = FakeCore()
    engine = DataEngine(
        host="x", port=1, password="", db=0, key_prefix="", snapshot_bars=500, pairs=[("hk50", "h1", "trigger"), ("HK50", "H2", "CONTEXT")], components=True, client=client, events_client=events,
    )
    assert engine.subscribe() == 2
    assert sorted(events.pubsubs[0].patterns) == ["__keyspace@0__:*_CONTEXT_*:*", "__keyspace@0__:*_TRIGGER_*:*"]  # chế độ cấu phần: mỗi (db, vai trò) một mẫu, không mỗi cặp một mẫu
    queue = events.pubsubs[0].queue
    for channel, data in (
        ("__keyspace@0__:HK50_TRIGGER_H1:2026-11-03 00:00:00", "hset"),
        ("__keyspace@0__:HK50_TRIGGER_H1:2026-11-03 00:00:00", "expire"),  # không phải hset: bỏ
        ("__keyspace@0__:HK50_CONTEXT_H1:2026-11-03 00:00:00", "hset"),  # cặp chưa đăng ký
        ("__keyspace@3__:HK50_CONTEXT_H2:2026-11-03 00:00:00", "hset"),  # db khác
        ("__keyspace@0__:HK50_CONTEXT_H2:2026-11-03 00:00:00", "hset"),
    ):
        queue.append({"type": "pmessage", "pattern": "", "channel": channel, "data": data})
    assert engine.poll_events(0.0) == [Pair("HK50", "H1", "TRIGGER"), Pair("HK50", "H2", "CONTEXT")]


def test_event_cau_phan_ghi_bu_mot_moc_cu_buoc_doc_lai_day_du() -> None:
    client = _Spy()
    _write(client, "CONTEXT", _table(20, atr=[1.0] * 20))
    events = FakeCore()
    engine = DataEngine(host="x", port=1, password="", db=0, key_prefix="", snapshot_bars=500, pairs=[("HK50", "H1", "CONTEXT")], components=True, client=client, events_client=events)
    engine.subscribe()
    engine.snapshot(HK_CONTEXT)
    events.pubsubs[0].queue.append({"type": "pmessage", "pattern": "", "channel": "__keyspace@0__:HK50_CONTEXT_H1:2026-11-03 05:00:00", "data": "hset"})  # ghi lại một mốc cũ
    assert engine.poll_events(0.0) == [HK_CONTEXT]
    client.reads.clear()
    engine.snapshot(HK_CONTEXT)
    assert sum(1 for r in client.reads if r[0] == "hgetall") == 20


# =============================================================================
# 8. Khoá có định danh (chiến lược / phương pháp trend) và engine đọc HAI db (TRIGGER/CONTEXT ở db0, TREND ở db1).
# =============================================================================


def test_khoa_cau_phan_co_dinh_danh_giua_vai_tro_va_khung_va_phan_tich_nguoc() -> None:
    assert pair_list_key("", "hk50", "h1", "setup", "combo") == "HK50_SETUP_COMBO_H1"
    assert data_engine.parse_pair_key("HK50_SETUP_COMBO_H1", "", components=True) == Pair("HK50", "H1", "SETUP", "COMBO")
    assert data_engine.COMPONENT_ROLES == ("TRIGGER", "SETUP", "CONTEXT", "TREND")
    assert pair_list_key("", "hk50", "h1", "trigger", "combo") == "HK50_TRIGGER_COMBO_H1"
    assert pair_list_key("", "HK50", "H2", "CONTEXT") == "HK50_CONTEXT_H2"  # CONTEXT không có định danh: chứa mọi chỉ báo
    assert pair_list_key("DE", "US30", "H4", "TREND", "knn") == "DE_US30_TREND_KNN_H4"
    assert data_engine.parse_pair_key("HK50_TRIGGER_COMBO_H1", "", components=True) == Pair("HK50", "H1", "TRIGGER", "COMBO")
    assert data_engine.parse_pair_key("HK50_CONTEXT_H2", "", components=True) == Pair("HK50", "H2", "CONTEXT", "")
    assert data_engine.parse_pair_key("DE_US30_TREND_KNN_H4", "DE", components=True) == Pair("US30", "H4", "TREND", "KNN")
    for bad in ("HK50_TREND_KNN_H4:2026-11-03 00:00:00", "HK50_FOO_COMBO_H1", "HK50_TRIGGER_A_B_H1", "HK50_H1", "TRIGGER_COMBO_H1"):
        assert data_engine.parse_pair_key(bad, "", components=True) is None, bad


def test_engine_hai_db_doc_trend_o_db1_va_cau_phan_con_lai_o_db0_va_nghe_ca_hai() -> None:
    db0, db1, events = _Spy(), _Spy(), FakeCore()
    _write(db0, "CONTEXT", _table(30, macd_h=[0.5] * 30, ma=[100.0] * 30))
    strategy_runner.ComponentWriter(host="x", port=1, password="", db=1, key_prefix="", retention_max_entries=1000, client=db1).write(
        "HK50", "TREND", "H4", _table(30, trend_bias=[1] * 30), qualifier="KNN"
    )
    trend = Pair("HK50", "H4", "TREND", "KNN")
    engine = DataEngine(
        host="x", port=6380, password="", db=0, key_prefix="", snapshot_bars=500, pairs=[HK_CONTEXT, trend], components=True, role_db={"TREND": 1},
        client=db0, clients={1: db1}, events_client=events,
    )
    assert engine.subscribe() == 2
    assert sorted(events.pubsubs[0].patterns) == ["__keyspace@0__:*_CONTEXT_*:*", "__keyspace@1__:*_TREND_*:*"]  # một mẫu mỗi (db, vai trò)
    assert len(engine.snapshot(trend)) == 30 and len(engine.snapshot(HK_CONTEXT)) == 30
    assert {r[1].split(":")[0] for r in db1.reads} == {"HK50_TREND_KNN_H4"}  # TREND chỉ được hỏi ở db1
    assert all("TREND" not in r[1] for r in db0.reads)  # db0 không bị hỏi về TREND
    queue = events.pubsubs[0].queue
    for channel in (
        "__keyspace@1__:HK50_TREND_KNN_H4:2026-11-04 06:00:00",  # đúng db
        "__keyspace@0__:HK50_TREND_KNN_H4:2026-11-04 06:00:00",  # TREND không nằm ở db0: bỏ
        "__keyspace@1__:HK50_CONTEXT_H1:2026-11-04 06:00:00",  # CONTEXT không nằm ở db1: bỏ
        "__keyspace@0__:HK50_CONTEXT_H1:2026-11-04 06:00:00",
        "__keyspace@1__:HK50_TREND_ZIGZAG_H4:2026-11-04 06:00:00",  # khối chưa đăng ký: bỏ
    ):
        queue.append({"type": "pmessage", "pattern": "", "channel": channel, "data": "hset"})
    assert engine.poll_events(0.0) == [trend, HK_CONTEXT]
    engine.ping()
    engine.close()
