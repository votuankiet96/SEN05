"""Test characterization cho core_engine — hermetic, KHÔNG đụng Redis.

Mọi giá trị mong đợi trong file này được tính từ CHÍNH pipeline thật trên bộ
nến tổng hợp tất định bên dưới (không phải số bịa). Mục đích: khoá lại hành
vi hiện tại để phát hiện thay đổi logic ngoài ý muốn. Khi sửa logic chiến
lược CÓ CHỦ ĐÍCH, phải chạy lại pipeline để tính giá trị mới rồi cập nhật
test — không tự đoán số.

Cần de_config.yaml tồn tại (hệ thống chỉ chạy trên vm-og, luôn có sẵn file
thật). KHÔNG mở kết nối Redis nào.
"""

from __future__ import annotations

import hashlib
import math
from itertools import pairwise

import numpy as np
import pandas as pd
import pytest
from engine_support import run_strategy

from core_engine.src.configuration import (
    STRATEGIES,
    alternating_filter_for,
    get_strategy,
)
from core_engine.src.indicator import (
    add_ema_reach_indicators,
    add_ichimoku_indicators,
    atr,
    rma,
    sma,
)
from core_engine.src.strategies.ema_reach import detect_ema_reach_signals
from core_engine.src.strategies.ichimoku import detect_ichimoku_signals

normalize_combo_params = STRATEGIES["combo"].normalize_params
normalize_ma_cross_params = STRATEGIES["ma_cross"].normalize_params
normalize_ema_reach_params = STRATEGIES["ema_reach"].normalize_params
normalize_macd_break_params = STRATEGIES["macd_break"].normalize_params
normalize_ichimoku_params = STRATEGIES["ichimoku"].normalize_params
COMBO_TREND_TIMEFRAMES = STRATEGIES["combo"].trend_timeframes

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


def _pipeline_digest(df: pd.DataFrame) -> str:
    cols = ["signal", "entry_price", "atr"]
    return hashlib.sha256(df[cols].to_csv(index=False, float_format="%.8f").encode()).hexdigest()


# =============================================================================
# 1. Golden lock: toàn bộ pipeline 1 lượt chạy.
# =============================================================================

# Digest tính từ pipeline thật trên synthetic_bars() ở trên, 3 cột
# signal/entry_price/atr. Tính lại 2026-10-04 khi bỏ SL/TP khỏi DE: digest 3
# cột này từ code TRƯỚC khi bỏ và SAU khi bỏ trùng khớp tuyệt đối, tức việc
# bỏ SL/TP không đổi tín hiệu/entry. Đổi digest = đổi tín hiệu/entry -> phải
# là thay đổi CÓ CHỦ ĐÍCH.
# combo tính lại 2026-10-11 khi áp dụng lọc lặp cùng chiều (_alternating_signals):
# trước đó 2 BUY / 3 SELL (digest 1fb2c847...; SELL@24,132,193, BUY@219,389), nay
# còn SELL@24 và BUY@219 — chạy lại pipeline với bộ lọc tắt tái hiện đúng digest cũ.
# ĐỒNG BỘ VỚI we_core 2026-10-21 (người dùng yêu cầu DE cho ra đúng cùng kết quả chiến lược như we_core): combo entry =
# high/low không đệm X (số tín hiệu và bar không đổi, chỉ entry_price đổi đúng ∓10 = X của US30 trước đó; entry mới ± 10
# dựng lại đúng digest cũ ee5a702a... — cờ lọc lặp tắt: 1fb2c847...); ma_cross đổi sang luật trạng thái + lọc lặp cùng chiều
# (trước đó 7 BUY / 6 SELL, SELL đầu ở bar 86, digest d456518f...). Hai digest mới TRÙNG đúng golden của des/we_core vì cùng
# bộ nến synthetic_bars() và cùng luật.
GOLDEN = {
    "combo": {
        "tf": "H1",
        "n_buy": 1,
        "n_sell": 1,
        "first_buy_idx": 219,
        "first_sell_idx": 24,
        "digest": "cade7c4cb0486d8c79807b71f5a83ecaf2a39c7be78a214bf8fe4ae4f6cf75ee",
    },
    "ma_cross": {
        "tf": "M30",
        "n_buy": 7,
        "n_sell": 7,
        "first_buy_idx": 57,
        "first_sell_idx": 33,
        "digest": "25df71757bb2a7d3fa294a4b7560c078a2c380165452df72aed8da1093940d86",
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


def test_trend_tat_thi_signal_bang_raw_signal(bars: pd.DataFrame) -> None:
    for strategy, tf in (("combo", "H1"), ("ma_cross", "M30")):
        df = run_strategy(strategy, symbol="US30", tf=tf, bars=bars)
        pd.testing.assert_series_equal(
            df["signal"].astype(int), df["raw_signal"].astype(int), check_names=False
        )
        assert set(df["trend_filter_status"]) == {"disabled"}
        # risk_reward đã bỏ hẳn: không có công thức nào tính nó.
        assert "risk_reward" not in df.columns


def test_trend_bat_ma_thieu_cot_trend_thi_raise(bars: pd.DataFrame) -> None:
    """run_strategy() (1 timeframe, không bảng trend) không bao giờ sinh trend_bias. Bật trend
    filter trên đường đó phải RAISE rõ ràng chứ không âm thầm bỏ qua bộ lọc —
    configuration.process_plan dựa vào điều này: bản lọc chỉ chạy khi đã lắp được cột trend_bias.
    """
    with pytest.raises(ValueError, match="trend_bias"):
        run_strategy(
            "combo", symbol="US30", tf="H1", bars=bars, overrides={"TREND_FILTER_ENABLED": True}
        )


def test_ten_khoi_trend_la_bi_chan_va_trend_type_cu_bi_bo_qua() -> None:
    with pytest.raises(ValueError, match="unknown trend method"):
        normalize_combo_params({"TREND_METHODS": "ema"}, "US30", "H1")
    # khoá cũ TREND_TYPE (trước 2026-10-25) nay chỉ là khoá lạ: bị bỏ qua như mọi khoá lạ khác
    assert "TREND_TYPE" not in normalize_combo_params({"TREND_TYPE": "ema"}, "US30", "H1")


def test_trend_tf_ngoai_danh_sach_bi_chan() -> None:
    with pytest.raises(ValueError, match="TREND_TF must be one of"):
        normalize_combo_params({"TREND_TF": "M5"}, "US30", "H1")


def test_strategies_gom_combo_ma_cross_macd_break_ichimoku_va_ema_reach() -> None:
    assert sorted(STRATEGIES) == ["combo", "ema_reach", "ichimoku", "ma_cross", "macd_break"]


# =============================================================================
# 3. signal_reason -> field `comment` của DB1, OF đọc nguyên văn.
# =============================================================================

REASONS = {
    ("combo", 1): "bullish candle crossing above MA, MACD histogram > 0",
    ("combo", -1): "bearish candle crossing below MA, MACD histogram < 0",
    ("ma_cross", 1): "fast SMA above slow SMA; MACD histogram > 0",
    ("ma_cross", -1): "fast SMA below slow SMA; MACD histogram < 0",
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
# 4. Entry. DE không còn SL/TP (OF tự tính từ KSL/KTP của họ, 2026-10-04).
# =============================================================================


@pytest.mark.parametrize("strategy,tf", [("combo", "H1"), ("ma_cross", "M30")])
def test_de_khong_con_cot_sl_tp(bars: pd.DataFrame, strategy: str, tf: str) -> None:
    df = run_strategy(strategy, symbol="US30", tf=tf, bars=bars)
    assert not {"sl_price", "tp_price", "ksl", "ktp"} & set(df.columns)


def test_combo_entry_la_mep_nen_tin_hieu_khong_dem(bars: pd.DataFrame) -> None:
    """Combo là lệnh STOP đặt đúng mép nến tín hiệu (từ 2026-10-21, đồng bộ we_core): entry BUY = high, SELL = low,
    không đệm X."""
    df = run_strategy("combo", symbol="US30", tf="H1", bars=bars)
    sig = df["signal"].astype(int)
    buy, sell = df[sig == 1], df[sig == -1]
    assert len(buy) > 0 and len(sell) > 0
    np.testing.assert_array_equal(buy["entry_price"].to_numpy(), buy["high"].to_numpy())
    np.testing.assert_array_equal(sell["entry_price"].to_numpy(), sell["low"].to_numpy())
    assert (buy["entry_price"] >= buy["close"]).all() and (sell["entry_price"] <= sell["close"]).all()
    assert df.loc[sig == 0, "entry_price"].isna().all()


def _combo_alternating(setups: str) -> list[int]:
    """Chuỗi ký tự -> setup: B = setup BUY, S = setup SELL, . = không có setup."""
    from core_engine.src.rules.alternating import alternating_signals

    buy = pd.Series([c == "B" for c in setups])
    sell = pd.Series([c == "S" for c in setups])
    return alternating_signals(buy, sell).tolist()


def test_combo_alternating_chi_ban_tin_hieu_dau_khi_doi_chieu() -> None:
    """Lọc lặp cùng chiều (áp dụng 2026-10-11): chỉ setup đầu tiên khi đổi chiều bắn."""
    assert _combo_alternating("BBSSBBS") == [1, 0, -1, 0, 1, 0, -1]
    # bar không có setup thì không đổi trạng thái
    assert _combo_alternating("B..B..S.S") == [1, 0, 0, 0, 0, 0, -1, 0, 0]
    # chiều đầu tiên của cửa sổ luôn bắn
    assert _combo_alternating("SSB") == [-1, 0, 1]
    assert _combo_alternating("......") == [0] * 6


def test_combo_pipeline_tin_hieu_luon_xen_ke(bars: pd.DataFrame) -> None:
    """Trên pipeline thật: hai tín hiệu liên tiếp không bao giờ cùng chiều."""
    sig = run_strategy("combo", symbol="US30", tf="H1", bars=bars)["signal"].astype(int)
    fired = sig[sig != 0].tolist()
    assert len(fired) >= 2
    assert all(a != b for a, b in pairwise(fired))


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
# 9. Chiến lược từ chối timeframe ngoài danh sách (cửa sổ hiệu lực và định tuyến nay ở test_live_plan.py).
# =============================================================================


def test_ma_cross_tu_choi_timeframe_ngoai_danh_sach(bars: pd.DataFrame) -> None:
    """ma_cross chạy cả 8 khung M10–H4 (2026-11-02); chặn cứng supported_timeframes vẫn còn, kiểm bằng khung ngoài danh sách."""
    with pytest.raises(ValueError, match="supports only these timeframes"):
        run_strategy("ma_cross", symbol="US30", tf="M5", bars=bars)
    assert len(run_strategy("ma_cross", symbol="US30", tf="H1", bars=bars)) == len(bars)  # khung H nay được phép


# =============================================================================
# 9b. Tra cứu chiến lược theo key.
# =============================================================================


def test_get_strategy_khong_phan_biet_hoa_thuong() -> None:
    assert get_strategy("COMBO").key == "combo"
    assert get_strategy(" ma_cross ").key == "ma_cross"
    # ema_cross đã gỡ khỏi core_engine có chủ đích.
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


def test_combo_khong_con_tham_so_x_va_symbol_x() -> None:
    """Từ 2026-10-21 combo không còn đệm X theo symbol: không còn param X, COMBO_SYMBOL_X hay khoá symbol_x."""
    from core_engine.src import configuration as cfg

    combo = get_strategy("combo")
    assert "X" not in combo.default_params and "X" not in combo.bounds
    for symbol in ("US30", "ZZZ999"):
        assert "X" not in normalize_combo_params({"X": "10"}, symbol, "H1")  # override cũ bị bỏ qua
    assert not hasattr(cfg, "COMBO_SYMBOL_X") and not hasattr(cfg, "_to_float")
    assert "symbol_x" not in cfg.CONFIG["strategies"]["combo"]


# =============================================================================
# 12. Đồng bộ chiến lược với we_core (2026-10-21): combo entry = high/low không đệm, ma_cross luật trạng thái + lọc lặp
#     cùng chiều, macd_break thêm vào DE, Dow (sl_dow) đã bỏ. Phần lớn test dưới đây là bản chép từ des/we_core (cùng
#     luật, cùng golden); chạy chiến lược qua đúng pipeline của DE.
# =============================================================================


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


# Golden của macd_break trên range_bars(): TRÙNG đúng golden của des/we_core (cùng bộ nến, cùng luật).
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
MACD_BREAK_TIMEFRAMES = ("M10", "M20", "M30", "M45", "H1", "H2", "H3", "H4")


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
    from core_engine.src.strategies.macd_break import detect_macd_break_signals

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


def test_macd_break_chi_ban_nen_dau_khi_cung_chieu_va_tat_co_thi_moi_nen_deu_ban() -> None:
    from core_engine.src.strategies.macd_break import detect_macd_break_signals

    frame = _macd_break_frame([(110, 105, 95, 0.5, 1.0)] * 3)
    assert detect_macd_break_signals(frame, params={"ALTERNATING_FILTER_ENABLED": True})["signal"].tolist() == [1, 0, 0]
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


def test_macd_break_chi_bao_loai_nen_hien_tai_va_chi_dung_close() -> None:
    from core_engine.src.indicator import add_macd_break_indicators

    params = normalize_macd_break_params({"LOOKBACK_BARS": 3}, "US30", "H1")
    df = synthetic_bars(60)
    out = add_macd_break_indicators(df, params)

    assert out["prior_high_close"].iloc[:3].isna().all()
    assert out["prior_high_close"].iloc[3] == df["close"].iloc[0:3].max()  # không gồm nến hiện tại
    assert out["prior_low_close"].iloc[10] == df["close"].iloc[7:10].min()
    moved = add_macd_break_indicators(df.assign(high=df["high"] + 50.0, low=df["low"] - 50.0), params)
    pd.testing.assert_series_equal(out["prior_high_close"], moved["prior_high_close"])  # chỉ dùng close
    pd.testing.assert_series_equal(out["prior_low_close"], moved["prior_low_close"])
    pd.testing.assert_frame_equal(df, synthetic_bars(60))  # không sửa input tại chỗ


def test_macd_break_signal_reason_va_entry_la_close_va_khong_co_sl_tp(rbars: pd.DataFrame) -> None:
    df = run_strategy("macd_break", symbol="US30", tf="H1", bars=rbars)
    sig = df["signal"].astype(int)
    assert set(df[sig == 1]["signal_reason"]) == {MACD_BREAK_BUY_REASON}
    assert set(df[sig == -1]["signal_reason"]) == {MACD_BREAK_SELL_REASON}
    assert set(df[sig == 0]["signal_reason"]) <= {""}
    has = df[sig != 0]
    assert len(has) == GOLDEN_MACD_BREAK["n_buy"] + GOLDEN_MACD_BREAK["n_sell"]
    np.testing.assert_allclose(has["entry_price"], has["close"], rtol=1e-12)  # MARKET: entry = close
    assert df.loc[sig == 0, "entry_price"].isna().all()
    assert not {"sl_price", "tp_price", "ksl", "ktp", "risk_reward"} & set(df.columns)


def test_macd_break_nen_cuoi_cua_cua_so_500_nen_cua_de(rbars: pd.DataFrame) -> None:
    """DE đọc snapshot_bars (500) nến và chỉ xét nến cuối: cửa sổ 500 nến kết thúc ở một nến tín hiệu phải cho đúng tín
    hiệu đó; cửa sổ chỉ có LOOKBACK nến thì nến cuối chưa có prior_high_close nên KHÔNG có tín hiệu."""
    lookback = normalize_macd_break_params(None, "US30", "H1")["LOOKBACK_BARS"]
    full = run_strategy("macd_break", symbol="US30", tf="H1", bars=rbars)
    last = int(full.index[full["signal"].astype(int) != 0][-1])
    assert last >= 499

    window = rbars.iloc[last - 499 : last + 1].reset_index(drop=True)
    result = run_strategy("macd_break", symbol="US30", tf="H1", bars=window).iloc[-1]
    assert int(result["signal"]) == int(full.loc[last, "signal"]) != 0

    short = rbars.iloc[last - lookback + 1 : last + 1].reset_index(drop=True)
    out = run_strategy("macd_break", symbol="US30", tf="H1", bars=short)
    assert out["prior_high_close"].isna().all() and int(out.iloc[-1]["signal"]) == 0


def test_macd_break_tham_so_va_8_timeframe_da_chot(rbars: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="MACD_FAST must be smaller"):
        normalize_macd_break_params({"MACD_FAST": 30, "MACD_SLOW": 10}, "US30", "H1")
    assert normalize_macd_break_params({"LOOKBACK_BARS": 99999}, "US30", "H1")["LOOKBACK_BARS"] == 300  # max
    assert normalize_macd_break_params({"LOOKBACK_BARS": 0}, "US30", "H1")["LOOKBACK_BARS"] == 2  # min
    assert normalize_macd_break_params(None, "US30", "H1")["LOOKBACK_BARS"] == 100  # mặc định từ config
    spec = get_strategy("macd_break")
    assert set(spec.recommended_timeframes) == set(spec.supported_timeframes) == set(MACD_BREAK_TIMEFRAMES)
    for tf in MACD_BREAK_TIMEFRAMES:
        assert not run_strategy("macd_break", symbol="US30", tf=tf, bars=rbars).empty
    for tf in ("M5", "M15", "H6", "D1"):
        with pytest.raises(ValueError, match="supports only these timeframes"):
            run_strategy("macd_break", symbol="US30", tf=tf, bars=rbars)


def test_macd_break_bat_trend_tren_1_timeframe_thi_raise(rbars: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="trend_bias"):
        run_strategy("macd_break", symbol="US30", tf="H1", bars=rbars, overrides={"TREND_FILTER_ENABLED": True})


# --- cờ lọc lặp cùng chiều và cấu hình mẫu ---------------------------------------------------------------------


def test_apply_alternating_filter_bat_tat_theo_co() -> None:
    from core_engine.src.rules.alternating import (
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
    assert apply_alternating_filter(buy, sell, {}).tolist() == off.tolist()  # thiếu key = tắt
    assert apply_alternating_filter(buy, sell, None).tolist() == off.tolist()
    for text, want in (("true", True), ("1", True), ("on", True), ("false", False), ("0", False), ("", False)):
        assert alternating_filter_enabled({"ALTERNATING_FILTER_ENABLED": text}) is want


def test_alternating_coi_nan_la_khong_co_setup() -> None:
    from core_engine.src.rules.alternating import alternating_signals

    buy = pd.Series([True, float("nan"), True], dtype="object")
    assert alternating_signals(buy, pd.Series([False, False, False])).tolist() == [1, 0, 0]


def test_co_alternating_doc_tu_config_o_ca_bon_chien_luoc() -> None:
    for key, tf in (("combo", "H1"), ("ma_cross", "M30"), ("macd_break", "H1"), ("ichimoku", "H1")):
        spec = get_strategy(key)
        assert spec.default_params["ALTERNATING_FILTER_ENABLED"] is True
        assert spec.normalize_params(None, "US30", tf)["ALTERNATING_FILTER_ENABLED"] is True
        assert spec.normalize_params({"ALTERNATING_FILTER_ENABLED": "false"}, "US30", tf)["ALTERNATING_FILTER_ENABLED"] is False


def test_config_mau_dung_cau_hinh_moi_cho_ca_bon_chien_luoc() -> None:
    from pathlib import Path

    import yaml

    example = yaml.safe_load((Path(__file__).resolve().parents[1] / "de_config.example.yaml").read_text(encoding="utf-8"))

    for key in ("combo", "ma_cross", "macd_break"):
        assert example["strategies"][key]["alternating_filter_enabled"] is True
    assert "symbol_x" not in example["strategies"]["combo"]
    assert "enabled_strategies" not in example["live"] and "plans" not in example  # DE chạy mọi chiến lược ở mọi (symbol, khung) theo bảng của we
    for key in ("combo", "ma_cross", "macd_break", "ema_reach"):  # bảng khung trend theo (symbol, khung vào lệnh) của we; không còn khung tắt trend hay danh sách khối theo chiến lược
        assert "trend_tf_by_symbol" in example["strategies"][key] and not {"trend_off_timeframes", "trend_methods"} & set(example["strategies"][key]), key
    assert not {"trend_off_timeframes", "trend_tf_by_symbol", "trend_methods"} & set(example["strategies"]["ichimoku"])
    assert example["strategies"]["macd_break"]["supported_timeframes"] == list(MACD_BREAK_TIMEFRAMES)
    assert example["strategies"]["macd_break"]["lookback_bars"] == 100
    assert example["strategies"]["macd_break"]["excluded_trend_methods"] == ["breakout"]
    assert example["live"]["signal_validity"]["macd_break"] == {"mode": "seconds_after_bar_close", "seconds": 600}
    ichimoku = example["strategies"]["ichimoku"]  # không có trend: không khai khoá trend nào
    assert not [key for key in ichimoku if key.startswith("trend")]
    assert (ichimoku["tenkan_period"], ichimoku["kijun_period"], ichimoku["senkou_b_period"], ichimoku["displacement"], ichimoku["atr_period"]) == (9, 26, 52, 26, 5)
    assert ichimoku["alternating_filter_enabled"] is True and ichimoku["supported_timeframes"] == list(ICHIMOKU_TIMEFRAMES)
    assert example["live"]["signal_validity"]["ichimoku"] == {"mode": "seconds_after_bar_close", "seconds": 600}  # quy tắc giống ma_cross


def test_combo_tat_co_dung_digest_cu_khi_dung_lai_entry_cu(bars: pd.DataFrame) -> None:
    """Cờ lọc lặp tắt: signal đúng golden combo trước 2026-10-11 (2 BUY / 3 SELL); entry mới ± 10 (X cũ của US30) dựng
    lại đúng digest cũ 1fb2c847... (chỉ cột entry đổi, đúng bằng X)."""
    df = run_strategy("combo", symbol="US30", tf="H1", bars=bars, overrides={"ALTERNATING_FILTER_ENABLED": "false"})
    sig = df["signal"].astype(int)
    assert int((sig == 1).sum()) == 2 and int((sig == -1).sum()) == 3
    assert int(sig[sig == 1].index[0]) == GOLDEN["combo"]["first_buy_idx"]
    assert int(sig[sig == -1].index[0]) == GOLDEN["combo"]["first_sell_idx"]
    assert _pipeline_digest(df).startswith("f34265d8")
    old = df.copy()
    old.loc[sig == 1, "entry_price"] = df.loc[sig == 1, "entry_price"] + 10.0
    old.loc[sig == -1, "entry_price"] = df.loc[sig == -1, "entry_price"] - 10.0
    assert _pipeline_digest(old).startswith("1fb2c847")


# --- ma_cross luật trạng thái ----------------------------------------------------------------------------------


def _combo_trend_frame(rows: str, bias: list[int]) -> pd.DataFrame:
    """Khung tối giản cho detect_combo_signals: "B"/"S" = bar có setup BUY/SELL, "." = không có."""
    spec = {
        "B": {"open": 10.0, "close": 11.0, "prev_close": 10.0, "macd_h": 1.0},
        "S": {"open": 11.0, "close": 10.0, "prev_close": 11.0, "macd_h": -1.0},
        ".": {"open": 10.0, "close": 10.0, "prev_close": 10.0, "macd_h": 0.0},
    }
    frame = pd.DataFrame([spec[c] for c in rows])
    frame["ma"] = 10.5
    frame["bartime"] = pd.date_range("2026-01-01", periods=len(rows), freq="h")
    frame["trend_bias"] = bias
    return frame


def _ma_cross_trend_frame(rows: str, bias: list[int]) -> pd.DataFrame:
    """Khung tối giản cho detect_ma_cross_signals: "B"/"S" = setup BUY/SELL (fast trên/dưới slow + MACD cùng
    dấu), "." = không có setup (fast = slow)."""
    spec = {
        "B": {"fast_ma": 2.0, "slow_ma": 1.0, "macd_h": 1.0},
        "S": {"fast_ma": 1.0, "slow_ma": 2.0, "macd_h": -1.0},
        ".": {"fast_ma": 1.0, "slow_ma": 1.0, "macd_h": 0.0},
    }
    frame = pd.DataFrame([spec[c] for c in rows])
    frame["atr"] = 1.0
    frame["trend_bias"] = bias
    return frame


def _macd_break_trend_frame(rows: str, bias: list[int]) -> pd.DataFrame:
    spec = {"B": (110, 105, 95, 0.5, 1.0), "S": (90, 105, 95, -0.5, 1.0), ".": (100, 105, 95, 0.5, 1.0)}
    frame = _macd_break_frame([spec[c] for c in rows])
    frame["trend_bias"] = bias
    return frame


def test_ma_cross_setup_theo_trang_thai_va_chi_nen_dau_cua_moi_dot_ban() -> None:
    from core_engine.src.strategies.ma_cross import detect_ma_cross_signals

    frame = _ma_cross_trend_frame("BBB.SSB", [0] * 7)
    assert detect_ma_cross_signals(frame, params={"ALTERNATING_FILTER_ENABLED": True})["signal"].tolist() == [
        1, 0, 0, 0, -1, 0, 1,
    ]
    for params in ({"ALTERNATING_FILTER_ENABLED": False}, {}):  # cờ tắt (hoặc thiếu key): nến nào thỏa cũng bắn
        assert detect_ma_cross_signals(frame, params=params)["signal"].tolist() == [1, 1, 1, 0, -1, -1, 1]
    flat = _ma_cross_trend_frame("...", [0] * 3)  # bằng nhau hoặc MACD bằng 0 thì không có setup
    assert detect_ma_cross_signals(flat, params={"ALTERNATING_FILTER_ENABLED": False})["signal"].tolist() == [0, 0, 0]


def test_ma_cross_khong_con_cot_sma_ky_truoc_va_khong_con_dow(bars: pd.DataFrame) -> None:
    df = run_strategy("ma_cross", symbol="US30", tf="M30", bars=bars)
    assert not {"prev_fast_ma", "prev_slow_ma", "raw_signal_reason", "sl_dow", "sl_dow_buy", "sl_dow_sell", "dow_swing_low"} & set(df.columns)
    assert {"fast_ma", "slow_ma", "macd_h", "atr", "entry_price"} <= set(df.columns)


def test_ma_cross_tat_co_moi_nen_thoa_dieu_kien_deu_ban(bars: pd.DataFrame) -> None:
    df = run_strategy("ma_cross", symbol="US30", tf="M30", bars=bars, overrides={"ALTERNATING_FILTER_ENABLED": "false"})
    setup = ((df["fast_ma"] > df["slow_ma"]) & (df["macd_h"] > 0)) | ((df["fast_ma"] < df["slow_ma"]) & (df["macd_h"] < 0))
    valid = df[["fast_ma", "slow_ma", "macd_h", "atr"]].notna().all(axis=1)
    assert ((df["signal"].astype(int) != 0) == (setup & valid)).all()
    assert int((df["signal"].astype(int) != 0).sum()) == 151  # tính từ pipeline thật, trùng des/we_core


def _signal_tham_chieu(setups: str, bias: list[int], *, trend_on: bool, alternating_on: bool) -> list[int]:
    """Bản tham chiếu viết độc lập bằng vòng lặp thuần: (1) lọc xen kẽ trên mọi setup, (2) trend giữ tín hiệu
    cùng chiều bias."""
    out, state = [], 0
    for ch, b in zip(setups, bias, strict=True):
        side = {"B": 1, "S": -1, ".": 0}[ch]
        raw = 0 if side == 0 or (alternating_on and side == state) else side
        if side != 0:
            state = side
        out.append(0 if trend_on and raw != b else raw)
    return out


@pytest.mark.parametrize("trend_on", [True, False])
@pytest.mark.parametrize("alternating_on", [True, False])
def test_ca_ba_chien_luoc_khop_ban_tham_chieu_xen_ke_truoc_trend_sau_tren_khung_ngau_nhien(
    trend_on: bool, alternating_on: bool
) -> None:
    from core_engine.src.strategies.combo import detect_combo_signals
    from core_engine.src.strategies.ma_cross import detect_ma_cross_signals
    from core_engine.src.strategies.macd_break import detect_macd_break_signals

    rng = np.random.default_rng(20261016)
    params = {"SESSION_HOURS_UTC": [], "TREND_FILTER_ENABLED": trend_on, "ALTERNATING_FILTER_ENABLED": alternating_on}
    adjacent_same_direction_cases = 0
    for _ in range(150):
        n = int(rng.integers(1, 25))
        rows = "".join(rng.choice(list("BS.."), n))
        bias = rng.choice([-1, 0, 1], n).tolist()
        expected = _signal_tham_chieu(rows, bias, trend_on=trend_on, alternating_on=alternating_on)
        expected_raw = _signal_tham_chieu(rows, bias, trend_on=False, alternating_on=alternating_on)
        for out in (
            detect_combo_signals(_combo_trend_frame(rows, bias), params=params),
            detect_macd_break_signals(_macd_break_trend_frame(rows, bias), params=params),
            detect_ma_cross_signals(_ma_cross_trend_frame(rows, bias), params=params),
        ):
            assert out["signal"].tolist() == expected
            assert out["raw_signal"].tolist() == expected_raw  # raw_signal không phụ thuộc trend
            assert all(s in (0, r) for s, r in zip(out["signal"].tolist(), out["raw_signal"].tolist(), strict=True))
            if not trend_on:
                assert out["signal"].tolist() == out["raw_signal"].tolist()
            elif alternating_on:
                seq = [v for v in out["signal"].tolist() if v]
                adjacent_same_direction_cases += int(any(a == b for a, b in pairwise(seq)))
    if trend_on and alternating_on:
        assert adjacent_same_direction_cases > 0  # trend loại setup ngược chiều ở giữa: ca này có xuất hiện thật


# --- Dow đã bỏ: payload, indicator-----------------------------------------------------------------------


def test_indicator_khong_con_ham_dow() -> None:
    from core_engine.src import indicator

    assert not [name for name in dir(indicator) if "dow" in name.lower()]
    assert hasattr(indicator, "add_macd_break_indicators") and not hasattr(indicator, "calc_ai_trend_navigator")
    from core_engine.src.trend_methods import (
        knn,  # công thức KNN nằm ở khối trend_methods/knn.py (đồng bộ we_core 2026-10-15)
    )

    assert hasattr(knn, "calc_ai_trend_navigator")


# =============================================================================
# 13. Ichimoku (thêm 2026-10-28, đồng bộ we_core): Ichimoku gốc (Sanyaku Kouten / Gyakuten), KHÔNG có trend, chỉ nguồn raw.
#     Mọi giá trị mong đợi tính từ CHÍNH pipeline thật trên bộ nến tất định (range_bars / synthetic_bars) và được đối chiếu
#     với bản viết lại ĐỘC LẬP từ đặc tả bằng vòng lặp tường minh (_ichimoku_reference), không dùng rolling/shift của pandas.
# =============================================================================

ICHIMOKU_TIMEFRAMES = ("M10", "M20", "M30", "M45", "H1", "H2", "H3", "H4")
ICHIMOKU_BUY_REASON = "Tenkan > Kijun, close above the lagging close (Chikou), close above the cloud"
ICHIMOKU_SELL_REASON = "Tenkan < Kijun, close below the lagging close (Chikou), close below the cloud"
# Golden của ichimoku trên range_bars() (H1; M30 cho cùng kết quả vì chiến lược không phụ thuộc khung). Bộ nến tất định nên phía we_core
# đối chiếu được: cùng luật, cùng bộ nến thì phải ra cùng chỉ số nến tín hiệu và cùng digest. Nến 77 là nến ĐẦU TIÊN có đủ mây (52 + 26 = 78
# nến): nếu lúc đó đã đủ ba điều kiện thì bắn ngay (nến trước đó "chưa đủ" vì thiếu dữ liệu), đúng đặc tả.
GOLDEN_ICHIMOKU = {
    "tf": "H1",
    "n_buy": 4,
    "n_sell": 4,
    "first_buy_idx": 77,
    "first_sell_idx": 81,
    "signal_idx": [77, 81, 172, 253, 339, 434, 514, 597],
    "digest": "0fc9eec8d8917df90e3aa7412aa1f76d2375bb8615d2671c0bcd9cb51ba7a78b",
}
_NAN = float("nan")
ICHIMOKU_FLAT = (10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 1.0)  # tenkan, kijun, senkou_a, senkou_b, close, close_lag, atr: không điều kiện nào đúng
ICHIMOKU_BUY = (11.0, 10.0, 5.0, 8.0, 20.0, 15.0, 1.0)  # tenkan > kijun, close > close_lag, close > max(A, B) = 8
ICHIMOKU_SELL = (9.0, 10.0, 25.0, 22.0, 5.0, 15.0, 1.0)  # tenkan < kijun, close < close_lag, close < min(A, B) = 22


def _ichimoku_frame(rows: list[tuple]) -> pd.DataFrame:
    """DataFrame tối thiểu cho detect_ichimoku_signals: mỗi dòng là (tenkan, kijun, senkou_a, senkou_b, close, close_lag, atr)."""
    names = ("tenkan", "kijun", "senkou_a", "senkou_b", "close", "close_lag", "atr")
    frame = pd.DataFrame(rows, columns=names)
    frame.insert(0, "bartime", pd.date_range("2026-01-05", periods=len(rows), freq="1h"))
    return frame


def _ichimoku_reference(df: pd.DataFrame, *, alternating: bool = True) -> list[int]:
    """Tín hiệu Ichimoku viết thẳng từ đặc tả bằng vòng lặp tường minh (không dùng rolling/shift của pandas)."""
    high, low, close = df["high"].tolist(), df["low"].tolist(), df["close"].tolist()
    atr_values = atr(df, 5).tolist()
    n = len(close)

    def midpoint(i: int, period: int) -> float:
        if i + 1 < period:
            return _NAN
        window = range(i - period + 1, i + 1)
        return (max(high[j] for j in window) + min(low[j] for j in window)) / 2.0

    senkou_a = [(midpoint(i, 9) + midpoint(i, 26)) / 2.0 for i in range(n)]
    senkou_b = [midpoint(i, 52) for i in range(n)]

    def state(i: int) -> tuple[bool, bool]:
        if i < 26:
            return False, False
        a, b, tenkan, kijun = senkou_a[i - 26], senkou_b[i - 26], midpoint(i, 9), midpoint(i, 26)  # mây tại i = Senkou đã tính ở nến i - 26
        if any(math.isnan(value) for value in (a, b, tenkan, kijun)):
            return False, False
        lag = close[i - 26]
        return (tenkan > kijun and close[i] > lag and close[i] > max(a, b), tenkan < kijun and close[i] < lag and close[i] < min(a, b))

    states = [state(i) for i in range(n)]
    out, last = [], 0
    for i in range(n):
        atr_ok = not math.isnan(atr_values[i])
        buy = states[i][0] and not (i > 0 and states[i - 1][0]) and atr_ok
        sell = states[i][1] and not (i > 0 and states[i - 1][1]) and atr_ok
        signal = 0
        if alternating:
            if buy and last != 1:
                signal, last = 1, 1
            elif sell and last != -1:
                signal, last = -1, -1
        else:
            signal = 1 if buy else (-1 if sell else 0)
        out.append(signal)
    return out


def test_ichimoku_pipeline_golden(rbars: pd.DataFrame) -> None:
    want = GOLDEN_ICHIMOKU
    df = run_strategy("ichimoku", symbol="US30", tf=want["tf"], bars=rbars)
    sig = df["signal"].astype(int)

    assert int((sig == 1).sum()) == want["n_buy"]
    assert int((sig == -1).sum()) == want["n_sell"]
    assert int(sig[sig == 1].index[0]) == want["first_buy_idx"]
    assert int(sig[sig == -1].index[0]) == want["first_sell_idx"]
    assert sig[sig != 0].index.tolist() == want["signal_idx"]
    assert _pipeline_digest(df) == want["digest"]
    assert _pipeline_digest(run_strategy("ichimoku", symbol="US30", tf="M30", bars=rbars)) == want["digest"]  # không phụ thuộc khung


def test_ichimoku_khong_sua_input_tai_cho(rbars: pd.DataFrame) -> None:
    before = rbars.copy()
    run_strategy("ichimoku", symbol="US30", tf="H1", bars=rbars)
    pd.testing.assert_frame_equal(rbars, before)


def test_ichimoku_chi_bao_khop_ban_tinh_tay(rbars: pd.DataFrame) -> None:
    out = add_ichimoku_indicators(rbars, normalize_ichimoku_params(None, "US30", "H1"))
    high, low, close = rbars["high"].tolist(), rbars["low"].tolist(), rbars["close"].tolist()

    def midpoint(i: int, period: int) -> float:
        if i + 1 < period:
            return _NAN
        window = range(i - period + 1, i + 1)
        return (max(high[j] for j in window) + min(low[j] for j in window)) / 2.0

    n = len(rbars)
    tenkan, kijun = [midpoint(i, 9) for i in range(n)], [midpoint(i, 26) for i in range(n)]
    cloud_a = [_NAN] * 26 + [(tenkan[i] + kijun[i]) / 2.0 for i in range(n - 26)]  # mây tại t = Senkou đã tính ở nến t - 26
    cloud_b = [_NAN] * 26 + [midpoint(i, 52) for i in range(n - 26)]
    lag = [_NAN] * 26 + close[: n - 26]
    for column, want in (("tenkan", tenkan), ("kijun", kijun), ("senkou_a", cloud_a), ("senkou_b", cloud_b), ("close_lag", lag)):
        np.testing.assert_allclose(out[column].to_numpy(), want, equal_nan=True, err_msg=column)
    first_valid = {column: int(out[column].first_valid_index()) for column in ("tenkan", "kijun", "senkou_a", "senkou_b", "close_lag", "atr")}
    assert first_valid == {"tenkan": 8, "kijun": 25, "senkou_a": 51, "senkou_b": 77, "close_lag": 26, "atr": first_valid["atr"]}
    assert (out["atr"] == atr(rbars, 5)).sum() == out["atr"].notna().sum()  # ATR Wilder chu kỳ 5 của config


def test_ichimoku_dieu_kien_buy_sell_ba_dieu_kien_nghiem_ngat() -> None:
    params = {"ALTERNATING_FILTER_ENABLED": False}  # mỗi sự kiện đều bắn: soi riêng từng điều kiện
    cases = {
        "buy đủ ba điều kiện": ([ICHIMOKU_FLAT, ICHIMOKU_BUY], 1),
        "buy, Senkou A trên B": ([ICHIMOKU_FLAT, (11.0, 10.0, 8.0, 5.0, 20.0, 15.0, 1.0)], 1),
        "sell đủ ba điều kiện": ([ICHIMOKU_FLAT, ICHIMOKU_SELL], -1),
        "sell, Senkou A dưới B": ([ICHIMOKU_FLAT, (9.0, 10.0, 22.0, 25.0, 5.0, 15.0, 1.0)], -1),
        "tenkan bằng kijun": ([ICHIMOKU_FLAT, (10.0, 10.0, 5.0, 8.0, 20.0, 15.0, 1.0)], 0),
        "buy: tenkan dưới kijun": ([ICHIMOKU_FLAT, (9.0, 10.0, 5.0, 8.0, 20.0, 15.0, 1.0)], 0),
        "close bằng close_lag": ([ICHIMOKU_FLAT, (11.0, 10.0, 5.0, 8.0, 15.0, 15.0, 1.0)], 0),
        "buy: close dưới close_lag": ([ICHIMOKU_FLAT, (11.0, 10.0, 5.0, 8.0, 12.0, 15.0, 1.0)], 0),
        "close bằng biên trên mây": ([ICHIMOKU_FLAT, (11.0, 10.0, 5.0, 20.0, 20.0, 15.0, 1.0)], 0),
        "close trong mây": ([ICHIMOKU_FLAT, (11.0, 10.0, 5.0, 25.0, 20.0, 15.0, 1.0)], 0),
        "sell: close bằng biên dưới mây": ([ICHIMOKU_FLAT, (9.0, 10.0, 25.0, 5.0, 5.0, 15.0, 1.0)], 0),
        "sell: tenkan trên kijun": ([ICHIMOKU_FLAT, (11.0, 10.0, 25.0, 22.0, 5.0, 15.0, 1.0)], 0),
        "thiếu tenkan": ([ICHIMOKU_FLAT, (_NAN, 10.0, 5.0, 8.0, 20.0, 15.0, 1.0)], 0),
        "thiếu senkou_b (chưa đủ 78 nến)": ([ICHIMOKU_FLAT, (11.0, 10.0, 5.0, _NAN, 20.0, 15.0, 1.0)], 0),
        "thiếu senkou_a": ([ICHIMOKU_FLAT, (11.0, 10.0, _NAN, 8.0, 20.0, 15.0, 1.0)], 0),
        "thiếu close_lag": ([ICHIMOKU_FLAT, (11.0, 10.0, 5.0, 8.0, 20.0, _NAN, 1.0)], 0),
    }
    for label, (rows, want) in cases.items():
        out = detect_ichimoku_signals(_ichimoku_frame(rows), params=params)
        assert out["signal"].astype(int).tolist() == [0, want], label


def test_ichimoku_tin_hieu_la_su_kien_vua_du_ba_dieu_kien_va_xen_ke() -> None:
    frame = _ichimoku_frame([ICHIMOKU_FLAT, ICHIMOKU_BUY, ICHIMOKU_BUY, ICHIMOKU_BUY, ICHIMOKU_FLAT, ICHIMOKU_BUY, ICHIMOKU_SELL, ICHIMOKU_SELL, ICHIMOKU_BUY])
    off = detect_ichimoku_signals(frame, params={"ALTERNATING_FILTER_ENABLED": False})
    # chỉ nến ĐẦU của mỗi đợt đủ ba điều kiện bắn (nến 1, 5, 6, 8); các nến cùng đợt (2, 3, 7) thì không
    assert off["signal"].astype(int).tolist() == [0, 1, 0, 0, 0, 1, -1, 0, 1]
    on = detect_ichimoku_signals(frame, params={"ALTERNATING_FILTER_ENABLED": True})
    assert on["signal"].astype(int).tolist() == [0, 1, 0, 0, 0, 0, -1, 0, 1]  # BUY thứ hai (nến 5) bị chặn: chưa có SELL nào xen vào
    assert on["raw_signal"].astype(int).tolist() == on["signal"].astype(int).tolist()  # không có trend: signal == raw_signal
    assert set(on["trend_filter_status"]) == {"disabled"}
    assert on.loc[1, "signal_reason"] == ICHIMOKU_BUY_REASON and on.loc[6, "signal_reason"] == ICHIMOKU_SELL_REASON
    assert (on.loc[on["signal"] == 0, "signal_reason"] == "").all()
    # nến đầu của khung đã đủ ba điều kiện thì bắn ở nến đó (không có nến trước để "chưa đủ"): đặc tả của we_core, chỉ xảy ra ở đầu cửa sổ
    first = detect_ichimoku_signals(_ichimoku_frame([ICHIMOKU_BUY, ICHIMOKU_BUY, ICHIMOKU_FLAT]), params={"ALTERNATING_FILTER_ENABLED": False})
    assert first["signal"].astype(int).tolist() == [1, 0, 0]


def test_ichimoku_atr_chua_hop_le_thi_khong_phat_nhung_khong_lam_lech_nen_truoc() -> None:
    params = {"ALTERNATING_FILTER_ENABLED": False}
    no_atr_buy = (*ICHIMOKU_BUY[:-1], _NAN)
    # nến 1 đủ ba điều kiện nhưng atr NaN (build_signal cần atr) -> không phát; nến 2 vẫn là nến cùng đợt (atr không tham gia định nghĩa
    # trạng thái) -> không phải sự kiện mới. Không bao giờ phát tín hiệu thiếu atr, cũng không "dời" sự kiện sang nến sau.
    out = detect_ichimoku_signals(_ichimoku_frame([ICHIMOKU_FLAT, no_atr_buy, ICHIMOKU_BUY, ICHIMOKU_BUY]), params=params)
    assert out["signal"].astype(int).tolist() == [0, 0, 0, 0]
    out = detect_ichimoku_signals(_ichimoku_frame([ICHIMOKU_FLAT, ICHIMOKU_BUY, ICHIMOKU_BUY]), params=params)
    assert out["signal"].astype(int).tolist() == [0, 1, 0]


def test_ichimoku_pipeline_khop_ban_tham_chieu_doc_lap(rbars: pd.DataFrame, bars: pd.DataFrame) -> None:
    for name, frame in (("range_bars", rbars), ("synthetic_bars", bars), ("range_bars 250 nến", rbars.iloc[:250].reset_index(drop=True))):
        for alternating in (True, False):
            df = run_strategy("ichimoku", symbol="US30", tf="H1", bars=frame, overrides={"ALTERNATING_FILTER_ENABLED": alternating})
            assert df["signal"].astype(int).tolist() == _ichimoku_reference(frame, alternating=alternating), (name, alternating)
            assert df["raw_signal"].astype(int).tolist() == df["signal"].astype(int).tolist()
    # bộ lọc xen kẽ: BUY/SELL xen kẽ tuyệt đối
    signals = [s for s in run_strategy("ichimoku", symbol="US30", tf="H1", bars=rbars)["signal"].astype(int).tolist() if s]
    assert signals and all(a != b for a, b in pairwise(signals))


def test_ichimoku_la_nhan_qua_cat_bot_nen_cuoi_khong_doi_tin_hieu_truoc_do(rbars: pd.DataFrame) -> None:
    full = run_strategy("ichimoku", symbol="US30", tf="H1", bars=rbars)["signal"].astype(int).tolist()
    for cut in (90, 200, 345, 599):
        part = run_strategy("ichimoku", symbol="US30", tf="H1", bars=rbars.iloc[:cut].reset_index(drop=True))["signal"].astype(int).tolist()
        assert part == full[:cut]


def test_ichimoku_entry_la_close_khong_co_sl_tp_va_trend_tat(rbars: pd.DataFrame) -> None:
    df = run_strategy("ichimoku", symbol="US30", tf="H1", bars=rbars)
    signalled = df[df["signal"].astype(int) != 0]
    assert len(signalled) == len(GOLDEN_ICHIMOKU["signal_idx"])
    np.testing.assert_allclose(signalled["entry_price"].to_numpy(), signalled["close"].to_numpy())  # lệnh MARKET: entry = close nến tín hiệu
    assert df.loc[df["signal"].astype(int) == 0, "entry_price"].isna().all()
    assert not {"sl_price", "tp_price", "risk_reward", "ksl", "ktp"} & set(df.columns)  # SL/TP/thoát lệnh là việc của hệ đặt lệnh
    assert set(df["trend_filter_status"]) == {"disabled"}
    assert not [column for column in df.columns if column.startswith("trend_") and column != "trend_filter_status"]
    assert set(signalled["signal_reason"]) <= {ICHIMOKU_BUY_REASON, ICHIMOKU_SELL_REASON}
    assert (signalled["signal_reason"][signalled["signal"].astype(int) == 1] == ICHIMOKU_BUY_REASON).all()
    for _, row in signalled.iterrows():  # mỗi tín hiệu đúng ba điều kiện và nến trước đó thì chưa đủ
        side = int(row["signal"])
        cloud_edge = max(row["senkou_a"], row["senkou_b"]) if side == 1 else min(row["senkou_a"], row["senkou_b"])
        assert side * (row["tenkan"] - row["kijun"]) > 0 and side * (row["close"] - row["close_lag"]) > 0 and side * (row["close"] - cloud_edge) > 0


def test_ichimoku_tham_so_va_8_timeframe_da_chot(rbars: pd.DataFrame) -> None:
    assert normalize_ichimoku_params(None, "US30", "H1") == {
        "TENKAN_PERIOD": 9, "KIJUN_PERIOD": 26, "SENKOU_B_PERIOD": 52, "DISPLACEMENT": 26, "ATR_PERIOD": 5, "ALTERNATING_FILTER_ENABLED": True,
    }
    assert normalize_ichimoku_params({"TENKAN_PERIOD": 99999})["TENKAN_PERIOD"] == 100  # max
    assert normalize_ichimoku_params({"TENKAN_PERIOD": 0})["TENKAN_PERIOD"] == 2  # min
    assert normalize_ichimoku_params({"SENKOU_B_PERIOD": 99999})["SENKOU_B_PERIOD"] == 300
    assert normalize_ichimoku_params({"DISPLACEMENT": 0})["DISPLACEMENT"] == 1 and normalize_ichimoku_params({"DISPLACEMENT": 500})["DISPLACEMENT"] == 100
    assert normalize_ichimoku_params({"KIJUN_PERIOD": "abc"})["KIJUN_PERIOD"] == 26  # sai kiểu -> mặc định từ config
    assert normalize_ichimoku_params({"ALTERNATING_FILTER_ENABLED": "false"})["ALTERNATING_FILTER_ENABLED"] is False
    assert "TREND_TF" not in normalize_ichimoku_params({"TREND_TF": "H4", "TREND_METHODS": "knn"}, "US30", "H1")  # không có trend: khoá trend bị bỏ qua
    spec = get_strategy("ichimoku")
    assert set(spec.recommended_timeframes) == set(spec.supported_timeframes) == set(ICHIMOKU_TIMEFRAMES)
    for tf in ICHIMOKU_TIMEFRAMES:
        assert not run_strategy("ichimoku", symbol="US30", tf=tf, bars=rbars).empty
    for tf in ("M5", "M15", "H6", "D1"):
        with pytest.raises(ValueError, match="supports only these timeframes"):
            run_strategy("ichimoku", symbol="US30", tf=tf, bars=rbars)


def test_ichimoku_chu_ky_khac_dung_chu_ky_cua_config_khong_phai_so_cung(rbars: pd.DataFrame) -> None:
    custom = {"TENKAN_PERIOD": 7, "KIJUN_PERIOD": 20, "SENKOU_B_PERIOD": 40, "DISPLACEMENT": 20, "ATR_PERIOD": 8}
    out = add_ichimoku_indicators(rbars, normalize_ichimoku_params(custom))
    first_valid = {column: int(out[column].first_valid_index()) for column in ("tenkan", "kijun", "senkou_a", "senkou_b", "close_lag")}
    assert first_valid == {"tenkan": 6, "kijun": 19, "senkou_a": 39, "senkou_b": 59, "close_lag": 20}
    default = add_ichimoku_indicators(rbars, normalize_ichimoku_params(None))
    assert not out["senkou_b"].equals(default["senkou_b"])


def test_ichimoku_khong_co_trend(rbars: pd.DataFrame) -> None:
    spec = get_strategy("ichimoku")
    assert spec.uses_trend is False and spec.trend_timeframes == ()
    assert alternating_filter_for("ichimoku") is True
    for tf in ICHIMOKU_TIMEFRAMES:
        assert not {"TREND_METHODS", "TREND_TF", "TREND_FILTER_ENABLED"} & set(spec.normalize_params(None, "US30", tf))  # kế hoạch của ichimoku không được khai trend (xem test_config_guards)


# =============================================================================
# 14. macd_break không dùng khối breakout ở bất kỳ khung nào (excluded_trend_methods, chốt lại 2026-10-28, đồng bộ we_core/des); bốn chiến lược dùng MACD khai macd_h là cột xác nhận.
# =============================================================================


def test_khung_chay_duoc_cua_nam_chien_luoc() -> None:
    assert get_strategy("macd_break").excluded_trend_methods == ("breakout",)
    # cả năm chiến lược chạy được ở cả 8 khung M10–H4 (2026-11-02); khung trend của từng (symbol, khung vào lệnh) là bảng của we trong config
    for key in STRATEGIES:
        assert set(get_strategy(key).live_timeframes) == set(MACD_BREAK_TIMEFRAMES), key


def test_macd_break_khong_dung_breakout_o_moi_khung() -> None:
    assert get_strategy("macd_break").excluded_trend_methods == ("breakout",)
    for key in ("combo", "ma_cross", "ema_reach"):  # chỉ macd_break khai khoá
        assert get_strategy(key).excluded_trend_methods == ()
    for tf in ("M10", "M45", "H1", "H4"):  # đè TREND_METHODS bằng khối bị loại là lỗi, ở mọi khung
        with pytest.raises(ValueError, match="cannot be used with this strategy"):
            get_strategy("macd_break").normalize_params({"TREND_METHODS": "breakout"}, "US30", tf)
    for method in ("knn", "zigzag", "tstat"):  # khối cần khung tham chiếu riêng: kế hoạch khai tf
        assert get_strategy("macd_break").normalize_params({"TREND_METHODS": method, "TREND_TF": "H2"}, "US30", "H1")["TREND_METHODS"] == (method,)
    assert get_strategy("macd_break").normalize_params({"TREND_METHODS": "cloud"}, "US30", "H1")["TREND_TF"] == "H1"  # cloud tự suy ra khung từ khung vào lệnh


@pytest.mark.parametrize(
    "config, message",
    [
        ({"excluded_trend_methods": "breakout"}, "must be a list"),
        ({"excluded_trend_methods": ["nope"]}, "unknown trend method"),
        ({"excluded_trend_methods": ["breakout", "breakout"]}, "more than once"),
    ],
)
def test_excluded_trend_methods_sai_cau_hinh_bi_tu_choi(config: dict, message: str) -> None:
    from core_engine.src.configuration import _excluded_trend_methods

    with pytest.raises((ValueError, TypeError), match=message):
        _excluded_trend_methods(config, "strategies.x")
    assert _excluded_trend_methods({}, "strategies.x") == ()
    assert _excluded_trend_methods({"excluded_trend_methods": []}, "strategies.x") == ()
    assert _excluded_trend_methods({"excluded_trend_methods": [" Breakout "]}, "strategies.x") == ("breakout",)


# =============================================================================
# 15. ema_reach (we_core thêm 2026-10-30, DE đồng bộ cùng ngày): EMA 13/34 + phá đỉnh/đáy close 20 nến + MACD Histogram.
# Cùng khuôn ma_cross/macd_break: lọc lặp cùng chiều TRƯỚC (raw), lọc trend SAU (các nguồn knn/breakout/zigzag/tstat/cloud),
# entry = close (lệnh MARKET). Luật là bản sao của we_core (kiểm AST từng hàm giống hệt).
# =============================================================================


def _random_bars(n: int, seed: int, drift: float = 0.0, freq: str = "30min") -> pd.DataFrame:
    """Nến bước ngẫu nhiên tất định theo seed (không phụ thuộc phiên bản numpy khác ngoài default_rng cố định)."""
    rng = np.random.default_rng(seed)
    close = 1000.0 + np.cumsum(rng.normal(drift, 3.0, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame(
        {
            "bartime": pd.date_range("2026-01-05", periods=n, freq=freq).astype("datetime64[us]"),
            "open": open_,
            "high": np.maximum(open_, close) + np.abs(rng.normal(0.0, 1.5, n)),
            "low": np.minimum(open_, close) - np.abs(rng.normal(0.0, 1.5, n)),
            "close": close,
            "volume": np.nan,
        }
    )


def _ema_reach_frame(rows):
    """DataFrame tối thiểu cho detect_ema_reach_signals: mỗi dòng là (close, ema_fast, ema_slow, prior_high, prior_low, macd_h, atr)."""
    return pd.DataFrame(
        {
            "bartime": pd.date_range("2026-01-05", periods=len(rows), freq="30min"),
            "close": [r[0] for r in rows],
            "ema_fast": [r[1] for r in rows],
            "ema_slow": [r[2] for r in rows],
            "prior_high_close": [r[3] for r in rows],
            "prior_low_close": [r[4] for r in rows],
            "macd_h": [r[5] for r in rows],
            "atr": [r[6] for r in rows],
        }
    )


def _ema_reach_reference(df, *, fast=13, slow=34, lookback=20, macd=(5, 25, 5), alternating=True):
    """Bản viết lại ĐỘC LẬP bằng vòng lặp thuần Python (không dùng indicator.py / strategies / rules): trả mảng raw_signal."""
    closes = df["close"].astype(float).tolist()

    def ema(values, period):
        alpha = 2.0 / (period + 1)
        out = [values[0]]
        for value in values[1:]:
            out.append(alpha * value + (1 - alpha) * out[-1])
        return out

    fast_line, slow_line = ema(closes, fast), ema(closes, slow)
    macd_line = [a - b for a, b in zip(ema(closes, macd[0]), ema(closes, macd[1]), strict=True)]
    hist = [a - b for a, b in zip(macd_line, ema(macd_line, macd[2]), strict=True)]
    state, signals = 0, []
    for i, close in enumerate(closes):
        buy = sell = False
        if i >= max(slow - 1, lookback, 4):  # EMA chậm đủ nến, đủ lookback, ATR(5) đã có giá trị
            window = closes[i - lookback : i]
            buy = fast_line[i] > slow_line[i] and close >= max(window) and hist[i] > 0
            sell = fast_line[i] < slow_line[i] and close <= min(window) and hist[i] < 0
        signal = 0
        if alternating:
            if buy and state != 1:
                signal, state = 1, 1
            elif sell and state != -1:
                signal, state = -1, -1
        else:
            signal = 1 if buy else (-1 if sell else 0)
        signals.append(signal)
    return signals


EMA_REACH_BUY_REASON = "EMA fast above EMA slow, close broke above prior highest close, MACD histogram > 0"
EMA_REACH_SELL_REASON = "EMA fast below EMA slow, close broke below prior lowest close, MACD histogram < 0"


def test_ema_reach_buy_sell_can_ca_ba_yeu_to() -> None:
    nan = float("nan")
    frame = _ema_reach_frame(
        [
            (110, 12, 10, 105, 95, 0.5, 1.0),  # cả ba đúng (EMA lên, phá đỉnh, MACD > 0)        -> BUY
            (110, 10, 12, 105, 95, 0.5, 1.0),  # phá đỉnh + MACD > 0 nhưng EMA nhanh < chậm       -> không
            (110, 12, 10, 105, 95, -0.5, 1.0),  # EMA lên + phá đỉnh nhưng MACD < 0                -> không
            (100, 12, 10, 105, 95, 0.5, 1.0),  # EMA lên + MACD > 0 nhưng không phá đỉnh           -> không
            (90, 10, 12, 105, 95, -0.5, 1.0),  # cả ba đúng phía bán                              -> SELL
            (90, 12, 10, 105, 95, -0.5, 1.0),  # phá đáy + MACD < 0 nhưng EMA nhanh > chậm         -> không
            (90, 10, 12, 105, 95, 0.5, 1.0),  # EMA xuống + phá đáy nhưng MACD > 0               -> không
            (105, 12, 10, 105, 95, 0.5, 1.0),  # close == đỉnh cũ (bằng tính)                     -> BUY
            (95, 10, 12, 105, 95, -0.5, 1.0),  # close == đáy cũ (bằng tính)                      -> SELL
            (110, 11, 11, 105, 95, 0.5, 1.0),  # EMA nhanh == chậm                                 -> không
            (110, 12, 10, 105, 95, 0.0, 1.0),  # MACD đúng 0                                      -> không
            (110, nan, 10, 105, 95, 0.5, 1.0),  # thiếu EMA nhanh (chưa đủ nến)                    -> không
            (110, 12, nan, 105, 95, 0.5, 1.0),  # thiếu EMA chậm                                   -> không
            (110, 12, 10, nan, 95, 0.5, 1.0),  # thiếu prior_high (chưa đủ lookback)               -> không
            (110, 12, 10, 105, 95, 0.5, nan),  # thiếu atr                                        -> không
        ]
    )
    out = detect_ema_reach_signals(frame, params={"ALTERNATING_FILTER_ENABLED": False})

    assert out["signal"].tolist() == [1, 0, 0, 0, -1, 0, 0, 1, -1, 0, 0, 0, 0, 0, 0]
    assert out["raw_signal"].tolist() == out["signal"].tolist()  # trend tắt: signal == raw_signal
    assert out["signal_reason"].iloc[0] == EMA_REACH_BUY_REASON
    assert out["signal_reason"].iloc[4] == EMA_REACH_SELL_REASON


def test_ema_reach_chong_trung_chi_ban_nen_dau_khi_cung_chieu_va_tat_co_thi_nen_nao_cung_ban() -> None:
    buy = (110, 12, 10, 105, 95, 0.5, 1.0)
    sell = (90, 10, 12, 105, 95, -0.5, 1.0)
    frame = _ema_reach_frame([buy, buy, buy, sell, sell, buy])

    on = detect_ema_reach_signals(frame, params={"ALTERNATING_FILTER_ENABLED": True})
    off = detect_ema_reach_signals(frame, params={"ALTERNATING_FILTER_ENABLED": False})

    assert on["signal"].tolist() == [1, 0, 0, -1, 0, 1]
    assert off["signal"].tolist() == [1, 1, 1, -1, -1, 1]


def test_ema_reach_chi_bao_ema_nan_den_du_nen_loai_nen_hien_tai_khoi_dinh_day_va_khong_nhin_truoc() -> None:
    params = normalize_ema_reach_params({"LOOKBACK_BARS": 3, "FAST_EMA": 4, "SLOW_EMA": 6}, "US30", "M30")
    df = _random_bars(80, seed=3)
    out = add_ema_reach_indicators(df, params)

    # EMA NaN cho tới khi đủ `period` nến (cùng quy ước SMA / ta.ema), rồi bằng ewm(span, adjust=False) của close
    assert out["ema_fast"].iloc[:3].isna().all() and out["ema_fast"].iloc[3:].notna().all()
    assert out["ema_slow"].iloc[:5].isna().all() and out["ema_slow"].iloc[5:].notna().all()
    assert out["ema_slow"].iloc[40] == pytest.approx(df["close"].ewm(span=6, adjust=False).mean().iloc[40])
    # đỉnh/đáy của N nến TRƯỚC, chỉ dùng close, không gồm nến hiện tại
    assert out["prior_high_close"].iloc[:3].isna().all()
    assert out["prior_high_close"].iloc[3] == df["close"].iloc[0:3].max()
    assert out["prior_low_close"].iloc[10] == df["close"].iloc[7:10].min()
    assert out["prior_high_close"].iloc[10] != df["high"].iloc[7:10].max()
    assert {"macd_h", "atr"}.issubset(out.columns)
    # không nhìn trước: chỉ báo trên tiền tố == chỉ báo trên toàn chuỗi rồi cắt
    prefix = add_ema_reach_indicators(df.iloc[:50].reset_index(drop=True), params)
    for column in ("ema_fast", "ema_slow", "prior_high_close", "prior_low_close", "macd_h", "atr"):
        pd.testing.assert_series_equal(prefix[column], out[column].iloc[:50].reset_index(drop=True), check_names=False)


@pytest.mark.parametrize("alternating", [True, False])
def test_ema_reach_khop_ban_viet_lai_doc_lap_tren_nhieu_chuoi_ngau_nhien(alternating: bool) -> None:
    for seed in range(1, 41):
        df = _random_bars(500, seed=seed, drift=0.02 * (seed % 5 - 2))
        out = run_strategy("ema_reach", symbol="US30", tf="M30", bars=df, overrides={"ALTERNATING_FILTER_ENABLED": alternating})
        assert out["raw_signal"].tolist() == _ema_reach_reference(df, alternating=alternating), f"seed {seed}"
        assert out["signal"].tolist() == out["raw_signal"].tolist()  # trend tắt


def test_ema_reach_khong_nhin_truoc(rbars: pd.DataFrame) -> None:
    full = run_strategy("ema_reach", symbol="US30", tf="M30", bars=rbars)
    prefix = run_strategy("ema_reach", symbol="US30", tf="M30", bars=rbars.iloc[:350].reset_index(drop=True))

    assert full["signal"].iloc[:350].tolist() == prefix["signal"].tolist()


def test_ema_reach_golden_tren_bo_nen_dao_dong(rbars: pd.DataFrame) -> None:
    """Golden của ema_reach trên range_bars() (bước M30): số tín hiệu, tín hiệu đầu, digest 3 cột signal/entry_price/atr."""
    enriched = run_strategy("ema_reach", symbol="US30", tf="M30", bars=rbars)
    sig = enriched["signal"].astype(int)

    assert (int((sig == 1).sum()), int((sig == -1).sum())) == GOLDEN_EMA_REACH["on"]
    assert int(sig[sig != 0].index[0]) == GOLDEN_EMA_REACH["first_idx"]
    assert _pipeline_digest(enriched) == GOLDEN_EMA_REACH["digest"]
    rows = enriched[sig != 0]
    assert all(a != b for a, b in pairwise(rows["signal"].astype(int).tolist()))  # BUY/SELL xen kẽ tuyệt đối
    assert rows["entry_price"].eq(rows["close"]).all()  # entry = close, không đệm
    off = run_strategy("ema_reach", symbol="US30", tf="M30", bars=rbars, overrides={"ALTERNATING_FILTER_ENABLED": "false"})
    off_sig = off["signal"].astype(int)
    assert (int((off_sig == 1).sum()), int((off_sig == -1).sum())) == GOLDEN_EMA_REACH["off"]


def test_ema_reach_tham_so_mac_dinh_kiem_tra_va_khung_theo_o() -> None:
    params = normalize_ema_reach_params({}, "US30", "M30")

    assert (params["FAST_EMA"], params["SLOW_EMA"], params["LOOKBACK_BARS"]) == (13, 34, 20)
    assert (params["MACD_FAST"], params["MACD_SLOW"], params["MACD_SIGNAL"]) == (5, 25, 5)
    assert params["ATR_PERIOD"] == 5
    assert params["TREND_FILTER_ENABLED"] is False and params["ALTERNATING_FILTER_ENABLED"] is True
    assert params["TREND_METHODS"] == () and params["TREND_TF"] == ""  # khối và khung trend do kế hoạch của symbol khai (plans.<symbol>.trend), không có mặc định
    spec = get_strategy("ema_reach")
    assert spec.supported_timeframes == ("M10", "M20", "M30", "M45", "H1", "H2", "H3", "H4") and spec.recommended_timeframes == spec.supported_timeframes
    assert spec.excluded_trend_methods == ()  # ema_reach dùng được mọi khối trend (kể cả breakout)

    with pytest.raises(ValueError, match="FAST_EMA must be smaller than SLOW_EMA"):
        normalize_ema_reach_params({"FAST_EMA": "40", "SLOW_EMA": "34"})
    with pytest.raises(ValueError, match="MACD_FAST must be smaller than MACD_SLOW"):
        normalize_ema_reach_params({"MACD_FAST": "30", "MACD_SLOW": "25"})
    with pytest.raises(ValueError, match="supports only these timeframes"):  # ema_reach chạy cả 8 khung M10–H4 (2026-11-02): chặn cứng kiểm bằng khung ngoài danh sách
        run_strategy("ema_reach", symbol="US30", tf="M5", bars=_random_bars(150, seed=1, freq="5min"))
    assert len(run_strategy("ema_reach", symbol="US30", tf="H1", bars=_random_bars(150, seed=1, freq="1h"))) == 150  # khung H nay được phép
    # LOOKBACK_BARS kẹp tối đa 300: DE đọc 500 nến, lookback dài hơn thì chiến lược im lặng không bao giờ bắn
    assert normalize_ema_reach_params({"LOOKBACK_BARS": "5000"})["LOOKBACK_BARS"] == 300


def test_ema_reach_entry_la_close_va_khong_co_cot_tam_thoi(rbars: pd.DataFrame) -> None:
    out = run_strategy("ema_reach", symbol="US30", tf="M30", bars=rbars)

    kept = out[out["signal"] != 0]
    assert kept["entry_price"].eq(kept["close"]).all() and out.loc[out["signal"] == 0, "entry_price"].isna().all()
    assert {"ema_fast", "ema_slow", "prior_high_close", "prior_low_close", "macd_h", "atr"}.issubset(out.columns)
    assert "raw_signal_reason" not in out.columns


def test_cau_hinh_that_va_mau_ema_reach_khai_du_va_dong_bo_voi_we_core() -> None:
    from pathlib import Path

    import yaml

    from core_engine.src.configuration import CONFIG

    example = yaml.safe_load((Path(__file__).resolve().parents[1] / "de_config.example.yaml").read_text(encoding="utf-8"))
    for name, cfg in (("config thật", CONFIG), ("config mẫu", example)):
        assert cfg["live"]["signal_validity"]["ema_reach"] == {"mode": "seconds_after_bar_close", "seconds": 600}, name  # như ma_cross
        spec = cfg["strategies"]["ema_reach"]
        assert (spec["fast_ema"], spec["slow_ema"], spec["lookback_bars"]) == (13, 34, 20), name
        assert (spec["macd_fast"], spec["macd_slow"], spec["macd_signal"], spec["atr_period"]) == (5, 25, 5, 5), name
        assert spec["supported_timeframes"] == ["M10", "M20", "M30", "M45", "H1", "H2", "H3", "H4"], name
        assert spec["alternating_filter_enabled"] is True, name
        assert spec["trend_timeframes"] == ["H1", "H2", "H3", "H4"] and "excluded_trend_methods" not in spec, name
        assert not {"trend_methods", "trend_off_timeframes"} & set(spec) and "trend_tf_by_symbol" in spec, name  # khung trend theo (symbol, khung vào lệnh) là bảng của we; khối trend khai ở redis.trend_outputs


# Golden tính từ CHÍNH pipeline DE trên range_bars() (bước M30, tf M30): số (BUY, SELL) khi bật / tắt lọc lặp, chỉ số tín hiệu đầu, digest 3 cột.
GOLDEN_EMA_REACH = {
    "on": (6, 7),
    "off": (110, 119),
    "first_idx": 36,
    "digest": "4595683e3b35f6257bb0b7ee334498c57b05fc4a9f1164a551470d7e4db20b19",
}


def test_gioi_han_va_luat_thu_tu_tham_so_cua_nam_chien_luoc() -> None:
    """Khoá bảng giới hạn min/max và các cặp "nhỏ < lớn" của từng chiến lược (đổi chúng đổi cách kẹp tham số mà không đổi tên khoá nào)."""
    assert {key: spec.bounds for key, spec in STRATEGIES.items()} == {
        "combo": {"MA_PERIOD": (2, 500), "MACD_FAST": (1, 200), "MACD_SLOW": (2, 300), "MACD_SIGNAL": (1, 200), "ATR_PERIOD": (2, 200)},
        "ma_cross": {"FAST_MA": (1, 300), "SLOW_MA": (2, 500), "MACD_FAST": (1, 200), "MACD_SLOW": (2, 300), "MACD_SIGNAL": (1, 200), "ATR_PERIOD": (2, 200)},
        "ema_reach": {"FAST_EMA": (1, 300), "SLOW_EMA": (2, 300), "LOOKBACK_BARS": (2, 300), "MACD_FAST": (1, 200), "MACD_SLOW": (2, 300), "MACD_SIGNAL": (1, 200), "ATR_PERIOD": (2, 200)},
        "macd_break": {"LOOKBACK_BARS": (2, 300), "MACD_FAST": (1, 200), "MACD_SLOW": (2, 300), "MACD_SIGNAL": (1, 200), "ATR_PERIOD": (2, 200)},
        "ichimoku": {"TENKAN_PERIOD": (2, 100), "KIJUN_PERIOD": (2, 200), "SENKOU_B_PERIOD": (2, 300), "DISPLACEMENT": (1, 100), "ATR_PERIOD": (2, 200)},
    }
    assert {key: spec.ordered_pairs for key, spec in STRATEGIES.items() if spec.ordered_pairs} == {
        "ma_cross": (("FAST_MA", "SLOW_MA"), ("MACD_FAST", "MACD_SLOW")),
        "ema_reach": (("FAST_EMA", "SLOW_EMA"), ("MACD_FAST", "MACD_SLOW")),
        "macd_break": (("MACD_FAST", "MACD_SLOW"),),
    }  # combo không kiểm MACD_FAST < MACD_SLOW (giữ nguyên hành vi cũ)
