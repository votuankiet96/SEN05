"""Live real-time fetch: TradingView straight to Redis, independent of SQL."""
from __future__ import annotations
import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable

import redis

from .auth import AuthError
from ..log import log_event
from .pipeline import validate_candles
from .sql_connector import Pair, pair_key, select_pairs
from .websocket import FetchRequest, fetch_candles_batch, request_key

LOGGER = logging.getLogger(__name__)
_MAX_CONSECUTIVE_GROUP_FAILURES = 2
_MAX_TOTAL_GROUP_FAILURES = 3
_CYCLE_BUDGET_SECONDS = 120
_PUSH_BATCH_SIZE = 100

# File này chạy live: mỗi cycle lấy nến mới nhất từ TradingView và đẩy thẳng
# lên Redis. Live không đụng SQL -- SQL là việc riêng của backfill. Redis
# List mỗi pair giữ tối đa redis.bars_per_snapshot nến đóng gần nhất -- đúng
# bằng số đó nếu pair có đủ lịch sử thật, ít hơn nếu lịch sử TradingView của
# pair đó ngắn hơn (ví dụ symbol mới niêm yết ở khung W/D).
#
# Cơ chế: mỗi cycle fetch một đuôi nhỏ (live.bars_per_request) cho mọi pair,
# gộp theo symbol để giảm số socket. Nếu List Redis của pair đó đã đủ
# bars_per_snapshot nến (hoặc đã xác nhận đây là trần lịch sử thật, xem
# _needs_full_reload) và đuôi mới fetch nối liền nến cuối đang có, chỉ cần
# đẩy đúng đuôi nhỏ này (RPUSH nến mới, LPOP/DEL nến cũ dư ra qua Lua). Nếu
# Redis rỗng, hoặc có gap so với đuôi vừa fetch, tự fetch lại đủ
# bars_per_snapshot nến rồi đẩy qua cùng cơ chế đó -- tự phục hồi, không cần
# một bước "bootstrap" tách riêng khỏi vòng lặp thường. Pair có lịch sử thật
# ngắn hơn window chỉ bị full reload cho tới khi TradingView xác nhận (trả
# về ít hơn window nến dù đã xin đủ) -- từ đó chỉ còn đường tail-push bình
# thường, không ép full reload mỗi cycle nữa (marker "dp:live:ceiling:...",
# tự hết hạn nếu bars_per_snapshot trong config đổi).
#
# Không tự PUBLISH message riêng nào cho OG (bỏ kênh redis.event_channel /
# "dp:events:candles" 2026-09-23, sau khi order_gateway xác nhận đã chuyển
# hẳn sang tự lắng nghe Redis keyspace notification trên chính HSET/RPUSH ở
# trên -- không còn ai subscribe kênh cũ). OG tự biết "key nào vừa bị ghi"
# từ chính Redis, DP không cần soạn thêm message nào nữa.

HASH_FIELDS = ("timestamp", "open", "high", "low", "close", "time_update")

# ARGV: [1]=max_size [2]=candle_prefix, rồi mỗi nến 6 ô: stamp, open, high,
# low, close, time_update. Chỉ HSET/RPUSH nến nào thật sự mới hoặc đổi giá;
# evict đúng phần dư ra khỏi max_size từ đầu List. Script này không đổi cho
# dù nạp 3 nến hay 500 nến. Không tự PUBLISH gì nữa (bỏ 2026-09-23, xem
# module docstring) -- OG tự biết "key nào vừa bị ghi" qua Redis keyspace
# notification của chính HSET/RPUSH dưới đây, không cần DP báo thêm.
_INCREMENTAL_SCRIPT = """
local list_key = KEYS[1]
local max_size, candle_prefix = tonumber(ARGV[1]), ARGV[2]
local changed, added = 0, 0
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
for i = 3, #ARGV, 6 do
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
return {changed, added, evicted_count}
"""

_client: Any | None = None
_script: Any | None = None
_circuit_open_until = 0.0


def _get_client(settings: dict[str, Any]) -> Any:
    global _client
    if _client is None:
        _client = redis.Redis(
            host=settings["host"], port=settings["port"], db=settings["db"],
            username=settings["username"] or None, password=settings["password"] or None,
            socket_connect_timeout=settings["timeout_seconds"],
            socket_timeout=settings["timeout_seconds"], socket_keepalive=True,
            health_check_interval=30, decode_responses=True,
        )
    return _client


def _get_script(settings: dict[str, Any]) -> Any:
    global _script
    if _script is None:
        _script = _get_client(settings).register_script(_INCREMENTAL_SCRIPT)
    return _script


def _keys(settings: dict[str, Any], tf_code: str, symbol: str) -> tuple[str, str]:
    # Key Hash = key List nối thêm ":" + stamp -- một phép nối duy nhất.
    base = f"{settings['key_prefix']}_{symbol}_{tf_code}"
    return base, f"{base}:"


def _stamp(value: datetime) -> str:
    # Mốc UTC 'YYYY-MM-DD HH:MM:SS', rộng cố định để so sánh chuỗi đúng thứ tự.
    normalized = value.astimezone(timezone.utc) if value.tzinfo else value
    return normalized.replace(microsecond=0, tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _parse_stamp(value: str) -> datetime:
    # Stamp luôn là UTC theo contract -- gắn lại tzinfo để so sánh được với
    # timestamp đã validate_candles() chuẩn hoá (luôn timezone-aware).
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


_PRICE_SCALE = Decimal("0.01")


def _round_price(value: Decimal) -> str:
    # Redis chỉ cần 2 chữ số thập phân (chốt 2026-09-24, operator yêu cầu) --
    # không còn giữ scale DECIMAL(18,8) của warehouse nữa, vì live không ghi
    # SQL. ROUND_HALF_UP khớp đúng quy ước _decimal_text() phía
    # sql_connector.py để nhất quán cách làm tròn trong toàn hệ thống.
    # validate_candles() đã đảm bảo giá trị hữu hạn trước khi candle tới đây.
    return str(value.quantize(_PRICE_SCALE, rounding=ROUND_HALF_UP))


def _prices(candle: dict[str, Any]) -> tuple[str, ...]:
    # 2 chữ số thập phân, không dùng chung hàm với sql_connector.py nữa --
    # live và SQL giờ có 2 quy ước làm tròn độc lập, tách hẳn khỏi nhau.
    return tuple(_round_price(candle[key]) for key in ("open", "high", "low", "close"))


def _candles_to_args(candles: list[dict[str, Any]]) -> list[str]:
    # 6 ô mỗi nến: stamp, 4 giá, time_update. time_update giờ là thời điểm
    # DP fetch/đẩy nến này lên Redis (không còn SQL CreatedAt, vì live
    # không ghi SQL nữa).
    now_stamp = _stamp(datetime.now(timezone.utc))
    args: list[str] = []
    for candle in candles:
        stamp = _stamp(candle["timestamp"])
        args.extend((stamp, *_prices(candle), now_stamp))
    return args


def _ceiling_key(list_key: str) -> str:
    # Namespace ngoài hẳn "{key_prefix}_" nên không khớp pattern PSUBSCRIBE
    # "{key_prefix}_*" phía OG (event_listener.py) -- marker này thuần nội
    # bộ live.py, OG không cần biết và không nên thấy nó.
    return f"dp:live:ceiling:{list_key}"


def _needs_full_reload(
    settings: dict[str, Any], list_key: str, earliest_new: datetime, minutes: int,
) -> bool:
    # Redis rỗng, hoặc đuôi vừa fetch không nối liền nến cuối đang có -> phải
    # nạp lại đủ bars_per_snapshot nến, không chỉ đẩy đuôi nhỏ.
    client = _get_client(settings)
    window = int(settings["bars_per_snapshot"])
    if client.llen(list_key) < window:
        # List ngắn hơn window: có thể đang bootstrap (cần full reload để
        # bắt kịp), hoặc pair này có lịch sử TradingView thật NGẮN HƠN
        # window (sẽ không bao giờ dài thêm) -- 2 trường hợp khác nhau.
        # Marker "dp:live:ceiling:..." chỉ được ghi ở _push_to_redis's
        # caller khi một lần reload đã xác nhận trần thật (xem
        # run_live_pairs). Marker khớp đúng window hiện tại -> đã biết
        # chắc, không ép full reload nữa mỗi cycle chỉ vì List ngắn; đổi
        # bars_per_snapshot trong config sẽ tự làm marker cũ hết khớp,
        # tự quay lại full reload để dò lại đúng trần mới.
        if client.get(_ceiling_key(list_key)) != str(window):
            return True
    tail = client.lindex(list_key, -1)
    if tail is None:
        return True
    return earliest_new > _parse_stamp(tail) + timedelta(minutes=minutes)


def _push_to_redis(
    config: dict[str, Any], symbol: dict[str, Any], timeframe: dict[str, Any],
    candles: list[dict[str, Any]],
) -> None:
    settings = config["redis"]
    list_key, candle_prefix = _keys(settings, timeframe["code"], symbol["symbol"])
    window = int(settings["bars_per_snapshot"])
    script = _get_script(settings)
    args = _candles_to_args(candles)
    stride = _PUSH_BATCH_SIZE * 6
    for offset in range(0, len(args), stride):
        script(
            keys=[list_key],
            args=[window, candle_prefix, *args[offset:offset + stride]],
        )


def _open_circuit(config: dict[str, Any], exc: Exception) -> None:
    global _circuit_open_until, _client, _script
    already_open = _circuit_open_until > time.monotonic()
    _circuit_open_until = time.monotonic() + int(config["redis"]["circuit_cooldown_seconds"])
    _client = _script = None
    if not already_open:
        log_event(
            LOGGER, logging.WARNING, "REDIS_PUBLISH_FAILED", "MEDIUM", component="redis",
            error_type=type(exc).__name__, action="retaining pairs and retrying after cooldown",
        )


def _mark_recovered() -> None:
    global _circuit_open_until
    if _circuit_open_until:
        log_event(LOGGER, logging.INFO, "REDIS_PUBLISH_RECOVERED", "NONE", component="redis")
    _circuit_open_until = 0.0


def _log_pair_failure(
    symbol: dict[str, Any], timeframe: dict[str, Any], error: Exception, *, stage: str,
) -> None:
    log_event(
        LOGGER, logging.ERROR, "PAIR_FAILED", "HIGH", component="live",
        workflow="live", symbol=f"{symbol['exchange']}:{symbol['symbol']}",
        timeframe=timeframe["code"], stage=stage, error_type=type(error).__name__,
        error=error, action="pair retried next live cycle",
    )


def run_live_pairs(
    config: dict[str, Any], pairs: list[Pair], *,
    stop_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """One finite live pass: sync every pair's latest closed candles to Redis."""
    # Không còn watermark hay pending phải nhớ qua cycle: mỗi cycle tự kiểm
    # lại trạng thái Redis và tự vá nếu thiếu/gap, nên tự phục hồi hoàn toàn.
    started = time.monotonic()
    summary: dict[str, Any] = {
        "pairs": len(pairs), "ok": 0, "failed": 0, "reloaded": 0,
        "group_failures": 0, "failed_pairs": [], "redis_circuit_open": False,
    }
    if not bool(config["redis"]["enabled"]):
        raise RuntimeError("live requires redis.enabled=true; it no longer writes SQL")
    processed_keys: set[str] = set()

    def mark_failed(pair: Pair, exc: Exception, stage: str) -> None:
        summary["failed"] += 1
        key = pair_key(pair)
        summary["failed_pairs"].append(key)
        processed_keys.add(key)
        _log_pair_failure(pair[0], pair[1], exc, stage=stage)

    if time.monotonic() < _circuit_open_until:
        summary["redis_circuit_open"] = True
        for pair in pairs:
            mark_failed(pair, RuntimeError("redis circuit open"), "redis")
        summary["duration_seconds"] = 0.0
        return summary

    tail_bars = int(config["live"]["bars_per_request"])
    closed_only = bool(config["live"]["closed_candles_only"])
    window = int(config["redis"]["bars_per_snapshot"])
    settings = config["redis"]
    by_symbol: dict[tuple[str, str], list[Pair]] = {}
    for pair in pairs:
        by_symbol.setdefault((pair[0]["exchange"], pair[0]["symbol"]), []).append(pair)

    consecutive = total_failures = 0
    for group in by_symbol.values():
        if (stop_requested and stop_requested()) or time.monotonic() - started >= _CYCLE_BUDGET_SECONDS:
            break
        try:
            tail_requests = [FetchRequest(s, t, tail_bars, tail_bars) for s, t in group]
            fetched = fetch_candles_batch(config, tail_requests)
        except AuthError:
            raise
        except Exception as exc:
            summary["group_failures"] += 1
            consecutive += 1
            total_failures += 1
            for pair in group:
                mark_failed(pair, exc, "fetch")
            if consecutive >= _MAX_CONSECUTIVE_GROUP_FAILURES or total_failures >= _MAX_TOTAL_GROUP_FAILURES:
                log_event(
                    LOGGER, logging.ERROR, "LIVE_CIRCUIT_OPEN", "HIGH", component="live",
                    consecutive_failures=consecutive, cycle_failures=total_failures,
                    action="remaining symbol batches deferred to the next cycle",
                )
                break
            continue
        consecutive = 0
        try:
            plan: list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]] = []
            reload_group: list[Pair] = []
            for symbol, timeframe in group:
                valid = validate_candles(
                    fetched[request_key(FetchRequest(symbol, timeframe, tail_bars, tail_bars))].candles,
                    timeframe, closed_only=closed_only,
                )
                if not valid:
                    continue
                list_key, _prefix = _keys(settings, timeframe["code"], symbol["symbol"])
                if _needs_full_reload(settings, list_key, valid[0]["timestamp"], int(timeframe["minutes"])):
                    reload_group.append((symbol, timeframe))
                else:
                    plan.append((symbol, timeframe, valid))
            reload_keys = {pair_key(pair) for pair in reload_group}
            if reload_group:
                reload_requests = [FetchRequest(s, t, window, window) for s, t in reload_group]
                reload_fetched = fetch_candles_batch(config, reload_requests)
                for symbol, timeframe in reload_group:
                    valid = validate_candles(
                        reload_fetched[request_key(FetchRequest(symbol, timeframe, window, window))].candles,
                        timeframe, closed_only=closed_only,
                    )[-window:]
                    if valid and len(valid) < window:
                        # TradingView đã trả lời đủ 1 lần fetch bars=window mà
                        # vẫn ít hơn window -- đây là trần lịch sử thật của
                        # pair này (không phải thiếu tạm thời), ghi nhận lại
                        # để _needs_full_reload() không ép full reload nữa mỗi
                        # cycle chỉ vì List ngắn hơn window (xem đó).
                        list_key, _prefix = _keys(settings, timeframe["code"], symbol["symbol"])
                        _get_client(settings).set(_ceiling_key(list_key), str(window))
                    plan.append((symbol, timeframe, valid))
            for symbol, timeframe, candles in plan:
                _push_to_redis(config, symbol, timeframe, candles)
                summary["ok"] += 1
                key = pair_key((symbol, timeframe))
                processed_keys.add(key)
                if key in reload_keys:
                    summary["reloaded"] += 1
            _mark_recovered()
        except AuthError:
            raise
        except redis.RedisError as exc:
            _open_circuit(config, exc)
            summary["redis_circuit_open"] = True
            for pair in pairs:
                if pair_key(pair) not in processed_keys:
                    mark_failed(pair, exc, "redis")
            break
        except Exception as exc:
            for pair in group:
                if pair_key(pair) not in processed_keys:
                    mark_failed(pair, exc, "reload")
    summary["duration_seconds"] = round(time.monotonic() - started, 3)
    return summary


def run_live_cycle(
    config: dict[str, Any], *, symbol: str | None = None, timeframe: str | None = None,
) -> dict[str, Any]:
    """Run exactly one finite live cycle (manual/debug entry point)."""
    if not config["live"].get("enabled", True):
        raise RuntimeError("live fetching is disabled in config.yaml")
    pairs = select_pairs(config, live=True, symbol_filter=symbol, timeframe_filter=timeframe)
    return run_live_pairs(config, pairs)


def shutdown_redis() -> bool:
    """Close the module-level Redis connection on service stop."""
    global _client, _script
    closed = True
    if _client is not None:
        try:
            _client.close()
        except Exception:
            closed = False
    _client = _script = None
    return closed
