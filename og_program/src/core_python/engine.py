"""Strategy runner for core_python: DataFrame in, SQL/date-window orchestration in.

This module owns both the source-agnostic single-timeframe strategy pipeline
(normalize -> indicators -> signals -> levels on an already-loaded OHLCV
DataFrame) and the SQL/date-window orchestration that loads bars from
DP6 for the dashboard and CSV export. Only Combo and MA Cross are
supported — both are single-timeframe strategies, so there is exactly one
execution path (no multi-timeframe branching).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd

from core_python.data.loader import load, load_range
from core_python.strategies.registry import StrategySpec, get_strategy

OHLCV_COLUMNS = ["bartime", "open", "high", "low", "close", "volume"]
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


def normalize_ohlcv_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Return a sorted, numeric OHLCV frame accepted by all single-frame strategies."""
    missing = [col for col in OHLCV_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"OHLCV frame missing columns: {missing}")
    out = df.loc[:, OHLCV_COLUMNS].copy()
    out["bartime"] = pd.to_datetime(out["bartime"], errors="coerce")
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["bartime", "open", "high", "low", "close"])
    out = out.drop_duplicates(subset=["bartime"], keep="last")
    return out.sort_values("bartime").reset_index(drop=True)


def empty_ohlcv_frame() -> pd.DataFrame:
    """Build an empty frame with the standard OHLCV columns."""
    return pd.DataFrame(
        {
            "bartime": pd.Series(dtype="datetime64[ns]"),
            "open": pd.Series(dtype="float64"),
            "high": pd.Series(dtype="float64"),
            "low": pd.Series(dtype="float64"),
            "close": pd.Series(dtype="float64"),
            "volume": pd.Series(dtype="float64"),
        }
    )


def validate_strategy_timeframe(spec: StrategySpec, tf: str) -> None:
    """Reject a timeframe outside a strategy's explicit execution set."""
    if spec.supported_timeframes and tf not in spec.supported_timeframes:
        allowed = ", ".join(spec.supported_timeframes)
        raise ValueError(
            f"Strategy '{spec.key}' supports only these timeframes: {allowed}."
        )


def run_single_frame_pipeline(
    spec: StrategySpec,
    bars: pd.DataFrame,
    params: dict[str, Any],
    symbol: str,
) -> pd.DataFrame:
    """Run normalize-independent indicator/signal/level stages on one frame."""
    with_indicators = spec.add_indicators(bars, params)
    with_signals = spec.detect_signals(with_indicators, symbol=symbol, params=params)
    return spec.add_levels(with_signals, params, symbol)


def run_strategy_on_bars(
    strategy_key: str,
    *,
    symbol: str,
    tf: str,
    bars: pd.DataFrame,
    params: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
) -> StrategyRunResult:
    """Run a strategy on an already-loaded OHLCV DataFrame (no SQL, no date window)."""
    spec = get_strategy(strategy_key)
    selected_symbol = str(symbol).strip().upper()
    selected_tf = str(tf).strip().upper()
    validate_strategy_timeframe(spec, selected_tf)
    resolved_params = params if params is not None else spec.normalize_params(overrides, selected_symbol)

    bars = normalize_ohlcv_frame(bars)
    if bars.empty:
        enriched = empty_ohlcv_frame()
        enriched["signal"] = pd.Series(dtype="int64")
        return StrategyRunResult(
            strategy=spec.key,
            strategy_label=spec.label,
            symbol=selected_symbol,
            tf=selected_tf,
            bars=0,
            params=resolved_params,
            enriched=enriched,
        )

    enriched = run_single_frame_pipeline(spec, bars, resolved_params, selected_symbol)
    return StrategyRunResult(
        strategy=spec.key,
        strategy_label=spec.label,
        symbol=selected_symbol,
        tf=selected_tf,
        bars=len(enriched),
        params=resolved_params,
        enriched=enriched,
    )


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
    """Run a strategy from request-like inputs (dashboard/CLI) and return the enriched frame."""
    spec = get_strategy(strategy_key)
    selected_symbol = str(symbol or "").strip().upper()
    selected_tf = str(tf or "").strip().upper()
    validate_strategy_timeframe(spec, selected_tf)
    request_args = dict(args or {})

    local_overrides = overrides
    if local_overrides is None:
        local_overrides = strategy_overrides(request_args, spec.param_fields)
    params = spec.normalize_params(local_overrides, selected_symbol)

    raw = load_request_frame(selected_symbol, selected_tf, bars, date_window, spec.key, params)
    core_result = run_strategy_on_bars(spec.key, symbol=selected_symbol, tf=selected_tf, bars=raw, params=params)
    enriched = trim_to_window(core_result.enriched, date_window)
    return StrategyRunResult(
        strategy=spec.key,
        strategy_label=spec.label,
        symbol=selected_symbol,
        tf=selected_tf,
        bars=len(enriched) if date_window else bars,
        params=params,
        enriched=enriched,
        date_window=date_window,
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
) -> pd.DataFrame:
    if date_window is None:
        return load(symbol, tf, bars)
    return load_range(
        symbol,
        tf,
        date_window.start,
        date_window.end,
        warmup_bars=range_warmup_bars(strategy_key, params),
        tail_bars=0,
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


def range_warmup_bars(strategy_key: str, params: dict[str, Any]) -> int:
    if strategy_key == "combo":
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
            param_int(params, "SLOW_MA")
            + param_int(params, "MACD_SLOW")
            + param_int(params, "MACD_SIGNAL")
            + param_int(params, "ATR_PERIOD")
            + 50,
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
