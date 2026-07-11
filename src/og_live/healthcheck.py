"""Operational healthcheck for the OG live Redis pipeline."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import redis

from og_live import settings
from og_live.sinks.redis_signals import get_client

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
        client = get_client()
        client.ping()
        checks.append(CheckResult("redis_ping", OK, "Redis connection is alive"))
    except redis.RedisError as exc:
        checks.append(CheckResult("redis_ping", FAIL, f"Redis connection failed: {exc}"))
        return _report(now, checks, fail_on_warn=args.fail_on_warn)

    expected_pairs = _expected_pairs()
    checks.append(_check_input_stream(client, now, args))
    checks.append(_check_consumer_group(client, args))
    checks.append(_check_snapshot_coverage(client, expected_pairs, args))
    checks.append(_check_signal_stream(client))
    checks.append(_check_local_state(args))
    return _report(now, checks, fail_on_warn=args.fail_on_warn)


def _check_input_stream(client: redis.Redis, now: datetime, args: argparse.Namespace) -> CheckResult:
    stream = settings.CANDLE_SNAPSHOT_STREAM
    try:
        length = client.xlen(stream)
        latest = client.xrevrange(stream, count=1)
    except redis.RedisError as exc:
        return CheckResult("input_stream", FAIL, f"Cannot read {stream}: {exc}")

    if length <= 0 or not latest:
        return CheckResult("input_stream", FAIL, f"{stream} is empty", {"stream": stream, "length": length})

    entry_id, fields = latest[0]
    age_seconds = max(0.0, (now - _stream_id_time(entry_id)).total_seconds())
    status = OK
    message = f"{stream} latest entry age is {age_seconds:.1f}s"
    if age_seconds > args.max_snapshot_age_seconds:
        status = FAIL
        message = f"{stream} latest entry is stale: {age_seconds:.1f}s"

    bars_summary = _summarize_bars(fields.get("bars"))
    return CheckResult(
        "input_stream",
        status,
        message,
        {
            "stream": stream,
            "length": length,
            "latest_entry_id": entry_id,
            "latest_symbol": fields.get("tv_symbol"),
            "latest_timeframe": fields.get("tf_code"),
            "latest_bars": bars_summary,
        },
    )


def _check_consumer_group(client: redis.Redis, args: argparse.Namespace) -> CheckResult:
    stream = settings.CANDLE_SNAPSHOT_STREAM
    try:
        groups = client.xinfo_groups(stream)
    except redis.RedisError as exc:
        return CheckResult("consumer_group", FAIL, f"Cannot read consumer groups for {stream}: {exc}")

    target = next((group for group in groups if group.get("name") == settings.CONSUMER_GROUP), None)
    if target is None:
        return CheckResult(
            "consumer_group",
            FAIL,
            f"Consumer group {settings.CONSUMER_GROUP} is missing",
            {"groups": [_compact_group(group) for group in groups]},
        )

    lag = target.get("lag")
    pending = int(target.get("pending") or 0)
    consumers = int(target.get("consumers") or 0)
    failures: list[str] = []
    if lag is not None and int(lag) > args.max_lag:
        failures.append(f"lag={lag} > {args.max_lag}")
    if pending > args.max_pending:
        failures.append(f"pending={pending} > {args.max_pending}")
    if consumers <= 0:
        failures.append("no active consumers")

    status = FAIL if failures else OK
    message = "; ".join(failures) if failures else "Consumer group is caught up"
    return CheckResult(
        "consumer_group",
        status,
        message,
        {
            "target": _compact_group(target),
            "other_groups": [_compact_group(group) for group in groups if group.get("name") != settings.CONSUMER_GROUP],
        },
    )


def _check_snapshot_coverage(
    client: redis.Redis,
    expected_pairs: set[tuple[str, str]],
    args: argparse.Namespace,
) -> CheckResult:
    stream = settings.CANDLE_SNAPSHOT_STREAM
    if not expected_pairs:
        return CheckResult("snapshot_coverage", FAIL, "No watched symbol/timeframe pairs configured")

    try:
        entries = client.xrevrange(stream, count=args.coverage_count)
    except redis.RedisError as exc:
        return CheckResult("snapshot_coverage", FAIL, f"Cannot scan {stream}: {exc}")

    newest_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    malformed = 0
    for entry_id, fields in entries:
        symbol = str(fields.get("tv_symbol") or "").strip().upper()
        tf = str(fields.get("tf_code") or "").strip().upper()
        pair = (symbol, tf)
        if pair not in expected_pairs or pair in newest_by_pair:
            continue
        try:
            bars = json.loads(fields.get("bars") or "[]")
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(bars, list):
            malformed += 1
            continue
        newest_by_pair[pair] = {
            "entry_id": entry_id,
            "bars": len(bars),
            "first_bar_time": (bars[0] if bars else {}).get("bar_time"),
            "last_bar_time": (bars[-1] if bars else {}).get("bar_time"),
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
            "coverage_count": args.coverage_count,
            "missing_pairs": missing[:20],
            "pairs_below_min_bars": short[:20],
            "pairs_not_strict_bars": non_strict[:20],
        },
    )


def _check_signal_stream(client: redis.Redis) -> CheckResult:
    stream = settings.signal_stream_key("combo")
    try:
        length = client.xlen(stream)
        latest = client.xrevrange(stream, count=1)
    except redis.RedisError as exc:
        return CheckResult("signal_stream", FAIL, f"Cannot read {stream}: {exc}")

    if length <= 0:
        return CheckResult("signal_stream", WARN, f"{stream} has no signals yet", {"stream": stream, "length": length})

    latest_id, fields = latest[0]
    missing_fields = [field for field in ("signal_id", "symbol", "timeframe", "bar_time", "side") if not fields.get(field)]
    status = FAIL if missing_fields else OK
    message = f"Latest signal is {fields.get('symbol')} {fields.get('timeframe')} {fields.get('side')}"
    if missing_fields:
        message = f"Latest signal is missing fields: {missing_fields}"
    return CheckResult(
        "signal_stream",
        status,
        message,
        {
            "stream": stream,
            "length": length,
            "latest_entry_id": latest_id,
            "latest_signal_id": fields.get("signal_id"),
            "latest_symbol": fields.get("symbol"),
            "latest_timeframe": fields.get("timeframe"),
            "latest_side": fields.get("side"),
            "latest_bar_time": fields.get("bar_time"),
            "latest_produced_at": fields.get("produced_at"),
        },
    )


def _check_local_state(args: argparse.Namespace) -> CheckResult:
    runtime = settings.runtime_dir()
    outbox_path = runtime / "delivery_outbox.json"
    state_path = runtime / "state.json"

    try:
        outbox = _read_json(outbox_path, default=[])
        state = _read_json(state_path, default={})
    except ValueError as exc:
        return CheckResult("local_state", FAIL, str(exc))

    pending = len(outbox) if isinstance(outbox, list) else 0
    delivered_seen = len(state) if isinstance(state, dict) else 0
    if pending > args.max_outbox_pending:
        return CheckResult(
            "local_state",
            FAIL,
            f"Delivery outbox has {pending} pending signal(s)",
            {"outbox_path": str(outbox_path), "pending": pending, "delivered_seen": delivered_seen},
        )

    return CheckResult(
        "local_state",
        OK,
        "Local state is readable and outbox is clear",
        {"outbox_path": str(outbox_path), "pending": pending, "state_path": str(state_path), "delivered_seen": delivered_seen},
    )


def _expected_pairs() -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for item in settings.load_watched_items():
        for symbol in item.symbols:
            pairs.add((str(symbol).upper(), item.tf))
    return pairs


def _stream_id_time(stream_id: str) -> datetime:
    milliseconds = int(str(stream_id).split("-", 1)[0])
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)


def _summarize_bars(raw_bars: str | None) -> dict[str, Any]:
    try:
        bars = json.loads(raw_bars or "[]")
    except json.JSONDecodeError:
        return {"valid": False, "error": "invalid_json"}
    if not isinstance(bars, list):
        return {"valid": False, "error": "not_array"}
    return {
        "valid": True,
        "count": len(bars),
        "first_bar_time": (bars[0] if bars else {}).get("bar_time"),
        "last_bar_time": (bars[-1] if bars else {}).get("bar_time"),
    }


def _read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read {path}: {exc}") from exc


def _compact_group(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": group.get("name"),
        "consumers": group.get("consumers"),
        "pending": group.get("pending"),
        "lag": group.get("lag"),
        "last_delivered_id": group.get("last-delivered-id"),
    }


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
    parser = argparse.ArgumentParser(description="Check OG live Redis pipeline health.")
    parser.add_argument("--json", action="store_true", help="Print a JSON report.")
    parser.add_argument("--compact-json", action="store_true", help="Print JSON on one line for journal-friendly logs.")
    parser.add_argument("--fail-on-warn", action="store_true", help="Return exit code 1 when any warning is present.")
    parser.add_argument("--coverage-count", type=int, default=2000, help="Recent candle_snapshot entries to scan.")
    parser.add_argument("--max-lag", type=int, default=0, help="Maximum allowed og_live consumer group lag.")
    parser.add_argument("--max-pending", type=int, default=0, help="Maximum allowed og_live pending entries.")
    parser.add_argument("--max-snapshot-age-seconds", type=int, default=15 * 60, help="Maximum latest stream-entry age.")
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
