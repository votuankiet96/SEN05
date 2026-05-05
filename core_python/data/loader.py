"""SQL Server OHLCV loader for the simplified dashboard."""

from __future__ import annotations

import logging

import pandas as pd

from modules.db_connector import get_connection
from core_python.config import get_symbol

logger = logging.getLogger(__name__)

OHLCV_COLUMNS = ["bartime", "open", "high", "low", "close", "volume"]


def _read_sql(query: str, conn, params: tuple) -> pd.DataFrame:
    """Read SQL through a pyodbc cursor into a DataFrame."""
    cursor = conn.cursor()
    try:
        cursor.execute(query, params)
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
        return pd.DataFrame.from_records(rows, columns=columns)
    finally:
        cursor.close()


def _validate(df: pd.DataFrame, symbol: str, tf: str) -> pd.DataFrame:
    """Drop clearly bad rows and warn; does not do full gap analysis."""
    n_in = len(df)

    # 1. Drop rows where bartime failed to parse
    df = df.dropna(subset=["bartime"])

    # 2. Drop rows with any null OHLC (volume can legitimately be null)
    df = df.dropna(subset=["open", "high", "low", "close"])

    # 3. Drop duplicate bartimes (keep last = most recent write)
    df = df.drop_duplicates(subset=["bartime"], keep="last")

    # 4. Ensure sorted ascending (should already be, but guard)
    df = df.sort_values("bartime").reset_index(drop=True)

    n_dropped = n_in - len(df)
    if n_dropped:
        logger.warning("loader: dropped %d bad rows for %s %s", n_dropped, symbol, tf)

    return df


def load(symbol: str, tf: str, n_bars: int) -> pd.DataFrame:
    """Load recent OHLCV bars from DWH.Fact_OHLCV.

    Contract: BarTime is stored as UTC-naive in the DB (Capital.com / MT5
    convention).  Callers that need timezone-aware timestamps must localize
    to UTC themselves — see _drop_open_bar() in signal_watcher.py.
    """
    symbol_cfg = get_symbol(symbol)
    tf_code = str(tf).strip().upper()
    limit = max(1, int(n_bars))

    query = """
        SELECT TOP (?)
               f.BarTime AS bartime,
               f.[Open] AS [open],
               f.High AS [high],
               f.Low AS [low],
               f.[Close] AS [close],
               f.Volume AS [volume]
        FROM DWH.Fact_OHLCV f
        JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
        WHERE f.SymbolID = ?
          AND tf.Code = ?
        ORDER BY f.BarTime DESC
    """

    conn = get_connection()
    try:
        df = _read_sql(query, conn, params=(limit, symbol_cfg["symbol_id"], tf_code))
    finally:
        conn.close()

    if df.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    df["bartime"] = pd.to_datetime(df["bartime"], errors="coerce")
    df = df.sort_values("bartime").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = _validate(df, symbol, tf_code)
    return df[OHLCV_COLUMNS]
