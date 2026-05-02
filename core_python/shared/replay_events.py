"""Shared optional replay-event helpers for backtest engines."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

import numpy as np
import pandas as pd

from core_python.shared.contracts import BacktestReplayEvent


def direction_to_side(direction: Any) -> str | None:
    try:
        d = int(direction)
    except Exception:
        return None
    if d > 0:
        return "BUY"
    if d < 0:
        return "SELL"
    return None


def emit_replay_event(
    sink: list[BacktestReplayEvent] | None,
    *,
    time: Any,
    symbol: str,
    event_type: str,
    side: str | None = None,
    direction: Any = None,
    price: Any = None,
    entry: Any = None,
    sl: Any = None,
    tp: Any = None,
    lot_size: Any = None,
    equity: Any = None,
    pnl_usd: Any = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append a replay event when a caller opted in with an event sink."""
    if sink is None:
        return
    sink.append(
        BacktestReplayEvent(
            time=pd.Timestamp(time),
            symbol=str(symbol),
            event_type=str(event_type),
            side=side or direction_to_side(direction),
            price=_maybe_float_or_none(price),
            entry=_maybe_float_or_none(entry),
            sl=_maybe_float_or_none(sl),
            tp=_maybe_float_or_none(tp),
            lot_size=_maybe_float_or_none(lot_size),
            equity=_maybe_float_or_none(equity),
            pnl_usd=_maybe_float_or_none(pnl_usd),
            reason=reason,
            metadata=dict(metadata or {}),
        )
    )


def replay_events_to_frame(
    events: list[BacktestReplayEvent] | list[dict[str, Any]],
) -> pd.DataFrame:
    """Convert replay event objects to a sorted DataFrame."""
    rows: list[dict[str, Any]] = []
    for event in events or []:
        if is_dataclass(event):
            rows.append(asdict(event))
        elif isinstance(event, dict):
            rows.append(dict(event))
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "time",
                "symbol",
                "event_type",
                "side",
                "price",
                "entry",
                "sl",
                "tp",
                "lot_size",
                "equity",
                "pnl_usd",
                "reason",
                "metadata",
            ]
        )
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    return frame.dropna(subset=["time"]).sort_values(["time", "symbol", "event_type"]).reset_index(drop=True)


def _maybe_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except Exception:
        return None
    return out if np.isfinite(out) else None


__all__ = [
    "BacktestReplayEvent",
    "direction_to_side",
    "emit_replay_event",
    "replay_events_to_frame",
]
