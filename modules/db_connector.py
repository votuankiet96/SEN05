# =============================================================================
# modules/db_connector.py - Tang truy cap SQL Server cho he thong Auto Trading
# =============================================================================
# Muc tieu file:
# - Mo/kiem tra ket noi DB an toan (co retry).
# - Insert/merge du lieu staging, day vao Fact_OHLCV.
# - Cung cap ham utility de gap-check, cleanup, upsert va delete.
#
# Muc do nhay cam:
# - Day la file rui ro cao: thay doi sai co the gay trung lap/mat du lieu lich su.
# - Uu tien sua tham so o tang goi ham; han che sua SQL core neu chua test ky.
#
# Nguyen tac van hanh:
# - Moi thao tac ghi co commit/rollback ro rang.
# - Cac ham xoa/ghi de uoc luong tac dong truoc qua rowcount/log.
# =============================================================================

import logging
import time

import pyodbc

from config import SQL_DATABASE, SQL_DRIVER, SQL_PWD, SQL_SERVER, SQL_UID

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _build_conn_str() -> str:
    """Tao connection string tu config; uu tien SQL auth, fallback Trusted_Connection."""
    base = (
        f"DRIVER={{{SQL_DRIVER}}};"
        f"SERVER={SQL_SERVER};"
        f"DATABASE={SQL_DATABASE};"
    )
    if SQL_UID and SQL_PWD:
        return base + f"UID={SQL_UID};PWD={SQL_PWD};"
    return base + "Trusted_Connection=yes;"


_DB_RETRY_COUNT = 3
_DB_RETRY_DELAY = 5   # seconds between attempts


def get_connection() -> pyodbc.Connection:
    """
    Tra ve ket noi pyodbc dang song voi co che retry.

    Tai sao can retry:
    - Loi mang/DB ngan han hay xay ra trong pipeline.
    - Retry giam false-fail cho job chay tu dong.
    """
    last_err: Exception = RuntimeError("unreachable")
    for attempt in range(1, _DB_RETRY_COUNT + 1):
        try:
            return pyodbc.connect(_build_conn_str(), timeout=30)
        except pyodbc.Error as e:
            last_err = e
            logger.warning("DB connect attempt %d/%d failed: %s",
                           attempt, _DB_RETRY_COUNT, e)
            if attempt < _DB_RETRY_COUNT:
                time.sleep(_DB_RETRY_DELAY)
    logger.error("Cannot connect to SQL Server after %d attempts: %s",
                 _DB_RETRY_COUNT, last_err)
    raise last_err


def test_connection() -> bool:
    """
    Health-check nhanh truoc khi pipeline chay.

    Dau ra:
    - True neu ket noi OK.
    - False neu khong ket noi duoc.

    Tac dong:
    - Giup fail-fast o dau job thay vi loi muon o giua quy trinh.
    """
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA IN ('SEN','DWH','MART')
        """)
        count = cursor.fetchone()[0]
        conn.close()
        print(f"[OK] SQL Server connected. {count} tables found in SEN/DWH/MART.")
        return True
    except Exception as e:
        print(f"[ERROR] Cannot connect: {e}")
        return False


# ---------------------------------------------------------------------------
# Staging insert
# ---------------------------------------------------------------------------

def insert_staging_batch(df, symbol_id: int, staging_table: str) -> int:
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
        ts     = bar_time.strftime("%Y-%m-%d %H:%M:%S")
        volume = None if (row.get("volume") != row.get("volume")) else row.get("volume")
        rows.append((
            symbol_id, ts,
            float(row["open"]), float(row["high"]),
            float(row["low"]),  float(row["close"]),
            volume
        ))

    conn   = get_connection()
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

        # 2. Bulk-insert into temp table (no unique constraint → no errors)
        cursor.executemany("""
            INSERT INTO #tmp_staging
                (SymbolID, BarTime, [Open], High, Low,
                 [Close], Volume, IsProcessed)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """, rows)

        # 3. MERGE into staging — skip existing (SymbolID + BarTime)
        cursor.execute(f"""
            MERGE {staging_table} AS tgt
            USING #tmp_staging AS src
                ON tgt.SymbolID = src.SymbolID AND tgt.BarTime = src.BarTime
            WHEN NOT MATCHED THEN
                INSERT (SymbolID, BarTime, [Open], High, Low,
                        [Close], Volume, IsProcessed)
                VALUES (src.SymbolID, src.BarTime, src.[Open], src.High,
                        src.Low, src.[Close], src.Volume, src.IsProcessed);
        """)
        inserted = cursor.rowcount

        conn.commit()
        logger.info("INSERT %s SymbolID=%d: %d new rows (MERGE).",
                    staging_table, symbol_id, inserted)
        return inserted

    except Exception as e:
        conn.rollback()
        logger.error("INSERT FAIL %s SymbolID=%d: %s", staging_table, symbol_id, e)
        return -1  # -1 = lỗi thật; 0 = thành công nhưng không có row mới (tất cả đã tồn tại)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ETL callers
# ---------------------------------------------------------------------------

def run_etl_direct(symbol_id: int, tf_code: str, staging_table: str) -> int:
    """
    Goi stored procedure DWH.usp_LoadDirect de nap du lieu 1:1 vao Fact_OHLCV.

    Dau ra:
    - So dong moi chen vao Fact (tinh theo before/after count).

    Anh huong he thong:
    - Ham an toan khi goi lap lai, do SP da chong duplicate.
    """
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        # Count rows in Fact before ETL
        cursor.execute(
            "SELECT COUNT(*) FROM DWH.Fact_OHLCV f "
            "JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID "
            "WHERE f.SymbolID = ? AND tf.Code = ?",
            (symbol_id, tf_code)
        )
        before = cursor.fetchone()[0]

        cursor.execute(
            "EXEC DWH.usp_LoadDirect ?, ?, ?",
            (symbol_id, tf_code, staging_table)
        )
        conn.commit()

        # Count rows in Fact after ETL
        cursor.execute(
            "SELECT COUNT(*) FROM DWH.Fact_OHLCV f "
            "JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID "
            "WHERE f.SymbolID = ? AND tf.Code = ?",
            (symbol_id, tf_code)
        )
        after = cursor.fetchone()[0]

        inserted = after - before
        logger.info("ETL Direct OK: %s SymbolID=%d TF=%s, %d new rows → Fact_OHLCV",
                    staging_table, symbol_id, tf_code, inserted)
        return inserted
    except Exception as e:
        logger.error("ETL Direct FAIL: SymbolID=%d TF=%s — %s",
                     symbol_id, tf_code, e)
        conn.rollback()
        return -1  # -1 = lỗi; 0 = ETL chạy xong nhưng không có row mới (đã tồn tại)
    finally:
        conn.close()


def run_etl_aggregate(symbol_id: int, target_tf: str, source_staging_table: str) -> None:
    """
        Goi DWH.usp_AggregateFromStaging de tong hop timeframe phai sinh.

        Quy tac tong hop:
        - M5 -> M10, M20
        - M30 -> M90
        - H3 -> H6
        - H4 -> H8

        Tac dong:
        - Tao khung thoi gian khong co san tu nguon TV.
    """
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "EXEC DWH.usp_AggregateFromStaging ?, ?, ?",
            (symbol_id, source_staging_table, target_tf)
        )
        conn.commit()
        logger.info("ETL Aggregate OK: %s → %s SymbolID=%d",
                    source_staging_table, target_tf, symbol_id)
    except Exception as e:
        logger.error("ETL Aggregate FAIL: %s → %s SymbolID=%d — %s",
                     source_staging_table, target_tf, symbol_id, e)
        conn.rollback()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Aggregate derived TFs directly from Fact_OHLCV (no staging required)
# ---------------------------------------------------------------------------

# target_tf_code → (source_tf_code, interval_minutes)
_DERIVED_TF_SPECS = {
    'M10': ('M5',   10),
    'M20': ('M5',   20),
    'M90': ('M30',  90),
    'H6':  ('H3',  360),
    'H8':  ('H4',  480),
}


def aggregate_from_fact(symbol_id: int, target_tf_code: str) -> int:
    """
    Tong hop timeframe phai sinh truc tiep tu DWH.Fact_OHLCV (khong qua staging).

    Ho tro: M10/M20/M90/H6/H8.

    Dau ra:
    - So dong moi duoc chen (0 co the do da ton tai hoac thieu source bar).

    Tai sao can ham nay:
    - Dung cho che do bo sung du lieu nhanh tu Fact ma khong can pull lai TV.
    """
    if target_tf_code not in _DERIVED_TF_SPECS:
        raise ValueError(
            f"aggregate_from_fact: unsupported target TF '{target_tf_code}'. "
            f"Supported: {list(_DERIVED_TF_SPECS)}"
        )
    src_tf_code, interval_minutes = _DERIVED_TF_SPECS[target_tf_code]

    conn   = get_connection()
    cursor = conn.cursor()
    try:
        # Resolve TimeframeIDs
        cursor.execute(
            "SELECT Code, TimeframeID FROM DWH.Dim_Timeframe WHERE Code IN (?, ?)",
            (src_tf_code, target_tf_code)
        )
        tf_map = {row[0]: row[1] for row in cursor.fetchall()}
        src_tf_id    = tf_map[src_tf_code]
        target_tf_id = tf_map[target_tf_code]

        # CTE aggregation: align BarTime to interval boundary, then
        # pick Open from first bar and Close from last bar in each group.
        # Dùng MERGE (upsert) thay vì INSERT WHERE NOT EXISTS để:
        # - Sửa các bars đã tạo sớm (premature) khi source bar thứ 2 chưa load xong
        # - Cập nhật TickCount và OHLCV mỗi lần source data đầy đủ hơn
        cursor.execute("""
            WITH src AS (
                SELECT
                    f.BarTime,
                    f.[Open],
                    f.High,
                    f.Low,
                    f.[Close],
                    f.Volume,
                    DATEADD(MINUTE,
                        (DATEDIFF(MINUTE, CAST('2000-01-01' AS DATETIME), f.BarTime) / ?) * ?,
                        CAST('2000-01-01' AS DATETIME)
                    ) AS AggBarTime
                FROM DWH.Fact_OHLCV f
                WHERE f.SymbolID = ? AND f.TimeframeID = ?
            ),
            agg AS (
                SELECT
                    AggBarTime,
                    MIN(BarTime)   AS FirstBarTime,
                    MAX(BarTime)   AS LastBarTime,
                    MAX(High) AS High,
                    MIN(Low)  AS Low,
                    SUM(Volume)    AS Volume,
                    COUNT(*)       AS SrcCount
                FROM src
                GROUP BY AggBarTime
            )
            MERGE DWH.Fact_OHLCV AS tgt
            USING (
                SELECT
                    ? AS SymbolID,
                    ? AS TimeframeID,
                    CAST(CONVERT(VARCHAR(8), a.FirstBarTime, 112) AS INT) AS DateKey,
                    a.FirstBarTime AS BarTime,
                    so.[Open],
                    a.High,
                    a.Low,
                    sc.[Close],
                    a.Volume,
                    a.SrcCount
                FROM agg a
                JOIN src so ON so.BarTime = a.FirstBarTime
                JOIN src sc ON sc.BarTime = a.LastBarTime
            ) AS src_data
            ON  tgt.SymbolID    = src_data.SymbolID
            AND tgt.TimeframeID = src_data.TimeframeID
            AND tgt.BarTime     = src_data.BarTime
            WHEN MATCHED AND (
                tgt.TickCount IS NULL OR tgt.TickCount < src_data.SrcCount
            ) THEN UPDATE SET
                tgt.[Open]  = src_data.[Open],
                tgt.High  = src_data.High,
                tgt.Low   = src_data.Low,
                tgt.[Close] = src_data.[Close],
                tgt.Volume     = src_data.Volume,
                tgt.TickCount  = src_data.SrcCount
            WHEN NOT MATCHED THEN INSERT
                (SymbolID, TimeframeID, DateKey, BarTime,
                 [Open], High, Low, [Close], Volume, TickCount)
            VALUES
                (src_data.SymbolID, src_data.TimeframeID, src_data.DateKey,
                 src_data.BarTime, src_data.[Open], src_data.High,
                 src_data.Low, src_data.[Close], src_data.Volume,
                 src_data.SrcCount);
        """, (interval_minutes, interval_minutes, symbol_id, src_tf_id,
              symbol_id, target_tf_id))

        rows = cursor.rowcount
        conn.commit()
        logger.info("aggregate_from_fact OK: %s->%s SymbolID=%d, %d rows upserted",
                    src_tf_code, target_tf_code, symbol_id, rows)
        return rows
    except Exception as e:
        conn.rollback()
        logger.error("aggregate_from_fact FAIL: %s→%s SymbolID=%d — %s",
                     src_tf_code, target_tf_code, symbol_id, e)
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def get_candle_count(symbol_id: int, tf_code: str) -> int:
    """Dem so candle hien co trong Fact_OHLCV theo symbol + timeframe."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*)
            FROM DWH.Fact_OHLCV f
            JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
            WHERE f.SymbolID = ? AND tf.Code = ?
        """, (symbol_id, tf_code))
        return cursor.fetchone()[0]
    finally:
        conn.close()


_ALL_STAGING_TABLES = [
    "SEN.TF_W",   "SEN.TF_D1",  "SEN.TF_H4",
    "SEN.TF_H3",  "SEN.TF_H2",  "SEN.TF_H1",
    "SEN.TF_M45", "SEN.TF_M30", "SEN.TF_M15", "SEN.TF_M5",
]


def purge_staging(days_to_keep: int = 7) -> dict:
    """
    Don dep staging da xu ly, chi giu lai N ngay gan nhat.

    Tai sao can:
    - Staging chi la bo dem tam.
    - Neu khong don dep dinh ky, bang se phinh to va lam cham pipeline.

    Dau ra:
    - dict {ten_bang: so_dong_da_xoa} de bao cao log.
    """
    deleted_summary = {}
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        for table in _ALL_STAGING_TABLES:
            cursor.execute(
                f"DELETE FROM {table}"
                f" WHERE IsProcessed = 1"
                f" AND BarTime < DATEADD(day, ?, GETUTCDATE())",
                (-days_to_keep,)
            )
            deleted_summary[table] = cursor.rowcount
        conn.commit()
        total = sum(deleted_summary.values())
        logger.info("Staging purge: %d rows removed (kept last %d days).",
                    total, days_to_keep)
    except Exception as e:
        conn.rollback()
        logger.error("Staging purge failed: %s", e)
    finally:
        conn.close()
    return deleted_summary


# ---------------------------------------------------------------------------
# Pipeline utility — get latest bar times for all (symbol, TF) pairs
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
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"""
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
        """, [lookback_days] + list(tf_codes))

        result = {}
        for row in cursor.fetchall():
            key = (row[0], row[1])
            result.setdefault(key, []).append((row[2], row[3], row[4]))
        logger.info("get_internal_gaps: %d (symbol, TF) pairs with raw gaps found.",
                    len(result))
        return result
    except Exception as e:
        logger.error("get_internal_gaps failed: %s", e)
        return {}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Price continuity check — find spike candles (close[i] ≠ open[i+1])
# ---------------------------------------------------------------------------

def find_price_spikes(tf_codes: list, lookback_days: int,
                      threshold_atr: float = 3.0) -> list:
    """
    Tim cac buoc nhay gia bat thuong giua hai bar lien tiep.

    For each consecutive bar pair (i, i+1) ordered by BarTime:
      jump  = |close[i] - open[i+1]|
      ATR14 = rolling 14-period average of candle range (high - low)
      spike = jump / ATR14 > threshold_atr

    Returns list of dicts sorted by severity (highest jump_atr first):
      {symbol_id, tf_code, bar_time, close, next_open, next_bar_time,
       gap_minutes, atr14, price_jump, jump_atr}

    Luu y van hanh:
    - Nen loc them gap_minutes de loai tru jump qua dem/khac phien.
    """
    if not tf_codes:
        return []
    placeholders = ",".join("?" * len(tf_codes))
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"""
            WITH bars AS (
                SELECT
                    f.SymbolID,
                    tf.Code                                           AS TFCode,
                    f.BarTime,
                    f.[Close],
                    LEAD(f.[Open]) OVER (
                        PARTITION BY f.SymbolID, f.TimeframeID
                        ORDER BY f.BarTime
                    )                                                 AS NextOpen,
                    LEAD(f.BarTime) OVER (
                        PARTITION BY f.SymbolID, f.TimeframeID
                        ORDER BY f.BarTime
                    )                                                 AS NextBarTime,
                    AVG(f.High - f.Low) OVER (
                        PARTITION BY f.SymbolID, f.TimeframeID
                        ORDER BY f.BarTime
                        ROWS BETWEEN 13 PRECEDING AND CURRENT ROW
                    )                                                 AS ATR14
                FROM DWH.Fact_OHLCV f
                JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
                WHERE f.BarTime >= DATEADD(day, -?, GETUTCDATE())
                  AND tf.Code IN ({placeholders})
            )
            SELECT
                SymbolID,
                TFCode,
                BarTime,
                [Close],
                NextOpen,
                NextBarTime,
                ATR14,
                ABS([Close] - NextOpen)                           AS PriceJump,
                ABS([Close] - NextOpen) / NULLIF(ATR14, 0)       AS JumpATR,
                DATEDIFF(MINUTE, BarTime, NextBarTime)               AS GapMinutes
            FROM bars
            WHERE NextBarTime IS NOT NULL
              AND ATR14 > 0
              AND ABS([Close] - NextOpen) / NULLIF(ATR14, 0) > ?
            ORDER BY JumpATR DESC
        """, [lookback_days] + list(tf_codes) + [threshold_atr])

        results = []
        for row in cursor.fetchall():
            results.append({
                "symbol_id":      row[0],
                "tf_code":        row[1],
                "bar_time":       row[2],
                "close":          float(row[3]),
                "next_open":      float(row[4]),
                "next_bar_time":  row[5],
                "atr14":          float(row[6]),
                "price_jump":     float(row[7]),
                "jump_atr":       float(row[8]),
                "gap_minutes":    int(row[9]) if row[9] is not None else 0,
            })
        logger.info(
            "find_price_spikes: %d discontinuities above %.1f×ATR.",
            len(results), threshold_atr,
        )
        return results
    except Exception as e:
        logger.error("find_price_spikes failed: %s", e)
        return []
    finally:
        conn.close()


def delete_fact_bars(symbol_id: int, tf_code: str) -> int:
    """
    Xoa toan bo bar cua (symbol_id, tf_code) trong Fact_OHLCV.

    Dung khi:
    - Can re-pull day du vi nghi ngo du lieu hu/lech.

    Dau ra:
    - So dong da xoa.
    """
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT TimeframeID FROM DWH.Dim_Timeframe WHERE Code = ?", (tf_code,)
        )
        row = cursor.fetchone()
        if not row:
            logger.warning("delete_fact_bars: unknown TF code '%s'", tf_code)
            return 0
        tf_id = row[0]
        cursor.execute(
            "DELETE FROM DWH.Fact_OHLCV WHERE SymbolID = ? AND TimeframeID = ?",
            (symbol_id, tf_id),
        )
        deleted = cursor.rowcount
        conn.commit()
        logger.info("delete_fact_bars: SymbolID=%d TF=%s → %d rows deleted.",
                    symbol_id, tf_code, deleted)
        return deleted
    except Exception as e:
        conn.rollback()
        logger.error("delete_fact_bars FAIL: SymbolID=%d TF=%s — %s",
                     symbol_id, tf_code, e)
        return 0
    finally:
        conn.close()


def delete_staging_bars(symbol_id: int, staging_table: str) -> int:
    """
    Xoa toan bo bar cua symbol trong mot staging table.

    Dung khi:
    - Chuan bi pull lai tu dau de loai bo staging cu/co kha nang loi.

    Dau ra:
    - So dong da xoa.
    """
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"DELETE FROM {staging_table} WHERE SymbolID = ?", (symbol_id,)
        )
        deleted = cursor.rowcount
        conn.commit()
        logger.info("delete_staging_bars: %s SymbolID=%d → %d rows deleted.",
                    staging_table, symbol_id, deleted)
        return deleted
    except Exception as e:
        conn.rollback()
        logger.error("delete_staging_bars FAIL: %s SymbolID=%d — %s",
                     staging_table, symbol_id, e)
        return 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Upsert a single OHLCV bar (update if values differ, skip if identical)
# ---------------------------------------------------------------------------

def upsert_ohlcv_bar(symbol_id: int, tf_code: str,
                     bar_time, open_p: float, high_p: float,
                     low_p: float, close_p: float, volume=None,
                     price_tol: float = 1e-4) -> str:
    """
        Upsert 1 bar vao Fact_OHLCV: them moi, cap nhat, hoac giu nguyen.

        Gia tri tra ve:
        - inserted: chua ton tai, da them moi.
        - updated: da ton tai nhung khac du lieu vuot nguong, da cap nhat.
        - ok: da ton tai va khop trong nguong sai so.
        - error: co loi.

        price_tol:
        - Nguong sai so tuong doi khi so sanh OHLC de tranh update vo ich do sai so so hoc.
    """
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        # Lookup TimeframeID
        cursor.execute(
            "SELECT TimeframeID FROM DWH.Dim_Timeframe WHERE Code = ?", (tf_code,)
        )
        row = cursor.fetchone()
        if not row:
            return "error"
        tf_id = row[0]

        # Check existing bar
        cursor.execute("""
            SELECT [Open], High, Low, [Close]
            FROM DWH.Fact_OHLCV
            WHERE SymbolID = ? AND TimeframeID = ? AND BarTime = ?
        """, (symbol_id, tf_id, bar_time))
        existing = cursor.fetchone()

        def _close(a, b):
            return abs(a - b) / max(abs(b), 1e-12) <= price_tol

        if existing is None:
            # Insert
            date_key = int(bar_time.strftime("%Y%m%d"))
            cursor.execute("""
                INSERT INTO DWH.Fact_OHLCV
                    (SymbolID, TimeframeID, DateKey, BarTime,
                     [Open], High, Low, [Close], Volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (symbol_id, tf_id, date_key, bar_time,
                  open_p, high_p, low_p, close_p, volume))
            conn.commit()
            return "inserted"

        eo, eh, el, ec = existing
        if (_close(eo, open_p) and _close(eh, high_p)
                and _close(el, low_p) and _close(ec, close_p)):
            return "ok"

        # Update
        cursor.execute("""
            UPDATE DWH.Fact_OHLCV
               SET [Open]  = ?,
                   High  = ?,
                   Low   = ?,
                   [Close] = ?,
                   Volume     = ?
             WHERE SymbolID = ? AND TimeframeID = ? AND BarTime = ?
        """, (open_p, high_p, low_p, close_p, volume,
              symbol_id, tf_id, bar_time))
        conn.commit()
        return "updated"

    except Exception as e:
        conn.rollback()
        logger.error("upsert_ohlcv_bar FAIL SymbolID=%d TF=%s BarTime=%s: %s",
                     symbol_id, tf_code, bar_time, e)
        return "error"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Delete bars in DB that no longer exist on TradingView (DB_ONLY cleanup)
# ---------------------------------------------------------------------------

def delete_ohlcv_bars(symbol_id: int, tf_code: str, bar_times: list) -> int:
    """
    Xoa mot danh sach bar_time cu the trong Fact_OHLCV.

    Dau vao:
    - bar_times: danh sach datetime can xoa.

    Dau ra:
    - Tong so dong da xoa.
    """
    if not bar_times:
        return 0
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT TimeframeID FROM DWH.Dim_Timeframe WHERE Code = ?", (tf_code,)
        )
        row = cursor.fetchone()
        if not row:
            return 0
        tf_id = row[0]

        deleted = 0
        for bt in bar_times:
            cursor.execute("""
                DELETE FROM DWH.Fact_OHLCV
                WHERE SymbolID = ? AND TimeframeID = ? AND BarTime = ?
            """, (symbol_id, tf_id, bt))
            deleted += cursor.rowcount
        conn.commit()
        logger.info("delete_ohlcv_bars: SymbolID=%d TF=%s — %d rows deleted",
                    symbol_id, tf_code, deleted)
        return deleted
    except Exception as e:
        conn.rollback()
        logger.error("delete_ohlcv_bars FAIL: SymbolID=%d TF=%s — %s",
                     symbol_id, tf_code, e)
        return 0
    finally:
        conn.close()

