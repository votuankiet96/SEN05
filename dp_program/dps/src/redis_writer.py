"""Mọi thứ về Redis của DPS: hợp đồng ghi của live và mọi truy cập Redis (clean, seed, ghi một mốc, checkpoint).

Hai phần có tiêu đề riêng bên dưới:
  1. Hợp đồng Redis của live (khớp `dp_program/engine/live.py`), hàm thuần không I/O: tên key, định dạng stamp và
     giá, bộ field của Hash, Lua script ghi nến và cách đóng gói tham số. `test_contract.py` đối chiếu từng phần với
     live thật để chống lệch hợp đồng. Mỗi nến là một Hash `{key_List}:{stamp}` (6 field) và một List
     `{prefix}_{SYMBOL}_{TF}` chứa các stamp GIẢM DẦN (index 0 = nến mới nhất), giữ tối đa `window` nến gần nhất.
  2. Writer: DPS chỉ ghi vào db nằm trong `allowed_dbs`; db đó chỉ dành cho DPS nên mỗi lần chạy mới xóa sạch nó trước\n     (`clean`, không bao giờ FLUSHALL). Mỗi mốc ghi trong MỘT
     MULTI/EXEC gồm các nến + `dps:clock` + checkpoint `dps:state`, nên consumer không thấy trạng thái nửa vời và
     checkpoint luôn khớp dữ liệu. Script Lua giống hệt live nên idempotent: ghi lại cùng giá thì không sinh `hset`.
     Các key điều khiển `dps:*` nằm ngoài mẫu `L_CANDLE_*` mà consumer lắng nghe.
"""
from __future__ import annotations

import functools
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from itertools import groupby
from typing import Any, Sequence

import redis

from configuration import RedisConfig
from runtime import Candle, DpsError, Pair, Tick


# ---------------------------------------------------------------------------------------------
# 1. Hợp đồng Redis của live: hàm thuần, không I/O
# ---------------------------------------------------------------------------------------------
HASH_FIELDS = ("timestamp", "open", "high", "low", "close", "time_update")

# ARGV: [1]=max_size [2]=hash_prefix [3]=ttl_seconds, rồi mỗi nến 6 ô: stamp, open, high, low, close,
# time_update. Nến được đẩy theo thứ tự tăng dần nên mỗi nến mới thành index 0 của List. Chỉ HSET (+EXPIRE)
# nến thật sự mới hoặc đổi giá; evict phần dư ra khỏi max_size từ đuôi List. Phải giống HỆT script của live.
INCREMENTAL_SCRIPT = """
local list_key = KEYS[1]
local max_size, candle_prefix, ttl = tonumber(ARGV[1]), ARGV[2], tonumber(ARGV[3])
local changed, added = 0, 0
local function insert_sorted(stamp)
    local current, rebuilt, inserted = redis.call('LRANGE', list_key, 0, -1), {}, false
    for _, existing in ipairs(current) do
        if not inserted and stamp > existing then
            table.insert(rebuilt, stamp); inserted = true
        end
        table.insert(rebuilt, existing)
    end
    if not inserted then table.insert(rebuilt, stamp) end
    redis.call('DEL', list_key)
    redis.call('RPUSH', list_key, unpack(rebuilt))
end
for i = 4, #ARGV, 6 do
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
        redis.call('EXPIRE', key, ttl)
        changed = changed + 1
    end
    if not redis.call('LPOS', list_key, stamp) then
        local head = redis.call('LINDEX', list_key, 0)
        if not head or stamp > head then
            redis.call('LPUSH', list_key, stamp)
        else
            insert_sorted(stamp)
        end
        added = added + 1
    end
end
local evicted_count = math.max(0, redis.call('LLEN', list_key) - max_size)
if evicted_count > 0 then
    for _, bt in ipairs(redis.call('RPOP', list_key, evicted_count)) do
        redis.call('DEL', candle_prefix .. bt)
    end
end
return {changed, added, evicted_count}
"""

_STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_PRICE_SCALE = Decimal("0.01")


def stamp(value: datetime) -> str:
    # Mốc UTC 'YYYY-MM-DD HH:MM:SS', rộng cố định nên so sánh chuỗi đúng thứ tự thời gian (Lua dựa vào đó).
    normalized = value.astimezone(timezone.utc) if value.tzinfo else value
    return normalized.replace(microsecond=0, tzinfo=None).strftime(_STAMP_FORMAT)


def parse_stamp(text: str) -> datetime:
    # Ngược của stamp(): trả datetime naive UTC.
    return datetime.strptime(text, _STAMP_FORMAT)


def round_price(value: Decimal) -> str:
    # Tối đa 2 số lẻ (ROUND_HALF_UP) rồi bỏ số 0 thừa: "7649.9", "25653". Dùng format "f" rồi rstrip,
    # KHÔNG dùng Decimal.normalize() vì nó đổi 52020 thành "5.202E+4".
    text = format(value.quantize(_PRICE_SCALE, rounding=ROUND_HALF_UP), "f")
    return text.rstrip("0").rstrip(".")


def list_key(prefix: str, pair: Pair) -> str:
    return f"{prefix}_{pair.symbol}_{pair.timeframe}"


def hash_prefix(key: str) -> str:
    # Key Hash = key List nối thêm ":" + stamp — một phép nối duy nhất; tên List không chứa ":".
    return f"{key}:"


def script_args(window: int, key: str, ttl_seconds: int, candles: Sequence[Candle], time_update: str) -> list:
    """Tham số ARGV cho INCREMENTAL_SCRIPT: [window, hash_prefix, ttl, rồi 6 ô mỗi nến]."""
    args: list = [window, hash_prefix(key), ttl_seconds]
    for candle in candles:
        args.extend((
            stamp(candle.bar_time), round_price(candle.open), round_price(candle.high),
            round_price(candle.low), round_price(candle.close), time_update,
        ))
    return args


# ---------------------------------------------------------------------------------------------
# 2. Writer: clean, seed, ghi một mốc nguyên tử, checkpoint
# ---------------------------------------------------------------------------------------------
CLOCK_KEY, STATE_KEY = "dps:clock", "dps:state"
_SEED_CHUNK = 100   # nến mỗi lần gọi Lua (giống live)
_SEED_FLUSH = 50    # số lần gọi Lua mỗi round-trip khi seed


class GuardError(DpsError):
    """Từ chối ghi vì db đích không an toàn."""


class RedisUnavailable(DpsError):
    """Redis tạm thời không trả lời (mất kết nối, hết thời gian chờ, đang nạp dữ liệu, chưa ghi được đĩa): người gọi nên chờ rồi thử lại."""


_TRANSIENT_ERRORS = (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError)    # BusyLoadingError là con của ConnectionError
_TRANSIENT_REPLIES = ("MISCONF", "LOADING", "READONLY")


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, redis.exceptions.AuthenticationError):       # con của ConnectionError nhưng là lỗi cấu hình: chờ cũng không hết
        return False
    if isinstance(exc, _TRANSIENT_ERRORS):
        return True
    return isinstance(exc, redis.exceptions.ResponseError) and str(exc).startswith(_TRANSIENT_REPLIES)


def _translate(method: Any) -> Any:
    """Đổi lỗi Redis TẠM THỜI thành RedisUnavailable (để chương trình chờ rồi chạy tiếp); lỗi khác (sai kiểu key, script lỗi...) giữ nguyên."""
    @functools.wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(self, *args, **kwargs)
        except redis.exceptions.RedisError as exc:
            if _is_transient(exc):
                raise RedisUnavailable(type(exc).__name__) from exc
            raise
    return wrapper


@dataclass(frozen=True, slots=True)
class WriteStats:
    candles: int = 0
    changed: int = 0    # nến thật sự HSET (mới hoặc đổi giá)
    added: int = 0      # phần tử mới vào List
    evicted: int = 0    # nến bị đẩy ra khỏi cửa sổ


def _sums(results: Sequence[Sequence[int]]) -> tuple[int, int, int]:
    return (sum(r[0] for r in results), sum(r[1] for r in results), sum(r[2] for r in results))


def _connect(config: RedisConfig) -> redis.Redis:
    return redis.Redis(
        host=config.host, port=config.port, db=config.db, username=config.username or None,
        password=config.password or None, socket_connect_timeout=config.connect_timeout_seconds,
        socket_timeout=config.socket_timeout_seconds, socket_keepalive=True, health_check_interval=30,
        decode_responses=True,
    )


class RedisWriter:
    def __init__(self, config: RedisConfig, *, client: Any = None) -> None:
        self._config = config
        self._probe = client is None            # chỉ kết nối thật mới cần thử mở TCP nhanh trong ping()
        self._client = client if client is not None else _connect(config)
        self._script: Any = None

    def __enter__(self) -> "RedisWriter":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001 - đóng kết nối không được làm hỏng luồng xử lý lỗi chính
            pass

    @property
    def _lua(self) -> Any:
        if self._script is None:
            self._script = self._client.register_script(INCREMENTAL_SCRIPT)
        return self._script

    # ------------------------------------------------------------------ an toàn
    def _check_db(self) -> None:
        if self._config.db not in self._config.allowed_dbs:
            raise GuardError(f"db {self._config.db} is not in allowed_dbs {list(self._config.allowed_dbs)}")

    @_translate
    def ping(self) -> bool:
        """Redis đã trả lời lại chưa (dùng khi chờ). Kết nối thật thử mở TCP nhanh trước, để một lần thử không bị vòng thử lại của thư viện giữ ~40 giây."""
        if self._probe:
            try:
                socket.create_connection((self._config.host, self._config.port), timeout=self._config.connect_timeout_seconds).close()
            except OSError as exc:
                raise RedisUnavailable(type(exc).__name__) from exc
        return bool(self._client.ping())

    @_translate
    def clean(self) -> int:
        """Xóa toàn bộ db đích (FLUSHDB ASYNC) và trả số key đã có. db đích chỉ dành cho DPS nên chỉ cần nằm trong `allowed_dbs`; không bao giờ FLUSHALL."""
        self._check_db()
        size = self._client.dbsize()
        self._client.flushdb(asynchronous=True)
        return size
    # ------------------------------------------------------------------ đọc
    def size(self) -> int:
        return self._client.dbsize()

    @_translate
    def read_state(self) -> dict[str, str]:
        return self._client.hgetall(STATE_KEY)

    def read_clock(self) -> str | None:
        return self._client.get(CLOCK_KEY)

    # ------------------------------------------------------------------ ghi
    @_translate
    def seed(self, candles: Sequence[Candle], at: datetime) -> WriteStats:
        """Nạp lịch sử đã đóng trước T0 (mỗi pair theo thứ tự tăng dần). Không cần nguyên tử: consumer bật sau khi seed."""
        cfg, time_update = self._config, stamp(at)
        pipe = self._client.pipeline(transaction=False)
        count = queued = changed = added = evicted = 0
        for pair, group in groupby(candles, key=lambda candle: candle.pair):
            key, batch = list_key(cfg.key_prefix, pair), list(group)
            for offset in range(0, len(batch), _SEED_CHUNK):
                chunk = batch[offset:offset + _SEED_CHUNK]
                self._lua(keys=[key], args=script_args(cfg.bars_per_snapshot, key, cfg.hash_ttl_seconds, chunk, time_update), client=pipe)
                count, queued = count + len(chunk), queued + 1
                if queued >= _SEED_FLUSH:
                    c, a, e = _sums(pipe.execute())
                    changed, added, evicted, queued = changed + c, added + a, evicted + e, 0
        if queued:
            c, a, e = _sums(pipe.execute())
            changed, added, evicted = changed + c, added + a, evicted + e
        return WriteStats(count, changed, added, evicted)

    @_translate
    def begin_run(self, run_id: str, at: datetime, *, tick_seq: int = 0, digest: str = "") -> None:
        """Ghi checkpoint (mặc định khởi đầu: tick_seq = 0) và đồng hồ ảo = `at`; nạp lại giữa chừng thì truyền mốc đã ghi gần nhất."""
        sim_time = stamp(at)
        pipe = self._client.pipeline(transaction=True)
        pipe.set(CLOCK_KEY, sim_time)
        pipe.hset(STATE_KEY, mapping={"run_id": run_id, "tick_seq": tick_seq, "sim_time": sim_time, "digest": digest, "status": "running"})
        pipe.execute()

    @_translate
    def write_tick(self, tick: Tick, *, run_id: str, digest: str) -> WriteStats:
        """Ghi mọi nến của mốc + đồng hồ ảo + checkpoint trong MỘT MULTI/EXEC; `time_update` = giờ ảo của mốc."""
        cfg, sim_time = self._config, stamp(tick.release)
        pipe = self._client.pipeline(transaction=True)
        for candle in tick.candles:
            key = list_key(cfg.key_prefix, candle.pair)
            self._lua(keys=[key], args=script_args(cfg.bars_per_snapshot, key, cfg.hash_ttl_seconds, [candle], sim_time), client=pipe)
        pipe.set(CLOCK_KEY, sim_time)
        pipe.hset(STATE_KEY, mapping={"run_id": run_id, "tick_seq": tick.seq, "sim_time": sim_time, "digest": digest, "status": "running"})
        changed, added, evicted = _sums(pipe.execute()[:len(tick.candles)])
        return WriteStats(len(tick.candles), changed, added, evicted)

    @_translate
    def mark_finished(self) -> None:
        self._client.hset(STATE_KEY, "status", "finished")
