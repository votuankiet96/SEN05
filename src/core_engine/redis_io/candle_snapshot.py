"""Best-effort Redis handoff for live OHLCV candle snapshots.

The live DB writer must never wait for Redis or for a 500-bar SQL snapshot
query. Public calls only enqueue a small request; a daemon worker performs the
read + Redis write in the background.

Redis contract:
- State key keeps the latest N bars for each symbol/timeframe.
- Event stream only tells OG which state key changed after a live commit.
- Optional Pub/Sub tells OG Live trial subscribers that the state key changed.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from core_engine.warehouse.reader import get_latest_ohlcv_snapshot
from core_engine.settings import CANDLE_SNAPSHOT

logger = logging.getLogger("candle_snapshot")


def _runtime_logger() -> logging.Logger:
    live_logger = logging.getLogger("live_fetching")
    if live_logger.handlers:
        return live_logger
    return logger


@dataclass(frozen=True)
class SnapshotRequest:
    symbol_id: int
    tv_symbol: str
    tf_code: str
    reason: str
    emit_event: bool


class CandleSnapshotPublisher:
    def __init__(self) -> None:
        self._queue: queue.Queue[SnapshotRequest] = queue.Queue(maxsize=CANDLE_SNAPSHOT.queue_maxsize)
        self._client: Any | None = None
        self._client_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._circuit_open_until = 0.0
        self._pubsub_circuit_open_until = 0.0
        self._last_queue_full_log_at = 0.0

    def enqueue(self, symbol_id: int, tv_symbol: str, tf_code: str) -> None:
        if not CANDLE_SNAPSHOT.enabled:
            return

        try:
            self._ensure_worker()
            request = SnapshotRequest(
                int(symbol_id),
                _clean_token(tv_symbol),
                _clean_token(tf_code).upper(),
                "live_commit",
                True,
            )
            self._queue.put_nowait(request)
        except queue.Full:
            self._log_queue_full()
        except Exception:
            _runtime_logger().exception("CANDLE_SNAPSHOT | enqueue failed safely | result=skipped")

    def seed(
        self,
        symbols: Iterable[Mapping[str, Any]],
        tf_codes: Iterable[str],
        *,
        reason: str = "startup",
    ) -> dict[str, int | str]:
        """Queue one initial snapshot request for every live symbol/timeframe."""
        if not CANDLE_SNAPSHOT.enabled:
            return {"reason": reason, "queued": 0, "skipped": 0, "result": "disabled"}

        queued = 0
        skipped = 0
        try:
            self._ensure_worker()
            tf_list = [str(tf).upper() for tf in tf_codes if str(tf or "").strip()]
            for symbol in symbols:
                try:
                    symbol_id = int(symbol["symbol_id"])
                    tv_symbol = str(symbol["tv_symbol"])
                except Exception:
                    skipped += len(tf_list)
                    continue

                for tf_code in tf_list:
                    try:
                        self._queue.put_nowait(
                            SnapshotRequest(symbol_id, _clean_token(tv_symbol), tf_code, reason, False)
                        )
                        queued += 1
                    except queue.Full:
                        skipped += 1
                        self._log_queue_full()

            _runtime_logger().info(
                "CANDLE_SNAPSHOT | startup seed queued | reason=%s | queued=%s | skipped=%s | result=queued",
                reason,
                queued,
                skipped,
            )
            return {"reason": reason, "queued": queued, "skipped": skipped, "result": "queued"}
        except Exception:
            _runtime_logger().exception("CANDLE_SNAPSHOT | startup seed failed safely | result=skipped")
            return {"reason": reason, "queued": queued, "skipped": skipped, "result": "failed"}

    def _ensure_worker(self) -> None:
        with self._state_lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="candle-snapshot-publisher",
                daemon=True,
            )
            self._thread.start()

    def _worker_loop(self) -> None:
        while True:
            request = self._queue.get()
            try:
                self._publish_request(request)
            except Exception:
                _runtime_logger().exception("CANDLE_SNAPSHOT | worker failed safely | result=skipped")
            finally:
                self._queue.task_done()

    def _publish_request(self, request: SnapshotRequest) -> None:
        if not self._circuit_allows_attempt():
            return

        try:
            bars = get_latest_ohlcv_snapshot(
                request.symbol_id,
                request.tf_code,
                limit=CANDLE_SNAPSHOT.bars_per_snapshot,
            )
            if not bars:
                _runtime_logger().warning(
                    "CANDLE_SNAPSHOT | no bars found | symbol=%s | timeframe=%s | result=skipped",
                    request.tv_symbol,
                    request.tf_code,
                )
                return

            latest_bar_time = str(bars[-1].get("bar_time", "")) if bars else ""
            generated_at = _utc_now_text()
            state_key = _state_key(request.tv_symbol, request.tf_code)
            snapshot_version = f"{request.tv_symbol}:{request.tf_code}:{latest_bar_time}"
            payload = {
                "schema_version": 1,
                "program": "dp_program",
                "source": "live_fetching",
                "symbol_id": request.symbol_id,
                "tv_symbol": request.tv_symbol,
                "tf_code": request.tf_code,
                "bars_count": len(bars),
                "latest_bar_time": latest_bar_time,
                "generated_at_utc": generated_at,
                "snapshot_version": snapshot_version,
                "bars": bars,
            }

            client = self._get_client()
            client.set(state_key, json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

            if request.emit_event:
                event_fields = {
                    "schema_version": "1",
                    "event_type": "snapshot_updated",
                    "program": "dp_program",
                    "source": "live_fetching",
                    "symbol_id": str(request.symbol_id),
                    "tv_symbol": request.tv_symbol,
                    "tf_code": request.tf_code,
                    "bar_time": latest_bar_time,
                    "state_key": state_key,
                    "bars_count": str(len(bars)),
                    "published_at_utc": generated_at,
                    "snapshot_version": snapshot_version,
                }
                client.xadd(
                    CANDLE_SNAPSHOT.event_stream,
                    event_fields,
                    maxlen=CANDLE_SNAPSHOT.event_maxlen,
                    approximate=True,
                )
                self._publish_pubsub_event(client, event_fields)

            self._mark_recovered()
        except Exception:
            self._open_circuit()

    def _publish_pubsub_event(self, client: Any, event_fields: Mapping[str, str]) -> None:
        """Best-effort realtime notification after durable snapshot + stream writes."""
        if not CANDLE_SNAPSHOT.pubsub_enabled:
            return
        if not CANDLE_SNAPSHOT.pubsub_channel:
            return
        if not self._pubsub_circuit_allows_attempt():
            return

        try:
            message = {
                "schema_version": CANDLE_SNAPSHOT.pubsub_schema_version,
                "event_type": str(event_fields.get("event_type") or "snapshot_updated"),
                "symbol_id": _safe_int(event_fields.get("symbol_id")),
                "tv_symbol": str(event_fields.get("tv_symbol") or ""),
                "tf_code": str(event_fields.get("tf_code") or ""),
                "bar_time": str(event_fields.get("bar_time") or ""),
                "state_key": str(event_fields.get("state_key") or ""),
                "snapshot_version": str(event_fields.get("snapshot_version") or ""),
                "bars_count": _safe_int(event_fields.get("bars_count")),
                "published_at_utc": _utc_with_z(str(event_fields.get("published_at_utc") or "")),
            }
            client.publish(
                CANDLE_SNAPSHOT.pubsub_channel,
                json.dumps(message, separators=(",", ":"), ensure_ascii=False),
            )
            self._mark_pubsub_recovered()
        except Exception:
            self._open_pubsub_circuit()

    def _get_client(self) -> Any:
        with self._client_lock:
            if self._client is None:
                import redis

                self._client = redis.Redis(
                    host=CANDLE_SNAPSHOT.redis_host,
                    port=CANDLE_SNAPSHOT.redis_port,
                    username=CANDLE_SNAPSHOT.redis_username or None,
                    password=CANDLE_SNAPSHOT.redis_password or None,
                    db=CANDLE_SNAPSHOT.redis_db,
                    socket_connect_timeout=CANDLE_SNAPSHOT.timeout_sec,
                    socket_timeout=CANDLE_SNAPSHOT.timeout_sec,
                    decode_responses=True,
                )
            return self._client

    def _circuit_allows_attempt(self) -> bool:
        now = time.monotonic()
        with self._state_lock:
            return now >= self._circuit_open_until

    def _pubsub_circuit_allows_attempt(self) -> bool:
        now = time.monotonic()
        with self._state_lock:
            return now >= self._pubsub_circuit_open_until

    def _open_circuit(self) -> None:
        now = time.monotonic()
        should_log = False
        with self._state_lock:
            should_log = self._circuit_open_until == 0.0 or now >= self._circuit_open_until
            self._circuit_open_until = now + CANDLE_SNAPSHOT.circuit_cooldown_sec
        with self._client_lock:
            self._client = None
        if should_log:
            _runtime_logger().warning(
                "CANDLE_SNAPSHOT | publish failed; pausing snapshot handoff | "
                "cooldown_seconds=%s | result=paused",
                CANDLE_SNAPSHOT.circuit_cooldown_sec,
                exc_info=True,
            )

    def _mark_recovered(self) -> None:
        with self._state_lock:
            was_open = self._circuit_open_until != 0.0
            self._circuit_open_until = 0.0
        if was_open:
            _runtime_logger().info("CANDLE_SNAPSHOT | Redis recovered | result=publishing")

    def _open_pubsub_circuit(self) -> None:
        now = time.monotonic()
        should_log = False
        with self._state_lock:
            should_log = self._pubsub_circuit_open_until == 0.0 or now >= self._pubsub_circuit_open_until
            self._pubsub_circuit_open_until = now + CANDLE_SNAPSHOT.circuit_cooldown_sec
        if should_log:
            _runtime_logger().warning(
                "CANDLE_SNAPSHOT_PUBSUB | publish failed; pausing realtime trial lane | "
                "channel=%s | cooldown_seconds=%s | result=paused",
                CANDLE_SNAPSHOT.pubsub_channel,
                CANDLE_SNAPSHOT.circuit_cooldown_sec,
                exc_info=True,
            )

    def _mark_pubsub_recovered(self) -> None:
        with self._state_lock:
            was_open = self._pubsub_circuit_open_until != 0.0
            self._pubsub_circuit_open_until = 0.0
        if was_open:
            _runtime_logger().info(
                "CANDLE_SNAPSHOT_PUBSUB | Redis Pub/Sub recovered | channel=%s | result=publishing",
                CANDLE_SNAPSHOT.pubsub_channel,
            )

    def _log_queue_full(self) -> None:
        now = time.monotonic()
        if now - self._last_queue_full_log_at < 60:
            return
        self._last_queue_full_log_at = now
        _runtime_logger().warning(
            "CANDLE_SNAPSHOT | snapshot queue is full; dropping newest snapshot request | "
            "queue_maxsize=%s | result=dropped",
            CANDLE_SNAPSHOT.queue_maxsize,
        )


_publisher = CandleSnapshotPublisher()


def _clean_token(value: object) -> str:
    return str(value or "").strip().replace(" ", "_")


def _state_key(tv_symbol: str, tf_code: str) -> str:
    return f"{CANDLE_SNAPSHOT.state_prefix}:{_clean_token(tv_symbol)}:{_clean_token(tf_code).upper()}"


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat()


def _utc_with_z(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if text.endswith("Z") or text.endswith("+00:00"):
        return text.replace("+00:00", "Z")
    return f"{text}Z"


def _safe_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def publish_candle_snapshot(symbol_id: int, tv_symbol: str, tf_code: str) -> None:
    """Queue one candle snapshot handoff after live Fact_OHLCV commit succeeds."""
    _publisher.enqueue(symbol_id, tv_symbol, tf_code)


def seed_candle_snapshots(
    symbols: Iterable[Mapping[str, Any]],
    tf_codes: Iterable[str],
    *,
    reason: str = "startup",
) -> dict[str, int | str]:
    """Queue initial candle snapshots for the live universe without blocking live writes."""
    return _publisher.seed(symbols, tf_codes, reason=reason)
