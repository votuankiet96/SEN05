"""Command line CSV export for core_python strategies."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from core_python.data.loader import load_bounds
from core_python.engine import date_window_from_args, validate_strategy_timeframe
from core_python.export.service import (
    build_bulk_export,
    build_single_export,
    parse_symbol_list,
)
from core_python.strategies.registry import STRATEGIES, get_strategy
from core_python.util import config

DEFAULT_COLS = "bartime,side,signal,entry_price,sl_price,tp_price,risk_reward,signal_reason"
DEFAULT_BULK_COLS = (
    "bartime,symbol,signal,entry_price,sl_price,tp_price,risk_reward,signal_reason"
)
DEFAULT_EXPORT_SYMBOL = "US30"


def export_single(args: argparse.Namespace) -> Path:
    """Export one symbol/timeframe strategy run to a CSV file."""
    request_args = _request_args(args)
    tf = _strategy_timeframe(args.strategy, args.tf)
    export = build_single_export(
        args.strategy,
        symbol=args.symbol,
        tf=tf,
        bars=args.bars,
        args=request_args,
        date_window=date_window_from_args(request_args),
    )
    return _write_export(export.data, export.filename, args)


def export_bulk(args: argparse.Namespace) -> Path:
    """Export multiple symbols for one timeframe to a CSV file."""
    request_args = _request_args(args)
    request_args["symbols"] = args.symbols
    tf = _strategy_timeframe(args.strategy, args.tf)
    export = build_bulk_export(
        args.strategy,
        tf=tf,
        bars=args.bars,
        args=request_args,
        date_window=date_window_from_args(request_args),
    )
    return _write_export(export.data, export.filename, args)


def export_wizard(
    args: argparse.Namespace,
    *,
    input_fn: Callable[[str], str] = input,
) -> Path | None:
    """Interactively configure and run one signal CSV export."""
    print("core_python - signal CSV export")
    print()

    strategy_key = _prompt_strategy(input_fn)
    spec = get_strategy(strategy_key)
    default_tf = _default_strategy_timeframe(strategy_key)
    _print_timeframe_help(strategy_key)
    tf = _prompt(input_fn, "Timeframe", default_tf).upper()
    validate_strategy_timeframe(spec, tf)

    print()
    print("Enter one symbol or a comma-separated list. Enter ALL for every configured symbol.")
    symbols_text = _prompt(input_fn, "Symbols", DEFAULT_EXPORT_SYMBOL)
    symbols = _wizard_symbols(symbols_text)

    start_default, end_default = _full_date_defaults(symbols, tf)
    print()
    print(f"Full SQL data range: {start_default} -> {end_default}")
    print("Press Enter on both dates to export the full available range.")
    start_date = _prompt(input_fn, "Start date", start_default)
    end_date = _prompt(input_fn, "End date", end_default)
    request_args = {
        "start_date": start_date,
        "end_date": end_date,
        "cols": DEFAULT_COLS if len(symbols) == 1 else DEFAULT_BULK_COLS,
    }
    date_window = date_window_from_args(request_args)

    print()
    print("Export summary")
    print(f"  Strategy : {spec.label} ({spec.key})")
    print(f"  Timeframe: {tf}")
    print(f"  Symbols  : {', '.join(symbols)}")
    print(f"  Date range: {date_window.start_label} -> {date_window.end_label}")
    if not _confirm(input_fn, "Create CSV now?", default=True):
        return None

    if len(symbols) == 1:
        export = build_single_export(
            spec.key,
            symbol=symbols[0],
            tf=tf,
            bars=config.N_BARS,
            args=request_args,
            date_window=date_window,
        )
    else:
        request_args["symbols"] = ",".join(symbols)
        export = build_bulk_export(
            spec.key,
            tf=tf,
            bars=config.N_BARS,
            args=request_args,
            date_window=date_window,
        )
    return _write_export(export.data, export.filename, args)


def _prompt_strategy(input_fn: Callable[[str], str]) -> str:
    print("Available strategies:")
    strategies = tuple(STRATEGIES.values())
    for index, spec in enumerate(strategies, start=1):
        print(f"  {index}. {spec.label} ({spec.key})")
    value = _prompt(input_fn, "Strategy", "combo").strip().lower()
    if value.isdigit():
        index = int(value) - 1
        if 0 <= index < len(strategies):
            return strategies[index].key
    return get_strategy(value).key


def _default_strategy_timeframe(strategy: str) -> str:
    spec = get_strategy(strategy)
    if spec.default_timeframe:
        return str(spec.default_timeframe).upper()
    if spec.recommended_timeframes:
        return str(spec.recommended_timeframes[0]).upper()
    return config.DEFAULT_TF


def _print_timeframe_help(strategy: str) -> None:
    spec = get_strategy(strategy)
    if spec.supported_timeframes:
        print(f"Allowed timeframes: {', '.join(spec.supported_timeframes)}")
    elif spec.recommended_timeframes:
        print(
            "Recommended timeframes: "
            f"{', '.join(spec.recommended_timeframes)} (other configured timeframes are accepted)"
        )
    else:
        print(f"Available timeframes: {', '.join(config.timeframe_codes())}")


def _wizard_symbols(value: str) -> list[str]:
    if str(value).strip().upper() == "ALL":
        return list(config.SYMBOLS)
    symbols = parse_symbol_list(value)
    if not symbols:
        raise ValueError("At least one symbol is required.")
    for symbol in symbols:
        config.get_symbol(symbol)
    return symbols


def _full_date_defaults(symbols: list[str], tf: str) -> tuple[str, str]:
    bounds: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    missing: list[str] = []
    for symbol in symbols:
        item = load_bounds(symbol, tf)
        if item is None:
            missing.append(symbol)
        else:
            bounds.append(item)
    if missing:
        raise ValueError(f"No SQL data for {tf}: {', '.join(missing)}")

    start = min(item[0] for item in bounds)
    end = max(item[1] for item in bounds)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _prompt(input_fn: Callable[[str], str], label: str, default: str) -> str:
    value = input_fn(f"{label} [{default}]: ").strip()
    return value or default


def _confirm(
    input_fn: Callable[[str], str],
    label: str,
    *,
    default: bool,
) -> bool:
    suffix = "Y/n" if default else "y/N"
    value = input_fn(f"{label} [{suffix}]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes"}


def _request_args(args: argparse.Namespace) -> dict[str, object]:
    request_args: dict[str, object] = {"cols": args.cols}
    if args.start_date:
        request_args["start_date"] = args.start_date
    if args.end_date:
        request_args["end_date"] = args.end_date
    for item in args.param:
        if "=" not in item:
            raise ValueError(f"Invalid --param '{item}', expected NAME=VALUE.")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid --param '{item}', empty name.")
        request_args[key] = value.strip()
    return request_args


def _write_export(csv_data: str, default_filename: str, args: argparse.Namespace) -> Path:
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / default_filename
    output.write_text(csv_data, encoding="utf-8")
    return output


def _strategy_timeframe(strategy: str, value: str | None) -> str:
    """Resolve an explicit timeframe or use the selected strategy default."""
    if value and str(value).strip():
        return str(value).strip().upper()
    return _default_strategy_timeframe(strategy)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--strategy", default="combo", help="Strategy key: combo or ma_cross.")
    parser.add_argument("--tf", default="", help="Timeframe code; omitted uses the strategy default.")
    parser.add_argument("--bars", type=int, default=500, help="Latest bars to query when no date range is provided.")
    parser.add_argument("--cols", default=DEFAULT_COLS, help="Comma-separated CSV columns.")
    parser.add_argument("--start-date", default="", help="Optional start date, e.g. 2026-07-01 or 01/07/2026.")
    parser.add_argument("--end-date", default="", help="Optional end date, e.g. 2026-07-11 or 11/07/2026.")
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        help="Extra strategy parameter as NAME=VALUE. Can be repeated.",
    )
    parser.add_argument("--output-dir", default="runtime/exports", help="Directory for generated CSV files.")
    parser.add_argument("--output", default="", help="Exact output file path. Overrides --output-dir.")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export core_python strategy signals to CSV.")
    sub = parser.add_subparsers(dest="command", required=True)

    single = sub.add_parser("single", help="Export one symbol/timeframe.")
    _add_common_args(single)
    single.add_argument("--symbol", default="US30", help="Symbol code.")

    bulk = sub.add_parser("bulk", help="Export multiple symbols on one timeframe.")
    _add_common_args(bulk)
    bulk.add_argument("--symbols", required=True, help="Comma-separated symbol list.")

    wizard = sub.add_parser("wizard", help="Interactively configure and export signal CSV.")
    wizard.add_argument("--output-dir", default="runtime/exports", help="Directory for generated CSV files.")
    wizard.add_argument("--output", default="", help="Exact output file path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
        if args.command == "wizard":
            output = export_wizard(args)
        elif args.command == "single":
            output = export_single(args)
        else:
            output = export_bulk(args)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    if output is None:
        print("Export cancelled.")
        return 0
    print(f"CSV exported: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
