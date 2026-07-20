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
