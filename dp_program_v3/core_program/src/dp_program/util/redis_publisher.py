"""Non-blocking Redis publish of live candle snapshots for the OG consumer."""
# Publisher chạy cùng tiến trình live; lỗi Redis không được làm dừng engine.
# Mỗi lần publish luôn đọc lại nguyên cửa sổ nến mới nhất từ SQL rồi ghi đè
# toàn bộ vào đúng key — không tự giữ delta, để không bao giờ lệch khỏi SQL
# kể cả khi live vừa catch-up nhiều nến cùng lúc sau một lần mất kết nối.
# OG tự đọc key này; báo "có mới" dựa vào keyspace notification của chính
# Redis server (bật ngoài phạm vi code này), không qua pub/sub thủ công.

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import timezone
from typing import Any

from ..engine.sql_connector import read_latest_candles
from ..log import log_event

LOGGER = logging.getLogger(__name__)
_Item = tuple[int, str, str]
# Xoá key cũ rồi ghi lại toàn bộ trong 1 lệnh atomic (Redis chạy Lua script
# không thể chia cắt) — OG không bao giờ thấy trạng thái giữa chừng key rỗng.
_REPLACE_SCRIPT = """
redis.call('DEL', KEYS[1])
if #ARGV > 0 then
    redis.call('ZADD', KEYS[1], unpack(ARGV))
end
return 1
"""


def _epoch_seconds(bartime: Any) -> float:
    # Score của ZSET = bartime dạng epoch giây (UTC-naive từ SQL -> gán UTC).
    if hasattr(bartime, "timestamp"):
        aware = bartime if bartime.tzinfo else bartime.replace(tzinfo=timezone.utc)
        return aware.timestamp()
    return float(bartime)


def _zset_args(rows: list[tuple[Any, ...]]) -> list[Any]:
    # Mỗi nến thành 1 cặp (score, member) phẳng để truyền cho ZADD qua EVAL.
    args: list[Any] = []
    for bartime, open_, high, low, close, volume in rows:
        member = json.dumps(
            {
                "bartime": bartime.isoformat(sep=" ") if hasattr(bartime, "isoformat") else str(bartime),
                "open": float(open_), "high": float(high), "low": float(low),
                "close": float(close), "volume": float(volume) if volume is not None else None,
            },
            separators=(",", ":"),
        )
        args.extend((_epoch_seconds(bartime), member))
    return args


class _RedisPublisher:
    """Own one bounded worker thread for the lifetime of the process."""

    def __init__(self) -> None:
        self._queue: queue.Queue[_Item] = queue.Queue(maxsize=1000)
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._client: Any | None = None
        self._circuit_open_until = 0.0

    def enqueue(self, config: dict[str, Any], symbol_id: int, symbol: str, tf_code: str) -> None:
        settings = config.get("redis") or {}
        if not bool(settings.get("enabled")):
            return
        self._ensure_worker(config)
        try:
            self._queue.put_nowait((int(symbol_id), str(symbol), str(tf_code)))
        except queue.Full:
            log_event(
                LOGGER, logging.WARNING, "REDIS_QUEUE_FULL", "MEDIUM",
                component="redis", action="publish dropped; engine continues",
            )

    def _ensure_worker(self, config: dict[str, Any]) -> None:
        with self._start_lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._worker_loop, args=(config,), name="dp-redis-publisher", daemon=True,
            )
            self._thread.start()

    def _worker_loop(self, config: dict[str, Any]) -> None:
        while True:
            symbol_id, symbol, tf_code = self._queue.get()
            try:
                self._publish_one(config, symbol_id, symbol, tf_code)
            except Exception as exc:
                self._open_circuit(config, exc)

    def _publish_one(self, config: dict[str, Any], symbol_id: int, symbol: str, tf_code: str) -> None:
        if time.monotonic() < self._circuit_open_until:
            return
        settings = config["redis"]
        rows = read_latest_candles(config, symbol_id, tf_code, int(settings["bars_per_snapshot"]))
        if not rows:
            return
        key = f"{settings['key_prefix']}:{tf_code}:{symbol}"
        client = self._get_client(settings)
        client.eval(_REPLACE_SCRIPT, 1, key, *_zset_args(rows))
        self._mark_recovered()

    def _get_client(self, settings: dict[str, Any]) -> Any:
        if self._client is None:
            import redis
            self._client = redis.Redis(
                host=settings["host"], port=settings["port"], db=settings["db"],
                username=settings["username"] or None, password=settings["password"] or None,
                socket_connect_timeout=settings["timeout_seconds"],
                socket_timeout=settings["timeout_seconds"], decode_responses=True,
            )
        return self._client

    def _open_circuit(self, config: dict[str, Any], exc: Exception) -> None:
        now = time.monotonic()
        already_open = self._circuit_open_until > now
        self._circuit_open_until = now + int(config["redis"]["circuit_cooldown_seconds"])
        self._client = None
        if not already_open:
            log_event(
                LOGGER, logging.WARNING, "REDIS_PUBLISH_FAILED", "MEDIUM",
                component="redis", error_type=type(exc).__name__,
                action="pausing Redis publish",
            )

    def _mark_recovered(self) -> None:
        if self._circuit_open_until:
            log_event(LOGGER, logging.INFO, "REDIS_PUBLISH_RECOVERED", "NONE", component="redis")
        self._circuit_open_until = 0.0


_publisher = _RedisPublisher()


def publish_candle_update(config: dict[str, Any], symbol_id: int, symbol: str, tf_code: str) -> None:
    """Queue one Redis snapshot refresh for a pair that just committed to the warehouse."""
    _publisher.enqueue(config, symbol_id, symbol, tf_code)


def seed_all_live_pairs(config: dict[str, Any], pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> None:
    """Queue one refresh for every live pair — called once at live service startup."""
    for symbol, timeframe in pairs:
        _publisher.enqueue(config, symbol["symbol_id"], symbol["symbol"], timeframe["code"])
