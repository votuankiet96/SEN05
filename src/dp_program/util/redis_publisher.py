"""Incremental Redis publish of live candle updates for the OG consumer.

Two Redis structures per pair (not ZSET anymore -- see AGENTS.md history
for why this replaced the v4 ZSET design):
  - HASH  {key_prefix}:{tf}:{symbol}:data   field=bartime (epoch seconds,
          as string), value=JSON candle. Upsert by field is the whole
          point: a revised candle overwrites cleanly, a new candle adds a
          field -- neither can ever produce a duplicate the way a ZSET
          member (identity = the full JSON string) can.
  - LIST  {key_prefix}:{tf}:{symbol}:order  bartimes, oldest at head,
          newest appended at tail. Only used to know which field is
          oldest when the window needs trimming -- NOT trusted as the
          authoritative read order (OG sorts by bartime itself after
          HGETALL). Because it only holds small bartime strings, not full
          JSON payloads, it is always cheap to rebuild wholesale, which is
          exactly what reconcile_pair() does -- so a late-arriving candle
          appended at the wrong end of this list for one live cycle is
          harmless; the next reconciliation pass fixes it.

On top of the two structures, every publish also PUBLISHes the actual
changed candle(s) as a JSON payload on one Pub/Sub channel
({event_channel} in config) -- this is the real "event" a subscriber
reacts to directly, without needing to read Redis at all in the common
case. Pub/Sub is fire-and-forget (a subscriber that is briefly
disconnected loses the message), which is why the Hash stays the durable,
always-correct mirror: a subscriber can always HGETALL to catch up, and
reconcile_pair() (called once at service startup, and periodically from
runtime.py) guarantees the Hash+List never permanently drift from SQL
even if an update or a Pub/Sub message is ever missed.
"""
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
_Item = tuple[int, str, str, "list[dict[str, Any]] | None"]

# Đường ghi nóng (hot path): HSET đúng field đổi, RPUSH bartime nếu là
# field mới (HSET trả 1), rồi trim đầu List nếu vượt cửa sổ, rồi PUBLISH
# đúng các nến vừa đổi. Toàn bộ atomic trong 1 EVAL.
_INCREMENTAL_SCRIPT = """
local data_key, order_key, channel = KEYS[1], KEYS[2], KEYS[3]
local max_size = tonumber(ARGV[1])
local event_prefix = ARGV[2]
local events = {}
for i = 3, #ARGV, 2 do
    local bartime, payload = ARGV[i], ARGV[i + 1]
    if redis.call('HSET', data_key, bartime, payload) == 1 then
        redis.call('RPUSH', order_key, bartime)
    end
    table.insert(events, payload)
end
local size = redis.call('LLEN', order_key)
if size > max_size then
    local evicted = redis.call('LPOP', order_key, size - max_size)
    if evicted then
        for _, bt in ipairs(evicted) do redis.call('HDEL', data_key, bt) end
    end
end
if #events > 0 then
    redis.call('PUBLISH', channel, event_prefix .. table.concat(events, ',') .. ']')
end
return 1
"""

# Đường đối chiếu (reconcile): so field hiện có với đúng 500 nến mới nhất
# từ SQL, chỉ HSET/HDEL đúng chỗ lệch, rồi dựng lại toàn bộ List (rẻ, chỉ
# là bartime, không phải JSON) theo đúng thứ tự SQL trả về. Không PUBLISH
# gì -- đây là việc âm thầm giữ đúng trạng thái, không phải sự kiện mới.
_RECONCILE_SCRIPT = """
local data_key, order_key = KEYS[1], KEYS[2]
local desired = {}
for i = 1, #ARGV, 2 do
    local bartime, payload = ARGV[i], ARGV[i + 1]
    desired[bartime] = true
    redis.call('HSET', data_key, bartime, payload)
end
local current = redis.call('HKEYS', data_key)
for _, bartime in ipairs(current) do
    if not desired[bartime] then redis.call('HDEL', data_key, bartime) end
end
redis.call('DEL', order_key)
if #ARGV > 0 then
    local order = {}
    for i = 1, #ARGV, 2 do table.insert(order, ARGV[i]) end
    redis.call('RPUSH', order_key, unpack(order))
end
return 1
"""


def _epoch_key(bartime: Any) -> str:
    # Field/bartime luôn là epoch giây dạng chuỗi -- cùng số chữ số trong
    # nhiều thập kỷ tới nên so sánh chuỗi cũng đúng thứ tự thời gian.
    if hasattr(bartime, "timestamp"):
        aware = bartime if bartime.tzinfo else bartime.replace(tzinfo=timezone.utc)
        return str(int(aware.timestamp()))
    return str(int(float(bartime)))


def _candle_json(bartime: Any, open_: Any, high: Any, low: Any, close: Any, volume: Any) -> str:
    text = bartime.isoformat(sep=" ") if hasattr(bartime, "isoformat") else str(bartime)
    return json.dumps(
        {
            "bartime": text, "open": float(open_), "high": float(high), "low": float(low),
            "close": float(close), "volume": float(volume) if volume is not None else None,
        },
        separators=(",", ":"),
    )


def _pipeline_candles_to_args(candles: list[dict[str, Any]]) -> list[str]:
    # delivered_candles từ pipeline.py dùng key "timestamp" (không phải
    # "bartime") và giá Decimal -- chuẩn hoá đúng 1 chỗ này.
    args: list[str] = []
    for candle in candles:
        bartime = candle["timestamp"]
        args.append(_epoch_key(bartime))
        args.append(_candle_json(
            bartime, candle["open"], candle["high"], candle["low"],
            candle["close"], candle.get("volume"),
        ))
    return args


def _sql_rows_to_args(rows: list[tuple[Any, ...]]) -> list[str]:
    args: list[str] = []
    for bartime, open_, high, low, close, volume in rows:
        args.append(_epoch_key(bartime))
        args.append(_candle_json(bartime, open_, high, low, close, volume))
    return args


class _RedisPublisher:
    """Own one bounded worker thread for the lifetime of the process."""

    def __init__(self) -> None:
        self._queue: queue.Queue[_Item] = queue.Queue(maxsize=1000)
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._client: Any | None = None
        self._circuit_open_until = 0.0

    def enqueue(
        self, config: dict[str, Any], symbol_id: int, symbol: str, tf_code: str,
        candles: list[dict[str, Any]] | None,
    ) -> None:
        settings = config.get("redis") or {}
        if not bool(settings.get("enabled")):
            return
        self._ensure_worker(config)
        try:
            self._queue.put_nowait((int(symbol_id), str(symbol), str(tf_code), candles))
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
            symbol_id, symbol, tf_code, candles = self._queue.get()
            try:
                if candles is None:
                    self._reconcile_one(config, symbol_id, symbol, tf_code)
                else:
                    self._publish_one(config, symbol, tf_code, candles)
            except Exception as exc:
                self._open_circuit(config, exc)

    def _keys(self, settings: dict[str, Any], tf_code: str, symbol: str) -> tuple[str, str]:
        base = f"{settings['key_prefix']}:{tf_code}:{symbol}"
        return f"{base}:data", f"{base}:order"

    def _publish_one(
        self, config: dict[str, Any], symbol: str, tf_code: str, candles: list[dict[str, Any]],
    ) -> None:
        if time.monotonic() < self._circuit_open_until or not candles:
            return
        settings = config["redis"]
        data_key, order_key = self._keys(settings, tf_code, symbol)
        channel = settings["event_channel"]
        prefix = f'{{"symbol":"{symbol}","timeframe":"{tf_code}","candles":['
        client = self._get_client(settings)
        client.eval(
            _INCREMENTAL_SCRIPT, 3, data_key, order_key, channel,
            settings["bars_per_snapshot"], prefix, *_pipeline_candles_to_args(candles),
        )
        self._mark_recovered()

    def _reconcile_one(self, config: dict[str, Any], symbol_id: int, symbol: str, tf_code: str) -> None:
        if time.monotonic() < self._circuit_open_until:
            return
        settings = config["redis"]
        rows = read_latest_candles(config, symbol_id, tf_code, int(settings["bars_per_snapshot"]))
        data_key, order_key = self._keys(settings, tf_code, symbol)
        client = self._get_client(settings)
        client.eval(_RECONCILE_SCRIPT, 2, data_key, order_key, *_sql_rows_to_args(rows))
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


def publish_candle_update(
    config: dict[str, Any], symbol_id: int, symbol: str, tf_code: str,
    candles: list[dict[str, Any]],
) -> None:
    """Queue an incremental Redis update for the candles a live cycle just wrote."""
    _publisher.enqueue(config, symbol_id, symbol, tf_code, candles)


def reconcile_pair(config: dict[str, Any], symbol_id: int, symbol: str, tf_code: str) -> None:
    """Queue a full SQL-truth reconciliation for one pair (diff-and-patch, not blind overwrite)."""
    _publisher.enqueue(config, symbol_id, symbol, tf_code, None)


def reconcile_all_live_pairs(
    config: dict[str, Any], pairs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> None:
    """Queue a full reconciliation for every live pair.

    Called once at live service startup (bootstrap/catch-up after any
    downtime) and periodically from runtime.py's live loop (safety net --
    guarantees Hash+List can never permanently drift from SQL even if an
    incremental update or its Pub/Sub event was ever missed).
    """
    for symbol, timeframe in pairs:
        reconcile_pair(config, symbol["symbol_id"], symbol["symbol"], timeframe["code"])
