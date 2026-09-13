"""Đồng bộ Redis Hash + List từ SQL mà không chặn đường ghi warehouse.

Bố trí key — mỗi nến là một Hash riêng, List giữ thứ tự thời gian:

    LIST  L_CANDLE_US30_H1                      [..., 2026-09-08_13:00:00,
                                                      2026-09-08_14:00:00]
    HASH  L_CANDLE_US30_H1:2026-09-08_14:00:00  open 52880.10000000
                                                high 52906.10000000
                                                low  52720.10000000
                                                close 52845.10000000
                                                volume 11314.0000

Key Hash suy ra từ key List bằng đúng một phép nối: ``list_key + ":" +
<phần tử lấy từ List>``. Consumer không cần biết thêm quy ước nào khác.

Mốc thời gian dùng làm chỉ mục là UTC, không hậu tố offset, rộng cố định
``YYYY-MM-DD_HH:MM:SS`` — nhờ vậy so sánh chuỗi cho đúng thứ tự thời gian
(đã kiểm cả các mốc vượt ngày/tháng/năm), nên Lua so sánh trực tiếp bằng
``<``/``>`` chứ không cần ``tonumber``. Định dạng phải tuyệt đối nhất
quán: chỉ cần một bản ghi lẫn offset là vừa hỏng thứ tự vừa sinh ra key
thứ hai cho cùng một nến.

List là chỉ mục duy nhất: mọi nến tồn tại đều có mốc của nó trong List, và
eviction xoá key nến trong cùng script atomic đã LPOP mốc đó, nên nến mồ
côi không thể sinh ra. Consumer đọc N nến gần nhất bằng LRANGE lấy mốc rồi
pipeline HGET/HMGET đúng field cần — không phải parse gì.

Hash chỉ chứa OHLCV; mốc thời gian đã nằm trong chính tên key nên không
lặp lại thành field. Hash nhỏ (5 field) giữ được encoding listpack, tốn
~200 B/nến thay vì ~378 B/nến nếu gộp nhiều nến vào một Hash lớn.

Pub/Sub vẫn phát object JSON chứa symbol, timeframe và các candle thực sự
thay đổi — đó là push một lần mỗi nến, không phải truy vấn hàng loạt, nên
giữ nguyên dạng JSON. Trường ``bartime`` trong event dùng đúng định dạng
mốc ở trên để consumer dựng thẳng ra key Hash.
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

# Key nến được ghép trong Lua từ candle_prefix (deployment một node, không
# dùng Redis Cluster) vì số key mỗi lần gọi là động — truyền cả trăm key qua
# KEYS[] chỉ làm script khó đọc mà không đổi ngữ nghĩa.
#
# Mốc thời gian rộng cố định nên so sánh chuỗi (`<`, `>`) đã cho đúng thứ tự
# thời gian; không dùng tonumber vì "2026-09-08_14:00:00" không phải số.
#
# Script chỉ phát event khi giá trị canonical thực sự đổi. LPOS chỉ chạy cho
# delta nhỏ; nến đến muộn được chèn đúng vị trí và List luôn bị giới hạn.
#
# ARGV: [1]=max_size [2]=candle_prefix [3]=event_prefix, rồi mỗi nến 7 ô:
#       stamp, open, high, low, close, volume, payload JSON của event.
_INCREMENTAL_SCRIPT = """
local list_key, channel = KEYS[1], KEYS[2]
local max_size, candle_prefix, event_prefix = tonumber(ARGV[1]), ARGV[2], ARGV[3]
local events, changed, added = {}, 0, 0
local function insert_sorted(stamp)
    local current, rebuilt, inserted = redis.call('LRANGE', list_key, 0, -1), {}, false
    for _, existing in ipairs(current) do
        if not inserted and stamp < existing then
            table.insert(rebuilt, stamp); inserted = true
        end
        table.insert(rebuilt, existing)
    end
    if not inserted then table.insert(rebuilt, stamp) end
    redis.call('DEL', list_key)
    redis.call('RPUSH', list_key, unpack(rebuilt))
end
for i = 4, #ARGV, 7 do
    local stamp = ARGV[i]
    local key = candle_prefix .. stamp
    local old = redis.call('HMGET', key, 'open', 'high', 'low', 'close', 'volume')
    local differs = false
    for n = 1, 5 do
        if old[n] ~= ARGV[i + n] then differs = true end
    end
    if differs then
        redis.call('HSET', key,
            'open', ARGV[i+1], 'high', ARGV[i+2], 'low', ARGV[i+3],
            'close', ARGV[i+4], 'volume', ARGV[i+5])
        table.insert(events, ARGV[i+6]); changed = changed + 1
    end
    if not redis.call('LPOS', list_key, stamp) then
        local tail = redis.call('LINDEX', list_key, -1)
        if not tail or stamp > tail then
            redis.call('RPUSH', list_key, stamp)
        else
            insert_sorted(stamp)
        end
        added = added + 1
    end
end
local evicted_count = math.max(0, redis.call('LLEN', list_key) - max_size)
if evicted_count > 0 then
    for _, bt in ipairs(redis.call('LPOP', list_key, evicted_count)) do
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
#
# ARGV: [1]=candle_prefix, rồi mỗi nến 6 ô: stamp, open, high, low, close,
#       volume (không có payload event vì reconcile không publish).
_RECONCILE_SCRIPT = """
local list_key = KEYS[1]
local candle_prefix = ARGV[1]
local desired, desired_order, changed = {}, {}, 0
for i = 2, #ARGV, 6 do
    local stamp = ARGV[i]
    local key = candle_prefix .. stamp
    local old = redis.call('HMGET', key, 'open', 'high', 'low', 'close', 'volume')
    local differs = false
    for n = 1, 5 do
        if old[n] ~= ARGV[i + n] then differs = true end
    end
    if differs then
        redis.call('HSET', key,
            'open', ARGV[i+1], 'high', ARGV[i+2], 'low', ARGV[i+3],
            'close', ARGV[i+4], 'volume', ARGV[i+5])
        changed = changed + 1
    end
    desired[stamp] = true
    table.insert(desired_order, stamp)
end
local current, removed, rebuild = redis.call('LRANGE', list_key, 0, -1), 0, false
for _, stamp in ipairs(current) do
    if not desired[stamp] then
        redis.call('DEL', candle_prefix .. stamp); removed = removed + 1
    end
end
if #current ~= #desired_order then rebuild = true end
if not rebuild then
    for i = 1, #desired_order do
        if current[i] ~= desired_order[i] then rebuild = true; break end
    end
end
if rebuild then
    redis.call('DEL', list_key)
    if #desired_order > 0 then redis.call('RPUSH', list_key, unpack(desired_order)) end
end
return {changed, removed, rebuild and 1 or 0}
"""


def _stamp(bartime: Any) -> str:
    """Mốc thời gian dùng làm chỉ mục List và làm đuôi key Hash.

    Luôn UTC, luôn bỏ offset, rộng cố định ``YYYY-MM-DD_HH:MM:SS``. Ba tính
    chất này là bắt buộc chứ không phải thẩm mỹ: rộng cố định + đệm 0 thì so
    sánh chuỗi mới trùng với thứ tự thời gian (Lua dựa vào đó), và chỉ một
    dạng duy nhất thì một nến mới không thể sinh ra hai key khác nhau.
    """
    if isinstance(bartime, datetime):
        value = (bartime.astimezone(timezone.utc) if bartime.tzinfo else bartime).replace(
            microsecond=0, tzinfo=None
        )
    else:
        value = datetime.fromtimestamp(int(float(bartime)), timezone.utc).replace(tzinfo=None)
    return value.strftime("%Y-%m-%d_%H:%M:%S")


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
    """Payload event. ``bartime`` dùng đúng định dạng mốc của key Hash nên
    consumer nối thẳng ra key được, không phải chuyển đổi gì."""
    o, h, low_text, c, v = _candle_fields(open_, high, low, close, volume, signature)
    return (
        f'{{"bartime":"{_stamp(bartime)}","open":{o},"high":{h},"low":{low_text},'
        f'"close":{c},"volume":{v}}}'
    )


def _pipeline_candles_to_args(candles: list[dict[str, Any]]) -> list[str]:
    """Sort và coalesce candle theo mốc thời gian trước khi gọi Lua.

    Mỗi nến chiếm 7 ARGV: stamp, open, high, low, close, volume, payload
    JSON của event. Khử trùng theo stamp (bản cuối thắng) rồi sắp tăng dần
    để Lua không phải xử lý input lộn xộn.
    """
    unique = {_stamp(candle["timestamp"]): candle for candle in candles}
    args: list[str] = []
    for stamp in sorted(unique):
        candle = unique[stamp]
        signature = candle.get("_signature") or warehouse_value_signature(
            candle["open"], candle["high"], candle["low"], candle["close"], candle.get("volume")
        )
        args.extend((stamp, *_candle_fields(
            candle["open"], candle["high"], candle["low"], candle["close"],
            candle.get("volume"), signature,
        ), _candle_json(
            candle["timestamp"], candle["open"], candle["high"], candle["low"],
            candle["close"], candle.get("volume"), signature,
        )))
    return args


def _sql_rows_to_args(rows: list[tuple[Any, ...]]) -> list[str]:
    """Mỗi nến chiếm 6 ARGV: stamp, open, high, low, close, volume."""
    args: list[str] = []
    for bartime, open_, high, low, close, volume in rows:
        signature = warehouse_value_signature(open_, high, low, close, volume)
        args.extend((_stamp(bartime), *_candle_fields(
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
                pending.update({_stamp(item["timestamp"]): item for item in candles})
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
            return "update", (key, [values[item] for item in sorted(values)])
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
                failed = {_stamp(item["timestamp"]): item for item in candles}
                failed.update(self._updates.get(key, {}))
                self._updates[key] = failed
            else:
                for key in job[1]:
                    self._reconciles[key] = None
            self._condition.notify()

    @staticmethod
    def _keys(settings: dict[str, Any], tf_code: str, symbol: str) -> tuple[str, str]:
        """Trả về (list_key, candle_prefix).

        List key chính là tên gốc ``{prefix}_{SYMBOL}_{TIMEFRAME}``; key nến
        chỉ là nó nối thêm ``":" + stamp``. Một phép nối duy nhất, nên bên đọc
        cầm tên List và một phần tử bất kỳ trong đó là dựng ra key nến ngay.
        """
        base = f"{settings['key_prefix']}_{symbol}_{tf_code}"
        return base, f"{base}:"

    def _scripts(self, settings: dict[str, Any]) -> tuple[Any, Any]:
        client = self._get_client(settings)
        if self._incremental is None:
            self._incremental = client.register_script(_INCREMENTAL_SCRIPT)
            self._reconcile = client.register_script(_RECONCILE_SCRIPT)
        return self._incremental, self._reconcile

    def _publish_one(
        self, config: dict[str, Any], symbol: str, tf_code: str, candles: list[dict[str, Any]],
    ) -> None:
        if not candles:
            return
        settings = config["redis"]
        list_key, candle_prefix = self._keys(settings, tf_code, symbol)
        prefix = (
            '{"symbol":' + json.dumps(symbol) + ',"timeframe":'
            + json.dumps(tf_code) + ',"candles":['
        )
        script, _ = self._scripts(settings)
        args = _pipeline_candles_to_args(candles)
        stride = _PUBLISH_BATCH_SIZE * 7
        for offset in range(0, len(args), stride):
            script(
                keys=[list_key, settings["event_channel"]],
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
        list_key, candle_prefix = self._keys(settings, tf_code, symbol)
        _, script = self._scripts(settings)
        result = script(keys=[list_key], args=[candle_prefix, *_sql_rows_to_args(rows)])
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
                list_key, candle_prefix = self._keys(settings, tf_code, symbol)
                script(
                    keys=[list_key],
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
