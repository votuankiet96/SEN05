"""Đồng bộ Redis Hash + List từ SQL mà không chặn đường ghi warehouse.

    LIST  L_CANDLE_BTCUSD_H1                        [..., 2026-09-14 03:00:00]
    HASH  L_CANDLE_BTCUSD_H1:2026-09-14 03:00:00    timestamp   2026-09-14 03:00:00
                                                    open        77545.35000000
                                                    high        77807.90000000
                                                    low         77450.75000000
                                                    close       77537.05000000
                                                    time_update 2026-09-14 03:05:12

Key Hash = key List nối thêm ``":" + <phần tử lấy từ List>``; một phép nối
duy nhất, consumer không cần quy ước nào khác.
"""
# Phần tử List, đuôi key Hash và field `timestamp` là CÙNG MỘT CHUỖI
# 'YYYY-MM-DD HH:MM:SS' -- open time (Fact_OHLCV.BarTime). Cả ba lấy từ
# _stamp() nên không chỗ nào tự định dạng lại và không thể lệch nhau.
#
# Mốc rộng cố định và đệm 0 nên so sánh chuỗi trùng khít thứ tự thời gian, kể cả
# khi có dấu cách và dấu ':' bên trong: các ký tự phân tách nằm ở vị trí cố định
# nên không bao giờ ảnh hưởng tới thứ tự giữa hai mốc. Lua dựa hẳn vào tính chất
# đó ('<'/'>', không 'tonumber'). Định dạng phải tuyệt đối nhất quán: chỉ một bản
# ghi lệch là vừa hỏng thứ tự vừa sinh key thứ hai cho cùng một nến.
#
# Hệ quả đã biết và chấp nhận: vì mốc chứa ':', Redis GUI tách mỗi nến thành
# nhiều tầng thư mục. Đánh đổi có chủ ý để giữ 'phần tử List == field timestamp'.
#
# `time_update` là Fact_OHLCV.CreatedAt -- thời điểm row vào SQL, KHÔNG phải
# thời điểm publish Redis. Đó là lý do cả hai đường ghi đều đọc lại row từ SQL
# trước khi ghi: Redis là bản chụp của SQL, không field nào được bịa.
#
# List là chỉ mục duy nhất: mọi nến tồn tại đều có mốc của nó trong List, và
# eviction xoá key nến trong cùng script atomic đã LPOP mốc đó, nên nến mồ côi
# không sinh ra được. Consumer đọc N nến gần nhất bằng LRANGE lấy mốc rồi
# pipeline HGET/HMGET đúng field cần, không phải parse gì.
#
# Pub/Sub vẫn phát JSON chứa symbol, timeframe và các candle thực sự đổi giá
# trị. `bartime` trong event dùng đúng mốc của key Hash để consumer nối thẳng.
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
# Đúng bộ field của một Hash nến, theo thứ tự ghi.
HASH_FIELDS = ("timestamp", "open", "high", "low", "close", "time_update")
# Bốn field giá là phần duy nhất tham gia so sánh "có đổi không".
_PRICE_FIELDS = ("open", "high", "low", "close")

# Key nến được ghép trong Lua từ candle_prefix (deployment một node, không
# dùng Redis Cluster) vì số key mỗi lần gọi là động -- truyền cả trăm key qua
# KEYS[] chỉ làm script khó đọc mà không đổi ngữ nghĩa.
#
# So sánh chỉ chạy trên 4 field giá: `timestamp` là chính mốc đặt tên key nên
# không thể lệch, còn `time_update` đi theo đúng row SQL đã sinh ra giá đó.
# Nhờ vậy ghi lại y hệt vẫn không sinh event.
#
# ARGV: [1]=max_size [2]=candle_prefix [3]=event_prefix, rồi mỗi nến 7 ô:
#       stamp, open, high, low, close, time_update, payload JSON của event.
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
    local old = redis.call('HMGET', key, 'open', 'high', 'low', 'close')
    local differs = false
    for n = 1, 4 do
        if old[n] ~= ARGV[i + n] then differs = true end
    end
    if differs then
        redis.call('HSET', key,
            'timestamp', stamp,
            'open', ARGV[i+1], 'high', ARGV[i+2],
            'low', ARGV[i+3], 'close', ARGV[i+4],
            'time_update', ARGV[i+5])
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
# không còn giữ được xoá qua chính List -- List là chỉ mục duy nhất nên không
# cần SCAN keyspace.
#
# ARGV: [1]=candle_prefix, rồi mỗi nến 6 ô như trên nhưng không có payload
#       event vì reconcile không publish.
_RECONCILE_SCRIPT = """
local list_key = KEYS[1]
local candle_prefix = ARGV[1]
local desired, desired_order, changed = {}, {}, 0
for i = 2, #ARGV, 6 do
    local stamp = ARGV[i]
    local key = candle_prefix .. stamp
    local old = redis.call('HMGET', key, 'open', 'high', 'low', 'close')
    local differs = false
    for n = 1, 4 do
        if old[n] ~= ARGV[i + n] then differs = true end
    end
    if differs then
        redis.call('HSET', key,
            'timestamp', stamp,
            'open', ARGV[i+1], 'high', ARGV[i+2],
            'low', ARGV[i+3], 'close', ARGV[i+4],
            'time_update', ARGV[i+5])
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


def _utc(value: Any) -> datetime:
    """Chuẩn hoá mọi kiểu thời gian về datetime UTC naive, tròn giây."""
    # Naive được coi là đã UTC: SQL DATETIME2(0) của DP lưu UTC không offset.
    if isinstance(value, datetime):
        return (value.astimezone(timezone.utc) if value.tzinfo else value).replace(
            microsecond=0, tzinfo=None
        )
    return datetime.fromtimestamp(int(float(value)), timezone.utc).replace(tzinfo=None)


def _stamp(value: Any) -> str:
    """Mốc `YYYY-MM-DD HH:MM:SS`, luôn UTC, không offset, rộng cố định.

    Dùng cho phần tử List, đuôi key Hash, field `timestamp` và field
    `time_update` -- một hàm duy nhất nên mọi mốc trên Redis cùng một dạng.
    """
    return _utc(value).strftime("%Y-%m-%d %H:%M:%S")


def _prices(open_: Any, high: Any, low: Any, close: Any) -> tuple[str, str, str, str]:
    """Serialize đúng DECIMAL contract của warehouse, không đi qua float."""
    return warehouse_value_signature(open_, high, low, close, None)[:4]  # type: ignore[return-value]


def _candle_json(stamp: str, prices: tuple[str, ...]) -> str:
    """Payload event; `bartime` dùng đúng mốc của key Hash."""
    o, h, low_text, c = prices
    return (
        f'{{"bartime":"{stamp}","open":{o},"high":{h},'
        f'"low":{low_text},"close":{c}}}'
    )


def _rows_to_args(rows: list[tuple[Any, ...]], *, with_event: bool) -> list[str]:
    """Trải các row SQL thành ARGV cho Lua.

    Mỗi nến chiếm 6 ô theo thứ tự stamp, open, high, low, close, time_update;
    thêm ô thứ 7 là payload event khi `with_event`. `timestamp` không chiếm ô
    riêng vì nó bằng đúng `stamp`, Lua ghi thẳng.
    """
    args: list[str] = []
    for bartime, open_, high, low, close, _volume, created_at in rows:
        stamp = _stamp(bartime)
        prices = _prices(open_, high, low, close)
        args.extend((stamp, *prices, _stamp(created_at)))
        if with_event:
            args.append(_candle_json(stamp, prices))
    return args


class _RedisPublisher:
    """Coalesce công việc theo pair trong một worker có retry hữu hạn tài nguyên."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._start_lock = threading.Lock()
        self._updates: dict[_Key, set[datetime]] = {}
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
                # Chỉ giữ mốc, không giữ giá trị provider: lúc publish sẽ đọc
                # lại chính row SQL, nên Redis luôn là bản chụp của SQL.
                self._updates.setdefault(key, set()).update(
                    _utc(item["timestamp"]) for item in candles
                )
            self._condition.notify()

    def enqueue_reconcile_all(
        self, config: dict[str, Any], pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    ) -> None:
        if not bool((config.get("redis") or {}).get("enabled")) or not pairs:
            return
        self._ensure_worker(config)
        with self._condition:
            for symbol, timeframe in pairs:
                self._reconciles[(
                    int(symbol["symbol_id"]), str(symbol["symbol"]), str(timeframe["code"]),
                )] = None
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
            return "update", (key, sorted(self._updates.pop(key)))
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
                    self._publish_one(config, *job[1])
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
                key, bartimes = job[1]
                self._updates.setdefault(key, set()).update(bartimes)
            else:
                for key in job[1]:
                    self._reconciles[key] = None
            self._condition.notify()

    @staticmethod
    def _keys(settings: dict[str, Any], tf_code: str, symbol: str) -> tuple[str, str]:
        """Trả về (list_key, candle_prefix).

        `{prefix}_{SYMBOL}_{TIMEFRAME}`, và key nến chỉ là nó nối thêm
        `":" + stamp`. Một phép nối duy nhất, nên bên đọc cầm tên List và một
        phần tử bất kỳ trong đó là dựng ra key nến ngay. Tên List không chứa
        `":"`; key nến thì có (một của phép nối, hai của giờ-phút-giây).
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
        self, config: dict[str, Any], key: _Key, bartimes: list[datetime],
    ) -> None:
        symbol_id, symbol, tf_code = key
        settings = config["redis"]
        window = int(settings["bars_per_snapshot"])
        # Chỉ lấy `window` mốc mới nhất: nến cũ hơn cửa sổ sẽ bị eviction xoá
        # ngay trong cùng script, nên ghi rồi phát event cho chúng là vô nghĩa.
        rows = read_latest_candles(config, symbol_id, tf_code, window, bartimes[-window:])
        if not rows:
            return
        list_key, candle_prefix = self._keys(settings, tf_code, symbol)
        prefix = (
            '{"symbol":' + json.dumps(symbol) + ',"timeframe":'
            + json.dumps(tf_code) + ',"candles":['
        )
        script, _ = self._scripts(settings)
        args = _rows_to_args(rows, with_event=True)
        stride = _PUBLISH_BATCH_SIZE * 7
        for offset in range(0, len(args), stride):
            script(
                keys=[list_key, settings["event_channel"]],
                args=[window, candle_prefix, prefix, *args[offset:offset + stride]],
            )
        self._mark_recovered()

    def _reconcile_one(
        self, config: dict[str, Any], symbol_id: int, symbol: str, tf_code: str,
    ) -> Any:
        settings = config["redis"]
        rows = read_latest_candles(config, symbol_id, tf_code, int(settings["bars_per_snapshot"]))
        list_key, candle_prefix = self._keys(settings, tf_code, symbol)
        _, script = self._scripts(settings)
        result = script(
            keys=[list_key], args=[candle_prefix, *_rows_to_args(rows, with_event=False)],
        )
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
                    args=[candle_prefix,
                          *_rows_to_args(rows[(symbol_id, tf_code)], with_event=False)],
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
