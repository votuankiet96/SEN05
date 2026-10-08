"""Tích hợp trên Redis THẬT (db trong config.yaml): hợp đồng live, nguyên tử, ghi lặp, clean.

Chỉ chạy khi DPS_INTEGRATION=1. Dùng dữ liệu giả; db đích phải RỖNG lúc bắt đầu (nếu không test dừng ngay và
không đụng gì) và luôn được dọn sạch lúc kết thúc. Không bao giờ chạy với db không nằm trong allowed_dbs.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import timedelta
from decimal import Decimal

import pytest

from configuration import load_config
from redis_writer import CLOCK_KEY, HASH_FIELDS, STATE_KEY, RedisWriter, _connect, stamp
from runtime import Candle, Tick
from schedule import iter_ticks
from support import M5, M15, at, fetch_from

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("DPS_INTEGRATION") != "1", reason="set DPS_INTEGRATION=1 to run against the real Redis db"),
]
T0 = at("2026-09-30 13:00")
KEY5, KEY15 = "L_CANDLE_GOLD_M5", "L_CANDLE_GOLD_M15"


@pytest.fixture()
def target():
    config = load_config()
    writer = RedisWriter(config.redis)
    if writer.size() != 0:
        writer.close()
        pytest.fail(f"db {config.redis.db} is not empty; refusing to run (nothing was touched)")
    try:
        yield config, writer
    finally:
        writer.clean()
        writer.close()


def priced(pair, open_time, number):
    price = Decimal(f"{4000 + number}.25")
    return Candle(pair, open_time, price, price + 1, price - 1, price + Decimal("0.5"))


def seed_candles():
    m5 = [priced(M5, T0 - timedelta(minutes=5 * (1200 - i)), i) for i in range(1200)]
    m15 = [priced(M15, T0 - timedelta(minutes=15 * (40 - i)), i) for i in range(40)]
    return m5 + m15


def collect_hset(pubsub, *, expected=None, quiet=0.6, timeout=3.0):
    """Gom sự kiện `hset` (tên key): dừng khi đủ `expected` hoặc khi yên lặng `quiet` giây."""
    events, deadline, last = [], time.monotonic() + timeout, time.monotonic()
    while time.monotonic() < deadline:
        message = pubsub.get_message(timeout=0.1)
        if message and message["type"] == "pmessage":
            if message["data"] == "hset":
                events.append(message["channel"].split(":", 1)[1])
            last = time.monotonic()
        if expected is not None and len(events) >= expected and time.monotonic() - last > 0.2:
            break
        if expected is None and time.monotonic() - last > quiet:
            break
    return events


def test_seed_ticks_and_replay_follow_the_live_contract(target):
    config, writer = target
    client = writer._client
    notify = client.config_get("notify-keyspace-events")["notify-keyspace-events"]
    assert "K" in notify and ("h" in notify or "A" in notify), f"keyspace notifications for hashes are off ({notify!r})"

    seeded = writer.seed(seed_candles(), T0)
    assert (seeded.candles, seeded.changed) == (1240, 1240)
    assert client.llen(KEY5) == 1200 and client.llen(KEY15) == 40
    head, tail = client.lindex(KEY5, 0), client.lindex(KEY5, -1)
    assert head == stamp(T0 - timedelta(minutes=5)) and tail == stamp(T0 - timedelta(minutes=5 * 1200))
    stamps = client.lrange(KEY5, 0, 9)
    assert stamps == sorted(stamps, reverse=True)                                  # List giảm dần: index 0 = mới nhất
    fields = client.hgetall(f"{KEY5}:{head}")
    assert set(fields) == set(HASH_FIELDS)
    assert fields["timestamp"] == head and fields["time_update"] == stamp(T0)
    assert (fields["open"], fields["high"], fields["low"], fields["close"]) == ("5199.25", "5200.25", "5198.25", "5199.75")
    assert 0 < client.ttl(f"{KEY5}:{head}") <= config.redis.hash_ttl_seconds

    candles = [priced(M5, T0, 1200), priced(M5, T0 + timedelta(minutes=5), 1201),
               priced(M5, T0 + timedelta(minutes=10), 1202), priced(M15, T0, 40)]
    ticks = list(iter_ticks(fetch_from(candles), T0, T0 + timedelta(minutes=15)))
    assert [len(t.candles) for t in ticks] == [1, 1, 2]                            # tick 3: M5 và M15 cùng một lượt

    pubsub = client.pubsub(ignore_subscribe_messages=True)
    pubsub.psubscribe(f"__keyspace@{config.redis.db}__:L_CANDLE_*")
    pubsub.get_message(timeout=1.0)
    try:
        for tick in ticks:
            stats = writer.write_tick(tick, run_id="itest", digest=f"d{tick.seq}")
            assert (stats.candles, stats.changed, stats.added) == (len(tick.candles), len(tick.candles), len(tick.candles))
        events = collect_hset(pubsub, expected=4)
        assert sorted(events) == sorted([f"{KEY5}:{stamp(T0)}", f"{KEY5}:{stamp(T0 + timedelta(minutes=5))}",
                                         f"{KEY5}:{stamp(T0 + timedelta(minutes=10))}", f"{KEY15}:{stamp(T0)}"])

        assert client.llen(KEY5) == 1200 and client.llen(KEY15) == 41              # cửa sổ 1200: nến cũ nhất bị đẩy ra
        assert client.lindex(KEY5, 0) == stamp(T0 + timedelta(minutes=10))
        assert not client.exists(f"{KEY5}:{tail}")                                 # Hash của nến bị đẩy ra cũng bị xóa
        assert client.get(CLOCK_KEY) == stamp(T0 + timedelta(minutes=15))
        assert client.hgetall(STATE_KEY) == {"run_id": "itest", "tick_seq": "3", "sim_time": stamp(T0 + timedelta(minutes=15)),
                                            "digest": "d3", "status": "running"}

        replay = writer.write_tick(ticks[2], run_id="itest", digest="d3")          # ghi lặp cùng dữ liệu
        assert (replay.changed, replay.added, replay.evicted) == (0, 0, 0)
        assert collect_hset(pubsub) == []                                          # idempotent = im lặng: không sự kiện hset nào
    finally:
        pubsub.close()

    writer.mark_finished()
    assert client.hget(STATE_KEY, "status") == "finished"
    assert writer.clean() > 1200 and writer.size() == 0


def test_a_tick_is_atomic_for_concurrent_readers(target):
    config, writer = target
    reader = _connect(config.redis)
    mismatches, reads, stop = [], [0], threading.Event()

    def read_both_heads():
        while not stop.is_set():
            pipe = reader.pipeline(transaction=True)                               # đọc hai head trong một MULTI/EXEC
            pipe.lindex(KEY5, 0)
            pipe.lindex(KEY15, 0)
            five, fifteen = pipe.execute()
            reads[0] += 1
            if five != fifteen:
                mismatches.append((five, fifteen))

    # M5 và M15 giả có cùng giờ mở nên cùng stamp: một mốc bị nhìn thấy nửa vời sẽ làm hai head lệch nhau.
    for pair in (M5, M15):
        writer.seed([priced(pair, T0, 0)], T0)
    thread = threading.Thread(target=read_both_heads)
    thread.start()
    try:
        for k in range(1, 301):
            when = T0 + timedelta(minutes=k)
            writer.write_tick(Tick(k, when, (priced(M5, when, k), priced(M15, when, k))), run_id="itest", digest="x")
    finally:
        stop.set()
        thread.join(timeout=10)
        reader.close()
    assert reads[0] > 50 and mismatches == []
