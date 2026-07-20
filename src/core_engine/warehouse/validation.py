"""OHLCV normalization and validation shared by historical/live flows."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone


def utc_naive_now() -> datetime:
    """Return current UTC as a naive datetime for SQL Server DATETIME columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_tv_hist_df_to_utc(df):
    """Normalize tz-aware or local-naive TradingView indexes to UTC-naive."""
    if df is None or df.empty:
        return df, False

    idx = getattr(df, "index", None)
    if idx is None:
        return df, False

    try:
        tz_attr = getattr(idx, "tz", None)
        if tz_attr is not None:
            normalized_idx = idx.tz_convert(timezone.utc).tz_localize(None)
        else:
            local_tz = datetime.now().astimezone().tzinfo or timezone.utc
            normalized_idx = idx.tz_localize(local_tz).tz_convert(timezone.utc).tz_localize(None)
    except Exception:
        return df, False

    if normalized_idx.equals(idx):
        return df, False

    df = df.copy()
    df.index = normalized_idx
    return df, True


def validate_ohlcv_df(
    df,
    tv_symbol: str,
    tf_code: str,
    logger: logging.Logger,
    *,
    normalize_timestamps: bool = True,
):
    """Return (cleaned_df, had_issues) before any OHLCV write path."""
    if df is None or df.empty:
        return df, False

    if normalize_timestamps:
        df, normalized = normalize_tv_hist_df_to_utc(df)
        if normalized:
            logger.debug("  VALIDATE %s %s: normalized timestamps to UTC", tv_symbol, tf_code)

    original_len = len(df)
    had_issues = False

    ohlc_cols = [c for c in ("open", "high", "low", "close") if c in df.columns]
    if ohlc_cols:
        null_mask = df[ohlc_cols].isnull().any(axis=1)
        if null_mask.any():
            logger.warning(
                "  VALIDATE %s %s: %d rows with null OHLC -> dropped",
                tv_symbol,
                tf_code,
                int(null_mask.sum()),
            )
            df = df[~null_mask]
            had_issues = True

    if df.empty:
        return df, had_issues

    if "high" in df.columns and "low" in df.columns:
        invalid_hl = df["high"] < df["low"]
        if invalid_hl.any():
            logger.warning(
                "  VALIDATE %s %s: %d rows with High < Low -> dropped",
                tv_symbol,
                tf_code,
                int(invalid_hl.sum()),
            )
            df = df[~invalid_hl]
            had_issues = True

    if df.empty:
        return df, had_issues

    if df.index.duplicated().any():
        n_dupes = int(df.index.duplicated().sum())
        logger.warning(
            "  VALIDATE %s %s: %d duplicate timestamps -> kept first",
            tv_symbol,
            tf_code,
            n_dupes,
        )
        df = df[~df.index.duplicated(keep="first")]
        had_issues = True

    if not df.index.is_monotonic_increasing:
        logger.warning("  VALIDATE %s %s: timestamps not monotonic -> sorted", tv_symbol, tf_code)
        df = df.sort_index()
        had_issues = True

    now_cutoff = utc_naive_now() + timedelta(minutes=1)
    future_mask = df.index > now_cutoff
    if future_mask.any():
        n_future = int(future_mask.sum())
        logger.warning(
            "  VALIDATE %s %s: %d future bar(s) beyond %s -> dropped",
            tv_symbol,
            tf_code,
            n_future,
            now_cutoff.strftime("%Y-%m-%d %H:%M:%S"),
        )
        df = df[~future_mask]
        had_issues = True

    if df.empty:
        return df, had_issues

    if had_issues:
        dropped = original_len - len(df)
        if dropped > 0:
            logger.warning(
                "  VALIDATE %s %s: %d/%d rows removed total",
                tv_symbol,
                tf_code,
                dropped,
                original_len,
            )

    return df, had_issues
