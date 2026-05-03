"""Config and adjustable visual-chart parameters for the MA Cross strategy."""

from __future__ import annotations

from typing import Any


MA_TYPE = "sma"
FAST_MA = 10
SLOW_MA = 20
ATR_PERIOD = 14
ATR_STOP_MULT = 2.0
ATR_TP_MULT = 2.0
SPREAD_PTS = 0.0
SLIPPAGE_PTS = 0.0
SESSION_HOURS_UTC: list[int] = []
ENTRY_LINE_BARS = 20
SHOW_MA = True
SHOW_ATR = True
SHOW_LEVELS = True

DEFAULT_PARAMS: dict[str, Any] = {
    "MA_TYPE": MA_TYPE,
    "FAST_MA": FAST_MA,
    "SLOW_MA": SLOW_MA,
    "ATR_PERIOD": ATR_PERIOD,
    "ATR_STOP_MULT": ATR_STOP_MULT,
    "ATR_TP_MULT": ATR_TP_MULT,
    "SPREAD_PTS": SPREAD_PTS,
    "SLIPPAGE_PTS": SLIPPAGE_PTS,
    "SESSION_HOURS_UTC": [],
    "ENTRY_LINE_BARS": ENTRY_LINE_BARS,
    "SHOW_MA": SHOW_MA,
    "SHOW_ATR": SHOW_ATR,
    "SHOW_LEVELS": SHOW_LEVELS,
}

PARAM_FIELDS: list[dict[str, Any]] = [
    {"key": "MA_TYPE", "label": "MA Type", "type": "select", "options": ["sma", "ema"]},
    {"key": "FAST_MA", "label": "Fast MA", "type": "number", "min": 1, "max": 300, "step": 1},
    {"key": "SLOW_MA", "label": "Slow MA", "type": "number", "min": 2, "max": 500, "step": 1},
    {"key": "ATR_PERIOD", "label": "ATR", "type": "number", "min": 2, "max": 300, "step": 1},
    {"key": "ATR_STOP_MULT", "label": "SL ATR", "type": "number", "min": 0.1, "max": 20, "step": 0.1},
    {"key": "ATR_TP_MULT", "label": "TP ATR", "type": "number", "min": 0, "max": 20, "step": 0.1},
    {"key": "SPREAD_PTS", "label": "Spread", "type": "number", "min": 0, "max": 1_000_000, "step": 0.01},
    {"key": "SLIPPAGE_PTS", "label": "Slippage", "type": "number", "min": 0, "max": 1_000_000, "step": 0.01},
    {"key": "SESSION_HOURS_UTC", "label": "UTC Hours", "type": "text"},
    {"key": "ENTRY_LINE_BARS", "label": "Level Bars", "type": "number", "min": 1, "max": 300, "step": 1},
    {"key": "SHOW_MA", "label": "Show MA", "type": "bool"},
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
    """Return default indicator parameters consumed by MA Cross signals."""
    return {
        "MA_TYPE": MA_TYPE,
        "FAST_MA": FAST_MA,
        "SLOW_MA": SLOW_MA,
        "ATR_PERIOD": ATR_PERIOD,
    }


def normalize_params(overrides: dict[str, Any] | None = None, symbol: str | None = None) -> dict[str, Any]:
    """Merge defaults and user overrides into validated MA Cross params."""
    _ = symbol
    raw = {**DEFAULT_PARAMS, **(overrides or {})}
    ma_type = str(raw.get("MA_TYPE", MA_TYPE)).lower().strip()
    if ma_type not in {"sma", "ema"}:
        raise ValueError("MA_TYPE must be 'sma' or 'ema'")

    fast = _to_int(raw.get("FAST_MA"), FAST_MA, 1, 300)
    slow = _to_int(raw.get("SLOW_MA"), SLOW_MA, 2, 500)
    if fast >= slow:
        raise ValueError("FAST_MA must be smaller than SLOW_MA")

    return {
        "MA_TYPE": ma_type,
        "FAST_MA": fast,
        "SLOW_MA": slow,
        "ATR_PERIOD": _to_int(raw.get("ATR_PERIOD"), ATR_PERIOD, 2, 300),
        "ATR_STOP_MULT": _to_float(raw.get("ATR_STOP_MULT"), ATR_STOP_MULT, 0.1, 20.0),
        "ATR_TP_MULT": _to_float(raw.get("ATR_TP_MULT"), ATR_TP_MULT, 0.0, 20.0),
        "SPREAD_PTS": _to_float(raw.get("SPREAD_PTS"), SPREAD_PTS, 0.0, 1_000_000.0),
        "SLIPPAGE_PTS": _to_float(raw.get("SLIPPAGE_PTS"), SLIPPAGE_PTS, 0.0, 1_000_000.0),
        "SESSION_HOURS_UTC": _parse_session_hours(raw.get("SESSION_HOURS_UTC"), SESSION_HOURS_UTC),
        "ENTRY_LINE_BARS": _to_int(raw.get("ENTRY_LINE_BARS"), ENTRY_LINE_BARS, 1, 300),
        "SHOW_MA": _to_bool(raw.get("SHOW_MA"), SHOW_MA),
        "SHOW_ATR": _to_bool(raw.get("SHOW_ATR"), SHOW_ATR),
        "SHOW_LEVELS": _to_bool(raw.get("SHOW_LEVELS"), SHOW_LEVELS),
    }
