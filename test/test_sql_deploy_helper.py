from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "scripts" / "windows_task" / "sql_deploy.py"
SPEC = importlib.util.spec_from_file_location("dp_sql_deploy", HELPER_PATH)
assert SPEC and SPEC.loader
sql_deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sql_deploy)


def test_split_go_batches_handles_case_whitespace_and_comments():
    sql = "SELECT 1;\nGO\nSELECT 2;\n go -- delimiter\nSELECT 3;\n"
    assert sql_deploy.split_go_batches(sql) == ["SELECT 1;", "SELECT 2;", "SELECT 3;"]


def test_render_sqlcmd_variables_requires_full_commit_and_leaves_none_unresolved():
    commit = "a" * 40
    rendered = sql_deploy.render_sqlcmd_variables(
        "DECLARE @c varchar(40)='$(DeploymentCommit)';", deployment_commit=commit
    )
    assert commit in rendered
    assert "$(" not in rendered

    with pytest.raises(ValueError):
        sql_deploy.render_sqlcmd_variables("SELECT 1", deployment_commit="short")


def test_all_production_migrations_are_batch_parseable_without_sqlcmd():
    commit = "b" * 40
    for name in (
        "10_migration_usp_loaddirect_v3_date_fence.sql",
        "09_migration_lock_fencing.sql",
        "11_migration_archive_us500_d1_unsupported_calendar.sql",
    ):
        source = (ROOT / "scripts" / "sql" / name).read_text(encoding="utf-8-sig")
        rendered = sql_deploy.render_sqlcmd_variables(source, deployment_commit=commit)
        batches = sql_deploy.split_go_batches(rendered)
        assert batches, name
        assert all(batch.strip() for batch in batches)
        assert not any(line.strip().upper() == "GO" for batch in batches for line in batch.splitlines())


def test_trusted_connection_string_contains_no_password():
    value = sql_deploy.connection_string(
        server="localhost", database="SEN05_AutoTrading", driver="ODBC Driver 18 for SQL Server"
    )
    assert "Trusted_Connection=yes" in value
    assert "PWD=" not in value
    assert "UID=" not in value


def test_execute_batch_does_not_require_cursor_timeout_attribute():
    class ProductionShapedCursor:
        description = None
        messages = []

        def __init__(self):
            self.executed = []

        def execute(self, sql):
            self.executed.append(sql)
            return self

        def nextset(self):
            return False

    class Connection:
        def __init__(self):
            self.value = ProductionShapedCursor()

        def cursor(self):
            return self.value

    conn = Connection()
    assert sql_deploy.execute_batch(conn, "SELECT 1") == []
    assert conn.value.executed == ["SELECT 1"]
    assert not hasattr(conn.value, "timeout")


def test_backup_avoids_odbc_stats_result_stream_and_gates_on_file():
    source = HELPER_PATH.read_text(encoding="utf-8")
    assert "STATS = 10" not in source
    assert "backup_file.is_file()" in source
    assert "backup_file.stat().st_size > 0" in source
    assert "RESTORE VERIFYONLY" in source


def test_backup_supports_alternate_directory_and_cleans_only_unverified_artifact():
    parser = sql_deploy.build_parser()
    args = parser.parse_args(
        [
            "backup",
            "--stamp",
            "20260721T000000Z",
            "--short-commit",
            "a" * 12,
            "--backup-directory",
            r"D:\\SQLBackup",
            "--output",
            "manifest.json",
        ]
    )
    assert args.backup_directory == r"D:\\SQLBackup"

    source = HELPER_PATH.read_text(encoding="utf-8")
    assert "not backup_verified and backup_file.is_file()" in source
    assert "backup_file.unlink()" in source
