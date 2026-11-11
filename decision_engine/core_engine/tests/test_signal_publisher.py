"""Test signal_publisher.py (phía xuất bản tín hiệu): lắp bảng của một ô từ cấu phần db0 theo luật nến đóng (assemble_cell_table, entry_complete, reference_ready), tên key/kênh, nội dung HASH theo hợp đồng OE,
cửa sổ hiệu lực, và ghi tín hiệu theo List–Hash (RawSignalWriter) — hermetic, không Redis thật.

Giá trị mong đợi của phần ghi là bản ghi tín hiệu THẬT của đường xử lý (ma_cross HK50 H1 nến mở 2026-10-16 16:00, SELL).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np
import pandas as pd
import pytest
from engine_support import RecordingRedis, decode_eval

from core_engine.src import redis_connector
from core_engine.src.signal_publisher import (
    Outcome,
    RawSignalWriter,
    assemble_cell_table,
    build_signal,
    channel_name,
    check_order_type,
    de_list_key,
    entry_complete,
    reference_ready,
    signal_window,
    strategy_token,
    timeframe_duration,
    to_hash_fields,
)

BARTIME = pd.Timestamp("2026-10-16 16:00:00")
VALID_FROM = pd.Timestamp("2026-10-16 17:00:00", tz="UTC")
VALID_UNTIL = pd.Timestamp("2026-10-16 17:10:00", tz="UTC")
RULE = {"mode": "seconds_after_bar_close", "seconds": 600}
ROW = pd.Series(
    {
        "bartime": BARTIME, "signal": -1, "raw_signal": -1, "entry_price": 24137.7, "atr": 66.475406,
        "signal_reason": "fast SMA below slow SMA; MACD histogram < 0",
    }
)
EXPECTED_FIELDS = {
    "symbol": "HK50", "timeframe": "H1", "strategy": "ma_cross", "bartime": "2026-10-16T16:00:00Z", "tradeSide": "SELL", "orderType": "MARKET",
    "expirationTimestamp": "1792170600", "clientOrderId": "ma_cross:H1:HK50:20261016T160000Z",
    "comment": "fast SMA below slow SMA; MACD histogram < 0", "atr": "66.48", "valid_from": "2026-10-16T17:00:00Z",
}


def _latest(**changes: Any) -> pd.Series:
    row = ROW.copy()
    for key, value in changes.items():
        row[key] = value
    return row


# =============================================================================
# 1. Tên, nội dung.
# =============================================================================


def test_ten_key_va_kenh_theo_hop_dong_voi_oe() -> None:
    assert strategy_token("ma_cross") == "MACROSS" and strategy_token("macd_break") == "MACDBREAK" and strategy_token("ema_reach") == "EMAREACH"
    assert de_list_key("L_DE", "ma_cross", "hk50", "h1") == "L_DE_MACROSS_HK50_H1"
    assert channel_name("L_CHANNEL", "combo", "US30", "H4") == "L_CHANNEL_COMBO_US30_H4"


def test_build_signal_va_to_hash_fields_cho_lenh_market() -> None:
    signal_id, stamp, payload = build_signal(strategy="ma_cross", timeframe="h1", symbol="hk50", row=ROW, valid_from=VALID_FROM, valid_until=VALID_UNTIL)
    assert signal_id == "ma_cross:H1:HK50:20261016T160000Z" and stamp == "2026-10-16 16:00:00"
    assert payload["side"] == "SELL" and payload["entry"] == 24137.7 and payload["atr"] == 66.48
    fields = to_hash_fields(payload, order_type="MARKET")
    assert fields == EXPECTED_FIELDS and "stopPrice" not in fields  # lệnh MARKET không mang giá entry


def test_build_signal_lam_tron_entry_va_atr_theo_so_le_cua_symbol() -> None:
    row = _latest(entry_price=1.082346, atr=0.000412)
    _, _, default = build_signal(strategy="ma_cross", timeframe="M10", symbol="EURUSD", row=row, valid_from=VALID_FROM, valid_until=VALID_UNTIL)
    _, _, forex = build_signal(strategy="ma_cross", timeframe="M10", symbol="EURUSD", row=row, valid_from=VALID_FROM, valid_until=VALID_UNTIL, decimals=5)
    assert (default["entry"], default["atr"]) == (1.08, 0.0)  # mặc định 2 số lẻ: ATR của forex bị xoá về 0
    assert (forex["entry"], forex["atr"]) == (1.08235, 0.00041)


def test_to_hash_fields_lenh_stop_co_stop_price_bang_entry() -> None:
    _, _, payload = build_signal(strategy="combo", timeframe="H4", symbol="US30", row=_latest(signal=1, entry_price=42000.5), valid_from=VALID_FROM, valid_until=VALID_UNTIL)
    fields = to_hash_fields(payload, order_type="STOP")
    assert fields["orderType"] == "STOP" and fields["stopPrice"] == "42000.5" and fields["tradeSide"] == "BUY"


def test_loai_lenh_la_la_loi() -> None:
    assert check_order_type("STOP") == "STOP" and check_order_type("MARKET") == "MARKET"
    with pytest.raises(ValueError, match="Unsupported orderType 'LIMIT'"):
        check_order_type("LIMIT")
    _, _, payload = build_signal(strategy="ma_cross", timeframe="H1", symbol="HK50", row=ROW, valid_from=VALID_FROM, valid_until=VALID_UNTIL)
    with pytest.raises(ValueError, match="Unsupported orderType"):
        to_hash_fields(payload, order_type="LIMIT")


def test_build_signal_chi_nhan_dong_mua_ban_va_so_huu_han() -> None:
    with pytest.raises(ValueError, match="Only BUY/SELL"):
        build_signal(strategy="ma_cross", timeframe="H1", symbol="HK50", row=_latest(signal=0), valid_from=VALID_FROM, valid_until=VALID_UNTIL)
    with pytest.raises(ValueError, match="'entry_price' must be a finite number"):
        build_signal(strategy="ma_cross", timeframe="H1", symbol="HK50", row=_latest(entry_price=float("nan")), valid_from=VALID_FROM, valid_until=VALID_UNTIL)
    with pytest.raises(ValueError, match="'atr' must be a finite number"):
        build_signal(strategy="ma_cross", timeframe="H1", symbol="HK50", row=_latest(atr=float("inf")), valid_from=VALID_FROM, valid_until=VALID_UNTIL)


# =============================================================================
# 2. Hạn hiệu lực.
# =============================================================================


def test_timeframe_duration() -> None:
    assert timeframe_duration("M45") == pd.Timedelta(minutes=45) and timeframe_duration("h4") == pd.Timedelta(hours=4)
    assert timeframe_duration("D1") == pd.Timedelta(days=1) and timeframe_duration("W") == pd.Timedelta(weeks=1)
    for bad in ("X5", "M0", "M-1", ""):
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            timeframe_duration(bad)


def test_signal_window_theo_hai_che_do() -> None:
    assert signal_window("ma_cross", "H1", BARTIME, RULE) == (VALID_FROM, VALID_UNTIL)  # đóng lúc 17:00, hạn 600 giây sau
    next_bar = {"mode": "next_bar", "valid_bars": 3}
    assert signal_window("combo", "M10", pd.Timestamp("2026-10-16 10:20:00"), next_bar) == (
        pd.Timestamp("2026-10-16 10:30:00", tz="UTC"), pd.Timestamp("2026-10-16 11:00:00", tz="UTC"),  # 3 nến M10 = 30 phút
    )
    with pytest.raises(ValueError, match="Unsupported validity mode for 'x': weekly"):
        signal_window("x", "H1", BARTIME, {"mode": "weekly"})


# =============================================================================
# 3. Lắp bảng từ cấu phần db0: luật "nến tham chiếu đóng không muộn hơn giờ đóng nến vào lệnh", chờ khi nến tham chiếu đóng đúng lúc chưa về.
# =============================================================================


def _closes(*stamps: str) -> pd.DataFrame:
    return pd.DataFrame({"close_time": pd.to_datetime(list(stamps)).astype("datetime64[us]")})


M5, M10 = pd.Timedelta(minutes=5), pd.Timedelta(minutes=10)


def test_reference_ready_bang_t2_t5_t10_cua_chu_he() -> None:
    """Ví dụ của chủ hệ (M1 vào lệnh, MACD M5, trend M10; t = phút, nhãn = giờ ĐÓNG): t2 không chờ gì, t5 chờ MACD t5 còn trend vẫn dùng t0, t10 chờ cả hai."""
    t = pd.Timestamp
    macd = _closes("2026-11-09 00:00")  # M5: mới có nến đóng t0
    trend = _closes("2026-11-09 00:00")  # M10: mới có nến đóng t0
    assert reference_ready(macd, t("2026-11-09 00:02"), M5) and reference_ready(trend, t("2026-11-09 00:02"), M10)  # t2: dùng nến t0 của cả hai
    assert not reference_ready(macd, t("2026-11-09 00:05"), M5)  # t5: nến M5 đóng đúng lúc chưa về -> chờ
    assert reference_ready(trend, t("2026-11-09 00:05"), M10)  # t5: trend M10 chưa tới mốc đóng kế -> vẫn dùng t0
    macd = _closes("2026-11-09 00:00", "2026-11-09 00:05")
    assert reference_ready(macd, t("2026-11-09 00:05"), M5)  # nến M5 t5 đã về
    assert not reference_ready(macd, t("2026-11-09 00:10"), M5) and not reference_ready(trend, t("2026-11-09 00:10"), M10)  # t10: chờ cả hai
    trend = _closes("2026-11-09 00:00", "2026-11-09 00:10")
    assert reference_ready(trend, t("2026-11-09 00:10"), M10) and not reference_ready(macd, t("2026-11-09 00:10"), M5)  # trend t10 về rồi, MACD t10 vẫn chưa
    macd = _closes("2026-11-09 00:00", "2026-11-09 00:05", "2026-11-09 00:10")
    assert reference_ready(macd, t("2026-11-09 00:10"), M5) and reference_ready(trend, t("2026-11-09 00:10"), M10)


def test_reference_ready_cac_truong_hop_bien() -> None:
    t = pd.Timestamp
    assert not reference_ready(_closes(), t("2026-11-09 00:05"), M5)  # chưa có nến tham chiếu nào
    assert reference_ready(_closes("2026-11-09 00:00", "2026-11-09 00:15"), t("2026-11-09 00:10"), M5)  # tham chiếu đã đi trước nến vào lệnh, không có nến đóng đúng 00:10: dùng nến đã đóng gần nhất
    stale = _closes("2026-11-04 21:00")  # sau cuối tuần: nến tham chiếu cuối cách 5 ngày
    assert not reference_ready(stale, t("2026-11-09 06:00"), pd.Timedelta(hours=1))  # 06:00 rơi đúng lưới giờ của nến tham chiếu: nến đóng 06:00 phải về
    assert reference_ready(stale, t("2026-11-09 06:20"), pd.Timedelta(hours=1))  # không rơi đúng lưới: dùng nến cũ nhất đã đóng (nghỉ phiên không chặn)
    assert reference_ready(_closes("2026-11-09 06:00"), t("2026-11-09 06:00"), pd.Timedelta(hours=1))  # nến đóng đúng lúc đã về


def _trigger_table(n: int = 6, start: str = "2026-11-09 00:00", freq: str = "h") -> pd.DataFrame:
    bartime = pd.date_range(start, periods=n, freq=freq).astype("datetime64[us]")
    step = pd.Timedelta(freq if freq != "h" else "1h")
    return pd.DataFrame(
        {
            "bartime": bartime, "close_time": (bartime + step).astype("datetime64[us]"),
            "open": np.arange(n) + 100.0, "high": np.arange(n) + 102.0, "low": np.arange(n) + 99.0, "close": np.arange(n) + 101.0,
        }
    )


def _reference(start: str, n: int, hours: int, **columns: list[float]) -> pd.DataFrame:
    bartime = pd.date_range(start, periods=n, freq=f"{hours}h").astype("datetime64[us]")
    return pd.DataFrame({"bartime": bartime, "close_time": (bartime + pd.Timedelta(hours=hours)).astype("datetime64[us]"), **columns})


def test_entry_complete_can_context_cua_nen_vao_lenh_moi_nhat() -> None:
    setup = _trigger_table(3)
    context = pd.DataFrame({"bartime": setup["bartime"].iloc[:2], "close_time": setup["close_time"].iloc[:2], "atr": [1.0, 2.0]})
    assert not entry_complete(setup, context)  # CONTEXT mới tới nến thứ 2, SETUP đã có nến thứ 3
    full = pd.DataFrame({"bartime": setup["bartime"], "close_time": setup["close_time"], "atr": [1.0, 2.0, 3.0]})
    assert entry_complete(setup, full)
    assert not entry_complete(setup.iloc[0:0], full) and not entry_complete(setup, full.iloc[0:0])


def test_assemble_cell_table_cung_khung_noi_context_theo_bartime_va_mang_hai_cot_context() -> None:
    trigger = _trigger_table(4)
    context = pd.DataFrame({"bartime": trigger["bartime"], "close_time": trigger["close_time"], "macd_h": [1.0, 2.0, 3.0, 4.0], "ma": [5.0, 6.0, 7.0, 8.0]})
    table = assemble_cell_table(trigger, None, context, same_frame=True)
    assert table["context_macd_h"].tolist() == [1.0, 2.0, 3.0, 4.0] and table["context_ma"].tolist() == [5.0, 6.0, 7.0, 8.0]
    assert {"macd_h", "ma", "context_close_time"}.isdisjoint(table.columns) and list(table["close"]) == list(trigger["close"])  # cột SETUP giữ nguyên, không đè lên nhau
    assert assemble_cell_table(trigger, None, context.iloc[:3], same_frame=True)["context_ma"].isna().tolist() == [False, False, False, True]  # thiếu CONTEXT của nến cuối: NaN (entry_complete chặn trước khi quyết định)


def test_assemble_cell_table_khung_context_lon_hon_ghep_theo_nen_dong_khong_muon_hon_gio_dong_nen_trigger() -> None:
    trigger = _trigger_table(6)  # H1: nến mở 00..05, đóng 01..06 giờ
    context = _reference("2026-11-09 00:00", 3, 2, macd_h=[10.0, 20.0, 30.0], ma=[1.0, 2.0, 3.0])  # H2: đóng 02, 04, 06 giờ
    table = assemble_cell_table(trigger, None, context, same_frame=False)
    assert table["bartime"].is_monotonic_increasing and list(table["close"]) == list(trigger["close"])
    # nến trigger đóng 01: H2 chưa có nến đóng nào; 02: H2 đóng ĐÚNG 02 (10); 03: vẫn 10; 04: 20 (đúng lúc); 05: 20; 06: 30 (đúng lúc)
    assert np.isnan(table["context_macd_h"].iloc[0]) and table["context_macd_h"].iloc[1:].tolist() == [10.0, 10.0, 20.0, 20.0, 30.0]
    assert table["context_ma"].iloc[1:].tolist() == [1.0, 1.0, 2.0, 2.0, 3.0] and table["context_close_time"].iloc[2] == pd.Timestamp("2026-11-09 02:00")


def test_assemble_cell_table_context_rong_cho_cot_nan() -> None:
    trigger = _trigger_table(3)
    empty = pd.DataFrame({"bartime": pd.Series(dtype="datetime64[us]"), "close_time": pd.Series(dtype="datetime64[us]"), "macd_h": pd.Series(dtype="float64"), "ma": pd.Series(dtype="float64")})
    table = assemble_cell_table(trigger, None, empty, same_frame=False)
    assert table["context_macd_h"].isna().all() and table["context_ma"].isna().all()


def _events(setup: pd.DataFrame, **rows: tuple[float, str]) -> pd.DataFrame:
    """Bảng TRIGGER (chỉ sự kiện) cho các nến của ``setup`` theo vị trí: ``n=(signal, reason)``."""
    picked = [(int(name[1:]), value) for name, value in rows.items()]
    return pd.DataFrame(
        {
            "bartime": [setup["bartime"].iloc[i] for i, _ in picked], "close_time": [setup["close_time"].iloc[i] for i, _ in picked],
            "signal": [signal for _, (signal, _) in picked], "reason": [reason for _, (_, reason) in picked],
        }
    )


def test_assemble_cell_table_tin_hieu_va_ly_do_lay_tu_trigger_nen_khong_co_dong_thi_signal_0_ly_do_rong() -> None:
    """SETUP có dòng cho MỌI nến, TRIGGER chỉ nến có tín hiệu: bảng ghép phải có signal/reason đúng tại nến sự kiện, 0 và rỗng ở mọi nến khác, và bỏ sự kiện nằm ngoài các nến của SETUP."""
    setup = _trigger_table(5)
    context = pd.DataFrame({"bartime": setup["bartime"], "close_time": setup["close_time"], "macd_h": [0.0] * 5, "ma": [1.0] * 5})
    outside = pd.DataFrame({"bartime": [pd.Timestamp("2025-01-01")], "close_time": [pd.Timestamp("2025-01-01 01:00")], "signal": [1.0], "reason": ["quá cũ"]}).astype({"bartime": "datetime64[us]", "close_time": "datetime64[us]"})
    events = pd.concat([_events(setup, n1=(-1.0, "lý do A"), n3=(1.0, "lý do B")), outside], ignore_index=True)
    table = assemble_cell_table(setup, events, context, same_frame=True)
    assert len(table) == 5 and table["bartime"].tolist() == setup["bartime"].tolist()  # đúng các nến của SETUP, sự kiện ngoài cửa sổ bị bỏ
    assert table["signal"].tolist() == [0.0, -1.0, 0.0, 1.0, 0.0] and table["reason"].tolist() == ["", "lý do A", "", "lý do B", ""]


def test_assemble_cell_table_trigger_none_hoac_rong_nghia_la_khong_nen_nao_co_tin_hieu() -> None:
    """Không nến nào trong cửa sổ có tín hiệu thì LIST TRIGGER không tồn tại (None) hoặc rỗng: bình thường, mọi nến signal 0, không lỗi."""
    setup = _trigger_table(3)
    context = pd.DataFrame({"bartime": setup["bartime"], "close_time": setup["close_time"], "macd_h": [0.0] * 3, "ma": [1.0] * 3})
    for events in (None, _events(setup).iloc[0:0]):
        table = assemble_cell_table(setup, events, context, same_frame=True)
        assert table["signal"].tolist() == [0.0, 0.0, 0.0] and table["reason"].tolist() == ["", "", ""]


# =============================================================================
# 4. Ghi tín hiệu (RawSignalWriter) -- chuyển từ strategy_runner khi phần xuất bản về file này.
# =============================================================================


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
