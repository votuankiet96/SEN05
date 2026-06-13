"""Realtime adapter for AI Trend H3/M45 production semantics."""

from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from core_python.strategies.ai_trend.alerts import (
    H3_TREND_CHANGE,
    M45_ENTRY_SIGNAL,
    ai_trend_alert_key,
    extract_h3_trend_alerts,
    extract_m45_entry_alerts,
    format_ai_trend_alert,
)
from core_python.strategies.ai_trend.signals import (
    build_ai_trend_frames,
    prepare_trend_frame,
)
from core_python.strategies.realtime import RealtimeScanResult, RealtimeSignal


def detect_realtime_signals(
    *,
    symbol: str,
    tf: str,
    bars: int,
    overrides: dict[str, Any] | None,
    closed_only: bool,
    event_type: str | None,
    run_ai_trend_alerts_func: Callable[..., tuple[list[Any], pd.Timestamp | None]],
) -> RealtimeScanResult:
    """Return AI Trend H3 trend-change or M45 entry signals as the common contract."""
    normalized_event_type = normalize_ai_trend_event_type(event_type, tf)
    alerts, latest = run_ai_trend_alerts_func(
        symbol=symbol,
        tf=tf,
        bars=bars,
        event_type=normalized_event_type,
        overrides=overrides,
        closed_only=closed_only,
    )
    signals: list[RealtimeSignal] = []
    for alert in alerts:
        side = ai_trend_alert_side(alert.direction, alert.kind)
        pre_skip_reason = None
        if not ai_trend_m45_matches_h3(alert):
            pre_skip_reason = f"because H3 bias is {getattr(alert, 'h3_bias', None)}"
        signals.append(
            RealtimeSignal(
                strategy="ai_trend",
                event_type=normalized_event_type,
                symbol=symbol.upper(),
                tf=tf.upper(),
                direction=int(alert.direction),
                bar_time=alert.bar_time,
                event_time=alert.event_time,
                dedup_key=ai_trend_alert_key(alert),
                payload_source=alert,
                alert_message=format_ai_trend_alert(alert),
                alert_backend="discord",
                side=side,
                kind=normalized_event_type,
                event_text=f"{normalized_event_type} {side}",
                historical_text=f"AI Trend {normalized_event_type} {side}",
                skip_text=f"AI Trend M45 {side}",
                pre_skip_reason=pre_skip_reason,
            )
        )
    return RealtimeScanResult(latest_bar_ts=latest, signals=signals, no_signal_text="AI Trend alert")


def run_ai_trend_alerts(
    *,
    symbol: str,
    tf: str,
    bars: int,
    event_type: str | None,
    overrides: dict[str, Any] | None = None,
    closed_only: bool = True,
    get_strategy_func: Callable[[str], Any],
    load_ohlcv_func: Callable[[str, str, int], pd.DataFrame],
    drop_open_bar_func: Callable[[pd.DataFrame, str], pd.DataFrame],
    latest_bar_ts_func: Callable[[pd.DataFrame], pd.Timestamp | None],
    warn_if_stale_bar_func: Callable[..., bool],
    build_frames_func: Callable[[pd.DataFrame, pd.DataFrame, dict[str, Any]], tuple[pd.DataFrame, pd.DataFrame]] = build_ai_trend_frames,
) -> tuple[list[Any], pd.Timestamp | None]:
    """Build closed AI Trend frames and extract alerts for the requested event type."""
    spec = get_strategy_func("ai_trend")
    local_overrides = dict(overrides or {})
    tf_code = str(tf).strip().upper()
    normalized_event_type = normalize_ai_trend_event_type(event_type, tf_code)
    if normalized_event_type == H3_TREND_CHANGE:
        local_overrides.setdefault("TREND_BARS", bars)
    elif normalized_event_type == M45_ENTRY_SIGNAL:
        local_overrides.setdefault("ENTRY_BARS", bars)

    params = spec.normalize_params(local_overrides, symbol)
    selected_symbol = params["SYMBOL"]

    trend_raw = load_ohlcv_func(selected_symbol, params["TREND_TF"], int(params["TREND_BARS"]))
    if closed_only:
        trend_raw = drop_open_bar_func(trend_raw, params["TREND_TF"])
    trend_latest_ts = latest_bar_ts_func(trend_raw)
    if trend_latest_ts is not None:
        warn_if_stale_bar_func(selected_symbol, params["TREND_TF"], trend_latest_ts)
    if trend_raw.empty:
        raise ValueError(f"{selected_symbol} has no closed {params['TREND_TF']} data")

    if normalized_event_type == H3_TREND_CHANGE:
        trend_frame = prepare_trend_frame(trend_raw, params)
        return extract_h3_trend_alerts(trend_frame, selected_symbol), trend_latest_ts

    entry_raw = load_ohlcv_func(selected_symbol, params["ENTRY_TF"], int(params["ENTRY_BARS"]))
    if closed_only:
        entry_raw = drop_open_bar_func(entry_raw, params["ENTRY_TF"])
    entry_latest_ts = latest_bar_ts_func(entry_raw)
    if entry_latest_ts is not None:
        warn_if_stale_bar_func(selected_symbol, params["ENTRY_TF"], entry_latest_ts)
    if entry_raw.empty:
        raise ValueError(f"{selected_symbol} has no closed {params['ENTRY_TF']} data")

    _trend_frame, entry_frame = build_frames_func(trend_raw, entry_raw, params)
    entry_frame = spec.add_levels(entry_frame, params, selected_symbol)
    return extract_m45_entry_alerts(entry_frame, selected_symbol), entry_latest_ts


def normalize_ai_trend_event_type(event_type: str | None, tf: str) -> str:
    normalized = str(event_type or "").strip().lower()
    if normalized in {H3_TREND_CHANGE, M45_ENTRY_SIGNAL}:
        return normalized
    tf_code = str(tf).strip().upper()
    if tf_code == "H3":
        return H3_TREND_CHANGE
    if tf_code == "M45":
        return M45_ENTRY_SIGNAL
    raise ValueError("AI Trend notify supports only H3 trend-change and M45 entry-signal groups")


def ai_trend_alert_side(direction: int, kind: str) -> str:
    if kind == H3_TREND_CHANGE:
        return "BULLISH" if int(direction) == 1 else "BEARISH"
    return "BUY" if int(direction) == 1 else "SELL"


def ai_trend_m45_matches_h3(alert: Any) -> bool:
    if getattr(alert, "kind", None) != M45_ENTRY_SIGNAL:
        return True
    h3_bias = getattr(alert, "h3_bias", None)
    if h3_bias is None or pd.isna(h3_bias):
        return False
    return int(h3_bias) == int(alert.direction)
