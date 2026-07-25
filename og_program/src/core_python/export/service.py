"""Dashboard CSV export service.

Builds signal-only CSV payloads from the shared strategy engine for the
dashboard's manual export routes (/api/export, /api/export/bulk), without
putting export loops inside Flask route handlers.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Any

import pandas as pd

from core_python.util import config
from core_python.engine import DateWindow, run_strategy_request
from core_python.strategies.registry import get_strategy


@dataclass(frozen=True)
class CsvExport:
    data: str
    filename: str


EXPORT_CACHE_MAX_ITEMS = 32
EXPORT_CACHE_TTL_SECONDS = 120
_EXPORT_CACHE: OrderedDict[tuple[Any, ...], tuple[float, CsvExport]] = OrderedDict()


def build_single_export(
    strategy_key: str,
    *,
    symbol: str,
    tf: str,
    bars: int,
    args: Mapping[str, Any],
    date_window: DateWindow | None,
) -> CsvExport:
    """Build one signal-only CSV download for the selected strategy run."""

    cache_key = _export_cache_key("single", strategy_key, symbol, tf, bars, args, date_window)
    cached = _export_cache_get(cache_key)
    if cached is not None:
        return cached

    result = run_strategy_request(
        strategy_key,
        symbol=symbol,
        tf=tf,
        bars=bars,
        args=args,
        date_window=date_window,
    )
    rows = signal_export_rows(result.enriched)
    requested = parse_csv_list(args.get("cols"))
    csv_data = single_export_csv(rows, requested)
    filename = f"{result.strategy}_{result.symbol}_{result.tf}_{export_range_suffix(date_window, bars)}_signals.csv"
    export = CsvExport(data=csv_data, filename=filename)
    _export_cache_put(cache_key, export)
    return export


def build_bulk_export(
    strategy_key: str,
    *,
    tf: str,
    bars: int,
    args: Mapping[str, Any],
    date_window: DateWindow | None,
) -> CsvExport:
    """Build a signal-only CSV across multiple symbols for one strategy/TF."""

    cache_key = _export_cache_key("bulk", strategy_key, "", tf, bars, args, date_window)
    cached = _export_cache_get(cache_key)
    if cached is not None:
        return cached

    spec = get_strategy(strategy_key)
    tf_code = str(tf or config.DEFAULT_TF).strip().upper()
    if tf_code not in config.TF_MINUTES:
        raise ValueError(f"Unsupported timeframe '{tf_code}'.")

    symbols = parse_symbol_list(args.get("symbols") or args.get("symbol"))
    if not symbols:
        raise ValueError("Bulk export requires at least one symbol.")
    for symbol in symbols:
        config.get_symbol(symbol)

    requested_cols = parse_csv_list(args.get("cols"))
    requested_cols = [col for col in requested_cols if col not in {"bartime", "symbol", "signal"}]

    frames: list[pd.DataFrame] = []
    for symbol in symbols:
        result = run_strategy_request(
            spec.key,
            symbol=symbol,
            tf=tf_code,
            bars=bars,
            args=dict(args),
            date_window=date_window,
        )
        rows = signal_export_rows(result.enriched)
        if rows.empty:
            continue

        out = pd.DataFrame(
            {
                "bartime": pd.to_datetime(rows["bartime"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": symbol,
                "signal": pd.to_numeric(rows.get("signal", 0), errors="coerce").fillna(0).astype(int),
            }
        )
        for col in requested_cols:
            if col in rows.columns:
                out[col] = rows[col].values
        frames.append(out)

    columns = ["bartime", "symbol", "signal", *requested_cols]
    if frames:
        result_df = pd.concat(frames, ignore_index=True)
        result_df = result_df.reindex(columns=[col for col in columns if col in result_df.columns])
        result_df = result_df.sort_values(["bartime", "symbol"], kind="stable")
    else:
        result_df = pd.DataFrame(columns=columns)

    export = CsvExport(
        data=result_df.to_csv(index=False),
        filename=f"{spec.key}_{tf_code}_{export_range_suffix(date_window, bars)}_bulk_signals.csv",
    )
    _export_cache_put(cache_key, export)
    return export


def signal_export_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "signal" not in df.columns:
        return df.iloc[0:0].copy()
    return df[df["signal"].fillna(0).astype(int).ne(0)].copy()


def single_export_csv(rows: pd.DataFrame, requested: list[str]) -> str:
    out: dict[str, Any] = {}
    for col in requested:
        if col == "bartime":
            out["bartime"] = pd.to_datetime(rows["bartime"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M")
        elif col == "side":
            signal = pd.to_numeric(rows.get("signal", 0), errors="coerce").fillna(0).astype(int)
            out["side"] = signal.map({1: "BUY", -1: "SELL"}).fillna("")
        elif col == "signal":
            out["signal"] = pd.to_numeric(rows.get("signal", 0), errors="coerce").fillna(0).astype(int)
        elif col in rows.columns:
            out[col] = rows[col].values
    return pd.DataFrame(out).to_csv(index=False)


def export_range_suffix(date_window: DateWindow | None, bars: int) -> str:
    if date_window is None:
        return f"latest_{int(bars)}bars"
    start = date_window.start.strftime("%Y%m%d")
    end = (date_window.end - pd.Timedelta(microseconds=1)).strftime("%Y%m%d")
    return f"{start}_{end}"


def _export_cache_key(
    kind: str,
    strategy_key: str,
    symbol: str,
    tf: str,
    bars: int,
    args: Mapping[str, Any],
    date_window: DateWindow | None,
) -> tuple[Any, ...]:
    window_key = None
    if date_window is not None:
        window_key = (
            date_window.start.isoformat(),
            date_window.end.isoformat(),
        )
    normalized_args = tuple(sorted((str(key), str(value)) for key, value in args.items()))
    return (
        kind,
        str(strategy_key).strip().lower(),
        str(symbol).strip().upper(),
        str(tf).strip().upper(),
        int(bars),
        window_key,
        normalized_args,
    )


def _export_cache_get(key: tuple[Any, ...]) -> CsvExport | None:
    cached = _EXPORT_CACHE.get(key)
    if cached is None:
        return None
    created_at, export = cached
    if monotonic() - created_at > EXPORT_CACHE_TTL_SECONDS:
        _EXPORT_CACHE.pop(key, None)
        return None
    _EXPORT_CACHE.move_to_end(key)
    return export


def _export_cache_put(key: tuple[Any, ...], export: CsvExport) -> None:
    _EXPORT_CACHE[key] = (monotonic(), export)
    _EXPORT_CACHE.move_to_end(key)
    while len(_EXPORT_CACHE) > EXPORT_CACHE_MAX_ITEMS:
        _EXPORT_CACHE.popitem(last=False)


def parse_csv_list(value: object) -> list[str]:
    if value is None:
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def parse_symbol_list(value: object) -> list[str]:
    seen: set[str] = set()
    symbols: list[str] = []
    for item in parse_csv_list(value):
        symbol = item.upper()
        if symbol and symbol not in seen:
            symbols.append(symbol)
            seen.add(symbol)
    return symbols
