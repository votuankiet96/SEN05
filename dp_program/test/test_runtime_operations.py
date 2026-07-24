from __future__ import annotations

from pathlib import Path

from core_engine.other.exit_codes import EXIT_CANCELLED
from core_engine.util.cli import _terminal_completion_status


ROOT = Path(__file__).resolve().parents[1]


def test_pytest_never_collects_runtime_artifacts():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '[tool.pytest.ini_options]' in pyproject
    assert 'testpaths = ["test"]' in pyproject
    assert '"runtime"' in pyproject


def test_runbook_names_scheduled_task_not_scm_commands():
    runbook = (ROOT / "docs" / "OPERATOR_RUNBOOK.md").read_text(encoding="utf-8")
    assert "SEN05 DP Program 24x7" in runbook
    assert "Start-ScheduledTask" in runbook
    assert "Get-Service SEN05DataProvider" not in runbook
    assert "Start-Service SEN05DataProvider" not in runbook
    assert "Stop-Service SEN05DataProvider" not in runbook


def test_launcher_uses_canonical_log_streams_and_supported_queries():
    launcher = (ROOT / "scripts" / "launcher" / "dp_launcher.ps1").read_text(
        encoding="utf-8"
    )

    for stream in ("system.log", "live.log", "historical.log", "alerts.log"):
        assert f"runtime\\logs\\{stream}" in launcher
    for stale_path in (
        "runtime\\logs\\system\\",
        "runtime\\logs\\operation\\",
        "activity*.log",
        "auth*.log",
        "discord*.log",
        "subprocess_*.log",
    ):
        assert stale_path not in launcher
    assert '"logs", "status"' in launcher
    assert '"logs", "find"' in launcher
    assert '"logs", "risks"' in launcher
    assert '"logs", "trace"' in launcher


def test_read_only_health_findings_are_logged_as_attention_not_failures():
    status, message = _terminal_completion_status(1, ["logs", "risks", "--since", "30m"])

    assert status == "warning"
    assert "needs attention" in message
    assert _terminal_completion_status(1, ["doctor"])[0] == "warning"
    assert _terminal_completion_status(1, ["reconcile-fact"])[0] == "warning"
    assert _terminal_completion_status(1, ["reconcile-fact", "--apply"])[0] == "failed"


def test_terminal_completion_status_preserves_success_stop_and_real_failure():
    assert _terminal_completion_status(0, ["settings"])[0] == "completed"
    assert _terminal_completion_status(EXIT_CANCELLED, ["stop"])[0] == "stopped"
    assert _terminal_completion_status(1, ["live"])[0] == "failed"
