"""Operational healthcheck for the OG Live Pub/Sub mechanism."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import redis

from og_live.common.candle_snapshot import MalformedSnapshotError, parse_state_snapshot
from og_live.pubsub_mechanism import settings
from og_live.pubsub_mechanism.signals import get_input_client, get_output_client

OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass
class CheckResult:
    """One healthcheck result with machine-readable details."""

    name: str
    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def run_healthcheck(args: argparse.Namespace) -> dict[str, Any]:
    """Run all checks and return a JSON-serializable health report."""
    now = datetime.now(timezone.utc)
    checks: list[CheckResult] = []

    try:
        input_client = get_input_client()
        output_client = get_output_client()
        input_client.ping()
        output_client.ping()
        checks.append(
            CheckResult(
                "redis_ping",
                OK,
                "Redis Pub/Sub input/state and output connections are alive",
                {"input_db": settings.INPUT_REDIS_DB, "output_db": settings.OUTPUT_REDIS_DB},
            )
        )
    except redis.RedisError as exc:
        checks.append(CheckResult("redis_ping", FAIL, f"Redis connection failed: {exc}"))
        return _report(now, checks, fail_on_warn=args.fail_on_warn)

    expected_pairs = _expected_pairs()
    checks.append(_check_pubsub_channel(input_client, args))
    checks.append(_check_snapshot_coverage(input_client, expected_pairs, args))
    checks.append(_check_signal_stream(output_client))
    checks.append(_check_local_state(args))
    return _report(now, checks, fail_on_warn=args.fail_on_warn)


def _check_pubsub_channel(client: redis.Redis, args: argparse.Namespace) -> CheckResult:
    try:
        numsub = client.pubsub_numsub(settings.PUBSUB_CHANNEL)
    except redis.RedisError as exc:
        return CheckResult("pubsub_channel", FAIL, f"Cannot inspect Pub/Sub channel: {exc}")
    subscribers = 0
    if numsub:
        _channel, count = numsub[0]
        subscribers = int(count)
    status = OK
    message = f"{settings.PUBSUB_CHANNEL} has {subscribers} subscriber(s)"
    if settings.ENABLED and subscribers < args.min_subscribers:
        status = FAIL
        message = f"{settings.PUBSUB_CHANNEL} subscriber count {subscribers} < {args.min_subscribers}"
    if not settings.ENABLED:
        status = WARN
        message = "Pub/Sub mechanism is disabled by config"
    return CheckResult(
        "pubsub_channel",
        status,
        message,
        {
            "channel": settings.PUBSUB_CHANNEL,
            "enabled": settings.ENABLED,
            "subscribers": subscribers,
            "min_subscribers": args.min_subscribers,
        },
    )


def _check_snapshot_coverage(
    client: redis.Redis,
    expected_pairs: set[tuple[str, str]],
    args: argparse.Namespace,
) -> CheckResult:
    if not expected_pairs:
        return CheckResult("snapshot_coverage", FAIL, "No watched symbol/timeframe pairs configured")

    newest_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    malformed = 0
    for pair in sorted(expected_pairs):
        symbol, tf = pair
        state_key = settings.candle_state_key(symbol, tf)
        try:
            raw_snapshot = client.get(state_key)
        except redis.RedisError as exc:
            return CheckResult("snapshot_coverage", FAIL, f"Cannot read {state_key}: {exc}")
        if raw_snapshot is None:
            continue
        try:
            snapshot = parse_state_snapshot(raw_snapshot)
        except MalformedSnapshotError:
            malformed += 1
            continue
        if snapshot.symbol != symbol or snapshot.tf != tf:
            malformed += 1
            continue
        newest_by_pair[pair] = {
            "state_key": state_key,
            "bars": len(snapshot.bars),
            "first_bar_time": _frame_bar_time(snapshot.bars, 0),
            "last_bar_time": _frame_bar_time(snapshot.bars, -1),
            "latest_bar_time": snapshot.latest_bar_time,
        }

    missing = sorted(expected_pairs - set(newest_by_pair))
    short = sorted(
        (symbol, tf, meta["bars"])
        for (symbol, tf), meta in newest_by_pair.items()
        if int(meta["bars"]) < args.min_bars
    )
    non_strict = sorted(
        (symbol, tf, meta["bars"])
        for (symbol, tf), meta in newest_by_pair.items()
        if int(meta["bars"]) != args.strict_bars
    )

    failures: list[str] = []
    if missing:
        failures.append(f"missing_pairs={len(missing)}")
    if short:
        failures.append(f"pairs_below_min_bars={len(short)}")
    if malformed:
        failures.append(f"malformed_entries={malformed}")

    if failures:
        status = FAIL
        message = "; ".join(failures)
    elif non_strict:
        status = WARN
        message = f"{len(non_strict)} pair(s) are not exactly {args.strict_bars} bars"
    else:
        status = OK
        message = "All watched pairs are present with expected bar count"

    return CheckResult(
        "snapshot_coverage",
        status,
        message,
        {
            "expected_pairs": len(expected_pairs),
            "covered_pairs": len(newest_by_pair),
            "state_prefix": settings.CANDLE_STATE_PREFIX,
            "missing_pairs": missing[:20],
            "pairs_below_min_bars": short[:20],
            "pairs_not_strict_bars": non_strict[:20],
        },
    )


def _check_signal_stream(client: redis.Redis) -> CheckResult:
    streams = _configured_signal_streams()
    stream_lengths: dict[str, int] = {}
    latest_stream = None
    latest_id = None
    fields: dict[str, Any] = {}
    try:
        for stream in streams:
            length = client.xlen(stream)
            stream_lengths[stream] = length
            latest = client.xrevrange(stream, count=1) if length else []
            if latest and (latest_id is None or _stream_id_time(latest[0][0]) > _stream_id_time(latest_id)):
                latest_stream = stream
                latest_id, fields = latest[0]
    except redis.RedisError as exc:
        return CheckResult("signal_stream", FAIL, f"Cannot read configured Pub/Sub signal streams: {exc}")

    if not latest_stream or latest_id is None:
        return CheckResult(
            "signal_stream",
            OK,
            "Pub/Sub signal output is configured; no signals have been published yet",
            {
                "streams": streams[:20],
                "streams_checked": len(streams),
                "stream_lengths": stream_lengths,
                "output_mode": settings.SIGNAL_OUTPUT_MODE,
                "output_db": settings.OUTPUT_REDIS_DB,
            },
        )

    missing_fields = [field for field in ("signal_id", "symbol", "timeframe", "bar_time", "side") if not fields.get(field)]
    status = FAIL if missing_fields else OK
    message = f"Latest Pub/Sub signal is {fields.get('symbol')} {fields.get('timeframe')} {fields.get('side')}"
    if missing_fields:
        message = f"Latest Pub/Sub signal is missing fields: {missing_fields}"
    return CheckResult(
        "signal_stream",
        status,
        message,
        {
            "stream": latest_stream,
            "length": stream_lengths.get(latest_stream, 0),
            "streams_checked": len(streams),
            "stream_lengths": stream_lengths,
            "latest_entry_id": latest_id,
            "latest_signal_id": fields.get("signal_id"),
            "latest_symbol": fields.get("symbol"),
            "latest_timeframe": fields.get("timeframe"),
            "latest_side": fields.get("side"),
            "latest_bar_time": fields.get("bar_time"),
            "latest_produced_at": fields.get("produced_at"),
            "output_mode": settings.SIGNAL_OUTPUT_MODE,
            "output_db": settings.OUTPUT_REDIS_DB,
        },
    )


def _check_local_state(args: argparse.Namespace) -> CheckResult:
    runtime = settings.runtime_dir()
    outbox_path = runtime / "delivery_outbox.json"
    state_path = runtime / "state.json"
    processed_path = runtime / "processed_snapshots.json"

    try:
        outbox = _read_json(outbox_path, default=[])
        state = _read_json(state_path, default={})
        processed = _read_json(processed_path, default={})
    except ValueError as exc:
        return CheckResult("local_state", FAIL, str(exc))

    pending = len(outbox) if isinstance(outbox, list) else 0
    delivered_seen = len(state) if isinstance(state, dict) else 0
    processed_seen = len(processed) if isinstance(processed, dict) else 0
    if pending > args.max_outbox_pending:
        return CheckResult(
            "local_state",
            FAIL,
            f"Delivery outbox has {pending} pending signal(s)",
            {
                "outbox_path": str(outbox_path),
                "pending": pending,
                "delivered_seen": delivered_seen,
                "processed_seen": processed_seen,
            },
        )

    return CheckResult(
        "local_state",
        OK,
        "Local state is readable and outbox is clear",
        {
            "outbox_path": str(outbox_path),
            "pending": pending,
            "state_path": str(state_path),
            "processed_path": str(processed_path),
            "delivered_seen": delivered_seen,
            "processed_seen": processed_seen,
        },
    )


def _expected_pairs() -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for item in settings.load_watched_items():
        for symbol in item.symbols:
            pairs.add((str(symbol).upper(), item.tf))
    return pairs


def _configured_signal_streams() -> list[str]:
    streams: list[str] = []
    strategies = {item.strategy for item in settings.load_watched_items()}
    for strategy in sorted(strategies):
        if settings.SIGNAL_OUTPUT_MODE in {"legacy", "dual"}:
            streams.append(settings.signal_stream_key(strategy))
        if settings.SIGNAL_OUTPUT_MODE in {"routed", "dual"}:
            for item in settings.load_watched_items():
                if item.strategy != strategy:
                    continue
                for symbol in item.symbols:
                    streams.append(settings.routed_signal_stream_key(strategy, symbol, item.tf))
    return list(dict.fromkeys(streams))


def _stream_id_time(stream_id: str) -> datetime:
    milliseconds = int(str(stream_id).split("-", 1)[0])
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)


def _frame_bar_time(frame: Any, index: int) -> str | None:
    if getattr(frame, "empty", True):
        return None
    value = frame.iloc[index]["bartime"]
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read {path}: {exc}") from exc


def _report(now: datetime, checks: list[CheckResult], *, fail_on_warn: bool) -> dict[str, Any]:
    status = OK
    if any(check.status == FAIL for check in checks):
        status = FAIL
    elif any(check.status == WARN for check in checks):
        status = WARN

    exit_status = FAIL if status == FAIL or (status == WARN and fail_on_warn) else OK
    return {
        "status": status,
        "exit_status": exit_status,
        "checked_at": now.isoformat(),
        "checks": [asdict(check) for check in checks],
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check OG Live Pub/Sub mechanism health.")
    parser.add_argument("--json", action="store_true", help="Print a JSON report.")
    parser.add_argument("--compact-json", action="store_true", help="Print JSON on one line for journal-friendly logs.")
    parser.add_argument("--fail-on-warn", action="store_true", help="Return exit code 1 when any warning is present.")
    parser.add_argument("--min-subscribers", type=int, default=1, help="Minimum expected Pub/Sub subscriber count.")
    parser.add_argument("--min-bars", type=int, default=300, help="Minimum acceptable bar count per watched pair.")
    parser.add_argument("--strict-bars", type=int, default=500, help="Expected bar count per watched pair for warnings.")
    parser.add_argument("--max-outbox-pending", type=int, default=0, help="Maximum allowed local delivery outbox size.")
    return parser.parse_args(argv)


def _print_text(report: dict[str, Any]) -> None:
    print(f"STATUS {report['status']} checked_at={report['checked_at']}")
    for check in report["checks"]:
        print(f"[{check['status'].upper()}] {check['name']}: {check['message']}")
        if check.get("details"):
            print(json.dumps(check["details"], ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = run_healthcheck(args)
    if args.json:
        indent = None if args.compact_json else 2
        print(json.dumps(report, ensure_ascii=False, indent=indent, sort_keys=True))
    else:
        _print_text(report)
    return 1 if report["exit_status"] == FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

