"""SQL Server connection primitives for the data provider."""

from __future__ import annotations

import time

import pyodbc

from core_engine.logkit.formatters import operation_line
from core_engine.logkit.factory import get_logger
from core_engine.settings import DATA_WAREHOUSE_LOG, DB, build_conn_str

logger = get_logger("data_warehouse", str(DATA_WAREHOUSE_LOG), rotating=True, console=False, utc=True)


class DatabaseWriteError(RuntimeError):
    """Raised when a staging or ETL write fails and the caller must not continue silently."""


_DB_RETRY_COUNT = DB.retry_count
_DB_RETRY_DELAY = DB.retry_delay_sec

# core_engine.warehouse.connection.verify_database_contract() checks this
# against DWH.usp_LoadDirect's DPContractVersion extended property (set by
# scripts/sql/04_business_objects.sql / 08_migration_usp_loaddirect_v2.sql).
# Bump both together whenever usp_LoadDirect's shape changes.
EXPECTED_CONTRACT_VERSION = "3"


def get_connection() -> pyodbc.Connection:
    """Return a live pyodbc connection with short transient retry.

    Applies a per-statement command timeout and SET LOCK_TIMEOUT so a
    hung statement or lock wait cannot block a worker thread (or shutdown)
    indefinitely - see settings.DB.command_timeout_seconds/lock_timeout_ms.
    """
    last_err: Exception = RuntimeError("unreachable")
    for attempt in range(1, _DB_RETRY_COUNT + 1):
        try:
            conn = pyodbc.connect(build_conn_str(), timeout=DB.health_timeout_seconds)
            conn.timeout = DB.command_timeout_seconds
            try:
                conn.execute(f"SET LOCK_TIMEOUT {int(DB.lock_timeout_ms)}")
            except pyodbc.Error:
                pass
            return conn
        except pyodbc.Error as exc:
            last_err = exc
            logger.warning(
                "WAREHOUSE | database_connection | SQL Server | connect | attempt=%d/%d | result=failed_retrying | reason=%s",
                attempt,
                _DB_RETRY_COUNT,
                exc,
            )
            if attempt < _DB_RETRY_COUNT:
                time.sleep(_DB_RETRY_DELAY)
    logger.error(
        "WAREHOUSE | database_connection | SQL Server | connect | attempts=%d | result=failed | reason=%s",
        _DB_RETRY_COUNT,
        last_err,
    )
    raise last_err


def test_connection() -> bool:
    """Operator-facing SQL health check."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA IN ('SEN','DWH','MART')
            """
        )
        count = cursor.fetchone()[0]
        print(operation_line("DATABASE", "SQL Server connection ready", tables=count, schemas="SEN,DWH,MART", result="ready"))
        return True
    except Exception as exc:
        print(operation_line("DATABASE", "SQL Server connection failed", reason=exc, result="failed"))
        return False
    finally:
        if conn is not None:
            conn.close()


def verify_database_contract() -> dict:
    """Check the stored-procedure and advisory-lock contracts required by writers.

    Reads the DPContractVersion extended property set by
    scripts/sql/04_business_objects.sql (or the standalone
    08_migration_usp_loaddirect_v2.sql controlled-deploy script) and
    compares it to EXPECTED_CONTRACT_VERSION. A missing property means the
    deployed procedure predates contract tagging entirely (the exact
    drift found in the round-2 audit: an INSERT-only procedure with no
    result set, which breaks every run_etl_direct() call with "No
    results. Previous SQL was not a query.").

    Never raises - callers decide whether a mismatch is fatal. A
    connection failure is reported as its own non-ok reason distinct from
    a version mismatch, since the caller may want to treat "database
    unreachable" and "database reachable but running the wrong contract"
    differently.
    """
    try:
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT CAST(value AS NVARCHAR(50))
                FROM sys.extended_properties
                WHERE major_id = OBJECT_ID('DWH.usp_LoadDirect')
                  AND minor_id = 0
                  AND class = 1
                  AND name = 'DPContractVersion'
                """
            )
            row = cursor.fetchone()
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM sys.columns
                WHERE object_id = OBJECT_ID('SEN.ActiveTask')
                  AND name IN ('OwnerId', 'Fence')
                """
            )
            lock_row = cursor.fetchone()
        finally:
            conn.close()
    except Exception as exc:
        return {
            "ok": False,
            "object": "DWH.usp_LoadDirect",
            "version": None,
            "expected_version": EXPECTED_CONTRACT_VERSION,
            "reason": f"contract check could not run: {exc}",
        }

    version = str(row[0]) if row and row[0] is not None else None
    lock_columns = int(lock_row[0] or 0) if lock_row else 0
    ok = version == EXPECTED_CONTRACT_VERSION and lock_columns == 2
    if ok:
        reason = None
    elif version is None:
        reason = (
            "DWH.usp_LoadDirect has no DPContractVersion property - it predates "
            "contract tagging and is almost certainly the stale INSERT-only shape. "
            "Run scripts/sql/08_migration_usp_loaddirect_v2.sql through a controlled "
            "deploy before resuming writes."
        )
    elif version != EXPECTED_CONTRACT_VERSION:
        reason = (
            f"DWH.usp_LoadDirect contract version mismatch: found {version}, "
            f"expected {EXPECTED_CONTRACT_VERSION}."
        )
    else:
        reason = (
            "SEN.ActiveTask lock-fencing contract is missing or partial: "
            f"found {lock_columns}/2 required columns (OwnerId, Fence). "
            "Run scripts/sql/09_migration_lock_fencing.sql through a controlled deploy."
        )
    return {
        "ok": ok,
        "object": "DWH.usp_LoadDirect",
        "version": version,
        "expected_version": EXPECTED_CONTRACT_VERSION,
        "lock_fencing_columns": lock_columns,
        "reason": reason,
    }
