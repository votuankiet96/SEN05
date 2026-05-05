"""24/7 signal watcher: polls DB bars, detects signals, sends Telegram alerts.

Run modes:
  python -m core_python.notify.signal_watcher              # 24/7, reads scan_config.py
  python -m core_python.notify.signal_watcher --once       # one-shot, then exit
  python -m core_python.notify.signal_watcher --dry-run    # print messages, no send
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

import pandas as pd

from core_python import config
from core_python.data.loader import load
from core_python.export.to_csv import export_signals
from core_python.notify.formatter import format_signal_message
from core_python.notify.notifier import Notifier
from core_python.notify.state import SignalState, signal_key
from core_python.strategies.registry import get_strategy


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


def all_new_signal_rows(
    df: pd.DataFrame,
    state: SignalState,
    strategy: str,
    symbol: str,
    tf: str,
) -> list[pd.Series]:
    """Return all unseen non-zero signal rows in chronological order."""
    if df.empty or "signal" not in df:
        return []
    signals = df[df["signal"].fillna(0).astype(int).ne(0)]
    result = []
    for _, row in signals.iterrows():
        key = signal_key(strategy, symbol, tf, row["bartime"], int(row["signal"]))
        if not state.has(key):
            result.append(row)
    return result


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
    chat_id: str | None = None,
) -> list[str]:
    """Check all symbols once; notify every new signal and return event summaries."""
    events: list[str] = []
    for symbol in symbols:
        try:
            frame, spec, _params = run_strategy_frame(
                strategy=strategy,
                symbol=symbol,
                tf=tf,
                bars=bars,
                overrides=overrides,
                closed_only=closed_only,
            )
        except Exception as exc:
            events.append(f"{symbol} {tf}: load error — {exc}")
            continue

        new_rows = all_new_signal_rows(frame, state, strategy, symbol, tf)
        if not new_rows:
            events.append(f"{symbol} {tf}: no new signal")
            continue

        for row in new_rows:
            key = signal_key(strategy, symbol, tf, row["bartime"], int(row["signal"]))
            export_path = None
            if export_on_signal:
                try:
                    export_path = export_signals(
                        frame, symbol=symbol, strategy=strategy, output_dir=output_dir
                    )
                except Exception:
                    pass

            message = format_signal_message(row, strategy_label=spec.label, symbol=symbol, tf=tf)
            result = notifier.send(message, chat_id=chat_id)

            if result.sent and not notifier.dry_run:
                state.add(key)
                events.append(f"{symbol} {tf}: ✓ alerted via {result.backend}")
            elif result.sent:
                events.append(f"{symbol} {tf}: dry-run OK")
            else:
                events.append(f"{symbol} {tf}: FAILED — {result.detail}")

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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="24/7 signal watcher — Telegram notifier.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Use scan_config.py defaults (Indice, H1-H4)\n"
            "  python -m core_python.notify.signal_watcher\n\n"
            "  # Override: specific symbols and TFs\n"
            "  python -m core_python.notify.signal_watcher --symbols US30,GOLD --tf H1,H4\n\n"
            "  # Dry-run one-shot check\n"
            "  python -m core_python.notify.signal_watcher --dry-run --once"
        ),
    )
    parser.add_argument("--backend", default="auto", choices=["auto", "telegram", "discord", "none"])
    parser.add_argument("--dry-run", action="store_true", help="Print messages instead of sending.")
    parser.add_argument("--once", action="store_true", help="Run one check per group and exit.")
    parser.add_argument(
        "--warm-up",
        action="store_true",
        help="Mark all current signals as seen without sending. Run once before production start.",
    )
    parser.add_argument("--state-path", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--bars", type=int, default=config.N_BARS)
    parser.add_argument("--include-open-bar", action="store_true")
    parser.add_argument("--no-export", action="store_true", help="Skip CSV export on signal.")
    parser.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated symbols to scan. Overrides scan_config.py groups.",
    )
    parser.add_argument(
        "--tf",
        default=None,
        help="Comma-separated timeframes to scan. Overrides scan_config.py groups.",
    )
    parser.add_argument("--strategy", default="combo", choices=["combo", "ma_cross"])
    return parser.parse_args()


def _build_groups_from_args(args: argparse.Namespace) -> list[dict]:
    """Build scan groups from CLI --symbols and --tf overrides."""
    from core_python.notify.scan_config import TF_POLL_SECONDS
    symbols = [s.strip().upper() for s in args.symbols.replace(";", ",").split(",") if s.strip()]
    tfs = [t.strip().upper() for t in args.tf.replace(";", ",").split(",") if t.strip()]
    groups = []
    for tf in tfs:
        groups.append({
            "strategy": args.strategy,
            "symbols": symbols,
            "tf": tf,
            "bars": args.bars,
            "poll_seconds": TF_POLL_SECONDS.get(tf, 300),
            "chat_id": None,
            "overrides": {},
        })
    return groups


def main() -> int:
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except AttributeError:
        pass

    args = _parse_args()

    # Build scan groups: CLI override or scan_config defaults
    if args.symbols and args.tf:
        scan_groups = _build_groups_from_args(args)
        mode_desc = f"CLI override — symbols: {args.symbols} | TF: {args.tf}"
    else:
        from core_python.notify.scan_config import SCAN_GROUPS
        scan_groups = SCAN_GROUPS
        mode_desc = "scan_config.py defaults"

    state = SignalState(args.state_path)
    notifier = Notifier(backend=args.backend, dry_run=args.dry_run)
    closed_only = not args.include_open_bar

    if args.warm_up:
        print("Warm-up: marking all current signals as seen (no Telegram send)...")
        total = 0
        from core_python.notify.state import signal_key as _sk
        for group in scan_groups:
            for symbol in group["symbols"]:
                try:
                    frame, _spec, _params = run_strategy_frame(
                        strategy=group["strategy"],
                        symbol=symbol,
                        tf=group["tf"],
                        bars=group.get("bars", args.bars),
                        overrides=group.get("overrides") or {},
                        closed_only=closed_only,
                    )
                    if frame.empty or "signal" not in frame.columns:
                        continue
                    for _, row in frame[frame["signal"].fillna(0).astype(int).ne(0)].iterrows():
                        key = _sk(group["strategy"], symbol, group["tf"], row["bartime"], int(row["signal"]))
                        if not state.has(key):
                            state.add(key)
                            total += 1
                except Exception as exc:
                    print(f"  {symbol} {group['tf']}: skip — {exc}")
        print(f"Warm-up complete: {total} signals marked as seen. Now run without --warm-up.")
        return 0

    n_groups = len(scan_groups)
    total_slots = sum(len(g["symbols"]) for g in scan_groups)
    startup_msg = (
        f"🚀 <b>SEN05 Watcher started</b>\n"
        f"{n_groups} groups · {total_slots} symbol-TF slots"
        + (" · DRY RUN" if args.dry_run else "")
    )
    notifier.send(startup_msg)
    print(startup_msg.replace("<b>", "").replace("</b>", ""))
    print(f"Mode: {mode_desc}")

    last_run: dict[int, float] = {}

    while True:
        now = time.monotonic()
        for i, group in enumerate(scan_groups):
            if now - last_run.get(i, 0.0) < group["poll_seconds"]:
                continue
            try:
                events = check_once(
                    strategy=group["strategy"],
                    symbols=group["symbols"],
                    tf=group["tf"],
                    bars=group.get("bars", args.bars),
                    state=state,
                    notifier=notifier,
                    output_dir=args.output_dir,
                    overrides=group.get("overrides") or {},
                    closed_only=closed_only,
                    export_on_signal=not args.no_export,
                    chat_id=group.get("chat_id"),
                )
            except Exception as exc:
                events = [f"[ERROR] group {i} {group['tf']}: {exc}"]
            ts = pd.Timestamp.utcnow().strftime("%H:%M:%S")
            for event in events:
                print(f"[{ts}] {event}")
            last_run[i] = time.monotonic()

        if args.once:
            return 0
        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
