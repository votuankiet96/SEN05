# =============================================================================
# modules/db_connector.py - Tang truy cap SQL Server cho he thong Auto Trading
# =============================================================================
# Muc tieu file:
# - Mo/kiem tra ket noi DB an toan (co retry).
# - Insert/merge du lieu staging, day vao Fact_OHLCV.
# - Cung cap ham utility de gap-check va windowed replacement.
#
# Muc do nhay cam:
# - Day la file rui ro cao: thay doi sai co the gay trung lap/mat du lieu lich su.
# - Uu tien sua tham so o tang goi ham; han che sua SQL core neu chua test ky.
#
# Nguyen tac van hanh:
# - Moi thao tac ghi co commit/rollback ro rang.
# - Cac ham xoa/ghi de uoc luong tac dong truoc qua rowcount/log.
# =============================================================================

import time

from core_engine.settings import (
    DATA_WAREHOUSE_LOG,
    HISTORICAL,
    TF_DISPLAY_ORDER,
    TF_STAGING,
)
from core_engine.reporting.logging_setup import setup_logger
from core_engine.data_processing.warehouse_connection import (
    DatabaseWriteError,
    get_connection,
    test_connection,
)

logger = setup_logger("data_warehouse", str(DATA_WAREHOUSE_LOG), rotating=True, console=False, utc=True)


def _fmt_count(value) -> str:
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


def _tf_from_staging_table(staging_table: str | None) -> str | None:
    if not staging_table:
        return None
    table = str(staging_table).lower()
    for tf_code, configured in TF_STAGING.items():
        if table == str(configured).lower():
            return tf_code
    return None


def _target_label(
    *,
    symbol: str | None = None,
    symbol_id: int | None = None,
    tf_code: str | None = None,
    staging_table: str | None = None,
) -> str:
    tf = tf_code or _tf_from_staging_table(staging_table) or "-"
    if symbol:
        return f"{symbol} {tf}"
    if symbol_id is not None:
        return f"SymbolID={symbol_id} {tf}"
    return f"scope {tf}"


def _field(key: str, value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        value = _fmt_count(value)
    return f"{key}={value}"


def _warehouse_log(
    level: int,
    *,
    source: str,
    target: str,
    action: str,
    result: str,
    **fields,
) -> None:
    parts = [
        "WAREHOUSE",
        source,
        target,
        action,
    ]
    parts.extend(part for key, value in fields.items() if (part := _field(key, value)))
    parts.append(f"result={result}")
    logger.log(level, " | ".join(parts))


# ---------------------------------------------------------------------------
# Staging insert
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# ETL callers
# ---------------------------------------------------------------------------


def run_etl_direct(
    symbol_id: int,
    tf_code: str,
    staging_table: str,
    *,
    source: str = "unknown_source",
    symbol: str | None = None,
) -> int:
    """
    Goi stored procedure DWH.usp_LoadDirect de nap du lieu 1:1 vao Fact_OHLCV.

    Dau ra:
    - So dong moi chen vao Fact (tinh theo before/after count).

    Anh huong he thong:
    - Ham an toan khi goi lap lai, do SP da chong duplicate.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # Count rows in Fact before ETL
        cursor.execute(
            "SELECT COUNT(*) FROM DWH.Fact_OHLCV f "
            "JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID "
            "WHERE f.SymbolID = ? AND tf.Code = ?",
            (symbol_id, tf_code),
        )
        before = cursor.fetchone()[0]

        cursor.execute("EXEC DWH.usp_LoadDirect ?, ?, ?", (symbol_id, tf_code, staging_table))
        conn.commit()

        # Count rows in Fact after ETL
        cursor.execute(
            "SELECT COUNT(*) FROM DWH.Fact_OHLCV f "
            "JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID "
            "WHERE f.SymbolID = ? AND tf.Code = ?",
            (symbol_id, tf_code),
        )
        after = cursor.fetchone()[0]

        inserted = after - before
        _warehouse_log(
            20,
            source=source,
            target=_target_label(symbol=symbol, symbol_id=symbol_id, tf_code=tf_code, staging_table=staging_table),
            action="fact_save",
            fact_saved=inserted,
            table=staging_table,
            result="ok" if inserted else "no_new_rows",
        )
        return inserted
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


# ---------------------------------------------------------------------------
# Staging alignment cleanup â€” prevent DST-shift contamination
# ---------------------------------------------------------------------------


_ALL_STAGING_TABLES = [TF_STAGING[tf_code] for tf_code in TF_DISPLAY_ORDER if tf_code in TF_STAGING]


def purge_staging(
    days_to_keep: int = 7,
    *,
    batch_size: int | None = None,
    pause_sec: float | None = None,
    max_rows_per_run: int | None = None,
    checkpoint: bool | None = None,
) -> dict:
    """
    Don dep staging da xu ly, chi giu lai N ngay gan nhat.

    Tai sao can:
    - Staging chi la bo dem tam.
    - Neu khong don dep dinh ky, bang se phinh to va lam cham pipeline.

    Dau ra:
    - dict {ten_bang: so_dong_da_xoa} de bao cao log.
    """
    batch_size = HISTORICAL.staging_cleanup_batch_rows if batch_size is None else int(batch_size)
    pause_sec = HISTORICAL.staging_cleanup_pause_sec if pause_sec is None else float(pause_sec)
    max_rows_per_run = (
        HISTORICAL.staging_cleanup_max_rows_per_run if max_rows_per_run is None else int(max_rows_per_run)
    )
    checkpoint = HISTORICAL.staging_cleanup_checkpoint if checkpoint is None else bool(checkpoint)

    batch_size = max(500, min(int(batch_size), 50_000))
    pause_sec = max(0.0, min(float(pause_sec), 5.0))
    max_rows_per_run = max(0, int(max_rows_per_run))
    deleted_summary = {}
    total_deleted = 0
    conn = get_connection()
    cursor = conn.cursor()
    try:
        for table in _ALL_STAGING_TABLES:
            table_deleted = 0
            batches = 0
            while True:
                if max_rows_per_run and total_deleted >= max_rows_per_run:
                    deleted_summary[table] = table_deleted
                    deleted_summary["__partial__"] = (
                        f"cleanup paused after {total_deleted:,} row(s); "
                        "remaining old temporary rows will be cleaned in later runs"
                    )
                    _warehouse_log(
                        20,
                        source="maintenance",
                        target="all staging tables",
                        action="staging_cleanup_budget_reached",
                        deleted=total_deleted,
                        max_rows=max_rows_per_run,
                        result="partial",
                    )
                    return deleted_summary

                effective_batch = batch_size
                if max_rows_per_run:
                    effective_batch = min(effective_batch, max_rows_per_run - total_deleted)
                    if effective_batch <= 0:
                        break

                cursor.execute(
                    f"DELETE TOP ({effective_batch}) FROM {table} WITH (ROWLOCK)"
                    f" WHERE IsProcessed = 1"
                    f" AND BarTime < DATEADD(day, ?, GETUTCDATE())",
                    (-days_to_keep,),
                )
                rowcount = max(0, int(cursor.rowcount or 0))
                conn.commit()
                if rowcount == 0:
                    break
                table_deleted += rowcount
                total_deleted += rowcount
                batches += 1
                if checkpoint:
                    try:
                        cursor.execute("CHECKPOINT")
                        conn.commit()
                    except Exception as checkpoint_error:
                        _warehouse_log(
                            30,
                            source="maintenance",
                            target=table,
                            action="staging_cleanup_checkpoint",
                            result="warning",
                            reason=checkpoint_error,
                        )
                _warehouse_log(
                    20,
                    source="maintenance",
                    target=table,
                    action="staging_cleanup_batch",
                    deleted=rowcount,
                    total_deleted=table_deleted,
                    batch_size=effective_batch,
                    keep_days=days_to_keep,
                    result="ok",
                )
                if pause_sec:
                    time.sleep(pause_sec)
            deleted_summary[table] = table_deleted
            if table_deleted:
                _warehouse_log(
                    20,
                    source="maintenance",
                    target=table,
                    action="staging_cleanup_table",
                    deleted=table_deleted,
                    batches=batches,
                    keep_days=days_to_keep,
                    result="ok",
                )
        total = sum(deleted_summary.values())
        _warehouse_log(
            20,
            source="maintenance",
            target="all staging tables",
            action="staging_cleanup",
            deleted=total,
            batch_size=batch_size,
            max_rows=max_rows_per_run,
            pause_sec=pause_sec,
            checkpoint="yes" if checkpoint else "no",
            keep_days=days_to_keep,
            result="ok",
        )
    except Exception as e:
        conn.rollback()
        deleted_summary["__error__"] = str(e)
        _warehouse_log(
            40,
            source="maintenance",
            target="all staging tables",
            action="staging_cleanup",
            result="failed",
            reason=e,
        )
    finally:
        conn.close()
    return deleted_summary


# ---------------------------------------------------------------------------
# Pipeline utility â€” get latest bar times for all (symbol, TF) pairs
# ---------------------------------------------------------------------------


def get_latest_bars() -> dict:
    """
    Lay moc BarTime moi nhat cho moi cap (symbol_id, tf_code).

    Muc dich:
    - Giup gap_fill/pipeline_status xac dinh cap nao dang thieu hoac tre du lieu.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT f.SymbolID, tf.Code, MAX(f.BarTime) AS LastBar
            FROM DWH.Fact_OHLCV f
            JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
            GROUP BY f.SymbolID, tf.Code
        """)
        return {(row[0], row[1]): row[2] for row in cursor.fetchall()}
    finally:
        conn.close()


def get_latest_ohlcv_snapshot(symbol_id: int, tf_code: str, limit: int = 500) -> list[dict]:
    """
    Return the latest committed OHLCV bars for one symbol/timeframe.

    The result is sorted oldest -> newest so downstream consumers can process the
    payload directly without reordering. This is read-only and is used by the
    candle_snapshot Redis handoff worker after live ETL has committed data.
    """
    safe_limit = max(1, min(int(limit or 500), 5000))
    safe_tf = str(tf_code or "").strip().upper()
    safe_symbol_id = int(symbol_id)

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT BarTime, [Open], High, Low, [Close], Volume
            FROM (
                SELECT TOP ({safe_limit})
                    f.BarTime,
                    f.[Open],
                    f.High,
                    f.Low,
                    f.[Close],
                    f.Volume
                FROM DWH.Fact_OHLCV f
                JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
                WHERE f.SymbolID = ?
                  AND tf.Code = ?
                ORDER BY f.BarTime DESC
            ) latest
            ORDER BY BarTime ASC
            """,
            (safe_symbol_id, safe_tf),
        )

        bars: list[dict] = []
        for row in cursor.fetchall():
            bar_time = row[0]
            if hasattr(bar_time, "replace"):
                bar_time_text = bar_time.replace(microsecond=0).isoformat()
            else:
                bar_time_text = str(bar_time).replace(" ", "T")

            bars.append(
                {
                    "bar_time": bar_time_text,
                    "open": float(row[1]) if row[1] is not None else None,
                    "high": float(row[2]) if row[2] is not None else None,
                    "low": float(row[3]) if row[3] is not None else None,
                    "close": float(row[4]) if row[4] is not None else None,
                    "volume": float(row[5]) if row[5] is not None else None,
                }
            )
        return bars
    finally:
        conn.close()


def get_internal_gaps(tf_codes: list, lookback_days: int = 60) -> dict:
    """
    Phat hien lo hong du lieu ben trong Fact_OHLCV.

    Co che:
    - Dung LEAD() de so sanh tung cap bar lien tiep trong lookback_days.
    - Tra ve cac khoang cach > 10 phut, sau do caller tu loc theo quy tac TF.

    Returns:
        {(symbol_id, tf_code): [(gap_start_dt, gap_end_dt, gap_minutes_int), ...]}
        Only pairs that have at least one gap are included.
    """
    if not tf_codes:
        return {}

    placeholders = ",".join("?" * len(tf_codes))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            WITH ordered AS (
                SELECT
                    f.SymbolID,
                    tf.Code AS TFCode,
                    f.BarTime,
                    LEAD(f.BarTime) OVER (
                        PARTITION BY f.SymbolID, f.TimeframeID
                        ORDER BY f.BarTime
                    ) AS NextBarTime
                FROM DWH.Fact_OHLCV f
                JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
                WHERE f.BarTime >= DATEADD(day, -?, GETUTCDATE())
                  AND tf.Code IN ({placeholders})
            )
            SELECT SymbolID, TFCode, BarTime, NextBarTime,
                   DATEDIFF(MINUTE, BarTime, NextBarTime) AS GapMinutes
            FROM ordered
            WHERE NextBarTime IS NOT NULL
              AND DATEDIFF(MINUTE, BarTime, NextBarTime) > 10
            ORDER BY SymbolID, TFCode, BarTime
        """,
            [lookback_days] + list(tf_codes),
        )

        result = {}
        for row in cursor.fetchall():
            key = (row[0], row[1])
            result.setdefault(key, []).append((row[2], row[3], row[4]))
        _warehouse_log(
            20,
            source="data_health",
            target=f"{len(tf_codes)} timeframe(s)",
            action="gap_scan",
            lookback_days=lookback_days,
            pairs_with_gaps=len(result),
            result="ok",
        )
        return result
    except Exception as e:
        _warehouse_log(
            40,
            source="data_health",
            target=f"{len(tf_codes)} timeframe(s)",
            action="gap_scan",
            lookback_days=lookback_days,
            result="failed",
            reason=e,
        )
        return {}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Price continuity check â€” find spike candles (close[i] â‰  open[i+1])
# ---------------------------------------------------------------------------


def delete_fact_bars_range(
    symbol_id: int,
    tf_code: str,
    from_dt,
    to_dt,
    *,
    source: str = "historical_repair",
    symbol: str | None = None,
) -> int:
    """
    Xoa Fact_OHLCV trong dung cua so BarTime [from_dt, to_dt].

    Dung cho windowed replacement: staging da co data thay the, chi xoa
    phan Fact nam trong cua so vua pull lai de giu nguyen lich su ngoai window.
    """
    if from_dt is None or to_dt is None or from_dt > to_dt:
        _warehouse_log(
            30,
            source=source,
            target=_target_label(symbol=symbol, symbol_id=symbol_id, tf_code=tf_code),
            action="fact_delete_window",
            from_time=from_dt,
            to_time=to_dt,
            result="invalid_window",
        )
        return 0

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT TimeframeID FROM DWH.Dim_Timeframe WHERE Code = ?", (tf_code,))
        row = cursor.fetchone()
        if not row:
            _warehouse_log(
                30,
                source=source,
                target=_target_label(symbol=symbol, symbol_id=symbol_id, tf_code=tf_code),
                action="fact_delete_window",
                result="unknown_timeframe",
            )
            return 0
        tf_id = row[0]
        cursor.execute(
            """
            DELETE FROM DWH.Fact_OHLCV
            WHERE SymbolID = ?
              AND TimeframeID = ?
              AND BarTime >= ?
              AND BarTime <= ?
            """,
            (symbol_id, tf_id, from_dt, to_dt),
        )
        deleted = cursor.rowcount
        conn.commit()
        _warehouse_log(
            20,
            source=source,
            target=_target_label(symbol=symbol, symbol_id=symbol_id, tf_code=tf_code),
            action="fact_delete_window",
            fact_deleted=deleted,
            from_time=from_dt,
            to_time=to_dt,
            result="ok",
        )
        return deleted
    except Exception as e:
        conn.rollback()
        _warehouse_log(
            40,
            source=source,
            target=_target_label(symbol=symbol, symbol_id=symbol_id, tf_code=tf_code),
            action="fact_delete_window",
            from_time=from_dt,
            to_time=to_dt,
            result="failed",
            reason=e,
        )
        return 0
    finally:
        conn.close()


def get_staging_bar_window(symbol_id: int, staging_table: str) -> tuple:
    """
    Tra ve (first_bar, last_bar, row_count) cua staging cho symbol.

    staging_table den tu config.TF_STAGING; cac ham staging hien co cung dung
    table name controlled nay trong SQL dynamic.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            SELECT MIN(BarTime), MAX(BarTime), COUNT(*)
            FROM {staging_table}
            WHERE SymbolID = ?
            """,
            (symbol_id,),
        )
        first_bar, last_bar, row_count = cursor.fetchone()
        return first_bar, last_bar, int(row_count or 0)
    except Exception as e:
        _warehouse_log(
            40,
            source="historical_repair",
            target=_target_label(symbol_id=symbol_id, staging_table=staging_table),
            action="staging_window_check",
            table=staging_table,
            result="failed",
            reason=e,
        )
        return None, None, 0
    finally:
        conn.close()


def get_fact_bar_window_context(symbol_id: int, tf_code: str, from_dt, to_dt) -> dict:
    """
    Lay context quanh replacement window trong Fact.

    Tra ve:
      - window_count: so row Fact hien co trong [from_dt, to_dt]
      - prev_bar: row gan nhat truoc from_dt
      - next_bar: row gan nhat sau to_dt
    """
    result = {"window_count": 0, "prev_bar": None, "next_bar": None}
    if from_dt is None or to_dt is None or from_dt > to_dt:
        return result

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT TimeframeID FROM DWH.Dim_Timeframe WHERE Code = ?", (tf_code,))
        row = cursor.fetchone()
        if not row:
            _warehouse_log(
                30,
                source="historical_repair",
                target=_target_label(symbol_id=symbol_id, tf_code=tf_code),
                action="fact_window_check",
                result="unknown_timeframe",
            )
            return result
        tf_id = row[0]

        cursor.execute(
            """
            SELECT COUNT(*)
            FROM DWH.Fact_OHLCV
            WHERE SymbolID = ?
              AND TimeframeID = ?
              AND BarTime >= ?
              AND BarTime <= ?
            """,
            (symbol_id, tf_id, from_dt, to_dt),
        )
        result["window_count"] = int(cursor.fetchone()[0] or 0)

        cursor.execute(
            """
            SELECT TOP 1 BarTime
            FROM DWH.Fact_OHLCV
            WHERE SymbolID = ?
              AND TimeframeID = ?
              AND BarTime < ?
            ORDER BY BarTime DESC
            """,
            (symbol_id, tf_id, from_dt),
        )
        row = cursor.fetchone()
        if row:
            result["prev_bar"] = row[0]

        cursor.execute(
            """
            SELECT TOP 1 BarTime
            FROM DWH.Fact_OHLCV
            WHERE SymbolID = ?
              AND TimeframeID = ?
              AND BarTime > ?
            ORDER BY BarTime ASC
            """,
            (symbol_id, tf_id, to_dt),
        )
        row = cursor.fetchone()
        if row:
            result["next_bar"] = row[0]

        return result
    except Exception as e:
        _warehouse_log(
            40,
            source="historical_repair",
            target=_target_label(symbol_id=symbol_id, tf_code=tf_code),
            action="fact_window_check",
            from_time=from_dt,
            to_time=to_dt,
            result="failed",
            reason=e,
        )
        return result
    finally:
        conn.close()


def delete_staging_bars(
    symbol_id: int,
    staging_table: str,
    *,
    source: str = "historical_repair",
    symbol: str | None = None,
    tf_code: str | None = None,
) -> int:
    """
    Xoa toan bo bar cua symbol trong mot staging table.

    Dung khi:
    - Chuan bi pull lai tu dau de loai bo staging cu/co kha nang loi.

    Dau ra:
    - So dong da xoa.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"DELETE FROM {staging_table} WHERE SymbolID = ?", (symbol_id,))
        deleted = cursor.rowcount
        conn.commit()
        _warehouse_log(
            20,
            source=source,
            target=_target_label(symbol=symbol, symbol_id=symbol_id, tf_code=tf_code, staging_table=staging_table),
            action="staging_delete",
            staging_deleted=deleted,
            table=staging_table,
            result="ok",
        )
        return deleted
    except Exception as e:
        conn.rollback()
        _warehouse_log(
            40,
            source=source,
            target=_target_label(symbol=symbol, symbol_id=symbol_id, tf_code=tf_code, staging_table=staging_table),
            action="staging_delete",
            table=staging_table,
            result="failed",
            reason=e,
        )
        return 0
    finally:
        conn.close()


def _sql_in_placeholders(values: list) -> str:
    return ",".join("?" for _ in values)


def preview_ohlcv_reset_scope(
    symbol_ids: list[int],
    tf_codes: list[str],
    *,
    source: str = "historical_reset",
    scope_label: str | None = None,
) -> dict:
    """
    Count rows that would be removed by historical reset for a scoped target.

    This function never mutates data. It is intentionally paired with
    reset_ohlcv_scope so the operator can review the blast radius first.
    """
    symbol_ids = [int(value) for value in symbol_ids]
    tf_codes = [str(value).upper() for value in tf_codes if str(value).upper() in TF_STAGING]
    summary = {"fact_rows": 0, "staging_rows": {}, "staging_total": 0}
    if not symbol_ids or not tf_codes:
        return summary

    sid_sql = _sql_in_placeholders(symbol_ids)
    tf_sql = _sql_in_placeholders(tf_codes)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            SELECT COUNT(*)
            FROM DWH.Fact_OHLCV f
            JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
            WHERE f.SymbolID IN ({sid_sql})
              AND tf.Code IN ({tf_sql})
            """,
            symbol_ids + tf_codes,
        )
        summary["fact_rows"] = int(cursor.fetchone()[0] or 0)

        for tf_code in tf_codes:
            table = TF_STAGING[tf_code]
            cursor.execute(
                f"SELECT COUNT(*) FROM {table} WHERE SymbolID IN ({sid_sql})",
                symbol_ids,
            )
            count = int(cursor.fetchone()[0] or 0)
            summary["staging_rows"][table] = count
            summary["staging_total"] += count
        _warehouse_log(
            30,
            source=source,
            target=scope_label or f"{len(symbol_ids)} symbol(s), {len(tf_codes)} timeframe(s)",
            action="reset_preview",
            fact_rows=summary["fact_rows"],
            staging_rows=summary["staging_total"],
            timeframes=",".join(tf_codes),
            result="preview_only",
        )
        return summary
    finally:
        conn.close()


def reset_ohlcv_scope(
    symbol_ids: list[int],
    tf_codes: list[str],
    *,
    source: str = "historical_reset",
    scope_label: str | None = None,
) -> dict:
    """
    Delete Fact_OHLCV and staging rows for a scoped historical reset.

    The caller is responsible for operator confirmation and runtime locks.
    """
    symbol_ids = [int(value) for value in symbol_ids]
    tf_codes = [str(value).upper() for value in tf_codes if str(value).upper() in TF_STAGING]
    summary = {"fact_rows": 0, "staging_rows": {}, "staging_total": 0}
    if not symbol_ids or not tf_codes:
        return summary

    sid_sql = _sql_in_placeholders(symbol_ids)
    tf_sql = _sql_in_placeholders(tf_codes)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            DELETE f
            FROM DWH.Fact_OHLCV f
            JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
            WHERE f.SymbolID IN ({sid_sql})
              AND tf.Code IN ({tf_sql})
            """,
            symbol_ids + tf_codes,
        )
        summary["fact_rows"] = int(cursor.rowcount or 0)

        for tf_code in tf_codes:
            table = TF_STAGING[tf_code]
            cursor.execute(
                f"DELETE FROM {table} WHERE SymbolID IN ({sid_sql})",
                symbol_ids,
            )
            deleted = int(cursor.rowcount or 0)
            summary["staging_rows"][table] = deleted
            summary["staging_total"] += deleted

        conn.commit()
        _warehouse_log(
            30,
            source=source,
            target=scope_label or f"{len(symbol_ids)} symbol(s), {len(tf_codes)} timeframe(s)",
            action="reset_delete",
            fact_deleted=summary["fact_rows"],
            staging_deleted=summary["staging_total"],
            timeframes=",".join(tf_codes),
            result="completed",
        )
        return summary
    except Exception:
        conn.rollback()
        _warehouse_log(
            40,
            source=source,
            target=scope_label or f"{len(symbol_ids)} symbol(s), {len(tf_codes)} timeframe(s)",
            action="reset_delete",
            timeframes=",".join(tf_codes),
            result="failed_rolled_back",
        )
        logger.exception("WAREHOUSE | %s | reset_delete | transaction rolled back", source)
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Upsert a single OHLCV bar (update if values differ, skip if identical)
# ---------------------------------------------------------------------------


