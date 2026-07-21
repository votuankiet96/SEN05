"""Tests for core_engine.health._live_state_check - the P0-5 fix.

Two gaps closed here:
  1. "failed"/"stopped" were never in the active_status set at all, so a
     live worker that crashed (and was not yet auto-restarted, e.g. mid
     backoff) reported "ok" via doctor/status instead of "fail", even
     though live is configured to run 24/7 (WS_LIVE_AUTO_START=1).
  2. The only staleness signal was updated_at, a heartbeat written by its
     own dedicated thread - a deadlocked main batch loop could keep that
     heartbeat fresh forever while never finishing another batch.
     batch_completed_at is now checked as a second, independent signal.
"""

from __future__ import annotations

import json
import os
import sys
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from core_engine import health


@pytest.fixture
def live_state_path(tmp_path, monkeypatch):
    path = tmp_path / "ws_live_state.json"
    monkeypatch.setattr(health, "WS_LIVE_STATE", path)
    return path


@pytest.fixture
def backend_enabled(monkeypatch):
    monkeypatch.setattr(health, "BACKEND", SimpleNamespace(live_stale_minutes=15, live_auto_start=True))


@pytest.fixture
def backend_disabled(monkeypatch):
    monkeypatch.setattr(health, "BACKEND", SimpleNamespace(live_stale_minutes=15, live_auto_start=False))


def _write_state(path, **fields):
    path.write_text(json.dumps(fields), encoding="utf-8")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _redis_settings(*, enabled=True, host="redis.internal"):
    return SimpleNamespace(
        enabled=enabled,
        redis_host=host,
        redis_port=6379,
        redis_username="",
        redis_password="",
        redis_db=0,
        timeout_sec=0.1,
    )


@pytest.mark.parametrize(
    ("free_gb", "expected"),
    [(13.0, "ok"), (4.0, "warn"), (0.5, "fail")],
)
def test_runtime_health_reports_low_disk_space(monkeypatch, tmp_path, free_gb, expected):
    for name in ("LOG_DIR", "CACHE_DIR", "RUN_DIR", "SPOOL_DIR"):
        path = tmp_path / name.lower()
        path.mkdir()
        monkeypatch.setattr(health, name, path)
    monkeypatch.setattr(health, "APP_ROOT", tmp_path)
    monkeypatch.setattr(health, "ensure_runtime_dirs", lambda: None)
    monkeypatch.setattr(
        health,
        "BACKEND",
        SimpleNamespace(disk_warn_free_gb=5.0, disk_fail_free_gb=1.0),
    )
    DiskUsage = namedtuple("usage", "total used free")
    total = 100 * 1024**3
    free = int(free_gb * 1024**3)
    monkeypatch.setattr(health.shutil, "disk_usage", lambda _path: DiskUsage(total, total - free, free))

    check = health._runtime_check()

    assert check.status == expected
    assert check.detail["disk_free_gb"] == free_gb


def test_redis_snapshot_health_is_disabled_in_sql_mode(monkeypatch):
    monkeypatch.setattr(health, "STORAGE", SimpleNamespace(mode="sql"))
    monkeypatch.setattr(health, "CANDLE_SNAPSHOT", _redis_settings())

    check = health._redis_snapshot_check()

    assert check.status == "ok"
    assert check.detail["enabled"] is False


def test_redis_snapshot_health_warns_when_enabled_without_host(monkeypatch):
    monkeypatch.setattr(health, "STORAGE", SimpleNamespace(mode="both"))
    monkeypatch.setattr(health, "CANDLE_SNAPSHOT", _redis_settings(host=""))

    check = health._redis_snapshot_check()

    assert check.status == "warn"
    assert check.detail["configured"] is False


@pytest.mark.parametrize("ping_result, expected", [(True, "ok"), (False, "warn")])
def test_redis_snapshot_health_reports_ping_result(monkeypatch, ping_result, expected):
    closed = []

    class FakeRedis:
        def __init__(self, **_kwargs):
            pass

        def ping(self):
            return ping_result

        def close(self):
            closed.append(True)

    monkeypatch.setattr(health, "STORAGE", SimpleNamespace(mode="both"))
    monkeypatch.setattr(health, "CANDLE_SNAPSHOT", _redis_settings())
    monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=FakeRedis))

    check = health._redis_snapshot_check()

    assert check.status == expected
    assert closed == [True]


def test_missing_state_file_is_warn(live_state_path, backend_enabled):
    check = health._live_state_check()
    assert check.status == "warn"


def test_failed_status_is_fail_when_live_auto_start_enabled(live_state_path, backend_enabled):
    _write_state(live_state_path, status="failed", pid=None, updated_at=_iso(datetime.now(timezone.utc)))
    check = health._live_state_check()
    assert check.status == "fail"
    assert "failed" in check.message.lower() or "'failed'" in check.message


def test_stopped_status_is_fail_when_live_auto_start_enabled(live_state_path, backend_enabled):
    _write_state(live_state_path, status="stopped", pid=None, updated_at=_iso(datetime.now(timezone.utc)))
    check = health._live_state_check()
    assert check.status == "fail"


def test_failed_status_is_not_fail_when_live_auto_start_disabled(live_state_path, backend_disabled):
    # If the operator has WS_LIVE_AUTO_START=0, "stopped"/"failed" is
    # expected, not a health problem to page anyone about.
    _write_state(live_state_path, status="stopped", pid=None, updated_at=_iso(datetime.now(timezone.utc)))
    check = health._live_state_check()
    assert check.status != "fail"


def test_running_with_fresh_heartbeat_and_fresh_batch_is_ok(live_state_path, backend_enabled):
    now = datetime.now(timezone.utc)
    _write_state(
        live_state_path,
        status="waiting",
        pid=os.getpid(),  # must be a real, currently-alive PID for this test process
        updated_at=_iso(now),
        batch_completed_at=_iso(now - timedelta(seconds=30)),
    )
    check = health._live_state_check()
    assert check.status == "ok"


def test_live_recovery_metrics_are_exposed_in_health_detail(live_state_path, backend_enabled):
    now = datetime.now(timezone.utc)
    _write_state(
        live_state_path,
        status="waiting",
        pid=os.getpid(),
        updated_at=_iso(now),
        batch_completed_at=_iso(now),
        ws_forced_socket_closes=2,
        ws_orphaned_threads=1,
        ws_wedged_group_recycles=1,
    )

    check = health._live_state_check()

    assert check.detail["ws_forced_socket_closes"] == 2
    assert check.detail["ws_orphaned_threads"] == 1
    assert check.detail["ws_wedged_group_recycles"] == 1


def test_running_with_stale_batch_completed_at_is_fail_even_with_fresh_heartbeat(live_state_path, backend_enabled):
    now = datetime.now(timezone.utc)
    _write_state(
        live_state_path,
        status="waiting",
        pid=None,
        updated_at=_iso(now),  # heartbeat is fresh...
        batch_completed_at=_iso(now - timedelta(minutes=60)),  # ...but no batch finished in an hour
    )
    check = health._live_state_check()
    assert check.status == "fail"
    assert "batch" in check.message.lower()


def test_running_with_stale_heartbeat_is_fail(live_state_path, backend_enabled):
    now = datetime.now(timezone.utc)
    _write_state(
        live_state_path,
        status="waiting",
        pid=None,
        updated_at=_iso(now - timedelta(minutes=60)),
    )
    check = health._live_state_check()
    assert check.status == "fail"


def test_missing_batch_completed_at_does_not_fail_a_fresh_heartbeat(live_state_path, backend_enabled):
    # A freshly-started process may not have completed a batch yet at all;
    # absence of the field must not itself be treated as staleness.
    now = datetime.now(timezone.utc)
    _write_state(live_state_path, status="starting", pid=None, updated_at=_iso(now))
    check = health._live_state_check()
    assert check.status == "ok"


def test_batch_running_with_dead_pid_is_not_reported_healthy(live_state_path, backend_enabled):
    now = datetime.now(timezone.utc)
    _write_state(
        live_state_path,
        status="batch_running",
        pid=999_999_999,
        updated_at=_iso(now),
        batch_started_at=_iso(now - timedelta(minutes=1)),
    )

    check = health._live_state_check()

    assert check.status == "fail"
    assert "pid" in check.message.lower()


def test_first_batch_stall_fails_after_startup_grace(live_state_path, backend_enabled):
    now = datetime.now(timezone.utc)
    _write_state(
        live_state_path,
        status="batch_running",
        pid=os.getpid(),
        updated_at=_iso(now),
        child_started_at=_iso(now - timedelta(minutes=30)),
        batch_started_at=_iso(now - timedelta(minutes=25)),
        batch_completed_at=None,
    )

    check = health._live_state_check()

    assert check.status == "fail"
    assert "first batch" in check.message.lower()
