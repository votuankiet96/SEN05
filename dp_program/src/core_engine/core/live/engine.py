"""Live OHLCV batch fetch engine.

Inputs:
- config/dp_provider.env and core_engine.settings.instruments symbols/timeframes
- TradingView auth/token/cookie runtime
- SQL Server warehouse connection

Process:
- every interval, open short-lived TradingView websocket batches
- accept only closed bars newer than committed watermarks
- queue, overflow, or durable-spool bars until DB writes complete
- defer Fact merges while historical maintenance holds the warehouse lock

Outputs:
- DWH.Fact_OHLCV rows
- runtime/logs/live.log
- runtime/run/ws_live_state.json
"""

import atexit
import logging
import math
import os
import socket
import threading
import time
import traceback
from datetime import datetime, timezone

from core_engine.util.primitives.exit_codes import EXIT_LOCK_CONFLICT
from core_engine.util.notify.discord import sanitize_ssl_keylogfile
sanitize_ssl_keylogfile()

from core_engine.util.coordination.locks import (
    acquire as _acquire_task_lock,
    cleanup_stale_lock,
    fetch_lock as _fetch_task_lock,
    is_locked as _is_task_locked,
    release as _release_task_lock,
    renew as _renew_task_lock,
)
from core_engine.util.notify.discord import (
    QUICK_COMMANDS_HINT,
    notify_live_event,
    send_alert as _send_alert,
)
from core_engine.shared.warehouse.connection import get_connection, test_connection

from core_engine.settings import (
    LIVE,
    STORAGE,
    NOTIFICATION,
    SYMBOLS,
    SYMBOL_OVERNIGHT_MINS,
    TRADINGVIEW,
    LIVE_LOG,
    WS_LIVE_STATE,
)

from core_engine.core.live.telemetry import (
    log_block as _log_block,
    log_report_block as _log_report_block,
    log_report_text as _log_report_text,
    logger,
    operation_line as _llog,
    reporter as _reporter,
    start_candle_table as _start_candle_table,
    summarize_backlog as _summarize_backlog,
    summarize_counts_by_symbol as _summarize_counts_by_symbol,
    summarize_counts_by_tf as _summarize_counts_by_tf,
    summarize_pair_counts as _summarize_pair_counts,
    write_live_summary as _write_live_summary,
)
from core_engine.core.live.scheduler import run_aligned_schedule
from core_engine.core.live.delivery import (
    _db_worker,
    _etl_worker,
    _fact_backlog_size,
    _lease_spool_into_queue,
    _report_db_worker_stopped_at_shutdown,
    _wait_for_queue_drain,
)
from core_engine.core.live.fetcher import BatchFetcher
from core_engine.core.live.telemetry import (
    live_next_batch_block,
    live_start_block,
)
from core_engine.util.redis_io.candle_snapshot import seed_candle_snapshots, set_recovery_callback
from core_engine.core.live.runtime import (
    freshness_alert_threshold_minutes,
    freshness_threshold_minutes,
    is_market_expected_live,
    run_auth_preflight,
    runtime_payload,
)
from core_engine.core.live import runtime as _runtime
from core_engine.core.live.runtime import (
    _GUEST_ALERT_THRESHOLD,
    _backlog,
    _backlog_lock,
    _db_queue,
    _db_worker_done,
    _etl_wakeup,
    _hourly_lock,
    _hourly_stats,
    _last_bar_ts,
    _overflow_buf,
    _overflow_lock,
    _requires_backfill,
    _runtime_lock,
    _shutdown,
    _source_bar_ts,
    _spool,
    _spool_pending,
    _state_heartbeat_loop,
    _state_lock,
    _stats,
    _tradingview_connectivity_ok,
    _write_live_state,
    init_batch_metrics as _init_batch_metrics,
    wait_for_batch_db as _wait_for_batch_db,
)
from core_engine.shared.time import as_utc_timestamp, utc_iso as _utc_iso
from core_engine.util.logkit import set_context

from core_engine.shared.tradingview import auth as _tv_auth

from core_engine.shared.tradingview import history_client as _tv_ws_history
from core_engine.util.coordination.locks import (
    WS_LIVE_SHUTDOWN_GRACE_SECONDS,
    acquire_live_batch_window,
    cleanup_orphan_live_batch_window,
    is_ws_live_shutdown_requested,
    release_live_batch_window,
    request_ws_live_shutdown,
)


class LiveProcessRecycleRequired(RuntimeError):
    """Fatal live-child condition that only an OS process restart can heal."""


def _resolve_ws_symbols(symbols: list[dict], asset_types: tuple[str, ...]) -> list[dict]:
    return [s for s in symbols if s["asset_type"] in set(asset_types)]


def _check_expected_live_symbol_count(count: int, expected: int, asset_types: tuple[str, ...]) -> None:
    """Refuse a live universe that differs from the reviewed system contract."""
    if expected and count != expected:
        raise RuntimeError(
            f"The system contract expects {expected} live symbols in "
            f"{','.join(asset_types)}, "
            f"currently resolves to {count} symbol(s) from instruments.py. Refusing to start "
            "rather than silently watching a different live universe than expected - update "
            "settings/system.py or instruments.py only after the changed business scope has "
            "been reviewed and approved."
        )


# The live universe is a reviewed system contract, not an operator toggle.
# FOREX remains historical-only by business decision.
WS_SYMBOLS = _resolve_ws_symbols(SYMBOLS, LIVE.asset_types)

_check_expected_live_symbol_count(len(WS_SYMBOLS), LIVE.expected_symbol_count, LIVE.asset_types)

_live_settings = LIVE

WS_SYMBOLS_PER_CONN = _live_settings.symbols_per_conn
BATCH_INTERVAL_MIN = _live_settings.batch_interval_min
SHUTDOWN_POLL_SEC = _live_settings.shutdown_poll_sec
BATCH_FETCH_TIMEOUT = _live_settings.batch_fetch_timeout_sec
BATCH_GROUP_JOIN_TIMEOUT_SEC = _live_settings.batch_group_join_timeout_sec
GROUP_WEDGE_HARD_DEADLINE_BATCHES = _live_settings.group_wedge_hard_deadline_batches
STATE_HEARTBEAT_SEC = _live_settings.state_heartbeat_sec
TV_WS_GUEST_POLICY = _live_settings.guest_policy
TV_WS_GUEST_PAUSE_SEC = _live_settings.guest_pause_sec
TV_WS_PREFLIGHT_REQUIRE_HEADLESS = _live_settings.preflight_require_headless
TV_WS_CONNECTIVITY_PREFLIGHT = _live_settings.connectivity_preflight
TV_WS_CONNECTIVITY_TIMEOUT_SEC = _live_settings.connectivity_timeout_sec
TV_WS_CONNECTIVITY_COOLDOWN_SEC = _live_settings.connectivity_cooldown_sec


STATUS_INTERVAL_SEC = _live_settings.status_interval_sec
MAX_SPOOL_ROWS = _live_settings.max_spool_rows

WS_TF_INTERVAL = _tv_ws_history.get_ws_interval_map()

WS_TF_CODES = tuple(WS_TF_INTERVAL.keys())

_configured_symbols_per_conn = WS_SYMBOLS_PER_CONN

_symbols_per_conn_cap = max(1, 90 // max(1, len(WS_TF_CODES)))

WS_SYMBOLS_PER_CONN = min(_configured_symbols_per_conn, _symbols_per_conn_cap)

WS_SYMBOLS_PER_CONN_CAPPED = WS_SYMBOLS_PER_CONN < _configured_symbols_per_conn

WS_SYMBOL_IDS = tuple(s["symbol_id"] for s in WS_SYMBOLS)

WS_WATCH_KEYS = frozenset((sid, tf_code) for sid in WS_SYMBOL_IDS for tf_code in WS_TF_CODES)

_SYMBOL_META_BY_ID = {s["symbol_id"]: s for s in WS_SYMBOLS}

def _refresh_watermarks_from_fact(reason: str = "refresh") -> int:
    loaded = 0

    if not WS_SYMBOL_IDS or not WS_TF_CODES:
        return 0

    ws_symbol_ids = WS_SYMBOL_IDS

    ws_tf_codes = WS_TF_CODES

    sym_placeholders = ",".join("?" * len(ws_symbol_ids))

    tf_placeholders = ",".join("?" * len(ws_tf_codes))

    params = [*ws_symbol_ids, *ws_tf_codes]

    conn = None

    try:
        conn = get_connection()

        cursor = conn.cursor()

        cursor.execute(
            f"""
            SELECT f.SymbolID, tf.Code, MAX(f.BarTime)
            FROM DWH.Fact_OHLCV f
            JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
            WHERE f.SymbolID IN ({sym_placeholders})
              AND tf.Code IN ({tf_placeholders})
              AND f.BarTime < DATEADD(minute, 1, GETUTCDATE())
            GROUP BY f.SymbolID, tf.Code
        """,
            params,
        )

        updates: dict[tuple[int, str], float] = {}

        for symbol_id, tf_code, max_bt in cursor.fetchall():
            if max_bt is not None:
                key = (int(symbol_id), str(tf_code))

                if key in WS_WATCH_KEYS:
                    updates[key] = _as_utc_timestamp(max_bt)

        with _state_lock:
            for key, max_ts in updates.items():
                _last_bar_ts[key] = max(max_ts, _last_bar_ts.get(key, 0.0))

        loaded = len(updates)

        logger.info("%s", _llog("Latest saved candle index refreshed", trigger=reason, entries=loaded, result="ready"))

    except Exception as exc:
        logger.warning("%s", _llog("Latest saved candle index refresh failed", trigger=reason, reason=exc, result="warning"))

    finally:
        if conn is not None:
            conn.close()

    return loaded

def _load_watermarks() -> None:
    logger.info("%s", _llog("Loading latest saved candle index", source="DWH.Fact_OHLCV", result="started"))

    loaded = _refresh_watermarks_from_fact("startup")

    logger.info("%s", _llog("Latest saved candle index loaded", entries=loaded, result="ready"))

    now_dt = datetime.now(timezone.utc)

    now_ts = now_dt.timestamp()

    stale = [
        (sym_id, tf_code, (now_ts - wm_ts) / 60)

        for (sym_id, tf_code), wm_ts in _last_bar_ts.items()

        if (sym_id, tf_code) in WS_WATCH_KEYS

        if _is_market_expected_live(sym_id, now_dt)

        if (now_ts - wm_ts) / 60 > _freshness_threshold_minutes(sym_id, tf_code)
    ]

    if stale:
        worst = max(stale, key=lambda x: x[2])
        worst_symbol = next(
            (s["tv_symbol"] for s in WS_SYMBOLS if s["symbol_id"] == worst[0]),
            f"SymbolID={worst[0]}",
        )

        logger.warning(
            "%s",
            _llog(
                "Old data detected at startup",
                stale_pairs=len(stale),
                worst_symbol_id=worst[0],
                worst_timeframe=worst[1],
                worst_age_minutes=round(worst[2]),
                recommended_action="run_historical_gap",
                result="warning",
            ),
        )

        _send_alert(
            "WARNING",
            "Live feed started with outdated data\n"
            f"Pairs affected: {len(stale)}\n"
            f"Worst pair: {worst_symbol}/{worst[1]} ({worst[2]:.0f} minutes old)\n"
            "Suggested action: run historical backfill.",
        )

_as_utc_timestamp = as_utc_timestamp

def _is_market_expected_live(symbol_id: int, now_utc: datetime) -> bool:
    return is_market_expected_live(_SYMBOL_META_BY_ID, symbol_id, now_utc)

def _freshness_threshold_minutes(symbol_id: int, tf_code: str) -> int:
    return freshness_threshold_minutes(
        _SYMBOL_META_BY_ID,
        SYMBOL_OVERNIGHT_MINS,
        symbol_id,
        tf_code,
    )

def _freshness_alert_threshold_minutes(symbol_id: int, tf_code: str) -> int:
    return freshness_alert_threshold_minutes(
        _SYMBOL_META_BY_ID,
        SYMBOL_OVERNIGHT_MINS,
        symbol_id,
        tf_code,
    )

def _cleanup_dead_ws_live_runtime_lock() -> bool:
    return cleanup_stale_lock("ws_live_runtime")

_acquire_local_runtime_lock = _runtime_lock.acquire

_release_local_runtime_lock = _runtime_lock.release

_ws_live_runtime_payload = runtime_payload

def _update_guest_mode_counter(is_guest: bool) -> None:

    if is_guest:
        _runtime._consecutive_guest_batches += 1

        if _runtime._consecutive_guest_batches >= _GUEST_ALERT_THRESHOLD:
            logger.error(
                "%s",
                _llog(
                    "TradingView limited login repeated",
                    consecutive_batches=_runtime._consecutive_guest_batches,
                    risk="data_depth_may_be_limited",
                    result="warning",
                ),
            )

            _send_alert(
                "WARNING",
                "TradingView session is limited\n"
                f"Consecutive live batches: {_runtime._consecutive_guest_batches}\n"
                "Meaning: data depth may be limited or some premium symbols may not return enough candles.\n"
                "Suggested action: refresh TradingView login if this repeats during market hours.",
            )

    else:
        if _runtime._consecutive_guest_batches >= _GUEST_ALERT_THRESHOLD:
            logger.info(
                "%s",
                _llog("TradingView login recovered", previous_limited_batches=_runtime._consecutive_guest_batches, result="healthy"),
            )

        _runtime._consecutive_guest_batches = 0

def _spool_full_blocks_batch() -> bool:
    """Pause unless the durable outbox can admit one complete live batch.

    One spool row contains all newly accepted bars for one symbol/timeframe,
    so the worst normal batch needs one row per configured live pair. Waiting
    until ``persist_pending()`` returns ``None`` is one item too late: that
    item has already been accepted from TradingView but cannot be persisted.
    Reserve the whole batch up front and resume only after that capacity is
    available again.
    """
    pending = _spool.count()
    if pending is None:
        _runtime.spool_full_pause = True
        logger.critical(
            "%s",
            _llog("Live batch paused: durable outbox is not readable", result="paused"),
        )
        return True

    reserve_rows = min(MAX_SPOOL_ROWS, len(WS_SYMBOLS) * len(WS_TF_CODES))
    has_capacity = pending + reserve_rows <= MAX_SPOOL_ROWS
    if has_capacity:
        if _runtime.spool_full_pause:
            _runtime.spool_full_pause = False
            logger.warning(
                "%s",
                _llog(
                    "Durable outbox capacity restored - batches resumed",
                    pending=pending,
                    reserved_rows=reserve_rows,
                    result="resumed",
                ),
            )
        return False

    _runtime.spool_full_pause = True
    logger.warning(
        "%s",
        _llog(
            "Live batch paused: durable outbox capacity reserved",
            pending=pending,
            capacity=MAX_SPOOL_ROWS,
            required_free_rows=reserve_rows,
            result="skipped_this_batch",
        ),
    )

    return True


def _guest_mode_blocks_batch() -> bool:
    is_guest = _tv_auth.is_guest_mode()

    _update_guest_mode_counter(is_guest)

    if not is_guest:
        return False

    if TV_WS_GUEST_POLICY == "allow":
        return False

    logger.warning("%s", _llog("TradingView limited login detected", action="refresh_before_batch", result="recovering"))

    _tv_auth.renew(logger)

    recovered = not _tv_auth.is_guest_mode()

    if recovered:
        _update_guest_mode_counter(False)

        return False

    if TV_WS_GUEST_POLICY == "abort":
        logger.error("%s", _llog("Live batch blocked by limited TradingView login", policy="abort", result="stopping"))

        _send_alert(
            "ERROR",
            "Live feed stopped because TradingView login is not valid\n"
            "Policy: stop instead of running with limited access\n"
            "Suggested action: refresh TradingView login, then start live feed again.",
        )

        _shutdown.set()

        return True

    logger.warning(
        "%s",
        _llog(
            "Live batch paused by limited TradingView login",
            policy="pause",
            wait_seconds=TV_WS_GUEST_PAUSE_SEC,
            result="skipped_this_batch",
        ),
    )

    _shutdown.wait(max(1, TV_WS_GUEST_PAUSE_SEC))

    return True


def _classify_group_batch_outcomes(
    groups: list,
    *,
    hard_deadline_batches: int,
    unrequested_group_ids: set[int] | None = None,
) -> tuple[list[str], list[int]]:
    """After _run_batch has requested a batch from every group's persistent
    worker and waited up to BATCH_GROUP_JOIN_TIMEOUT_SEC, classify which
    groups are still busy (stuck for this cycle) and which have been busy
    for hard_deadline_batches CONSECUTIVE cycles in a row (wedged - a
    forced socket close already failed to unblock them, so nothing short
    of exiting the process can reclaim that thread). Also updates each
    group's _consecutive_stuck_batches counter as a side effect.

    Split out from _run_batch, and accepting duck-typed group objects
    (only .group_id/._busy/._requires_process_recycle/
    ._consecutive_stuck_batches are read/written),
    purely so this decision is unit-testable without needing a real
    BatchFetcher/websocket stack.
    """
    stuck_group_names: list[str] = []

    wedged_group_ids: list[int] = []

    unrequested = unrequested_group_ids or set()

    for g in groups:
        if g.group_id in unrequested or g._busy or g._requires_process_recycle:
            stuck_group_names.append(f"G{g.group_id}")

            g._consecutive_stuck_batches += 1

            if g._consecutive_stuck_batches >= hard_deadline_batches:
                wedged_group_ids.append(g.group_id)
        else:
            g._consecutive_stuck_batches = 0

    return stuck_group_names, wedged_group_ids


def _ws_recovery_metrics_snapshot() -> dict[str, int]:
    with _state_lock:
        return {
            "ws_forced_socket_closes": int(_stats.get("ws_forced_socket_closes", 0)),
            "ws_orphaned_threads": int(_stats.get("ws_orphaned_threads", 0)),
            "ws_wedged_group_recycles": int(_stats.get("ws_wedged_group_recycles", 0)),
        }


def _run_batch(groups: list[BatchFetcher]) -> None:
    ok, connectivity_detail = _tradingview_connectivity_ok()

    if not ok:
        logger.warning(
            "%s",
            _llog("TradingView network check failed", reason=connectivity_detail, result="batch_skipped"),
        )

        _write_live_state(
            status="network_blocked",
            network_error=connectivity_detail,
            blocked_until=datetime.fromtimestamp(
                _runtime._tv_connectivity_block_until, tz=timezone.utc
            ).isoformat()

            if _runtime._tv_connectivity_block_until

            else None,
        )

        _send_alert(
            "WARNING",
            "Live feed is waiting for TradingView to become reachable\n"
            f"Reason: {connectivity_detail}\n"
            f"Next retry: about {TV_WS_CONNECTIVITY_COOLDOWN_SEC}s",
        )

        return

    if _guest_mode_blocks_batch():
        return

    if _spool_full_blocks_batch():
        return

    with _state_lock:
        _stats["batches_run"] += 1

        batch_id = _stats["batches_run"]

    set_context(batch_id=batch_id, correlation_id=batch_id)
    _init_batch_metrics(batch_id)

    batch_start_dt = datetime.now(timezone.utc)
    batch_start = batch_start_dt.strftime("%H:%M:%S")
    batch_start_iso = batch_start_dt.isoformat()

    logger.info("%s", _llog("Live batch started", batch=batch_id, started_at=batch_start, connection_groups=len(groups), result="running"))
    _start_candle_table(batch_id)

    _write_live_state(
        status="batch_running",
        batch_id=batch_id,
        batch_started_at=batch_start_iso,
        batch_group_timeout_sec=BATCH_GROUP_JOIN_TIMEOUT_SEC,
        groups=len(groups),
        **_ws_recovery_metrics_snapshot(),
    )

    live_batch_lock = acquire_live_batch_window(logger)

    # Signal every group's persistent worker (started once in main() via
    # BatchFetcher.start_worker()) instead of spawning a fresh batch-g{id}
    # thread here every cycle - see BatchFetcher._worker_loop.
    requested_groups: list[BatchFetcher] = []
    unrequested_group_ids: set[int] = set()

    for g in groups:
        if g.request_batch(batch_id):
            requested_groups.append(g)
        else:
            # The prior cycle did not unwind despite its cancellation, or an
            # orphaned websocket already requires process recycling.  Never
            # overwrite _pending_batch_id/_batch_complete in that state.
            unrequested_group_ids.add(g.group_id)

    stuck_group_names: list[str] = []

    wedged_group_ids: list[int] = []

    try:
        deadline = time.monotonic() + BATCH_GROUP_JOIN_TIMEOUT_SEC

        for g in requested_groups:
            remaining = max(0.0, deadline - time.monotonic())

            g._batch_complete.wait(timeout=remaining)

        stuck_group_names, wedged_group_ids = _classify_group_batch_outcomes(
            groups,
            hard_deadline_batches=GROUP_WEDGE_HARD_DEADLINE_BATCHES,
            unrequested_group_ids=unrequested_group_ids,
        )

        for g in groups:
            if f"G{g.group_id}" in stuck_group_names:
                g.abandon_batch(batch_id)

        if stuck_group_names:
            _write_live_state(
                status="batch_stale_released",
                batch_id=batch_id,
                stale_threads=stuck_group_names,
                batch_completed_at=_utc_iso(),
            )

    finally:
        release_live_batch_window(live_batch_lock)

    if stuck_group_names:
        logger.error(
            "%s",
            _llog(
                "Live batch exceeded safe time limit",
                batch=batch_id,
                limit_seconds=BATCH_GROUP_JOIN_TIMEOUT_SEC,
                stuck_groups=", ".join(stuck_group_names),
                result="released_for_next_batch",
            ),
        )

        _send_alert(
            "ERROR",
            "Live feed batch took too long and was released safely\n"
            f"Batch: #{batch_id}\n"
            f"Stuck worker groups: {', '.join(stuck_group_names)}\n"
            "Meaning: this batch was abandoned so the next jobs are not blocked.",
        )

    if wedged_group_ids:
        # A worker thread that is STILL busy after GROUP_WEDGE_HARD_
        # DEADLINE_BATCHES consecutive scheduled batches - even after
        # fetch()'s own timeout path already tried a forced raw-socket
        # close - is blocked in a native call Python cannot reclaim from
        # inside this process. The only guaranteed reclamation is exiting
        # the process: the supervisor's existing restart/backoff (P0-4)
        # then starts a fresh one, which is a full, clean OS-level
        # recycle of every thread/socket/fd this process was holding.
        detail = ", ".join(f"G{gid}" for gid in wedged_group_ids)

        logger.critical(
            "%s",
            _llog(
                "Connection group worker wedged past hard deadline",
                groups=detail,
                consecutive_batches=GROUP_WEDGE_HARD_DEADLINE_BATCHES,
                result="recycling_process",
            ),
        )

        _send_alert(
            "CRITICAL",
            "Live feed is recycling itself to reclaim a stuck connection\n"
            f"Wedged group(s): {detail}\n"
            f"Unresponsive for {GROUP_WEDGE_HARD_DEADLINE_BATCHES} consecutive batches, "
            "even after a forced socket close.\n"
            "This process will exit now; the DP supervisor will start a replacement." + QUICK_COMMANDS_HINT,
        )

        with _state_lock:
            _stats["ws_wedged_group_recycles"] = _stats.get("ws_wedged_group_recycles", 0) + 1

        _shutdown.set()

        # _run_batch executes on the critical live-batch-loop thread.  An
        # uncaught exception is intentional here: the installed thread
        # excepthook marks the live child failed, yielding exit code 1 so the
        # supervisor records a crash/restart instead of a clean operator stop.
        raise LiveProcessRecycleRequired(f"unreclaimable websocket group(s): {detail}")

    total_new = sum(g._new_bars_count for g in groups)

    batch_pair_bars: dict[tuple[int, str], int] = {}

    for g in groups:
        for key, cnt in g._pair_new_bars.items():
            batch_pair_bars[key] = batch_pair_bars.get(key, 0) + cnt

    with _backlog_lock:
        backlog_snap = dict(_backlog)

    db_metrics = _wait_for_batch_db(batch_id)

    pending_db = max(0, int(db_metrics.get("accepted", 0)) - int(db_metrics.get("db_processed", 0)))

    _on_batch_complete(batch_id, total_new, backlog_snap, batch_pair_bars, db_metrics)

    for group in groups:
        with group._lock:
            group_report_title = group._report_title
            group_report_lines = list(group._report_lines)
            group_report_level = group._report_level

        if group_report_title and group_report_lines:
            _log_report_block(group_report_title, group_report_lines, group_report_level)

    with _overflow_lock:
        overflow_depth = len(_overflow_buf)

    spool_depth = _spool.count()

    queue_depth = _db_queue.qsize()

    fact_inserted = int(db_metrics.get("fact_inserted", 0))

    staging_rows = int(db_metrics.get("staging_rows", 0))

    deferred_items = _fact_backlog_size()

    db_processed = int(db_metrics.get("db_processed", 0))

    expected_sessions = 0

    received_sessions = 0

    for group in groups:
        with group._lock:
            expected_sessions += len(group._expected)

            received_sessions += len(group._received)

    missed_sessions = max(0, expected_sessions - received_sessions)

    if expected_sessions == 0:
        analysis = (
            "No TradingView chart sessions were established in this batch. "

            "Treat as network/auth/session setup failure."
        )

    elif missed_sessions:
        analysis = (
            f"{missed_sessions} session(s) did not answer; affected pairs are tracked in backlog."
        )

    elif pending_db:
        analysis = f"{pending_db} accepted bar(s) are still waiting for DB worker confirmation."

    elif deferred_items:
        analysis = f"{deferred_items} item(s) were staged but deferred because a repair/maintenance lock is active."

    elif total_new == 0 and not backlog_snap:
        analysis = "No new closed bars in this cycle; WebSocket sessions answered normally."

    else:
        analysis = "Batch flow is healthy; data moved from WebSocket to staging/Fact as expected."

    batch_level = logging.ERROR if expected_sessions == 0 else (
        logging.WARNING if (missed_sessions or pending_db or backlog_snap) else logging.INFO
    )

    _log_report_block(
        f"WS LIVE BATCH REPORT #{batch_id}",
        [
            f"Window   : started {batch_start} UTC | groups={len(groups)} | sessions={received_sessions}/{expected_sessions}",
            f"Accepted : {total_new:,} closed candles | DB processed={db_processed:,} | pending={pending_db:,}",
            f"Database : temporary rows={staging_rows:,} | saved rows={fact_inserted:,} | deferred={deferred_items:,}",
            f"Buffers  : write queue={queue_depth:,} | memory safety={overflow_depth:,} | disk safety={spool_depth if spool_depth is not None else 'n/a'}",
            f"Retry    : {len(backlog_snap)} pair(s) | {_summarize_backlog(backlog_snap)}",
            f"By symbol: {_summarize_counts_by_symbol(batch_pair_bars)}",
            f"By TF    : {_summarize_counts_by_tf(batch_pair_bars)}",
            f"Top pairs: {_summarize_pair_counts(batch_pair_bars)}",
            f"Analysis : {analysis}",
        ],
        batch_level,
    )

    logger.debug(
        "%s",
        _llog(
            "Live batch audit",
            batch=batch_id,
            closed_candles_received=total_new,
            rows_saved=int(db_metrics.get("fact_inserted", 0)),
            temporary_rows=int(db_metrics.get("staging_rows", 0)),
            pending_writes=pending_db,
            retry_pairs=len(backlog_snap),
            checked_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
            result="recorded",
        ),
    )

    _write_live_summary(
        {
            "written_at": _utc_iso(),
            "batch_id": batch_id,
            "status": "warning" if batch_level >= logging.WARNING else "ok",
            "started_at_utc": batch_start_iso,
            "sessions_received": received_sessions,
            "sessions_expected": expected_sessions,
            "accepted_closed_candles": total_new,
            "db_processed": db_processed,
            "pending_db": pending_db,
            "temporary_rows": staging_rows,
            "saved_rows": fact_inserted,
            "deferred_items": deferred_items,
            "backlog_pairs": len(backlog_snap),
            "write_queue": queue_depth,
            "memory_safety_buffer": overflow_depth,
            "disk_safety_buffer": spool_depth,
            "analysis": analysis,
        }
    )

    _write_live_state(
        status="waiting",
        last_batch_id=batch_id,
        batch_started_at=None,
        batch_group_timeout_sec=None,
        batch_completed_at=_utc_iso(),
        accepted_bars=total_new,
        fact_inserted=fact_inserted,
        backlog_pairs=len(backlog_snap),
        stale_threads=[],
        **_ws_recovery_metrics_snapshot(),
    )

def _on_batch_complete(
    batch_num: int,
    total_accepted: int,
    backlog_snap: dict,
    pair_bars: dict[tuple[int, str], int] | None = None,
    db_metrics: dict | None = None,
) -> None:
    with _hourly_lock:
        _hourly_stats["batches"] += 1

        _hourly_stats["accepted_bars"] += total_accepted

        if total_accepted == 0:
            _hourly_stats["zero_bar_batches"] += 1

        bl = len(backlog_snap)

        if bl > _hourly_stats["backlog_peak"]:
            _hourly_stats["backlog_peak"] = bl

        for key, cnt in (pair_bars or {}).items():
            prev = _hourly_stats["pair_accepted"].get(key, 0)

            _hourly_stats["pair_accepted"][key] = prev + cnt

    if batch_num == 1:
        db_metrics = db_metrics or {}

        fact_inserted = int(db_metrics.get("fact_inserted", 0))

        staging_rows = int(db_metrics.get("staging_rows", 0))

        db_pending = max(
            0,
            int(db_metrics.get("accepted", 0)) - int(db_metrics.get("db_processed", 0)),
        )

        _send_alert(
            "INFO",
            "Live feed first batch finished\n"
            f"Batch: #{batch_num}\n"
            f"Closed candles received: {total_accepted:,}\n"
            f"Temporary rows written: {staging_rows:,}\n"
            f"Rows saved to main table: {fact_inserted:,}\n"
            f"Still waiting to write: {db_pending:,}\n"
            f"Pairs waiting for retry: {bl}",
        )

    elif total_accepted == 0:
        logger.info(
            "%s",
            _llog(
                "Live batch returned no new closed candles",
                batch=batch_num,
                retry_pairs=bl,
                note="single empty batch is tracked; hourly health will alert if the feed becomes stale",
                result="no_new_candles",
            ),
        )

def _prepare_live_batch() -> None:
    _tv_auth.check_and_refresh(logger)
    _tv_auth.ensure_cookie_fresh(logger)
    _refresh_watermarks_from_fact("pre-batch")


def _report_batch_wait(wait: float) -> None:
    _log_report_text(
        logging.INFO,
        live_next_batch_block(
            wait_seconds=round(wait),
            interval_minutes=BATCH_INTERVAL_MIN,
        ),
    )
    connectivity_cooldown = bool(
        _runtime._tv_connectivity_block_until
        and time.time() < float(_runtime._tv_connectivity_block_until)
    )
    blocked_until = (
        datetime.fromtimestamp(
            _runtime._tv_connectivity_block_until,
            tz=timezone.utc,
        ).isoformat()
        if connectivity_cooldown
        else None
    )
    _write_live_state(
        status="network_blocked" if connectivity_cooldown else "waiting",
        next_batch_in_sec=round(wait, 3),
        next_batch_after=_utc_iso(),
        blocked_until=blocked_until,
        network_error=(
            _runtime._tv_connectivity_last_error if connectivity_cooldown else None
        ),
        batch_started_at=None,
        batch_group_timeout_sec=None,
    )


def _batch_loop(groups: list[BatchFetcher]) -> None:
    run_aligned_schedule(
        shutdown=_shutdown,
        interval_minutes=BATCH_INTERVAL_MIN,
        prepare_batch=_prepare_live_batch,
        run_batch=lambda: _run_batch(groups),
        report_wait=_report_batch_wait,
    )

def _status_reporter() -> None:
    while not _shutdown.wait(STATUS_INTERVAL_SEC):
        _refresh_watermarks_from_fact("status")

        with _state_lock:
            s = dict(_stats)

        with _overflow_lock:
            overflow = len(_overflow_buf)

        with _backlog_lock:
            n_miss_active = len(_backlog) + len(_requires_backfill)

        now_dt = datetime.now(timezone.utc)

        now_ts = now_dt.timestamp()

        with _state_lock:
            wm_snapshot = dict(_last_bar_ts)

            source_snapshot = dict(_source_bar_ts)

        stale_count = 0

        closed_stale_count = 0

        source_lag_count = 0

        max_age_h = 0.0

        stale_entries = []

        source_lag_entries = []

        for sid, tf_code in WS_WATCH_KEYS:
            wm_ts = wm_snapshot.get((sid, tf_code), 0.0)

            if not wm_ts:
                continue

            age_h = (now_ts - wm_ts) / 3600

            stale_threshold_h = _freshness_alert_threshold_minutes(sid, tf_code) / 60

            market_live = _is_market_expected_live(sid, now_dt)

            if market_live and age_h > stale_threshold_h:
                stale_count += 1

                stale_entries.append((age_h, sid, tf_code, wm_ts))

            elif not market_live and age_h > stale_threshold_h:
                closed_stale_count += 1

            if market_live and age_h > max_age_h:
                max_age_h = age_h

            src_ts = source_snapshot.get((sid, tf_code), 0.0)

            if src_ts:
                src_age_h = (now_ts - src_ts) / 3600

                if market_live and src_age_h > stale_threshold_h:
                    source_lag_count += 1

                    source_lag_entries.append((src_age_h, sid, tf_code, src_ts))

        stale_entries.sort(reverse=True)

        source_lag_entries.sort(reverse=True)

        _spool.cleanup_old()

        spool_count_raw, spool_oldest_age_seconds = _spool.health_snapshot()
        spool_count = spool_count_raw or 0

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        with _hourly_lock:
            h = dict(_hourly_stats)

            _hourly_stats["batches"] = 0

            _hourly_stats["accepted_bars"] = 0

            _hourly_stats["fact_bars"] = 0

            _hourly_stats["staging_rows"] = 0

            _hourly_stats["errors"] = 0

            _hourly_stats["ws_errors"] = 0

            _hourly_stats["zero_bar_batches"] = 0

            _hourly_stats["backlog_peak"] = 0

            _hourly_stats["pair_bars"] = {}

            _hourly_stats["pair_accepted"] = {}

            _hourly_stats["pair_staging"] = {}

        sym_line = _summarize_counts_by_symbol(h.get("pair_bars", {}))

        tf_line = _summarize_counts_by_tf(h.get("pair_bars", {}))

        acc_sym_line = _summarize_counts_by_symbol(h.get("pair_accepted", {}))

        acc_tf_line = _summarize_counts_by_tf(h.get("pair_accepted", {}))

        stage_sym_line = _summarize_counts_by_symbol(h.get("pair_staging", {}))

        stage_tf_line = _summarize_counts_by_tf(h.get("pair_staging", {}))

        current_token = _tv_auth.get_current_token()
        is_guest = current_token == _tv_auth.GUEST_TOKEN

        token_secs = _tv_auth.token_expires_in(current_token)

        if is_guest:
            auth_info = f"Guest ({_runtime._consecutive_guest_batches} batches in a row)"

        elif token_secs > 0:
            th = int(token_secs) // 3600

            tm = (int(token_secs) % 3600) // 60

            auth_info = f"Premium | Token expires in: {th}h{tm:02d}m"

        elif token_secs < 0:
            auth_info = "Premium (active)"

        else:
            auth_info = "Premium (token expired - renewing)"

        recent_errors = int(h.get("errors", 0))

        total_errors = int(s.get("errors", 0))

        recent_ws_errors = int(h.get("ws_errors", 0) or 0)

        total_ws_errors = int(s.get("ws_errors", 0) or 0)

        accepted_h = int(h.get("accepted_bars", h.get("bars", 0)))

        fact_h = int(h.get("fact_bars", 0))

        staging_h = int(h.get("staging_rows", 0))

        health_status, notify_level, issues = _reporter.classify_health(
            recent_errors=recent_errors,
            total_errors=total_errors,
            last_hour_accepted=accepted_h,
            last_hour_saved=fact_h,
            stale_count=stale_count,
            source_lag_count=source_lag_count,
            source_lag_entries=source_lag_entries,
            spool_count=spool_count,
            spool_oldest_age_seconds=spool_oldest_age_seconds,
            recent_ws_errors=recent_ws_errors,
            total_ws_errors=total_ws_errors,
            n_miss_active=n_miss_active,
            is_guest=is_guest,
            consecutive_guest_batches=_runtime._consecutive_guest_batches,
        )
        previous_health_status = getattr(_status_reporter, "_last_health_status", None)
        recovered_from_warning = (
            previous_health_status in {"WARNING", "CRITICAL"} and health_status == "HEALTHY"
        )
        _status_reporter._last_health_status = health_status

        logger.info(
            "%s",
            _llog(
                "Hourly health summary",
                checked_at=now,
                status=health_status,
                login=auth_info,
                closed_candles_received=s.get("accepted_bars", 0),
                rows_saved=s["fact_inserted"],
                recent_errors=recent_errors,
                total_errors=total_errors,
                recent_websocket_errors=recent_ws_errors,
                total_websocket_errors=total_ws_errors,
                batches=s["batches_run"],
                write_queue=s["queue_depth"],
                memory_buffer=overflow,
                disk_buffer=spool_count,
                disk_buffer_oldest_seconds=(
                    round(spool_oldest_age_seconds, 1)
                    if spool_oldest_age_seconds is not None
                    else None
                ),
                missing_pairs=n_miss_active,
                late_pairs=stale_count,
                feed_delay_pairs=source_lag_count,
                closed_market_late_pairs=closed_stale_count,
                oldest_active_pair_hours=f"{max_age_h:.1f}",
                result=health_status.lower(),
            ),
        )

        hourly_parts = [
            f"{h['batches']} live batches",
            f"{accepted_h} closed candles received",
            f"{staging_h} temporary rows written",
            f"{fact_h} rows saved",
        ]

        if h["zero_bar_batches"]:
            hourly_parts.append(f"{h['zero_bar_batches']} batches returned no new closed candles")

        if h["backlog_peak"]:
            hourly_parts.append(f"retry list peak: {h['backlog_peak']} pairs")

        hourly_summary = "  |  ".join(hourly_parts)

        issue_lines = issues[:3] if issues else ["Everything looks normal."]

        issue_text = " | ".join(issue_lines)

        health_lines = [
            f"Status   : {health_status} | {now}",
            f"Login    : {auth_info}",
            f"Last hour: {hourly_summary}",
            f"Total    : {s['batches_run']} batches | {s.get('accepted_bars', 0):,} accepted | "

            f"{s['fact_inserted']:,} saved rows | "

            f"{recent_errors} recent errors / {total_errors} total errors",
            f"Network  : {recent_ws_errors} recent WebSocket errors / {total_ws_errors} total WebSocket errors",
            f"Buffers  : write queue={s['queue_depth']:,} | memory safety buffer={overflow:,} | disk safety buffer={spool_count:,}",
            f"Freshness: oldest active={max_age_h:.1f}h | late active={stale_count} | "

            f"source lag={source_lag_count} | missing={n_miss_active} | closed stale={closed_stale_count}",
            f"Accepted : symbols {acc_sym_line}",
            f"Accepted : TFs {acc_tf_line}",
            f"Staging  : symbols {stage_sym_line}",
            f"Staging  : TFs {stage_tf_line}",
            f"Fact     : symbols {sym_line}",
            f"Fact     : TFs {tf_line}",
            f"Issues   : {issue_text}",
        ]

        _log_report_block(
            "WS LIVE HEALTH REPORT",
            health_lines,
            logging.ERROR

            if notify_level == "ERROR"

            else logging.WARNING

            if notify_level == "WARNING"

            else logging.INFO,
        )

        notify_live_event(
            severity=notify_level,
            title=(
                "Live feed recovered: HEALTHY"
                if recovered_from_warning
                else f"Live feed health report: {health_status}"
            ),
            summary=(
                "The live data feed has recovered after the previous warning state."
                if recovered_from_warning
                else "Scheduled live OHLCV status report for the running data feed."
            ),
            current_state={
                "time_utc": now,
                "tradingview_session": auth_info,
                "batches_since_start": s["batches_run"],
                "recent_websocket_errors": recent_ws_errors,
                "total_websocket_errors": total_ws_errors,
                "write_queue": s["queue_depth"],
                "memory_safety_buffer": overflow,
                "disk_safety_buffer": spool_count,
                "disk_safety_buffer_oldest_seconds": (
                    round(spool_oldest_age_seconds, 1)
                    if spool_oldest_age_seconds is not None
                    else None
                ),
            },
            data_result={
                "last_hour": hourly_summary,
                "total_closed_candles_received": f"{s.get('accepted_bars', 0):,}",
                "total_rows_saved": f"{s['fact_inserted']:,}",
                "recent_write_errors": recent_errors,
                "total_write_errors": total_errors,
                "received_by_symbol": acc_sym_line,
                "received_by_timeframe": acc_tf_line,
                "saved_by_symbol": sym_line,
                "saved_by_timeframe": tf_line,
            },
            health_risk={
                "oldest_active_pair_age": f"{max_age_h:.1f}h",
                "active_pairs_running_late": stale_count,
                "feed_delay_pairs": source_lag_count,
                "missing_pairs": n_miss_active,
                "closed_market_outdated_pairs": closed_stale_count,
                "meaning": " | ".join(issue_lines),
            },
            reason="Everything looks normal." if not issues else " | ".join(issue_lines),
            recommended_action=(
                "No action needed now. Keep DP Program running."
                if notify_level == "SUCCESS"
                else "Watch the next cycle. If this repeats during market hours, check TradingView login, network, and SQL Server."
            ),
            trace={"log_file": str(LIVE_LOG), "state_file": str(WS_LIVE_STATE)},
            result="healthy" if notify_level == "SUCCESS" else "warning" if notify_level == "WARNING" else "failed",
        )

def main(smoke_seconds: int | None = None, *, conflict_policy: str | None = None) -> int:
    import sys as _sys

    if hasattr(_sys.stdout, "reconfigure"):
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if STORAGE.mode == "redis":
        # Redis-only is not a durable warehouse implementation: this engine
        # always commits SQL first and only then publishes optional Redis
        # candle snapshots. Keep this defensive guard even though storage mode
        # is now a reviewed system constant rather than an operator setting.
        logger.critical(
            "%s",
            _llog(
                "Unsupported storage mode requested",
                mode=STORAGE.mode,
                reason="redis-only mode is not wired into the live engine's durable write path",
                result="refused_to_start",
            ),
        )
        print(
            f"ERROR: System storage mode {STORAGE.mode!r} is unsupported because SQL is "
            "the durable system of record. Correct settings/system.py before deployment."
        )
        return 1

    n_groups = math.ceil(len(WS_SYMBOLS) / WS_SYMBOLS_PER_CONN)
    started_utc = datetime.now(timezone.utc)
    started_local = started_utc.astimezone()

    _log_block(
        logging.INFO,
        live_start_block(
            pid=os.getpid(),
            utc_started=started_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
            local_started=started_local.strftime("%Y-%m-%d %H:%M:%S %z"),
            interval_min=BATCH_INTERVAL_MIN,
            symbols=len(WS_SYMBOLS),
            timeframes=len(WS_TF_INTERVAL),
            groups=n_groups,
        ),
    )

    print("=" * 65)

    print("  REAL-TIME PRICE UPDATE SYSTEM (WS Live)")

    print("=" * 65)

    print(f"  Watched pairs    : {len(WS_SYMBOLS)} pairs (indices, metals, crypto)")

    print(f"  Timeframes       : {len(WS_TF_INTERVAL)} direct")

    print(f"  Connection groups: {n_groups} groups (~{WS_SYMBOLS_PER_CONN} pairs/group)")

    if WS_SYMBOLS_PER_CONN_CAPPED:
        print(
            f"  Safety cap       : WS_LIVE_SYMBOLS_PER_CONN {_configured_symbols_per_conn} -> "

            f"{WS_SYMBOLS_PER_CONN} to keep each WS below ~90 chart sessions"
        )

    print(f"  Update interval  : every {BATCH_INTERVAL_MIN} minutes, aligned to clock time")

    print(f"  Connect timeout  : {BATCH_FETCH_TIMEOUT} seconds per connection")

    print(f"  Login method     : {'Cookie + Token' if TRADINGVIEW.cookie else 'Username / Password'}")

    print(f"  Discord alerts   : {'On' if NOTIFICATION.discord_webhook_url else 'Off'}")

    print(f"  Started at (UTC) : {started_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    print("=" * 65)

    print("\n[Step 1/4] Checking database connection...")

    if not test_connection():
        print("ERROR: Could not connect to the database. The system did not start.")

        return 1

    from core_engine.shared.warehouse.connection import verify_database_contract

    _contract = verify_database_contract()
    if not _contract["ok"]:
        logger.critical("%s", _llog("Database contract check failed", reason=_contract["reason"], result="refused_to_start"))
        print(f"ERROR: Database contract check failed: {_contract['reason']}")
        print("Run scripts/sql/08_migration_usp_loaddirect_v2.sql through a controlled deploy, then retry.")
        return 1

    from core_engine.util.coordination.locks import cleanup_expired as _cleanup_expired

    _cleanup_expired()

    if _cleanup_dead_ws_live_runtime_lock():
        logger.warning("%s", _llog("Old live lock cleaned before startup", lock="ws_live_runtime", result="cleaned"))

    _lock_acquired_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")

    ws_lock = _acquire_task_lock(
        "ws_live_runtime",
        duration_min=60,
        payload=_ws_live_runtime_payload(started_at=_lock_acquired_at),
    )

    conflict_policy = (conflict_policy or os.environ.get("DP_LIVE_CONFLICT_POLICY") or "skip").lower()

    if not ws_lock:
        if conflict_policy != "replace":
            record = _fetch_task_lock("ws_live_runtime", active_only=True)
            detail = record.meta if record else {}
            logger.warning(
                "%s",
                _llog(
                    "Live start skipped",
                    active_pid=detail.get("pid"),
                    active_started=detail.get("started"),
                    policy=conflict_policy,
                    result="already_running",
                ),
            )
            notify_live_event(
                severity="WARNING",
                title="Live start skipped",
                summary="A live fetching request was not started because another live process is already running.",
                current_state={
                    "active_pid": detail.get("pid") or "-",
                    "active_started": detail.get("started") or "-",
                    "policy": conflict_policy,
                },
                data_result="No new live process was started. The existing live feed remains responsible for candles.",
                health_risk="Low. The system avoided two live workers writing at the same time.",
                recommended_action="Use Status/logs to inspect the active live process, or choose restart/replace intentionally.",
                trace={"lock": "ws_live_runtime", "log_file": str(LIVE_LOG)},
                result="skipped",
            )
            print("Live fetching is already running. New live process was skipped.")
            return EXIT_LOCK_CONFLICT

        logger.info("%s", _llog("Live handoff requested", action="ask_old_instance_to_stop", result="waiting"))

        request_ws_live_shutdown(logger)

        handoff_wait_seconds = WS_LIVE_SHUTDOWN_GRACE_SECONDS + 10

        logger.info(
            "%s",
            _llog("Waiting for old live instance to stop", max_wait_seconds=handoff_wait_seconds, result="waiting"),
        )

        _write_live_state(
            status="handoff_waiting",
            lock_name="ws_live_runtime",
            handoff_wait_seconds=handoff_wait_seconds,
            batch_started_at=None,
            batch_group_timeout_sec=None,
        )

        for _wait_i in range(handoff_wait_seconds):
            if not _is_task_locked("ws_live_runtime"):
                break

            _write_live_state(
                status="handoff_waiting",
                lock_name="ws_live_runtime",
                handoff_elapsed_seconds=_wait_i + 1,
                handoff_wait_seconds=handoff_wait_seconds,
                batch_started_at=None,
                batch_group_timeout_sec=None,
            )

            time.sleep(1)

        _cleanup_expired()

        _lock_acquired_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")

        ws_lock = _acquire_task_lock(
            "ws_live_runtime",
            duration_min=60,
            payload=_ws_live_runtime_payload(started_at=_lock_acquired_at),
        )

        if not ws_lock:
            logger.error(
                "%s",
                _llog("Live handoff failed", lock="ws_live_runtime", reason="old_instance_lock_still_active", result="startup_aborted"),
            )

            return 1

        logger.info("%s", _llog("Live handoff completed", lock="ws_live_runtime", result="lock_acquired"))

    atexit.register(_release_task_lock, "ws_live_runtime")

    if not _acquire_local_runtime_lock():
        _release_task_lock("ws_live_runtime")

        return 1

    atexit.register(_release_local_runtime_lock)

    ws_lock_stop = threading.Event()

    def _ws_lock_heartbeat() -> None:
        while not ws_lock_stop.wait(900):
            now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")

            heartbeat_payload = (
                f"host={socket.gethostname()};"

                f"pid={os.getpid()};"

                f"started={_lock_acquired_at};"

                f"heartbeat={now}"
            )

            renewed = _renew_task_lock("ws_live_runtime", duration_min=60, payload=heartbeat_payload)

            if not renewed:
                # A renew() that matches 0 rows (once lock fencing is
                # active - see coordination.locks._supports_owner_fencing)
                # is a real signal, not a transient blip: either the lock
                # row is gone, or - the split-brain case this exists to
                # catch - a different process already re-acquired it under
                # a new OwnerId after this one appeared unresponsive.
                # Continuing to write in either case risks two writers
                # racing on the same data, so this stops immediately
                # instead of waiting out the remaining TTL.
                logger.critical(
                    "%s",
                    _llog(
                        "Live lock renewal failed - stopping immediately",
                        lock="ws_live_runtime",
                        risk="possible_second_writer_holds_this_lock",
                        result="shutting_down",
                    ),
                )
                _shutdown.set()
                return

    threading.Thread(target=_ws_lock_heartbeat, name="ws-live-lock-heartbeat", daemon=True).start()

    _write_live_state(
        status="starting",
        child_started_at=_utc_iso(),
        batch_interval_min=BATCH_INTERVAL_MIN,
        heartbeat_sec=STATE_HEARTBEAT_SEC,
        **_ws_recovery_metrics_snapshot(),
    )

    threading.Thread(target=_state_heartbeat_loop, name="ws-live-state-heartbeat", daemon=True).start()

    print("\n[Step 2/4] Logging in to TradingView...")

    _cache = _tv_auth.load_token_cache()

    _cache_token = _cache.get("TV_AUTH_TOKEN", "")

    _cached_cookie = _cache.get("TV_COOKIE", "")

    if _cached_cookie and not _tv_auth.get_current_cookie():
        _tv_auth.set_current_cookie(_cached_cookie)

        logger.info("%s", _llog("TradingView session cookie restored", source="runtime_cache", cookie_length=len(_cached_cookie), result="ready"))

    def _usable_token(token: str | None) -> bool:
        if not token or token == _tv_auth.GUEST_TOKEN:
            return False

        return _tv_auth.token_expires_in(token) > 300

    _has_valid_token = _usable_token(_cache_token) or _usable_token(TRADINGVIEW.auth_token)

    if not _has_valid_token:
        while not _shutdown.is_set():
            tv_network_ok, tv_network_detail = _tradingview_connectivity_ok()

            if tv_network_ok:
                break

            blocked_until = (
                datetime.fromtimestamp(_runtime._tv_connectivity_block_until, tz=timezone.utc).isoformat()

                if _runtime._tv_connectivity_block_until

                else None
            )

            logger.warning(
                "%s",
                _llog("TradingView network unavailable before login refresh", reason=tv_network_detail, result="waiting"),
            )

            _write_live_state(
                status="network_blocked",
                phase="auth_preflight",
                network_error=tv_network_detail,
                blocked_until=blocked_until,
            )

            _send_alert(
                "WARNING",
                "Live feed is waiting for TradingView before refreshing login\n"
                f"Reason: {tv_network_detail}\n"
                f"Next retry after: {blocked_until or 'waiting period'}",
            )

            _shutdown.wait(max(30, TV_WS_CONNECTIVITY_COOLDOWN_SEC))

        if _shutdown.is_set():
            logger.info("%s", _llog("Live stopped during TradingView login wait", result="shutdown_requested"))

            return

    if not _has_valid_token:
        print("  No valid token found - getting a new token automatically...")

        auth_token, token_source = _tv_auth.bootstrap(logger)
        _tv_auth.set_current_token(auth_token)

    else:
        auth_token, token_source = _tv_auth.resolve_auth_token(logger)
        _tv_auth.set_current_token(auth_token)

    print(f"  Login source    : {token_source}")

    if not run_auth_preflight(
        tv_auth=_tv_auth,
        logger=logger,
        token_source=token_source,
        static_cookie=TRADINGVIEW.cookie,
        username=TRADINGVIEW.username,
        password=TRADINGVIEW.password,
        guest_policy=TV_WS_GUEST_POLICY,
        require_headless=TV_WS_PREFLIGHT_REQUIRE_HEADLESS,
        alert=_send_alert,
    ):
        print("  ERROR: Auth preflight failed. See runtime/logs/live.log for details.")

        return 1

    if _tv_auth.is_guest_mode():
        print("  WARNING: Guest mode is active - data may be limited.")

        notify_live_event(
            severity="WARNING",
            title="Live feed started with limited TradingView access",
            summary="Live fetching can run, but TradingView is using limited access instead of a confirmed logged-in session.",
            current_state={"tradingview_session": "guest / limited", "worker": "starting"},
            data_result="Candles may still arrive, but history depth and some symbols can be limited.",
            health_risk="Medium. Data coverage may be lower than expected during market hours.",
            reason="The current TradingView session resolved to guest mode.",
            recommended_action="Refresh TradingView login if this repeats or if symbols stop receiving candles.",
            trace={"log_file": str(LIVE_LOG)},
            result="warning",
        )

    print("\n[Step 3/4] Loading latest data timestamps...")

    _load_watermarks()

    set_recovery_callback(lambda: seed_candle_snapshots(WS_SYMBOLS, WS_TF_CODES, reason="circuit_recovered"))

    seed_result = seed_candle_snapshots(WS_SYMBOLS, WS_TF_CODES, reason="live_startup")
    if seed_result.get("result") != "disabled":
        logger.info(
            "%s",
            _llog(
                "Initial candle snapshots queued",
                queued=seed_result.get("queued", 0),
                skipped=seed_result.get("skipped", 0),
                result=seed_result.get("result", "unknown"),
            ),
        )

    print("\n[Step 3b] Starting offline safety buffer...")

    _spool.init()

    initial_spool_rows = _spool.count()
    if initial_spool_rows is None or initial_spool_rows > 0:
        _spool_pending.set()

    recovered = _lease_spool_into_queue()

    remaining_spool_rows = _spool.count()
    if remaining_spool_rows == 0:
        _spool_pending.clear()
    elif remaining_spool_rows is None or remaining_spool_rows > 0:
        _spool_pending.set()

    with _state_lock:
        _stats["queue_depth"] = _db_queue.qsize()

    if recovered:
        print(f"  Restored {recovered} bars left from the previous run.")

    groups = [
        BatchFetcher(i, WS_SYMBOLS[i * WS_SYMBOLS_PER_CONN : (i + 1) * WS_SYMBOLS_PER_CONN])

        for i in range(n_groups)
    ]

    print("\n[Step 4/4] Starting background workers...\n")

    critical_thread_failure = threading.Event()
    critical_thread_detail: dict[str, str] = {}
    critical_thread_lock = threading.Lock()

    def _mark_critical_thread_failure(task: str, reason: str) -> bool:
        with critical_thread_lock:
            if critical_thread_failure.is_set():
                return False
            critical_thread_detail.update({"task": task, "reason": reason})
            critical_thread_failure.set()
        _shutdown.set()
        return True

    def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return

        tb = "".join(
            traceback.format_exception(
                args.exc_type,
                args.exc_value,
                args.exc_traceback,
            )
        )

        tname = getattr(args.thread, "name", "unknown")

        is_critical = tname in {"db-worker", "etl-worker", "live-batch-loop"}
        if is_critical:
            _mark_critical_thread_failure(tname, f"{args.exc_type.__name__}: {args.exc_value}")

        logger.critical("[THREAD CRASH] %s: %s\n%s", tname, args.exc_value, tb)

        notify_live_event(
            severity="ERROR",
            title="Live feed internal task stopped unexpectedly",
            summary="One internal task inside live fetching crashed.",
            current_state={"task": tname, "worker": "live fetching"},
            data_result="Some live batches or database writes may pause depending on which task crashed.",
            health_risk=(
                "High. This critical task failure will stop the live child so the supervisor can restart it."
                if is_critical
                else "Medium. The failed auxiliary task was reported for operator review."
            ),
            reason=f"{args.exc_type.__name__}: {args.exc_value}",
            recommended_action=(
                "Confirm the DP supervisor starts a replacement live child and the next batch reaches backlog=0."
                if is_critical
                else "Check runtime/logs/live.log for the auxiliary task failure."
            ),
            trace={"log_file": str(LIVE_LOG), "traceback_tail": tb[-800:]},
            result="failed",
        )

    threading.excepthook = _thread_excepthook

    # Start each connection group's persistent worker before the batch
    # scheduler can issue its first request_batch() - see BatchFetcher.
    # start_worker()/High-11 redesign.
    for g in groups:
        g.start_worker()

    _db_worker_done.clear()
    _etl_wakeup.clear()
    etl_thread = threading.Thread(target=_etl_worker, name="etl-worker", daemon=True)
    db_thread = threading.Thread(target=_db_worker, name="db-worker", daemon=False)

    etl_thread.start()
    db_thread.start()

    threading.Thread(target=_status_reporter, name="status", daemon=True).start()

    sched_thread = threading.Thread(
        target=_batch_loop,
        args=(groups,),
        name="live-batch-loop",
        daemon=True,
    )

    sched_thread.start()

    logger.info(
        "%s",
        _llog(
            "Live feed started",
            connection_groups=n_groups,
            sessions_per_batch=len(WS_SYMBOLS) * len(WS_TF_INTERVAL),
            interval_minutes=BATCH_INTERVAL_MIN,
            login_source=token_source,
            result="running",
        ),
    )

    notify_live_event(
        severity="INFO",
        title="Live feed started",
        summary="Live OHLCV collection is running and will fetch closed candles on its configured interval.",
        current_state={
            "watched_pairs": len(WS_SYMBOLS),
            "timeframes": len(WS_TF_INTERVAL),
            "update_interval": f"every {BATCH_INTERVAL_MIN} minutes",
            "tradingview_session_source": token_source,
            "health_report": "every hour",
        },
        data_result="No saved rows are expected until the first live batch finishes.",
        health_risk="Low. Startup completed and background workers are active.",
        recommended_action="No action needed now. Watch the first health report and runtime/logs/live.log.",
        trace={"log_file": str(LIVE_LOG), "state_file": str(WS_LIVE_STATE)},
        result="started",
    )

    logger.info("%s", _llog("Discord notification channel ready", mode="send_only", result="ready"))

    print("System is running. Press Ctrl+C to stop.\n")

    if smoke_seconds is not None:
        smoke_seconds = int(smoke_seconds)

        if smoke_seconds <= 0:
            raise ValueError("smoke_seconds must be greater than 0")

        def _smoke_stop() -> None:
            logger.info("%s", _llog("Smoke test time limit reached", seconds=smoke_seconds, result="stopping"))

            _shutdown.set()

        smoke_timer = threading.Timer(smoke_seconds, _smoke_stop)

        smoke_timer.daemon = True

        smoke_timer.start()

        logger.info("%s", _llog("Smoke test auto-stop scheduled", seconds=smoke_seconds, result="waiting"))

    _shutdown_check_counter = 0

    try:
        while True:
            time.sleep(1)

            if _shutdown.is_set():
                break

            if not db_thread.is_alive():
                if _mark_critical_thread_failure("db-worker", "thread exited while live fetching was active"):
                    logger.critical(
                        "%s",
                        _llog(
                            "Critical live task stopped",
                            task="db-worker",
                            action="exit_for_supervisor_restart",
                            result="failed",
                        ),
                    )
                break

            if not etl_thread.is_alive():
                if _mark_critical_thread_failure("etl-worker", "thread exited while live fetching was active"):
                    logger.critical(
                        "%s",
                        _llog(
                            "Critical live task stopped",
                            task="etl-worker",
                            action="exit_for_supervisor_restart",
                            result="failed",
                        ),
                    )
                break

            if not sched_thread.is_alive():
                if _mark_critical_thread_failure("live-batch-loop", "thread exited while live fetching was active"):
                    logger.critical(
                        "%s",
                        _llog(
                            "Critical live task stopped",
                            task="live-batch-loop",
                            action="exit_for_supervisor_restart",
                            result="failed",
                        ),
                    )
                break

            _shutdown_check_counter += 1

            if _shutdown_check_counter >= SHUTDOWN_POLL_SEC:
                _shutdown_check_counter = 0

                if is_ws_live_shutdown_requested():
                    logger.info(
                        "%s",
                        _llog("Graceful shutdown signal received", source="new_live_instance_or_operator", result="stopping"),
                    )

                    _shutdown.set()

                    break

    except KeyboardInterrupt:
        print(
            "\n\nStopping - waiting for all queued data to be written to the database. Please wait..."
        )

        _shutdown.set()

    except Exception as exc:
        tb = traceback.format_exc()

        logger.critical("[MAIN CRASH] %s", exc)

        notify_live_event(
            severity="ERROR",
            title="Live feed stopped unexpectedly",
            summary="The live fetching main loop hit an unexpected error and is shutting down.",
            current_state={"worker": "live fetching", "shutdown_requested": True},
            data_result="Live candles may stop until the DP Program supervisor restarts this worker.",
            health_risk="High for 24/7 operation if the supervisor cannot restart it.",
            reason=f"{type(exc).__name__}: {exc}",
            recommended_action="Check runtime/logs/live.log and runtime/logs/system.log, then confirm whether DP Program restarted live fetching.",
            trace={"log_file": str(LIVE_LOG), "traceback_tail": tb[-800:]},
            result="failed",
        )

        _shutdown.set()

    sched_thread.join(timeout=WS_LIVE_SHUTDOWN_GRACE_SECONDS)

    if sched_thread.is_alive():
        logger.warning(
            "%s",
            _llog(
                "Live batch loop did not stop in time",
                grace_seconds=WS_LIVE_SHUTDOWN_GRACE_SECONDS,
                action="release_batch_lock_before_exit",
                result="warning",
            ),
        )

        release_live_batch_window(True)

    else:
        cleanup_orphan_live_batch_window(logger)

    if db_thread.is_alive():
        # Bounded wait instead of the unbounded _db_queue.join(): every
        # item still in the queue is already durable in the spool outbox
        # (persisted before being queued - see _enqueue_or_buffer), so
        # timing out here and proceeding to shutdown loses nothing. The
        # next process start leases whatever is still pending and retries
        # it; this bound just prevents a slow/hung SQL Server from making
        # shutdown itself hang indefinitely.
        drained = _wait_for_queue_drain(WS_LIVE_SHUTDOWN_GRACE_SECONDS)
        if not drained:
            logger.warning(
                "%s",
                _llog(
                    "Shutdown queue drain timed out",
                    remaining_items=_db_queue.qsize(),
                    timeout_seconds=WS_LIVE_SHUTDOWN_GRACE_SECONDS,
                    action="items_remain_durable_in_spool",
                    result="timeout",
                ),
            )
    else:
        # The worker's own loop exits as soon as shutdown is set and every
        # queued/spooled row has drained. Reaching this branch with zero
        # pending items is therefore the normal successful shutdown path,
        # not a worker failure (the main loop separately detects a worker
        # that dies while live collection is still active).
        _report_db_worker_stopped_at_shutdown(_db_queue.qsize())

    db_thread.join(timeout=30)

    _etl_wakeup.set()
    etl_thread.join(timeout=30)
    if etl_thread.is_alive():
        logger.warning(
            "%s",
            _llog(
                "Fact loader did not stop within bounded grace",
                timeout_seconds=30,
                action="staged_rows_remain_durable_for_next_start",
                result="timeout",
            ),
        )

    failed_task = critical_thread_detail.get("task")
    failed_reason = critical_thread_detail.get("reason")
    exit_code = 1 if critical_thread_failure.is_set() else 0

    with _state_lock:
        s = dict(_stats)

    logger.info(
        "%s",
        _llog(
            "Live feed stopped after critical task failure" if exit_code else "Live feed stopped cleanly",
            closed_candles_received=s.get("accepted_bars", 0),
            rows_saved=s["fact_inserted"],
            temporary_rows=s.get("staging_rows", 0),
            errors=s["errors"],
            websocket_errors=s.get("ws_errors", 0),
            events=s["events"],
            batches=s["batches_run"],
            failed_task=failed_task,
            reason=failed_reason,
            result="failed" if exit_code else "stopped",
        ),
    )

    _write_live_state(
        status="failed" if exit_code else "stopped",
        stopped_at=_utc_iso(),
        accepted_bars=s.get("accepted_bars", 0),
        fact_inserted=s["fact_inserted"],
        errors=s["errors"],
        batches_run=s["batches_run"],
        failed_task=failed_task,
        failure_reason=failed_reason,
        **_ws_recovery_metrics_snapshot(),
    )

    notify_live_event(
        severity="ERROR" if exit_code else "INFO",
        title="Live feed stopped after internal failure" if exit_code else "Live feed stopped",
        summary=(
            "A critical internal task failed, so the live child exited for a clean supervisor restart."
            if exit_code
            else "Live OHLCV collection stopped after flushing queued database writes."
        ),
        current_state={"worker": "stopped", "batches_completed": s["batches_run"], "errors": s["errors"]},
        data_result={
            "closed_candles_received": s.get("accepted_bars", 0),
            "rows_saved_to_main_table": s["fact_inserted"],
            "temporary_rows_touched": s.get("staging_rows", 0),
        },
        health_risk=(
            "High until the supervisor replacement child completes a healthy batch."
            if exit_code
            else "Low if shutdown was intentional. No new live candles will be saved while stopped."
        ),
        recommended_action=(
            "Confirm the supervisor replacement and verify the next batch has full sessions and backlog=0."
            if exit_code
            else "Restart live fetching if the system should keep collecting data 24/7."
        ),
        trace={"log_file": str(LIVE_LOG), "state_file": str(WS_LIVE_STATE)},
        result="failed" if exit_code else "stopped",
    )

    ws_lock_stop.set()

    try:
        atexit.unregister(_release_task_lock)
    except Exception:
        pass
    _release_task_lock("ws_live_runtime")

    print(f"\n  Accepted bars : {s.get('accepted_bars', 0):,}")

    print(f"  Fact inserted : {s['fact_inserted']:,}")

    print(f"  Staging rows  : {s.get('staging_rows', 0):,}")

    print(f"  Errors        : {s['errors']}")

    print(f"  WS events     : {s['events']:,}")

    print(f"  Batches run   : {s['batches_run']}")
    return exit_code

if __name__ == "__main__":
    raise SystemExit(main())



