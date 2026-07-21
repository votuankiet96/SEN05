"""Controlled SQL Server deployment operations for VM-DP6.

This helper deliberately uses the production Python/pyodbc stack so the GO
promotion does not depend on git.exe, sqlcmd.exe, Invoke-Sqlcmd, or SSMS being
installed on the VM. It never reads or prints application credentials; VM-DP6
uses the approved Windows trusted connection to localhost.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pyodbc


_GO_LINE = re.compile(r"^\s*GO\s*(?:--.*)?$", re.IGNORECASE)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")
_FULL_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(message: str) -> None:
    print(f"{utc_now()} | {message}", flush=True)


def split_go_batches(sql: str) -> list[str]:
    batches: list[str] = []
    current: list[str] = []
    for line in sql.splitlines():
        if _GO_LINE.match(line):
            batch = "\n".join(current).strip()
            if batch:
                batches.append(batch)
            current = []
        else:
            current.append(line)
    batch = "\n".join(current).strip()
    if batch:
        batches.append(batch)
    return batches


def render_sqlcmd_variables(sql: str, *, deployment_commit: str) -> str:
    if not _FULL_COMMIT.fullmatch(deployment_commit):
        raise ValueError("deployment_commit must be a full lowercase git SHA")
    rendered = sql.replace("$(DeploymentCommit)", deployment_commit)
    unresolved = re.findall(r"\$\([A-Za-z0-9_]+\)", rendered)
    if unresolved:
        raise ValueError(f"unresolved SQLCMD variable(s): {sorted(set(unresolved))}")
    return rendered


def connection_string(*, server: str, database: str, driver: str) -> str:
    return (
        f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};"
        "Trusted_Connection=yes;Encrypt=no;TrustServerCertificate=yes;"
        "APP=dp-program-controlled-deploy;"
    )


def connect(*, server: str, database: str, driver: str) -> pyodbc.Connection:
    return pyodbc.connect(
        connection_string(server=server, database=database, driver=driver),
        autocommit=True,
        timeout=30,
    )


def drain_results(cursor: pyodbc.Cursor) -> list[list[object]]:
    result_sets: list[list[object]] = []
    while True:
        if cursor.description:
            rows = [list(row) for row in cursor.fetchall()]
            result_sets.append(rows)
        messages = list(getattr(cursor, "messages", None) or [])
        for _state, message in messages:
            if message:
                emit(f"SQL message: {str(message).strip()}")
        try:
            if not cursor.nextset():
                break
        except pyodbc.ProgrammingError:
            break
    return result_sets


def execute_batch(conn: pyodbc.Connection, sql: str) -> list[list[object]]:
    cursor = conn.cursor()
    cursor.timeout = 0
    cursor.execute(sql)
    return drain_results(cursor)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def validate_database(database: str) -> str:
    if not _SAFE_IDENTIFIER.fullmatch(database):
        raise ValueError(f"unsafe database identifier: {database!r}")
    return database


def backup_database(args: argparse.Namespace) -> int:
    database = validate_database(args.database)
    conn = connect(server=args.server, database="master", driver=args.driver)
    try:
        cursor = conn.cursor()
        row = cursor.execute(
            "SELECT CONVERT(nvarchar(4000), SERVERPROPERTY('InstanceDefaultBackupPath'))"
        ).fetchone()
        if not row or not row[0]:
            raise RuntimeError("SQL Server did not report InstanceDefaultBackupPath")
        backup_root = Path(str(row[0]))
        backup_file = backup_root / f"{database}_GO_{args.stamp}_{args.short_commit}.bak"
        escaped = str(backup_file).replace("'", "''")

        emit(f"backup started | database={database} | file={backup_file}")
        execute_batch(
            conn,
            f"BACKUP DATABASE [{database}] TO DISK = N'{escaped}' "
            "WITH COPY_ONLY, CHECKSUM, COMPRESSION, INIT, STATS = 10;",
        )
        emit("backup completed; RESTORE VERIFYONLY started")
        execute_batch(conn, f"RESTORE VERIFYONLY FROM DISK = N'{escaped}' WITH CHECKSUM;")
        emit("RESTORE VERIFYONLY completed")

        payload = {
            "result": "verified",
            "database": database,
            "backup_file": str(backup_file),
            "copy_only": True,
            "checksum": True,
            "verified_at_utc": utc_now(),
        }
        write_json(Path(args.output), payload)
        print(json.dumps(payload), flush=True)
        return 0
    finally:
        conn.close()


def migrate_database(args: argparse.Namespace) -> int:
    database = validate_database(args.database)
    conn = connect(server=args.server, database=database, driver=args.driver)
    applied: list[str] = []
    try:
        for raw_path in args.files:
            path = Path(raw_path).resolve()
            sql = render_sqlcmd_variables(
                path.read_text(encoding="utf-8-sig"), deployment_commit=args.commit
            )
            batches = split_go_batches(sql)
            emit(f"migration started | file={path.name} | batches={len(batches)}")
            for index, batch in enumerate(batches, start=1):
                emit(f"migration batch | file={path.name} | batch={index}/{len(batches)}")
                result_sets = execute_batch(conn, batch)
                for rows in result_sets:
                    for row in rows:
                        emit(f"SQL result | {row}")
            applied.append(path.name)
            emit(f"migration completed | file={path.name}")
        payload = {"result": "migrated", "files": applied, "completed_at_utc": utc_now()}
        write_json(Path(args.output), payload)
        print(json.dumps(payload), flush=True)
        return 0
    finally:
        conn.close()


def contract_status(args: argparse.Namespace) -> int:
    database = validate_database(args.database)
    conn = connect(server=args.server, database=database, driver=args.driver)
    try:
        cursor = conn.cursor()
        row = cursor.execute(
            """
            SELECT
              CAST(ep.value AS varchar(20)) AS contract_version,
              (SELECT COUNT(*) FROM sys.columns
               WHERE object_id=OBJECT_ID('SEN.ActiveTask')
                 AND name IN ('OwnerId','Fence')) AS lock_fencing_columns,
              (SELECT COUNT(*) FROM SEN.OHLCV_UnsupportedCalendar
               WHERE SourceTable='SEN.TF_D1' AND SymbolID=8
                 AND Reason='unsupported_calendar_date_before_2008') AS archived_unsupported_rows,
              (SELECT COUNT(*) FROM SEN.TF_D1 s
               LEFT JOIN DWH.Dim_Date d ON d.FullDate=CAST(s.BarTime AS date)
               WHERE s.SymbolID=8 AND d.DateKey IS NULL) AS unsupported_staging_rows
            FROM sys.extended_properties ep
            WHERE ep.major_id=OBJECT_ID('DWH.usp_LoadDirect')
              AND ep.minor_id=0 AND ep.name='DPContractVersion'
            """
        ).fetchone()
        if row is None:
            raise RuntimeError("DWH.usp_LoadDirect contract property is missing")
        payload = {
            "contract_version": str(row[0]) if row[0] is not None else None,
            "lock_fencing_columns": int(row[1]),
            "archived_unsupported_rows": int(row[2]),
            "unsupported_staging_rows": int(row[3]),
            "checked_at_utc": utc_now(),
        }
        write_json(Path(args.output), payload)
        print(json.dumps(payload), flush=True)
        return 0
    finally:
        conn.close()


def fact_watermark(args: argparse.Namespace) -> int:
    database = validate_database(args.database)
    conn = connect(server=args.server, database=database, driver=args.driver)
    try:
        row = conn.cursor().execute(
            "SELECT COUNT_BIG(*), MAX(BarTime) FROM DWH.Fact_OHLCV"
        ).fetchone()
        payload = {
            "fact_rows": int(row[0]),
            "max_bar_time_utc": str(row[1]) if row[1] is not None else None,
            "checked_at_utc": utc_now(),
        }
        write_json(Path(args.output), payload)
        print(json.dumps(payload), flush=True)
        return 0
    finally:
        conn.close()


def restore_database(args: argparse.Namespace) -> int:
    database = validate_database(args.database)
    backup_file = Path(args.backup_file)
    escaped = str(backup_file).replace("'", "''")
    conn = connect(server=args.server, database="master", driver=args.driver)
    try:
        emit(f"rollback restore started | database={database} | file={backup_file}")
        try:
            execute_batch(
                conn,
                f"ALTER DATABASE [{database}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
                f"RESTORE DATABASE [{database}] FROM DISK = N'{escaped}' WITH REPLACE, CHECKSUM; "
                f"ALTER DATABASE [{database}] SET MULTI_USER;",
            )
        except Exception:
            try:
                execute_batch(conn, f"ALTER DATABASE [{database}] SET MULTI_USER;")
            except Exception:
                pass
            raise
        emit("rollback restore completed")
        return 0
    finally:
        conn.close()


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--server", default="localhost")
    parser.add_argument("--database", default="SEN05_AutoTrading")
    parser.add_argument("--driver", default="ODBC Driver 18 for SQL Server")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup")
    add_common(backup)
    backup.add_argument("--stamp", required=True)
    backup.add_argument("--short-commit", required=True)
    backup.add_argument("--output", required=True)
    backup.set_defaults(func=backup_database)

    migrate = sub.add_parser("migrate")
    add_common(migrate)
    migrate.add_argument("--commit", required=True)
    migrate.add_argument("--files", nargs="+", required=True)
    migrate.add_argument("--output", required=True)
    migrate.set_defaults(func=migrate_database)

    contract = sub.add_parser("contract")
    add_common(contract)
    contract.add_argument("--output", required=True)
    contract.set_defaults(func=contract_status)

    watermark = sub.add_parser("watermark")
    add_common(watermark)
    watermark.add_argument("--output", required=True)
    watermark.set_defaults(func=fact_watermark)

    restore = sub.add_parser("restore")
    add_common(restore)
    restore.add_argument("--backup-file", required=True)
    restore.set_defaults(func=restore_database)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        emit(f"FAILED | {type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    sys.exit(main())
