"""Alert semantics for the AI Trend multi-timeframe strategy.

This module belongs to the strategy layer: it converts already-built H3/M45
strategy frames into domain alerts and Telegram-ready HTML messages. It does
not load data, write state, schedule checks, or call notifier backends.
"""

from __future__ import annotations

from dataclasses import dataclass
import html

import pandas as pd


H3_TREND_CHANGE = "h3_trend_change"
M45_ENTRY_SIGNAL = "m45_entry_signal"


@dataclass(frozen=True)
class AiTrendAlert:
    """A deduplicatable alert produced by the AI Trend strategy."""

    kind: str
    symbol: str
    tf: str
    direction: int
    bar_time: pd.Timestamp
    event_time: pd.Timestamp
    h3_segment: int | None = None
    h3_close_time: pd.Timestamp | None = None
    ai_knn: float | None = None
    ai_avg: float | None = None
    ema_fast: float | None = None
    ema_slow: float | None = None
    close: float | None = None
    reason: str = ""


def extract_h3_trend_alerts(trend_frame: pd.DataFrame, symbol: str) -> list[AiTrendAlert]:
    """Return H3 KNN bias-change alerts from a closed H3 trend frame."""
    required = {"bartime", "h3_close_time", "h3_bias"}
    if trend_frame.empty or not required.issubset(trend_frame.columns):
        return []

    frame = trend_frame.sort_values("bartime").copy()
    frame["prev_h3_bias"] = frame["h3_bias"].shift(1)
    changed = frame["h3_bias"].isin([1, -1]) & frame["h3_bias"].ne(frame["prev_h3_bias"])

    alerts: list[AiTrendAlert] = []
    for _, row in frame[changed].iterrows():
        direction = int(row["h3_bias"])
        side = "bullish" if direction == 1 else "bearish"
        alerts.append(
            AiTrendAlert(
                kind=H3_TREND_CHANGE,
                symbol=symbol.upper(),
                tf="H3",
                direction=direction,
                bar_time=pd.Timestamp(row["bartime"]),
                event_time=pd.Timestamp(row["h3_close_time"]),
                h3_segment=_to_int_or_none(row.get("h3_bias_segment")),
                h3_close_time=pd.Timestamp(row["h3_close_time"]),
                ai_knn=_to_float_or_none(row.get("ai_knn")),
                ai_avg=_to_float_or_none(row.get("ai_avg")),
                close=_to_float_or_none(row.get("close")),
                reason=f"H3 KNN bias changed to {side}",
            )
        )
    return alerts


def extract_m45_entry_alerts(entry_frame: pd.DataFrame, symbol: str) -> list[AiTrendAlert]:
    """Return first aligned M45 BUY/SELL alerts inside each H3 KNN segment."""
    required = {"bartime", "m45_close_time", "signal"}
    if entry_frame.empty or not required.issubset(entry_frame.columns):
        return []

    signals = entry_frame[entry_frame["signal"].fillna(0).astype(int).ne(0)].copy()
    alerts: list[AiTrendAlert] = []
    for _, row in signals.iterrows():
        direction = int(row["signal"])
        side = "BUY" if direction == 1 else "SELL"
        alerts.append(
            AiTrendAlert(
                kind=M45_ENTRY_SIGNAL,
                symbol=symbol.upper(),
                tf="M45",
                direction=direction,
                bar_time=pd.Timestamp(row["bartime"]),
                event_time=pd.Timestamp(row["m45_close_time"]),
                h3_segment=_to_int_or_none(row.get("h3_bias_segment")),
                h3_close_time=_to_timestamp_or_none(row.get("h3_close_time")),
                ai_knn=_to_float_or_none(row.get("h3_ai_knn")),
                ai_avg=_to_float_or_none(row.get("h3_ai_avg")),
                ema_fast=_to_float_or_none(row.get("ema_fast")),
                ema_slow=_to_float_or_none(row.get("ema_slow")),
                close=_to_float_or_none(row.get("close")),
                reason=str(row.get("signal_reason") or f"First aligned M45 {side} in H3 segment"),
            )
        )
    return alerts


def ai_trend_alert_key(alert: AiTrendAlert) -> str:
    """Build a stable state key for an AI Trend alert."""
    return (
        f"ai_trend|{alert.kind}|{alert.symbol.upper()}|{alert.tf.upper()}|"
        f"{_key_time(alert.event_time)}|{int(alert.direction)}"
    )


def format_ai_trend_alert(alert: AiTrendAlert) -> str:
    """Format an AI Trend alert as Telegram HTML."""
    if alert.kind == H3_TREND_CHANGE:
        return _format_h3_alert(alert)
    if alert.kind == M45_ENTRY_SIGNAL:
        return _format_m45_alert(alert)
    raise ValueError(f"Unknown AI Trend alert kind: {alert.kind}")


def _format_h3_alert(alert: AiTrendAlert) -> str:
    direction = "BULLISH" if alert.direction == 1 else "BEARISH"
    lines = [
        f"<b>AI Trend H3 Trend Change</b> - <b>{html.escape(alert.symbol.upper())}</b>",
        "",
        f"Direction: <b>{direction}</b>",
        f"H3 close: <code>{_fmt_time(alert.event_time)} UTC</code>",
        f"H3 open:  <code>{_fmt_time(alert.bar_time)} UTC</code>",
        f"Close:    <code>{_fmt(alert.close)}</code>",
        f"KNN:      <code>{_fmt(alert.ai_knn)}</code>",
        f"Average:  <code>{_fmt(alert.ai_avg)}</code>",
    ]
    if alert.h3_segment is not None:
        lines.append(f"Segment:  <code>{alert.h3_segment}</code>")
    if alert.reason:
        lines.extend(["", html.escape(alert.reason)])
    return "\n".join(lines)


def _format_m45_alert(alert: AiTrendAlert) -> str:
    side = "BUY" if alert.direction == 1 else "SELL"
    lines = [
        f"<b>AI Trend M45 {side}</b> - <b>{html.escape(alert.symbol.upper())}</b>",
        "",
        f"M45 close: <code>{_fmt_time(alert.event_time)} UTC</code>",
        f"M45 open:  <code>{_fmt_time(alert.bar_time)} UTC</code>",
    ]
    if alert.h3_close_time is not None:
        lines.append(f"H3 close:  <code>{_fmt_time(alert.h3_close_time)} UTC</code>")
    lines.extend(
        [
            f"Close:     <code>{_fmt(alert.close)}</code>",
            f"EMA fast:  <code>{_fmt(alert.ema_fast)}</code>",
            f"EMA slow:  <code>{_fmt(alert.ema_slow)}</code>",
            f"H3 KNN:    <code>{_fmt(alert.ai_knn)}</code>",
            f"H3 Avg:    <code>{_fmt(alert.ai_avg)}</code>",
        ]
    )
    if alert.h3_segment is not None:
        lines.append(f"Segment:   <code>{alert.h3_segment}</code>")
    if alert.reason:
        lines.extend(["", html.escape(alert.reason)])
    return "\n".join(lines)


def _fmt_time(value: pd.Timestamp) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M")


def _key_time(value: pd.Timestamp) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def _fmt(value: object, digits: int = 5) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")


def _to_float_or_none(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _to_int_or_none(value: object) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _to_timestamp_or_none(value: object) -> pd.Timestamp | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value)
