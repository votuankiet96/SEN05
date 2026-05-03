"""DB-backed signal export and notification watcher.

This worker is intentionally read-only with respect to market data. It reads
validated bars from DWH.Fact_OHLCV through simplified_core_python.data.loader,
computes strategy signals, exports signal CSVs, and sends alerts.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

import pandas as pd

from simplified_core_python import config
from simplified_core_python.data.loader import load
from simplified_core_python.export.to_csv import export_signals
from simplified_core_python.notify.formatter import format_signal_message
from simplified_core_python.notify.notifier import Notifier
from simplified_core_python.notify.state import SignalState, signal_key
from simplified_core_python.strategies.registry import get_strategy


def run_strategy_frame(
    *,
    strategy: str,
    symbol: str,
    tf: str,
    bars: int,
    overrides: dict[str, Any] | None = None,
    closed_only: bool = True,
) -> tuple[pd.DataFrame, Any, dict[str, Any]]:
    """Load DB bars and return an enriched strategy signal frame."""
    spec = get_strategy(strategy)
    params = spec.normalize_params(overrides or {}, symbol)
    raw = load(symbol, tf, bars)
    if closed_only:
        raw = _drop_open_bar(raw, tf)
    with_indicators = spec.add_indicators(raw, params)
    with_signals = spec.detect_signals(with_indicators, symbol=symbol, params=params)
    enriched = spec.add_levels(with_signals, params, symbol)
    return enriched, spec, params


def latest_signal_row(df: pd.DataFrame) -> pd.Series | None:
    """Return the latest non-zero signal row, if any."""
    if df.empty or "signal" not in df:
        return None
    signals = df[df["signal"].fillna(0).astype(int).ne(0)]
    if signals.empty:
        return None
    return signals.iloc[-1]


def check_once(
    *,
    strategy: str,
    symbols: list[str],
    tf: str,
    bars: int,
    state: SignalState,
    notifier: Notifier,
    output_dir: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
    closed_only: bool = True,
    export_on_signal: bool = True,
) -> list[str]:
    """Check symbols once, export new signal CSVs, notify, and return event summaries."""
    events: list[str] = []
    for symbol in symbols:
        frame, spec, _params = run_strategy_frame(
            strategy=strategy,
            symbol=symbol,
            tf=tf,
            bars=bars,
            overrides=overrides,
            closed_only=closed_only,
        )
        row = latest_signal_row(frame)
        if row is None:
            events.append(f"{symbol} {tf}: no signal")
            continue

        key = signal_key(strategy, symbol, tf, row["bartime"], int(row["signal"]))
        if state.has(key):
            events.append(f"{symbol} {tf}: duplicate signal skipped")
            continue

        export_path = None
        if export_on_signal:
            export_path = export_signals(frame, symbol=symbol, strategy=strategy, output_dir=output_dir)

        message = format_signal_message(row, strategy_label=spec.label, symbol=symbol, tf=tf)
        if export_path:
            message = f"{message}\n\nCSV: {export_path}"
        result = notifier.send(message)
        if result.sent and not notifier.dry_run:
            state.add(key)
            events.append(f"{symbol} {tf}: alerted via {result.backend}")
        elif result.sent:
            events.append(f"{symbol} {tf}: dry-run alert rendered")
        else:
            events.append(f"{symbol} {tf}: notify failed ({result.detail})")
    return events


def _drop_open_bar(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Drop bars that are too recent to be confidently closed."""
    if df.empty or "bartime" not in df:
        return df
    tf_code = str(tf).upper()
    minutes = config.TF_MINUTES.get(tf_code)
    if not minutes:
        return df
    now_utc = pd.Timestamp.utcnow()
    if now_utc.tzinfo is None:
        now_utc = now_utc.tz_localize("UTC")
    cutoff = now_utc - pd.Timedelta(minutes=int(minutes))
    bar_times = pd.to_datetime(df["bartime"], errors="coerce")
    if getattr(bar_times.dt, "tz", None) is None:
        bar_times = bar_times.dt.tz_localize("UTC")
    else:
        bar_times = bar_times.dt.tz_convert("UTC")
    return df.loc[bar_times <= cutoff].reset_index(drop=True)


def _parse_symbols(value: str) -> list[str]:
    symbols = [part.strip().upper() for part in value.replace(";", ",").split(",") if part.strip()]
    if not symbols:
        raise ValueError("At least one symbol is required")
    return symbols


def _parse_overrides(values: list[str]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Invalid --param '{value}'. Use KEY=VALUE.")
        key, raw = value.split("=", 1)
        overrides[key.strip().upper()] = raw.strip()
    return overrides


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export and notify new strategy signals from DB data.")
    parser.add_argument("--strategy", default="combo", choices=["combo", "ma_cross"])
    parser.add_argument("--symbols", default=config.DEFAULT_SYMBOL, help="Comma-separated symbols.")
    parser.add_argument("--tf", default=config.DEFAULT_TF)
    parser.add_argument("--bars", type=int, default=config.N_BARS)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--once", action="store_true", help="Run one check and exit.")
    parser.add_argument("--state-path", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--backend", default="auto", choices=["auto", "telegram", "discord", "none"])
    parser.add_argument("--dry-run", action="store_true", help="Print messages instead of sending.")
    parser.add_argument("--include-open-bar", action="store_true", help="Allow latest not-yet-closed bar.")
    parser.add_argument("--no-export", action="store_true", help="Do not export CSV on new signal.")
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        help="Strategy parameter override as KEY=VALUE. Can be repeated.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    symbols = _parse_symbols(args.symbols)
    overrides = _parse_overrides(args.param)
    state = SignalState(args.state_path)
    notifier = Notifier(backend=args.backend, dry_run=args.dry_run)
    closed_only = not args.include_open_bar

    while True:
        events = check_once(
            strategy=args.strategy,
            symbols=symbols,
            tf=args.tf,
            bars=args.bars,
            state=state,
            notifier=notifier,
            output_dir=args.output_dir,
            overrides=overrides,
            closed_only=closed_only,
            export_on_signal=not args.no_export,
        )
        for event in events:
            print(event)
        if args.once:
            return 0
        time.sleep(max(5, int(args.poll_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
