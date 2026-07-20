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


def get_connection() -> pyodbc.Connection:
    """Return a live pyodbc connection with short transient retry."""
    last_err: Exception = RuntimeError("unreachable")
    for attempt in range(1, _DB_RETRY_COUNT + 1):
        try:
            return pyodbc.connect(build_conn_str(), timeout=DB.health_timeout_seconds)
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
        conn.close()
        print(operation_line("DATABASE", "SQL Server connection ready", tables=count, schemas="SEN,DWH,MART", result="ready"))
        return True
    except Exception as exc:
        print(operation_line("DATABASE", "SQL Server connection failed", reason=exc, result="failed"))
        return False
