"""Redis db0 GIẢ (Core) cho test data_engine / configuration: hermetic, KHÔNG nối Redis thật.

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
# Cấu hình lane thử (đúng hình dạng de_config.yaml, KHÔNG có nguồn trend: bản này chỉ chạy raw).
# =============================================================================

GROUP_SYMBOLS: tuple[str, ...] = ("US30", "US500", "US100", "GER40", "HK50", "JP225", "XAUUSD", "BTCUSD")
GROUP_TIMEFRAMES: tuple[str, ...] = ("M10", "M20", "M30", "M45", "H1", "H2", "H3", "H4")
VALIDITY = {
    "combo": {"mode": "next_bar", "valid_bars": 3},
    "ma_cross": {"mode": "seconds_after_bar_close", "seconds": 600},
    "macd_break": {"mode": "seconds_after_bar_close", "seconds": 600},
    "ichimoku": {"mode": "seconds_after_bar_close", "seconds": 600},
    "ema_reach": {"mode": "seconds_after_bar_close", "seconds": 600},
}


def lane_config(enabled: tuple[str, ...] = ("combo", "ma_cross")) -> dict:
    """Một dict cấu hình đủ cho ``configuration.build_lane_job`` (các giá trị khớp config mẫu: db0 -> db1, raw giữ tên L_DE / L_CHANNEL)."""
    return {
        "redis": {
            "enabled": True,
            "host": "127.0.0.1",
            "password": "",
            "input": {
                "db": 0, "key_prefix": PREFIX, "snapshot_bars": 500, "process_on_startup": True,
                "symbols": list(GROUP_SYMBOLS), "timeframes": list(GROUP_TIMEFRAMES),
            },
            "output": {"db": 0, "port": 6380, "password": "", "key_prefix": "L_DE", "channel_prefix": "L_CHANNEL"},
            "trend_output": {"db": 1, "key_prefix": "L_TREND"},
            "filtered_output": {"db": 2},
        },
        "live": {"enabled_strategies": list(enabled), "signal_validity": {k: dict(v) for k, v in VALIDITY.items()}, "log_level": "INFO"},
    }


TREND_SOURCES: tuple[str, ...] = ("knn", "breakout", "zigzag", "tstat", "cloud")
ALL_STRATEGIES: tuple[str, ...] = ("combo", "ma_cross", "macd_break", "ichimoku", "ema_reach")


def trend_config(enabled: tuple[str, ...] = ALL_STRATEGIES, sources: tuple[str, ...] = TREND_SOURCES) -> dict:
    """``lane_config`` cộng ``redis.trend_outputs`` cho các nguồn trend được chọn (mỗi nguồn một tiền tố khoá và kênh riêng, như de_config.example.yaml)."""
    config = lane_config(enabled)
    config["redis"]["trend_outputs"] = {name: {"key_prefix": f"L_DE_{name.upper()}", "channel_prefix": f"L_CHANNEL_{name.upper()}"} for name in sources}
    return config


# Nguồn nến thêm (redis.input_sources.og8): Redis khác máy, nhóm symbol riêng (symbol phải có trong bảng khung trend của config thật, vì bảng được nạp lúc import).
OG8_SOURCE: dict = {"host": "10.11.12.8", "port": 6380, "password": "mat-khau-og8", "db": 0, "key_prefix": "L_CANDLE", "symbols": ["EURUSD", "fr40"]}


def source_config(enabled: tuple[str, ...] = ALL_STRATEGIES, sources: tuple[str, ...] = TREND_SOURCES) -> dict:
    """``trend_config`` cộng nguồn nến thêm ``og8``: nhóm mặc định chạy ``GROUP_SYMBOLS``, nhóm og8 chạy EURUSD và FR40 từ Redis khác máy."""
    config = trend_config(enabled, sources)
    config["redis"]["input_sources"] = {"og8": dict(OG8_SOURCE)}
    return config


class FakeSnapshots:
    """Engine giả cho ``process_pair`` / ``process_trend_pair``: ``snapshot(pair)`` trả bảng nến đã nạp cho cặp (None nếu chưa có), ghi lại các cặp đã hỏi."""

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

    def eval(self, script: str, _numkeys: int, *args: object) -> _EvalPipe:
        self.queued.append(("eval", args))
        return self

    def hmget(self, key: str, *fields: str) -> _EvalPipe:
        self.queued.append(("hmget", key, fields))
        return self

    def execute(self) -> list:
        results = [self.owner.record(item[1]) if item[0] == "eval" else self.owner.hmget(item[1], *item[2]) for item in self.queued]
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
        self.pinged = 0
        self.closed = False

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
        del stamps[: -int(values[4])]
        return 1

    def eval(self, script: str, _numkeys: int, *args: object) -> int:
        return self.record(args)

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self.lists.get(key, [])
        return list(items[start : (None if end == -1 else end + 1)])

    def hmget(self, key: str, *fields: str) -> list[str | None]:
        row = self.hashes.get(key)
        return [row.get(field) for field in fields] if row else [None] * len(fields)

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
