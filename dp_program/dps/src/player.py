"""Điều phối replay: chạy thật (`run_replay`) và chạy khô (`plan_replay`) trên cùng một lịch.

`play` chỉ điều phối: nhận các cộng tác viên đã dựng sẵn (nguồn SQL, writer Redis, pacer, runtime) nên kiểm thử được
bằng đối tượng giả. MỖI LẦN `run` LÀ MỘT LẦN CHẠY MỚI: chờ Redis -> xóa sạch db đích (chỉ dành cho DPS) -> seed tối đa
1200 nến/pair đã đóng trước T0 -> với mỗi mốc: kiểm dừng -> chờ theo pacing -> ghi nguyên tử kèm checkpoint. Khởi động
lại DPS nghĩa là làm lại từ đầu; không có lệnh chạy tiếp từ checkpoint của tiến trình đã chết.

Redis mất kết nối khi tiến trình DPS vẫn đang chạy KHÔNG làm chạy hỏng: `play` chờ (thử lại sau 1, 2, 4, 8 rồi 15 giây
một lần; vẫn ghi heartbeat và nghe lệnh dừng) rồi tự chạy tiếp. Nếu checkpoint trong Redis còn khớp mốc đang dở thì ghi lại
đúng mốc đó (an toàn vì mỗi mốc là một giao dịch nguyên tử và script Lua ghi lặp không đổi kết quả); nếu Redis quay lại với
snapshot cũ hơn hoặc mất sạch dữ liệu thì xóa db, nạp lại cửa sổ nến tại giờ ảo của mốc đã ghi gần nhất rồi chạy tiếp.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator

import schedule
from configuration import Config
from redis_writer import RedisUnavailable, RedisWriter, WriteStats
from runtime import Pair, Runtime, Tick, instance_lock, log_event, make_pacer
from sql_source import SqlSource

LOGGER = logging.getLogger("dps.player")
_PROGRESS_LOG_SECONDS = 10.0
_RETRY_FIRST_SECONDS, _RETRY_MAX_SECONDS = 1.0, 15.0     # chờ Redis: thử lại sau 1, 2, 4, 8 rồi 15 giây một lần
_OUTAGE_LOG_SECONDS = 60.0                               # nhắc "vẫn chưa có Redis" mỗi phút
_WAIT_SLICE_SECONDS = 0.25                               # lát sleep khi chờ: nghe lệnh dừng và ghi heartbeat đều đặn


@dataclass(slots=True)
class RunSummary:
    status: str = "finished"    # finished | stopped
    run_id: str = ""
    ticks: int = 0
    candles: int = 0
    changed: int = 0
    evicted: int = 0
    max_lag_seconds: float = 0.0
    last_sim_time: str = ""
    digest: str = ""


@dataclass(frozen=True, slots=True)
class PlanResult:
    pairs: int
    stats: schedule.PlanStats


def _ticks(config: Config, source: Any, pairs: list[Pair]) -> Iterator[Tick]:
    replay = config.replay
    return schedule.iter_ticks(
        lambda after, upto: source.window(pairs, after, upto), replay.start, replay.end,
        window=timedelta(days=replay.window_days),
    )


def _await_redis(
    writer: Any, runtime: Any, error: Exception, *, clock: Callable[[], float], sleep: Callable[[float], None],
) -> bool:
    """Chờ tới khi Redis trả lời lại. True = đã sẵn sàng; False = có yêu cầu dừng trong lúc chờ. Không bao giờ bỏ cuộc vì hết giờ."""
    since, attempts, delay = clock(), 0, _RETRY_FIRST_SECONDS
    started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    last_log = since
    log_event(LOGGER, logging.WARNING, "REDIS_UNAVAILABLE", error=error, action="waiting; the run continues by itself when Redis answers")
    while True:
        runtime.write_state(status="waiting_redis", redis_error=str(error), waiting_since=started_at, redis_retries=attempts, force=True)
        deadline = clock() + delay
        while clock() < deadline:
            if runtime.stop_requested():
                runtime.write_state(redis_error=None, waiting_since=None, redis_retries=None)
                return False
            sleep(min(_WAIT_SLICE_SECONDS, max(0.0, deadline - clock())))
            runtime.write_state(status="waiting_redis")          # heartbeat (có giãn nhịp) để `status` thấy tiến trình còn sống
        attempts += 1
        try:
            writer.ping()
            break
        except RedisUnavailable as exc:
            error, delay = exc, min(delay * 2, _RETRY_MAX_SECONDS)
            if clock() - last_log >= _OUTAGE_LOG_SECONDS:
                last_log = clock()
                log_event(LOGGER, logging.WARNING, "REDIS_STILL_UNAVAILABLE", waited_s=round(clock() - since), retries=attempts, error=exc)
    log_event(LOGGER, logging.INFO, "REDIS_RECOVERED", waited_s=round(clock() - since), retries=attempts)
    runtime.write_state(status="running", redis_error=None, waiting_since=None, redis_retries=None, force=True)
    return True


def _start(
    config: Config, source: Any, writer: Any, runtime: Any, pairs: list[Pair], run_id: str, *,
    clock: Callable[[], float], sleep: Callable[[float], None],
) -> bool:
    """Bắt đầu lần chạy mới: xóa db đích, seed, ghi checkpoint khởi đầu. Redis mất kết nối giữa chừng thì làm lại từ bước xóa
    (chưa có gì cần giữ). False = bị yêu cầu dừng trong lúc chờ."""
    replay = config.replay
    while True:
        try:
            writer.ping()
            cleared = writer.clean()
            seeded = writer.seed(source.seed(pairs, replay.start, config.redis.bars_per_snapshot), replay.start)
            writer.begin_run(run_id, replay.start)
            log_event(LOGGER, logging.INFO, "SEED_DONE", cleared_keys=cleared, candles=seeded.candles, hset=seeded.changed, evicted=seeded.evicted)
            return True
        except RedisUnavailable as exc:
            if not _await_redis(writer, runtime, exc, clock=clock, sleep=sleep):
                return False


def _reseed(config: Config, source: Any, writer: Any, pairs: list[Pair], run_id: str, committed: tuple[int, datetime, str]) -> None:
    """Redis quay lại với snapshot cũ hoặc mất sạch dữ liệu: xóa db, nạp lại cửa sổ nến tại GIỜ ẢO của mốc đã ghi gần nhất
    (cửa sổ mỗi List chỉ phụ thuộc 1200 nến gần nhất nên đây đúng là trạng thái sau mốc đó) và ghi lại checkpoint của mốc đó."""
    seq, release, digest = committed
    log_event(LOGGER, logging.WARNING, "REDIS_STATE_LOST", last_tick=seq, sim_time=f"{release:%Y-%m-%d %H:%M:%S}",
              action="clearing the db and re-seeding the window at the last written tick")
    writer.clean()
    writer.seed(source.seed(pairs, release, config.redis.bars_per_snapshot), release)
    writer.begin_run(run_id, release, tick_seq=seq, digest=digest)
    log_event(LOGGER, logging.INFO, "REDIS_RESEEDED", last_tick=seq)


def _write_tick(
    config: Config, source: Any, writer: Any, pacer: Any, runtime: Any, pairs: list[Pair], tick: Tick, *,
    run_id: str, digest: str, committed: tuple[int, datetime, str],
    clock: Callable[[], float], sleep: Callable[[float], None],
) -> WriteStats | None:
    """Ghi một mốc; Redis mất kết nối thì chờ rồi ghi lại đúng mốc đó. None = bị yêu cầu dừng trong lúc chờ."""
    try:
        return writer.write_tick(tick, run_id=run_id, digest=digest)
    except RedisUnavailable as exc:
        error: Exception = exc
    while True:
        if not _await_redis(writer, runtime, error, clock=clock, sleep=sleep):
            return None
        try:
            state = writer.read_state()
            seq = int(state.get("tick_seq") or -1) if state.get("run_id") == run_id else -1
            if seq not in (tick.seq - 1, tick.seq):             # snapshot cũ hoặc mất dữ liệu: dựng lại trạng thái trước mốc này
                _reseed(config, source, writer, pairs, run_id, committed)
            pacer.begin(tick.release)                           # đặt lại gốc thời gian: sau khi chờ không phát bù dồn dập
            if seq == tick.seq:                                 # giao dịch lần trước đã được Redis áp dụng dù phản hồi bị mất
                return WriteStats(len(tick.candles))
            return writer.write_tick(tick, run_id=run_id, digest=digest)
        except RedisUnavailable as exc:
            error = exc


def _finish(writer: Any, runtime: Any, *, clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
    """Đánh dấu `finished`; mất kết nối ngay lúc này thì chờ rồi đánh dấu lại (lệnh ghi lặp được)."""
    while True:
        try:
            writer.mark_finished()
            return
        except RedisUnavailable as exc:
            if not _await_redis(writer, runtime, exc, clock=clock, sleep=sleep):
                return


def play(
    config: Config, source: Any, writer: Any, pacer: Any, runtime: Any, *,
    clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
) -> RunSummary:
    replay = config.replay
    pairs = source.pairs(replay.symbols, replay.timeframes)
    summary = RunSummary(run_id=uuid.uuid4().hex[:12])
    log_event(LOGGER, logging.INFO, "RUN_START", run_id=summary.run_id, pairs=len(pairs), start=f"{replay.start:%Y-%m-%d %H:%M:%S}",
              end=f"{replay.end:%Y-%m-%d %H:%M:%S}", pacing=config.pacing.mode)
    runtime.write_state(status="running", run_id=summary.run_id, force=True)
    if runtime.stop_requested() or not _start(config, source, writer, runtime, pairs, summary.run_id, clock=clock, sleep=sleep):
        summary.status = "stopped"
        return summary

    digest, begun, last_log = schedule.ScheduleDigest(), False, clock()
    committed: tuple[int, datetime, str] = (0, replay.start, "")     # (seq, giờ ảo, mã băm) của mốc đã ghi gần nhất
    for tick in _ticks(config, source, pairs):
        running = digest.update(tick)
        if not begun:               # đồng hồ pacing chỉ bắt đầu khi dữ liệu của mốc đầu tiên cần ghi đã sẵn sàng
            pacer.begin(replay.start)
            begun = True
        lag = None if runtime.stop_requested() else pacer.wait(tick.release, runtime.stop_requested)
        if lag is None:
            summary.status = "stopped"
            break
        stats = _write_tick(config, source, writer, pacer, runtime, pairs, tick, run_id=summary.run_id, digest=running,
                            committed=committed, clock=clock, sleep=sleep)
        if stats is None:
            summary.status = "stopped"
            break
        committed = (tick.seq, tick.release, running)
        summary.ticks += 1
        summary.candles += stats.candles
        summary.changed += stats.changed
        summary.evicted += stats.evicted
        summary.max_lag_seconds = max(summary.max_lag_seconds, lag)
        summary.last_sim_time = f"{tick.release:%Y-%m-%d %H:%M:%S}"
        runtime.write_state(tick_seq=tick.seq, sim_time=summary.last_sim_time, ticks=summary.ticks, candles=summary.candles)
        if clock() - last_log >= _PROGRESS_LOG_SECONDS:
            last_log = clock()
            log_event(LOGGER, logging.INFO, "PROGRESS", tick_seq=tick.seq, sim_time=summary.last_sim_time,
                      ticks=summary.ticks, candles=summary.candles, max_lag_s=round(summary.max_lag_seconds, 3))
    # Chạy xong: mã băm toàn lịch. Bị dừng: mã băm tại mốc đã ghi (mốc vừa đọc nhưng chưa ghi không được tính).
    summary.digest = digest.hexdigest() if summary.status == "finished" else committed[2]
    if summary.status == "finished":
        _finish(writer, runtime, clock=clock, sleep=sleep)
    log_event(LOGGER, logging.INFO, "RUN_FINISHED" if summary.status == "finished" else "RUN_STOPPED",
              run_id=summary.run_id, ticks=summary.ticks, candles=summary.candles, hset=summary.changed,
              max_lag_s=round(summary.max_lag_seconds, 3), digest=summary.digest[:16])
    return summary


def run_replay(config: Config) -> RunSummary:
    """Chạy thật (luôn là lần chạy mới): khóa một-instance, dựng nguồn SQL và writer Redis, chạy `play`, ghi state và manifest."""
    runtime = Runtime(config.runtime_dir)
    pacer = make_pacer(config.pacing.mode, config.pacing.delay_seconds, config.pacing.speed)
    with instance_lock(config.runtime_dir):
        runtime.install_signal_handlers()
        runtime.clear_stop_request()
        runtime.write_state(status="starting", force=True)
        try:
            with SqlSource(config.sql) as source, RedisWriter(config.redis) as writer:
                summary = play(config, source, writer, pacer, runtime)
        except Exception as exc:
            log_event(LOGGER, logging.ERROR, "RUN_FAILED", error_type=type(exc).__name__, error=exc)
            runtime.write_state(status="failed", error_type=type(exc).__name__, force=True)
            raise
        runtime.write_state(status=summary.status, ticks=summary.ticks, candles=summary.candles, force=True)
        runtime.write_manifest({
            "run_id": summary.run_id, "status": summary.status, "config": config.summary(),
            "ticks": summary.ticks, "candles": summary.candles, "schedule_digest": summary.digest,
            "last_sim_time": summary.last_sim_time,
        })
        return summary


def plan_replay(config: Config, *, on_tick: Callable[[Tick], None] | None = None) -> PlanResult:
    """Chạy khô: dựng lịch phát từ SQL (không đụng Redis), thống kê và băm; `on_tick` nhận từng mốc để in/xuất."""
    with SqlSource(config.sql) as source:
        pairs = source.pairs(config.replay.symbols, config.replay.timeframes)
        stats = schedule.PlanStats()
        for tick in _ticks(config, source, pairs):
            stats.add(tick)
            if on_tick is not None:
                on_tick(tick)
    return PlanResult(len(pairs), stats)
