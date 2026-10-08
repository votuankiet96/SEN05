"""Writer Redis: db đích chỉ dành cho DPS (chỉ cần nằm trong allowed_dbs), clean, checkpoint, và dịch lỗi mất kết nối."""
from __future__ import annotations

import pytest
import redis

from configuration import RedisConfig
from redis_writer import CLOCK_KEY, STATE_KEY, GuardError, RedisUnavailable, RedisWriter
from support import at


class FakePipeline:
    def __init__(self):
        self.commands = []

    def set(self, key, value):
        self.commands.append(("set", key, value))

    def hset(self, key, mapping=None):
        self.commands.append(("hset", key, dict(mapping)))

    def execute(self):
        self.commands.append(("execute",))
        return []


class FakeClient:
    def __init__(self, keys=()):
        self.keys, self.flush_async, self.pipelines = set(keys), None, []

    def dbsize(self):
        return len(self.keys)

    def flushdb(self, asynchronous=False):
        self.flush_async = asynchronous
        self.keys.clear()

    def pipeline(self, transaction=True):
        self.pipelines.append(FakePipeline())
        return self.pipelines[-1]

    def close(self):
        pass


def writer(client, *, db=14, allowed=(14,)):
    config = RedisConfig(host="h", port=6379, username="", password="x", db=db, allowed_dbs=allowed, key_prefix="L_CANDLE",
                         bars_per_snapshot=1200, hash_ttl_seconds=604800)
    return RedisWriter(config, client=client)


def test_clean_refuses_a_db_that_is_not_allowed_and_touches_nothing():
    client = FakeClient({"a"})
    with pytest.raises(GuardError, match="allowed_dbs"):
        writer(client, db=0, allowed=(14,)).clean()
    assert client.keys == {"a"} and client.flush_async is None


def test_clean_flushes_the_allowed_db_asynchronously_whatever_it_holds_and_returns_the_key_count():
    client = FakeClient({"dps:state", "L_CANDLE_GOLD_M5", "something:else"})
    assert writer(client).clean() == 3
    assert client.flush_async is True and not client.keys
    assert writer(FakeClient()).clean() == 0


def test_begin_run_writes_the_clock_and_a_checkpoint_in_one_transaction():
    client = FakeClient()
    writer(client).begin_run("run1", at("2026-09-30 13:00"))
    assert client.pipelines[0].commands == [
        ("set", CLOCK_KEY, "2026-09-30 13:00:00"),
        ("hset", STATE_KEY, {"run_id": "run1", "tick_seq": 0, "sim_time": "2026-09-30 13:00:00", "digest": "", "status": "running"}),
        ("execute",)]


def test_begin_run_can_rewrite_the_checkpoint_of_the_last_written_tick():
    client = FakeClient()
    writer(client).begin_run("run1", at("2026-09-30 13:35"), tick_seq=7, digest="abc")
    state = client.pipelines[0].commands[1][2]
    assert (state["tick_seq"], state["sim_time"], state["digest"]) == (7, "2026-09-30 13:35:00", "abc")


class DownClient(FakeClient):
    """Client mà mọi lệnh đều ném `error` (Redis tắt, mạng đứt, đang nạp lại...)."""

    def __init__(self, error):
        super().__init__()
        self.error = error

    def dbsize(self):
        raise self.error

    def hgetall(self, key):
        raise self.error

    def ping(self):
        raise self.error


TRANSIENT = [
    redis.exceptions.ConnectionError("down"),
    redis.exceptions.TimeoutError("slow"),
    redis.exceptions.BusyLoadingError("loading"),
    redis.exceptions.ResponseError("MISCONF Redis is configured to save RDB snapshots, but it is currently not able to persist on disk"),
]


@pytest.mark.parametrize("error", TRANSIENT, ids=lambda e: type(e).__name__ + ":" + str(e)[:6])
def test_transient_redis_errors_become_redis_unavailable_on_every_io_method(error):
    for call in (lambda w: w.ping(), lambda w: w.read_state(), lambda w: w.clean()):
        with pytest.raises(RedisUnavailable) as caught:
            call(writer(DownClient(error)))
        assert isinstance(caught.value.__cause__, type(error))


@pytest.mark.parametrize("error", [
    redis.exceptions.AuthenticationError("wrong password"),
    redis.exceptions.ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value"),
    redis.exceptions.NoPermissionError("this user has no permissions"),
], ids=lambda e: type(e).__name__)
def test_permanent_redis_errors_are_not_treated_as_an_outage(error):
    with pytest.raises(type(error)):
        writer(DownClient(error)).read_state()


def test_ping_on_a_healthy_client_is_true_and_never_needs_a_tcp_probe():
    class Healthy(FakeClient):
        def ping(self):
            return True

    assert writer(Healthy()).ping() is True            # client giả => host "h" không bị phân giải/mở TCP
