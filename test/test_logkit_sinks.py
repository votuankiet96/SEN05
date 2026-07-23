"""Fault, concurrency, rotation, and retention tests for production sinks."""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from core_engine.util.logkit import flush_logs
from core_engine.util.logkit import sink


def _settings(**overrides):
    values = {
        "queue_size": 1000,
        "queue_wait_ms": 5,
        "max_file_mb": 1,
        "retention_days": 30,
        "disk_budget_mb": 100,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_two_processes_share_one_canonical_file_without_lost_lines(tmp_path):
    env = os.environ.copy()
    env["DP_APP_ROOT"] = str(tmp_path)
    env["DP_PROCESS_ROLE"] = "live"
    env["DP_DISABLE_CONSOLE_LOG"] = "1"
    source_root = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = source_root + os.pathsep + env.get("PYTHONPATH", "")
    code = (
        "import os;"
        "from core_engine.util.logkit import get_logger,flush_logs,operation_line;"
        "log=get_logger('probe-'+str(os.getpid()),stream='live',console=False);"
        "[log.info(operation_line('LIVE','Concurrent write',sequence=i,pid=os.getpid())) for i in range(100)];"
        "assert flush_logs(10)"
    )
    children = [
        subprocess.Popen(
            [sys.executable, "-c", code],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    for child in children:
        stdout, stderr = child.communicate(timeout=30)
        assert child.returncode == 0, stdout + stderr

    path = tmp_path / "runtime" / "logs" / "live.log"
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if "Concurrent write" in line]
    assert len(lines) == 200
    assert not list((tmp_path / "runtime" / "logs").glob("*.active.*"))


def test_rotation_moves_closed_file_to_compressed_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(sink, "LOG_ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(sink, "LOG_LOCK_DIR", tmp_path / "locks")
    monkeypatch.setattr(sink, "LOGGING", _settings(max_file_mb=1))
    target = tmp_path / "live.log"
    target.write_bytes(b"x" * (1024 * 1024 - 10))

    sink._append_line(target, "y" * 100, durable=False)

    archives = list((tmp_path / "archive").rglob("live.*.log.gz"))
    assert len(archives) == 1
    with gzip.open(archives[0], "rt", encoding="utf-8") as handle:
        assert handle.read().startswith("x")
    assert "y" * 100 in target.read_text(encoding="utf-8")


def test_sink_failure_uses_emergency_file_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(sink, "LOG_EMERGENCY_DIR", tmp_path / "emergency")
    sink._emergency_line("record", PermissionError("denied"))
    files = list((tmp_path / "emergency").glob("*.log"))
    assert len(files) == 1
    assert "denied" in files[0].read_text(encoding="utf-8")


def test_retention_removes_expired_archives_and_persists_state(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    run = tmp_path / "run"
    locks = run / "locks"
    archive.mkdir()
    old = archive / "old.log.gz"
    recent = archive / "recent.log.gz"
    old.write_text("old", encoding="utf-8")
    recent.write_text("recent", encoding="utf-8")
    os.utime(old, (1, 1))
    monkeypatch.setattr(sink, "LOG_ARCHIVE_DIR", archive)
    monkeypatch.setattr(sink, "RUN_DIR", run)
    monkeypatch.setattr(sink, "LOG_LOCK_DIR", locks)
    monkeypatch.setattr(sink, "LOGGING", _settings(retention_days=1))
    monkeypatch.setattr(sink, "_STREAM_PATHS", {})

    result = sink.cleanup_archives()

    assert not old.exists()
    assert recent.exists()
    assert not result["failed"]
    state = json.loads((run / "log_retention_state.json").read_text(encoding="utf-8"))
    assert str(old) in state["deleted"]


def test_retention_reports_delete_failure_instead_of_claiming_success(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    run = tmp_path / "run"
    archive.mkdir()
    old = archive / "locked.log.gz"
    old.write_text("old", encoding="utf-8")
    os.utime(old, (1, 1))
    monkeypatch.setattr(sink, "LOG_ARCHIVE_DIR", archive)
    monkeypatch.setattr(sink, "RUN_DIR", run)
    monkeypatch.setattr(sink, "LOG_LOCK_DIR", run / "locks")
    monkeypatch.setattr(sink, "LOGGING", _settings(retention_days=1))
    monkeypatch.setattr(sink, "_STREAM_PATHS", {})
    original_unlink = Path.unlink

    def fail_one(path, *args, **kwargs):
        if Path(path) == old:
            raise PermissionError("rotation reader still owns the file")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_one)
    result = sink.cleanup_archives()

    assert old.exists()
    assert result["failed"]
    assert result["failed"][0]["path"] == str(old)


def test_global_manager_flushes_without_leaving_queue_work():
    assert flush_logs(5)


def test_register_creates_source_and_alert_files_before_first_warning(tmp_path, monkeypatch):
    source = tmp_path / "system.log"
    alerts = tmp_path / "alerts.log"
    monkeypatch.setattr(sink, "LOG_LOCK_DIR", tmp_path / "locks")
    monkeypatch.setattr(sink, "RUN_DIR", tmp_path / "run")
    monkeypatch.setattr(
        sink,
        "_STREAM_PATHS",
        {"system": source, "alerts": alerts},
    )
    manager = sink.SinkManager()
    try:
        manager.register("system")
        assert source.is_file()
        assert alerts.is_file()
    finally:
        manager.close()


def test_log_registry_uses_retrying_atomic_writer(tmp_path, monkeypatch):
    writes = []
    monkeypatch.setattr(sink, "RUN_DIR", tmp_path)
    monkeypatch.setattr(sink, "process_role", lambda: "live")
    monkeypatch.setattr(
        sink,
        "atomic_write_json",
        lambda path, payload: writes.append((path, payload)),
    )

    sink._register_streams({"live", "alerts"})

    assert len(writes) == 1
    assert writes[0][0] == tmp_path / "log_sinks" / f"live.{os.getpid()}.json"
    assert writes[0][1]["streams"] == ["alerts", "live"]
