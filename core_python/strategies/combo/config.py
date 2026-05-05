"""Config and adjustable visual-chart parameters for the Combo strategy."""

from __future__ import annotations

from typing import Any


MA_PERIOD = 20
MACD_FAST = 5
MACD_SLOW = 25
MACD_SIGNAL = 5
ATR_PERIOD = 5
KTP = 2.272
MIN_RR = None
ENTRY_LINE_BARS = 2
SHOW_MA = True
SHOW_MACD = True
SHOW_ATR = True
SHOW_LEVELS = True

SYMBOL_X = {
    "US30": 10.0,
    "US500": 1.0,
    "US100": 5.0,
    "DE40": 5.0,
    "UK100": 5.0,
    "FR40": 5.0,
    "SP35": 5.0,
    "HK50": 15.0,
    "J225": 15.0,
    "GOLD": 0.5,
    "BTCUSD": 50.0,
}

SESSION_HOURS_UTC = {symbol: [] for symbol in SYMBOL_X}

DEFAULT_PARAMS: dict[str, Any] = {
    "MA_PERIOD": MA_PERIOD,
    "MACD_FAST": MACD_FAST,
    "MACD_SLOW": MACD_SLOW,
    "MACD_SIGNAL": MACD_SIGNAL,
    "ATR_PERIOD": ATR_PERIOD,
    "KTP": KTP,
    "MIN_RR": MIN_RR,
    "X": None,
    "SESSION_HOURS_UTC": [],
    "ENTRY_LINE_BARS": 2,
    "SHOW_MA": SHOW_MA,
    "SHOW_MACD": SHOW_MACD,
    "SHOW_ATR": SHOW_ATR,
    "SHOW_LEVELS": SHOW_LEVELS,
}

PARAM_FIELDS: list[dict[str, Any]] = [
    {"key": "MA_PERIOD", "label": "MA", "type": "number", "min": 2, "max": 500, "step": 1},
    {"key": "MACD_FAST", "label": "MACD Fast", "type": "number", "min": 1, "max": 200, "step": 1},
    {"key": "MACD_SLOW", "label": "MACD Slow", "type": "number", "min": 2, "max": 300, "step": 1},
    {"key": "MACD_SIGNAL", "label": "MACD Signal", "type": "number", "min": 1, "max": 200, "step": 1},
    {"key": "ATR_PERIOD", "label": "ATR", "type": "number", "min": 2, "max": 200, "step": 1},
    {"key": "X", "label": "X Buffer", "type": "number", "min": 0, "max": 1_000_000, "step": 0.01},
    {"key": "KTP", "label": "KTP", "type": "number", "min": 0.1, "max": 20, "step": 0.001},
    {"key": "MIN_RR", "label": "Min RR", "type": "optional_number", "min": 0, "max": 20, "step": 0.1},
    {"key": "SESSION_HOURS_UTC", "label": "UTC Hours", "type": "text"},
    {"key": "ENTRY_LINE_BARS", "label": "Level Bars", "type": "number", "min": 1, "max": 300, "step": 1},
    {"key": "SHOW_MA", "label": "Show MA", "type": "bool"},
    {"key": "SHOW_MACD", "label": "Show MACD", "type": "bool"},
    {"key": "SHOW_ATR", "label": "Show ATR", "type": "bool"},
    {"key": "SHOW_LEVELS", "label": "Show Levels", "type": "bool"},
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


def _to_optional_float(
    value: object,
    default: float | None,
    min_value: float,
    max_value: float,
) -> float | None:
    if value is None:
        return default
    raw = str(value).strip().lower()
    if raw in {"", "none", "null", "off"}:
        return None
    return _to_float(value, float(default if default is not None else min_value), min_value, max_value)


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
        if str(part).strip() == "":
            continue
        hour = int(part)
        if hour < 0 or hour > 23:
            raise ValueError("SESSION_HOURS_UTC must contain UTC hours in range 0..23")
        hours.add(hour)
    return sorted(hours)


def get_indicator_params() -> dict:
    """Return default indicator parameters consumed by Combo signals."""
    return {
        "MA_PERIOD": MA_PERIOD,
        "MACD_FAST": MACD_FAST,
        "MACD_SLOW": MACD_SLOW,
        "MACD_SIGNAL": MACD_SIGNAL,
        "ATR_PERIOD": ATR_PERIOD,
        "KTP": KTP,
        "MIN_RR": MIN_RR,
    }


def get_symbol_params(symbol: str | None) -> dict:
    """Return signal-only per-symbol Combo parameters."""
    key = str(symbol or "").strip().upper()
    return {
        "x": float(SYMBOL_X.get(key, 0.0)),
        "session_hours_utc": list(SESSION_HOURS_UTC.get(key, [])),
    }


def normalize_params(overrides: dict[str, Any] | None = None, symbol: str | None = None) -> dict[str, Any]:
    """Merge defaults, symbol defaults, and user overrides into validated params."""
    raw = {**DEFAULT_PARAMS, **(overrides or {})}
    symbol_params = get_symbol_params(symbol)
    return {
        "MA_PERIOD": _to_int(raw.get("MA_PERIOD"), MA_PERIOD, 2, 500),
        "MACD_FAST": _to_int(raw.get("MACD_FAST"), MACD_FAST, 1, 200),
        "MACD_SLOW": _to_int(raw.get("MACD_SLOW"), MACD_SLOW, 2, 300),
        "MACD_SIGNAL": _to_int(raw.get("MACD_SIGNAL"), MACD_SIGNAL, 1, 200),
        "ATR_PERIOD": _to_int(raw.get("ATR_PERIOD"), ATR_PERIOD, 2, 200),
        "KTP": _to_float(raw.get("KTP"), KTP, 0.1, 20.0),
        "MIN_RR": _to_optional_float(raw.get("MIN_RR"), MIN_RR, 0.0, 20.0),
        "X": _to_float(raw.get("X"), symbol_params["x"], 0.0, 1_000_000.0),
        "SESSION_HOURS_UTC": _parse_session_hours(
            raw.get("SESSION_HOURS_UTC"),
            symbol_params["session_hours_utc"],
        ),
        "ENTRY_LINE_BARS": _to_int(raw.get("ENTRY_LINE_BARS"), ENTRY_LINE_BARS, 1, 300),
        "SHOW_MA": _to_bool(raw.get("SHOW_MA"), SHOW_MA),
        "SHOW_MACD": _to_bool(raw.get("SHOW_MACD"), SHOW_MACD),
        "SHOW_ATR": _to_bool(raw.get("SHOW_ATR"), SHOW_ATR),
        "SHOW_LEVELS": _to_bool(raw.get("SHOW_LEVELS"), SHOW_LEVELS),
    }
