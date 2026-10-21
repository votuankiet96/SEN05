"""Tests cho các khối xác nhận xu hướng (src/trend_methods/) của des: khối KNN, sổ đăng ký, ghép nhiều khối.

Chưa có khối thứ hai trong sản xuất, nên phần "nhiều khối" được kiểm bằng một khối GIẢ chỉ tồn tại trong
test (đăng ký tạm bằng monkeypatch, tự gỡ sau mỗi test). Bộ nến tạo tất định ngay trong file này
(des/tests không có __init__.py nên không import chéo giữa các file test).
"""

from __future__ import annotations

import types

import numpy as np
import pandas as pd
import pytest
from wisdom_engine.des.src import configuration
from wisdom_engine.des.src.rules import trend_tf
from wisdom_engine.des.src.rules.trend_tf import apply_trend_filter, combine_trend_bias
from wisdom_engine.des.src.trend_methods import knn

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


def test_methods_lists_knn_and_every_block_meets_the_contract() -> None:
    assert trend_tf.method_names() == ("knn",)
    assert trend_tf.get_method("knn") is knn
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
    with pytest.raises(ValueError, match=r"unknown trend method\(s\): ema \(available: knn\)"):
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
    assert trend_tf.method_names() == ("knn", "fake")


# --- cấu hình des -----------------------------------------------------------------------


def test_trend_methods_come_from_des_config_and_reject_unregistered_override() -> None:
    assert configuration.trend_methods_for("combo") == ("knn",)
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
