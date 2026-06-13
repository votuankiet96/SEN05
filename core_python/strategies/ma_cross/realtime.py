"""Realtime adapter for level-based MA Cross style signals."""

from __future__ import annotations

import html
from typing import Any, Callable

import pandas as pd

from core_python.strategies.realtime import (
    RealtimeScanResult,
    RealtimeSignal,
    event_close_from_bar_open,
    fmt_number,
    latest_bar_ts,
    realtime_signal_key,
)


def detect_realtime_signals(
    *,
    strategy: str,
    symbol: str,
    tf: str,
    bars: int,
    overrides: dict[str, Any] | None,
    closed_only: bool,
    event_type: str | None,
    run_strategy_frame_func: Callable[..., tuple[pd.DataFrame, Any, dict[str, Any]]],
) -> RealtimeScanResult:
    """Run a standard level-based strategy and return realtime signal objects."""
    frame, spec, _params = run_strategy_frame_func(
        strategy=strategy,
        symbol=symbol,
        tf=tf,
        bars=bars,
        overrides=overrides,
        closed_only=closed_only,
    )
    latest = latest_bar_ts(frame)
    signals: list[RealtimeSignal] = []
    if frame.empty or "signal" not in frame.columns:
        return RealtimeScanResult(latest_bar_ts=latest, signals=signals, export_frame=frame)

    signal_rows = frame[frame["signal"].fillna(0).astype(int).ne(0)]
    for _, row in signal_rows.iterrows():
        direction = int(row["signal"])
        side = "BUY" if direction == 1 else "SELL"
        event_close = event_close_from_bar_open(row["bartime"], tf)
        signals.append(
            RealtimeSignal(
                strategy=strategy,
                event_type=str(event_type or "").strip().lower(),
                symbol=symbol.upper(),
                tf=tf.upper(),
                direction=direction,
                bar_time=row["bartime"],
                event_time=event_close,
                dedup_key=realtime_signal_key(strategy, symbol, tf, row["bartime"], direction),
                payload_source=row,
                alert_message=format_signal_message(row, strategy_label=spec.label, symbol=symbol, tf=tf),
                alert_backend="discord",
                side=side,
                kind=str(event_type or "signal").strip().lower() or "signal",
                event_text=side,
                historical_text=side,
            )
        )
    return RealtimeScanResult(latest_bar_ts=latest, signals=signals, export_frame=frame)


def format_signal_message(
    row: pd.Series,
    *,
    strategy_label: str,
    symbol: str,
    tf: str,
) -> str:
    """Format a level-based strategy signal for Telegram/Discord-compatible HTML."""
    direction = "BUY" if int(row["signal"]) == 1 else "SELL"
    signal_time = pd.Timestamp(row["bartime"]).strftime("%Y-%m-%d %H:%M")

    lines = [
        f"<b>{direction}</b> - {strategy_label} <b>{symbol.upper()}</b> {tf.upper()}",
        "",
        f"Time: <code>{signal_time} UTC</code>",
        f"Entry: <code>{fmt_number(row.get('entry_price'))}</code>",
        f"SL:    <code>{fmt_number(row.get('sl_price'))}</code>",
        f"TP:    <code>{fmt_number(row.get('tp_price'))}</code>",
    ]
    rr = row.get("risk_reward")
    if pd.notna(rr):
        lines.append(f"R:R:   <code>{fmt_number(rr, 2)}</code>")
    atr_value = row.get("atr")
    if pd.notna(atr_value):
        lines.append(f"ATR:   <code>{fmt_number(atr_value)}</code>")

    reason = html.escape(str(row.get("signal_reason") or "").strip())
    if reason:
        lines.extend(["", reason])
    return "\n".join(lines)
