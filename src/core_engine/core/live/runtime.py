"""Runtime context, live health policy, and process-owned state.

Every lock, queue, counter, and long-lived helper object the live engine's
worker threads coordinate through lives here: `core_engine.core.live.engine`
(the WebSocket client, ETL writer, batch orchestrator, status reporter,
and startup/shutdown sequence) imports from this module rather than the
other way around, so this module has no dependency on `engine`.

Most of what's here is a mutable *container* (dict, list, Queue, Lock,
Event, or a stateful object like LiveOutbox) - those are safe to import by
name (`from core_engine.core.live.runtime import _stats`) because callers mutate
what the name points to, they never reassign the name itself.

A handful of plain scalars ARE reassigned at runtime
(`_candle_table_rows`, `_ws_cooldown_until`, `_ws_cooldown_reason`,
`_consecutive_guest_batches`, `_tv_connectivity_block_until`,
`_tv_connectivity_last_error`). Python's `from module import name` binds a
snapshot for those, not a live reference, so callers outside this module
must go through the module object (`from core_engine.core.live import runtime`
then `runtime._candle_table_rows = ...`) to see/make updates correctly.
"""

from __future__ import annotations

import logging
import os
import queue
import socket
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from core_engine.core.live.outbox import LiveOutbox
from core_engine.settings import (
    LIVE,
    OVERNIGHT_GAP_MINUTES,
    TF_MINUTES,
    WS_LIVE_PID,
    WS_LIVE_STATE,
    WS_OVERFLOW_SPOOL,
)
from core_engine.shared.freshness import stale_after_minutes
from core_engine.shared.tradingview.diagnostics import ConnectivityProbe, playwright_browser_status
from core_engine.util.coordination.locks import LocalProcessLock, format_payload, utc_stamp
from core_engine.util.runtime_state import RuntimeStateWriter

# Bare reference to the same logger name core_engine.core.live.engine configures
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

# The staging writer and Fact loader are deliberately separate workers.  The
# metadata map lets the Fact worker report the original batch only after the
# exact staged outbox row has crossed the Fact commit boundary.  The durable
# source of truth remains LiveOutbox; this map is only in-process bookkeeping
# and may safely disappear on a crash because LiveOutbox.init() requeues staged
# rows on the replacement process.
_etl_item_meta: dict[int, dict] = {}

_etl_wakeup = threading.Event()

_db_worker_done = threading.Event()

_write_defer_lock_cache: dict = {"locked": False, "checked_at": 0.0, "name": ""}

_WRITE_DEFER_LOCK_TTL = 30.0

_DEFERRED_ETL_WARN = 2000

_DEFERRED_ETL_MAX = 5000

_WRITE_DEFER_LOCKS = ("warehouse_maintenance",)

_last_bar_ts: dict[tuple[int, str], float] = {}

_source_bar_ts: dict[tuple[int, str], float] = {}

_stats = {
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

_live_state_writer = RuntimeStateWriter(WS_LIVE_STATE, logger)

_write_live_state = _live_state_writer.write

_runtime_lock = LocalProcessLock(
    WS_LIVE_PID,
    logger,
    command_markers=("live_fetching", "core_engine run --live"),
)

_tv_connectivity_probe = ConnectivityProbe(
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
# module must go through `core_engine.core.live.runtime` (the module object),
# not `from core_engine.core.live.runtime import spool_full_pause`.
spool_full_pause = False

_shutdown = threading.Event()

_overflow_buf = []

_overflow_lock = threading.Lock()

_db_queue: queue.Queue = queue.Queue(maxsize=LIVE.db_queue_maxsize)

_spool = LiveOutbox(WS_OVERFLOW_SPOOL, max_rows=LIVE.max_spool_rows, logger=logger)

_spool_pending = threading.Event()

_consecutive_guest_batches = 0

_GUEST_ALERT_THRESHOLD = 3

_ws_cooldown_lock = threading.Lock()

_ws_cooldown_until = 0.0

_ws_cooldown_reason = ""

_backlog: dict[tuple[int, str], int] = {}

_backlog_lock = threading.Lock()

# Pairs that exhausted live retry and now require historical repair remain
# visible to health reporting until a later live response proves recovery.
_requires_backfill: set[tuple[int, str]] = set()


@dataclass(frozen=True)
class MissingPairUpdate:
    recovered: tuple[tuple[int, str], ...]
    pending: tuple[tuple[tuple[int, str], int], ...]
    repeated_alerts: tuple[tuple[tuple[int, str], int], ...]
    requires_backfill: tuple[tuple[tuple[int, str], int], ...]
    backlog: dict[tuple[int, str], int]


def update_missing_pairs(
    received: set[tuple[int, str]],
    missed: set[tuple[int, str]],
    *,
    alert_every: int,
    max_live_retries: int,
) -> MissingPairUpdate:
    """Advance the single live missing-pair state machine atomically."""

    recovered: list[tuple[int, str]] = []
    pending: list[tuple[tuple[int, str], int]] = []
    repeated_alerts: list[tuple[tuple[int, str], int]] = []
    requires_backfill: list[tuple[tuple[int, str], int]] = []
    with _backlog_lock:
        for pair in received:
            if pair in _backlog or pair in _requires_backfill:
                recovered.append(pair)
            _backlog.pop(pair, None)
            _requires_backfill.discard(pair)

        for pair in missed:
            count = _backlog.get(pair, 0) + 1
            if count <= max_live_retries:
                _backlog[pair] = count
                pending.append((pair, count))
                if alert_every > 0 and count % alert_every == 0:
                    repeated_alerts.append((pair, count))
            else:
                _backlog.pop(pair, None)
                _requires_backfill.add(pair)
                requires_backfill.append((pair, count))

        snapshot = dict(_backlog)

    return MissingPairUpdate(
        recovered=tuple(recovered),
        pending=tuple(pending),
        repeated_alerts=tuple(repeated_alerts),
        requires_backfill=tuple(requires_backfill),
        backlog=snapshot,
    )

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


def _empty_batch_metrics() -> dict:
    return {
        "accepted": 0,
        "db_processed": 0,
        "staging_rows": 0,
        "fact_inserted": 0,
    }


def init_batch_metrics(batch_id: int) -> None:
    with _batch_metrics_lock:
        _batch_metrics[batch_id] = _empty_batch_metrics()
        if len(_batch_metrics) > MAX_BATCH_METRIC_HISTORY:
            for old_batch_id in sorted(_batch_metrics)[:-MAX_BATCH_METRIC_HISTORY]:
                if old_batch_id != 0:
                    _batch_metrics.pop(old_batch_id, None)


def record_accepted(batch_id: int, key: tuple[int, str], count: int) -> None:
    del key
    if count <= 0:
        return
    with _batch_metrics_lock:
        metrics = _batch_metrics.setdefault(batch_id, _empty_batch_metrics())
        metrics["accepted"] += count
    with _state_lock:
        _stats["accepted_bars"] += count


def record_db_result(
    batch_id: int,
    key: tuple[int, str],
    accepted_count: int,
    staging_rows: int,
    fact_inserted: int,
) -> None:
    staging_rows = max(0, int(staging_rows or 0))
    fact_inserted = max(0, int(fact_inserted or 0))
    with _batch_metrics_lock:
        metrics = _batch_metrics.setdefault(batch_id, _empty_batch_metrics())
        metrics["db_processed"] += max(0, accepted_count)
        metrics["staging_rows"] += staging_rows
        metrics["fact_inserted"] += fact_inserted

    if fact_inserted:
        with _hourly_lock:
            _hourly_stats["fact_bars"] += fact_inserted
            _hourly_stats["pair_bars"][key] = (
                _hourly_stats["pair_bars"].get(key, 0) + fact_inserted
            )

    if staging_rows or fact_inserted:
        with _state_lock:
            _stats["staging_rows"] += staging_rows
            _stats["fact_inserted"] += fact_inserted
        if staging_rows:
            with _hourly_lock:
                _hourly_stats["staging_rows"] += staging_rows
                _hourly_stats["pair_staging"][key] = (
                    _hourly_stats["pair_staging"].get(key, 0) + staging_rows
                )


def snapshot_batch_metrics(batch_id: int) -> dict:
    with _batch_metrics_lock:
        return dict(_batch_metrics.get(batch_id, {}))


def wait_for_batch_db(
    batch_id: int,
    timeout_sec: float = BATCH_DB_REPORT_WAIT_SEC,
) -> dict:
    deadline = time.monotonic() + timeout_sec
    while True:
        metrics = snapshot_batch_metrics(batch_id)
        accepted = int(metrics.get("accepted", 0))
        processed = int(metrics.get("db_processed", 0))
        if accepted == 0 or processed >= accepted or time.monotonic() >= deadline:
            return metrics
        _shutdown.wait(0.25)
        if _shutdown.is_set():
            return snapshot_batch_metrics(batch_id)


def runtime_payload(*, started_at: str | None = None) -> str:
    """Build the live runtime lock payload consumed by lock diagnostics."""

    now = utc_stamp()
    return format_payload(
        {
            "kind": "live_runtime",
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "started": started_at or now,
            "heartbeat": now,
        }
    )


def is_market_expected_live(
    symbol_meta_by_id: Mapping[int, Mapping[str, object]],
    symbol_id: int,
    now_utc: datetime,
) -> bool:
    meta = symbol_meta_by_id.get(symbol_id, {})
    if meta.get("asset_type") == "Crypto":
        return True

    weekday = now_utc.weekday()
    if weekday == 5:
        return False
    if weekday == 6 and now_utc.hour < 22:
        return False
    if weekday == 4 and now_utc.hour >= 22:
        return False
    return True


def freshness_threshold_minutes(
    symbol_meta_by_id: Mapping[int, Mapping[str, object]],
    symbol_overnight_mins: Mapping[str, int],
    symbol_id: int,
    tf_code: str,
) -> int:
    tf_min = int(TF_MINUTES.get(tf_code, 60))
    meta = symbol_meta_by_id.get(symbol_id, {})
    asset_type = meta.get("asset_type")
    tv_symbol = meta.get("tv_symbol")

    if asset_type == "Crypto":
        overnight_min = 0
    elif tv_symbol in symbol_overnight_mins:
        overnight_min = int(symbol_overnight_mins.get(str(tv_symbol), 0))
    else:
        overnight_min = int(OVERNIGHT_GAP_MINUTES.get(str(asset_type), 0))
    return stale_after_minutes(tf_min, overnight_min)


def freshness_alert_threshold_minutes(
    symbol_meta_by_id: Mapping[int, Mapping[str, object]],
    symbol_overnight_mins: Mapping[str, int],
    symbol_id: int,
    tf_code: str,
) -> int:
    """Return the age that is serious enough to affect operator alerts."""

    base = freshness_threshold_minutes(
        symbol_meta_by_id,
        symbol_overnight_mins,
        symbol_id,
        tf_code,
    )
    meta = symbol_meta_by_id.get(symbol_id, {})
    asset_type = str(meta.get("asset_type") or "")
    tf_code = str(tf_code).upper()

    if asset_type == "Crypto":
        minimums = {
            "M5": 30,
            "M10": 45,
            "M15": 60,
            "M20": 90,
            "M30": 120,
            "M45": 180,
            "H1": 240,
            "M90": 300,
            "H2": 360,
            "H3": 480,
            "H4": 720,
            "H6": 900,
            "H8": 1080,
            "D1": 2160,
            "W": 12960,
        }
    elif tf_code == "W":
        minimums = {"W": 12960}
    elif tf_code == "D1":
        minimums = {"D1": 7200}
    elif asset_type == "Indice":
        minimums = {
            "M5": 720,
            "M10": 720,
            "M15": 720,
            "M20": 720,
            "M30": 720,
            "M45": 720,
            "H1": 720,
            "M90": 720,
            "H2": 720,
            "H3": 960,
            "H4": 1080,
            "H6": 1440,
            "H8": 1440,
        }
    elif asset_type == "Metal":
        minimums = {
            "M5": 360,
            "M10": 360,
            "M15": 360,
            "M20": 360,
            "M30": 360,
            "M45": 360,
            "H1": 480,
            "M90": 480,
            "H2": 600,
            "H3": 720,
            "H4": 900,
            "H6": 1080,
            "H8": 1440,
        }
    else:
        minimums = {}
    return max(base, int(minimums.get(tf_code, 0)))


def run_auth_preflight(
    *,
    tv_auth,
    logger: logging.Logger,
    token_source: str,
    static_cookie: str,
    username: str,
    password: str,
    guest_policy: str,
    require_headless: bool,
    alert: Callable[[str, str], None],
) -> bool:
    """Log live auth readiness before batches start without exposing secrets."""

    cache = tv_auth.load_token_cache()
    current_token = tv_auth.get_current_token()
    ttl = tv_auth.token_expires_in(current_token)
    ttl_text = "unknown" if ttl < 0 else f"{int(ttl)}s"
    has_cookie = bool(tv_auth.get_current_cookie() or static_cookie or cache.get("TV_COOKIE"))
    has_userpass = bool(username and password)
    headless_ok, headless_detail = playwright_browser_status()

    logger.info(
        "[PREFLIGHT] auth_source=%s token=%s ttl=%s cookie=%s userpass=%s "
        "headless=%s guest_policy=%s",
        token_source,
        "guest" if current_token == tv_auth.GUEST_TOKEN else "authenticated",
        ttl_text,
        "yes" if has_cookie else "no",
        "yes" if has_userpass else "no",
        "ready" if headless_ok else "not-ready",
        guest_policy,
    )
    if not headless_ok:
        logger.warning("[PREFLIGHT] Headless TradingView renewal is not ready: %s.", headless_detail)
        if require_headless:
            alert(
                "ERROR",
                "Live feed login check failed\n"
                "Reason: Playwright Chromium is not ready for automatic TradingView refresh.\n"
                "Suggested action: run initial setup to install Chromium.",
            )
            return False

    if tv_auth.has_2fa_secret():
        try:
            __import__("pyotp")
        except ImportError:
            logger.warning("[PREFLIGHT] TRADINGVIEW.two_fa_secret is set but pyotp is not installed.")

    if current_token == tv_auth.GUEST_TOKEN and guest_policy == "abort":
        alert(
            "ERROR",
            "Live feed login check failed\n"
            "Reason: TradingView is using limited access and the policy does not allow it.\n"
            "Suggested action: refresh TradingView login, then start live feed again.",
        )
        return False
    return True
