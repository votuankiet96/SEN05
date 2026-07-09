"""Common signal event model for historical exports and live publishing."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class SignalEvent:
    """A strategy signal independent of CSV, dashboard JSON, or Redis."""

    signal_id: str
    strategy: str
    symbol: str
    timeframe: str
    direction: int
    side: str
    bar_time: str
    event_close: float | None
    entry_price: float | None
    sl_price: float | None
    tp_price: float | None
    risk_reward: float | None
    atr: float | None
    signal_reason: str
    produced_at: str

    def as_payload(self) -> dict[str, Any]:
        """Return a Redis/JSON-friendly payload dictionary."""
        return {
            "signal_id": self.signal_id,
            "strategy": self.strategy,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "side": self.side,
            "bar_time": self.bar_time,
            "event_close": self.event_close,
            "entry_price": self.entry_price,
            "sl_price": self.sl_price,
            "tp_price": self.tp_price,
            "risk_reward": self.risk_reward,
            "atr": self.atr,
            "signal_reason": self.signal_reason,
            "produced_at": self.produced_at,
        }


def build_signal_id(strategy: str, symbol: str, tf: str, bar_time: object, direction: int) -> str:
    """Build a deterministic id for one strategy signal."""
    bar_time_key = pd.Timestamp(bar_time).isoformat()
    raw = f"{strategy}|{symbol}|{tf}|{bar_time_key}|{int(direction)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def events_from_frame(strategy: str, symbol: str, tf: str, df: pd.DataFrame) -> list[SignalEvent]:
    """Extract signal events from an enriched strategy frame."""
    if df.empty or "signal" not in df.columns:
        return []
    signal_values = pd.to_numeric(df["signal"], errors="coerce").fillna(0).astype(int)
    signals = df[signal_values.ne(0)]
    return [event_from_row(strategy, symbol, tf, row) for _, row in signals.iterrows()]


def payloads_from_frame(strategy: str, symbol: str, tf: str, df: pd.DataFrame) -> list[dict[str, Any]]:
    """Extract signal payload dictionaries from an enriched strategy frame."""
    return [event.as_payload() for event in events_from_frame(strategy, symbol, tf, df)]


def event_from_row(strategy: str, symbol: str, tf: str, row: pd.Series) -> SignalEvent:
    """Build one signal event from an enriched strategy row."""
    direction = int(row["signal"])
    bar_time = row["bartime"]
    return SignalEvent(
        signal_id=build_signal_id(strategy, symbol, tf, bar_time, direction),
        strategy=strategy,
        symbol=symbol,
        timeframe=tf,
        direction=direction,
        side="BUY" if direction == 1 else "SELL",
        bar_time=pd.Timestamp(bar_time).isoformat(),
        event_close=_num(row.get("close")),
        entry_price=_num(row.get("entry_price")),
        sl_price=_num(row.get("sl_price")),
        tp_price=_num(row.get("tp_price")),
        risk_reward=_num(row.get("risk_reward")),
        atr=_num(row.get("atr")),
        signal_reason=str(row.get("signal_reason", "") or ""),
        produced_at=pd.Timestamp.now(tz="UTC").isoformat(),
    )


def _num(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)
