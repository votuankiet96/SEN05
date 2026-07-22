"""Clock-aligned scheduling for the live batch pipeline."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from threading import Event


def seconds_until_boundary(interval_minutes: int, *, now: datetime | None = None) -> float:
    """Return seconds to the next wall-clock interval boundary."""

    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")
    current = now or datetime.now()
    elapsed = (
        (current.minute % interval_minutes) * 60
        + current.second
        + current.microsecond / 1_000_000
    )
    wait = interval_minutes * 60 - elapsed
    return wait if wait > 5 else interval_minutes * 60


def run_aligned_schedule(
    *,
    shutdown: Event,
    interval_minutes: int,
    prepare_batch: Callable[[], None],
    run_batch: Callable[[], None],
    report_wait: Callable[[float], None],
) -> None:
    """Run once immediately, then at each aligned boundary until shutdown."""

    if not shutdown.is_set():
        prepare_batch()
        run_batch()

    while not shutdown.is_set():
        wait = seconds_until_boundary(interval_minutes)
        report_wait(wait)
        shutdown.wait(wait)
        if shutdown.is_set():
            break
        prepare_batch()
        run_batch()
