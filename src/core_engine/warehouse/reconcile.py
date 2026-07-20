"""Staging -> Fact reconciliation: find and repair missing or stale Fact
candles stuck behind a failed/skipped ETL call.

Two known ways a staging row can end up "IsProcessed=1 but never reached
Fact_OHLCV" without raising loudly at the time:
  1. usp_LoadDirect failed or was running a stale/broken contract version
     (see core_engine.warehouse.connection.verify_database_contract).
  2. historical/pipeline.py's _write_ohlcv_frame skipped run_etl_direct
     entirely because insert_staging_batch's MERGE returned 0 rows (the
     bars were already staged unchanged from a prior partial run) - fixed
     going forward by always calling ETL, but old databases can still
     carry rows stuck from before that fix.

This module is read-mostly by default (`--apply` is required to actually
call run_etl_direct) so it is safe to run against production for a status
check at any time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core_engine.settings import TF_DISPLAY_ORDER, TF_STAGING
from core_engine.warehouse.connection import get_connection
from core_engine.warehouse.writer import run_etl_direct


@dataclass
class TimeframeReconcileResult:
    tf_code: str
    staging_table: str
    missing_before: int
    repaired: int
    missing_after: int | None
    symbols_affected: list[int] = field(default_factory=list)
    error: str | None = None


def _missing_count_by_symbol(cursor, staging_table: str, tf_code: str) -> dict[int, int]:
    """Return staging rows whose Fact key is absent or whose OHLCV differs.

    The public result fields retain their established ``missing_*`` names for
    CLI compatibility, but they represent all staging-to-Fact divergence. A
    key-only NOT EXISTS scan misses the correction crash case: staging can
    contain newer OHLCV values while Fact already has the same key with old
    values, and usp_LoadDirect v2 is responsible for updating it.
    """
    cursor.execute(
        f"""
        SELECT s.SymbolID, COUNT(*)
        FROM {staging_table} s
        JOIN DWH.Dim_Timeframe tf ON tf.Code = ?
        LEFT JOIN DWH.Fact_OHLCV f
          ON f.SymbolID = s.SymbolID
         AND f.TimeframeID = tf.TimeframeID
         AND f.BarTime = s.BarTime
        WHERE s.IsProcessed = 1
          AND (
              f.SymbolID IS NULL
              OR f.[Open] <> s.[Open]
              OR f.High <> s.High
              OR f.Low <> s.Low
              OR f.[Close] <> s.[Close]
              OR ISNULL(f.Volume, -1) <> ISNULL(s.Volume, -1)
          )
        GROUP BY s.SymbolID
        """,
        (tf_code,),
    )
    return {int(row[0]): int(row[1]) for row in cursor.fetchall()}


def scan_timeframe(tf_code: str) -> TimeframeReconcileResult:
    """Count staging rows that never made it to Fact for one timeframe."""
    staging_table = TF_STAGING.get(tf_code)
    if not staging_table:
        return TimeframeReconcileResult(
            tf_code=tf_code, staging_table="?", missing_before=0, repaired=0,
            missing_after=0, error=f"unknown timeframe code {tf_code!r}",
        )
    conn = get_connection()
    try:
        cursor = conn.cursor()
        by_symbol = _missing_count_by_symbol(cursor, staging_table, tf_code)
        total = sum(by_symbol.values())
        return TimeframeReconcileResult(
            tf_code=tf_code,
            staging_table=staging_table,
            missing_before=total,
            repaired=0,
            missing_after=total,
            symbols_affected=sorted(by_symbol),
        )
    except Exception as exc:
        return TimeframeReconcileResult(
            tf_code=tf_code, staging_table=staging_table, missing_before=0,
            repaired=0, missing_after=None, error=str(exc),
        )
    finally:
        conn.close()


def reconcile_timeframe(tf_code: str, *, apply: bool) -> TimeframeReconcileResult:
    """Scan one timeframe and, if apply=True, re-run ETL per affected symbol.

    Re-verifies the missing count after applying so the caller can trust
    missing_after without a second pass. Never raises for a per-symbol ETL
    failure - those are reflected by a non-zero missing_after count instead,
    so one broken symbol does not abort reconciliation for the rest.
    """
    before = scan_timeframe(tf_code)
    if before.error or not before.symbols_affected or not apply:
        return before

    repaired = 0
    for symbol_id in before.symbols_affected:
        try:
            run_etl_direct(
                symbol_id,
                tf_code,
                before.staging_table,
                source="reconcile_fact",
            )
            repaired += 1
        except Exception:
            continue

    after = scan_timeframe(tf_code)
    return TimeframeReconcileResult(
        tf_code=tf_code,
        staging_table=before.staging_table,
        missing_before=before.missing_before,
        repaired=repaired,
        missing_after=after.missing_after,
        symbols_affected=after.symbols_affected,
    )


def reconcile_all(*, apply: bool, tf_filter: set[str] | None = None) -> list[TimeframeReconcileResult]:
    """Scan (and optionally repair) every timeframe, or a filtered subset."""
    codes = [tf for tf in TF_DISPLAY_ORDER if tf in TF_STAGING and (not tf_filter or tf in tf_filter)]
    return [reconcile_timeframe(tf_code, apply=apply) for tf_code in codes]


def total_missing(results: list[TimeframeReconcileResult]) -> int:
    """Sum missing_after across results; treats an unreadable timeframe as non-zero
    (unknown is not the same as verified-clean) so callers cannot mistake a scan
    failure for a clean reconciliation."""
    total = 0
    for r in results:
        if r.error or r.missing_after is None:
            total += 1
        else:
            total += r.missing_after
    return total
