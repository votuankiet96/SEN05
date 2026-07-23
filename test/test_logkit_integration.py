"""Architecture, crash capture, query, and pipeline-oriented logging tests."""

from __future__ import annotations

import ast
import io
import logging
import os
import subprocess
import sys
from pathlib import Path

from core_engine.util.logkit import formatter, query


def test_unhandled_import_exception_is_captured_before_logger(tmp_path):
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
    files = list((tmp_path / "runtime" / "run" / "log_emergency").glob("crash.live.*.log"))
    assert len(files) == 1
    assert "RuntimeError: early-crash-probe" in files[0].read_text(encoding="utf-8")


def test_query_trace_and_risk_evidence(tmp_path, monkeypatch):
    live = tmp_path / "live.log"
    historical = tmp_path / "historical.log"
    system = tmp_path / "system.log"
    alerts = tmp_path / "alerts.log"

    def write(path: Path, message, level=logging.INFO):
        record = logging.LogRecord("dp.live.test", level, __file__, 1, message, (), None)
        record.dp_stream = "live"
        record.dp_component = "test"
        record.dp_role = "live"
        record.dp_context = {"correlation_id": "L-99", "batch_id": 99}
        record.dp_fields = {}
        path.write_text(
            path.read_text(encoding="utf-8") + formatter.OperatorFormatter().format(record) + "\n"
            if path.exists()
            else formatter.OperatorFormatter().format(record) + "\n",
            encoding="utf-8",
        )

    write(live, formatter.operation_line("LIVE", "Live batch started", stage="START"))
    write(live, formatter.operation_line("DATABASE", "Main data store update failed", result="failed"), logging.ERROR)
    monkeypatch.setattr(
        query,
        "_CURRENT",
        {"live": live, "historical": historical, "system": system, "alerts": alerts},
    )
    monkeypatch.setattr(query, "LOG_ARCHIVE_DIR", tmp_path / "archive")

    traced = query.find_events(correlation_id="L-99", since="24h")
    assert len(traced) == 2
    report = query.risk_report(since="24h")
    assert report["status"] == "risk"
    assert any(item["kind"] == "logged_failure" for item in report["issues"])
    evidence = next(item for item in report["issues"] if item["kind"] == "logged_failure")["evidence"]
    assert evidence["file"] == str(live)
    assert evidence["line"] == 2


def test_query_deduplicates_alert_mirror_by_event_id(tmp_path, monkeypatch):
    system = tmp_path / "system.log"
    alerts = tmp_path / "alerts.log"
    record = logging.LogRecord(
        "dp.system.test",
        logging.WARNING,
        __file__,
        1,
        formatter.operation_line("SYSTEM", "One warning"),
        (),
        None,
    )
    record.dp_stream = "system"
    record.dp_component = "test"
    record.dp_role = "supervisor"
    record.dp_context = {}
    record.dp_fields = {}
    record.dp_event_id = "mirror-1"
    line = formatter.OperatorFormatter().format(record) + "\n"
    system.write_text(line, encoding="utf-8")
    alerts.write_text(line, encoding="utf-8")
    monkeypatch.setattr(
        query,
        "_CURRENT",
        {
            "live": tmp_path / "live.log",
            "historical": tmp_path / "historical.log",
            "system": system,
            "alerts": alerts,
        },
    )
    monkeypatch.setattr(query, "LOG_ARCHIVE_DIR", tmp_path / "archive")

    assert len(query.find_events(since="24h")) == 1


def test_child_stderr_is_classified_and_persisted_in_system_log():
    from core_engine.settings import SYSTEM_LOG
    from core_engine.util.logkit import flush_logs
    from core_engine.util.supervisor.engine import _pump_child_stderr
    from core_engine.util.supervisor.process_control import ManagedProcess

    marker = f"stderr-probe-{os.getpid()}"
    managed = ManagedProcess(
        "live",
        stderr_handle=io.StringIO(
            f"{marker} diagnostic\n"
            f"WARNING: {marker} warning\n"
            f"RuntimeError: {marker} failed\n"
        ),
    )
    _pump_child_stderr(managed)
    assert flush_logs(3)
    events = [
        formatter.parse_operator_line(line)
        for line in SYSTEM_LOG.read_text(encoding="utf-8").splitlines()
        if marker in line
    ]
    assert [event["level"] for event in events if event] == [
        "INFO",
        "WARNING",
        "ERROR",
    ]


def test_only_six_production_modules_exist_in_logkit():
    root = Path(__file__).resolve().parents[1] / "src" / "core_engine" / "util" / "logkit"
    assert {path.name for path in root.glob("*.py")} == {
        "__init__.py",
        "bootstrap.py",
        "core.py",
        "formatter.py",
        "query.py",
        "sink.py",
    }


def test_domains_do_not_create_file_handlers_or_write_canonical_logs():
    source = Path(__file__).resolve().parents[1] / "src" / "core_engine"
    violations = []
    for path in source.rglob("*.py"):
        if "util/logkit" in path.as_posix():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "RotatingFileHandler(" in text or "FileHandler(" in text:
            violations.append(str(path))
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"open", "write_text", "write_bytes"}:
                continue
            if isinstance(node.func.value, ast.Name) and node.func.value.id in {
                "LIVE_LOG",
                "HISTORICAL_LOG",
                "SYSTEM_LOG",
                "ALERTS_LOG",
            }:
                violations.append(str(path))
    assert not violations
