from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
import redis as redis_lib

from dp_program.engine import live
from dp_program.engine import websocket as ws


@pytest.fixture(autouse=True)
def _reset_live_module_state():
    """Live's Redis client/script/circuit are module globals; isolate tests."""
    live._client = None
    live._script = None
    live._last_refresh_slot = -1
    live._circuit_open_until = 0.0
    yield
    live._client = None
    live._script = None
    live._last_refresh_slot = -1
    live._circuit_open_until = 0.0


_TTL = 604800


def _config(*, bars: int = 3, window: int = 3, cooldown: int = 30) -> dict[str, Any]:
    return {
        "redis": {
            "enabled": True,
            "host": "localhost", "port": 6379, "db": 0,
            "username": "", "password": "",
            "bars_per_snapshot": window,
            "hash_ttl_seconds": _TTL,
            "circuit_cooldown_seconds": cooldown,
            "key_prefix": "T_CANDLE",
            "timeout_seconds": 0.3,
        },
        "live": {"bars_per_request": bars, "closed_candles_only": True, "enabled": True},
    }


_SYMBOL = {"symbol_id": 9, "exchange": "CAPITALCOM", "symbol": "GOLD"}
_TIMEFRAME = {"code": "M5", "minutes": 5}
_FIELDS = ("timestamp", "open", "high", "low", "close", "time_update")
_COMPARED = ("open", "high", "low", "close")


_BASE = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def _candle(minute_offset: int, *, close: str = "101") -> dict[str, Any]:
    return {
        "symbol_id": 9, "timestamp": _BASE + timedelta(minutes=minute_offset),
        "open": Decimal("100"), "high": Decimal("102"), "low": Decimal("99"), "close": Decimal(close),
    }


def _expect_stamp(candle: dict[str, Any]) -> str:
    """Mốc kỳ vọng, tự dựng lại độc lập với live._stamp()."""
    return candle["timestamp"].strftime("%Y-%m-%d %H:%M:%S")


def _desc(*minute_offsets: int) -> list[str]:
    """List Redis đúng contract mới: giảm dần, nến mới nhất ở index 0."""
    return sorted((_expect_stamp(_candle(m)) for m in minute_offsets), reverse=True)


class _FakePipeline:
    def __init__(self, client: "_SemanticRedis") -> None:
        self.client, self.calls = client, []

    def expire(self, key: str, seconds: int) -> None:
        self.calls.append((key, seconds))

    def execute(self) -> list[bool]:
        self.client.pipeline_executes += 1
        results = []
        for key, seconds in self.calls:
            alive = key in self.client.hashes
            if alive:
                self.client.ttls[key] = seconds
            results.append(alive)
        return results


class _SemanticRedis:
    """Redis thu nhỏ kiểm contract giữa Python và Lua script duy nhất còn lại.

    Cũng lộ ra `llen`/`lindex`/`lrange`/`pipeline` để live._needs_full_reload()
    và live._refresh_ttl_slice() đọc trực tiếp, không qua script. Emulator này
    KHÔNG chạy Lua thật -- script được kiểm riêng bằng trình thông dịch Lua.
    """

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}
        self.strings: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.registered: list[str] = []
        self.hash_writes = 0
        self.pipeline_executes = 0

    def llen(self, list_key: str) -> int:
        return len(self.lists.get(list_key, []))

    def lindex(self, list_key: str, index: int) -> str | None:
        order = self.lists.get(list_key, [])
        try:
            return order[index]
        except IndexError:
            return None

    def lrange(self, list_key: str, start: int, end: int) -> list[str]:
        order = self.lists.get(list_key, [])
        return list(order[start:] if end == -1 else order[start:end + 1])

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        return _FakePipeline(self)

    def get(self, key: str) -> str | None:
        return self.strings.get(key)

    def set(self, key: str, value: str) -> None:
        self.strings[key] = value

    def register_script(self, script: str) -> Callable[..., list[int]]:
        self.registered.append(script)

        def execute(*, keys: list[str], args: list[Any]) -> list[int]:
            return self._incremental([str(v) for v in keys], [str(v) for v in args])

        return execute

    def _write(self, key: str, values: list[str]) -> None:
        candle = self.hashes.setdefault(key, {})
        candle.update(zip(_FIELDS, values, strict=True))
        self.hash_writes += 1

    def _differs(self, key: str, values: list[str]) -> bool:
        candle = self.hashes.get(key, {})
        return tuple(candle.get(field) for field in _COMPARED) != tuple(values)

    def _incremental(self, keys: list[str], args: list[str]) -> list[int]:
        (list_key,) = keys
        max_size = int(args[0])
        candle_prefix = args[1]
        ttl = int(args[2])
        order = self.lists.setdefault(list_key, [])
        changed = 0
        added = 0
        for index in range(3, len(args), 6):
            stamp = args[index]
            prices = args[index + 1:index + 5]
            updated = args[index + 5]
            key = candle_prefix + stamp
            if self._differs(key, prices):
                self._write(key, [stamp, *prices, updated])
                self.ttls[key] = ttl
                changed += 1
            if stamp not in order:
                if not order or stamp > order[0]:
                    order.insert(0, stamp)
                else:
                    order.append(stamp)
                    order.sort(reverse=True)
                added += 1
        evicted = max(0, len(order) - max_size)
        for stamp in order[len(order) - evicted:]:
            self.hashes.pop(candle_prefix + stamp, None)
            self.ttls.pop(candle_prefix + stamp, None)
        if evicted:
            del order[len(order) - evicted:]
        return [changed, added, evicted]


def _keys() -> tuple[str, str]:
    return "T_CANDLE_GOLD_M5", "T_CANDLE_GOLD_M5:"


def _with_client(monkeypatch: pytest.MonkeyPatch) -> _SemanticRedis:
    client = _SemanticRedis()
    monkeypatch.setattr(live, "_get_client", lambda _settings: client)
    return client


def test_key_layout_is_one_concatenation_and_list_key_never_holds_a_colon() -> None:
    list_key, candle_prefix = live._keys(_config()["redis"], "M5", "GOLD")
    stamp = _expect_stamp(_candle(0))
    candle_key = candle_prefix + stamp

    assert (list_key, candle_prefix) == _keys()
    assert candle_key == f"{list_key}:{stamp}"
    assert ":" not in list_key
    assert stamp.count(":") == 2 and candle_key.count(":") == 3


def test_push_writes_all_six_hash_fields_and_list_member_equals_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _with_client(monkeypatch)
    candle = _candle(0)

    live._push_to_redis(_config(), _SYMBOL, _TIMEFRAME, [candle])

    list_key, candle_prefix = _keys()
    member = client.lists[list_key][0]
    written = client.hashes[candle_prefix + member]
    assert member == _expect_stamp(candle)
    assert written["timestamp"] == member
    assert set(written) == set(_FIELDS)
    assert written["open"] == "100"  # bỏ số 0 thừa, khớp OG18
    assert client.ttls[candle_prefix + member] == _TTL


def test_push_rounds_prices_to_two_decimals_half_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chốt 2026-09-24: Redis chỉ cần 2 chữ số thập phân, không còn giữ scale
    DECIMAL(18,8) của warehouse (đó là việc riêng của SQL/backfill).
    ROUND_HALF_UP tại đúng biên .005 để không mơ hồ làm tròn lên hay xuống."""
    client = _with_client(monkeypatch)
    candle = {
        "timestamp": datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc),
        "open": Decimal("9999999999.12345678"), "high": Decimal("100.005"),
        "low": Decimal("100.004"), "close": Decimal("9999999999.12345679"),
    }

    live._push_to_redis(_config(), _SYMBOL, _TIMEFRAME, [candle])

    _, candle_prefix = _keys()
    stamp = _expect_stamp(candle)
    written = client.hashes[candle_prefix + stamp]
    assert written["open"] == "9999999999.12"
    assert written["high"] == "100.01"  # .005 làm tròn LÊN
    assert written["low"] == "100"      # .004 làm tròn XUỐNG rồi bỏ số 0 thừa
    assert written["close"] == "9999999999.12"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("100", "100"), ("100.00", "100"), ("7649.90", "7649.9"), ("25653.00", "25653"),
        ("52020", "52020"), ("52020.00", "52020"), ("84035.35", "84035.35"),
        ("100.005", "100.01"), ("100.004", "100"), ("0.10", "0.1"), ("10", "10"),
        ("9999999999.12345678", "9999999999.12"),
    ],
)
def test_round_price_strips_trailing_zeros_without_exponent_or_integer_damage(
    raw: str, expected: str,
) -> None:
    """Số 0 ở phần nguyên (52020) tuyệt đối không được bị cắt, và không bao
    giờ ra dạng mũ ("5.202E+4") như Decimal.normalize()."""
    result = live._round_price(Decimal(raw))

    assert result == expected
    assert "E" not in result.upper()


def test_push_skips_hash_write_for_sub_cent_price_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lợi ích phụ của làm tròn 2 số: dao động giá dưới 1 cent (cùng làm
    tròn ra một giá trị) không còn khiến Lua HSET/PUBLISH nữa -- giảm bớt
    keyspace notification vô ích phía OG cho những đổi giá không đáng kể."""
    client = _with_client(monkeypatch)
    config = _config()
    original = _candle(0, close="101.251")
    sub_cent_noise = _candle(0, close="101.254")  # cùng làm tròn ra 101.25

    live._push_to_redis(config, _SYMBOL, _TIMEFRAME, [original])
    writes_after_first = client.hash_writes
    live._push_to_redis(config, _SYMBOL, _TIMEFRAME, [sub_cent_noise])

    assert client.hash_writes == writes_after_first  # không HSET thêm lần nào
    _, candle_prefix = _keys()
    stamp = _expect_stamp(original)
    assert client.hashes[candle_prefix + stamp]["close"] == "101.25"


def test_push_is_idempotent_and_revision_updates_without_duplicating_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _with_client(monkeypatch)
    config = _config()
    original, revision = _candle(0), _candle(0, close="101.25")

    live._push_to_redis(config, _SYMBOL, _TIMEFRAME, [original])
    live._push_to_redis(config, _SYMBOL, _TIMEFRAME, [original])
    live._push_to_redis(config, _SYMBOL, _TIMEFRAME, [revision])

    list_key, candle_prefix = _keys()
    stamp = _expect_stamp(original)
    assert client.lists[list_key] == [stamp]
    assert client.hashes[candle_prefix + stamp]["close"] == "101.25"


def test_push_sorts_late_arrival_and_trims_oldest_without_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _with_client(monkeypatch)
    config = _config(window=3)
    candles = [_candle(0), _candle(10), _candle(5), _candle(15)]

    for candle in candles:
        live._push_to_redis(config, _SYMBOL, _TIMEFRAME, [candle])

    list_key, candle_prefix = _keys()
    expected = sorted((_expect_stamp(c) for c in candles[1:]), reverse=True)
    assert client.lists[list_key] == expected  # giảm dần: mới nhất ở index 0
    assert set(client.hashes) == {candle_prefix + stamp for stamp in expected}
    oldest = _expect_stamp(candles[0])
    assert candle_prefix + oldest not in client.hashes
    assert candle_prefix + oldest not in client.ttls


def test_push_keeps_list_newest_first_and_evicts_from_the_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nến nạp theo thứ tự tăng dần (như full reload) phải cho ra List giảm
    dần: index 0 là nến mới nhất; nến bị evict là nến CŨ NHẤT ở đuôi."""
    client = _with_client(monkeypatch)
    candles = [_candle(m) for m in (0, 5, 10, 15, 20)]

    live._push_to_redis(_config(window=3), _SYMBOL, _TIMEFRAME, candles)

    list_key, _ = _keys()
    assert client.lists[list_key] == _desc(20, 15, 10)
    assert client.lists[list_key][0] == _expect_stamp(candles[-1])


def test_push_sets_ttl_only_when_hash_is_written(monkeypatch: pytest.MonkeyPatch) -> None:
    """TTL đặt ngay lúc HSET; nến không đổi thì Hash không bị đụng (việc làm
    mới TTL là của _refresh_ttl_slice, không phải của push)."""
    client = _with_client(monkeypatch)
    config = _config()
    _, candle_prefix = _keys()
    stamp = _expect_stamp(_candle(0))

    live._push_to_redis(config, _SYMBOL, _TIMEFRAME, [_candle(0)])
    client.ttls[candle_prefix + stamp] = 1  # giả lập TTL đã cạn dần
    live._push_to_redis(config, _SYMBOL, _TIMEFRAME, [_candle(0)])  # không đổi giá
    assert client.ttls[candle_prefix + stamp] == 1

    live._push_to_redis(config, _SYMBOL, _TIMEFRAME, [_candle(0, close="105")])  # đổi giá
    assert client.ttls[candle_prefix + stamp] == _TTL


def test_incremental_script_pushes_to_head_and_evicts_from_tail() -> None:
    """Emulator không chạy Lua thật; chốt chặn văn bản này giữ đúng hướng
    (LPUSH đầu List, RPOP đuôi List) và luôn đặt TTL cho Hash vừa ghi."""
    script = live._INCREMENTAL_SCRIPT
    assert "redis.call('LPUSH', list_key, stamp)" in script
    assert "redis.call('RPOP', list_key, evicted_count)" in script
    assert "redis.call('EXPIRE', key, ttl)" in script
    assert "RPUSH', list_key, stamp" not in script and "'LPOP'" not in script


def test_script_registered_once_across_multiple_pushes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _with_client(monkeypatch)

    live._push_to_redis(_config(), _SYMBOL, _TIMEFRAME, [_candle(0)])
    live._push_to_redis(_config(), _SYMBOL, _TIMEFRAME, [_candle(5)])

    assert len(client.registered) == 1


def test_needs_full_reload_when_list_empty_or_shorter_than_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _with_client(monkeypatch)
    settings = _config(window=5)["redis"]
    list_key, _ = _keys()

    assert live._needs_full_reload(settings, list_key, _candle(0)["timestamp"], 5) is True

    client.lists[list_key] = _desc(0, 5, 10)
    assert live._needs_full_reload(settings, list_key, _candle(15)["timestamp"], 5) is True


def test_needs_full_reload_stays_false_once_real_history_ceiling_is_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pair có lịch sử TradingView thật ngắn hơn window (vd BTCUSD/W chỉ có
    490/500) không được ép full reload mỗi cycle mãi mãi một khi đã xác
    nhận đây là trần thật -- chỉ còn quan tâm đuôi có nối liền hay không,
    y như mọi pair đủ window khác."""
    client = _with_client(monkeypatch)
    settings = _config(window=5)["redis"]
    list_key, _ = _keys()
    client.lists[list_key] = _desc(0, 5, 10)  # 3 < window=5
    client.strings[live._ceiling_key(list_key)] = "5"  # trần đã xác nhận đúng window hiện tại

    assert live._needs_full_reload(settings, list_key, _candle(15)["timestamp"], 5) is False


def test_needs_full_reload_ignores_a_stale_ceiling_marker_from_a_different_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator tăng bars_per_snapshot trong config.yaml -> marker trần cũ
    (ứng với window nhỏ hơn) phải tự hết hiệu lực, quay lại full reload để
    dò xem TradingView có đủ nến cho window mới hay không."""
    client = _with_client(monkeypatch)
    settings = _config(window=5)["redis"]
    list_key, _ = _keys()
    client.lists[list_key] = _desc(0, 5, 10)
    client.strings[live._ceiling_key(list_key)] = "3"  # trần cũ, ứng với window=3 đã đổi

    assert live._needs_full_reload(settings, list_key, _candle(15)["timestamp"], 5) is True


def test_needs_full_reload_false_when_tail_is_contiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _with_client(monkeypatch)
    settings = _config(window=3)["redis"]
    list_key, _ = _keys()
    client.lists[list_key] = _desc(0, 5, 10)

    assert live._needs_full_reload(settings, list_key, _candle(15)["timestamp"], 5) is False


def test_needs_full_reload_true_when_tail_has_a_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _with_client(monkeypatch)
    settings = _config(window=3)["redis"]
    list_key, _ = _keys()
    client.lists[list_key] = _desc(0, 5, 10)

    assert live._needs_full_reload(settings, list_key, _candle(25)["timestamp"], 5) is True


def _pair(symbol_id: int, name: str, code: str = "M5", minutes: int = 5) -> tuple[dict, dict]:
    return (
        {"symbol_id": symbol_id, "exchange": "CAPITALCOM", "symbol": name},
        {"code": code, "minutes": minutes},
    )


def test_run_live_pairs_pushes_tail_without_reload_when_no_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _with_client(monkeypatch)
    config = _config(bars=1, window=3)
    pair = _pair(9, "GOLD")
    list_key, _ = _keys()
    client.lists[list_key] = _desc(0, 5, 10)
    new_candle = _candle(15)
    calls: list[list[int]] = []

    def fake_fetch(_config, requests):
        calls.append([r.bars for r in requests])
        return {ws.request_key(r): ws.FetchResult([new_candle], r.bars, 0) for r in requests}

    monkeypatch.setattr(live, "fetch_candles_batch", fake_fetch)

    summary = live.run_live_pairs(config, [pair])

    assert calls == [[1]]
    assert summary["ok"] == 1
    assert summary["reloaded"] == 0
    assert client.lists[list_key][0] == _expect_stamp(new_candle)  # nến mới nhất ở index 0


def test_run_live_pairs_reloads_full_window_when_list_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _with_client(monkeypatch)
    config = _config(bars=1, window=3)
    pair = _pair(9, "GOLD")
    full_window = [_candle(0), _candle(5), _candle(10)]
    calls: list[list[int]] = []

    def fake_fetch(_config, requests):
        calls.append(sorted(r.bars for r in requests))
        results = {}
        for request in requests:
            candles = full_window if request.bars == 3 else [_candle(10)]
            results[ws.request_key(request)] = ws.FetchResult(candles, request.bars, 0)
        return results

    monkeypatch.setattr(live, "fetch_candles_batch", fake_fetch)

    summary = live.run_live_pairs(config, [pair])

    assert calls == [[1], [3]]
    assert summary["ok"] == 1
    assert summary["reloaded"] == 1
    list_key, _ = _keys()
    assert client.lists[list_key] == _desc(0, 5, 10)


def test_run_live_pairs_records_ceiling_when_reload_returns_fewer_than_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TradingView trả đủ 1 lần fetch bars=window mà vẫn ít hơn window (như
    BTCUSD/W chỉ có 490/500 nến thật) -- phải ghi nhận trần, không phải bỏ
    qua như thiếu tạm thời."""
    client = _with_client(monkeypatch)
    config = _config(bars=1, window=5)
    pair = _pair(9, "GOLD")
    short_history = [_candle(0), _candle(5), _candle(10)]  # chỉ 3 < window=5

    def fake_fetch(_config, requests):
        results = {}
        for request in requests:
            candles = short_history if request.bars == 5 else [_candle(10)]
            results[ws.request_key(request)] = ws.FetchResult(candles, request.bars, 0)
        return results

    monkeypatch.setattr(live, "fetch_candles_batch", fake_fetch)

    live.run_live_pairs(config, [pair])

    list_key, _ = _keys()
    assert client.strings[live._ceiling_key(list_key)] == "5"


def test_run_live_pairs_skips_forced_reload_once_ceiling_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cycle sau, cùng pair ngắn lịch sử đó với đuôi vẫn nối liền: không
    được fetch lại nguyên window (500/5) nữa -- chỉ đuôi nhỏ, y như pair
    đã đủ window."""
    client = _with_client(monkeypatch)
    config = _config(bars=1, window=5)
    pair = _pair(9, "GOLD")
    list_key, _ = _keys()
    client.lists[list_key] = _desc(0, 5, 10)  # 3 < window=5
    client.strings[live._ceiling_key(list_key)] = "5"  # trần đã xác nhận ở cycle trước
    new_candle = _candle(15)
    calls: list[list[int]] = []

    def fake_fetch(_config, requests):
        calls.append([r.bars for r in requests])
        return {ws.request_key(r): ws.FetchResult([new_candle], r.bars, 0) for r in requests}

    monkeypatch.setattr(live, "fetch_candles_batch", fake_fetch)

    summary = live.run_live_pairs(config, [pair])

    assert calls == [[1]]  # chỉ đuôi nhỏ, không có lượt fetch window=5 nào
    assert summary["ok"] == 1
    assert summary["reloaded"] == 0
    assert client.lists[list_key][0] == _expect_stamp(new_candle)  # nến mới nhất ở index 0


def test_run_live_pairs_batches_fetch_by_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _with_client(monkeypatch)
    config = _config(bars=1, window=1)
    symbol = {"symbol_id": 9, "exchange": "CAPITALCOM", "symbol": "GOLD"}
    m5, m10 = {"code": "M5", "minutes": 5}, {"code": "M10", "minutes": 10}
    pairs = [(symbol, m5), (symbol, m10)]
    # Pre-seed both lists as already caught up so the tail push alone is
    # enough -- no reload batch -- isolating the by-symbol grouping itself.
    client.lists["T_CANDLE_GOLD_M5"] = [_expect_stamp(_candle(-5))]
    client.lists["T_CANDLE_GOLD_M10"] = [_expect_stamp(_candle(-10))]
    batches: list[list[str]] = []

    def fake_fetch(_config, requests):
        batches.append([r.timeframe["code"] for r in requests])
        return {ws.request_key(r): ws.FetchResult([_candle(0)], r.bars, 0) for r in requests}

    monkeypatch.setattr(live, "fetch_candles_batch", fake_fetch)

    live.run_live_pairs(config, pairs)

    assert batches == [["M5", "M10"]]


def test_run_live_pairs_one_group_failure_does_not_block_next_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_client(monkeypatch)
    config = _config(bars=1, window=1)
    pairs = [_pair(9, "GOLD"), _pair(81, "BTCUSD")]

    def fake_fetch(_config, requests):
        if requests[0].symbol["symbol"] == "GOLD":
            raise ws.IncompleteFetchError("boom")
        return {ws.request_key(r): ws.FetchResult([_candle(0)], r.bars, 0) for r in requests}

    monkeypatch.setattr(live, "fetch_candles_batch", fake_fetch)

    summary = live.run_live_pairs(config, pairs)

    assert summary["ok"] == 1
    assert summary["failed"] == 1
    assert summary["failed_pairs"] == ["CAPITALCOM:GOLD/M5"]


def test_run_live_pairs_opens_circuit_after_two_consecutive_group_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_client(monkeypatch)
    config = _config(bars=1, window=1)
    pairs = [_pair(2, "FR40"), _pair(3, "DE40"), _pair(4, "HK50")]
    visited: list[str] = []

    def fake_fetch(_config, requests):
        visited.append(requests[0].symbol["symbol"])
        raise ws.IncompleteFetchError("boom")

    monkeypatch.setattr(live, "fetch_candles_batch", fake_fetch)

    summary = live.run_live_pairs(config, pairs)

    assert visited == ["FR40", "DE40"]
    assert summary["failed"] == 2
    assert summary["group_failures"] == 2


def test_run_live_pairs_redis_failure_opens_circuit_and_stops_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(bars=1, window=1, cooldown=30)
    pairs = [_pair(9, "GOLD"), _pair(81, "BTCUSD")]

    def fake_fetch(_config, requests):
        return {ws.request_key(r): ws.FetchResult([_candle(0)], r.bars, 0) for r in requests}

    monkeypatch.setattr(live, "fetch_candles_batch", fake_fetch)

    def broken_client(_settings):
        raise redis_lib.ConnectionError("down")

    monkeypatch.setattr(live, "_get_client", broken_client)

    summary = live.run_live_pairs(config, pairs)

    assert summary["redis_circuit_open"] is True
    assert summary["failed"] == 2
    assert summary["ok"] == 0

    calls: list[int] = []
    monkeypatch.setattr(live, "fetch_candles_batch", lambda *_a: calls.append(1) or {})
    summary2 = live.run_live_pairs(config, pairs)
    assert calls == []
    assert summary2["redis_circuit_open"] is True
    assert summary2["failed"] == 2


def test_run_live_pairs_requires_redis_enabled() -> None:
    config = _config()
    config["redis"]["enabled"] = False
    with pytest.raises(RuntimeError, match="redis.enabled"):
        live.run_live_pairs(config, [_pair(9, "GOLD")])


def test_run_live_pairs_stop_request_defers_remaining_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_client(monkeypatch)
    config = _config()
    calls: list[int] = []
    monkeypatch.setattr(live, "fetch_candles_batch", lambda *_a: calls.append(1) or {})

    summary = live.run_live_pairs(config, [_pair(9, "GOLD")], stop_requested=lambda: True)

    assert calls == []
    assert summary["ok"] == 0
    assert summary["failed"] == 0


def _seed_full_list(client: _SemanticRedis, symbol: str, minutes: tuple[int, ...] = (0, 5, 10)) -> str:
    """Dựng sẵn 1 List đủ Hash (TTL đã cạn về 1) cho symbol M5, trả về key List."""
    list_key = f"T_CANDLE_{symbol}_M5"
    client.lists[list_key] = _desc(*minutes)
    for stamp in client.lists[list_key]:
        client.hashes[f"{list_key}:{stamp}"] = {"timestamp": stamp}
        client.ttls[f"{list_key}:{stamp}"] = 1
    return list_key


def _at_slot(monkeypatch: pytest.MonkeyPatch, slot: int) -> None:
    monkeypatch.setattr(live.time, "time", lambda: slot * live._REFRESH_SLOT_SECONDS + 7.0)


def test_refresh_ttl_slice_rotates_one_list_per_slot_in_stable_name_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mỗi slot làm mới đúng MỘT List theo thứ tự tên cố định (slot % số List),
    nên không phụ thuộc restart và không làm mới cả 165 List một lượt."""
    client = _with_client(monkeypatch)
    config = _config()
    names = ["BTCUSD", "GOLD", "US500"]
    keys = [_seed_full_list(client, n) for n in names]
    pairs = [_pair(i, n) for i, n in enumerate(reversed(names))]  # cố tình đảo thứ tự vào

    for slot in (30, 31, 32, 33):
        _at_slot(monkeypatch, slot)
        live._refresh_ttl_slice(config, pairs)
        target = sorted(keys)[slot % 3]
        assert all(client.ttls[f"{target}:{s}"] == _TTL for s in client.lists[target])
        untouched = [k for k in keys if k != target and slot < 33]
        assert all(client.ttls[f"{k}:{s}"] in (1, _TTL) for k in untouched for s in client.lists[k])

    assert client.pipeline_executes == 4  # đúng một lần mỗi slot


def test_refresh_ttl_slice_runs_once_per_slot_and_only_touches_that_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _with_client(monkeypatch)
    config = _config()
    first, second = _seed_full_list(client, "BTCUSD"), _seed_full_list(client, "GOLD")
    pairs = [_pair(1, "BTCUSD"), _pair(2, "GOLD")]
    _at_slot(monkeypatch, 40)  # 40 % 2 == 0 -> BTCUSD (thứ tự tên đầu tiên)

    live._refresh_ttl_slice(config, pairs)
    live._refresh_ttl_slice(config, pairs)  # cùng slot: không làm gì thêm

    assert client.pipeline_executes == 1
    assert all(client.ttls[f"{first}:{s}"] == _TTL for s in client.lists[first])
    assert all(client.ttls[f"{second}:{s}"] == 1 for s in client.lists[second])  # chưa tới lượt


def test_refresh_ttl_slice_reports_dangling_stamp_whose_hash_is_gone(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    client = _with_client(monkeypatch)
    list_key = _seed_full_list(client, "GOLD")
    del client.hashes[f"{list_key}:{client.lists[list_key][-1]}"]  # List trỏ tới Hash đã mất
    _at_slot(monkeypatch, 50)

    with caplog.at_level("INFO"):
        live._refresh_ttl_slice(_config(), [_pair(1, "GOLD")])

    assert "REDIS_TTL_REFRESHED" in caplog.text
    assert "risk=MEDIUM" in caplog.text and "dangling=1" in caplog.text


def test_run_live_pairs_refreshes_ttl_slice_after_the_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _with_client(monkeypatch)
    config = _config(bars=1, window=3)
    list_key = _seed_full_list(client, "GOLD")
    new_candle = _candle(15)
    monkeypatch.setattr(
        live, "fetch_candles_batch",
        lambda _c, requests: {ws.request_key(r): ws.FetchResult([new_candle], r.bars, 0) for r in requests},
    )
    _at_slot(monkeypatch, 60)

    summary = live.run_live_pairs(config, [_pair(9, "GOLD")])

    assert summary["ok"] == 1 and client.pipeline_executes == 1
    assert client.lists[list_key][0] == _expect_stamp(new_candle)
    survivors = [s for s in client.lists[list_key] if f"{list_key}:{s}" in client.hashes]
    assert survivors and all(client.ttls[f"{list_key}:{s}"] == _TTL for s in survivors)


def test_run_live_pairs_ttl_refresh_redis_error_opens_circuit_but_keeps_cycle_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RefreshDown(_SemanticRedis):
        def lrange(self, list_key: str, start: int, end: int) -> list[str]:
            raise redis_lib.ConnectionError("down during refresh")

    client = _RefreshDown()
    monkeypatch.setattr(live, "_get_client", lambda _settings: client)
    config = _config(bars=1, window=3)
    client.lists[_keys()[0]] = _desc(0, 5, 10)
    monkeypatch.setattr(
        live, "fetch_candles_batch",
        lambda _c, requests: {ws.request_key(r): ws.FetchResult([_candle(15)], r.bars, 0) for r in requests},
    )

    summary = live.run_live_pairs(config, [_pair(9, "GOLD")])

    assert summary["ok"] == 1  # nến đã đẩy xong trước khi làm mới TTL lỗi
    assert summary["redis_circuit_open"] is True
