"""Tests for the P0-4 fix: BackendSupervisor's live-restart budget used to
give up permanently once BACKEND_LIVE_MAX_RESTARTS_PER_HOUR was exhausted
(operator had to notice and restart DP Program by hand), and start_live()
unconditionally cleared the Graceful Stop flag file - so a stop request
that arrived during a restart cooldown was silently erased. This file
covers the replacement: a non-blocking exponential-backoff retry that
keeps trying automatically, exit-code-aware slow retries for lock-conflict/
cancelled exits, a stable-uptime reset of the failure count, and
start_live() no longer touching the stop flag.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from core_engine.exit_codes import EXIT_CANCELLED, EXIT_LOCK_CONFLICT
from core_engine.supervisor import engine as supervisor_engine
from core_engine.supervisor.engine import (
    LIVE_RESTART_BACKOFF_BASE_SEC,
    LIVE_RESTART_BACKOFF_MAX_SEC,
    LIVE_RESTART_SLOW_MIN_SEC,
    BackendSupervisor,
)


@pytest.fixture
def sup(monkeypatch):
    # Notifications/log_activity touch Discord + activity log machinery we
    # do not want to exercise for real in a unit test.
    monkeypatch.setattr(supervisor_engine, "notify_live_event", lambda **k: None)
    monkeypatch.setattr(supervisor_engine, "notify_backend_event", lambda **k: None)
    monkeypatch.setattr(supervisor_engine, "notify_historical_event", lambda **k: None)
    monkeypatch.setattr(supervisor_engine, "flush_pending", lambda: None)
    monkeypatch.setattr(supervisor_engine, "log_activity", lambda *a, **k: None)
    monkeypatch.setattr(supervisor_engine, "_atomic_write_json", lambda *a, **k: None)
    return BackendSupervisor(live_enabled=True, schedule_enabled=False)


def test_arm_live_backoff_schedules_future_retry_and_counts_failure(sup):
    assert sup.live_failure_count == 0
    sup._arm_live_backoff(exit_code=1, reason="test")
    assert sup.live_failure_count == 1
    assert sup._live_retry_wait_seconds() > 0
    assert sup.live_retry_not_before > time.time()


def test_live_retry_due_is_false_before_deadline_true_after(sup):
    sup.live_retry_not_before = time.time() + 3600
    assert sup._live_retry_due() is False

    sup.live_retry_not_before = time.time() - 1
    assert sup._live_retry_due() is True


def test_live_retry_due_requires_live_not_already_running(sup):
    sup.live_retry_not_before = time.time() - 1
    sup.live.process = SimpleNamespace(poll=lambda: None)  # still running
    assert sup._live_retry_due() is False


def test_backoff_grows_exponentially_and_caps(sup):
    delays = []
    for _ in range(8):
        before = sup.live_retry_not_before
        sup._arm_live_backoff(exit_code=1, reason="test")
        delays.append(sup.live_retry_not_before - max(before, time.time() - 1))

    # Roughly increasing (allowing for timing jitter across the loop) and
    # never exceeding the configured cap.
    assert delays[0] < delays[3]
    assert all(d <= LIVE_RESTART_BACKOFF_MAX_SEC + 2 for d in delays)


def test_lock_conflict_exit_code_gets_slow_minimum_backoff(sup):
    sup._arm_live_backoff(exit_code=EXIT_LOCK_CONFLICT, reason="test")
    assert sup._live_retry_wait_seconds() >= LIVE_RESTART_SLOW_MIN_SEC - 1


def test_cancelled_exit_code_also_gets_slow_minimum_backoff(sup):
    sup._arm_live_backoff(exit_code=EXIT_CANCELLED, reason="test")
    assert sup._live_retry_wait_seconds() >= LIVE_RESTART_SLOW_MIN_SEC - 1


def test_restart_live_skips_immediate_retry_for_lock_conflict(sup, monkeypatch):
    calls = []
    monkeypatch.setattr(sup, "stop_live", lambda **k: calls.append("stop_live"))
    monkeypatch.setattr(sup, "start_live", lambda **k: calls.append("start_live"))

    sup._restart_live("exit_code=5", exit_code=EXIT_LOCK_CONFLICT)

    assert calls == []  # no immediate stop/start attempt
    assert sup.live_failure_count == 1
    assert sup._live_retry_wait_seconds() > 0


def test_restart_live_retries_immediately_within_budget(sup, monkeypatch):
    calls = []
    monkeypatch.setattr(sup, "stop_live", lambda **k: calls.append("stop_live"))
    monkeypatch.setattr(sup, "start_live", lambda **k: calls.append("start_live"))
    monkeypatch.setattr(supervisor_engine.time, "sleep", lambda *_: None)

    sup._restart_live("exit_code=1", exit_code=1)

    assert calls == ["stop_live", "start_live"]
    assert sup.live_failure_count == 0  # budget path does not touch the backoff counter


def test_restart_live_falls_back_to_backoff_once_budget_exhausted(sup, monkeypatch):
    # BACKEND is a frozen dataclass singleton - simulate an exhausted
    # per-hour budget by monkeypatching the check method directly instead
    # of trying to mutate frozen settings.
    monkeypatch.setattr(sup, "_restart_budget_available", lambda: False)
    monkeypatch.setattr(supervisor_engine.time, "sleep", lambda *_: None)
    calls = []
    monkeypatch.setattr(sup, "stop_live", lambda **k: calls.append("stop_live"))
    monkeypatch.setattr(sup, "start_live", lambda **k: calls.append("start_live"))

    sup._restart_live("exit_code=1", exit_code=1)  # budget already exhausted

    assert calls == []
    assert sup.live_failure_count == 1
    assert sup._live_retry_wait_seconds() > 0


def test_retry_live_after_backoff_clears_deadline_and_calls_start(sup, monkeypatch):
    sup.live_retry_not_before = time.time() - 1
    calls = []
    monkeypatch.setattr(sup, "start_live", lambda **k: calls.append(k))

    sup._retry_live_after_backoff()

    assert sup.live_retry_not_before == 0.0
    assert len(calls) == 1


def test_reset_live_backoff_if_stable_clears_after_long_uptime(sup):
    sup.live_failure_count = 3
    sup.live_retry_not_before = time.time() + 100
    sup.live.process = SimpleNamespace(poll=lambda: None, pid=None)
    sup.live.started_at = time.time() - (LIVE_RESTART_BACKOFF_BASE_SEC * 10_000)  # long ago

    sup._reset_live_backoff_if_stable()

    assert sup.live_failure_count == 0
    assert sup.live_retry_not_before == 0.0


def test_reset_live_backoff_if_stable_does_nothing_for_short_uptime(sup):
    sup.live_failure_count = 3
    sup.live.process = SimpleNamespace(poll=lambda: None)
    sup.live.started_at = time.time() - 5  # just started

    sup._reset_live_backoff_if_stable()

    assert sup.live_failure_count == 3


# --- P0-5: periodic DB-inclusive health check (not just at startup) ------


def test_periodic_db_health_check_respects_interval(sup, monkeypatch):
    calls = []
    monkeypatch.setattr(supervisor_engine, "collect_health", lambda **k: calls.append(k) or {"checks": []})
    monkeypatch.setattr(supervisor_engine.time, "time", lambda: 1_000_000.0)
    sup._last_db_health_at = 1_000_000.0 - 10  # well inside the interval

    sup._run_periodic_db_health_check()

    assert calls == []  # too soon since the last check


def test_periodic_db_health_check_runs_after_interval_elapses(sup, monkeypatch):
    from core_engine.settings import BACKEND

    calls = []
    monkeypatch.setattr(supervisor_engine, "collect_health", lambda **k: calls.append(k) or {"checks": []})
    monkeypatch.setattr(supervisor_engine.time, "time", lambda: 1_000_000.0)
    sup._last_db_health_at = 1_000_000.0 - BACKEND.db_health_interval_sec - 1

    sup._run_periodic_db_health_check()

    assert len(calls) == 1
    assert calls[0].get("include_database") is True


def test_periodic_db_health_check_escalates_contract_mismatch_to_critical(sup, monkeypatch, caplog):
    monkeypatch.setattr(supervisor_engine.time, "time", lambda: 1_000_000.0)
    sup._last_db_health_at = 0.0
    monkeypatch.setattr(
        supervisor_engine, "collect_health",
        lambda **k: {
            "checks": [
                {"name": "db_contract", "status": "fail", "message": "contract version mismatch: found 1, expected 2"},
            ]
        },
    )
    notified = []
    monkeypatch.setattr(sup, "_safe_notify", lambda fn, **k: notified.append(k))

    import logging

    with caplog.at_level(logging.CRITICAL, logger="system"):
        sup._run_periodic_db_health_check()

    assert any(r.levelno == logging.CRITICAL for r in caplog.records)
    assert len(notified) == 1
    assert notified[0]["severity"] == "CRITICAL"


def test_periodic_db_health_check_does_not_escalate_when_db_unreachable(sup, monkeypatch):
    monkeypatch.setattr(supervisor_engine.time, "time", lambda: 1_000_000.0)
    sup._last_db_health_at = 0.0
    monkeypatch.setattr(
        supervisor_engine, "collect_health",
        lambda **k: {
            "checks": [
                {"name": "db_contract", "status": "fail", "message": "contract check could not run: SQL Server unreachable"},
            ]
        },
    )
    notified = []
    monkeypatch.setattr(sup, "_safe_notify", lambda fn, **k: notified.append(k))

    sup._run_periodic_db_health_check()

    # A totally unreachable database is a different, already-covered
    # problem (the general "database" check) - this path must not also
    # fire the contract-mismatch CRITICAL alert for it.
    assert notified == []


# --- High-10: HISTORICAL_MAX_RUNTIME_MINUTES was defined but never used --


def _backend_with(**overrides):
    # BackendSettings is a frozen dataclass singleton - cannot setattr()
    # onto it directly. dataclasses.replace() makes a new instance with
    # just the given field(s) overridden, safe to monkeypatch the module-
    # level BACKEND name to point at instead.
    import dataclasses
    from core_engine.settings import BACKEND

    return dataclasses.replace(BACKEND, **overrides)


def test_enforce_historical_runtime_limit_cancels_when_exceeded(sup, monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(supervisor_engine, "BACKEND", _backend_with(historical_max_runtime_minutes=30))
    sup.historical.process = SimpleNamespace(poll=lambda: None)
    sup.active_historical_started_at = datetime.now(timezone.utc) - timedelta(minutes=45)
    calls = []
    monkeypatch.setattr(sup, "stop_historical", lambda **k: calls.append(k))

    sup._enforce_historical_runtime_limit()

    assert len(calls) == 1
    assert calls[0]["force_after_grace"] is True


def test_enforce_historical_runtime_limit_does_nothing_within_limit(sup, monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr(supervisor_engine, "BACKEND", _backend_with(historical_max_runtime_minutes=30))
    sup.historical.process = SimpleNamespace(poll=lambda: None)
    sup.active_historical_started_at = datetime.now(timezone.utc)
    calls = []
    monkeypatch.setattr(sup, "stop_historical", lambda **k: calls.append(k))

    sup._enforce_historical_runtime_limit()

    assert calls == []


def test_enforce_historical_runtime_limit_can_be_explicitly_disabled(sup, monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(supervisor_engine, "BACKEND", _backend_with(historical_max_runtime_minutes=0))
    sup.historical.process = SimpleNamespace(poll=lambda: None)
    sup.active_historical_started_at = datetime.now(timezone.utc) - timedelta(days=1)
    calls = []
    monkeypatch.setattr(sup, "stop_historical", lambda **k: calls.append(k))

    sup._enforce_historical_runtime_limit()

    assert calls == []


# --- Medium-17: fully-malformed HISTORICAL_BACKFILL_UTC must be detectable -


def test_schedule_times_returns_empty_for_fully_malformed_value(sup, monkeypatch):
    monkeypatch.setattr(supervisor_engine, "BACKEND", _backend_with(historical_backfill_utc="not,a,time"))
    assert sup._schedule_times() == []


def test_schedule_times_parses_valid_value_normally(sup, monkeypatch):
    monkeypatch.setattr(supervisor_engine, "BACKEND", _backend_with(historical_backfill_utc="11:00,22:00"))
    times = sup._schedule_times()
    assert len(times) == 2
    assert [sup._schedule_label(t) for t in times] == ["11:00", "22:00"]


def test_schedule_times_skips_only_the_bad_tokens_and_keeps_good_ones(sup, monkeypatch):
    monkeypatch.setattr(supervisor_engine, "BACKEND", _backend_with(historical_backfill_utc="11:00,garbage,22:00"))
    times = sup._schedule_times()
    assert [sup._schedule_label(t) for t in times] == ["11:00", "22:00"]


def test_start_live_no_longer_clears_the_stop_flag(sup, monkeypatch):
    called = {"clear_stop_request": False}
    monkeypatch.setattr(supervisor_engine, "clear_stop_request", lambda: called.update(clear_stop_request=True))
    monkeypatch.setattr(sup, "_spawn", lambda *a, **k: None)

    sup.start_live(reason="test")

    assert called["clear_stop_request"] is False, (
        "start_live() must not clear a pending Graceful Stop request - only "
        "run() should do that once, at supervisor startup"
    )


def test_monitor_restarts_when_first_batch_never_completes(sup, monkeypatch):
    """A fresh heartbeat must not hide a main loop stuck in its first batch."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    sup.live.process = SimpleNamespace(poll=lambda: None, pid=None)
    sup.live.started_at = time.time() - 3600
    monkeypatch.setattr(
        supervisor_engine,
        "_load_json",
        lambda _path: {
            "pid": None,
            "status": "batch_running",
            "updated_at": now.isoformat(),
            "child_started_at": (now - timedelta(hours=1)).isoformat(),
            "batch_started_at": (now - timedelta(minutes=30)).isoformat(),
            "batch_completed_at": None,
        },
    )
    restarted = []
    monkeypatch.setattr(sup, "_restart_live", lambda reason, **kwargs: restarted.append(reason))

    sup._monitor_live_freshness()

    assert restarted and "first_batch" in restarted[0]
