"""Small operational CLI helpers for the OG launcher."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any

from og_core import config
from og_core.strategies.registry import STRATEGIES


def print_config() -> None:
    """Print the main OG universe/config in a terminal-friendly format."""
    print("OG Core config")
    print(f"default_symbol: {config.DEFAULT_SYMBOL}")
    print(f"default_tf: {config.DEFAULT_TF}")
    print(f"default_bars: {config.N_BARS}")
    print()
    print(f"symbols ({len(config.SYMBOLS)}):")
    for symbol, meta in config.SYMBOLS.items():
        print(f"  {symbol:8} id={meta['symbol_id']:<3} asset={meta['asset_type']}")
    print()
    print(f"timeframes ({len(config.TF_MINUTES)}):")
    for tf, minutes in config.TF_MINUTES.items():
        print(f"  {tf:4} {minutes:>6} minutes")


def print_strategies(*, as_json: bool = False) -> None:
    """Print registered strategy specs and configurable parameters."""
    rows: list[dict[str, Any]] = []
    for key, spec in STRATEGIES.items():
        rows.append(
            {
                "key": key,
                "label": spec.label,
                "defaults": spec.default_params,
                "fields": spec.param_fields,
            }
        )
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True))
        return

    print("OG strategies")
    for row in rows:
        print(f"\n[{row['key']}] {row['label']}")
        defaults = row["defaults"]
        if defaults:
            print("  defaults:")
            for name, value in defaults.items():
                print(f"    {name}={value}")
        fields = row["fields"]
        if fields:
            print("  params:")
            for field in fields:
                name = field.get("name") or field.get("key")
                kind = field.get("type")
                label = field.get("label", name)
                print(f"    {name} ({kind}) - {label}")


def print_services() -> int:
    """Print user-systemd status for the production services when available."""
    cmd = [
        "systemctl",
        "--user",
        "show",
        "og-live.service",
        "og-dashboard.service",
        "-p",
        "Id",
        "-p",
        "ActiveState",
        "-p",
        "SubState",
        "-p",
        "MainPID",
        "-p",
        "NRestarts",
        "--no-pager",
    ]
    try:
        completed = subprocess.run(cmd, check=False, text=True, capture_output=True)
    except FileNotFoundError:
        print("systemctl is not available on this host.")
        return 1

    if completed.stdout:
        print(completed.stdout.strip())
    if completed.stderr:
        print(completed.stderr.strip(), file=sys.stderr)
    return completed.returncode


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OG operational helper commands.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("config", help="Print core symbols/timeframes/defaults.")
    strategies = sub.add_parser("strategies", help="Print registered strategies and parameters.")
    strategies.add_argument("--json", action="store_true", help="Print JSON.")
    sub.add_parser("services", help="Print user-systemd service status.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "config":
        print_config()
        return 0
    if args.command == "strategies":
        print_strategies(as_json=args.json)
        return 0
    if args.command == "services":
        return print_services()
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
