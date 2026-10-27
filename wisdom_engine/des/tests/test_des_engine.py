"""Test characterization cho des (bản mô phỏng của OG) — hermetic, KHÔNG đụng Redis.

Mọi giá trị mong đợi trong file này được tính từ CHÍNH pipeline thật trên bộ
nến tổng hợp tất định bên dưới (không phải số bịa). Mục đích: khoá lại hành
vi hiện tại để phát hiện thay đổi logic ngoài ý muốn. Khi sửa logic chiến
lược CÓ CHỦ ĐÍCH, phải chạy lại pipeline để tính giá trị mới rồi cập nhật
test — không tự đoán số.

Cần des_config.yaml tồn tại (hệ thống chỉ chạy trên vm-og, luôn có sẵn file
thật). KHÔNG mở kết nối Redis nào.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import hashlib
import logging
import types
from itertools import pairwise

import numpy as np
import pandas as pd
import pytest
from wisdom_engine.des.src.configuration import (
    COMBO_TREND_TIMEFRAMES,
    STRATEGIES,
    TrendSource,
    alternating_filter_for,
    get_strategy,
    normalize_combo_params,
    normalize_ma_cross_params,
    normalize_macd_break_params,
    run_strategy,
    run_strategy_with_trend_reference,
    trend_filter_enabled_for,
    trend_source_for,
)
from wisdom_engine.des.src.indicator import atr, rma, sma
from wisdom_engine.des.src.redis_io.candle_reader import (
    OUTPUT_SOURCES,
    candle_key,
    normalize_redis_config,
    pair_list_key,
    parse_pair_key,
)
from wisdom_engine.des.src.redis_io.live_worker import (
    signal_valid_from,
    strategies_for_timeframe,
    timeframe_duration,
)
from wisdom_engine.des.src.redis_io.signal_publisher import (
    build_signal,
    clear_signals,
    og_list_key,
    publish_signal,
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
# tiên của mỗi chiều không đổi, ma_cross không đổi. ma_cross tính lại 2026-10-16 khi đồng bộ với we_core
# (luật trạng thái + lọc lặp cùng chiều, thay luật cắt): trước đó 7 BUY / 6 SELL, SELL đầu ở bar 86,
# digest d456518f...; bar BUY đầu tiên không đổi (57), combo không đổi. Combo tính lại 2026-10-21 khi entry đổi
# thành high (BUY) / low (SELL) không đệm X: số tín hiệu và bar không đổi, chỉ cột entry_price đổi đúng ∓10 (X của US30
# trước đó) — đã kiểm bằng cách dựng lại entry cũ (entry mới ± 10) rồi băm: ra đúng digest cũ ee5a702a... (cờ tắt: 1fb2c847...).
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


def test_mac_dinh_trend_filter_phai_tat() -> None:
    """Mặc định vận hành: cả 3 chiến lược đều TẮT cờ trend filter (đường 1 khung không lọc trend). Nếu ai commit
    des_config.yaml với true, test này đỏ — vì đó là thay đổi hành vi production, phải cố ý. Khung trend của từng nguồn
    lọc: knn và zigzag tra bảng trend_tf_by_symbol (khung lớn H1..H4); breakout = chính khung của tín hiệu.
    """
    for key, entry_tf in (("combo", "H1"), ("ma_cross", "M30"), ("macd_break", "H1")):
        assert trend_filter_enabled_for(key) is False, f"{key}: trend filter đang BẬT trong des_config.yaml"
        for source in ("knn", "zigzag"):
            assert trend_source_for(key, "US30", entry_tf, source).trend_tf in COMBO_TREND_TIMEFRAMES
        assert trend_source_for(key, "US30", entry_tf, "breakout").trend_tf == entry_tf
        # ô ngoài bảng (symbol không vận hành): khối cần khung tham chiếu riêng chưa có khung trend (không còn mặc định chung)
        assert trend_source_for(key, "EURUSD", entry_tf, "knn").trend_tf == ""


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
    ("ma_cross", "M30", "H2", 2, 14, 13),  # raw 13 -> 14 khi đồng bộ luật ma_cross với we_core (2026-10-16)
    ("ma_cross", "M30", "H3", 3, 14, 3),
    ("ma_cross", "M30", "H4", 4, 14, 0),
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
        overrides={"TREND_METHODS": "knn"},  # đường 2 khung cần khối có khung tham chiếu riêng; mặc định combo/ma_cross là breakout
    )

    assert {"trend_bias", "trend_ai_knn", "trend_ai_avg", "trend_close_time"} <= set(on.columns)
    assert not [col for col in on.columns if col.startswith("trend_bias_")]  # cột hướng riêng từng khối ở lại khung trend

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


def test_trend_methods_chi_chap_khoi_da_dang_ky() -> None:
    with pytest.raises(ValueError, match=r"unknown trend method\(s\): ema"):
        normalize_combo_params({"TREND_METHODS": "ema"}, "US30", "H1")
    assert normalize_combo_params({"TREND_METHODS": " KNN "}, "US30", "H1")["TREND_METHODS"] == ("knn",)


def test_trend_tf_ngoai_danh_sach_bi_chan() -> None:
    with pytest.raises(ValueError, match="TREND_TF must be one of"):
        normalize_combo_params({"TREND_METHODS": "knn", "TREND_TF": "M5"}, "US30", "H1")  # knn chỉ nhận H1–H4
    with pytest.raises(ValueError, match="always use the entry timeframe H1; TREND_TF M5 is not allowed"):
        normalize_combo_params({"TREND_METHODS": "breakout", "TREND_TF": "M5"}, "US30", "H1")  # breakout: chỉ khung của tín hiệu


def test_strategies_gom_combo_ma_cross_va_macd_break() -> None:
    assert sorted(STRATEGIES) == ["combo", "ma_cross", "macd_break"]


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
# 4. Entry. OG không còn SL/TP (OF tự tính từ KSL/KTP của họ, 2026-10-04).
# =============================================================================


@pytest.mark.parametrize("strategy,tf", [("combo", "H1"), ("ma_cross", "M30"), ("macd_break", "H1")])
def test_og_khong_con_cot_sl_tp(bars: pd.DataFrame, strategy: str, tf: str) -> None:
    df = run_strategy(strategy, symbol="US30", tf=tf, bars=bars)
    assert not {"sl_price", "tp_price", "ksl", "ktp"} & set(df.columns)


def _alternating(setups: str) -> list[int]:
    """"B"/"S" = bar có setup BUY/SELL, "." = không có setup -> signal sau lọc xen kẽ."""
    from wisdom_engine.des.src.rules.alternating import alternating_signals

    buy = pd.Series([c == "B" for c in setups])
    sell = pd.Series([c == "S" for c in setups])
    return alternating_signals(buy, sell).tolist()


def test_alternating_chi_ban_tin_hieu_dau_khi_doi_chieu() -> None:
    assert _alternating("BBSSBBS") == [1, 0, -1, 0, 1, 0, -1]
    assert _alternating("B..B..S.S") == [1, 0, 0, 0, 0, 0, -1, 0, 0]  # bar không setup không đổi trạng thái
    assert _alternating("SSB") == [-1, 0, 1]  # chiều đầu tiên của cửa sổ luôn bắn
    assert _alternating("......") == [0] * 6


def test_alternating_coi_nan_la_khong_co_setup() -> None:
    from wisdom_engine.des.src.rules.alternating import alternating_signals

    buy = pd.Series([True, float("nan"), True], dtype="object")
    sell = pd.Series([False, False, False])
    assert alternating_signals(buy, sell).tolist() == [1, 0, 0]


def test_apply_alternating_filter_bat_tat_theo_co() -> None:
    from wisdom_engine.des.src.rules.alternating import (
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


def test_co_alternating_doc_tu_config_o_ca_ba_chien_luoc() -> None:
    from wisdom_engine.des.src.configuration import (
        COMBO_DEFAULT_PARAMS,
        COMBO_PARAM_FIELDS,
        MA_CROSS_DEFAULT_PARAMS,
        MA_CROSS_PARAM_FIELDS,
        MACD_BREAK_DEFAULT_PARAMS,
        MACD_BREAK_PARAM_FIELDS,
    )

    assert alternating_filter_for("combo") is True
    assert alternating_filter_for("ma_cross") is True  # từ 2026-10-16 ma_cross cũng có cờ, như we_core
    assert COMBO_DEFAULT_PARAMS["ALTERNATING_FILTER_ENABLED"] is True
    assert {"key": "ALTERNATING_FILTER_ENABLED", "label": "Alternating Filter", "type": "bool"} in COMBO_PARAM_FIELDS
    assert normalize_combo_params(None, "US30", "H1")["ALTERNATING_FILTER_ENABLED"] is True
    assert normalize_combo_params({"ALTERNATING_FILTER_ENABLED": "false"}, "US30", "H1")["ALTERNATING_FILTER_ENABLED"] is False
    assert MA_CROSS_DEFAULT_PARAMS["ALTERNATING_FILTER_ENABLED"] is True
    assert {"key": "ALTERNATING_FILTER_ENABLED", "label": "Alternating Filter", "type": "bool"} in MA_CROSS_PARAM_FIELDS
    assert normalize_ma_cross_params(None, "US30", "M30")["ALTERNATING_FILTER_ENABLED"] is True
    assert normalize_ma_cross_params({"ALTERNATING_FILTER_ENABLED": "false"}, "US30", "M30")["ALTERNATING_FILTER_ENABLED"] is False
    # macd_break (đăng ký 2026-10-14) có cờ như combo, mặc định bật.
    assert alternating_filter_for("macd_break") is True
    assert MACD_BREAK_DEFAULT_PARAMS["ALTERNATING_FILTER_ENABLED"] is True
    assert {"key": "ALTERNATING_FILTER_ENABLED", "label": "Alternating Filter", "type": "bool"} in MACD_BREAK_PARAM_FIELDS
    assert normalize_macd_break_params(None, "US30", "H1")["ALTERNATING_FILTER_ENABLED"] is True
    assert (
        normalize_macd_break_params({"ALTERNATING_FILTER_ENABLED": "false"}, "US30", "H1")["ALTERNATING_FILTER_ENABLED"]
        is False
    )


def test_config_mau_khai_bao_co_alternating_cho_ca_ba_chien_luoc() -> None:
    from pathlib import Path

    import yaml

    example = yaml.safe_load((Path(__file__).resolve().parents[1] / "des_config.example.yaml").read_text(encoding="utf-8"))

    assert example["strategies"]["combo"]["alternating_filter_enabled"] is True
    assert example["strategies"]["ma_cross"]["alternating_filter_enabled"] is True
    # macd_break: có mục cấu hình + được bật trong live.enabled_strategies của bản mẫu.
    assert example["strategies"]["macd_break"]["alternating_filter_enabled"] is True
    assert "macd_break" in example["live"]["enabled_strategies"]
    assert example["strategies"]["macd_break"]["supported_timeframes"] == ["M10", "M20", "M30", "M45", "H1", "H2", "H3", "H4"]


def test_combo_tat_co_giong_het_hanh_vi_truoc_khi_co_loc_lap_cung_chieu(bars: pd.DataFrame) -> None:
    """Cờ tắt phải cho đúng golden của combo trước 2026-10-11 (2 BUY / 3 SELL), với entry mới (2026-10-21) không đệm X:
    digest cũ 1fb2c847... đổi thành f34265d8...; entry mới ± 10 (X cũ của US30) dựng lại đúng digest cũ."""
    df = run_strategy("combo", symbol="US30", tf="H1", bars=bars, overrides={"ALTERNATING_FILTER_ENABLED": "false"})
    sig = df["signal"].astype(int)

    assert int((sig == 1).sum()) == 2
    assert int((sig == -1).sum()) == 3
    assert int(sig[sig == 1].index[0]) == GOLDEN["combo"]["first_buy_idx"]  # bar đầu mỗi chiều không đổi
    assert int(sig[sig == -1].index[0]) == GOLDEN["combo"]["first_sell_idx"]
    assert _pipeline_digest(df).startswith("f34265d8")
    old = df.copy()                       # dựng lại entry cũ = high + 10 (BUY) / low − 10 (SELL): phải ra đúng digest trước 2026-10-21
    old.loc[sig == 1, "entry_price"] = df.loc[sig == 1, "entry_price"] + 10.0
    old.loc[sig == -1, "entry_price"] = df.loc[sig == -1, "entry_price"] - 10.0
    assert _pipeline_digest(old).startswith("1fb2c847")


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
    from wisdom_engine.des.src.strategies.combo import detect_combo_signals

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


def test_combo_entry_la_mep_nen_tin_hieu_khong_dem(bars: pd.DataFrame) -> None:
    """Combo là lệnh STOP đặt đúng mép nến tín hiệu (từ 2026-10-21): entry BUY = high, SELL = low, không đệm X."""
    df = run_strategy("combo", symbol="US30", tf="H1", bars=bars)
    sig = df["signal"].astype(int)
    buy, sell = df[sig == 1], df[sig == -1]
    assert len(buy) > 0 and len(sell) > 0
    np.testing.assert_array_equal(buy["entry_price"].to_numpy(), buy["high"].to_numpy())
    np.testing.assert_array_equal(sell["entry_price"].to_numpy(), sell["low"].to_numpy())
    assert (buy["entry_price"] >= buy["close"]).all() and (sell["entry_price"] <= sell["close"]).all()
    assert df.loc[sig == 0, "entry_price"].isna().all()


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


def test_key_tin_hieu_theo_strategy_symbol_tf_va_khong_con_kenh_pubsub() -> None:
    """3 chiều, STRATEGY đứng đầu (chốt 2026-10-04): key tín hiệu L_DES_{STRATEGY}_{SYMBOL}_{TF}. Không còn tên kênh
    Pub/Sub nào (quyết định 2026-10-25: des chỉ lưu LIST/HASH, không publish).
    """
    from wisdom_engine.des.src.redis_io import signal_publisher

    key = og_list_key("L_DES", "combo", "us30", "h1")
    assert key == "L_DES_COMBO_US30_H1"
    assert stamp_hash_key(key, "2026-09-22 13:00:00") == "L_DES_COMBO_US30_H1:2026-09-22 13:00:00"
    # ma_cross -> MACROSS (bỏ '_' để token nào cũng không chứa ký tự phân cách).
    assert og_list_key("L_DES", "ma_cross", "US30", "M30") == "L_DES_MACROSS_US30_M30"
    assert strategy_token("MA_CROSS") == "MACROSS"
    assert strategy_token("combo") == "COMBO"
    # macd_break -> MACDBREAK (cùng quy tắc bỏ '_').
    assert og_list_key("L_DES", "macd_break", "us30", "h1") == "L_DES_MACDBREAK_US30_H1"
    assert strategy_token("MACD_BREAK") == "MACDBREAK"
    assert not hasattr(signal_publisher, "channel_name") and not hasattr(signal_publisher, "signal_message")


def _full_redis_config() -> dict:
    return {
        "host": "127.0.0.1",
        "password": "x",
        "input": {"db": 0, "key_prefix": "L_DFS", "snapshot_bars": 500, "process_on_startup": True},
        "outputs": {source: {"db": 1 + index, "key_prefix": "L_DES"} for index, source in enumerate(OUTPUT_SOURCES)},
    }


def test_redis_config_thieu_key_thi_raise() -> None:
    """Key vận hành bắt buộc: input 4 + (db, key_prefix) cho MỖI trong 4 nguồn đầu ra — thiếu phải raise rõ ràng, không fallback.

    input.event_channel KHÔNG còn trong danh sách này (gỡ 2026-09-23): OG không còn trigger qua dp:events:candles, trigger
    qua keyspace notification của chính Redis trên DB nến (xem event_listener.py) — chỉ cần
    db/key_prefix/snapshot_bars/process_on_startup ở input.
    """
    full = _full_redis_config()
    normalized = normalize_redis_config(full)
    assert normalized["input"]["reconcile_interval_seconds"] == 1800
    assert list(normalized["outputs"]) == list(OUTPUT_SOURCES)
    # des không hết hạn/cắt bớt và không Pub/Sub: mỗi nguồn đầu ra CHỈ có db + tiền tố (quyết định 2026-10-07 và 2026-10-25).
    assert all(set(output) == {"db", "key_prefix"} for output in normalized["outputs"].values())
    # input.event_channel thật sự không còn tồn tại trong config đã chuẩn hoá.
    assert "event_channel" not in normalized["input"]

    for key in ("db", "key_prefix", "snapshot_bars", "process_on_startup"):
        broken = {**full, "input": {k: v for k, v in full["input"].items() if k != key}}
        with pytest.raises(KeyError, match=f"redis.input.{key}"):
            normalize_redis_config(broken)
    for source in OUTPUT_SOURCES:
        for key in ("db", "key_prefix"):
            output = {k: v for k, v in full["outputs"][source].items() if k != key}
            broken = {**full, "outputs": {**full["outputs"], source: output}}
            with pytest.raises(KeyError, match=f"redis.outputs.{source}.{key}"):
                normalize_redis_config(broken)
    thieu_nguon = {"outputs": {s: o for s, o in full["outputs"].items() if s != "zigzag"}}
    with pytest.raises(KeyError, match=r"redis\.outputs\.zigzag\.db"):
        normalize_redis_config({**full, **thieu_nguon})


def test_redis_config_tu_choi_khoa_cu_va_nguon_la() -> None:
    """Khoá của thiết kế cũ (output / output_trend / output_best) và nguồn ngoài OUTPUT_SOURCES bị TỪ CHỐI rõ ràng, không âm thầm bỏ qua."""
    full = _full_redis_config()
    for legacy in ("output", "output_trend", "output_best"):
        with pytest.raises(ValueError, match="replaced by redis.outputs"):
            normalize_redis_config({**full, legacy: {"db": 9, "key_prefix": "L_DES"}})
    with pytest.raises(ValueError, match="unknown source"):
        normalize_redis_config({**full, "outputs": {**full["outputs"], "ema": {"db": 9, "key_prefix": "L_DES"}}})


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
    # des KHÔNG đặt hết hạn: không có expirationTimestamp; vẫn giữ valid_from (giờ nến ĐÓNG).
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


def test_publish_ghi_hash_va_list_khong_pubsub(bars: pd.DataFrame) -> None:
    """1 EVAL: 2 key (hash/list của tín hiệu), HSET rồi RPUSH, KHÔNG PUBLISH, KHÔNG nhận tham số kênh."""
    row = _one_signal_row("combo", "H1", bars)
    vf = signal_valid_from("H1", row["bartime"])
    _, stamp, payload = build_signal(
        strategy="combo", timeframe="H1", symbol="US30", row=row, valid_from=vf
    )
    fields = to_hash_fields("combo", payload)
    client = _RecordingRedis()

    assert publish_signal(
        client, key_prefix="L_DES", symbol="US30", timeframe="H1", strategy="combo", stamp=stamp, hash_fields=fields,
    ) is True

    (script, numkeys, args), = client.calls
    assert numkeys == 2  # chỉ hash + list của tín hiệu, không có key nào cho kênh
    assert args[:3] == (f"L_DES_COMBO_US30_H1:{stamp}", "L_DES_COMBO_US30_H1", stamp)
    assert dict(zip(args[3::2], args[4::2], strict=True)) == fields  # Hash = đúng nội dung các field
    assert "PUBLISH" not in script and "KEYS[3]" not in script
    # Trùng lặp chỉ chặn ở hash; các lệnh ghi nằm sau kiểm tra EXISTS.
    assert script.index("EXISTS") < script.index("HSET") < script.index("RPUSH")
    # des giữ lại mọi tín hiệu: script KHÔNG có TTL (EXPIRE) và KHÔNG cắt LIST (LTRIM).
    assert "EXPIRE" not in script and "LTRIM" not in script
    with pytest.raises(TypeError):
        publish_signal(  # type: ignore[call-arg]
            client, key_prefix="L_DES", channel_prefix="L_CHANNEL", symbol="US30", timeframe="H1",
            strategy="combo", stamp=stamp, hash_fields=fields,
        )


def test_publish_ma_cross_dung_token_macross_nhung_giu_field_ma_cross(bars: pd.DataFrame) -> None:
    """Chỉ TÊN key đổi sang MACROSS; field strategy và clientOrderId trong Hash vẫn là tên thật 'ma_cross' (chốt 2026-10-05)."""
    row = _one_signal_row("ma_cross", "M30", bars)
    vf = signal_valid_from("M30", row["bartime"])
    _, stamp, payload = build_signal(
        strategy="ma_cross", timeframe="M30", symbol="US30", row=row, valid_from=vf
    )
    fields = to_hash_fields("ma_cross", payload)
    client = _RecordingRedis()
    publish_signal(
        client, key_prefix="L_DES", symbol="US30", timeframe="M30", strategy="ma_cross", stamp=stamp, hash_fields=fields,
    )
    (_, _, args), = client.calls
    assert args[:2] == (
        f"L_DES_MACROSS_US30_M30:{stamp}",
        "L_DES_MACROSS_US30_M30",
    )
    written = dict(zip(args[3::2], args[4::2], strict=True))
    assert written["strategy"] == "ma_cross"
    assert written["clientOrderId"].startswith("ma_cross:M30:US30:")


def test_publish_trung_thi_bao_false() -> None:
    client = _RecordingRedis(created=0)
    assert publish_signal(
        client, key_prefix="L_DES", symbol="US30", timeframe="H1", strategy="combo",
        stamp="2026-09-22 13:00:00", hash_fields={"symbol": "US30"},
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
    # des không có cửa sổ hiệu lực: hàm cũ signal_window (valid_until) đã bỏ hẳn.
    from wisdom_engine.des.src.redis_io import live_worker

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
# 9b. live_worker: BỐN nguồn (raw / knn / breakout / zigzag), mỗi nguồn một db — hermetic, client giả.
# =============================================================================


class _FakeInput:
    """Trả đúng bộ nến đã dựng sẵn cho từng timeframe, ghi lại TF nào bị đọc (và xin bao nhiêu nến)."""

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames
        self.doc: list[str] = []
        self.requests: list[tuple[str, int]] = []

    def read(self, *, symbol: str, timeframe: str, key_prefix: str, snapshot_bars: int):
        self.doc.append(timeframe)
        self.requests.append((timeframe, snapshot_bars))
        return self.frames.get(timeframe, pd.DataFrame())


def _outputs_config(prefix: str = "L_DES") -> dict:
    """redis.outputs của config thử: mỗi nguồn một db riêng (11 .. 15), cùng tiền tố."""
    return {source: {"db": 11 + index, "key_prefix": prefix} for index, source in enumerate(OUTPUT_SOURCES)}


def _worker_config(enabled: tuple[str, ...] = ("combo",)) -> dict:
    return {
        "redis": {"input": {"key_prefix": "L_DFS", "snapshot_bars": 500}, "outputs": _outputs_config()},
        "live": {"enabled_strategies": list(enabled), "discord": {"enabled": False}},
    }


def _sinks(*sources: str) -> dict[str, _RecordingRedis]:
    """Client đầu ra giả (ghi lại lời gọi EVAL) cho đúng các nguồn được nêu."""
    return {source: _RecordingRedis() for source in sources}


def _trend_frame(source: pd.DataFrame, hours: int) -> pd.DataFrame:
    """Nến khung lớn dựng từ nến H1 (gộp `hours` nến một) — chỉ để test; cùng cột với `source`."""
    frame = source.copy()
    frame["bartime"] = pd.to_datetime(frame["bartime"])
    grouped = (
        frame.groupby(frame["bartime"].dt.floor(f"{hours}h"))
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .reset_index()
    )
    return grouped[source.columns]


def _window_ending_at_last_signal(strategy: str, source: pd.DataFrame) -> pd.DataFrame:
    """Cửa sổ nến H1 mà NẾN CUỐI có tín hiệu cuối cùng của `strategy` trên cả chuỗi."""
    df = run_strategy(strategy, symbol="US30", tf="H1", bars=source)
    last = int(df.index[df["signal"].astype(int) != 0][-1])
    return source.iloc[: last + 1].reset_index(drop=True)


def _closes_at(window: pd.DataFrame) -> pd.Timestamp:
    return pd.Timestamp(window.iloc[-1]["bartime"], tz="UTC") + pd.Timedelta(hours=1)


def _patch_worker_reads_only(lw, monkeypatch: pytest.MonkeyPatch, frames: dict[str, pd.DataFrame]) -> _FakeInput:
    """Worker đọc nến từ `frames` (không Redis); khung trend của mỗi nguồn là khung THẬT của config."""
    fake_in = _FakeInput(frames)
    monkeypatch.setattr(lw, "read_candles_from_redis", lambda client, **kw: fake_in.read(**kw))
    monkeypatch.setattr(lw, "normalize_app_config", lambda cfg: cfg)
    return fake_in


def _patch_trend_tf(lw, monkeypatch: pytest.MonkeyPatch, trend_tf: str) -> None:
    """Khung trend của knn và zigzag là `trend_tf` ở mọi ô (breakout vẫn là chính khung vào lệnh): test không phụ thuộc bảng config."""
    real = lw.trend_source_for

    def fake(key, symbol, timeframe, method):
        found = real(key, symbol, timeframe, method)
        return found if method == "breakout" else dataclasses.replace(found, trend_tf=trend_tf)

    monkeypatch.setattr(lw, "trend_source_for", fake)


def _patch_worker_inputs(
    lw, monkeypatch: pytest.MonkeyPatch, frames: dict[str, pd.DataFrame], trend_tf: str = "H4"
) -> _FakeInput:
    fake_in = _patch_worker_reads_only(lw, monkeypatch, frames)
    _patch_trend_tf(lw, monkeypatch, trend_tf)
    return fake_in


def test_cac_nguon_dau_ra_khop_ten_khoi_trend() -> None:
    from wisdom_engine.des.src.rules import trend_tf

    assert OUTPUT_SOURCES == ("raw", "knn", "breakout", "zigzag", "tstat")  # DB0 / DB1 / DB2 / DB4 / DB3 của we_core
    # mỗi nguồn lọc = một khối đã đăng ký và ngược lại: từ 2026-10-27 mọi khối trend của des đều là một nguồn đầu ra (tstat thêm vào db15)
    assert set(OUTPUT_SOURCES) - {"raw"} == set(trend_tf.METHODS)


def test_worker_chi_nguon_raw_thi_chi_doc_mot_timeframe(bars: pd.DataFrame, monkeypatch: pytest.MonkeyPatch) -> None:
    """Chỉ có client nguồn raw: không tính nguồn lọc trend nào — đọc đúng 1 timeframe, không đụng khung trend."""
    lw, fake_in, closed_at = _worker_with_last_candle_signal(bars, monkeypatch)
    sinks = _sinks("raw")
    published = lw.process_candle_update(
        fake_in, sinks, timeframe="H1", symbol="US30", config=_worker_config(), now=closed_at + pd.Timedelta(seconds=30)
    )
    assert fake_in.doc == ["H1"]
    assert published == 1 and len(sinks["raw"].calls) == 1


def test_worker_nen_cuoi_khong_co_tin_hieu_thi_khong_doc_khung_trend(monkeypatch: pytest.MonkeyPatch) -> None:
    """macd_break + knn / zigzag (không dời tín hiệu): tín hiệu đã lọc ⊆ tín hiệu thô nên nến cuối không có tín hiệu thô thì
    hai nguồn này không tốn lần đọc nến khung lớn nào. (combo / ma_cross + breakout thì khác — xem
    test_worker_breakout_tinh_o_moi_su_kien_va_ghi_tin_hieu_doi.)"""
    from wisdom_engine.des.src.redis_io import live_worker as lw

    source = range_bars(900)
    df = run_strategy("macd_break", symbol="US30", tf="H1", bars=source)
    last = int(df.index[df["signal"].astype(int) != 0][-1])
    window = source.iloc[: last + 2].reset_index(drop=True)  # tín hiệu cuối cùng nằm ngay TRƯỚC nến cuối
    fake_in = _patch_worker_inputs(lw, monkeypatch, {"H1": window, "H4": _trend_frame(source, 4)})
    sinks = _sinks("raw", "knn", "zigzag")

    published = lw.process_candle_update(
        fake_in,
        sinks,
        timeframe="H1",
        symbol="US30",
        config=_worker_config(("macd_break",)),
        now=_closes_at(window) + pd.Timedelta(days=1),
    )

    assert published == 0
    assert fake_in.doc == ["H1"]
    assert all(sink.calls == [] for sink in sinks.values())


def test_worker_tin_hieu_qua_trend_thi_ghi_raw_va_knn_khong_pubsub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tín hiệu cùng chiều trend: ghi vào nguồn raw VÀ nguồn knn (cùng khoá, khác DB), KHÔNG Pub/Sub ở nguồn nào."""
    from wisdom_engine.des.src.redis_io import live_worker as lw

    source = range_bars(900)
    window = _window_ending_at_last_signal("macd_break", source)
    trend = _trend_frame(source, 4)
    expected = run_strategy_with_trend_reference(
        "macd_break", symbol="US30", entry_tf="H1", entry_bars=window, trend_tf="H4", trend_bars=trend,
        overrides={"TREND_METHODS": "knn"},
    ).iloc[-1]
    assert int(expected["signal"]) != 0, "tiền đề của test: ca này phải qua được lọc trend"
    fake_in = _patch_worker_inputs(lw, monkeypatch, {"H1": window, "H4": trend})
    sinks = _sinks("raw", "knn")

    published = lw.process_candle_update(
        fake_in,
        sinks,
        timeframe="H1",
        symbol="US30",
        config=_worker_config(("macd_break",)),
        now=_closes_at(window) + pd.Timedelta(seconds=30),
    )

    assert published == 2
    assert fake_in.doc == ["H1", "H4"]
    (raw_script, _, raw_args), = sinks["raw"].calls
    (knn_script, _, knn_args), = sinks["knn"].calls
    assert "PUBLISH" not in raw_script and "PUBLISH" not in knn_script
    assert raw_args[:3] == knn_args[:3]  # cùng khoá HASH/LIST và stamp (chỉ khác DB)
    raw_fields = dict(zip(raw_args[3::2], raw_args[4::2], strict=True))
    knn_fields = dict(zip(knn_args[3::2], knn_args[4::2], strict=True))
    assert knn_fields.pop("comment") == raw_fields.pop("comment") + "; aligned with trend reference"
    assert knn_fields == raw_fields  # còn lại giống hệt: cùng hướng, entry, atr, valid_from...


def test_worker_tin_hieu_bi_trend_loc_thi_chi_ghi_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tín hiệu thô ngược/không cùng chiều trend: nguồn raw VẪN ghi (đó là DB không lọc), nguồn knn không ghi."""
    from wisdom_engine.des.src.redis_io import live_worker as lw

    source = range_bars(900)
    window = _window_ending_at_last_signal("combo", source)
    trend = _trend_frame(source, 4)
    expected = run_strategy_with_trend_reference(
        "combo", symbol="US30", entry_tf="H1", entry_bars=window, trend_tf="H4", trend_bars=trend,
        overrides={"TREND_METHODS": "knn"},
    ).iloc[-1]
    assert int(expected["raw_signal"]) != 0 and int(expected["signal"]) == 0, "tiền đề: ca này phải bị lọc trend"
    fake_in = _patch_worker_inputs(lw, monkeypatch, {"H1": window, "H4": trend})
    sinks = _sinks("raw", "knn")

    published = lw.process_candle_update(
        fake_in, sinks, timeframe="H1", symbol="US30", config=_worker_config(),
        now=_closes_at(window) + pd.Timedelta(seconds=30),
    )

    assert published == 1
    assert fake_in.doc == ["H1", "H4"]
    assert len(sinks["raw"].calls) == 1 and sinks["knn"].calls == []


def test_worker_thieu_nen_trend_thi_khong_ghi_nguon_trend(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB đầu vào không có nến khung lớn cho symbol này -> nguồn knn và zigzag BỎ QUA, tuyệt đối không ghi tín hiệu chưa lọc
    (im lặng bỏ qua bộ lọc là kịch bản tệ nhất: người xem tin rằng nguồn đó đã được lọc). Nguồn raw vẫn ghi."""
    from wisdom_engine.des.src.redis_io import live_worker as lw

    window = _window_ending_at_last_signal("combo", range_bars(900))
    fake_in = _patch_worker_inputs(lw, monkeypatch, {"H1": window})  # không có H4
    sinks = _sinks("raw", "knn", "zigzag")

    published = lw.process_candle_update(
        fake_in, sinks, timeframe="H1", symbol="US30", config=_worker_config(),
        now=_closes_at(window) + pd.Timedelta(seconds=30),
    )

    assert fake_in.doc == ["H1", "H4", "H4"]  # knn rồi zigzag đều thử đọc nến H4
    assert published == 1 and len(sinks["raw"].calls) == 1
    assert sinks["knn"].calls == [] and sinks["zigzag"].calls == []  # không hề gọi tới Redis của hai nguồn đó


def test_worker_o_chua_co_khung_trend_thi_bo_qua_nguon_knn_va_zigzag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Symbol ngoài bảng trend_tf_by_symbol (khung trend rỗng): bỏ qua nguồn cần khung tham chiếu, không đọc nến, không ném lỗi."""
    from wisdom_engine.des.src.redis_io import live_worker as lw

    source = range_bars(900)
    window = _window_ending_at_last_signal("combo", source)
    fake_in = _patch_worker_reads_only(lw, monkeypatch, {"H1": window, "H4": _trend_frame(source, 4)})
    real = lw.trend_source_for
    monkeypatch.setattr(
        lw, "trend_source_for",
        lambda key, symbol, timeframe, method: dataclasses.replace(real(key, symbol, timeframe, method), trend_tf=""),
    )
    sinks = _sinks("raw", "knn", "zigzag")

    published = lw.process_candle_update(
        fake_in, sinks, timeframe="H1", symbol="US30", config=_worker_config(),
        now=_closes_at(window) + pd.Timedelta(seconds=30),
    )

    assert published == 1 and len(sinks["raw"].calls) == 1
    assert fake_in.doc == ["H1"] and sinks["knn"].calls == [] and sinks["zigzag"].calls == []


def test_worker_loi_o_mot_nguon_khong_lam_mat_nguon_khac(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Một nguồn lỗi (vd khối trend gặp dữ liệu lạ) không được chặn nguồn raw, các nguồn còn lại, hay chiến lược kế tiếp trên cùng nến."""
    from wisdom_engine.des.src.redis_io import live_worker as lw

    source = range_bars(900)
    window = _window_ending_at_last_signal("combo", source)
    fake_in = _patch_worker_inputs(lw, monkeypatch, {"H1": window, "H4": _trend_frame(source, 4)})
    combo_frame = run_strategy("combo", symbol="US30", tf="H1", bars=window)
    sinks = _sinks("raw", "knn", "zigzag")

    def flaky_trend(key, **kwargs):
        if kwargs["overrides"]["TREND_METHODS"] == "knn":
            raise ValueError("loi gia lap o khoi knn")
        return combo_frame  # nguồn zigzag "thấy" tín hiệu ở nến cuối

    with caplog.at_level(logging.ERROR, logger="live_worker"):
        published = lw.process_candle_update(
            fake_in,
            sinks,
            timeframe="H1",
            symbol="US30",
            config=_worker_config(("combo", "macd_break")),
            now=_closes_at(window) + pd.Timedelta(seconds=30),
            strategy_runner=lambda key, **kwargs: combo_frame,  # cả hai chiến lược cùng "thấy" tín hiệu ở nến cuối
            trend_runner=flaky_trend,
        )

    # knn lỗi ở cả hai chiến lược nhưng raw và zigzag vẫn ghi đủ cho cả hai.
    assert published == 4
    assert len(sinks["raw"].calls) == 2 and len(sinks["zigzag"].calls) == 2 and sinks["knn"].calls == []
    failed = [r.getMessage() for r in caplog.records if "event=trend_output_failed" in r.getMessage()]
    assert [message.split(" strategy=")[0] for message in failed] == ["event=trend_output_failed source=knn"] * 2


def test_trend_source_for_theo_khoi_va_bang_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    from wisdom_engine.des.src.rules import trend_tf

    # breakout: chính khung vào lệnh, dời tín hiệu, không cần nến thêm
    assert trend_source_for("combo", "US30", "H1", "breakout") == TrendSource("H1", True, 0)
    assert trend_source_for("ma_cross", "US30", "M30", "breakout") == TrendSource("M30", True, 0)
    # knn: khung lớn tra bảng trend_tf_by_symbol, không dời tín hiệu
    knn = trend_source_for("macd_break", "US30", "H1", "knn")
    assert knn.shifts_signals is False and knn.warmup_bars == 0 and knn.trend_tf in COMBO_TREND_TIMEFRAMES
    # zigzag: cùng bảng khung với knn, không dời tín hiệu, cần ~3000 nến khung trend để chính xác tuyệt đối
    zigzag = trend_source_for("combo", "US30", "H4", "zigzag")
    assert zigzag == TrendSource(zigzag.trend_tf, False, 3000) and zigzag.trend_tf in COMBO_TREND_TIMEFRAMES
    assert zigzag.trend_tf == trend_source_for("combo", "US30", "H4", "knn").trend_tf
    # bật lại chính sách dời cho knn (một dòng ở bảng POLICY) thì nguồn knn cũng dời
    monkeypatch.setitem(trend_tf.POLICY, "knn", trend_tf.MethodPolicy(first_aligned_per_run=True))
    assert trend_source_for("macd_break", "US30", "H1", "knn").shifts_signals is True
    # ngoài bảng: khối cần khung tham chiếu riêng chưa có khung trend; breakout vẫn là chính khung vào lệnh
    assert trend_source_for("combo", "EURUSD", "H1", "knn").trend_tf == ""
    assert trend_source_for("combo", "EURUSD", "H1", "breakout").trend_tf == "H1"


def test_worker_breakout_tinh_o_moi_su_kien_va_ghi_tin_hieu_doi(monkeypatch: pytest.MonkeyPatch) -> None:
    """combo + breakout: tín hiệu thô bị trend loại được giữ tới khi trend xác nhận nên tín hiệu đã lọc có thể nằm ở nến KHÔNG
    có tín hiệu thô. Nguồn raw không có gì ở nến đó, nguồn breakout ghi tín hiệu dời, KHÔNG Pub/Sub, và chỉ đọc MỘT snapshot
    (khung trend = khung vào lệnh nên dùng lại nến vào lệnh). knn và zigzag (không dời tín hiệu) không có gì để làm."""
    from wisdom_engine.des.src.redis_io import live_worker as lw

    source = range_bars(900)
    window = source.iloc[:138].reset_index(drop=True)  # nến cuối là nến 137: tín hiệu dời SELL của combo H1 (raw_signal = 0 ở đó)
    expected = run_strategy_with_trend_reference(
        "combo", symbol="US30", entry_tf="H1", entry_bars=window, trend_tf="H1", trend_bars=window,
        overrides={"TREND_METHODS": "breakout"},
    ).iloc[-1]
    assert int(expected["raw_signal"]) == 0 and int(expected["signal"]) == -1, "tiền đề: ca này phải là tín hiệu dời"
    assert int(run_strategy("combo", symbol="US30", tf="H1", bars=window).iloc[-1]["signal"]) == 0
    fake_in = _patch_worker_reads_only(lw, monkeypatch, {"H1": window})
    sinks = _sinks("raw", "knn", "breakout", "zigzag")  # không tstat: tstat cũng dời tín hiệu và đọc nến khung trend, có test riêng

    published = lw.process_candle_update(
        fake_in, sinks, timeframe="H1", symbol="US30", config=_worker_config(),
        now=_closes_at(window) + pd.Timedelta(seconds=30),
    )

    assert published == 1
    assert fake_in.doc == ["H1"]  # không đọc nến khung trend lần hai
    assert all(sinks[name].calls == [] for name in ("raw", "knn", "zigzag"))
    (script, _, args), = sinks["breakout"].calls
    assert "PUBLISH" not in script
    stamp = pd.Timestamp(window.iloc[-1]["bartime"]).strftime("%Y-%m-%d %H:%M:%S")
    assert args[:3] == (f"L_DES_COMBO_US30_H1:{stamp}", "L_DES_COMBO_US30_H1", stamp)
    fields = dict(zip(args[3::2], args[4::2], strict=True))
    assert fields["tradeSide"] == "SELL"
    assert fields["comment"] == "bearish MA cross earlier in the run, held until the trend aligned; aligned with trend reference"


def test_worker_breakout_van_tinh_khi_khong_co_gi_de_ghi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chiến lược có khối dời tín hiệu: nguồn breakout được tính ở MỌI sự kiện (kể cả khi nến cuối không có gì), nhưng không ghi
    gì nếu tín hiệu đã lọc là 0; knn và zigzag (không dời) không được tính."""
    from wisdom_engine.des.src.redis_io import live_worker as lw

    source = range_bars(900)
    window = source.iloc[:120].reset_index(drop=True)  # nến 119: không có tín hiệu thô lẫn tín hiệu dời
    full = run_strategy_with_trend_reference(
        "combo", symbol="US30", entry_tf="H1", entry_bars=window, trend_tf="H1", trend_bars=window,
        overrides={"TREND_METHODS": "breakout"},
    ).iloc[-1]
    assert int(full["raw_signal"]) == 0 and int(full["signal"]) == 0
    fake_in = _patch_worker_reads_only(lw, monkeypatch, {"H1": window})
    sinks = _sinks("raw", "knn", "breakout", "zigzag")  # không tstat: tstat cũng dời tín hiệu và đọc nến khung trend, có test riêng
    calls: list[tuple[str, str]] = []

    def spy(key: str, **kwargs):
        calls.append((key, kwargs["overrides"]["TREND_METHODS"]))
        return run_strategy_with_trend_reference(key, **kwargs)

    published = lw.process_candle_update(
        fake_in, sinks, timeframe="H1", symbol="US30", config=_worker_config(),
        now=_closes_at(window) + pd.Timedelta(days=1), trend_runner=spy,
    )

    assert calls == [("combo", "breakout")]  # đã tính đường trend dù nến cuối không có tín hiệu thô
    assert published == 0 and all(sink.calls == [] for sink in sinks.values())
    assert fake_in.doc == ["H1"]


def test_worker_khong_co_nguon_trend_thi_khong_tinh_duong_trend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chỉ có client nguồn raw: không tính đường trend nào, kể cả với strategy có khối dời tín hiệu."""
    from wisdom_engine.des.src.redis_io import live_worker as lw

    window = range_bars(900).iloc[:120].reset_index(drop=True)
    fake_in = _patch_worker_reads_only(lw, monkeypatch, {"H1": window})

    def never(key: str, **kwargs):
        raise AssertionError("đường trend không được chạy khi không có client nguồn lọc trend")

    published = lw.process_candle_update(
        fake_in, _sinks("raw"), timeframe="H1", symbol="US30", config=_worker_config(),
        now=_closes_at(window) + pd.Timedelta(days=1), trend_runner=never,
    )
    assert published == 0 and fake_in.doc == ["H1"]


def test_worker_khung_trend_bang_khung_vao_lenh_thi_dung_lai_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ô của bảng khung trend khai bằng chính khung vào lệnh (knn H1 -> H1) và breakout (luôn là chính khung vào lệnh):
    cũng chỉ đọc một snapshot."""
    from wisdom_engine.des.src.redis_io import live_worker as lw

    window = _window_ending_at_last_signal("combo", range_bars(900))
    fake_in = _patch_worker_inputs(lw, monkeypatch, {"H1": window}, trend_tf="H1")
    sinks = _sinks("raw", "knn", "breakout")

    lw.process_candle_update(
        fake_in, sinks, timeframe="H1", symbol="US30", config=_worker_config(),
        now=_closes_at(window) + pd.Timedelta(seconds=30),
    )

    assert fake_in.doc == ["H1"] and len(sinks["raw"].calls) == 1


@pytest.mark.parametrize("trend_tf", ["H4", "H1"])
def test_worker_zigzag_xin_nhieu_nen_khung_trend_hon_snapshot(monkeypatch: pytest.MonkeyPatch, trend_tf: str) -> None:
    """zigzag cần ~3000 nến khung trend để chính xác tuyệt đối: worker xin max(snapshot_bars, 3000) nến (DFS chỉ giữ tối đa 1200
    nên nhận được chừng đó), kể cả khi khung trend trùng khung vào lệnh (không dùng lại snapshot 500 nến)."""
    from wisdom_engine.des.src.redis_io import live_worker as lw

    source = range_bars(900)
    window = _window_ending_at_last_signal("macd_break", source)
    fake_in = _patch_worker_inputs(lw, monkeypatch, {"H1": window, "H4": _trend_frame(source, 4)}, trend_tf=trend_tf)

    lw.process_candle_update(
        fake_in, _sinks("zigzag"), timeframe="H1", symbol="US30", config=_worker_config(("macd_break",)),
        now=_closes_at(window) + pd.Timedelta(seconds=30),
    )

    assert fake_in.requests == [("H1", 500), (trend_tf, 3000)]


def test_worker_moi_nguon_ghi_vao_db_cua_chinh_no_va_chay_dung_khoi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Định tuyến: một tín hiệu ở nến cuối được ghi vào CẢ NĂM nguồn, mỗi nguồn vào client + tiền tố của riêng nó; các nguồn lọc
    trend gọi trend_runner với đúng khối của mình (overrides TREND_METHODS) theo thứ tự knn, breakout, zigzag, tstat."""
    from wisdom_engine.des.src.redis_io import live_worker as lw

    source = range_bars(900)
    window = _window_ending_at_last_signal("combo", source)
    frame = run_strategy("combo", symbol="US30", tf="H1", bars=window)
    fake_in = _patch_worker_inputs(lw, monkeypatch, {"H1": window, "H4": _trend_frame(source, 4)})
    config = _worker_config()
    for name in OUTPUT_SOURCES:
        config["redis"]["outputs"][name]["key_prefix"] = f"L_DES_{name.upper()}"
    sinks = _sinks(*OUTPUT_SOURCES)
    seen: list[dict] = []

    def runner(key: str, **kwargs):
        seen.append(kwargs["overrides"])
        return frame  # mọi nguồn lọc "thấy" cùng một tín hiệu ở nến cuối

    published = lw.process_candle_update(
        fake_in, sinks, timeframe="H1", symbol="US30", config=config,
        now=_closes_at(window) + pd.Timedelta(seconds=30), trend_runner=runner,
    )

    assert published == 5
    assert seen == [{"TREND_METHODS": "knn"}, {"TREND_METHODS": "breakout"}, {"TREND_METHODS": "zigzag"}, {"TREND_METHODS": "tstat"}]
    for name in OUTPUT_SOURCES:
        (_, _, args), = sinks[name].calls
        assert args[1] == f"L_DES_{name.upper()}_COMBO_US30_H1"
    assert len({sinks[name].calls[0][2][2:] for name in OUTPUT_SOURCES}) == 1  # stamp + field giống nhau ở cả năm nguồn


def test_worker_tstat_tinh_o_moi_su_kien_nhu_breakout_con_knn_va_zigzag_thi_khong(monkeypatch: pytest.MonkeyPatch) -> None:
    """tstat có chính sách dời tín hiệu (bật 2026-10-26) nên được tính ở MỌI sự kiện của kênh, kể cả khi nến cuối không có tín hiệu
    thô; knn và zigzag (không dời) thì không. Khung trend của tstat là ô trong bảng trend_tf_by_symbol, như knn."""
    from wisdom_engine.des.src.redis_io import live_worker as lw

    source = range_bars(900)
    window = source.iloc[:120].reset_index(drop=True)  # nến 119: không có tín hiệu thô
    assert int(run_strategy("combo", symbol="US30", tf="H1", bars=window).iloc[-1]["raw_signal"]) == 0
    fake_in = _patch_worker_inputs(lw, monkeypatch, {"H1": window, "H4": _trend_frame(source, 4)})
    sinks = _sinks("raw", "knn", "zigzag", "tstat")
    calls: list[tuple[str, str, str]] = []

    def spy(key: str, **kwargs):
        calls.append((key, kwargs["overrides"]["TREND_METHODS"], kwargs["trend_tf"]))
        return run_strategy_with_trend_reference(key, **kwargs)

    lw.process_candle_update(
        fake_in, sinks, timeframe="H1", symbol="US30", config=_worker_config(),
        now=_closes_at(window) + pd.Timedelta(days=1), trend_runner=spy,
    )

    assert [(key, method) for key, method, _ in calls] == [("combo", "tstat")]
    assert calls[0][2] == trend_source_for("combo", "US30", "H1", "tstat").trend_tf == trend_source_for("combo", "US30", "H1", "knn").trend_tf


def test_worker_chi_nguon_raw_gui_discord(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discord (nếu bật) chỉ gửi cho nguồn raw: các nguồn lọc trend là bản sao của cùng tín hiệu, gửi thêm sẽ là tin trùng."""
    from wisdom_engine.des.src.redis_io import live_worker as lw

    source = range_bars(900)
    window = _window_ending_at_last_signal("macd_break", source)
    fake_in = _patch_worker_inputs(lw, monkeypatch, {"H1": window, "H4": _trend_frame(source, 4)})
    config = _worker_config(("macd_break",))
    config["live"]["discord"] = {"enabled": True, "webhook_url": "x"}
    sent: list[str] = []
    monkeypatch.setattr(lw, "send_discord_signal", lambda payload, cfg: (sent.append(payload["side"]) or (True, "ok")))

    published = lw.process_candle_update(
        fake_in, _sinks("raw", "knn"), timeframe="H1", symbol="US30", config=config,
        now=_closes_at(window) + pd.Timedelta(seconds=30),
    )

    assert published == 2 and len(sent) == 1


def test_get_strategy_khong_phan_biet_hoa_thuong() -> None:
    assert get_strategy("COMBO").key == "combo"
    assert get_strategy(" ma_cross ").key == "ma_cross"
    # ema_cross đã gỡ khỏi des có chủ đích.
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


def test_combo_khong_con_tham_so_x_va_symbol_van_hanh_la_danh_sach_trong_config() -> None:
    from wisdom_engine.des.src import configuration as cfg

    assert "X" not in cfg.COMBO_DEFAULT_PARAMS and "X" not in {field["key"] for field in cfg.COMBO_PARAM_FIELDS}
    for symbol in ("US30", "ZZZ999"):
        assert "X" not in normalize_combo_params({"X": "10"}, symbol, "H1")          # override cũ bị bỏ qua
    assert not hasattr(cfg, "COMBO_SYMBOL_X") and not hasattr(cfg, "_to_float")
    assert cfg.OPERATING_SYMBOLS == ("US30", "US500", "US100", "DE40", "UK100", "FR40", "SP35", "HK50", "J225", "GOLD", "BTCUSD")


def test_doc_danh_sach_symbol_kiem_tra_rong_trung_va_sai_kieu() -> None:
    from wisdom_engine.des.src.configuration import _operating_symbols as read

    assert read({"symbols": [" us30 ", "Eurusd"]}, "strategies.combo") == ("US30", "EURUSD")
    with pytest.raises(KeyError, match="symbols"):
        read({}, "strategies.combo")
    with pytest.raises(TypeError, match="non-empty list"):
        read({"symbols": []}, "strategies.combo")
    with pytest.raises(TypeError, match="non-empty list"):
        read({"symbols": {"US30": 10.0}}, "strategies.combo")        # dạng cũ symbol_x (dict) không được nhận
    with pytest.raises(ValueError, match="empty symbol"):
        read({"symbols": ["US30", " "]}, "strategies.combo")
    with pytest.raises(ValueError, match="twice: US30"):
        read({"symbols": ["US30", "us30", "BTCUSD"]}, "strategies.combo")


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
    from wisdom_engine.des.src.redis_io.event_listener import (
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

    import wisdom_engine.des.src.redis_io.event_listener as event_listener_module

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
# 12. des KHÔNG hết hạn (quyết định 2026-10-07) -- khác biệt duy nhất so với OG
#     live: không cửa sổ hiệu lực, không TTL, không cắt LIST. Cổng "nến chưa
#     đóng" vẫn giữ nguyên.
# =============================================================================


def _worker_with_last_candle_signal(bars: pd.DataFrame, monkeypatch: pytest.MonkeyPatch):
    """Cửa sổ nến mà NẾN CUỐI có tín hiệu combo, kèm worker đã vá để đọc nến từ bộ nhớ."""
    from wisdom_engine.des.src.redis_io import live_worker as lw

    df = run_strategy("combo", symbol="US30", tf="H1", bars=bars)
    last = int(df.index[df["signal"].astype(int) != 0][-1])
    window = bars.iloc[: last + 1].reset_index(drop=True)
    fake_in = _FakeInput({"H1": window})
    monkeypatch.setattr(lw, "read_candles_from_redis", lambda client, **kw: fake_in.read(**kw))
    monkeypatch.setattr(lw, "normalize_app_config", lambda cfg: cfg)
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
        fake_in, {"raw": out}, timeframe="H1", symbol="US30", config=_worker_config(), now=closed_at + age
    )
    assert published == 1
    (script, _, args), = out.calls
    fields = dict(zip(args[3::2], args[4::2], strict=True))
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
        {"raw": out},
        timeframe="H1",
        symbol="US30",
        config=_worker_config(),
        now=closed_at - pd.Timedelta(minutes=1),
    )
    assert published == 0
    assert out.calls == []


def test_validate_config_khong_doi_signal_validity_hay_retention() -> None:
    from wisdom_engine.des.src.redis_io import live_worker as lw

    config = _config_live_hop_le()
    lw._validate_config(config)  # không raise: không còn live.signal_validity / redis.output.retention_*
    outputs = config["redis"]["outputs"]
    input_db = config["redis"]["input"]["db"]
    # Đầu vào và năm nguồn đầu ra phải là SÁU db khác nhau (đụng cặp nào cũng sai).
    for source in OUTPUT_SOURCES:
        others = [outputs[other]["db"] for other in OUTPUT_SOURCES if other != source]
        for taken in (input_db, *others):
            broken = {**config["redis"], "outputs": {**outputs, source: {**outputs[source], "db": taken}}}
            with pytest.raises(ValueError, match="6 different databases"):
                lw._validate_config({**config, "redis": broken})
    no_prefix = {**config["redis"], "outputs": {**outputs, "knn": {"db": outputs["knn"]["db"], "key_prefix": " "}}}
    with pytest.raises(ValueError, match=r"outputs\.knn\.key_prefix"):
        lw._validate_config({**config, "redis": no_prefix})


# =============================================================================
# 13. Mỗi lần START des là xoá sạch DB tín hiệu (quyết định 2026-10-08).
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


def test_clear_signals_chi_xoa_dung_namespace_cua_des() -> None:
    og = {f"L_DES_COMBO_US30_H{i}" for i in range(1, 5)} | {
        f"L_DES_COMBO_US30_H1:2022-01-{d:02d} 00:00:00" for d in range(1, 29)
    }
    la = {"L_DFS_US30_H1", "dfs:state", "L_PastSignal_US30_H1_COMBO"}
    fake = _FakeKeyspace(og | la)
    assert clear_signals(fake, key_prefix="L_DES") == len(og)
    assert fake.keys == la  # không FLUSHDB: key ngoài tiền tố L_DES_ còn nguyên
    assert clear_signals(fake, key_prefix="L_DES") == 0  # DB đã sạch -> không lỗi


def test_clear_signals_xoa_het_khi_nhieu_hon_mot_lo() -> None:
    og = {f"L_DES_MACROSS_US30_M10:2022-01-01 {h:02d}:{m:02d}:00" for h in range(24) for m in range(0, 60, 2)}
    assert len(og) > 500  # vượt _CLEAR_BATCH nên phải xoá nhiều lượt
    fake = _FakeKeyspace(og | {"giu_lai"})
    assert clear_signals(fake, key_prefix="L_DES") == len(og)
    assert fake.keys == {"giu_lai"}


def test_start_xoa_ca_nam_db_dau_ra_dung_mot_lan_reconnect_khong_xoa_lai(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import redis as redis_lib
    from wisdom_engine.des.src.redis_io import live_worker as lw

    outputs = _outputs_config()
    candles = _FakeKeyspace({"L_DFS_US30_H1", "dfs:state"})
    seeds = {
        "raw": {"L_DES_COMBO_US30_H1", "L_DES_COMBO_US30_H1:2022-01-01 00:00:00", "khac:raw"},
        "knn": {"L_DES_COMBO_US30_H1", "L_DES_COMBO_US30_H1:2022-01-01 00:00:00", "L_DES_MACDBREAK_US30_H1"},
        "breakout": {"L_DES_MACROSS_US30_M10", "L_DES_MACROSS_US30_M10:2022-01-01 00:00:00", "L_DES_COMBO_US30_H1", "khac:breakout"},
        "zigzag": {"L_DES_COMBO_US30_H4", "khac:zigzag"},
        "tstat": {"L_DES_COMBO_US30_H1", "L_DES_MACDBREAK_US30_H4", "khac:tstat"},
    }
    output_stores = {source: _FakeKeyspace(seeds[source]) for source in OUTPUT_SOURCES}
    stores = {10: candles, **{int(outputs[source]["db"]): output_stores[source] for source in OUTPUT_SOURCES}}
    lan_goi: list[int] = []

    class _Dung(Exception):
        pass

    def fake_listen(*args, **kwargs) -> None:
        lan_goi.append(1)
        if len(lan_goi) == 1:
            # Lần subscribe đầu: CẢ NĂM DB tín hiệu đã sạch; des vừa ghi 1 tín hiệu mới vào mỗi DB rồi mất kết nối.
            for source in OUTPUT_SOURCES:
                assert not any(k.startswith("L_DES_") for k in output_stores[source].keys)
                output_stores[source].keys.add(f"L_DES_NEW_{source.upper()}")
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
                "db": 10, "key_prefix": "L_DFS", "process_on_startup": False,
                "reconcile_interval_seconds": 1800,
            },
            "outputs": outputs,
        },
        "live": {"enabled_strategies": ["combo"]},
    }
    with caplog.at_level(logging.INFO, logger="live_worker"), pytest.raises(_Dung):
        lw.run_forever(config)

    assert len(lan_goi) == 2  # đã có 1 lần reconnect trong cùng tiến trình
    # tín hiệu mới giữ (reconnect không xoá lại), tín hiệu cũ đã bị xoá, key ngoài tiền tố L_DES_ còn nguyên
    assert output_stores["raw"].keys == {"L_DES_NEW_RAW", "khac:raw"}
    assert output_stores["knn"].keys == {"L_DES_NEW_KNN"}
    assert output_stores["breakout"].keys == {"L_DES_NEW_BREAKOUT", "khac:breakout"}
    assert output_stores["zigzag"].keys == {"L_DES_NEW_ZIGZAG", "khac:zigzag"}
    assert output_stores["tstat"].keys == {"L_DES_NEW_TSTAT", "khac:tstat"}
    assert candles.keys == {"L_DFS_US30_H1", "dfs:state"}  # DB nến không bị đụng
    cleared = [r.getMessage() for r in caplog.records if "event=signals_cleared" in r.getMessage()]
    assert len(cleared) == 5  # một dòng cho mỗi DB đầu ra, mỗi tiến trình đúng một lần
    for line, (source, count) in zip(cleared, (("raw", 2), ("knn", 3), ("breakout", 3), ("zigzag", 1), ("tstat", 2)), strict=True):
        assert f"source={source} db={outputs[source]['db']} " in line and f"keys={count}" in line


# =============================================================================
# 14. MACD Break (đăng ký vào des 2026-10-14): bản sao luật của we_core, lệnh MARKET.
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
    from wisdom_engine.des.src.strategies.macd_break import detect_macd_break_signals

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
    from wisdom_engine.des.src.strategies.macd_break import detect_macd_break_signals

    # 3 nến liên tiếp cùng phá đỉnh + MACD dương: cờ bật thì chỉ nến đầu bắn.
    frame = _macd_break_frame([(110, 105, 95, 0.5, 1.0)] * 3)
    out = detect_macd_break_signals(frame, params={"ALTERNATING_FILTER_ENABLED": True})
    assert out["signal"].tolist() == [1, 0, 0]


def test_macd_break_tat_co_thi_nen_nao_thoa_dieu_kien_cung_ban() -> None:
    from wisdom_engine.des.src.strategies.macd_break import detect_macd_break_signals

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
    from wisdom_engine.des.src.strategies.macd_break import detect_macd_break_signals

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
    from wisdom_engine.des.src.indicator import add_macd_break_indicators

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


def test_macd_break_nen_cuoi_cua_cua_so_des(rbars: pd.DataFrame) -> None:
    """des đọc snapshot_bars (500) nến và chỉ xét nến cuối: cửa sổ 500 nến kết thúc ở một nến tín hiệu phải cho
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
    """Chỉ TÊN key bỏ '_' thành MACDBREAK; field strategy và clientOrderId vẫn là tên thật 'macd_break'."""
    row = _one_signal_row("macd_break", "H1", rbars)
    vf = signal_valid_from("H1", row["bartime"])
    _, stamp, payload = build_signal(
        strategy="macd_break", timeframe="H1", symbol="US30", row=row, valid_from=vf
    )
    fields = to_hash_fields("macd_break", payload)
    client = _RecordingRedis()
    assert publish_signal(
        client, key_prefix="L_DES", symbol="US30", timeframe="H1", strategy="macd_break", stamp=stamp, hash_fields=fields,
    ) is True
    (_, _, args), = client.calls
    assert args[:2] == (f"L_DES_MACDBREAK_US30_H1:{stamp}", "L_DES_MACDBREAK_US30_H1")
    written = dict(zip(args[3::2], args[4::2], strict=True))
    assert written == fields
    assert written["strategy"] == "macd_break"
    assert written["clientOrderId"].startswith("macd_break:H1:US30:")


def test_worker_ghi_tin_hieu_macd_break_tu_cua_so_500_nen(
    rbars: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Đường live đầy đủ (hermetic): sự kiện nến H1 -> đọc cửa sổ 500 nến -> macd_break ở nến cuối -> ghi 1 tín hiệu."""
    from wisdom_engine.des.src.redis_io import live_worker as lw

    full = run_strategy("macd_break", symbol="US30", tf="H1", bars=rbars)
    last = int(full.index[full["signal"].astype(int) != 0][-1])
    window = rbars.iloc[last - 499 : last + 1].reset_index(drop=True)
    fake_in = _patch_worker_reads_only(lw, monkeypatch, {"H1": window})
    config = _worker_config(("macd_break",))
    closed_at = pd.Timestamp(window.iloc[-1]["bartime"], tz="UTC") + pd.Timedelta(hours=1)
    out = _RecordingRedis()

    published = lw.process_candle_update(
        fake_in, {"raw": out}, timeframe="H1", symbol="US30", config=config, now=closed_at + pd.Timedelta(seconds=30)
    )

    assert published == 1
    (script, _, args), = out.calls
    assert args[0].startswith("L_DES_MACDBREAK_US30_H1:") and args[1] == "L_DES_MACDBREAK_US30_H1"
    fields = dict(zip(args[3::2], args[4::2], strict=True))
    assert fields["orderType"] == "MARKET" and "stopPrice" not in fields
    assert fields["strategy"] == "macd_break" and fields["timeframe"] == "H1"
    assert fields["valid_from"] == closed_at.isoformat().replace("+00:00", "Z")
    assert "EXPIRE" not in script and "LTRIM" not in script  # không hết hạn, không cắt LIST


# =============================================================================
# 15. Đồng bộ với we_core (2026-10-16): ma_cross luật trạng thái + lọc lặp cùng chiều (xen kẽ trước, trend
#     sau), khung trend theo từng (symbol, khung vào lệnh) và quy tắc "khung trend >= khung vào lệnh".
# =============================================================================


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
    """Khung tối giản cho detect_macd_break_signals: (close, prior_high, prior_low, macd_h, atr) mỗi bar."""
    spec = {"B": (110, 105, 95, 0.5, 1.0), "S": (90, 105, 95, -0.5, 1.0), ".": (100, 105, 95, 0.5, 1.0)}
    frame = pd.DataFrame(
        {
            "bartime": pd.date_range("2026-01-05", periods=len(rows), freq="1h"),
            "close": [spec[c][0] for c in rows],
            "prior_high_close": [spec[c][1] for c in rows],
            "prior_low_close": [spec[c][2] for c in rows],
            "macd_h": [spec[c][3] for c in rows],
            "atr": [spec[c][4] for c in rows],
        }
    )
    frame["trend_bias"] = bias
    return frame


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
    from wisdom_engine.des.src.strategies.combo import detect_combo_signals
    from wisdom_engine.des.src.strategies.ma_cross import detect_ma_cross_signals
    from wisdom_engine.des.src.strategies.macd_break import detect_macd_break_signals

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
            # signal ⊆ raw_signal: mỗi tín hiệu giữ lại đúng là một tín hiệu raw, cùng nến, cùng hướng
            assert all(s in (0, r) for s, r in zip(out["signal"].tolist(), out["raw_signal"].tolist(), strict=True))
            if not trend_on:
                assert out["signal"].tolist() == out["raw_signal"].tolist()
            elif alternating_on:
                seq = [v for v in out["signal"].tolist() if v]
                adjacent_same_direction_cases += int(any(a == b for a, b in pairwise(seq)))
    if trend_on and alternating_on:
        # trend loại setup ngược chiều ở giữa thì hai tín hiệu liền nhau có thể cùng chiều: ca này có xuất hiện thật
        assert adjacent_same_direction_cases > 0


def test_ma_cross_setup_theo_trang_thai_va_chi_nen_dau_cua_moi_dot_ban() -> None:
    from wisdom_engine.des.src.strategies.ma_cross import detect_ma_cross_signals

    frame = _ma_cross_trend_frame("BBB.SSB", [0] * 7)
    assert detect_ma_cross_signals(frame, params={"ALTERNATING_FILTER_ENABLED": True})["signal"].tolist() == [
        1, 0, 0, 0, -1, 0, 1,
    ]
    # cờ tắt (hoặc thiếu key): nến nào thỏa điều kiện cũng bắn
    for params in ({"ALTERNATING_FILTER_ENABLED": False}, {}):
        assert detect_ma_cross_signals(frame, params=params)["signal"].tolist() == [1, 1, 1, 0, -1, -1, 1]
    # bằng nhau hoặc MACD bằng 0 thì không có setup
    flat = _ma_cross_trend_frame("...", [0] * 3)
    assert detect_ma_cross_signals(flat, params={"ALTERNATING_FILTER_ENABLED": False})["signal"].tolist() == [0, 0, 0]


def test_ma_cross_khong_con_cot_sma_ky_truoc(bars: pd.DataFrame) -> None:
    df = run_strategy("ma_cross", symbol="US30", tf="M30", bars=bars)
    assert not {"prev_fast_ma", "prev_slow_ma", "raw_signal_reason", "entry_time"} & set(df.columns)
    assert {"fast_ma", "slow_ma", "macd_h", "atr", "entry_price"} <= set(df.columns)


def test_ma_cross_tat_co_moi_nen_thoa_dieu_kien_deu_ban(bars: pd.DataFrame) -> None:
    df = run_strategy("ma_cross", symbol="US30", tf="M30", bars=bars, overrides={"ALTERNATING_FILTER_ENABLED": "false"})
    setup = ((df["fast_ma"] > df["slow_ma"]) & (df["macd_h"] > 0)) | ((df["fast_ma"] < df["slow_ma"]) & (df["macd_h"] < 0))
    valid = df[["fast_ma", "slow_ma", "macd_h", "atr"]].notna().all(axis=1)
    assert ((df["signal"].astype(int) != 0) == (setup & valid)).all()
    assert int((df["signal"].astype(int) != 0).sum()) == 151  # tính từ pipeline thật trên synthetic_bars()


def test_ma_cross_lock_xen_ke_truoc_roi_trend_sau() -> None:
    from wisdom_engine.des.src.strategies.ma_cross import detect_ma_cross_signals

    params = {"TREND_FILTER_ENABLED": True, "ALTERNATING_FILTER_ENABLED": True}
    # BUY lệch trend (-1) rồi BUY cùng trend (+1): BUY thứ nhất chiếm "lượt đầu" dù bị trend loại -> cả đợt mất.
    out = detect_ma_cross_signals(_ma_cross_trend_frame(".B.B", [0, -1, 0, 1]), params=params)
    assert out["raw_signal"].tolist() == [0, 1, 0, 0]
    assert out["signal"].tolist() == [0, 0, 0, 0]
    # setup SELL xen giữa bị trend loại vẫn "mở lượt" cho BUY sau nó -> hai BUY liền nhau cùng chiều trong signal
    out = detect_ma_cross_signals(_ma_cross_trend_frame("BSB", [1, 0, 1]), params=params)
    assert out["raw_signal"].tolist() == [1, -1, 1]
    assert out["signal"].tolist() == [1, 0, 1]


def test_check_trend_timeframe_cho_phep_lon_hon_hoac_bang_va_cam_nho_hon() -> None:
    from wisdom_engine.des.src.configuration import check_trend_timeframe

    check_trend_timeframe("M30", "H1")
    check_trend_timeframe("h2", "H2")  # bằng nhau vẫn được, không phân biệt hoa/thường
    with pytest.raises(ValueError, match="must not be smaller than the entry timeframe H2"):
        check_trend_timeframe("H2", "H1")
    with pytest.raises(ValueError):
        check_trend_timeframe("H1", "X9")  # mã khung sai


def test_run_strategy_with_trend_reference_chan_khung_trend_nho_hon_truoc_khi_tinh(bars: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="must not be smaller than the entry timeframe H2"):
        run_strategy_with_trend_reference(
            "combo", symbol="US30", entry_tf="H2", entry_bars=bars, trend_tf="H1", trend_bars=bars
        )


def test_doc_bang_khung_trend_theo_o_hop_le_va_bao_moi_o_sai_mot_lan() -> None:
    from wisdom_engine.des.src.configuration import _strategy_trend_tf_by_symbol

    common = {
        "symbols": ("US30", "BTCUSD"),
        "entry_timeframes": ("H1", "H2"),
        "trend_timeframes": ("H1", "H2", "H4"),
    }
    full = {"us30": {"h1": "h4", "H2": "H2"}, "BTCUSD": {"H1": "H1", "H2": "H4"}}
    ok = _strategy_trend_tf_by_symbol({"trend_tf_by_symbol": full}, "strategies.combo", **common)
    assert ok == {"US30": {"H1": "H4", "H2": "H2"}, "BTCUSD": {"H1": "H1", "H2": "H4"}}
    # khoá BẮT BUỘC và phải đủ ô (không còn khung mặc định chung để dự phòng)
    with pytest.raises(KeyError, match="trend_tf_by_symbol"):
        _strategy_trend_tf_by_symbol({}, "strategies.combo", **common)
    with pytest.raises(TypeError, match="trend_tf_by_symbol"):
        _strategy_trend_tf_by_symbol({"trend_tf_by_symbol": ["US30"]}, "strategies.combo", **common)
    with pytest.raises(ValueError, match=r"US30: no trend timeframe for entry timeframe\(s\) H2"):
        _strategy_trend_tf_by_symbol(
            {"trend_tf_by_symbol": {"US30": {"H1": "H1"}, "BTCUSD": {"H1": "H1", "H2": "H2"}}}, "strategies.combo", **common
        )
    with pytest.raises(ValueError, match=r"BTCUSD: no trend timeframe for entry timeframe\(s\) H1, H2"):
        _strategy_trend_tf_by_symbol({"trend_tf_by_symbol": {"US30": {"H1": "H1", "H2": "H2"}}}, "strategies.combo", **common)
    with pytest.raises(ValueError) as exc:
        _strategy_trend_tf_by_symbol(
            {"trend_tf_by_symbol": {"XXX": {"H1": "H1"}, "US30": {"H3": "H4", "H2": "H1", "H1": "M30"}}},
            "strategies.combo",
            **common,
        )
    message = str(exc.value)
    for part in (
        "strategies.combo.trend_tf_by_symbol",
        "XXX: unknown symbol",
        "US30 H3: entry timeframe not in",
        "US30 H2: TREND_TF H1 must not be smaller",
        "US30 H1: trend timeframe M30 not in",
    ):
        assert part in message  # báo MỌI ô sai trong một lần


@pytest.mark.parametrize(
    "key,constant,entry_tf",
    [
        ("combo", "COMBO_TREND_TF_BY_SYMBOL", "H1"),
        ("ma_cross", "MA_CROSS_TREND_TF_BY_SYMBOL", "M10"),
        ("macd_break", "MACD_BREAK_TREND_TF_BY_SYMBOL", "M10"),
    ],
)
def test_khung_trend_tra_theo_o_override_tuong_minh_thang_va_thieu_o_thi_chua_xac_dinh(
    monkeypatch: pytest.MonkeyPatch, key: str, constant: str, entry_tf: str
) -> None:
    from wisdom_engine.des.src import configuration as cfg

    spec = get_strategy(key)
    assert "TREND_TF" not in cfg._DEFAULT_PARAMS_BY_KEY[key]  # không còn khung trend mặc định chung
    other = "H3"
    monkeypatch.setattr(cfg, constant, {"US30": {entry_tf: other}})

    # knn và zigzag (khung tham chiếu riêng) tra cùng một bảng
    for method in ("knn", "zigzag"):
        method_params = {"TREND_METHODS": method}
        assert spec.normalize_params(method_params, "US30", entry_tf)["TREND_TF"] == other
        assert spec.normalize_params(method_params, "BTCUSD", entry_tf)["TREND_TF"] == ""  # symbol không có bảng -> chưa xác định
        assert spec.normalize_params(method_params, "US30", "H4")["TREND_TF"] == ""  # thiếu ô khung vào -> chưa xác định
        assert spec.normalize_params({**method_params, "TREND_TF": "H1"}, "US30", entry_tf)["TREND_TF"] == "H1"  # đè tường minh thắng
        assert spec.normalize_params({**method_params, "TREND_TF": ""}, "US30", entry_tf)["TREND_TF"] == other  # rỗng = không đè
        assert trend_source_for(key, "US30", entry_tf, method).trend_tf == other
        assert trend_source_for(key, "BTCUSD", entry_tf, method).trend_tf == ""
    # breakout: khung trend = khung vào lệnh, không tra bảng
    assert trend_source_for(key, "US30", entry_tf, "breakout").trend_tf == entry_tf
    assert spec.normalize_params({"TREND_METHODS": "breakout"}, "US30", entry_tf)["TREND_TF"] == entry_tf
    # mặc định của config là knn cho cả ba chiến lược (như we_core): ô của bảng áp dụng ngay không cần chọn khối
    assert spec.normalize_params(None, "US30", entry_tf)["TREND_TF"] == other
    assert spec.normalize_params(None, "BTCUSD", entry_tf)["TREND_TF"] == ""


def test_strategy_spec_mang_bang_khung_trend_cua_config() -> None:
    from wisdom_engine.des.src import configuration as cfg

    assert get_strategy("combo").trend_tf_by_symbol is cfg.COMBO_TREND_TF_BY_SYMBOL
    assert get_strategy("ma_cross").trend_tf_by_symbol is cfg.MA_CROSS_TREND_TF_BY_SYMBOL
    assert get_strategy("macd_break").trend_tf_by_symbol is cfg.MACD_BREAK_TREND_TF_BY_SYMBOL


def test_khung_trend_cua_config_thoa_quy_tac_voi_moi_symbol_va_khung_vao() -> None:
    from wisdom_engine.des.src.configuration import (
        OPERATING_SYMBOLS,
        check_trend_timeframe,
    )

    for key in ("combo", "ma_cross", "macd_break"):
        spec = get_strategy(key)
        for timeframe in spec.recommended_timeframes or spec.supported_timeframes:
            for symbol in OPERATING_SYMBOLS:
                for source in ("knn", "breakout", "zigzag"):
                    check_trend_timeframe(timeframe, trend_source_for(key, symbol, timeframe, source).trend_tf)


def test_config_mau_co_bang_khung_trend_hop_le_cho_ca_ba_chien_luoc() -> None:
    from pathlib import Path

    import yaml
    from wisdom_engine.des.src.configuration import _strategy_trend_tf_by_symbol

    example = yaml.safe_load((Path(__file__).resolve().parents[1] / "des_config.example.yaml").read_text(encoding="utf-8"))
    symbols = tuple(example["strategies"]["combo"]["symbols"])
    for key in ("combo", "ma_cross", "macd_break"):
        section = example["strategies"][key]
        entry = tuple(section.get("recommended_timeframes") or section["supported_timeframes"])
        table = _strategy_trend_tf_by_symbol(
            section,
            f"strategies.{key}",
            symbols=symbols,
            entry_timeframes=entry,
            trend_timeframes=tuple(section["trend_timeframes"]),
        )
        assert set(table) == set(symbols)
        assert all(set(cells) == set(entry) for cells in table.values())  # bảng đủ mọi ô


def _config_live_hop_le() -> dict:
    return {
        "redis": {
            "input": {"db": 10, "key_prefix": "L_DFS", "snapshot_bars": 500, "process_on_startup": False},
            "outputs": _outputs_config(),
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


def test_check_trend_timeframes_pass_voi_config_that() -> None:
    from wisdom_engine.des.src.redis_io import live_worker as lw

    lw.check_trend_timeframes(["combo", "ma_cross", "macd_break"])


def test_check_trend_timeframes_liet_ke_moi_cap_sai_trong_mot_thong_bao(monkeypatch: pytest.MonkeyPatch) -> None:
    from wisdom_engine.des.src.redis_io import live_worker as lw

    monkeypatch.setattr(
        lw, "trend_source_for",
        lambda key, symbol, timeframe, method: TrendSource("H1" if key == "combo" else "H4", False, 0),
    )
    with pytest.raises(ValueError, match="smaller than the entry timeframe") as exc:
        lw.check_trend_timeframes(["combo", "ma_cross"])
    message = str(exc.value)
    for timeframe in ("H2", "H3", "H4"):
        assert f"combo {timeframe}:" in message
    assert "combo H1:" not in message  # H1 vào lệnh với trend H1 vẫn hợp lệ
    # mọi nguồn lọc trend đều được kiểm, báo theo từng symbol/nguồn
    assert "US30/knn" in message and "US30/breakout" in message and "US30/zigzag" in message
    assert "ma_cross" not in message


def test_validate_config_goi_check_trend_timeframes(monkeypatch: pytest.MonkeyPatch) -> None:
    from wisdom_engine.des.src.redis_io import live_worker as lw

    seen: list[list[str]] = []
    monkeypatch.setattr(lw, "check_trend_timeframes", lambda keys: seen.append(list(keys)))
    lw._validate_config(_config_live_hop_le())
    assert seen == [["combo", "ma_cross", "macd_break"]]


def test_validate_config_tu_choi_co_trend_filter_enabled_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cờ trend_filter_enabled giữ nghĩa của we_core (mặc định của đường 1 khung) nên ở des phải false: nguồn raw luôn không lọc,
    các nguồn lọc trend tự ép lọc. true sẽ làm đường 1 khung lỗi ở MỌI sự kiện -> dừng ngay lúc khởi động."""
    from wisdom_engine.des.src.redis_io import live_worker as lw

    monkeypatch.setattr(lw, "check_trend_timeframes", lambda keys: None)
    monkeypatch.setattr(lw, "trend_filter_enabled_for", lambda key: key == "ma_cross")
    with pytest.raises(ValueError, match=r"strategies\.ma_cross\.trend_filter_enabled must be false"):
        lw._validate_config(_config_live_hop_le())


def test_config_that_co_trend_filter_enabled_false_cho_ca_ba_chien_luoc() -> None:
    for key in ("combo", "ma_cross", "macd_break"):
        assert trend_filter_enabled_for(key) is False


def test_khung_trend_sai_thi_run_forever_dung_truoc_khi_ket_noi_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    from wisdom_engine.des.src.redis_io import live_worker as lw

    monkeypatch.setattr(lw, "create_client", lambda *a, **k: pytest.fail("không được kết nối Redis khi cấu hình sai"))
    monkeypatch.setattr(lw, "trend_source_for", lambda key, symbol, timeframe, method: TrendSource("H1", False, 0))
    with pytest.raises(ValueError, match="smaller than the entry timeframe"):
        lw.run_forever(_config_live_hop_le())


# =============================================================================
# 16. Cấu hình bốn nguồn (redis.outputs, 2026-10-25): DFS ghi nến ở db10, mỗi nguồn lọc một db đầu ra — phản chiếu we_core.
# =============================================================================


def _doc_config(name: str) -> dict:
    from pathlib import Path

    import yaml

    return yaml.safe_load((Path(__file__).resolve().parents[1] / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["des_config.yaml", "des_config.example.yaml"])
def test_config_khai_dau_vao_db10_va_nam_nguon_dau_ra_khac_db(name: str) -> None:
    config = _doc_config(name)
    redis_config = normalize_redis_config(config["redis"])
    assert redis_config["input"]["db"] == 10 and redis_config["input"]["key_prefix"] == "L_DFS"
    assert {source: output["db"] for source, output in redis_config["outputs"].items()} == {
        "raw": 11, "knn": 12, "breakout": 13, "zigzag": 14, "tstat": 15,
    }  # cùng thứ tự DB0 / DB1 / DB2 / DB4 / DB3 của we_core
    assert all(output["key_prefix"] == "L_DES" for output in redis_config["outputs"].values())
    assert not {"output", "output_trend", "output_best"} & set(config["redis"])  # khoá của thiết kế cũ đã bỏ
    for key in ("combo", "ma_cross", "macd_break"):
        section = config["strategies"][key]
        assert "best_trend_by_symbol" not in section  # bản đồ best đã bỏ: mọi kênh có đủ bốn nguồn
        assert section["trend_methods"] == ["knn"], (name, key)  # như we_core (DB1 = knn cho cả ba)
        assert section["trend_filter_enabled"] is False, (name, key)  # nguồn raw luôn không lọc trend


def test_config_that_pass_kiem_tra_khoi_dong_cua_worker() -> None:
    from wisdom_engine.des.src.configuration import CONFIG
    from wisdom_engine.des.src.redis_io import live_worker as lw

    lw._validate_config(CONFIG)  # không raise: bốn nguồn khác db, tiền tố đủ, khung trend của mọi nguồn >= khung vào lệnh
