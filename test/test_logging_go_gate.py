"""Fault-oriented GO-gate tests for process logging and retention."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core_engine.util import health
from core_engine.util.logkit.paths import process_scoped_log_path, register_log_sink
from core_engine.util.supervisor.engine import BackendSupervisor
from core_engine.util.supervisor.process_control import ManagedProcess


def test_process_scoped_path_is_unique_per_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("DP_PROCESS_ROLE", "live")
    actual = process_scoped_log_path(tmp_path / "component.log")
    assert actual.name == f"component.live.{os.getpid()}.log"


def test_sink_registry_records_physical_and_logical_path(tmp_path, monkeypatch):
    monkeypatch.setenv("DP_PROCESS_ROLE", "live")
    monkeypatch.setenv("DP_APP_ROOT", str(tmp_path))
    physical = tmp_path / "runtime" / "logs" / f"probe.live.{os.getpid()}.log"
    physical.parent.mkdir(parents=True)
    physical.touch()

    assert register_log_sink(physical, logical_path=physical.parent / "probe.log")

    manifest = json.loads(
        (
            tmp_path
            / "runtime"
            / "run"
            / "log_sinks"
            / f"live.{os.getpid()}.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["pid"] == os.getpid()
    assert manifest["role"] == "live"
    assert manifest["sinks"][0]["physical_path"] == str(physical.resolve())
    assert manifest["sinks"][0]["logical_path"].endswith("probe.log")


def test_unhandled_import_process_exception_is_persisted_before_logger(tmp_path):
    env = os.environ.copy()
    env["DP_APP_ROOT"] = str(tmp_path)
    env["DP_PROCESS_ROLE"] = "live"
    source_root = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = source_root + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [sys.executable, "-c", "import core_engine; raise RuntimeError('early-crash-probe')"],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode != 0
    crash_files = list((tmp_path / "runtime" / "logs" / "system").glob("crash.live.*.log"))
    assert len(crash_files) == 1
    text = crash_files[0].read_text(encoding="utf-8")
    assert "RuntimeError: early-crash-probe" in text


def test_two_same_role_processes_never_share_a_rotating_file(tmp_path):
    env = os.environ.copy()
    env["DP_APP_ROOT"] = str(tmp_path)
    env["DP_PROCESS_ROLE"] = "live"
    source_root = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = source_root + os.pathsep + env.get("PYTHONPATH", "")
    code = (
        "from core_engine.util.logkit.factory import get_logger;"
        "from pathlib import Path;"
        f"p=Path({str(tmp_path)!r})/'runtime'/'logs'/'probe.log';"
        "log=get_logger('probe-'+str(__import__('os').getpid()),str(p),rotating=True);"
        "log.info('process-unique-probe')"
    )
    children = [
        subprocess.Popen(
            [sys.executable, "-c", code],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    for child in children:
        _stdout, stderr = child.communicate(timeout=20)
        assert child.returncode == 0, stderr

    logs = list((tmp_path / "runtime" / "logs").glob("probe.live.*.log"))
    assert len(logs) == 2
    assert all("process-unique-probe" in path.read_text(encoding="utf-8") for path in logs)
    assert not list((tmp_path / "runtime" / "logs").glob("*.active.*.log"))


def test_child_stderr_pump_drains_tail_before_close(tmp_path):
    supervisor = BackendSupervisor(live_enabled=False, schedule_enabled=False)
    managed = ManagedProcess("live")
    lifecycle = tmp_path / "subprocess_debug.log"

    supervisor._spawn(
        managed,
        [
            sys.executable,
            "-c",
            "import sys; print('stderr-first', file=sys.stderr); print('stderr-tail', file=sys.stderr)",
        ],
        lifecycle,
    )
    assert managed.process is not None
    managed.process.wait(timeout=20)
    managed.poll()

    stderr_files = list(tmp_path.glob("subprocess_stderr.live.*.log"))
    assert len(stderr_files) == 1
    output = stderr_files[0].read_text(encoding="utf-8")
    assert "stderr-first" in output
    assert "stderr-tail" in output
    assert managed.stderr_thread is None
    assert managed.stderr_handle is None


def test_sink_setup_failure_does_not_leave_spawned_child_orphaned(
    tmp_path, monkeypatch
):
    import core_engine.util.supervisor.engine as engine

    created: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    def recording_popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        created.append(child)
        return child

    monkeypatch.setattr(engine.subprocess, "Popen", recording_popen)
    monkeypatch.setattr(
        engine,
        "ResilientRotatingFileHandler",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("sink denied")),
    )
    supervisor = BackendSupervisor(live_enabled=False, schedule_enabled=False)

    with pytest.raises(OSError, match="sink denied"):
        supervisor._spawn(
            ManagedProcess("live"),
            [sys.executable, "-c", "import time; time.sleep(30)"],
            tmp_path / "subprocess_debug.log",
        )

    assert len(created) == 1
    assert created[0].poll() is not None


def test_retention_covers_rotations_text_and_reports_delete_failure(
    tmp_path, monkeypatch
):
    log_dir = tmp_path / "logs"
    run_dir = tmp_path / "run"
    spool_dir = tmp_path / "spool"
    for directory in (log_dir, run_dir, spool_dir):
        directory.mkdir()
    targets = [
        log_dir / "worker.log.1",
        log_dir / "task.out",
        log_dir / "trace.txt",
    ]
    for path in targets:
        path.write_text("old", encoding="utf-8")
        os.utime(path, (1, 1))
    blocked = log_dir / "blocked.log.2"
    blocked.write_text("old", encoding="utf-8")
    os.utime(blocked, (1, 1))

    monkeypatch.setattr(health, "LOG_DIR", log_dir)
    monkeypatch.setattr(health, "RUN_DIR", run_dir)
    monkeypatch.setattr(health, "SPOOL_DIR", spool_dir)
    monkeypatch.setattr(health, "_active_runtime_roles", lambda: {})
    real_unlink = Path.unlink

    def fault_unlink(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError("fault-injected lock")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fault_unlink)
    result = health.cleanup_old_runtime_files(days=1)

    assert all(not path.exists() for path in targets)
    assert blocked.exists()
    assert result["failed"]
    assert "fault-injected lock" in result["failed"][0]["reason"]
    assert (run_dir / "log_retention_state.json").exists()

