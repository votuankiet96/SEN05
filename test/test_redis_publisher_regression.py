from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest


def _config(*, bars: int = 3) -> dict[str, Any]:
    return {
        "redis": {
            "enabled": True,
            "bars_per_snapshot": bars,
            "circuit_cooldown_seconds": 30,
            "key_prefix": "T_CANDLE",
            "event_channel": "test:dp:events:candles",
        }
    }


def _candle(
    minute: int,
    *,
    open_: str = "100",
    high: str = "102",
    low: str = "99",
    close: str = "101",
    volume: str | None = "12.5",
) -> dict[str, Any]:
    return {
        "timestamp": datetime(2026, 9, 8, 12, minute, tzinfo=timezone.utc),
        "open": Decimal(open_),
        "high": Decimal(high),
        "low": Decimal(low),
        "close": Decimal(close),
        "volume": None if volume is None else Decimal(volume),
    }


_FIELDS = ("open", "high", "low", "close", "volume")


def _expect_stamp(candle: dict[str, Any]) -> str:
    """Moc ky vong, tu dung lai doc lap voi redis_publisher._stamp().

    Neu goi thang ham cua production thi mot loi trong ham do se tu xac
    nhan la dung -- test phai tu biet ky vong dung la gi.
    """
    return candle["timestamp"].strftime("%Y-%m-%d_%H:%M:%S")


class _SemanticRedis:
    """Redis thu nhỏ để kiểm tra contract giữa Python và hai Lua script.

    Mỗi nến là một Hash riêng tại ``candle_prefix + stamp``; List chỉ giữ
    mốc thời gian và là chỉ mục duy nhất để tìm nến cần xoá.
    """

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}
        self.strings: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []
        self.registered: list[str] = []
        self.script_calls: list[tuple[str, list[str], list[str]]] = []
        self.hash_writes = 0
        self.list_rebuilds = 0

    def register_script(self, script: str) -> Callable[..., list[int]]:
        self.registered.append(script)
        kind = "incremental" if "PUBLISH" in script else "reconcile"

        def execute(*, keys: list[str], args: list[Any], client: Any = None) -> list[int]:
            del client
            text_args = [str(value) for value in args]
            self.script_calls.append((kind, list(keys), text_args))
            if kind == "incremental":
                return self._incremental(list(keys), text_args)
            return self._reconcile(list(keys), text_args)

        return execute

    def _write(self, key: str, values: list[str]) -> None:
        candle = self.hashes.setdefault(key, {})
        candle.update(zip(_FIELDS, values, strict=True))
        self.hash_writes += 1

    def _differs(self, key: str, values: list[str]) -> bool:
        candle = self.hashes.get(key, {})
        return tuple(candle.get(field) for field in _FIELDS) != tuple(values)

    def _incremental(self, keys: list[str], args: list[str]) -> list[int]:
        list_key, channel = keys
        max_size = int(args[0])
        candle_prefix, event_prefix = args[1], args[2]
        order = self.lists.setdefault(list_key, [])
        changed_events: list[str] = []
        added = 0
        for index in range(3, len(args), 7):
            stamp, *values, event = args[index:index + 7]
            key = candle_prefix + stamp
            if self._differs(key, values):
                self._write(key, values)
                changed_events.append(event)
            if stamp not in order:
                order.append(stamp)
                order.sort()  # moc rong co dinh -> sap chuoi la dung thu tu
                added += 1
        evicted = max(0, len(order) - max_size)
        for stamp in order[:evicted]:
            self.hashes.pop(candle_prefix + stamp, None)
        if evicted:
            del order[:evicted]
        if changed_events:
            self.published.append(
                (channel, event_prefix + ",".join(changed_events) + "]}")
            )
        return [len(changed_events), added, evicted]

    def _reconcile(self, keys: list[str], args: list[str]) -> list[int]:
        list_key = keys[0]
        candle_prefix = args[0]
        wanted_order: list[str] = []
        changed = 0
        for index in range(1, len(args), 6):
            stamp, *values = args[index:index + 6]
            wanted_order.append(stamp)
            key = candle_prefix + stamp
            if self._differs(key, values):
                self._write(key, values)
                changed += 1
        current = self.lists.get(list_key, [])
        desired = set(wanted_order)
        removed = 0
        for stamp in current:
            if stamp not in desired:
                self.hashes.pop(candle_prefix + stamp, None)
                removed += 1
        rebuilt = int(current != wanted_order)
        if rebuilt:
            self.lists[list_key] = list(wanted_order)
            self.list_rebuilds += 1
        return [changed, removed, rebuilt]


def _publisher_with_client(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, _SemanticRedis]:
    from dp_program.util import redis_publisher

    client = _SemanticRedis()
    publisher = redis_publisher._RedisPublisher()
    monkeypatch.setattr(publisher, "_get_client", lambda _settings: client)
    return publisher, client


def _keys() -> tuple[str, str]:
    """(list_key, candle_prefix) -- key nen la candle_prefix + stamp."""
    return "T_CANDLE_GOLD_M5", "T_CANDLE_GOLD_M5:"


def test_redis_payload_preserves_warehouse_decimal_contract() -> None:
    from dp_program.util import redis_publisher

    candle = _candle(
        0,
        open_="9999999999.12345678",
        high="9999999999.22345678",
        low="9999999999.02345678",
        close="9999999999.12345679",
        volume="1234567890123456.1234",
    )
    args = redis_publisher._pipeline_candles_to_args([candle])

    assert len(args) == 7
    assert args[1:6] == [
        "9999999999.12345678",
        "9999999999.22345678",
        "9999999999.02345678",
        "9999999999.12345679",
        "1234567890123456.1234",
    ]
    assert args[0] == "2026-09-08_12:00:00"  # moc lam chi muc + duoi key Hash
    event = json.loads(args[6], parse_float=Decimal)
    assert event == {
        "bartime": "2026-09-08_12:00:00",
        "open": Decimal("9999999999.12345678"),
        "high": Decimal("9999999999.22345678"),
        "low": Decimal("9999999999.02345678"),
        "close": Decimal("9999999999.12345679"),
        "volume": Decimal("1234567890123456.1234"),
    }


def test_redis_payload_uses_fixed_warehouse_scales_and_null_volume() -> None:
    from dp_program.util import redis_publisher

    args = redis_publisher._pipeline_candles_to_args([_candle(0, volume=None)])

    assert args[1:6] == [
        "100.00000000", "102.00000000", "99.00000000", "101.00000000", "null",
    ]
    assert json.loads(args[6], parse_float=Decimal)["volume"] is None


def test_redis_payload_reuses_the_exact_signature_committed_to_sql() -> None:
    from dp_program.util import redis_publisher

    candle = _candle(
        0,
        open_="100.123456785",
        high="102.123456785",
        low="99.123456785",
        close="101.123456785",
        volume="12.34565",
    )
    candle["_signature"] = (
        "100.12345679", "102.12345679", "99.12345679", "101.12345679", "12.3457",
    )

    args = redis_publisher._pipeline_candles_to_args([candle])

    assert args[1:6] == list(candle["_signature"])
    event = json.loads(args[6], parse_float=Decimal)
    assert event["close"] == Decimal("101.12345679")
    assert event["volume"] == Decimal("12.3457")


def test_incremental_is_idempotent_and_revision_does_not_duplicate_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, client = _publisher_with_client(monkeypatch)
    config = _config()
    original = _candle(0)
    revision = _candle(0, close="101.25")

    publisher._publish_one(config, "GOLD", "M5", [original])
    publisher._publish_one(config, "GOLD", "M5", [original])
    publisher._publish_one(config, "GOLD", "M5", [revision])

    list_key, candle_prefix = _keys()
    stamp = _expect_stamp(original)
    assert client.lists[list_key] == [stamp]
    assert client.hashes[candle_prefix + stamp]["close"] == "101.25000000"
    assert len(client.published) == 2
    assert all(len(json.loads(message)["candles"]) == 1 for _, message in client.published)


def test_incremental_sorts_late_arrival_and_trims_oldest_without_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, client = _publisher_with_client(monkeypatch)
    config = _config(bars=3)
    candles = [_candle(0), _candle(10), _candle(5), _candle(15)]

    for candle in candles:
        publisher._publish_one(config, "GOLD", "M5", [candle])
    publisher._publish_one(config, "GOLD", "M5", [_candle(10)])

    list_key, candle_prefix = _keys()
    expected = sorted(_expect_stamp(candle) for candle in candles[1:])
    assert client.lists[list_key] == expected
    assert set(client.hashes) == {candle_prefix + stamp for stamp in expected}
    assert len(client.lists[list_key]) == len(set(client.lists[list_key]))
    oldest = _expect_stamp(candles[0])
    assert candle_prefix + oldest not in client.hashes


def test_incremental_deduplicates_unsorted_input_with_latest_value_winning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, client = _publisher_with_client(monkeypatch)
    original = _candle(5, close="101")
    latest = _candle(5, close="101.75")

    publisher._publish_one(_config(), "GOLD", "M5", [_candle(10), original, latest, _candle(0)])

    list_key, candle_prefix = _keys()
    order = client.lists[list_key]
    assert order == sorted(set(order))
    stamp = _expect_stamp(latest)
    assert client.hashes[candle_prefix + stamp]["close"] == "101.75000000"
    assert len(json.loads(client.published[0][1])["candles"]) == 3


def test_incremental_repairs_partial_hash_without_duplicating_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, client = _publisher_with_client(monkeypatch)
    candle = _candle(0)
    publisher._publish_one(_config(), "GOLD", "M5", [candle])
    list_key, candle_prefix = _keys()
    stamp = client.lists[list_key][0]
    del client.hashes[candle_prefix + stamp]["volume"]

    publisher._publish_one(_config(), "GOLD", "M5", [candle])

    assert client.lists[list_key] == [stamp]
    assert client.hashes[candle_prefix + stamp]["volume"] == "12.5000"
    assert len(client.published) == 2


def test_reconcile_patches_only_differences_and_rebuilds_list_only_when_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dp_program.util import redis_publisher

    publisher, client = _publisher_with_client(monkeypatch)
    list_key, candle_prefix = _keys()
    rows = [
        (
            candle["timestamp"], candle["open"], candle["high"], candle["low"],
            candle["close"], candle["volume"],
        )
        for candle in (_candle(0), _candle(5), _candle(10))
    ]
    monkeypatch.setattr(redis_publisher, "read_latest_candles", lambda *_args, **_kwargs: rows)

    publisher._reconcile_one(_config(), 9, "GOLD", "M5")
    writes_after_first = client.hash_writes
    rebuilds_after_first = client.list_rebuilds
    publisher._reconcile_one(_config(), 9, "GOLD", "M5")

    assert client.hash_writes == writes_after_first
    assert client.list_rebuilds == rebuilds_after_first
    client.lists[list_key] = list(reversed(client.lists[list_key]))
    stale = candle_prefix + "1757000000"
    client.lists[list_key].append("1757000000")
    client.hashes[stale] = {"bartime": "cu", "close": "1"}
    publisher._reconcile_one(_config(), 9, "GOLD", "M5")
    assert client.lists[list_key] == sorted(client.lists[list_key])
    assert stale not in client.hashes
    assert client.list_rebuilds == rebuilds_after_first + 1
    assert client.published == []


def test_publisher_registers_lua_instead_of_sending_eval_source_every_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, client = _publisher_with_client(monkeypatch)

    publisher._publish_one(_config(), "GOLD", "M5", [_candle(0)])
    publisher._publish_one(_config(), "GOLD", "M5", [_candle(5)])

    incremental_scripts = [script for script in client.registered if "PUBLISH" in script]
    assert len(incremental_scripts) == 1
    assert len(client.script_calls) == 2
