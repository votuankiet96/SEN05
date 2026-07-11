"""Command line CSV export for OG Past strategies."""

from __future__ import annotations

import argparse
from pathlib import Path

from og_past.engine import date_window_from_args
from og_past.export.service import build_bulk_export, build_single_export

DEFAULT_COLS = "bartime,side,signal,entry_price,sl_price,tp_price,risk_reward,signal_reason"


def export_single(args: argparse.Namespace) -> Path:
    """Export one symbol/timeframe strategy run to a CSV file."""
    request_args = _request_args(args)
    export = build_single_export(
        args.strategy,
        symbol=args.symbol,
        tf=args.tf,
        bars=args.bars,
        args=request_args,
        date_window=date_window_from_args(request_args),
    )
    return _write_export(export.data, export.filename, args)


def export_bulk(args: argparse.Namespace) -> Path:
    """Export multiple symbols for one timeframe to a CSV file."""
    request_args = _request_args(args)
    request_args["symbols"] = args.symbols
    export = build_bulk_export(
        args.strategy,
        tf=args.tf,
        bars=args.bars,
        args=request_args,
        date_window=date_window_from_args(request_args),
    )
    return _write_export(export.data, export.filename, args)


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


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--strategy", default="combo", help="Strategy key, e.g. combo, ai_trend, knn_combo, ma_cross.")
    parser.add_argument("--tf", default="H1", help="Timeframe code.")
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
    parser = argparse.ArgumentParser(description="Export OG Past strategy signals to CSV.")
    sub = parser.add_subparsers(dest="command", required=True)

    single = sub.add_parser("single", help="Export one symbol/timeframe.")
    _add_common_args(single)
    single.add_argument("--symbol", default="US30", help="Symbol code.")

    bulk = sub.add_parser("bulk", help="Export multiple symbols on one timeframe.")
    _add_common_args(bulk)
    bulk.add_argument("--symbols", required=True, help="Comma-separated symbol list.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
        output = export_single(args) if args.command == "single" else export_bulk(args)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"CSV exported: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
