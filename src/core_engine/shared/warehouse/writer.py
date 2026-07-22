"""Warehouse write path: staging insert and the staging -> Fact ETL call."""

from __future__ import annotations

from core_engine.shared.warehouse.connection import DatabaseWriteError, get_connection
from core_engine.shared.warehouse.operation_log import _target_label, _warehouse_log


def insert_staging_batch(
    df,
    symbol_id: int,
    staging_table: str,
    *,
    source: str = "unknown_source",
    symbol: str | None = None,
    tf_code: str | None = None,
) -> int:
    """
    Ghi loat OHLCV vao staging table an toan va nhanh.

    Chien luoc:
    1. Do tat ca vao temp table (nhanh, it rang buoc).
    2. MERGE sang staging de bo qua bar trung (SymbolID + BarTime).

    Dau ra:
    - So dong moi duoc chen.

    Anh huong he thong:
    - Day la diem then chot de tranh duplicate truoc khi ETL vao Fact.
    """
    if df is None or df.empty:
        return 0

    # Build parameter list
    rows = []
    for bar_time, row in df.iterrows():
        ts = bar_time.strftime("%Y-%m-%d %H:%M:%S")
        volume = None if (row.get("volume") != row.get("volume")) else row.get("volume")
        rows.append(
            (
                symbol_id,
                ts,
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                volume,
            )
        )

    conn = get_connection()
    cursor = conn.cursor()

    try:
        # 1. Create temp table with same schema (no constraints)
        cursor.execute(f"""
            SELECT TOP 0
                SymbolID, BarTime, [Open], High, Low,
                [Close], Volume, IsProcessed
            INTO #tmp_staging
            FROM {staging_table}
        """)

        # 2. Bulk-insert into temp table (no unique constraint â†’ no errors)
        cursor.executemany(
            """
            INSERT INTO #tmp_staging
                (SymbolID, BarTime, [Open], High, Low,
                 [Close], Volume, IsProcessed)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """,
            rows,
        )

        # 3. MERGE into staging â€” insert new rows, update existing if OHLCV changed
        cursor.execute(f"""
            MERGE {staging_table} AS tgt
            USING #tmp_staging AS src
                ON tgt.SymbolID = src.SymbolID AND tgt.BarTime = src.BarTime
            WHEN NOT MATCHED THEN
                INSERT (SymbolID, BarTime, [Open], High, Low,
                        [Close], Volume, IsProcessed)
                VALUES (src.SymbolID, src.BarTime, src.[Open], src.High,
                        src.Low, src.[Close], src.Volume, src.IsProcessed)
            WHEN MATCHED AND (
                tgt.[Open]  <> src.[Open]  OR tgt.High    <> src.High  OR
                tgt.Low     <> src.Low     OR tgt.[Close] <> src.[Close] OR
                ISNULL(tgt.Volume, -1) <> ISNULL(src.Volume, -1)
            ) THEN UPDATE SET
                tgt.[Open]  = src.[Open],
                tgt.High    = src.High,
                tgt.Low     = src.Low,
                tgt.[Close] = src.[Close],
                tgt.Volume  = src.Volume;
        """)
        affected = cursor.rowcount

        conn.commit()
        _warehouse_log(
            20,
            source=source,
            target=_target_label(symbol=symbol, symbol_id=symbol_id, tf_code=tf_code, staging_table=staging_table),
            action="staging_save",
            staged=affected,
            table=staging_table,
            result="ok" if affected else "no_change",
        )
        return affected

    except Exception as e:
        conn.rollback()
        _warehouse_log(
            40,
            source=source,
            target=_target_label(symbol=symbol, symbol_id=symbol_id, tf_code=tf_code, staging_table=staging_table),
            action="staging_save",
            table=staging_table,
            result="failed",
            reason=e,
        )
        raise DatabaseWriteError(
            f"insert_staging_batch failed for {staging_table} SymbolID={symbol_id}"
        ) from e
    finally:
        conn.close()

def run_etl_direct(
    symbol_id: int,
    tf_code: str,
    staging_table: str,
    *,
    source: str = "unknown_source",
    symbol: str | None = None,
    from_time: str | None = None,
) -> int:
    """
    Goi stored procedure DWH.usp_LoadDirect de nap du lieu 1:1 vao Fact_OHLCV.

    from_time (optional): scopes the SP's NOT EXISTS idempotency scan to
    BarTime >= from_time instead of full history (2008-01-01 default).
    Pass the earliest bar time of the batch/window just written so a
    catch-up call after a skipped/failed prior attempt stays cheap instead
    of rescanning the whole staging table on every call.

    Dau ra:
    - So dong duoc chen moi hoac cap nhat OHLC trong Fact.

    Anh huong he thong:
    - Ham an toan khi goi lap lai, do SP da chong duplicate.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "EXEC DWH.usp_LoadDirect ?, ?, ?, ?",
            (symbol_id, tf_code, staging_table, from_time),
        )

        result = cursor.fetchone()
        if result is None:
            raise DatabaseWriteError("DWH.usp_LoadDirect did not return row counts")

        updated = max(0, int(result[0] or 0))
        inserted = max(0, int(result[1] or 0))
        affected = max(0, int(result[2] or 0))
        conn.commit()
        _warehouse_log(
            20,
            source=source,
            target=_target_label(symbol=symbol, symbol_id=symbol_id, tf_code=tf_code, staging_table=staging_table),
            action="fact_save",
            fact_saved=affected,
            fact_inserted=inserted,
            fact_updated=updated,
            table=staging_table,
            result="ok" if affected else "no_change",
        )
        return affected
    except Exception as e:
        _warehouse_log(
            40,
            source=source,
            target=_target_label(symbol=symbol, symbol_id=symbol_id, tf_code=tf_code, staging_table=staging_table),
            action="fact_save",
            table=staging_table,
            result="failed",
            reason=e,
        )
        conn.rollback()
        raise DatabaseWriteError(
            f"run_etl_direct failed for SymbolID={symbol_id} TF={tf_code}"
        ) from e
    finally:
        conn.close()
