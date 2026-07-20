"""Warehouse maintenance path: purge, delete, and scoped reset.

Split out of the former db_connector.py / repository.py and kept
separate from writer.py/reader.py deliberately: everything here deletes
or overwrites historical data, the highest-blast-radius operations in
the warehouse layer.
"""

from __future__ import annotations

import time

from core_engine.settings import HISTORICAL, TF_DISPLAY_ORDER, TF_STAGING
from core_engine.warehouse.connection import get_connection
from core_engine.warehouse.operation_log import _target_label, _warehouse_log, logger


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
