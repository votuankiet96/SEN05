"""CLI của DPS. Chỉ phân tích tham số rồi gọi các module khác.

  python -B src plan   [--show N] [--export FILE]   chạy khô: in lịch phát + mã băm, không đụng Redis
  python -B src run                                 chạy MỚI: xóa db đích, seed rồi replay lên Redis đích
  python -B src status                              trạng thái tiến trình và checkpoint
  python -B src stop                                yêu cầu dừng sạch (lần `run` sau là chạy mới từ đầu)
  python -B src clean  --yes                        xóa db đích (chỉ db nằm trong allowed_dbs)
(chạy từ thư mục dps/; `python src` thực thi src/__main__.py và đặt src lên sys.path nên các module import nhau trực tiếp)
Tùy chọn của plan/run: --start --end --symbols --timeframes và --delay hoặc --speed (ghi đè config.yaml).
Tùy chọn chung: --config PATH.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys

from configuration import Config, load_config, with_overrides
from player import plan_replay, run_replay
from redis_writer import RedisWriter
from runtime import DpsError, Tick, estimate_seconds, request_stop, service_status, setup_logging

LOGGER = logging.getLogger("dps")
_OVERRIDES = ("start", "end", "symbols", "timeframes", "delay", "speed")


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="path to config.yaml (default: dps/config.yaml)")
    replay = argparse.ArgumentParser(add_help=False)
    replay.add_argument("--start", help="T0, UTC, e.g. '2026-09-30 13:00:00'")
    replay.add_argument("--end", help="last simulated instant, UTC")
    replay.add_argument("--symbols", help="comma separated, e.g. GOLD,DE40")
    replay.add_argument("--timeframes", help="comma separated, e.g. M5,M15")
    pace = replay.add_mutually_exclusive_group()
    pace.add_argument("--delay", type=float, help="real seconds between ticks (0 = maximum speed)")
    pace.add_argument("--speed", type=float, help="simulated seconds per real second (300 = one M5 bar per second)")
    parser = argparse.ArgumentParser(prog="dps", description="Data Provider Simulator: replay SQL candles onto Redis as if live.")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", parents=[common, replay], help="dry run: print the release schedule, no Redis")
    plan.add_argument("--show", type=int, default=0, metavar="N", help="print the first N ticks")
    plan.add_argument("--export", metavar="FILE", help="write the whole schedule as CSV")
    sub.add_parser("run", parents=[common, replay], help="start a NEW run: clear the target Redis db, seed it and replay onto it")
    sub.add_parser("status", parents=[common], help="show process state and the Redis checkpoint")
    stop = sub.add_parser("stop", parents=[common], help="request a clean stop")
    stop.add_argument("--wait", type=int, default=60, help="seconds to wait for the process to exit")
    clean = sub.add_parser("clean", parents=[common], help="flush the target Redis db (only a db listed in allowed_dbs)")
    clean.add_argument("--yes", action="store_true", help="really flush; without it only report")
    return parser


def _cmd_plan(args: argparse.Namespace, config: Config) -> int:
    shown = 0
    export = open(args.export, "w", newline="", encoding="utf-8") if args.export else None
    writer = csv.writer(export) if export else None
    if writer:
        writer.writerow(["seq", "release_utc", "symbol", "timeframe", "bar_open_utc"])

    def on_tick(tick: Tick) -> None:
        nonlocal shown
        if shown < args.show:
            shown += 1
            print(f"{tick.seq:>7}  {tick.release:%Y-%m-%d %H:%M}  "
                  + " + ".join(f"{c.pair.symbol}.{c.pair.timeframe}@{c.bar_time:%H:%M}" for c in tick.candles))
        if writer:
            writer.writerows([tick.seq, f"{tick.release:%Y-%m-%d %H:%M:%S}", c.pair.symbol, c.pair.timeframe, f"{c.bar_time:%Y-%m-%d %H:%M:%S}"]
                             for c in tick.candles)
    try:
        result = plan_replay(config, on_tick=on_tick)
    finally:
        if export:
            export.close()
    stats, pacing = result.stats, config.pacing
    seconds = estimate_seconds(pacing.mode, pacing.delay_seconds, pacing.speed, stats.ticks, config.replay.start, stats.last)
    print(f"pairs: {result.pairs} | ticks: {stats.ticks} | candles: {stats.candles} | max candles per tick: {stats.max_per_tick}")
    print(f"first tick: {stats.first} | last tick: {stats.last}")
    print(f"schedule digest: {stats.hexdigest}")
    print(f"estimated run time at the current pacing ({pacing.mode}): {seconds:,.1f} s = {seconds / 3600:.2f} h")
    return 0


def _cmd_run(args: argparse.Namespace, config: Config) -> int:
    summary = run_replay(config)
    print(f"{summary.status}: run_id={summary.run_id} ticks={summary.ticks} candles={summary.candles} hset={summary.changed} "
          f"evicted={summary.evicted} max_lag={summary.max_lag_seconds:.3f}s last_sim_time={summary.last_sim_time or '-'}")
    print(f"schedule digest: {summary.digest}")
    return 0


def _cmd_status(_args: argparse.Namespace, config: Config) -> int:
    status = service_status(config.runtime_dir)
    keys = ["status", "pid", "process_alive", "healthy", "heartbeat_age_seconds", "tick_seq", "sim_time", "ticks", "candles", "error_type"]
    if status.get("status") == "waiting_redis":         # đang chờ Redis quay lại: cho biết từ lúc nào và lỗi gì
        keys += ["waiting_since", "redis_retries", "redis_error"]
    print("process:", {key: status.get(key) for key in keys})
    try:
        with RedisWriter(config.redis) as writer:
            print(f"redis db {config.redis.db}: keys={writer.size()} clock={writer.read_clock()} checkpoint={writer.read_state()}")
    except Exception as exc:  # noqa: BLE001 - status phải luôn in được phần còn lại
        print(f"redis: unreachable ({exc if isinstance(exc, DpsError) else type(exc).__name__})")
    return 0


def _cmd_stop(args: argparse.Namespace, config: Config) -> int:
    result = request_stop(config.runtime_dir, wait_seconds=args.wait)
    print(f"stop requested; process {'stopped' if result['stopped'] else 'still running'} (pid {result['pid']})")
    return 0 if result["stopped"] else 1


def _cmd_clean(args: argparse.Namespace, config: Config) -> int:
    with RedisWriter(config.redis) as writer:
        if not args.yes:
            print(f"db {config.redis.db} holds {writer.size()} keys; rerun with --yes to flush it")
            return 0
        print(f"flushed db {config.redis.db}: {writer.clean()} keys")
    return 0


_HANDLERS = {"plan": _cmd_plan, "run": _cmd_run, "status": _cmd_status, "stop": _cmd_stop, "clean": _cmd_clean}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = with_overrides(load_config(args.config), **{name: getattr(args, name, None) for name in _OVERRIDES})
        setup_logging(config.runtime_dir)
        return _HANDLERS[args.command](args, config)
    except DpsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - chi tiết đã nằm trong log; không in message vì có thể chứa thông tin kết nối
        LOGGER.exception("UNEXPECTED_FAILURE")
        print(f"error: unexpected {type(exc).__name__}; see runtime/logs/dps.log", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
