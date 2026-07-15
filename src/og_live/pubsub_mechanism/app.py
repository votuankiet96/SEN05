"""Long-running OG Live Pub/Sub mechanism application."""

from __future__ import annotations

import json
import logging
import threading
import time

import pandas as pd
import redis

from og_core import config as core_config
from og_live.common import pipeline
from og_live.common.audit import AuditLogger, elapsed_ms
from og_live.common.candle_snapshot import (
    MalformedSnapshotError,
    parse_snapshot_event,
    parse_state_snapshot,
    snapshot_matches_event,
)
from og_live.common.outbox import DeliveryOutbox
from og_live.common.settings import WatchedItem
from og_live.common.state import ProcessedSnapshotState, SignalState
from og_live.pubsub_mechanism import settings
from og_live.pubsub_mechanism.signals import (
    DUPLICATE_SIGNAL,
    get_input_client,
    publish_signal,
    reset_clients,
    reset_input_client,
)

logger = logging.getLogger(__name__)


class PubSubSignalApp:
    """Consumes DP candle snapshot Pub/Sub messages and publishes Pub/Sub mechanism signals."""

    def __init__(
        self,
        *,
        watched: list[WatchedItem] | None = None,
        state: SignalState | None = None,
        processed_snapshots: ProcessedSnapshotState | None = None,
        outbox: DeliveryOutbox | None = None,
        audit: AuditLogger | None = None,
    ) -> None:
        self.watched = watched if watched is not None else settings.load_watched_items()
        runtime_dir = settings.runtime_dir()
        self.state = state if state is not None else SignalState(runtime_dir=runtime_dir)
        self.processed_snapshots = (
            processed_snapshots
            if processed_snapshots is not None
            else ProcessedSnapshotState(runtime_dir=runtime_dir)
        )
        self.outbox = outbox if outbox is not None else DeliveryOutbox(runtime_dir=runtime_dir)
        self.audit = audit if audit is not None else AuditLogger(
            mechanism="pubsub",
            path=settings.audit_file_path(),
            enabled=settings.AUDIT_LOG_ENABLED,
            max_bytes=settings.AUDIT_LOG_MAX_BYTES,
            backup_count=settings.AUDIT_LOG_BACKUP_COUNT,
        )
        self._last_outbox_retry = 0.0

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        """Run the Pub/Sub loop until interrupted or stop_event is set."""
        logger.info(
            "og_live_pubsub: starting channel=%s state_prefix=%s watched=%s enabled=%s",
            settings.PUBSUB_CHANNEL,
            settings.CANDLE_STATE_PREFIX,
            len(self.watched),
            settings.ENABLED,
        )
        self.audit.write(
            "service_started",
            status="running" if settings.ENABLED else "disabled",
            source="redis_pubsub",
            input_db=settings.INPUT_REDIS_DB,
            output_db=settings.OUTPUT_REDIS_DB,
            pubsub_channel=settings.PUBSUB_CHANNEL,
            watched_items=len(self.watched),
            enabled=settings.ENABLED,
            audit_log=str(settings.audit_file_path()),
        )
        while stop_event is None or not stop_event.is_set():
            if not settings.ENABLED:
                logger.warning("og_live_pubsub: disabled by OG_PUBSUB_ENABLED=false")
                self.audit.write(
                    "service_disabled",
                    status="waiting",
                    source="redis_pubsub",
                    pubsub_channel=settings.PUBSUB_CHANNEL,
                    message="OG_PUBSUB_ENABLED=false",
                )
                if _wait(stop_event, settings.RESTART_PAUSE_SECONDS):
                    break
                continue
            try:
                self._run_connected_loop(stop_event)
            except Exception:
                logger.exception("og_live_pubsub: live loop failed, restarting")
                self.audit.write("service_error", status="restarting", message="live loop failed")
                reset_clients()
                if _wait(stop_event, settings.RESTART_PAUSE_SECONDS):
                    break

    def run_once(self, *, timeout_seconds: float = 30.0) -> int:
        """Subscribe and process at most one Pub/Sub message. Useful for smoke tests."""
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        pubsub = get_input_client().pubsub(ignore_subscribe_messages=True)
        try:
            pubsub.subscribe(settings.PUBSUB_CHANNEL)
            while time.monotonic() < deadline:
                message = pubsub.get_message(timeout=min(1.0, deadline - time.monotonic()))
                if not message or message.get("type") != "message":
                    continue
                return self._handle_message(message.get("data"))
            return 0
        finally:
            pubsub.close()

    def retry_outbox(self) -> int:
        """Retry queued Redis publishes and mark delivered signals in local state."""
        delivered_payloads = self.outbox.retry_all(publish_signal)
        for payload in delivered_payloads:
            signal_id = payload.get("signal_id")
            if signal_id:
                self.state.mark_delivered(str(signal_id))
                self.audit.write(
                    "outbox_signal_published",
                    status="published",
                    signal_id=signal_id,
                    strategy=payload.get("strategy"),
                    symbol=payload.get("symbol"),
                    timeframe=payload.get("timeframe"),
                    snapshot_version=payload.get("source_snapshot_version"),
                    bar_time=payload.get("bar_time"),
                    output_db=settings.OUTPUT_REDIS_DB,
                    message="pending signal delivered from local outbox",
                )
        if delivered_payloads:
            logger.info("og_live_pubsub: outbox delivered %d signal(s)", len(delivered_payloads))
        return len(delivered_payloads)

    def _run_connected_loop(self, stop_event: threading.Event | None) -> None:
        pubsub = get_input_client().pubsub(ignore_subscribe_messages=True)
        try:
            pubsub.subscribe(settings.PUBSUB_CHANNEL)
            logger.info("og_live_pubsub: subscribed to %s", settings.PUBSUB_CHANNEL)
            while stop_event is None or not stop_event.is_set():
                now = time.monotonic()
                if now - self._last_outbox_retry >= settings.OUTBOX_RETRY_INTERVAL_SECONDS:
                    self.retry_outbox()
                    self._last_outbox_retry = now

                message = pubsub.get_message(timeout=settings.MESSAGE_POLL_TIMEOUT_SECONDS)
                if not message or message.get("type") != "message":
                    continue
                self._handle_message(message.get("data"))
        except redis.RedisError as exc:
            logger.warning("og_live_pubsub: Redis Pub/Sub read failed: %s", exc)
            self.audit.write(
                "input_read_error",
                status="redis_error",
                source="redis_pubsub",
                pubsub_channel=settings.PUBSUB_CHANNEL,
                message=str(exc),
            )
            reset_input_client()
            raise
        finally:
            pubsub.close()

    def _handle_message(self, raw_message: object) -> int:
        event_start = time.monotonic()
        try:
            fields = _decode_message(raw_message)
            event = parse_snapshot_event(fields)
        except (MalformedSnapshotError, ValueError, TypeError) as exc:
            logger.warning("og_live_pubsub: malformed Pub/Sub message, skipping: %s", exc)
            self.audit.write(
                "event_skipped",
                status="malformed",
                source="redis_pubsub",
                pubsub_channel=settings.PUBSUB_CHANNEL,
                message=str(exc),
            )
            return 0

        items = pipeline.matching_items(event.symbol, event.tf, self.watched)
        if not items:
            return 0

        strategies = [item.strategy for item in items]
        self.audit.write(
            "event_received",
            status="matched_watchlist",
            source="redis_pubsub",
            pubsub_channel=settings.PUBSUB_CHANNEL,
            input_db=settings.INPUT_REDIS_DB,
            strategies=strategies,
            symbol=event.symbol,
            timeframe=event.tf,
            snapshot_version=event.snapshot_version,
            bar_time=event.bar_time,
            state_key=event.state_key,
            event_bars_count=event.bars_count,
            source_published_at_utc=event.published_at_utc,
        )

        if self._event_is_stale(event.published_at_utc):
            logger.warning(
                "og_live_pubsub: stale Pub/Sub event skipped symbol=%s tf=%s version=%s",
                event.symbol,
                event.tf,
                event.snapshot_version,
            )
            self.audit.write(
                "event_skipped",
                status="stale",
                source="redis_pubsub",
                pubsub_channel=settings.PUBSUB_CHANNEL,
                strategies=strategies,
                symbol=event.symbol,
                timeframe=event.tf,
                snapshot_version=event.snapshot_version,
                bar_time=event.bar_time,
                state_key=event.state_key,
                message="input event is older than max allowed age",
            )
            return 0

        try:
            raw_snapshot = get_input_client().get(event.state_key)
        except redis.RedisError as exc:
            self.audit.write(
                "state_load_error",
                status="redis_error",
                source="redis_pubsub",
                pubsub_channel=settings.PUBSUB_CHANNEL,
                strategies=strategies,
                symbol=event.symbol,
                timeframe=event.tf,
                snapshot_version=event.snapshot_version,
                bar_time=event.bar_time,
                state_key=event.state_key,
                message=str(exc),
            )
            reset_input_client()
            raise
        if raw_snapshot is None:
            logger.warning("og_live_pubsub: state key missing for Pub/Sub event: %s", event.state_key)
            self.audit.write(
                "state_missing",
                status="missing",
                source="redis_pubsub",
                pubsub_channel=settings.PUBSUB_CHANNEL,
                strategies=strategies,
                symbol=event.symbol,
                timeframe=event.tf,
                snapshot_version=event.snapshot_version,
                bar_time=event.bar_time,
                state_key=event.state_key,
                message="state key was not found in Redis",
            )
            return 0

        try:
            snapshot = parse_state_snapshot(raw_snapshot)
        except MalformedSnapshotError as exc:
            logger.warning("og_live_pubsub: malformed state snapshot for key=%s, skipping: %s", event.state_key, exc)
            self.audit.write(
                "state_invalid",
                status="malformed",
                source="redis_pubsub",
                pubsub_channel=settings.PUBSUB_CHANNEL,
                strategies=strategies,
                symbol=event.symbol,
                timeframe=event.tf,
                snapshot_version=event.snapshot_version,
                bar_time=event.bar_time,
                state_key=event.state_key,
                message=str(exc),
            )
            return 0

        if not snapshot_matches_event(snapshot, event):
            logger.warning(
                "og_live_pubsub: snapshot state no longer matches event, skipping event=%s %s %s state_latest=%s",
                event.symbol,
                event.tf,
                event.bar_time,
                snapshot.latest_bar_time,
            )
            self.audit.write(
                "state_mismatch",
                status="mismatch",
                source="redis_pubsub",
                pubsub_channel=settings.PUBSUB_CHANNEL,
                strategies=strategies,
                symbol=event.symbol,
                timeframe=event.tf,
                snapshot_version=event.snapshot_version,
                bar_time=event.bar_time,
                state_key=event.state_key,
                state_latest_bar_time=snapshot.latest_bar_time,
                state_snapshot_version=snapshot.snapshot_version,
                message="snapshot state no longer matches triggering event",
            )
            return 0

        self.audit.write(
            "state_loaded",
            status="validated",
            source="redis_pubsub",
            pubsub_channel=settings.PUBSUB_CHANNEL,
            strategies=strategies,
            symbol=event.symbol,
            timeframe=event.tf,
            snapshot_version=event.snapshot_version,
            bar_time=event.bar_time,
            state_key=event.state_key,
            bars_count=snapshot.bars_count,
            state_latest_bar_time=snapshot.latest_bar_time,
            elapsed_ms=elapsed_ms(event_start, time.monotonic()),
        )

        delivered = 0
        for item in items:
            process_key = settings.snapshot_process_key(item.strategy, event.snapshot_version, event.symbol, event.tf)
            if self.processed_snapshots.processed(process_key):
                self.audit.write(
                    "snapshot_skipped",
                    status="already_processed",
                    source="redis_pubsub",
                    pubsub_channel=settings.PUBSUB_CHANNEL,
                    strategy=item.strategy,
                    symbol=event.symbol,
                    timeframe=event.tf,
                    snapshot_version=event.snapshot_version,
                    bar_time=event.bar_time,
                    state_key=event.state_key,
                    process_key=process_key,
                    message="snapshot was already processed by this mechanism",
                )
                continue
            process_start = time.monotonic()
            rows = pipeline.signal_payloads_from_bars(item, event.symbol, event.tf, snapshot.bars)
            for row in rows:
                self._add_source_metadata(row, event)
            publish_stats = self._publish_new(rows, item=item, event=event)
            delivered += publish_stats["published"]
            self.processed_snapshots.mark_processed(process_key)
            self.audit.write(
                "snapshot_processed",
                status="done",
                source="redis_pubsub",
                pubsub_channel=settings.PUBSUB_CHANNEL,
                strategy=item.strategy,
                symbol=event.symbol,
                timeframe=event.tf,
                snapshot_version=event.snapshot_version,
                bar_time=event.bar_time,
                state_key=event.state_key,
                process_key=process_key,
                bars_count=snapshot.bars_count,
                signals_generated=len(rows),
                signals_published=publish_stats["published"],
                signals_queued=publish_stats["queued"],
                signals_duplicate=publish_stats["duplicate"],
                signals_skipped=publish_stats["skipped"],
                elapsed_ms=elapsed_ms(process_start, time.monotonic()),
            )
        return delivered

    def _publish_new(
        self,
        rows: list[dict[str, object]],
        *,
        item: WatchedItem,
        event: object,
    ) -> dict[str, int]:
        stats = {"published": 0, "queued": 0, "duplicate": 0, "skipped": 0}
        for row in rows:
            signal_id = str(row.get("signal_id", ""))
            strategy = str(row.get("strategy", ""))
            if not signal_id or not strategy:
                stats["skipped"] += 1
                self.audit.write(
                    "signal_skipped",
                    status="invalid_payload",
                    source="redis_pubsub",
                    pubsub_channel=settings.PUBSUB_CHANNEL,
                    strategy=item.strategy,
                    symbol=getattr(event, "symbol"),
                    timeframe=getattr(event, "tf"),
                    snapshot_version=getattr(event, "snapshot_version"),
                    bar_time=getattr(event, "bar_time"),
                    message="signal payload has no signal_id or strategy",
                )
                continue
            if self.state.seen(signal_id) or self.outbox.has(signal_id):
                stats["duplicate"] += 1
                self.audit.write(
                    "signal_skipped",
                    status="already_delivered_or_pending",
                    source="redis_pubsub",
                    pubsub_channel=settings.PUBSUB_CHANNEL,
                    strategy=strategy,
                    symbol=row.get("symbol"),
                    timeframe=row.get("timeframe"),
                    snapshot_version=row.get("source_snapshot_version"),
                    bar_time=row.get("bar_time"),
                    signal_id=signal_id,
                    side=row.get("side"),
                    message="signal already exists in local delivery state or outbox",
                )
                continue
            try:
                output_keys = settings.signal_stream_keys(strategy, row)
            except ValueError as exc:
                stats["skipped"] += 1
                self.audit.write(
                    "signal_skipped",
                    status="invalid_route",
                    source="redis_pubsub",
                    pubsub_channel=settings.PUBSUB_CHANNEL,
                    strategy=strategy,
                    symbol=row.get("symbol"),
                    timeframe=row.get("timeframe"),
                    snapshot_version=row.get("source_snapshot_version"),
                    bar_time=row.get("bar_time"),
                    signal_id=signal_id,
                    side=row.get("side"),
                    message=str(exc),
                )
                continue
            result = publish_signal(strategy, row)
            if result is not None:
                self.state.mark_delivered(signal_id)
                if result == DUPLICATE_SIGNAL:
                    stats["duplicate"] += 1
                    status = "duplicate_redis"
                else:
                    stats["published"] += 1
                    status = "published"
                self.audit.write(
                    "signal_published",
                    status=status,
                    source="redis_pubsub",
                    pubsub_channel=settings.PUBSUB_CHANNEL,
                    strategy=strategy,
                    symbol=row.get("symbol"),
                    timeframe=row.get("timeframe"),
                    snapshot_version=row.get("source_snapshot_version"),
                    bar_time=row.get("bar_time"),
                    signal_id=signal_id,
                    side=row.get("side"),
                    output_db=settings.OUTPUT_REDIS_DB,
                    output_keys=list(output_keys),
                    output_key=output_keys[0] if output_keys else None,
                    output_entry_id=None if result == DUPLICATE_SIGNAL else result,
                )
            else:
                queued = self.outbox.add_pending(strategy, row)
                stats["queued" if queued else "duplicate"] += 1
                self.audit.write(
                    "signal_queued" if queued else "signal_skipped",
                    status="publish_failed" if queued else "already_pending",
                    source="redis_pubsub",
                    pubsub_channel=settings.PUBSUB_CHANNEL,
                    strategy=strategy,
                    symbol=row.get("symbol"),
                    timeframe=row.get("timeframe"),
                    snapshot_version=row.get("source_snapshot_version"),
                    bar_time=row.get("bar_time"),
                    signal_id=signal_id,
                    side=row.get("side"),
                    output_db=settings.OUTPUT_REDIS_DB,
                    output_keys=list(output_keys),
                    message="Redis publish failed; signal queued in local outbox" if queued else "signal already pending in local outbox",
                )
        return stats

    def _event_is_stale(self, published_at: str | None) -> bool:
        event_time = _parse_event_time(published_at)
        if event_time is None:
            return False
        age = pd.Timestamp.utcnow() - event_time
        return age.total_seconds() > settings.MAX_EVENT_AGE_SECONDS

    def _add_source_metadata(self, row: dict[str, object], event: object) -> None:
        symbol = str(row.get("symbol") or "").upper()
        row["schema_version"] = 1
        row["asset_type"] = core_config.SYMBOLS.get(symbol, {}).get("asset_type", "")
        row["source_program"] = "dp_program"
        row["source_mechanism"] = "pubsub"
        row["source_pubsub_channel"] = settings.PUBSUB_CHANNEL
        row["source_state_key"] = getattr(event, "state_key")
        row["source_snapshot_version"] = getattr(event, "snapshot_version")
        row["source_bar_time"] = getattr(event, "bar_time")
        row["source_published_at_utc"] = getattr(event, "published_at_utc")


def _decode_message(raw_message: object) -> dict[str, str]:
    if isinstance(raw_message, bytes):
        text = raw_message.decode("utf-8")
    else:
        text = str(raw_message)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Pub/Sub JSON message must be an object")
    return {str(key): "" if value is None else str(value) for key, value in data.items()}


def _wait(stop_event: threading.Event | None, seconds: float) -> bool:
    if stop_event is None:
        time.sleep(seconds)
        return False
    return stop_event.wait(seconds)


def _parse_event_time(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None
    try:
        timestamp = pd.Timestamp(value)
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")
