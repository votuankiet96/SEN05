"""Entrypoint for the 24/7 realtime signal watcher.

This module intentionally stays thin: CLI parsing, process lock, warm-up, and
thread wiring live here. Strategy detection is in notify.detector; runtime loops
are in notify.runtime; strategy message semantics live in strategy realtime
adapters; alert delivery is in notify.alerts.
"""

from __future__ import annotations

import argparse
import atexit
import html
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd

from core_python import config
from core_python.notify import redis_publisher
from core_python.notify.alerts import Notifier
from core_python.notify.delivery_outbox import DeliveryOutbox, NullDeliveryOutbox
from core_python.notify.detector import (
    SentSignalEvent,
    _DEFAULT_BAR_CLOSE_BUFFER_SECONDS,
    _DEFAULT_POST_CLOSE_RETRY_SECONDS,
    _DEFAULT_POST_CLOSE_WATCH_SECONDS,
    _as_utc_ts,
    _scan_fingerprint_error,
    run_ai_trend_alerts,
    run_strategy_frame,
    scan_fingerprint,
)
from core_python.notify.runtime import (
    _DEFAULT_FALLBACK_SCAN_SECONDS,
    _DEFAULT_QUEUE_MAXSIZE,
    _DEFAULT_RELAY_ALERT_SECONDS,
    _DEFAULT_RELAY_RETRY_SECONDS,
    _WAKE_RELAY,
    _bar_ready_subscriber_loop,
    _delivery_relay_loop,
    _drain_outbox_once,
    _fallback_scanner_loop,
    _run_all_groups_once,
    _worker_loop,
)
from core_python.notify.state import SignalState
from core_python.strategies.ai_trend.alerts import H3_TREND_CHANGE, M45_ENTRY_SIGNAL, ai_trend_alert_key
from core_python.strategies.ai_trend.realtime import normalize_ai_trend_event_type

_IDLE_SLEEP_SECONDS = 1.0

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="24/7 signal watcher - fixed-route notifier.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Use scan_config.py defaults (AI Trend + Combo production groups)\n"
            "  python -m core_python.notify.signal_watcher\n\n"
            "  # Override: specific symbols and TFs\n"
            "  python -m core_python.notify.signal_watcher --symbols US30,GOLD --tf H1,H4\n\n"
            "  # Dry-run one-shot check\n"
            "  python -m core_python.notify.signal_watcher --dry-run --once"
        ),
    )
    parser.add_argument(
        "--backend",
        default="auto",
        choices=["auto", "telegram", "discord", "none"],
        help=(
            "Compatibility option. Signal routing is fixed by strategy "
            "(Combo/MA Cross -> Discord, AI Trend -> Telegram); use 'none' as a send kill switch."
        ),
    )
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
    parser.add_argument("--strategy", default="combo", choices=["combo", "ma_cross", "ai_trend"])
    parser.add_argument(
        "--bar-close-buffer-seconds",
        type=int,
        default=_DEFAULT_BAR_CLOSE_BUFFER_SECONDS,
        help="First scan delay after a candle close. Default: 5 seconds.",
    )
    parser.add_argument(
        "--post-close-retry-seconds",
        type=int,
        default=_DEFAULT_POST_CLOSE_RETRY_SECONDS,
        help="Retry interval after the first post-close scan. Default: 5 seconds.",
    )
    parser.add_argument(
        "--post-close-watch-seconds",
        type=int,
        default=_DEFAULT_POST_CLOSE_WATCH_SECONDS,
        help="How long to keep retrying after the first post-close scan. Default: 10 seconds.",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Append console output to this file while still printing to screen.",
    )
    parser.add_argument(
        "--health-interval-minutes",
        type=int,
        default=60,
        help="Deprecated; hourly Telegram health summary is disabled.",
    )
    parser.add_argument(
        "--max-alert-age-minutes",
        type=int,
        default=None,
        help=(
            "Do not send unseen signals older than this. "
            "Default: max(3x timeframe, 120 minutes); skipped signals are marked seen."
        ),
    )
    parser.add_argument(
        "--fallback-scan-seconds",
        type=int,
        default=_DEFAULT_FALLBACK_SCAN_SECONDS,
        help="Safety scan interval when Redis bar_ready is delayed or disabled. Default: env WATCHER_FALLBACK_SCAN_SECONDS or 300.",
    )
    parser.add_argument(
        "--relay-retry-seconds",
        type=int,
        default=_DEFAULT_RELAY_RETRY_SECONDS,
        help="Redis signal relay wake/retry interval. Default: env SIGNAL_RELAY_RETRY_SECONDS or 30.",
    )
    parser.add_argument(
        "--relay-alert-seconds",
        type=int,
        default=_DEFAULT_RELAY_ALERT_SECONDS,
        help="Alert when the oldest pending outbox signal exceeds this age. Default: env SIGNAL_RELAY_ALERT_SECONDS or 300.",
    )
    parser.add_argument("--quiet", action="store_true", help="Hide per-symbol scan progress.")
    return parser.parse_args()


def _build_groups_from_args(args: argparse.Namespace) -> list[dict]:
    """Build scan groups from CLI --symbols and --tf overrides."""
    from core_python.notify.scan_config import TF_POLL_SECONDS

    symbols = [s.strip().upper() for s in args.symbols.replace(";", ",").split(",") if s.strip()]
    tfs = [t.strip().upper() for t in args.tf.replace(";", ",").split(",") if t.strip()]
    groups = []
    for tf in tfs:
        group_bars = args.bars
        overrides: dict[str, Any] = {}
        event_type = None
        if args.strategy == "ai_trend":
            event_type = normalize_ai_trend_event_type(None, tf)
            if event_type == H3_TREND_CHANGE:
                group_bars = min(int(args.bars), 400)
                overrides = {"TREND_BARS": group_bars}
            elif event_type == M45_ENTRY_SIGNAL:
                group_bars = int(args.bars)
                overrides = {"TREND_BARS": 400, "ENTRY_BARS": group_bars}
        groups.append(
            {
                "strategy": args.strategy,
                "symbols": symbols,
                "tf": tf,
                "bars": group_bars,
                "poll_seconds": TF_POLL_SECONDS.get(tf, 300),
                "chat_id": None,
                "overrides": overrides,
                "event_type": event_type,
            }
        )
    return groups


class _WatcherLock:
    """Best-effort single-instance lock for the production watcher process."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            self._lock_handle(handle)
        except OSError as exc:
            handle.close()
            raise RuntimeError(
                f"another signal_watcher instance appears to be running (lock: {self.path})"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\nstarted_utc={pd.Timestamp.now('UTC').isoformat()}\n")
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            self._unlock_handle(handle)
        finally:
            handle.close()
            self._handle = None

    @staticmethod
    def _lock_handle(handle: Any) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_handle(handle: Any) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main() -> int:
    import sys
    import atexit

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except AttributeError:
        pass

    args = _parse_args()

    if args.log_file:
        _tee_console_to_file(args.log_file)

    if args.symbols and args.tf:
        scan_groups = _build_groups_from_args(args)
        mode_desc = f"CLI override - symbols: {args.symbols} | TF: {args.tf}"
    else:
        from core_python.notify.scan_config import SCAN_GROUPS

        scan_groups = SCAN_GROUPS
        mode_desc = "scan_config.py defaults"

    state = SignalState(args.state_path)
    notifier = Notifier(backend=args.backend, dry_run=args.dry_run)
    closed_only = not args.include_open_bar
    current_scan_fingerprint = scan_fingerprint(scan_groups, closed_only)

    if args.warm_up:
        print("Warm-up: marking all current signals as seen (no Telegram send)...", flush=True)
        total = 0
        n_errors = 0
        from core_python.notify.state import signal_key as _sk

        for group in scan_groups:
            for symbol in group["symbols"]:
                try:
                    if group["strategy"] == "ai_trend":
                        alerts, _latest = run_ai_trend_alerts(
                            symbol=symbol,
                            tf=group["tf"],
                            bars=group.get("bars", args.bars),
                            event_type=group.get("event_type"),
                            overrides=group.get("overrides") or {},
                            closed_only=closed_only,
                        )
                        for alert in alerts:
                            key = ai_trend_alert_key(alert)
                            if not state.has(key):
                                state.add(key)
                                total += 1
                        continue

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
                        key = _sk(
                            group["strategy"],
                            symbol,
                            group["tf"],
                            row["bartime"],
                            int(row["signal"]),
                        )
                        if not state.has(key):
                            state.add(key)
                            total += 1
                except Exception as exc:
                    n_errors += 1
                    print(f"  [ERROR] {symbol} {group['tf']}: {exc}", flush=True)

        if n_errors > 0:
            print(
                f"[WARNING] Warm-up had {n_errors} scan error(s). "
                "State sentinel NOT written — DB may be unavailable or symbols incorrect. "
                "Fix the errors and re-run warm-up before starting the watcher.",
                flush=True,
            )
            return 1

        state.set_scan_fingerprint(current_scan_fingerprint)
        print(f"Warm-up complete: {total} signals marked as seen. Now run without --warm-up.")
        return 0

    if scan_groups and not state.path.exists():
        print(
            "[ERROR] Watcher state file not found. Run warm-up before starting production:\n"
            f"  python -m core_python.notify.signal_watcher --warm-up\n"
            f"  (expected state path: {state.path})",
            flush=True,
        )
        return 1
    if scan_groups:
        fingerprint_error = _scan_fingerprint_error(state, current_scan_fingerprint)
        if fingerprint_error:
            print(fingerprint_error, flush=True)
            return 1

    watcher_lock = _WatcherLock(state.path.parent / "signal_watcher.lock")
    try:
        watcher_lock.acquire()
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", flush=True)
        return 1
    atexit.register(watcher_lock.release)

    redis_on = bool(redis_publisher._enabled() and not args.dry_run)
    outbox: Any
    if redis_on:
        outbox = DeliveryOutbox(state.path.parent / "delivery_outbox.json")
    else:
        outbox = NullDeliveryOutbox()
    sent_signals: list[SentSignalEvent] = []

    n_groups = len(scan_groups)
    total_slots = sum(len(g["symbols"]) for g in scan_groups)
    startup_msg = (
        "<b>SEN05 Watcher started</b>\n"
        f"{n_groups} groups / {total_slots} symbol-TF slots"
        + (" / DRY RUN" if args.dry_run else "")
    )
    print(startup_msg.replace("<b>", "").replace("</b>", ""), flush=True)
    print(f"Mode: {mode_desc}", flush=True)
    print(
        "Schedule: Redis bar_ready event-driven"
        + (f", fallback every {args.fallback_scan_seconds}s" if scan_groups else ""),
        flush=True,
    )
    print(f"Redis signal stream: {'ON' if redis_on else 'OFF'}", flush=True)
    print(
        "Signal routing: Combo/MA Cross -> Discord, AI Trend -> Telegram; "
        "--backend none disables sending.",
        flush=True,
    )
    print(_health_summary(state), flush=True)

    if args.once:
        _run_all_groups_once(
            scan_groups=scan_groups,
            args=args,
            state=state,
            notifier=notifier,
            closed_only=closed_only,
            outbox=outbox,
            redis_on=redis_on,
            sent_signals=sent_signals,
        )
        if redis_on and isinstance(outbox, DeliveryOutbox):
            delivered, failed = _drain_outbox_once(outbox)
            print(f"Redis outbox drain: delivered={delivered} failed={failed}", flush=True)
        return 0

    event_queue: queue.Queue = queue.Queue(maxsize=max(1, int(_DEFAULT_QUEUE_MAXSIZE)))
    worker = threading.Thread(
        target=_worker_loop,
        name="SignalWorker",
        kwargs={
            "event_queue": event_queue,
            "scan_groups": scan_groups,
            "args": args,
            "state": state,
            "notifier": notifier,
            "closed_only": closed_only,
            "outbox": outbox,
            "redis_on": redis_on,
            "sent_signals": sent_signals,
        },
        daemon=True,
    )
    worker.start()

    if redis_on and isinstance(outbox, DeliveryOutbox):
        threading.Thread(
            target=_delivery_relay_loop,
            name="DeliveryRelay",
            kwargs={
                "outbox": outbox,
                "notifier": notifier,
                "retry_seconds": args.relay_retry_seconds,
                "alert_seconds": args.relay_alert_seconds,
            },
            daemon=True,
        ).start()
        threading.Thread(
            target=_bar_ready_subscriber_loop,
            name="BarReadySubscriber",
            args=(event_queue,),
            daemon=True,
        ).start()
        _WAKE_RELAY.set()

    threading.Thread(
        target=_fallback_scanner_loop,
        name="FallbackScanner",
        kwargs={
            "event_queue": event_queue,
            "scan_groups": scan_groups,
            "interval_seconds": args.fallback_scan_seconds,
        },
        daemon=True,
    ).start()

    last_metrics_at = 0.0
    while True:
        time.sleep(_IDLE_SLEEP_SECONDS)
        now = time.time()
        if now - last_metrics_at >= 60:
            last_metrics_at = now
            pending_count = outbox.pending_count()
            oldest_age = outbox.oldest_pending_age_seconds()
            oldest_text = "-" if oldest_age is None else f"{int(oldest_age)}s"
            ts = pd.Timestamp.now("UTC").strftime("%H:%M:%S")
            print(
                f"[{ts}] runtime: queue_depth={event_queue.qsize()} "
                f"outbox_pending={pending_count} oldest_pending={oldest_text}",
                flush=True,
            )


def _health_summary(state: SignalState, minutes: int = 60) -> str:
    now = pd.Timestamp.now("UTC")
    cutoff = now - pd.Timedelta(minutes=int(minutes))
    recent: list[tuple[str, pd.Timestamp]] = []
    for key, sent_at in state.sent.items():
        try:
            ts = pd.Timestamp(sent_at)
        except Exception:
            continue
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        if ts >= cutoff:
            recent.append((key, ts))
    recent.sort(key=lambda item: item[1])
    last = recent[-1][0] if recent else "-"
    return (
        f"[{now.strftime('%H:%M:%S')}] HEALTH last {minutes}m: "
        f"signals={len(recent)}, last={last}"
    )


def _format_hourly_symbol_summary(
    sent_events: list[SentSignalEvent],
    scan_groups: list[dict],
    minutes: int = 60,
) -> str:
    """Build a Telegram summary grouped by symbol for production-sent signals."""
    now = pd.Timestamp.now("UTC")
    cutoff = now - pd.Timedelta(minutes=int(minutes))
    recent = [
        event
        for event in sent_events
        if _as_utc_ts(event.sent_at) >= cutoff and _include_in_hourly_summary(event)
    ]
    symbols = _summary_symbols(scan_groups)
    by_symbol: dict[str, list[SentSignalEvent]] = {symbol: [] for symbol in symbols}
    for event in recent:
        by_symbol.setdefault(event.symbol.upper(), []).append(event)

    lines = [
        "<b>SEN05 Hourly Signal Summary</b>",
        "",
        f"Window: <code>{_fmt_summary_time(cutoff)} - {_fmt_summary_time(now)} UTC</code>",
        f"Signals: <b>{len(recent)}</b>",
        "",
        "<b>By symbol</b>",
    ]
    for symbol in sorted(by_symbol):
        symbol_events = sorted(by_symbol[symbol], key=lambda item: item.sent_at)
        if not symbol_events:
            lines.append(f"{html.escape(symbol)}: <code>no new signal</code>")
            continue
        details = "; ".join(_summary_event_text(event) for event in symbol_events)
        lines.append(f"{html.escape(symbol)}: {details}")
    return "\n".join(lines)


def _summary_symbols(scan_groups: list[dict]) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for group in scan_groups:
        for symbol in group.get("symbols", []):
            normalized = str(symbol).upper()
            if normalized in seen:
                continue
            seen.add(normalized)
            symbols.append(normalized)
    return symbols


def _summary_event_text(event: SentSignalEvent) -> str:
    strategy = "AI Trend" if event.strategy == "ai_trend" else event.strategy.title()
    kind = ""
    if event.strategy == "ai_trend":
        if event.kind == H3_TREND_CHANGE:
            kind = " H3"
        elif event.kind == M45_ENTRY_SIGNAL:
            kind = " M45"
    else:
        kind = f" {event.tf.upper()}"
    return (
        f"<code>{html.escape(strategy + kind)} "
        f"{html.escape(event.side.upper())} "
        f"{_fmt_summary_time(event.event_time)}</code>"
    )


def _include_in_hourly_summary(event: SentSignalEvent) -> bool:
    if event.strategy != "ai_trend":
        return True
    return event.kind == M45_ENTRY_SIGNAL


def _fmt_summary_time(value: object) -> str:
    return _as_utc_ts(value).strftime("%Y-%m-%d %H:%M")


class _TeeStream:
    def __init__(self, stream: Any, log_file: Any) -> None:
        self.stream = stream
        self.log_file = log_file
        self.encoding = getattr(stream, "encoding", "utf-8")

    def write(self, data: str) -> int:
        written = self.stream.write(data)
        self.log_file.write(data)
        return written

    def flush(self) -> None:
        self.stream.flush()
        self.log_file.flush()

    def isatty(self) -> bool:
        return bool(getattr(self.stream, "isatty", lambda: False)())


def _tee_console_to_file(path: str | Path) -> None:
    import sys

    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = _TeeStream(sys.stdout, log_file)  # type: ignore[assignment]
    sys.stderr = _TeeStream(sys.stderr, log_file)  # type: ignore[assignment]


def _ensure_state_file(state: SignalState) -> None:
    """Write an empty state file if none exists yet — serves as warm-up sentinel."""
    import json

    if state.path.exists():
        return
    state.path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state.path.with_suffix(state.path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"sent": state.sent, "meta": state.meta}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(state.path)


if __name__ == "__main__":
    raise SystemExit(main())
