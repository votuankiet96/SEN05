"""Export Combo signal/order plans for external cTrader validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .config import TIMEFRAME, get_account_settings
from .orders import create_order_intent_from_signal
from .symbol.backtest import build_symbol_signal_frame, load_backtest_data


CTRADER_COLUMNS = [
    "symbol",
    "timeframe",
    "bar_time_utc",
    "signal",
    "direction",
    "order_type",
    "entry",
    "sl",
    "tp",
    "expiry_bars",
    "strategy",
    "comment",
]


def build_ctrader_signal_export(
    symbol_key: str,
    df_raw: pd.DataFrame | None = None,
    *,
    timeframe: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    indicator_overrides: dict[str, Any] | None = None,
    symbol_overrides: dict[str, Any] | None = None,
    strategy_overrides: dict[str, Any] | None = None,
    broker_profile: str | None = None,
    max_bars: int = 60000,
) -> pd.DataFrame:
    """Build a cTrader-ready CSV frame from the exact Combo signal/order logic."""
    tf = timeframe or TIMEFRAME
    if df_raw is None:
        from .config import get_symbol_params

        cfg = get_symbol_params(symbol_key, broker_profile=broker_profile)
        df_raw = load_backtest_data(cfg["symbol_id"], date_to=date_to, tf=tf, max_bars=max_bars)

    signal_frame, cfg, _params = build_symbol_signal_frame(
        symbol_key,
        df_raw,
        date_from=date_from,
        date_to=date_to,
        indicator_overrides=indicator_overrides,
        symbol_overrides=symbol_overrides,
        broker_profile=broker_profile,
    )
    strategy = get_account_settings("standard", strategy_overrides)

    rows: list[dict[str, Any]] = []
    for _, bar in signal_frame.iterrows():
        if int(bar.get("signal", 0) or 0) == 0:
            continue
        if not bool(bar.get("in_window", True)):
            continue
        intent = create_order_intent_from_signal(symbol_key, bar, cfg, strategy)
        if intent is None:
            continue
        rows.append(
            {
                "symbol": symbol_key,
                "timeframe": tf,
                "bar_time_utc": intent.created_time.tz_localize(None).isoformat(),
                "signal": int(intent.direction),
                "direction": "BUY" if intent.direction == 1 else "SELL",
                "order_type": "PENDING_STOP",
                "entry": round(float(intent.entry), 8),
                "sl": round(float(intent.sl), 8),
                "tp": round(float(intent.tp), 8),
                "expiry_bars": int(intent.ttl_bars),
                "strategy": intent.strategy_name,
                "comment": f"combo|x={intent.metadata.get('x')}|ktp={intent.metadata.get('ktp')}",
            }
        )
    return pd.DataFrame(rows, columns=CTRADER_COLUMNS)


def export_ctrader_csv(output_path: str | Path, *args: Any, **kwargs: Any) -> Path:
    """Write a cTrader signal export CSV and return the resolved path."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = build_ctrader_signal_export(*args, **kwargs)
    frame.to_csv(path, index=False)
    return path


def validate_ctrader_export(frame: pd.DataFrame) -> dict[str, Any]:
    """Return a compact quality report for an export frame."""
    missing = [col for col in CTRADER_COLUMNS if col not in frame.columns]
    return {
        "ok": not missing and frame["bar_time_utc"].is_unique,
        "rows": int(len(frame)),
        "missing_columns": missing,
        "duplicate_times": int(frame["bar_time_utc"].duplicated().sum()) if "bar_time_utc" in frame else None,
    }


__all__ = [
    "CTRADER_COLUMNS",
    "build_ctrader_signal_export",
    "export_ctrader_csv",
    "validate_ctrader_export",
]
