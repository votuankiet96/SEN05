"""Test signal_publisher.py (phần dùng chung về tín hiệu): tên key/kênh, nội dung HASH theo hợp đồng OE, cửa sổ hiệu lực — hermetic, không Redis.

Giá trị mong đợi là bản ghi tín hiệu THẬT của đường xử lý (ma_cross HK50 H1 nến mở 2026-10-16 16:00, SELL). Việc GHI tín hiệu raw nằm ở strategy_runner.RawSignalWriter (test_strategy_runner.py).
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from core_engine.src.signal_publisher import (
    build_signal,
    channel_name,
    check_order_type,
    de_list_key,
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
