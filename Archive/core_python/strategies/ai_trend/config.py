"""
Configuration and validation for the AI Trend signal strategy.

The strategy is display-first:
    - Trend TF uses AI Trend Navigator / KNN as the trend chart.
    - Entry TF uses EMA 13 and EMA 34 as the entry chart.
    - Entry is a market-order signal on the entry-timeframe close.
    - Stop-loss uses the nearest confirmed Dow swing.
    - Take-profit uses a configurable fixed R:R.
    - Entry signals are confirmed by entry-timeframe MACD histogram.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core_python.config import TF_MINUTES


SYMBOL = "GOLD"
TREND_TF = "H3"
ENTRY_TF = "M45"
TREND_TF_OPTIONS = ("H1", "H2", "H3", "H4")
ENTRY_TF_OPTIONS = ("M45", "M30", "M20", "M15", "M10", "M5")

TREND_BARS = 500
ENTRY_BARS = 2000

PRICE_VALUE = "hl2"
TARGET_VALUE = "Price Action"
AI_MA_LEN = 5
AI_TARGET_LEN = 5
AI_K = 3
AI_SMOOTH = 50

EMA_FAST = 13
EMA_SLOW = 34
ATR_PERIOD = 5
M45_CONTEXT_BARS = 45

SHOW_H3_KNN = True
SHOW_M45_EMA = True
SHOW_M45_DOW = True
SHOW_M45_DOW_LABELS = False
DOW_PIVOT_LEFT = 3
DOW_PIVOT_RIGHT = 5
DOW_MIN_ATR_MULT = 0.5
TP_RR = 1.5
ENTRY_LINE_BARS = 3
SHOW_LEVELS = True
MACD_FAST = 5
MACD_SLOW = 25
MACD_SIGNAL = 5
SHOW_M45_MACD = True
SHOW_M45_ATR = True

DEFAULT_PARAMS: dict[str, Any] = {
    "SYMBOL": SYMBOL,
    "TREND_TF": TREND_TF,
    "ENTRY_TF": ENTRY_TF,
    "TREND_BARS": TREND_BARS,
    "ENTRY_BARS": ENTRY_BARS,
    "PRICE_VALUE": PRICE_VALUE,
    "TARGET_VALUE": TARGET_VALUE,
    "AI_MA_LEN": AI_MA_LEN,
    "AI_TARGET_LEN": AI_TARGET_LEN,
    "AI_K": AI_K,
    "AI_SMOOTH": AI_SMOOTH,
    "EMA_FAST": EMA_FAST,
    "EMA_SLOW": EMA_SLOW,
    "ATR_PERIOD": ATR_PERIOD,
    "M45_CONTEXT_BARS": M45_CONTEXT_BARS,
    "SHOW_H3_KNN": SHOW_H3_KNN,
    "SHOW_M45_EMA": SHOW_M45_EMA,
    "SHOW_M45_DOW": SHOW_M45_DOW,
    "SHOW_M45_DOW_LABELS": SHOW_M45_DOW_LABELS,
    "DOW_PIVOT_LEFT": DOW_PIVOT_LEFT,
    "DOW_PIVOT_RIGHT": DOW_PIVOT_RIGHT,
    "DOW_MIN_ATR_MULT": DOW_MIN_ATR_MULT,
    "TP_RR": TP_RR,
    "ENTRY_LINE_BARS": ENTRY_LINE_BARS,
    "SHOW_LEVELS": SHOW_LEVELS,
    "MACD_FAST": MACD_FAST,
    "MACD_SLOW": MACD_SLOW,
    "MACD_SIGNAL": MACD_SIGNAL,
    "SHOW_M45_MACD": SHOW_M45_MACD,
    "SHOW_M45_ATR": SHOW_M45_ATR,
}


PARAM_FIELDS: list[dict[str, Any]] = [
    {"key": "TREND_TF", "label": "Trend TF", "type": "select", "options": list(TREND_TF_OPTIONS)},
    {"key": "ENTRY_TF", "label": "Entry TF", "type": "select", "options": list(ENTRY_TF_OPTIONS)},
    {"key": "TREND_BARS", "label": "Trend Bars", "type": "number", "min": 50, "max": 20000, "step": 50},
    {"key": "ENTRY_BARS", "label": "Entry Bars", "type": "number", "min": 50, "max": 20000, "step": 50},
    {"key": "AI_MA_LEN", "label": "KNN MA", "type": "number", "min": 2, "max": 200, "step": 1},
    {"key": "AI_TARGET_LEN", "label": "KNN Target", "type": "number", "min": 2, "max": 200, "step": 1},
    {"key": "AI_K", "label": "KNN K", "type": "number", "min": 2, "max": 200, "step": 1},
    {"key": "AI_SMOOTH", "label": "KNN Smooth", "type": "number", "min": 2, "max": 500, "step": 1},
    {"key": "EMA_FAST", "label": "EMA Fast", "type": "number", "min": 1, "max": 300, "step": 1},
    {"key": "EMA_SLOW", "label": "EMA Slow", "type": "number", "min": 2, "max": 500, "step": 1},
    {"key": "ATR_PERIOD", "label": "ATR", "type": "number", "min": 2, "max": 300, "step": 1},
    {"key": "M45_CONTEXT_BARS", "label": "Entry Context", "type": "number", "min": 6, "max": 200, "step": 1},
    {"key": "DOW_PIVOT_LEFT", "label": "Dow Left", "type": "number", "min": 1, "max": 20, "step": 1},
    {"key": "DOW_PIVOT_RIGHT", "label": "Dow Right", "type": "number", "min": 1, "max": 20, "step": 1},
    {"key": "DOW_MIN_ATR_MULT", "label": "Dow ATR Filter", "type": "number", "min": 0, "max": 10, "step": 0.1},
    {"key": "TP_RR", "label": "TP R:R", "type": "number", "min": 0.1, "max": 20, "step": 0.1},
    {"key": "SHOW_H3_KNN", "label": "Show Trend KNN", "type": "bool"},
    {"key": "SHOW_M45_EMA", "label": "Show Entry EMA", "type": "bool"},
    {"key": "SHOW_M45_ATR", "label": "Show Entry ATR", "type": "bool"},
    {"key": "SHOW_M45_DOW", "label": "Show Entry Dow", "type": "bool"},
    {"key": "SHOW_M45_DOW_LABELS", "label": "Show Dow Labels", "type": "bool"},
]


def _to_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value: object, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(float(value)) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(parsed, max_value))


def _to_float(value: object, default: float, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(parsed, max_value))


def timeframe_minutes(tf: object) -> int:
    candidate = str(tf or "").strip().upper()
    if candidate not in TF_MINUTES:
        raise ValueError(f"Unsupported timeframe '{candidate}'.")
    return int(TF_MINUTES[candidate])


def _to_choice(value: object, default: str, allowed: Sequence[str]) -> str:
    candidate = str(value or default).strip().upper()
    allowed_values = tuple(str(item).upper() for item in allowed)
    if candidate not in set(allowed_values):
        raise ValueError(f"Unsupported timeframe '{candidate}'. Allowed: {', '.join(allowed_values)}")
    return candidate


def normalize_params(overrides: dict[str, Any] | None = None, symbol: str | None = None) -> dict[str, Any]:
    raw = {**DEFAULT_PARAMS, **(overrides or {})}
    selected_symbol = str(symbol or raw.get("SYMBOL") or SYMBOL).strip().upper()

    fast = _to_int(raw.get("EMA_FAST"), EMA_FAST, 1, 300)
    slow = _to_int(raw.get("EMA_SLOW"), EMA_SLOW, 2, 500)
    if fast >= slow:
        raise ValueError("EMA_FAST must be smaller than EMA_SLOW")
    macd_fast = _to_int(raw.get("MACD_FAST"), MACD_FAST, 1, 300)
    macd_slow = _to_int(raw.get("MACD_SLOW"), MACD_SLOW, 2, 500)
    if macd_fast >= macd_slow:
        raise ValueError("MACD_FAST must be smaller than MACD_SLOW")

    return {
        "SYMBOL": selected_symbol,
        "TREND_TF": _to_choice(raw.get("TREND_TF"), TREND_TF, TREND_TF_OPTIONS),
        "ENTRY_TF": _to_choice(raw.get("ENTRY_TF"), ENTRY_TF, ENTRY_TF_OPTIONS),
        "TREND_BARS": _to_int(raw.get("TREND_BARS"), TREND_BARS, 50, 20000),
        "ENTRY_BARS": _to_int(raw.get("ENTRY_BARS"), ENTRY_BARS, 50, 20000),
        "PRICE_VALUE": str(raw.get("PRICE_VALUE") or PRICE_VALUE),
        "TARGET_VALUE": str(raw.get("TARGET_VALUE") or TARGET_VALUE),
        "AI_MA_LEN": _to_int(raw.get("AI_MA_LEN"), AI_MA_LEN, 2, 200),
        "AI_TARGET_LEN": _to_int(raw.get("AI_TARGET_LEN"), AI_TARGET_LEN, 2, 200),
        "AI_K": _to_int(raw.get("AI_K"), AI_K, 2, 200),
        "AI_SMOOTH": _to_int(raw.get("AI_SMOOTH"), AI_SMOOTH, 2, 500),
        "EMA_FAST": fast,
        "EMA_SLOW": slow,
        "ATR_PERIOD": _to_int(raw.get("ATR_PERIOD"), ATR_PERIOD, 2, 300),
        "M45_CONTEXT_BARS": _to_int(raw.get("M45_CONTEXT_BARS"), M45_CONTEXT_BARS, 6, 200),
        "DOW_PIVOT_LEFT": _to_int(raw.get("DOW_PIVOT_LEFT"), DOW_PIVOT_LEFT, 1, 20),
        "DOW_PIVOT_RIGHT": _to_int(raw.get("DOW_PIVOT_RIGHT"), DOW_PIVOT_RIGHT, 1, 20),
        "DOW_MIN_ATR_MULT": _to_float(raw.get("DOW_MIN_ATR_MULT"), DOW_MIN_ATR_MULT, 0, 10),
        "TP_RR": _to_float(raw.get("TP_RR"), TP_RR, 0.1, 20),
        "ENTRY_LINE_BARS": _to_int(raw.get("ENTRY_LINE_BARS"), ENTRY_LINE_BARS, 1, 300),
        "MACD_FAST": macd_fast,
        "MACD_SLOW": macd_slow,
        "MACD_SIGNAL": _to_int(raw.get("MACD_SIGNAL"), MACD_SIGNAL, 1, 300),
        "SHOW_H3_KNN": _to_bool(raw.get("SHOW_H3_KNN"), SHOW_H3_KNN),
        "SHOW_M45_EMA": _to_bool(raw.get("SHOW_M45_EMA"), SHOW_M45_EMA),
        "SHOW_M45_ATR": _to_bool(raw.get("SHOW_M45_ATR"), SHOW_M45_ATR),
        "SHOW_M45_DOW": _to_bool(raw.get("SHOW_M45_DOW"), SHOW_M45_DOW),
        "SHOW_M45_DOW_LABELS": _to_bool(raw.get("SHOW_M45_DOW_LABELS"), SHOW_M45_DOW_LABELS),
        "SHOW_LEVELS": _to_bool(raw.get("SHOW_LEVELS"), SHOW_LEVELS),
        "SHOW_M45_MACD": _to_bool(raw.get("SHOW_M45_MACD"), SHOW_M45_MACD),
    }
