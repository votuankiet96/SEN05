"""OHLCV adapters and alignment helpers."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Iterable

import pandas as pd

from backtest_optimize.contracts import Bar, SignalRow
from backtest_optimize.io.signal_loader import normalize_timestamp

OHLCV_COLUMNS = ["bartime", "open", "high", "low", "close", "volume"]
logger = logging.getLogger(__name__)


def normalize_ohlcv_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize OHLCV into UTC-naive sorted bars."""
    if df.empty:
        raise ValueError("OHLCV DataFrame is empty.")

    out = df.copy()
    out.columns = [str(col).strip().lower() for col in out.columns]

    missing = {"bartime", "open", "high", "low", "close"} - set(out.columns)
    if missing:
        raise ValueError(f"OHLCV DataFrame missing columns: {sorted(missing)}")

    if "volume" not in out.columns:
        out["volume"] = None

    out["bartime"] = out["bartime"].map(normalize_timestamp)
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out.dropna(subset=["bartime", "open", "high", "low", "close"])
    dup_count = int(out.duplicated(subset=["bartime"]).sum())
    if dup_count:
        logger.warning("Dropped %d duplicate OHLCV bars; kept last per bartime.", dup_count)
    out = out.sort_values("bartime").drop_duplicates(subset=["bartime"], keep="last")
    out = out.reset_index(drop=True)

    bad_range = (out["high"] < out[["open", "close", "low"]].max(axis=1)) | (
        out["low"] > out[["open", "close", "high"]].min(axis=1)
    )
    if bad_range.any():
        sample = out.loc[bad_range, ["bartime", "open", "high", "low", "close"]].head(5)
        raise ValueError(f"Invalid OHLC ranges detected: {sample.to_dict('records')}")

    return out[OHLCV_COLUMNS]


def load_ohlcv_from_core(
    symbol: str,
    timeframe: str,
    *,
    start: object | None = None,
    end: object | None = None,
    warmup_bars: int = 0,
    tail_bars: int = 1,
    n_bars: int | None = None,
) -> pd.DataFrame:
    """Load OHLCV through `core_python.data.loader` in read-only mode."""
    from core_python.data import loader

    if start is not None and end is not None:
        raw = loader.load_range(
            symbol,
            timeframe,
            start,
            end,
            warmup_bars=warmup_bars,
            tail_bars=tail_bars,
        )
    elif n_bars is not None:
        raw = loader.load(symbol, timeframe, n_bars)
    else:
        raise ValueError("Pass either start/end or n_bars.")

    return normalize_ohlcv_frame(raw)


def assert_signal_bartimes_exist(signals: Iterable[SignalRow], bars: pd.DataFrame) -> None:
    """Raise if any signal bartime is missing from the OHLCV index."""
    index = set(bars["bartime"] if _is_normalized_ohlcv(bars) else normalize_ohlcv_frame(bars)["bartime"])
    missing = [signal for signal in signals if signal.bartime not in index]
    if missing:
        preview = [
            {
                "symbol": signal.symbol,
                "timeframe": signal.timeframe,
                "bartime": str(signal.bartime),
            }
            for signal in missing[:10]
        ]
        raise ValueError(f"{len(missing)} signal bartime values are missing in OHLCV: {preview}")


def find_bar_index(bars: pd.DataFrame, bartime: object) -> int:
    """Return the integer row index for a bar time."""
    normalized = bars if _is_normalized_ohlcv(bars) else normalize_ohlcv_frame(bars)
    ts = normalize_timestamp(bartime)
    matches = normalized.index[normalized["bartime"] == ts].tolist()
    if not matches:
        raise ValueError(f"Bar time not found in OHLCV: {ts}")
    return int(matches[0])


def get_next_open_bar(signal: SignalRow, bars: pd.DataFrame) -> Bar:
    """Return the entry bar for a signal, i.e. bar after signal.bartime."""
    normalized = bars if _is_normalized_ohlcv(bars) else normalize_ohlcv_frame(bars)
    idx = find_bar_index(normalized, signal.bartime)
    next_idx = idx + 1
    if next_idx >= len(normalized):
        raise ValueError(f"No next-open bar after signal {signal.bartime}.")
    return row_to_bar(normalized.iloc[next_idx])


def bars_from_time(bars: pd.DataFrame, start_time: object) -> pd.DataFrame:
    """Return bars from `start_time` inclusive."""
    normalized = bars if _is_normalized_ohlcv(bars) else normalize_ohlcv_frame(bars)
    ts = normalize_timestamp(start_time)
    return normalized[normalized["bartime"] >= ts].reset_index(drop=True)


def row_to_bar(row: pd.Series | dict) -> Bar:
    """Convert a pandas row or dict into a Bar."""
    data = row.to_dict() if isinstance(row, pd.Series) else dict(row)
    return Bar(
        bartime=normalize_timestamp(data["bartime"]),
        open=float(data["open"]),
        high=float(data["high"]),
        low=float(data["low"]),
        close=float(data["close"]),
        volume=None if pd.isna(data.get("volume")) else float(data.get("volume")),
    )


def bar_to_dict(bar: Bar) -> dict:
    """Return a serializable bar dict."""
    return asdict(bar)


def _is_normalized_ohlcv(df: pd.DataFrame) -> bool:
    """Best-effort check to avoid repeated normalization in hot paths."""
    return (
        list(df.columns[: len(OHLCV_COLUMNS)]) == OHLCV_COLUMNS
        and df["bartime"].is_monotonic_increasing
        and not df["bartime"].duplicated().any()
    )
