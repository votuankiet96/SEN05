"""Shared realtime signal contract for strategy adapters.

Strategy-specific realtime modules return these objects to the notify runtime.
The notify layer then applies common rules: state de-dup, historical filtering,
outbox delivery, and human alert sending.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from core_python import config


@dataclass(frozen=True)
class RealtimeSignal:
    strategy: str
    event_type: str
    symbol: str
    tf: str
    direction: int
    bar_time: Any
    event_time: Any
    dedup_key: str
    payload_source: Any
    alert_message: str
    alert_backend: str
    alert_kwargs: dict[str, Any] = field(default_factory=dict)
    side: str = ""
    kind: str = "signal"
    event_text: str = ""
    historical_text: str = ""
    skip_text: str = ""
    pre_skip_reason: str | None = None


@dataclass(frozen=True)
class RealtimeScanResult:
    latest_bar_ts: pd.Timestamp | None
    signals: list[RealtimeSignal]
    export_frame: pd.DataFrame | None = None
    no_signal_text: str = "signal"


def realtime_signal_key(strategy: str, symbol: str, tf: str, bartime: object, direction: int) -> str:
    ts = pd.Timestamp(bartime)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return f"{strategy}|{symbol.upper()}|{tf.upper()}|{ts.strftime('%Y-%m-%d %H:%M:%S')}|{int(direction)}"


def as_utc_ts(value: object) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def latest_bar_ts(df: pd.DataFrame) -> pd.Timestamp | None:
    if df.empty or "bartime" not in df:
        return None
    latest = pd.to_datetime(df["bartime"], errors="coerce").dropna()
    if latest.empty:
        return None
    return pd.Timestamp(latest.iloc[-1])


def event_close_from_bar_open(bartime: object, tf: str) -> pd.Timestamp:
    ts = as_utc_ts(bartime)
    minutes = config.TF_MINUTES.get(str(tf).strip().upper())
    if not minutes:
        return ts
    return ts + pd.Timedelta(minutes=int(minutes))


def fmt_number(value: object, digits: int = 5) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
