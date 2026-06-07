from __future__ import annotations

from pathlib import Path

import pandas as pd

from core_python.export.service import single_export_csv
from core_python.strategies.ai_trend.config import normalize_params
from core_python.strategies.ai_trend.payload import build_ai_trend_payload
from core_python.strategies.ai_trend.signals import prepare_entry_frame


ROOT = Path(__file__).resolve().parents[1]


def _entry_ohlcv(rows: int = 12) -> pd.DataFrame:
    close = pd.Series([100.0 + idx for idx in range(rows)])
    return pd.DataFrame(
        {
            "bartime": pd.date_range("2026-05-01", periods=rows, freq="45min"),
            "open": close - 0.25,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1000.0,
        }
    )


def test_ai_trend_defaults_to_entry_atr_period_5() -> None:
    params = normalize_params()

    assert params["ATR_PERIOD"] == 5
    assert params["SHOW_M45_ATR"] is True
    assert params["DOW_PIVOT_LEFT"] == 3
    assert params["DOW_PIVOT_RIGHT"] == 5
    assert params["DOW_MIN_ATR_MULT"] == 0.5


def test_ai_trend_entry_frame_calculates_atr_columns() -> None:
    params = normalize_params()

    out = prepare_entry_frame(_entry_ohlcv(20), params)

    assert {"atr", "atr5", "atr14"}.issubset(out.columns)
    assert out["atr5"].iloc[:4].isna().all()
    assert out["atr14"].iloc[:13].isna().all()
    assert out["atr5"].iloc[-1] > 0
    assert out["atr14"].iloc[-1] > 0
    pd.testing.assert_series_equal(out["atr"], out["atr5"], check_names=False)


def test_ai_trend_payload_includes_entry_atr_panel() -> None:
    params = normalize_params(
        {
            "SHOW_H3_KNN": False,
            "SHOW_M45_DOW": False,
            "SHOW_M45_MACD": False,
        }
    )
    entry = prepare_entry_frame(_entry_ohlcv(), params)
    entry["signal"] = 0

    payload = build_ai_trend_payload(
        pd.DataFrame(),
        entry,
        strategy="ai_trend",
        strategy_label="AI Trend",
        symbol="GOLD",
        params=params,
    )

    atr_panel = next(panel for panel in payload["charts"]["entry"]["panels"] if panel["key"] == "atr")
    assert atr_panel["label"] == "ATR 5"
    assert atr_panel["type"] == "line"
    assert atr_panel["data"]


def test_ai_trend_export_column_lists_use_signal_sl_dow_and_fixed_atr_defaults() -> None:
    app_js = (ROOT / "core_python" / "chart" / "static" / "app.js").read_text(encoding="utf-8")
    single_start = app_js.index("const EXPORT_COLUMNS")
    bulk_start = app_js.index("const BULK_EXPORT_COLUMNS")
    single_ai_start = app_js.index("  ai_trend: [", single_start, bulk_start)
    bulk_ai_start = app_js.index("  ai_trend: [", bulk_start)
    single_ai = app_js[single_ai_start : app_js.index("  knn_combo: [", single_ai_start)]
    bulk_ai = app_js[bulk_ai_start : app_js.index("  knn_combo: [", bulk_ai_start)]

    for expected in (
        '{ key: "bartime", label: "Bar Time", checked: true }',
        '{ key: "side", label: "Side", checked: false }',
        '{ key: "signal", label: "Signal", checked: true }',
        '{ key: "sl_dow", label: "SL_Dow(3,5,0.5)", checked: true }',
        '{ key: "atr", label: "ATR", checked: false }',
        '{ key: "atr5", label: "ATR5", checked: true }',
        '{ key: "atr14", label: "ATR14", checked: true }',
    ):
        assert expected in single_ai

    for expected in (
        '{ key: "sl_dow", label: "SL_Dow(3,5,0.5)", checked: true }',
        '{ key: "atr5", label: "ATR5", checked: true }',
        '{ key: "atr14", label: "ATR14", checked: true }',
        '{ key: "entry_price", label: "Entry", checked: false }',
        '{ key: "atr", label: "ATR", checked: false }',
    ):
        assert expected in bulk_ai


def test_ai_trend_default_export_columns_can_be_exported_to_csv() -> None:
    params = normalize_params()
    rows = prepare_entry_frame(_entry_ohlcv(20), params).tail(1).copy()
    rows["signal"] = 1
    rows["sl_dow"] = 99.0

    csv_data = single_export_csv(rows, ["bartime", "signal", "sl_dow", "atr5", "atr14"])
    lines = csv_data.strip().splitlines()

    assert lines[0] == "bartime,signal,sl_dow,atr5,atr14"
    assert len(lines) == 2
    values = lines[1].split(",")
    assert values[1:3] == ["1", "99.0"]
    assert values[3]
    assert values[4]
