"""The only DP Program V3 module that talks to SQL Server."""
from __future__ import annotations
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable
import pyodbc
_SQL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")
def _quoted_name(name: str) -> str:
    if not _SQL_NAME.fullmatch(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return ".".join(f"[{part}]" for part in name.split("."))
def build_connection_string(sql: dict[str, Any]) -> str:
    """Build a pyodbc connection string from resolved configuration."""
    server = sql["server"]
    if sql.get("port"):
        server = f"{server},{sql['port']}"
    parts = [f"DRIVER={{{sql['driver']}}}", f"SERVER={server}",
             f"DATABASE={sql['database']}", f"Encrypt={sql.get('encrypt', 'no')}",
             f"TrustServerCertificate={'yes' if sql.get('trust_server_certificate') else 'no'}"]
    if sql.get("username") and sql.get("password"):
        parts.extend((f"UID={sql['username']}", f"PWD={sql['password']}"))
    elif sql.get("trusted_connection", True):
        parts.append("Trusted_Connection=yes")
    else:
        raise ValueError("SQL credentials are missing and trusted_connection is disabled")
    return ";".join(parts) + ";"
def get_connection(config: dict[str, Any]) -> pyodbc.Connection:
    """Open a SQL connection with one bounded retry policy."""
    sql = config["sql_server"]
    last_error: Exception | None = None
    for attempt in range(1, int(sql["retry_count"]) + 1):
        try:
            connection = pyodbc.connect(build_connection_string(sql),
                                        timeout=int(sql["timeout_seconds"]))
            connection.timeout = int(sql["command_timeout_seconds"])
            return connection
        except pyodbc.Error as exc:
            last_error = exc
            if attempt < int(sql["retry_count"]):
                time.sleep(float(sql.get("retry_delay_seconds", 1)))
    raise ConnectionError(f"SQL Server connection failed: {last_error}") from last_error
def read_chart_rows(config: dict[str, Any], symbol: str, timeframe: str, bars: int) -> list[tuple[Any, ...]]:
    """Read recent committed Fact candles for the offline operator chart."""
    fact_table = _quoted_name(config["tables"]["fact_table"])
    connection = get_connection(config)
    try:
        cursor = connection.cursor()
        cursor.execute(
            f"""SELECT TOP (?) f.BarTime,f.[Open],f.High,f.Low,f.[Close],f.Volume
            FROM {fact_table} f JOIN DWH.Dim_Symbol s ON s.SymbolID=f.SymbolID JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID=f.TimeframeID
            WHERE s.Symbol=? AND tf.Code=? ORDER BY f.BarTime DESC""", int(bars), symbol, timeframe)
        return list(cursor.fetchall())
    finally:
        connection.close()
def _policy_epoch(config: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(config["backfill"]["policy_epoch_utc"]).astimezone(timezone.utc)
def bootstrap_state_is_current(completed_at: datetime | None, epoch: datetime) -> bool:
    """Return whether a completion timestamp proves the active backfill policy."""
    if completed_at is None:
        return False
    value = completed_at.replace(tzinfo=timezone.utc) if completed_at.tzinfo is None else completed_at
    return value.astimezone(timezone.utc) >= epoch.astimezone(timezone.utc)
def check_connection(config: dict[str, Any]) -> dict[str, Any]:
    """Return read-only schema and stored-procedure contract evidence."""
    fact_table = _quoted_name(config["tables"]["fact_table"])
    state_name = config["tables"]["backfill_state_table"]
    state_table = _quoted_name(state_name)
    procedure = config["tables"]["load_procedure"]
    connection = get_connection(config)
    try:
        cursor = connection.cursor()
        cursor.execute(f"SELECT COUNT_BIG(*), MAX(BarTime) FROM {fact_table}")
        fact_row = cursor.fetchone()
        fact_rows = int(fact_row[0])
        fact_watermark = fact_row[1]
        cursor.execute("""SELECT CAST(value AS NVARCHAR(50)) FROM sys.extended_properties
            WHERE major_id=OBJECT_ID(?) AND minor_id=0 AND class=1 AND name='DPContractVersion'""", procedure)
        row = cursor.fetchone()
        version = str(row[0]) if row and row[0] is not None else None
        cursor.execute("SELECT CASE WHEN OBJECT_ID(?, 'U') IS NULL THEN 0 ELSE 1 END", state_name)
        state_table_exists = bool(cursor.fetchone()[0])
        state_rows = 0
        bootstrap_completed_pairs = 0
        if state_table_exists:
            cursor.execute(f"""SELECT COUNT_BIG(*),SUM(CASE WHEN BootstrapCompletedAt>=? THEN 1 ELSE 0 END)
                FROM {state_table}""", _sql_timestamp(_policy_epoch(config)))
            state_row = cursor.fetchone()
            state_rows = int(state_row[0] or 0)
            bootstrap_completed_pairs = int(state_row[1] or 0)
        expected_pairs = len(config["data"]["symbols"]) * len(config["data"]["timeframes"])
        expected = str(config["sql_server"]["contract_version"])
        return {
            "ok": version == expected and state_table_exists,
            "database": config["sql_server"]["database"], "fact_rows": fact_rows,
            "fact_watermark_utc": fact_watermark, "backfill_state_table": state_name,
            "backfill_state_table_exists": state_table_exists,
            "bootstrap_state_rows_total": state_rows, "bootstrap_completed_pairs": bootstrap_completed_pairs,
            "bootstrap_remaining_pairs": max(0, expected_pairs - bootstrap_completed_pairs),
            "backfill_policy_epoch_utc": config["backfill"]["policy_epoch_utc"],
            "procedure": procedure, "contract_version": version,
            "expected_contract_version": expected,
        }
    finally:
        connection.close()
def get_pair_states(
    config: dict[str, Any],
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[tuple[int, str], dict[str, Any]]:
    """Batch-read durable policy completion and latest Fact watermarks."""
    if not pairs:
        return {}
    state_table = _quoted_name(config["tables"]["backfill_state_table"])
    fact_table = _quoted_name(config["tables"]["fact_table"])
    requested = sorted({(int(s["symbol_id"]), str(tf["code"])) for s, tf in pairs})
    connection = get_connection(config)
    try:
        cursor = connection.cursor()
        cursor.execute("CREATE TABLE #Pairs (SymbolID INT NOT NULL,TFCode VARCHAR(5) COLLATE DATABASE_DEFAULT NOT NULL,"
                       "PRIMARY KEY (SymbolID,TFCode))")
        cursor.fast_executemany = True
        cursor.executemany("INSERT INTO #Pairs (SymbolID, TFCode) VALUES (?, ?)", requested)
        cursor.execute(f"""SELECT p.SymbolID,p.TFCode,state.BootstrapCompletedAt,latest.BarTime
            FROM #Pairs p JOIN DWH.Dim_Timeframe tf ON tf.Code=p.TFCode
            LEFT JOIN {state_table} state ON state.SymbolID=p.SymbolID AND state.TimeframeID=tf.TimeframeID
            OUTER APPLY (SELECT TOP (1) f.BarTime FROM {fact_table} f WHERE f.SymbolID=p.SymbolID
            AND f.TimeframeID=tf.TimeframeID ORDER BY f.BarTime DESC) latest""")
        epoch = _policy_epoch(config)
        return {
            (int(row[0]), str(row[1])): {
                "bootstrap_complete": bootstrap_state_is_current(row[2], epoch),
                "bootstrap_completed_at": row[2],
                "latest": row[3],
            }
            for row in cursor.fetchall()
        }
    finally:
        connection.close()
def fetch_existing_candles(config: dict[str, Any], symbol_id: int, timeframe_code: str,
                           start_time: datetime, end_time: datetime, connection: pyodbc.Connection | None = None) -> dict[datetime, tuple[str | None, ...]]:
    """Read committed Fact values in an inclusive provider-observed window."""
    fact_table = _quoted_name(config["tables"]["fact_table"])
    active = connection or get_connection(config)
    try:
        cursor = active.cursor()
        cursor.execute(f"""SELECT f.BarTime,f.[Open],f.High,f.Low,f.[Close],f.Volume
            FROM {fact_table} f JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID=f.TimeframeID
            WHERE f.SymbolID=? AND tf.Code=? AND f.BarTime>=? AND f.BarTime<=?""",
            int(symbol_id), timeframe_code, _sql_timestamp(start_time), _sql_timestamp(end_time))
        return {
            row[0]: _value_signature(row[1], row[2], row[3], row[4], row[5])
            for row in cursor.fetchall()
        }
    finally:
        if connection is None: active.close()
def _sql_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(microsecond=0)
    return value.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)
def _decimal_text(value: Any, *, precision: int, scale: int) -> str:
    """Bind DECIMAL values as fixed-scale text to avoid ODBC precision inference."""
    try:
        decimal_value = Decimal(value)
        quantum = Decimal(1).scaleb(-scale)
        normalized = decimal_value.quantize(quantum, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid SQL decimal value: {value!r}") from exc
    if not normalized.is_finite() or abs(normalized) >= Decimal(10) ** (precision - scale):
        raise ValueError(f"SQL decimal value exceeds DECIMAL({precision},{scale})")
    return f"{normalized:.{scale}f}"
def _value_signature(open_: Any, high: Any, low: Any, close: Any,
                     volume: Any) -> tuple[str | None, ...]:
    prices = tuple(_decimal_text(value, precision=18, scale=8)
                   for value in (open_, high, low, close))
    normalized_volume = None if volume is None else _decimal_text(
        volume, precision=20, scale=4)
    return (*prices, normalized_volume)
def candle_signature(candle: dict[str, Any]) -> tuple[str | None, ...]:
    """Return values exactly normalized to the warehouse DECIMAL contract."""
    return _value_signature(candle["open"], candle["high"], candle["low"],
                            candle["close"], candle.get("volume"))
def prepare_rows(candles: Iterable[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """Create pyodbc rows, reusing any signature already computed during comparison."""
    return [(int(candle["symbol_id"]), _sql_timestamp(candle["timestamp"]),
             *(candle.get("_signature") or candle_signature(candle))) for candle in candles]
def _require_contract(cursor: pyodbc.Cursor, procedure: str, expected: str) -> None:
    cursor.execute("""SELECT CAST(value AS NVARCHAR(50)) FROM sys.extended_properties
        WHERE major_id=OBJECT_ID(?) AND minor_id=0 AND class=1 AND name='DPContractVersion'""",
        procedure)
    row = cursor.fetchone()
    actual = str(row[0]) if row and row[0] is not None else None
    if actual != expected:
        raise RuntimeError(
            f"{procedure} contract mismatch: expected {expected}, found {actual or 'missing'}"
        )
def _fetch_result_row(cursor: pyodbc.Cursor, operation: str) -> Any:
    """Return the first query row after any preceding DML row-count sets."""
    while True:
        if cursor.description is not None:
            row = cursor.fetchone()
            if row is not None:
                return row
        if not cursor.nextset():
            raise RuntimeError(f"{operation} did not return row counts")
def bulk_upsert_candles(
    config: dict[str, Any], timeframe: dict[str, Any],
    candles: list[dict[str, Any]], *, complete_bootstrap: bool = False,
    symbol_id: int | None = None, connection: pyodbc.Connection | None = None,
) -> dict[str, int]:
    """Load one complete provider window and atomically advance bootstrap state."""
    empty = {"input": 0, "staged_inserted": 0, "staged_updated": 0,
             "fact_inserted": 0, "fact_updated": 0, "affected": 0, "skipped": 0}
    symbol_ids = {int(candle["symbol_id"]) for candle in candles}
    if len(symbol_ids) > 1:
        raise ValueError("bulk_upsert_candles accepts one symbol per batch")
    if symbol_ids:
        resolved_symbol_id = next(iter(symbol_ids))
        if symbol_id is not None and int(symbol_id) != resolved_symbol_id:
            raise ValueError("symbol_id does not match candle batch")
    elif symbol_id is not None:
        resolved_symbol_id = int(symbol_id)
    elif complete_bootstrap:
        raise ValueError("symbol_id is required for empty bootstrap completion")
    else:
        return empty
    rows = prepare_rows(candles)
    staging_name = timeframe["staging_table"]
    staging_table = _quoted_name(staging_name)
    fact_table = _quoted_name(config["tables"]["fact_table"])
    procedure_name = config["tables"]["load_procedure"]
    procedure = _quoted_name(procedure_name)
    active = connection or get_connection(config)
    try:
        cursor = active.cursor()
        _require_contract(cursor, procedure_name, str(config["sql_server"]["contract_version"]))
        staged_inserted = staged_updated = fact_inserted = fact_updated = affected = 0
        if rows:
            cursor.execute("""IF OBJECT_ID('tempdb..#V3Candles') IS NOT NULL DROP TABLE #V3Candles;
                CREATE TABLE #V3Candles (SymbolID INT NOT NULL,BarTime DATETIME2(0) NOT NULL,[Open] DECIMAL(18,8) NOT NULL,High DECIMAL(18,8) NOT NULL,Low DECIMAL(18,8) NOT NULL,
                [Close] DECIMAL(18,8) NOT NULL,Volume DECIMAL(20,4) NULL,PRIMARY KEY(SymbolID,BarTime))""")
            cursor.fast_executemany = True
            insert = """INSERT INTO #V3Candles(SymbolID,BarTime,[Open],High,Low,[Close],Volume)
                VALUES(?,?,?,?,?,?,?)"""
            size = int(config["sql_server"]["batch_size"])
            for offset in range(0, len(rows), size):
                cursor.executemany(insert, rows[offset : offset + size])
            cursor.execute(f"""
                DECLARE @Actions TABLE (ActionName NVARCHAR(10));
                MERGE {staging_table} WITH (HOLDLOCK) AS target
                USING #V3Candles AS source
                  ON target.SymbolID=source.SymbolID AND target.BarTime=source.BarTime
                WHEN NOT MATCHED THEN INSERT
                  (SymbolID, BarTime, [Open], High, Low, [Close], Volume, IsProcessed)
                  VALUES (source.SymbolID, source.BarTime, source.[Open], source.High,
                          source.Low, source.[Close], source.Volume, 1)
                WHEN MATCHED AND (
                  target.[Open]<>source.[Open] OR target.High<>source.High
                  OR target.Low<>source.Low OR target.[Close]<>source.[Close]
                  OR ISNULL(target.Volume,-1)<>ISNULL(source.Volume,-1)
                  OR target.IsProcessed<>1)
                THEN UPDATE SET [Open]=source.[Open], High=source.High, Low=source.Low,
                  [Close]=source.[Close], Volume=source.Volume, IsProcessed=1
                OUTPUT $action INTO @Actions;
                SELECT SUM(CASE WHEN ActionName='INSERT' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN ActionName='UPDATE' THEN 1 ELSE 0 END) FROM @Actions;""")
            stage_row = _fetch_result_row(cursor, "staging merge")
            staged_inserted, staged_updated = (int(value or 0) for value in stage_row)
            from_time = min(row[1] for row in rows)
            cursor.execute(f"EXEC {procedure} ?,?,?,?", resolved_symbol_id,
                           timeframe["code"], staging_name, from_time)
            fact_row = _fetch_result_row(cursor, procedure_name)
            fact_updated, fact_inserted, affected = (
                max(0, int(value or 0)) for value in fact_row)
            cursor.execute(f"""IF EXISTS(SELECT 1 FROM #V3Candles source
                JOIN DWH.Dim_Timeframe tf ON tf.Code=? LEFT JOIN {fact_table} fact
                ON fact.SymbolID=source.SymbolID AND fact.TimeframeID=tf.TimeframeID
                AND fact.BarTime=source.BarTime WHERE fact.SymbolID IS NULL
                OR fact.[Open]<>source.[Open] OR fact.High<>source.High OR fact.Low<>source.Low
                OR fact.[Close]<>source.[Close] OR ISNULL(fact.Volume,-1)<>ISNULL(source.Volume,-1))
                THROW 51021,'Fact verification failed for provider batch.',1;""", timeframe["code"])
        if complete_bootstrap:
            state_table = _quoted_name(config["tables"]["backfill_state_table"])
            cursor.execute(f"""MERGE {state_table} WITH(HOLDLOCK) AS target USING
                (SELECT CAST(? AS INT) SymbolID,TimeframeID FROM DWH.Dim_Timeframe WHERE Code=?) AS source
                ON target.SymbolID=source.SymbolID AND target.TimeframeID=source.TimeframeID
                WHEN NOT MATCHED THEN INSERT(SymbolID,TimeframeID,BootstrapCompletedAt)
                VALUES(source.SymbolID,source.TimeframeID,SYSUTCDATETIME())
                WHEN MATCHED THEN UPDATE SET BootstrapCompletedAt=SYSUTCDATETIME();
                IF @@ROWCOUNT=0 THROW 51020,'Cannot complete bootstrap for unknown timeframe.',1;""",
                resolved_symbol_id, timeframe["code"])
        active.commit()
        return {"input": len(rows), "staged_inserted": staged_inserted, "staged_updated": staged_updated,
                "fact_inserted": fact_inserted, "fact_updated": fact_updated, "affected": affected,
                "skipped": max(0, len(rows) - affected)}
    except Exception:
        active.rollback()
        raise
    finally:
        if connection is None: active.close()
