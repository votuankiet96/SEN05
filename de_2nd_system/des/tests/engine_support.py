"""Redis giả và cấu hình thử cho test data_engine / util / configuration: hermetic, KHÔNG nối Redis thật.

Mô phỏng đúng hợp đồng db0: LIST ``{prefix}_{SYMBOL}_{TF}`` giữ các mốc, MỚI NHẤT ĐỨNG ĐẦU (Core LPUSH); HASH ``<key LIST>:{mốc}`` giữ OHLCV dạng chuỗi; keyspace notification giả
(``emit_hset``). Ghi lại mọi lệnh đã gọi (``calls``) để test kiểm "nến mới chỉ đọc nến mới" mà không phải đoán.
Module này là helper (không có test): pytest chèn thư mục tests vào sys.path nên các file test import thẳng ``engine_support``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
PREFIX = "L_CANDLE"


def stamp_of(bartime: pd.Timestamp) -> str:
    return pd.Timestamp(bartime).strftime(STAMP_FORMAT)


class _Pipe:
    def __init__(self, core: FakeCore) -> None:
        self.core = core
        self.commands: list[tuple] = []

    def hmget(self, key: str, *fields: str) -> None:
        self.commands.append(("hmget", key, fields))

    def execute(self) -> list:
        results = []
        for _, key, fields in self.commands:
            self.core.calls.append(("hmget", key))
            row = self.core.hashes.get(key)
            results.append([row.get(field) for field in fields] if row else [None] * len(fields))
        self.commands = []
        return results


class FakePubSub:
    def __init__(self, core: FakeCore) -> None:
        self.core = core
        self.patterns: list[str] = []
        self.queue: list[dict] = []
        self.closed = False

    def psubscribe(self, *patterns: str) -> None:
        self.patterns.extend(patterns)
        if self.core.confirm_subscribe:
            self.queue.extend({"type": "psubscribe", "pattern": pattern, "data": index + 1} for index, pattern in enumerate(patterns))

    def get_message(self, ignore_subscribe_messages: bool = False, timeout: float = 0) -> dict | None:
        while self.queue:
            message = self.queue.pop(0)
            if ignore_subscribe_messages and message["type"] == "psubscribe":
                continue
            return message
        return None

    def close(self) -> None:
        self.closed = True


class FakeCore:
    """Một Redis db0 giả: dùng làm cả client đọc lẫn client nghe event."""

    def __init__(self, db: int = 0) -> None:
        self.db = db
        self.lists: dict[str, list[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.strings: dict[str, str] = {}  # key chuỗi lẻ, vd dfs:clock (giờ ảo)
        self.calls: list[tuple] = []
        self.flags = "hK"
        self.confirm_subscribe = True
        self.pubsubs: list[FakePubSub] = []
        self.closed = False

    # -- dữ liệu --------------------------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def list_key(symbol: str, timeframe: str) -> str:
        return f"{PREFIX}_{symbol}_{timeframe}"

    def push(self, symbol: str, timeframe: str, stamp: str, row: dict[str, str], *, with_hash: bool = True, duplicate: bool = False) -> None:
        """Core ghi một nến: HMSET HASH rồi LPUSH mốc lên đầu LIST (``duplicate`` = LPUSH mốc hai lần liền; ``with_hash=False`` = LIST đi trước HASH)."""
        key = self.list_key(symbol, timeframe)
        if with_hash:
            self.hashes[f"{key}:{stamp}"] = dict(row)
        self.lists.setdefault(key, []).insert(0, stamp)
        if duplicate:
            self.lists[key].insert(0, stamp)

    def fill(self, symbol: str, timeframe: str, frame: pd.DataFrame) -> None:
        """Nạp cả bảng nến (tăng dần) như Core đã ghi lần lượt từng nến."""
        for _, bar in frame.iterrows():
            self.push(symbol, timeframe, stamp_of(bar["bartime"]), bar_row(bar))

    # -- lệnh Redis -----------------------------------------------------------------------------------------------------------------------------------
    def lrange(self, key: str, start: int, end: int) -> list[str]:
        self.calls.append(("lrange", key, start, end))
        items = self.lists.get(key, [])
        return list(items[start : (None if end == -1 else end + 1)])

    def get(self, key: str) -> str | None:
        self.calls.append(("get", key))
        return self.strings.get(key)

    def pipeline(self, **_kwargs: object) -> _Pipe:
        return _Pipe(self)

    def config_get(self, name: str) -> dict[str, str]:
        return {name: self.flags}

    def pubsub(self) -> FakePubSub:
        pubsub = FakePubSub(self)
        self.pubsubs.append(pubsub)
        return pubsub

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        self.closed = True

    # -- keyspace giả ---------------------------------------------------------------------------------------------------------------------------------
    def emit_hset(self, symbol: str, timeframe: str, stamp: str, *, data: str = "hset") -> None:
        """Redis phát event cho mọi PSUBSCRIBE khớp: channel = __keyspace@{db}__:<key HASH>."""
        channel = f"__keyspace@{self.db}__:{self.list_key(symbol, timeframe)}:{stamp}"
        for pubsub in self.pubsubs:
            if any(channel.startswith(pattern[:-1]) for pattern in pubsub.patterns):
                pubsub.queue.append({"type": "pmessage", "pattern": "", "channel": channel, "data": data})


def bar_row(bar: pd.Series) -> dict[str, str]:
    """Một nến dạng HASH của Core: giá là chuỗi thập phân thuần, KHÔNG có trường volume (Core không ghi volume)."""
    return {field: repr(float(bar[field])) for field in ("open", "high", "low", "close")}


def make_bars(n: int = 600, freq: str = "h") -> pd.DataFrame:
    """Bộ nến tất định (dao động quanh một mức, không trend), đúng hình dạng bảng mà data_engine trả về: bartime datetime64[us] cộng 5 cột float64, volume NaN."""
    i = np.arange(n, dtype=float)
    base = 1000.0 + 30.0 * np.sin(i / 9.0) + 50.0 * np.sin(i / 29.0) + 40.0 * np.sin(i / 71.0 + 1.0) + 6.0 * np.sin(i / 3.1)
    op = base + 0.8 * np.sin(i / 3.3)
    cl = base - 0.8 * np.sin(i / 3.3)
    hi = np.maximum(op, cl) + 2.0 + np.abs(np.sin(i / 5.0))
    lo = np.minimum(op, cl) - 2.0 - np.abs(np.cos(i / 4.0))
    return pd.DataFrame(
        {
            "bartime": pd.date_range("2026-01-01", periods=n, freq=freq).astype("datetime64[us]"),
            "open": op, "high": hi, "low": lo, "close": cl, "volume": np.nan,
        }
    )


# =============================================================================
# Cấu hình thử (đúng hình dạng des_config.yaml mới): nguồn nến AEN (+ OG8), Redis đầu ra, đăng ký module, số lẻ giá.
# =============================================================================

CLOCK_KEY = "dfs:clock"
GROUP_SYMBOLS: tuple[str, ...] = ("US30", "BTCUSD")
OG8_SYMBOLS: tuple[str, ...] = ("EURUSD", "FR40")
GROUP_TIMEFRAMES: tuple[str, ...] = ("M10", "M20", "M30", "M45", "H1", "H2", "H3", "H4")
SERVERS: dict = {
    "aen": {"host": "127.0.0.1", "port": 6379, "password": ""},
    "og8": {"host": "10.11.12.8", "port": 6380, "password": "mat-khau-og8"},
    "de": {"host": "127.0.0.1", "port": 6380, "password": "mat-khau-de"},
}
STORE: dict = {"server": "de", "db": 11, "key_prefix": "L_CONFIG", "keep_versions": 20}


def shared_config(*, og8: bool = False, **changes: object) -> dict:
    """Phần config được nạp lên Redis (mọi thứ trừ ``redis.servers`` và ``redis.store``): giá trị khớp config mẫu nhưng ít symbol. ``og8`` thêm nguồn nến thứ hai; ``changes`` đè các khoá cấp 1
    (``redis``, ``trends``, ``strategies``, ``live``). Trả về dict MỚI mỗi lần gọi nên test sửa tự do."""
    import copy

    sources = {"default": {"server": "aen", "db": 10, "key_prefix": PREFIX, "clock_key": CLOCK_KEY, "symbols": list(GROUP_SYMBOLS)}}
    if og8:
        sources["og8"] = {"server": "og8", "db": 10, "key_prefix": PREFIX, "clock_key": CLOCK_KEY, "symbols": list(OG8_SYMBOLS)}
    config = {
        "redis": {
            "enabled": True,
            "candles": {"timeframes": list(GROUP_TIMEFRAMES), "snapshot_bars": 500, "warmup_bars": 300, "process_on_startup": True, "sources": sources},
            "output": {
                "server": "de", "db": 11, "strategy_prefix": "L_MODSTR", "trend_prefix": "L_MODTRE", "signal_prefix": "L_DE", "channel_prefix": "L_CHANNEL",
                "retention_seconds": 604800, "retention_max_entries": 500,
            },
        },
        "trends": {
            "macd": {"type": "macd", "fast": 5, "slow": 25, "signal": 5},
            "sma20": {"type": "sma", "period": 20},
        },
        "strategies": {
            "ma_cross": {
                "file": "mac_strategy", "trigger": "M10", "symbols": "all",
                "params": {"fast_ma": 13, "slow_ma": 34, "macd_fast": 5, "macd_slow": 25, "macd_signal": 5},
                "atr_period": 5, "max_shift_bars": 3, "validity_seconds": 600, "order_type": "MARKET", "trend": {"macd": "M30", "sma20": "M30"},
            },
        },
        "live": {"log_level": "INFO", "price_decimals": {"EURUSD": 5} if og8 else {}},
    }
    config.update(copy.deepcopy(changes))
    return config


def with_combo(config: dict) -> dict:
    """Thêm chiến lược ``combo`` (khung trigger M30, lệnh STOP, KHÔNG dời tín hiệu, tham chiếu ``macd`` ở khung H1: cùng yếu tố ma_cross dùng ở M30) vào ``config`` (sửa tại chỗ, trả lại chính nó)."""
    config["strategies"]["combo"] = {
        "file": "combo_strategy", "trigger": "M30", "symbols": "all",
        "params": {"ma_period": 20, "macd_fast": 5, "macd_slow": 25, "macd_signal": 5},
        "atr_period": 5, "max_shift_bars": 0, "validity_bars": 3, "order_type": "STOP", "trend": {"macd": "H1"},
    }
    return config


def with_break20(config: dict) -> dict:
    """Thêm yếu tố ``break20`` (loại ``break``) và cho ``combo`` (phải có sẵn, xem ``with_combo``) tham chiếu nó ở khung M45 thay cho ``macd`` @H1 (sửa tại chỗ, trả lại chính nó). Dành cho test về yếu tố loại break."""
    config["trends"]["break20"] = {"type": "break", "lookback": 20}
    config["strategies"]["combo"]["trend"] = {"break20": "M45"}
    return config


def local_config() -> dict:
    """Phần bootstrap (đúng hình dạng ``configuration.load_bootstrap`` trả về) cho test dựng việc."""
    from des.src import configuration

    return configuration._bootstrap({"redis": {"servers": SERVERS, "store": STORE}})


def plan(**kwargs: object):
    """``configuration.Plan`` từ ``shared_config(**kwargs)`` và ``SERVERS``."""
    from des.src import configuration

    return configuration.parse_plan(shared_config(**kwargs), SERVERS)


class FakeSnapshots:
    """Engine giả cho ``process_module`` / ``process_decision``: ``snapshot(pair)`` trả bảng đã nạp cho cặp (None nếu chưa có), ghi lại các cặp đã hỏi."""

    def __init__(self, frames: dict) -> None:
        self.frames = frames
        self.asked: list = []

    def snapshot(self, pair):
        self.asked.append(pair)
        return self.frames.get(pair)


# =============================================================================
# Đồng hồ và engine giả cho vòng lặp sự kiện (configuration._run_loop).
# =============================================================================


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class LoopEngine:
    """Engine giả: hàng đợi event theo từng lần gọi; ``poll_events`` tiến đồng hồ đúng bằng thời gian vòng lặp xin chờ."""

    def __init__(self, clock: FakeClock, pairs: list) -> None:
        self.clock = clock
        self.pairs = tuple(pairs)
        self.batches: list[list] = []
        self.polls = 0

    def poll_events(self, wait: float) -> list:
        self.polls += 1
        self.clock.now += wait
        return self.batches.pop(0) if self.batches else []


class _EvalPipe:
    """Pipeline của Redis đầu ra giả: xếp hàng cả lệnh ghi (``eval``) lẫn lệnh đọc (``hmget``), ``execute`` trả kết quả theo đúng thứ tự xếp hàng."""

    def __init__(self, owner: RecordingRedis) -> None:
        self.owner = owner
        self.queued: list[tuple] = []

    def eval(self, script: str, numkeys: int, *args: object) -> _EvalPipe:
        self.queued.append(("trim" if numkeys == 1 else "eval", args))  # script bỏ mốc cũ có 1 khoá (LIST); script ghi List–Hash có 2 (HASH, LIST)
        return self

    def hmget(self, key: str, *fields: str) -> _EvalPipe:
        self.queued.append(("hmget", key, fields))
        return self

    def hgetall(self, key: str) -> _EvalPipe:
        self.queued.append(("hgetall", key))
        return self

    def execute(self) -> list:
        results = []
        for item in self.queued:
            if item[0] == "eval":
                results.append(self.owner.record(item[1]))
            elif item[0] == "trim":
                results.append(self.owner.trim(item[1]))
            elif item[0] == "hgetall":
                results.append(self.owner.hgetall(item[1]))
            else:
                results.append(self.owner.hmget(item[1], *item[2]))
        self.queued = []
        return results


class RecordingRedis:
    """Redis đầu ra giả: ghi lại các lệnh EVAL (script ghi List–Hash nguyên tử) của ``redis_connector``. ``created`` cố định kết quả (1 = ghi mới, 0 = đã có);
    ``emulate=True`` thì tự giả lập: HASH đã từng được ghi thì trả 0, và LIST/HASH được lưu thật để đọc lại bằng ``lrange`` / ``hmget`` (LIST mốc cũ đứng trước, cắt theo số mốc tối đa)."""

    def __init__(self, created: int = 1, *, emulate: bool = False) -> None:
        self.created = created
        self.emulate = emulate
        self.calls: list[tuple] = []
        self.seen: set[str] = set()
        self.lists: dict[str, list[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.trims: list[tuple[str, str]] = []  # (key LIST, mốc sớm nhất còn giữ) của mỗi lần bỏ mốc cũ
        self.pinged = 0
        self.closed = False

    def trim(self, args: tuple) -> int:
        """Giả lập script bỏ mốc cũ: lấy dần từ đầu LIST các mốc cũ hơn mốc cho trước, xoá cả HASH của chúng (không ghi vào ``calls``, nơi chỉ có các lệnh ghi List–Hash)."""
        list_key, cutoff = str(args[0]), str(args[1])
        self.trims.append((list_key, cutoff))
        stamps = self.lists.get(list_key, [])
        removed = 0
        while stamps and stamps[0] < cutoff:
            gone = stamps.pop(0)
            self.hashes.pop(f"{list_key}:{gone}", None)
            self.seen.discard(f"{list_key}:{gone}")
            removed += 1
        return removed

    def record(self, args: tuple) -> int:
        self.calls.append(args)
        if not self.emulate:
            return self.created
        key = str(args[0])
        if key in self.seen:
            return 0
        self.seen.add(key)
        values = [str(item) for item in args]
        self.hashes[key] = dict(zip(values[7::2], values[8::2], strict=True))
        stamps = self.lists.setdefault(values[1], [])
        stamps.append(values[2])
        while len(stamps) > int(values[4]):  # như script thật: mốc cũ nhất ra khỏi LIST thì HASH của nó cũng bị xoá
            gone = stamps.pop(0)
            self.hashes.pop(f"{values[1]}:{gone}", None)
            self.seen.discard(f"{values[1]}:{gone}")
        return 1

    def eval(self, script: str, numkeys: int, *args: object) -> int:
        return self.trim(args) if numkeys == 1 else self.record(args)

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self.lists.get(key, [])
        return list(items[start : (None if end == -1 else end + 1)])

    def hmget(self, key: str, *fields: str) -> list[str | None]:
        row = self.hashes.get(key)
        return [row.get(field) for field in fields] if row else [None] * len(fields)

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    def pipeline(self, **_kwargs: object) -> _EvalPipe:
        return _EvalPipe(self)

    def ping(self) -> bool:
        self.pinged += 1
        return True

    def close(self) -> None:
        self.closed = True


def decode_eval(args: tuple) -> dict:
    """Một lệnh EVAL đã ghi -> các phần của nó: key HASH, key LIST, mốc, hạn lưu, số mốc tối đa, kênh, message và các trường."""
    return {
        "hash_key": str(args[0]), "list_key": str(args[1]), "stamp": str(args[2]), "ttl": str(args[3]), "max": str(args[4]),
        "channel": str(args[5]), "message": str(args[6]), "fields": dict(zip(args[7::2], args[8::2], strict=True)),
    }


class FakeConfigRedis:
    """Redis giả chạy lại đúng ngữ nghĩa của script ghi config: HASH của phiên bản đã có thì không ghi (trả 0); ghi HASH rồi LIST; LIST dài quá thì bỏ phiên bản cũ nhất và xoá HASH của nó."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.evals = 0

    def lindex(self, key: str, index: int) -> str | None:
        items = self.lists.get(key, [])
        return items[index] if items else None

    def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    def eval(self, script: str, numkeys: int, *args: object) -> int:
        from des.src import redis_connector

        assert script == redis_connector._WRITE_CONFIG_SCRIPT and numkeys == 2
        hash_key, list_key, version, keep, text = (str(item) for item in args)
        self.evals += 1
        if hash_key in self.hashes:
            return 0
        self.hashes[hash_key] = {"config": text}
        stamps = self.lists.setdefault(list_key, [])
        stamps.append(version)
        while len(stamps) > int(keep):
            self.hashes.pop(f"{list_key}:{stamps.pop(0)}", None)
        return 1
