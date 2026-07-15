"""Reader-friendly operational views for the OG Live Pub/Sub mechanism."""

from __future__ import annotations

import argparse
from typing import Any

from og_live.pubsub_mechanism import healthcheck, settings


def print_health(*, fail_on_warn: bool = False) -> int:
    """Print a concise Pub/Sub mechanism health summary for operators."""
    report = healthcheck.run_healthcheck(_health_args(fail_on_warn=fail_on_warn))
    checks = _checks_by_name(report)

    print("OG Live Pub/Sub - health")
    print(f"Overall: {report['status'].upper()}   checked_at={report['checked_at']}")
    _print_watchlist()
    _print_redis_route()
    print()

    _print_health_line("Redis", checks.get("redis_ping"))
    _print_channel_summary(checks.get("pubsub_channel"))
    _print_coverage_summary(checks.get("snapshot_coverage"))
    _print_signal_summary(checks.get("signal_stream"))
    _print_state_summary(checks.get("local_state"))

    warnings = [check for check in report["checks"] if check["status"] == healthcheck.WARN]
    failures = [check for check in report["checks"] if check["status"] == healthcheck.FAIL]
    if warnings or failures:
        print()
        print("Attention:")
        for check in failures:
            print(f"  FAIL - {check['name']}: {check['message']}")
        for check in warnings:
            print(f"  WARN - {check['name']}: {check['message']}")

    return 1 if report["exit_status"] == healthcheck.FAIL else 0


def print_inspect(*, fail_on_warn: bool = False) -> int:
    """Print a structured Pub/Sub input/output inspection."""
    report = healthcheck.run_healthcheck(_health_args(fail_on_warn=fail_on_warn))
    checks = _checks_by_name(report)

    print("OG Live Pub/Sub - channel/state and latest signal")
    print(f"Overall: {report['status'].upper()}   checked_at={report['checked_at']}")
    _print_watchlist()
    _print_redis_route()
    print()
    _print_channel_details(checks.get("pubsub_channel"))
    print()
    _print_coverage_details(checks.get("snapshot_coverage"))
    print()
    _print_signal_details(checks.get("signal_stream"))
    print()
    _print_state_details(checks.get("local_state"))

    return 1 if report["exit_status"] == healthcheck.FAIL else 0


def _health_args(*, fail_on_warn: bool) -> argparse.Namespace:
    return argparse.Namespace(
        fail_on_warn=fail_on_warn,
        min_subscribers=1,
        min_bars=300,
        strict_bars=500,
        max_outbox_pending=0,
    )


def _checks_by_name(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(check["name"]): check for check in report.get("checks", [])}


def _status(check: dict[str, Any] | None) -> str:
    return str((check or {}).get("status", "unknown")).upper()


def _message(check: dict[str, Any] | None) -> str:
    return str((check or {}).get("message", "not available"))


def _details(check: dict[str, Any] | None) -> dict[str, Any]:
    details = (check or {}).get("details", {})
    return details if isinstance(details, dict) else {}


def _print_health_line(label: str, check: dict[str, Any] | None) -> None:
    d = _details(check)
    suffix = ""
    if label == "Redis" and d:
        suffix = f" (input_db={d.get('input_db', 'n/a')}, output_db={d.get('output_db', 'n/a')})"
    print(f"{label:18} {_status(check):5} {_message(check)}{suffix}")


def _print_channel_summary(check: dict[str, Any] | None) -> None:
    d = _details(check)
    print(
        f"{'Pub/Sub channel':18} {_status(check):5} "
        f"channel={d.get('channel', 'n/a')}, subscribers={d.get('subscribers', 'n/a')}"
    )


def _print_coverage_summary(check: dict[str, Any] | None) -> None:
    d = _details(check)
    print(
        f"{'State coverage':18} {_status(check):5} "
        f"{d.get('covered_pairs', 'n/a')}/{d.get('expected_pairs', 'n/a')} pairs covered"
    )


def _print_signal_summary(check: dict[str, Any] | None) -> None:
    d = _details(check)
    latest = _pair(d.get("latest_symbol"), d.get("latest_timeframe"))
    side = d.get("latest_side", "n/a")
    bar_time = d.get("latest_bar_time", "n/a")
    stream = d.get("stream", "n/a")
    print(f"{'Signal output':18} {_status(check):5} latest={latest} {side}, bar={bar_time}, stream={stream}")


def _print_state_summary(check: dict[str, Any] | None) -> None:
    d = _details(check)
    print(
        f"{'Local delivery':18} {_status(check):5} "
        f"outbox_pending={d.get('pending', 'n/a')}, delivered_seen={d.get('delivered_seen', 'n/a')}, "
        f"processed_events={d.get('processed_seen', 'n/a')}"
    )


def _print_channel_details(check: dict[str, Any] | None) -> None:
    d = _details(check)
    print(f"Pub/Sub channel: {_status(check)}")
    print(f"  channel:       {d.get('channel', 'n/a')}")
    print(f"  enabled:       {d.get('enabled', 'n/a')}")
    print(f"  subscribers:   {d.get('subscribers', 'n/a')}")
    print(f"  note:          {_message(check)}")


def _print_coverage_details(check: dict[str, Any] | None) -> None:
    d = _details(check)
    print(f"State coverage: {_status(check)}")
    print(f"  covered pairs:   {d.get('covered_pairs', 'n/a')}/{d.get('expected_pairs', 'n/a')}")
    print(f"  state prefix:    {d.get('state_prefix', 'n/a')}")
    _print_pair_list("missing pairs", d.get("missing_pairs"))
    _print_pair_list("below min bars", d.get("pairs_below_min_bars"))
    _print_pair_list("not exactly 500", d.get("pairs_not_strict_bars"))
    print(f"  note:            {_message(check)}")


def _print_signal_details(check: dict[str, Any] | None) -> None:
    d = _details(check)
    print(f"Signal output: {_status(check)}")
    print(f"  output db:       {settings.OUTPUT_REDIS_DB}")
    print(f"  output mode:     {d.get('output_mode', 'n/a')}")
    print(f"  streams checked: {d.get('streams_checked', 'n/a')}")
    print(f"  latest stream:   {d.get('stream', 'n/a')}")
    print(f"  stream length:   {d.get('length', 'n/a')}")
    print(f"  latest entry:    {d.get('latest_entry_id', 'n/a')}")
    print(f"  signal id:       {d.get('latest_signal_id', 'n/a')}")
    print(f"  latest signal:   {_pair(d.get('latest_symbol'), d.get('latest_timeframe'))} {d.get('latest_side', 'n/a')}")
    print(f"  signal bar:      {d.get('latest_bar_time', 'n/a')}")
    print(f"  produced at:     {d.get('latest_produced_at', 'n/a')}")


def _print_state_details(check: dict[str, Any] | None) -> None:
    d = _details(check)
    print(f"Local delivery state: {_status(check)}")
    print(f"  pending outbox:  {d.get('pending', 'n/a')}")
    print(f"  delivered seen:  {d.get('delivered_seen', 'n/a')}")
    print(f"  processed events:{d.get('processed_seen', 'n/a')}")
    print(f"  outbox path:     {d.get('outbox_path', 'n/a')}")
    print(f"  state path:      {d.get('state_path', 'n/a')}")
    print(f"  processed path:  {d.get('processed_path', 'n/a')}")


def _print_pair_list(label: str, items: object) -> None:
    if not isinstance(items, list) or not items:
        print(f"  {label:15} none")
        return
    print(f"  {label}:")
    for item in items[:10]:
        if isinstance(item, list | tuple):
            print("    " + " ".join(str(part) for part in item))
        else:
            print(f"    {item}")


def _pair(symbol: object, timeframe: object) -> str:
    left = str(symbol or "n/a")
    right = str(timeframe or "n/a")
    return f"{left} {right}"


def _print_watchlist() -> None:
    summary = settings.watched_summary()
    print(
        "Watchlist: "
        f"{', '.join(summary['strategies'])} | "
        f"{', '.join(summary['timeframes'])} | "
        f"{summary['pairs']} pair(s)"
    )
    print(f"Symbols:   {', '.join(summary['symbols'])}")


def _print_redis_route() -> None:
    print(
        "Redis:     "
        f"state input db{settings.INPUT_REDIS_DB}; Pub/Sub channel={settings.PUBSUB_CHANNEL}; "
        f"signal output db{settings.OUTPUT_REDIS_DB}"
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OG Live Pub/Sub operator-friendly commands.")
    sub = parser.add_subparsers(dest="command", required=True)

    health = sub.add_parser("health", help="Print a concise Pub/Sub health summary.")
    health.add_argument("--fail-on-warn", action="store_true")

    inspect = sub.add_parser("inspect", help="Print Pub/Sub channel and latest signal details.")
    inspect.add_argument("--fail-on-warn", action="store_true")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "health":
        return print_health(fail_on_warn=args.fail_on_warn)
    if args.command == "inspect":
        return print_inspect(fail_on_warn=args.fail_on_warn)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())

