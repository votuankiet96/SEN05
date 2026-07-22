"""Live OHLCV batch fetch engine.

Inputs:
- config/dp_provider.env and config/instruments.py symbols/timeframes
- TradingView auth/token/cookie runtime
- SQL Server warehouse connection

Process:
- every interval, open short-lived TradingView websocket batches
- accept only closed bars newer than committed watermarks
- queue, overflow, or durable-spool bars until DB writes complete
- defer Fact merges while historical maintenance holds the warehouse lock

Outputs:
- DWH.Fact_OHLCV rows
- runtime/logs/operation/live_fetching.log
- runtime/logs/operation/live_reports.log
- runtime/run/ws_live_state.json
"""

import atexit
import json
import logging
import math
import os
import queue
import socket
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from core_engine.other.exit_codes import EXIT_LOCK_CONFLICT
from core_engine.util.notify.discord import sanitize_ssl_keylogfile
sanitize_ssl_keylogfile()

import websocket

from core_engine.util.logkit.factory import get_logger
from core_engine.util.logkit.handlers import ResilientRotatingFileHandler
from core_engine.util.logkit.jsonl import append_jsonl_capped
from core_engine.shared.warehouse.validation import validate_ohlcv_df
from core_engine.util.coordination.locks import (
    acquire as _acquire_task_lock,
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
from core_engine.shared.warehouse.writer import (
    insert_staging_batch,
    run_etl_direct,
)
from core_engine.shared.warehouse.connection import get_connection, test_connection

from core_engine.settings import (
    LIVE,
    STORAGE,
    LIVE_SUMMARY_LOG,
    NOTIFICATION,
    SYMBOLS,
    SYMBOL_OVERNIGHT_MINS,
    TF_STAGING,
    TRADINGVIEW,
    WS_LIVE_LOG,
    WS_LIVE_PID,
    WS_LIVE_REPORT_LOG,
    WS_LIVE_STATE,
    RUN_DIR,
)

from core_engine.shared.tradingview import protocol as live_protocol
from core_engine.core.live.reporter import (
    LiveReporter,
    live_candle_header_refresh,
    live_candle_section,
    live_db_line,
    live_next_batch_block,
    log_live_block,
    live_operation_line,
    live_start_block,
    live_tv_line,
)
from core_engine.util.redis_io.candle_snapshot import (
    publish_candle_snapshot,
    seed_candle_snapshots,
    set_recovery_callback,
)
from core_engine.core.live.runtime_support import (
    as_utc_timestamp,
    freshness_alert_threshold_minutes,
    freshness_threshold_minutes,
    future_cutoff_ts,
    is_market_expected_live,
    cleanup_dead_runtime_lock,
    run_auth_preflight,
    runtime_payload as make_runtime_payload,
    utc_iso as _utc_iso,
)
from core_engine.core.live import state as _state
from core_engine.core.live.state import (
    BATCH_DB_REPORT_WAIT_SEC,
    MAX_BATCH_METRIC_HISTORY,
    _CANDLE_HEADER_REPEAT_ROWS,
    _GUEST_ALERT_THRESHOLD,
    _DEFERRED_ETL_MAX,
    _DEFERRED_ETL_WARN,
    _WRITE_DEFER_LOCK_TTL,
    _WRITE_DEFER_LOCKS,
    _backlog,
    _backlog_lock,
    _batch_metrics,
    _batch_metrics_lock,
    _candle_table_lock,
    _db_queue,
    _db_worker_done,
    _deferred_etl,
    _deferred_etl_next_attempt,
    _deferred_lock,
    _etl_item_meta,
    _etl_wakeup,
    _hourly_lock,
    _hourly_stats,
    _increment_data_error_counter,
    _last_bar_ts,
    _live_table_file_lock,
    _missed_lock,
    _missed_pairs,
    _overflow_buf,
    _overflow_lock,
    _received_bar_ts,
    _runtime_lock,
    _shutdown,
    _source_bar_ts,
    _spool,
    _spool_pending,
    _state_heartbeat_loop,
    _state_lock,
    _stats,
    _tradingview_connectivity_ok,
    _write_defer_lock_cache,
    _write_live_state,
    _ws_cooldown_lock,
)

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
    """Raises if EXPECTED_LIVE_SYMBOLS is set and does not match. Split out
    from the module-level call below purely so it is unit-testable without
    needing to reload core_engine.core.live.engine (a module-level import-time
    side effect otherwise)."""
    if expected and count != expected:
        raise RuntimeError(
            f"EXPECTED_LIVE_SYMBOLS={expected} but LIVE_ASSET_TYPES={','.join(asset_types)} "
            f"currently resolves to {count} symbol(s) from instruments.py. Refusing to start "
            "rather than silently watching a different live universe than expected - update "
            "EXPECTED_LIVE_SYMBOLS (or LIVE_ASSET_TYPES/instruments.py, whichever changed) "
            "after confirming the new count is intentional."
        )


def _claim_ws_callback_stall_fault(group_id: int) -> Path | None:
    """Claim a deliberately planted, one-shot maintenance fault marker.

    This hook is inert unless an operator creates the exact group marker
    containing ``STALL_ONCE``.  Renaming the request before stalling makes
    the injection one-shot across the supervisor-driven process restart and
    leaves an evidence file with the PID/timestamp.  It exists solely to
    exercise the real websocket timeout/force-close/orphan/recycle path on
    VM-DP6 during an approved maintenance window.
    """
    request = RUN_DIR / f"fault_inject_ws_callback_stall_g{group_id}.request"
    try:
        if request.read_text(encoding="utf-8").strip() != "STALL_ONCE":
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        active = request.with_name(f"{request.stem}.active.{os.getpid()}.{stamp}")
        request.replace(active)
        return active
    except (FileNotFoundError, OSError):
        return None


# Which asset types are watched live is operator-configurable
# (LIVE_ASSET_TYPES), defaulting to the historical "Indice,Metal,Crypto"
# scope - see settings.operational.LiveSettings.asset_types for why the 26
# FOREX symbols are not in this set by default and why that is a business
# decision, not a bug to silently fix here.
WS_SYMBOLS = _resolve_ws_symbols(SYMBOLS, LIVE.asset_types)

_check_expected_live_symbol_count(len(WS_SYMBOLS), LIVE.expected_symbol_count, LIVE.asset_types)

WS_LOG_FILE = str(WS_LIVE_LOG)

logger = get_logger(
    "live_fetching",
    WS_LOG_FILE,
    rotating=True,
    utc=True,
    pipe_format=True,
    normalize_prefixes=True,
)


def _setup_message_only_file_logger(name: str, log_file) -> logging.Logger:
    aux_logger = logging.getLogger(name)
    if aux_logger.handlers:
        return aux_logger
    aux_logger.setLevel(logger.level)
    aux_logger.propagate = False
    os.makedirs(os.path.dirname(os.path.abspath(str(log_file))), exist_ok=True)
    handler = ResilientRotatingFileHandler(
        str(log_file),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    aux_logger.addHandler(handler)
    return aux_logger


report_logger = get_logger(
    "live_reports",
    str(WS_LIVE_REPORT_LOG),
    rotating=True,
    console=False,
    utc=True,
    pipe_format=True,
)


def _llog(event: str, *details: str, **fields) -> str:
    return live_operation_line(event, *details, **fields)

_LOCAL_RUNTIME_LOCK_FILE = WS_LIVE_PID

TV_BASE_URL = "wss://data.tradingview.com/socket.io/websocket"

_live_settings = LIVE

WS_SYMBOLS_PER_CONN = _live_settings.symbols_per_conn
N_BARS_WS = _live_settings.n_bars
BATCH_INTERVAL_MIN = _live_settings.batch_interval_min
SHUTDOWN_POLL_SEC = _live_settings.shutdown_poll_sec
BATCH_FETCH_TIMEOUT = _live_settings.batch_fetch_timeout_sec
WS_THREAD_JOIN_GRACE_SEC = _live_settings.ws_thread_join_grace_sec
BATCH_MAX_RETRIES = _live_settings.batch_max_retries
RECONNECT_BASE_SEC = _live_settings.reconnect_base_sec
RECONNECT_MAX_SEC = _live_settings.reconnect_max_sec
BATCH_GROUP_JOIN_TIMEOUT_SEC = _live_settings.batch_group_join_timeout_sec
GROUP_WEDGE_HARD_DEADLINE_BATCHES = _live_settings.group_wedge_hard_deadline_batches
STATE_HEARTBEAT_SEC = _live_settings.state_heartbeat_sec
TV_WS_GUEST_POLICY = _live_settings.guest_policy
TV_WS_GUEST_PAUSE_SEC = _live_settings.guest_pause_sec
TV_WS_RATE_LIMIT_COOLDOWN_SEC = _live_settings.rate_limit_cooldown_sec
TV_WS_FORBIDDEN_COOLDOWN_SEC = _live_settings.forbidden_cooldown_sec
TV_WS_PREFLIGHT_REQUIRE_HEADLESS = _live_settings.preflight_require_headless
TV_WS_CONNECTIVITY_PREFLIGHT = _live_settings.connectivity_preflight
TV_WS_CONNECTIVITY_TIMEOUT_SEC = _live_settings.connectivity_timeout_sec
TV_WS_CONNECTIVITY_COOLDOWN_SEC = _live_settings.connectivity_cooldown_sec


DB_QUEUE_MAXSIZE = _live_settings.db_queue_maxsize
OVERFLOW_BUFFER_MAX = _live_settings.overflow_buffer_max
SESSION_THROTTLE = _live_settings.session_throttle_sec
STATUS_INTERVAL_SEC = _live_settings.status_interval_sec
ETL_DIRECT_RETRIES = _live_settings.etl_direct_retries
ETL_DIRECT_RETRY_DELAY_SEC = _live_settings.etl_direct_retry_delay_sec
ETL_DEFERRED_RETRY_COOLDOWN_SEC = _live_settings.etl_deferred_retry_cooldown_sec

TOKEN_EXPIRY_KEYWORDS = _tv_auth.TOKEN_EXPIRY_KEYWORDS

MAX_MISS_RETRIES = _live_settings.max_miss_retries
N_BARS_WS_BACKLOG = _live_settings.n_bars_backlog
MAX_BACKLOG_BATCHES = _live_settings.max_backlog_batches
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

_SYMBOL_NAME_BY_ID = {sid: s["tv_symbol"] for sid, s in _SYMBOL_META_BY_ID.items()}

_reporter = LiveReporter(logger, _SYMBOL_NAME_BY_ID)

_report_file_reporter = LiveReporter(report_logger, _SYMBOL_NAME_BY_ID)


def _log_report_block(title: str, lines: list[str], level: int = logging.INFO) -> None:
    _append_live_table_text(_reporter.format_block(title, lines, level=level))
    _report_file_reporter.log_block(title, lines, level)



def _live_log_rotating_handler() -> logging.Handler | None:
    for handler in logger.handlers:
        if isinstance(handler, ResilientRotatingFileHandler):
            return handler
    return None


def _maybe_rotate_live_log() -> None:
    """Manually trigger the same size-based rollover _append_live_table_text's
    raw file writes below would otherwise bypass entirely.

    logger's ResilientRotatingFileHandler only ever checks maxBytes inside
    its own emit() - i.e. only when a real logger.info()/warning()/etc.
    call happens. The live candle-flow table (the majority of this file's
    volume - a full table is appended per batch, per candle row) is
    written via a separate raw path.open("a") append instead of through
    the logger, so it never triggers that check. The size cap was
    effectively unenforced for most of this file's actual growth: rollover
    would only happen whenever the next unrelated logger.X() call
    happened to fire, by which point the file could already be many times
    over maxBytes. This re-checks after every raw append instead.
    """
    handler = _live_log_rotating_handler()
    if handler is None:
        return
    try:
        if handler.maxBytes > 0 and os.path.getsize(WS_LOG_FILE) >= handler.maxBytes:
            # Acquire the handler's own lock before calling doRollover()
            # directly - emit() normally holds this for the same reason
            # (a concurrent logger.info() call from another thread must
            # not race a rollover happening underneath it).
            handler.acquire()
            try:
                handler.doRollover()
            finally:
                handler.release()
    except Exception:
        pass


def _append_live_table_text(text: str) -> None:
    block = str(text).strip("\n")
    if not block:
        return
    try:
        print(block, flush=True)
    except Exception:
        pass
    try:
        path = Path(WS_LOG_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _live_table_file_lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(block + "\n")
            _maybe_rotate_live_log()
    except Exception as exc:
        logger.warning("Could not write live table log: %s", exc)

def _log_block(level: int, text: str) -> None:
    log_live_block(logger, level, text)


def _log_report_text(level: int, text: str) -> None:
    log_live_block(report_logger, level, text)


def _log_candle_block(text: str) -> None:
    _append_live_table_text(text)


def _start_candle_table(batch_id: int) -> None:
    with _candle_table_lock:
        _state._candle_table_rows = 0
    _log_candle_block(live_candle_section(batch_id))


def _log_candle_row(line: str) -> None:
    with _candle_table_lock:
        if _state._candle_table_rows > 0 and _state._candle_table_rows % _CANDLE_HEADER_REPEAT_ROWS == 0:
            _log_candle_block(live_candle_header_refresh())
        _state._candle_table_rows += 1
    _append_live_table_text(line)

_fmt_pair_label = _reporter.pair_label

_summarize_pair_counts = _reporter.pair_counts

_summarize_counts_by_symbol = _reporter.counts_by_symbol

_summarize_counts_by_tf = _reporter.counts_by_tf

_summarize_backlog = _reporter.backlog


def _write_live_summary(row: dict) -> None:
    try:
        # Size-capped instead of a bare unbounded append: an actively-
        # written file's mtime never looks "old" to the age-based runtime
        # retention job, so this file was growing forever otherwise - see
        # core_engine.util.logkit.jsonl.
        append_jsonl_capped(LIVE_SUMMARY_LOG, row)
    except Exception as exc:
        logger.warning("Could not write live batch summary: %s", exc)


TV_WS_TIMEZONE = _live_settings.timezone


def _classify_ws_error(error) -> tuple[str, int | None, int]:
    return live_protocol.classify_ws_error(
        error,
        reconnect_max_sec=RECONNECT_MAX_SEC,
        rate_limit_cooldown_sec=TV_WS_RATE_LIMIT_COOLDOWN_SEC,
        forbidden_cooldown_sec=TV_WS_FORBIDDEN_COOLDOWN_SEC,
        token_expiry_keywords=TOKEN_EXPIRY_KEYWORDS,
    )

def _set_ws_cooldown(seconds: int, reason: str) -> None:

    if seconds <= 0:
        return

    until = time.time() + seconds

    notify = False

    with _ws_cooldown_lock:
        if until > _state._ws_cooldown_until + 5:
            _state._ws_cooldown_until = until

            _state._ws_cooldown_reason = reason

            notify = True

    if notify:
        logger.warning("%s", _llog("TradingView reconnect cooldown started", wait_seconds=seconds, reason=reason, result="waiting"))

        _send_alert(
            "WARNING",
            "Live feed is waiting before reconnecting\n"
            f"Waiting time: {seconds}s\n"
            f"Reason: {reason}",
        )

def _wait_for_ws_cooldown(label: str) -> None:
    with _ws_cooldown_lock:
        remaining = max(0.0, _state._ws_cooldown_until - time.time())

        reason = _state._ws_cooldown_reason

    if remaining <= 0:
        return

    logger.warning("%s", _llog("TradingView reconnect cooldown active", worker=label, remaining_seconds=round(remaining), reason=reason, result="waiting"))

    _shutdown.wait(remaining)

def _handle_ws_transport_error(group_id: int, error) -> tuple[str, int | None]:
    kind, status, cooldown = _classify_ws_error(error)

    if kind == "normal_close":
        logger.info(
            "%s",
            _llog(
                "TradingView WebSocket closed normally",
                group=group_id,
                code=status or 1000,
                result="closed",
            ),
        )
        return kind, status

    logger.error("%s", _llog("TradingView WebSocket error", group=group_id, type=kind, status=status or "n/a", reason=error, result="failed"))

    with _state_lock:
        _stats["ws_errors"] += 1

        if kind == "auth":
            _stats["ws_auth_errors"] += 1

        elif kind == "rate_limit":
            _stats["ws_rate_limits"] += 1

        elif kind == "server":
            _stats["ws_server_errors"] += 1

    with _hourly_lock:
        _hourly_stats["ws_errors"] = int(_hourly_stats.get("ws_errors", 0)) + 1

    if kind == "auth":
        _tv_auth.set_current_token(_tv_auth.GUEST_TOKEN)

        threading.Thread(
            target=_tv_auth.renew,
            args=(logger,),
            daemon=True,
            name="ws-auth-renew",
        ).start()

        if cooldown:
            _set_ws_cooldown(cooldown, f"TradingView WS auth/forbidden status={status}")

    elif kind == "rate_limit":
        _set_ws_cooldown(cooldown, f"TradingView WS rate limit status={status or 'unknown'}")

    return kind, status

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

_future_cutoff_ts = future_cutoff_ts

_as_utc_timestamp = as_utc_timestamp

def _fmt_bar_time_utc(value: float | int | datetime) -> str:
    try:
        if isinstance(value, datetime):
            dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
        else:
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        return dt.strftime("%H:%M UTC")
    except Exception:
        return str(value)

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

def _set_received_watermark(key: tuple[int, str], max_ts: float) -> None:
    with _state_lock:
        _received_bar_ts[key] = max(max_ts, _received_bar_ts.get(key, 0.0))

def _set_source_watermark(key: tuple[int, str], max_ts: float) -> None:
    with _state_lock:
        _source_bar_ts[key] = max(max_ts, _source_bar_ts.get(key, 0.0))

def _set_committed_watermark(key: tuple[int, str], max_ts: float) -> None:
    with _state_lock:
        _last_bar_ts[key] = max(max_ts, _last_bar_ts.get(key, 0.0))

def _init_batch_metrics(batch_id: int) -> None:
    with _batch_metrics_lock:
        _batch_metrics[batch_id] = {
            "accepted": 0,
            "db_processed": 0,
            "staging_rows": 0,
            "fact_inserted": 0,
            "deferred_items": 0,
            "errors": 0,
            "pair_accepted": {},
            "pair_fact": {},
        }

        if len(_batch_metrics) > MAX_BATCH_METRIC_HISTORY:
            for old_batch_id in sorted(_batch_metrics)[:-MAX_BATCH_METRIC_HISTORY]:
                if old_batch_id != 0:
                    _batch_metrics.pop(old_batch_id, None)

def _record_batch_accepted(batch_id: int, key: tuple[int, str], count: int) -> None:
    if count <= 0:
        return

    with _batch_metrics_lock:
        metrics = _batch_metrics.setdefault(
            batch_id,
            {
                "accepted": 0,
                "db_processed": 0,
                "staging_rows": 0,
                "fact_inserted": 0,
                "deferred_items": 0,
                "errors": 0,
                "pair_accepted": {},
                "pair_fact": {},
            },
        )

        metrics["accepted"] += count

        metrics["pair_accepted"][key] = metrics["pair_accepted"].get(key, 0) + count

    with _state_lock:
        _stats["accepted_bars"] += count

def _record_db_result(
    batch_id: int,
    key: tuple[int, str],
    accepted_count: int,
    staging_rows: int,
    fact_inserted: int,
    *,
    deferred: bool = False,
    error: bool = False,
) -> None:
    staging_rows = max(0, int(staging_rows or 0))

    fact_inserted = max(0, int(fact_inserted or 0))

    with _batch_metrics_lock:
        metrics = _batch_metrics.setdefault(
            batch_id,
            {
                "accepted": 0,
                "db_processed": 0,
                "staging_rows": 0,
                "fact_inserted": 0,
                "deferred_items": 0,
                "errors": 0,
                "pair_accepted": {},
                "pair_fact": {},
            },
        )

        metrics["db_processed"] += max(0, accepted_count)

        metrics["staging_rows"] += staging_rows

        metrics["fact_inserted"] += fact_inserted

        if deferred:
            metrics["deferred_items"] += 1

        if error:
            metrics["errors"] += 1

        if fact_inserted:
            metrics["pair_fact"][key] = metrics["pair_fact"].get(key, 0) + fact_inserted

    if fact_inserted:
        with _hourly_lock:
            _hourly_stats["fact_bars"] += fact_inserted

            _hourly_stats["pair_bars"][key] = _hourly_stats["pair_bars"].get(key, 0) + fact_inserted

    if staging_rows or fact_inserted:
        with _state_lock:
            _stats["staging_rows"] += staging_rows

            _stats["fact_inserted"] += fact_inserted

            _stats["bars_inserted"] += fact_inserted

        if staging_rows:
            with _hourly_lock:
                _hourly_stats["staging_rows"] += staging_rows

                _hourly_stats["pair_staging"][key] = (
                    _hourly_stats["pair_staging"].get(key, 0) + staging_rows
                )

def _record_etl_direct_error(
    batch_id: int,
    key: tuple[int, str],
    accepted_count: int,
    inserted: int,
    tv_symbol: str,
    tf_code: str,
    exc: Exception,
) -> None:
    logger.error("%s", _llog("Main table update failed", symbol=tv_symbol, timeframe=tf_code, reason=exc, result="failed"))

    _increment_data_error_counter()

    _record_db_result(batch_id, key, accepted_count, inserted, 0, error=True)

def _run_etl_direct_with_retry(
    symbol_id: int,
    tf_code: str,
    staging_table: str,
    tv_symbol: str,
    *,
    context: str,
    from_time: str | None,
    max_attempts: int | None = None,
) -> int:
    last_exc: Exception | None = None

    attempts = ETL_DIRECT_RETRIES if max_attempts is None else max(1, int(max_attempts))

    for attempt in range(1, attempts + 1):
        try:
            source = "live_fetching" if context == "live" else f"live_fetching_{context}"
            return run_etl_direct(
                symbol_id,
                tf_code,
                staging_table,
                source=source,
                symbol=tv_symbol,
                from_time=from_time,
            )

        except Exception as exc:
            last_exc = exc

            if attempt >= attempts:
                break

            delay = ETL_DIRECT_RETRY_DELAY_SEC * attempt

            logger.warning(
                "%s",
                _llog(
                    "Main table update retry scheduled",
                    symbol=tv_symbol,
                    timeframe=tf_code,
                    context=context,
                    attempt=f"{attempt + 1}/{attempts}",
                    wait_seconds=delay,
                    reason=exc,
                    result="retrying",
                ),
            )

            if _shutdown.wait(delay):
                break

    if last_exc is not None:
        raise last_exc

    raise RuntimeError("ETL direct retry interrupted")

def _snapshot_batch_metrics(batch_id: int) -> dict:
    with _batch_metrics_lock:
        metrics = dict(_batch_metrics.get(batch_id, {}))

        metrics["pair_accepted"] = dict(metrics.get("pair_accepted", {}))

        metrics["pair_fact"] = dict(metrics.get("pair_fact", {}))

        return metrics

def _wait_for_batch_db(batch_id: int, timeout_sec: float = BATCH_DB_REPORT_WAIT_SEC) -> dict:
    deadline = time.monotonic() + timeout_sec

    while True:
        metrics = _snapshot_batch_metrics(batch_id)

        accepted = int(metrics.get("accepted", 0))

        processed = int(metrics.get("db_processed", 0))

        if accepted == 0 or processed >= accepted or time.monotonic() >= deadline:
            return metrics

        _shutdown.wait(0.25)

        if _shutdown.is_set():
            return _snapshot_batch_metrics(batch_id)

def _flush_overflow_to_queue() -> None:
    with _overflow_lock:
        recharged = 0

        remaining = []

        for item in _overflow_buf:
            try:
                _db_queue.put_nowait(item)

                recharged += 1

            except queue.Full:
                remaining.append(item)

        _overflow_buf[:] = remaining

        if recharged:
            logger.info("%s", _llog("Memory safety buffer restored to queue", bars=recharged, result="queued"))

    if _spool_pending.is_set() and not _db_queue.full():
        _lease_spool_into_queue()

        pending = _spool.count()
        if pending == 0:
            _spool_pending.clear()
        elif pending is None or pending > 0:
            _spool_pending.set()

    if _state.spool_full_pause:
        pending = _spool.count()
        reserve_rows = min(MAX_SPOOL_ROWS, len(WS_SYMBOLS) * len(WS_TF_CODES))
        if pending is not None and pending + reserve_rows <= MAX_SPOOL_ROWS:
            _state.spool_full_pause = False
            logger.warning(
                "%s",
                _llog(
                    "Durable outbox capacity restored - batches resumed",
                    pending=pending,
                    reserved_rows=reserve_rows,
                    result="resumed",
                ),
            )


def _lease_spool_into_queue(limit: int = 200) -> int:
    """Non-destructively claim pending spool rows and hand
    them to the RAM dispatch queue. Unlike the old flush_to_queue(), a row
    stays in the spool (status='leased') until _db_worker acks it - so a
    crash before that ack simply leaves it eligible to be leased again."""
    leased = _spool.lease_batch(limit=limit)
    queued = 0
    for row_id, item in leased:
        try:
            _db_queue.put_nowait((row_id, *item))
            queued += 1
        except queue.Full:
            # Release immediately rather than holding a lease on a row we
            # could not hand off - otherwise it sits "leased" and
            # unavailable to a future pass until the lease time expires.
            _spool.release_for_retry(row_id, error="queue full during lease recovery")
    if queued:
        logger.info("%s", _llog("Recovered bars from durable outbox", bars=queued, result="queued"))
        with _state_lock:
            _stats["queue_depth"] = _db_queue.qsize()
    return queued

def _enqueue_or_buffer(item: tuple, group_id: int, tv_symbol: str, tf_code: str) -> str:
    """Persist item to the durable spool FIRST, then hand a (row_id, item)
    tuple to the fast in-memory dispatch path (queue, then RAM overflow).

    The row is only deleted from the spool by _db_worker's exact ACK after
    both staging and Fact commit succeed - never here. This means every candle is
    durable on disk from the moment it is accepted, and a process crash at
    any point between here and a successful staging commit loses nothing:
    the next process start leases the still-present row and retries it.
    """
    try:
        row_id = _spool.persist_pending(item)
    except Exception as exc:
        logger.error(
            "%s",
            _llog("Durable outbox write failed", group=group_id, symbol=tv_symbol, timeframe=tf_code, reason=exc, result="rejected"),
        )
        _send_alert(
            "ERROR",
            "Live feed could not durably store a candle\n"
            f"Affected pair: {tv_symbol}/{tf_code}\n"
            "The disk safety buffer write itself failed.\n"
            "Suggested action: check disk space and permissions now." + QUICK_COMMANDS_HINT,
        )
        _increment_data_error_counter()
        with _state_lock:
            _stats["queue_depth"] = _db_queue.qsize()
        return "rejected"

    if row_id is None:
        # The durable outbox itself is full. Accepting the item anyway
        # would mean holding it only in RAM with no durability guarantee -
        # exactly the gap this design exists to close. Pause new batch
        # fetching instead (checked by _spool_full_blocks_batch) and raise
        # CRITICAL every time so the operator cannot miss a growing
        # backlog silently.
        _state.spool_full_pause = True
        logger.critical(
            "%s",
            _llog(
                "Durable outbox full - new batches paused",
                group=group_id,
                symbol=tv_symbol,
                timeframe=tf_code,
                capacity=MAX_SPOOL_ROWS,
                result="paused",
            ),
        )
        _send_alert(
            "CRITICAL",
            "Live feed cannot store new candles safely\n"
            f"Disk safety buffer is full: {MAX_SPOOL_ROWS} rows\n"
            f"Affected pair: {tv_symbol}/{tf_code}\n"
            "New batches are paused until the backlog drains.\n"
            "Suggested action: check SQL Server and free disk space now." + QUICK_COMMANDS_HINT,
        )
        _increment_data_error_counter()
        with _state_lock:
            _stats["queue_depth"] = _db_queue.qsize()
        return "rejected"

    if not _spool.claim_for_dispatch(row_id):
        # Another recovery pass claimed it first. Its durable copy remains
        # authoritative; do not create a second in-memory delivery.
        _spool_pending.set()
        logger.warning(
            "%s",
            _llog(
                "Durable outbox row already claimed",
                group=group_id,
                symbol=tv_symbol,
                timeframe=tf_code,
                row_id=row_id,
                result="spooled",
            ),
        )
        return "spooled"

    queued_item = (row_id, *item)
    try:
        _db_queue.put_nowait(queued_item)

        with _state_lock:
            _stats["queue_depth"] = _db_queue.qsize()

        return "queued"

    except queue.Full:
        with _overflow_lock:
            buf_len = len(_overflow_buf)

            if buf_len < OVERFLOW_BUFFER_MAX:
                _overflow_buf.append(queued_item)

                new_len = buf_len + 1

                logger.warning(
                    "%s",
                    _llog(
                        "Database write queue full",
                        group=group_id,
                        symbol=tv_symbol,
                        timeframe=tf_code,
                        memory_buffer_rows=new_len,
                        result="buffered",
                    ),
                )

                warn_threshold = int(OVERFLOW_BUFFER_MAX * 0.8)

                if new_len >= warn_threshold and buf_len < warn_threshold:
                    logger.error(
                        "%s",
                        _llog(
                            "Memory safety buffer near capacity",
                            rows=new_len,
                            capacity=OVERFLOW_BUFFER_MAX,
                            risk="database_writer_may_be_slow",
                            result="warning",
                        ),
                    )

                    _send_alert(
                        "WARNING",
                        "Database writer is falling behind\n"
                        f"Memory safety buffer: {new_len}/{OVERFLOW_BUFFER_MAX}\n"
                        "Suggested action: check SQL Server connection and disk speed.",
                    )

                with _state_lock:
                    _stats["queue_depth"] = _db_queue.qsize()

                return "buffered"

            else:
                # Both the RAM queue and RAM overflow buffer are full, but
                # the item is already safely persisted on disk (above) -
                # nothing to lose here. It will be picked up on the next
                # lease pass in _flush_overflow_to_queue once room frees up.
                _spool.release_for_retry(row_id, error="RAM dispatch capacity full")
                _spool_pending.set()

                logger.warning(
                    "%s",
                    _llog("Disk safety buffer used", group=group_id, symbol=tv_symbol, timeframe=tf_code, result="spooled"),
                )

                with _state_lock:
                    _stats["queue_depth"] = _db_queue.qsize()

                return "spooled"

    except Exception as exc:
        _spool.release_for_retry(row_id, error=f"unexpected queue failure: {exc}")
        _spool_pending.set()
        logger.exception(
            "%s",
            _llog("Unexpected queue failure", group=group_id, symbol=tv_symbol, timeframe=tf_code, reason=exc, result="dropped"),
        )

        _increment_data_error_counter()

        with _state_lock:
            _stats["queue_depth"] = _db_queue.qsize()

        return "rejected"

def _write_defer_lock_active() -> bool:
    now = time.monotonic()

    if now - _write_defer_lock_cache["checked_at"] < _WRITE_DEFER_LOCK_TTL:
        return _write_defer_lock_cache["locked"]

    lock_name = ""

    try:
        from core_engine.util.coordination.locks import is_locked

        result = False

        for task_name in _WRITE_DEFER_LOCKS:
            if is_locked(task_name):
                result = True

                lock_name = task_name

                break

    except Exception:
        result = False

    _write_defer_lock_cache.update({"locked": result, "checked_at": now, "name": lock_name})

    return result

def _write_defer_lock_name() -> str:
    name = str(_write_defer_lock_cache.get("name") or "").strip()

    return name or "write-defer"

def _wait_for_queue_drain(timeout_sec: float) -> bool:
    """Bounded equivalent of _db_queue.join() - returns True if the queue
    fully drained within timeout_sec, False on timeout. Uses the same
    unfinished_tasks/mutex Queue.join() itself relies on internally."""
    deadline = time.monotonic() + max(0.0, timeout_sec)
    while True:
        with _db_queue.mutex:
            if _db_queue.unfinished_tasks == 0:
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)


def _report_db_worker_stopped_at_shutdown(pending_items: int) -> bool:
    """Report a stopped DB worker; return True only when data is at risk."""
    pending_items = max(0, int(pending_items))
    if pending_items > 0:
        logger.critical(
            "%s",
            _llog(
                "Database writer unavailable during shutdown",
                pending_items=pending_items,
                action="skip_unbounded_queue_wait",
                result="failed",
            ),
        )
        return True
    logger.info(
        "%s",
        _llog(
            "Database writer already stopped after drain",
            pending_items=0,
            result="stopped",
        ),
    )
    return False


def _queue_fact_load(
    *,
    row_id: int,
    batch_id: int,
    symbol_id: int,
    tf_code: str,
    staging_table: str,
    tv_symbol: str,
    accepted_count: int,
    staging_rows: int,
    max_committed_ts: float,
    latest_saved_utc: str,
) -> None:
    """Register one staged outbox row for the independent Fact worker."""
    defer_key = (symbol_id, tf_code, staging_table, tv_symbol)
    with _deferred_lock:
        _etl_item_meta[row_id] = {
            "batch_id": batch_id,
            "key": (symbol_id, tf_code),
            "accepted_count": accepted_count,
            "staging_rows": staging_rows,
            "max_committed_ts": max_committed_ts,
            "latest_saved_utc": latest_saved_utc,
        }
        _deferred_etl[defer_key] = max(
            max_committed_ts,
            _deferred_etl.get(defer_key, 0.0),
        )
        _deferred_etl_next_attempt.setdefault(defer_key, 0.0)
        waiting = len(_deferred_etl)

    if waiting >= _DEFERRED_ETL_MAX:
        logger.error(
            "%s",
            _llog(
                "Main table update backlog high",
                waiting_items=waiting,
                capacity=_DEFERRED_ETL_MAX,
                symbol=tv_symbol,
                timeframe=tf_code,
                result="waiting_in_temporary_table",
            ),
        )
    elif waiting >= _DEFERRED_ETL_WARN:
        logger.warning(
            "%s",
            _llog(
                "Main table update backlog growing",
                waiting_items=waiting,
                capacity=_DEFERRED_ETL_MAX,
                result="waiting",
            ),
        )
    _etl_wakeup.set()


def _take_ready_fact_key() -> tuple[tuple[int, str, str, str] | None, float, float]:
    """Pop one eligible key, preserving dict order for fair round-robin."""
    now_mono = time.monotonic()
    wait_seconds = 1.0
    with _deferred_lock:
        for defer_key, max_ts in list(_deferred_etl.items()):
            next_attempt = float(_deferred_etl_next_attempt.get(defer_key, 0.0) or 0.0)
            if next_attempt > now_mono:
                wait_seconds = min(wait_seconds, max(0.05, next_attempt - now_mono))
                continue
            _deferred_etl.pop(defer_key, None)
            _deferred_etl_next_attempt.pop(defer_key, None)
            return defer_key, max_ts, 0.0
    return None, 0.0, wait_seconds


def _db_worker() -> None:
    """Persist live candles to staging without waiting on Fact ETL."""
    logger.info("%s", _llog("Database staging writer started", result="running"))
    _db_worker_done.clear()
    while True:
        _flush_overflow_to_queue()
        if _shutdown.is_set() and _db_queue.empty():
            with _overflow_lock:
                overflow_pending = len(_overflow_buf)
            if overflow_pending == 0 and (_spool.count_unstaged() or 0) == 0:
                break

        try:
            item = _db_queue.get(timeout=1.0)
            with _state_lock:
                _stats["queue_depth"] = _db_queue.qsize()
        except queue.Empty:
            continue

        row_id, batch_id, symbol_id, tf_code, staging_table, tv_symbol, df = item
        key = (symbol_id, tf_code)
        accepted_count = len(df.index) if hasattr(df, "index") else 0
        df, _ = validate_ohlcv_df(
            df, tv_symbol, tf_code, logger, normalize_timestamps=False
        )
        if df.empty:
            _record_db_result(batch_id, key, accepted_count, 0, 0)
            _spool.ack(row_id)
            _db_queue.task_done()
            continue

        staging_retries = 3
        inserted = 0
        staging_ok = False
        for attempt in range(1, staging_retries + 1):
            try:
                inserted = insert_staging_batch(
                    df,
                    symbol_id,
                    staging_table,
                    source="live_fetching",
                    symbol=tv_symbol,
                    tf_code=tf_code,
                )
                staging_ok = True
                break
            except Exception as exc:
                if attempt == staging_retries:
                    logger.error(
                        "%s",
                        _llog(
                            "Temporary table write failed",
                            symbol=tv_symbol,
                            timeframe=tf_code,
                            attempts=staging_retries,
                            reason=exc,
                            result="durable_retry",
                        ),
                    )
                    _increment_data_error_counter()
                    _send_alert(
                        "ERROR",
                        "Database staging save failed after retries\n"
                        f"Affected pair: {tv_symbol}/{tf_code}\n"
                        f"Retries: {staging_retries}\nReason: `{exc}`",
                    )
                else:
                    logger.warning(
                        "%s",
                        _llog(
                            "Temporary table write retry scheduled",
                            symbol=tv_symbol,
                            timeframe=tf_code,
                            attempt=f"{attempt}/{staging_retries}",
                            wait_seconds=5,
                            reason=exc,
                            result="retrying",
                        ),
                    )
                    _shutdown.wait(5)

        if not staging_ok:
            _record_db_result(batch_id, key, accepted_count, 0, 0, error=True)
            _spool.release_for_retry(
                row_id, error="insert_staging_batch failed after retries"
            )
            _db_queue.task_done()
            continue

        # The row remains durable in status='staged' until the independent
        # Fact worker commits usp_LoadDirect and acknowledges this exact id.
        _spool.mark_staged(row_id)
        max_committed_ts = max(_as_utc_timestamp(ts) for ts in df.index)
        latest_saved_utc = _fmt_bar_time_utc(max_committed_ts)
        _queue_fact_load(
            row_id=row_id,
            batch_id=batch_id,
            symbol_id=symbol_id,
            tf_code=tf_code,
            staging_table=staging_table,
            tv_symbol=tv_symbol,
            accepted_count=accepted_count,
            staging_rows=inserted,
            max_committed_ts=max_committed_ts,
            latest_saved_utc=latest_saved_utc,
        )
        _db_queue.task_done()
        with _state_lock:
            _stats["queue_depth"] = _db_queue.qsize()

    _db_worker_done.set()
    _etl_wakeup.set()
    logger.info("%s", _llog("Database staging writer stopped", result="stopped"))


def _etl_worker() -> None:
    """Load staged rows into Fact fairly, one bounded attempt per key."""
    logger.info("%s", _llog("Fact loader started", result="running"))
    while True:
        if _write_defer_lock_active():
            if _shutdown.is_set() and _db_worker_done.is_set():
                break
            _etl_wakeup.wait(1.0)
            _etl_wakeup.clear()
            continue

        defer_key, max_ts, wait_seconds = _take_ready_fact_key()
        if defer_key is None:
            if _shutdown.is_set() and _db_worker_done.is_set():
                break
            _etl_wakeup.wait(wait_seconds)
            _etl_wakeup.clear()
            continue

        sym_id, tf_c, stg_tbl, sym_nm = defer_key
        covered_spool_ids: list[int] = []
        try:
            covered_spool_ids, covered_from_time = _spool.staged_snapshot_for_key(
                sym_id, tf_c, stg_tbl, sym_nm
            )
            if not covered_spool_ids:
                with _deferred_lock:
                    stale_ids = [
                        row_id
                        for row_id, meta in _etl_item_meta.items()
                        if meta.get("key") == (sym_id, tf_c)
                    ]
                    for row_id in stale_ids:
                        _etl_item_meta.pop(row_id, None)
                continue

            affected = _run_etl_direct_with_retry(
                sym_id,
                tf_c,
                stg_tbl,
                sym_nm,
                context="deferred",
                from_time=covered_from_time,
                max_attempts=1,
            )
            # Fact commit happened inside run_etl_direct. Ack only the exact
            # pre-call snapshot; a concurrently staged row remains for the
            # next key turn.
            _spool.ack_staged_ids(covered_spool_ids)
        except Exception as exc:
            logger.error(
                "%s",
                _llog(
                    "Delayed main table update failed",
                    symbol=sym_nm,
                    timeframe=tf_c,
                    retry_after_seconds=ETL_DEFERRED_RETRY_COOLDOWN_SEC,
                    reason=exc,
                    result="deferred",
                ),
            )
            _increment_data_error_counter()
            if not _shutdown.is_set():
                with _deferred_lock:
                    _deferred_etl[defer_key] = max(
                        max_ts, _deferred_etl.get(defer_key, 0.0)
                    )
                    _deferred_etl_next_attempt[defer_key] = (
                        time.monotonic() + ETL_DEFERRED_RETRY_COOLDOWN_SEC
                    )
                _etl_wakeup.set()
            continue

        with _deferred_lock:
            covered_meta = [
                _etl_item_meta.pop(row_id, None) for row_id in covered_spool_ids
            ]
        covered_meta = [meta for meta in covered_meta if meta is not None]
        committed_ts = max(
            [max_ts] + [float(meta["max_committed_ts"]) for meta in covered_meta]
        )
        _set_committed_watermark((sym_id, tf_c), committed_ts)

        for index, meta in enumerate(covered_meta):
            _record_db_result(
                int(meta["batch_id"]),
                tuple(meta["key"]),
                int(meta["accepted_count"]),
                int(meta["staging_rows"]),
                affected if index == 0 else 0,
            )
        if affected or any(int(meta["staging_rows"]) for meta in covered_meta):
            publish_candle_snapshot(sym_id, sym_nm, tf_c)
            _log_candle_row(
                live_db_line(
                    logged_at=datetime.now(timezone.utc).strftime("%H:%M:%S"),
                    symbol=sym_nm,
                    timeframe=tf_c,
                    action="saved",
                    candles=sum(int(meta["accepted_count"]) for meta in covered_meta),
                    latest_utc=(
                        covered_meta[-1]["latest_saved_utc"]
                        if covered_meta
                        else _fmt_bar_time_utc(committed_ts)
                    ),
                    temporary_rows=sum(int(meta["staging_rows"]) for meta in covered_meta),
                    saved_rows=affected,
                )
            )

    logger.info(
        "%s",
        _llog(
            "Fact loader stopped",
            staged_rows_remaining=_spool.count() or 0,
            action="remaining_rows_requeue_on_next_start",
            result="stopped",
        ),
    )

_gen_id = live_protocol.gen_session_id

_send = live_protocol.send_tv_message

_parse_packets = live_protocol.parse_packets

_bars_to_df = live_protocol.bars_to_df

def _cleanup_dead_ws_live_runtime_lock() -> bool:
    return cleanup_dead_runtime_lock(
        task_name="ws_live_runtime",
        get_connection=get_connection,
        logger=logger,
    )

_acquire_local_runtime_lock = _runtime_lock.acquire

_release_local_runtime_lock = _runtime_lock.release

_ws_live_runtime_payload = make_runtime_payload

def _is_token_error(msg_type: str, data: str) -> bool:
    if msg_type in ("error", "critical_error"):
        return any(kw in data.lower() for kw in TOKEN_EXPIRY_KEYWORDS)

    return False

def _update_missed_pairs(
    received: set[tuple[int, str]],
    missed: set[tuple[int, str]],
) -> None:
    alerts: list[tuple[tuple[int, str], int]] = []

    with _missed_lock:
        for key in received:
            _missed_pairs.pop(key, None)

        for key in missed:
            count = _missed_pairs.get(key, 0) + 1

            _missed_pairs[key] = count

            if count >= MAX_MISS_RETRIES:
                alerts.append((key, count))

                _missed_pairs[key] = 0

    for (symbol_id, tf_code), count in alerts:
        sym_name = next(
            (s["tv_symbol"] for s in WS_SYMBOLS if s["symbol_id"] == symbol_id),
            str(symbol_id),
        )

        logger.warning(
            "[MISS] %s [%s] missed %d batch(es) in a row - sending alert.",
            sym_name,
            tf_code,
            count,
        )

        _send_alert(
            "WARNING",
            "Live feed is missing repeated candles\n"
            f"Pair: {sym_name}/{tf_code}\n"
            f"Repeated misses: {count} live batches\n"
            "Suggested action: check TradingView availability for this pair, then run historical backfill if a gap remains.",
        )

class BatchFetcher:
    def __init__(self, group_id: int, symbols: list) -> None:
        self.group_id = group_id

        self.symbols = symbols

        self._cs_map: dict[str, tuple[int, str, str, str]] = {}

        self._expected: set[str] = set()

        self._received: set[str] = set()

        self._new_bars_count = 0

        self._pair_new_bars: dict[tuple[int, str], int] = {}

        self._batch_id = 0

        self._registering: bool = False

        self._done = threading.Event()

        self._lock = threading.Lock()

        self._ws: websocket.WebSocketApp | None = None

        self._fetch_token = 0

        self._fault_marker_checked_token = 0

        self._timed_out = False

        self._report_title = ""

        self._report_lines: list[str] = []

        self._report_level = logging.INFO

        # Persistent-worker plumbing (High-11 redesign): one dedicated
        # thread per group, started once in main() and reused for the
        # process lifetime, instead of _run_batch spawning a fresh
        # batch-g{id} thread every scheduled cycle. request_batch()
        # signals this event; the worker loop clears it, runs the fetch
        # retry loop, then signals _batch_complete. This also guarantees
        # at most one fetch attempt in flight per group at a time - the
        # previous design could start a brand-new batch-g{id}/ws-g{id}
        # thread pair for a group whose previous cycle's thread was still
        # running past BATCH_GROUP_JOIN_TIMEOUT_SEC.
        self._batch_request = threading.Event()

        self._batch_complete = threading.Event()

        # Set by the scheduler when the outer batch deadline has already
        # published/released this cycle.  A worker must not keep retrying an
        # old batch after that point: late callbacks mutate backlog/metrics
        # after the report and can race the next request's completion Event.
        self._batch_cancel = threading.Event()

        self._pending_batch_id: int | None = None

        self._busy = False

        self._worker_thread: threading.Thread | None = None

        # Consecutive scheduled batches this group's worker was still
        # busy (wedged) at BATCH_GROUP_JOIN_TIMEOUT_SEC. Reset to 0 the
        # moment the worker completes a batch. See GROUP_WEDGE_HARD_
        # DEADLINE_BATCHES and _run_batch()'s use of this counter.
        self._consecutive_stuck_batches = 0

        # Once a ws-g{id} thread has ignored both WebSocketApp.close() and a
        # forced raw-socket close, Python cannot reclaim it.  The persistent
        # group worker itself may already be idle at that point, so this flag
        # must survive independently of _busy until the whole child process
        # is recycled by the supervisor.
        self._requires_process_recycle = False

    def start_worker(self) -> None:
        """Start this group's persistent worker thread. Called once from
        main() for every group, before the batch scheduler loop starts."""
        self._worker_thread = threading.Thread(
            target=self._worker_loop, name=f"worker-g{self.group_id}", daemon=True
        )
        self._worker_thread.start()

    def request_batch(self, batch_id: int) -> bool:
        """Hand this group's persistent worker the next batch id to fetch.
        Return False without mutating the pending request when the prior
        cycle is still busy or this process must be recycled."""
        if self._busy or self._requires_process_recycle:
            return False

        self._pending_batch_id = batch_id
        self._batch_cancel.clear()
        self._batch_complete.clear()
        self._batch_request.set()
        return True

    def abandon_batch(self, batch_id: int) -> None:
        """Cancel retry/backoff work after the scheduler released a batch.

        ``fetch()`` has its own bounded socket timeout/force-close path.  The
        event here handles the later retry/backoff loop, ensuring that an old
        batch cannot continue receiving callbacks after its summary and live
        state have already been finalized.
        """

        if self._pending_batch_id == batch_id:
            self._batch_cancel.set()

    def _worker_loop(self) -> None:
        while not _shutdown.is_set():
            if not self._batch_request.wait(timeout=1.0):
                continue

            if _shutdown.is_set():
                return

            self._batch_request.clear()

            batch_id = self._pending_batch_id

            self._busy = True

            try:
                if batch_id is not None:
                    self._fetch_with_retry(batch_id)
            except Exception as exc:  # noqa: BLE001 - a wedged/crashed
                # worker must not silently stop answering future batches.
                logger.error(
                    "%s",
                    _llog(
                        "Connection group worker loop raised",
                        group=self.group_id,
                        reason=exc,
                        result="continuing",
                    ),
                )
            finally:
                self._busy = False

                self._batch_complete.set()

    def _fetch_with_retry(self, batch_id: int) -> None:
        """Attempt this group's batch fetch with retry/backoff. Moved out
        of _run_batch's per-cycle closure into a method so the persistent
        worker thread (see _worker_loop) can call it directly instead of
        a new thread being created to run it every cycle."""
        delay = RECONNECT_BASE_SEC

        for attempt in range(1, BATCH_MAX_RETRIES + 1):
            if _shutdown.is_set() or self._requires_process_recycle or self._batch_cancel.is_set():
                return

            try:
                _wait_for_ws_cooldown(f"G{self.group_id}")

                if _shutdown.is_set():
                    return

                success = self.fetch(batch_id)

                if success or self._batch_cancel.is_set():
                    return

                logger.warning(
                    "%s",
                    _llog(
                        "Connection group fetch incomplete",
                        group=self.group_id,
                        attempt=f"{attempt}/{BATCH_MAX_RETRIES}",
                        result="retrying",
                    ),
                )

            except Exception as exc:
                logger.error(
                    "%s",
                    _llog(
                        "Connection group fetch failed",
                        group=self.group_id,
                        attempt=f"{attempt}/{BATCH_MAX_RETRIES}",
                        reason=exc,
                        result="failed",
                    ),
                )

            if attempt < BATCH_MAX_RETRIES:
                logger.info("%s", _llog("Connection group retry scheduled", group=self.group_id, wait_seconds=delay, result="waiting"))

                if self._batch_cancel.wait(delay):
                    return

                if _shutdown.is_set():
                    return

                delay = min(delay * 2, RECONNECT_MAX_SEC)

    def _next_fetch_token(self) -> int:
        with self._lock:
            self._fetch_token += 1

            self._timed_out = False

            return self._fetch_token

    def _is_current_fetch(self, token: int) -> bool:
        with self._lock:
            return token == self._fetch_token and not self._timed_out and not _shutdown.is_set()

    def _is_current_ws(self, ws) -> bool:
        with self._lock:
            return ws is self._ws and not self._timed_out and not _shutdown.is_set()

    def _build_headers(self) -> list[str]:
        headers = [
            "Origin: https://www.tradingview.com",
            "Referer: https://www.tradingview.com/",
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "

            "AppleWebKit/537.36 (KHTML, like Gecko) "

            "Chrome/124.0.0.0 Safari/537.36",
        ]

        active_cookie = _tv_auth.get_current_cookie()

        if active_cookie:
            headers.append(f"Cookie: {active_cookie}")

        return headers

    def _on_open(self, ws, token: int | None = None) -> None:
        if token is None:
            with self._lock:
                token = self._fetch_token

        if not self._is_current_ws(ws) or not self._is_current_fetch(token):
            return

        logger.debug("%s", _llog("Connection group connected", group=self.group_id, result="registering"))

        threading.Thread(
            target=self._register_sessions,
            args=(ws, token),
            daemon=True,
            name=f"reg-g{self.group_id}",
        ).start()

    def _register_sessions(self, ws, token: int) -> None:
        def _ws_alive() -> bool:
            try:
                return ws.sock is not None and ws.sock.connected

            except Exception:
                return False

        if not self._is_current_ws(ws) or not self._is_current_fetch(token):
            return

        with self._lock:
            self._cs_map.clear()

            self._expected.clear()

            self._received.clear()

        if not _ws_alive() or not self._is_current_fetch(token):
            logger.warning("%s", _llog("Connection group closed before registration", group=self.group_id, result="warning"))

            self._done.set()

            return

        try:
            _send(ws, ["set_auth_token", _tv_auth.get_current_token()])

        except Exception as exc:
            logger.warning("%s", _llog("Connection group auth send failed", group=self.group_id, reason=exc, result="warning"))

            self._done.set()

            return

        time.sleep(0.5)

        with self._lock:
            self._registering = True

        for sym in self.symbols:
            for tf_code, interval in WS_TF_INTERVAL.items():
                if _shutdown.is_set() or not _ws_alive() or not self._is_current_fetch(token):
                    with self._lock:
                        self._registering = False

                    self._done.set()

                    return

                cs = _gen_id("cs")

                staging_table = TF_STAGING[tf_code]

                try:
                    _send(ws, ["chart_create_session", cs, ""])

                    time.sleep(0.1)

                    _send(ws, ["switch_timezone", cs, TV_WS_TIMEZONE])

                    time.sleep(0.05)

                    sym_json = json.dumps(
                        {
                            "symbol": f"{sym['tv_exchange']}:{sym['tv_symbol']}",
                            "adjustment": "splits",
                        }
                    )

                    _send(ws, ["resolve_symbol", cs, "sds_sym_1", f"={sym_json}"])

                    time.sleep(0.1)

                    with _backlog_lock:
                        n_req = (
                            N_BARS_WS_BACKLOG

                            if (sym["symbol_id"], tf_code) in _backlog

                            else N_BARS_WS
                        )

                    _send(
                        ws,
                        [
                            "create_series",
                            cs,
                            "sds_1",
                            "sds_sym_1",
                            "sds_sym_1",
                            interval,
                            n_req,
                            "",
                        ],
                    )

                    self._cs_map[cs] = (sym["symbol_id"], tf_code, staging_table, sym["tv_symbol"])

                    self._expected.add(cs)

                    time.sleep(SESSION_THROTTLE)

                except Exception as exc:
                    logger.warning("%s", _llog("Session registration failed", group=self.group_id, reason=exc, result="warning"))

                    with self._lock:
                        self._registering = False

                    self._done.set()

                    return

        with self._lock:
            self._registering = False

            already_done = self._expected and self._received >= self._expected

        if already_done:
            logger.debug(
                "%s",
                _llog(
                    "Connection group completed",
                    group=self.group_id,
                    sessions=len(self._expected),
                    trigger="post_registration_check",
                    result="closing",
                ),
            )

            self._done.set()

            try:
                ws.close()

            except Exception:
                pass

        logger.debug(
            "%s",
            _llog(
                "Connection group registered",
                group=self.group_id,
                sessions=len(self._expected),
                symbols=len(self.symbols),
                timeframes=len(WS_TF_INTERVAL),
                result="waiting_for_data",
            ),
        )

    def _on_message(self, ws, raw: str, token: int | None = None) -> None:
        if token is None:
            with self._lock:
                token = self._fetch_token

        if not self._is_current_ws(ws) or not self._is_current_fetch(token):
            return

        with self._lock:
            check_fault_marker = self._fault_marker_checked_token != token
            if check_fault_marker:
                self._fault_marker_checked_token = token

        fault_evidence = _claim_ws_callback_stall_fault(self.group_id) if check_fault_marker else None
        if fault_evidence is not None:
            logger.warning(
                "%s",
                _llog(
                    "Controlled WebSocket callback stall injected",
                    group=self.group_id,
                    evidence=fault_evidence,
                    action="exercise_force_close_and_process_recycle",
                    result="fault_injected",
                ),
            )
            # Deliberately ignore shutdown: the purpose of this maintenance
            # hook is to simulate a native callback that Python cannot
            # reclaim.  ws-g{id} is daemonized and the live child must exit.
            while True:
                time.sleep(60)

        with _state_lock:
            _stats["events"] += 1

        for data in _parse_packets(raw):
            if data.startswith("~h~"):
                try:
                    ws.send(f"~m~{len(data)}~m~{data}")

                except Exception:
                    pass

                continue

            try:
                msg = json.loads(data)

            except json.JSONDecodeError:
                continue

            if not isinstance(msg, dict):
                continue

            msg_type = msg.get("m", "")

            p = msg.get("p", [])

            if _is_token_error(msg_type, data):
                logger.warning(
                    "%s",
                    _llog("TradingView auth error detected", group=self.group_id, action="renew_token", result="recovering"),
                )

                with _state_lock:
                    _stats["ws_auth_errors"] += 1

                _tv_auth.set_current_token(_tv_auth.GUEST_TOKEN)

                threading.Thread(
                    target=_tv_auth.renew, args=(logger,), daemon=True, name="ws-auth-renew"
                ).start()

                self._done.set()

                try:
                    ws.close()

                except Exception:
                    pass

                return

            if msg_type in ("du", "timescale_update") and len(p) >= 2:
                self._handle_series(p[0], p[1], ws, token)

    def _handle_series(self, cs: str, series_data: dict, ws, token: int) -> None:
        if not self._is_current_ws(ws) or not self._is_current_fetch(token):
            return

        if cs not in self._cs_map:
            return

        symbol_id, tf_code, staging_table, tv_symbol = self._cs_map[cs]

        sds = series_data.get("sds_1")

        if sds is None:
            return

        bars = [b for b in sds.get("s", []) if len(b.get("v", [])) >= 6]

        with self._lock:
            self._received.add(cs)

        _new_count = 0

        if bars:
            bars.sort(key=lambda b: b["v"][0])

            closed_bars = bars[:-1]

            if closed_bars:
                key = (symbol_id, tf_code)

                _set_source_watermark(key, closed_bars[-1]["v"][0])

                with _state_lock:
                    last_ts = _last_bar_ts.get(key, 0.0)

                with _backlog_lock:
                    miss_count = _backlog.get(key, 0)

                if miss_count > 0:
                    from core_engine.settings import TF_MINUTES as _TF_MIN

                    tf_min = _TF_MIN.get(tf_code, 5)

                    effective_wm = max(0.0, last_ts - miss_count * tf_min * 60 * 2)

                    logger.debug(
                        "%s",
                        _llog(
                            "Backlog catch-up watermark adjusted",
                            group=self.group_id,
                            symbol=tv_symbol,
                            timeframe=tf_code,
                            minutes=miss_count * tf_min * 2,
                            result="adjusted",
                        ),
                    )

                else:
                    effective_wm = last_ts

                new_bars = [b for b in closed_bars if b["v"][0] > effective_wm]

                if new_bars:
                    df = _bars_to_df(new_bars)

                    if not df.empty:
                        future_cutoff = _future_cutoff_ts()

                        safe_new_bars = [b for b in new_bars if b["v"][0] <= future_cutoff]

                        if not safe_new_bars:
                            logger.warning(
                                "%s",
                                _llog(
                                    "Future-only candle batch ignored",
                                    group=self.group_id,
                                    symbol=tv_symbol,
                                    timeframe=tf_code,
                                    result="ignored",
                                ),
                            )

                            return

                        if len(safe_new_bars) != len(new_bars):
                            logger.warning(
                                "%s",
                                _llog(
                                    "Future candles dropped before enqueue",
                                    group=self.group_id,
                                    symbol=tv_symbol,
                                    timeframe=tf_code,
                                    dropped=len(new_bars) - len(safe_new_bars),
                                    result="cleaned",
                                ),
                            )

                            df = _bars_to_df(safe_new_bars)

                            if df.empty:
                                return

                        item = (self._batch_id, symbol_id, tf_code, staging_table, tv_symbol, df)

                        enqueue_status = _enqueue_or_buffer(item, self.group_id, tv_symbol, tf_code)

                        if enqueue_status == "rejected":
                            with _overflow_lock:
                                overflow_depth = len(_overflow_buf)

                            spool_depth = _spool.count()

                            logger.error(
                                "%s",
                                _llog(
                                    "Write queue rejected candles",
                                    group=self.group_id,
                                    symbol=tv_symbol,
                                    timeframe=tf_code,
                                    queue_depth=_db_queue.qsize(),
                                    memory_buffer=overflow_depth,
                                    disk_buffer="n/a" if spool_depth is None else spool_depth,
                                    result="failed",
                                ),
                            )

                            return

                        _set_received_watermark(key, safe_new_bars[-1]["v"][0])

                        with self._lock:
                            self._new_bars_count += len(safe_new_bars)

                            self._pair_new_bars[(symbol_id, tf_code)] = self._pair_new_bars.get(
                                (symbol_id, tf_code), 0
                            ) + len(safe_new_bars)

                        _new_count = len(safe_new_bars)

                        _record_batch_accepted(self._batch_id, key, len(safe_new_bars))

                        with _state_lock:
                            _stats["queue_depth"] = _db_queue.qsize()

                        _log_candle_row(
                            live_tv_line(
                                logged_at=datetime.now(timezone.utc).strftime("%H:%M:%S"),
                                symbol=tv_symbol,
                                timeframe=tf_code,
                                candles=len(safe_new_bars),
                                first_utc=_fmt_bar_time_utc(safe_new_bars[0]["v"][0]) if len(safe_new_bars) > 1 else None,
                                latest_utc=_fmt_bar_time_utc(safe_new_bars[-1]["v"][0]),
                                queue_depth=_db_queue.qsize(),
                            )
                        )

        if _new_count <= 0:
            logger.debug(
                "%s",
                _llog(
                    "No new closed candle",
                    group=self.group_id,
                    symbol=tv_symbol,
                    timeframe=tf_code,
                    received=len(bars),
                    result="no_change",
                ),
            )

        with self._lock:
            if (
                not self._registering

                and self._expected

                and self._received >= self._expected

                and not self._done.is_set()
            ):
                logger.debug(
                    "%s",
                    _llog(
                        "Connection group completed",
                        group=self.group_id,
                        sessions=len(self._expected),
                        result="closing",
                    ),
                )

                self._done.set()

                try:
                    ws.close()

                except Exception:
                    pass

    def _on_error(self, _ws, error, token: int | None = None) -> None:
        if token is None:
            with self._lock:
                token = self._fetch_token

        if not self._is_current_fetch(token):
            return

        _handle_ws_transport_error(self.group_id, error)

        self._done.set()

    def _on_close(self, _ws, status_code, _msg, token: int | None = None) -> None:
        if token is None:
            with self._lock:
                token = self._fetch_token

        if not self._is_current_fetch(token):
            return

        logger.debug("%s", _llog("Connection group disconnected", group=self.group_id, code=status_code or "-", result="closed"))

        self._done.set()

    def fetch(self, batch_id: int, timeout: int = BATCH_FETCH_TIMEOUT) -> bool:
        token = self._next_fetch_token()

        self._done.clear()

        with self._lock:
            self._new_bars_count = 0

            self._pair_new_bars.clear()

            self._cs_map.clear()

            self._expected.clear()

            self._received.clear()

            self._registering = False

            self._batch_id = batch_id

            self._report_title = ""

            self._report_lines = []

            self._report_level = logging.INFO

        ts = datetime.now().strftime("%Y_%m_%d-%H_%M_%S")

        url = f"{TV_BASE_URL}?from=chart%2F&date={ts}"

        ws = websocket.WebSocketApp(
            url,
            header=self._build_headers(),
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        self._ws = ws

        ws_thread = threading.Thread(
            target=ws.run_forever, daemon=True, name=f"ws-g{self.group_id}"
        )

        ws_thread.start()

        completed = self._done.wait(timeout=timeout)

        if not completed:
            with self._lock:
                self._timed_out = True

                expected_snapshot = set(self._expected)

                received_snapshot = set(self._received)

                cs_map_timeout_snapshot = dict(self._cs_map)

            self._done.set()

            try:
                ws.keep_running = False

            except Exception:
                pass

            # keep_running=False is only checked between reads by
            # websocket-client's own loop - it does not unblock a thread
            # that is already stuck inside a blocking socket recv() (e.g.
            # TradingView stopped sending data without closing the TCP
            # connection). Without forcing the underlying socket closed
            # too, that ws_thread can linger indefinitely; over many days
            # of repeated timeouts, these accumulate as leaked threads and
            # open file descriptors even though _is_current_fetch()
            # already prevents a stale thread's callbacks from corrupting
            # newer batches' data.
            try:
                sock = getattr(ws, "sock", None)
                raw_sock = getattr(sock, "sock", None) if sock is not None else None
                if raw_sock is not None:
                    raw_sock.close()
                    with _state_lock:
                        _stats["ws_forced_socket_closes"] = _stats.get("ws_forced_socket_closes", 0) + 1
            except Exception:
                pass

            missing = [
                f"{cs_map_timeout_snapshot[cs][3]} {cs_map_timeout_snapshot[cs][1]}"

                for cs in (expected_snapshot - received_snapshot)

                if cs in cs_map_timeout_snapshot
            ]

            logger.warning(
                "%s",
                _llog(
                    "Connection group fetch timeout",
                    group=self.group_id,
                    timeout_seconds=timeout,
                    sessions=f"{len(received_snapshot)}/{len(expected_snapshot)}",
                    missing=", ".join(missing[:8]) if missing else "none",
                    result="warning",
                ),
            )

            if not expected_snapshot:
                _send_alert(
                    "ERROR",
                    "Live feed could not open TradingView chart sessions\n"
                    f"Connection group: {self.group_id}\n"
                    f"Waited: {timeout}s\n"
                    "Meaning: TradingView login, network, or chart-session setup failed before data could be requested.",
                )

            else:
                _send_alert(
                    "WARNING",
                    "Live feed batch timed out\n"
                    f"Connection group: {self.group_id}\n"
                    f"Answered sessions: {len(received_snapshot)}/{len(expected_snapshot)}\n"
                    + (f"Missing pairs: {', '.join(missing)}" if missing else "Missing pairs: none"),
                )

            try:
                ws.close()

            except Exception:
                pass

        ws_thread.join(timeout=WS_THREAD_JOIN_GRACE_SEC)

        if ws_thread.is_alive():
            log_method = logger.info if _shutdown.is_set() else logger.error
            log_method(
                "%s",
                _llog(
                    "WebSocket thread did not stop after timeout",
                    group=self.group_id,
                    action="stale_callback_guard",
                    result="warning" if _shutdown.is_set() else "failed",
                ),
            )

            if not _shutdown.is_set():
                # This specific OS thread is now genuinely unreclaimable
                # from Python (it ignored keep_running=False AND a forced
                # raw-socket close). _run_batch's consecutive-stuck-batch
                # counter is what decides whether to recycle the whole
                # process; this counter is the operator-facing metric for
                # "how often has that actually happened" (see doctor/
                # status output and _stats).
                with _state_lock:
                    _stats["ws_orphaned_threads"] = _stats.get("ws_orphaned_threads", 0) + 1

                self._requires_process_recycle = True

        self._ws = None

        with self._lock:
            cs_map_snapshot = dict(self._cs_map)

            received_final = set(self._received)

            expected_final = set(self._expected)

        received_pairs: set[tuple[int, str]] = {
            cs_map_snapshot[cs][:2]

            for cs in received_final

            if cs in cs_map_snapshot
        }

        missed_pairs: set[tuple[int, str]] = {
            cs_map_snapshot[cs][:2]

            for cs in (expected_final - received_final)

            if cs in cs_map_snapshot
        }

        _update_missed_pairs(received_pairs, missed_pairs)

        _sym_name = {s["symbol_id"]: s["tv_symbol"] for s in WS_SYMBOLS}

        with _backlog_lock:
            for pair in received_pairs:
                if pair in _backlog:
                    logger.info(
                        "%s",
                        _llog(
                            "Pair recovered from retry list",
                            symbol=_sym_name.get(pair[0], str(pair[0])),
                            timeframe=pair[1],
                            result="recovered",
                        ),
                    )

                _backlog.pop(pair, None)

            for pair in missed_pairs:
                count = _backlog.get(pair, 0) + 1

                if count <= MAX_BACKLOG_BATCHES:
                    _backlog[pair] = count

                    logger.info(
                        "%s",
                        _llog(
                            "Pair missed this batch",
                            symbol=_sym_name.get(pair[0], str(pair[0])),
                            timeframe=pair[1],
                            consecutive_misses=count,
                            next_request_bars=N_BARS_WS_BACKLOG,
                            result="retry_next_batch",
                        ),
                    )

                    logger.info(
                        "%s",
                        _llog(
                            "Pair miss audit",
                            symbol=_sym_name.get(pair[0], str(pair[0])),
                            timeframe=pair[1],
                            consecutive_misses=count,
                            result="tracked",
                        ),
                    )

                else:
                    logger.error(
                        "%s",
                        _llog(
                            "Pair missed too many batches",
                            symbol=_sym_name.get(pair[0], str(pair[0])),
                            timeframe=pair[1],
                            consecutive_misses=count,
                            result="gap_requires_backfill",
                        ),
                    )

                    _send_alert(
                        "ERROR",
                        "Live feed stopped retrying one missing pair\n"
                        f"Pair: {_sym_name.get(pair[0], str(pair[0]))}/{pair[1]}\n"
                        f"Missed batches: {count} in a row (about {count * 5} minutes)\n"
                        "Meaning: this pair likely has a real data gap now.\n"
                        "Suggested action: run historical backfill/replay to repair the gap." + QUICK_COMMANDS_HINT,
                    )

                    _backlog.pop(pair, None)

        with _backlog_lock:
            backlog_snap = dict(_backlog)

        pair_new_bars_snap: dict[tuple[int, str], int]

        with self._lock:
            pair_new_bars_snap = dict(self._pair_new_bars)

        with self._lock:
            expected_count = len(self._expected)

            received_count = len(self._received)

        changed_pairs = [key for key, count in pair_new_bars_snap.items() if int(count or 0) > 0]

        changed_pairs.sort(key=lambda key: (-pair_new_bars_snap[key], _fmt_pair_label(key)))

        changed_text = []

        for key in changed_pairs[:12]:
            with _state_lock:
                wm_ts = _last_bar_ts.get(key)

            latest = (
                datetime.fromtimestamp(wm_ts, tz=timezone.utc).strftime("%H:%M UTC")

                if wm_ts

                else "-"
            )

            changed_text.append(f"{_fmt_pair_label(key)} +{pair_new_bars_snap[key]} ({latest})")

        missed_sorted = sorted(missed_pairs, key=_fmt_pair_label)

        missed_text = ", ".join(_fmt_pair_label(key) for key in missed_sorted[:12]) or "none"

        if len(missed_sorted) > 12:
            missed_text += f", ... +{len(missed_sorted) - 12} more"

        if expected_count == 0:
            analysis = (
                "No TradingView chart sessions were established; this is a connection/auth "

                "failure, not a normal no-bar cycle."
            )

        elif missed_pairs:
            analysis = (
                f"{len(missed_pairs)} pair(s) did not answer; next batch requests "

                f"{N_BARS_WS_BACKLOG} bars for backlog recovery."
            )

        elif self._new_bars_count == 0:
            analysis = (
                "OK  no new closed bar - all sessions answered; no new closed bars were available."
            )

        else:
            analysis = "Group is healthy; accepted bars were queued for database writes."

        report_lines = [
            f"Sessions : {received_count}/{expected_count} answered",
            f"Accepted : {self._new_bars_count:,} bars across {len(changed_pairs)} pair(s)",
            f"Symbols  : {_summarize_counts_by_symbol(pair_new_bars_snap)}",
            f"TFs      : {_summarize_counts_by_tf(pair_new_bars_snap)}",
            f"Changed  : {'; '.join(changed_text) if changed_text else '-'}",
            f"Missing  : {missed_text}",
        ]

        if backlog_snap:
            report_lines.append(f"Backlog  : {_summarize_backlog(backlog_snap)}")

        report_lines.append(f"Analysis : {analysis}")

        with self._lock:
            self._report_title = f"WS LIVE GROUP G{self.group_id} REPORT"
            self._report_lines = list(report_lines)
            self._report_level = logging.ERROR if expected_count == 0 else logging.WARNING if missed_pairs else logging.INFO

        logger.debug(
            "%s",
            _llog(
                "Connection group audit",
                group=self.group_id,
                sessions_answered=f"{received_count}/{expected_count}",
                closed_candles_received=self._new_bars_count,
                missed_pairs=len(missed_pairs),
                checked_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
                result="recorded",
            ),
        )

        return completed and expected_count > 0 and received_count >= expected_count

def _update_guest_mode_counter(is_guest: bool) -> None:

    if is_guest:
        _state._consecutive_guest_batches += 1

        if _state._consecutive_guest_batches >= _GUEST_ALERT_THRESHOLD:
            logger.error(
                "%s",
                _llog(
                    "TradingView limited login repeated",
                    consecutive_batches=_state._consecutive_guest_batches,
                    risk="data_depth_may_be_limited",
                    result="warning",
                ),
            )

            _send_alert(
                "WARNING",
                "TradingView session is limited\n"
                f"Consecutive live batches: {_state._consecutive_guest_batches}\n"
                "Meaning: data depth may be limited or some premium symbols may not return enough candles.\n"
                "Suggested action: refresh TradingView login if this repeats during market hours.",
            )

    else:
        if _state._consecutive_guest_batches >= _GUEST_ALERT_THRESHOLD:
            logger.info(
                "%s",
                _llog("TradingView login recovered", previous_limited_batches=_state._consecutive_guest_batches, result="healthy"),
            )

        _state._consecutive_guest_batches = 0

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
        _state.spool_full_pause = True
        logger.critical(
            "%s",
            _llog("Live batch paused: durable outbox is not readable", result="paused"),
        )
        return True

    reserve_rows = min(MAX_SPOOL_ROWS, len(WS_SYMBOLS) * len(WS_TF_CODES))
    has_capacity = pending + reserve_rows <= MAX_SPOOL_ROWS
    if has_capacity:
        if _state.spool_full_pause:
            _state.spool_full_pause = False
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

    _state.spool_full_pause = True
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
                _state._tv_connectivity_block_until, tz=timezone.utc
            ).isoformat()

            if _state._tv_connectivity_block_until

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

    _sym_name = {s["symbol_id"]: s["tv_symbol"] for s in WS_SYMBOLS}

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

    deferred_items = int(db_metrics.get("deferred_items", 0))

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

def _seconds_until_next_boundary(interval_minutes: int) -> float:
    now = datetime.now()

    elapsed = (now.minute % interval_minutes) * 60 + now.second + now.microsecond / 1_000_000

    wait = interval_minutes * 60 - elapsed

    return wait if wait > 5 else interval_minutes * 60

def _batch_loop(groups: list[BatchFetcher]) -> None:
    if not _shutdown.is_set():
        _tv_auth.check_and_refresh(logger)

        _tv_auth.ensure_cookie_fresh(logger)

        _refresh_watermarks_from_fact("pre-batch")

        _run_batch(groups)

    while not _shutdown.is_set():
        wait = _seconds_until_next_boundary(BATCH_INTERVAL_MIN)

        _log_report_text(
            logging.INFO,
            live_next_batch_block(
                wait_seconds=round(wait),
                interval_minutes=BATCH_INTERVAL_MIN,
            ),
        )

        connectivity_cooldown = (
            bool(_state._tv_connectivity_block_until)

            and time.time() < float(_state._tv_connectivity_block_until)
        )

        blocked_until = (
            datetime.fromtimestamp(_state._tv_connectivity_block_until, tz=timezone.utc).isoformat()

            if connectivity_cooldown

            else None
        )

        _write_live_state(
            status="network_blocked" if connectivity_cooldown else "waiting",
            next_batch_in_sec=round(wait, 3),
            next_batch_after=_utc_iso(),
            blocked_until=blocked_until,
            network_error=_state._tv_connectivity_last_error if connectivity_cooldown else None,
            batch_started_at=None,
            batch_group_timeout_sec=None,
        )

        _shutdown.wait(wait)

        if _shutdown.is_set():
            break

        _tv_auth.check_and_refresh(logger)

        _tv_auth.ensure_cookie_fresh(logger)

        _refresh_watermarks_from_fact("pre-batch")

        _run_batch(groups)

def _status_reporter() -> None:
    while not _shutdown.wait(STATUS_INTERVAL_SEC):
        _refresh_watermarks_from_fact("status")

        with _state_lock:
            s = dict(_stats)

        with _overflow_lock:
            overflow = len(_overflow_buf)

        with _missed_lock:
            n_miss_active = sum(1 for v in _missed_pairs.values() if v > 0)

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

        spool_count = _spool.count() or 0

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
            auth_info = f"Guest ({_state._consecutive_guest_batches} batches in a row)"

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
            recent_ws_errors=recent_ws_errors,
            total_ws_errors=total_ws_errors,
            n_miss_active=n_miss_active,
            is_guest=is_guest,
            consecutive_guest_batches=_state._consecutive_guest_batches,
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
                rows_saved=s.get("fact_inserted", s["bars_inserted"]),
                recent_errors=recent_errors,
                total_errors=total_errors,
                recent_websocket_errors=recent_ws_errors,
                total_websocket_errors=total_ws_errors,
                batches=s["batches_run"],
                write_queue=s["queue_depth"],
                memory_buffer=overflow,
                disk_buffer=spool_count,
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

            f"{s.get('fact_inserted', s['bars_inserted']):,} saved rows | "

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
            },
            data_result={
                "last_hour": hourly_summary,
                "total_closed_candles_received": f"{s.get('accepted_bars', 0):,}",
                "total_rows_saved": f"{s.get('fact_inserted', s['bars_inserted']):,}",
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
            trace={"log_file": str(WS_LIVE_LOG), "state_file": str(WS_LIVE_STATE)},
            result="healthy" if notify_level == "SUCCESS" else "warning" if notify_level == "WARNING" else "failed",
        )

def main(smoke_seconds: int | None = None, *, conflict_policy: str | None = None) -> int:
    import sys as _sys

    if hasattr(_sys.stdout, "reconfigure"):
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if STORAGE.mode == "redis":
        # Redis-only is not a durable warehouse implementation: this engine
        # always commits SQL first and only then publishes optional Redis
        # candle snapshots. ``both`` is valid (and is the backward-compatible
        # result of CANDLE_SNAPSHOT_ENABLED=1); SQL remains authoritative.
        # Refuse only ``redis`` so an operator cannot mistakenly take SQL
        # offline while believing the setting changed the durable write path.
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
            f"ERROR: DP_STORAGE_MODE={STORAGE.mode!r} is not supported by the live engine "
            "because SQL is the durable system of record. Set DP_STORAGE_MODE=sql or both."
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
                trace={"lock": "ws_live_runtime", "log_file": str(WS_LIVE_LOG)},
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
                datetime.fromtimestamp(_state._tv_connectivity_block_until, tz=timezone.utc).isoformat()

                if _state._tv_connectivity_block_until

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
        print("  ERROR: Auth preflight failed. See live_fetching.log for details.")

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
            trace={"log_file": str(WS_LIVE_LOG)},
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
                else "Check runtime/logs/operation/live_fetching.log for the auxiliary task failure."
            ),
            trace={"log_file": str(WS_LIVE_LOG), "traceback_tail": tb[-800:]},
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
        recommended_action="No action needed now. Watch the first health report and live_fetching.log.",
        trace={"log_file": str(WS_LIVE_LOG), "state_file": str(WS_LIVE_STATE)},
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
            recommended_action="Check runtime/logs/operation/live_fetching.log and runtime/logs/system/system.log, then confirm whether DP Program restarted live fetching.",
            trace={"log_file": str(WS_LIVE_LOG), "traceback_tail": tb[-800:]},
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
            rows_saved=s.get("fact_inserted", s["bars_inserted"]),
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
        fact_inserted=s.get("fact_inserted", s["bars_inserted"]),
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
            "rows_saved_to_main_table": s.get("fact_inserted", s["bars_inserted"]),
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
        trace={"log_file": str(WS_LIVE_LOG), "state_file": str(WS_LIVE_STATE)},
        result="failed" if exit_code else "stopped",
    )

    ws_lock_stop.set()

    try:
        atexit.unregister(_release_task_lock)
    except Exception:
        pass
    _release_task_lock("ws_live_runtime")

    print(f"\n  Accepted bars : {s.get('accepted_bars', 0):,}")

    print(f"  Fact inserted : {s.get('fact_inserted', s['bars_inserted']):,}")

    print(f"  Staging rows  : {s.get('staging_rows', 0):,}")

    print(f"  Errors        : {s['errors']}")

    print(f"  WS events     : {s['events']:,}")

    print(f"  Batches run   : {s['batches_run']}")
    return exit_code

if __name__ == "__main__":
    raise SystemExit(main())



