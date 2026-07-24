"""Runtime threads for the realtime signal watcher.

The watcher entrypoint wires these loops together. This module owns event
coalescing, Redis bar-ready subscription, fallback scanning, worker dispatch,
and delivery relay retry behavior.
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd

from core_python.notify import redis_publisher
from core_python.notify.alerts import Notifier
from core_python.notify.delivery_outbox import DeliveryOutbox
from core_python.notify.detector import (
    SentSignalEvent,
    _as_utc_iso,
    _as_utc_ts,
    _drop_open_bar,
    _find_matching_groups,
    _group_runtime_key,
    _latest_bar_ts,
    _load_ohlcv,
    _overrides_hash,
    _utcnow_iso,
    check_ai_trend_once,
    check_once,
    get_db_load_count,
)
from core_python.notify.state import SignalState

_DEFAULT_FALLBACK_SCAN_SECONDS = int(os.getenv("WATCHER_FALLBACK_SCAN_SECONDS", "300"))
_DEFAULT_RELAY_RETRY_SECONDS = int(os.getenv("SIGNAL_RELAY_RETRY_SECONDS", "30"))
_DEFAULT_RELAY_ALERT_SECONDS = int(os.getenv("SIGNAL_RELAY_ALERT_SECONDS", "300"))
_DEFAULT_RELAY_MAX_BACKOFF_SECONDS = int(os.getenv("SIGNAL_RELAY_MAX_BACKOFF_SECONDS", "300"))
_DEFAULT_QUEUE_MAXSIZE = int(os.getenv("SIGNAL_WATCHER_QUEUE_MAXSIZE", "1000"))

logger = logging.getLogger(__name__)
_WAKE_RELAY = threading.Event()
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT_EVENTS: set[tuple[str, str, str]] = set()


@dataclass(frozen=True)
class GroupTriggerEvent:
    """A bar-ready or fallback event for one symbol/timeframe."""

    source: str
    symbol: str
    tf: str
    bartime: str = ""


def _event_bartime_key(event: GroupTriggerEvent) -> str:
    return _as_utc_iso(event.bartime) if event.bartime else ""


def _trigger_bartime_ts(event: GroupTriggerEvent) -> pd.Timestamp | None:
    if not event.bartime:
        return None
    try:
        return _as_utc_ts(event.bartime)
    except Exception:
        return None


def _enqueue_trigger(event_queue: queue.Queue, event: GroupTriggerEvent) -> bool:
    normalized = GroupTriggerEvent(
        source=event.source,
        symbol=str(event.symbol).strip().upper(),
        tf=str(event.tf).strip().upper(),
        bartime=str(event.bartime or ""),
    )
    key = (normalized.symbol, normalized.tf, _event_bartime_key(normalized))
    with _INFLIGHT_LOCK:
        if key in _INFLIGHT_EVENTS:
            return False
        _INFLIGHT_EVENTS.add(key)
    try:
        event_queue.put_nowait(normalized)
        return True
    except queue.Full:
        with _INFLIGHT_LOCK:
            _INFLIGHT_EVENTS.discard(key)
        logger.warning("signal watcher queue full; dropped %s %s %s", normalized.symbol, normalized.tf, normalized.bartime)
        return False


def _release_inflight(event: GroupTriggerEvent) -> None:
    key = (
        str(event.symbol).strip().upper(),
        str(event.tf).strip().upper(),
        _event_bartime_key(event),
    )
    with _INFLIGHT_LOCK:
        _INFLIGHT_EVENTS.discard(key)


def _pending_entry_due(entry: dict[str, Any]) -> bool:
    last_attempt = entry.get("last_attempt")
    if not last_attempt:
        return True
    try:
        last_ts = _as_utc_ts(last_attempt)
    except Exception:
        return True
    attempts = max(0, int(entry.get("attempts") or 0))
    backoff_seconds = min(2 ** min(attempts, 16), _DEFAULT_RELAY_MAX_BACKOFF_SECONDS)
    return pd.Timestamp.now("UTC") >= last_ts + pd.Timedelta(seconds=backoff_seconds)


def _drain_outbox_once(outbox: DeliveryOutbox) -> tuple[int, int]:
    delivered = 0
    failed = 0
    pending = outbox.get_pending()
    for signal_id, entry in pending.items():
        if not _pending_entry_due(entry):
            continue
        payload = dict(entry.get("payload") or {})
        payload["delivered_at"] = _utcnow_iso()
        try:
            stream_id = redis_publisher.xadd_signal(payload)
        except Exception:
            stream_id = None
            logger.exception("Redis XADD raised for signal_id=%s", signal_id)
        if stream_id:
            outbox.mark_delivered(signal_id)
            delivered += 1
            logger.info("Redis signal delivered signal_id=%s stream_id=%s", signal_id, stream_id)
        else:
            outbox.record_attempt(signal_id)
            failed += 1
    return delivered, failed


def _delivery_relay_loop(
    *,
    outbox: DeliveryOutbox,
    notifier: Notifier,
    retry_seconds: int = _DEFAULT_RELAY_RETRY_SECONDS,
    alert_seconds: int = _DEFAULT_RELAY_ALERT_SECONDS,
) -> None:
    last_alert_at: pd.Timestamp | None = None
    while True:
        try:
            _drain_outbox_once(outbox)

            age = outbox.oldest_pending_age_seconds()
            if age is not None and age >= alert_seconds:
                now = pd.Timestamp.now("UTC")
                if last_alert_at is None or now >= last_alert_at + pd.Timedelta(seconds=alert_seconds):
                    last_alert_at = now
                    message = (
                        "<b>SEN05 Redis signal relay pending</b>\n"
                        f"pending={outbox.pending_count()} oldest={int(age)}s"
                    )
                    logger.error(message.replace("<b>", "").replace("</b>", ""))
                    try:
                        notifier.send(message, backend="telegram")
                    except Exception:
                        logger.exception("relay pending alert failed")
            elif age is None:
                last_alert_at = None
        except Exception:
            logger.exception("delivery relay loop failed")
        finally:
            _WAKE_RELAY.wait(timeout=max(1, int(retry_seconds)))
            _WAKE_RELAY.clear()


def _decode_redis_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _bar_ready_subscriber_loop(event_queue: queue.Queue) -> None:
    pattern = "bar_ready:*"
    while True:
        client = redis_publisher._get_client()
        if client is None:
            time.sleep(_DEFAULT_RELAY_RETRY_SECONDS)
            continue
        pubsub = None
        try:
            pubsub = client.pubsub()
            pubsub.psubscribe(pattern)
            logger.info("subscribed to Redis pattern %s", pattern)
            for message in pubsub.listen():
                if message.get("type") != "pmessage":
                    continue
                channel = _decode_redis_value(message.get("channel", ""))
                data = _decode_redis_value(message.get("data", ""))
                parts = channel.split(":")
                if len(parts) != 3:
                    logger.warning("ignored malformed bar_ready channel: %s", channel)
                    continue
                _enqueue_trigger(
                    event_queue,
                    GroupTriggerEvent(source="bar_ready", symbol=parts[1], tf=parts[2], bartime=data),
                )
        except Exception as exc:
            logger.warning("Redis bar_ready subscriber disconnected: %s", exc, exc_info=True)
            time.sleep(_DEFAULT_RELAY_RETRY_SECONDS)
        finally:
            if pubsub is not None:
                try:
                    pubsub.close()
                except Exception:
                    pass


def _latest_closed_bartime(symbol: str, tf: str) -> pd.Timestamp | None:
    frame = _load_ohlcv(symbol, tf, 2)
    frame = _drop_open_bar(frame, tf)
    return _latest_bar_ts(frame)


def _fallback_scan_once(event_queue: queue.Queue, scan_groups: list[dict[str, Any]]) -> int:
    enqueued = 0
    seen: set[tuple[str, str]] = set()
    for group in scan_groups:
        tf = str(group.get("tf", "")).strip().upper()
        for symbol in group.get("symbols", []):
            symbol_code = str(symbol).strip().upper()
            if not symbol_code or not tf:
                continue
            key = (symbol_code, tf)
            if key in seen:
                continue
            seen.add(key)
            try:
                latest = _latest_closed_bartime(symbol_code, tf)
            except Exception as exc:
                logger.warning("fallback latest bar check failed for %s %s: %s", symbol_code, tf, exc)
                continue
            if latest is None:
                continue
            if _enqueue_trigger(
                event_queue,
                GroupTriggerEvent(
                    source="fallback",
                    symbol=symbol_code,
                    tf=tf,
                    bartime=_as_utc_iso(latest),
                ),
            ):
                enqueued += 1
    return enqueued


def _fallback_scanner_loop(
    *,
    event_queue: queue.Queue,
    scan_groups: list[dict[str, Any]],
    interval_seconds: int,
) -> None:
    while True:
        try:
            started = time.perf_counter()
            enqueued = _fallback_scan_once(event_queue, scan_groups)
            elapsed = time.perf_counter() - started
            logger.info("fallback scan enqueued=%s elapsed=%.2fs", enqueued, elapsed)
        except Exception:
            logger.exception("fallback scanner loop failed")
        time.sleep(max(5, int(interval_seconds)))


def _run_group_for_symbol(
    *,
    group: dict[str, Any],
    symbol: str,
    args: argparse.Namespace,
    state: SignalState,
    notifier: Notifier,
    closed_only: bool,
    outbox: Any,
    redis_on: bool,
    sent_signals: list[SentSignalEvent] | None,
) -> list[str]:
    overrides = group.get("overrides") or {}
    overrides_hash = _overrides_hash(overrides)
    if group["strategy"] == "ai_trend":
        return check_ai_trend_once(
            symbols=[symbol],
            tf=group["tf"],
            bars=group.get("bars", args.bars),
            state=state,
            notifier=notifier,
            event_type=group.get("event_type"),
            overrides=overrides,
            closed_only=closed_only,
            chat_id=group.get("chat_id"),
            show_progress=not args.quiet,
            sent_signals=sent_signals,
            max_alert_age_minutes=args.max_alert_age_minutes,
            outbox=outbox,
            redis_on=redis_on,
            overrides_hash=overrides_hash,
            wake_delivery=_WAKE_RELAY.set,
        )
    return check_once(
        strategy=group["strategy"],
        symbols=[symbol],
        tf=group["tf"],
        bars=group.get("bars", args.bars),
        state=state,
        notifier=notifier,
        output_dir=args.output_dir,
        overrides=overrides,
        closed_only=closed_only,
        export_on_signal=not args.no_export,
        chat_id=group.get("chat_id"),
        show_progress=not args.quiet,
        sent_signals=sent_signals,
        max_alert_age_minutes=args.max_alert_age_minutes,
        outbox=outbox,
        redis_on=redis_on,
        event_type=group.get("event_type"),
        overrides_hash=overrides_hash,
        wake_delivery=_WAKE_RELAY.set,
    )


def _run_all_groups_once(
    *,
    scan_groups: list[dict[str, Any]],
    args: argparse.Namespace,
    state: SignalState,
    notifier: Notifier,
    closed_only: bool,
    outbox: Any,
    redis_on: bool,
    sent_signals: list[SentSignalEvent] | None,
) -> None:
    for group in scan_groups:
        started = time.perf_counter()
        db_loads_before = get_db_load_count()
        events: list[str] = []
        for symbol in group.get("symbols", []):
            events.extend(
                _run_group_for_symbol(
                    group=group,
                    symbol=str(symbol).strip().upper(),
                    args=args,
                    state=state,
                    notifier=notifier,
                    closed_only=closed_only,
                    outbox=outbox,
                    redis_on=redis_on,
                    sent_signals=sent_signals,
                )
            )
        ts = pd.Timestamp.now("UTC").strftime("%H:%M:%S")
        for event in events:
            print(f"[{ts}] {event}", flush=True)
        print(
            f"[{ts}] [{group['tf']}] group done: symbols={len(group.get('symbols', []))}, "
            f"db_loads={get_db_load_count() - db_loads_before}, elapsed={time.perf_counter() - started:.2f}s",
            flush=True,
        )


def _handle_trigger_event(
    *,
    event: GroupTriggerEvent,
    scan_groups: list[dict[str, Any]],
    args: argparse.Namespace,
    state: SignalState,
    notifier: Notifier,
    closed_only: bool,
    outbox: Any,
    redis_on: bool,
    sent_signals: list[SentSignalEvent] | None,
    last_processed: dict[tuple[str, str, str, str, str], pd.Timestamp],
) -> None:
    matches = _find_matching_groups(event.symbol, event.tf, scan_groups)
    if not matches:
        logger.info("bar_ready had no matching groups: %s %s", event.symbol, event.tf)
        return
    event_ts = _trigger_bartime_ts(event)
    for group in matches:
        dedup_key = (*_group_runtime_key(group), str(event.symbol).strip().upper())
        last_ts = last_processed.get(dedup_key)
        if event_ts is not None and last_ts is not None and event_ts <= last_ts:
            logger.debug(
                "skip already processed trigger %s %s %s for group=%s",
                event.symbol,
                event.tf,
                event.bartime,
                dedup_key,
            )
            continue

        started = time.perf_counter()
        db_loads_before = get_db_load_count()
        try:
            events = _run_group_for_symbol(
                group=group,
                symbol=str(event.symbol).strip().upper(),
                args=args,
                state=state,
                notifier=notifier,
                closed_only=closed_only,
                outbox=outbox,
                redis_on=redis_on,
                sent_signals=sent_signals,
            )
        except Exception as exc:
            events = [f"[ERROR] group {group.get('tf')} {event.symbol}: {exc}"]
            logger.exception("trigger handling failed for %s", dedup_key)

        ts = pd.Timestamp.now("UTC").strftime("%H:%M:%S")
        for line in events:
            print(f"[{ts}] {line}", flush=True)
        print(
            f"[{ts}] [{group['tf']}] trigger={event.source} {event.symbol} {event.bartime or '-'} "
            f"db_loads={get_db_load_count() - db_loads_before} elapsed={time.perf_counter() - started:.2f}s",
            flush=True,
        )

        has_retryable_failure = any(
            "load error" in line or "notifier FAILED" in line or line.startswith("[ERROR]")
            for line in events
        )
        if event_ts is not None and not has_retryable_failure:
            last_processed[dedup_key] = event_ts


def _worker_loop(
    *,
    event_queue: queue.Queue,
    scan_groups: list[dict[str, Any]],
    args: argparse.Namespace,
    state: SignalState,
    notifier: Notifier,
    closed_only: bool,
    outbox: Any,
    redis_on: bool,
    sent_signals: list[SentSignalEvent] | None,
) -> None:
    last_processed: dict[tuple[str, str, str, str, str], pd.Timestamp] = {}
    while True:
        event = event_queue.get()
        try:
            _handle_trigger_event(
                event=event,
                scan_groups=scan_groups,
                args=args,
                state=state,
                notifier=notifier,
                closed_only=closed_only,
                outbox=outbox,
                redis_on=redis_on,
                sent_signals=sent_signals,
                last_processed=last_processed,
            )
        except Exception:
            logger.exception("signal worker loop failed")
        finally:
            _release_inflight(event)
            event_queue.task_done()
