"""Warehouse read path: watermarks, snapshots, and gap queries.

Split out of the former db_connector.py / repository.py. Read-only -
nothing here writes to SEN.* or DWH.* tables.
"""

from __future__ import annotations

from core_engine.warehouse.connection import get_connection
from core_engine.warehouse.operation_log import _target_label, _warehouse_log


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
        # Re-raise instead of returning {}: an empty dict here is
        # indistinguishable from "scanned successfully, found zero gaps",
        # and the caller (historical.runtime_support.find_hole_pairs) used
        # to treat that as "clean" - meaning a transient SQL failure during
        # the gap scan silently masked real gaps instead of surfacing an
        # error. Callers must now explicitly handle scan failure as
        # "unknown", not "clean".
        raise
    finally:
        conn.close()


def fact_covers_window(
    symbol_id: int,
    tf_code: str,
    gap_start,
    gap_end,
    *,
    max_gap_minutes: int,
) -> bool:
    """Return whether a repaired Fact window no longer contains a large gap.

    ``gap_start`` and ``gap_end`` are the two existing bars which exposed
    the hole. Merely finding a row in the inclusive window is therefore not
    evidence of repair: those boundary rows make that test true even when no
    data was added. Verify every adjacent pair and cache the window only when
    its largest remaining raw gap is within the discovery threshold.

    This is deliberately conservative around exchange closures: a legitimate
    closure may be rechecked later, but an incomplete repair must not be hidden
    by the verified-gap cache for 30 days.
    """
    if max_gap_minutes <= 0:
        raise ValueError("max_gap_minutes must be positive")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            WITH ordered AS (
                SELECT
                    f.BarTime,
                    LEAD(f.BarTime) OVER (ORDER BY f.BarTime) AS NextBarTime
                FROM DWH.Fact_OHLCV f
                JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
                WHERE f.SymbolID = ?
                  AND tf.Code = ?
                  AND f.BarTime >= ?
                  AND f.BarTime <= ?
            )
            SELECT
                COUNT_BIG(*) AS BarCount,
                MAX(DATEDIFF(MINUTE, BarTime, NextBarTime)) AS MaxGapMinutes
            FROM ordered
            """,
            (symbol_id, tf_code, gap_start, gap_end),
        )
        row = cursor.fetchone()
        if row is None:
            return False
        bar_count, largest_gap = int(row[0] or 0), row[1]
        return bar_count >= 2 and largest_gap is not None and int(largest_gap) <= max_gap_minutes
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
