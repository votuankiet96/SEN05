"""Test data_engine.py (cửa vào duy nhất từ db0): đọc nến, bộ đệm gia tăng, nghe keyspace theo cặp — hermetic, Redis db0 giả (engine_support.FakeCore).

Nguyên tắc kiểm: bảng nến mà bộ đệm trả về phải GIỐNG HỆT đọc đầy đủ ở mọi bước (``assert_frame_equal``, kể cả dtype), và các lệnh Redis đã gọi cho thấy đúng đường nào được dùng
(gia tăng chỉ hỏi cửa sổ đầu LIST và HASH của nến mới; bất thường thì đọc đầy đủ).
"""

from __future__ import annotations

import logging

import pandas as pd
import pytest
import redis
from engine_support import PREFIX, FakeCore, bar_row, make_bars, stamp_of

from core_engine.src import data_engine, redis_connector
from core_engine.src.data_engine import (
    HEAD_WINDOW,
    DataEngine,
    Pair,
    parse_pair_key,
    read_candles,
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
