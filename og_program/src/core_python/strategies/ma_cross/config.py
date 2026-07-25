"""Configuration and validation for the MA Cross strategy.

MA Cross uses SMA 13/34 crossovers confirmed by the Combo MACD Histogram
settings. ATR and a symbol-specific X buffer define the output trade levels.
"""

from __future__ import annotations

from typing import Any


# Indicator defaults.
FAST_MA = 13
SLOW_MA = 34
MACD_FAST = 5
MACD_SLOW = 25
MACD_SIGNAL = 5
ATR_PERIOD = 5

# Level defaults.
KTP = 2.0
KTP_MIN = 2.0
KTP_MAX = 2.6
ENTRY_LINE_BARS = 2

# MA Cross is intentionally limited to these execution timeframes.
SUPPORTED_TIMEFRAMES: tuple[str, ...] = ("M10", "M20", "M30", "M45")
RECOMMENDED_TIMEFRAMES: tuple[str, ...] = SUPPORTED_TIMEFRAMES
DEFAULT_TIMEFRAME = "M30"

# Dashboard display toggles.
SHOW_MA = True
SHOW_MACD = True
SHOW_ATR = True
SHOW_LEVELS = True

# X buffer by canonical SEN05 symbol. The seven index values come from the
# MA Cross specification. Remaining entries preserve the same symbol coverage
# as Combo; unlisted symbols resolve to X=0 and can still be overridden.
SYMBOL_X: dict[str, float] = {
    "US30": 15.0,
    "US500": 2.0,
    "US100": 5.0,
    "DE40": 8.0,
    "UK100": 5.0,
    "FR40": 5.0,
    "SP35": 5.0,
    "HK50": 20.0,
    "J225": 20.0,
    "GOLD": 0.5,
    "BTCUSD": 50.0,
}


DEFAULT_PARAMS: dict[str, Any] = {
    "FAST_MA": FAST_MA,
    "SLOW_MA": SLOW_MA,
    "MACD_FAST": MACD_FAST,
    "MACD_SLOW": MACD_SLOW,
    "MACD_SIGNAL": MACD_SIGNAL,
    "ATR_PERIOD": ATR_PERIOD,
    "X": None,
    "KTP": KTP,
    "ENTRY_LINE_BARS": ENTRY_LINE_BARS,
    "SHOW_MA": SHOW_MA,
    "SHOW_MACD": SHOW_MACD,
    "SHOW_ATR": SHOW_ATR,
    "SHOW_LEVELS": SHOW_LEVELS,
}


PARAM_FIELDS: list[dict[str, Any]] = [
    {"key": "FAST_MA", "label": "Fast SMA", "type": "number", "min": 1, "max": 300, "step": 1},
    {"key": "SLOW_MA", "label": "Slow SMA", "type": "number", "min": 2, "max": 500, "step": 1},
    {"key": "MACD_FAST", "label": "MACD Fast", "type": "number", "min": 1, "max": 200, "step": 1},
    {"key": "MACD_SLOW", "label": "MACD Slow", "type": "number", "min": 2, "max": 300, "step": 1},
    {"key": "MACD_SIGNAL", "label": "MACD Signal", "type": "number", "min": 1, "max": 200, "step": 1},
    {"key": "ATR_PERIOD", "label": "ATR", "type": "number", "min": 2, "max": 200, "step": 1},
    {"key": "X", "label": "X Buffer", "type": "number", "min": 0, "max": 1_000_000, "step": 0.01},
    {"key": "KTP", "label": "KTP", "type": "number", "min": KTP_MIN, "max": KTP_MAX, "step": 0.1},
    {"key": "ENTRY_LINE_BARS", "label": "Level Bars", "type": "number", "min": 1, "max": 300, "step": 1},
    {"key": "SHOW_MA", "label": "Show SMA", "type": "bool"},
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


def get_indicator_params() -> dict[str, int]:
    """Return defaults consumed by ``add_ma_cross_indicators``."""
    return {
        "FAST_MA": FAST_MA,
        "SLOW_MA": SLOW_MA,
        "MACD_FAST": MACD_FAST,
        "MACD_SLOW": MACD_SLOW,
        "MACD_SIGNAL": MACD_SIGNAL,
        "ATR_PERIOD": ATR_PERIOD,
    }


def get_symbol_params(symbol: str | None) -> dict[str, float]:
    """Return the MA Cross X default for one canonical symbol."""
    key = str(symbol or "").strip().upper()
    return {"x": float(SYMBOL_X.get(key, 0.0))}


def normalize_params(
    overrides: dict[str, Any] | None = None,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Merge defaults, symbol X and user overrides into validated parameters."""
    raw = {**DEFAULT_PARAMS, **(overrides or {})}
    symbol_params = get_symbol_params(symbol)

    fast_ma = _to_int(raw.get("FAST_MA"), FAST_MA, 1, 300)
    slow_ma = _to_int(raw.get("SLOW_MA"), SLOW_MA, 2, 500)
    if fast_ma >= slow_ma:
        raise ValueError("FAST_MA must be smaller than SLOW_MA")

    macd_fast = _to_int(raw.get("MACD_FAST"), MACD_FAST, 1, 200)
    macd_slow = _to_int(raw.get("MACD_SLOW"), MACD_SLOW, 2, 300)
    if macd_fast >= macd_slow:
        raise ValueError("MACD_FAST must be smaller than MACD_SLOW")

    return {
        "FAST_MA": fast_ma,
        "SLOW_MA": slow_ma,
        "MACD_FAST": macd_fast,
        "MACD_SLOW": macd_slow,
        "MACD_SIGNAL": _to_int(raw.get("MACD_SIGNAL"), MACD_SIGNAL, 1, 200),
        "ATR_PERIOD": _to_int(raw.get("ATR_PERIOD"), ATR_PERIOD, 2, 200),
        "X": _to_float(raw.get("X"), symbol_params["x"], 0.0, 1_000_000.0),
        "KTP": _to_float(raw.get("KTP"), KTP, KTP_MIN, KTP_MAX),
        "ENTRY_LINE_BARS": _to_int(raw.get("ENTRY_LINE_BARS"), ENTRY_LINE_BARS, 1, 300),
        "SHOW_MA": _to_bool(raw.get("SHOW_MA"), SHOW_MA),
        "SHOW_MACD": _to_bool(raw.get("SHOW_MACD"), SHOW_MACD),
        "SHOW_ATR": _to_bool(raw.get("SHOW_ATR"), SHOW_ATR),
        "SHOW_LEVELS": _to_bool(raw.get("SHOW_LEVELS"), SHOW_LEVELS),
    }
