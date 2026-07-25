"""Operational CLI helpers for the core_python launcher."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from core_python.util import config
from core_python.strategies.combo import config as combo_config
from core_python.strategies.ma_cross import config as ma_cross_config
from core_python.strategies.registry import STRATEGIES

DEFAULT_BASE_URL = "http://127.0.0.1:8516"
SERVICE_NAMES = ("og-dashboard.service",)
EXPECTED_DASHBOARD_APP = "core_python.chart.server:create_app()"


def print_config() -> None:
    """Print the active core_python operational universe in a reader-friendly format."""
    print("core_python - operation config")
    print()
    print(f"Symbol universe: {len(config.SYMBOLS)} symbols (see config.py for the full DWH-backed list).")
    print(f"Timeframes: {', '.join(config.timeframe_codes())}")
    print()
    print("Strategies:")
    for key, spec in STRATEGIES.items():
        recommended = ", ".join(spec.recommended_timeframes) or "any"
        print(f"  {key:10} {spec.label:12} recommended timeframes: {recommended}")


def print_strategies(*, as_json: bool = False, strategy: str = "") -> int:
    """Print selected strategy signal rules in a reader-friendly format."""
    rows: list[dict[str, Any]] = []
    for key, spec in STRATEGIES.items():
        rows.append(
            {
                "key": key,
                "label": spec.label,
                "defaults": spec.default_params,
                "fields": spec.param_fields,
                "recommended_timeframes": list(spec.recommended_timeframes),
            }
        )
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    strategy_key = strategy.strip().lower()
    if not strategy_key:
        print("Choose one strategy to view its BUY/SELL signal rules:")
        for row in rows:
            print(f"  {row['key']:10} {row['label']}")
        print()
        print("Example: python -m core_python.util.ops strategies --strategy combo")
        return 0

    if strategy_key not in STRATEGIES:
        print(f"Unknown strategy: {strategy}", file=sys.stderr)
        print("Available strategies: " + ", ".join(STRATEGIES), file=sys.stderr)
        return 2

    _print_strategy_rules(strategy_key)
    return 0


def _print_strategy_rules(strategy_key: str) -> None:
    if strategy_key == "combo":
        _print_combo_strategy()
    elif strategy_key == "ma_cross":
        _print_ma_cross_strategy()
    else:
        raise AssertionError(strategy_key)


def _print_param_line(params: dict[str, object], names: tuple[str, ...]) -> None:
    parts = [f"{name}={params.get(name)}" for name in names if name in params]
    if parts:
        print("Main parameters:")
        print("  " + ", ".join(parts))


def _print_combo_strategy() -> None:
    """Print Combo signal rules."""
    print("Strategy: Combo")
    print(f"Timeframe: recommended {', '.join(combo_config.RECOMMENDED_TIMEFRAMES)}; any valid timeframe accepted.")
    print()
    print("A BUY signal is emitted when a closed candle meets all conditions:")
    print("  1. Bullish candle: close is above open.")
    print("  2. Close is above the MA line.")
    print("  3. MACD histogram is positive.")
    print("  4. The previous Combo state was not already BUY, so only the first BUY is emitted.")
    print()
    print("A SELL signal is emitted when a closed candle meets all conditions:")
    print("  1. Bearish candle: close is below open.")
    print("  2. Close is below the MA line.")
    print("  3. MACD histogram is negative.")
    print("  4. The previous Combo state was not already SELL, so only the first SELL is emitted.")
    print()
    _print_param_line(
        combo_config.DEFAULT_PARAMS,
        ("MA_PERIOD", "MACD_FAST", "MACD_SLOW", "MACD_SIGNAL", "ATR_PERIOD", "KTP"),
    )
    print("Symbol-specific review points:")
    print("  X is the breakout buffer for each symbol.")
    print("  KTP is currently a global default and can be overridden when symbol tuning is needed.")
    for symbol, x in combo_config.SYMBOL_X.items():
        print(f"      {symbol:8} X={x} KTP={combo_config.KTP}")


def _print_ma_cross_strategy() -> None:
    """Print MA Cross signal rules."""
    print("Strategy: MA Cross")
    print(
        f"Timeframes: {', '.join(ma_cross_config.SUPPORTED_TIMEFRAMES)} "
        f"(default {ma_cross_config.DEFAULT_TIMEFRAME})."
    )
    print()
    print("A BUY signal is emitted when the fast MA crosses above the slow MA on a closed candle:")
    print("  1. On the previous candle, fast MA was below or equal to slow MA.")
    print("  2. On the current candle, fast MA moves above slow MA.")
    print("  3. MACD Histogram is positive.")
    print()
    print("A SELL signal is emitted when the fast MA crosses below the slow MA on a closed candle:")
    print("  1. On the previous candle, fast MA was above or equal to slow MA.")
    print("  2. On the current candle, fast MA moves below slow MA.")
    print("  3. MACD Histogram is negative.")
    print()
    print("Levels:")
    print("  BUY:  Entry=Close, SL=Low-X,  TP=Close+KTP*ATR")
    print("  SELL: Entry=Close, SL=High+X, TP=Close-KTP*ATR")
    print()
    _print_param_line(
        ma_cross_config.DEFAULT_PARAMS,
        (
            "FAST_MA",
            "SLOW_MA",
            "MACD_FAST",
            "MACD_SLOW",
            "MACD_SIGNAL",
            "ATR_PERIOD",
            "KTP",
        ),
    )
    print("Symbol-specific X:")
    for symbol, x in ma_cross_config.SYMBOL_X.items():
        print(f"      {symbol:8} X={x}")


def print_services() -> int:
    """Print a concise user-systemd status summary for production services."""
    print("core_python - production service status")
    rc = 0
    for service in SERVICE_NAMES:
        data, error, code = _systemctl_show(
            service,
            ("Id", "ActiveState", "SubState", "MainPID", "NRestarts", "ExecStart"),
        )
        rc = rc or code
        if error:
            print(f"  {service}: {error}")
            continue
        print(
            f"  {data.get('Id', service):24} "
            f"{data.get('ActiveState', 'unknown')}/{data.get('SubState', 'unknown')} "
            f"pid={data.get('MainPID', '0')} "
            f"restarts={data.get('NRestarts', '0')}"
        )
        exec_start = data.get("ExecStart", "")
        if EXPECTED_DASHBOARD_APP in exec_start:
            print(f"  {'':24} unit app={EXPECTED_DASHBOARD_APP}")
        else:
            print(
                "  WARNING: unit ExecStart does not point to "
                f"{EXPECTED_DASHBOARD_APP}. Update the installed user service before the next restart/reboot."
            )
    return rc


def run_validate() -> int:
    """Run the standard code validation suite with readable section headers."""
    checks = (
        ("Ruff lint", [sys.executable, "-m", "ruff", "check", "src/", "tests/"]),
        ("Pytest", [sys.executable, "-m", "pytest", "-q"]),
        ("Vulture static audit", [sys.executable, "-m", "vulture", "src", "tests", "--min-confidence", "80"]),
    )
    final_rc = 0
    print("core_python - validation", flush=True)
    for label, cmd in checks:
        print(flush=True)
        print(f"== {label} ==", flush=True)
        completed = subprocess.run(cmd, check=False)
        if completed.returncode == 0:
            print(f"{label}: PASSED", flush=True)
        else:
            print(f"{label}: FAILED ({completed.returncode})", flush=True)
            final_rc = final_rc or completed.returncode
    return final_rc


def check_dashboard(base_url: str) -> int:
    """Check the dashboard health endpoint."""
    url = f"{base_url.rstrip('/')}/health"
    print("core_python - dashboard health")
    print(f"URL: {url}")
    try:
        body = _http_get(url, timeout=5)
    except OSError as exc:
        print(f"FAILED: {exc}")
        return 1
    print("PASSED")
    print(body)
    return 0


def smoke_dashboard(base_url: str) -> int:
    """Run a small dashboard API scan to prove DB + strategy calculation work."""
    query = urlencode({"strategy": "combo", "symbol": "US30", "tf": "H1", "bars": "50"})
    url = f"{base_url.rstrip('/')}/api/scan?{query}"
    print("core_python - dashboard API smoke test")
    print("Scenario: combo / US30 / H1 / latest 50 bars")
    try:
        body = _http_get(url, timeout=15)
    except OSError as exc:
        print(f"FAILED: {exc}")
        return 1
    print("PASSED")
    print(f"Response bytes: {len(body.encode('utf-8'))}")
    return 0


def list_exports(export_dir: Path, limit: int) -> int:
    """Print the latest CSV exports."""
    print("core_python - latest CSV exports")
    print(f"Directory: {export_dir}")
    if not export_dir.exists():
        print("No export directory found yet.")
        return 0

    files = sorted(
        (path for path in export_dir.glob("*.csv") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not files:
        print("No CSV exports found yet.")
        return 0

    for path in files[:limit]:
        stat = path.stat()
        print(f"  {path.name:60} {stat.st_size:>10} bytes")
    return 0


def _http_get(url: str, *, timeout: int) -> str:
    try:
        with urlopen(url, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise OSError(f"HTTP {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise OSError(str(exc.reason)) from exc


def _systemctl_show(unit: str, fields: tuple[str, ...]) -> tuple[dict[str, str], str, int]:
    """Return selected systemd user-unit fields."""
    cmd = ["systemctl", "--user", "show", unit, "--no-pager"]
    for field in fields:
        cmd.extend(["-p", field])
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            text=True,
            capture_output=True,
            env=_systemd_env(),
        )
    except FileNotFoundError:
        print("systemctl is not available on this host.")
        return {}, "systemctl is not available on this host", 1
    data: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key] = value
    error = completed.stderr.strip()
    return data, error, completed.returncode


def _systemd_env() -> dict[str, str]:
    env = os.environ.copy()
    if "XDG_RUNTIME_DIR" not in env and hasattr(os, "getuid"):
        env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
    return env


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="core_python operational helper commands.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("config", help="Print the operational config summary.")
    strategies = sub.add_parser("strategies", help="Print strategy signal rules.")
    strategies.add_argument("--json", action="store_true", help="Print JSON.")
    strategies.add_argument("--strategy", default="", help="Strategy key to inspect.")
    sub.add_parser("services", help="Print user-systemd service status.")
    sub.add_parser("validate", help="Run lint, tests, and static audit.")

    health = sub.add_parser("health", help="Check the dashboard /health endpoint.")
    health.add_argument("--base-url", default=DEFAULT_BASE_URL)

    smoke = sub.add_parser("smoke", help="Run a small dashboard API scan.")
    smoke.add_argument("--base-url", default=DEFAULT_BASE_URL)

    exports = sub.add_parser("exports", help="List latest exported CSV files.")
    exports.add_argument("--dir", default="runtime/exports")
    exports.add_argument("--limit", type=int, default=20)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "config":
        print_config()
        return 0
    if args.command == "strategies":
        return print_strategies(as_json=args.json, strategy=args.strategy)
    if args.command == "services":
        return print_services()
    if args.command == "validate":
        return run_validate()
    if args.command == "health":
        return check_dashboard(args.base_url)
    if args.command == "smoke":
        return smoke_dashboard(args.base_url)
    if args.command == "exports":
        return list_exports(Path(args.dir), max(1, args.limit))
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
