"""Format signal notification messages for Telegram (HTML parse mode)."""

from __future__ import annotations

import pandas as pd


def format_signal_message(
    row: pd.Series,
    *,
    strategy_label: str,
    symbol: str,
    tf: str,
) -> str:
    """Return an HTML-formatted signal message for Telegram."""
    direction = "BUY" if int(row["signal"]) == 1 else "SELL"
    icon = "🟢" if direction == "BUY" else "🔴"
    signal_time = pd.Timestamp(row["bartime"]).strftime("%Y-%m-%d %H:%M")

    lines = [
        f"{icon} <b>{direction}</b> — {strategy_label} <b>{symbol.upper()}</b> {tf.upper()}",
        "",
        f"⏰ {signal_time} UTC",
        f"📍 Entry: <code>{_fmt(row.get('entry_price'))}</code>",
        f"🛑 SL:    <code>{_fmt(row.get('sl_price'))}</code>",
        f"🎯 TP:    <code>{_fmt(row.get('tp_price'))}</code>",
    ]
    rr = row.get("risk_reward")
    if pd.notna(rr):
        lines.append(f"⚖️ R:R:   <code>{_fmt(rr, 2)}</code>")
    atr = row.get("atr")
    if pd.notna(atr):
        lines.append(f"📊 ATR:   <code>{_fmt(atr)}</code>")
    reason = str(row.get("signal_reason") or "").strip()
    if reason:
        lines.extend(["", f"💬 {reason}"])
    return "\n".join(lines)


def _fmt(value: object, digits: int = 5) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
