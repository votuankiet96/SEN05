"""Vòng điều phối với đối tượng giả: mỗi lần chạy là lần chạy MỚI (xóa rồi seed), dừng sạch, Redis mất kết nối giữa chừng."""
from __future__ import annotations

from pathlib import Path

import pytest

from configuration import load_config, with_overrides
from player import play
from redis_writer import RedisUnavailable, WriteStats, stamp
from schedule import release_time
from support import M5, M15, at, series

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.yaml"
START, END = "2026-09-30 13:00:00", "2026-09-30 14:00:00"
PRE = series(M5, "2026-09-30 11:00", 24) + series(M15, "2026-09-30 11:00", 8)          # nến đã đóng trước T0 (giờ phát <= 13:00)
CANDLES = series(M5, "2026-09-30 13:00", 12) + series(M15, "2026-09-30 13:00", 4)      # giờ phát 13:05 .. 14:00


def make_config():
    return with_overrides(load_config(EXAMPLE), start=START, end=END)


class FakeSource:
    def __init__(self):
        self.seed_calls = []

    def pairs(self, symbols, timeframes):
        return [M5, M15]

    def window(self, pairs, after, upto):
        return [c for c in CANDLES if after < release_time(c) <= upto]

    def seed(self, pairs, at_time, limit):
        self.seed_calls.append((at_time, limit))
        released = [c for c in PRE + CANDLES if release_time(c) <= at_time]
        return [c for pair in pairs for c in [x for x in released if x.pair == pair][-limit:]]


class FakeWriter:
    def __init__(self, state=None, on_tick=None, keys=0):
        self.state, self.calls, self.on_tick, self.keys = dict(state or {}), [], on_tick, keys

    def ping(self):
        return True

    def clean(self):
        self.calls.append(("clean",))
        cleared, self.keys, self.state = self.keys, 0, {}
        return cleared

    def read_state(self):
        return dict(self.state)

    def seed(self, candles, at_time):
        self.calls.append(("seed", len(candles), at_time))
        self.keys = len(candles)
        return WriteStats(len(candles), len(candles))

    def begin_run(self, run_id, at_time, *, tick_seq=0, digest=""):
        self.calls.append(("begin", tick_seq))
        self.state = {"run_id": run_id, "tick_seq": str(tick_seq), "sim_time": stamp(at_time), "digest": digest, "status": "running"}

    def write_tick(self, tick, *, run_id, digest):
        self.calls.append(("tick", tick.seq))
        self.state.update(tick_seq=str(tick.seq), sim_time=stamp(tick.release), digest=digest)
        if self.on_tick:
            self.on_tick(tick)
        return WriteStats(len(tick.candles), len(tick.candles))

    def mark_finished(self):
        self.calls.append(("finished",))
        self.state["status"] = "finished"


class FakePacer:
    def __init__(self):
        self.origin, self.origins = None, []

    def begin(self, origin):
        self.origin = origin
        self.origins.append(origin)

    def wait(self, release, stop):
        return None if stop() else 0.0


class FakeRuntime:
    def __init__(self):
        self.stop, self.states = False, []

    def stop_requested(self):
        return self.stop

    def write_state(self, *, force=False, **fields):
        self.states.append(fields)
        return True


def run(writer, *, runtime=None, pacer=None, source=None, **play_kwargs):
    runtime, pacer, source = runtime or FakeRuntime(), pacer or FakePacer(), source or FakeSource()
    summary = play(make_config(), source, writer, pacer, runtime, **play_kwargs)
    return summary, source, pacer, runtime


def ticks_of(writer):
    return [call[1] for call in writer.calls if call[0] == "tick"]


def kinds(writer):
    return [call[0] for call in writer.calls]


def test_a_run_clears_the_db_then_seeds_then_writes_every_tick_in_order_then_finishes():
    leftover = {"run_id": "old", "tick_seq": "9", "sim_time": "2026-09-30 13:45:00", "digest": "d", "status": "running"}
    writer = FakeWriter(state=leftover, keys=777)                    # db còn dữ liệu của lần chạy trước
    summary, source, pacer, _ = run(writer)
    assert kinds(writer)[:3] == ["clean", "seed", "begin"] and kinds(writer)[-1] == "finished"
    assert ticks_of(writer) == list(range(1, 13))
    assert source.seed_calls == [(at("2026-09-30 13:00"), 1200)]
    assert pacer.origin == at("2026-09-30 13:00")
    assert (summary.status, summary.ticks, summary.candles, summary.last_sim_time) == ("finished", 12, 16, "2026-09-30 14:00:00")
    assert writer.state["status"] == "finished" and writer.state["tick_seq"] == "12" and writer.state["run_id"] == summary.run_id != "old"
    assert len(summary.digest) == 64


def test_stop_request_ends_cleanly_without_marking_finished_and_running_again_starts_over():
    runtime = FakeRuntime()
    first = FakeWriter(on_tick=lambda tick: setattr(runtime, "stop", tick.seq == 5))
    summary, *_ = run(first, runtime=runtime)
    assert summary.status == "stopped" and ticks_of(first) == [1, 2, 3, 4, 5]
    assert ("finished",) not in first.calls and first.state["status"] == "running" and first.state["tick_seq"] == "5"
    assert summary.digest == first.state["digest"]                 # mã băm lần chạy bị dừng = mã băm checkpoint, không tính mốc chưa ghi

    again = FakeWriter(state=first.state, keys=50)                 # chạy lại trên db còn dở: làm lại từ đầu, không tiếp từ mốc 5
    rerun, *_ = run(again)
    assert kinds(again)[:3] == ["clean", "seed", "begin"] and ticks_of(again) == list(range(1, 13)) and rerun.status == "finished"
    assert rerun.digest == run(FakeWriter())[0].digest             # chạy lại ra đúng kết quả như chạy mới tinh


def test_stop_requested_before_the_start_touches_nothing():
    runtime = FakeRuntime()
    runtime.stop = True
    writer = FakeWriter()
    summary, *_ = run(writer, runtime=runtime)
    assert summary.status == "stopped" and writer.calls == []


def test_pacing_clock_starts_only_after_the_first_window_has_been_read():
    order = []

    class SourceSpy(FakeSource):
        def window(self, pairs, after, upto):
            order.append("window")
            return super().window(pairs, after, upto)

    class PacerSpy(FakePacer):
        def begin(self, origin):
            order.append("begin")
            super().begin(origin)

    run(FakeWriter(), source=SourceSpy(), pacer=PacerSpy())
    assert order.index("window") < order.index("begin")


# ---------------------------------------------------------------------------------------------
# Redis mất kết nối trong lúc tiến trình DPS vẫn chạy: chờ rồi chạy tiếp, không tự thoát
# ---------------------------------------------------------------------------------------------
class FakeTime:
    def __init__(self):
        self.now, self.sleeps = 1000.0, []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class OutageWriter(FakeWriter):
    """Redis giả mất kết nối đúng lúc ghi mốc `fail_on_seq` (hoặc lúc seed / đánh dấu kết thúc) và theo kịch bản."""

    def __init__(self, *, fail_on_seq=None, fail_times=1, ping_fails=0, start_ping_fails=0, commit_before_fail=False,
                 rollback_to=None, lose_state=False, seed_fails=0, finish_fails=0, **kwargs):
        super().__init__(**kwargs)
        self.fail_on_seq, self.fail_left, self.ping_fails, self.ping_left = fail_on_seq, fail_times, ping_fails, start_ping_fails
        self.commit_before_fail, self.rollback_to, self.lose_state = commit_before_fail, rollback_to, lose_state
        self.seed_fails, self.finish_fails = seed_fails, finish_fails
        self.history = {}

    def ping(self):
        if self.ping_left > 0:
            self.ping_left -= 1
            raise RedisUnavailable("TimeoutError")
        return True

    def seed(self, candles, at_time):
        if self.seed_fails > 0:
            self.seed_fails -= 1
            self.ping_left = self.ping_fails
            raise RedisUnavailable("TimeoutError")
        return super().seed(candles, at_time)

    def begin_run(self, run_id, at_time, *, tick_seq=0, digest=""):
        super().begin_run(run_id, at_time, tick_seq=tick_seq, digest=digest)
        self.history[tick_seq] = dict(self.state)

    def mark_finished(self):
        if self.finish_fails > 0:
            self.finish_fails -= 1
            raise RedisUnavailable("TimeoutError")
        super().mark_finished()

    def write_tick(self, tick, *, run_id, digest):
        if tick.seq == self.fail_on_seq and self.fail_left > 0:
            self.fail_left -= 1
            self.ping_left = self.ping_fails
            if self.commit_before_fail:                      # Redis đã áp dụng giao dịch nhưng phản hồi bị mất
                super().write_tick(tick, run_id=run_id, digest=digest)
                self.history[tick.seq] = dict(self.state)
            if self.rollback_to is not None:                 # Redis khởi động lại bằng snapshot cũ
                self.state = dict(self.history[self.rollback_to])
            if self.lose_state:                              # Redis khởi động lại mà không giữ dữ liệu
                self.state = {}
            raise RedisUnavailable("TimeoutError")
        stats = super().write_tick(tick, run_id=run_id, digest=digest)
        self.history[tick.seq] = dict(self.state)
        return stats


def uninterrupted_digest():
    return run(FakeWriter())[0].digest


def test_a_redis_outage_is_waited_out_and_the_run_finishes_by_itself_with_the_same_digest():
    clock, writer = FakeTime(), OutageWriter(fail_on_seq=5, ping_fails=3)
    summary, _, pacer, runtime = run(writer, clock=clock.clock, sleep=clock.sleep)
    assert summary.status == "finished" and ticks_of(writer) == list(range(1, 13))        # mỗi mốc đúng một lần, không sót, không trùng
    assert kinds(writer).count("clean") == 1 and kinds(writer).count("seed") == 1           # checkpoint còn khớp nên không xóa/nạp lại
    assert summary.digest == uninterrupted_digest() and writer.state["tick_seq"] == "12" and writer.state["status"] == "finished"
    assert sum(clock.sleeps) >= 1 + 2 + 4 + 8 - 1e-6                                       # thử lại sau 1, 2, 4, 8 giây (backoff)
    statuses = [state.get("status") for state in runtime.states]
    assert "waiting_redis" in statuses and runtime.states[-1].get("status") != "waiting_redis"
    assert pacer.origins == [at("2026-09-30 13:00"), at("2026-09-30 13:25")]              # đồng hồ pacing đặt lại sau khi chờ: không phát bù dồn dập


def test_a_reply_lost_after_redis_applied_the_tick_does_not_write_it_twice():
    clock, writer = FakeTime(), OutageWriter(fail_on_seq=5, commit_before_fail=True)
    summary, *_ = run(writer, clock=clock.clock, sleep=clock.sleep)
    assert summary.status == "finished" and ticks_of(writer) == list(range(1, 13))
    assert summary.digest == uninterrupted_digest()


def test_redis_coming_back_with_an_older_snapshot_is_cleared_and_re_seeded_at_the_last_written_tick():
    clock, source, writer = FakeTime(), FakeSource(), OutageWriter(fail_on_seq=8, rollback_to=3)
    summary, _, pacer, _ = run(writer, source=source, clock=clock.clock, sleep=clock.sleep)
    assert ticks_of(writer) == list(range(1, 13))                                          # không ghi lại mốc nào: nạp lại cửa sổ rồi đi tiếp
    assert kinds(writer).count("clean") == 2 and ("begin", 7) in writer.calls              # checkpoint viết lại đúng mốc 7 (đã ghi gần nhất)
    assert source.seed_calls[-1] == (at("2026-09-30 13:35"), 1200)                         # giờ ảo của mốc 7
    assert ("seed", 24 + 8 + 7 + 2, at("2026-09-30 13:35")) in writer.calls                # đúng các nến đã đóng tới 13:35: 32 trước T0 + 7 M5 + 2 M15
    assert summary.status == "finished" and writer.state["tick_seq"] == "12" and summary.digest == uninterrupted_digest()
    assert pacer.origins[-1] == at("2026-09-30 13:40")


def test_redis_coming_back_empty_is_re_seeded_and_the_run_continues():
    clock, source, writer = FakeTime(), FakeSource(), OutageWriter(fail_on_seq=6, lose_state=True)
    summary, *_ = run(writer, source=source, clock=clock.clock, sleep=clock.sleep)
    assert ticks_of(writer) == list(range(1, 13)) and summary.status == "finished"
    assert ("seed", 24 + 8 + 5 + 1, at("2026-09-30 13:25")) in writer.calls                # trạng thái sau mốc 5: 32 + 5 M5 + 1 M15
    assert source.seed_calls[-1] == (at("2026-09-30 13:25"), 1200) and summary.digest == uninterrupted_digest()


def test_a_stop_request_during_the_wait_ends_the_run_cleanly():
    clock, runtime = FakeTime(), FakeRuntime()
    original = clock.sleep

    def sleep_then_stop(seconds):
        original(seconds)
        runtime.stop = clock.now - 1000.0 > 10

    writer = OutageWriter(fail_on_seq=5, fail_times=10 ** 6, ping_fails=10 ** 6)
    summary, *_ = run(writer, runtime=runtime, clock=clock.clock, sleep=sleep_then_stop)
    assert summary.status == "stopped" and ticks_of(writer) == [1, 2, 3, 4] and ("finished",) not in writer.calls
    assert summary.digest == writer.state["digest"] and writer.state["tick_seq"] == "4"


def test_starting_while_redis_is_down_waits_and_then_runs_normally():
    clock = FakeTime()
    writer = OutageWriter(start_ping_fails=3)
    summary, *_ = run(writer, clock=clock.clock, sleep=clock.sleep)
    assert summary.status == "finished" and ticks_of(writer) == list(range(1, 13)) and sum(clock.sleeps) >= 1 + 2 + 4 - 1e-6


def test_a_stop_request_while_redis_is_down_at_start_writes_nothing():
    clock, runtime = FakeTime(), FakeRuntime()
    original = clock.sleep

    def sleep_then_stop(seconds):
        original(seconds)
        runtime.stop = clock.now - 1000.0 > 5

    writer = OutageWriter(start_ping_fails=10 ** 6)
    summary, *_ = run(writer, runtime=runtime, clock=clock.clock, sleep=sleep_then_stop)
    assert summary.status == "stopped" and writer.calls == []


def test_losing_redis_during_the_start_redoes_the_whole_start():
    clock, writer = FakeTime(), OutageWriter(seed_fails=1, ping_fails=2)
    summary, *_ = run(writer, clock=clock.clock, sleep=clock.sleep)
    assert kinds(writer)[:4] == ["clean", "clean", "seed", "begin"]                       # lần seed hỏng bị xóa đi và làm lại từ đầu
    assert summary.status == "finished" and ticks_of(writer) == list(range(1, 13)) and summary.digest == uninterrupted_digest()


def test_losing_redis_just_as_the_run_finishes_still_marks_it_finished():
    clock, writer = FakeTime(), OutageWriter(finish_fails=1, ping_fails=1)
    summary, *_ = run(writer, clock=clock.clock, sleep=clock.sleep)
    assert summary.status == "finished" and writer.state["status"] == "finished" and ticks_of(writer) == list(range(1, 13))


def test_errors_that_are_not_a_redis_outage_still_stop_the_run():
    class Broken(FakeWriter):
        def write_tick(self, tick, *, run_id, digest):
            raise ValueError("script error")

    with pytest.raises(ValueError):
        run(Broken())
