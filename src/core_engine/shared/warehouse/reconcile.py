"""Staging -> Fact reconciliation: find and repair missing or stale Fact
candles stuck behind a failed/skipped ETL call.

Two known ways a staging row can end up "IsProcessed=1 but never reached
Fact_OHLCV" without raising loudly at the time:
  1. usp_LoadDirect failed or was running a stale/broken contract version
     (see core_engine.shared.warehouse.connection.verify_database_contract).
  2. historical/pipeline.py's _write_ohlcv_frame skipped run_etl_direct
     entirely because insert_staging_batch's MERGE returned 0 rows (the
     bars were already staged unchanged from a prior partial run) - fixed
     going forward by always calling ETL, but old databases can still
     carry rows stuck from before that fix.

A THIRD case looks identical to (1)/(2) from a plain "Fact row missing"
scan but is NOT a bug: usp_LoadDirect v4 (scripts/sql/12_migration_usp_
loaddirect_v3_date_fence.sql) resolves each staging row's Fact DateKey by
joining DWH.Dim_Date on the row's calendar date, and deliberately never
inserts a row whose date has no Dim_Date entry (this is what stops a
single out-of-calendar bar from aborting an entire ETL transaction via
FK_Fact_Date, which is what v2 did). Those rows will NEVER reach Fact
until Dim_Date is extended to cover them - re-running ETL against them
forever is a guaranteed no-op, not something a fixed number of retries
will resolve.

This module therefore separates staging/Fact divergence into two buckets:
  - "in range": the row's date IS covered by Dim_Date, so a missing/
    stale Fact row here is a real bug. This is what missing_before/
    missing_after/symbols_affected mean, unchanged from before - callers
    (reconcile-fact's exit code, total_missing()) keep working the same
    way with no config change required.
  - "unsupported calendar": the row's date is outside Dim_Date's covered
    range entirely. Reported separately via unsupported_calendar_count/
    unsupported_calendar_symbols/unsupported_calendar_range and NEVER
    folded into missing_before/missing_after by default, so reconcile-fact
    does not fail a deploy over data usp_LoadDirect is intentionally
    fencing off.

Whether to fold unsupported-calendar rows back into missing_before/
missing_after (the pre-fence behavior) is controlled by the
count_unsupported_as_missing flag on scan_timeframe/reconcile_timeframe/
reconcile_all (also exposed as `reconcile-fact --count-unsupported-as-
missing`), defaulting to False. This module does not decide - and must
not decide - whether to extend DWH.Dim_Date backward or purge the
out-of-range staging rows; that is an explicit business/data decision
left to the operator (see docs/OPERATOR_RUNBOOK.md and the reconcile-fact
CLI's printed guidance).

This module is read-mostly by default (`--apply` is required to actually
call run_etl_direct) so it is safe to run against production for a status
check at any time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core_engine.settings import TF_DISPLAY_ORDER, TF_STAGING
from core_engine.shared.warehouse.connection import get_connection
from core_engine.shared.warehouse.writer import run_etl_direct


@dataclass
class TimeframeReconcileResult:
    tf_code: str
    staging_table: str
    missing_before: int
    repaired: int
    missing_after: int | None
    symbols_affected: list[int] = field(default_factory=list)
    error: str | None = None
    unsupported_calendar_count: int = 0
    unsupported_calendar_symbols: list[int] = field(default_factory=list)
    unsupported_calendar_range: tuple[str, str] | None = None
    counted_unsupported_as_missing: bool = False
    supported_missing_before: int = 0
    supported_mismatched_before: int = 0
    supported_missing_after: int | None = 0
    supported_mismatched_after: int | None = 0


@dataclass
class _SymbolDivergence:
    supported_missing: int = 0
    supported_mismatched: int = 0
    unsupported: int = 0
    unsupported_min: str | None = None
    unsupported_max: str | None = None


def _divergence_by_symbol(cursor, staging_table: str, tf_code: str) -> dict[int, _SymbolDivergence]:
    """Return, per symbol, staging rows whose Fact key is absent or whose
    OHLCV differs - split into calendar-in-range vs. calendar-unsupported.

    The in-range bucket retains the established ``missing_*`` meaning for
    CLI compatibility, but it represents all staging-to-Fact divergence, not
    just missing keys. A key-only NOT EXISTS scan misses the correction
    crash case: staging can contain newer OHLCV values while Fact already
    has the same key with old values, and usp_LoadDirect is responsible for
    updating it.

    The LEFT JOIN to DWH.Dim_Date mirrors usp_LoadDirect v4's own join
    condition exactly (``d.FullDate = CAST(BarTime AS DATE)``) so this scan
    can never disagree with what the stored procedure will actually do.
    """
    cursor.execute(
        f"""
        SELECT
            s.SymbolID,
            SUM(CASE
                    WHEN d.DateKey IS NOT NULL AND f.SymbolID IS NULL THEN 1
                    ELSE 0
                END) AS supported_missing,
            SUM(CASE
                    WHEN d.DateKey IS NOT NULL AND f.SymbolID IS NOT NULL THEN 1
                    ELSE 0
                END) AS supported_mismatched,
            SUM(CASE WHEN d.DateKey IS NULL THEN 1 ELSE 0 END) AS unsupported,
            MIN(CASE WHEN d.DateKey IS NULL THEN s.BarTime END) AS unsupported_min,
            MAX(CASE WHEN d.DateKey IS NULL THEN s.BarTime END) AS unsupported_max
        FROM {staging_table} s
        JOIN DWH.Dim_Timeframe tf ON tf.Code = ?
        LEFT JOIN DWH.Fact_OHLCV f
          ON f.SymbolID = s.SymbolID
         AND f.TimeframeID = tf.TimeframeID
         AND f.BarTime = s.BarTime
        LEFT JOIN DWH.Dim_Date d
          ON d.FullDate = CAST(s.BarTime AS DATE)
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
    result: dict[int, _SymbolDivergence] = {}
    for row in cursor.fetchall():
        (
            symbol_id,
            supported_missing,
            supported_mismatched,
            unsupported,
            unsupported_min,
            unsupported_max,
        ) = row
        result[int(symbol_id)] = _SymbolDivergence(
            supported_missing=int(supported_missing or 0),
            supported_mismatched=int(supported_mismatched or 0),
            unsupported=int(unsupported or 0),
            unsupported_min=str(unsupported_min) if unsupported_min is not None else None,
            unsupported_max=str(unsupported_max) if unsupported_max is not None else None,
        )
    return result


def scan_timeframe(tf_code: str, *, count_unsupported_as_missing: bool = False) -> TimeframeReconcileResult:
    """Count staging rows that never made it to Fact for one timeframe.

    By default, rows outside DWH.Dim_Date's covered range are excluded from
    missing_before/missing_after/symbols_affected (they are reported
    separately instead) since usp_LoadDirect v4 will never insert them
    regardless of how many times ETL is retried. Pass
    count_unsupported_as_missing=True to fold them back in (the pre-fence
    behavior), e.g. for an operator who wants a strict/full divergence
    count regardless of cause.
    """
    staging_table = TF_STAGING.get(tf_code)
    if not staging_table:
        return TimeframeReconcileResult(
            tf_code=tf_code, staging_table="?", missing_before=0, repaired=0,
            missing_after=0, error=f"unknown timeframe code {tf_code!r}",
        )
    conn = get_connection()
    try:
        cursor = conn.cursor()
        by_symbol = _divergence_by_symbol(cursor, staging_table, tf_code)

        in_range_symbols = sorted(
            sid
            for sid, d in by_symbol.items()
            if d.supported_missing > 0 or d.supported_mismatched > 0
        )
        unsupported_symbols = sorted(sid for sid, d in by_symbol.items() if d.unsupported > 0)
        supported_missing_total = sum(d.supported_missing for d in by_symbol.values())
        supported_mismatched_total = sum(d.supported_mismatched for d in by_symbol.values())
        unsupported_total = sum(d.unsupported for d in by_symbol.values())

        mins = [d.unsupported_min for d in by_symbol.values() if d.unsupported_min]
        maxs = [d.unsupported_max for d in by_symbol.values() if d.unsupported_max]
        unsupported_range = (min(mins), max(maxs)) if mins and maxs else None

        if count_unsupported_as_missing:
            missing_total = supported_missing_total + supported_mismatched_total + unsupported_total
            affected_symbols = sorted(set(in_range_symbols) | set(unsupported_symbols))
        else:
            missing_total = supported_missing_total + supported_mismatched_total
            affected_symbols = in_range_symbols

        return TimeframeReconcileResult(
            tf_code=tf_code,
            staging_table=staging_table,
            missing_before=missing_total,
            repaired=0,
            missing_after=missing_total,
            symbols_affected=affected_symbols,
            unsupported_calendar_count=unsupported_total,
            unsupported_calendar_symbols=unsupported_symbols,
            unsupported_calendar_range=unsupported_range,
            counted_unsupported_as_missing=count_unsupported_as_missing,
            supported_missing_before=supported_missing_total,
            supported_mismatched_before=supported_mismatched_total,
            supported_missing_after=supported_missing_total,
            supported_mismatched_after=supported_mismatched_total,
        )
    except Exception as exc:
        return TimeframeReconcileResult(
            tf_code=tf_code, staging_table=staging_table, missing_before=0,
            repaired=0, missing_after=None, error=str(exc),
        )
    finally:
        conn.close()


def reconcile_timeframe(
    tf_code: str, *, apply: bool, count_unsupported_as_missing: bool = False
) -> TimeframeReconcileResult:
    """Scan one timeframe and, if apply=True, re-run ETL per affected symbol.

    symbols_affected (and therefore which symbols get an ETL retry under
    apply=True) excludes symbols whose only divergence is calendar-
    unsupported rows, unless count_unsupported_as_missing=True - retrying
    ETL for a symbol that has nothing but out-of-calendar rows is a
    guaranteed no-op against usp_LoadDirect v4.

    Re-verifies the missing count after applying so the caller can trust
    missing_after without a second pass. Never raises for a per-symbol ETL
    failure - those are reflected by a non-zero missing_after count instead,
    so one broken symbol does not abort reconciliation for the rest.
    """
    before = scan_timeframe(tf_code, count_unsupported_as_missing=count_unsupported_as_missing)
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

    after = scan_timeframe(tf_code, count_unsupported_as_missing=count_unsupported_as_missing)
    return TimeframeReconcileResult(
        tf_code=tf_code,
        staging_table=before.staging_table,
        missing_before=before.missing_before,
        repaired=repaired,
        missing_after=after.missing_after,
        symbols_affected=after.symbols_affected,
        unsupported_calendar_count=after.unsupported_calendar_count,
        unsupported_calendar_symbols=after.unsupported_calendar_symbols,
            unsupported_calendar_range=after.unsupported_calendar_range,
            counted_unsupported_as_missing=count_unsupported_as_missing,
            supported_missing_before=before.supported_missing_before,
            supported_mismatched_before=before.supported_mismatched_before,
            supported_missing_after=after.supported_missing_after,
            supported_mismatched_after=after.supported_mismatched_after,
        )


def reconcile_all(
    *, apply: bool, tf_filter: set[str] | None = None, count_unsupported_as_missing: bool = False
) -> list[TimeframeReconcileResult]:
    """Scan (and optionally repair) every timeframe, or a filtered subset."""
    codes = [tf for tf in TF_DISPLAY_ORDER if tf in TF_STAGING and (not tf_filter or tf in tf_filter)]
    return [
        reconcile_timeframe(tf_code, apply=apply, count_unsupported_as_missing=count_unsupported_as_missing)
        for tf_code in codes
    ]


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
