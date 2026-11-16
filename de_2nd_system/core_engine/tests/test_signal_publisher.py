"""Test decision_engine.py (sổ đăng ký, quyết định tín hiệu cuối) và util.py (tên key, nội dung, hạn hiệu lực, ghi ra db0) — hermetic, Redis giả (RecordingRedis).

``decide`` được kiểm bằng đúng các kịch bản đã chốt với chủ hệ: có tín hiệu và trend cùng chiều (phải chờ nến trend đóng đúng lúc), trend ngược chiều thì dời tối đa N nến, tín hiệu ngược chiều giữa chừng thì hủy.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest
from engine_support import RecordingRedis, decode_eval, make_bars

from core_engine.src.decision_engine import (
    TREND_SUFFIX,
    Registration,
    TrendRef,
    decide,
    register,
)
from core_engine.src.util import (
    DEFAULT_PRICE_DECIMALS,
    ModuleWriter,
    SignalWriter,
    as_utc,
    bias_rows,
    build_signal,
    channel_name,
    module_token,
    signal_rows,
    signal_window,
    to_hash_fields,
)

M10, M30 = pd.Timedelta(minutes=10), pd.Timedelta(minutes=30)
M45 = pd.Timedelta(minutes=45)
REG = Registration("ma_cross", "BTCUSD", "M10", M10, (TrendRef("trend", "M30", M30),))  # một chiến lược tham chiếu đúng MỘT yếu tố tên "trend" ở M30
T = pd.Timestamp

# =============================================================================
# 1. Sổ đăng ký.
# =============================================================================


def test_so_dang_ky_moi_symbol_mot_o_tham_chieu_cac_yeu_to_theo_thu_tu_khai() -> None:
    refs = [TrendRef("macd", "M30", M30), TrendRef("break20", "M45", M45)]
    book = register("ma_cross", ("US30", "BTCUSD"), "M10", M10, refs)
    assert [(r.strategy, r.symbol, r.trigger_tf) for r in book] == [("ma_cross", "US30", "M10"), ("ma_cross", "BTCUSD", "M10")]
    assert book[0] == Registration("ma_cross", "US30", "M10", M10, tuple(refs))


def test_yeu_to_bang_khung_trigger_van_duoc_nhung_nho_hon_thi_loi_neu_dung_yeu_to() -> None:
    assert len(register("combo", ("US30",), "M30", M30, [TrendRef("sma20", "M30", M30)])) == 1
    with pytest.raises(ValueError, match="strategy combo: trend sma20 on M20 is smaller than trigger timeframe M30"):
        register("combo", ("US30",), "M30", M30, [TrendRef("sma20", "M20", 2 * M10)])


def test_chien_luoc_khong_tham_chieu_yeu_to_nao_thi_loi() -> None:
    with pytest.raises(ValueError, match="strategy combo references no trend"):
        register("combo", ("US30",), "M30", M30, [])


# =============================================================================
# 2. Quyết định tín hiệu cuối.
# =============================================================================


def decide_one(signals, candles, trend, registration, **kwargs):
    """``decide`` cho chiến lược tham chiếu đúng MỘT yếu tố tên "trend" (``REG``) và lệnh MARKET: các test một yếu tố giữ nguyên hình dạng cũ."""
    return decide(signals, candles, {"trend": trend}, registration, stop_entry=False, **kwargs)


def _candles(until: str, start: str = "2026-11-16 06:00") -> pd.DataFrame:
    """Nến M10 tăng dần từ ``start`` tới nến MỞ lúc ``until`` (nến mới nhất đã về); giá lấy từ bộ nến tất định."""
    index = pd.date_range(start, f"2026-11-16 {until}", freq="10min").astype("datetime64[us]")
    bars = make_bars(len(index))
    bars["bartime"] = index
    return bars


def _signals(*items: tuple[str, int]) -> pd.DataFrame:
    """Kết quả module MA Cross: (giờ mở nến, chiều)."""
    opens = pd.to_datetime([f"2026-11-16 {hour}" for hour, _ in items]).astype("datetime64[us]")
    return pd.DataFrame(
        {
            "bartime": opens,
            "close_time": opens + M10,
            "signal": [float(side) for _, side in items],
            "reason": ["fast SMA crossed above slow SMA" if side > 0 else "fast SMA crossed below slow SMA" for _, side in items],
        }
    )


def _trend(*items: tuple[str, int]) -> pd.DataFrame:
    """Kết quả module trend: (giờ mở nến M30, hướng)."""
    opens = pd.to_datetime([f"2026-11-16 {hour}" for hour, _ in items]).astype("datetime64[us]")
    return pd.DataFrame({"bartime": opens, "close_time": opens + M30, "trend_bias": [float(bias) for _, bias in items]})


def _fires(signals, trend, until: str, *, shift: int = 3) -> list[tuple[str, int]]:
    out = decide_one(signals, _candles(until), trend, REG, max_shift_bars=shift, atr_period=5)
    return [(row["bartime"].strftime("%H:%M"), int(row["signal"])) for row in out]


def test_trend_cung_chieu_nhung_nen_trend_dong_dung_luc_chua_ve_thi_cho_ve_roi_phat_ngay_o_nen_tin_hieu() -> None:
    signals = _signals(("10:20", 1))  # nến mở 10:20 đóng 10:30, trùng giờ đóng nến M30 mở 10:00
    assert _fires(signals, _trend(("09:00", 1), ("09:30", 1)), "10:20") == []  # nến M30 mở 10:00 chưa về: chờ, không dùng nến M30 mở 09:30 thay
    assert _fires(signals, _trend(("09:30", 1), ("10:00", 1)), "10:20") == [("10:20", 1)]


def test_nen_tin_hieu_khong_trung_gio_dong_nen_trend_thi_dung_nen_trend_da_dong_gan_nhat_khong_cho() -> None:
    assert _fires(_signals(("10:10", 1)), _trend(("09:00", 1), ("09:30", 1)), "10:10") == [("10:10", 1)]  # đóng 10:20, nến M30 mở 09:30 (đóng 10:00) là nến phải dùng


def test_ly_do_la_ly_do_cua_module_cong_hau_to_trend_gia_vao_lenh_la_close_va_atr_tinh_tu_nen_phat() -> None:
    candles = _candles("10:20")
    [row] = decide_one(_signals(("10:20", 1)), candles, _trend(("09:30", 1), ("10:00", 1)), REG, max_shift_bars=3, atr_period=5)
    assert row["signal_reason"] == "fast SMA crossed above slow SMA" + TREND_SUFFIX
    last = candles.iloc[-1]
    assert row["bartime"] == last["bartime"] and row["entry_price"] == last["close"] and row["signal"] == 1
    from core_engine.src.indicator import atr

    assert row["atr"] == pytest.approx(atr(candles, 5).iloc[-1])


def test_trend_nguoc_chieu_thi_doi_den_nen_dau_tien_trend_cung_chieu() -> None:
    signals = _signals(("10:20", 1))
    trend_sell = _trend(("09:30", -1), ("10:00", -1))
    assert _fires(signals, trend_sell, "10:20") == []  # bị chặn ở nến tín hiệu
    assert _fires(signals, trend_sell, "10:40") == []  # dời lần 1 và 2: vẫn dùng nến M30 mở 10:00 (SELL)
    assert _fires(signals, trend_sell, "10:50") == []  # nến mở 10:50 (đóng 11:00) phải chờ nến M30 mở 10:30
    assert _fires(signals, _trend(("10:00", -1), ("10:30", 1)), "10:50") == [("10:50", 1)]  # nến M30 mở 10:30 về, BUY: phát ở nến dời thứ 3, mốc là nến phát
    assert _fires(signals, _trend(("10:00", -1), ("10:30", -1)), "10:50") == []  # vẫn SELL: hết 3 nến thì bỏ


def test_dang_cho_dung_dau_khi_nen_tin_hieu_da_cach_nen_moi_nhat_qua_so_nen_doi() -> None:
    assert _fires(_signals(("10:20", 1)), _trend(("10:00", -1), ("10:30", 1)), "11:00") == []  # nến mới nhất mở 11:00, đã quá 3 nến dời kể từ 10:20
    assert _fires(_signals(("10:20", 1)), _trend(("10:00", 1), ("10:30", 1), ("11:00", 1)), "11:10") == []


def test_max_shift_bars_bang_0_thi_khong_doi() -> None:
    signals, trend = _signals(("10:20", 1)), _trend(("09:30", -1), ("10:00", -1), ("10:30", 1))
    assert _fires(signals, trend, "10:50", shift=0) == []
    assert _fires(signals, _trend(("09:30", 1), ("10:00", 1)), "10:20", shift=0) == [("10:20", 1)]


def test_tin_hieu_nguoc_chieu_giua_chung_huy_tin_hieu_dang_cho_va_tin_hieu_nguoc_chay_moi() -> None:
    signals = _signals(("10:20", 1), ("10:40", -1))
    trend_sell = _trend(("09:30", -1), ("10:00", -1))
    assert _fires(signals, trend_sell, "10:40") == [("10:40", -1)]  # BUY 10:20 bị hủy; SELL 10:40 (đóng 10:50, không trùng giờ đóng M30) dùng nến M30 mở 10:00 (SELL) nên phát ngay
    assert _fires(signals, _trend(("09:30", 1), ("10:00", 1)), "10:40") == [("10:20", 1)]  # trend BUY: BUY phát từ nến tín hiệu; SELL bị chặn và còn trong cửa sổ dời
    assert _fires(_signals(("10:20", 1), ("10:30", -1)), trend_sell, "10:30") == [("10:30", -1)]  # SELL ngay nến kế: nến 10:30 thuộc tín hiệu ngược, BUY không dời được sang nó


def test_khong_co_tin_hieu_nen_hoac_trend_thi_chua_co_gi_de_quyet() -> None:
    trend = _trend(("09:30", 1), ("10:00", 1))
    assert decide_one(None, _candles("10:20"), trend, REG, max_shift_bars=3, atr_period=5) == []
    assert decide_one(_signals(("10:20", 1))[:0], _candles("10:20"), trend, REG, max_shift_bars=3, atr_period=5) == []
    assert decide_one(_signals(("10:20", 1)), _candles("10:20")[:0], trend, REG, max_shift_bars=3, atr_period=5) == []
    assert decide_one(_signals(("10:20", 1)), _candles("10:20"), None, REG, max_shift_bars=3, atr_period=5) == []  # chưa có nến trend nào về: chờ
    assert decide_one(_signals(("03:00", 1)), _candles("10:20"), trend, REG, max_shift_bars=3, atr_period=5) == []  # nến tín hiệu không còn trong bảng nến


def test_nen_chua_du_de_tinh_atr_thi_khong_phat() -> None:
    candles = _candles("10:20", start="2026-11-16 10:00")  # chỉ 3 nến: ATR chu kỳ 5 là NaN
    assert decide_one(_signals(("10:20", 1)), candles, _trend(("09:30", 1), ("10:00", 1)), REG, max_shift_bars=3, atr_period=5) == []


def test_decide_la_ham_thuan_goi_lai_cho_cung_ket_qua_va_khong_sua_dau_vao() -> None:
    signals, trend, candles = _signals(("10:20", 1)), _trend(("09:30", 1), ("10:00", 1)), _candles("10:20")
    before = (signals.copy(), trend.copy(), candles.copy())
    first = decide_one(signals, candles, trend, REG, max_shift_bars=3, atr_period=5)
    second = decide_one(signals, candles, trend, REG, max_shift_bars=3, atr_period=5)
    pd.testing.assert_frame_equal(pd.DataFrame(first), pd.DataFrame(second))
    for frame, original in zip((signals, trend, candles), before, strict=True):
        pd.testing.assert_frame_equal(frame, original)


def _factor(length: pd.Timedelta, *items: tuple[str, int]) -> pd.DataFrame:
    """Kết quả một yếu tố trend ở khung có độ dài ``length``: (giờ mở nến, hướng)."""
    opens = pd.to_datetime([f"2026-11-16 {hour}" for hour, _ in items]).astype("datetime64[us]")
    return pd.DataFrame({"bartime": opens, "close_time": opens + length, "trend_bias": [float(bias) for _, bias in items]})


REG2 = Registration("ma_cross", "BTCUSD", "M10", M10, (TrendRef("macd", "M30", M30), TrendRef("sma20", "M30", M30)))
REG_BREAK = Registration("ma_cross", "BTCUSD", "M10", M10, (TrendRef("macd", "M30", M30), TrendRef("break20", "M45", M45)))


def _fires_many(registration, trends, until: str = "10:20", signals=None, **kwargs) -> list[tuple[str, int]]:
    out = decide(_signals(("10:20", 1)) if signals is None else signals, _candles(until), trends, registration, max_shift_bars=3, atr_period=5, stop_entry=kwargs.get("stop_entry", False))
    return [(row["bartime"].strftime("%H:%M"), int(row["signal"])) for row in out]


def test_nhieu_yeu_to_chi_phat_khi_tat_ca_cung_chieu() -> None:
    macd, sma20 = _factor(M30, ("09:30", 1), ("10:00", 1)), _factor(M30, ("09:30", 1), ("10:00", -1))
    assert _fires_many(REG2, {"macd": macd, "sma20": sma20}) == []  # sma20 ngược chiều ở nến M30 mở 10:00: bị chặn ở nến tín hiệu
    assert _fires_many(REG2, {"macd": macd, "sma20": _factor(M30, ("09:30", 1), ("10:00", 1))}) == [("10:20", 1)]


def test_nhieu_yeu_to_moi_yeu_to_cho_nen_dong_dung_luc_cua_rieng_no() -> None:
    macd = _factor(M30, ("09:30", 1), ("10:00", 1))  # đủ: nến M30 mở 10:00 (đóng 10:30) đã về
    waiting = _factor(M45, ("08:15", 1), ("09:00", 1))  # nến M45 mở 09:45 (đóng 10:30) chưa về: phải chờ, không dùng nến M45 cũ thay
    assert _fires_many(REG_BREAK, {"macd": macd, "break20": waiting}) == []
    assert _fires_many(REG_BREAK, {"macd": macd, "break20": _factor(M45, ("08:15", 1), ("09:00", 1), ("09:45", 1))}) == [("10:20", 1)]
    assert _fires_many(REG_BREAK, {"macd": macd, "break20": _factor(M45, ("08:15", 1), ("09:00", 1), ("09:45", -1))}) == []  # về rồi nhưng ngược chiều


def test_thieu_ten_yeu_to_trong_trends_thi_chua_co_gi_de_quyet() -> None:
    macd = _factor(M30, ("09:30", 1), ("10:00", 1))
    assert _fires_many(REG2, {"macd": macd}) == [] and _fires_many(REG2, {"macd": macd, "sma20": None}) == [] and _fires_many(REG2, {}) == []


def test_lenh_stop_lay_gia_cho_o_dinh_nen_phat_voi_buy_va_day_nen_phat_voi_sell_con_market_lay_close() -> None:
    candles = _candles("10:20")
    last = candles.iloc[-1]
    trend_up = {"trend": _factor(M30, ("09:30", 1), ("10:00", 1))}
    [buy] = decide(_signals(("10:20", 1)), candles, trend_up, REG, max_shift_bars=3, atr_period=5, stop_entry=True)
    assert buy["entry_price"] == last["high"] and buy["signal"] == 1
    [market] = decide(_signals(("10:20", 1)), candles, trend_up, REG, max_shift_bars=3, atr_period=5, stop_entry=False)
    assert market["entry_price"] == last["close"]
    [sell] = decide(_signals(("10:20", -1)), candles, {"trend": _factor(M30, ("09:30", -1), ("10:00", -1))}, REG, max_shift_bars=3, atr_period=5, stop_entry=True)
    assert sell["entry_price"] == last["low"] and sell["signal"] == -1


def test_hai_yeu_to_macd_va_sma20_cung_khung_cho_dung_ket_qua_cua_luat_trend_gop_cu() -> None:
    """Luật trend gộp cũ: +1 khi close > SMA20 và MACD Histogram > 0, -1 khi ngược lại, còn lại 0. Tách thành hai yếu tố cùng khung, bắt cả hai cùng chiều, phải ra đúng tập tín hiệu cuối như một yếu tố gộp."""
    from core_engine.src.de_module import mac_strategy, trend

    m10, m30 = make_bars(900, freq="10min"), make_bars(300, freq="30min")  # cùng khoảng thời gian: 150 giờ từ 2026-01-01
    out = mac_strategy.detect(m10, fast_ma=13, slow_ma=34, macd_fast=5, macd_slow=25, macd_signal=5)
    found = out.loc[out["signal"] != 0]
    signals = pd.DataFrame({"bartime": found["bartime"], "close_time": found["bartime"] + M10, "signal": found["signal"].astype(float), "reason": found["signal_reason"]})
    macd, sma20 = trend.bias_macd(m30, fast=5, slow=25, signal=5), trend.bias_sma(m30, period=20)
    fused = np.where((macd == 1) & (sma20 == 1), 1, np.where((macd == -1) & (sma20 == -1), -1, 0))

    def frame(bias: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame({"bartime": m30["bartime"], "close_time": m30["bartime"] + M30, "trend_bias": bias.astype(float)})

    split, single = Registration("ma_cross", "BTCUSD", "M10", M10, (TrendRef("macd", "M30", M30), TrendRef("sma20", "M30", M30))), REG
    compared = 0
    for cut in range(120, 900, 5):  # xét như thể nến M10 mới nhất là nến thứ ``cut``
        candles = m10.iloc[:cut]
        two = decide(signals, candles, {"macd": frame(macd), "sma20": frame(sma20)}, split, max_shift_bars=3, atr_period=5, stop_entry=False)
        one = decide(signals, candles, {"trend": frame(fused)}, single, max_shift_bars=3, atr_period=5, stop_entry=False)
        assert [(row["bartime"], row["signal"], row["entry_price"], row["atr"]) for row in two] == [(row["bartime"], row["signal"], row["entry_price"], row["atr"]) for row in one], cut
        compared += len(two)
    assert compared >= 5  # bộ nến có đủ tín hiệu đi qua nên phép so sánh không rỗng


# =============================================================================
# 3. Tên, nội dung, hạn hiệu lực (util).
# =============================================================================

ROW = pd.Series({"bartime": T("2026-11-16 10:20:00"), "signal": 1, "entry_price": 98455.123456, "atr": 41.23456, "signal_reason": "fast SMA crossed above slow SMA"})
VALID_FROM, VALID_UNTIL = T("2026-11-16 10:30:00", tz="UTC"), T("2026-11-16 10:40:00", tz="UTC")


def test_token_va_ten_kenh() -> None:
    assert module_token("ma_cross") == "MACROSS" and module_token("trend") == "TREND" and module_token("break20") == "BREAK20"
    assert channel_name("L_CHANNEL", "ma_cross", "btcusd", "m10") == "L_CHANNEL_MACROSS_BTCUSD_M10"


def test_signal_window_la_tu_gio_nen_dong_den_han_chot() -> None:
    assert signal_window(M10, T("2026-11-16 10:20:00"), 600) == (VALID_FROM, VALID_UNTIL)  # nến mở 10:20 đóng 10:30, hiệu lực 600 giây


def test_build_signal_lam_tron_entry_va_atr_theo_so_le_cua_symbol_va_dung_id_va_mot_moc() -> None:
    signal_id, stamp, payload = build_signal(strategy="ma_cross", timeframe="m10", symbol="btcusd", row=ROW, valid_from=VALID_FROM, valid_until=VALID_UNTIL, order_type="MARKET")
    assert signal_id == "ma_cross:M10:BTCUSD:20261116T102000Z" and stamp == "2026-11-16 10:20:00"
    assert payload["entry"] == round(98455.123456, DEFAULT_PRICE_DECIMALS) and payload["atr"] == 41.23 and payload["side"] == "BUY"
    assert payload["bartime"] == "2026-11-16T10:20:00Z" and payload["valid_from"] == "2026-11-16T10:30:00Z" and payload["valid_until"] == "2026-11-16T10:40:00Z"
    _, _, forex = build_signal(strategy="ma_cross", timeframe="M10", symbol="EURUSD", row=ROW, valid_from=VALID_FROM, valid_until=VALID_UNTIL, order_type="MARKET", decimals=5)
    assert forex["entry"] == 98455.12346 and forex["atr"] == 41.23456


def test_build_signal_loai_lenh_la_market_hoac_stop_va_chi_nhan_dong_mua_ban_va_so_huu_han() -> None:
    kwargs = {"strategy": "ma_cross", "timeframe": "M10", "symbol": "BTCUSD", "valid_from": VALID_FROM, "valid_until": VALID_UNTIL, "order_type": "MARKET"}
    for column, value, message in (("signal", 0, "Only BUY/SELL"), ("entry_price", float("nan"), "finite"), ("atr", float("inf"), "finite")):
        row = ROW.copy()
        row[column] = value
        with pytest.raises(ValueError, match=message):
            build_signal(row=row, **kwargs)
    with pytest.raises(ValueError, match="order_type must be one of"):
        build_signal(row=ROW, **{**kwargs, "order_type": "LIMIT"})


def test_to_hash_fields_lenh_market_khong_co_stop_price_con_lenh_stop_co_stop_price() -> None:
    _, _, payload = build_signal(strategy="ma_cross", timeframe="M10", symbol="BTCUSD", row=ROW, valid_from=VALID_FROM, valid_until=VALID_UNTIL, order_type="MARKET")
    fields = to_hash_fields(payload)
    assert fields == {
        "symbol": "BTCUSD", "timeframe": "M10", "strategy": "ma_cross", "bartime": "2026-11-16T10:20:00Z", "tradeSide": "BUY", "orderType": "MARKET",
        "expirationTimestamp": str(int(VALID_UNTIL.timestamp())), "clientOrderId": "ma_cross:M10:BTCUSD:20261116T102000Z", "comment": "fast SMA crossed above slow SMA",
        "atr": "41.23", "valid_from": "2026-11-16T10:30:00Z",
    }
    assert "stopPrice" not in fields and as_utc("2026-11-16 10:40:00") == VALID_UNTIL
    _, _, stop_payload = build_signal(strategy="combo", timeframe="M30", symbol="BTCUSD", row=ROW, valid_from=VALID_FROM, valid_until=VALID_UNTIL, order_type="stop")
    stop_fields = to_hash_fields(stop_payload)
    assert stop_fields["orderType"] == "STOP" and stop_fields["stopPrice"] == "98455.12" and stop_fields["strategy"] == "combo"  # giá chờ = entry đã làm tròn theo số lẻ của symbol
    assert {key: value for key, value in stop_fields.items() if key not in ("orderType", "stopPrice", "strategy", "timeframe", "clientOrderId")} == {
        key: value for key, value in fields.items() if key not in ("orderType", "strategy", "timeframe", "clientOrderId")
    }


def test_signal_rows_chi_co_dong_o_nen_co_tin_hieu_va_bias_rows_co_dong_o_moi_nen() -> None:
    frame = pd.DataFrame(
        {
            "bartime": pd.to_datetime(["2026-11-16 10:00", "2026-11-16 10:10", "2026-11-16 10:20"]),
            "signal": [0, 1, -1], "signal_reason": ["", "fast SMA crossed above slow SMA", "fast SMA crossed below slow SMA"], "trend_bias": [0, 1, -1],
        }
    )
    assert signal_rows(frame, M10) == [
        ("2026-11-16 10:10:00", {"close_time": "2026-11-16 10:20:00", "signal": "1", "reason": "fast SMA crossed above slow SMA"}),
        ("2026-11-16 10:20:00", {"close_time": "2026-11-16 10:30:00", "signal": "-1", "reason": "fast SMA crossed below slow SMA"}),
    ]
    assert bias_rows(frame, M30) == [
        ("2026-11-16 10:00:00", {"close_time": "2026-11-16 10:30:00", "trend_bias": "0"}),
        ("2026-11-16 10:10:00", {"close_time": "2026-11-16 10:40:00", "trend_bias": "1"}),
        ("2026-11-16 10:20:00", {"close_time": "2026-11-16 10:50:00", "trend_bias": "-1"}),
    ]


# =============================================================================
# 4. Ghi ra db0.
# =============================================================================


def _module_writer(client: RecordingRedis) -> ModuleWriter:
    return ModuleWriter(host="x", port=1, password="", db=0, key_prefix="L_MODULE", retention_max_entries=500, client=client)


def _signal_writer(client: RecordingRedis, **kwargs) -> SignalWriter:
    return SignalWriter(
        host="x", port=1, password="", db=0, key_prefix="L_DE", channel_prefix="L_CHANNEL", retention_seconds=604800, retention_max_entries=500, client=client, **kwargs
    )


def test_module_writer_ghi_dung_key_hash_va_chi_giu_tu_keep_from() -> None:
    client = RecordingRedis(emulate=True)
    writer = _module_writer(client)
    rows = [(f"2026-11-16 10:{minute}:00", {"close_time": f"2026-11-16 10:{minute + 10}:00", "signal": "1", "reason": "r"}) for minute in (10, 20, 30)]
    assert writer.write(module="ma_cross", symbol="BTCUSD", timeframe="M10", rows=rows, retention_seconds=1200, keep_from="2026-11-16 10:20:00") == 3
    assert client.lists["L_MODULE_MACROSS_BTCUSD_M10"] == ["2026-11-16 10:20:00", "2026-11-16 10:30:00"]  # mốc cũ hơn keep_from bị bỏ cùng HASH
    assert "L_MODULE_MACROSS_BTCUSD_M10:2026-11-16 10:10:00" not in client.hashes
    first = decode_eval(client.calls[0])
    assert first["hash_key"] == "L_MODULE_MACROSS_BTCUSD_M10:2026-11-16 10:10:00" and first["ttl"] == "1200" and first["max"] == "500" and first["channel"] == ""  # module không báo kênh
    assert writer.write(module="ma_cross", symbol="BTCUSD", timeframe="M10", rows=rows[1:], retention_seconds=1200, keep_from="2026-11-16 10:20:00") == 0  # ghi lại: mỗi mốc đúng một lần


def test_module_writer_khong_co_dong_nao_van_don_mot_cu_cua_so() -> None:
    client = RecordingRedis(emulate=True)
    writer = _module_writer(client)
    writer.write(module="trend", symbol="BTCUSD", timeframe="M30", rows=[("2026-11-16 09:00:00", {"close_time": "x", "trend_bias": "1"})], retention_seconds=60, keep_from="2000-01-01 00:00:00")
    assert writer.write(module="trend", symbol="BTCUSD", timeframe="M30", rows=[], retention_seconds=60, keep_from="2026-11-16 10:00:00") == 0
    assert client.lists["L_MODULE_TREND_BTCUSD_M30"] == []


NOW = T("2026-11-16 10:30:14", tz="UTC")


def test_signal_writer_ghi_list_hash_o_db0_va_bao_kenh_voi_message_la_moc() -> None:
    client = RecordingRedis(emulate=True)
    outcome = _signal_writer(client).publish(strategy="ma_cross", timeframe="M10", symbol="BTCUSD", row=ROW, length=M10, validity_seconds=600, order_type="MARKET", now=NOW)
    assert outcome == ("published", 1)
    call = decode_eval(client.calls[0])
    assert call["list_key"] == "L_DE_BTCUSD_M10" and call["hash_key"] == "L_DE_BTCUSD_M10:2026-11-16 10:20:00" and call["stamp"] == "2026-11-16 10:20:00"
    assert call["channel"] == "L_CHANNEL_MACROSS_BTCUSD_M10" and call["message"] == "2026-11-16 10:20:00"  # message chỉ là mốc, bên nhận tự đọc HASH
    assert call["ttl"] == "604800" and call["max"] == "500"
    assert call["fields"]["tradeSide"] == "BUY" and call["fields"]["orderType"] == "MARKET" and call["fields"]["clientOrderId"] == "ma_cross:M10:BTCUSD:20261116T102000Z"


def test_signal_writer_da_co_mot_moc_thi_khong_ghi_lai_va_khong_bao_kenh_lai(caplog: pytest.LogCaptureFixture) -> None:
    client = RecordingRedis(emulate=True)
    writer = _signal_writer(client)
    kwargs = {"strategy": "ma_cross", "timeframe": "M10", "symbol": "BTCUSD", "row": ROW, "length": M10, "validity_seconds": 600, "order_type": "MARKET", "now": NOW}
    writer.publish(**kwargs)
    with caplog.at_level(logging.INFO):
        assert writer.publish(**kwargs) == ("duplicate", 0)
    assert "event=signal_duplicate" in caplog.text and client.lists["L_DE_BTCUSD_M10"] == ["2026-11-16 10:20:00"]


def test_signal_writer_qua_han_hieu_luc_thi_bo_khong_ghi_gi(caplog: pytest.LogCaptureFixture) -> None:
    client = RecordingRedis(emulate=True)
    late = T("2026-11-16 10:40:00", tz="UTC")  # đúng hạn chót: đã hết hiệu lực
    with caplog.at_level(logging.INFO):
        outcome = _signal_writer(client).publish(strategy="ma_cross", timeframe="M10", symbol="BTCUSD", row=ROW, length=M10, validity_seconds=600, order_type="MARKET", now=late)
    assert outcome == ("expired", 0) and client.calls == [] and "event=signal_expired" in caplog.text


def test_signal_writer_lam_tron_theo_so_le_cua_symbol() -> None:
    client = RecordingRedis(emulate=True)
    _signal_writer(client, price_decimals={"EURUSD": 5}).publish(strategy="ma_cross", timeframe="M10", symbol="eurusd", row=ROW, length=M10, validity_seconds=600, order_type="MARKET", now=NOW)
    assert decode_eval(client.calls[0])["fields"]["atr"] == "41.23456"
    assert np.isclose(float(decode_eval(client.calls[0])["fields"]["atr"]), 41.23456)
