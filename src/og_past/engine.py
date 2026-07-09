"""Historical strategy runner for dashboard and CSV export.

This module owns SQL/date-window orchestration for past-data workflows. The
source-agnostic single-frame strategy execution lives in og_core.engine.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd

from og_core import config
from og_core.engine import run_strategy_on_bars
from og_past.data.loader import load, load_range
from og_core.strategies.ai_trend.signals import build_ai_trend_frames
from og_core.strategies.combo.pipeline import build_combo_mtf_frames
from og_core.strategies.combo.trend_filter import drop_open_bar as drop_combo_open_bar
from og_core.strategies.knn_combo.pipeline import build_knn_combo_strategy_frames
from og_core.strategies.registry import get_strategy

DEFAULT_RANGE_WARMUP_BARS = 600


@dataclass(frozen=True)
class DateWindow:
    """UTC-naive half-open range used by scan/export requests."""

    start: pd.Timestamp
    end: pd.Timestamp
    start_label: str
    end_label: str


@dataclass(frozen=True)
class StrategyRunResult:
    """Computed strategy output ready for chart/export consumers."""

    strategy: str
    strategy_label: str
    symbol: str
    tf: str
    bars: int
    params: dict[str, Any]
    enriched: pd.DataFrame
    date_window: DateWindow | None = None
    layout: str = "single"
    trend_frame: pd.DataFrame | None = None
    trend_tf: str | None = None


def run_strategy_request(
    strategy_key: str,
    *,
    symbol: str,
    tf: str,
    bars: int,
    args: Mapping[str, Any] | None = None,
    date_window: DateWindow | None = None,
    overrides: dict[str, Any] | None = None,
) -> StrategyRunResult:
    """Run a strategy from request-like inputs and return enriched frames."""

    spec = get_strategy(strategy_key)
    selected_symbol = str(symbol or config.DEFAULT_SYMBOL).strip().upper()
    selected_tf = str(tf or config.DEFAULT_TF).strip().upper()
    request_args = dict(args or {})

    if spec.key == "ai_trend":
        return run_ai_trend_strategy(
            spec,
            request_args,
            selected_symbol,
            int(bars),
            date_window=date_window,
        )

    if spec.key == "knn_combo":
        return run_knn_combo_strategy(
            spec,
            request_args,
            selected_symbol,
            int(bars),
            date_window=date_window,
        )

    local_overrides = overrides
    if local_overrides is None:
        local_overrides = strategy_overrides(request_args, spec.param_fields)
    params = spec.normalize_params(local_overrides, selected_symbol)

    if spec.key == "combo" and params.get("HTF_TREND_ENABLED", False):
        return run_combo_mtf_strategy(
            spec,
            symbol=selected_symbol,
            tf=selected_tf,
            bars=int(bars),
            params=params,
            date_window=date_window,
        )

    return run_single_timeframe_strategy(
        spec,
        symbol=selected_symbol,
        tf=selected_tf,
        bars=int(bars),
        params=params,
        date_window=date_window,
    )


def run_single_timeframe_strategy(
    spec: Any,
    *,
    symbol: str,
    tf: str,
    bars: int,
    params: dict[str, Any],
    date_window: DateWindow | None = None,
) -> StrategyRunResult:
    """Run the generic single-timeframe strategy pipeline."""

    raw = load_request_frame(
        symbol,
        tf,
        bars,
        date_window,
        spec.key,
        params,
        role="entry",
        tail_bars=1,
    )
    core_result = run_strategy_on_bars(spec.key, symbol=symbol, tf=tf, bars=raw, params=params)
    enriched = trim_to_window(core_result.enriched, date_window)
    return StrategyRunResult(
        strategy=spec.key,
        strategy_label=spec.label,
        symbol=symbol,
        tf=tf,
        bars=len(enriched) if date_window else bars,
        params=params,
        enriched=enriched,
        date_window=date_window,
    )


def run_combo_mtf_strategy(
    spec: Any,
    *,
    symbol: str,
    tf: str,
    bars: int,
    params: dict[str, Any],
    date_window: DateWindow | None = None,
) -> StrategyRunResult:
    """Run Combo with a closed higher-timeframe trend frame."""

    entry_raw = load_request_frame(
        symbol,
        tf,
        bars,
        date_window,
        spec.key,
        params,
        role="entry",
        tail_bars=1,
    )
    trend_raw = load_request_frame(
        symbol,
        params["HTF_TF"],
        int(params["HTF_BARS"]),
        date_window,
        spec.key,
        params,
        role="trend",
    )
    trend_raw = drop_combo_open_bar(trend_raw, params["HTF_TF"])
    if trend_raw.empty:
        raise ValueError(f"{symbol} has no closed {params['HTF_TF']} data for Combo HTF trend.")
    if entry_raw.empty:
        raise ValueError(f"{symbol} has no {tf} data for Combo entry.")

    trend_full, enriched_full = build_combo_mtf_frames(trend_raw, entry_raw, params, symbol)
    trend_frame = trim_to_window(trend_full, date_window)
    enriched = trim_to_window(enriched_full, date_window)
    if date_window and enriched.empty:
        raise ValueError(f"{symbol} has no {tf} data in the selected date range.")

    return StrategyRunResult(
        strategy=spec.key,
        strategy_label=spec.label,
        symbol=symbol,
        tf=tf,
        bars=len(enriched) if date_window else bars,
        params=params,
        enriched=enriched,
        date_window=date_window,
        layout="combo_mtf",
        trend_frame=trend_frame,
        trend_tf=params["HTF_TF"],
    )


def run_ai_trend_strategy(
    spec: Any,
    args: Mapping[str, Any],
    symbol: str,
    bars: int,
    *,
    date_window: DateWindow | None = None,
) -> StrategyRunResult:
    """Run the AI Trend two-timeframe path."""

    overrides = strategy_overrides(dict(args), spec.param_fields)
    overrides.setdefault("SYMBOL", symbol or "GOLD")
    if to_bool(args.get("AUTO_ENTRY_BARS"), False):
        trend_bars = overrides.get("TREND_BARS", spec.default_params.get("TREND_BARS", bars))
        trend_tf = overrides.get("TREND_TF", spec.default_params.get("TREND_TF", "H3"))
        entry_tf = overrides.get("ENTRY_TF", spec.default_params.get("ENTRY_TF", "M45"))
        overrides["ENTRY_BARS"] = corresponding_bars(trend_bars, trend_tf, entry_tf)
    else:
        overrides.setdefault("ENTRY_BARS", bars)
    params = spec.normalize_params(overrides, symbol)

    trend_raw = load_request_frame(
        params["SYMBOL"],
        params["TREND_TF"],
        int(params["TREND_BARS"]),
        date_window,
        spec.key,
        params,
        role="trend",
    )
    entry_raw = load_request_frame(
        params["SYMBOL"],
        params["ENTRY_TF"],
        int(params["ENTRY_BARS"]),
        date_window,
        spec.key,
        params,
        role="entry",
        tail_bars=1,
    )
    if trend_raw.empty or entry_raw.empty:
        missing = []
        if trend_raw.empty:
            missing.append(params["TREND_TF"])
        if entry_raw.empty:
            missing.append(params["ENTRY_TF"])
        raise ValueError(
            f"AI Trend requires data for both {params['TREND_TF']} and {params['ENTRY_TF']}. "
            f"{params['SYMBOL']} has no data for: {', '.join(missing)}."
        )

    trend_full, entry_full = build_ai_trend_frames(trend_raw, entry_raw, params)
    enriched_full = spec.add_levels(entry_full, params, params["SYMBOL"])
    trend_frame = trim_to_window(trend_full, date_window)
    enriched = trim_to_window(enriched_full, date_window)
    if date_window and enriched.empty:
        raise ValueError(f"{params['SYMBOL']} has no {params['ENTRY_TF']} data in the selected date range.")

    return StrategyRunResult(
        strategy=spec.key,
        strategy_label=spec.label,
        symbol=params["SYMBOL"],
        tf=params["ENTRY_TF"],
        bars=len(enriched) if date_window else int(params["ENTRY_BARS"]),
        params=params,
        enriched=enriched,
        date_window=date_window,
        layout="ai_trend_mtf",
        trend_frame=trend_frame,
        trend_tf=params["TREND_TF"],
    )


def run_knn_combo_strategy(
    spec: Any,
    args: Mapping[str, Any],
    symbol: str,
    bars: int,
    *,
    date_window: DateWindow | None = None,
) -> StrategyRunResult:
    """Run the KNN Combo two-timeframe path."""

    overrides = strategy_overrides(dict(args), spec.param_fields)
    overrides.setdefault("SYMBOL", symbol or "US30")
    if to_bool(args.get("AUTO_ENTRY_BARS"), False):
        trend_bars = overrides.get("TREND_BARS", spec.default_params.get("TREND_BARS", bars))
        trend_tf = overrides.get("TREND_TF", spec.default_params.get("TREND_TF", "H3"))
        entry_tf = overrides.get("ENTRY_TF", spec.default_params.get("ENTRY_TF", "H1"))
        overrides["ENTRY_BARS"] = corresponding_bars(trend_bars, trend_tf, entry_tf)
    else:
        overrides.setdefault("ENTRY_BARS", bars)
    params = spec.normalize_params(overrides, symbol)

    trend_raw = load_request_frame(
        params["SYMBOL"],
        params["TREND_TF"],
        int(params["TREND_BARS"]),
        date_window,
        spec.key,
        params,
        role="trend",
    )
    entry_raw = load_request_frame(
        params["SYMBOL"],
        params["ENTRY_TF"],
        int(params["ENTRY_BARS"]),
        date_window,
        spec.key,
        params,
        role="entry",
        tail_bars=1,
    )
    if trend_raw.empty or entry_raw.empty:
        missing = []
        if trend_raw.empty:
            missing.append(params["TREND_TF"])
        if entry_raw.empty:
            missing.append(params["ENTRY_TF"])
        raise ValueError(
            f"KNN Combo requires data for both {params['TREND_TF']} and {params['ENTRY_TF']}. "
            f"{params['SYMBOL']} has no data for: {', '.join(missing)}."
        )

    trend_full, enriched_full = build_knn_combo_strategy_frames(trend_raw, entry_raw, params, params["SYMBOL"])
    trend_frame = trim_to_window(trend_full, date_window)
    enriched = trim_to_window(enriched_full, date_window)
    if date_window and enriched.empty:
        raise ValueError(f"{params['SYMBOL']} has no {params['ENTRY_TF']} data in the selected date range.")

    return StrategyRunResult(
        strategy=spec.key,
        strategy_label=spec.label,
        symbol=params["SYMBOL"],
        tf=params["ENTRY_TF"],
        bars=len(enriched) if date_window else int(params["ENTRY_BARS"]),
        params=params,
        enriched=enriched,
        date_window=date_window,
        layout="knn_combo_mtf",
        trend_frame=trend_frame,
        trend_tf=params["TREND_TF"],
    )


def date_window_from_args(args: Mapping[str, Any]) -> DateWindow | None:
    start_raw = args.get("start_date") or args.get("start")
    end_raw = args.get("end_date") or args.get("end")
    if not start_raw and not end_raw:
        return None
    if not start_raw or not end_raw:
        raise ValueError("Both start_date and end_date are required for date-range mode.")

    start = parse_range_bound(start_raw, "start_date", end_bound=False)
    end = parse_range_bound(end_raw, "end_date", end_bound=True)
    if end <= start:
        raise ValueError("end_date must be the same date as or later than start_date.")
    return DateWindow(
        start=start,
        end=end,
        start_label=pd.Timestamp(start).strftime("%d/%m/%Y"),
        end_label=(pd.Timestamp(end) - pd.Timedelta(microseconds=1)).strftime("%d/%m/%Y"),
    )


def parse_range_bound(value: object, label: str, *, end_bound: bool) -> pd.Timestamp:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required.")
    date_only = False
    parts = text.split("/")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        day, month, year = (int(part) for part in parts)
        if year < 1000:
            raise ValueError(f"Invalid {label}: {text}")
        try:
            ts = pd.Timestamp(year=year, month=month, day=day)
        except ValueError as exc:
            raise ValueError(f"Invalid {label}: {text}") from exc
        date_only = True
    else:
        date_only = len(text) == 10 and text[4] == "-" and text[7] == "-"
        try:
            ts = pd.Timestamp(text)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid {label}: {text}") from exc
    if pd.isna(ts):
        raise ValueError(f"Invalid {label}: {text}")
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    if end_bound and date_only:
        ts = ts + pd.Timedelta(days=1)
    return ts


def load_request_frame(
    symbol: str,
    tf: str,
    bars: int,
    date_window: DateWindow | None,
    strategy_key: str,
    params: dict[str, Any],
    *,
    role: str,
    tail_bars: int = 0,
) -> pd.DataFrame:
    if date_window is None:
        return load(symbol, tf, bars)
    return load_range(
        symbol,
        tf,
        date_window.start,
        date_window.end,
        warmup_bars=range_warmup_bars(strategy_key, params, role),
        tail_bars=tail_bars,
    )


def trim_to_window(
    df: pd.DataFrame,
    date_window: DateWindow | None,
    column: str = "bartime",
) -> pd.DataFrame:
    if date_window is None or df.empty or column not in df.columns:
        return df.copy()
    times = pd.to_datetime(df[column], errors="coerce")
    mask = times.ge(date_window.start) & times.lt(date_window.end)
    return df.loc[mask].reset_index(drop=True)


def attach_window_meta(payload: dict[str, Any], date_window: DateWindow | None) -> None:
    meta = payload.setdefault("meta", {})
    if date_window is None:
        meta["rangeMode"] = "bars"
        return
    meta["rangeMode"] = "date"
    meta["startDate"] = date_window.start_label
    meta["endDate"] = date_window.end_label
    meta["startTime"] = date_window.start.strftime("%Y-%m-%d %H:%M:%S")
    meta["endTimeExclusive"] = date_window.end.strftime("%Y-%m-%d %H:%M:%S")


def range_warmup_bars(strategy_key: str, params: dict[str, Any], role: str) -> int:
    if strategy_key == "ai_trend":
        if role == "trend":
            return max(
                DEFAULT_RANGE_WARMUP_BARS,
                param_int(params, "AI_SMOOTH") + param_int(params, "AI_TARGET_LEN") + 50,
            )
        return max(
            DEFAULT_RANGE_WARMUP_BARS,
            param_int(params, "EMA_SLOW")
            + param_int(params, "MACD_SLOW")
            + param_int(params, "MACD_SIGNAL")
            + param_int(params, "DOW_PIVOT_LEFT")
            + param_int(params, "DOW_PIVOT_RIGHT")
            + 50,
        )
    if strategy_key == "knn_combo":
        if role == "trend":
            return max(
                DEFAULT_RANGE_WARMUP_BARS,
                param_int(params, "AI_SMOOTH") + param_int(params, "AI_TARGET_LEN") + 50,
            )
        return max(
            DEFAULT_RANGE_WARMUP_BARS,
            param_int(params, "MA_PERIOD")
            + param_int(params, "MACD_SLOW")
            + param_int(params, "MACD_SIGNAL")
            + param_int(params, "ATR_PERIOD")
            + 50,
        )
    if strategy_key == "combo":
        if role == "trend":
            return max(
                DEFAULT_RANGE_WARMUP_BARS,
                param_int(params, "HTF_SLOW_EMA") + param_int(params, "HTF_SLOPE_LOOKBACK") + 50,
            )
        return max(
            DEFAULT_RANGE_WARMUP_BARS,
            param_int(params, "MA_PERIOD")
            + param_int(params, "MACD_SLOW")
            + param_int(params, "MACD_SIGNAL")
            + param_int(params, "ATR_PERIOD")
            + 50,
        )
    if strategy_key == "ma_cross":
        return max(
            DEFAULT_RANGE_WARMUP_BARS,
            param_int(params, "SLOW_MA") + param_int(params, "ATR_PERIOD") + param_int(params, "MACD_SIGNAL") + 50,
        )
    return DEFAULT_RANGE_WARMUP_BARS


def param_int(params: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(float(params.get(key, default)))
    except (TypeError, ValueError):
        return default


def to_int(value: object, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(float(value)) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(parsed, max_value))


def to_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def corresponding_bars(
    trend_bars: object,
    trend_tf: object,
    entry_tf: object,
    *,
    min_value: int = 50,
    max_value: int = 20000,
) -> int:
    trend_code = str(trend_tf or "").strip().upper()
    entry_code = str(entry_tf or "").strip().upper()
    if trend_code not in config.TF_MINUTES:
        raise ValueError(f"Unsupported timeframe '{trend_code}'.")
    if entry_code not in config.TF_MINUTES:
        raise ValueError(f"Unsupported timeframe '{entry_code}'.")
    base_bars = to_int(trend_bars, min_value, min_value, max_value)
    trend_minutes = int(config.TF_MINUTES[trend_code])
    entry_minutes = int(config.TF_MINUTES[entry_code])
    raw_bars = (base_bars * trend_minutes + entry_minutes - 1) // entry_minutes
    return max(min_value, min(int(raw_bars), max_value))


def strategy_overrides(args: Mapping[str, Any], fields: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract strategy-owned params from request args."""

    keys = {field["key"] for field in fields}
    lower_to_key = {key.lower(): key for key in keys}
    overrides: dict[str, Any] = {}
    for key, value in args.items():
        normalized = lower_to_key.get(str(key).lower())
        if normalized:
            overrides[normalized] = value
    return overrides
