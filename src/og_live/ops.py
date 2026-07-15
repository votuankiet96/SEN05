"""Operator commands shared by OG Live mechanisms."""

from __future__ import annotations

import argparse

from og_live.common import audit
from og_live.pubsub_mechanism import settings as pubsub_settings
from og_live.stream_mechanism import settings as stream_settings


def print_audit(
    *,
    mechanism: str,
    limit: int,
    strategy: str | None,
    symbol: str | None,
    timeframe: str | None,
    snapshot_version: str | None,
    stages: list[str],
) -> int:
    """Print one or both mechanism audit logs in a readable table."""
    filters = _filters(strategy=strategy, symbol=symbol, timeframe=timeframe, snapshot_version=snapshot_version)
    if mechanism in {"stream", "both"}:
        events = audit.read_events(
            stream_settings.audit_file_path(),
            limit=limit,
            strategy=strategy,
            symbol=symbol,
            timeframe=timeframe,
            snapshot_version=snapshot_version,
            stages=stages,
        )
        audit.print_audit_table(
            title="OG Live Stream - audit log",
            path=stream_settings.audit_file_path(),
            events=events,
            filters=filters,
        )
    if mechanism == "both":
        print()
    if mechanism in {"pubsub", "both"}:
        events = audit.read_events(
            pubsub_settings.audit_file_path(),
            limit=limit,
            strategy=strategy,
            symbol=symbol,
            timeframe=timeframe,
            snapshot_version=snapshot_version,
            stages=stages,
        )
        audit.print_audit_table(
            title="OG Live Pub/Sub - audit log",
            path=pubsub_settings.audit_file_path(),
            events=events,
            filters=filters,
        )
    return 0


def print_compare(
    *,
    limit: int,
    strategy: str | None,
    symbol: str | None,
    timeframe: str | None,
    snapshot_version: str | None,
) -> int:
    """Compare Stream and Pub/Sub audit logs by strategy/symbol/timeframe/snapshot."""
    filters = _filters(strategy=strategy, symbol=symbol, timeframe=timeframe, snapshot_version=snapshot_version)
    read_limit = max(limit * 20, 200)
    stream_events = audit.read_events(
        stream_settings.audit_file_path(),
        limit=read_limit,
        strategy=strategy,
        symbol=symbol,
        timeframe=timeframe,
        snapshot_version=snapshot_version,
    )
    pubsub_events = audit.read_events(
        pubsub_settings.audit_file_path(),
        limit=read_limit,
        strategy=strategy,
        symbol=symbol,
        timeframe=timeframe,
        snapshot_version=snapshot_version,
    )
    audit.print_comparison_table(
        stream_path=stream_settings.audit_file_path(),
        pubsub_path=pubsub_settings.audit_file_path(),
        stream_events=stream_events,
        pubsub_events=pubsub_events,
        filters=filters,
        limit=limit,
    )
    return 0


def _filters(
    *,
    strategy: str | None,
    symbol: str | None,
    timeframe: str | None,
    snapshot_version: str | None,
) -> dict[str, str | None]:
    return {
        "strategy": strategy,
        "symbol": symbol.upper() if symbol else None,
        "timeframe": timeframe.upper() if timeframe else None,
        "snapshot_version": snapshot_version,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OG Live shared operator commands.")
    sub = parser.add_subparsers(dest="command", required=True)

    audit_cmd = sub.add_parser("audit", help="Print Stream/PubSub audit events as a table.")
    audit_cmd.add_argument("--mechanism", choices=("stream", "pubsub", "both"), default="both")
    audit_cmd.add_argument("--limit", type=int, default=40)
    audit_cmd.add_argument("--strategy")
    audit_cmd.add_argument("--symbol")
    audit_cmd.add_argument("--timeframe")
    audit_cmd.add_argument("--snapshot-version")
    audit_cmd.add_argument(
        "--stage",
        action="append",
        default=[],
        help="Filter by stage. May be passed multiple times.",
    )

    compare_cmd = sub.add_parser("compare", help="Compare Stream and Pub/Sub audit events by snapshot.")
    compare_cmd.add_argument("--limit", type=int, default=30)
    compare_cmd.add_argument("--strategy")
    compare_cmd.add_argument("--symbol")
    compare_cmd.add_argument("--timeframe")
    compare_cmd.add_argument("--snapshot-version")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "audit":
        return print_audit(
            mechanism=args.mechanism,
            limit=args.limit,
            strategy=args.strategy,
            symbol=args.symbol,
            timeframe=args.timeframe,
            snapshot_version=args.snapshot_version,
            stages=args.stage,
        )
    if args.command == "compare":
        return print_compare(
            limit=args.limit,
            strategy=args.strategy,
            symbol=args.symbol,
            timeframe=args.timeframe,
            snapshot_version=args.snapshot_version,
        )
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
