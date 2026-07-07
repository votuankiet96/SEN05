"""Configuration and validation for the KNN Combo strategy.

KNN Combo is a standalone strategy that combines a higher-timeframe KNN trend
gate with lower-timeframe Combo-style raw signals. It does not calculate
entry, stop-loss, take-profit, position sizing, or execution instructions.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from og_core.config import TF_MINUTES

SYMBOL = "US30"
TREND_TF = "H3"
ENTRY_TF = "H1"
TREND_TF_OPTIONS = ("H3", "H4", "H6", "H8", "D1", "W")
ENTRY_TF_OPTIONS = ("H1", "M45", "M30")

TREND_BARS = 400
ENTRY_BARS = 1200

PRICE_VALUE = "hl2"
TARGET_VALUE = "Price Action"
AI_MA_LEN = 5
AI_TARGET_LEN = 5
AI_K = 3
AI_SMOOTH = 50

MA_PERIOD = 20
MACD_FAST = 5
MACD_SLOW = 25
MACD_SIGNAL = 5
ATR_PERIOD = 5
SESSION_HOURS_UTC: list[int] = []

NEUTRAL_BLOCK = True
SHOW_TREND_KNN = True
SHOW_MA = True
SHOW_MACD = True
SHOW_ATR = True
SHOW_FILTERED_SIGNALS = True

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
    "MA_PERIOD": MA_PERIOD,
    "MACD_FAST": MACD_FAST,
    "MACD_SLOW": MACD_SLOW,
    "MACD_SIGNAL": MACD_SIGNAL,
    "ATR_PERIOD": ATR_PERIOD,
    "SESSION_HOURS_UTC": SESSION_HOURS_UTC,
    "NEUTRAL_BLOCK": NEUTRAL_BLOCK,
    "SHOW_TREND_KNN": SHOW_TREND_KNN,
    "SHOW_MA": SHOW_MA,
    "SHOW_MACD": SHOW_MACD,
    "SHOW_ATR": SHOW_ATR,
    "SHOW_FILTERED_SIGNALS": SHOW_FILTERED_SIGNALS,
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
    {"key": "MA_PERIOD", "label": "MA", "type": "number", "min": 2, "max": 500, "step": 1},
    {"key": "MACD_FAST", "label": "MACD Fast", "type": "number", "min": 1, "max": 200, "step": 1},
    {"key": "MACD_SLOW", "label": "MACD Slow", "type": "number", "min": 2, "max": 300, "step": 1},
    {"key": "MACD_SIGNAL", "label": "MACD Signal", "type": "number", "min": 1, "max": 200, "step": 1},
    {"key": "ATR_PERIOD", "label": "ATR", "type": "number", "min": 2, "max": 200, "step": 1},
    {"key": "SESSION_HOURS_UTC", "label": "UTC Hours", "type": "text"},
    {"key": "NEUTRAL_BLOCK", "label": "Block Neutral KNN", "type": "bool"},
    {"key": "SHOW_TREND_KNN", "label": "Show Trend KNN", "type": "bool"},
    {"key": "SHOW_MA", "label": "Show MA", "type": "bool"},
    {"key": "SHOW_MACD", "label": "Show MACD", "type": "bool"},
    {"key": "SHOW_ATR", "label": "Show ATR", "type": "bool"},
    {"key": "SHOW_FILTERED_SIGNALS", "label": "Show Filtered Raw", "type": "bool"},
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


def _to_choice(value: object, default: str, allowed: Sequence[str]) -> str:
    candidate = str(value or default).strip().upper()
    allowed_values = tuple(str(item).upper() for item in allowed)
    if candidate not in set(allowed_values):
        raise ValueError(f"Unsupported timeframe '{candidate}'. Allowed: {', '.join(allowed_values)}")
    if candidate not in TF_MINUTES:
        raise ValueError(f"Unsupported timeframe '{candidate}'.")
    return candidate


def _parse_session_hours(value: object, default: list[int]) -> list[int]:
    if value is None:
        return list(default)
    if isinstance(value, (list, tuple, set)):
        parts = value
    else:
        raw = str(value).strip()
        if raw == "":
            return []
        parts = raw.replace(";", ",").replace(" ", ",").split(",")

    hours: set[int] = set()
    for part in parts:
        text = str(part).strip()
        if not text:
            continue
        hour = int(text)
        if hour < 0 or hour > 23:
            raise ValueError("SESSION_HOURS_UTC must contain UTC hours in range 0..23")
        hours.add(hour)
    return sorted(hours)


def timeframe_minutes(tf: object) -> int:
    code = str(tf or "").strip().upper()
    if code not in TF_MINUTES:
        raise ValueError(f"Unsupported timeframe '{code}'.")
    return int(TF_MINUTES[code])


def normalize_params(overrides: dict[str, Any] | None = None, symbol: str | None = None) -> dict[str, Any]:
    raw = {**DEFAULT_PARAMS, **(overrides or {})}
    selected_symbol = str(symbol or raw.get("SYMBOL") or SYMBOL).strip().upper()

    macd_fast = _to_int(raw.get("MACD_FAST"), MACD_FAST, 1, 200)
    macd_slow = _to_int(raw.get("MACD_SLOW"), MACD_SLOW, 2, 300)
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
        "MA_PERIOD": _to_int(raw.get("MA_PERIOD"), MA_PERIOD, 2, 500),
        "MACD_FAST": macd_fast,
        "MACD_SLOW": macd_slow,
        "MACD_SIGNAL": _to_int(raw.get("MACD_SIGNAL"), MACD_SIGNAL, 1, 200),
        "ATR_PERIOD": _to_int(raw.get("ATR_PERIOD"), ATR_PERIOD, 2, 200),
        "SESSION_HOURS_UTC": _parse_session_hours(raw.get("SESSION_HOURS_UTC"), SESSION_HOURS_UTC),
        "NEUTRAL_BLOCK": _to_bool(raw.get("NEUTRAL_BLOCK"), NEUTRAL_BLOCK),
        "SHOW_TREND_KNN": _to_bool(raw.get("SHOW_TREND_KNN"), SHOW_TREND_KNN),
        "SHOW_MA": _to_bool(raw.get("SHOW_MA"), SHOW_MA),
        "SHOW_MACD": _to_bool(raw.get("SHOW_MACD"), SHOW_MACD),
        "SHOW_ATR": _to_bool(raw.get("SHOW_ATR"), SHOW_ATR),
        "SHOW_FILTERED_SIGNALS": _to_bool(raw.get("SHOW_FILTERED_SIGNALS"), SHOW_FILTERED_SIGNALS),
    }

