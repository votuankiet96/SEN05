"""Tests cho các khối xác nhận xu hướng (src/trend_methods/) của des: khối KNN, khối T-STAT, sổ đăng ký, ghép nhiều khối.

Khối thứ hai (breakout), bảng chính sách POLICY và bộ điều phối apply_signal_filters được chép từ we_core (đồng bộ
2026-10-23); khối thứ ba (zigzag) và warm-up nến trend của nó chép từ we_core (đồng bộ 2026-10-25); phần "nhiều khối" vẫn được kiểm bằng một khối GIẢ chỉ tồn tại trong test (đăng ký tạm bằng monkeypatch,
tự gỡ sau mỗi test). Bộ nến tạo tất định ngay trong file này (des/tests không có __init__.py nên không import chéo
giữa các file test).
"""

from __future__ import annotations

import ast
import inspect
import types
from itertools import pairwise

import numpy as np
import pandas as pd
import pytest
from wisdom_engine.des.src import configuration, indicator
from wisdom_engine.des.src.rules import shift, trend_tf
from wisdom_engine.des.src.rules.alternating import apply_alternating_filter
from wisdom_engine.des.src.rules.trend_tf import (
    apply_signal_filters,
    apply_trend_filter,
    combine_trend_bias,
)
from wisdom_engine.des.src.strategies.combo import detect_combo_signals
from wisdom_engine.des.src.strategies.ma_cross import detect_ma_cross_signals
from wisdom_engine.des.src.strategies.macd_break import detect_macd_break_signals
from wisdom_engine.des.src.trend_methods import breakout, knn, tstat, zigzag

TREND_PARAMS = {"TREND_METHODS": ("knn",), "TREND_TF": "H4"}


def make_bars(n: int, *, freq: str = "h", phase: float = 0.0) -> pd.DataFrame:
    """Dao động nhiều chu kỳ quanh một mức cố định, không random: sinh được cả BUY lẫn SELL."""
    i = np.arange(n, dtype=float)
    base = (
        1000.0
        + 30.0 * np.sin(i / 9.0 + phase)
        + 50.0 * np.sin(i / 29.0 + phase)
        + 40.0 * np.sin(i / 71.0 + 1.0 + phase)
        + 6.0 * np.sin(i / 3.1)
    )
    op = base + 0.8 * np.sin(i / 3.3)
    cl = base - 0.8 * np.sin(i / 3.3)
    hi = np.maximum(op, cl) + 2.0 + np.abs(np.sin(i / 5.0))
    lo = np.minimum(op, cl) - 2.0 - np.abs(np.cos(i / 4.0))
    return pd.DataFrame(
        {
            "bartime": pd.date_range("2026-01-01", periods=n, freq=freq).astype("datetime64[us]"),
            "open": op,
            "high": hi,
            "low": lo,
            "close": cl,
            "volume": np.nan,
        }
    )


def _ohlcv(n: int, *, freq_minutes: int = 60, seed: int = 1) -> pd.DataFrame:
    """Adapter cho các test chép từ we_core (make_ohlcv): nến dao động tất định của des, `seed` đổi pha."""
    return make_bars(n, freq=f"{freq_minutes}min", phase=0.35 * seed)


def _fake_block(pattern: list[int]) -> types.SimpleNamespace:
    """Khối giả: hướng lặp theo `pattern`, có một cột riêng, tuân đủ hợp đồng khối."""

    def build(trend_bars, params=None):
        out = trend_bars.copy()
        out["trend_fake_line"] = 1.0
        out["trend_bias_fake"] = [pattern[i % len(pattern)] for i in range(len(out))]
        return out

    return types.SimpleNamespace(NAME="fake", OUTPUT_COLUMNS=("trend_fake_line",), OVERLAYS=(), build=build)


# --- khối KNN ---------------------------------------------------------------------


def test_knn_calc_ai_trend_navigator_returns_direction_columns() -> None:
    bars = make_bars(300)
    result = knn.calc_ai_trend_navigator(
        bars,
        price_value="hl2",
        ma_len=5,
        target_value="Price Action",
        target_len=5,
        number_of_closest_values=3,
        smoothing_period=50,
    )

    assert list(result.columns) == ["ai_knn", "ai_avg", "ai_direction"]
    assert len(result) == len(bars)
    assert set(result["ai_direction"].dropna().unique()) <= {-1, 0, 1}


def test_knn_technical_params_are_validated_and_do_not_need_trend_tf() -> None:
    assert knn.knn_trend_indicator_params(None) == knn.KNN_TREND_DEFAULT_PARAMS
    assert knn.knn_trend_indicator_params({"AI_K": "5"})["AI_K"] == 5
    with pytest.raises(ValueError, match="AI_K must be a positive integer"):
        knn.knn_trend_indicator_params({"AI_K": 0})


def test_knn_build_adds_own_columns_and_bias_from_direction_without_touching_input() -> None:
    bars = make_bars(300, freq="4h")
    before = bars.copy()

    out = knn.build(bars, TREND_PARAMS)

    pd.testing.assert_frame_equal(bars, before)  # không sửa input tại chỗ
    assert list(out.columns) == [*before.columns, *knn.OUTPUT_COLUMNS, knn.BIAS_COLUMN]
    expected = out["trend_ai_direction"].map({1: 1, -1: -1}).fillna(0).astype("int64")
    assert out[knn.BIAS_COLUMN].dtype == np.int64
    assert out[knn.BIAS_COLUMN].tolist() == expected.tolist()
    assert set(out[knn.BIAS_COLUMN].unique()) == {-1, 0, 1}


def test_knn_block_declares_the_block_contract() -> None:
    assert knn.NAME == "knn"
    assert knn.BIAS_COLUMN == trend_tf.bias_column("knn") == "trend_bias_knn"
    assert {line["column"] for line in knn.OVERLAYS} <= set(knn.OUTPUT_COLUMNS)
    for line in knn.OVERLAYS:
        assert set(line) == {"column", "label", "color", "colored_by_bias"}


# --- sổ đăng ký ---------------------------------------------------------------------


def test_methods_lists_every_block_and_every_block_meets_the_contract() -> None:
    assert trend_tf.method_names() == ("knn", "breakout", "zigzag", "tstat")
    assert trend_tf.get_method("knn") is knn
    assert trend_tf.get_method("breakout") is breakout
    assert trend_tf.get_method("tstat") is tstat
    assert trend_tf.get_method("zigzag") is zigzag
    trend_tf._check_contract()  # không raise


def test_contract_check_rejects_an_incomplete_block(monkeypatch) -> None:
    monkeypatch.setitem(trend_tf.METHODS, "bad", types.SimpleNamespace(NAME="bad"))

    with pytest.raises(ValueError, match="violates the block contract"):
        trend_tf._check_contract()


def test_parse_methods_accepts_list_tuple_and_comma_string() -> None:
    assert trend_tf.parse_methods(["knn"]) == ("knn",)
    assert trend_tf.parse_methods(("KNN",)) == ("knn",)
    assert trend_tf.parse_methods(" Knn ") == ("knn",)


def test_parse_methods_rejects_unknown_empty_duplicate_and_wrong_type() -> None:
    with pytest.raises(ValueError, match=r"unknown trend method\(s\): ema \(available: knn, breakout, zigzag, tstat\)"):
        trend_tf.parse_methods("knn,ema")
    with pytest.raises(ValueError, match="at least one trend method"):
        trend_tf.parse_methods([])
    with pytest.raises(ValueError, match="at least one trend method"):
        trend_tf.parse_methods("")
    with pytest.raises(ValueError, match="same trend method more than once: knn"):
        trend_tf.parse_methods("knn,knn")
    with pytest.raises(TypeError, match="must be a list"):
        trend_tf.parse_methods(None)


def test_parse_methods_error_names_the_config_path() -> None:
    with pytest.raises(ValueError, match=r"strategies\.combo\.trend_methods has unknown"):
        trend_tf.parse_methods(["nope"], label="strategies.combo.trend_methods")


def test_parse_methods_knows_a_registered_extra_block(monkeypatch) -> None:
    monkeypatch.setitem(trend_tf.METHODS, "fake", _fake_block([1]))

    assert trend_tf.parse_methods("knn,fake") == ("knn", "fake")
    assert trend_tf.method_names() == ("knn", "breakout", "zigzag", "tstat", "fake")


# --- cấu hình des -----------------------------------------------------------------------


def test_trend_methods_come_from_des_config_and_reject_unregistered_override() -> None:
    # đồng bộ với we_core (23/10): knn là khối mặc định của cả ba chiến lược; breakout và zigzag là hai nguồn riêng của des
    for key in ("combo", "ma_cross", "macd_break"):
        assert configuration._DEFAULT_PARAMS_BY_KEY[key]["TREND_METHODS"] == ("knn",)
    assert configuration.normalize_combo_params({}, "US30", "H1")["TREND_METHODS"] == ("knn",)
    assert configuration.normalize_combo_params({"TREND_METHODS": " KNN "}, "US30", "H1")["TREND_METHODS"] == ("knn",)
    with pytest.raises(ValueError, match=r"unknown trend method\(s\): ema"):
        configuration.normalize_combo_params({"TREND_METHODS": "ema"}, "US30", "H1")


# --- dựng khung trend -----------------------------------------------------------------


def test_build_trend_frame_for_knn_alone_keeps_the_legacy_columns() -> None:
    bars = make_bars(300, freq="4h")

    out = trend_tf.build_trend_frame(bars, TREND_PARAMS)

    assert list(out.columns)[-6:] == [
        "trend_ai_knn",
        "trend_ai_avg",
        "trend_ai_direction",
        "trend_bias_knn",
        "trend_bias",
        "trend_close_time",
    ]
    assert out["trend_bias"].tolist() == out["trend_bias_knn"].tolist()
    assert (out["trend_close_time"] - out["bartime"]).unique().tolist() == [pd.Timedelta(minutes=240)]


def test_build_trend_frame_requires_trend_tf() -> None:
    with pytest.raises(ValueError, match="TREND_TF is required"):
        trend_tf.build_trend_frame(make_bars(50, freq="4h"), {"TREND_METHODS": ("knn",)})


def test_build_trend_frame_rejects_a_block_that_forgets_its_bias_column(monkeypatch) -> None:
    block = _fake_block([1])
    block.build = lambda trend_bars, params=None: trend_bars.copy()
    monkeypatch.setitem(trend_tf.METHODS, "fake", block)

    with pytest.raises(ValueError, match="did not produce the 'trend_bias_fake' column"):
        trend_tf.build_trend_frame(make_bars(50, freq="4h"), {"TREND_METHODS": ("fake",), "TREND_TF": "H4"})


def test_two_blocks_combine_by_consensus_and_disagreement_gives_zero(monkeypatch) -> None:
    monkeypatch.setitem(trend_tf.METHODS, "fake", _fake_block([1, -1, 0]))
    bars = make_bars(300, freq="4h")

    out = trend_tf.build_trend_frame(bars, {"TREND_METHODS": ("knn", "fake"), "TREND_TF": "H4"})

    knn_bias, fake_bias = out["trend_bias_knn"], out["trend_bias_fake"]
    expected = np.where((knn_bias == 1) & (fake_bias == 1), 1, np.where((knn_bias == -1) & (fake_bias == -1), -1, 0))
    assert out["trend_bias"].tolist() == expected.tolist()
    assert (out["trend_bias"] != 0).any()  # có chỗ đồng thuận
    assert (out["trend_bias"] != knn_bias).any()  # và có chỗ bất đồng bị về 0
    assert (out["trend_bias"].abs() <= knn_bias.abs()).all()  # ghép không bao giờ thêm hướng KNN không có


def test_enabling_a_second_block_never_adds_signals_and_filters_by_the_combined_bias(monkeypatch) -> None:
    monkeypatch.setitem(trend_tf.METHODS, "fake", _fake_block([1, -1, 0]))
    entry, trend = make_bars(600), make_bars(600, freq="4h", phase=0.7)
    kwargs = {"symbol": "US30", "entry_tf": "H1", "entry_bars": entry, "trend_tf": "H4", "trend_bars": trend}

    solo = configuration.run_strategy_with_trend_reference("macd_break", **kwargs)
    both = configuration.run_strategy_with_trend_reference("macd_break", overrides={"TREND_METHODS": "knn,fake"}, **kwargs)

    assert (both["raw_signal"] == solo["raw_signal"]).all()  # khối xác nhận không đổi tín hiệu gốc
    assert ((both["signal"] != 0) <= (solo["signal"] != 0)).all()  # chỉ bớt, không thêm
    assert int((both["signal"] != 0).sum()) < int((solo["signal"] != 0).sum())
    assert (both["trend_bias"].fillna(0) != solo["trend_bias"].fillna(0)).any()  # khối thứ hai thật sự đổi hướng ghép
    kept = both[both["signal"] != 0]
    assert (kept["signal"].astype(int) == kept["trend_bias"].astype(int)).all()
    assert not [col for col in both.columns if col.startswith("trend_bias_")]  # cột hướng riêng không sang khung vào lệnh
    assert "trend_fake_line" in both.columns  # cột riêng của khối thì có


def test_a_second_block_that_never_confirms_vetoes_every_signal(monkeypatch) -> None:
    monkeypatch.setitem(trend_tf.METHODS, "fake", _fake_block([0]))
    entry, trend = make_bars(600), make_bars(600, freq="4h", phase=0.7)
    kwargs = {"symbol": "US30", "entry_tf": "H1", "entry_bars": entry, "trend_tf": "H4", "trend_bars": trend}

    solo = configuration.run_strategy_with_trend_reference("macd_break", **kwargs)
    both = configuration.run_strategy_with_trend_reference("macd_break", overrides={"TREND_METHODS": "knn,fake"}, **kwargs)

    assert int((solo["signal"] != 0).sum()) >= 1  # có gì đó để bị bác bỏ, test mới có nghĩa
    assert (both["signal"] == 0).all()
    assert (both["raw_signal"] == solo["raw_signal"]).all()  # tín hiệu gốc vẫn nguyên, chỉ không được xác nhận


# --- ghép hướng và bộ lọc -------------------------------------------------------------------


def test_combine_trend_bias_single_column_is_identity_as_int64() -> None:
    df = pd.DataFrame({"a": [1, -1, 0, None]})

    out = combine_trend_bias(df, ["a"])

    assert out.dtype == np.int64
    assert out.tolist() == [1, -1, 0, 0]  # NaN coi là 0


def test_combine_trend_bias_requires_all_columns_to_agree() -> None:
    df = pd.DataFrame({"a": [1, 1, -1, -1, 0, 1], "b": [1, -1, -1, 1, 0, None], "c": [1, 1, -1, -1, 1, 1]})

    out = combine_trend_bias(df, ["a", "b", "c"])

    assert out.tolist() == [1, 0, -1, 0, 0, 0]
    assert out.dtype == np.int64


def test_combine_trend_bias_needs_at_least_one_column() -> None:
    with pytest.raises(ValueError, match="at least one bias column"):
        combine_trend_bias(pd.DataFrame({"a": [1]}), [])


def test_apply_trend_filter_reads_only_the_combined_bias() -> None:
    df = pd.DataFrame({"raw_signal": [1, 1, -1, -1], "trend_bias": [1, 0, -1, 1], "trend_bias_knn": [1, 1, 1, 1]})

    out = apply_trend_filter(df, {"TREND_FILTER_ENABLED": True})

    assert out["signal"].tolist() == [1, 0, -1, 0]

# =============================================================================================
# Từ đây: khối BREAKOUT, chính sách POLICY và bộ điều phối apply_signal_filters — chép từ
# we_core/tests/test_trend_methods.py (đồng bộ 2026-10-23); dữ liệu nến là bộ tất định của des.
# =============================================================================================


# --- khối BREAKOUT --------------------------------------------------------------------


def _breakout_bars(seed: int = 1, n: int = 700) -> pd.DataFrame:
    return _ohlcv(n, freq_minutes=240, seed=seed)


def test_breakout_block_declares_the_block_contract():
    assert breakout.NAME == "breakout"
    for block in (knn, breakout, tstat):  # khối chỉ tính hướng: chính sách lọc nằm ở rules/trend_tf.POLICY, không ở khối
        assert not hasattr(block, "SAME_TIMEFRAME") and not hasattr(block, "FIRST_ALIGNED_PER_RUN")
    assert breakout.BIAS_COLUMN == trend_tf.bias_column("breakout") == "trend_bias_breakout"
    assert {line["column"] for line in breakout.OVERLAYS} <= set(breakout.OUTPUT_COLUMNS)
    for line in breakout.OVERLAYS:
        assert set(line) == {"column", "label", "color", "colored_by_bias"}


def test_breakout_bias_keeps_the_latest_break_direction_until_an_opposite_break(monkeypatch):
    n = 10
    up = np.zeros(n, dtype=bool)
    down = np.zeros(n, dtype=bool)
    up[[1, 3, 8]] = True  # break lên ở nến 1 (lặp lại ở 3), break xuống ở nến 5, break lên lại ở nến 8
    down[5] = True
    bars = pd.DataFrame({"bartime": pd.date_range("2026-01-05", periods=n, freq="240min"), "close": np.arange(n, dtype=float)})
    monkeypatch.setattr(breakout, "_detect_breaks", lambda frame: (up, down, pd.Series(np.zeros(n)), pd.Series(np.zeros(n))))

    out = breakout.build(bars)

    # nến 0 trung tính (chưa có break); break cùng chiều lặp lại không đổi gì; chỉ break ngược mới đổi hướng
    assert out[breakout.BIAS_COLUMN].tolist() == [0, 1, 1, 1, 1, -1, -1, -1, 1, 1]


def test_breakout_build_adds_own_columns_and_bias_without_touching_input():
    bars = _breakout_bars()
    before = bars.copy()

    out = breakout.build(bars, {"TREND_METHODS": ("breakout",), "TREND_TF": "H4"})

    pd.testing.assert_frame_equal(bars, before)  # không sửa input tại chỗ
    assert list(out.columns) == [*before.columns, *breakout.OUTPUT_COLUMNS, breakout.BIAS_COLUMN]
    assert out[breakout.BIAS_COLUMN].dtype == np.int64
    assert set(out[breakout.BIAS_COLUMN].unique()) == {-1, 0, 1}
    assert out["trend_breakout_high"].iloc[: breakout.LOOKBACK_BARS].isna().all()  # chưa đủ 100 nến trước thì chưa có đường
    assert (out[breakout.BIAS_COLUMN].iloc[: breakout.LOOKBACK_BARS] == 0).all()  # và chưa thể có break -> trung tính


def _macd_break_reference(bars: pd.DataFrame, *, alternating: bool):
    """Bản tham chiếu: chỉ báo và tín hiệu lấy từ CHÍNH chiến lược macd_break (bật/tắt lọc xen kẽ)."""
    params = {
        "LOOKBACK_BARS": breakout.LOOKBACK_BARS,
        "MACD_FAST": breakout.MACD_FAST,
        "MACD_SLOW": breakout.MACD_SLOW,
        "MACD_SIGNAL": breakout.MACD_SIGNAL,
        "ATR_PERIOD": 5,
    }
    ind = indicator.add_macd_break_indicators(bars, params)
    signal = detect_macd_break_signals(ind, params={"ALTERNATING_FILTER_ENABLED": alternating})["raw_signal"]
    return ind, signal


def _latest_direction(signal: pd.Series) -> pd.Series:
    return signal.replace(0, np.nan).ffill().fillna(0).astype(int)


@pytest.mark.parametrize("seed", [1, 2, 4, 5, 6, 8])
def test_breakout_bias_equals_the_direction_of_the_latest_macd_break_signal(seed):
    bars = _breakout_bars(seed)
    ind, every_setup = _macd_break_reference(bars, alternating=False)
    _, first_of_run = _macd_break_reference(bars, alternating=True)  # tín hiệu BUY/SELL thật của macd_break (DB0)

    up, down, _, _ = breakout._detect_breaks(bars)
    out = breakout.build(bars)
    bias = out[breakout.BIAS_COLUMN]
    changed = bias.diff().fillna(0).ne(0)

    assert up.tolist() == every_setup.eq(1).tolist()  # break lên/xuống y hệt setup của chiến lược macd_break
    assert down.tolist() == every_setup.eq(-1).tolist()
    assert bias.tolist() == _latest_direction(every_setup).tolist()
    assert bias.tolist() == _latest_direction(first_of_run).tolist()  # bộ lọc xen kẽ không đổi hướng
    pd.testing.assert_series_equal(out["trend_breakout_high"], ind["prior_high_close"], check_names=False)
    pd.testing.assert_series_equal(out["trend_breakout_low"], ind["prior_low_close"], check_names=False)
    # hướng đổi ĐÚNG tại các tín hiệu macd_break (mũi tên BUY/SELL trên chart), và đổi sang đúng chiều tín hiệu
    assert changed.tolist() == first_of_run.ne(0).tolist()
    assert (bias[changed] == first_of_run[changed]).all()
    # dữ liệu mẫu phải đủ phong phú để phép so không vô nghĩa: có cả break lên lẫn break xuống và ít nhất hai lần đảo chiều thật
    assert every_setup.eq(1).sum() >= 2 and every_setup.eq(-1).sum() >= 2 and (bias.diff().abs() == 2).sum() >= 2


@pytest.mark.parametrize(
    ("strategy", "entry_minutes", "entry_tf", "knn_trend_tf"),
    [("combo", 60, "H1", "H4"), ("ma_cross", 30, "M30", "H1")],
)
def test_breakout_runs_on_the_entry_timeframe_through_the_pipeline_and_only_filters(strategy, entry_minutes, entry_tf, knn_trend_tf):
    entry = _ohlcv(900, freq_minutes=entry_minutes)
    reference = _ohlcv(900, freq_minutes=60, seed=7)
    base = {"symbol": "US30", "entry_tf": entry_tf, "entry_bars": entry}

    with_knn = configuration.run_strategy_with_trend_reference(
        strategy, trend_tf=knn_trend_tf, trend_bars=reference, overrides={"TREND_METHODS": "knn"}, **base
    )
    # breakout: khung trend LÀ khung vào lệnh -> trend_bars chính là nến vào lệnh, trend_tf = entry_tf
    run = configuration.run_strategy_with_trend_reference(
        strategy, trend_tf=entry_tf, trend_bars=entry, overrides={"TREND_METHODS": "breakout"}, **base
    )

    assert (run["raw_signal"] == with_knn["raw_signal"]).all()  # đổi khối xác nhận không đổi tín hiệu gốc
    kept = run[run["signal"] != 0]
    assert (kept["signal"] == kept["trend_bias"]).all()  # mọi tín hiệu giữ lại đều cùng chiều trend
    old_rule = (run["raw_signal"] != 0) & (run["raw_signal"] == run["trend_bias"].fillna(0))  # nến đầu đợt đã cùng chiều trend
    assert (run.loc[old_rule, "signal"] == run.loc[old_rule, "raw_signal"]).all()  # cách lọc cũ vẫn được giữ nguyên
    assert len(kept) >= 1 and ((run["raw_signal"] != 0) & (run["signal"] == 0)).any()  # có giữ và có lọc
    # knn KHÔNG dời tín hiệu (rules/trend_tf.POLICY): signal đúng bằng cách lọc cũ, chỉ nến raw_signal đã cùng chiều trend
    old_rule_knn = (with_knn["raw_signal"] != 0) & (with_knn["raw_signal"] == with_knn["trend_bias"].fillna(0))
    assert with_knn["signal"].tolist() == with_knn["raw_signal"].where(old_rule_knn, 0).tolist()
    if strategy == "combo":  # setup là sự kiện: đợt nào có nến trend cùng chiều thì có ĐÚNG một tín hiệu (không còn đợt trend đổi mà không có tín hiệu)
        raw, trend = run["raw_signal"].to_numpy(), run["trend_bias"].fillna(0).to_numpy().astype(int)
        starts = np.flatnonzero(raw != 0)
        counts = [(int((run["signal"].to_numpy()[a:b] != 0).sum()), bool((trend[a:b] == raw[a]).any())) for a, b in zip(starts, np.append(starts[1:], len(raw)), strict=True)]
        assert all(fired == int(aligned) for fired, aligned in counts) and any(aligned for _, aligned in counts)
    assert (run["trend_bias"].fillna(0) != with_knn["trend_bias"].fillna(0)).any()  # khối mới thật sự đổi hướng ghép
    assert "trend_breakout_high" in run.columns and "trend_ai_knn" not in run.columns  # chỉ khối đang bật mới có cột
    assert not [col for col in run.columns if col.startswith("trend_bias_")]  # cột hướng riêng không sang khung vào lệnh
    # khung bằng nhau: nến t nhìn trend của nến t-1 (đã đóng trước khi nến t mở), không nhìn chính nó
    own = breakout.build(entry)[breakout.BIAS_COLUMN].tolist()
    assert pd.isna(run["trend_bias"].iloc[0])
    assert run["trend_bias"].iloc[1:].astype(int).tolist() == own[:-1]


def test_breakout_always_uses_the_entry_timeframe():
    for strategy, entry_tf in (("combo", "H2"), ("ma_cross", "M30"), ("macd_break", "M10")):
        params = configuration.get_strategy(strategy).normalize_params({"TREND_METHODS": "breakout"}, "US30", entry_tf)
        assert params["TREND_TF"] == entry_tf  # kể cả M10/M30: H1–H4 chỉ áp cho khối cần khung tham chiếu (knn)
    ma_cross = configuration.get_strategy("ma_cross")
    assert ma_cross.normalize_params(None, "US30", "M30")["TREND_TF"] in ("H1", "H2", "H3", "H4")  # mặc định của config là knn (như we_core): khung tham chiếu tra từ bảng, không phải khung của tín hiệu
    assert ma_cross.normalize_params({"TREND_METHODS": "knn"}, "US30", "M30")["TREND_TF"] in ("H1", "H2", "H3", "H4")  # knn vẫn tra bảng

    combo = configuration.get_strategy("combo")
    with pytest.raises(ValueError, match="always use the entry timeframe H1; TREND_TF H4 is not allowed"):
        combo.normalize_params({"TREND_METHODS": "breakout", "TREND_TF": "H4"}, "US30", "H1")
    assert combo.normalize_params({"TREND_METHODS": "breakout", "TREND_TF": "h1"}, "US30", "H1")["TREND_TF"] == "H1"
    with pytest.raises(ValueError, match="cannot be combined"):  # mọi khối đang bật dùng chung một khung trend
        combo.normalize_params({"TREND_METHODS": "knn,breakout"}, "US30", "H1")

    entry, trend = _ohlcv(300), _ohlcv(300, seed=7)
    kwargs = {"symbol": "US30", "entry_bars": entry, "overrides": {"TREND_METHODS": "breakout"}}
    with pytest.raises(ValueError, match="always use the entry timeframe"):  # đường ống cũng chặn khung tham chiếu khác
        configuration.run_strategy_with_trend_reference("combo", entry_tf="H1", trend_tf="H4", trend_bars=trend, **kwargs)
    assert not configuration.run_strategy_with_trend_reference("ma_cross", entry_tf="M30", trend_tf="M30", trend_bars=entry, **kwargs).empty


def test_knn_keeps_using_the_reference_timeframe_from_the_table_and_only_h1_to_h4():
    """Chỉ breakout tính trên chính khung của tín hiệu; knn giữ nguyên: khung tham chiếu tra theo trend_tf_by_symbol, chỉ H1–H4."""
    tables = {
        "combo": configuration.COMBO_TREND_TF_BY_SYMBOL,
        "ma_cross": configuration.MA_CROSS_TREND_TF_BY_SYMBOL,
        "macd_break": configuration.MACD_BREAK_TREND_TF_BY_SYMBOL,
    }
    checked = 0
    for strategy, table in tables.items():
        spec = configuration.get_strategy(strategy)
        for symbol, cells in table.items():
            for entry_tf, expected in cells.items():
                default_tf = entry_tf if configuration._DEFAULT_PARAMS_BY_KEY[strategy]["TREND_METHODS"] == ("breakout",) else expected  # mặc định đặt riêng theo từng chiến lược
                assert spec.normalize_params(None, symbol, entry_tf)["TREND_TF"] == default_tf
                assert spec.normalize_params({"TREND_METHODS": "knn"}, symbol, entry_tf)["TREND_TF"] == expected
                checked += 1
    assert checked == len(configuration.OPERATING_SYMBOLS) * (4 + 4 + 8)  # mọi ô của bảng (mọi symbol vận hành × 16 khung vào lệnh của ba chiến lược)
    ma_cross = configuration.get_strategy("ma_cross")
    assert ma_cross.normalize_params({"TREND_METHODS": "knn", "TREND_TF": "H3"}, "US30", "M30")["TREND_TF"] == "H3"  # đè tường minh vẫn thắng
    with pytest.raises(ValueError, match="TREND_TF must be one of: H1, H2, H3, H4"):
        ma_cross.normalize_params({"TREND_METHODS": "knn", "TREND_TF": "M30"}, "US30", "M30")  # knn không dùng khung ngoài H1–H4


def test_trend_tf_tells_which_methods_use_the_entry_timeframe():
    assert trend_tf.is_same_timeframe("breakout") and not trend_tf.is_same_timeframe("knn")
    assert trend_tf.uses_entry_timeframe(("breakout",)) is True
    assert trend_tf.uses_entry_timeframe(("knn",)) is False
    assert trend_tf.uses_entry_timeframe(()) is False
    with pytest.raises(ValueError, match=r"breakout \(entry timeframe\) and knn \(reference timeframe\) cannot be combined"):
        trend_tf.uses_entry_timeframe(("knn", "breakout"))


# --- tín hiệu đầu tiên sau khi trend xác nhận (FIRST_ALIGNED_PER_RUN, chỉ breakout) -----------------------------

# Kịch bản dựng tay: hướng setup (±1, 0 = không có setup) và hướng trend mà nến vào lệnh nhìn thấy. Đợt SELL mở ở nến 2 khi
# trend còn UP (nến 1–3) và trend sang DOWN ở nến 4 (giống ví dụ US30 M10 04/04/2024); đợt BUY mở ở nến 9 khi trend còn DOWN,
# trend sang UP ở nến 10; đợt SELL cuối mở ở nến 12 khi trend đã DOWN sẵn.
SETUP_DIRECTIONS = [0, 0, -1, -1, -1, -1, 0, -1, 0, 1, 1, 0, -1, -1, 0]
TREND_DIRECTIONS = [0, 1, 1, 1, -1, -1, -1, -1, -1, -1, 1, 1, -1, -1, -1]
RAW_AT = {2: -1, 9: 1, 12: -1}  # DB0 (bộ lọc xen kẽ): nến đầu mỗi đợt
FIRST_ALIGNED_AT = {4: -1, 10: 1, 12: -1}  # mỗi đợt: nến setup đầu tiên mà trend đã cùng chiều
OLD_RULE_AT = {12: -1}  # chỉ xét nến đầu đợt: hai đợt đầu lệch trend ở nến đó nên mất cả đợt


def _at(series: pd.Series) -> dict[int, int]:
    return {int(i): int(v) for i, v in series.items() if v != 0}


def _setup_frame(strategy: str, directions: list[int], trend: list[int]) -> pd.DataFrame:
    """Khung dựng tay: mỗi dòng đúng điều kiện setup BUY (+1) / SELL (-1) / không có (0) của chiến lược."""
    d = np.array(directions, dtype=float)
    frame = pd.DataFrame({"bartime": pd.date_range("2026-01-05", periods=len(d), freq="30min"), "trend_bias": trend})
    frame["atr"] = 1.0
    frame["macd_h"] = d  # dấu MACD histogram cùng chiều setup
    if strategy == "ma_cross":
        frame["fast_ma"], frame["slow_ma"] = 100.0 + d, 100.0
    elif strategy == "macd_break":
        frame["close"], frame["prior_high_close"], frame["prior_low_close"] = 100.0 + 10.0 * d, 105.0, 95.0
    else:  # combo: nến tăng/giảm vừa cắt MA
        frame["open"], frame["prev_close"] = 100.0, 100.0
        frame["close"], frame["ma"] = 100.0 + 2.0 * d, 100.0 + d
    return frame


_DETECT = {"combo": detect_combo_signals, "ma_cross": detect_ma_cross_signals, "macd_break": detect_macd_break_signals}


def _old_rule(monkeypatch) -> tuple[str, ...]:
    """Đăng ký một khối KHÔNG có ô trong POLICY (chính sách mặc định = cách lọc cũ: chỉ xét nến đầu của đợt); trả về TREND_METHODS của nó."""
    monkeypatch.setitem(trend_tf.METHODS, "fake", _fake_block([1]))
    return ("fake",)


def test_policy_table_holds_the_filter_policy_of_every_block(monkeypatch):
    assert trend_tf.policy_of("knn") == trend_tf.MethodPolicy(same_timeframe=False, first_aligned_per_run=False)
    assert trend_tf.policy_of("breakout") == trend_tf.MethodPolicy(same_timeframe=True, first_aligned_per_run=True)
    zigzag_policy = trend_tf.policy_of("zigzag")  # khung tham chiếu riêng như knn, không dời tín hiệu; warm-up nến trend dài hơn (xem test warm-up)
    assert (zigzag_policy.same_timeframe, zigzag_policy.first_aligned_per_run) == (False, False)
    assert trend_tf.is_same_timeframe("breakout") and not trend_tf.is_same_timeframe("knn")
    assert trend_tf.uses_first_aligned_per_run(("breakout",)) is True
    assert trend_tf.uses_first_aligned_per_run(("knn",)) is False  # knn không dời tín hiệu (bật 2026-10-23 rồi tắt lại cùng ngày)
    assert trend_tf.uses_first_aligned_per_run(()) is False
    with pytest.raises(KeyError):
        trend_tf.policy_of("nope")  # tên lạ báo lỗi, không im lặng trả chính sách mặc định
    # khối không có ô trong POLICY dùng chính sách mặc định (cả hai False) -> cách lọc cũ
    monkeypatch.setitem(trend_tf.METHODS, "fake", _fake_block([1]))
    assert trend_tf.policy_of("fake") == trend_tf.MethodPolicy() == trend_tf.MethodPolicy(False, False)
    assert not trend_tf.is_same_timeframe("fake") and not trend_tf.uses_first_aligned_per_run(("fake",))
    # ghi một ô cho khối đó là có hiệu lực ngay: chính sách không viết riêng cho khối nào, chỉ cần MỘT khối có là đủ
    monkeypatch.setitem(trend_tf.POLICY, "fake", trend_tf.MethodPolicy(first_aligned_per_run=True))
    assert trend_tf.uses_first_aligned_per_run(("fake",)) is True
    assert trend_tf.uses_first_aligned_per_run(("knn", "fake")) is True
    monkeypatch.setitem(trend_tf.POLICY, "fake", trend_tf.MethodPolicy(first_aligned_per_run=False))
    assert trend_tf.uses_first_aligned_per_run(("knn", "fake")) is False
    # bật lại cơ chế dời cho knn chỉ cần đổi đúng ô của knn trong bảng
    monkeypatch.setitem(trend_tf.POLICY, "knn", trend_tf.MethodPolicy(first_aligned_per_run=True))
    assert trend_tf.uses_first_aligned_per_run(("knn", "fake")) is True


def test_policy_table_holds_the_trend_warmup_of_every_block(monkeypatch):
    assert trend_tf.MethodPolicy().trend_warmup_bars == 0                       # mặc định: dùng warm-up chung của hệ
    assert trend_tf.policy_of("knn").trend_warmup_bars == 0 and trend_tf.policy_of("breakout").trend_warmup_bars == 0
    zigzag_warmup = trend_tf.policy_of("zigzag").trend_warmup_bars
    assert zigzag_warmup > int(configuration.CONFIG["redis"]["input"]["snapshot_bars"])  # máy trạng thái nhớ lâu hơn cửa sổ nến của des
    assert trend_tf.max_trend_warmup_bars(()) == 0
    assert trend_tf.max_trend_warmup_bars(("knn",)) == 0 and trend_tf.max_trend_warmup_bars(("breakout",)) == 0
    assert trend_tf.max_trend_warmup_bars(("zigzag",)) == zigzag_warmup
    assert trend_tf.max_trend_warmup_bars(("knn", "zigzag")) == zigzag_warmup   # nhiều khối: lấy khối đòi nhiều nhất
    monkeypatch.setitem(trend_tf.METHODS, "fake", _fake_block([1]))
    assert trend_tf.max_trend_warmup_bars(("fake",)) == 0                        # khối không có ô trong POLICY: 0
    monkeypatch.setitem(trend_tf.POLICY, "fake", trend_tf.MethodPolicy(trend_warmup_bars=zigzag_warmup + 7))
    assert trend_tf.max_trend_warmup_bars(("knn", "fake", "zigzag")) == zigzag_warmup + 7
    with pytest.raises(KeyError):
        trend_tf.max_trend_warmup_bars(("nope",))                                # tên lạ báo lỗi, không im lặng trả 0


@pytest.mark.parametrize("strategy", ["combo", "ma_cross", "macd_break"])
def test_first_aligned_per_run_keeps_the_first_signal_after_the_trend_confirms_for_every_strategy(strategy, monkeypatch):
    method = "breakout"  # khối có chính sách first_aligned_per_run; knn không dời tín hiệu (test_knn_filters_only_the_first_bar...)
    old_rule = _old_rule(monkeypatch)
    frame = _setup_frame(strategy, SETUP_DIRECTIONS, TREND_DIRECTIONS)
    base = {"TREND_FILTER_ENABLED": True, "ALTERNATING_FILTER_ENABLED": True}
    detect = _DETECT[strategy]

    out = detect(frame, params={**base, "TREND_METHODS": (method,)})
    plain = detect(frame, params={**base, "TREND_METHODS": old_rule})

    assert _at(out["raw_signal"]) == RAW_AT == _at(plain["raw_signal"])  # DB0 không đổi: nến đầu mỗi đợt
    assert _at(out["signal"]) == FIRST_ALIGNED_AT
    assert _at(plain["signal"]) == OLD_RULE_AT  # cách lọc cũ: nến đầu đợt lệch trend thì mất cả đợt
    # trend_filter_status mô tả từng nến: nến mở đợt lệch trend = filtered, nến bắn dời = aligned dù raw_signal = 0 ở nến đó
    assert out["trend_filter_status"].tolist()[2:5] == ["filtered", "no_raw_signal", "aligned"]
    assert out["trend_filter_status"].iloc[9] == "filtered" and out["trend_filter_status"].iloc[10] == "aligned"
    assert out["signal_reason"].iloc[4].endswith("; aligned with trend reference") and out["signal_reason"].iloc[3] == ""
    assert out["signal_reason"].iloc[12] == plain["signal_reason"].iloc[12]  # nến đầu đợt đã cùng chiều trend: lý do như thường
    if strategy == "combo":  # nến cắt MA nằm sớm hơn trong đợt nên lý do của tín hiệu bắn dời ghi đúng như vậy
        assert out["signal_reason"].iloc[4] == "bearish MA cross earlier in the run, held until the trend aligned; aligned with trend reference"
    else:  # setup còn đúng ở chính nến đó nên lý do như thường
        assert out["signal_reason"].iloc[4] == plain["signal_reason"].iloc[12]
    # trend tắt: signal == raw_signal, kể cả khi khối đang chọn có chính sách dời tín hiệu
    off = detect(frame, params={**base, "TREND_FILTER_ENABLED": False, "TREND_METHODS": (method,)})
    assert off["signal"].tolist() == off["raw_signal"].tolist() and set(off["trend_filter_status"]) == {"disabled"}
    # cờ lọc xen kẽ tắt: mỗi nến setup là một đợt riêng nên cả hai cách lọc cho cùng kết quả
    no_alt = {**base, "ALTERNATING_FILTER_ENABLED": False}
    assert _at(detect(frame, params={**no_alt, "TREND_METHODS": (method,)})["signal"]) == {4: -1, 5: -1, 7: -1, 10: 1, 12: -1, 13: -1}
    assert detect(frame, params={**no_alt, "TREND_METHODS": (method,)})["signal"].tolist() == detect(frame, params={**no_alt, "TREND_METHODS": old_rule})["signal"].tolist()


# Setup là SỰ KIỆN (một nến) rồi không còn nến setup nào: SELL ở nến 1 khi trend còn UP, trend sang DOWN ở nến 3; BUY ở nến 7 khi
# trend còn DOWN, trend sang UP ở nến 8.
EVENT_SETUPS = [0, -1, 0, 0, 0, 0, 0, 1, 0, 0]
EVENT_TREND = [1, 1, 1, -1, -1, -1, -1, -1, 1, 1]
EVENT_PARAMS = {"TREND_FILTER_ENABLED": True, "ALTERNATING_FILTER_ENABLED": True}


def test_combo_holds_the_filtered_signal_until_the_trend_turns_while_state_setups_need_a_setup_bar(monkeypatch):
    method = "breakout"  # khối có chính sách first_aligned_per_run (knn không dời tín hiệu)
    old_rule = _old_rule(monkeypatch)
    for strategy in ("combo", "ma_cross", "macd_break"):
        frame = _setup_frame(strategy, EVENT_SETUPS, EVENT_TREND)
        out = _DETECT[strategy](frame, params={**EVENT_PARAMS, "TREND_METHODS": (method,)})
        plain = _DETECT[strategy](frame, params={**EVENT_PARAMS, "TREND_METHODS": old_rule})

        assert _at(out["raw_signal"]) == {1: -1, 7: 1}, strategy
        assert _at(plain["signal"]) == {}, strategy  # cách lọc cũ: nến đầu đợt lệch trend nên mất cả đợt
        # combo: setup là sự kiện nên tín hiệu thô được giữ tới hết đợt và bắn ngay nến trend đổi; ma_cross/macd_break có setup là
        # trạng thái, cần một nến setup SAU khi trend đổi (ở đây không có) nên không bắn
        assert _at(out["signal"]) == ({3: -1, 8: 1} if strategy == "combo" else {}), strategy
    combo = _DETECT["combo"](_setup_frame("combo", EVENT_SETUPS, EVENT_TREND), params={**EVENT_PARAMS, "TREND_METHODS": (method,)})
    assert combo["signal_reason"].iloc[3] == "bearish MA cross earlier in the run, held until the trend aligned; aligned with trend reference"
    assert combo["signal_reason"].iloc[8] == "bullish MA cross earlier in the run, held until the trend aligned; aligned with trend reference"
    assert combo["trend_filter_status"].tolist()[1:5] == ["filtered", "no_raw_signal", "aligned", "no_raw_signal"]


def test_combo_held_signal_respects_the_session_hours():
    frame = _setup_frame("combo", EVENT_SETUPS, EVENT_TREND)  # nến 30 phút từ 00:00: nến 0–1 ở giờ 0, nến 2–3 ở giờ 1, nến 7 lúc 03:30
    base = {**EVENT_PARAMS, "TREND_METHODS": ("breakout",)}

    all_hours = detect_combo_signals(frame, params=base)
    only_hour_0 = detect_combo_signals(frame, params={**base, "SESSION_HOURS_UTC": [0]})

    assert _at(all_hours["signal"]) == {3: -1, 8: 1}
    assert _at(only_hour_0["raw_signal"]) == {1: -1}  # nến 7 nằm ngoài session nên không có setup BUY
    assert _at(only_hour_0["signal"]) == {}  # nến trend đổi (01:30, giờ 1) ngoài session: không bắn dời


def test_apply_trend_filter_needs_the_setup_masks_to_move_the_signal():
    frame = _setup_frame("ma_cross", SETUP_DIRECTIONS, TREND_DIRECTIONS)
    frame["raw_signal"] = [RAW_AT.get(i, 0) for i in range(len(frame))]
    params = {"TREND_FILTER_ENABLED": True, "TREND_METHODS": ("breakout",)}
    buy, sell = pd.Series(np.array(SETUP_DIRECTIONS) == 1), pd.Series(np.array(SETUP_DIRECTIONS) == -1)

    without_setups = apply_trend_filter(frame, params)  # chiến lược không truyền setup: luôn lọc theo cách cũ
    with_setups = apply_trend_filter(frame, params, buy_setup=buy, sell_setup=sell)

    assert _at(without_setups["signal"]) == OLD_RULE_AT
    assert _at(with_setups["signal"]) == FIRST_ALIGNED_AT


def _first_aligned_loop(setups: np.ndarray, bias: np.ndarray, *, alternating: bool) -> list[int]:
    """Bản tham chiếu viết bằng vòng lặp thuần: mỗi đợt bắn một lần, ở nến setup đầu tiên mà trend đã cùng chiều."""
    state, fired, signals = 0, False, []
    for direction, trend in zip(setups.tolist(), bias.tolist(), strict=True):
        signal = 0
        if direction != 0:
            if not alternating or direction != state:
                state, fired = direction, False  # nến đầu của đợt (cờ xen kẽ tắt: mỗi nến setup là một đợt)
            if trend == direction and not fired:
                signal, fired = direction, True
        signals.append(signal)
    return signals


def _random_setups_and_bias(seed: int, n: int = 400) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    setups, bias = np.zeros(n, dtype=int), np.zeros(n, dtype=int)
    direction, trend = 1, 0
    for i in range(n):
        if rng.random() < 0.3:
            trend = int(rng.choice([-1, 0, 1]))  # trend giữ vài chục nến rồi đổi
        if rng.random() < 0.45:
            direction = direction if rng.random() < 0.75 else -direction
            setups[i] = direction
        bias[i] = trend
    return setups, bias


def test_first_aligned_filter_matches_an_independent_loop_and_only_adds_signals(monkeypatch):
    params = {"TREND_FILTER_ENABLED": True, "TREND_METHODS": ("breakout",)}  # khối có chính sách first_aligned_per_run
    old_params = {"TREND_FILTER_ENABLED": True, "TREND_METHODS": _old_rule(monkeypatch)}
    extra_total = 0
    for seed in range(60):
        setups, bias = _random_setups_and_bias(seed)
        buy, sell = pd.Series(setups == 1), pd.Series(setups == -1)
        for alternating in (True, False):
            raw = apply_alternating_filter(buy, sell, {"ALTERNATING_FILTER_ENABLED": alternating})
            frame = pd.DataFrame({"raw_signal": raw, "trend_bias": bias})

            new = apply_trend_filter(frame, params, buy_setup=buy, sell_setup=sell)["signal"]
            old = apply_trend_filter(frame, old_params, buy_setup=buy, sell_setup=sell)["signal"]

            assert new.tolist() == _first_aligned_loop(setups, bias, alternating=alternating)
            assert (new[old != 0] == old[old != 0]).all()  # mọi tín hiệu của cách cũ vẫn còn
            extra = (new != 0) & (old == 0)
            assert (raw[extra] == 0).all()  # tín hiệu thêm luôn nằm giữa đợt, không phải nến có raw_signal
            assert (new[new != 0] == bias[new != 0]).all() and (setups[new != 0] == new[new != 0]).all()  # cùng chiều trend, đúng nến setup
            assert (new.ne(0).groupby(raw.ne(0).cumsum()).sum() <= 1).all()  # tối đa một tín hiệu mỗi đợt
            if not alternating:
                assert new.tolist() == old.tolist()
            extra_total += int(extra.sum())
    assert extra_total >= 100  # dữ liệu ngẫu nhiên đủ phong phú để phép so không vô nghĩa


def test_held_direction_masks_give_every_run_whose_trend_aligns_exactly_one_signal(monkeypatch):
    """Mask = hướng của đợt đang giữ (cách combo dùng): đợt nào có nến trend cùng chiều thì có ĐÚNG một tín hiệu, ở nến đó đầu tiên."""
    params = {"TREND_FILTER_ENABLED": True, "TREND_METHODS": ("breakout",)}  # khối có chính sách first_aligned_per_run
    old_params = {"TREND_FILTER_ENABLED": True, "TREND_METHODS": _old_rule(monkeypatch)}
    runs_total = runs_rescued = 0
    for seed in range(60):
        setups, bias = _random_setups_and_bias(seed)
        buy, sell = pd.Series(setups == 1), pd.Series(setups == -1)
        raw = apply_alternating_filter(buy, sell, {"ALTERNATING_FILTER_ENABLED": True})
        held = raw.where(raw.ne(0)).ffill()
        frame = pd.DataFrame({"raw_signal": raw, "trend_bias": bias})

        new = apply_trend_filter(frame, params, buy_setup=held.eq(1), sell_setup=held.eq(-1))["signal"].to_numpy()
        old = apply_trend_filter(frame, old_params)["signal"].to_numpy()

        expected = np.zeros(len(raw), dtype=int)
        starts = np.flatnonzero(raw.to_numpy() != 0)
        for start, end in zip(starts, np.append(starts[1:], len(raw)), strict=True):
            aligned = np.flatnonzero(bias[start:end] == raw.iloc[start])
            if len(aligned):
                expected[start + aligned[0]] = raw.iloc[start]
                runs_rescued += int(old[start] == 0)
            runs_total += 1
        assert new.tolist() == expected.tolist()  # không còn đợt nào trend đổi đúng chiều mà không có tín hiệu
        assert (new[old != 0] == old[old != 0]).all()  # mọi tín hiệu của cách cũ vẫn còn
    assert runs_total >= 1000 and runs_rescued >= 200  # dữ liệu ngẫu nhiên đủ phong phú để phép so không vô nghĩa


# --- knn KHÔNG dời tín hiệu (bật 2026-10-23, tắt lại cùng ngày) ------------------------------------------------


@pytest.mark.parametrize("strategy", ["combo", "ma_cross", "macd_break"])
def test_knn_filters_only_the_first_bar_of_each_run_like_the_old_rule(strategy, monkeypatch):
    """knn giống hệt một khối không có ô trong POLICY (cách lọc cũ) ở cả hai kịch bản dựng tay: nến đầu đợt lệch trend thì mất cả đợt."""
    old_rule = _old_rule(monkeypatch)
    base = {"TREND_FILTER_ENABLED": True, "ALTERNATING_FILTER_ENABLED": True}
    for setups, trend, expected in ((SETUP_DIRECTIONS, TREND_DIRECTIONS, OLD_RULE_AT), (EVENT_SETUPS, EVENT_TREND, {})):
        frame = _setup_frame(strategy, setups, trend)

        out = _DETECT[strategy](frame, params={**base, "TREND_METHODS": ("knn",)})
        plain = _DETECT[strategy](frame, params={**base, "TREND_METHODS": old_rule})

        pd.testing.assert_frame_equal(out, plain)  # từng cột, từng ô: signal, trend_filter_status, signal_reason, entry...
        assert _at(out["signal"]) == expected
        assert not ((out["signal"] != 0) & (out["raw_signal"] == 0)).any()  # signal ⊆ raw_signal (DB1 ⊆ DB0)


def test_knn_ignores_the_setup_masks_so_the_filter_stays_the_old_rule(monkeypatch):
    """Dù chiến lược truyền mask setup (như thật), knn vẫn lọc theo cách cũ — kể cả mask hướng-đang-giữ mà combo dùng."""
    knn_params = {"TREND_FILTER_ENABLED": True, "TREND_METHODS": ("knn",)}
    old_params = {"TREND_FILTER_ENABLED": True, "TREND_METHODS": _old_rule(monkeypatch)}
    signals = 0
    for seed in range(60):
        setups, bias = _random_setups_and_bias(seed)
        buy, sell = pd.Series(setups == 1), pd.Series(setups == -1)
        raw = apply_alternating_filter(buy, sell, {"ALTERNATING_FILTER_ENABLED": True})
        held = raw.where(raw.ne(0)).ffill()
        frame = pd.DataFrame({"raw_signal": raw, "trend_bias": bias})

        plain = apply_trend_filter(frame, old_params)
        for masks in ({"buy_setup": buy, "sell_setup": sell}, {"buy_setup": held.eq(1), "sell_setup": held.eq(-1)}):
            pd.testing.assert_frame_equal(apply_trend_filter(frame, knn_params, **masks), plain)
        fired = plain["signal"] != 0
        assert (plain["signal"][fired] == raw[fired]).all()  # signal ⊆ raw_signal
        signals += int(fired.sum())
    assert signals >= 100  # dữ liệu ngẫu nhiên đủ phong phú để phép so không vô nghĩa


# --- bộ điều phối apply_signal_filters -----------------------------------------------------


def _filters_input(directions: list[int], trend: list[int]) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Khung tối thiểu + hai mask setup BUY/SELL, để gọi thẳng bộ điều phối (không qua chiến lược)."""
    d = np.array(directions)
    frame = pd.DataFrame({"bartime": pd.date_range("2026-01-05", periods=len(d), freq="30min"), "trend_bias": trend})
    return frame, pd.Series(d == 1), pd.Series(d == -1)


FILTER_PARAMS = {"TREND_FILTER_ENABLED": True, "ALTERNATING_FILTER_ENABLED": True, "TREND_METHODS": ("breakout",)}


def test_apply_signal_filters_writes_the_four_columns_in_order_and_leaves_the_input_alone():
    frame, buy, sell = _filters_input(SETUP_DIRECTIONS, TREND_DIRECTIONS)
    before = frame.copy()

    out = apply_signal_filters(frame, FILTER_PARAMS, buy_setup=buy, sell_setup=sell, buy_reason="up", sell_reason="down")

    pd.testing.assert_frame_equal(frame, before)  # không sửa khung của người gọi
    assert list(out.columns) == [*before.columns, "raw_signal", "signal", "trend_filter_status", "signal_reason"]
    assert out["raw_signal"].dtype == out["signal"].dtype == np.int64
    assert _at(out["raw_signal"]) == RAW_AT and _at(out["signal"]) == FIRST_ALIGNED_AT
    assert out["signal_reason"].iloc[4] == "down; aligned with trend reference" and out["signal_reason"].iloc[3] == ""


def test_apply_signal_filters_with_trend_off_gives_raw_signal_and_plain_reasons():
    frame, buy, sell = _filters_input(SETUP_DIRECTIONS, TREND_DIRECTIONS)

    out = apply_signal_filters(
        frame, {**FILTER_PARAMS, "TREND_FILTER_ENABLED": False}, buy_setup=buy, sell_setup=sell, buy_reason="up", sell_reason="down"
    )

    assert out["signal"].tolist() == out["raw_signal"].tolist() and _at(out["signal"]) == RAW_AT
    assert set(out["trend_filter_status"]) == {"disabled"}
    assert out.loc[out["signal"].eq(1), "signal_reason"].unique().tolist() == ["up"]  # không có hậu tố trend khi trend tắt
    assert out.loc[out["signal"].eq(-1), "signal_reason"].unique().tolist() == ["down"]


def test_apply_signal_filters_state_setups_use_the_setup_as_mask_and_event_setups_the_held_direction():
    frame, buy, sell = _filters_input(EVENT_SETUPS, EVENT_TREND)
    texts = {"buy_reason": "up", "sell_reason": "down"}

    state = apply_signal_filters(frame, FILTER_PARAMS, buy_setup=buy, sell_setup=sell, **texts)
    event = apply_signal_filters(
        frame, FILTER_PARAMS, buy_setup=buy, sell_setup=sell, hold_valid=pd.Series(True, index=frame.index),
        delayed_buy_reason="held up", delayed_sell_reason="held down", **texts,
    )

    assert _at(state["signal"]) == {}  # setup là trạng thái: cần một nến setup SAU khi trend đổi, ở đây không có
    assert _at(event["signal"]) == {3: -1, 8: 1}  # setup là sự kiện: giữ tới hết đợt, bắn ngay nến trend đổi đúng chiều
    assert event["signal_reason"].iloc[3] == "held down; aligned with trend reference"
    assert event["signal_reason"].iloc[8] == "held up; aligned with trend reference"
    # hold_valid là điều kiện từng nến: nến trend đổi (3) không hợp lệ thì tín hiệu giữ của đợt bắn ở nến hợp lệ kế tiếp có trend cùng chiều (4)
    invalid_at_3 = pd.Series(np.arange(len(frame)) != 3)
    assert _at(apply_signal_filters(frame, FILTER_PARAMS, buy_setup=buy, sell_setup=sell, hold_valid=invalid_at_3, **texts)["signal"]) == {4: -1, 8: 1}
    never_valid = pd.Series(False, index=frame.index)
    assert _at(apply_signal_filters(frame, FILTER_PARAMS, buy_setup=buy, sell_setup=sell, hold_valid=never_valid, **texts)["signal"]) == {}


def test_apply_signal_filters_delayed_reasons_only_label_signals_off_the_raw_bar():
    frame, buy, sell = _filters_input(SETUP_DIRECTIONS, TREND_DIRECTIONS)

    out = apply_signal_filters(
        frame, FILTER_PARAMS, buy_setup=buy, sell_setup=sell, buy_reason="up", sell_reason="down",
        delayed_buy_reason="late up", delayed_sell_reason="late down",
    )

    # tín hiệu ở nến 4 và 10 bắn dời (nến đầu đợt lệch trend); tín hiệu ở nến 12 nằm đúng nến raw nên giữ lý do thường
    assert out["signal_reason"].iloc[4] == "late down; aligned with trend reference"
    assert out["signal_reason"].iloc[10] == "late up; aligned with trend reference"
    assert out["signal_reason"].iloc[12] == "down; aligned with trend reference"


def test_apply_signal_filters_without_a_policy_keeps_the_old_rule_even_with_a_held_mask(monkeypatch):
    frame, buy, sell = _filters_input(EVENT_SETUPS, EVENT_TREND)
    params = {**FILTER_PARAMS, "TREND_METHODS": _old_rule(monkeypatch)}

    out = apply_signal_filters(
        frame, params, buy_setup=buy, sell_setup=sell, hold_valid=pd.Series(True, index=frame.index), buy_reason="up", sell_reason="down"
    )

    assert _at(out["raw_signal"]) == {1: -1, 7: 1} and _at(out["signal"]) == {}  # khối không có chính sách: nến đầu đợt lệch trend thì mất cả đợt
    assert out["trend_filter_status"].tolist()[1] == "filtered"


# --- khối ZIGZAG (cấu trúc thị trường, thêm 2026-10-24) -----------------------------------------------------------------

ZZ_ATR_FAST = {"ZIGZAG_PIVOT_MODE": "atr", "ZIGZAG_ATR_MULT": 0.5, "ZIGZAG_ATR_PERIOD": 3}  # đỉnh/đáy chốt ngay nhịp đảo đầu tiên


def _zigzag_path_bars(points: list[float], step: float = 2.0) -> pd.DataFrame:
    """Đường giá gấp khúc: từng nhịp đi thẳng tới điểm cực trị kế tiếp, mỗi nến đi `step`; open = close nến trước, không có râu."""
    closes = [float(points[0])]
    for start, stop in pairwise(points):
        count = round(abs(stop - start) / step)
        closes += [start + (stop - start) * k / count for k in range(1, count + 1)]
    close = np.array(closes)
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame(
        {
            "bartime": pd.date_range("2026-01-05", periods=len(close), freq="4h"),
            "open": open_,
            "high": np.maximum(open_, close),
            "low": np.minimum(open_, close),
            "close": close,
            "volume": 1.0,
        }
    )


def _mirror_bars(bars: pd.DataFrame) -> pd.DataFrame:
    """Gương giá: giá -> -giá, high <-> low. Luật của khối là đối xứng nên hướng phải đổi dấu đúng từng nến."""
    out = bars.copy()
    out["open"], out["close"] = -bars["open"], -bars["close"]
    out["high"], out["low"] = -bars["low"], -bars["high"]
    return out


def _zigzag_bias(bars: pd.DataFrame, params: dict | None = None) -> np.ndarray:
    return zigzag.build(bars, params)[zigzag.BIAS_COLUMN].to_numpy()


# Tăng (đỉnh 110 -> 116 -> 124, đáy 104 -> 108), rồi hồi về 114 (L2 > L7 = 108), bật lên 120 (đỉnh thấp hơn 124), rồi thủng 108.
ZZ_REVERSAL = [100, 110, 104, 116, 108, 124, 114, 120, 100]
# Như trên nhưng sau đỉnh 124 giá SẬP THẲNG xuống 100 (thủng L7 = 108 ngay, chưa có đỉnh thấp hơn), hồi lên 110 rồi thủng 100.
ZZ_CRASH = [100, 110, 104, 116, 108, 124, 100, 110, 96]


def test_zigzag_block_declares_the_block_contract():
    assert zigzag.NAME == "zigzag"
    assert zigzag.BIAS_COLUMN == trend_tf.bias_column("zigzag") == "trend_bias_zigzag"
    assert not hasattr(zigzag, "SAME_TIMEFRAME") and not hasattr(zigzag, "FIRST_ALIGNED_PER_RUN")  # chính sách ở rules/trend_tf.POLICY
    assert {line["column"] for line in zigzag.OVERLAYS} <= set(zigzag.OUTPUT_COLUMNS)
    for line in zigzag.OVERLAYS:
        assert set(line) == {"column", "label", "color", "colored_by_bias"}
    assert trend_tf.get_method("zigzag") is zigzag
    # khung tham chiếu riêng như knn (tra trend_tf_by_symbol), không dời tín hiệu (mặc định, chờ người dùng chọn khác)
    policy = trend_tf.policy_of("zigzag")
    assert (policy.same_timeframe, policy.first_aligned_per_run) == (False, False)   # warm-up nến trend dài: test_policy_table_holds_the_trend_warmup...
    assert not trend_tf.uses_entry_timeframe(("zigzag",)) and not trend_tf.uses_first_aligned_per_run(("zigzag",))


def test_zigzag_build_adds_the_columns_with_an_int_bias_and_leaves_the_input_untouched():
    bars = _ohlcv(700, freq_minutes=240, seed=2)
    before = bars.copy()

    out = zigzag.build(bars)

    pd.testing.assert_frame_equal(bars, before)  # không sửa input
    assert list(out.columns) == [*before.columns, *zigzag.OUTPUT_COLUMNS, zigzag.BIAS_COLUMN]
    assert out[zigzag.BIAS_COLUMN].dtype == np.int64 and set(out[zigzag.BIAS_COLUMN]) <= {-1, 0, 1}
    assert (out[zigzag.BIAS_COLUMN] == out["trend_zigzag_direction"]).all()
    assert set(out[zigzag.BIAS_COLUMN]) >= {-1, 1}  # chuỗi đủ dài để có cả hai hướng


def test_zigzag_empty_frame_gives_empty_columns():
    empty = pd.DataFrame({"bartime": pd.to_datetime([]), "open": [], "high": [], "low": [], "close": [], "volume": []})
    out = zigzag.build(empty)
    assert len(out) == 0 and set(zigzag.OUTPUT_COLUMNS) <= set(out.columns) and zigzag.BIAS_COLUMN in out.columns
    assert list(zigzag.calc_zigzag_structure(empty).columns) == ["zigzag_pivot", "zigzag_key_level", "zigzag_direction"]


def test_zigzag_params_accept_aliases_and_reject_bad_values():
    assert zigzag.zigzag_trend_indicator_params(None) == {
        "ZIGZAG_PIVOT_MODE": "both", "ZIGZAG_DEPTH": 5, "ZIGZAG_ATR_MULT": 3.14, "ZIGZAG_ATR_PERIOD": 5,
    }
    for alias, mode in (("1", "atr"), ("2", "depth"), ("3", "both"), (" ATR ", "atr"), ("Depth", "depth"), (2, "depth")):
        assert zigzag.zigzag_trend_indicator_params({"ZIGZAG_PIVOT_MODE": alias})["ZIGZAG_PIVOT_MODE"] == mode
    assert zigzag.zigzag_trend_indicator_params({"ZIGZAG_DEPTH": "7", "ZIGZAG_ATR_MULT": "2.5"})["ZIGZAG_DEPTH"] == 7
    with pytest.raises(ValueError, match="ZIGZAG_PIVOT_MODE must be one of atr, depth, both"):
        zigzag.zigzag_trend_indicator_params({"ZIGZAG_PIVOT_MODE": "ema"})
    for key, bad in (("ZIGZAG_DEPTH", 0), ("ZIGZAG_DEPTH", -3), ("ZIGZAG_DEPTH", "x"), ("ZIGZAG_ATR_PERIOD", 0)):
        with pytest.raises(ValueError, match=f"{key} must be a positive integer"):
            zigzag.zigzag_trend_indicator_params({key: bad})
    for bad in (0, -1.0, float("nan"), float("inf"), "x"):
        with pytest.raises(ValueError, match="ZIGZAG_ATR_MULT must be a positive number"):
            zigzag.zigzag_trend_indicator_params({"ZIGZAG_ATR_MULT": bad})


@pytest.mark.parametrize("mode", ["atr", "depth", "both"])
def test_zigzag_bias_and_key_level_are_causal_while_the_pivot_line_is_drawn_retroactively(mode):
    bars = _ohlcv(900, freq_minutes=240, seed=3)
    params = {"ZIGZAG_PIVOT_MODE": mode}
    full = zigzag.build(bars, params)
    pivot_changed = False
    for cut in (250, 400, 650, 899):
        part = zigzag.build(bars.iloc[: cut + 1].reset_index(drop=True), params)
        # hướng và ngưỡng phá tại nến t chỉ phụ thuộc nến <= t: cắt bớt dữ liệu cuối không đổi phần đầu
        assert (part[zigzag.BIAS_COLUMN].to_numpy() == full[zigzag.BIAS_COLUMN].to_numpy()[: cut + 1]).all()
        assert np.array_equal(part["trend_zigzag_key_level"].to_numpy(), full["trend_zigzag_key_level"].to_numpy()[: cut + 1], equal_nan=True)
        pivot_changed |= not np.array_equal(part["trend_zigzag_pivot"].to_numpy(), full["trend_zigzag_pivot"].to_numpy()[: cut + 1], equal_nan=True)
    assert pivot_changed  # đường ZigZag chỉ để VẼ (nội suy ngược tới đỉnh/đáy chốt sau): không dùng để lọc


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
@pytest.mark.parametrize("mode", ["atr", "depth", "both"])
def test_zigzag_is_mirror_symmetric(seed, mode):
    """Tăng và giảm đối xứng hoàn toàn: gương giá thì hướng đổi dấu đúng từng nến."""
    bars = _ohlcv(800, freq_minutes=240, seed=seed)
    params = {"ZIGZAG_PIVOT_MODE": mode}
    assert (_zigzag_bias(_mirror_bars(bars), params) == -_zigzag_bias(bars, params)).all()


def test_zigzag_reversal_needs_a_lower_high_and_a_close_below_both_the_support_and_the_pullback_low():
    bars = _zigzag_path_bars(ZZ_REVERSAL)
    close = bars["close"].to_numpy()
    out = zigzag.build(bars, ZZ_ATR_FAST)
    bias, key = out[zigzag.BIAS_COLUMN].to_numpy(), out["trend_zigzag_key_level"].to_numpy()
    first_up, flip = 12, 41  # nến đóng trên đỉnh 110 đầu tiên; nến đóng 106 (< L7 = 108) đầu tiên sau nhịp hồi
    assert close[first_up] == 112.0 and close[flip] == 106.0 and close[flip - 1] == 108.0
    assert (bias[:first_up] == 0).all()  # chưa đủ cấu trúc khởi tạo: trung tính
    assert (bias[first_up:flip] == 1).all()  # kể cả khi giá hồi về 114 (L2 > L7) rồi tụt về 108 (chạm L7 chưa phải phá)
    assert (bias[flip:] == -1).all()  # đảo chiều đúng nến đóng cửa đầu tiên dưới min(L7 = 108, L2 = 114)
    assert key[flip - 1] == 108.0 and close[flip] < key[flip - 1]  # ngưỡng phá trước nến lật
    assert key[flip] == 120.0  # kháng cự mới của xu hướng giảm = đỉnh thấp hơn vừa hình thành


def test_zigzag_waits_for_both_conditions_after_a_straight_crash():
    """Sập thẳng thủng L7 mà CHƯA có cấu trúc giảm (đáy L2 + đỉnh hồi thấp hơn): vẫn giữ +1; lật khi hồi rồi đóng dưới đáy L2."""
    bars = _zigzag_path_bars(ZZ_CRASH)
    close = bars["close"].to_numpy()
    out = zigzag.build(bars, ZZ_ATR_FAST)
    bias, key = out[zigzag.BIAS_COLUMN].to_numpy(), out["trend_zigzag_key_level"].to_numpy()
    first_up, flip = 12, 49
    assert close[flip] == 98.0 and close[flip - 1] == 100.0
    crash = np.flatnonzero(close < 108.0)  # có nhiều nến đóng dưới L7 = 108 nhưng chưa đủ điều kiện
    assert ((crash > 26) & (crash < flip)).sum() > 10
    assert (bias[first_up:flip] == 1).all() and (bias[flip:] == -1).all()
    assert key[flip - 1] == 100.0  # min(L7 = 108, L2 = đáy 100 của cú sập)


@pytest.mark.parametrize("points", [ZZ_REVERSAL, ZZ_CRASH], ids=["reversal", "crash"])
def test_zigzag_scenarios_mirror_into_the_downtrend_rules(points):
    """Luật xu hướng giảm đối xứng hoàn toàn với tăng: -1 -> +1 khi đã có đáy cao hơn và đóng trên cả H7 lẫn H2."""
    bars = _zigzag_path_bars(points)
    mirrored = _mirror_bars(bars)
    bias = _zigzag_bias(bars, ZZ_ATR_FAST)
    assert (_zigzag_bias(mirrored, ZZ_ATR_FAST) == -bias).all()
    assert (_zigzag_bias(mirrored, ZZ_ATR_FAST)[12:] != 0).all() and _zigzag_bias(mirrored, ZZ_ATR_FAST)[-1] == 1


@pytest.mark.parametrize(
    ("strategy", "entry_tf", "trend_tf_name"),
    [("combo", "H1", "H4"), ("ma_cross", "M30", "H1"), ("macd_break", "H1", "H4")],
)
def test_zigzag_filters_signals_to_the_trend_direction_without_moving_them(strategy, entry_tf, trend_tf_name):
    entry, trend = _ohlcv(900), _ohlcv(900, seed=7)
    run = configuration.run_strategy_with_trend_reference(
        strategy, symbol="US30", entry_tf=entry_tf, entry_bars=entry, trend_tf=trend_tf_name, trend_bars=trend,
        overrides={"TREND_METHODS": "zigzag"},
    )

    signal, raw, bias = run["signal"].to_numpy(), run["raw_signal"].to_numpy(), run["trend_bias"].to_numpy()
    assert (bias != 0).any() and (signal != 0).any() and (signal != 0).sum() < (raw != 0).sum()  # có lọc thật
    assert (signal[signal != 0] == raw[signal != 0]).all() and (signal[signal != 0] == bias[signal != 0]).all()  # cùng chiều trend, không dời nến
    assert set(zigzag.OUTPUT_COLUMNS) <= set(run.columns) and zigzag.BIAS_COLUMN not in run.columns  # cột riêng sang khung vào lệnh, cột hướng riêng thì không


def test_zigzag_takes_its_trend_timeframe_from_the_table_like_knn():
    params = configuration.get_strategy("combo").normalize_params({"TREND_METHODS": "zigzag"}, "US30", "H1")
    assert params["TREND_METHODS"] == ("zigzag",)
    assert params["TREND_TF"] == configuration.get_strategy("combo").trend_tf_by_symbol["US30"]["H1"]  # ô của bảng, KHÔNG phải khung vào lệnh


# --- khối T-STAT --------------------------------------------------------------------------------------------------


def _tstat_bars(n: int = 700, *, seed: int = 11, drift: float = 0.0, freq_minutes: int = 240) -> pd.DataFrame:
    """Nến có giá DƯƠNG (bước ngẫu nhiên hình học, cố định seed) vì khối lấy log; high/low bao quanh open/close."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(drift + 0.004 * rng.standard_normal(n)))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame(
        {
            "bartime": pd.date_range("2026-01-05", periods=n, freq=f"{freq_minutes}min"),
            "open": open_,
            "high": np.maximum(open_, close) * 1.001,
            "low": np.minimum(open_, close) * 0.999,
            "close": close,
            "volume": 100.0,
        }
    )


def _tstat_reference(window: np.ndarray, lag: int, mode: str) -> float:
    """Bản độc lập bằng đại số ma trận Newey–West ĐẦY ĐỦ (không dùng công thức rút gọn của khối)."""
    n = len(window)
    design = np.column_stack([np.ones(n), np.arange(n, dtype=float)]) if mode == "slope" else np.ones((n, 1))
    index = design.shape[1] - 1
    inverse = np.linalg.inv(design.T @ design)
    beta = inverse @ design.T @ window
    scores = design * (window - design @ beta)[:, None]
    sigma = scores.T @ scores
    for step in range(1, lag + 1):
        gamma = scores[step:].T @ scores[:-step]
        sigma = sigma + (1.0 - step / (lag + 1.0)) * (gamma + gamma.T)
    covariance = inverse @ sigma @ inverse
    return float(beta[index] / np.sqrt(covariance[index, index]))


def test_tstat_block_declares_the_block_contract():
    assert tstat.NAME == "tstat"
    assert tstat.BIAS_COLUMN == trend_tf.bias_column("tstat") == "trend_bias_tstat"
    assert not hasattr(tstat, "SAME_TIMEFRAME") and not hasattr(tstat, "FIRST_ALIGNED_PER_RUN")  # chính sách ở rules/trend_tf.POLICY
    assert {line["column"] for line in tstat.OVERLAYS} <= set(tstat.OUTPUT_COLUMNS)
    for line in tstat.OVERLAYS:
        assert set(line) == {"column", "label", "color", "colored_by_bias"}
    # khung tham chiếu riêng như knn/zigzag (tra trend_tf_by_symbol), CÓ dời tín hiệu (bật 2026-10-26), warm-up nến trend chung của hệ
    assert trend_tf.policy_of("tstat") == trend_tf.MethodPolicy(same_timeframe=False, first_aligned_per_run=True, trend_warmup_bars=0)
    assert not trend_tf.uses_entry_timeframe(("tstat",)) and trend_tf.uses_first_aligned_per_run(("tstat",))


def test_tstat_build_adds_the_columns_with_an_int_bias_and_leaves_the_input_untouched():
    bars = _tstat_bars(700)
    before = bars.copy()

    out = tstat.build(bars)

    pd.testing.assert_frame_equal(bars, before)  # không sửa input
    assert list(out.columns) == [*before.columns, *tstat.OUTPUT_COLUMNS, tstat.BIAS_COLUMN]
    bias, t_stat = out[tstat.BIAS_COLUMN], out["trend_tstat_t"]
    assert bias.dtype == np.int64 and set(bias) == {-1, 0, 1}  # bước ngẫu nhiên đủ dài có cả hai hướng và vùng trung tính
    assert (bias.iloc[:99] == 0).all() and t_stat.iloc[:99].isna().all() and t_stat.iloc[99:].notna().all()  # N = 100: nến hợp lệ đầu tiên là nến thứ 100
    assert (bias[t_stat > 2.0] == 1).all() and (bias[t_stat < -2.0] == -1).all() and (bias[t_stat.abs() <= 2.0] == 0).all()
    assert out["trend_tstat_fit"].iloc[99:].between(bars["close"].min() * 0.5, bars["close"].max() * 2.0).all()  # giá khớp OLS cùng thang giá


def test_tstat_return_mode_needs_one_more_bar_than_the_lookback():
    out = tstat.build(_tstat_bars(300), {"TSTAT_MODE": "return", "TSTAT_LOOKBACK": 50})
    assert out["trend_tstat_t"].iloc[:50].isna().all() and out["trend_tstat_t"].iloc[50:].notna().all()  # N lợi suất = N + 1 nến đóng cửa


def test_tstat_short_and_empty_frames_are_neutral():
    short = tstat.build(_tstat_bars(60))  # ngắn hơn N = 100
    assert (short[tstat.BIAS_COLUMN] == 0).all() and short["trend_tstat_t"].isna().all() and short["trend_tstat_fit"].isna().all()
    empty = pd.DataFrame({"bartime": pd.to_datetime([]), "open": [], "high": [], "low": [], "close": [], "volume": []})
    out = tstat.build(empty)
    assert len(out) == 0 and set(tstat.OUTPUT_COLUMNS) <= set(out.columns) and tstat.BIAS_COLUMN in out.columns


def test_tstat_params_accept_aliases_and_reject_bad_values():
    assert tstat.tstat_trend_indicator_params(None) == {"TSTAT_MODE": "slope", "TSTAT_LOOKBACK": 100, "TSTAT_THRESHOLD": 2.0, "TSTAT_NW_LAG": 4}
    for alias, mode in (("1", "slope"), ("2", "return"), (" RETURN ", "return"), ("Slope", "slope"), (2, "return")):
        assert tstat.tstat_trend_indicator_params({"TSTAT_MODE": alias})["TSTAT_MODE"] == mode
    assert [tstat.default_nw_lag(n) for n in (50, 100, 200, 250)] == [3, 4, 4, 4]  # floor(4·(N/100)^(2/9))
    assert tstat.tstat_trend_indicator_params({"TSTAT_LOOKBACK": 200})["TSTAT_NW_LAG"] == 4  # None = quy tắc Newey–West theo N
    assert tstat.tstat_trend_indicator_params({"TSTAT_LOOKBACK": "60", "TSTAT_NW_LAG": "0", "TSTAT_THRESHOLD": "1.5"}) == {
        "TSTAT_MODE": "slope", "TSTAT_LOOKBACK": 60, "TSTAT_THRESHOLD": 1.5, "TSTAT_NW_LAG": 0,
    }
    with pytest.raises(ValueError, match="TSTAT_MODE must be one of slope, return"):
        tstat.tstat_trend_indicator_params({"TSTAT_MODE": "ema"})
    for bad in (0, -3, "x", 4):
        with pytest.raises(ValueError, match="TSTAT_LOOKBACK must be"):
            tstat.tstat_trend_indicator_params({"TSTAT_LOOKBACK": bad})
    for bad in (-1, "x"):
        with pytest.raises(ValueError, match="TSTAT_NW_LAG must be a non-negative integer"):
            tstat.tstat_trend_indicator_params({"TSTAT_NW_LAG": bad})
    with pytest.raises(ValueError, match="TSTAT_NW_LAG must be smaller than TSTAT_LOOKBACK"):
        tstat.tstat_trend_indicator_params({"TSTAT_LOOKBACK": 20, "TSTAT_NW_LAG": 20})
    for bad in (0, -1.0, float("nan"), float("inf"), "x"):
        with pytest.raises(ValueError, match="TSTAT_THRESHOLD must be a positive number"):
            tstat.tstat_trend_indicator_params({"TSTAT_THRESHOLD": bad})


@pytest.mark.parametrize("mode", ["slope", "return"])
@pytest.mark.parametrize("lag", [0, 3, 8])
def test_tstat_matches_an_independent_full_matrix_newey_west_implementation(mode, lag):
    bars = _tstat_bars(400, seed=5, drift=0.0006)
    size = 40
    out = tstat.build(bars, {"TSTAT_MODE": mode, "TSTAT_LOOKBACK": size, "TSTAT_NW_LAG": lag})
    log_close = np.log(bars["close"].to_numpy())
    series = log_close if mode == "slope" else np.r_[np.nan, np.diff(log_close)]
    for i in (size + 2, 123, 250, 399):
        expected = _tstat_reference(series[i - size + 1 : i + 1], lag, mode)
        assert out["trend_tstat_t"].iloc[i] == pytest.approx(expected, rel=1e-9, abs=1e-9)


def test_tstat_fit_is_the_ols_line_of_the_last_n_log_prices_at_the_last_bar():
    bars = _tstat_bars(300, seed=9)
    out = tstat.build(bars, {"TSTAT_LOOKBACK": 30})
    log_close = np.log(bars["close"].to_numpy())
    for i in (29, 100, 299):
        window = log_close[i - 29 : i + 1]
        slope, intercept = np.polyfit(np.arange(30, dtype=float), window, 1)
        assert out["trend_tstat_fit"].iloc[i] == pytest.approx(np.exp(intercept + slope * 29), rel=1e-9)


@pytest.mark.parametrize("mode", ["slope", "return"])
def test_tstat_is_causal(mode):
    bars = _tstat_bars(900, seed=3, drift=0.0003)
    params = {"TSTAT_MODE": mode, "TSTAT_LOOKBACK": 80}
    full = tstat.build(bars, params)
    for cut in (120, 400, 899):
        part = tstat.build(bars.iloc[: cut + 1].reset_index(drop=True), params)
        # cắt bớt nến cuối không đổi phần đầu: hướng giống hệt; t và đường khớp giống tới sai số làm tròn (BLAS chia khối theo số hàng)
        assert np.array_equal(part[tstat.BIAS_COLUMN].to_numpy(), full[tstat.BIAS_COLUMN].to_numpy()[: cut + 1])
        for column in tstat.OUTPUT_COLUMNS:
            np.testing.assert_allclose(part[column].to_numpy(), full[column].to_numpy()[: cut + 1], rtol=1e-9, atol=1e-12, equal_nan=True)


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("mode", ["slope", "return"])
def test_tstat_is_mirror_symmetric(seed, mode):
    """Gương log-giá (close -> K / close) thì t đổi dấu đúng từng nến, hướng đổi dấu, đường khớp thành K / đường khớp."""
    bars = _tstat_bars(600, seed=seed, drift=0.0004)
    mirrored = bars.copy()
    mirrored["close"] = 10_000.0 / bars["close"]
    params = {"TSTAT_MODE": mode, "TSTAT_LOOKBACK": 60}
    a, b = tstat.build(bars, params), tstat.build(mirrored, params)
    np.testing.assert_allclose(b["trend_tstat_t"].to_numpy(), -a["trend_tstat_t"].to_numpy(), rtol=1e-9, atol=1e-9, equal_nan=True)
    assert (b[tstat.BIAS_COLUMN].to_numpy() == -a[tstat.BIAS_COLUMN].to_numpy()).all()
    np.testing.assert_allclose(b["trend_tstat_fit"].to_numpy(), 10_000.0 / a["trend_tstat_fit"].to_numpy(), rtol=1e-9, equal_nan=True)


@pytest.mark.parametrize("mode", ["slope", "return"])
def test_tstat_flags_a_clean_trend_and_stays_neutral_on_a_flat_series(mode):
    index = np.arange(300, dtype=float)
    params = {"TSTAT_MODE": mode, "TSTAT_LOOKBACK": 50}
    up, down, flat = (_tstat_bars(300).assign(close=100.0 * np.exp(slope * index)) for slope in (0.001, -0.001, 0.0))
    assert (tstat.build(up, params)[tstat.BIAS_COLUMN].iloc[60:] == 1).all()
    assert (tstat.build(down, params)[tstat.BIAS_COLUMN].iloc[60:] == -1).all()
    out = tstat.build(flat, params)  # đứng yên: 0/0 -> NaN -> trung tính (không cảnh báo, không lỗi)
    assert (out[tstat.BIAS_COLUMN] == 0).all()


def test_tstat_windows_with_an_invalid_price_are_neutral_until_the_price_leaves_the_window():
    bars = _tstat_bars(400, seed=4, drift=0.002)  # xu hướng tăng rõ nên ngoài vùng hỏng hướng khác 0
    bars.loc[200, "close"] = np.nan
    bars.loc[250, "close"] = -5.0
    out = tstat.build(bars, {"TSTAT_MODE": "slope", "TSTAT_LOOKBACK": 40, "TSTAT_NW_LAG": 2})
    bad = np.zeros(400, dtype=bool)
    bad[200:240] = True  # các cửa sổ chứa nến 200
    bad[250:290] = True  # các cửa sổ chứa nến 250
    assert out["trend_tstat_t"].isna().to_numpy()[100:][bad[100:]].all()
    assert (out[tstat.BIAS_COLUMN].to_numpy()[bad] == 0).all()
    assert out["trend_tstat_t"].iloc[290:].notna().all() and (out[tstat.BIAS_COLUMN].iloc[290:] != 0).any()


def test_tstat_result_does_not_depend_on_the_chunk_size(monkeypatch):
    bars = _tstat_bars(900, seed=6, drift=0.0002)
    expected = tstat.build(bars, {"TSTAT_MODE": "slope", "TSTAT_LOOKBACK": 70})
    monkeypatch.setattr(tstat, "_CHUNK_ELEMENTS", 700)  # ~10 cửa sổ mỗi khối
    chunked = tstat.build(bars, {"TSTAT_MODE": "slope", "TSTAT_LOOKBACK": 70})
    pd.testing.assert_frame_equal(chunked, expected)


def test_tstat_slope_overrejects_a_driftless_random_walk_while_return_mode_is_calibrated():
    """Hồi quy giả trên chuỗi I(1): t của độ dốc log-giá vượt ±2 ở phần lớn cửa sổ dù KHÔNG có xu hướng (đo 2026-10-26: ~75%, khớp việc bài báo
    gốc chỉ thấy ~9–18% hợp đồng trung tính), còn t của lợi suất trung bình là kiểm định đúng cỡ (~5,5%). Test khoá cả hai kết quả."""
    close = 100.0 * np.exp(np.cumsum(0.002 * np.random.default_rng(5).standard_normal(30_000)))
    slope_t, _ = tstat.calc_trend_tstat(close, mode="slope", lookback=100, nw_lag=4)
    return_t, _ = tstat.calc_trend_tstat(close, mode="return", lookback=100, nw_lag=4)

    def share(values: np.ndarray) -> float:
        valid = values[~np.isnan(values)]
        return float((np.abs(valid) > 2.0).mean())

    assert share(slope_t) > 0.5
    assert share(return_t) < 0.10


def test_tstat_lookback_and_lag_fit_inside_the_snapshot_des_reads_for_the_trend_timeframe():
    params = tstat.tstat_trend_indicator_params(None)
    snapshot_bars = int(configuration.CONFIG["redis"]["input"]["snapshot_bars"])  # des đọc ít nhất chừng này nến khung trend (không có warm-up riêng)
    assert params["TSTAT_LOOKBACK"] + params["TSTAT_NW_LAG"] + 1 <= snapshot_bars
    assert trend_tf.policy_of("tstat").trend_warmup_bars == 0 and trend_tf.max_trend_warmup_bars(("tstat",)) == 0


def test_tstat_runs_through_build_trend_frame_and_carries_its_columns():
    bars = _tstat_bars(400)
    out = trend_tf.build_trend_frame(bars, {"TREND_METHODS": ("tstat",), "TREND_TF": "H4"})
    assert set(tstat.OUTPUT_COLUMNS) <= set(out.columns) and "trend_bias" in out.columns and "trend_close_time" in out.columns
    assert (out["trend_bias"].to_numpy() == out[tstat.BIAS_COLUMN].to_numpy()).all()


@pytest.mark.parametrize(
    ("strategy", "entry_tf", "trend_tf_name"),
    [("combo", "H1", "H4"), ("ma_cross", "M30", "H1"), ("macd_break", "H1", "H4")],
)
def test_tstat_shifts_signals_to_the_first_aligned_bar_of_each_run(strategy, entry_tf, trend_tf_name, monkeypatch):
    entry, trend = _tstat_bars(1200, seed=21, freq_minutes=60), _tstat_bars(1200, seed=7)

    def run_with(shift_on: bool) -> pd.DataFrame:
        monkeypatch.setitem(trend_tf.POLICY, "tstat", trend_tf.MethodPolicy(first_aligned_per_run=shift_on))
        return configuration.run_strategy_with_trend_reference(
            strategy, symbol="US30", entry_tf=entry_tf, entry_bars=entry, trend_tf=trend_tf_name, trend_bars=trend,
            overrides={"TREND_METHODS": "tstat"},
        )

    run, plain = run_with(True), run_with(False)
    signal, raw, bias, old = (run["signal"].to_numpy(), run["raw_signal"].to_numpy(), run["trend_bias"].to_numpy(), plain["signal"].to_numpy())
    assert (bias != 0).any() and (signal != 0).any() and (signal != 0).sum() < (raw != 0).sum()  # vẫn lọc thật
    assert (signal[signal != 0] == bias[signal != 0]).all()  # mọi tín hiệu cùng chiều trend
    assert (signal[old != 0] == old[old != 0]).all() and (signal != 0).sum() >= (old != 0).sum()  # dời không làm mất tín hiệu của cách cũ
    assert (signal != 0).sum() <= (raw != 0).sum()  # tối đa một tín hiệu mỗi đợt, mỗi đợt mở bằng một raw_signal
    assert set(tstat.OUTPUT_COLUMNS) <= set(run.columns) and tstat.BIAS_COLUMN not in run.columns  # cột riêng sang khung vào lệnh, cột hướng riêng thì không


def test_tstat_takes_its_trend_timeframe_from_the_table_like_knn():
    params = configuration.get_strategy("combo").normalize_params({"TREND_METHODS": "tstat"}, "US30", "H1")
    assert params["TREND_METHODS"] == ("tstat",)
    assert params["TREND_TF"] == configuration.get_strategy("combo").trend_tf_by_symbol["US30"]["H1"]  # ô của bảng, KHÔNG phải khung vào lệnh


# =============================================================================
# Dời tín hiệu tách thành rules/shift.py (2026-10-26), bật cho breakout và tstat.
# =============================================================================


def test_the_shift_is_enabled_for_exactly_breakout_and_tstat():
    assert {name for name, policy in trend_tf.POLICY.items() if policy.first_aligned_per_run} == {"breakout", "tstat"}


def test_shift_module_is_a_bottom_layer_and_trend_tf_delegates_to_it():
    tree = ast.parse(inspect.getsource(shift))
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert imported <= {"__future__", "numpy", "pandas"}  # tầng đáy: không import gì trong src/
    assert trend_tf.first_aligned_signal is shift.first_aligned_signal and trend_tf.allowed_masks is shift.allowed_masks
    assert not hasattr(trend_tf, "_first_aligned_signal")  # thuật toán chỉ còn ở một nơi


def test_first_aligned_signal_fires_once_per_run_at_the_first_aligned_bar():
    raw = pd.Series([1, 0, 0, -1, 0, 0])
    bias = pd.Series([-1, -1, 1, 1, -1, -1])
    buy = pd.Series([True, True, True, False, False, False])
    sell = pd.Series([False, False, False, True, True, True])
    # đợt 1 (nến 0-2): trend lệch ở nến 0-1, cùng chiều ở nến 2 -> BUY dời tới nến 2; đợt 2 (nến 3-5): SELL ở nến 4, nến 5 không bắn lại
    assert shift.first_aligned_signal(raw, bias, buy, sell).tolist() == [0, 0, 1, 0, -1, 0]


def test_allowed_masks_use_the_setup_for_state_setups_and_the_held_direction_for_event_setups():
    raw = pd.Series([0, 1, 0, 0, -1, 0, 0])
    buy = pd.Series([False, True, False, False, False, False, False])
    sell = pd.Series([False, False, False, False, True, False, False])
    state_buy, state_sell = shift.allowed_masks(raw, buy, sell)
    assert state_buy.equals(buy) and state_sell.equals(sell)  # setup là trạng thái: chính các nến còn setup
    valid = pd.Series([True, True, True, False, True, True, True])
    held_buy, held_sell = shift.allowed_masks(raw, buy, sell, valid)
    assert held_buy.tolist() == [False, True, True, False, False, False, False]   # BUY giữ tới trước nến SELL, trừ nến hold_valid sai
    assert held_sell.tolist() == [False, False, False, False, True, True, True]
