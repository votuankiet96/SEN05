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
    from wisdom_engine.we_core.src.levels import add_combo_levels
    from wisdom_engine.we_core.src.strategies.combo import detect_combo_signals

    # US30 (X thật = 10.0) thay cho TESTSYM cũ (X = 0.0 do fallback) nên
    # entry_price lệch đúng 10 so với bản golden cũ.
    df = make_ohlcv(300)
    params = configuration.normalize_combo_params({}, "US30", "H1")
    indicators = add_combo_indicators(df, params)
    signals = detect_combo_signals(indicators, symbol="US30", params=params)
    enriched = add_combo_levels(signals, params, "US30")

    # Tính lại 2026-10-11 khi khôi phục lọc lặp cùng chiều (alternating.py): trước đó
    # {0: 269, 1: 15, -1: 16} và 31 dòng; bar đầu/cuối vẫn là hai tín hiệu dưới đây.
    assert signals["signal"].value_counts().to_dict() == {0: 277, 1: 11, -1: 12}
    rows = enriched[enriched["signal"] != 0]
    assert len(rows) == 23

    first = rows.iloc[0]
    assert str(first["bartime"]) == "2026-01-05 01:45:00"
    assert int(first["signal"]) == -1
    assert round(float(first["entry_price"]), 6) == 88.288889
    # wisdom_engine không tính SL/TP (KSL/KTP chuyển sang order_gateway,
    # chốt 2026-09-21) -- sl_price/tp_price luôn NaN, xem levels.py.
    assert pd.isna(first["sl_price"])
    assert pd.isna(first["tp_price"])

    last = rows.iloc[-1]
    assert str(last["bartime"]) == "2026-01-06 00:25:00"
    assert int(last["signal"]) == -1
    assert round(float(last["entry_price"]), 6) == 78.30068
    assert pd.isna(last["sl_price"])
    assert pd.isna(last["tp_price"])


def _alternating(setups: str) -> list[int]:
    """"B"/"S" = bar có setup BUY/SELL, "." = không có setup -> signal sau lọc xen kẽ."""
    from wisdom_engine.we_core.src.alternating import alternating_signals

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
    from wisdom_engine.we_core.src.alternating import alternating_signals

    buy = pd.Series([True, float("nan"), True], dtype="object")
    sell = pd.Series([False, False, False])
    assert alternating_signals(buy, sell).tolist() == [1, 0, 0]


def test_apply_alternating_filter_bat_tat_theo_co():
    from wisdom_engine.we_core.src.alternating import (
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


def test_combo_lock_xen_ke_dung_truoc_trend_filter():
    """Khoá thứ tự: lọc xen kẽ chạy trên setup TRƯỚC, rồi trend filter mới lọc (signal ⊆ raw_signal, DB1 ⊆ DB0).
    Hệ quả đã biết: setup bị trend filter loại vẫn chiếm lượt đầu tiên của hướng đó."""
    from wisdom_engine.we_core.src.strategies.combo import detect_combo_signals

    params = {"SESSION_HOURS_UTC": [], "TREND_FILTER_ENABLED": True, "ALTERNATING_FILTER_ENABLED": True}
    # BUY lệch trend (-1) rồi BUY cùng trend (+1), chưa có setup SELL giữa: BUY thứ hai bị coi là lặp -> mất
    out = detect_combo_signals(_combo_trend_frame(".B.B", [0, -1, 0, 1]), params=params)
    assert out["raw_signal"].tolist() == [0, 1, 0, 0]
    assert out["signal"].tolist() == [0, 0, 0, 0]
    # có setup SELL xen giữa (dù cũng bị trend loại) thì BUY sau lại là "lượt đầu" -> bắn nếu cùng trend
    out = detect_combo_signals(_combo_trend_frame(".BSB", [0, -1, -1, 1]), params=params)
    assert out["raw_signal"].tolist() == [0, 1, -1, 1]
    assert out["signal"].tolist() == [0, 0, -1, 1]
    assert ((out["signal"] != 0) <= (out["raw_signal"] != 0)).all()


def test_combo_trend_config_has_only_strategy_level_switches():
    params = configuration.normalize_combo_params({}, "US30", "H1")

    assert params["TREND_FILTER_ENABLED"] is False
    assert params["TREND_TYPE"] == "knn"
    assert params["TREND_TF"] == "H4"
    assert TECHNICAL_TREND_KEYS.isdisjoint(params)
    assert ORDER_MANAGEMENT_KEYS.isdisjoint(params)
    assert configuration.COMBO_TREND_TIMEFRAMES == ("H1", "H2", "H3", "H4")


def test_knn_trend_technical_defaults_live_in_trend_module():
    from wisdom_engine.we_core.src.trend import (
        KNN_TREND_DEFAULT_PARAMS,
        knn_trend_indicator_params,
    )

    params = knn_trend_indicator_params({"TREND_TF": "H4"})

    assert set(KNN_TREND_DEFAULT_PARAMS) == TECHNICAL_TREND_KEYS
    assert params["TREND_TF"] == "H4"
    assert params["PRICE_VALUE"] == "hl2"
    assert params["AI_K"] == 3


def test_ma_cross_signals_match_golden():
    from wisdom_engine.we_core.src.indicator import add_ma_cross_indicators
    from wisdom_engine.we_core.src.levels import add_ma_cross_levels
    from wisdom_engine.we_core.src.strategies.ma_cross import detect_ma_cross_signals

    # ma_cross không có tham số nào theo symbol (X, session...) nên
    # entry_price/signal giữ nguyên y hệt bản TESTSYM cũ.
    df = make_ohlcv(300)
    params = configuration.normalize_ma_cross_params({}, "US30", "M30")
    indicators = add_ma_cross_indicators(df, params)
    signals = detect_ma_cross_signals(indicators, symbol="US30", params=params)
    enriched = add_ma_cross_levels(signals, params, "US30")

    assert signals["signal"].value_counts().to_dict() == {0: 291, -1: 5, 1: 4}
    rows = enriched[enriched["signal"] != 0]
    assert len(rows) == 9

    first = rows.iloc[0]
    assert str(first["bartime"]) == "2026-01-05 05:40:00"
    assert int(first["signal"]) == 1
    assert round(float(first["entry_price"]), 6) == 104.270629
    # Không tính SL/TP ở wisdom_engine (xem test combo golden phía trên).
    assert pd.isna(first["sl_price"])
    assert pd.isna(first["tp_price"])


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
    assert params["TREND_TYPE"] == "knn"
    assert params["TREND_TF"] == "H4"
    assert TECHNICAL_TREND_KEYS.isdisjoint(params)
    assert ORDER_MANAGEMENT_KEYS.isdisjoint(params)
    assert DOW_CONFIG_KEYS.isdisjoint(params)
    assert configuration.MA_CROSS_DEFAULT_TIMEFRAME == "M30"
    assert configuration.MA_CROSS_SUPPORTED_TIMEFRAMES == ("M10", "M20", "M30", "M45")
    assert configuration.MA_CROSS_TREND_TIMEFRAMES == ("H1", "H2", "H3", "H4")


def test_ma_cross_requires_macd_histogram_confirmation():
    from wisdom_engine.we_core.src.strategies.ma_cross import detect_ma_cross_signals

    frame = pd.DataFrame(
        {
            "fast_ma": [2.0, 2.0, 1.0, 1.0],
            "slow_ma": [1.0, 1.0, 2.0, 2.0],
            "prev_fast_ma": [0.0, 0.0, 3.0, 3.0],
            "prev_slow_ma": [1.0, 1.0, 2.0, 2.0],
            "macd_h": [0.1, -0.1, -0.1, 0.1],
            "atr": [1.0, 1.0, 1.0, 1.0],
        }
    )

    result = detect_ma_cross_signals(frame)

    assert result["signal"].tolist() == [1, 0, -1, 0]


def test_ma_cross_trend_wait_fires_aligned_cross_and_holds_misaligned_one_pending():
    from wisdom_engine.we_core.src.strategies.ma_cross import detect_ma_cross_signals

    frame = pd.DataFrame(
        {
            "fast_ma": [2.0, 2.0, 1.0, 1.0],
            "slow_ma": [1.0, 1.0, 2.0, 2.0],
            "prev_fast_ma": [0.0, 0.0, 3.0, 3.0],
            "prev_slow_ma": [1.0, 1.0, 2.0, 2.0],
            "macd_h": [0.1, -0.1, -0.1, 0.1],
            "atr": [1.0, 1.0, 1.0, 1.0],
            "trend_bias": [1, 1, 1, -1],
        }
    )

    result = detect_ma_cross_signals(frame, params={"TREND_FILTER_ENABLED": True})

    assert result["raw_signal"].tolist() == [1, 0, -1, 0]
    assert result["signal"].tolist() == [1, 0, 0, 0]
    # Trend-wait (_apply_trend_wait, chốt 2026-09-25): bar 0 cắt lên khi trend
    # đã +1 -> nổ ngay; bar 2 cắt xuống nhưng trend +1 -> CHỜ (không bị loại
    # hẳn như trend filter cũ), bar 3 vẫn chờ vì MA chưa đảo và MACD > 0.
    assert result["trend_filter_status"].tolist() == ["fired", "no_regime", "pending", "pending"]


def test_ma_cross_levels_add_market_entry():
    from wisdom_engine.we_core.src.levels import add_ma_cross_levels

    frame = pd.DataFrame(
        {
            "bartime": pd.to_datetime(["2026-01-01 10:00", "2026-01-01 10:30"]),
            "close": [52252.8, 52252.8],
            "signal": [1, -1],
        }
    )
    params = configuration.normalize_ma_cross_params({}, "US30", "M30")

    result = add_ma_cross_levels(frame, params, "US30")
    buy, sell = result.iloc[0], result.iloc[1]

    assert buy["entry_price"] == pytest.approx(52252.8)
    assert pd.isna(buy["sl_price"])
    assert pd.isna(buy["tp_price"])
    assert pd.isna(buy["risk_reward"])

    assert sell["entry_price"] == pytest.approx(52252.8)
    assert pd.isna(sell["sl_price"])
    assert pd.isna(sell["tp_price"])
    assert pd.isna(sell["risk_reward"])


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


def test_macd_break_lock_xen_ke_dung_truoc_trend_filter():
    """Khoá thứ tự: lọc xen kẽ chạy trên setup TRƯỚC, rồi trend filter mới lọc (signal ⊆ raw_signal, DB1 ⊆ DB0).
    Hệ quả đã biết: setup bị trend filter loại vẫn chiếm lượt đầu tiên của hướng đó."""
    from wisdom_engine.we_core.src.strategies.macd_break import (
        detect_macd_break_signals,
    )

    params = {"TREND_FILTER_ENABLED": True, "ALTERNATING_FILTER_ENABLED": True}
    # BUY lệch trend (-1) rồi BUY cùng trend (+1), chưa có setup SELL giữa: BUY thứ hai bị coi là lặp -> mất
    out = detect_macd_break_signals(_macd_break_trend_frame(".B.B", [0, -1, 0, 1]), params=params)
    assert out["raw_signal"].tolist() == [0, 1, 0, 0]
    assert out["signal"].tolist() == [0, 0, 0, 0]
    # có setup SELL xen giữa (dù cũng bị trend loại) thì BUY sau lại là "lượt đầu" -> bắn nếu cùng trend
    out = detect_macd_break_signals(_macd_break_trend_frame(".BSB", [0, -1, -1, 1]), params=params)
    assert out["raw_signal"].tolist() == [0, 1, -1, 1]
    assert out["signal"].tolist() == [0, 0, -1, 1]
    assert ((out["signal"] != 0) <= (out["raw_signal"] != 0)).all()


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
    # wisdom_engine không tính SL/TP (thuộc quản lý lệnh)
    assert pd.isna(first["sl_price"])
    assert pd.isna(first["tp_price"])
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
    assert params["TREND_TYPE"] == "knn"
    assert params["TREND_TF"] == "H4"
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
        "macd_break", symbol="US30", entry_tf="H1", entry_bars=df, trend_tf="H4", trend_bars=trend_bars
    )

    kept = out[out["signal"] != 0]
    assert (kept["signal"] == kept["trend_bias"]).all()
    assert len(kept) < int((out["raw_signal"] != 0).sum())


# --- Cờ alternating_filter_enabled ---------------------------------------------


def test_co_alternating_doc_tu_config_va_chi_co_o_combo_macd_break():
    for key in ("combo", "macd_break"):
        spec = configuration.get_strategy(key)
        assert spec.default_params["ALTERNATING_FILTER_ENABLED"] is True
        assert {"key": "ALTERNATING_FILTER_ENABLED", "label": "Alternating Filter", "type": "bool"} in spec.param_fields
        assert spec.normalize_params(None, "US30", "H1")["ALTERNATING_FILTER_ENABLED"] is True
        # override từ CLI/dashboard thắng giá trị trong config
        off = spec.normalize_params({"ALTERNATING_FILTER_ENABLED": "false"}, "US30", "H1")
        assert off["ALTERNATING_FILTER_ENABLED"] is False
    # chiến lược không dùng bộ lọc thì không có cờ
    for key in ("ma_cross", "sma_trend"):
        spec = configuration.get_strategy(key)
        assert "ALTERNATING_FILTER_ENABLED" not in spec.default_params
        assert all(field["key"] != "ALTERNATING_FILTER_ENABLED" for field in spec.param_fields)


def test_config_mau_khai_bao_co_alternating_cho_dung_cac_chien_luoc():
    from pathlib import Path

    import yaml

    example = yaml.safe_load((Path(__file__).resolve().parents[1] / "we_config.example.yaml").read_text(encoding="utf-8"))
    strategies = example["strategies"]

    assert strategies["combo"]["alternating_filter_enabled"] is True
    assert strategies["macd_break"]["alternating_filter_enabled"] is True
    assert "alternating_filter_enabled" not in strategies["ma_cross"]
    assert "alternating_filter_enabled" not in strategies["sma_trend"]


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
