"""Backfill orchestration: symbol matching, one reactor-driven backfill
session, and the batched/retrying runner used by scheduled and manual jobs.

Each batch runs as its own ``python -m src backfill`` subprocess
(spawned by ``run_batched_backfill``) rather than an in-process loop:
Twisted's global reactor cannot be restarted once stopped
(``ReactorNotRestartable``), so a fresh interpreter per batch is the
simplest correct way to open a fresh cTrader connection per batch.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.auth import ensure_fresh_access_token
from src.configuration import RemoteSymbol, TargetSymbol, redact_operator_secrets
from src.ctrader_client import (
    fetch_history_side,
    fetch_remote_symbols_with_schedules,
    load_ctrader_sdk,
    new_client,
    send_auth_chain,
    stop_reactor,
)
from src.notify import notify, write_system_event
from src.pipeline import DecodedHistoricalTick, iter_tick_windows, merge_historical_quote_ticks, utc_from_millis
from src.spool import CancelRequested, TickBatcher, TickSpool, cancel_requested, raise_if_cancelled

MS_PER_SECOND, MS_PER_MINUTE = 1000, 60_000
_NON_RETRYABLE_AUTH_MARKERS = (
    "RET_ACCOUNT_DISABLED", "RET_ACCOUNT_NOT_FOUND", "RET_ACCOUNT_NOT_AUTHORIZED",
    "RET_INVALID_ACCOUNT", "cTrader account auth rejected",
)

# ---------------------------------------------------------------------------
# Symbol matching (local universe <-> cTrader account symbol list)
# ---------------------------------------------------------------------------

NORMALIZE_RE = re.compile(r"[^A-Z0-9]+")
LOCAL_ALIASES: dict[str, tuple[str, ...]] = {
    "FR40": ("FR40", "FRA40", "CAC40", "FRANCE40"),
    "DE40": ("DE40", "GER40", "DAX40", "DAX", "GERMANY40"),
    "HK50": ("HK50", "HSI", "HONGKONG50"),
    "J225": ("J225", "JP225", "JPN225", "NI225", "NIKKEI225"),
    "SP35": ("SP35", "ES35", "IBEX35", "SPAIN35", "SPN35"),
    "UK100": ("UK100", "FTSE100", "UKX"),
    "US500": ("US500", "SPX500", "SP500", "USSPX500"),
    "US100": ("US100", "NAS100", "NDX", "USTECH100"),
    "US30": ("US30", "DJ30", "DJI", "DOW30", "USWALLST30"),
    "GOLD": ("GOLD", "XAUUSD", "XAU", "XAU/USD"),
    "BTCUSD": ("BTCUSD", "BTC/USD", "BITCOIN"),
}


@dataclass(frozen=True)
class SymbolMatch:
    target: TargetSymbol
    remote: RemoteSymbol | None
    score: int
    status: str
    reason: str


def _normalize(value: str | None) -> str:
    return NORMALIZE_RE.sub("", str(value or "").upper())


def _score(target: TargetSymbol, remote: RemoteSymbol) -> tuple[int, str]:
    local_norm, remote_norm = _normalize(target.local_symbol), _normalize(remote.symbol_name)
    desc_norm = _normalize(remote.description)
    aliases = {_normalize(a) for a in LOCAL_ALIASES.get(target.local_symbol.upper(), (target.local_symbol,))}
    if remote_norm == local_norm:
        return 100, "exact local symbol"
    if remote_norm in aliases:
        return 96, "exact alias"
    if any(remote_norm.startswith(a) for a in aliases):
        return 88, "symbol starts with alias"
    if any(a in remote_norm for a in aliases):
        return 82, "symbol contains alias"
    if desc_norm and any(a in desc_norm for a in aliases):
        return 76, "description contains alias"
    return 0, "no reliable symbol signal"


def build_symbol_matches(targets: tuple[TargetSymbol, ...], remotes: list[RemoteSymbol], min_score: int = 80) -> list[SymbolMatch]:
    matches = []
    for target in targets:
        ranked = sorted(((_score(target, r), r) for r in remotes), key=lambda item: item[0][0], reverse=True)
        usable = [(score, reason, remote) for (score, reason), remote in ranked if score >= min_score]
        if not usable:
            matches.append(SymbolMatch(target, None, 0, "UNMATCHED", "no candidate above threshold"))
            continue
        top_score, top_reason, top_remote = usable[0]
        second = usable[1] if len(usable) > 1 else None
        if second and second[0] >= top_score - 5:
            matches.append(SymbolMatch(target, top_remote, top_score, "AMBIGUOUS", f"{top_reason}; close alternative {second[2].symbol_name}"))
        else:
            matches.append(SymbolMatch(target, top_remote, top_score, "MATCHED", top_reason))
    return matches


def sync_symbols(settings: Any, store: Any, apply: bool) -> list[str]:
    """Fetch remote symbols, match them against DWH.Dim_Symbol-resolved targets
    (``store.targets``), optionally persist to tick.SymbolMap.

    Also fetches each matched symbol's real trading schedule + holiday
    calendar (see ctrader_client.fetch_remote_symbols_with_schedules) and
    persists it alongside the match -- this is the data notify.py's
    is_market_closed() uses instead of the hand-maintained SESSION_RULES
    fallback table. Both fetches share a single cTrader connection/reactor
    session (Twisted's reactor can only run once per process); a schedule
    parsing/persist failure is logged and swallowed, not raised, so symbol
    matching (the more important half of this command) still gets applied
    even if something about the schedule step goes wrong.
    """
    settings = ensure_fresh_access_token(settings, "symbol sync")
    matches_holder: list[list[Any]] = []

    def _schedule_ids(remotes: list[Any]) -> list[int]:
        matches = build_symbol_matches(tuple(store.targets.values()), remotes)
        matches_holder.append(matches)
        return [m.remote.ctrader_symbol_id for m in matches if m.remote is not None]

    _remotes, schedules = fetch_remote_symbols_with_schedules(settings, schedule_ids=_schedule_ids)
    matches = matches_holder[0] if matches_holder else build_symbol_matches(tuple(store.targets.values()), [])
    if apply:
        store.upsert_symbol_matches(matches)
        if schedules:
            try:
                updated = store.update_symbol_schedules(schedules)
                write_system_event("backfill", "SCHEDULE_SYNC_OK", symbols=updated)
            except Exception as exc:
                write_system_event("backfill", "SCHEDULE_SYNC_FAILED", str(exc), level="WARNING")
    return [
        f"{m.target.local_symbol}: {m.status} score={m.score} "
        f"remote={m.remote.symbol_name if m.remote else 'NONE'} "
        f"id={m.remote.ctrader_symbol_id if m.remote else 'NONE'} reason={m.reason}"
        for m in matches
    ]


# ---------------------------------------------------------------------------
# One reactor-driven backfill session (runs inside a single `backfill` process)
# ---------------------------------------------------------------------------


def _non_retryable_auth_reason(text: str) -> str | None:
    return next((m for m in _NON_RETRYABLE_AUTH_MARKERS if m in text), None)


def _fmt_history_time(ms: int | None) -> str:
    return "-" if ms is None else utc_from_millis(ms).strftime("%Y-%m-%d %H:%M:%SZ")


@dataclass(frozen=True)
class HistoryWindowRequest:
    target: TargetSymbol
    remote: RemoteSymbol
    from_ms: int
    to_ms: int


def run_history_backfill(
    settings: Any, store: Any, from_ms: int, to_ms: int, *,
    symbols: list[str] | None = None, request_timeout_seconds: float | None = None,
    timeout_seconds: int | None = None, notify_enabled: bool = True,
) -> None:
    """Backfill historical BID/ASK ticks for matched symbols over one window range."""
    raise_if_cancelled()
    if from_ms > to_ms:
        raise ValueError("from_ms must be <= to_ms")
    started_mono = time.monotonic()
    settings = ensure_fresh_access_token(settings, "history backfill")
    if request_timeout_seconds:
        from dataclasses import replace
        settings = replace(settings, response_timeout_seconds=float(request_timeout_seconds))
    missing = settings.missing_api_fields
    if missing:
        raise ValueError(f"missing cTrader config fields: {', '.join(missing)}")

    matched = store.fetch_matched_symbols()
    if symbols:
        requested = {s.upper() for s in symbols}
        matched = {k: v for k, v in matched.items() if k in requested}
    if not matched:
        raise RuntimeError("no matched symbols selected for backfill")

    sdk = load_ctrader_sdk()
    client = new_client(settings, sdk)
    spool = TickSpool(settings.spool_path)

    def on_spooled(records: list, spooled: int, exc: Exception) -> None:
        if notify_enabled:
            notify("WARNING", "Tick backfill SQL write failed; ticks were spooled",
                   conclusion="The failed batch was saved to the local spool. No data is lost yet.",
                   action="Check SQL Server connectivity, then rerun backfill once it recovers.",
                   details=[("Spooled ticks", spooled)], technical=[("SQL error", redact_operator_secrets(exc)[:300])],
                   throttle_key="tick-backfill-spool-write-failed", throttle_seconds=300)

    batcher = TickBatcher(store, spool, settings.batch_size, settings.flush_seconds, on_spooled=on_spooled)
    ingest_run_id = store.start_ingest_run("BACKFILL", note=f"{from_ms}->{to_ms}")

    queue: deque[HistoryWindowRequest] = deque()
    for target, remote in matched.values():
        for start_ms, end_ms in iter_tick_windows(from_ms, to_ms):
            queue.append(HistoryWindowRequest(target, remote, start_ms, end_ms))
    total_timeout = int(timeout_seconds) if timeout_seconds else max(120, int(len(queue) * 2 * settings.response_timeout_seconds + 60))
    write_system_event(
        "backfill", "BACKFILL_SESSION_STARTED",
        run=ingest_run_id[:8], env=settings.env, symbols=len(matched),
        window_from=_fmt_history_time(from_ms), window_to=_fmt_history_time(to_ms),
    )

    state: dict[str, object] = {"finished": False, "error": None, "cancelled": False}

    def on_error(failure: Any) -> None:
        if state["finished"]:
            return
        state["finished"] = True
        state["error"] = str(getattr(failure, "value", failure))
        write_system_event("backfill", "BACKFILL_SESSION_FAILED", redact_operator_secrets(state["error"]), level="ERROR", run=ingest_run_id[:8])
        stop_reactor(sdk, client)

    def cancel_backfill() -> bool:
        if not cancel_requested():
            return False
        if state["finished"]:
            return True
        state["cancelled"] = True
        try:
            batcher.flush()
        except Exception as exc:
            state["error"] = str(exc)
        state["finished"] = True
        stop_reactor(sdk, client)
        return True

    def send_next() -> None:
        if cancel_backfill():
            return
        if not queue:
            batcher.flush()
            state["finished"] = True
            stop_reactor(sdk, client)
            return
        item = queue.popleft()

        def on_ask_complete(ask_ticks: list[DecodedHistoricalTick], bid_ticks: list[DecodedHistoricalTick]) -> None:
            # merge_historical_quote_ticks + batcher.flush() (a synchronous,
            # blocking SQL insert) used to run directly on Twisted's reactor
            # thread here. For a large merge (hundreds of thousands of ticks
            # -- a routine size for a multi-hour historical batch) that
            # blocks the reactor for a minute or more, during which it
            # cannot run its own heartbeat LoopingCall or process any
            # network I/O at all, regardless of how short the configured
            # heartbeat interval is -- confirmed live (2026-09-17): a 93s
            # merge/insert gap for one symbol was immediately followed by
            # "Connection was closed cleanly" on the very next request.
            # deferToThread moves the blocking work to a worker thread so
            # the reactor keeps ticking (including its heartbeat) while it
            # runs; Twisted guarantees the callback below still runs back on
            # the reactor thread, so calling client.send() from send_next()
            # remains safe.
            def do_merge_and_insert() -> tuple[list[Any], Any]:
                records, stats = merge_historical_quote_ticks(
                    item.target, item.remote, bid_ticks, ask_ticks,
                    ingest_run_id=ingest_run_id, max_side_age_seconds=settings.max_quote_side_age_seconds,
                )
                for record in records:
                    batcher.add(record)
                batcher.flush()
                store.record_fetch_attempt(item.target.local_symbol, datetime.now(timezone.utc))
                return records, stats

            def on_merged(result: tuple[list[Any], Any]) -> None:
                # Same blanket try/except the old synchronous version relied
                # on: a Deferred's own callback isn't covered by the errback
                # that fires for do_merge_and_insert's exceptions, so without
                # this, an error here (e.g. from send_next() opening the next
                # request) would just be logged by Twisted and silently stop
                # the whole backfill instead of reaching on_error/stop_reactor.
                try:
                    records, stats = result
                    write_system_event(
                        "pipeline", "QUOTE_MERGE_COMPLETED",
                        run=ingest_run_id[:8], symbol=item.target.local_symbol,
                        bid_ticks=stats.bid_ticks, ask_ticks=stats.ask_ticks,
                        quotes=len(records), dropped=stats.dropped_total,
                        inserted_total=batcher.rows_inserted, spooled_total=batcher.rows_spooled,
                    )
                    if stats.dropped_total:
                        write_system_event(
                            "pipeline", "QUOTE_MERGE_DROPPED_TICKS", level="WARNING",
                            run=ingest_run_id[:8], symbol=item.target.local_symbol,
                            dropped_bid_outliers=stats.dropped_bid_outliers, dropped_ask_outliers=stats.dropped_ask_outliers,
                            dropped_unseeded=stats.dropped_unseeded, dropped_stale_side=stats.dropped_stale_side,
                            dropped_crossed=stats.dropped_crossed, dropped_wide_spread=stats.dropped_wide_spread,
                            dropped_duplicate_quote=stats.dropped_duplicate_quote,
                        )
                    send_next()
                except Exception as exc:
                    on_error(exc)

            sdk.deferToThread(do_merge_and_insert).addCallbacks(on_merged, on_error)

        def on_bid_complete(bid_ticks: list[DecodedHistoricalTick]) -> None:
            fetch_history_side(
                settings, sdk, client, account_id=int(settings.account_id), symbol_id=item.remote.ctrader_symbol_id,
                symbol_label=item.target.local_symbol, quote_type="ASK", from_timestamp_ms=item.from_ms,
                to_timestamp_ms=item.to_ms, ingest_run_id=ingest_run_id,
                on_complete=lambda ask_ticks: on_ask_complete(ask_ticks, bid_ticks),
                on_error=on_error, should_abort=cancel_backfill,
            )

        fetch_history_side(
            settings, sdk, client, account_id=int(settings.account_id), symbol_id=item.remote.ctrader_symbol_id,
            symbol_label=item.target.local_symbol, quote_type="BID", from_timestamp_ms=item.from_ms,
            to_timestamp_ms=item.to_ms, ingest_run_id=ingest_run_id,
            on_complete=on_bid_complete, on_error=on_error, should_abort=cancel_backfill,
        )

    client.setConnectedCallback(lambda _c: send_auth_chain(settings, sdk, client, send_next, on_error))
    client.setDisconnectedCallback(lambda _c, reason: None if state["finished"] else on_error(reason))
    client.startService()
    sdk.reactor.callLater(total_timeout, lambda: on_error(TimeoutError("history backfill timed out")))
    sdk.reactor.run()

    if state["cancelled"]:
        store.finish_ingest_run(ingest_run_id, status="STOPPED", rows_inserted=batcher.rows_inserted,
                                 rows_spooled=batcher.rows_spooled, note="cancel requested")
        raise CancelRequested("history backfill cancelled")
    if state["error"]:
        store.finish_ingest_run(ingest_run_id, status="FAILED", rows_inserted=batcher.rows_inserted,
                                 rows_spooled=batcher.rows_spooled, note=str(state["error"]))
        if notify_enabled:
            notify("ERROR", "Tick backfill failed", conclusion="Backfill failed before completion.",
                   action="Check logs, cTrader token, network, and database connectivity.",
                   technical=[("Error", redact_operator_secrets(state["error"])[:600])],
                   throttle_key="tick-backfill-failed", throttle_seconds=60)
        raise RuntimeError(f"history backfill failed: {state['error']}")
    store.finish_ingest_run(ingest_run_id, status="DONE", rows_inserted=batcher.rows_inserted, rows_spooled=batcher.rows_spooled)
    write_system_event(
        "backfill", "BACKFILL_SESSION_COMPLETED",
        run=ingest_run_id[:8], symbols=len(matched), inserted=batcher.rows_inserted, spooled=batcher.rows_spooled,
        duration_seconds=round(time.monotonic() - started_mono, 3),
    )


# ---------------------------------------------------------------------------
# Batch window planning + retrying multi-batch runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackfillBatch:
    index: int
    total: int
    request_from_ms: int
    to_ms: int


def iter_backfill_batches(from_ms: int, to_ms: int, *, batch_minutes: int, overlap_seconds: int) -> list[BackfillBatch]:
    if from_ms > to_ms:
        raise ValueError("from_ms must be <= to_ms")
    batch_ms, overlap_ms = int(batch_minutes) * MS_PER_MINUTE, int(overlap_seconds) * MS_PER_SECOND
    raw: list[tuple[int, int]] = []
    cursor = from_ms
    while True:
        end_ms = min(to_ms, cursor + batch_ms)
        request_from = max(from_ms, cursor - overlap_ms) if raw else from_ms
        raw.append((request_from, end_ms))
        if end_ms >= to_ms:
            break
        cursor = end_ms
    total = len(raw)
    return [BackfillBatch(i, total, req_from, end) for i, (req_from, end) in enumerate(raw, start=1)]


def default_progress_path(from_ms: int, to_ms: int) -> Path:
    from src.configuration import RUN_DIR

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return RUN_DIR / "backfill_batches" / f"manual_backfill_{stamp}_{from_ms}_{to_ms}.json"


def _write_progress(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _run_batch_subprocess(batch: BackfillBatch, *, symbols: list[str] | None, request_timeout: float | None,
                           timeout_per_batch: int | None, wait_lock_seconds: int) -> tuple[int, str | None]:
    from src.configuration import APP_ROOT
    from src.spool import CANCEL_ENV

    # Frozen (PyInstaller) builds: sys.executable IS the app itself, not a
    # python.exe to hand `-B -m src ...` interpreter flags to -- it re-invokes
    # itself with a plain CLI subcommand instead, which the frozen entry
    # point (scripts/windows/tick_program_entry.py) dispatches straight to
    # src.__main__.main() before considering its service/menu/watchdog modes.
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "backfill",
               "--from", str(batch.request_from_ms), "--to", str(batch.to_ms),
               "--wait-lock-seconds", str(max(0, int(wait_lock_seconds))), "--no-notify"]
    else:
        cmd = [sys.executable, "-u", "-B", "-m", "src", "backfill",
               "--from", str(batch.request_from_ms), "--to", str(batch.to_ms),
               "--wait-lock-seconds", str(max(0, int(wait_lock_seconds))), "--no-notify"]
    if symbols:
        cmd += ["--symbols", *symbols]
    if request_timeout is not None:
        cmd += ["--request-timeout", str(float(request_timeout))]
    if timeout_per_batch is not None:
        cmd += ["--timeout", str(int(timeout_per_batch))]
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(cmd, cwd=str(APP_ROOT), env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    fatal_reason = None
    assert proc.stdout is not None
    for line in proc.stdout:
        clean = line.rstrip("\r\n")
        if clean:
            fatal_reason = fatal_reason or _non_retryable_auth_reason(clean)
            print(clean, flush=True)
    return int(proc.wait()), fatal_reason


def run_batched_backfill(
    *, from_ms: int, to_ms: int, symbols: list[str] | None = None, batch_minutes: int = 60,
    overlap_seconds: int = 60, wait_lock_seconds: int = 300, request_timeout: float | None = None,
    timeout_per_batch: int | None = None, sleep_seconds: float = 0.0, max_attempts: int = 3,
    retry_sleep_seconds: float = 10.0, retry_sleep_max_seconds: float = 90.0,
    notify_summary: bool = False, notify_success_summary: bool = True,
    progress_path: Path | None = None, dry_run: bool = False,
) -> int:
    """Run a backfill as short, independently-reconnecting batches (each its own subprocess)."""
    from src.spool import CANCEL_ENV, cancel_file_for, clear_cancel_file

    started_mono = time.monotonic()
    batches = iter_backfill_batches(from_ms, to_ms, batch_minutes=batch_minutes, overlap_seconds=overlap_seconds)
    if not dry_run:
        write_system_event(
            "backfill", "BATCHED_BACKFILL_STARTED",
            batches=len(batches), window_from=_fmt_history_time(from_ms), window_to=_fmt_history_time(to_ms),
        )
    if not os.environ.get(CANCEL_ENV):
        cancel_file = cancel_file_for("manual-backfill")
        clear_cancel_file(cancel_file)
        os.environ[CANCEL_ENV] = str(cancel_file)
    progress_path = progress_path or default_progress_path(from_ms, to_ms)
    state: dict[str, object] = {
        "status": "DRY_RUN" if dry_run else "RUNNING", "host": socket.gethostname(), "process_id": os.getpid(),
        "from_ms": from_ms, "to_ms": to_ms, "batches": [],
    }
    if dry_run:
        return 0
    _write_progress(progress_path, state)

    for batch in batches:
        raise_if_cancelled()
        record: dict[str, object] = {"index": batch.index, "total": batch.total, "status": "RUNNING"}
        state["batches"].append(record)  # type: ignore[attr-defined]
        _write_progress(progress_path, state)
        rc = 1
        for attempt in range(1, int(max_attempts) + 1):
            raise_if_cancelled()
            rc, fatal_reason = _run_batch_subprocess(
                batch, symbols=symbols, request_timeout=request_timeout,
                timeout_per_batch=timeout_per_batch, wait_lock_seconds=wait_lock_seconds,
            )
            if rc == 0 or fatal_reason:
                if fatal_reason:
                    write_system_event(
                        "backfill", "BATCH_RETRY_STOPPED", fatal_reason, level="ERROR",
                        batch=f"{batch.index}/{batch.total}",
                    )
                break
            if attempt < max_attempts and retry_sleep_seconds:
                delay = min(retry_sleep_max_seconds or retry_sleep_seconds, retry_sleep_seconds * (3 ** (attempt - 1)))
                write_system_event(
                    "backfill", "BATCH_RETRY_SCHEDULED", level="WARNING",
                    batch=f"{batch.index}/{batch.total}", attempt=attempt, max_attempts=max_attempts,
                    exit_code=rc, sleep_seconds=round(delay, 1),
                )
                time.sleep(delay)
        record["status"] = "DONE" if rc == 0 else "FAILED"
        _write_progress(progress_path, state)
        write_system_event(
            "backfill", "BATCH_COMPLETED" if rc == 0 else "BATCH_FAILED", level="INFO" if rc == 0 else "ERROR",
            batch=f"{batch.index}/{batch.total}", exit_code=rc,
        )
        if rc != 0:
            state["status"] = "FAILED"
            _write_progress(progress_path, state)
            if notify_summary:
                notify("ERROR", "Tick batched backfill failed",
                       conclusion="A scheduled batched backfill failed after all retry attempts.",
                       details=[("Batch", f"{batch.index}/{batch.total}"), ("Exit code", rc)],
                       throttle_key="tick-batched-backfill-failed", throttle_seconds=900)
            return rc
        if sleep_seconds and batch.index < batch.total:
            time.sleep(sleep_seconds)

    state["status"] = "DONE"
    _write_progress(progress_path, state)
    write_system_event(
        "backfill", "BATCHED_BACKFILL_COMPLETED",
        batches=len(batches), duration_seconds=round(time.monotonic() - started_mono, 3),
    )
    if notify_summary and notify_success_summary:
        notify("INFO", "Tick batched backfill completed", conclusion="The scheduled batched backfill completed all batches.",
               details=[("Batches", len(batches))], throttle_key="tick-batched-backfill-completed",
               throttle_seconds=3600)
    return 0


def run_gap_fill_backfill(
    settings: Any, store: Any, *, max_lookback_days: int, batch_minutes: int = 360,
    overlap_seconds: int = 60, delay_seconds: int = 120, wait_lock_seconds: int = 0,
    request_timeout: float | None = None, timeout_per_batch: int | None = None,
    max_attempts: int = 5, retry_sleep_seconds: float = 15.0, retry_sleep_max_seconds: float = 180.0,
    notify_summary: bool = False,
) -> int:
    """Backfill from the oldest per-symbol watermark to now.

    Self-healing regardless of how long the service was down: queries
    tick.IngestState for the real watermark instead of guessing a fixed
    lookback window. Capped at ``max_lookback_days`` so a completely empty
    IngestState (fresh install) or a data-loss edge case can't trigger an
    unbounded historical pull.
    """
    now = datetime.now(timezone.utc) - timedelta(seconds=delay_seconds)
    floor = now - timedelta(days=max_lookback_days)
    watermark = store.min_ingest_watermark()
    capped = watermark is None or watermark < floor
    from_dt = floor if capped else watermark
    from_dt = min(from_dt, now)
    from_ms, to_ms = int(from_dt.timestamp() * 1000), int(now.timestamp() * 1000)
    write_system_event(
        "backfill", "GAP_FILL_PLANNED",
        watermark=str(watermark) if watermark else "none", capped=capped, max_lookback_days=max_lookback_days,
        window_from=_fmt_history_time(from_ms), window_to=_fmt_history_time(to_ms),
    )
    return run_batched_backfill(
        from_ms=from_ms, to_ms=to_ms, batch_minutes=batch_minutes, overlap_seconds=overlap_seconds,
        wait_lock_seconds=wait_lock_seconds, request_timeout=request_timeout, timeout_per_batch=timeout_per_batch,
        max_attempts=max_attempts, retry_sleep_seconds=retry_sleep_seconds, retry_sleep_max_seconds=retry_sleep_max_seconds,
        notify_summary=notify_summary, progress_path=default_progress_path(from_ms, to_ms),
    )
