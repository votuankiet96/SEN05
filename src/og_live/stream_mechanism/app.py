"""Long-running OG Live Stream mechanism application."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import pandas as pd
import redis

from og_core import config as core_config
from og_live.common import pipeline
from og_live.common.audit import AuditLogger, elapsed_ms
from og_live.common.candle_snapshot import (
    MalformedSnapshotError,
    normalize_fields,
    parse_snapshot_event,
    parse_state_snapshot,
    snapshot_matches_event,
)
from og_live.common.outbox import DeliveryOutbox
from og_live.common.settings import WatchedItem
from og_live.common.state import ProcessedSnapshotState, SignalState
from og_live.stream_mechanism import settings
from og_live.stream_mechanism.signals import (
    DUPLICATE_SIGNAL,
    ensure_consumer_group,
    get_input_client,
    publish_signal,
    reset_clients,
    reset_input_client,
)

logger = logging.getLogger(__name__)


class StreamSignalApp:
    """Consumes DP candle snapshot stream events and publishes Stream mechanism signals."""

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
            mechanism="stream",
            path=settings.audit_file_path(),
            enabled=settings.AUDIT_LOG_ENABLED,
            max_bytes=settings.AUDIT_LOG_MAX_BYTES,
            backup_count=settings.AUDIT_LOG_BACKUP_COUNT,
        )
        self._last_outbox_retry = 0.0

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        """Run the live stream loop until interrupted or stop_event is set."""
        logger.info(
            "og_live_stream: starting event_stream=%s state_prefix=%s group=%s consumer=%s watched=%s",
            settings.CANDLE_EVENT_STREAM,
            settings.CANDLE_STATE_PREFIX,
            settings.CONSUMER_GROUP,
            settings.CONSUMER_NAME,
            len(self.watched),
        )
        self.audit.write(
            "service_started",
            status="running",
            source="redis_stream",
            input_db=settings.INPUT_REDIS_DB,
            output_db=settings.OUTPUT_REDIS_DB,
            input_stream=settings.CANDLE_EVENT_STREAM,
            consumer_group=settings.CONSUMER_GROUP,
            consumer_name=settings.CONSUMER_NAME,
            watched_items=len(self.watched),
            audit_log=str(settings.audit_file_path()),
        )
        while stop_event is None or not stop_event.is_set():
            try:
                self._run_connected_loop(stop_event)
            except Exception:
                logger.exception("og_live_stream: live loop failed, restarting")
                self.audit.write("service_error", status="restarting", message="live loop failed")
                reset_clients()
                if _wait(stop_event, settings.RESTART_PAUSE_SECONDS):
                    break

    def run_once(self, *, block_ms: int = 1000) -> int:
        """Process pending entries, then at most one new batch. Useful for smoke tests."""
        ensure_consumer_group()
        work_done = self.retry_outbox()
        work_done += self._read_and_process("0", block_ms=None)
        if work_done == 0:
            work_done += self._read_and_process(">", block_ms=block_ms)
        return work_done

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
            logger.info("og_live_stream: outbox delivered %d signal(s)", len(delivered_payloads))
        return len(delivered_payloads)

    def _run_connected_loop(self, stop_event: threading.Event | None) -> None:
        ensure_consumer_group()
        logger.info("og_live_stream: connected to Redis and listening for candle snapshot stream events")
        while stop_event is None or not stop_event.is_set():
            now = time.monotonic()
            if now - self._last_outbox_retry >= settings.OUTBOX_RETRY_INTERVAL_SECONDS:
                self.retry_outbox()
                self._last_outbox_retry = now

            processed = self._read_and_process("0", block_ms=None)
            if processed:
                continue
            self._read_and_process(">", block_ms=settings.BLOCK_MS)

    def _read_and_process(self, stream_id: str, *, block_ms: int | None) -> int:
        try:
            entries = get_input_client().xreadgroup(
                settings.CONSUMER_GROUP,
                settings.CONSUMER_NAME,
                {settings.CANDLE_EVENT_STREAM: stream_id},
                count=settings.READ_COUNT,
                block=block_ms,
            )
        except redis.RedisError as exc:
            logger.warning("og_live_stream: XREADGROUP failed: %s", exc)
            self.audit.write(
                "input_read_error",
                status="redis_error",
                source="redis_stream",
                input_stream=settings.CANDLE_EVENT_STREAM,
                message=str(exc),
            )
            reset_input_client()
            raise

        processed_messages = 0
        for _stream_name, messages in entries or []:
            for message_id, fields in messages:
                self._handle_entry(message_id, fields)
                processed_messages += 1
        return processed_messages

    def _handle_entry(self, message_id: str, fields: dict[Any, Any]) -> int:
        delivered = 0
        ack = True
        try:
            delivered, ack = self._handle_event(message_id, normalize_fields(fields))
        except redis.RedisError:
            ack = False
            raise
        except Exception:
            logger.exception("og_live_stream: unexpected error handling snapshot entry %s", message_id)
            self.audit.write(
                "event_error",
                status="exception",
                source="redis_stream",
                input_entry_id=message_id,
                message="unexpected error handling snapshot entry",
            )
        finally:
            if ack:
                try:
                    get_input_client().xack(settings.CANDLE_EVENT_STREAM, settings.CONSUMER_GROUP, message_id)
                except redis.RedisError as exc:
                    logger.warning("og_live_stream: XACK failed for %s: %s", message_id, exc)
                    self.audit.write(
                        "event_ack_error",
                        status="redis_error",
                        source="redis_stream",
                        input_entry_id=message_id,
                        input_stream=settings.CANDLE_EVENT_STREAM,
                        message=str(exc),
                    )
                    reset_input_client()
        return delivered

    def _handle_event(self, message_id: str, fields: dict[str, str]) -> tuple[int, bool]:
        event_start = time.monotonic()
        try:
            event = parse_snapshot_event(fields)
        except MalformedSnapshotError as exc:
            logger.warning("og_live_stream: malformed snapshot event, skipping: %s", exc)
            self.audit.write(
                "event_skipped",
                status="malformed",
                source="redis_stream",
                input_entry_id=message_id,
                input_stream=settings.CANDLE_EVENT_STREAM,
                message=str(exc),
            )
            return 0, True

        items = pipeline.matching_items(event.symbol, event.tf, self.watched)
        if not items:
            return 0, True

        strategies = [item.strategy for item in items]
        self.audit.write(
            "event_received",
            status="matched_watchlist",
            source="redis_stream",
            input_entry_id=message_id,
            input_stream=settings.CANDLE_EVENT_STREAM,
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

        if self._event_is_stale(event.published_at_utc, message_id):
            logger.warning(
                "og_live_stream: stale snapshot event skipped symbol=%s tf=%s version=%s",
                event.symbol,
                event.tf,
                event.snapshot_version,
            )
            self.audit.write(
                "event_skipped",
                status="stale",
                source="redis_stream",
                input_entry_id=message_id,
                strategies=strategies,
                symbol=event.symbol,
                timeframe=event.tf,
                snapshot_version=event.snapshot_version,
                bar_time=event.bar_time,
                state_key=event.state_key,
                message="input event is older than max allowed age",
            )
            return 0, True

        try:
            raw_snapshot = get_input_client().get(event.state_key)
        except redis.RedisError as exc:
            self.audit.write(
                "state_load_error",
                status="redis_error",
                source="redis_stream",
                input_entry_id=message_id,
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
            logger.warning("og_live_stream: state key missing for snapshot event: %s", event.state_key)
            self.audit.write(
                "state_missing",
                status="missing",
                source="redis_stream",
                input_entry_id=message_id,
                strategies=strategies,
                symbol=event.symbol,
                timeframe=event.tf,
                snapshot_version=event.snapshot_version,
                bar_time=event.bar_time,
                state_key=event.state_key,
                message="state key was not found in Redis",
            )
            return 0, True

        try:
            snapshot = parse_state_snapshot(raw_snapshot)
        except MalformedSnapshotError as exc:
            logger.warning("og_live_stream: malformed state snapshot for key=%s, skipping: %s", event.state_key, exc)
            self.audit.write(
                "state_invalid",
                status="malformed",
                source="redis_stream",
                input_entry_id=message_id,
                strategies=strategies,
                symbol=event.symbol,
                timeframe=event.tf,
                snapshot_version=event.snapshot_version,
                bar_time=event.bar_time,
                state_key=event.state_key,
                message=str(exc),
            )
            return 0, True

        if not snapshot_matches_event(snapshot, event):
            logger.warning(
                "og_live_stream: snapshot state no longer matches event, skipping event=%s %s %s state_latest=%s",
                event.symbol,
                event.tf,
                event.bar_time,
                snapshot.latest_bar_time,
            )
            self.audit.write(
                "state_mismatch",
                status="mismatch",
                source="redis_stream",
                input_entry_id=message_id,
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
            return 0, True

        self.audit.write(
            "state_loaded",
            status="validated",
            source="redis_stream",
            input_entry_id=message_id,
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
                    source="redis_stream",
                    input_entry_id=message_id,
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
                self._add_source_metadata(row, event, message_id)
            publish_stats = self._publish_new(rows, item=item, event=event, input_entry_id=message_id)
            delivered += publish_stats["published"]
            self.processed_snapshots.mark_processed(process_key)
            self.audit.write(
                "snapshot_processed",
                status="done",
                source="redis_stream",
                input_entry_id=message_id,
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
        return delivered, True

    def _publish_new(
        self,
        rows: list[dict[str, object]],
        *,
        item: WatchedItem,
        event: object,
        input_entry_id: str,
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
                    source="redis_stream",
                    input_entry_id=input_entry_id,
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
                    source="redis_stream",
                    input_entry_id=input_entry_id,
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
                    source="redis_stream",
                    input_entry_id=input_entry_id,
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
                    source="redis_stream",
                    input_entry_id=input_entry_id,
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
                    source="redis_stream",
                    input_entry_id=input_entry_id,
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

    def _event_is_stale(self, published_at: str | None, message_id: str) -> bool:
        event_time = _parse_event_time(published_at) or _stream_id_time(message_id)
        if event_time is None:
            return False
        age = pd.Timestamp.utcnow() - event_time
        return age.total_seconds() > settings.MAX_EVENT_AGE_SECONDS

    def _add_source_metadata(self, row: dict[str, object], event: object, message_id: str) -> None:
        symbol = str(row.get("symbol") or "").upper()
        row["schema_version"] = 1
        row["asset_type"] = core_config.SYMBOLS.get(symbol, {}).get("asset_type", "")
        row["source_program"] = "dp_program"
        row["source_mechanism"] = "stream"
        row["source_stream"] = settings.CANDLE_EVENT_STREAM
        row["source_entry_id"] = message_id
        row["source_state_key"] = getattr(event, "state_key")
        row["source_snapshot_version"] = getattr(event, "snapshot_version")
        row["source_bar_time"] = getattr(event, "bar_time")


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


def _stream_id_time(stream_id: str) -> pd.Timestamp | None:
    try:
        milliseconds = int(str(stream_id).split("-", 1)[0])
    except (TypeError, ValueError):
        return None
    return pd.Timestamp(milliseconds, unit="ms", tz="UTC")
