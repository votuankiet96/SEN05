"""Nhớ đệm của configuration (enable_memo): kết quả y hệt khi tắt, khoá theo NỘI DUNG, không làm hỏng bản đã nhớ, tắt mặc định,
và khoá hai giả định về tham số mà khoá nhớ đệm dựa vào — hermetic, KHÔNG nối Redis."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from core_engine.src import strategy_runner
from core_engine.src.configuration import STRATEGIES
from core_engine.src.strategy_runner import run_strategy


def _bars(n: int = 600, freq: str = "h") -> pd.DataFrame:
    """Bộ nến tất định dao động quanh một mức cố định (cùng dạng range_bars ở test_sources)."""
    i = np.arange(n, dtype=float)
    base = 1000.0 + 30.0 * np.sin(i / 9.0) + 50.0 * np.sin(i / 29.0) + 40.0 * np.sin(i / 71.0 + 1.0) + 6.0 * np.sin(i / 3.1)
    op = base + 0.8 * np.sin(i / 3.3)
    cl = base - 0.8 * np.sin(i / 3.3)
    return pd.DataFrame(
        {
            "bartime": pd.date_range("2026-01-01", periods=n, freq=freq).astype("datetime64[us]"),
            "open": op,
            "high": np.maximum(op, cl) + 2.0 + np.abs(np.sin(i / 5.0)),
            "low": np.minimum(op, cl) - 2.0 - np.abs(np.cos(i / 4.0)),
            "close": cl,
            "volume": np.nan,
        }
    )


def _aggregate(bars: pd.DataFrame, n: int) -> pd.DataFrame:
    """Gộp mỗi n nến thành 1 nến khung lớn (bỏ nhóm cuối nếu chưa đủ n nến)."""
    group = np.arange(len(bars)) // n
    out = bars.groupby(group).agg(
        bartime=("bartime", "first"), open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"), volume=("volume", "sum")
    )
    return out[out.index < group.max()].reset_index(drop=True)


# (chiến lược, khung vào lệnh hợp lệ của nó)
CASES = [("combo", "H1"), ("ma_cross", "M30"), ("ema_reach", "M30"), ("macd_break", "M30"), ("ichimoku", "H1")]
TREND_CASES = [case for case in CASES if case[0] != "ichimoku"]  # ichimoku không có trend


def _freq(tf: str) -> str:
    return "30min" if tf == "M30" else "h"


def _trend_inputs(method: str, tf: str, bars: pd.DataFrame) -> tuple[str, pd.DataFrame]:
    """(khung trend, nến trend) hợp lệ cho từng khối: breakout = chính khung vào lệnh, cloud = không nhỏ hơn H1, còn lại H4."""
    per_h1 = 2 if tf == "M30" else 1
    if method == "breakout":
        return tf, bars
    if method == "cloud":
        return "H1", bars if tf == "H1" else _aggregate(bars, per_h1)
    return "H4", _aggregate(bars, 4 * per_h1)


@pytest.fixture(autouse=True)
def _tat_nho_dem_truoc_va_sau_moi_test():
    strategy_runner.disable_memo()
    yield
    strategy_runner.disable_memo()


def test_nho_dem_tat_mac_dinh() -> None:
    assert strategy_runner.memo_stats() == {}


@pytest.mark.parametrize(("key", "tf"), CASES)
def test_run_strategy_co_nho_dem_cho_ket_qua_y_het_khi_tat(key: str, tf: str) -> None:
    bars = _bars(600, _freq(tf))
    expected = run_strategy(key, symbol="US30", tf=tf, bars=bars)
    strategy_runner.enable_memo(8)
    first = run_strategy(key, symbol="US30", tf=tf, bars=bars)
    second = run_strategy(key, symbol="US30", tf=tf, bars=bars)  # lần này lấy từ nhớ đệm
    assert_frame_equal(first, expected)
    assert_frame_equal(second, expected)
    assert strategy_runner.memo_stats() == {"hits": 1, "misses": 1, "size": 1}


def test_nho_dem_khoa_theo_noi_dung() -> None:
    strategy_runner.enable_memo(16)
    bars = _bars(600)
    run_strategy("combo", symbol="US30", tf="H1", bars=bars)
    run_strategy("combo", symbol="US30", tf="H1", bars=bars.copy())  # cùng nội dung, đối tượng khác -> trúng
    assert strategy_runner.memo_stats()["hits"] == 1
    changed = bars.copy()
    changed.loc[300, "close"] += 0.01  # đổi MỘT giá -> trượt
    run_strategy("combo", symbol="US30", tf="H1", bars=changed)
    assert strategy_runner.memo_stats()["misses"] == 2
    run_strategy("combo", symbol="US30", tf="H1", bars=bars, overrides={"MA_PERIOD": 21})  # đổi tham số của chiến lược -> trượt
    assert strategy_runner.memo_stats()["misses"] == 3
    run_strategy("ma_cross", symbol="US30", tf="M30", bars=_bars(600, "30min"))  # chiến lược khác -> khoá khác
    assert strategy_runner.memo_stats()["misses"] == 4


def test_nho_dem_tra_ban_sao_nguoi_goi_khong_lam_hong_duoc() -> None:
    memo = strategy_runner._FrameMemo(4)
    source = pd.DataFrame({"a": [1.0, 2.0, 3.0]})
    first = memo.get_or_compute(("k",), lambda: source)
    first["a"] = -1.0  # người gọi sửa kết quả nhận được
    again = memo.get_or_compute(("k",), lambda: pytest.fail("không được tính lại"))
    assert_frame_equal(again, pd.DataFrame({"a": [1.0, 2.0, 3.0]}))


def test_nho_dem_gioi_han_kich_thuoc_va_bo_cai_dung_lau_nhat() -> None:
    memo = strategy_runner._FrameMemo(2)
    for i in range(5):
        memo.get_or_compute((i,), lambda i=i: pd.DataFrame({"a": [i]}))
    assert list(memo.items) == [(3,), (4,)]


@pytest.mark.parametrize(("key", "tf"), CASES)
def test_chi_bao_khong_doc_khoa_trend(key: str, tf: str) -> None:
    """Khoá nhớ đệm của chỉ báo bỏ các khoá TREND*: test này khoá giả định đó (chỉ báo không được đọc chúng)."""
    spec = STRATEGIES[key]
    bars = _bars(600, _freq(tf))
    params = spec.normalize_params(None, "US30", tf)
    noisy = {**params, "TREND_TF": "H4", "TREND_METHODS": ("knn", "zigzag"), "TREND_FILTER_ENABLED": True, "TREND_FOO": 1}
    assert_frame_equal(spec.add_indicators(bars, params), spec.add_indicators(bars, noisy))
