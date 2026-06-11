"""Realtime signal detection shared by the watcher runtime.

The notify layer owns orchestration only:
- load a scan group,
- call the matching strategy realtime adapter,
- apply common historical and state de-dup rules,
- queue Redis delivery through the outbox,
- send the human alert.

Strategy-specific production semantics live under ``core_python.strategies``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import pandas as pd

from core_python import config
from core_python.data.loader import load
from core_python.export.to_csv import export_signals
from core_python.notify.alerts import Notifier
from core_python.notify.state import SignalState, signal_key
from core_python.strategies.ai_trend import realtime as ai_trend_realtime
from core_python.strategies.combo import realtime as combo_realtime
from core_python.strategies.combo.pipeline import build_combo_mtf_frames
from core_python.strategies.knn_combo.pipeline import build_knn_combo_strategy_frames
from core_python.strategies.ma_cross import realtime as ma_cross_realtime
from core_python.strategies.realtime import RealtimeScanResult, RealtimeSignal
from core_python.strategies.registry import get_strategy

_DEFAULT_BAR_CLOSE_BUFFER_SECONDS = 5
_DEFAULT_POST_CLOSE_RETRY_SECONDS = 5
_DEFAULT_POST_CLOSE_WATCH_SECONDS = 10
_STALE_WARNING_LAG_MINUTES = 30
_STALE_WARNING_THROTTLE_MINUTES = 30
_HISTORICAL_ALERT_AGE_MULTIPLIER = 3
_HISTORICAL_ALERT_MIN_AGE_MINUTES = 120
_SIGNAL_SCHEMA_VERSION = "3.2"

logger = logging.getLogger(__name__)
_DB_LOAD_COUNT = 0
_LAST_STALE_LOGGED: dict[tuple[str, str], pd.Timestamp] = {}
_PRODUCER_RUN_ID = uuid4().hex

# Kept as a compatibility injection point for existing tests and local tooling.
build_ai_trend_frames = ai_trend_realtime.build_ai_trend_frames


@dataclass(frozen=True)
class SentSignalEvent:
    """A production signal event used for summaries."""

    strategy: str
    symbol: str
    tf: str
    side: str
    event_time: pd.Timestamp
    sent_at: pd.Timestamp
    kind: str = "signal"


def get_db_load_count() -> int:
    return _DB_LOAD_COUNT


def scan_fingerprint(scan_groups: list[dict], closed_only: bool) -> str:
    """Return a stable hash of the scan universe covered by warm-up."""
    universe = []
    for group in scan_groups:
        overrides = _canonical_json_value(group.get("overrides") or {})
        symbols = sorted(str(symbol).strip().upper() for symbol in group.get("symbols", []) if str(symbol).strip())
        universe.append(
            {
                "strategy": str(group.get("strategy", "")).strip().lower(),
                "event_type": str(_group_event_type(group) or "").strip().lower(),
                "tf": str(group.get("tf", "")).strip().upper(),
                "bars": int(group.get("bars", config.N_BARS)),
                "symbols": symbols,
                "overrides": overrides,
                "closed_only": bool(closed_only),
            }
        )
    universe.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
    payload = json.dumps(universe, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _canonical_json_value(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, set):
        return sorted(
            (_canonical_json_value(item) for item in value),
            key=lambda item: json.dumps(item, sort_keys=True, default=str),
        )
    return value


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _as_utc_ts(value: object) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _as_utc_iso(value: Any) -> str:
    if value is None:
        return ""
    try:
        ts = _as_utc_ts(value)
    except Exception:
        return str(value)
    if pd.isna(ts):
        return ""
    return ts.isoformat()


def _payload_scalar(value: Any) -> Any:
    if _is_missing(value):
        return ""
    if isinstance(value, (pd.Timestamp, datetime)):
        return _as_utc_iso(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _row_get(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    try:
        return item[name]
    except Exception:
        return getattr(item, name, default)


def _overrides_hash(overrides: dict[str, Any] | None) -> str:
    canonical = _canonical_json_value(overrides or {})
    payload = json.dumps(canonical, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _group_event_type(group: dict[str, Any]) -> str:
    if str(group.get("strategy", "")).strip().lower() == "ai_trend":
        return ai_trend_realtime.normalize_ai_trend_event_type(group.get("event_type"), str(group.get("tf", "")))
    return str(group.get("event_type") or "").strip().lower()


def _group_runtime_key(group: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(group.get("strategy", "")).strip().lower(),
        _group_event_type(group),
        str(group.get("tf", "")).strip().upper(),
        _overrides_hash(group.get("overrides") or {}),
    )


def _find_matching_groups(symbol: str, tf: str, scan_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    symbol_code = str(symbol).strip().upper()
    tf_code = str(tf).strip().upper()
    matches = []
    for group in scan_groups:
        if str(group.get("tf", "")).strip().upper() != tf_code:
            continue
        symbols = {str(item).strip().upper() for item in group.get("symbols", []) if str(item).strip()}
        if symbol_code in symbols:
            matches.append(group)
    return matches


def _signal_direction(item: Any) -> int:
    value = _row_get(item, "signal", _row_get(item, "direction", 0))
    return int(value)


def _signal_bar_time(item: Any) -> Any:
    for field in ("bartime", "bar_time", "event_time"):
        value = _row_get(item, field, None)
        if not _is_missing(value):
            return value
    return None


def _signal_id(
    *,
    strategy: str,
    event_type: str,
    symbol: str,
    tf: str,
    bar_time: Any,
    direction: int,
    overrides_hash: str,
) -> str:
    parts = [
        str(strategy).strip().lower(),
        str(event_type or "").strip().lower(),
        str(symbol).strip().upper(),
        str(tf).strip().upper(),
        _as_utc_iso(bar_time),
        str(int(direction)),
        str(overrides_hash),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _build_signal_payload(
    *,
    strategy: str,
    event_type: str,
    symbol: str,
    tf: str,
    signal: Any,
    event_close: Any,
    overrides_hash: str,
) -> dict[str, Any]:
    direction = _signal_direction(signal)
    bar_time = _signal_bar_time(signal)
    close_ts = _as_utc_ts(event_close)
    signal_id = _signal_id(
        strategy=strategy,
        event_type=event_type,
        symbol=symbol,
        tf=tf,
        bar_time=bar_time,
        direction=direction,
        overrides_hash=overrides_hash,
    )
    payload: dict[str, Any] = {
        "schema_version": _SIGNAL_SCHEMA_VERSION,
        "signal_id": signal_id,
        "producer_run_id": _PRODUCER_RUN_ID,
        "produced_at": _utcnow_iso(),
        "expires_at": (close_ts + pd.Timedelta(minutes=5)).isoformat(),
        "strategy": str(strategy).strip().lower(),
        "event_type": str(event_type or "").strip().lower(),
        "symbol": str(symbol).strip().upper(),
        "timeframe": str(tf).strip().upper(),
        "direction": direction,
        "side": "BUY" if direction == 1 else "SELL",
        "bar_time": _as_utc_iso(bar_time),
        "event_close": close_ts.isoformat(),
        "overrides_hash": overrides_hash,
    }
    for field in (
        "entry_price",
        "sl_price",
        "tp_price",
        "risk_reward",
        "atr",
        "open",
        "high",
        "low",
        "close",
        "signal_reason",
        "reason",
    ):
        value = _row_get(signal, field, None)
        if not _is_missing(value):
            payload[field] = _payload_scalar(value)
    return payload


def _emit_to_outbox(
    *,
    redis_on: bool,
    dry_run: bool,
    outbox: Any | None,
    strategy: str,
    event_type: str,
    symbol: str,
    tf: str,
    signal: Any,
    event_close: Any,
    overrides_hash: str,
    wake_delivery: Callable[[], None] | None = None,
) -> str | None:
    if not redis_on or dry_run or outbox is None:
        return None
    payload = _build_signal_payload(
        strategy=strategy,
        event_type=event_type,
        symbol=symbol,
        tf=tf,
        signal=signal,
        event_close=event_close,
        overrides_hash=overrides_hash,
    )
    outbox.add_pending(payload["signal_id"], payload)
    if wake_delivery is not None:
        wake_delivery()
    return str(payload["signal_id"])


def _scan_fingerprint_error(state: SignalState, expected_fingerprint: str) -> str | None:
    stored = state.get_scan_fingerprint()
    if stored == expected_fingerprint:
        return None
    if not stored:
        return (
            "[ERROR] Watcher state file has no scan fingerprint. "
            "Run warm-up before starting production:\n"
            "  python -m core_python.notify.signal_watcher --warm-up\n"
            f"  (expected state path: {state.path})"
        )
    return (
        "[ERROR] Watcher scan config changed since warm-up. "
        "Run warm-up again before starting production:\n"
        "  python -m core_python.notify.signal_watcher --warm-up\n"
        f"  stored fingerprint:  {stored}\n"
        f"  current fingerprint: {expected_fingerprint}"
    )


def _load_ohlcv(symbol: str, tf: str, bars: int) -> pd.DataFrame:
    global _DB_LOAD_COUNT
    _DB_LOAD_COUNT += 1
    return load(symbol, tf, bars)


def _warn_if_stale_bar(
    symbol: str,
    tf: str,
    latest_bartime: object,
    *,
    now_utc: pd.Timestamp | None = None,
    allowed_lag_minutes: int = _STALE_WARNING_LAG_MINUTES,
    throttle_minutes: int = _STALE_WARNING_THROTTLE_MINUTES,
) -> bool:
    tf_code = str(tf).strip().upper()
    minutes = config.TF_MINUTES.get(tf_code)
    if latest_bartime is None or not minutes:
        return False
    latest = _as_utc_ts(latest_bartime)
    now = pd.Timestamp.now("UTC") if now_utc is None else _as_utc_ts(now_utc)
    latest_close = latest + pd.Timedelta(minutes=int(minutes))
    age = now - latest_close
    key = (str(symbol).strip().upper(), tf_code)
    if age <= pd.Timedelta(minutes=int(allowed_lag_minutes)):
        _LAST_STALE_LOGGED.pop(key, None)
        return False

    last_logged = _LAST_STALE_LOGGED.get(key)
    if last_logged is None or now - last_logged >= pd.Timedelta(minutes=int(throttle_minutes)):
        logger.warning(
            "stale data: %s %s latest_close=%s age=%s threshold=%sm",
            key[0],
            key[1],
            latest_close.strftime("%Y-%m-%d %H:%M:%S UTC"),
            _format_timedelta(age),
            int(allowed_lag_minutes),
        )
        _LAST_STALE_LOGGED[key] = now
    return True


def _format_timedelta(delta: pd.Timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _alert_age_limit(tf: str, max_alert_age_minutes: int | None = None) -> pd.Timedelta:
    if max_alert_age_minutes is not None:
        return pd.Timedelta(minutes=max(1, int(max_alert_age_minutes)))
    minutes = config.TF_MINUTES.get(str(tf).strip().upper())
    if not minutes:
        return pd.Timedelta(minutes=_HISTORICAL_ALERT_MIN_AGE_MINUTES)
    return pd.Timedelta(
        minutes=max(
            _HISTORICAL_ALERT_MIN_AGE_MINUTES,
            _HISTORICAL_ALERT_AGE_MULTIPLIER * int(minutes),
        )
    )


def _event_close_from_bar_open(bartime: object, tf: str) -> pd.Timestamp:
    ts = _as_utc_ts(bartime)
    minutes = config.TF_MINUTES.get(str(tf).strip().upper())
    if not minutes:
        return ts
    return ts + pd.Timedelta(minutes=int(minutes))


def _is_historical_alert(
    event_time: object,
    tf: str,
    *,
    now_utc: pd.Timestamp | None = None,
    max_alert_age_minutes: int | None = None,
) -> tuple[bool, pd.Timedelta, pd.Timedelta]:
    now = pd.Timestamp.now("UTC") if now_utc is None else _as_utc_ts(now_utc)
    event_ts = _as_utc_ts(event_time)
    age = now - event_ts
    limit = _alert_age_limit(tf, max_alert_age_minutes=max_alert_age_minutes)
    return age > limit, age, limit


def _mark_historical_seen(state: SignalState, key: str, *, dry_run: bool) -> str:
    if dry_run:
        return "dry-run; not marked seen"
    state.add(key)
    return "marked seen"


def run_strategy_frame(
    *,
    strategy: str,
    symbol: str,
    tf: str,
    bars: int,
    overrides: dict[str, Any] | None = None,
    closed_only: bool = True,
) -> tuple[pd.DataFrame, Any, dict[str, Any]]:
    """Load OHLCV from DB and run the full strategy pipeline for realtime scanning."""
    spec = get_strategy(strategy)
    if spec.key == "knn_combo":
        local_overrides = dict(overrides or {})
        local_overrides.setdefault("ENTRY_TF", tf)
        local_overrides.setdefault("ENTRY_BARS", bars)
        params = spec.normalize_params(local_overrides, symbol)
        selected_symbol = params["SYMBOL"]
        trend_raw = _load_ohlcv(selected_symbol, params["TREND_TF"], int(params["TREND_BARS"]))
        entry_raw = _load_ohlcv(selected_symbol, params["ENTRY_TF"], int(params["ENTRY_BARS"]))
        if closed_only:
            trend_raw = _drop_open_bar(trend_raw, params["TREND_TF"])
            entry_raw = _drop_open_bar(entry_raw, params["ENTRY_TF"])
        if trend_raw.empty:
            raise ValueError(f"{selected_symbol} has no closed {params['TREND_TF']} data for KNN Combo trend")
        if entry_raw.empty:
            raise ValueError(f"{selected_symbol} has no closed {params['ENTRY_TF']} data for KNN Combo entry")
        trend_frame, enriched = build_knn_combo_strategy_frames(trend_raw, entry_raw, params, selected_symbol)
        _ = trend_frame
        return enriched, spec, params

    params = spec.normalize_params(overrides or {}, symbol)
    raw = _load_ohlcv(symbol, tf, bars)
    if closed_only:
        raw = _drop_open_bar(raw, tf)
    if spec.key == "combo" and params.get("HTF_TREND_ENABLED", False):
        trend_raw = _load_ohlcv(symbol, params["HTF_TF"], int(params["HTF_BARS"]))
        if closed_only:
            trend_raw = _drop_open_bar(trend_raw, params["HTF_TF"])
        if trend_raw.empty:
            raise ValueError(f"{symbol} has no closed {params['HTF_TF']} data for Combo HTF trend")
        trend_frame, enriched = build_combo_mtf_frames(trend_raw, raw, params, symbol)
        _ = trend_frame
        return enriched, spec, params

    with_indicators = spec.add_indicators(raw, params)
    with_signals = spec.detect_signals(with_indicators, symbol=symbol, params=params)
    enriched = spec.add_levels(with_signals, params, symbol)
    return enriched, spec, params


def all_new_signal_rows(
    df: pd.DataFrame,
    state: SignalState,
    strategy: str,
    symbol: str,
    tf: str,
) -> list[pd.Series]:
    """Return signal rows whose legacy state key has not been marked sent."""
    if df.empty or "signal" not in df:
        return []
    signals = df[df["signal"].fillna(0).astype(int).ne(0)]
    result = []
    for _, row in signals.iterrows():
        key = signal_key(strategy, symbol, tf, row["bartime"], int(row["signal"]))
        if not state.has(key):
            result.append(row)
    return result


def _send_realtime_alert(
    notifier: Notifier,
    signal: RealtimeSignal,
    *,
    chat_id: str | None = None,
) -> Any:
    kwargs = dict(signal.alert_kwargs)
    webhook_attr = kwargs.pop("discord_webhook_attr", None)
    if webhook_attr and "discord_webhook" not in kwargs:
        kwargs["discord_webhook"] = getattr(notifier, str(webhook_attr), None)
    if signal.alert_backend == "telegram":
        return notifier.send(signal.alert_message, chat_id=chat_id, backend="telegram", **kwargs)
    return notifier.send(signal.alert_message, backend=signal.alert_backend, **kwargs)


def _check_realtime_once(
    *,
    strategy: str,
    symbols: list[str],
    tf: str,
    bars: int,
    state: SignalState,
    notifier: Notifier,
    scanner: Callable[..., RealtimeScanResult],
    scanner_kwargs: dict[str, Any],
    output_dir: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
    closed_only: bool = True,
    export_on_signal: bool = True,
    chat_id: str | None = None,
    show_progress: bool = True,
    latest_bars: list[pd.Timestamp] | None = None,
    sent_signals: list[SentSignalEvent] | None = None,
    max_alert_age_minutes: int | None = None,
    outbox: Any | None = None,
    redis_on: bool = False,
    event_type: str | None = None,
    overrides_hash: str | None = None,
    wake_delivery: Callable[[], None] | None = None,
    progress_label: str | None = None,
    load_error_label: str = "load error",
) -> list[str]:
    events: list[str] = []
    runtime_event_type = str(event_type or "").strip().lower()
    runtime_overrides_hash = overrides_hash or _overrides_hash(overrides or {})
    for symbol in symbols:
        if show_progress:
            _print_status(f"scanning {progress_label or strategy} {symbol} {tf} ({bars} bars)")
        try:
            scan_result = scanner(
                symbol=symbol,
                tf=tf,
                bars=bars,
                overrides=overrides,
                closed_only=closed_only,
                event_type=runtime_event_type,
                **scanner_kwargs,
            )
        except Exception as exc:
            events.append(f"{symbol} {tf}: {load_error_label} - {exc}")
            continue

        latest_bar_ts = scan_result.latest_bar_ts
        if latest_bar_ts is not None and latest_bars is not None:
            latest_bars.append(latest_bar_ts)
        if latest_bar_ts is not None:
            _warn_if_stale_bar(symbol, tf, latest_bar_ts)
        latest_bar = _format_bar_ts(latest_bar_ts)

        new_signals = [signal for signal in scan_result.signals if not state.has(signal.dedup_key)]
        if not new_signals:
            events.append(f"{symbol} {tf}: no new {scan_result.no_signal_text} (latest bar {latest_bar})")
            continue

        for signal in new_signals:
            event_time = _as_utc_ts(signal.event_time)
            signal_time = event_time.strftime("%Y-%m-%d %H:%M")
            if signal.pre_skip_reason:
                mark_status = _mark_historical_seen(state, signal.dedup_key, dry_run=notifier.dry_run)
                events.append(
                    f"{symbol} {tf}: skipped {signal.skip_text} {signal_time} UTC "
                    f"{signal.pre_skip_reason} ({mark_status})"
                )
                continue

            is_historical, age, age_limit = _is_historical_alert(
                event_time,
                tf,
                max_alert_age_minutes=max_alert_age_minutes,
            )
            if is_historical:
                mark_status = _mark_historical_seen(state, signal.dedup_key, dry_run=notifier.dry_run)
                events.append(
                    f"{symbol} {tf}: skipped historical {signal.historical_text} {signal_time} UTC "
                    f"age={_format_timedelta(age)} limit={_format_timedelta(age_limit)} ({mark_status})"
                )
                continue

            signal_id = _emit_to_outbox(
                redis_on=redis_on,
                dry_run=notifier.dry_run,
                outbox=outbox,
                strategy=signal.strategy,
                event_type=signal.event_type or runtime_event_type,
                symbol=symbol,
                tf=tf,
                signal=signal.payload_source,
                event_close=event_time,
                overrides_hash=runtime_overrides_hash,
                wake_delivery=wake_delivery,
            )

            if export_on_signal and scan_result.export_frame is not None:
                try:
                    export_signals(scan_result.export_frame, symbol=symbol, strategy=signal.strategy, output_dir=output_dir)
                except Exception as exc:
                    logger.warning(
                        "CSV export failed for %s %s %s: %s",
                        signal.strategy,
                        symbol,
                        tf,
                        exc,
                        exc_info=True,
                    )

            result = _send_realtime_alert(notifier, signal, chat_id=chat_id)
            if result.sent and not notifier.dry_run:
                state.add(signal.dedup_key)
                if sent_signals is not None:
                    sent_signals.append(
                        SentSignalEvent(
                            strategy=signal.strategy,
                            symbol=symbol.upper(),
                            tf=tf.upper(),
                            side=signal.side,
                            event_time=event_time,
                            sent_at=pd.Timestamp.now("UTC"),
                            kind=signal.kind,
                        )
                    )
                suffix = f" redis={signal_id}" if signal_id else ""
                events.append(f"{symbol} {tf}: alerted via {result.backend} {signal.event_text} {signal_time} UTC{suffix}")
            elif result.sent:
                events.append(f"{symbol} {tf}: dry-run OK {signal.event_text} {signal_time} UTC")
            else:
                suffix = f"; redis queued {signal_id}" if signal_id else ""
                events.append(f"{symbol} {tf}: notifier FAILED - {result.detail}{suffix}")

    return events


def check_once(
    *,
    strategy: str,
    symbols: list[str],
    tf: str,
    bars: int,
    state: SignalState,
    notifier: Notifier,
    output_dir: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
    closed_only: bool = True,
    export_on_signal: bool = True,
    chat_id: str | None = None,
    show_progress: bool = True,
    latest_bars: list[pd.Timestamp] | None = None,
    sent_signals: list[SentSignalEvent] | None = None,
    max_alert_age_minutes: int | None = None,
    outbox: Any | None = None,
    redis_on: bool = False,
    event_type: str | None = None,
    overrides_hash: str | None = None,
    wake_delivery: Callable[[], None] | None = None,
) -> list[str]:
    """Check one non-AI realtime scan group through its strategy adapter."""
    scanner = combo_realtime.detect_realtime_signals if strategy.lower() == "combo" else ma_cross_realtime.detect_realtime_signals
    return _check_realtime_once(
        strategy=strategy,
        symbols=symbols,
        tf=tf,
        bars=bars,
        state=state,
        notifier=notifier,
        scanner=scanner,
        scanner_kwargs={"strategy": strategy, "run_strategy_frame_func": run_strategy_frame},
        output_dir=output_dir,
        overrides=overrides,
        closed_only=closed_only,
        export_on_signal=export_on_signal,
        chat_id=chat_id,
        show_progress=show_progress,
        latest_bars=latest_bars,
        sent_signals=sent_signals,
        max_alert_age_minutes=max_alert_age_minutes,
        outbox=outbox,
        redis_on=redis_on,
        event_type=event_type,
        overrides_hash=overrides_hash,
        wake_delivery=wake_delivery,
    )


def check_ai_trend_once(
    *,
    symbols: list[str],
    tf: str,
    bars: int,
    state: SignalState,
    notifier: Notifier,
    event_type: str | None,
    overrides: dict[str, Any] | None = None,
    closed_only: bool = True,
    chat_id: str | None = None,
    show_progress: bool = True,
    latest_bars: list[pd.Timestamp] | None = None,
    sent_signals: list[SentSignalEvent] | None = None,
    max_alert_age_minutes: int | None = None,
    outbox: Any | None = None,
    redis_on: bool = False,
    overrides_hash: str | None = None,
    wake_delivery: Callable[[], None] | None = None,
) -> list[str]:
    """Check one AI Trend H3/M45 realtime group through the AI Trend adapter."""
    normalized_event_type = ai_trend_realtime.normalize_ai_trend_event_type(event_type, tf)
    return _check_realtime_once(
        strategy="ai_trend",
        symbols=symbols,
        tf=tf,
        bars=bars,
        state=state,
        notifier=notifier,
        scanner=ai_trend_realtime.detect_realtime_signals,
        scanner_kwargs={"run_ai_trend_alerts_func": run_ai_trend_alerts},
        overrides=overrides,
        closed_only=closed_only,
        export_on_signal=False,
        chat_id=chat_id,
        show_progress=show_progress,
        latest_bars=latest_bars,
        sent_signals=sent_signals,
        max_alert_age_minutes=max_alert_age_minutes,
        outbox=outbox,
        redis_on=redis_on,
        event_type=normalized_event_type,
        overrides_hash=overrides_hash,
        wake_delivery=wake_delivery,
        progress_label=f"ai_trend {normalized_event_type}",
        load_error_label="AI Trend load error",
    )


def run_ai_trend_alerts(
    *,
    symbol: str,
    tf: str,
    bars: int,
    event_type: str | None,
    overrides: dict[str, Any] | None = None,
    closed_only: bool = True,
) -> tuple[list[Any], pd.Timestamp | None]:
    """Compatibility wrapper around the AI Trend realtime adapter."""
    return ai_trend_realtime.run_ai_trend_alerts(
        symbol=symbol,
        tf=tf,
        bars=bars,
        event_type=event_type,
        overrides=overrides,
        closed_only=closed_only,
        get_strategy_func=get_strategy,
        load_ohlcv_func=_load_ohlcv,
        drop_open_bar_func=_drop_open_bar,
        latest_bar_ts_func=_latest_bar_ts,
        warn_if_stale_bar_func=_warn_if_stale_bar,
        build_frames_func=build_ai_trend_frames,
    )


def _drop_open_bar(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Drop the last row when it is still inside the currently open candle."""
    if df.empty or "bartime" not in df:
        return df
    minutes = config.TF_MINUTES.get(str(tf).strip().upper())
    if not minutes:
        return df
    now_utc = pd.Timestamp.now("UTC")
    cutoff = now_utc - pd.Timedelta(minutes=int(minutes))
    bar_times = pd.to_datetime(df["bartime"], errors="coerce")
    if getattr(bar_times.dt, "tz", None) is None:
        bar_times = bar_times.dt.tz_localize("UTC")
    else:
        bar_times = bar_times.dt.tz_convert("UTC")
    return df.loc[bar_times <= cutoff].reset_index(drop=True)


def _next_bar_close_utc(
    tf: str,
    buffer_seconds: int = _DEFAULT_BAR_CLOSE_BUFFER_SECONDS,
) -> pd.Timestamp:
    minutes = config.TF_MINUTES.get(str(tf).strip().upper())
    now = pd.Timestamp.now("UTC")
    if not minutes:
        return now + pd.Timedelta(seconds=300)
    period = pd.Timedelta(minutes=minutes)
    floored = now.floor(period)
    next_close = floored + period + pd.Timedelta(seconds=max(0, int(buffer_seconds)))
    if next_close <= now:
        next_close += period
    return next_close


def _latest_bar_text(df: pd.DataFrame) -> str:
    return _format_bar_ts(_latest_bar_ts(df))


def _latest_bar_ts(df: pd.DataFrame) -> pd.Timestamp | None:
    if df.empty or "bartime" not in df:
        return None
    latest = pd.to_datetime(df["bartime"], errors="coerce").dropna()
    if latest.empty:
        return None
    return pd.Timestamp(latest.iloc[-1])


def _format_bar_ts(value: pd.Timestamp | None) -> str:
    if value is None:
        return "-"
    return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M")


def _print_status(message: str) -> None:
    now = pd.Timestamp.now("UTC").strftime("%H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def _prune_sent_signal_events(sent_events: list[SentSignalEvent], hours: int = 24) -> None:
    cutoff = pd.Timestamp.now("UTC") - pd.Timedelta(hours=int(hours))
    sent_events[:] = [event for event in sent_events if _as_utc_ts(event.sent_at) >= cutoff]
