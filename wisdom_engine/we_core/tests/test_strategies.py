"""Characterization tests for the Combo and MA Cross strategy pipelines.

These lock in current signal-detection behavior on fixed synthetic fixtures
so future refactors can be verified to not change any strategy logic.
"""

from __future__ import annotations

from itertools import pairwise

import pandas as pd
import pytest
from wisdom_engine.we_core.src import configuration

from tests.fixtures import make_ohlcv

TECHNICAL_TREND_KEYS = {
    "PRICE_VALUE",
    "TARGET_VALUE",
    "AI_MA_LEN",
    "AI_TARGET_LEN",
    "AI_K",
    "AI_SMOOTH",
}
ORDER_MANAGEMENT_KEYS = {"TP_RR"}
DOW_CONFIG_KEYS = {
    "DOW_PIVOT_LEFT",
    "DOW_PIVOT_RIGHT",
    "DOW_MIN_ATR_MULT",
    "DOW_ATR_PERIOD",
}


def test_combo_signals_match_golden():
    from wisdom_engine.we_core.src.indicator import add_combo_indicators
    from wisdom_engine.we_core.src.rules.entries import add_combo_entries
    from wisdom_engine.we_core.src.strategies.combo import detect_combo_signals

    # Entry combo = high (BUY) / low (SELL) của nến tín hiệu từ 2026-10-21 (trước đó high+X / low−X với X = 10 của US30):
    # entry_price của hai tín hiệu dưới đây tăng đúng 10 so với golden cũ (88,288889 → 98,288889; 78,30068 → 88,30068).
    df = make_ohlcv(300)
    params = configuration.normalize_combo_params({}, "US30", "H1")
    indicators = add_combo_indicators(df, params)
    signals = detect_combo_signals(indicators, symbol="US30", params=params)
    enriched = add_combo_entries(signals, params, "US30")

    # Tính lại 2026-10-11 khi khôi phục lọc lặp cùng chiều (alternating.py): trước đó
    # {0: 269, 1: 15, -1: 16} và 31 dòng; bar đầu/cuối vẫn là hai tín hiệu dưới đây.
    assert signals["signal"].value_counts().to_dict() == {0: 277, 1: 11, -1: 12}
    rows = enriched[enriched["signal"] != 0]
    assert len(rows) == 23

    first = rows.iloc[0]
    assert str(first["bartime"]) == "2026-01-05 01:45:00"
    assert int(first["signal"]) == -1
    assert round(float(first["entry_price"]), 6) == 98.288889
    assert float(first["entry_price"]) == float(first["low"])       # SELL: entry = low của nến tín hiệu
    # wisdom_engine không tính SL/TP (KSL/KTP chuyển sang order_gateway, chốt 2026-09-21);
    # các cột sl_price/tp_price/risk_reward/entry_time đã bỏ 2026-10-16 (xem entries.py).
    assert not {"sl_price", "tp_price", "risk_reward", "entry_time"} & set(enriched.columns)

    last = rows.iloc[-1]
    assert str(last["bartime"]) == "2026-01-06 00:25:00"
    assert int(last["signal"]) == -1
    assert round(float(last["entry_price"]), 6) == 88.30068
    assert float(last["entry_price"]) == float(last["low"])


def _alternating(setups: str) -> list[int]:
    """"B"/"S" = bar có setup BUY/SELL, "." = không có setup -> signal sau lọc xen kẽ."""
    from wisdom_engine.we_core.src.rules.alternating import alternating_signals

    buy = pd.Series([c == "B" for c in setups])
    sell = pd.Series([c == "S" for c in setups])
    return alternating_signals(buy, sell).tolist()


def test_alternating_chi_ban_tin_hieu_dau_khi_doi_chieu():
    assert _alternating("BBSSBBS") == [1, 0, -1, 0, 1, 0, -1]
    # bar không có setup KHÔNG đổi trạng thái: B..B vẫn là lặp cùng chiều
    assert _alternating("B..B..S.S") == [1, 0, 0, 0, 0, 0, -1, 0, 0]
    # chiều đầu tiên của cửa sổ luôn bắn (không biết gì về trước cửa sổ)
    assert _alternating("SSB") == [-1, 0, 1]
    assert _alternating("......") == [0] * 6


def test_alternating_coi_nan_la_khong_co_setup():
    from wisdom_engine.we_core.src.rules.alternating import alternating_signals

    buy = pd.Series([True, float("nan"), True], dtype="object")
    sell = pd.Series([False, False, False])
    assert alternating_signals(buy, sell).tolist() == [1, 0, 0]


def test_apply_alternating_filter_bat_tat_theo_co():
    from wisdom_engine.we_core.src.rules.alternating import (
        alternating_filter_enabled,
        apply_alternating_filter,
    )

    setups = "BBSSB.S"
    buy = pd.Series([c == "B" for c in setups], index=pd.RangeIndex(10, 10 + len(setups)))
    sell = pd.Series([c == "S" for c in setups], index=buy.index)

    on = apply_alternating_filter(buy, sell, {"ALTERNATING_FILTER_ENABLED": True})
    off = apply_alternating_filter(buy, sell, {"ALTERNATING_FILTER_ENABLED": False})
    assert on.tolist() == [1, 0, -1, 0, 1, 0, -1]
    assert off.tolist() == [1, 1, -1, -1, 1, 0, -1]      # tắt: bar nào có setup cũng bắn
    assert on.index.equals(buy.index) and off.index.equals(buy.index)
    assert str(on.dtype) == str(off.dtype) == "int64"
    # thiếu key / thiếu params = tắt (cùng quy ước với trend_filter_enabled)
    assert apply_alternating_filter(buy, sell, {}).tolist() == off.tolist()
    assert apply_alternating_filter(buy, sell, None).tolist() == off.tolist()
    # chuỗi từ CLI/dashboard cũng được hiểu như _to_bool của configuration
    for text, want in (("true", True), ("1", True), ("on", True), ("false", False), ("0", False), ("", False)):
        assert alternating_filter_enabled({"ALTERNATING_FILTER_ENABLED": text}) is want


def test_combo_signal_cuoi_xen_ke_va_chinh_xac_la_loc_cua_setup():
    """Trên dữ liệu mẫu: tín hiệu liên tiếp luôn đổi hướng, và đúng bằng lọc xen kẽ (viết độc lập,
    không dùng vòng lặp) áp lên toàn bộ setup -- tức chỉ bỏ tín hiệu lặp cùng chiều, không thêm/đổi gì khác."""
    from wisdom_engine.we_core.src.indicator import add_combo_indicators
    from wisdom_engine.we_core.src.strategies import combo

    df = make_ohlcv(600)
    params = configuration.normalize_combo_params({}, "US30", "H1")
    indicators = add_combo_indicators(df, params)
    out = combo.detect_combo_signals(indicators, symbol="US30", params=params)

    sequence = out.loc[out["signal"] != 0, "signal"].astype(int).tolist()
    assert len(sequence) >= 8, "mẫu phải đủ tín hiệu để phép kiểm có nghĩa"
    assert all(a != b for a, b in pairwise(sequence))

    # setup thô = mọi bar thỏa điều kiện (tắt cờ: "bar nào có setup cũng bắn")
    params_off = configuration.normalize_combo_params({"ALTERNATING_FILTER_ENABLED": "false"}, "US30", "H1")
    setups = combo.detect_combo_signals(indicators, symbol="US30", params=params_off)["raw_signal"].astype(int)
    last_setup = setups.where(setups != 0).ffill().shift(1)
    expected = setups.where((setups != 0) & (last_setup.isna() | (last_setup != setups)), 0)
    assert out["raw_signal"].astype(int).tolist() == expected.tolist()
    assert int((setups != 0).sum()) > int((out["raw_signal"] != 0).sum())  # có tín hiệu lặp bị chặn thật


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


def test_combo_lock_xen_ke_truoc_roi_trend_sau():
    """Khoá thứ tự (đổi lại 2026-10-16, về thứ tự có từ trước 2026-10-15): lọc xen kẽ chạy TRƯỚC trên mọi setup,
    trend filter lọc SAU trên raw_signal đã xen kẽ -> signal ⊆ raw_signal (DB1 ⊆ DB0)."""
    from wisdom_engine.we_core.src.strategies.combo import detect_combo_signals

    params = {"SESSION_HOURS_UTC": [], "TREND_FILTER_ENABLED": True, "ALTERNATING_FILTER_ENABLED": True}
    # BUY lệch trend (-1) rồi BUY cùng trend (+1), chưa có setup SELL giữa. BUY thứ nhất vẫn chiếm "lượt đầu" của
    # hướng BUY (raw_signal) dù bị trend loại, nên BUY thứ hai bị coi là lặp (raw_signal = 0) và KHÔNG bắn dù đúng
    # trend: cả đợt mất (bộ lọc không chờ trend đổi chiều giữa chừng).
    out = detect_combo_signals(_combo_trend_frame(".B.B", [0, -1, 0, 1]), params=params)
    assert out["raw_signal"].tolist() == [0, 1, 0, 0]
    assert out["signal"].tolist() == [0, 0, 0, 0]
    # setup SELL xen giữa bị trend loại vẫn "mở lượt" cho BUY sau nó -> hai BUY liền nhau cùng chiều trong signal
    out = detect_combo_signals(_combo_trend_frame("BSB", [1, 0, 1]), params=params)
    assert out["raw_signal"].tolist() == [1, -1, 1]
    assert out["signal"].tolist() == [1, 0, 1]
    assert not ((out["signal"] != 0) & (out["raw_signal"] == 0)).any()  # signal ⊆ raw_signal (DB1 ⊆ DB0)


def test_combo_trend_tat_thi_signal_bang_raw_signal_va_chi_con_loc_xen_ke():
    """Tắt trend: signal == raw_signal == bộ lọc xen kẽ đứng một mình (đúng hành vi trước khi đổi thứ tự)."""
    from wisdom_engine.we_core.src.strategies.combo import detect_combo_signals

    params = {"SESSION_HOURS_UTC": [], "TREND_FILTER_ENABLED": False, "ALTERNATING_FILTER_ENABLED": True}
    out = detect_combo_signals(_combo_trend_frame("BBSS.BSSB", [-1, 1, 0, 1, -1, 0, 1, 1, -1]), params=params)
    assert out["raw_signal"].tolist() == [1, 0, -1, 0, 0, 1, -1, 0, 1]
    assert out["signal"].tolist() == out["raw_signal"].tolist()
    assert set(out["trend_filter_status"]) == {"disabled"}


def test_combo_trend_config_has_only_strategy_level_switches():
    params = configuration.normalize_combo_params({}, "US30", "H1")

    assert params["TREND_FILTER_ENABLED"] is False
    assert params["TREND_METHODS"] == ("knn",)        # mặc định của config từ 2026-10-23: DB1 = KNN (breakout là DB2, qua redis.extra_trend_outputs)
    assert params["TREND_TF"] in configuration.COMBO_TREND_TIMEFRAMES   # knn tra từ bảng trend_tf_by_symbol (không còn khung mặc định chung)
    breakout = configuration.normalize_combo_params({"TREND_METHODS": "breakout"}, "US30", "H1")
    assert breakout["TREND_TF"] == "H1"               # breakout (chọn tường minh) tính trên chính khung của tín hiệu
    assert "default_trend_tf" not in configuration._COMBO_CFG           # khoá đã xoá 2026-10-16
    assert TECHNICAL_TREND_KEYS.isdisjoint(params)
    assert ORDER_MANAGEMENT_KEYS.isdisjoint(params)
    assert configuration.COMBO_TREND_TIMEFRAMES == ("H1", "H2", "H3", "H4")


def test_knn_trend_technical_defaults_live_in_the_knn_block():
    from wisdom_engine.we_core.src.trend_methods.knn import (
        KNN_TREND_DEFAULT_PARAMS,
        knn_trend_indicator_params,
    )

    params = knn_trend_indicator_params({"TREND_TF": "H4"})

    assert set(KNN_TREND_DEFAULT_PARAMS) == TECHNICAL_TREND_KEYS
    assert set(params) == TECHNICAL_TREND_KEYS
    assert params["PRICE_VALUE"] == "hl2"
    assert params["AI_K"] == 3


def test_ma_cross_signals_match_golden():
    from wisdom_engine.we_core.src.indicator import add_ma_cross_indicators
    from wisdom_engine.we_core.src.rules.entries import add_ma_cross_entries
    from wisdom_engine.we_core.src.strategies.ma_cross import detect_ma_cross_signals

    # ma_cross không có tham số nào theo symbol (X, session...) nên
    # entry_price/signal giữ nguyên y hệt bản TESTSYM cũ.
    df = make_ohlcv(300)
    params = configuration.normalize_ma_cross_params({}, "US30", "M30")
    indicators = add_ma_cross_indicators(df, params)
    signals = detect_ma_cross_signals(indicators, symbol="US30", params=params)
    enriched = add_ma_cross_entries(signals, params, "US30")

    # Tính lại 2026-10-16 khi đổi sang luật trạng thái (fast > slow & MACD > 0, chống trùng rồi trend): trước đó
    # {0: 291, -1: 5, 1: 4} và 9 dòng (luật cắt); nay 10 tín hiệu.
    assert signals["signal"].value_counts().to_dict() == {0: 290, 1: 5, -1: 5}
    rows = enriched[enriched["signal"] != 0]
    assert len(rows) == 10

    first = rows.iloc[0]
    assert str(first["bartime"]) == "2026-01-05 02:45:00"
    assert int(first["signal"]) == 1
    assert round(float(first["entry_price"]), 6) == 100.55884

    last = rows.iloc[-1]
    assert str(last["bartime"]) == "2026-01-06 00:45:00"
    assert int(last["signal"]) == -1
    assert round(float(last["entry_price"]), 6) == 89.389466


def test_ma_cross_defaults_match_strategy_specification():
    params = configuration.normalize_ma_cross_params({}, "US30", "M30")

    assert (params["FAST_MA"], params["SLOW_MA"]) == (13, 34)
    assert (
        params["MACD_FAST"],
        params["MACD_SLOW"],
        params["MACD_SIGNAL"],
    ) == (5, 25, 5)
    assert params["ATR_PERIOD"] == 5
    assert params["TREND_FILTER_ENABLED"] is False
    assert params["TREND_METHODS"] == ("knn",)        # mặc định của config từ 2026-10-23: DB1 = KNN
    assert params["TREND_TF"] in configuration.MA_CROSS_TREND_TIMEFRAMES   # knn tra từ bảng trend_tf_by_symbol
    breakout = configuration.normalize_ma_cross_params({"TREND_METHODS": "breakout"}, "US30", "M30")
    assert breakout["TREND_TF"] == "M30"              # breakout (chọn tường minh) tính trên chính khung của tín hiệu
    assert params["ALTERNATING_FILTER_ENABLED"] is True
    assert TECHNICAL_TREND_KEYS.isdisjoint(params)
    assert ORDER_MANAGEMENT_KEYS.isdisjoint(params)
    assert DOW_CONFIG_KEYS.isdisjoint(params)
    assert configuration.MA_CROSS_DEFAULT_TIMEFRAME == "M30"
    assert configuration.MA_CROSS_SUPPORTED_TIMEFRAMES == ("M10", "M20", "M30", "M45")
    assert configuration.MA_CROSS_TREND_TIMEFRAMES == ("H1", "H2", "H3", "H4")


def test_ma_cross_setup_theo_trang_thai_fast_so_slow_va_dau_macd():
    """Luật trạng thái (đổi 2026-10-16): BUY = fast > slow VÀ macd_h > 0; SELL = fast < slow VÀ macd_h < 0.
    Mỗi nến thỏa là một setup (không cần SMA vừa cắt); bằng nhau, thiếu một vế, NaN thì không có setup."""
    from wisdom_engine.we_core.src.strategies.ma_cross import detect_ma_cross_signals

    nan = float("nan")
    frame = pd.DataFrame(
        {
            #             0     1     2     3     4     5     6     7     8     9
            "fast_ma": [2.0, 2.0, 2.0, 1.0, 1.0, 1.0, 2.0, 2.0, nan, 2.0],
            "slow_ma": [1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 2.0, 1.0, 1.0, 1.0],
            "macd_h": [0.1, -0.1, 0.0, -0.1, 0.1, 0.0, 0.1, 0.1, 0.1, 0.1],
            "atr": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, nan],
        }
    )

    result = detect_ma_cross_signals(frame)   # không params: tắt cả trend lẫn chống trùng -> mỗi setup bắn

    # 0: BUY | 1: fast>slow nhưng MACD âm | 2: MACD = 0 | 3: SELL | 4: fast<slow nhưng MACD dương
    # 5: MACD = 0 | 6: fast = slow | 7: BUY (không cần vừa cắt) | 8: fast NaN | 9: ATR NaN
    assert result["signal"].tolist() == [1, 0, 0, -1, 0, 0, 0, 1, 0, 0]
    assert result["raw_signal"].tolist() == result["signal"].tolist()
    assert result.loc[0, "signal_reason"] == "fast SMA above slow SMA; MACD histogram > 0"
    assert result.loc[3, "signal_reason"] == "fast SMA below slow SMA; MACD histogram < 0"
    assert set(result["trend_filter_status"]) == {"disabled"}


def test_ma_cross_khong_con_cot_sma_ky_truoc_va_khong_can_su_kien_cat():
    """Bỏ prev_fast_ma/prev_slow_ma (không còn ai đọc): khung không có hai cột đó vẫn chạy, và nến fast > slow kéo dài
    nhiều nến liền nhau đều là setup (luật cũ chỉ bắn ở đúng nến cắt)."""
    from wisdom_engine.we_core.src.indicator import add_ma_cross_indicators
    from wisdom_engine.we_core.src.strategies.ma_cross import detect_ma_cross_signals

    params = configuration.normalize_ma_cross_params({"ALTERNATING_FILTER_ENABLED": "false"}, "US30", "M30")
    indicators = add_ma_cross_indicators(make_ohlcv(300), params)
    assert {"fast_ma", "slow_ma", "macd_h", "atr"} <= set(indicators.columns)
    assert not {"prev_fast_ma", "prev_slow_ma"} & set(indicators.columns)

    signals = detect_ma_cross_signals(indicators, symbol="US30", params=params)["signal"].astype(int)
    assert int((signals.eq(1) & signals.shift(1).eq(1)).sum()) > 0   # có BUY liền nhau
    assert int((signals.eq(-1) & signals.shift(1).eq(-1)).sum()) > 0   # có SELL liền nhau


def test_ma_cross_nhieu_nen_lien_tiep_cung_thoa_chi_ban_nen_dau_khi_bat_co():
    from wisdom_engine.we_core.src.strategies.ma_cross import detect_ma_cross_signals

    frame = _ma_cross_trend_frame("BBBSSB", [1, 1, 1, -1, -1, 1])
    on = detect_ma_cross_signals(frame, params={"TREND_FILTER_ENABLED": True, "ALTERNATING_FILTER_ENABLED": True})
    off = detect_ma_cross_signals(frame, params={"TREND_FILTER_ENABLED": True, "ALTERNATING_FILTER_ENABLED": False})

    assert on["signal"].tolist() == [1, 0, 0, -1, 0, 1]
    assert off["signal"].tolist() == [1, 1, 1, -1, -1, 1]   # tắt cờ: bar nào có setup (đã qua trend) cũng bắn


def _ma_cross_trend_frame(rows: str, bias: list[int]) -> pd.DataFrame:
    """Khung tối giản cho detect_ma_cross_signals: "B"/"S" = bar có setup BUY/SELL (fast trên/dưới slow + MACD cùng
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


def test_ma_cross_lock_xen_ke_truoc_roi_trend_sau():
    """Khoá thứ tự (như combo/macd_break; chốt lại 2026-10-16): lọc lặp cùng chiều chạy TRƯỚC trên mọi setup, trend
    filter lọc SAU trên raw_signal đã xen kẽ -> signal ⊆ raw_signal (DB1 ⊆ DB0)."""
    from wisdom_engine.we_core.src.strategies.ma_cross import detect_ma_cross_signals

    params = {"TREND_FILTER_ENABLED": True, "ALTERNATING_FILTER_ENABLED": True}
    # BUY lệch trend (-1) rồi BUY cùng trend (+1), chưa có setup SELL giữa: BUY thứ nhất chiếm "lượt đầu" (raw_signal)
    # dù bị trend loại, BUY thứ hai bị coi là lặp và KHÔNG bắn dù đúng trend -> cả đợt mất.
    out = detect_ma_cross_signals(_ma_cross_trend_frame(".B.B", [0, -1, 0, 1]), params=params)
    assert out["raw_signal"].tolist() == [0, 1, 0, 0]
    assert out["signal"].tolist() == [0, 0, 0, 0]
    # đợt BUY đầu đúng trend thì bắn ngay nến đầu; setup SELL xen giữa bị trend loại vẫn "mở lượt" cho BUY sau nó
    out = detect_ma_cross_signals(_ma_cross_trend_frame("BSB", [1, 0, 1]), params=params)
    assert out["raw_signal"].tolist() == [1, -1, 1]
    assert out["signal"].tolist() == [1, 0, 1]
    assert not ((out["signal"] != 0) & (out["raw_signal"] == 0)).any()  # signal ⊆ raw_signal (DB1 ⊆ DB0)


def test_ma_cross_trend_tat_thi_signal_bang_raw_signal_va_chi_con_loc_xen_ke():
    """Tắt trend: signal == raw_signal == bộ lọc lặp cùng chiều đứng một mình, từng bit."""
    from wisdom_engine.we_core.src.strategies.ma_cross import detect_ma_cross_signals

    params = {"TREND_FILTER_ENABLED": False, "ALTERNATING_FILTER_ENABLED": True}
    out = detect_ma_cross_signals(_ma_cross_trend_frame("BBSS.BSSB", [-1, 1, 0, 1, -1, 0, 1, 1, -1]), params=params)
    assert out["raw_signal"].tolist() == [1, 0, -1, 0, 0, 1, -1, 0, 1]
    assert out["signal"].tolist() == out["raw_signal"].tolist()
    assert set(out["trend_filter_status"]) == {"disabled"}


def test_ma_cross_trend_filter_keeps_only_aligned_signals():
    df = make_ohlcv(600)
    trend_bars = make_ohlcv(600, seed=7)
    out = configuration.run_strategy_with_trend_reference(
        "ma_cross", symbol="US30", entry_tf="M30", entry_bars=df, trend_tf="H2", trend_bars=trend_bars, overrides={"TREND_METHODS": "knn"}
    )

    kept = out[out["signal"] != 0]
    assert len(kept) > 0
    assert (kept["signal"] == kept["trend_bias"]).all()
    # xen kẽ chạy trước trend; knn KHÔNG dời tín hiệu (rules/trend_tf.POLICY): chỉ nến đầu đợt (nến có raw_signal) đã cùng chiều trend
    # mới bắn, nên signal ⊆ raw_signal (DB1 ⊆ DB0) và đúng bằng "raw_signal cùng chiều trend"
    raw = out["raw_signal"]
    old_rule = (raw != 0) & (raw == out["trend_bias"].fillna(0))
    assert out["signal"].tolist() == raw.where(old_rule, 0).tolist()
    assert not ((out["signal"] != 0) & (raw == 0)).any()


def test_ma_cross_entries_add_market_entry():
    from wisdom_engine.we_core.src.rules.entries import add_ma_cross_entries

    frame = pd.DataFrame(
        {
            "bartime": pd.to_datetime(["2026-01-01 10:00", "2026-01-01 10:30"]),
            "close": [52252.8, 52252.8],
            "signal": [1, -1],
        }
    )
    params = configuration.normalize_ma_cross_params({}, "US30", "M30")

    result = add_ma_cross_entries(frame, params, "US30")
    buy, sell = result.iloc[0], result.iloc[1]

    assert buy["entry_price"] == pytest.approx(52252.8)
    assert sell["entry_price"] == pytest.approx(52252.8)


def test_ma_cross_rejects_timeframe_outside_execution_set():
    with pytest.raises(ValueError, match="supports only these timeframes"):
        configuration.run_strategy(
            "ma_cross",
            symbol="US30",
            tf="M5",
            bars=make_ohlcv(100),
        )


# --- MACD Break ---------------------------------------------------------------


def _macd_break_frame(rows):
    """DataFrame tối thiểu cho detect_macd_break_signals: mỗi dòng là
    (close, prior_high, prior_low, macd_h, atr)."""
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


def test_macd_break_buy_sell_conditions():
    from wisdom_engine.we_core.src.strategies.macd_break import (
        detect_macd_break_signals,
    )

    nan = float("nan")
    frame = _macd_break_frame(
        [
            (110, 105, 95, 0.5, 1.0),   # phá đỉnh + MACD > 0           -> BUY
            (110, 105, 95, -0.5, 1.0),  # phá đỉnh nhưng MACD < 0       -> không
            (90, 105, 95, -0.5, 1.0),   # phá đáy + MACD < 0            -> SELL
            (90, 105, 95, 0.5, 1.0),    # phá đáy nhưng MACD > 0        -> không
            (105, 105, 95, 0.5, 1.0),   # close == đỉnh cũ (bằng tính)  -> BUY
            (95, 105, 95, -0.5, 1.0),   # close == đáy cũ (bằng tính)   -> SELL
            (110, 105, 95, 0.0, 1.0),   # MACD đúng 0                   -> không
            (100, 105, 95, 0.5, 1.0),   # nằm giữa, không phá           -> không
            (110, nan, 95, 0.5, 1.0),   # thiếu prior_high (chưa warmup) -> không
            (110, 105, 95, 0.5, nan),   # thiếu atr                     -> không
        ]
    )
    out = detect_macd_break_signals(frame, params={})

    assert out["signal"].tolist() == [1, 0, -1, 0, 1, -1, 0, 0, 0, 0]
    assert out["raw_signal"].tolist() == out["signal"].tolist()


def test_macd_break_chi_ban_nen_dau_khi_cung_chieu():
    from wisdom_engine.we_core.src.strategies.macd_break import (
        detect_macd_break_signals,
    )

    # 3 nến liên tiếp cùng phá đỉnh + MACD dương: cờ bật thì chỉ nến đầu bắn (lọc lặp cùng
    # chiều, thêm 2026-10-13).
    frame = _macd_break_frame([(110, 105, 95, 0.5, 1.0)] * 3)
    out = detect_macd_break_signals(frame, params={"ALTERNATING_FILTER_ENABLED": True})

    assert out["signal"].tolist() == [1, 0, 0]


def test_macd_break_tat_co_thi_nen_nao_thoa_dieu_kien_cung_ban():
    from wisdom_engine.we_core.src.strategies.macd_break import (
        detect_macd_break_signals,
    )

    # Cờ tắt (hoặc thiếu) = hành vi trước 2026-10-13: không nhớ "đã có BUY trước đó", nến nào
    # thoả điều kiện cũng bắn (việc bỏ qua tín hiệu khi đang giữ lệnh thuộc hệ thống đặt lệnh).
    frame = _macd_break_frame([(110, 105, 95, 0.5, 1.0)] * 3)
    for params in ({"ALTERNATING_FILTER_ENABLED": False}, {}):
        assert detect_macd_break_signals(frame, params=params)["signal"].tolist() == [1, 1, 1]


def test_macd_break_signal_cuoi_xen_ke_va_chinh_xac_la_loc_cua_setup():
    """Trên dữ liệu mẫu: tín hiệu liên tiếp luôn đổi hướng, và đúng bằng lọc xen kẽ (viết độc lập,
    không dùng vòng lặp) áp lên toàn bộ setup -- tức chỉ bỏ tín hiệu lặp cùng chiều, không thêm/đổi gì khác."""
    from wisdom_engine.we_core.src.indicator import add_macd_break_indicators
    from wisdom_engine.we_core.src.strategies import macd_break

    df = make_ohlcv(600)
    params = configuration.normalize_macd_break_params({}, "US30", "H1")
    indicators = add_macd_break_indicators(df, params)
    out = macd_break.detect_macd_break_signals(indicators, params=params)

    sequence = out.loc[out["signal"] != 0, "signal"].astype(int).tolist()
    assert len(sequence) >= 6, "mẫu phải đủ tín hiệu để phép kiểm có nghĩa"
    assert all(a != b for a, b in pairwise(sequence))

    # setup thô = mọi bar thỏa điều kiện (tắt cờ: "bar nào có setup cũng bắn")
    params_off = configuration.normalize_macd_break_params({"ALTERNATING_FILTER_ENABLED": "false"}, "US30", "H1")
    setups = macd_break.detect_macd_break_signals(indicators, params=params_off)["raw_signal"].astype(int)
    last_setup = setups.where(setups != 0).ffill().shift(1)
    expected = setups.where((setups != 0) & (last_setup.isna() | (last_setup != setups)), 0)
    assert out["raw_signal"].astype(int).tolist() == expected.tolist()
    assert int((setups != 0).sum()) > int((out["raw_signal"] != 0).sum())  # có tín hiệu lặp bị chặn thật


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


def test_macd_break_lock_xen_ke_truoc_roi_trend_sau():
    """Khoá thứ tự (đổi lại 2026-10-16, về thứ tự có từ trước 2026-10-15): lọc xen kẽ chạy TRƯỚC trên mọi setup,
    trend filter lọc SAU trên raw_signal đã xen kẽ -> signal ⊆ raw_signal (DB1 ⊆ DB0)."""
    from wisdom_engine.we_core.src.strategies.macd_break import (
        detect_macd_break_signals,
    )

    params = {"TREND_FILTER_ENABLED": True, "ALTERNATING_FILTER_ENABLED": True}
    # BUY lệch trend (-1) rồi BUY cùng trend (+1): BUY thứ nhất chiếm "lượt đầu" dù bị trend loại -> cả đợt mất.
    out = detect_macd_break_signals(_macd_break_trend_frame(".B.B", [0, -1, 0, 1]), params=params)
    assert out["raw_signal"].tolist() == [0, 1, 0, 0]
    assert out["signal"].tolist() == [0, 0, 0, 0]
    # setup SELL xen giữa bị trend loại vẫn "mở lượt" cho BUY sau nó -> hai BUY liền nhau cùng chiều trong signal
    out = detect_macd_break_signals(_macd_break_trend_frame("BSB", [1, 0, 1]), params=params)
    assert out["raw_signal"].tolist() == [1, -1, 1]
    assert out["signal"].tolist() == [1, 0, 1]
    assert not ((out["signal"] != 0) & (out["raw_signal"] == 0)).any()  # signal ⊆ raw_signal (DB1 ⊆ DB0)


def test_macd_break_trend_tat_thi_signal_bang_raw_signal_va_chi_con_loc_xen_ke():
    from wisdom_engine.we_core.src.strategies.macd_break import (
        detect_macd_break_signals,
    )

    params = {"TREND_FILTER_ENABLED": False, "ALTERNATING_FILTER_ENABLED": True}
    out = detect_macd_break_signals(_macd_break_trend_frame("BBSS.BSSB", [-1, 1, 0, 1, -1, 0, 1, 1, -1]), params=params)
    assert out["raw_signal"].tolist() == [1, 0, -1, 0, 0, 1, -1, 0, 1]
    assert out["signal"].tolist() == out["raw_signal"].tolist()
    assert set(out["trend_filter_status"]) == {"disabled"}


def _signal_tham_chieu(setups: str, bias: list[int], *, trend_on: bool, alternating_on: bool) -> list[int]:
    """Bản tham chiếu viết độc lập bằng vòng lặp thuần: (1) lọc xen kẽ trên mọi setup, (2) trend giữ tín hiệu cùng chiều bias."""
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
def test_signal_khop_ban_tham_chieu_xen_ke_truoc_trend_sau_tren_khung_ngau_nhien(trend_on, alternating_on):
    import numpy as np
    from wisdom_engine.we_core.src.strategies.combo import detect_combo_signals
    from wisdom_engine.we_core.src.strategies.ma_cross import detect_ma_cross_signals
    from wisdom_engine.we_core.src.strategies.macd_break import (
        detect_macd_break_signals,
    )

    rng = np.random.default_rng(20261016)
    params = {
        "SESSION_HOURS_UTC": [], "TREND_FILTER_ENABLED": trend_on, "ALTERNATING_FILTER_ENABLED": alternating_on,
    }
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
            assert out["raw_signal"].tolist() == expected_raw   # raw_signal không phụ thuộc trend
            # signal ⊆ raw_signal (DB1 ⊆ DB0): mỗi tín hiệu giữ lại đúng là một tín hiệu raw, cùng nến, cùng hướng
            assert all(s in (0, r) for s, r in zip(out["signal"].tolist(), out["raw_signal"].tolist(), strict=True))
            if not trend_on:
                assert out["signal"].tolist() == out["raw_signal"].tolist()
            elif alternating_on:
                seq = [v for v in out["signal"].tolist() if v]
                adjacent_same_direction_cases += int(any(a == b for a, b in pairwise(seq)))
    if trend_on and alternating_on:
        # trend loại setup ngược chiều ở giữa thì hai tín hiệu liền nhau có thể cùng chiều: ca này có xuất hiện thật
        assert adjacent_same_direction_cases > 0


def test_macd_break_indicators_exclude_current_bar_and_use_close_only():
    from wisdom_engine.we_core.src.indicator import add_macd_break_indicators

    params = configuration.normalize_macd_break_params({"LOOKBACK_BARS": 3}, "US30", "H1")
    df = make_ohlcv(60)
    out = add_macd_break_indicators(df, params)

    assert out["prior_high_close"].iloc[:3].isna().all()
    assert out["prior_high_close"].iloc[3] == df["close"].iloc[0:3].max()
    assert out["prior_low_close"].iloc[10] == df["close"].iloc[7:10].min()
    # nến hiện tại không nằm trong cửa sổ: high/low không được dùng
    assert out["prior_high_close"].iloc[10] != df["high"].iloc[7:10].max()
    assert {"macd_h", "atr"}.issubset(out.columns)


def test_macd_break_signals_match_golden():
    df = make_ohlcv(600)
    enriched = configuration.run_strategy("macd_break", symbol="US30", tf="H1", bars=df)

    # Tính lại 2026-10-13 khi thêm lọc lặp cùng chiều (alternating.py): trước đó
    # {0: 561, -1: 29, 1: 10} và 39 dòng; tín hiệu đầu tiên giữ nguyên.
    assert enriched["signal"].value_counts().to_dict() == {0: 593, -1: 4, 1: 3}
    # prior_high_close cần đủ LOOKBACK_BARS (100) nến trước -> không có tín hiệu trước bar 100
    assert int(enriched["prior_high_close"].notna().idxmax()) == 100
    rows = enriched[enriched["signal"] != 0]
    assert len(rows) == 7

    first = rows.iloc[0]
    assert str(first["bartime"]) == "2026-01-05 08:50:00"
    assert int(first["signal"]) == -1
    assert round(float(first["entry_price"]), 6) == 93.867578
    # không còn chuỗi nến liền nhau cùng bắn tín hiệu (trước 2026-10-13: 11 cặp nến kề nhau)
    assert int(rows.index.to_series().diff().eq(1).sum()) == 0
    assert all(a != b for a, b in pairwise(rows["signal"].astype(int).tolist()))


def test_macd_break_signals_do_not_look_ahead():
    df = make_ohlcv(600)
    full = configuration.run_strategy("macd_break", symbol="US30", tf="H1", bars=df)
    prefix = configuration.run_strategy("macd_break", symbol="US30", tf="H1", bars=df.iloc[:350].reset_index(drop=True))

    assert full["signal"].iloc[:350].tolist() == prefix["signal"].tolist()


def test_macd_break_defaults_match_strategy_specification():
    params = configuration.normalize_macd_break_params({}, "US30", "H1")

    assert params["LOOKBACK_BARS"] == 100
    assert (params["MACD_FAST"], params["MACD_SLOW"], params["MACD_SIGNAL"]) == (5, 25, 5)
    assert params["ATR_PERIOD"] == 5
    assert params["TREND_FILTER_ENABLED"] is False
    assert params["TREND_METHODS"] == ("knn",)        # macd_break đặt riêng knn (2026-10-23); combo và ma_cross mặc định breakout
    assert params["TREND_TF"] in configuration.MACD_BREAK_TREND_TIMEFRAMES   # knn tra từ bảng trend_tf_by_symbol
    breakout = configuration.normalize_macd_break_params({"TREND_METHODS": "breakout"}, "US30", "H1")
    assert breakout["TREND_TF"] == "H1"               # breakout vẫn chọn được: tính trên chính khung của tín hiệu
    assert "ALLOW_SHORT" not in params
    assert configuration.MACD_BREAK_DEFAULT_TIMEFRAME == "H1"
    assert configuration.MACD_BREAK_SUPPORTED_TIMEFRAMES == ("M10", "M20", "M30", "M45", "H1", "H2", "H3", "H4")
    assert configuration.get_strategy("macd_break").label == "MACD Break"
    assert "breakout_atr" not in configuration.STRATEGIES


def test_macd_break_validates_params_and_timeframes():
    with pytest.raises(ValueError, match="MACD_FAST must be smaller than MACD_SLOW"):
        configuration.normalize_macd_break_params({"MACD_FAST": "30", "MACD_SLOW": "25"})
    with pytest.raises(ValueError, match="supports only these timeframes"):
        configuration.run_strategy("macd_break", symbol="US30", tf="D1", bars=make_ohlcv(150))
    # LOOKBACK_BARS bị kẹp tối đa = số nến warmup, để tín hiệu đầu khoảng không mất im lặng
    kept = configuration.normalize_macd_break_params({"LOOKBACK_BARS": "5000"})
    assert kept["LOOKBACK_BARS"] == configuration.WARMUP_BARS


def test_macd_break_trend_filter_keeps_only_aligned_signals():
    df = make_ohlcv(600)
    trend_bars = make_ohlcv(600, seed=7)
    out = configuration.run_strategy_with_trend_reference(
        "macd_break", symbol="US30", entry_tf="H1", entry_bars=df, trend_tf="H4", trend_bars=trend_bars, overrides={"TREND_METHODS": "knn"}
    )

    kept = out[out["signal"] != 0]
    assert (kept["signal"] == kept["trend_bias"]).all()
    assert len(kept) < int((out["raw_signal"] != 0).sum())


# --- Cờ alternating_filter_enabled ---------------------------------------------


def test_co_alternating_doc_tu_config_cho_ca_ba_chien_luoc():
    for key in ("combo", "macd_break", "ma_cross"):
        spec = configuration.get_strategy(key)
        assert spec.default_params["ALTERNATING_FILTER_ENABLED"] is True
        assert {"key": "ALTERNATING_FILTER_ENABLED", "label": "Alternating Filter", "type": "bool"} in spec.param_fields
        assert spec.normalize_params(None, "US30", spec.default_timeframe)["ALTERNATING_FILTER_ENABLED"] is True
        # override từ CLI/dashboard thắng giá trị trong config
        off = spec.normalize_params({"ALTERNATING_FILTER_ENABLED": "false"}, "US30", spec.default_timeframe)
        assert off["ALTERNATING_FILTER_ENABLED"] is False


def test_config_mau_khai_bao_co_alternating_cho_dung_cac_chien_luoc():
    from pathlib import Path

    import yaml

    example = yaml.safe_load((Path(__file__).resolve().parents[1] / "we_config.example.yaml").read_text(encoding="utf-8"))
    strategies = example["strategies"]

    assert strategies["combo"]["alternating_filter_enabled"] is True
    assert strategies["macd_break"]["alternating_filter_enabled"] is True
    assert strategies["ma_cross"]["alternating_filter_enabled"] is True
    assert set(strategies) == {"combo", "ma_cross", "macd_break"}


def test_combo_tat_co_giong_het_hanh_vi_truoc_khi_co_loc_lap_cung_chieu():
    """Cờ tắt phải cho đúng số liệu golden của combo trước 2026-10-11 (chưa có lọc lặp cùng chiều)."""
    from wisdom_engine.we_core.src.indicator import add_combo_indicators
    from wisdom_engine.we_core.src.strategies.combo import detect_combo_signals

    params = configuration.normalize_combo_params({"ALTERNATING_FILTER_ENABLED": "false"}, "US30", "H1")
    signals = detect_combo_signals(add_combo_indicators(make_ohlcv(300), params), symbol="US30", params=params)

    assert signals["signal"].value_counts().to_dict() == {0: 269, 1: 15, -1: 16}
    assert int((signals["signal"] != 0).sum()) == 31


def test_macd_break_tat_co_giong_het_hanh_vi_truoc_khi_co_loc_lap_cung_chieu():
    """Cờ tắt phải cho đúng số liệu golden của macd_break trước 2026-10-13 (không trạng thái)."""
    enriched = configuration.run_strategy(
        "macd_break", symbol="US30", tf="H1", bars=make_ohlcv(600), overrides={"ALTERNATING_FILTER_ENABLED": "false"}
    )
    rows = enriched[enriched["signal"] != 0]

    assert enriched["signal"].value_counts().to_dict() == {0: 561, -1: 29, 1: 10}
    assert len(rows) == 39
    assert int(rows.index.to_series().diff().eq(1).sum()) == 11  # chuỗi nến liền nhau cùng bắn


def test_ma_cross_tat_co_moi_nen_thoa_dieu_kien_deu_ban():
    """Cờ tắt: bar nào có setup cũng bắn -> rất nhiều tín hiệu liền nhau (~43% số nến mẫu), chỉ để xem/so sánh.
    Bật cờ (mặc định) chỉ còn 10 tín hiệu trên cùng dữ liệu (golden ở test_ma_cross_signals_match_golden)."""
    from wisdom_engine.we_core.src.indicator import add_ma_cross_indicators
    from wisdom_engine.we_core.src.strategies.ma_cross import detect_ma_cross_signals

    params = configuration.normalize_ma_cross_params({"ALTERNATING_FILTER_ENABLED": "false"}, "US30", "M30")
    signals = detect_ma_cross_signals(add_ma_cross_indicators(make_ohlcv(300), params), symbol="US30", params=params)

    assert signals["signal"].value_counts().to_dict() == {0: 172, -1: 79, 1: 49}
    assert int((signals["signal"] != 0).sum()) == 128


def test_chi_con_ba_chien_luoc_sma_trend_da_xoa_khoi_he_thong():
    """sma_trend (chiến lược nghiên cứu) đã xoá khỏi we_core 2026-10-16: sổ đăng ký chỉ còn 3 chiến lược; không còn
    module, hàm chỉ báo/entry, trường StrategySpec.state_warmup_start (chỉ nó dùng), overlay dashboard và khối config."""
    import dataclasses
    import importlib.util
    from pathlib import Path

    from wisdom_engine.we_core.src import indicator
    from wisdom_engine.we_core.src.rules import entries
    from wisdom_engine.we_core.src.signal_display import server

    assert list(configuration.STRATEGIES) == ["combo", "ma_cross", "macd_break"]
    with pytest.raises(KeyError, match="Unknown strategy 'sma_trend'"):
        configuration.get_strategy("sma_trend")
    assert importlib.util.find_spec("wisdom_engine.we_core.src.strategies.sma_trend") is None
    assert not hasattr(indicator, "add_sma_trend_indicators")
    assert not hasattr(entries, "add_sma_trend_entries")
    assert "state_warmup_start" not in {f.name for f in dataclasses.fields(configuration.StrategySpec)}
    assert set(server._STRATEGY_OVERLAYS) == {"combo", "ma_cross", "macd_break"}
    folder = Path(__file__).resolve().parents[1] / "src" / "strategies"
    assert sorted(path.name for path in folder.glob("*.py")) == ["combo.py", "ma_cross.py", "macd_break.py"]


def test_entries_chi_tao_entry_price_va_ma_cross_macd_break_dung_chung_ham_close():
    """Đã bỏ 2026-10-16 các cột không ai đọc (entry_time, sl_price, tp_price, risk_reward): entries chỉ thêm entry_price.
    ma_cross và macd_break cùng một hàm 'entry = close' (trước đó ma_cross chép lại thân hàm)."""
    from wisdom_engine.we_core.src.rules import entries

    frame = pd.DataFrame(
        {
            "bartime": pd.to_datetime(["2026-01-01 10:00", "2026-01-01 10:30", "2026-01-01 11:00"]),
            "high": [11.0, 12.0, 13.0],
            "low": [9.0, 10.0, 11.0],
            "close": [10.5, 11.5, 12.5],
            "signal": [1, 0, -1],
        }
    )
    params = {}
    combo = entries.add_combo_entries(frame, params, "US30")
    cross = entries.add_ma_cross_entries(frame, params, "US30")
    brk = entries.add_macd_break_entries(frame, params, "US30")

    for out in (combo, cross, brk):
        assert set(out.columns) - set(frame.columns) == {"entry_price"}
    assert combo["entry_price"].tolist()[0] == 11.0 and combo["entry_price"].tolist()[2] == 11.0   # BUY = high, SELL = low
    assert pd.isna(combo["entry_price"].iloc[1])
    assert cross.equals(brk) and cross["entry_price"].tolist()[0] == 10.5 and cross["entry_price"].tolist()[2] == 12.5
    assert cross.equals(entries._add_close_market_entries(frame))
    # thiếu cột cần thiết thì giữ nguyên hành vi cũ: trả khung có entry_price toàn NaN, không lỗi
    assert entries.add_ma_cross_entries(frame.drop(columns=["close"]), params)["entry_price"].isna().all()


def test_chien_luoc_khong_con_cot_raw_signal_reason():
    """raw_signal_reason không ai đọc nên đã bỏ 2026-10-16 (des vẫn dùng làm comment, nhưng là hệ riêng)."""
    for key, tf in (("combo", "H1"), ("ma_cross", "M30"), ("macd_break", "H1")):
        out = configuration.run_strategy(key, symbol="US30", tf=tf, bars=make_ohlcv(600))
        assert "raw_signal_reason" not in out.columns
        assert {"raw_signal", "signal", "signal_reason", "trend_filter_status"} <= set(out.columns)


# --- Quy tắc khung trend >= khung tín hiệu (chốt 2026-10-16) --------------------------------------


@pytest.mark.parametrize(
    ("entry_tf", "trend_tf", "allowed"),
    [
        ("H1", "H1", True),     # bằng nhau vẫn được
        ("H1", "H4", True),
        ("M10", "M10", True),
        ("M45", "H1", True),    # 45 phút < 60 phút
        ("H2", "H3", True),     # khung lớn không chia đều khung nhỏ vẫn được
        ("h2", " h4 ", True),   # chữ thường/khoảng trắng được chuẩn hoá
        ("H2", "H1", False),
        ("H4", "H3", False),
        ("H1", "M45", False),
        ("M20", "M10", False),
    ],
)
def test_check_trend_timeframe_cho_phep_lon_hon_hoac_bang_va_cam_nho_hon(entry_tf, trend_tf, allowed):
    if allowed:
        assert configuration.check_trend_timeframe(entry_tf, trend_tf) is None
    else:
        with pytest.raises(ValueError, match="must not be smaller than the entry timeframe"):
            configuration.check_trend_timeframe(entry_tf, trend_tf)


def test_check_trend_timeframe_bao_loi_khi_ma_khung_khong_hop_le():
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        configuration.check_trend_timeframe("H1", "X9")


@pytest.mark.parametrize(
    ("strategy", "entry_tf", "trend_tf"),
    [("combo", "H2", "H1"), ("ma_cross", "M45", "M30"), ("macd_break", "H4", "H3")],
)
def test_run_strategy_with_trend_reference_cam_khung_trend_nho_hon_khung_tin_hieu(strategy, entry_tf, trend_tf):
    with pytest.raises(ValueError, match="must not be smaller than the entry timeframe"):
        configuration.run_strategy_with_trend_reference(
            strategy,
            symbol="US30",
            entry_tf=entry_tf,
            entry_bars=make_ohlcv(50),
            trend_tf=trend_tf,
            trend_bars=make_ohlcv(50),
        )


def test_run_strategy_with_trend_reference_van_chay_khi_khung_trend_bang_hoac_lon_hon():
    df, trend_bars = make_ohlcv(300), make_ohlcv(300, seed=7)
    for strategy, entry_tf, trend_tf in (("combo", "H1", "H1"), ("combo", "H1", "H4"), ("macd_break", "H2", "H4")):
        out = configuration.run_strategy_with_trend_reference(
            strategy, symbol="US30", entry_tf=entry_tf, entry_bars=df, trend_tf=trend_tf, trend_bars=trend_bars,
            overrides={"TREND_METHODS": "knn"},  # khung tham chiếu riêng chỉ có với knn; breakout luôn dùng khung vào lệnh
        )
        assert {"raw_signal", "signal", "trend_bias"} <= set(out.columns)


def _timeframes_the_worker_runs():
    from wisdom_engine.we_core.src.we_signal.redis_io import worker

    return worker._STRATEGY_TIMEFRAMES


def test_khung_trend_cua_config_thoa_quy_tac_voi_moi_symbol_va_khung_vao_lenh_cua_worker():
    """Config thật: khung trend của MỌI (chiến lược, symbol, khung vào lệnh) mà worker chạy (ô trong trend_tf_by_symbol, bắt buộc
    đủ ô) có mặt và >= khung vào lệnh."""
    from wisdom_engine.we_core.src.indicator import timeframe_minutes

    for strategy, timeframes in _timeframes_the_worker_runs().items():
        spec = configuration.get_strategy(strategy)
        for symbol in sorted(configuration.OPERATING_SYMBOLS):
            for timeframe in timeframes:
                trend_tf = spec.normalize_params(None, symbol, timeframe)["TREND_TF"]
                assert trend_tf, (strategy, symbol, timeframe)       # bảng đủ ô: không có ô nào chưa xác định
                assert timeframe_minutes(trend_tf) >= timeframe_minutes(timeframe), (strategy, symbol, timeframe, trend_tf)


def test_config_mau_co_khung_trend_thoa_quy_tac_voi_moi_khung_vao_lenh_cua_worker():
    from pathlib import Path

    import yaml
    from wisdom_engine.we_core.src.indicator import timeframe_minutes

    example = yaml.safe_load((Path(__file__).resolve().parents[1] / "we_config.example.yaml").read_text(encoding="utf-8"))
    combo_block = example["strategies"]["combo"]
    symbols = tuple(combo_block["symbols"])
    for strategy, timeframes in _timeframes_the_worker_runs().items():
        block = example["strategies"][strategy]
        by_symbol = configuration._strategy_trend_tf_by_symbol(     # bảng mẫu phải qua đúng bộ kiểm của config thật
            block, f"strategies.{strategy}", symbols=symbols, entry_timeframes=tuple(timeframes), trend_timeframes=tuple(block["trend_timeframes"])
        )
        for symbol in symbols:
            for timeframe in timeframes:
                trend_tf = configuration.resolve_trend_tf(by_symbol, symbol, timeframe)
                assert timeframe_minutes(trend_tf) >= timeframe_minutes(timeframe), (strategy, symbol, timeframe, trend_tf)
        assert "default_trend_tf" not in block          # khoá đã xoá 2026-10-16: bảng là nguồn duy nhất


# --- Khung trend riêng theo (symbol, khung vào lệnh): strategies.<tên>.trend_tf_by_symbol (2026-10-16) ----------------


def test_resolve_trend_tf_tra_o_theo_symbol_va_khung_vao_lenh_thieu_o_thi_rong():
    by_symbol = {"BTCUSD": {"H1": "H3", "H2": "H2"}}
    resolve = configuration.resolve_trend_tf

    assert resolve(by_symbol, "BTCUSD", "H1") == "H3"
    assert resolve(by_symbol, " btcusd ", "h2") == "H2"      # chữ thường/khoảng trắng được chuẩn hoá
    assert resolve(by_symbol, "BTCUSD", "H3") == ""          # ô thiếu -> chưa xác định (không có khung mặc định)
    assert resolve(by_symbol, "US30", "H1") == ""            # symbol không khai -> chưa xác định
    assert resolve(by_symbol, None, None) == ""
    assert resolve({}, "BTCUSD", "M30") == ""


@pytest.mark.parametrize(
    ("strategy", "attr", "mapped_tf", "other_tf"),
    [
        ("combo", "COMBO_TREND_TF_BY_SYMBOL", "H2", "H3"),
        ("ma_cross", "MA_CROSS_TREND_TF_BY_SYMBOL", "M30", "M20"),
        ("macd_break", "MACD_BREAK_TREND_TF_BY_SYMBOL", "H2", "H1"),
    ],
)
def test_normalize_params_dung_khung_trend_theo_o_va_override_van_thang(monkeypatch, strategy, attr, mapped_tf, other_tf):
    spec = configuration.get_strategy(strategy)
    assert "TREND_TF" not in spec.default_params    # không còn khung trend mặc định chung
    monkeypatch.setattr(configuration, attr, {"US30": {mapped_tf: "H3"}})

    knn = {"TREND_METHODS": "knn"}   # bảng khung trend chỉ dùng cho khối cần khung tham chiếu; mặc định của config là breakout (khung vào lệnh)
    default_is_breakout = spec.default_params["TREND_METHODS"] == ("breakout",)   # mặc định đặt riêng theo từng chiến lược trong config
    assert spec.normalize_params(None, "US30", mapped_tf)["TREND_TF"] == (mapped_tf if default_is_breakout else "H3")
    assert spec.normalize_params(knn, "US30", mapped_tf)["TREND_TF"] == "H3"          # ô riêng
    assert spec.normalize_params(knn, "US30", other_tf)["TREND_TF"] == ""             # khung vào lệnh khác, không có ô -> chưa xác định
    assert spec.normalize_params(knn, "DE40", mapped_tf)["TREND_TF"] == ""            # symbol khác -> chưa xác định
    assert spec.normalize_params({**knn, "TREND_TF": "H1"}, "US30", mapped_tf)["TREND_TF"] == "H1"   # override tường minh thắng
    assert spec.normalize_params({**knn, "TREND_TF": ""}, "US30", mapped_tf)["TREND_TF"] == "H3"     # override rỗng = không có
    assert spec.normalize_params({**knn, "TREND_TF": None}, "US30", mapped_tf)["TREND_TF"] == "H3"


def test_trend_tf_by_symbol_doc_va_chuan_hoa_config_hop_le():
    read = configuration._strategy_trend_tf_by_symbol
    kwargs = {"symbols": ("BTCUSD", "US30"), "entry_timeframes": ("H1", "H2"), "trend_timeframes": ("H1", "H2", "H3", "H4")}

    got = read({"trend_tf_by_symbol": {"btcusd": {"h1": "h4", "H2": "H2"}, "US30": {"H1": "H1", "H2": "H4"}}}, "strategies.combo", **kwargs)
    assert got == {"BTCUSD": {"H1": "H4", "H2": "H2"}, "US30": {"H1": "H1", "H2": "H4"}}

    # khoá BẮT BUỘC và phải đủ ô (2026-10-16: không còn khung mặc định chung để dự phòng)
    with pytest.raises(KeyError, match="trend_tf_by_symbol"):
        read({}, "strategies.combo", **kwargs)
    with pytest.raises(TypeError, match="must map symbol"):
        read({"trend_tf_by_symbol": None}, "strategies.combo", **kwargs)
    with pytest.raises(ValueError, match=r"US30: no trend timeframe for entry timeframe\(s\) H2"):
        read({"trend_tf_by_symbol": {"BTCUSD": {"H1": "H1", "H2": "H2"}, "US30": {"H1": "H1"}}}, "strategies.combo", **kwargs)
    with pytest.raises(ValueError, match=r"BTCUSD: no trend timeframe for entry timeframe\(s\) H1, H2"):
        read({"trend_tf_by_symbol": {"US30": {"H1": "H1", "H2": "H2"}}}, "strategies.combo", **kwargs)


def test_trend_tf_by_symbol_bao_loi_moi_o_sai_trong_mot_lan():
    read = configuration._strategy_trend_tf_by_symbol
    kwargs = {"symbols": ("BTCUSD", "US30"), "entry_timeframes": ("H1", "H2", "H3", "H4"), "trend_timeframes": ("H1", "H2", "H3", "H4")}
    bad = {
        "trend_tf_by_symbol": {
            "ETHUSD": {"H1": "H4"},                                  # symbol lạ
            "BTCUSD": {"H2": "H1", "M30": "H4", "H3": "M45"},        # nhỏ hơn khung vào lệnh / khung vào ngoài tập / khung trend ngoài danh sách
            "US30": "H4",                                            # sai kiểu
        }
    }

    with pytest.raises(ValueError) as excinfo:
        read(bad, "strategies.combo", **kwargs)

    message = str(excinfo.value)
    assert "strategies.combo.trend_tf_by_symbol" in message
    for part in (
        "ETHUSD: unknown symbol",
        "BTCUSD H2: TREND_TF H1 must not be smaller than the entry timeframe H2",
        "BTCUSD M30: entry timeframe not in",
        "BTCUSD H3: trend timeframe M45 not in",
        "US30: must map entry timeframe -> trend timeframe",
    ):
        assert part in message
    with pytest.raises(TypeError, match="must map symbol"):
        read({"trend_tf_by_symbol": ["H4"]}, "strategies.combo", **kwargs)


def test_strategy_spec_mang_bang_khung_trend_theo_o_cua_config():
    assert configuration.STRATEGIES["combo"].trend_tf_by_symbol is configuration.COMBO_TREND_TF_BY_SYMBOL
    assert configuration.STRATEGIES["ma_cross"].trend_tf_by_symbol is configuration.MA_CROSS_TREND_TF_BY_SYMBOL
    assert configuration.STRATEGIES["macd_break"].trend_tf_by_symbol is configuration.MACD_BREAK_TREND_TF_BY_SYMBOL


# --- Entry combo = high/low của nến tín hiệu (2026-10-21), không còn X hay % ATR ---------------------------------


def test_combo_entry_bang_dung_high_cua_buy_va_low_cua_sell_khong_dem():
    from wisdom_engine.we_core.src.rules import entries

    frame = pd.DataFrame(
        {
            "high": [1.1010, 1.1020, 1.1030, 50.0],
            "low": [1.0990, 1.1000, 1.1010, 40.0],
            "close": [1.1010, 1.1015, 1.1015, 45.0],        # nến đầu đóng cửa đúng tại high: entry == close (không xử lý đặc biệt)
            "atr": [0.0010, 0.0020, 0.0040, 3.0],
            "signal": [1, 0, -1, 1],
        }
    )
    # tham số cũ (X, ENTRY_ATR_PCT) nếu còn truyền vào cũng bị bỏ qua: không còn đệm nào
    for params in ({}, {"X": 99.0, "ENTRY_ATR_PCT": 5.0}):
        out = entries.add_combo_entries(frame, params, "EURUSD")
        assert out["entry_price"].iloc[0] == 1.1010 == out["close"].iloc[0]
        assert pd.isna(out["entry_price"].iloc[1])
        assert out["entry_price"].iloc[2] == 1.1010
        assert out["entry_price"].iloc[3] == 50.0
        assert set(out.columns) - set(frame.columns) == {"entry_price"}


def test_combo_entry_thieu_cot_can_thiet_tra_khung_entry_toan_nan_khong_loi():
    from wisdom_engine.we_core.src.rules import entries

    frame = pd.DataFrame({"high": [11.0], "signal": [1]})
    assert entries.add_combo_entries(frame, {}, "US30")["entry_price"].isna().all()


def test_combo_khong_con_tham_so_x_hay_entry_atr_pct_va_override_cu_bi_bo_qua():
    spec = configuration.get_strategy("combo")
    keys = {field["key"] for field in spec.param_fields}

    assert not {"X", "ENTRY_ATR_PCT"} & keys
    assert not {"X", "ENTRY_ATR_PCT"} & set(spec.default_params)
    for symbol in ("US30", "EURUSD", "USDJPY"):
        params = spec.normalize_params({"X": "10", "ENTRY_ATR_PCT": "5"}, symbol, "H1")
        assert not {"X", "ENTRY_ATR_PCT"} & set(params)
    assert not hasattr(configuration, "COMBO_SYMBOL_X") and not hasattr(configuration, "COMBO_SYMBOL_ENTRY_ATR_PCT")
    assert not hasattr(configuration.StrategySpec, "tuned_symbols")


def test_pipeline_combo_moi_symbol_ke_ca_forex_entry_bang_high_low_cua_nen_tin_hieu():
    for symbol in ("US30", "EURUSD", "USDJPY", "BTCUSD"):
        enriched = configuration.run_strategy("combo", symbol=symbol, tf="H1", bars=make_ohlcv(400))
        signals = enriched[enriched["signal"].isin([1, -1])]

        assert len(signals) > 0
        buy = signals[signals["signal"] == 1]
        sell = signals[signals["signal"] == -1]
        assert (buy["entry_price"] == buy["high"]).all() and (sell["entry_price"] == sell["low"]).all()
        assert (buy["entry_price"] >= buy["close"]).all() and (sell["entry_price"] <= sell["close"]).all()   # mép nến không bao giờ nằm trong thân


def test_symbol_van_hanh_la_danh_sach_strategies_combo_symbols_trong_config():
    assert configuration.OPERATING_SYMBOLS == (
        "US30", "US500", "US100", "DE40", "UK100", "FR40", "SP35", "HK50", "J225", "GOLD", "BTCUSD", "EURUSD", "USDJPY", "GBPUSD", "USDCAD",
    )
    assert len(set(configuration.OPERATING_SYMBOLS)) == len(configuration.OPERATING_SYMBOLS) == 15


def test_doc_danh_sach_symbol_kiem_tra_rong_trung_va_sai_kieu():
    read = configuration._operating_symbols

    assert read({"symbols": [" us30 ", "Eurusd"]}, "strategies.combo") == ("US30", "EURUSD")      # chuẩn hoá chữ hoa, bỏ khoảng trắng
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


def test_worker_bootstrap_chay_ca_ba_chien_luoc_tren_ca_nhom_forex():
    from wisdom_engine.we_core.src.we_signal.redis_io import worker

    pairs = worker._bootstrap_pairs()

    assert len(pairs) == 15 * (4 + 4 + 8)
    assert {(s, t) for strategy, s, t in pairs if strategy == "combo" and s == "EURUSD"} == {("EURUSD", f"H{i}") for i in (1, 2, 3, 4)}
    assert {strategy for strategy, s, _ in pairs if s == "USDJPY"} == {"combo", "ma_cross", "macd_break"}


def test_worker_cho_phep_trigger_forex_va_van_bo_qua_symbol_ngoai_pham_vi(monkeypatch):
    from wisdom_engine.we_core.src.we_signal.redis_io import worker

    seen = []
    monkeypatch.setattr(worker, "_safe_refresh_pair", lambda *args, **kwargs: seen.append(kwargs["symbol"]))
    on_update = worker._make_on_update(None, key_prefix="x", trend_client=None, trend_key_prefix="y")

    on_update("H1", "EURUSD", None, None)
    on_update("H1", "AUDCAD", None, None)          # có trong SQL nhưng không phải symbol vận hành
    on_update("W", "EURUSD", None, None)           # khung không chạy

    assert seen == ["EURUSD"]


def test_worker_ghi_so_chu_so_thap_phan_theo_symbol_vao_frame(monkeypatch):
    from wisdom_engine.we_core.src.we_signal.redis_io import worker

    calls = []
    monkeypatch.setattr(worker, "trim_to_requested_range", lambda df, start: df)
    monkeypatch.setattr(worker, "signal_frame", lambda df, *, strategy, decimals: calls.append(decimals) or pd.DataFrame())
    monkeypatch.setattr(worker, "publish_past_signals", lambda *args, **kwargs: 0)
    for symbol in ("US30", "EURUSD", "USDJPY"):
        worker._compute_and_publish(
            None, key_prefix="x", symbol=symbol, timeframe="H1", strategy="combo", enriched=pd.DataFrame(),
            effective_from=pd.Timestamp("2022-01-01"), delta_mode=False, window_to=None,
        )

    assert calls == [2, 5, 3]
