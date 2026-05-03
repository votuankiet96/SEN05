"""Format signal notification messages."""

from __future__ import annotations

import pandas as pd


def format_signal_message(
    row: pd.Series,
    *,
    strategy_label: str,
    symbol: str,
    tf: str,
) -> str:
    """Return a concise signal message suitable for Telegram/Discord."""
    direction = "BUY" if int(row["signal"]) == 1 else "SELL"
    signal_time = pd.Timestamp(row["bartime"]).strftime("%Y-%m-%d %H:%M:%S")
    entry_time = (
        pd.Timestamp(row["entry_time"]).strftime("%Y-%m-%d %H:%M:%S")
        if pd.notna(row.get("entry_time"))
        else "-"
    )

    lines = [
        f"[SIGNAL] {strategy_label} {symbol.upper()} {tf.upper()}",
        "",
        f"Time: {signal_time} UTC",
        f"Signal: {direction}",
        f"ATR: {_fmt(row.get('atr'))}",
        "",
        f"Entry time: {entry_time} UTC",
        f"Entry: {_fmt(row.get('entry_price'))}",
        f"SL: {_fmt(row.get('sl_price'))}",
        f"TP: {_fmt(row.get('tp_price'))}",
    ]
    rr = row.get("risk_reward")
    if pd.notna(rr):
        lines.append(f"RR: {_fmt(rr)}")
    reason = str(row.get("signal_reason") or "").strip()
    if reason:
        lines.extend(["", f"Reason: {reason}"])
    return "\n".join(lines)


def _fmt(value: object, digits: int = 5) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")

