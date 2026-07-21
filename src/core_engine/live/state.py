"""Shared mutable state for the live OHLCV fetch engine.

Every lock, queue, counter, and long-lived helper object the live engine's
worker threads coordinate through lives here: `core_engine.live.engine`
(the WebSocket client, ETL writer, batch orchestrator, status reporter,
and startup/shutdown sequence) imports from this module rather than the
other way around, so this module has no dependency on `engine`.

Most of what's here is a mutable *container* (dict, list, Queue, Lock,
Event, or a stateful object like LiveSpool) - those are safe to import by
name (`from core_engine.live.state import _stats`) because callers mutate
what the name points to, they never reassign the name itself.

A handful of plain scalars ARE reassigned at runtime
(`_candle_table_rows`, `_ws_cooldown_until`, `_ws_cooldown_reason`,
`_consecutive_guest_batches`, `_tv_connectivity_block_until`,
`_tv_connectivity_last_error`). Python's `from module import name` binds a
snapshot for those, not a live reference, so callers outside this module
must go through the module object (`from core_engine.live import state`
then `state._candle_table_rows = ...`) to see/make updates correctly.
"""

from __future__ import annotations

import logging
import queue
import threading

from core_engine.live.runtime_support import (
    LiveStateWriter,
    LocalRuntimeLock,
    TradingViewConnectivityProbe,
)
from core_engine.settings import LIVE, WS_LIVE_PID, WS_LIVE_STATE, WS_OVERFLOW_SPOOL
from core_engine.warehouse.spool import LiveSpool

# Bare reference to the same logger name core_engine.live.engine configures
# with get_logger(...). logging.getLogger(name) always returns the same
# object for a given name, so once engine.py's get_logger() call attaches
# handlers, everything logged through this reference uses them too -
# regardless of which module ran first.
logger = logging.getLogger("live_fetching")

_CANDLE_HEADER_REPEAT_ROWS = 30

_candle_table_lock = threading.Lock()

_candle_table_rows = 0

_live_table_file_lock = threading.Lock()

_state_lock = threading.Lock()

_deferred_etl: dict[tuple[int, str, str, str], float] = {}

_deferred_etl_next_attempt: dict[tuple[int, str, str, str], float] = {}

_deferred_lock = threading.Lock()

_write_defer_lock_cache: dict = {"locked": False, "checked_at": 0.0, "name": ""}

_WRITE_DEFER_LOCK_TTL = 30.0

_DEFERRED_ETL_WARN = 2000

_DEFERRED_ETL_MAX = 5000

_WRITE_DEFER_LOCKS = ("warehouse_maintenance",)

_last_bar_ts: dict[tuple[int, str], float] = {}

_received_bar_ts: dict[tuple[int, str], float] = {}

_source_bar_ts: dict[tuple[int, str], float] = {}

_stats = {
    "bars_inserted": 0,
    "accepted_bars": 0,
    "staging_rows": 0,
    "fact_inserted": 0,
    "errors": 0,
    "ws_errors": 0,
    "ws_auth_errors": 0,
    "ws_rate_limits": 0,
    "ws_server_errors": 0,
    # Count of connection-group fetches that timed out and had their
    # underlying socket forced closed to unblock a stuck thread - see
    # BatchFetcher.fetch()'s timeout branch. A high/growing count over a
    # long-running process signals accumulating leaked WS threads/sockets
    # worth investigating (network quality, TradingView-side stalls).
    "ws_forced_socket_closes": 0,
    # A forced socket close (above) did not unblock the underlying OS
    # thread within WS_LIVE_WS_THREAD_JOIN_GRACE_SEC - this specific
    # thread/fd is now permanently unreclaimable except by process exit.
    "ws_orphaned_threads": 0,
    # Times a connection group's persistent worker was still busy
    # (wedged) for WS_LIVE_GROUP_WEDGE_HARD_DEADLINE_BATCHES consecutive
    # scheduled batches and the live process recycled itself via the
    # supervisor to reclaim it - see BatchFetcher._worker_loop and
    # _run_batch's hard-deadline check (High-11).
    "ws_wedged_group_recycles": 0,
    "events": 0,
    "queue_depth": 0,
    "batches_run": 0,
}

_live_state_writer = LiveStateWriter(WS_LIVE_STATE, logger)

_write_live_state = _live_state_writer.write

_runtime_lock = LocalRuntimeLock(WS_LIVE_PID, logger)

_tv_connectivity_probe = TradingViewConnectivityProbe(
    enabled=LIVE.connectivity_preflight,
    timeout_sec=LIVE.connectivity_timeout_sec,
    cooldown_sec=LIVE.connectivity_cooldown_sec,
)


def _state_heartbeat_loop() -> None:
    _live_state_writer.heartbeat_loop(_shutdown, LIVE.state_heartbeat_sec)


def _tradingview_connectivity_ok() -> tuple[bool, str]:
    global _tv_connectivity_block_until, _tv_connectivity_last_error

    ok, detail = _tv_connectivity_probe.check()

    _tv_connectivity_block_until = _tv_connectivity_probe.block_until

    _tv_connectivity_last_error = _tv_connectivity_probe.last_error

    return ok, detail


_tv_connectivity_block_until = 0.0

_tv_connectivity_last_error = ""

# Set when the durable spool outbox itself is full (persist_pending()
# returned None) - the batch orchestrator checks this before starting a
# new batch so the system pauses fetching new candles it cannot durably
# store, instead of silently dropping them. Cleared once the backlog
# drains below capacity. Reassigned at runtime -> callers outside this
# module must go through `core_engine.live.state` (the module object),
# not `from core_engine.live.state import spool_full_pause`.
spool_full_pause = False

_shutdown = threading.Event()

_overflow_buf = []

_overflow_lock = threading.Lock()

_db_queue: queue.Queue = queue.Queue(maxsize=LIVE.db_queue_maxsize)

_spool = LiveSpool(WS_OVERFLOW_SPOOL, max_rows=LIVE.max_spool_rows, logger=logger)

_spool_pending = threading.Event()

_consecutive_guest_batches = 0

_GUEST_ALERT_THRESHOLD = 3

_ws_cooldown_lock = threading.Lock()

_ws_cooldown_until = 0.0

_ws_cooldown_reason = ""

_missed_pairs: dict[tuple[int, str], int] = {}

_missed_lock = threading.Lock()

_backlog: dict[tuple[int, str], int] = {}

_backlog_lock = threading.Lock()

_hourly_stats: dict = {
    "batches": 0,
    "accepted_bars": 0,
    "fact_bars": 0,
    "staging_rows": 0,
    "errors": 0,
    "ws_errors": 0,
    "zero_bar_batches": 0,
    "backlog_peak": 0,
    "pair_bars": {},
    "pair_accepted": {},
    "pair_staging": {},
}

_hourly_lock = threading.Lock()


def _increment_data_error_counter() -> None:
    with _state_lock:
        _stats["errors"] += 1

    with _hourly_lock:
        _hourly_stats["errors"] = int(_hourly_stats.get("errors", 0)) + 1


_batch_metrics: dict[int, dict] = {}

_batch_metrics_lock = threading.Lock()

BATCH_DB_REPORT_WAIT_SEC = 60.0

MAX_BATCH_METRIC_HISTORY = 288
