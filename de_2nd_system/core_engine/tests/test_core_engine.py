"""Test indicator.py (chỉ báo thuần), de_module/mac_strategy.py (luật MA Cross) và de_module/combo_strategy.py (luật Combo) — hermetic, thuần pandas/numpy.

Chỉ báo: SMA/EMA/MACD Histogram/ATR khớp công thức; ATR seed bằng SMA đúng như `ta.atr` của TradingView (không seed bằng điểm đầu tiên). MA Cross: tín hiệu là SỰ KIỆN SMA nhanh cắt SMA chậm xét trên hai nến
liền nhau và được MACD Histogram của nến cắt xác nhận. Combo có thêm điều kiện xen kẽ: chỉ setup đầu tiên mỗi khi đổi chiều thành tín hiệu.
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np
import pandas as pd
import pytest
from engine_support import make_bars

from core_engine.src import indicator
from core_engine.src.de_module import combo_strategy, mac_strategy
from core_engine.src.de_module.combo_strategy import detect_combo_signals
from core_engine.src.de_module.mac_strategy import detect_ma_cross_signals

MA_CROSS_PARAMS = {"fast_ma": 13, "slow_ma": 34, "macd_fast": 5, "macd_slow": 25, "macd_signal": 5}
COMBO_PARAMS = {"ma_period": 20, "macd_fast": 5, "macd_slow": 25, "macd_signal": 5}

# =============================================================================
# 1. Chỉ báo thuần.
# =============================================================================


def test_sma_can_du_period_diem_moi_co_gia_tri() -> None:
    result = indicator.sma(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), 3)
    assert result.isna().tolist() == [True, True, False, False, False]
    assert result.tolist()[2:] == [2.0, 3.0, 4.0]


def test_ema_khong_hieu_chinh_bias_dau_chuoi() -> None:
    values = pd.Series([10.0, 12.0, 11.0, 15.0])
    alpha = 2 / (3 + 1)
    expected = [10.0]
    for value in values.tolist()[1:]:
        expected.append(alpha * value + (1 - alpha) * expected[-1])
    assert indicator.ema(values, 3).tolist() == pytest.approx(expected)


def test_macd_hist_bang_macd_tru_signal() -> None:
    close = make_bars(120)["close"]
    macd_line = indicator.ema(close, 5) - indicator.ema(close, 25)
    expected = macd_line - indicator.ema(macd_line, 5)
    assert indicator.macd_hist(close, fast=5, slow=25, signal=5).tolist() == pytest.approx(expected.tolist())


def test_atr_seed_bang_sma_cua_true_range_roi_lam_muot_wilder() -> None:
    bars = make_bars(40)
    period = 5
    true_range = pd.concat(
        [bars["high"] - bars["low"], (bars["high"] - bars["close"].shift(1)).abs(), (bars["low"] - bars["close"].shift(1)).abs()], axis=1
    ).max(axis=1)
    result = indicator.atr(bars, period)
    assert result.iloc[: period - 1].isna().all()
    assert result.iloc[period - 1] == pytest.approx(true_range.iloc[:period].mean())  # seed = SMA(period) như TradingView, không phải điểm dữ liệu đầu tiên
    assert result.iloc[period] == pytest.approx(true_range.iloc[period] / period + (1 - 1 / period) * result.iloc[period - 1])


def test_atr_chuoi_ngan_hon_period_toan_nan() -> None:
    assert indicator.atr(make_bars(3), 5).isna().all()


# =============================================================================
# 2. MA Cross.
# =============================================================================


def _frame(fast: list[float], slow: list[float], hist: list[float] | None = None) -> pd.DataFrame:
    """Bảng đã có chỉ báo; ``hist`` (macd_h) mặc định dương ở mọi nến."""
    return pd.DataFrame(
        {"bartime": pd.date_range("2026-01-01", periods=len(fast), freq="10min"), "fast_ma": fast, "slow_ma": slow, "macd_h": [1.0] * len(fast) if hist is None else hist}
    )


def test_ma_cross_buy_khi_sma_nhanh_vua_cat_len_va_sell_khi_vua_cat_xuong() -> None:
    out = detect_ma_cross_signals(_frame([1, 2, 3, 2, 1], [2, 2, 2, 2, 2], [1, 1, 1, -1, -1]))
    assert out["signal"].tolist() == [0, 0, 1, 0, -1]  # nến 2: 2<=2 rồi 3>2 (BUY); nến 3 chạm nhau, nến 4: 2>=2 rồi 1<2 (SELL)
    assert out["signal_reason"].tolist() == ["", "", "fast SMA crossed above slow SMA", "", "fast SMA crossed below slow SMA"]


def test_ma_cross_chi_bat_o_nen_cat_khong_bat_o_moi_nen_con_nam_tren() -> None:
    out = detect_ma_cross_signals(_frame([1, 3, 4, 5, 6, 5], [2, 2, 2, 2, 2, 2]))
    assert out["signal"].tolist() == [0, 1, 0, 0, 0, 0]  # nhanh nằm trên chậm suốt nhiều nến mà chỉ nến cắt có tín hiệu


def test_ma_cross_cham_roi_cat_van_la_mot_tin_hieu_va_bang_nhau_khong_phai_cat() -> None:
    assert detect_ma_cross_signals(_frame([2, 2, 3], [2, 2, 2]))["signal"].tolist() == [0, 0, 1]  # bằng nhau hai nến liền rồi vượt lên
    assert detect_ma_cross_signals(_frame([2, 2, 2], [2, 2, 2]))["signal"].tolist() == [0, 0, 0]
    assert detect_ma_cross_signals(_frame([3, 3, 3], [2, 2, 2]))["signal"].tolist() == [0, 0, 0]  # đã ở trên từ đầu bảng: không có nến trước nên không cắt


def test_ma_cross_nen_con_nan_hoac_nen_dau_khong_co_tin_hieu() -> None:
    out = detect_ma_cross_signals(_frame([np.nan, np.nan, 1.0, 3.0], [np.nan, 2.0, 2.0, 2.0]))
    assert out["signal"].tolist() == [0, 0, 0, 1]  # nến 2 chưa có nến trước hợp lệ; nến 3 mới là lần cắt đầu tiên


def test_ma_cross_can_macd_histogram_xac_nhan_o_nen_cat() -> None:
    cross_up, cross_down = ([1, 3], [2, 2]), ([3, 1], [2, 2])
    assert detect_ma_cross_signals(_frame(*cross_up, [0.0, 0.5]))["signal"].tolist() == [0, 1]  # cắt lên và Histogram > 0: BUY
    assert detect_ma_cross_signals(_frame(*cross_up, [0.0, 0.0]))["signal"].tolist() == [0, 0]  # Histogram bằng 0 không xác nhận
    assert detect_ma_cross_signals(_frame(*cross_up, [0.0, -0.5]))["signal"].tolist() == [0, 0]  # cắt lên nhưng Histogram âm: không BUY
    assert detect_ma_cross_signals(_frame(*cross_up, [0.0, np.nan]))["signal"].tolist() == [0, 0]  # chưa có Histogram: không xác nhận
    assert detect_ma_cross_signals(_frame(*cross_down, [0.0, -0.5]))["signal"].tolist() == [0, -1]  # cắt xuống và Histogram < 0: SELL
    assert detect_ma_cross_signals(_frame(*cross_down, [0.0, 0.5]))["signal"].tolist() == [0, 0]  # cắt xuống nhưng Histogram dương: không SELL


def test_ma_cross_khong_can_cot_atr_va_khong_sua_input_ra_dung_kieu() -> None:
    frame = _frame([1, 3], [2, 2])
    before = frame.copy()
    out = detect_ma_cross_signals(frame)
    assert list(out.columns) == [*before.columns, "signal", "signal_reason"]
    assert str(out["signal"].dtype) == "int64"
    pd.testing.assert_frame_equal(frame, before)


def test_ma_cross_tren_bo_nen_that_khop_phep_tinh_doc_lap() -> None:
    bars = make_bars(600, freq="10min")
    out = mac_strategy.detect(bars, **MA_CROSS_PARAMS)
    fast, slow = bars["close"].rolling(13).mean(), bars["close"].rolling(34).mean()
    hist = indicator.macd_hist(bars["close"], fast=5, slow=25, signal=5)
    cross_up, cross_down = (fast.shift(1) <= slow.shift(1)) & (fast > slow), (fast.shift(1) >= slow.shift(1)) & (fast < slow)
    expected = np.where(cross_up & (hist > 0), 1, np.where(cross_down & (hist < 0), -1, 0))
    assert out["signal"].tolist() == expected.tolist()
    assert (out["signal"] != 0).sum() >= 4  # bộ nến có dao động nên vẫn có nhiều lần cắt được xác nhận


# =============================================================================
# 3. Combo: nến tăng/giảm vừa cắt MA, xác nhận bằng MACD Histogram, trên cùng một nến.
# =============================================================================

COMBO_BUY_REASON = "bullish candle crossing above MA, MACD histogram > 0"
COMBO_SELL_REASON = "bearish candle crossing below MA, MACD histogram < 0"


def _combo_frame(open_: list[float], close: list[float], hist: list[float], ma: list[float] | None = None) -> pd.DataFrame:
    """Bảng đã có chỉ báo; ``ma`` mặc định bằng 10 ở mọi nến."""
    return pd.DataFrame(
        {"bartime": pd.date_range("2026-01-01", periods=len(close), freq="1h"), "open": open_, "close": close, "ma": [10.0] * len(close) if ma is None else ma, "macd_h": hist}
    )


def test_combo_buy_khi_nen_tang_vua_cat_len_ma_va_histogram_duong_sell_doi_xung() -> None:
    out = detect_combo_signals(_combo_frame([9.0, 9.8, 10.6, 10.4, 9.7], [9.5, 10.5, 10.9, 9.6, 9.2], [0.2, 0.5, 0.6, -0.5, -0.4]))
    assert out["signal"].tolist() == [0, 1, 0, -1, 0]  # nến 1: tăng, 10,5 > ma, nến trước 9,5 < ma, Histogram > 0 (BUY); nến 2 đã ở trên ma từ trước; nến 3: giảm vừa cắt xuống (SELL); nến 4 đã ở dưới
    assert out["signal_reason"].tolist() == ["", COMBO_BUY_REASON, "", COMBO_SELL_REASON, ""]


def test_combo_thieu_mot_dieu_kien_buy_thi_khong_co_tin_hieu() -> None:
    assert detect_combo_signals(_combo_frame([9.0, 9.8], [9.5, 10.5], [0.5, 0.5]))["signal"].tolist() == [0, 1]  # nền: đủ bốn điều kiện
    assert detect_combo_signals(_combo_frame([9.0, 10.6], [9.5, 10.5], [0.5, 0.5]))["signal"].tolist() == [0, 0]  # nến giảm (close < open)
    assert detect_combo_signals(_combo_frame([9.0, 9.8], [9.5, 9.9], [0.5, 0.5]))["signal"].tolist() == [0, 0]  # close chưa vượt ma
    assert detect_combo_signals(_combo_frame([9.0, 10.3], [10.2, 10.5], [0.5, 0.5]))["signal"].tolist() == [0, 0]  # nến trước đã ở trên ma và nến này cũng mở trên ma: không phải vừa cắt
    assert detect_combo_signals(_combo_frame([9.0, 9.8], [9.5, 10.5], [0.5, 0.0]))["signal"].tolist() == [0, 0]  # Histogram bằng 0 không xác nhận
    assert detect_combo_signals(_combo_frame([9.0, 9.8], [9.5, 10.5], [0.5, -0.1]))["signal"].tolist() == [0, 0]  # Histogram âm
    assert detect_combo_signals(_combo_frame([9.0, 9.8], [9.5, 10.0], [0.5, 0.5]))["signal"].tolist() == [0, 0]  # close bằng ma: không phải trên ma
    assert detect_combo_signals(_combo_frame([9.0, 10.3], [10.0, 10.5], [0.5, 0.5]))["signal"].tolist() == [0, 0]  # nến trước đóng đúng tại ma (chưa dưới ma) và nến này mở trên ma: chưa phải vừa cắt


def test_combo_thieu_mot_dieu_kien_sell_thi_khong_co_tin_hieu() -> None:
    assert detect_combo_signals(_combo_frame([11.0, 10.2], [10.5, 9.5], [-0.5, -0.5]))["signal"].tolist() == [0, -1]  # nền: đủ bốn điều kiện
    assert detect_combo_signals(_combo_frame([11.0, 9.4], [10.5, 9.5], [-0.5, -0.5]))["signal"].tolist() == [0, 0]  # nến tăng (close > open)
    assert detect_combo_signals(_combo_frame([11.0, 10.2], [10.5, 10.1], [-0.5, -0.5]))["signal"].tolist() == [0, 0]  # close chưa xuống dưới ma
    assert detect_combo_signals(_combo_frame([11.0, 9.7], [9.8, 9.5], [-0.5, -0.5]))["signal"].tolist() == [0, 0]  # nến trước đã ở dưới ma và nến này cũng mở dưới ma: không phải vừa cắt
    assert detect_combo_signals(_combo_frame([11.0, 9.7], [10.0, 9.5], [-0.5, -0.5]))["signal"].tolist() == [0, 0]  # nến trước đóng đúng tại ma (chưa trên ma) và nến này mở dưới ma: chưa phải vừa cắt
    assert detect_combo_signals(_combo_frame([11.0, 10.2], [10.5, 9.5], [-0.5, 0.0]))["signal"].tolist() == [0, 0]  # Histogram bằng 0
    assert detect_combo_signals(_combo_frame([11.0, 10.2], [10.5, 9.5], [-0.5, 0.1]))["signal"].tolist() == [0, 0]  # Histogram dương


def test_combo_nan_hoac_nen_dau_khong_co_tin_hieu() -> None:
    assert detect_combo_signals(_combo_frame([9.8], [10.5], [0.5]))["signal"].tolist() == [0]  # nến đầu không có nến trước
    assert detect_combo_signals(_combo_frame([9.0, 9.8], [9.5, 10.5], [0.5, 0.5], ma=[10.0, np.nan]))["signal"].tolist() == [0, 0]  # chưa đủ nến cho ma
    assert detect_combo_signals(_combo_frame([9.0, 9.8], [9.5, 10.5], [0.5, np.nan]))["signal"].tolist() == [0, 0]  # chưa có Histogram
    assert detect_combo_signals(_combo_frame([9.0, np.nan], [9.5, 10.5], [0.5, 0.5]))["signal"].tolist() == [0, 0]  # thiếu giá mở cửa


def test_combo_cat_len_co_hai_nhanh_nen_tu_cat_trong_than_nen_hoac_cat_that_giua_hai_lan_dong_cua() -> None:
    # nhánh open < ma: nến trước đóng trên ma (10,2) nhưng nến này mở dưới ma (9,8) rồi đóng trên ma (10,5): nến tự cắt lên
    assert detect_combo_signals(_combo_frame([10.0, 9.8], [10.2, 10.5], [0.5, 0.5]))["signal"].tolist() == [0, 1]
    # nhánh prev_close < prev_ma: nến này mở trên ma nên không phải nhánh trên, nhưng nến trước đóng dưới ma CỦA CHÍNH NÓ; ma dốc xuống nên close nến trước đã nằm TRÊN ma hiện tại (ca HK50 10/08/2022 10:30, luật cũ bỏ lỡ)
    hk50 = _combo_frame([19636.5, 19645.0], [19644.5, 19650.5], [30.3, 27.6], ma=[19646.675, 19630.875])
    assert detect_combo_signals(hk50)["signal"].tolist() == [0, 1]
    # không nhánh nào: nến trước đóng trên ma của nó và nến này mở trên ma
    assert detect_combo_signals(_combo_frame([10.0, 10.3], [10.2, 10.5], [0.5, 0.5]))["signal"].tolist() == [0, 0]


def test_combo_cat_xuong_doi_xung_hai_nhanh() -> None:
    # nhánh open > ma: nến trước đóng dưới ma (9,8) nhưng nến này mở trên ma (10,2) rồi đóng dưới ma (9,5)
    assert detect_combo_signals(_combo_frame([10.0, 10.2], [9.8, 9.5], [-0.5, -0.5]))["signal"].tolist() == [0, -1]
    # nhánh prev_close > prev_ma (ca US30 15/08/2022 02:30, luật cũ bỏ lỡ): ma dốc lên nên close nến trước (33686) đã nằm DƯỚI ma hiện tại (33689,45) nhưng vẫn TRÊN ma của nến trước (33682,85); nến này cũng mở dưới ma
    us30 = _combo_frame([33726.0, 33686.0], [33686.0, 33682.0], [-13.1, -14.7], ma=[33682.85, 33689.45])
    assert detect_combo_signals(us30)["signal"].tolist() == [0, -1]
    # không nhánh nào: nến trước đóng dưới ma của nó và nến này mở dưới ma
    assert detect_combo_signals(_combo_frame([10.0, 9.7], [9.8, 9.5], [-0.5, -0.5]))["signal"].tolist() == [0, 0]


def test_combo_nhan_ra_nen_cat_that_thi_khong_con_mat_tin_hieu_ke_tiep_vi_xen_ke() -> None:
    # nến 1: BUY; nến 3: ma vọt lên 11,2 nên nến giảm vừa cắt xuống chỉ đúng nhánh prev_close > prev_ma (SELL; luật cũ bỏ lỡ); nến 4: BUY đổi chiều so với SELL nên được giữ
    frame = _combo_frame([9.0, 9.8, 10.6, 10.9, 10.9], [9.5, 10.5, 10.9, 10.7, 11.5], [0.5, 0.5, 0.5, -0.5, 0.5], ma=[10.0, 10.0, 10.0, 11.2, 11.2])
    assert detect_combo_signals(frame)["signal"].tolist() == [0, 1, 0, -1, 1]  # luật cũ: [0, 1, 0, 0, 0] vì mất SELL nên BUY nến 4 bị coi là BUY lặp


def test_combo_xen_ke_chi_giu_setup_dau_tien_moi_khi_doi_chieu_va_nen_khong_setup_khong_doi_trang_thai() -> None:
    # nến 1: BUY; 2: không setup (giảm vừa cắt xuống nhưng Histogram dương); 3: BUY thứ hai cùng chiều -> bỏ; 4: SELL (đổi chiều) -> giữ; 5: không setup; 6: SELL thứ hai -> bỏ; 7: BUY (đổi chiều) -> giữ
    frame = _combo_frame([9.0, 9.5, 10.5, 9.5, 10.5, 9.5, 10.5, 9.5], [9.5, 10.5, 9.5, 10.5, 9.5, 10.5, 9.5, 10.5], [0.5, 0.5, 0.5, 0.5, -0.5, -0.5, -0.5, 0.5])
    out = detect_combo_signals(frame)
    assert out["signal"].tolist() == [0, 1, 0, 0, -1, 0, 0, 1]
    assert out["signal_reason"].tolist() == ["", COMBO_BUY_REASON, "", "", COMBO_SELL_REASON, "", "", COMBO_BUY_REASON]  # setup bị bỏ vì cùng chiều không có lý do


def test_combo_xen_ke_setup_dau_bang_luon_thanh_tin_hieu_du_dang_giua_mot_pha() -> None:
    # bảng bắt đầu giữa một pha BUY: setup BUY đầu tiên thấy được vẫn là tín hiệu (không có setup nào trước nó trong bảng), setup BUY sau đó bị bỏ
    frame = _combo_frame([9.0, 9.5, 10.5, 9.5], [9.5, 10.5, 9.5, 10.5], [0.5, 0.5, 0.5, 0.5])
    assert detect_combo_signals(frame)["signal"].tolist() == [0, 1, 0, 0]


def test_combo_khong_sua_input_va_ra_dung_cot_kieu() -> None:
    frame = _combo_frame([9.0, 9.8], [9.5, 10.5], [0.5, 0.5])
    before = frame.copy()
    out = detect_combo_signals(frame)
    assert list(out.columns) == [*before.columns, "signal", "signal_reason"]
    assert str(out["signal"].dtype) == "int64"
    pd.testing.assert_frame_equal(frame, before)


def test_combo_tren_bo_nen_that_khop_phep_tinh_doc_lap() -> None:
    bars = make_bars(600, freq="1h")
    ma = bars["close"].rolling(20).mean()
    hist = indicator.macd_hist(bars["close"], fast=5, slow=25, signal=5)
    out = detect_combo_signals(bars.assign(ma=ma, macd_h=hist))
    prev, prev_ma = bars["close"].shift(1), ma.shift(1)
    buy = (bars["close"] > bars["open"]) & (bars["close"] > ma) & (hist > 0) & ((bars["open"] < ma) | (prev < prev_ma))
    sell = (bars["close"] < bars["open"]) & (bars["close"] < ma) & (hist < 0) & ((bars["open"] > ma) | (prev > prev_ma))
    setups = np.where(buy, 1, np.where(sell, -1, 0))
    expected, state = [], 0  # máy trạng thái xen kẽ viết tay: chỉ setup đổi chiều so với setup gần nhất trước đó mới thành tín hiệu
    for setup in setups:
        expected.append(int(setup) if setup != 0 and setup != state else 0)
        state = int(setup) if setup != 0 else state
    assert out["signal"].tolist() == expected
    assert (np.abs(setups) > 0).sum() > (np.abs(out["signal"]) > 0).sum()  # bộ nến có setup cùng chiều liên tiếp nên lọc xen kẽ có bỏ bớt
    assert (out["signal"] == 1).sum() >= 1 and (out["signal"] == -1).sum() >= 1  # bộ nến có dao động nên có cả hai chiều
    fired = out.loc[out["signal"] != 0, "signal"].tolist()
    assert len(fired) >= 3 and all(a != b for a, b in pairwise(fired))  # tín hiệu xen kẽ BUY, SELL, BUY, SELL


def test_ma_cross_detect_chi_them_hai_cot_khong_mang_cot_chi_bao_va_khong_sua_input() -> None:
    bars = make_bars(300, freq="10min")
    before = bars.copy()
    out = mac_strategy.detect(bars, **MA_CROSS_PARAMS)
    assert list(out.columns) == [*before.columns, "signal", "signal_reason"] and str(out["signal"].dtype) == "int64"
    pd.testing.assert_frame_equal(bars, before)


def test_ma_cross_detect_tham_so_vo_ly_hoac_thieu_thi_loi() -> None:
    bars = make_bars(60, freq="10min")
    with pytest.raises(ValueError, match=r"fast_ma \(34\) must be smaller than slow_ma \(34\)"):
        mac_strategy.detect(bars, **{**MA_CROSS_PARAMS, "fast_ma": 34})
    with pytest.raises(ValueError, match="macd_fast"):
        mac_strategy.detect(bars, **{**MA_CROSS_PARAMS, "macd_fast": 25})
    with pytest.raises(TypeError):
        mac_strategy.detect(bars, fast_ma=13, slow_ma=34)  # type: ignore[call-arg]


def test_combo_detect_tinh_ma_va_histogram_roi_ap_luat_va_khong_mang_cot_chi_bao() -> None:
    bars = make_bars(600, freq="1h")
    before = bars.copy()
    out = combo_strategy.detect(bars, **COMBO_PARAMS)
    ma = bars["close"].rolling(20).mean()
    hist = indicator.macd_hist(bars["close"], fast=5, slow=25, signal=5)
    prev, prev_ma = bars["close"].shift(1), ma.shift(1)
    buy = (bars["close"] > bars["open"]) & (bars["close"] > ma) & (hist > 0) & ((bars["open"] < ma) | (prev < prev_ma))
    sell = (bars["close"] < bars["open"]) & (bars["close"] < ma) & (hist < 0) & ((bars["open"] > ma) | (prev > prev_ma))
    setups = np.where(buy, 1, np.where(sell, -1, 0))
    expected, state = [], 0
    for setup in setups:
        expected.append(int(setup) if setup != 0 and setup != state else 0)
        state = int(setup) if setup != 0 else state
    assert out["signal"].tolist() == expected  # setup thô qua điều kiện xen kẽ
    assert list(out.columns) == [*before.columns, "signal", "signal_reason"]
    pd.testing.assert_frame_equal(bars, before)
    with pytest.raises(ValueError, match="macd_fast"):
        combo_strategy.detect(bars, **{**COMBO_PARAMS, "macd_fast": 25})
    with pytest.raises(TypeError):
        combo_strategy.detect(bars, ma_period=20)  # type: ignore[call-arg]
