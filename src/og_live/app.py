"""Long-running OG live application."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import redis

from og_live import pipeline, settings
from og_live.settings import WatchedItem
from og_live.sinks.redis_signals import (
    ensure_consumer_group,
    get_client,
    publish_signal,
    reset_client,
)
from og_live.sources.candle_snapshot import (
    MalformedSnapshotError,
    normalize_fields,
    parse_snapshot_entry,
    snapshot_symbol_tf,
)
from og_live.state.dedup import SignalState
from og_live.state.outbox import DeliveryOutbox

logger = logging.getLogger(__name__)


class LiveSignalApp:
    """Consumes DP6 candle snapshots and publishes OG strategy signals."""

    def __init__(
        self,
        *,
        watched: list[WatchedItem] | None = None,
        state: SignalState | None = None,
        outbox: DeliveryOutbox | None = None,
    ) -> None:
        self.watched = watched if watched is not None else settings.load_watched_items()
        self.state = state if state is not None else SignalState()
        self.outbox = outbox if outbox is not None else DeliveryOutbox()
        self._last_outbox_retry = 0.0

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        """Run the live stream loop until interrupted or stop_event is set."""
        logger.info(
            "og_live: starting stream=%s group=%s consumer=%s watched=%s",
            settings.CANDLE_SNAPSHOT_STREAM,
            settings.CONSUMER_GROUP,
            settings.CONSUMER_NAME,
            len(self.watched),
        )
        while stop_event is None or not stop_event.is_set():
            try:
                self._run_connected_loop(stop_event)
            except Exception:
                logger.exception("og_live: live loop failed, restarting")
                reset_client()
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
        if delivered_payloads:
            logger.info("og_live: outbox delivered %d signal(s)", len(delivered_payloads))
        return len(delivered_payloads)

    def _run_connected_loop(self, stop_event: threading.Event | None) -> None:
        ensure_consumer_group()
        logger.info("og_live: connected to Redis and listening for candle snapshots")
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
            entries = get_client().xreadgroup(
                settings.CONSUMER_GROUP,
                settings.CONSUMER_NAME,
                {settings.CANDLE_SNAPSHOT_STREAM: stream_id},
                count=settings.READ_COUNT,
                block=block_ms,
            )
        except redis.RedisError as exc:
            logger.warning("og_live: XREADGROUP failed: %s", exc)
            reset_client()
            raise

        processed_messages = 0
        for _stream_name, messages in entries or []:
            for message_id, fields in messages:
                self._handle_entry(message_id, fields)
                processed_messages += 1
        return processed_messages

    def _handle_entry(self, message_id: str, fields: dict[Any, Any]) -> int:
        delivered = 0
        try:
            delivered = self._handle_snapshot(normalize_fields(fields))
        except Exception:
            logger.exception("og_live: unexpected error handling snapshot entry %s", message_id)
        finally:
            try:
                get_client().xack(settings.CANDLE_SNAPSHOT_STREAM, settings.CONSUMER_GROUP, message_id)
            except redis.RedisError as exc:
                logger.warning("og_live: XACK failed for %s: %s", message_id, exc)
                reset_client()
        return delivered

    def _handle_snapshot(self, fields: dict[str, str]) -> int:
        try:
            symbol, tf = snapshot_symbol_tf(fields)
        except MalformedSnapshotError as exc:
            logger.warning("og_live: malformed snapshot metadata, skipping: %s", exc)
            return 0

        items = pipeline.matching_items(symbol, tf, self.watched)
        if not items:
            return 0

        try:
            bars = parse_snapshot_entry(fields)
        except MalformedSnapshotError as exc:
            logger.warning("og_live: malformed bars for %s %s, skipping: %s", symbol, tf, exc)
            return 0

        delivered = 0
        for item in items:
            rows = pipeline.signal_payloads_from_bars(item, symbol, tf, bars)
            delivered += self._publish_new(rows)
        return delivered

    def _publish_new(self, rows: list[dict[str, object]]) -> int:
        delivered = 0
        for row in rows:
            signal_id = str(row.get("signal_id", ""))
            strategy = str(row.get("strategy", ""))
            if not signal_id or not strategy:
                continue
            if self.state.seen(signal_id) or self.outbox.has(signal_id):
                continue
            result = publish_signal(strategy, row)
            if result is not None:
                self.state.mark_delivered(signal_id)
                delivered += 1
            else:
                self.outbox.add_pending(strategy, row)
        return delivered


def _wait(stop_event: threading.Event | None, seconds: float) -> bool:
    if stop_event is None:
        time.sleep(seconds)
        return False
    return stop_event.wait(seconds)
