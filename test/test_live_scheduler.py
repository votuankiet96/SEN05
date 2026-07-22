from datetime import datetime
import threading

import pytest

from core_engine.core.live import scheduler


def test_seconds_until_boundary_is_clock_aligned():
    now = datetime(2026, 7, 22, 10, 7, 30)
    assert scheduler.seconds_until_boundary(5, now=now) == pytest.approx(150.0)


def test_seconds_until_boundary_rolls_to_following_interval_near_boundary():
    now = datetime(2026, 7, 22, 10, 9, 58)
    assert scheduler.seconds_until_boundary(5, now=now) == 300.0


def test_run_aligned_schedule_runs_immediately_then_stops(monkeypatch):
    shutdown = threading.Event()
    calls: list[str] = []

    monkeypatch.setattr(scheduler, "seconds_until_boundary", lambda _interval: 2.0)

    def report_wait(wait: float) -> None:
        calls.append(f"wait:{wait}")
        shutdown.set()

    scheduler.run_aligned_schedule(
        shutdown=shutdown,
        interval_minutes=5,
        prepare_batch=lambda: calls.append("prepare"),
        run_batch=lambda: calls.append("run"),
        report_wait=report_wait,
    )

    assert calls == ["prepare", "run", "wait:2.0"]
