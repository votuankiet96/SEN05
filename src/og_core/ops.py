"""Small operational CLI helpers for the OG launcher."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any

from og_core import config
from og_core.strategies.ai_trend import config as ai_trend_config
from og_core.strategies.combo import config as combo_config
from og_core.strategies.knn_combo import config as knn_combo_config
from og_core.strategies.ma_cross import config as ma_cross_config
from og_core.strategies.registry import STRATEGIES
from og_live.pubsub_mechanism import settings as pubsub_settings
from og_live.stream_mechanism import settings as stream_settings

SERVICE_NAMES = ("og-live-stream.service", "og-live-pubsub.service", "og-dashboard.service")
TIMER_NAMES = ("og-live-stream-healthcheck.timer", "og-live-pubsub-healthcheck.timer")


def print_config() -> None:
    """Print the active OG operational universe in a reader-friendly format."""
    input_symbols = ", ".join(stream_settings.LIVE_FETCHING_SYMBOLS)
    input_timeframes = ", ".join(stream_settings.LIVE_FETCHING_TIMEFRAMES)
    stream_watched = stream_settings.watched_summary()
    pubsub_watched = pubsub_settings.watched_summary()

    print("OG Engine - operation config")
    print()
    print("OG Live has two mechanisms: Stream and Pub/Sub.")
    print(
        f"Each event points to a state key containing up to {config.N_BARS} latest closed candles "
        "for one symbol/timeframe pair."
    )
    print(
        f"Stream route: input db{stream_settings.INPUT_REDIS_DB} "
        f"-> signal output db{stream_settings.OUTPUT_REDIS_DB}"
    )
    print(
        f"Pub/Sub route: channel {pubsub_settings.PUBSUB_CHANNEL}; "
        f"state input db{pubsub_settings.INPUT_REDIS_DB} "
        f"-> signal output db{pubsub_settings.OUTPUT_REDIS_DB}"
    )
    print("Signal route: strategy > symbol > timeframe")
    print()
    print(f"DP6/live_fetching input universe contains {len(stream_settings.LIVE_FETCHING_SYMBOLS)} symbols:")
    print(f"  {input_symbols}")
    print()
    print(f"DP6/live_fetching input timeframes: {len(stream_settings.LIVE_FETCHING_TIMEFRAMES)}")
    print(f"  {input_timeframes}")
    print()
    print("OG Live Stream watchlist:")
    print(f"  strategies:  {', '.join(stream_watched['strategies'])}")
    print(f"  symbols:     {', '.join(stream_watched['symbols'])}")
    print(f"  timeframes:  {', '.join(stream_watched['timeframes'])}")
    print(f"  pairs:       {stream_watched['pairs']}")
    print(f"  bars:        {', '.join(str(value) for value in stream_watched['bars'])}")
    print(f"  latest_only: {', '.join(str(value) for value in stream_watched['latest_only'])}")
    print()
    print("OG Live Pub/Sub watchlist:")
    print(f"  strategies:  {', '.join(pubsub_watched['strategies'])}")
    print(f"  symbols:     {', '.join(pubsub_watched['symbols'])}")
    print(f"  timeframes:  {', '.join(pubsub_watched['timeframes'])}")
    print(f"  pairs:       {pubsub_watched['pairs']}")
    print(f"  bars:        {', '.join(str(value) for value in pubsub_watched['bars'])}")
    print(f"  latest_only: {', '.join(str(value) for value in pubsub_watched['latest_only'])}")
    print()
    print("Stream publish symbol details:")
    for symbol in stream_watched["symbols"]:
        meta = config.SYMBOLS.get(symbol, {})
        print(
            f"  {symbol:8} "
            f"id={meta.get('symbol_id', '-')!s:<3} "
            f"asset={_display_asset_type(str(meta.get('asset_type', '-')))}"
        )
    print()
    print("Publish timeframe details:")
    for tf in stream_watched["timeframes"]:
        minutes = config.TF_MINUTES[tf]
        print(f"  {tf:4} {minutes:>6} minutes")


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
        print("Example: python -m og_core.ops strategies --strategy combo")
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
    elif strategy_key == "ai_trend":
        _print_ai_trend_strategy()
    elif strategy_key == "knn_combo":
        _print_knn_combo_strategy()
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
    print("Timeframe:")
    print("  Dashboard/export runs on the timeframe selected by the operator.")
    print("  Live production runs on all 15 live_fetching timeframes.")
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
    for symbol in stream_settings.LIVE_FETCHING_SYMBOLS:
        print(f"      {symbol:8} X={combo_config.SYMBOL_X.get(symbol, 0.0)} KTP={combo_config.KTP}")


def _print_ma_cross_strategy() -> None:
    """Print MA Cross signal rules."""
    print("Strategy: MA Cross")
    print("Timeframe: runs on the timeframe selected by the operator.")
    print()
    print("A BUY signal is emitted when the fast MA crosses above the slow MA on a closed candle:")
    print("  1. On the previous candle, fast MA was below or equal to slow MA.")
    print("  2. On the current candle, fast MA moves above slow MA.")
    print("  3. Indicators are warmed up and the candle is inside the allowed session.")
    print()
    print("A SELL signal is emitted when the fast MA crosses below the slow MA on a closed candle:")
    print("  1. On the previous candle, fast MA was above or equal to slow MA.")
    print("  2. On the current candle, fast MA moves below slow MA.")
    print("  3. Indicators are warmed up and the candle is inside the allowed session.")
    print()
    _print_param_line(
        ma_cross_config.DEFAULT_PARAMS,
        ("MA_TYPE", "FAST_MA", "SLOW_MA", "ATR_PERIOD", "ATR_STOP_MULT", "ATR_TP_MULT"),
    )


def _print_ai_trend_strategy() -> None:
    """Print AI Trend signal rules."""
    print("Strategy: AI Trend")
    print("Timeframe: this is a multi-timeframe strategy.")
    print(f"  Default trend timeframe: {ai_trend_config.DEFAULT_PARAMS['TREND_TF']}")
    print(f"  Default entry timeframe: {ai_trend_config.DEFAULT_PARAMS['ENTRY_TF']}")
    print()
    print("A BUY signal is emitted when:")
    print("  1. The trend timeframe is closed and KNN trend is bullish.")
    print("  2. The entry bar is the first valid entry inside that bullish segment.")
    print("  3. Fast EMA is above slow EMA on the entry timeframe.")
    print("  4. MACD histogram on the entry timeframe is positive.")
    print()
    print("A SELL signal is emitted when:")
    print("  1. The trend timeframe is closed and KNN trend is bearish.")
    print("  2. The entry bar is the first valid entry inside that bearish segment.")
    print("  3. Fast EMA is below slow EMA on the entry timeframe.")
    print("  4. MACD histogram on the entry timeframe is negative.")
    print()
    _print_param_line(
        ai_trend_config.DEFAULT_PARAMS,
        ("TREND_TF", "ENTRY_TF", "AI_MA_LEN", "AI_TARGET_LEN", "AI_K", "AI_SMOOTH", "EMA_FAST", "EMA_SLOW"),
    )


def _print_knn_combo_strategy() -> None:
    """Print KNN Combo signal rules."""
    print("Strategy: KNN Combo")
    print("Timeframe: this is a multi-timeframe strategy.")
    print(f"  Default trend timeframe: {knn_combo_config.DEFAULT_PARAMS['TREND_TF']}")
    print(f"  Default entry timeframe: {knn_combo_config.DEFAULT_PARAMS['ENTRY_TF']}")
    print()
    print("A BUY signal is emitted when:")
    print("  1. The trend timeframe is closed and KNN trend is bullish.")
    print("  2. The entry timeframe has a valid Combo raw BUY:")
    print("     close crosses above MA, candle is bullish, MACD histogram is positive;")
    print("     or it is the first entry after KNN trend just turned bullish.")
    print("  3. If NEUTRAL_BLOCK=True, neutral trend blocks both BUY and SELL.")
    print()
    print("A SELL signal is emitted when:")
    print("  1. The trend timeframe is closed and KNN trend is bearish.")
    print("  2. The entry timeframe has a valid Combo raw SELL:")
    print("     close crosses below MA, candle is bearish, MACD histogram is negative;")
    print("     or it is the first entry after KNN trend just turned bearish.")
    print("  3. If NEUTRAL_BLOCK=True, neutral trend blocks both BUY and SELL.")
    print()
    _print_param_line(
        knn_combo_config.DEFAULT_PARAMS,
        ("TREND_TF", "ENTRY_TF", "AI_MA_LEN", "AI_TARGET_LEN", "AI_K", "AI_SMOOTH", "MA_PERIOD"),
    )


def print_services() -> int:
    """Print a concise user-systemd status summary for production services."""
    print("OG Engine - production service status")
    rc = 0
    for service in SERVICE_NAMES:
        data, error, code = _systemctl_show(
            service,
            ("Id", "ActiveState", "SubState", "MainPID", "NRestarts"),
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

    print()
    print("Healthcheck timers:")
    for timer_name in TIMER_NAMES:
        timer_data, timer_error, timer_code = _systemctl_show(
            timer_name,
            ("Id", "ActiveState", "SubState", "LastTriggerUSec", "NextElapseUSecRealtime"),
        )
        rc = rc or timer_code
        if timer_error:
            print(f"  {timer_name}: {timer_error}")
        else:
            print(
                f"  {timer_data.get('Id', timer_name):32} "
                f"{timer_data.get('ActiveState', 'unknown')}/{timer_data.get('SubState', 'unknown')} "
                f"last={timer_data.get('LastTriggerUSec') or 'n/a'} "
                f"next={timer_data.get('NextElapseUSecRealtime') or 'n/a'}"
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
    print("OG Engine - validation", flush=True)
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


def _display_asset_type(asset_type: str) -> str:
    """Return an English display label for configured asset types."""
    return {"Indice": "Index"}.get(asset_type, asset_type)


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
    parser = argparse.ArgumentParser(description="OG operational helper commands.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("config", help="Print live operation config.")
    strategies = sub.add_parser("strategies", help="Print strategy signal rules.")
    strategies.add_argument("--json", action="store_true", help="Print JSON.")
    strategies.add_argument("--strategy", default="", help="Strategy key to inspect.")
    sub.add_parser("services", help="Print user-systemd service status.")
    sub.add_parser("validate", help="Run lint, tests, and static audit.")
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
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
