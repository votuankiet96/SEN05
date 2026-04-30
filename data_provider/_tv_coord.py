"""Shared TradingView fetch coordination for live + historical jobs.

This module keeps 24/7 `ws_live` as the highest-priority consumer while
allowing `01_data_pipeline.py` and `04_checker.py` to run safely:

- only one heavy historical job may pull TradingView at a time
- historical pulls wait for the current live batch window when possible
- historical jobs slow down slightly while ws_live is active
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from _task_lock import acquire, is_locked, release
from modules.db_connector import get_connection

TV_HISTORICAL_JOB_LOCK = "tv_historical_job"
TV_LIVE_BATCH_LOCK = "tv_live_batch"
WS_LIVE_RUNTIME_LOCK = "ws_live_runtime"

_HEAVY_JOB_POLL_SEC = 15.0
_HEAVY_JOB_WAIT_SEC = 30 * 60.0
_LIVE_BATCH_POLL_SEC = 5.0
_LIVE_BATCH_WAIT_SEC = 3 * 60.0
_HEARTBEAT_SEC = 15 * 60.0
_WS_LIVE_BATCH_INTERVAL_MIN = 5.0
_HISTORICAL_YIELD_BEFORE_BATCH_SEC = 20.0
_HISTORICAL_HANDOFF_GRACE_SEC = 6.0
_HISTORICAL_HANDOFF_REQUEST_AFTER_SEC = 15.0


class HistoricalJobHandoffRequested(RuntimeError):
    """Raised when the current historical job should yield to a newer run."""


@dataclass
class HistoricalJobLease:
    stop_event: threading.Event
    owner: str
    run_id: str
    owner_token: str
    released: bool = False


_LOCAL_HISTORICAL_LEASE: HistoricalJobLease | None = None


def _serialize_historical_meta(meta: dict[str, str]) -> str:
    ordered_keys = [
        "run",
        "owner",
        "host",
        "pid",
        "heartbeat",
        "handoff_run",
        "handoff_owner",
        "handoff_at",
    ]
    seen: set[str] = set()
    parts: list[str] = []
    for key in ordered_keys:
        value = meta.get(key)
        if value:
            parts.append(f"{key}={value}")
            seen.add(key)
    for key in sorted(meta):
        if key in seen:
            continue
        value = meta.get(key)
        if value:
            parts.append(f"{key}={value}")
    return ";".join(parts)


def _format_historical_payload(
    owner: str,
    run_id: str,
    *,
    handoff_run: str | None = None,
    handoff_owner: str | None = None,
    handoff_at: str | None = None,
) -> str:
    timestamp = datetime.utcnow().isoformat(timespec="seconds")
    meta = {
        "run": run_id,
        "owner": owner,
        "host": socket.gethostname(),
        "pid": str(os.getpid()),
        "heartbeat": timestamp,
    }
    if handoff_run:
        meta["handoff_run"] = handoff_run
    if handoff_owner:
        meta["handoff_owner"] = handoff_owner
    if handoff_at:
        meta["handoff_at"] = handoff_at
    return _serialize_historical_meta(meta)


def _parse_historical_payload(payload: str | None) -> dict[str, str]:
    if not payload:
        return {}
    parts: dict[str, str] = {}
    for item in payload.split(";"):
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        parts[key.strip()] = value.strip()
    return parts


def _is_pid_alive(pid_text: str | None, host: str | None) -> bool:
    if not pid_text:
        return True
    if host and host.lower() != socket.gethostname().lower():
        return True
    try:
        pid = int(pid_text)
    except (TypeError, ValueError):
        return True

    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return True

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _fetch_historical_lock_row() -> dict[str, Any] | None:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT StartedAt, ExpiresAt, Payload
            FROM SEN.ActiveTask
            WHERE TaskName = ?
            """,
            (TV_HISTORICAL_JOB_LOCK,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "started_at": row[0],
            "expires_at": row[1],
            "payload": row[2],
        }
    except Exception:
        return None
    finally:
        if conn is not None:
            conn.close()


def _insert_historical_lock(duration_min: int, payload: str) -> bool:
    return acquire(TV_HISTORICAL_JOB_LOCK, duration_min=duration_min, payload=payload)


def _renew_historical_lock(duration_min: int, owner_token: str, payload: str) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE SEN.ActiveTask
            SET ExpiresAt = DATEADD(minute, ?, SYSUTCDATETIME()),
                Payload = ?
            WHERE TaskName = ? AND ExpiresAt > SYSUTCDATETIME()
              AND Payload LIKE ?
            """,
            (duration_min, payload, TV_HISTORICAL_JOB_LOCK, f"{owner_token}%"),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def _release_historical_lock(owner_token: str) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM SEN.ActiveTask WHERE TaskName = ? AND Payload LIKE ?",
            (TV_HISTORICAL_JOB_LOCK, f"{owner_token}%"),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def _request_historical_handoff(
    current_meta: dict[str, str],
    requester_owner: str,
    requester_run_id: str,
) -> bool:
    current_run = current_meta.get("run")
    if not current_run:
        return False
    if current_meta.get("handoff_run") == requester_run_id:
        return True

    merged = dict(current_meta)
    merged["handoff_run"] = requester_run_id
    merged["handoff_owner"] = requester_owner
    merged["handoff_at"] = datetime.utcnow().isoformat(timespec="seconds")
    payload = _serialize_historical_meta(merged)

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE SEN.ActiveTask
            SET Payload = ?
            WHERE TaskName = ? AND Payload LIKE ?
            """,
            (payload, TV_HISTORICAL_JOB_LOCK, f"run={current_run};%"),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def _checkpoint_historical_handoff(owner: str, logger: logging.Logger) -> None:
    global _LOCAL_HISTORICAL_LEASE

    lease = _LOCAL_HISTORICAL_LEASE
    if lease is None:
        return

    lock_row = _fetch_historical_lock_row()
    if not lock_row:
        lease.stop_event.set()
        logger.warning(
            "[TV_COORD] %s lost %s lock unexpectedly and will yield.",
            owner,
            TV_HISTORICAL_JOB_LOCK,
        )
        raise HistoricalJobHandoffRequested("historical lock disappeared")

    meta = _parse_historical_payload(lock_row.get("payload"))
    if meta.get("run") != lease.run_id:
        lease.stop_event.set()
        logger.warning(
            "[TV_COORD] %s no longer owns %s lock and will yield.",
            owner,
            TV_HISTORICAL_JOB_LOCK,
        )
        raise HistoricalJobHandoffRequested("historical lock ownership changed")

    handoff_run = meta.get("handoff_run")
    if handoff_run and handoff_run != lease.run_id:
        requester = meta.get("handoff_owner", "newer job")
        lease.stop_event.set()
        logger.info(
            "[TV_COORD] %s yielding %s lock to newer %s run=%s at a safe checkpoint.",
            owner,
            TV_HISTORICAL_JOB_LOCK,
            requester,
            handoff_run,
        )
        raise HistoricalJobHandoffRequested(
            f"yielding to newer historical job: {requester}/{handoff_run}"
        )


def _is_heartbeat_stale(
    heartbeat_str: str | None,
    started_at: Any | None = None,
    *,
    grace_sec: float = 120.0,
) -> bool:
    """
    Trả về True nếu lock holder đã bỏ lỡ ít nhất một chu kỳ heartbeat renewal.

    Một process còn sống luôn renew heartbeat mỗi _HEARTBEAT_SEC giây.
    Nếu heartbeat cũ hơn _HEARTBEAT_SEC + grace_sec → process gần như chắc chắn đã chết.

    Thứ tự kiểm tra:
    1. Trường 'heartbeat' trong Payload (chính xác nhất).
    2. Fallback: StartedAt từ DB nếu payload không có heartbeat (lock format cũ).
    Trả về False nếu không đọc được timestamp — tránh false positive.
    """
    threshold = _HEARTBEAT_SEC + grace_sec

    if heartbeat_str:
        try:
            age = (datetime.utcnow() - datetime.fromisoformat(heartbeat_str)).total_seconds()
            return age > threshold
        except Exception:
            pass

    # Fallback: dùng StartedAt từ DB khi payload không có heartbeat
    if started_at is not None and isinstance(started_at, datetime):
        try:
            age = (datetime.utcnow() - started_at).total_seconds()
            return age > threshold
        except Exception:
            pass

    return False


def acquire_historical_job(
    owner: str,
    logger: logging.Logger,
    *,
    duration_min: int = 240,
    wait_timeout_sec: float = _HEAVY_JOB_WAIT_SEC,
    poll_sec: float = _HEAVY_JOB_POLL_SEC,
) -> HistoricalJobLease | None:
    """
    Acquire the singleton lock for heavy TradingView history pulls.

    Pipeline and checker should not hammer TradingView at the same time.
    If another heavy job is already running, wait politely for it to finish.
    """
    start = time.monotonic()
    last_log = -poll_sec
    handoff_requested = False
    run_id = uuid.uuid4().hex[:12]
    owner_token = f"run={run_id};"
    while True:
        payload = _format_historical_payload(owner, run_id)
        if _insert_historical_lock(duration_min=duration_min, payload=payload):
            global _LOCAL_HISTORICAL_LEASE
            lease = HistoricalJobLease(
                stop_event=threading.Event(),
                owner=owner,
                run_id=run_id,
                owner_token=owner_token,
            )
            _LOCAL_HISTORICAL_LEASE = lease

            def _heartbeat() -> None:
                while not lease.stop_event.wait(_HEARTBEAT_SEC):
                    lock_row = _fetch_historical_lock_row()
                    meta = _parse_historical_payload(lock_row.get("payload")) if lock_row else {}
                    heartbeat_payload = _format_historical_payload(
                        owner,
                        run_id,
                        handoff_run=meta.get("handoff_run"),
                        handoff_owner=meta.get("handoff_owner"),
                        handoff_at=meta.get("handoff_at"),
                    )
                    if not _renew_historical_lock(
                        duration_min=duration_min,
                        owner_token=owner_token,
                        payload=heartbeat_payload,
                    ):
                        logger.warning(
                            "[TV_COORD] %s could not renew %s lock.",
                            owner,
                            TV_HISTORICAL_JOB_LOCK,
                        )

            threading.Thread(
                target=_heartbeat,
                name=f"{owner}-tv-history-heartbeat",
                daemon=True,
            ).start()
            logger.info(
                "[TV_COORD] %s acquired %s lock.",
                owner,
                TV_HISTORICAL_JOB_LOCK,
            )
            return lease

        lock_row = _fetch_historical_lock_row()
        if lock_row:
            meta = _parse_historical_payload(lock_row.get("payload"))
            stale_run = meta.get("run")
            pid_dead = stale_run and not _is_pid_alive(meta.get("pid"), meta.get("host"))
            heartbeat_stale = _is_heartbeat_stale(meta.get("heartbeat"), lock_row.get("started_at"))
            if pid_dead or heartbeat_stale:
                stale_owner = meta.get("owner", "unknown")
                reason = (
                    "pid không tồn tại"
                    if pid_dead
                    else f"heartbeat quá hạn ({meta.get('heartbeat') or 'không có'})"
                )
                logger.warning(
                    "[TV_COORD] %s phát hiện lock %s cũ từ %s pid=%s run=%s (%s) — thu hồi lại.",
                    owner,
                    TV_HISTORICAL_JOB_LOCK,
                    stale_owner,
                    meta.get("pid", "?"),
                    stale_run or "?",
                    reason,
                )
                if stale_run:
                    _release_historical_lock(f"run={stale_run};")
                else:
                    release(TV_HISTORICAL_JOB_LOCK)  # payload không có run_id — force delete
                time.sleep(1.0)
                continue
            waited = time.monotonic() - start
            if (
                stale_run
                and waited >= _HISTORICAL_HANDOFF_REQUEST_AFTER_SEC
                and not handoff_requested
            ):
                if _request_historical_handoff(meta, owner, run_id):
                    logger.info(
                        "[TV_COORD] %s requested a safe handoff from %s run=%s.",
                        owner,
                        meta.get("owner", "existing"),
                        stale_run,
                    )
                    handoff_requested = True

        waited = time.monotonic() - start
        if waited >= wait_timeout_sec:
            logger.error(
                "[TV_COORD] %s timed out after %.0fs waiting for %s lock.",
                owner,
                waited,
                TV_HISTORICAL_JOB_LOCK,
            )
            return None

        if waited - last_log >= 60.0:
            logger.info(
                "[TV_COORD] %s waiting %.0fs for another historical job to finish...",
                owner,
                waited,
            )
            last_log = waited
        time.sleep(poll_sec)


def release_historical_job(
    lease: HistoricalJobLease | None,
    owner: str,
    logger: logging.Logger,
) -> None:
    """Release the singleton historical-job lock."""
    global _LOCAL_HISTORICAL_LEASE
    if lease is not None and lease.released:
        return
    if lease is not None:
        lease.stop_event.set()
    released = _release_historical_lock(lease.owner_token) if lease is not None else False
    if _LOCAL_HISTORICAL_LEASE is lease:
        _LOCAL_HISTORICAL_LEASE = None
    if lease is not None:
        lease.released = True
    if released:
        logger.info("[TV_COORD] %s released %s lock.", owner, TV_HISTORICAL_JOB_LOCK)
    else:
        logger.info(
            "[TV_COORD] %s left %s lock untouched because it had already been handed off or released.",
            owner,
            TV_HISTORICAL_JOB_LOCK,
        )


def acquire_live_batch_window(
    logger: logging.Logger,
    *,
    duration_min: int = 10,
) -> bool:
    """
    Mark the short window in which ws_live is actively talking to TradingView.

    Historical jobs can use this signal to wait until the live batch clears.
    """
    locked = acquire(TV_LIVE_BATCH_LOCK, duration_min=duration_min)
    if not locked:
        logger.warning(
            "[TV_COORD] Could not acquire %s lock for current live batch.",
            TV_LIVE_BATCH_LOCK,
        )
    return locked


def release_live_batch_window(active: bool) -> None:
    """Release the short ws_live batch window lock."""
    if active:
        release(TV_LIVE_BATCH_LOCK)


def wait_for_live_batch_clear(
    owner: str,
    logger: logging.Logger,
    *,
    max_wait_sec: float = _LIVE_BATCH_WAIT_SEC,
    poll_sec: float = _LIVE_BATCH_POLL_SEC,
) -> bool:
    """
    Wait for the current ws_live fetch window to finish.

    Returns:
      True  -> live batch cleared before timeout
      False -> timeout reached; caller may continue cautiously
    """
    _checkpoint_historical_handoff(owner, logger)
    start = time.monotonic()
    last_log = -poll_sec
    while is_locked(TV_LIVE_BATCH_LOCK):
        _checkpoint_historical_handoff(owner, logger)
        waited = time.monotonic() - start
        if waited >= max_wait_sec:
            logger.warning(
                "[TV_COORD] %s waited %.0fs for live batch window and will continue cautiously.",
                owner,
                waited,
            )
            return False
        if waited - last_log >= 30.0:
            logger.info(
                "[TV_COORD] %s waiting %.0fs for ws_live batch window to clear...",
                owner,
                waited,
            )
            last_log = waited
        time.sleep(poll_sec)
    return True


def _seconds_until_next_live_boundary(interval_minutes: float = _WS_LIVE_BATCH_INTERVAL_MIN) -> float:
    """Return seconds until the next ws_live scheduler boundary."""
    now = datetime.now()
    elapsed = (now.minute % interval_minutes) * 60 + now.second + now.microsecond / 1_000_000
    wait = interval_minutes * 60 - elapsed
    return wait if wait > 5 else interval_minutes * 60


def wait_for_historical_slot(
    owner: str,
    logger: logging.Logger,
    *,
    max_wait_sec: float = _LIVE_BATCH_WAIT_SEC,
    poll_sec: float = _LIVE_BATCH_POLL_SEC,
    yield_before_batch_sec: float = _HISTORICAL_YIELD_BEFORE_BATCH_SEC,
    handoff_grace_sec: float = _HISTORICAL_HANDOFF_GRACE_SEC,
) -> bool:
    """
    Wait for a safe historical-fetch slot while ws_live has top priority.

    Behavior:
    - If a live batch is active now, wait for it to clear.
    - If ws_live is running and the next batch boundary is very close, pause
      briefly so historical jobs do not start a new TV request right before
      ws_live's next fetch window.
    """
    cleared = wait_for_live_batch_clear(
        owner,
        logger,
        max_wait_sec=max_wait_sec,
        poll_sec=poll_sec,
    )
    if not cleared:
        return False

    if not is_locked(WS_LIVE_RUNTIME_LOCK):
        return True

    until_boundary = _seconds_until_next_live_boundary()
    if until_boundary > yield_before_batch_sec:
        return True

    sleep_sec = until_boundary + handoff_grace_sec
    logger.info(
        "[TV_COORD] %s yielding %.0fs for upcoming ws_live batch boundary...",
        owner,
        sleep_sec,
    )
    time.sleep(sleep_sec)
    return wait_for_live_batch_clear(
        owner,
        logger,
        max_wait_sec=max_wait_sec,
        poll_sec=poll_sec,
    )


def sleep_between_historical_requests(tv_symbol: str) -> float:
    """
    Shared pacing for pipeline/checker historical requests.

    When ws_live is active 24/7, add a small extra delay so heavy jobs yield
    bandwidth to the live updater instead of hammering TradingView.
    """
    _checkpoint_historical_handoff("historical", logging.getLogger(__name__))
    base = 10.0 if tv_symbol == "GOLD" else 5.0
    if is_locked(WS_LIVE_RUNTIME_LOCK):
        base += 2.0 if tv_symbol == "GOLD" else 1.0
    time.sleep(base)
    _checkpoint_historical_handoff("historical", logging.getLogger(__name__))
    return base


# ─── WS Live graceful shutdown signal ────────────────────────────────────────

_WS_LIVE_SHUTDOWN_SIGNAL = "shutdown_requested=1"


def request_ws_live_shutdown(logger: logging.Logger) -> bool:
    """
    Yêu cầu instance ws_live đang chạy tắt gracefully để nhường chỗ cho instance mới.
    Ghi 'shutdown_requested=1' vào Payload của lock 'ws_live_runtime'.
    Trả về True nếu tín hiệu được ghi thành công.
    """
    from _task_lock import update_payload
    result = update_payload(WS_LIVE_RUNTIME_LOCK, _WS_LIVE_SHUTDOWN_SIGNAL)
    if result:
        logger.info(
            "[TV_COORD] Shutdown signal written to %s lock — waiting for old instance to exit.",
            WS_LIVE_RUNTIME_LOCK,
        )
    else:
        logger.warning(
            "[TV_COORD] Could not write shutdown signal to %s lock — lock may already be gone.",
            WS_LIVE_RUNTIME_LOCK,
        )
    return result


def is_ws_live_shutdown_requested() -> bool:
    """
    Được gọi bởi main loop của ws_live để kiểm tra xem instance mới có muốn tiếp quản không.
    Trả về True nếu Payload chứa 'shutdown_requested=1'.
    Fail-safe: trả về False khi DB lỗi — không bao giờ trigger shutdown nhầm.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT Payload FROM SEN.ActiveTask
            WHERE TaskName = ? AND ExpiresAt > SYSUTCDATETIME()
            """,
            (WS_LIVE_RUNTIME_LOCK,),
        )
        row = cursor.fetchone()
        if row and row[0]:
            return _WS_LIVE_SHUTDOWN_SIGNAL in str(row[0])
        return False
    except Exception:
        return False  # fail-safe: DB lỗi không được tắt ws_live nhầm
    finally:
        if conn is not None:
            conn.close()
