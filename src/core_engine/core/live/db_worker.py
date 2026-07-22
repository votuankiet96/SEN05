"""Durable live-candle dispatch, staging writer and Fact loader workers."""

from __future__ import annotations

import queue
import time
from datetime import datetime, timezone

from core_engine.core.live import state as _state
from core_engine.core.live.batch_metrics import record_db_result as _record_db_result
from core_engine.core.live.logging_support import (
    log_candle_row as _log_candle_row,
    logger,
    operation_line as _llog,
)
from core_engine.core.live.reporter import live_db_line
from core_engine.core.live.runtime_support import as_utc_timestamp as _as_utc_timestamp
from core_engine.core.live.state import (
    _DEFERRED_ETL_MAX,
    _DEFERRED_ETL_WARN,
    _WRITE_DEFER_LOCK_TTL,
    _WRITE_DEFER_LOCKS,
    _db_queue,
    _db_worker_done,
    _deferred_etl,
    _deferred_etl_next_attempt,
    _deferred_lock,
    _etl_item_meta,
    _etl_wakeup,
    _increment_data_error_counter,
    _last_bar_ts,
    _overflow_buf,
    _overflow_lock,
    _shutdown,
    _spool,
    _spool_pending,
    _state_lock,
    _stats,
    _write_defer_lock_cache,
)
from core_engine.settings import LIVE, SYMBOLS
from core_engine.shared.tradingview.history_client import get_ws_interval_map
from core_engine.shared.warehouse.validation import validate_ohlcv_df
from core_engine.shared.warehouse.writer import insert_staging_batch, run_etl_direct
from core_engine.util.coordination.locks import is_locked
from core_engine.util.notify.discord import QUICK_COMMANDS_HINT, send_alert as _send_alert
from core_engine.util.redis_io.candle_snapshot import publish_candle_snapshot


OVERFLOW_BUFFER_MAX = LIVE.overflow_buffer_max
ETL_DIRECT_RETRIES = LIVE.etl_direct_retries
ETL_DIRECT_RETRY_DELAY_SEC = LIVE.etl_direct_retry_delay_sec
ETL_DEFERRED_RETRY_COOLDOWN_SEC = LIVE.etl_deferred_retry_cooldown_sec
MAX_SPOOL_ROWS = LIVE.max_spool_rows
WS_SYMBOLS = [symbol for symbol in SYMBOLS if symbol["asset_type"] in set(LIVE.asset_types)]
WS_TF_CODES = tuple(get_ws_interval_map())


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


def _set_committed_watermark(key: tuple[int, str], max_ts: float) -> None:
    with _state_lock:
        _last_bar_ts[key] = max(max_ts, _last_bar_ts.get(key, 0.0))

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


def _fact_backlog_size() -> int:
    """Return symbol/timeframe keys currently waiting for Fact delivery."""
    with _deferred_lock:
        return len(_deferred_etl)


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
            _record_db_result(batch_id, key, accepted_count, 0, 0)
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
