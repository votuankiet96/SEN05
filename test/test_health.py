"""Tests for core_engine.util.health._live_state_check - the P0-5 fix.

Two gaps closed here:
  1. "failed"/"stopped" were never in the active_status set at all, so a
     live worker that crashed (and was not yet auto-restarted, e.g. mid
     backoff) reported "ok" via doctor/status instead of "fail", even
     though live is designed to run continuously.
  2. The only staleness signal was updated_at, a heartbeat written by its
     own dedicated thread - a deadlocked main batch loop could keep that
     heartbeat fresh forever while never finishing another batch.
     batch_completed_at is now checked as a second, independent signal.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from core_engine.util import health


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


def _repair_item(*, asset_type: str, reason: str = "STALE"):
    return {
        "sym": {
            "symbol_id": 1,
            "asset_type": asset_type,
            "tv_symbol": "TEST",
        },
        "tf_code": "M5",
        "reason": reason,
        "gap_hours": 1.0,
    }


def test_data_health_only_suppresses_schedule_current_historical_latency():
    items = [
        _repair_item(asset_type="FOREX"),
        _repair_item(asset_type="FOREX", reason="STALE+HOLE"),
        _repair_item(asset_type="Indice"),
        _repair_item(asset_type="FOREX", reason="MISS"),
    ]

    actionable, scheduled = health._partition_data_health_repairs(
        items,
        live_asset_types={"Indice", "Metal", "Crypto"},
        historical_schedule_current=True,
    )

    assert scheduled == [items[0]]
    assert actionable == items[1:]


def test_data_health_keeps_historical_latency_actionable_after_missed_schedule():
    item = _repair_item(asset_type="FOREX")

    actionable, scheduled = health._partition_data_health_repairs(
        [item],
        live_asset_types={"Indice", "Metal", "Crypto"},
        historical_schedule_current=False,
    )

    assert actionable == [item]
    assert scheduled == []


def test_historical_schedule_context_accepts_previous_success_during_new_slot_grace(
    monkeypatch,
    tmp_path,
):
    now = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(health, "_utc_now", lambda: now)
    monkeypatch.setattr(health, "RUN_DIR", tmp_path)
    monkeypatch.setattr(
        health,
        "BACKEND",
        SimpleNamespace(
            historical_backfill_enabled=True,
            historical_backfill_utc="11:00,22:00",
            historical_max_runtime_minutes=360,
        ),
    )
    _write_state(
        tmp_path / "historical_last_run.json",
        completed_at="2026-07-24T03:02:00+00:00",
        stats={"queued": 310, "ok": 310, "fail": 0},
    )

    context = health._historical_schedule_context()

    assert context["current"] is True
    assert context["required_success_after"] == "2026-07-23T22:00:00+00:00"
    assert context["grace_deadline"] == "2026-07-24T17:00:00+00:00"


def test_historical_schedule_context_detects_missed_completed_slot(
    monkeypatch,
    tmp_path,
):
    now = datetime(2026, 7, 24, 18, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(health, "_utc_now", lambda: now)
    monkeypatch.setattr(health, "RUN_DIR", tmp_path)
    monkeypatch.setattr(
        health,
        "BACKEND",
        SimpleNamespace(
            historical_backfill_enabled=True,
            historical_backfill_utc="11:00,22:00",
            historical_max_runtime_minutes=360,
        ),
    )
    _write_state(
        tmp_path / "historical_last_run.json",
        completed_at="2026-07-24T03:02:00+00:00",
        stats={"queued": 310, "ok": 310, "fail": 0},
    )

    context = health._historical_schedule_context()

    assert context["current"] is False
    assert context["required_success_after"] == "2026-07-24T11:00:00+00:00"


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


def test_live_spool_health_fails_when_fact_backlog_is_stale(monkeypatch, tmp_path):
    path = tmp_path / "overflow_spool.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE spool (status TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        conn.execute("CREATE TABLE spool_quarantine (id INTEGER)")
        old = datetime.now(timezone.utc) - timedelta(minutes=20)
        conn.execute(
            "INSERT INTO spool(status, created_at) VALUES (?, ?)",
            ("staged", old.strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    monkeypatch.setattr(health, "WS_OVERFLOW_SPOOL", path)

    check = health._live_spool_check()

    assert check.status == "fail"
    assert check.detail["pending_count"] == 1
    assert check.detail["by_status"] == {"staged": 1}


def test_live_spool_health_accepts_fresh_in_flight_row(monkeypatch, tmp_path):
    path = tmp_path / "overflow_spool.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE spool (status TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        conn.execute("CREATE TABLE spool_quarantine (id INTEGER)")
        conn.execute(
            "INSERT INTO spool(status, created_at) VALUES (?, datetime('now'))",
            ("leased",),
        )
        conn.commit()
    monkeypatch.setattr(health, "WS_OVERFLOW_SPOOL", path)

    check = health._live_spool_check()

    assert check.status == "ok"
    assert check.detail["pending_count"] == 1
    assert "in-flight" in check.message


def test_live_spool_health_is_ok_when_empty(monkeypatch, tmp_path):
    path = tmp_path / "overflow_spool.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE spool (status TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        conn.execute("CREATE TABLE spool_quarantine (id INTEGER)")
    monkeypatch.setattr(health, "WS_OVERFLOW_SPOOL", path)

    check = health._live_spool_check()

    assert check.status == "ok"
    assert check.detail["pending_count"] == 0


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
    # The helper still supports an intentionally disabled live component for
    # isolated maintenance/test deployments.
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


def test_critical_outbox_storage_failure_is_a_health_failure(monkeypatch):
    from core_engine.util.notify import critical_outbox

    fake = SimpleNamespace(
        status=lambda: {
            "healthy": False,
            "pending_count": None,
            "storage_error": "DatabaseError: corrupt",
        }
    )
    monkeypatch.setattr(critical_outbox, "critical_alert_outbox", lambda: fake)

    check = health._critical_outbox_check()

    assert check.status == "fail"
    assert "cannot be trusted" in check.message


def test_log_health_fails_when_active_process_has_no_sink_registry(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    run_dir = tmp_path / "run"
    log_dir.mkdir()
    run_dir.mkdir()
    monkeypatch.setattr(health, "LOG_DIR", log_dir)
    monkeypatch.setattr(health, "RUN_DIR", run_dir)
    monkeypatch.setattr(health, "_active_runtime_roles", lambda: {"live": 12345})

    check = health._log_sinks_check()

    assert check.status == "fail"
    assert "missing sink registry" in check.message


def test_discord_health_requires_confirmed_delivery_for_active_process(
    tmp_path, monkeypatch
):
    import core_engine.settings as settings

    run_dir = tmp_path / "run"
    status_dir = run_dir / "notification_status"
    status_dir.mkdir(parents=True)
    monkeypatch.setattr(health, "RUN_DIR", run_dir)
    monkeypatch.setattr(health, "_active_runtime_roles", lambda: {"live": 321})
    monkeypatch.setattr(
        settings,
        "NOTIFICATION",
        SimpleNamespace(discord_webhook_url="https://configured.invalid/webhook"),
    )
    (status_dir / "live.321.json").write_text(
        json.dumps(
            {
                "last_success_at": None,
                "last_failure_at": None,
                "queue_pending": 0,
                "queue_maxsize": 256,
                "worker_started": True,
                "worker_alive": True,
                "circuit_open_seconds": 0,
                "logger_error": None,
            }
        ),
        encoding="utf-8",
    )

    check = health._discord_check()

    assert check.status == "fail"
    assert "confirmed Discord" in check.message


def test_discord_health_does_not_fail_for_unexercised_child_when_owner_delivered(
    tmp_path, monkeypatch
):
    import core_engine.settings as settings

    run_dir = tmp_path / "run"
    status_dir = run_dir / "notification_status"
    status_dir.mkdir(parents=True)
    monkeypatch.setattr(health, "RUN_DIR", run_dir)
    monkeypatch.setattr(
        health,
        "_active_runtime_roles",
        lambda: {"supervisor": 100, "historical": 200},
    )
    monkeypatch.setattr(
        settings,
        "NOTIFICATION",
        SimpleNamespace(discord_webhook_url="https://configured.invalid/webhook"),
    )
    (status_dir / "supervisor.100.json").write_text(
        json.dumps(
            {
                "last_success_at": datetime.now(timezone.utc).isoformat(),
                "last_failure_at": None,
                "queue_pending": 0,
                "queue_maxsize": 256,
                "worker_started": True,
                "worker_alive": True,
                "circuit_open_seconds": 0,
                "logger_error": None,
            }
        ),
        encoding="utf-8",
    )

    check = health._discord_check()

    assert check.status == "ok"
    assert check.detail["unexercised_roles"] == ["historical"]
