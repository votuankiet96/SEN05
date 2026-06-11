from __future__ import annotations

import pandas as pd

import core_python.notify.detector as detector
from core_python.strategies.knn_combo.config import normalize_params
from core_python.strategies.knn_combo.levels import add_knn_combo_levels
from core_python.strategies.knn_combo.payload import build_knn_combo_payload
from core_python.strategies.knn_combo.signals import (
    detect_knn_combo_signals,
    merge_trend_into_entry,
)


def _probe_entry_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "bartime": pd.to_datetime(["2026-01-03 00:00:00", "2026-01-03 01:00:00"]),
            "open": [10.0, 10.0],
            "high": [12.0, 11.0],
            "low": [9.0, 8.0],
            "close": [11.0, 9.0],
            "ma": [10.0, 10.0],
            "prev_close": [9.0, 11.0],
            "prev_ma": [10.0, 10.0],
            "macd_h": [1.0, -1.0],
            "atr": [2.0, 2.0],
        }
    )


def test_knn_combo_defaults_keep_atr_period_5() -> None:
    params = normalize_params({}, "US30")

    assert params["ATR_PERIOD"] == 5
    assert params["TREND_TF"] == "H3"
    assert params["ENTRY_TF"] == "H1"


def test_knn_combo_merge_uses_only_closed_trend_bars() -> None:
    entry = pd.DataFrame(
        {
            "bartime": pd.to_datetime(
                [
                    "2026-01-03 08:59:00",
                    "2026-01-03 09:00:00",
                    "2026-01-03 11:59:00",
                    "2026-01-03 12:00:00",
                ]
            )
        }
    )
    trend = pd.DataFrame(
        {
            "bartime": pd.to_datetime(["2026-01-03 06:00:00", "2026-01-03 09:00:00"]),
            "trend_close_time": pd.to_datetime(["2026-01-03 09:00:00", "2026-01-03 12:00:00"]),
            "close": [100.0, 90.0],
            "ai_knn": [101.0, 89.0],
            "ai_avg": [100.0, 90.0],
            "ai_direction": [1, -1],
            "trend_bias": [1, -1],
            "trend_bias_label": ["Bullish", "Bearish"],
            "trend_bias_segment": [1, 2],
        }
    )

    merged = merge_trend_into_entry(entry, trend)

    assert pd.isna(merged.loc[0, "trend_bias"])
    assert int(merged.loc[1, "trend_bias"]) == 1
    assert int(merged.loc[2, "trend_bias"]) == 1
    assert int(merged.loc[3, "trend_bias"]) == -1
    valid = merged["trend_close_time"].notna()
    assert (merged.loc[valid, "bartime"] >= merged.loc[valid, "trend_close_time"]).all()


def test_knn_combo_filter_allows_only_signals_aligned_with_knn_bias() -> None:
    params = normalize_params({"NEUTRAL_BLOCK": True}, "US30")
    frame = _probe_entry_frame()
    frame["trend_bias"] = [1, 1]
    frame["trend_bias_label"] = ["Bullish", "Bullish"]

    out = detect_knn_combo_signals(frame, symbol="US30", params=params)

    assert out["raw_signal"].tolist() == [1, -1]
    assert out["signal"].tolist() == [1, 0]
    assert out.loc[1, "filter_reason"] == "H3 KNN bullish trend blocks SELL"
    assert "H3 KNN trend: Bullish" in out.loc[0, "signal_reason"]


def test_knn_combo_first_qualifying_entry_after_bullish_trend_can_signal_without_ma_cross() -> None:
    params = normalize_params({"NEUTRAL_BLOCK": True}, "US30")
    frame = pd.DataFrame(
        {
            "bartime": pd.to_datetime(
                [
                    "2026-01-03 09:00:00",
                    "2026-01-03 10:00:00",
                    "2026-01-03 11:00:00",
                    "2026-01-03 12:00:00",
                ]
            ),
            "open": [8.0, 11.0, 12.0, 10.0],
            "high": [9.5, 12.5, 13.5, 12.0],
            "low": [7.5, 10.5, 11.5, 9.5],
            "close": [9.0, 12.0, 13.0, 11.0],
            "ma": [10.0, 10.0, 10.0, 10.0],
            "prev_close": [11.0, 11.0, 12.0, 9.0],
            "prev_ma": [10.0, 10.0, 10.0, 10.0],
            "macd_h": [1.0, 1.0, 1.0, 1.0],
            "atr": [2.0, 2.0, 2.0, 2.0],
            "trend_close_time": pd.to_datetime(
                ["2026-01-03 09:00:00"] * 4
            ),
            "trend_bias": [1, 1, 1, 1],
            "trend_bias_label": ["Bullish", "Bullish", "Bullish", "Bullish"],
            "trend_bias_changed": [True, True, True, True],
            "trend_bias_segment": [2, 2, 2, 2],
        }
    )

    out = detect_knn_combo_signals(frame, symbol="US30", params=params)

    assert out["raw_signal"].tolist() == [0, 1, 0, 1]
    assert out["signal"].tolist() == [0, 1, 0, 1]
    assert out.loc[0, "signal_reason"] == ""
    assert "first entry bar after KNN bullish trend" in out.loc[1, "signal_reason"]
    assert out.loc[2, "signal_reason"] == ""
    assert "close crossed above MA" in out.loc[3, "signal_reason"]


def test_knn_combo_first_qualifying_entry_after_bearish_trend_can_signal_without_ma_cross() -> None:
    params = normalize_params({"NEUTRAL_BLOCK": True}, "US30")
    frame = pd.DataFrame(
        {
            "bartime": pd.to_datetime(
                [
                    "2026-01-03 09:00:00",
                    "2026-01-03 10:00:00",
                    "2026-01-03 11:00:00",
                    "2026-01-03 12:00:00",
                ]
            ),
            "open": [12.0, 9.0, 8.0, 10.0],
            "high": [13.0, 9.5, 8.5, 10.5],
            "low": [10.5, 7.5, 6.5, 7.5],
            "close": [11.0, 8.0, 7.0, 8.0],
            "ma": [10.0, 10.0, 10.0, 10.0],
            "prev_close": [9.0, 9.0, 8.0, 11.0],
            "prev_ma": [10.0, 10.0, 10.0, 10.0],
            "macd_h": [-1.0, -1.0, -1.0, -1.0],
            "atr": [2.0, 2.0, 2.0, 2.0],
            "trend_close_time": pd.to_datetime(
                ["2026-01-03 09:00:00"] * 4
            ),
            "trend_bias": [-1, -1, -1, -1],
            "trend_bias_label": ["Bearish", "Bearish", "Bearish", "Bearish"],
            "trend_bias_changed": [True, True, True, True],
            "trend_bias_segment": [2, 2, 2, 2],
        }
    )

    out = detect_knn_combo_signals(frame, symbol="US30", params=params)

    assert out["raw_signal"].tolist() == [0, -1, 0, -1]
    assert out["signal"].tolist() == [0, -1, 0, -1]
    assert out.loc[0, "signal_reason"] == ""
    assert "first entry bar after KNN bearish trend" in out.loc[1, "signal_reason"]
    assert out.loc[2, "signal_reason"] == ""
    assert "close crossed below MA" in out.loc[3, "signal_reason"]


def test_knn_combo_neutral_block_is_configurable() -> None:
    frame = _probe_entry_frame()
    frame["trend_bias"] = [0, 0]
    frame["trend_bias_label"] = ["Neutral", "Neutral"]

    blocked = detect_knn_combo_signals(
        frame,
        symbol="US30",
        params=normalize_params({"NEUTRAL_BLOCK": True}, "US30"),
    )
    allowed = detect_knn_combo_signals(
        frame,
        symbol="US30",
        params=normalize_params({"NEUTRAL_BLOCK": False}, "US30"),
    )

    assert blocked["signal"].tolist() == [0, 0]
    assert blocked["filter_reason"].tolist() == ["H3 KNN trend neutral", "H3 KNN trend neutral"]
    assert allowed["signal"].tolist() == [1, -1]


def test_knn_combo_levels_are_empty_until_trade_model_is_defined() -> None:
    frame = pd.DataFrame(
        {
            "bartime": [pd.Timestamp("2026-01-03 00:00:00")],
            "signal": [1],
        }
    )

    out = add_knn_combo_levels(frame, {}, "US30")

    assert pd.isna(out.loc[0, "entry_time"])
    assert pd.isna(out.loc[0, "entry_price"])
    assert pd.isna(out.loc[0, "sl_price"])
    assert pd.isna(out.loc[0, "tp_price"])
    assert pd.isna(out.loc[0, "risk_reward"])


def test_knn_combo_payload_omits_level_segments_and_reports_filtered_count() -> None:
    params = normalize_params({}, "US30")
    trend = pd.DataFrame(
        {
            "bartime": pd.to_datetime(["2026-01-03 06:00:00"]),
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "ai_knn": [101.0],
            "ai_avg": [100.0],
            "trend_bias": [1],
        }
    )
    entry = _probe_entry_frame()
    entry["trend_bartime"] = pd.Timestamp("2026-01-03 06:00:00")
    entry["trend_close_time"] = pd.Timestamp("2026-01-03 09:00:00")
    entry["trend_bias"] = [1, 1]
    entry["trend_bias_label"] = ["Bullish", "Bullish"]
    entry["trend_ai_knn"] = [101.0, 101.0]
    entry["trend_ai_avg"] = [100.0, 100.0]
    entry = detect_knn_combo_signals(entry, symbol="US30", params=params)
    entry = add_knn_combo_levels(entry, params, "US30")

    payload = build_knn_combo_payload(
        trend,
        entry,
        strategy="knn_combo",
        strategy_label="KNN Combo",
        symbol="US30",
        params=params,
    )

    assert payload["layout"] == "knn_combo_mtf"
    assert payload["charts"]["entry"]["segments"] == []
    assert payload["stats"]["total"] == 1
    assert payload["stats"]["filteredTotal"] == 1
    assert "entry" not in payload["signals"][0]


def test_knn_combo_watcher_path_loads_trend_and_entry_frames(monkeypatch) -> None:
    calls: list[tuple[str, str, int]] = []

    def fake_load(symbol: str, tf: str, bars: int) -> pd.DataFrame:
        calls.append((symbol, tf, bars))
        return pd.DataFrame(
            {
                "bartime": pd.to_datetime(["2026-01-03 00:00:00"]),
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.5],
            }
        )

    def fake_build(
        trend_df: pd.DataFrame,
        entry_df: pd.DataFrame,
        params: dict,
        symbol: str | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        assert symbol == "US30"
        assert params["TREND_TF"] == "H3"
        assert params["ENTRY_TF"] == "M30"
        enriched = entry_df.copy()
        enriched["signal"] = 1
        enriched["raw_signal"] = 1
        enriched["signal_reason"] = "test signal"
        return trend_df.copy(), enriched

    monkeypatch.setattr(detector, "_load_ohlcv", fake_load)
    monkeypatch.setattr(detector, "build_knn_combo_strategy_frames", fake_build)

    frame, spec, params = detector.run_strategy_frame(
        strategy="knn_combo",
        symbol="US30",
        tf="M30",
        bars=77,
        overrides={"TREND_BARS": 123},
        closed_only=False,
    )

    assert spec.key == "knn_combo"
    assert params["ENTRY_TF"] == "M30"
    assert params["ENTRY_BARS"] == 77
    assert frame["signal"].tolist() == [1]
    assert calls == [("US30", "H3", 123), ("US30", "M30", 77)]
