"""Tests for core_engine.warehouse.connection.verify_database_contract and
core_engine.warehouse.reconcile - the round-2-audit fixes for the
usp_LoadDirect version-drift blocker (contract check must fail loudly
instead of continue_and_report, and reconciliation must be able to find
and repair staging rows stuck behind a broken/skipped ETL call).

No real SQL Server is used: get_connection() is monkeypatched with a tiny
fake cursor/connection so these tests run in the no-DB/no-network suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core_engine.warehouse import connection as warehouse_connection
from core_engine.warehouse import reconcile as warehouse_reconcile


def test_v3_sql_contract_resolves_datekey_through_dimension():
    sql_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "sql"
        / "10_migration_usp_loaddirect_v3_date_fence.sql"
    )
    sql = sql_path.read_text(encoding="utf-8")

    assert "INNER JOIN DWH.Dim_Date AS d" in sql
    assert "d.DateKey" in sql
    assert "DPContractVersion', @value = N'3'" in sql


def test_us500_archive_migration_verifies_before_deleting_in_one_transaction():
    sql_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "sql"
        / "11_migration_archive_us500_d1_unsupported_calendar.sql"
    )
    sql = sql_path.read_text(encoding="utf-8")

    assert "BEGIN TRANSACTION" in sql
    assert "@ExpectedRows INT = 2231" in sql
    assert "@US500SymbolID INT = 8" in sql
    assert "@Inserted <> @SourceBefore" in sql
    assert "@Verified <> @SourceBefore" in sql
    assert "DELETE s" in sql
    assert sql.index("@Verified <> @SourceBefore") < sql.index("DELETE s")
    assert "@UnsupportedAfter <> 0 OR @ArchivedTotal <> @ExpectedRows" in sql
    assert "IF XACT_STATE() <> 0 ROLLBACK TRANSACTION" in sql
    assert "$(DeploymentCommit)" in sql


class _FakeCursor:
    def __init__(self, fetchone_result=None, fetchall_result=None, raise_on_execute=None):
        self._fetchone_result = fetchone_result
        self._fetchall_result = fetchall_result if fetchall_result is not None else []
        self._raise_on_execute = raise_on_execute
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if self._raise_on_execute is not None:
            raise self._raise_on_execute
        return self

    def fetchone(self):
        return self._fetchone_result

    def fetchall(self):
        return self._fetchall_result


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def _patch_get_connection(monkeypatch, module, cursor: _FakeCursor):
    conn = _FakeConnection(cursor)
    monkeypatch.setattr(module, "get_connection", lambda: conn)
    return conn


# --- verify_database_contract -------------------------------------------------


def test_verify_database_contract_ok_when_version_matches(monkeypatch):
    cursor = _FakeCursor()
    responses = iter([(warehouse_connection.EXPECTED_CONTRACT_VERSION,), (2,)])
    cursor.fetchone = lambda: next(responses)
    conn = _patch_get_connection(monkeypatch, warehouse_connection, cursor)

    result = warehouse_connection.verify_database_contract()

    assert result["ok"] is True
    assert result["version"] == warehouse_connection.EXPECTED_CONTRACT_VERSION
    assert result["reason"] is None
    assert conn.closed is True


def test_verify_database_contract_fails_on_version_mismatch(monkeypatch):
    cursor = _FakeCursor(fetchone_result=("1",))
    _patch_get_connection(monkeypatch, warehouse_connection, cursor)

    result = warehouse_connection.verify_database_contract()

    assert result["ok"] is False
    assert result["version"] == "1"
    assert "mismatch" in result["reason"]


def test_verify_database_contract_requires_both_lock_fencing_columns(monkeypatch):
    cursor = _FakeCursor()
    responses = iter([(warehouse_connection.EXPECTED_CONTRACT_VERSION,), (1,)])
    cursor.fetchone = lambda: next(responses)
    _patch_get_connection(monkeypatch, warehouse_connection, cursor)

    result = warehouse_connection.verify_database_contract()

    assert result["ok"] is False
    assert result["lock_fencing_columns"] == 1
    assert "missing or partial" in result["reason"]


def test_verify_database_contract_fails_when_property_missing():
    # No property found -> fetchone() returns None, exactly what the stale
    # (pre-audit) production procedure would produce: it was deployed before
    # DPContractVersion tagging existed at all.
    def _use_cursor(monkeypatch):
        cursor = _FakeCursor(fetchone_result=None)
        _patch_get_connection(monkeypatch, warehouse_connection, cursor)
        return warehouse_connection.verify_database_contract()

    import _pytest.monkeypatch as _mp

    with _mp.MonkeyPatch.context() as monkeypatch:
        result = _use_cursor(monkeypatch)

    assert result["ok"] is False
    assert result["version"] is None
    assert "predates contract tagging" in result["reason"]


def test_verify_database_contract_reports_connection_failure_distinctly(monkeypatch):
    def _boom():
        raise RuntimeError("SQL Server unreachable")

    monkeypatch.setattr(warehouse_connection, "get_connection", _boom)

    result = warehouse_connection.verify_database_contract()

    assert result["ok"] is False
    assert result["version"] is None
    assert "contract check could not run" in result["reason"]


def test_connection_closes_connection_when_health_query_fails(monkeypatch):
    cursor = _FakeCursor(raise_on_execute=RuntimeError("query failed"))
    conn = _patch_get_connection(monkeypatch, warehouse_connection, cursor)

    assert warehouse_connection.test_connection() is False
    assert conn.closed is True


# --- reconcile ------------------------------------------------------------


def test_scan_timeframe_reports_missing_rows_by_symbol(monkeypatch):
    # Row shape: (SymbolID, supported_missing, supported_mismatched,
    # unsupported, unsupported_min, unsupported_max).
    cursor = _FakeCursor(fetchall_result=[(11, 3, 0, 0, None, None), (22, 1, 0, 0, None, None)])
    _patch_get_connection(monkeypatch, warehouse_reconcile, cursor)

    result = warehouse_reconcile.scan_timeframe("M5")

    assert result.tf_code == "M5"
    assert result.missing_before == 4
    assert result.symbols_affected == [11, 22]
    assert result.error is None
    assert result.unsupported_calendar_count == 0
    assert result.supported_missing_after == 4
    assert result.supported_mismatched_after == 0


def test_scan_timeframe_separates_missing_fact_from_existing_fact_mismatch(monkeypatch):
    cursor = _FakeCursor(fetchall_result=[(11, 2, 3, 0, None, None)])
    _patch_get_connection(monkeypatch, warehouse_reconcile, cursor)

    result = warehouse_reconcile.scan_timeframe("M5")

    assert result.missing_after == 5
    assert result.supported_missing_after == 2
    assert result.supported_mismatched_after == 3

    sql = " ".join(cursor.executed[0][0].split())
    assert "d.DateKey IS NOT NULL AND f.SymbolID IS NULL" in sql
    assert "d.DateKey IS NOT NULL AND f.SymbolID IS NOT NULL" in sql


def test_reconcile_scan_includes_staging_corrections_not_only_missing_keys(monkeypatch):
    cursor = _FakeCursor(fetchall_result=[])
    _patch_get_connection(monkeypatch, warehouse_reconcile, cursor)

    warehouse_reconcile.scan_timeframe("M5")

    sql = " ".join(cursor.executed[0][0].split())
    assert "LEFT JOIN DWH.Fact_OHLCV" in sql
    assert "f.SymbolID IS NULL" in sql
    for column in ("[Open]", "High", "Low", "[Close]", "Volume"):
        assert f"f.{column}" in sql
        assert f"s.{column}" in sql


def test_reconcile_scan_joins_dim_date_matching_usp_loaddirect_v3(monkeypatch):
    # The fence must use the exact same join condition as usp_LoadDirect v3
    # (scripts/sql/10_migration_usp_loaddirect_v3_date_fence.sql) so this
    # scan can never disagree with what the stored procedure actually does.
    cursor = _FakeCursor(fetchall_result=[])
    _patch_get_connection(monkeypatch, warehouse_reconcile, cursor)

    warehouse_reconcile.scan_timeframe("M5")

    sql = " ".join(cursor.executed[0][0].split())
    assert "LEFT JOIN DWH.Dim_Date" in sql
    assert "d.FullDate = CAST(s.BarTime AS DATE)" in sql
    assert "d.DateKey IS NOT NULL" in sql
    assert "d.DateKey IS NULL" in sql


def test_scan_timeframe_excludes_unsupported_calendar_rows_from_missing_by_default(monkeypatch):
    # Symbol 8 (US500) has 2 real in-range gaps plus 2231 rows entirely
    # outside Dim_Date's covered range - exactly the round-3 evidence
    # (reconcile-fact wrongly reported missing_count=2231 for US500/D1).
    cursor = _FakeCursor(
        fetchall_result=[(8, 1, 1, 2231, "1999-02-17 22:00:00", "2007-12-30 23:00:00")]
    )
    _patch_get_connection(monkeypatch, warehouse_reconcile, cursor)

    result = warehouse_reconcile.scan_timeframe("D1")

    assert result.missing_before == 2
    assert result.missing_after == 2
    assert result.symbols_affected == [8]
    assert result.unsupported_calendar_count == 2231
    assert result.unsupported_calendar_symbols == [8]
    assert result.unsupported_calendar_range == ("1999-02-17 22:00:00", "2007-12-30 23:00:00")
    assert result.counted_unsupported_as_missing is False


def test_scan_timeframe_symbol_with_only_unsupported_rows_is_not_in_symbols_affected(monkeypatch):
    # A symbol whose ENTIRE divergence is calendar-unsupported must not
    # appear in symbols_affected by default - retrying ETL for it against
    # usp_LoadDirect v3 is a guaranteed no-op, not a real repair target.
    cursor = _FakeCursor(fetchall_result=[(8, 0, 0, 2231, "1999-02-17", "2007-12-30")])
    _patch_get_connection(monkeypatch, warehouse_reconcile, cursor)

    result = warehouse_reconcile.scan_timeframe("D1")

    assert result.missing_before == 0
    assert result.symbols_affected == []
    assert result.unsupported_calendar_symbols == [8]


def test_scan_timeframe_count_unsupported_as_missing_reverts_to_strict_behavior(monkeypatch):
    cursor = _FakeCursor(
        fetchall_result=[(8, 1, 1, 2231, "1999-02-17", "2007-12-30")]
    )
    _patch_get_connection(monkeypatch, warehouse_reconcile, cursor)

    result = warehouse_reconcile.scan_timeframe("D1", count_unsupported_as_missing=True)

    assert result.missing_before == 2233
    assert result.missing_after == 2233
    assert result.symbols_affected == [8]
    assert result.counted_unsupported_as_missing is True


def test_scan_timeframe_unknown_code_reports_error_not_zero():
    result = warehouse_reconcile.scan_timeframe("NOT_A_TF")
    assert result.error is not None
    # An unreadable/unknown timeframe must not be mistaken for "clean".
    assert warehouse_reconcile.total_missing([result]) == 1


def test_reconcile_timeframe_scan_only_does_not_call_etl(monkeypatch):
    cursor = _FakeCursor(fetchall_result=[(11, 2, 0, 0, None, None)])
    _patch_get_connection(monkeypatch, warehouse_reconcile, cursor)
    called = []
    monkeypatch.setattr(warehouse_reconcile, "run_etl_direct", lambda *a, **k: called.append(a))

    result = warehouse_reconcile.reconcile_timeframe("M5", apply=False)

    assert called == []
    assert result.missing_before == 2
    assert result.repaired == 0


def test_reconcile_timeframe_apply_reruns_etl_and_reverifies(monkeypatch):
    # First scan finds 2 stuck rows for symbol 11; after "repair" a second
    # scan (the re-verification pass) reports zero -> missing_after must
    # reflect the *second* scan, proving reconcile re-checks rather than
    # assuming success.
    calls = {"n": 0}

    def _fake_get_connection():
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeConnection(_FakeCursor(fetchall_result=[(11, 2, 0, 0, None, None)]))
        return _FakeConnection(_FakeCursor(fetchall_result=[]))

    monkeypatch.setattr(warehouse_reconcile, "get_connection", _fake_get_connection)
    etl_calls = []
    monkeypatch.setattr(
        warehouse_reconcile, "run_etl_direct",
        lambda symbol_id, tf_code, staging_table, **k: etl_calls.append(symbol_id),
    )

    result = warehouse_reconcile.reconcile_timeframe("M5", apply=True)

    assert etl_calls == [11]
    assert result.missing_before == 2
    assert result.repaired == 1
    assert result.missing_after == 0


def test_reconcile_timeframe_apply_continues_past_one_symbol_failure(monkeypatch):
    calls = {"n": 0}

    def _fake_get_connection():
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeConnection(_FakeCursor(fetchall_result=[(11, 1, 0, 0, None, None), (22, 1, 0, 0, None, None)]))
        return _FakeConnection(_FakeCursor(fetchall_result=[(11, 1, 0, 0, None, None)]))

    monkeypatch.setattr(warehouse_reconcile, "get_connection", _fake_get_connection)

    def _fake_etl(symbol_id, tf_code, staging_table, **k):
        if symbol_id == 11:
            raise RuntimeError("still broken for symbol 11")

    monkeypatch.setattr(warehouse_reconcile, "run_etl_direct", _fake_etl)

    result = warehouse_reconcile.reconcile_timeframe("M5", apply=True)

    # symbol 22 repaired, symbol 11 still failing -> reflected in re-verify,
    # not swallowed by the one exception.
    assert result.repaired == 1
    assert result.missing_after == 1


def test_reconcile_timeframe_apply_does_not_call_etl_for_unsupported_only_symbol(monkeypatch):
    # Symbol 8 has only calendar-unsupported divergence -> not in
    # symbols_affected -> apply must not waste an ETL call on it.
    cursor = _FakeCursor(fetchall_result=[(8, 0, 0, 2231, "1999-02-17", "2007-12-30")])
    _patch_get_connection(monkeypatch, warehouse_reconcile, cursor)
    etl_calls = []
    monkeypatch.setattr(
        warehouse_reconcile, "run_etl_direct",
        lambda symbol_id, tf_code, staging_table, **k: etl_calls.append(symbol_id),
    )

    result = warehouse_reconcile.reconcile_timeframe("D1", apply=True)

    assert etl_calls == []
    assert result.missing_before == 0
    assert result.repaired == 0
    assert result.unsupported_calendar_count == 2231


def test_total_missing_sums_across_timeframes(monkeypatch):
    ok_result = warehouse_reconcile.TimeframeReconcileResult(
        tf_code="M5", staging_table="SEN.TF_M5", missing_before=0, repaired=0, missing_after=0,
    )
    bad_result = warehouse_reconcile.TimeframeReconcileResult(
        tf_code="H1", staging_table="SEN.TF_H1", missing_before=3, repaired=0, missing_after=3,
    )
    assert warehouse_reconcile.total_missing([ok_result, bad_result]) == 3


def test_total_missing_ignores_unsupported_calendar_count_by_default():
    # total_missing() only ever looks at missing_after, which (by default,
    # count_unsupported_as_missing=False) already excludes unsupported-
    # calendar rows - so a timeframe with a large unsupported_calendar_count
    # but zero real gaps must not push reconcile-fact's exit code to 1.
    result = warehouse_reconcile.TimeframeReconcileResult(
        tf_code="D1", staging_table="SEN.TF_D1", missing_before=0, repaired=0, missing_after=0,
        unsupported_calendar_count=2231, unsupported_calendar_symbols=[8],
        unsupported_calendar_range=("1999-02-17", "2007-12-30"),
    )
    assert warehouse_reconcile.total_missing([result]) == 0


def test_reconcile_all_propagates_count_unsupported_as_missing_flag(monkeypatch):
    seen = []

    def _fake_reconcile_timeframe(tf_code, *, apply, count_unsupported_as_missing=False):
        seen.append((tf_code, count_unsupported_as_missing))
        return warehouse_reconcile.TimeframeReconcileResult(
            tf_code=tf_code, staging_table="?", missing_before=0, repaired=0, missing_after=0,
        )

    monkeypatch.setattr(warehouse_reconcile, "reconcile_timeframe", _fake_reconcile_timeframe)

    warehouse_reconcile.reconcile_all(apply=False, tf_filter={"D1"}, count_unsupported_as_missing=True)

    assert seen == [("D1", True)]
