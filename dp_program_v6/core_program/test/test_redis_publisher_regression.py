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


_PAIR = (9, "GOLD", "M5")


def _row(
    minute: int,
    *,
    open_: str = "100",
    high: str = "102",
    low: str = "99",
    close: str = "101",
    volume: str | None = "12.5",
    created_minute: int = 40,
) -> tuple[Any, ...]:
    """Mot row Fact: (BarTime, Open, High, Low, Close, Volume, CreatedAt)."""
    return (
        datetime(2026, 9, 8, 12, minute, tzinfo=timezone.utc),
        Decimal(open_), Decimal(high), Decimal(low), Decimal(close),
        None if volume is None else Decimal(volume),
        datetime(2026, 9, 8, 13, created_minute, tzinfo=timezone.utc),
    )


# Dung bo field Hash, dung thu tu ghi.
_FIELDS = ("timestamp", "open", "high", "low", "close", "time_update")
# Chi 4 o gia tham gia so sanh "co doi khong".
_COMPARED = ("open", "high", "low", "close")


def _expect_stamp(row: tuple[Any, ...]) -> str:
    """Moc ky vong, tu dung lai doc lap voi redis_publisher._stamp().

    Neu goi thang ham cua production thi mot loi trong ham do se tu xac
    nhan la dung -- test phai tu biet ky vong dung la gi.
    """
    return row[0].strftime("%Y-%m-%d %H:%M:%S")


class _FakeWarehouse:
    """SQL gia: publisher doc lai row tu day chu khong tin gia tri trong RAM."""

    def __init__(self) -> None:
        self.rows: dict[datetime, tuple[Any, ...]] = {}
        self.reads: list[list[datetime] | None] = []

    def store(self, *rows: tuple[Any, ...]) -> None:
        for row in rows:
            self.rows[row[0].replace(tzinfo=None)] = row

    def read(
        self, _config: Any, _symbol_id: int, _tf_code: str, limit: int,
        bartimes: list[datetime] | None = None,
    ) -> list[tuple[Any, ...]]:
        self.reads.append(None if bartimes is None else list(bartimes))
        wanted = sorted(self.rows)
        if bartimes is not None:
            wanted = [value for value in sorted(bartimes) if value in self.rows]
        return [self.rows[value] for value in wanted[-limit:]]


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
        return tuple(candle.get(field) for field in _COMPARED) != tuple(values)

    def _incremental(self, keys: list[str], args: list[str]) -> list[int]:
        list_key, channel = keys
        max_size = int(args[0])
        candle_prefix, event_prefix = args[1], args[2]
        order = self.lists.setdefault(list_key, [])
        changed_events: list[str] = []
        added = 0
        for index in range(3, len(args), 7):
            stamp = args[index]
            prices = args[index + 1:index + 5]
            updated, event = args[index + 5], args[index + 6]
            key = candle_prefix + stamp
            if self._differs(key, prices):
                self._write(key, [stamp, *prices, updated])
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
            stamp = args[index]
            prices = args[index + 1:index + 5]
            updated = args[index + 5]
            wanted_order.append(stamp)
            key = candle_prefix + stamp
            if self._differs(key, prices):
                self._write(key, [stamp, *prices, updated])
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


def _publisher_with_client(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, _SemanticRedis, _FakeWarehouse]:
    from dp_program.util import redis_publisher

    client = _SemanticRedis()
    warehouse = _FakeWarehouse()
    publisher = redis_publisher._RedisPublisher()
    monkeypatch.setattr(publisher, "_get_client", lambda _settings: client)
    monkeypatch.setattr(redis_publisher, "read_latest_candles", warehouse.read)
    return publisher, client, warehouse


def _publish(publisher: Any, config: dict[str, Any], warehouse: _FakeWarehouse,
             *rows: tuple[Any, ...]) -> None:
    """Ghi row vao SQL gia roi publish dung cac moc do."""
    warehouse.store(*rows)
    publisher._publish_one(
        config, _PAIR, [row[0].replace(tzinfo=None) for row in rows]
    )


def _keys() -> tuple[str, str]:
    """(list_key, candle_prefix) -- key nen la candle_prefix + stamp."""
    return "T_CANDLE_GOLD_M5", "T_CANDLE_GOLD_M5:"


def test_key_layout_is_one_concatenation_and_list_key_never_holds_a_colon() -> None:
    from dp_program.util import redis_publisher

    list_key, candle_prefix = redis_publisher._RedisPublisher._keys(
        _config()["redis"], "M5", "GOLD"
    )
    stamp = _expect_stamp(_row(0))
    candle_key = candle_prefix + stamp

    assert (list_key, candle_prefix) == _keys()
    # Key nen = key List noi them ":" + moc: dung mot phep noi, khong quy uoc khac.
    assert candle_key == f"{list_key}:{stamp}"
    # Ten List khong bao gio chua ":" -- do la cach duy nhat phan biet no voi
    # key Hash khi SCAN, vi moc co san hai dau ":" cua gio-phut-giay.
    assert ":" not in list_key
    assert stamp.count(":") == 2 and candle_key.count(":") == 3


def test_list_member_equals_the_hash_timestamp_field_and_the_key_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bat bien cot loi: ba cho do luon la CUNG mot chuoi."""
    publisher, client, warehouse = _publisher_with_client(monkeypatch)
    row = _row(0)

    _publish(publisher, _config(), warehouse, row)

    list_key, candle_prefix = _keys()
    member = client.lists[list_key][0]
    candle = client.hashes[candle_prefix + member]
    assert member == _expect_stamp(row)
    assert candle["timestamp"] == member
    assert f"{list_key}:{member}" == candle_prefix + member


def test_redis_payload_preserves_warehouse_decimal_contract() -> None:
    from dp_program.util import redis_publisher

    row = _row(
        0,
        open_="9999999999.12345678",
        high="9999999999.22345678",
        low="9999999999.02345678",
        close="9999999999.12345679",
        volume="1234567890123456.1234",
    )
    args = redis_publisher._rows_to_args([row], with_event=True)

    assert len(args) == 7
    assert args[1:5] == [
        "9999999999.12345678",
        "9999999999.22345678",
        "9999999999.02345678",
        "9999999999.12345679",
    ]
    assert args[0] == "2026-09-08 12:00:00"  # moc lam chi muc + duoi key Hash
    assert args[5] == "2026-09-08 13:40:00"  # time_update = Fact.CreatedAt
    event = json.loads(args[6], parse_float=Decimal)
    assert event == {
        "bartime": "2026-09-08 12:00:00",
        "open": Decimal("9999999999.12345678"),
        "high": Decimal("9999999999.22345678"),
        "low": Decimal("9999999999.02345678"),
        "close": Decimal("9999999999.12345679"),
    }


def test_redis_payload_uses_fixed_warehouse_scales_and_ignores_volume() -> None:
    """Hash chi con 4 field gia; volume cua SQL khong con duoc dua len Redis."""
    from dp_program.util import redis_publisher

    args = redis_publisher._rows_to_args([_row(0, volume=None)], with_event=True)

    assert args[1:5] == ["100.00000000", "102.00000000", "99.00000000", "101.00000000"]
    assert "volume" not in json.loads(args[6])


def test_reconcile_args_carry_every_hash_field_except_the_event_payload() -> None:
    from dp_program.util import redis_publisher

    args = redis_publisher._rows_to_args([_row(0), _row(5)], with_event=False)

    # 6 o moi nen: stamp + 4 gia + time_update (`timestamp` = chinh stamp).
    assert len(args) == 12
    assert args[0] == "2026-09-08 12:00:00"
    assert args[6] == "2026-09-08 12:05:00"


def test_incremental_writes_all_six_hash_fields_from_the_sql_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, client, warehouse = _publisher_with_client(monkeypatch)
    row = _row(0)

    _publish(publisher, _config(), warehouse, row)

    _, candle_prefix = _keys()
    candle = client.hashes[candle_prefix + _expect_stamp(row)]
    assert set(candle) == set(_FIELDS)
    assert candle == {
        "timestamp": "2026-09-08 12:00:00",
        "open": "100.00000000", "high": "102.00000000",
        "low": "99.00000000", "close": "101.00000000",
        "time_update": "2026-09-08 13:40:00",
    }


def test_incremental_publishes_sql_values_not_the_in_memory_candle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis la ban chup cua SQL: gia tri luon doc lai tu Fact truoc khi ghi."""
    publisher, client, warehouse = _publisher_with_client(monkeypatch)
    committed = _row(0, close="101.75")

    _publish(publisher, _config(), warehouse, committed)

    _, candle_prefix = _keys()
    assert client.hashes[candle_prefix + _expect_stamp(committed)]["close"] == "101.75000000"
    # Publisher chi gui moc sang SQL, khong gui gia tri nao.
    assert warehouse.reads == [[committed[0].replace(tzinfo=None)]]


def test_incremental_is_idempotent_and_revision_does_not_duplicate_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, client, warehouse = _publisher_with_client(monkeypatch)
    config = _config()
    original = _row(0)
    revision = _row(0, close="101.25")

    _publish(publisher, config, warehouse, original)
    _publish(publisher, config, warehouse, original)
    _publish(publisher, config, warehouse, revision)

    list_key, candle_prefix = _keys()
    stamp = _expect_stamp(original)
    assert client.lists[list_key] == [stamp]
    assert client.hashes[candle_prefix + stamp]["close"] == "101.25000000"
    assert len(client.published) == 2
    assert all(len(json.loads(message)["candles"]) == 1 for _, message in client.published)


def test_incremental_sorts_late_arrival_and_trims_oldest_without_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, client, warehouse = _publisher_with_client(monkeypatch)
    config = _config(bars=3)
    rows = [_row(0), _row(10), _row(5), _row(15)]

    for row in rows:
        _publish(publisher, config, warehouse, row)
    _publish(publisher, config, warehouse, _row(10))

    list_key, candle_prefix = _keys()
    expected = sorted(_expect_stamp(row) for row in rows[1:])
    assert client.lists[list_key] == expected
    assert set(client.hashes) == {candle_prefix + stamp for stamp in expected}
    assert len(client.lists[list_key]) == len(set(client.lists[list_key]))
    oldest = _expect_stamp(rows[0])
    assert candle_prefix + oldest not in client.hashes


def test_incremental_ignores_stamps_older_than_the_window_instead_of_publishing_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nen cu hon cua so se bi eviction xoa ngay, nen khong duoc ghi/phat event."""
    publisher, client, warehouse = _publisher_with_client(monkeypatch)
    rows = [_row(minute) for minute in (0, 5, 10, 15)]

    _publish(publisher, _config(bars=3), warehouse, *rows)

    list_key, _ = _keys()
    assert client.lists[list_key] == [_expect_stamp(row) for row in rows[1:]]
    # Chi doc 3 moc moi nhat tu SQL; moc cu nhat khong bao gio duoc yeu cau.
    assert warehouse.reads == [[row[0].replace(tzinfo=None) for row in rows[1:]]]
    assert len(json.loads(client.published[0][1])["candles"]) == 3


def test_incremental_repairs_partial_hash_without_duplicating_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, client, warehouse = _publisher_with_client(monkeypatch)
    row = _row(0)
    _publish(publisher, _config(), warehouse, row)
    list_key, candle_prefix = _keys()
    stamp = client.lists[list_key][0]
    del client.hashes[candle_prefix + stamp]["close"]

    _publish(publisher, _config(), warehouse, row)

    assert client.lists[list_key] == [stamp]
    assert client.hashes[candle_prefix + stamp]["close"] == "101.00000000"
    assert len(client.published) == 2


def test_reconcile_patches_only_differences_and_rebuilds_list_only_when_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, client, warehouse = _publisher_with_client(monkeypatch)
    list_key, candle_prefix = _keys()
    warehouse.store(_row(0), _row(5), _row(10))

    publisher._reconcile_one(_config(), 9, "GOLD", "M5")
    writes_after_first = client.hash_writes
    rebuilds_after_first = client.list_rebuilds
    publisher._reconcile_one(_config(), 9, "GOLD", "M5")

    assert client.hash_writes == writes_after_first
    assert client.list_rebuilds == rebuilds_after_first
    client.lists[list_key] = list(reversed(client.lists[list_key]))
    stale_stamp = "2026-09-08 11:00:00"
    stale = candle_prefix + stale_stamp
    client.lists[list_key].append(stale_stamp)
    client.hashes[stale] = {"close": "1"}
    publisher._reconcile_one(_config(), 9, "GOLD", "M5")
    assert client.lists[list_key] == sorted(client.lists[list_key])
    assert stale not in client.hashes
    assert client.list_rebuilds == rebuilds_after_first + 1
    assert client.published == []


def test_reconcile_writes_the_same_six_fields_as_the_incremental_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, client, warehouse = _publisher_with_client(monkeypatch)
    row = _row(0)
    warehouse.store(row)

    publisher._reconcile_one(_config(), 9, "GOLD", "M5")

    _, candle_prefix = _keys()
    candle = client.hashes[candle_prefix + _expect_stamp(row)]
    assert set(candle) == set(_FIELDS)
    assert candle["timestamp"] == _expect_stamp(row)
    assert candle["time_update"] == "2026-09-08 13:40:00"


def test_publisher_registers_lua_instead_of_sending_eval_source_every_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, client, warehouse = _publisher_with_client(monkeypatch)

    _publish(publisher, _config(), warehouse, _row(0))
    _publish(publisher, _config(), warehouse, _row(5))

    incremental_scripts = [script for script in client.registered if "PUBLISH" in script]
    assert len(incremental_scripts) == 1
    assert len(client.script_calls) == 2
