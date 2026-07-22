from __future__ import annotations

from pathlib import Path


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
