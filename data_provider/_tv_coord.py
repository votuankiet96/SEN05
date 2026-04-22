"""Shared TradingView fetch coordination for live + historical jobs.

This module keeps 24/7 `ws_live` as the highest-priority consumer while
allowing `01_data_pipeline.py` and `04_checker.py` to run safely:

- only one heavy historical job may pull TradingView at a time
- historical pulls wait for the current live batch window when possible
- historical jobs slow down slightly while ws_live is active
"""

from __future__ import annotations

import logging
import threading
import time

from _task_lock import acquire, is_locked, release, renew

TV_HISTORICAL_JOB_LOCK = "tv_historical_job"
TV_LIVE_BATCH_LOCK = "tv_live_batch"
WS_LIVE_RUNTIME_LOCK = "ws_live_runtime"

_HEAVY_JOB_POLL_SEC = 15.0
_HEAVY_JOB_WAIT_SEC = 30 * 60.0
_LIVE_BATCH_POLL_SEC = 5.0
_LIVE_BATCH_WAIT_SEC = 3 * 60.0
_HEARTBEAT_SEC = 15 * 60.0


def acquire_historical_job(
    owner: str,
    logger: logging.Logger,
    *,
    duration_min: int = 240,
    wait_timeout_sec: float = _HEAVY_JOB_WAIT_SEC,
    poll_sec: float = _HEAVY_JOB_POLL_SEC,
) -> threading.Event | None:
    """
    Acquire the singleton lock for heavy TradingView history pulls.

    Pipeline and checker should not hammer TradingView at the same time.
    If another heavy job is already running, wait politely for it to finish.
    """
    start = time.monotonic()
    last_log = -poll_sec
    while True:
        if acquire(TV_HISTORICAL_JOB_LOCK, duration_min=duration_min):
            stop = threading.Event()

            def _heartbeat() -> None:
                while not stop.wait(_HEARTBEAT_SEC):
                    if not renew(TV_HISTORICAL_JOB_LOCK, duration_min=duration_min):
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
            return stop

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
    stop_event: threading.Event | None,
    owner: str,
    logger: logging.Logger,
) -> None:
    """Release the singleton historical-job lock."""
    if stop_event is not None and stop_event.is_set():
        return
    if stop_event is not None:
        stop_event.set()
    release(TV_HISTORICAL_JOB_LOCK)
    logger.info("[TV_COORD] %s released %s lock.", owner, TV_HISTORICAL_JOB_LOCK)


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
    start = time.monotonic()
    last_log = -poll_sec
    while is_locked(TV_LIVE_BATCH_LOCK):
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


def sleep_between_historical_requests(tv_symbol: str) -> float:
    """
    Shared pacing for pipeline/checker historical requests.

    When ws_live is active 24/7, add a small extra delay so heavy jobs yield
    bandwidth to the live updater instead of hammering TradingView.
    """
    base = 10.0 if tv_symbol == "GOLD" else 5.0
    if is_locked(WS_LIVE_RUNTIME_LOCK):
        base += 2.0 if tv_symbol == "GOLD" else 1.0
    time.sleep(base)
    return base
