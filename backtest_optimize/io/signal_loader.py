"""Signal CSV adapters.

The loader is intentionally strict:

- It normalizes `side` to `direction`.
- It requires `symbol` and `timeframe` either in the CSV or as explicit caller
  arguments.
- It never infers symbol/timeframe from the filename.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from backtest_optimize.contracts import Direction, SignalRow

BUY_ALIASES = {"BUY", "LONG", "BULL", "BULLISH", "1", 1}
SELL_ALIASES = {"SELL", "SHORT", "BEAR", "BEARISH", "-1", -1}


def normalize_timestamp(value: object) -> pd.Timestamp:
    """Return a UTC-naive pandas timestamp."""
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        raise ValueError(f"Invalid timestamp: {value!r}")
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts


def normalize_direction(value: object) -> Direction:
    """Normalize BUY/SELL aliases into Direction."""
    raw = value
    if isinstance(value, str):
        raw = value.strip().upper()
    if raw in BUY_ALIASES:
        return Direction.BUY
    if raw in SELL_ALIASES:
        return Direction.SELL
    raise ValueError(f"Unsupported direction value: {value!r}")


def _first_present(columns: Iterable[str], candidates: tuple[str, ...]) -> str | None:
    lower_map = {str(col).strip().lower(): col for col in columns}
    for candidate in candidates:
        found = lower_map.get(candidate)
        if found is not None:
            return str(found)
    return None


def normalize_signal_frame(
    df: pd.DataFrame,
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
) -> pd.DataFrame:
    """Normalize a raw signal DataFrame into the package input contract."""
    if df.empty:
        raise ValueError("Signal DataFrame is empty.")

    out = df.copy()
    out.columns = [str(col).strip() for col in out.columns]

    time_col = _first_present(out.columns, ("bartime", "bar_time", "time", "timestamp"))
    if time_col is None:
        raise ValueError("Signal DataFrame must contain a bartime column.")
    if time_col != "bartime":
        out = out.rename(columns={time_col: "bartime"})

    direction_col = _first_present(out.columns, ("direction", "side", "signal"))
    if direction_col is None:
        raise ValueError("Signal DataFrame must contain direction or side.")
    if direction_col != "direction":
        out = out.rename(columns={direction_col: "direction"})

    if "symbol" not in out.columns:
        if not symbol:
            raise ValueError("Signal CSV has no symbol column; pass symbol explicitly.")
        out["symbol"] = str(symbol).strip().upper()
    else:
        out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()

    if "timeframe" not in out.columns:
        if not timeframe:
            raise ValueError("Signal CSV has no timeframe column; pass timeframe explicitly.")
        out["timeframe"] = str(timeframe).strip().upper()
    else:
        out["timeframe"] = out["timeframe"].astype(str).str.strip().str.upper()

    out["bartime"] = out["bartime"].map(normalize_timestamp)
    out["direction"] = out["direction"].map(normalize_direction)

    if "atr" in out.columns:
        out["atr"] = pd.to_numeric(out["atr"], errors="coerce")

    out = out.sort_values(["symbol", "timeframe", "bartime"]).reset_index(drop=True)
    duplicated = out.duplicated(subset=["symbol", "timeframe", "bartime"], keep=False)
    if duplicated.any():
        sample = out.loc[duplicated, ["symbol", "timeframe", "bartime"]].head(5)
        raise ValueError(f"Duplicate signal timestamps detected: {sample.to_dict('records')}")

    return out


def load_signal_csv(
    path: str | Path,
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    **read_csv_kwargs,
) -> pd.DataFrame:
    """Load and normalize a signal CSV.

    `symbol` and `timeframe` are explicit metadata overrides when the CSV lacks
    those columns. The filename is never parsed for metadata.
    """
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)
    df = pd.read_csv(csv_path, **read_csv_kwargs)
    return normalize_signal_frame(df, symbol=symbol, timeframe=timeframe)


def to_signal_rows(df: pd.DataFrame) -> list[SignalRow]:
    """Convert a normalized signal DataFrame into immutable SignalRow objects."""
    required = {"bartime", "symbol", "timeframe", "direction"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Signal DataFrame is missing required columns: {sorted(missing)}")

    rows: list[SignalRow] = []
    for record in df.to_dict("records"):
        extras = {
            key: value
            for key, value in record.items()
            if key not in {"bartime", "symbol", "timeframe", "direction", "atr"}
        }
        atr = record.get("atr")
        if pd.isna(atr):
            atr = None
        rows.append(
            SignalRow(
                bartime=normalize_timestamp(record["bartime"]),
                symbol=str(record["symbol"]).strip().upper(),
                timeframe=str(record["timeframe"]).strip().upper(),
                direction=normalize_direction(record["direction"]),
                atr=None if atr is None else float(atr),
                extras=extras,
            )
        )
    return rows
