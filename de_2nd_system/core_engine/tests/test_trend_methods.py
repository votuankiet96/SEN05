"""Test de_module/trend.py (các YẾU TỐ trend tách biệt): ``bias_macd``, ``bias_sma``, ``bias_break`` — hermetic, thuần pandas/numpy.

Mỗi hàm nhận nến của khung của nó và trả hướng (+1, -1, 0) của từng nến. Tham số không có giá trị mặc định (keyword-only): thiếu là TypeError, tham số vô lý là ValueError.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from engine_support import make_bars

from core_engine.src.de_module import trend


def _closes(close: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"bartime": pd.date_range("2026-01-01", periods=len(close), freq="30min"), "close": close})


def _range(high: list[float], low: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"bartime": pd.date_range("2026-01-01", periods=len(high), freq="30min"), "high": high, "low": low})


# =============================================================================
# 1. bias_macd: dấu của MACD Histogram.
# =============================================================================


def test_macd_khop_phep_tinh_doc_lap_tren_bo_nen_that() -> None:
    bars = make_bars(400, freq="30min")
    close = bars["close"]
    ema = lambda series, period: series.ewm(span=period, adjust=False).mean()
    macd = ema(close, 5) - ema(close, 25)
    hist = macd - ema(macd, 5)
    expected = np.where(hist > 0, 1, np.where(hist < 0, -1, 0))
    result = trend.bias_macd(bars, fast=5, slow=25, signal=5)
    assert result.tolist() == expected.tolist() and result.dtype == np.int64 and len(result) == len(bars)
    assert set(result.tolist()) == {-1, 0, 1}  # nến đầu có Histogram đúng bằng 0, còn lại dao động hai phía


def test_macd_tham_so_vo_ly_hoac_thieu_thi_loi() -> None:
    with pytest.raises(ValueError, match=r"fast \(25\) must be smaller than slow \(25\)"):
        trend.bias_macd(_closes([100.0] * 30), fast=25, slow=25, signal=5)
    with pytest.raises(TypeError):
        trend.bias_macd(_closes([100.0] * 30), fast=5, slow=25)  # type: ignore[call-arg]


# =============================================================================
# 2. bias_sma: close so với SMA.
# =============================================================================


def test_sma_tren_la_cong_mot_duoi_la_tru_mot_bang_hoac_chua_du_nen_la_khong() -> None:
    frame = _closes([10.0, 10.0, 10.0, 12.0, 12.0, 12.0, 8.0, 8.0, 8.0])
    assert trend.bias_sma(frame, period=3).tolist() == [0, 0, 0, 1, 1, 0, -1, -1, 0]  # hai nến đầu chưa đủ 3 nến; close bằng SMA ở nến 2, 5 và 8


def test_sma_khop_phep_tinh_doc_lap_va_khong_sua_input() -> None:
    bars = make_bars(400, freq="30min")
    before = bars.copy()
    close = bars["close"]
    average = close.rolling(20).mean()
    result = trend.bias_sma(bars, period=20)
    assert result.tolist() == np.where(close > average, 1, np.where(close < average, -1, 0)).tolist() and result.dtype == np.int64
    assert set(result.tolist()) == {-1, 0, 1} and (result[:19] == 0).all()
    pd.testing.assert_frame_equal(bars, before)


# =============================================================================
# 3. bias_break: chiều của lần phá gần nhất, theo High/Low của các nến trước.
# =============================================================================


def test_break_chieu_cua_lan_pha_gan_nhat_giu_tu_khi_pha_nguoc() -> None:
    frame = _range(high=[10, 11, 10.5, 12, 11, 10], low=[9, 10, 9.5, 11, 10, 8])
    # lookback 2: nến 3 có High 12 > High cao nhất của hai nến trước (11) nên phá lên; nến 4 không phá gì nên giữ +1; nến 5 có Low 8 < Low thấp nhất của hai nến trước (10) nên phá xuống
    assert trend.bias_break(frame, lookback=2).tolist() == [0, 0, 0, 1, 1, -1]


def test_break_nen_pha_ca_hai_phia_thi_giu_chieu_cu_va_bang_dinh_khong_phai_la_pha() -> None:
    outside = _range(high=[10, 11, 10.5, 12, 11, 10, 20], low=[9, 10, 9.5, 11, 10, 8, 1])
    assert trend.bias_break(outside, lookback=2).tolist() == [0, 0, 0, 1, 1, -1, -1]  # nến 6 vừa vượt đỉnh vừa thủng đáy: giữ -1
    assert trend.bias_break(_range(high=[10, 10], low=[9, 9]), lookback=1).tolist() == [0, 0]  # High bằng đỉnh, Low bằng đáy: chưa phá


def test_break_khop_phep_tinh_doc_lap_tren_bo_nen_that() -> None:
    bars = make_bars(400, freq="30min")
    before = bars.copy()
    lookback = 20
    expected, direction = [], 0
    for i in range(len(bars)):
        if i >= lookback:
            top, bottom = bars["high"].iloc[i - lookback : i].max(), bars["low"].iloc[i - lookback : i].min()
            up, down = bars["high"].iloc[i] > top, bars["low"].iloc[i] < bottom
            direction = 1 if up and not down else -1 if down and not up else direction
        expected.append(direction)
    result = trend.bias_break(bars, lookback=lookback)
    assert result.tolist() == expected and result.dtype == np.int64
    assert set(result.tolist()) == {-1, 0, 1} and (result[:lookback] == 0).all()
    pd.testing.assert_frame_equal(bars, before)
