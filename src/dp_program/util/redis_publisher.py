"""Đồng bộ Redis Hash + List từ SQL mà không chặn đường ghi warehouse.

Bố trí key (schema 2) — mỗi nến là một Hash riêng, List giữ thứ tự:

    LIST  dp:candles:H1:US30:order       [..., 1788872400, 1788876000]
    HASH  dp:candles:H1:US30:1788876000  bartime 2026-09-08 14:00:00+00:00
                                         open 52880.1   high 52906.1
                                         low  52720.1   close 52845.1
                                         volume 11314.0

List là chỉ mục duy nhất: mọi nến tồn tại đều có epoch trong List, và
eviction xoá key nến trong cùng script atomic đã LPOP epoch đó, nên nến
mồ côi không thể sinh ra. Consumer đọc N nến gần nhất bằng LRANGE lấy
epoch rồi pipeline HGET/HMGET đúng field cần — không phải parse gì.

So với schema 1 (một Hash chứa toàn bộ field phẳng ``{epoch}:o/h/l/c/v``):
hash nhỏ giữ được encoding listpack nên tốn 200 B/nến thay vì 378 B/nến,
và tên key tự nói rõ nến nào vừa đổi nên keyspace notification mới dùng
được. Pub/Sub vẫn phát object JSON chứa symbol, timeframe và các candle
thực sự thay đổi — đó là push một lần mỗi nến, không phải truy vấn hàng
loạt, nên giữ nguyên dạng JSON.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from ..engine.sql_connector import (
    read_latest_candles,
    read_latest_candles_for_pairs,
    warehouse_value_signature,
)
from ..log import log_event

LOGGER = logging.getLogger(__name__)
_Key = tuple[int, str, str]
_RECONCILE_PIPELINE_SIZE = 20
_PUBLISH_BATCH_SIZE = 100
# Ghi vào {key_prefix}:schema để consumer/probe chạy code cũ phát hiện được
# lệch phiên bản thay vì đọc nhầm dữ liệu trong im lặng.
SCHEMA_VERSION = "2"

# Key nến được ghép trong Lua từ candle_prefix (deployment một node, không
# dùng Redis Cluster) vì số key mỗi lần gọi là động — truyền cả 500 key qua
# KEYS[] chỉ làm script khó đọc mà không đổi ngữ nghĩa.
#
# Script chỉ phát event khi giá trị canonical thực sự đổi. LPOS chỉ chạy cho
# delta nhỏ; nến đến muộn được chèn đúng vị trí và List luôn bị giới hạn.
_INCREMENTAL_SCRIPT = """
local order_key, channel = KEYS[1], KEYS[2]
local max_size, candle_prefix, event_prefix = tonumber(ARGV[1]), ARGV[2], ARGV[3]
local events, changed, added = {}, 0, 0
local function insert_sorted(bartime)
    local current, rebuilt, inserted = redis.call('LRANGE', order_key, 0, -1), {}, false
    for _, existing in ipairs(current) do
        if not inserted and tonumber(bartime) < tonumber(existing) then
            table.insert(rebuilt, bartime); inserted = true
        end
        table.insert(rebuilt, existing)
    end
    if not inserted then table.insert(rebuilt, bartime) end
    redis.call('DEL', order_key)
    redis.call('RPUSH', order_key, unpack(rebuilt))
end
for i = 4, #ARGV, 8 do
    local bartime = ARGV[i]
    local key = candle_prefix .. bartime
    local old = redis.call('HMGET', key, 'open', 'high', 'low', 'close', 'volume')
    local differs = false
    for n = 1, 5 do
        if old[n] ~= ARGV[i + 1 + n] then differs = true end
    end
    if differs then
        redis.call('HSET', key,
            'bartime', ARGV[i+1], 'open', ARGV[i+2], 'high', ARGV[i+3],
            'low', ARGV[i+4], 'close', ARGV[i+5], 'volume', ARGV[i+6])
        table.insert(events, ARGV[i+7]); changed = changed + 1
    end
    if not redis.call('LPOS', order_key, bartime) then
        local tail = redis.call('LINDEX', order_key, -1)
        if not tail or tonumber(bartime) > tonumber(tail) then
            redis.call('RPUSH', order_key, bartime)
        else
            insert_sorted(bartime)
        end
        added = added + 1
    end
end
local evicted_count = math.max(0, redis.call('LLEN', order_key) - max_size)
if evicted_count > 0 then
    for _, bt in ipairs(redis.call('LPOP', order_key, evicted_count)) do
        redis.call('DEL', candle_prefix .. bt)
    end
end
if #events > 0 then
    redis.call('PUBLISH', channel, event_prefix .. table.concat(events, ',') .. ']}')
end
return {changed, added, evicted_count}
"""

# Reconcile so sánh từng row và chỉ dựng lại List khi List khác SQL. Nến SQL
# không còn giữ được xoá qua chính List — List là chỉ mục duy nhất nên không
# cần SCAN keyspace.
_RECONCILE_SCRIPT = """
local order_key = KEYS[1]
local candle_prefix = ARGV[1]
local desired, desired_order, changed = {}, {}, 0
for i = 2, #ARGV, 7 do
    local bartime = ARGV[i]
    local key = candle_prefix .. bartime
    local old = redis.call('HMGET', key, 'open', 'high', 'low', 'close', 'volume')
    local differs = false
    for n = 1, 5 do
        if old[n] ~= ARGV[i + 1 + n] then differs = true end
    end
    if differs then
        redis.call('HSET', key,
            'bartime', ARGV[i+1], 'open', ARGV[i+2], 'high', ARGV[i+3],
            'low', ARGV[i+4], 'close', ARGV[i+5], 'volume', ARGV[i+6])
        changed = changed + 1
    end
    desired[bartime] = true
    table.insert(desired_order, bartime)
end
local current, removed, rebuild = redis.call('LRANGE', order_key, 0, -1), 0, false
for _, bartime in ipairs(current) do
    if not desired[bartime] then
        redis.call('DEL', candle_prefix .. bartime); removed = removed + 1
    end
end
if #current ~= #desired_order then rebuild = true end
if not rebuild then
    for i = 1, #desired_order do
        if current[i] ~= desired_order[i] then rebuild = true; break end
    end
end
if rebuild then
    redis.call('DEL', order_key)
    if #desired_order > 0 then redis.call('RPUSH', order_key, unpack(desired_order)) end
end
return {changed, removed, rebuild and 1 or 0}
"""


def _epoch_key(bartime: Any) -> str:
    """Chuẩn hóa bartime thành epoch UTC theo giây."""
    if hasattr(bartime, "timestamp"):
        aware = bartime if bartime.tzinfo else bartime.replace(tzinfo=timezone.utc)
        return str(int(aware.timestamp()))
    return str(int(float(bartime)))


def _bartime_text(bartime: Any) -> str:
    """Giữ format ISO đang dùng trong event contract của consumer."""
    if isinstance(bartime, datetime):
        value = (
            bartime.astimezone(timezone.utc) if bartime.tzinfo else bartime
        ).replace(microsecond=0)
    else:
        value = datetime.fromtimestamp(int(float(bartime)), timezone.utc)
    return value.isoformat(sep=" ")


def _candle_fields(
    open_: Any, high: Any, low: Any, close: Any, volume: Any,
    signature: tuple[str | None, ...] | None = None,
) -> tuple[str, str, str, str, str]:
    """Serialize đúng DECIMAL contract của warehouse, không đi qua float."""
    values = signature or warehouse_value_signature(open_, high, low, close, volume)
    return tuple("null" if value is None else value for value in values)  # type: ignore[return-value]


def _candle_json(
    bartime: Any, open_: Any, high: Any, low: Any, close: Any, volume: Any,
    signature: tuple[str | None, ...] | None = None,
) -> str:
    o, h, low_text, c, v = _candle_fields(open_, high, low, close, volume, signature)
    text = json.dumps(_bartime_text(bartime), ensure_ascii=True)
    return (
        f'{{"bartime":{text},"open":{o},"high":{h},"low":{low_text},'
        f'"close":{c},"volume":{v}}}'
    )


def _pipeline_candles_to_args(candles: list[dict[str, Any]]) -> list[str]:
    """Sort và coalesce candle theo epoch trước khi gọi Lua.

    Mỗi nến chiếm 8 ARGV: epoch, bartime, open, high, low, close, volume,
    payload JSON của event.
    """
    unique = {_epoch_key(candle["timestamp"]): candle for candle in candles}
    args: list[str] = []
    for epoch in sorted(unique, key=int):
        candle = unique[epoch]
        signature = candle.get("_signature") or warehouse_value_signature(
            candle["open"], candle["high"], candle["low"], candle["close"], candle.get("volume")
        )
        args.extend((epoch, _bartime_text(candle["timestamp"]), *_candle_fields(
            candle["open"], candle["high"], candle["low"], candle["close"],
            candle.get("volume"), signature,
        ), _candle_json(
            candle["timestamp"], candle["open"], candle["high"], candle["low"],
            candle["close"], candle.get("volume"), signature,
        )))
    return args


def _sql_rows_to_args(rows: list[tuple[Any, ...]]) -> list[str]:
    """Mỗi nến chiếm 7 ARGV: epoch, bartime, open, high, low, close, volume."""
    args: list[str] = []
    for bartime, open_, high, low, close, volume in rows:
        signature = warehouse_value_signature(open_, high, low, close, volume)
        args.extend((_epoch_key(bartime), _bartime_text(bartime), *_candle_fields(
            open_, high, low, close, volume, signature,
        )))
    return args


class _RedisPublisher:
    """Coalesce công việc theo pair trong một worker có retry hữu hạn tài nguyên."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._start_lock = threading.Lock()
        self._updates: dict[_Key, dict[str, dict[str, Any]]] = {}
        self._reconciles: dict[_Key, None] = {}
        self._thread: threading.Thread | None = None
        self._client: Any | None = None
        self._incremental: Any | None = None
        self._reconcile: Any | None = None
        self._circuit_open_until = 0.0
        self._working = 0
        self._stopping = self._force_stop = False

    def enqueue(
        self, config: dict[str, Any], symbol_id: int, symbol: str, tf_code: str,
        candles: list[dict[str, Any]] | None,
    ) -> None:
        if not bool((config.get("redis") or {}).get("enabled")) or candles == []:
            return
        self._ensure_worker(config)
        key = (int(symbol_id), str(symbol), str(tf_code))
        with self._condition:
            if self._stopping:
                return
            if candles is None:
                self._reconciles[key] = None
            else:
                pending = self._updates.setdefault(key, {})
                pending.update({_epoch_key(item["timestamp"]): item for item in candles})
            self._condition.notify()

    def enqueue_reconcile_all(
        self, config: dict[str, Any], pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    ) -> None:
        if not bool((config.get("redis") or {}).get("enabled")) or not pairs:
            return
        self._ensure_worker(config)
        with self._condition:
            for symbol, timeframe in pairs:
                key = (int(symbol["symbol_id"]), str(symbol["symbol"]), str(timeframe["code"]))
                self._reconciles[key] = None
            self._condition.notify()

    def _ensure_worker(self, config: dict[str, Any]) -> None:
        with self._start_lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._worker_loop, args=(config,), name="dp-redis-publisher", daemon=True,
            )
            self._thread.start()

    def _next_job(self) -> tuple[str, Any] | None:
        if self._updates:
            key = next(iter(self._updates))
            values = self._updates.pop(key)
            return "update", (key, [values[item] for item in sorted(values, key=int)])
        if self._reconciles:
            keys = list(self._reconciles)
            self._reconciles.clear()
            return "reconcile", keys
        return None

    def _worker_loop(self, config: dict[str, Any]) -> None:
        while True:
            with self._condition:
                while not self._force_stop and not self._updates and not self._reconciles:
                    if self._stopping:
                        return
                    self._condition.wait()
                if self._force_stop:
                    return
                cooldown = self._circuit_open_until - time.monotonic()
                if cooldown > 0:
                    self._condition.wait(timeout=cooldown)
                    continue
                job = self._next_job()
                self._working += 1
            try:
                if job and job[0] == "update":
                    key, candles = job[1]
                    self._publish_one(config, key[1], key[2], candles)
                elif job:
                    self._reconcile_many(config, job[1])
            except Exception as exc:
                self._open_circuit(config, exc)
                if job:
                    self._requeue(job)
            finally:
                with self._condition:
                    self._working -= 1
                    self._condition.notify_all()

    def _requeue(self, job: tuple[str, Any]) -> None:
        with self._condition:
            if job[0] == "update":
                key, candles = job[1]
                failed = {_epoch_key(item["timestamp"]): item for item in candles}
                failed.update(self._updates.get(key, {}))
                self._updates[key] = failed
            else:
                for key in job[1]:
                    self._reconciles[key] = None
            self._condition.notify()

    @staticmethod
    def _keys(settings: dict[str, Any], tf_code: str, symbol: str) -> tuple[str, str]:
        """Trả về (order_key, candle_prefix); key nến là candle_prefix + epoch."""
        base = f"{settings['key_prefix']}:{tf_code}:{symbol}"
        return f"{base}:order", f"{base}:"

    def _scripts(self, settings: dict[str, Any]) -> tuple[Any, Any]:
        client = self._get_client(settings)
        if self._incremental is None:
            client.set(f"{settings['key_prefix']}:schema", SCHEMA_VERSION)
            self._incremental = client.register_script(_INCREMENTAL_SCRIPT)
            self._reconcile = client.register_script(_RECONCILE_SCRIPT)
        return self._incremental, self._reconcile

    def _publish_one(
        self, config: dict[str, Any], symbol: str, tf_code: str, candles: list[dict[str, Any]],
    ) -> None:
        if not candles:
            return
        settings = config["redis"]
        order_key, candle_prefix = self._keys(settings, tf_code, symbol)
        prefix = (
            '{"symbol":' + json.dumps(symbol) + ',"timeframe":'
            + json.dumps(tf_code) + ',"candles":['
        )
        script, _ = self._scripts(settings)
        args = _pipeline_candles_to_args(candles)
        stride = _PUBLISH_BATCH_SIZE * 8
        for offset in range(0, len(args), stride):
            script(
                keys=[order_key, settings["event_channel"]],
                args=[
                    settings["bars_per_snapshot"], candle_prefix, prefix,
                    *args[offset:offset + stride],
                ],
            )
        self._mark_recovered()

    def _reconcile_one(
        self, config: dict[str, Any], symbol_id: int, symbol: str, tf_code: str,
    ) -> Any:
        settings = config["redis"]
        rows = read_latest_candles(config, symbol_id, tf_code, int(settings["bars_per_snapshot"]))
        order_key, candle_prefix = self._keys(settings, tf_code, symbol)
        _, script = self._scripts(settings)
        result = script(keys=[order_key], args=[candle_prefix, *_sql_rows_to_args(rows)])
        self._mark_recovered()
        return result

    def _reconcile_many(self, config: dict[str, Any], keys: list[_Key]) -> None:
        started = time.monotonic()
        settings = config["redis"]
        rows = read_latest_candles_for_pairs(
            config, [(key[0], key[2]) for key in keys], int(settings["bars_per_snapshot"]),
        )
        client = self._get_client(settings)
        _, script = self._scripts(settings)
        totals = [0, 0, 0]
        for offset in range(0, len(keys), _RECONCILE_PIPELINE_SIZE):
            pipeline = client.pipeline(transaction=False)
            for symbol_id, symbol, tf_code in keys[offset:offset + _RECONCILE_PIPELINE_SIZE]:
                order_key, candle_prefix = self._keys(settings, tf_code, symbol)
                script(
                    keys=[order_key],
                    args=[candle_prefix, *_sql_rows_to_args(rows[(symbol_id, tf_code)])],
                    client=pipeline,
                )
            for result in pipeline.execute():
                for index, value in enumerate(result):
                    totals[index] += int(value)
        self._mark_recovered()
        log_event(
            LOGGER, logging.INFO, "REDIS_RECONCILE_COMPLETED", "NONE", component="redis",
            pairs=len(keys), changed=totals[0], removed=totals[1], order_rebuilt=totals[2],
            duration_seconds=round(time.monotonic() - started, 3),
        )

    def _get_client(self, settings: dict[str, Any]) -> Any:
        if self._client is None:
            import redis
            self._client = redis.Redis(
                host=settings["host"], port=settings["port"], db=settings["db"],
                username=settings["username"] or None, password=settings["password"] or None,
                socket_connect_timeout=settings["timeout_seconds"],
                socket_timeout=settings["timeout_seconds"], socket_keepalive=True,
                health_check_interval=30, decode_responses=True,
            )
        return self._client

    def _open_circuit(self, config: dict[str, Any], exc: Exception) -> None:
        now = time.monotonic()
        already_open = self._circuit_open_until > now
        self._circuit_open_until = now + int(config["redis"]["circuit_cooldown_seconds"])
        self._client = self._incremental = self._reconcile = None
        if not already_open:
            log_event(
                LOGGER, logging.WARNING, "REDIS_PUBLISH_FAILED", "MEDIUM",
                component="redis", error_type=type(exc).__name__,
                action="retaining updates and retrying after cooldown",
            )

    def _mark_recovered(self) -> None:
        if self._circuit_open_until:
            log_event(LOGGER, logging.INFO, "REDIS_PUBLISH_RECOVERED", "NONE", component="redis")
        self._circuit_open_until = 0.0

    def wait_idle(self, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while self._updates or self._reconciles or self._working:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)
            return True

    def shutdown(self, timeout_seconds: float = 10.0) -> bool:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        if not self._thread:
            return True
        self._thread.join(timeout=max(0.0, timeout_seconds))
        if self._thread.is_alive():
            with self._condition:
                pending = len(self._updates) + len(self._reconciles)
                self._updates.clear(); self._reconciles.clear(); self._force_stop = True
                self._condition.notify_all()
            self._thread.join(timeout=1.0)
            log_event(
                LOGGER, logging.WARNING, "REDIS_SHUTDOWN_TIMEOUT", "MEDIUM",
                component="redis", pending_pairs=pending,
                action="pending pairs will reconcile on next startup",
            )
        return not self._thread.is_alive()


_publisher = _RedisPublisher()


def publish_candle_update(
    config: dict[str, Any], symbol_id: int, symbol: str, tf_code: str,
    candles: list[dict[str, Any]],
) -> None:
    """Đưa delta Redis của một pair vào worker."""
    _publisher.enqueue(config, symbol_id, symbol, tf_code, candles)


def reconcile_pair(
    config: dict[str, Any], symbol_id: int, symbol: str, tf_code: str,
) -> None:
    """Đưa một pair vào hàng đợi đối chiếu SQL."""
    _publisher.enqueue(config, symbol_id, symbol, tf_code, None)


def reconcile_all_live_pairs(
    config: dict[str, Any], pairs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> None:
    """Coalesce toàn bộ live pairs thành một batch reconcile SQL."""
    _publisher.enqueue_reconcile_all(config, pairs)


def wait_for_redis_idle(timeout_seconds: float) -> bool:
    """Chờ bounded cho bootstrap hoặc kiểm chứng vận hành."""
    return _publisher.wait_idle(timeout_seconds)


def shutdown_redis_publisher(timeout_seconds: float = 10.0) -> bool:
    """Flush bounded worker trước khi live service thoát."""
    return _publisher.shutdown(timeout_seconds)
