"""Test isolation: no test should ever read or write real runtime state
(log file, PID file, heartbeat, caches) — everything routes to a per-test
tmp_path instead.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_runtime_paths(tmp_path, monkeypatch):
    import src.configuration as cfg

    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr(cfg, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(cfg, "LOG_DIR", runtime_dir / "logs")
    monkeypatch.setattr(cfg, "RUN_DIR", runtime_dir / "run")
    monkeypatch.setattr(cfg, "CACHE_DIR", runtime_dir / "cache")
    monkeypatch.setattr(cfg, "SPOOL_DIR", runtime_dir / "spool")
    monkeypatch.setattr(cfg, "SUPERVISOR_PID", runtime_dir / "run" / "supervisor.pid")
    monkeypatch.setattr(cfg, "SUPERVISOR_STOP", runtime_dir / "run" / "supervisor.stop")
    monkeypatch.setattr(cfg, "SERVICE_HEARTBEAT", runtime_dir / "run" / "service_heartbeat.json")
    monkeypatch.setattr(cfg, "TOKEN_CACHE", runtime_dir / "cache" / "ctrader_ftmo_oauth.json")
    monkeypatch.setattr(cfg, "SPOOL_DB", runtime_dir / "spool" / "tick_overflow.db")
    monkeypatch.setattr(cfg, "INCIDENT_STATE", runtime_dir / "cache" / "tick_health_incident_state.json")
    monkeypatch.setattr(cfg, "LOG_FILE", runtime_dir / "logs" / "tick_engine.log")
