"""Test characterization cho wes (bản mô phỏng của OG) — hermetic, KHÔNG đụng Redis.

Mọi giá trị mong đợi trong file này được tính từ CHÍNH pipeline thật trên bộ
nến tổng hợp tất định bên dưới (không phải số bịa). Mục đích: khoá lại hành
vi hiện tại để phát hiện thay đổi logic ngoài ý muốn. Khi sửa logic chiến
lược CÓ CHỦ ĐÍCH, phải chạy lại pipeline để tính giá trị mới rồi cập nhật
test — không tự đoán số.

Cần wes_config.yaml tồn tại (hệ thống chỉ chạy trên vm-og, luôn có sẵn file
thật). KHÔNG mở kết nối Redis nào.
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import types
from itertools import pairwise

import numpy as np
import pandas as pd
import pytest
from wisdom_engine.wes.src.configuration import (
    COMBO_TREND_TIMEFRAMES,
    STRATEGIES,
    alternating_filter_for,
    get_strategy,
    normalize_combo_params,
    normalize_ma_cross_params,
    normalize_macd_break_params,
    run_strategy,
    run_strategy_with_trend_reference,
    trend_reference_for,
)
from wisdom_engine.wes.src.indicator import atr, rma, sma
from wisdom_engine.wes.src.redis_io.candle_reader import (
    candle_key,
    normalize_redis_config,
    pair_list_key,
    parse_pair_key,
)
from wisdom_engine.wes.src.redis_io.live_worker import (
    signal_valid_from,
    strategies_for_timeframe,
    timeframe_duration,
)
from wisdom_engine.wes.src.redis_io.signal_publisher import (
    build_signal,
    channel_name,
    clear_signals,
    og_list_key,
    publish_signal,
    signal_message,
    stamp_hash_key,
    strategy_token,
    to_hash_fields,
)

# =============================================================================
# Bộ nến tổng hợp tất định — không random, nên mọi lần chạy cho cùng kết quả.
# =============================================================================


def synthetic_bars(n: int = 400) -> pd.DataFrame:
    """Sin nhiều chu kỳ + trend nhẹ: đủ để sinh CẢ BUY và SELL cho 2 chiến lược."""
    i = np.arange(n, dtype=float)
    base = 1000.0 + 40.0 * np.sin(i / 9.0) + 12.0 * np.sin(i / 2.7) + 0.35 * i
    op = base + 0.8 * np.sin(i / 3.3)
    cl = base - 0.8 * np.sin(i / 3.3)
    hi = np.maximum(op, cl) + 2.0 + np.abs(np.sin(i / 5.0))
    lo = np.minimum(op, cl) - 2.0 - np.abs(np.cos(i / 4.0))
    return pd.DataFrame(
        {
            "bartime": pd.date_range("2026-01-01", periods=n, freq="h").astype("datetime64[us]"),
            "open": op,
            "high": hi,
            "low": lo,
            "close": cl,
            "volume": np.nan,
        }
    )


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    return synthetic_bars()


def range_bars(n: int = 600) -> pd.DataFrame:
    """Dao động quanh một mức cố định, KHÔNG có trend: bộ nến synthetic_bars() có trend tăng nên macd_break
    (phá đỉnh/đáy 100 nến) trên đó chỉ ra BUY; bộ này sinh được cả BUY lẫn SELL."""
    i = np.arange(n, dtype=float)
    base = (
        1000.0
        + 30.0 * np.sin(i / 9.0)
        + 50.0 * np.sin(i / 29.0)
        + 40.0 * np.sin(i / 71.0 + 1.0)
        + 6.0 * np.sin(i / 3.1)
    )
    op = base + 0.8 * np.sin(i / 3.3)
    cl = base - 0.8 * np.sin(i / 3.3)
    hi = np.maximum(op, cl) + 2.0 + np.abs(np.sin(i / 5.0))
    lo = np.minimum(op, cl) - 2.0 - np.abs(np.cos(i / 4.0))
    return pd.DataFrame(
        {
            "bartime": pd.date_range("2026-01-01", periods=n, freq="h").astype("datetime64[us]"),
            "open": op,
            "high": hi,
            "low": lo,
            "close": cl,
            "volume": np.nan,
        }
    )


@pytest.fixture(scope="module")
def rbars() -> pd.DataFrame:
    return range_bars()


def _pipeline_digest(df: pd.DataFrame) -> str:
    cols = ["signal", "entry_price", "atr"]
    return hashlib.sha256(df[cols].to_csv(index=False, float_format="%.8f").encode()).hexdigest()


# =============================================================================
# 1. Golden lock: toàn bộ pipeline 1 lượt chạy.
# =============================================================================

# Digest tính từ pipeline thật trên synthetic_bars() ở trên, 3 cột
# signal/entry_price/atr. Tính lại 2026-10-04 khi bỏ SL/TP khỏi OG: digest 3
# cột này từ code TRƯỚC khi bỏ và SAU khi bỏ trùng khớp tuyệt đối, tức việc
# bỏ SL/TP không đổi tín hiệu/entry. Đổi digest = đổi tín hiệu/entry -> phải
# là thay đổi CÓ CHỦ ĐÍCH. Combo tính lại 2026-10-11 khi khôi phục lọc lặp cùng chiều
# (alternating.py): trước đó 2 BUY / 3 SELL, digest 1fb2c847...; bar tín hiệu đầu
# tiên của mỗi chiều không đổi, ma_cross không đổi.
GOLDEN = {
    "combo": {
        "tf": "H1",
        "n_buy": 1,
        "n_sell": 1,
        "first_buy_idx": 219,
        "first_sell_idx": 24,
        "digest": "ee5a702a093952796d962bfa5b38c1c58bf059357a14986e5570b312029d2430",
    },
    "ma_cross": {
        "tf": "M30",
        "n_buy": 7,
        "n_sell": 6,
        "first_buy_idx": 57,
        "first_sell_idx": 86,
        "digest": "d456518f582604979c92887d6faf7ee321845e9d1c76c4af04c40b7ffcf6d6b8",
    },
}


@pytest.mark.parametrize("strategy", sorted(GOLDEN))
def test_pipeline_golden(bars: pd.DataFrame, strategy: str) -> None:
    want = GOLDEN[strategy]
    df = run_strategy(strategy, symbol="US30", tf=want["tf"], bars=bars)
    sig = df["signal"].astype(int)

    assert int((sig == 1).sum()) == want["n_buy"]
    assert int((sig == -1).sum()) == want["n_sell"]
    assert int(sig[sig == 1].index[0]) == want["first_buy_idx"]
    assert int(sig[sig == -1].index[0]) == want["first_sell_idx"]
    assert _pipeline_digest(df) == want["digest"]


def test_pipeline_khong_sua_input_tai_cho(bars: pd.DataFrame) -> None:
    before = bars.copy()
    run_strategy("combo", symbol="US30", tf="H1", bars=bars)
    pd.testing.assert_frame_equal(bars, before)


# =============================================================================
# 2. Trend filter — mặc định TẮT, bật lên thì phải tham chiếu trend thật.
# =============================================================================


def test_mac_dinh_trend_filter_phai_tat() -> None:
    """Mặc định vận hành: cả 3 chiến lược đều TẮT trend filter, nên luồng live
    chạy 1 timeframe y như trước. Nếu ai commit wes_config.yaml với true, test
    này đỏ — vì đó là thay đổi hành vi production, phải cố ý.
    """
    for key in ("combo", "ma_cross", "macd_break"):
        enabled, trend_tf = trend_reference_for(key)
        assert enabled is False, f"{key}: trend filter đang BẬT trong wes_config.yaml"
        assert trend_tf in COMBO_TREND_TIMEFRAMES  # H1..H4


def test_trend_tat_thi_signal_bang_raw_signal(bars: pd.DataFrame) -> None:
    for strategy, tf in (("combo", "H1"), ("ma_cross", "M30"), ("macd_break", "H1")):
        df = run_strategy(strategy, symbol="US30", tf=tf, bars=bars)
        pd.testing.assert_series_equal(
            df["signal"].astype(int), df["raw_signal"].astype(int), check_names=False
        )
        assert set(df["trend_filter_status"]) == {"disabled"}
        # risk_reward đã bỏ hẳn: không có công thức nào tính nó.
        assert "risk_reward" not in df.columns


# (strategy, entry_tf, trend_tf, bước lấy mẫu trend, raw, còn lại sau khi lọc)
# Số "còn lại" tính từ pipeline thật — khoá lại để đổi logic lọc là test đỏ.
TREND_CASES = [
    ("ma_cross", "M30", "H2", 2, 13, 12),
    ("ma_cross", "M30", "H3", 3, 13, 3),
    ("ma_cross", "M30", "H4", 4, 13, 0),
    ("combo", "H1", "H4", 4, 2, 0),  # raw 5 -> 2 khi khôi phục lọc lặp cùng chiều (2026-10-11)
]


@pytest.mark.parametrize("strategy,entry_tf,trend_tf,step,n_raw,n_kept", TREND_CASES)
def test_trend_bat_thi_chi_giu_tin_hieu_cung_chieu(
    bars: pd.DataFrame,
    strategy: str,
    entry_tf: str,
    trend_tf: str,
    step: int,
    n_raw: int,
    n_kept: int,
) -> None:
    """Chạy đường 2 timeframe thật (entry + trend dựng từ cùng bộ nến).

    Khoá 4 tính chất quan trọng nhất của bộ lọc:
      1. Mọi tín hiệu còn lại phải CÙNG CHIỀU trend_bias.
      2. Không lookahead: trend bar dùng để lọc phải ĐÃ ĐÓNG trước entry bar.
      3. Lọc chỉ làm GIẢM số tín hiệu, không bao giờ tạo thêm.
      4. Tín hiệu bị lọc phải được ghi nhãn rõ, không âm thầm biến mất.
    """
    trend_bars = bars.iloc[::step].reset_index(drop=True)  # giả lập khung lớn hơn
    off = run_strategy(strategy, symbol="US30", tf=entry_tf, bars=bars)
    on = run_strategy_with_trend_reference(
        strategy,
        symbol="US30",
        entry_tf=entry_tf,
        entry_bars=bars,
        trend_tf=trend_tf,
        trend_bars=trend_bars,
    )

    assert {"trend_bias", "trend_ai_knn", "trend_ai_avg", "trend_close_time"} <= set(on.columns)

    # Bộ lọc không được đụng tới raw_signal — chỉ quyết định giữ hay bỏ.
    assert int((on["raw_signal"].astype(int) != 0).sum()) == n_raw
    assert int((off["signal"].astype(int) != 0).sum()) == n_raw

    kept = on[on["signal"].astype(int) != 0]
    assert len(kept) == n_kept
    assert len(kept) <= n_raw
    assert (kept["signal"].astype(int) == kept["trend_bias"].astype(int)).all()
    assert (kept["trend_close_time"] <= kept["bartime"]).all()
    assert set(on.loc[on["raw_signal"].astype(int) != 0, "trend_filter_status"]) <= {
        "aligned",
        "filtered",
        "neutral_trend",
    }
    if n_kept:
        assert set(kept["signal_reason"].str.endswith("; aligned with trend reference")) == {True}


def test_trend_bat_it_nhat_mot_cap_con_giu_tin_hieu(bars: pd.DataFrame) -> None:
    """Chốt rằng bộ lọc KHÔNG phải là "chặn hết" — có cặp thật giữ lại tín
    hiệu. Nếu một thay đổi nào đó làm mọi tín hiệu bị lọc sạch, test này đỏ.
    """
    assert any(n_kept > 0 for *_, n_kept in TREND_CASES)


def test_trend_bat_ma_thieu_cot_trend_thi_raise(bars: pd.DataFrame) -> None:
    """run_strategy() (1 timeframe) không bao giờ sinh trend_bias. Bật trend
    filter trên đường đó phải RAISE rõ ràng chứ không âm thầm bỏ qua bộ lọc —
    live_worker dựa vào điều này để không publish tín hiệu chưa lọc.
    """
    with pytest.raises(ValueError, match="trend_bias"):
        run_strategy(
            "combo", symbol="US30", tf="H1", bars=bars, overrides={"TREND_FILTER_ENABLED": True}
        )


def test_trend_type_chi_chap_nhan_knn() -> None:
    with pytest.raises(ValueError, match="TREND_TYPE must be 'knn'"):
        normalize_combo_params({"TREND_TYPE": "ema"}, "US30", "H1")


def test_trend_tf_ngoai_danh_sach_bi_chan() -> None:
    with pytest.raises(ValueError, match="TREND_TF must be one of"):
        normalize_combo_params({"TREND_TF": "M5"}, "US30", "H1")


def test_strategies_gom_combo_ma_cross_va_macd_break() -> None:
    assert sorted(STRATEGIES) == ["combo", "ma_cross", "macd_break"]


# =============================================================================
# 3. signal_reason -> field `comment` của DB1, OF đọc nguyên văn.
# =============================================================================

REASONS = {
    ("combo", 1): "bullish candle crossing above MA, MACD histogram > 0",
    ("combo", -1): "bearish candle crossing below MA, MACD histogram < 0",
    ("ma_cross", 1): "fast SMA crossed above slow SMA; MACD histogram > 0",
    ("ma_cross", -1): "fast SMA crossed below slow SMA; MACD histogram < 0",
}


@pytest.mark.parametrize("strategy,tf", [("combo", "H1"), ("ma_cross", "M30")])
def test_signal_reason_dung_nguyen_van(bars: pd.DataFrame, strategy: str, tf: str) -> None:
    df = run_strategy(strategy, symbol="US30", tf=tf, bars=bars)
    sig = df["signal"].astype(int)
    for side in (1, -1):
        rows = df[sig == side]
        assert not rows.empty, f"{strategy} không sinh được tín hiệu {side}"
        assert set(rows["signal_reason"]) == {REASONS[(strategy, side)]}
    # Dòng không có tín hiệu thì reason phải rỗng.
    assert set(df[sig == 0]["signal_reason"]) <= {""}


# =============================================================================
# 4. Entry. OG không còn SL/TP (OF tự tính từ KSL/KTP của họ, 2026-10-04).
# =============================================================================


@pytest.mark.parametrize("strategy,tf", [("combo", "H1"), ("ma_cross", "M30"), ("macd_break", "H1")])
def test_og_khong_con_cot_sl_tp(bars: pd.DataFrame, strategy: str, tf: str) -> None:
    df = run_strategy(strategy, symbol="US30", tf=tf, bars=bars)
    assert not {"sl_price", "tp_price", "ksl", "ktp"} & set(df.columns)


def _alternating(setups: str) -> list[int]:
    """"B"/"S" = bar có setup BUY/SELL, "." = không có setup -> signal sau lọc xen kẽ."""
    from wisdom_engine.wes.src.alternating import alternating_signals

    buy = pd.Series([c == "B" for c in setups])
    sell = pd.Series([c == "S" for c in setups])
    return alternating_signals(buy, sell).tolist()


def test_alternating_chi_ban_tin_hieu_dau_khi_doi_chieu() -> None:
    assert _alternating("BBSSBBS") == [1, 0, -1, 0, 1, 0, -1]
    assert _alternating("B..B..S.S") == [1, 0, 0, 0, 0, 0, -1, 0, 0]  # bar không setup không đổi trạng thái
    assert _alternating("SSB") == [-1, 0, 1]  # chiều đầu tiên của cửa sổ luôn bắn
    assert _alternating("......") == [0] * 6


def test_alternating_coi_nan_la_khong_co_setup() -> None:
    from wisdom_engine.wes.src.alternating import alternating_signals

    buy = pd.Series([True, float("nan"), True], dtype="object")
    sell = pd.Series([False, False, False])
    assert alternating_signals(buy, sell).tolist() == [1, 0, 0]


def test_apply_alternating_filter_bat_tat_theo_co() -> None:
    from wisdom_engine.wes.src.alternating import (
        alternating_filter_enabled,
        apply_alternating_filter,
    )

    setups = "BBSSB.S"
    buy = pd.Series([c == "B" for c in setups], index=pd.RangeIndex(10, 10 + len(setups)))
    sell = pd.Series([c == "S" for c in setups], index=buy.index)

    on = apply_alternating_filter(buy, sell, {"ALTERNATING_FILTER_ENABLED": True})
    off = apply_alternating_filter(buy, sell, {"ALTERNATING_FILTER_ENABLED": False})
    assert on.tolist() == [1, 0, -1, 0, 1, 0, -1]
    assert off.tolist() == [1, 1, -1, -1, 1, 0, -1]  # tắt: bar nào có setup cũng bắn
    assert on.index.equals(buy.index) and off.index.equals(buy.index)
    assert str(on.dtype) == str(off.dtype) == "int64"
    # thiếu key / thiếu params = tắt (cùng quy ước với trend_filter_enabled)
    assert apply_alternating_filter(buy, sell, {}).tolist() == off.tolist()
    assert apply_alternating_filter(buy, sell, None).tolist() == off.tolist()
    for text, want in (("true", True), ("1", True), ("on", True), ("false", False), ("0", False), ("", False)):
        assert alternating_filter_enabled({"ALTERNATING_FILTER_ENABLED": text}) is want


def test_co_alternating_doc_tu_config_va_chi_co_o_combo_va_macd_break() -> None:
    from wisdom_engine.wes.src.configuration import (
        COMBO_DEFAULT_PARAMS,
        COMBO_PARAM_FIELDS,
        MA_CROSS_DEFAULT_PARAMS,
        MA_CROSS_PARAM_FIELDS,
        MACD_BREAK_DEFAULT_PARAMS,
        MACD_BREAK_PARAM_FIELDS,
    )

    assert alternating_filter_for("combo") is True
    assert alternating_filter_for("ma_cross") is None  # ma_cross không dùng bộ lọc này
    assert COMBO_DEFAULT_PARAMS["ALTERNATING_FILTER_ENABLED"] is True
    assert {"key": "ALTERNATING_FILTER_ENABLED", "label": "Alternating Filter", "type": "bool"} in COMBO_PARAM_FIELDS
    assert normalize_combo_params(None, "US30", "H1")["ALTERNATING_FILTER_ENABLED"] is True
    assert normalize_combo_params({"ALTERNATING_FILTER_ENABLED": "false"}, "US30", "H1")["ALTERNATING_FILTER_ENABLED"] is False
    assert "ALTERNATING_FILTER_ENABLED" not in MA_CROSS_DEFAULT_PARAMS
    assert all(field["key"] != "ALTERNATING_FILTER_ENABLED" for field in MA_CROSS_PARAM_FIELDS)
    # macd_break (đăng ký 2026-10-14) có cờ như combo, mặc định bật.
    assert alternating_filter_for("macd_break") is True
    assert MACD_BREAK_DEFAULT_PARAMS["ALTERNATING_FILTER_ENABLED"] is True
    assert {"key": "ALTERNATING_FILTER_ENABLED", "label": "Alternating Filter", "type": "bool"} in MACD_BREAK_PARAM_FIELDS
    assert normalize_macd_break_params(None, "US30", "H1")["ALTERNATING_FILTER_ENABLED"] is True
    assert (
        normalize_macd_break_params({"ALTERNATING_FILTER_ENABLED": "false"}, "US30", "H1")["ALTERNATING_FILTER_ENABLED"]
        is False
    )


def test_config_mau_khai_bao_co_alternating_cho_combo_va_macd_break() -> None:
    from pathlib import Path

    import yaml

    example = yaml.safe_load((Path(__file__).resolve().parents[1] / "wes_config.example.yaml").read_text(encoding="utf-8"))

    assert example["strategies"]["combo"]["alternating_filter_enabled"] is True
    assert "alternating_filter_enabled" not in example["strategies"]["ma_cross"]
    # macd_break: có mục cấu hình + được bật trong live.enabled_strategies của bản mẫu.
    assert example["strategies"]["macd_break"]["alternating_filter_enabled"] is True
    assert "macd_break" in example["live"]["enabled_strategies"]
    assert example["strategies"]["macd_break"]["supported_timeframes"] == ["M10", "M20", "M30", "M45", "H1", "H2", "H3", "H4"]


def test_combo_tat_co_giong_het_hanh_vi_truoc_khi_co_loc_lap_cung_chieu(bars: pd.DataFrame) -> None:
    """Cờ tắt phải cho đúng golden của combo trước 2026-10-11 (2 BUY / 3 SELL, digest 1fb2c847...)."""
    df = run_strategy("combo", symbol="US30", tf="H1", bars=bars, overrides={"ALTERNATING_FILTER_ENABLED": "false"})
    sig = df["signal"].astype(int)

    assert int((sig == 1).sum()) == 2
    assert int((sig == -1).sum()) == 3
    assert int(sig[sig == 1].index[0]) == GOLDEN["combo"]["first_buy_idx"]  # bar đầu mỗi chiều không đổi
    assert int(sig[sig == -1].index[0]) == GOLDEN["combo"]["first_sell_idx"]
    assert _pipeline_digest(df).startswith("1fb2c847")


def test_combo_signal_cuoi_xen_ke_va_chinh_xac_la_loc_cua_setup() -> None:
    """Tín hiệu liên tiếp luôn đổi hướng, và đúng bằng lọc xen kẽ (viết độc lập, không vòng lặp) áp lên
    mọi setup: chỉ bỏ tín hiệu lặp cùng chiều, không thêm/đổi gì khác."""
    df = run_strategy("combo", symbol="US30", tf="H1", bars=synthetic_bars(900))
    sequence = df.loc[df["signal"].astype(int) != 0, "signal"].astype(int).tolist()
    assert len(sequence) >= 5, "mẫu phải đủ tín hiệu để phép kiểm có nghĩa"
    assert all(a != b for a, b in pairwise(sequence))

    setups = run_strategy(
        "combo", symbol="US30", tf="H1", bars=synthetic_bars(900), overrides={"ALTERNATING_FILTER_ENABLED": "false"}
    )["raw_signal"].astype(int)
    last_setup = setups.where(setups != 0).ffill().shift(1)
    expected = setups.where((setups != 0) & (last_setup.isna() | (last_setup != setups)), 0)
    assert df["raw_signal"].astype(int).tolist() == expected.tolist()
    assert int((setups != 0).sum()) > int((df["raw_signal"].astype(int) != 0).sum())  # có tín hiệu lặp bị chặn thật


def test_combo_lock_xen_ke_dung_truoc_trend_filter() -> None:
    """Lọc xen kẽ chạy trên setup TRƯỚC, rồi trend filter mới lọc: signal ⊆ raw_signal. Hệ quả đã biết: setup
    bị trend loại vẫn chiếm lượt đầu tiên của hướng đó."""
    from wisdom_engine.wes.src.strategies.combo import detect_combo_signals

    spec = {
        "B": {"open": 10.0, "close": 11.0, "prev_close": 10.0, "macd_h": 1.0},
        "S": {"open": 11.0, "close": 10.0, "prev_close": 11.0, "macd_h": -1.0},
        ".": {"open": 10.0, "close": 10.0, "prev_close": 10.0, "macd_h": 0.0},
    }

    def frame(rows: str, bias: list[int]) -> pd.DataFrame:
        out = pd.DataFrame([spec[c] for c in rows])
        out["ma"] = 10.5
        out["bartime"] = pd.date_range("2026-01-01", periods=len(rows), freq="h")
        out["trend_bias"] = bias
        return out

    params = {"SESSION_HOURS_UTC": [], "TREND_FILTER_ENABLED": True, "ALTERNATING_FILTER_ENABLED": True}
    out = detect_combo_signals(frame(".B.B", [0, -1, 0, 1]), params=params)
    assert out["raw_signal"].tolist() == [0, 1, 0, 0] and out["signal"].tolist() == [0, 0, 0, 0]
    out = detect_combo_signals(frame(".BSB", [0, -1, -1, 1]), params=params)
    assert out["raw_signal"].tolist() == [0, 1, -1, 1] and out["signal"].tolist() == [0, 0, -1, 1]


def test_combo_entry_la_breakout_ngoai_bar(bars: pd.DataFrame) -> None:
    """Combo là lệnh STOP: entry BUY phải nằm TRÊN high, SELL DƯỚI low."""
    df = run_strategy("combo", symbol="US30", tf="H1", bars=bars)
    sig = df["signal"].astype(int)
    x = normalize_combo_params(None, "US30", "H1")["X"]
    buy, sell = df[sig == 1], df[sig == -1]
    np.testing.assert_allclose(buy["entry_price"], buy["high"] + x, rtol=1e-12)
    np.testing.assert_allclose(sell["entry_price"], sell["low"] - x, rtol=1e-12)


def test_ma_cross_entry_la_close(bars: pd.DataFrame) -> None:
    """MA Cross là lệnh MARKET: entry = close của bar tín hiệu."""
    df = run_strategy("ma_cross", symbol="US30", tf="M30", bars=bars)
    has = df[df["signal"].astype(int) != 0]
    np.testing.assert_allclose(has["entry_price"], has["close"], rtol=1e-12)


# =============================================================================
# 5. ATR/RMA phải seed bằng SMA(period) — khớp ta.atr/ta.rma của TradingView.
#    KHÔNG được thay bằng ewm(adjust=False) thuần (lệch 2.5-4% ở bar đầu).
# =============================================================================


def test_atr_seed_bang_sma_khong_phai_ewm(bars: pd.DataFrame) -> None:
    period = 14
    tr = pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - bars["close"].shift(1)).abs(),
            (bars["low"] - bars["close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)

    got = atr(bars, period)
    first = got.first_valid_index()
    # Giá trị đầu tiên xuất hiện ở bar thứ `period` (index period-1), và bằng
    # đúng SMA(period) của True Range — KHÔNG phải ewm seed bằng 1 điểm.
    assert first == period - 1
    assert got.loc[first] == pytest.approx(tr.iloc[:period].mean(), rel=1e-12)

    # Nếu ai đổi sang ewm(adjust=False) thuần, bar đầu sẽ lệch hẳn (đã đo:
    # period=14 lệch ~4.2%). Test này là cái chặn.
    naive = tr.ewm(alpha=1 / period, adjust=False).mean()
    assert got.loc[first] != pytest.approx(naive.loc[first], rel=1e-3)
    assert abs(got.loc[first] - naive.loc[first]) / got.loc[first] > 0.01


def test_rma_seed_bang_sma(bars: pd.DataFrame) -> None:
    period = 50
    got = rma(bars["close"], period)
    first = got.first_valid_index()
    assert got.loc[first] == pytest.approx(sma(bars["close"], period).loc[first], rel=1e-12)


# =============================================================================
# 7. Hợp đồng key Redis DB0/DB1.
# =============================================================================


def test_db0_key_symbol_truoc_timeframe() -> None:
    assert pair_list_key("L_DFS", "us30", "m5") == "L_DFS_US30_M5"
    assert candle_key("L_DFS_US30_M5", "2026-09-14 03:00:00") == (
        "L_DFS_US30_M5:2026-09-14 03:00:00"
    )


def test_parse_pair_key_loai_key_hash() -> None:
    assert parse_pair_key("L_DFS_US30_M5", "L_DFS") == ("US30", "M5")
    # key HASH có dấu ':' -> không phải pair (mốc giờ cũng chứa ':')
    assert parse_pair_key("L_DFS_US30_M5:2026-09-14 03:00:00", "L_DFS") is None
    assert parse_pair_key("OTHER_US30_M5", "L_DFS") is None
    assert parse_pair_key("L_DFS_US30", "L_DFS") is None


def test_db1_key_og_va_channel_theo_strategy_symbol_tf() -> None:
    """3 chiều, STRATEGY đứng đầu (chốt 2026-10-04): key DB1
    L_WES_{STRATEGY}_{SYMBOL}_{TF} và tên kênh Pub/Sub
    L_CHANNEL_{STRATEGY}_{SYMBOL}_{TF} (kênh chỉ là đường truyền, không lưu key).
    """
    key = og_list_key("L_WES", "combo", "us30", "h1")
    assert key == "L_WES_COMBO_US30_H1"
    assert stamp_hash_key(key, "2026-09-22 13:00:00") == "L_WES_COMBO_US30_H1:2026-09-22 13:00:00"
    assert channel_name("L_CHANNEL", "combo", "us30", "h1") == "L_CHANNEL_COMBO_US30_H1"
    # ma_cross -> MACROSS (bỏ '_' để token nào cũng không chứa ký tự phân cách).
    assert og_list_key("L_WES", "ma_cross", "US30", "M30") == "L_WES_MACROSS_US30_M30"
    assert channel_name("L_CHANNEL", "ma_cross", "US30", "M30") == "L_CHANNEL_MACROSS_US30_M30"
    assert strategy_token("MA_CROSS") == "MACROSS"
    assert strategy_token("combo") == "COMBO"
    # macd_break -> MACDBREAK (cùng quy tắc bỏ '_').
    assert og_list_key("L_WES", "macd_break", "us30", "h1") == "L_WES_MACDBREAK_US30_H1"
    assert channel_name("L_CHANNEL", "macd_break", "us30", "h1") == "L_CHANNEL_MACDBREAK_US30_H1"
    assert strategy_token("MACD_BREAK") == "MACDBREAK"


def test_redis_config_thieu_key_thi_raise() -> None:
    """7 key vận hành bắt buộc — thiếu phải raise rõ ràng, không fallback.

    input.event_channel KHÔNG còn trong danh sách này (gỡ 2026-09-23): OG
    không còn trigger qua dp:events:candles, trigger qua keyspace
    notification của chính Redis trên DB0 (xem event_listener.py) -- chỉ
    cần db/key_prefix/snapshot_bars/process_on_startup ở input.
    """
    full = {
        "host": "127.0.0.1",
        "password": "x",
        "input": {
            "db": 0,
            "key_prefix": "L_DFS",
            "snapshot_bars": 500,
            "process_on_startup": True,
        },
        "output": {"db": 1, "key_prefix": "L_WES", "channel_prefix": "L_CHANNEL"},
    }
    assert normalize_redis_config(full)["input"]["reconcile_interval_seconds"] == 1800
    # wes không hết hạn/cắt bớt: đầu ra KHÔNG có mặc định retention nào (quyết định 2026-10-07).
    assert set(normalize_redis_config(full)["output"]) == {"db", "key_prefix", "channel_prefix"}
    # input.event_channel thật sự không còn tồn tại trong config đã chuẩn hoá.
    assert "event_channel" not in normalize_redis_config(full)["input"]

    for section, key in (
        ("input", "db"),
        ("input", "key_prefix"),
        ("input", "snapshot_bars"),
        ("input", "process_on_startup"),
        ("output", "db"),
        ("output", "key_prefix"),
        ("output", "channel_prefix"),
    ):
        broken = {**full, section: {k: v for k, v in full[section].items() if k != key}}
        with pytest.raises(KeyError, match=f"redis.{section}.{key}"):
            normalize_redis_config(broken)


# =============================================================================
# 8. Payload DB1 gửi cho OF.
# =============================================================================


def _one_signal_row(strategy: str, tf: str, bars: pd.DataFrame) -> pd.Series:
    df = run_strategy(strategy, symbol="US30", tf=tf, bars=bars)
    return df[df["signal"].astype(int) != 0].iloc[-1]


def test_hash_fields_combo_la_lenh_stop(bars: pd.DataFrame) -> None:
    row = _one_signal_row("combo", "H1", bars)
    vf = signal_valid_from("H1", row["bartime"])
    signal_id, stamp, payload = build_signal(
        strategy="combo", timeframe="H1", symbol="US30", row=row, valid_from=vf
    )
    fields = to_hash_fields("combo", payload)

    assert fields["orderType"] == "STOP"
    assert "stopPrice" in fields  # STOP phải có giá kích hoạt
    assert fields["tradeSide"] in {"BUY", "SELL"}
    assert fields["symbol"] == "US30"
    assert fields["timeframe"] == "H1"
    assert fields["strategy"] == "combo"
    assert fields["clientOrderId"] == signal_id
    assert stamp == pd.Timestamp(row["bartime"]).strftime("%Y-%m-%d %H:%M:%S")
    # wes KHÔNG đặt hết hạn: không có expirationTimestamp; vẫn giữ valid_from (giờ nến ĐÓNG).
    assert "expirationTimestamp" not in fields and "valid_until" not in payload
    assert fields["valid_from"] == vf.isoformat().replace("+00:00", "Z")
    # SL/TP không còn do OG publish -- OF tự tính.
    assert not {"stopLoss", "takeProfit", "ksl", "ktp"} & set(fields)
    # OG chỉ có 1 nguồn text -> chỉ 'comment', không có 'label'.
    assert "label" not in fields
    assert "risk_reward" not in fields


def test_hash_fields_ma_cross_la_lenh_market(bars: pd.DataFrame) -> None:
    row = _one_signal_row("ma_cross", "M30", bars)
    vf = signal_valid_from("M30", row["bartime"])
    _, _, payload = build_signal(
        strategy="ma_cross", timeframe="M30", symbol="US30", row=row, valid_from=vf
    )
    fields = to_hash_fields("ma_cross", payload)

    assert fields["orderType"] == "MARKET"
    assert "stopPrice" not in fields  # MARKET không có giá kích hoạt
    assert "atr" in fields  # OF tự tính volume từ atr
    assert "valid_from" in fields


def test_build_signal_tu_choi_dong_khong_co_tin_hieu(bars: pd.DataFrame) -> None:
    df = run_strategy("combo", symbol="US30", tf="H1", bars=bars)
    row = df[df["signal"].astype(int) == 0].iloc[-1]
    now = pd.Timestamp("2026-01-01", tz="UTC")
    with pytest.raises(ValueError, match="BUY/SELL"):
        build_signal(
            strategy="combo",
            timeframe="H1",
            symbol="US30",
            row=row,
            valid_from=now,
        )


class _RecordingRedis:
    """Ghi lại đúng lời gọi EVAL, không nối Redis thật."""

    def __init__(self, created: int = 1) -> None:
        self.created = created
        self.calls: list[tuple] = []

    def eval(self, script, numkeys, *args):
        self.calls.append((script, numkeys, args))
        return self.created


def test_publish_ghi_og_roi_publish_day_du(bars: pd.DataFrame) -> None:
    """1 EVAL: 2 key (OG hash/list), PUBLISH lên kênh L_CHANNEL_..., message là
    JSON đầy đủ đúng bằng các field của Hash. Kênh không lưu key nào.
    """
    import json

    row = _one_signal_row("combo", "H1", bars)
    vf = signal_valid_from("H1", row["bartime"])
    _, stamp, payload = build_signal(
        strategy="combo", timeframe="H1", symbol="US30", row=row, valid_from=vf
    )
    fields = to_hash_fields("combo", payload)
    client = _RecordingRedis()

    assert publish_signal(
        client, key_prefix="L_WES", channel_prefix="L_CHANNEL", symbol="US30",
        timeframe="H1", strategy="combo", stamp=stamp, hash_fields=fields,
    ) is True

    (script, numkeys, args), = client.calls
    assert numkeys == 2  # chỉ OG hash + OG list, không có key nào cho kênh
    assert args[:2] == (
        f"L_WES_COMBO_US30_H1:{stamp}",
        "L_WES_COMBO_US30_H1",
    )
    assert "KEYS[3]" not in script
    argv = args[2:]  # [stamp, kênh, message, field1, value1, ...]
    assert argv[0] == stamp
    assert argv[1] == "L_CHANNEL_COMBO_US30_H1"  # kênh Pub/Sub
    assert json.loads(argv[2]) == fields  # message = đầy đủ thông tin
    assert dict(zip(argv[3::2], argv[4::2])) == fields  # Hash = cùng nội dung
    # Trùng lặp chỉ chặn ở OG hash; PUBLISH nằm sau các lệnh ghi.
    assert script.index("EXISTS") < script.index("PUBLISH")
    assert signal_message(fields) == argv[2]
    # wes giữ lại mọi tín hiệu: script KHÔNG có TTL (EXPIRE) và KHÔNG cắt LIST (LTRIM).
    assert "EXPIRE" not in script and "LTRIM" not in script


def test_publish_ma_cross_dung_token_macross_nhung_giu_field_ma_cross(bars: pd.DataFrame) -> None:
    """Chỉ TÊN key/kênh đổi sang MACROSS; field strategy và clientOrderId trong
    message/Hash vẫn là tên thật 'ma_cross' (chốt 2026-10-05).
    """
    import json

    row = _one_signal_row("ma_cross", "M30", bars)
    vf = signal_valid_from("M30", row["bartime"])
    _, stamp, payload = build_signal(
        strategy="ma_cross", timeframe="M30", symbol="US30", row=row, valid_from=vf
    )
    fields = to_hash_fields("ma_cross", payload)
    client = _RecordingRedis()
    publish_signal(
        client, key_prefix="L_WES", channel_prefix="L_CHANNEL", symbol="US30",
        timeframe="M30", strategy="ma_cross", stamp=stamp, hash_fields=fields,
    )
    (_, _, args), = client.calls
    assert args[:2] == (
        f"L_WES_MACROSS_US30_M30:{stamp}",
        "L_WES_MACROSS_US30_M30",
    )
    assert args[2 + 1] == "L_CHANNEL_MACROSS_US30_M30"  # kênh Pub/Sub
    message = json.loads(args[2 + 2])
    assert message["strategy"] == "ma_cross"
    assert message["clientOrderId"].startswith("ma_cross:M30:US30:")


def test_publish_trung_thi_bao_false() -> None:
    client = _RecordingRedis(created=0)
    assert publish_signal(
        client, key_prefix="L_WES", channel_prefix="L_CHANNEL", symbol="US30",
        timeframe="H1", strategy="combo", stamp="2026-09-22 13:00:00",
        hash_fields={"symbol": "US30"},
    ) is False


# =============================================================================
# 9. Cửa sổ hiệu lực + định tuyến timeframe.
# =============================================================================


def test_timeframe_duration() -> None:
    assert timeframe_duration("M30") == pd.Timedelta(minutes=30)
    assert timeframe_duration("H4") == pd.Timedelta(hours=4)
    for bad in ("X5", "M0", "M-1", ""):
        with pytest.raises(ValueError):
            timeframe_duration(bad)


def test_signal_valid_from_la_gio_nen_dong() -> None:
    bartime = pd.Timestamp("2026-09-22 13:00:00")
    # valid_from = lúc bar ĐÓNG (giờ mở + độ dài khung), không phải lúc bar mở.
    assert signal_valid_from("H4", bartime) == pd.Timestamp("2026-09-22 17:00:00", tz="UTC")
    assert signal_valid_from("M30", bartime) == pd.Timestamp("2026-09-22 13:30:00", tz="UTC")
    # wes không có cửa sổ hiệu lực: hàm cũ signal_window (valid_until) đã bỏ hẳn.
    from wisdom_engine.wes.src.redis_io import live_worker

    assert not hasattr(live_worker, "signal_window")
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        signal_valid_from("X5", bartime)


def test_dinh_tuyen_timeframe() -> None:
    enabled = ["combo", "ma_cross"]
    assert strategies_for_timeframe(enabled, "H4") == ["combo"]
    assert strategies_for_timeframe(enabled, "M30") == ["ma_cross"]
    assert strategies_for_timeframe(enabled, "M5") == []  # không chiến lược nào


def test_dinh_tuyen_timeframe_co_macd_break() -> None:
    """macd_break chồng lên cả combo (H1-H4) lẫn ma_cross (M10-M45): một nến có thể kích hoạt 2 chiến lược."""
    enabled = ["combo", "ma_cross", "macd_break"]
    assert strategies_for_timeframe(enabled, "H4") == ["combo", "macd_break"]
    assert strategies_for_timeframe(enabled, "H1") == ["combo", "macd_break"]
    assert strategies_for_timeframe(enabled, "M30") == ["ma_cross", "macd_break"]
    assert strategies_for_timeframe(enabled, "M10") == ["ma_cross", "macd_break"]
    assert strategies_for_timeframe(enabled, "M5") == []
    assert strategies_for_timeframe(enabled, "H6") == []  # H6 không thuộc chiến lược nào


def test_ma_cross_tu_choi_timeframe_ngoai_danh_sach(bars: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="supports only these timeframes"):
        run_strategy("ma_cross", symbol="US30", tf="H1", bars=bars)


# =============================================================================
# 9b. live_worker chọn đúng đường chạy theo công tắc trend — hermetic, client giả.
# =============================================================================


class _FakeInput:
    """Trả đúng bộ nến đã dựng sẵn cho từng timeframe, ghi lại TF nào bị đọc."""

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames
        self.doc: list[str] = []

    def read(self, *, symbol: str, timeframe: str, key_prefix: str, snapshot_bars: int):
        self.doc.append(timeframe)
        return self.frames.get(timeframe, pd.DataFrame())


class _FakeOutput:
    """Không nối Redis. eval() luôn báo 'đã tồn tại' -> không bao giờ publish."""

    def __init__(self) -> None:
        self.calls = 0

    def eval(self, *args, **kwargs):
        self.calls += 1
        return 0


def _worker_config() -> dict:
    return {
        "redis": {
            "input": {"key_prefix": "L_DFS", "snapshot_bars": 500},
            "output": {
                "key_prefix": "L_WES",
                "channel_prefix": "L_CHANNEL",
            },
        },
        "live": {
            "enabled_strategies": ["combo"],
            "discord": {"enabled": False},
        },
    }


def test_worker_trend_tat_chi_doc_mot_timeframe(
    bars: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wisdom_engine.wes.src.redis_io import live_worker as lw

    fake_in = _FakeInput({"H1": bars})
    monkeypatch.setattr(lw, "read_candles_from_redis", lambda client, **kw: fake_in.read(**kw))
    monkeypatch.setattr(lw, "normalize_app_config", lambda cfg: cfg)
    monkeypatch.setattr(lw, "trend_reference_for", lambda key: (False, "H4"))

    lw.process_candle_update(
        fake_in,
        _FakeOutput(),
        timeframe="H1",
        symbol="US30",
        config=_worker_config(),
        now=pd.Timestamp("2026-01-17 16:00:00", tz="UTC"),
    )
    assert fake_in.doc == ["H1"]


def test_worker_trend_bat_doc_them_khung_lon(
    bars: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bật trend filter thì worker PHẢI đọc thêm nến trend timeframe, và phải
    chạy qua đường 2 timeframe (run_strategy_with_trend_reference).
    """
    from wisdom_engine.wes.src.redis_io import live_worker as lw

    fake_in = _FakeInput({"H1": bars, "H4": bars.iloc[::4].reset_index(drop=True)})
    dung_duong_trend: list[str] = []

    def spy_trend(key, **kwargs):
        dung_duong_trend.append(kwargs["trend_tf"])
        return run_strategy_with_trend_reference(key, **kwargs)

    monkeypatch.setattr(lw, "read_candles_from_redis", lambda client, **kw: fake_in.read(**kw))
    monkeypatch.setattr(lw, "normalize_app_config", lambda cfg: cfg)
    monkeypatch.setattr(lw, "trend_reference_for", lambda key: (True, "H4"))

    lw.process_candle_update(
        fake_in,
        _FakeOutput(),
        timeframe="H1",
        symbol="US30",
        config=_worker_config(),
        now=pd.Timestamp("2026-01-17 16:00:00", tz="UTC"),
        trend_runner=spy_trend,
    )
    assert fake_in.doc == ["H1", "H4"]
    assert dung_duong_trend == ["H4"]


def test_worker_thieu_nen_trend_thi_khong_publish(
    bars: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB0 không có nến khung lớn cho symbol này -> BỎ QUA, tuyệt đối không
    publish tín hiệu chưa lọc (im lặng bỏ qua bộ lọc là kịch bản tệ nhất: OF
    sẽ đặt lệnh mà operator tin là đã được lọc theo trend).
    """
    from wisdom_engine.wes.src.redis_io import live_worker as lw

    fake_in = _FakeInput({"H1": bars})  # không có H4
    fake_out = _FakeOutput()
    monkeypatch.setattr(lw, "read_candles_from_redis", lambda client, **kw: fake_in.read(**kw))
    monkeypatch.setattr(lw, "normalize_app_config", lambda cfg: cfg)
    monkeypatch.setattr(lw, "trend_reference_for", lambda key: (True, "H4"))

    published = lw.process_candle_update(
        fake_in,
        fake_out,
        timeframe="H1",
        symbol="US30",
        config=_worker_config(),
        now=pd.Timestamp("2026-01-17 16:00:00", tz="UTC"),
    )
    assert fake_in.doc == ["H1", "H4"]
    assert published == 0
    assert fake_out.calls == 0  # không hề gọi tới Redis DB1


def test_get_strategy_khong_phan_biet_hoa_thuong() -> None:
    assert get_strategy("COMBO").key == "combo"
    assert get_strategy(" ma_cross ").key == "ma_cross"
    # ema_cross đã gỡ khỏi wes có chủ đích.
    with pytest.raises(KeyError, match="Unknown strategy"):
        get_strategy("ema_cross")


# =============================================================================
# 10. Validate tham số.
# =============================================================================


def test_ma_cross_chan_fast_lon_hon_slow() -> None:
    with pytest.raises(ValueError, match="FAST_MA must be smaller"):
        normalize_ma_cross_params({"FAST_MA": 50, "SLOW_MA": 20}, "US30", "M30")
    with pytest.raises(ValueError, match="MACD_FAST must be smaller"):
        normalize_ma_cross_params({"MACD_FAST": 30, "MACD_SLOW": 10}, "US30", "M30")


def test_tham_so_bi_kep_vao_khoang_an_toan() -> None:
    p = normalize_combo_params({"MA_PERIOD": 99999, "ATR_PERIOD": -5}, "US30", "H1")
    assert p["MA_PERIOD"] == 500  # max
    assert p["ATR_PERIOD"] == 2  # min


def test_session_hours_ngoai_pham_vi_bi_chan() -> None:
    with pytest.raises(ValueError, match="0..23"):
        normalize_combo_params({"SESSION_HOURS_UTC": "9,99"}, "US30", "H1")


def test_symbol_khong_co_trong_bang_x_thi_x_bang_0() -> None:
    assert normalize_combo_params(None, "ZZZ999", "H1")["X"] == 0.0
    assert normalize_combo_params(None, "US30", "H1")["X"] == 10.0


# =============================================================================
# 11. listen_for_candle_hash_events -- trigger dựa keyspace notification (thay
#     dp:events:candles, chốt 2026-09-23). Hermetic bằng pubsub/client giả.
# =============================================================================


class _StopTest(Exception):
    """Thoát vòng lặp while True vô hạn của listener sau khi đã quan sát đủ."""


class _FakePubSub:
    def __init__(self, messages: list[dict]) -> None:
        self._messages = list(messages)
        self.psubscribed: list[str] = []
        self.closed = False

    def psubscribe(self, pattern: str) -> None:
        self.psubscribed.append(pattern)

    def get_message(self, **_kwargs: object) -> dict | None:
        return self._messages.pop(0) if self._messages else None

    def close(self) -> None:
        self.closed = True


class _FakeRedisClient:
    def __init__(self, messages: list[dict]) -> None:
        self._pubsub = _FakePubSub(messages)

    def pubsub(self):
        return self._pubsub


def test_listen_for_candle_hash_events_chi_dispatch_hset_hop_le() -> None:
    """Khoá đúng 4 quy tắc lọc của listener mới:
      1. Chỉ payload=='hset' mới dispatch -- 'del' (LPOP evict) bị bỏ qua.
      2. Channel sai db-prefix (không khớp __keyspace@{db}__:) bị bỏ qua.
      3. Channel parse ra list-key không hợp lệ (parse_pair_key trả None)
         bị bỏ qua.
      4. Channel hợp lệ -> on_update(timeframe, symbol) đúng thứ tự tham số.
    """
    from wisdom_engine.wes.src.redis_io.event_listener import (
        listen_for_candle_hash_events,
    )

    messages = [
        {"type": "psubscribe"},
        # 1. Sai lệnh (del = LPOP evict, không phải nến đổi giá) -> bỏ qua.
        {
            "type": "pmessage",
            "channel": "__keyspace@0__:L_DFS_US30_M5:2026-01-01 00:00:00",
            "data": "del",
        },
        # 2. Sai db trong channel prefix (đang nghe db=0) -> bỏ qua.
        {
            "type": "pmessage",
            "channel": "__keyspace@1__:L_DFS_US30_M5:2026-01-01 00:00:00",
            "data": "hset",
        },
        # 3. List-key không parse được (thiếu timeframe sau prefix) -> bỏ qua.
        {
            "type": "pmessage",
            "channel": "__keyspace@0__:L_DFS_:2026-01-01 00:00:00",
            "data": "hset",
        },
        # 4a. Hợp lệ -> dispatch.
        {
            "type": "pmessage",
            "channel": "__keyspace@0__:L_DFS_US30_M5:2026-01-01 00:00:00",
            "data": "hset",
        },
        # 4b. Hợp lệ, symbol/timeframe khác -> dispatch, dừng test ở đây.
        {
            "type": "pmessage",
            "channel": "__keyspace@0__:L_DFS_BTCUSD_H4:2026-01-02 00:00:00",
            "data": "hset",
        },
    ]
    client = _FakeRedisClient(messages)
    dispatched: list[tuple[str, str]] = []

    def on_update(timeframe: str, symbol: str) -> None:
        dispatched.append((timeframe, symbol))
        if len(dispatched) == 2:
            raise _StopTest

    started = []
    with pytest.raises(_StopTest):
        listen_for_candle_hash_events(
            client,
            db=0,
            key_prefix="L_DFS",
            on_update=on_update,
            on_started=lambda: started.append(True),
        )

    assert started == [True]
    assert dispatched == [("M5", "US30"), ("H4", "BTCUSD")]
    assert client._pubsub.psubscribed == ["__keyspace@0__:L_DFS_*"]
    assert client._pubsub.closed  # finally: pubsub.close() luôn chạy


def test_listen_for_candle_hash_events_goi_reconcile_dung_dieu_kien(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """on_started không bắt buộc (None vẫn chạy được), và tới hạn reconcile
    thì on_reconcile phải được gọi -- không phụ thuộc có message nào tới hay
    không. Giả lập đồng hồ để không phải chờ thật 1 giây (get_message giả
    không block, vòng lặp sẽ busy-spin tới khi time.monotonic() vượt hạn).
    """
    import itertools

    import wisdom_engine.wes.src.redis_io.event_listener as event_listener_module

    # Lần gọi đầu (tính next_reconcile) trả 0.0; mọi lần sau trả 999.0 --
    # vượt hạn ngay từ vòng lặp đầu tiên, không cần chờ thật 1 giây.
    ticks = itertools.chain([0.0], itertools.repeat(999.0))
    monkeypatch.setattr(event_listener_module.time, "monotonic", lambda: next(ticks))

    messages = [{"type": "psubscribe"}, {"type": "pmessage", "channel": "x", "data": "hset"}]
    client = _FakeRedisClient(messages)

    def on_update(timeframe: str, symbol: str) -> None:
        raise _StopTest  # sẽ không tới đây vì channel "x" không khớp prefix

    def on_reconcile() -> None:
        raise _StopTest

    with pytest.raises(_StopTest):
        event_listener_module.listen_for_candle_hash_events(
            client,
            db=0,
            key_prefix="L_DFS",
            on_update=on_update,
            on_started=None,
            reconcile_interval_seconds=1,
            on_reconcile=on_reconcile,
        )


# =============================================================================
# 12. wes KHÔNG hết hạn (quyết định 2026-10-07) -- khác biệt duy nhất so với OG
#     live: không cửa sổ hiệu lực, không TTL, không cắt LIST. Cổng "nến chưa
#     đóng" vẫn giữ nguyên.
# =============================================================================


def _worker_with_last_candle_signal(bars: pd.DataFrame, monkeypatch: pytest.MonkeyPatch):
    """Cửa sổ nến mà NẾN CUỐI có tín hiệu combo, kèm worker đã vá để đọc nến từ bộ nhớ."""
    from wisdom_engine.wes.src.redis_io import live_worker as lw

    df = run_strategy("combo", symbol="US30", tf="H1", bars=bars)
    last = int(df.index[df["signal"].astype(int) != 0][-1])
    window = bars.iloc[: last + 1].reset_index(drop=True)
    fake_in = _FakeInput({"H1": window})
    monkeypatch.setattr(lw, "read_candles_from_redis", lambda client, **kw: fake_in.read(**kw))
    monkeypatch.setattr(lw, "normalize_app_config", lambda cfg: cfg)
    monkeypatch.setattr(lw, "trend_reference_for", lambda key: (False, "H4"))
    closed_at = pd.Timestamp(window.iloc[-1]["bartime"], tz="UTC") + pd.Timedelta(hours=1)
    return lw, fake_in, closed_at


@pytest.mark.parametrize(
    "age", [pd.Timedelta(seconds=30), pd.Timedelta(days=3), pd.Timedelta(days=400)]
)
def test_tin_hieu_van_duoc_ghi_du_nen_da_cu_bao_lau(
    bars: pd.DataFrame, monkeypatch: pytest.MonkeyPatch, age: pd.Timedelta
) -> None:
    """Phát lại nến quá khứ: tín hiệu cách giờ nến đóng 30 giây hay 400 ngày đều PHẢI
    được ghi (OG live sẽ loại tín hiệu quá hạn), và không mang thông tin hết hạn nào.
    """
    lw, fake_in, closed_at = _worker_with_last_candle_signal(bars, monkeypatch)
    out = _RecordingRedis()
    published = lw.process_candle_update(
        fake_in, out, timeframe="H1", symbol="US30", config=_worker_config(), now=closed_at + age
    )
    assert published == 1
    (script, _, args), = out.calls
    fields = dict(zip(args[2 + 3 :: 2], args[2 + 4 :: 2]))
    assert "expirationTimestamp" not in fields and "valid_until" not in fields
    assert fields["valid_from"] == closed_at.isoformat().replace("+00:00", "Z")
    assert "EXPIRE" not in script and "LTRIM" not in script


def test_nen_chua_dong_van_bi_chan_du_khong_con_het_han(
    bars: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    lw, fake_in, closed_at = _worker_with_last_candle_signal(bars, monkeypatch)
    out = _RecordingRedis()
    published = lw.process_candle_update(
        fake_in,
        out,
        timeframe="H1",
        symbol="US30",
        config=_worker_config(),
        now=closed_at - pd.Timedelta(minutes=1),
    )
    assert published == 0
    assert out.calls == []


def test_validate_config_khong_doi_signal_validity_hay_retention() -> None:
    from wisdom_engine.wes.src.redis_io import live_worker as lw

    config = {
        "redis": {
            "input": {
                "db": 14,
                "key_prefix": "L_DFS",
                "snapshot_bars": 500,
                "process_on_startup": False,
            },
            "output": {"db": 15, "key_prefix": "L_WES", "channel_prefix": "L_CHANNEL_SIM"},
        },
        "live": {
            "enabled_strategies": ["combo", "ma_cross", "macd_break"],
            "discord": {
                "enabled": False,
                "webhook_url": "",
                "timeout_seconds": 10,
                "retry_count": 2,
                "retry_delay_seconds": 2,
            },
        },
    }
    lw._validate_config(config)  # không raise: không còn live.signal_validity / redis.output.retention_*
    with pytest.raises(ValueError, match="different databases"):
        lw._validate_config(
            {**config, "redis": {**config["redis"], "output": {**config["redis"]["output"], "db": 14}}}
        )


# =============================================================================
# 13. Mỗi lần START wes là xoá sạch DB tín hiệu (quyết định 2026-10-08).
# =============================================================================


class _FakeKeyspace:
    """Redis giả có đủ phần worker dùng: ping/config_get/close + scan_iter/unlink."""

    def __init__(self, keys: set[str]) -> None:
        self.keys = set(keys)
        self.closed = False

    def ping(self) -> bool:
        return True

    def config_get(self, name: str) -> dict[str, str]:
        return {"notify-keyspace-events": "g$hzK"}

    def close(self) -> None:
        self.closed = True

    def scan_iter(self, match: str = "*", count: int | None = None, **kwargs):
        return iter(sorted(k for k in self.keys if fnmatch.fnmatchcase(k, match)))

    def unlink(self, *keys: str) -> int:
        removed = sum(1 for k in keys if k in self.keys)
        self.keys.difference_update(keys)
        return removed


def test_clear_signals_chi_xoa_dung_namespace_cua_wes() -> None:
    og = {f"L_WES_COMBO_US30_H{i}" for i in range(1, 5)} | {
        f"L_WES_COMBO_US30_H1:2022-01-{d:02d} 00:00:00" for d in range(1, 29)
    }
    la = {"L_DFS_US30_H1", "dfs:state", "L_PastSignal_US30_H1_COMBO"}
    fake = _FakeKeyspace(og | la)
    assert clear_signals(fake, key_prefix="L_WES") == len(og)
    assert fake.keys == la  # không FLUSHDB: key ngoài tiền tố L_WES_ còn nguyên
    assert clear_signals(fake, key_prefix="L_WES") == 0  # DB đã sạch -> không lỗi


def test_clear_signals_xoa_het_khi_nhieu_hon_mot_lo() -> None:
    og = {f"L_WES_MACROSS_US30_M10:2022-01-01 {h:02d}:{m:02d}:00" for h in range(24) for m in range(0, 60, 2)}
    assert len(og) > 500  # vượt _CLEAR_BATCH nên phải xoá nhiều lượt
    fake = _FakeKeyspace(og | {"giu_lai"})
    assert clear_signals(fake, key_prefix="L_WES") == len(og)
    assert fake.keys == {"giu_lai"}


def test_start_xoa_db_tin_hieu_dung_mot_lan_reconnect_khong_xoa_lai(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import redis as redis_lib
    from wisdom_engine.wes.src.redis_io import live_worker as lw

    candles = _FakeKeyspace({"L_DFS_US30_H1", "dfs:state"})
    signals = _FakeKeyspace({"L_WES_COMBO_US30_H1", "L_WES_COMBO_US30_H1:2022-01-01 00:00:00", "khac:key"})
    stores = {14: candles, 15: signals}
    lan_goi: list[int] = []

    class _Dung(Exception):
        pass

    def fake_listen(*args, **kwargs) -> None:
        lan_goi.append(1)
        if len(lan_goi) == 1:
            # Lần subscribe đầu: DB tín hiệu đã sạch; wes vừa ghi 1 tín hiệu mới rồi mất kết nối.
            assert not any(k.startswith("L_WES_") for k in signals.keys)
            signals.keys.add("L_WES_COMBO_US30_H2")
            raise redis_lib.ConnectionError("mat ket noi")
        raise _Dung  # lần reconnect: thoát vòng lặp vô hạn của run_forever

    monkeypatch.setattr(lw, "normalize_app_config", lambda cfg: cfg)
    monkeypatch.setattr(lw, "_validate_config", lambda cfg: None)
    monkeypatch.setattr(lw, "create_client", lambda cfg, db: stores[int(db)])
    monkeypatch.setattr(lw, "listen_for_candle_hash_events", fake_listen)
    monkeypatch.setattr(lw, "time", types.SimpleNamespace(sleep=lambda s: None))
    config = {
        "redis": {
            "reconnect_delay_seconds": 0,
            "input": {
                "db": 14, "key_prefix": "L_DFS", "process_on_startup": False,
                "reconcile_interval_seconds": 1800,
            },
            "output": {"db": 15, "key_prefix": "L_WES", "channel_prefix": "L_CHANNEL_SIM"},
        },
        "live": {"enabled_strategies": ["combo"]},
    }
    with caplog.at_level(logging.INFO, logger="live_worker"), pytest.raises(_Dung):
        lw.run_forever(config)

    assert len(lan_goi) == 2  # đã có 1 lần reconnect trong cùng tiến trình
    assert signals.keys == {"L_WES_COMBO_US30_H2", "khac:key"}  # tín hiệu mới giữ, reconnect không xoá lại
    assert candles.keys == {"L_DFS_US30_H1", "dfs:state"}  # DB nến không bị đụng
    cleared = [r.getMessage() for r in caplog.records if "event=signals_cleared" in r.getMessage()]
    assert len(cleared) == 1 and "keys=2" in cleared[0]


# =============================================================================
# 14. MACD Break (đăng ký vào wes 2026-10-14): bản sao luật của we_core, lệnh MARKET.
# =============================================================================

# Golden tính từ pipeline thật trên range_bars() (không phải số bịa): đổi digest = đổi tín hiệu/entry,
# phải là thay đổi CÓ CHỦ ĐÍCH. Tính lại bằng run_strategy("macd_break", ...) rồi băm 3 cột signal/entry_price/atr.
GOLDEN_MACD_BREAK = {
    "tf": "H1",
    "n_buy": 3,
    "n_sell": 3,
    "first_buy_idx": 230,
    "first_sell_idx": 136,
    "digest": "87809d7cf671518e52718e8ddaf5d869f64f30ddebf73df138069ad4e401047d",
}
MACD_BREAK_BUY_REASON = "close broke above prior highest close, MACD histogram > 0"
MACD_BREAK_SELL_REASON = "close broke below prior lowest close, MACD histogram < 0"


def test_macd_break_pipeline_golden(rbars: pd.DataFrame) -> None:
    want = GOLDEN_MACD_BREAK
    df = run_strategy("macd_break", symbol="US30", tf=want["tf"], bars=rbars)
    sig = df["signal"].astype(int)

    assert int((sig == 1).sum()) == want["n_buy"]
    assert int((sig == -1).sum()) == want["n_sell"]
    assert int(sig[sig == 1].index[0]) == want["first_buy_idx"]
    assert int(sig[sig == -1].index[0]) == want["first_sell_idx"]
    assert _pipeline_digest(df) == want["digest"]


def test_macd_break_khong_sua_input_tai_cho(rbars: pd.DataFrame) -> None:
    before = rbars.copy()
    run_strategy("macd_break", symbol="US30", tf="H1", bars=rbars)
    pd.testing.assert_frame_equal(rbars, before)


def _macd_break_frame(rows: list[tuple]) -> pd.DataFrame:
    """DataFrame tối thiểu cho detect_macd_break_signals: mỗi dòng là (close, prior_high, prior_low, macd_h, atr)."""
    return pd.DataFrame(
        {
            "bartime": pd.date_range("2026-01-05", periods=len(rows), freq="1h"),
            "close": [r[0] for r in rows],
            "prior_high_close": [r[1] for r in rows],
            "prior_low_close": [r[2] for r in rows],
            "macd_h": [r[3] for r in rows],
            "atr": [r[4] for r in rows],
        }
    )


def test_macd_break_dieu_kien_buy_sell() -> None:
    from wisdom_engine.wes.src.strategies.macd_break import detect_macd_break_signals

    nan = float("nan")
    frame = _macd_break_frame(
        [
            (110, 105, 95, 0.5, 1.0),   # phá đỉnh + MACD > 0            -> BUY
            (110, 105, 95, -0.5, 1.0),  # phá đỉnh nhưng MACD < 0        -> không
            (90, 105, 95, -0.5, 1.0),   # phá đáy + MACD < 0             -> SELL
            (90, 105, 95, 0.5, 1.0),    # phá đáy nhưng MACD > 0         -> không
            (105, 105, 95, 0.5, 1.0),   # close == đỉnh cũ (bằng tính)   -> BUY
            (95, 105, 95, -0.5, 1.0),   # close == đáy cũ (bằng tính)    -> SELL
            (110, 105, 95, 0.0, 1.0),   # MACD đúng 0                    -> không
            (100, 105, 95, 0.5, 1.0),   # nằm giữa, không phá            -> không
            (110, nan, 95, 0.5, 1.0),   # thiếu prior_high (chưa warmup) -> không
            (110, 105, 95, 0.5, nan),   # thiếu atr                      -> không
        ]
    )
    out = detect_macd_break_signals(frame, params={})

    assert out["signal"].tolist() == [1, 0, -1, 0, 1, -1, 0, 0, 0, 0]
    assert out["raw_signal"].tolist() == out["signal"].tolist()


def test_macd_break_chi_ban_nen_dau_khi_cung_chieu() -> None:
    from wisdom_engine.wes.src.strategies.macd_break import detect_macd_break_signals

    # 3 nến liên tiếp cùng phá đỉnh + MACD dương: cờ bật thì chỉ nến đầu bắn.
    frame = _macd_break_frame([(110, 105, 95, 0.5, 1.0)] * 3)
    out = detect_macd_break_signals(frame, params={"ALTERNATING_FILTER_ENABLED": True})
    assert out["signal"].tolist() == [1, 0, 0]


def test_macd_break_tat_co_thi_nen_nao_thoa_dieu_kien_cung_ban() -> None:
    from wisdom_engine.wes.src.strategies.macd_break import detect_macd_break_signals

    # Cờ tắt (hoặc thiếu): không nhớ "đã có BUY trước đó", nến nào thoả điều kiện cũng bắn.
    frame = _macd_break_frame([(110, 105, 95, 0.5, 1.0)] * 3)
    for params in ({"ALTERNATING_FILTER_ENABLED": False}, {}):
        assert detect_macd_break_signals(frame, params=params)["signal"].tolist() == [1, 1, 1]


def test_macd_break_setup_dung_cong_thuc_viet_doc_lap() -> None:
    """Cờ tắt: raw_signal đúng bằng điều kiện phá đỉnh/đáy close của 100 nến TRƯỚC + dấu MACD, viết lại độc lập
    bằng pandas (bằng đỉnh/đáy cũng tính; NaN = không có tín hiệu)."""
    bars900 = range_bars(900)
    off = run_strategy(
        "macd_break", symbol="US30", tf="H1", bars=bars900, overrides={"ALTERNATING_FILTER_ENABLED": "false"}
    )
    close = bars900["close"]
    prior_high = close.shift(1).rolling(100).max()
    prior_low = close.shift(1).rolling(100).min()
    buy = (close >= prior_high) & (off["macd_h"] > 0)
    sell = (close <= prior_low) & (off["macd_h"] < 0)
    expected = pd.Series(np.where(buy, 1, np.where(sell, -1, 0)), index=off.index)
    assert off["raw_signal"].astype(int).tolist() == expected.tolist()
    assert int(buy.sum()) > 5 and int(sell.sum()) > 5, "mẫu phải đủ cả hai chiều để phép kiểm có nghĩa"
    assert off.loc[:99, "raw_signal"].astype(int).eq(0).all()  # nến thứ 101 mới là nến hợp lệ đầu tiên


def test_macd_break_signal_cuoi_xen_ke_va_chinh_xac_la_loc_cua_setup() -> None:
    """Tín hiệu liên tiếp luôn đổi hướng, và đúng bằng lọc xen kẽ (viết độc lập, không vòng lặp) áp lên mọi setup."""
    bars900 = range_bars(900)
    df = run_strategy("macd_break", symbol="US30", tf="H1", bars=bars900)
    sequence = df.loc[df["signal"].astype(int) != 0, "signal"].astype(int).tolist()
    assert len(sequence) >= 6, "mẫu phải đủ tín hiệu để phép kiểm có nghĩa"
    assert all(a != b for a, b in pairwise(sequence))

    setups = run_strategy(
        "macd_break", symbol="US30", tf="H1", bars=bars900, overrides={"ALTERNATING_FILTER_ENABLED": "false"}
    )["raw_signal"].astype(int)
    last_setup = setups.where(setups != 0).ffill().shift(1)
    expected = setups.where((setups != 0) & (last_setup.isna() | (last_setup != setups)), 0)
    assert df["raw_signal"].astype(int).tolist() == expected.tolist()
    assert int((setups != 0).sum()) > int((df["raw_signal"].astype(int) != 0).sum())  # có tín hiệu lặp bị chặn thật


def _macd_break_trend_frame(rows: str, bias: list[int]) -> pd.DataFrame:
    """Khung tối giản: "B"/"S" = bar có setup BUY/SELL (phá đỉnh/đáy + MACD cùng dấu), "." = không có."""
    spec = {
        "B": (110, 105, 95, 0.5, 1.0),
        "S": (90, 105, 95, -0.5, 1.0),
        ".": (100, 105, 95, 0.5, 1.0),
    }
    frame = _macd_break_frame([spec[c] for c in rows])
    frame["trend_bias"] = bias
    return frame


def test_macd_break_lock_xen_ke_dung_truoc_trend_filter() -> None:
    """Lọc xen kẽ chạy trên setup TRƯỚC, rồi trend filter mới lọc (signal ⊆ raw_signal). Hệ quả đã biết: setup
    bị trend loại vẫn chiếm lượt đầu tiên của hướng đó."""
    from wisdom_engine.wes.src.strategies.macd_break import detect_macd_break_signals

    params = {"TREND_FILTER_ENABLED": True, "ALTERNATING_FILTER_ENABLED": True}
    out = detect_macd_break_signals(_macd_break_trend_frame(".B.B", [0, -1, 0, 1]), params=params)
    assert out["raw_signal"].tolist() == [0, 1, 0, 0] and out["signal"].tolist() == [0, 0, 0, 0]
    out = detect_macd_break_signals(_macd_break_trend_frame(".BSB", [0, -1, -1, 1]), params=params)
    assert out["raw_signal"].tolist() == [0, 1, -1, 1] and out["signal"].tolist() == [0, 0, -1, 1]
    assert ((out["signal"] != 0) <= (out["raw_signal"] != 0)).all()


@pytest.mark.parametrize("trend_tf,step,n_raw,n_kept", [("H2", 2, 6, 6), ("H3", 3, 6, 6), ("H4", 4, 6, 5)])
def test_macd_break_trend_bat_chi_giu_tin_hieu_cung_chieu(
    rbars: pd.DataFrame, trend_tf: str, step: int, n_raw: int, n_kept: int
) -> None:
    """Đường 2 timeframe thật cho macd_break (entry + trend dựng từ cùng bộ nến), số liệu tính từ pipeline thật."""
    trend_bars = rbars.iloc[::step].reset_index(drop=True)  # giả lập khung lớn hơn
    on = run_strategy_with_trend_reference(
        "macd_break",
        symbol="US30",
        entry_tf="H1",
        entry_bars=rbars,
        trend_tf=trend_tf,
        trend_bars=trend_bars,
    )
    assert int((on["raw_signal"].astype(int) != 0).sum()) == n_raw  # bộ lọc không đụng tới raw_signal
    kept = on[on["signal"].astype(int) != 0]
    assert len(kept) == n_kept
    assert (kept["signal"].astype(int) == kept["trend_bias"].astype(int)).all()
    assert (kept["trend_close_time"] <= kept["bartime"]).all()  # không lookahead
    assert ((on["signal"].astype(int) != 0) <= (on["raw_signal"].astype(int) != 0)).all()
    assert set(kept["signal_reason"].str.endswith("; aligned with trend reference")) == {True}


def test_macd_break_bat_trend_tren_1_timeframe_thi_raise(rbars: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="trend_bias"):
        run_strategy(
            "macd_break", symbol="US30", tf="H1", bars=rbars, overrides={"TREND_FILTER_ENABLED": True}
        )


def test_macd_break_chi_bao_loai_nen_hien_tai_va_chi_dung_close() -> None:
    from wisdom_engine.wes.src.indicator import add_macd_break_indicators

    params = normalize_macd_break_params({"LOOKBACK_BARS": 3}, "US30", "H1")
    df = synthetic_bars(60)
    out = add_macd_break_indicators(df, params)

    assert out["prior_high_close"].iloc[:3].isna().all()
    assert out["prior_high_close"].iloc[3] == df["close"].iloc[0:3].max()  # không gồm nến hiện tại
    assert out["prior_low_close"].iloc[10] == df["close"].iloc[7:10].min()
    # Chỉ dùng close: dời high/low đi thật xa không đổi 2 cột này.
    moved = add_macd_break_indicators(df.assign(high=df["high"] + 50.0, low=df["low"] - 50.0), params)
    pd.testing.assert_series_equal(out["prior_high_close"], moved["prior_high_close"])
    pd.testing.assert_series_equal(out["prior_low_close"], moved["prior_low_close"])
    pd.testing.assert_frame_equal(df, synthetic_bars(60))  # không sửa input tại chỗ


def test_macd_break_signal_reason_dung_nguyen_van(rbars: pd.DataFrame) -> None:
    df = run_strategy("macd_break", symbol="US30", tf="H1", bars=rbars)
    sig = df["signal"].astype(int)
    assert set(df[sig == 1]["signal_reason"]) == {MACD_BREAK_BUY_REASON}
    assert set(df[sig == -1]["signal_reason"]) == {MACD_BREAK_SELL_REASON}
    assert set(df[sig == 0]["signal_reason"]) <= {""}


def test_macd_break_entry_la_close_va_khong_co_sl_tp(rbars: pd.DataFrame) -> None:
    """MACD Break là lệnh MARKET: entry = close của bar tín hiệu; không có SL/TP (OF tự tính)."""
    df = run_strategy("macd_break", symbol="US30", tf="H1", bars=rbars)
    has = df[df["signal"].astype(int) != 0]
    assert len(has) == GOLDEN_MACD_BREAK["n_buy"] + GOLDEN_MACD_BREAK["n_sell"]
    np.testing.assert_allclose(has["entry_price"], has["close"], rtol=1e-12)
    assert df.loc[df["signal"].astype(int) == 0, "entry_price"].isna().all()
    assert not {"sl_price", "tp_price", "ksl", "ktp", "risk_reward"} & set(df.columns)


def test_macd_break_nen_cuoi_cua_cua_so_wes(rbars: pd.DataFrame) -> None:
    """wes đọc snapshot_bars (500) nến và chỉ xét nến cuối: cửa sổ 500 nến kết thúc ở một nến tín hiệu phải cho
    đúng tín hiệu đó; cửa sổ chỉ có LOOKBACK nến thì nến cuối chưa có prior_high_close nên KHÔNG có tín hiệu."""
    lookback = normalize_macd_break_params(None, "US30", "H1")["LOOKBACK_BARS"]
    full = run_strategy("macd_break", symbol="US30", tf="H1", bars=rbars)
    last = int(full.index[full["signal"].astype(int) != 0][-1])
    snapshot_bars = 500
    assert last >= snapshot_bars - 1

    window = rbars.iloc[last - snapshot_bars + 1 : last + 1].reset_index(drop=True)
    assert len(window) == snapshot_bars
    result = run_strategy("macd_break", symbol="US30", tf="H1", bars=window).iloc[-1]
    assert int(result["signal"]) == int(full.loc[last, "signal"]) != 0

    short = rbars.iloc[last - lookback + 1 : last + 1].reset_index(drop=True)
    assert len(short) == lookback
    out = run_strategy("macd_break", symbol="US30", tf="H1", bars=short)
    assert out["prior_high_close"].isna().all() and int(out.iloc[-1]["signal"]) == 0
    enough = rbars.iloc[last - lookback : last + 1].reset_index(drop=True)
    assert pd.notna(run_strategy("macd_break", symbol="US30", tf="H1", bars=enough).iloc[-1]["prior_high_close"])


def test_macd_break_chan_macd_fast_lon_hon_slow() -> None:
    with pytest.raises(ValueError, match="MACD_FAST must be smaller"):
        normalize_macd_break_params({"MACD_FAST": 30, "MACD_SLOW": 10}, "US30", "H1")


def test_macd_break_lookback_bi_kep_vao_khoang_an_toan() -> None:
    assert normalize_macd_break_params({"LOOKBACK_BARS": 99999}, "US30", "H1")["LOOKBACK_BARS"] == 300  # max
    assert normalize_macd_break_params({"LOOKBACK_BARS": 0}, "US30", "H1")["LOOKBACK_BARS"] == 2  # min
    assert normalize_macd_break_params(None, "US30", "H1")["LOOKBACK_BARS"] == 100  # mặc định từ config


def test_macd_break_chay_dung_tren_8_timeframe_da_chot_va_tu_choi_con_lai(rbars: pd.DataFrame) -> None:
    spec = get_strategy("macd_break")
    allowed = ("M10", "M20", "M30", "M45", "H1", "H2", "H3", "H4")
    assert set(spec.recommended_timeframes) == set(spec.supported_timeframes) == set(allowed)
    for tf in allowed:
        assert not run_strategy("macd_break", symbol="US30", tf=tf, bars=rbars).empty
    for tf in ("M5", "M15", "H6", "D1"):
        with pytest.raises(ValueError, match="supports only these timeframes"):
            run_strategy("macd_break", symbol="US30", tf=tf, bars=rbars)


def test_hash_fields_macd_break_la_lenh_market(rbars: pd.DataFrame) -> None:
    row = _one_signal_row("macd_break", "H1", rbars)
    vf = signal_valid_from("H1", row["bartime"])
    signal_id, stamp, payload = build_signal(
        strategy="macd_break", timeframe="H1", symbol="US30", row=row, valid_from=vf
    )
    fields = to_hash_fields("macd_break", payload)

    assert fields["orderType"] == "MARKET"
    assert "stopPrice" not in fields  # MARKET không có giá kích hoạt
    assert fields["strategy"] == "macd_break"
    assert fields["tradeSide"] == ("BUY" if int(row["signal"]) == 1 else "SELL")
    assert fields["clientOrderId"] == signal_id and signal_id.startswith("macd_break:H1:US30:")
    assert fields["comment"] in {MACD_BREAK_BUY_REASON, MACD_BREAK_SELL_REASON}
    assert payload["entry"] == round(float(row["close"]), 2)  # entry tham chiếu = close nến tín hiệu
    assert {"atr", "valid_from"} <= set(fields)
    assert stamp == pd.Timestamp(row["bartime"]).strftime("%Y-%m-%d %H:%M:%S")
    assert not {"stopLoss", "takeProfit", "ksl", "ktp", "expirationTimestamp"} & set(fields)


def test_publish_macd_break_dung_token_macdbreak_nhung_giu_field_macd_break(rbars: pd.DataFrame) -> None:
    """Chỉ TÊN key/kênh bỏ '_' thành MACDBREAK; field strategy và clientOrderId vẫn là tên thật 'macd_break'."""
    import json

    row = _one_signal_row("macd_break", "H1", rbars)
    vf = signal_valid_from("H1", row["bartime"])
    _, stamp, payload = build_signal(
        strategy="macd_break", timeframe="H1", symbol="US30", row=row, valid_from=vf
    )
    fields = to_hash_fields("macd_break", payload)
    client = _RecordingRedis()
    assert publish_signal(
        client, key_prefix="L_WES", channel_prefix="L_CHANNEL", symbol="US30",
        timeframe="H1", strategy="macd_break", stamp=stamp, hash_fields=fields,
    ) is True
    (_, _, args), = client.calls
    assert args[:2] == (f"L_WES_MACDBREAK_US30_H1:{stamp}", "L_WES_MACDBREAK_US30_H1")
    assert args[2 + 1] == "L_CHANNEL_MACDBREAK_US30_H1"  # kênh Pub/Sub
    message = json.loads(args[2 + 2])
    assert message == fields
    assert message["strategy"] == "macd_break"
    assert message["clientOrderId"].startswith("macd_break:H1:US30:")


def test_worker_ghi_tin_hieu_macd_break_tu_cua_so_500_nen(
    rbars: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Đường live đầy đủ (hermetic): sự kiện nến H1 -> đọc cửa sổ 500 nến -> macd_break ở nến cuối -> ghi 1 tín hiệu."""
    from wisdom_engine.wes.src.redis_io import live_worker as lw

    full = run_strategy("macd_break", symbol="US30", tf="H1", bars=rbars)
    last = int(full.index[full["signal"].astype(int) != 0][-1])
    window = rbars.iloc[last - 499 : last + 1].reset_index(drop=True)
    fake_in = _FakeInput({"H1": window})
    monkeypatch.setattr(lw, "read_candles_from_redis", lambda client, **kw: fake_in.read(**kw))
    monkeypatch.setattr(lw, "normalize_app_config", lambda cfg: cfg)
    monkeypatch.setattr(lw, "trend_reference_for", lambda key: (False, "H4"))
    config = {**_worker_config(), "live": {"enabled_strategies": ["macd_break"], "discord": {"enabled": False}}}
    closed_at = pd.Timestamp(window.iloc[-1]["bartime"], tz="UTC") + pd.Timedelta(hours=1)
    out = _RecordingRedis()

    published = lw.process_candle_update(
        fake_in, out, timeframe="H1", symbol="US30", config=config, now=closed_at + pd.Timedelta(seconds=30)
    )

    assert published == 1
    (script, _, args), = out.calls
    assert args[0].startswith("L_WES_MACDBREAK_US30_H1:") and args[1] == "L_WES_MACDBREAK_US30_H1"
    fields = dict(zip(args[2 + 3 :: 2], args[2 + 4 :: 2]))
    assert fields["orderType"] == "MARKET" and "stopPrice" not in fields
    assert fields["strategy"] == "macd_break" and fields["timeframe"] == "H1"
    assert fields["valid_from"] == closed_at.isoformat().replace("+00:00", "Z")
    assert "EXPIRE" not in script and "LTRIM" not in script  # không hết hạn, không cắt LIST
