"""Shared market-data adapters for strategy research and backtests.

Architecture role
-----------------
- This file is the only place inside the new `core_python` structure that should
  know how to talk to the repo-level `modules.*` data stack and the local SQL DB.
- Strategy packages should consume normalized loaders from here instead of
  importing `modules.data_loader` or writing SQL directly.

Input / output contracts
------------------------
- `load_scan_ohlcv()` returns scanner-friendly lowercase OHLC data with `BarTime`
  as a plain column.
- `load_chart_candles()` returns the richer Title-Case chart contract used by the
  generic chart builder.
- `load_backtest_ohlcv()` returns DatetimeIndex + lowercase OHLCV for execution.

Change impact
-------------
- Any schema change here will ripple into signal generation, scanners, backtests,
  walk-forward tests, and portfolio aggregation.
"""

from __future__ import annotations

import logging

import pandas as pd

from modules.data_loader import (
    load_candles as _load_chart_candles,
    load_ohlcv as _load_scan_ohlcv,
    load_symbols,
    load_timeframes,
)
from modules.db_connector import get_connection

_log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# DATA QUALITY GATE
# ─────────────────────────────────────────────────────────────────────────────

class DataQualityError(ValueError):
    """Ném khi dữ liệu không đủ điều kiện để chạy backtest an toàn."""


def validate_backtest_data(
    df: pd.DataFrame,
    *,
    symbol_id: int | None = None,
    tf: str = "H4",
    min_bars: int = 100,
    fix_inplace: bool = True,
) -> tuple[pd.DataFrame, dict]:
    """Kiểm tra và tự sửa dữ liệu OHLCV trước khi đưa vào backtest.

    Mục tiêu: phát hiện sớm dữ liệu xấu trước khi execution engine chạy,
    thay vì để NaN / giá đảo ngược gây ra kết quả backtest sai lặng.

    Các vấn đề được xử lý:
    - NaN trong bất kỳ cột OHLCV nào → drop hàng (log count)
    - Duplicate timestamp → drop duplicate, giữ lại lần đầu (log count)
    - High < Low (impossible bar) → drop hàng (log count)
    - Index chưa sort → sort tăng dần
    - Số bar sau clean < min_bars → ném DataQualityError

    Parameters
    ----------
    df          : DataFrame với DatetimeIndex, cột lowercase ohlcv.
    symbol_id   : ghi vào log để dễ truy vết.
    tf          : timeframe, dùng trong log.
    min_bars    : số bar tối thiểu để backtest có ý nghĩa (mặc định 100).
    fix_inplace : nếu True, tự sửa và trả về df đã sửa; nếu False, chỉ báo cáo.

    Returns
    -------
    (df_clean, report)
    - df_clean : DataFrame đã loại bỏ hàng lỗi (hoặc df gốc nếu fix_inplace=False).
    - report   : dict với n_original, n_nan_dropped, n_dup_dropped, n_invalid_bars,
                 n_final, quality_ok.

    Raises
    ------
    DataQualityError
        Khi df rỗng, hoặc sau khi clean số bar < min_bars.
    """
    label = f"symbol_id={symbol_id}, tf={tf}"

    if df is None or df.empty:
        raise DataQualityError(
            f"[{label}] DataFrame rỗng — không có dữ liệu để backtest."
        )

    n_original = len(df)
    df_out     = df.sort_index() if not df.index.is_monotonic_increasing else df.copy()

    # ── Duplicate timestamps ─────────────────────────────────────────────────
    n_dup = df_out.index.duplicated(keep="first").sum()
    if n_dup:
        _log.warning("[%s] Xóa %d hàng timestamp trùng lặp", label, n_dup)
        df_out = df_out[~df_out.index.duplicated(keep="first")]

    # ── NaN trong OHLCV ──────────────────────────────────────────────────────
    ohlcv_cols = [c for c in ("open", "high", "low", "close") if c in df_out.columns]
    nan_mask   = df_out[ohlcv_cols].isna().any(axis=1)
    n_nan      = int(nan_mask.sum())
    if n_nan:
        _log.warning("[%s] Xóa %d hàng có NaN trong OHLCV", label, n_nan)
        if fix_inplace:
            df_out = df_out[~nan_mask]

    # ── High < Low (impossible bar) ──────────────────────────────────────────
    n_invalid = 0
    if "high" in df_out.columns and "low" in df_out.columns:
        bad_bar = df_out["high"] < df_out["low"]
        n_invalid = int(bad_bar.sum())
        if n_invalid:
            _log.warning("[%s] Xóa %d bar có high < low (dữ liệu hỏng)", label, n_invalid)
            if fix_inplace:
                df_out = df_out[~bad_bar]

    n_final   = len(df_out)
    quality_ok = n_final >= min_bars

    report = {
        "symbol_id":       symbol_id,
        "tf":              tf,
        "n_original":      n_original,
        "n_nan_dropped":   n_nan,
        "n_dup_dropped":   n_dup,
        "n_invalid_bars":  n_invalid,
        "n_final":         n_final,
        "quality_ok":      quality_ok,
    }

    if not quality_ok:
        raise DataQualityError(
            f"[{label}] Chỉ còn {n_final} bars sau khi clean, cần ít nhất {min_bars}. "
            f"Kiểm tra data pipeline hoặc tăng max_bars."
        )

    return (df_out if fix_inplace else df), report


def load_scan_ohlcv(
    symbol_id: int,
    n_bars: int,
    tf_code: str = "H4",
    warmup: int = 200,
    handle_missing: str = "flag",
) -> pd.DataFrame:
    """Load lowercase OHLCV data for scanners and visual research.

    This is a thin adapter over `modules.data_loader.load_ohlcv()` so the rest of
    `core_python` can depend on a stable function name and location.
    """
    return _load_scan_ohlcv(
        symbol_id=symbol_id,
        n_bars=n_bars,
        tf_code=tf_code,
        warmup=warmup,
        handle_missing=handle_missing,
    )


def load_chart_candles(symbol: str, tf: str, n_bars: int) -> pd.DataFrame:
    """Load Title-Case candles for generic chart dashboards."""
    return _load_chart_candles(symbol=symbol, tf=tf, n_bars=n_bars)


def load_backtest_ohlcv(
    symbol_id: int,
    *,
    date_to: str | None = None,
    tf: str = "H4",
    max_bars: int = 60000,
    warmup: int = 200,
    validate: bool = True,
    min_bars: int = 100,
) -> pd.DataFrame:
    """Load OHLCV history for backtests with DatetimeIndex and volume.

    Why a separate loader exists:
    - execution code works best with a monotonic DatetimeIndex
    - scanner/chart code uses a different schema and should not leak into
      backtest internals

    Parameters
    ----------
    validate : bool, default=True
        Khi True, gọi validate_backtest_data() trước khi trả về — phát hiện
        NaN, duplicate timestamps, và high<low. Ném DataQualityError nếu data
        không đủ chất lượng.
    min_bars : int, default=100
        Ngưỡng bar tối thiểu cho validate (chỉ áp dụng khi validate=True).
    """
    total = max_bars + max(int(warmup), 0)

    conn = get_connection()
    try:
        date_clause = "AND f.BarTime <= ?" if date_to else ""
        query = f"""
            SELECT TOP ({total})
                   f.BarTime,
                   f.[Open]  AS [open],
                   f.High    AS [high],
                   f.Low     AS [low],
                   f.[Close] AS [close],
                   f.Volume  AS [volume]
            FROM   DWH.Fact_OHLCV f
            JOIN   DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
            WHERE  f.SymbolID = ?
              AND  tf.Code    = ?
              {date_clause}
            ORDER  BY f.BarTime DESC
        """
        params = (symbol_id, tf, date_to) if date_to else (symbol_id, tf)
        df = pd.read_sql(
            query,
            conn,
            params=params,
            parse_dates=["BarTime"],
            index_col="BarTime",
        )
    finally:
        conn.close()

    if df.empty:
        if validate:
            raise DataQualityError(
                f"[symbol_id={symbol_id}, tf={tf}] Không có dữ liệu — "
                "kiểm tra SymbolID và data pipeline."
            )
        return df

    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_index()

    if validate:
        df, _ = validate_backtest_data(df, symbol_id=symbol_id, tf=tf, min_bars=min_bars)

    return df


def load_backtest_ohlcv_full(
    symbol_id: int,
    *,
    tf: str = "H4",
    max_bars: int = 80000,
) -> pd.DataFrame:
    """Load as much history as practical for optimization / caching."""
    return load_backtest_ohlcv(
        symbol_id=symbol_id,
        date_to=None,
        tf=tf,
        max_bars=max_bars,
        warmup=0,
    )
