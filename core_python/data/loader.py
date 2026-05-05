"""SQL Server OHLCV loader for the simplified dashboard."""

from __future__ import annotations

import pandas as pd

from modules.db_connector import get_connection
from simplified_core_python.config import get_symbol


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


def load(symbol: str, tf: str, n_bars: int) -> pd.DataFrame:
    """Load recent OHLCV bars from DWH.Fact_OHLCV."""
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
    return df[OHLCV_COLUMNS]
