from __future__ import annotations

import json
from pathlib import Path

from core_engine.util import health
from core_engine.settings import operational


ROOT = Path(__file__).resolve().parents[1]


def test_release_identity_accepts_matching_manifest(monkeypatch, tmp_path):
    manifest = {
        "release_commit": "a" * 40,
        "release_directory": str(tmp_path),
        "deployed_at_utc": "2026-07-21T00:00:00+00:00",
        "package_install": "non-editable, release-local virtualenv",
    }
    (tmp_path / "RELEASE_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(health, "APP_ROOT", tmp_path)

    check = health._release_identity_check()

    assert check.status == "ok"
    assert check.detail["release_commit"] == "a" * 40


def test_pytest_never_collects_deployment_evidence_copies():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '[tool.pytest.ini_options]' in pyproject
    assert 'testpaths = ["test"]' in pyproject
    assert '"runtime"' in pyproject


def test_release_identity_rejects_manifest_for_different_directory(monkeypatch, tmp_path):
    manifest = {"release_commit": "b" * 40, "release_directory": str(tmp_path / "elsewhere")}
    (tmp_path / "RELEASE_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(health, "APP_ROOT", tmp_path)

    assert health._release_identity_check().status == "fail"


def test_installed_package_discovers_release_root_from_manifest(tmp_path):
    module_file = tmp_path / ".venv" / "Lib" / "site-packages" / "core_engine" / "settings" / "operational.py"
    module_file.parent.mkdir(parents=True)
    module_file.write_text("# stand-in", encoding="utf-8")
    (tmp_path / "RELEASE_MANIFEST.json").write_text("{}", encoding="utf-8")

    assert operational._discover_app_root(module_file) == tmp_path


def test_vm_promotion_script_contains_all_destructive_safety_gates():
    script = (ROOT / "scripts" / "windows_task" / "promote_vm_dp6.ps1").read_text(encoding="utf-8")
    sql_helper = (ROOT / "scripts" / "windows_task" / "sql_deploy.py").read_text(encoding="utf-8")
    expected_in_order = [
        "database_backup_and_verify",
        "core_engine', 'stop'",
        "12_migration_usp_loaddirect_v4_bounded_plan.sql",
        "09_migration_lock_fencing.sql",
        "11_migration_archive_us500_d1_unsupported_calendar.sql",
        "reconcile-fact --apply --json",
        "deploy_release.ps1",
    ]
    positions = [script.index(token) for token in expected_in_order]
    assert positions == sorted(positions)
    assert "COPY_ONLY, CHECKSUM, COMPRESSION" in sql_helper
    assert "RESTORE VERIFYONLY" in sql_helper
    assert "Wait-TaskNotRunning -TimeoutSec 120" in script
    assert "rollback_database_restore.log" in script
    assert "production_candidate.json" in script
    assert "Get-FileHash" in script
    assert "& git" not in script
    assert "sqlcmd" not in script.lower()
    assert "sql_deploy.py" in script
    assert "Set-Phase 'promotion_complete'" in script
    assert "${(" not in script
    assert "$shortCommit = $commit.Substring(0, 12)" in script
    assert "[string]$BackupDirectory = ''" in script
    assert "@('--backup-directory', $BackupDirectory)" in script
    assert "[switch]$SkipDatabaseBackup" in script
    assert "-SkipDatabaseBackup requires a non-empty -BackupWaiverReason" in script
    assert "skipped_by_explicit_operator_waiver" in script
    assert "rollback_database_restore_skipped" in script
    assert "left_stopped_to_prevent_old_code_running_against_migrated_schema" in script


def test_release_script_uses_exact_archive_noneditable_venv_and_scheduled_task():
    script = (ROOT / "scripts" / "windows_task" / "deploy_release.ps1").read_text(encoding="utf-8")
    assert "git" in script and "'archive'" in script
    assert "pip', 'install'" in script
    assert "pip install -e" not in script
    assert "New-ScheduledTaskAction" in script
    assert "StableRoot = 'C:\\dp_program'" in script
    assert "$current = Join-Path $StableRoot 'current'" in script
    assert "expected_live_symbols" in script
    assert "symbol_timeframe_sessions" in script
    assert "ArtifactSha256" in script
    assert "Prebuilt artifact SHA-256 mismatch" in script


def test_runbook_names_scheduled_task_not_scm_commands():
    runbook = (ROOT / "docs" / "OPERATOR_RUNBOOK.md").read_text(encoding="utf-8")
    assert "SEN05 DP Program 24x7" in runbook
    assert "Start-ScheduledTask" in runbook
    assert "Get-Service SEN05DataProvider" not in runbook
    assert "Start-Service SEN05DataProvider" not in runbook
    assert "Stop-Service SEN05DataProvider" not in runbook
