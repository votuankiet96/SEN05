"""Tests for core_engine.warehouse.connection.verify_database_contract and
core_engine.warehouse.reconcile - the round-2-audit fixes for the
usp_LoadDirect version-drift blocker (contract check must fail loudly
instead of continue_and_report, and reconciliation must be able to find
and repair staging rows stuck behind a broken/skipped ETL call).

No real SQL Server is used: get_connection() is monkeypatched with a tiny
fake cursor/connection so these tests run in the no-DB/no-network suite.
"""

from __future__ import annotations

import pytest

from core_engine.warehouse import connection as warehouse_connection
from core_engine.warehouse import reconcile as warehouse_reconcile


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
    cursor = _FakeCursor(fetchone_result=(warehouse_connection.EXPECTED_CONTRACT_VERSION,))
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


# --- reconcile ------------------------------------------------------------


def test_scan_timeframe_reports_missing_rows_by_symbol(monkeypatch):
    cursor = _FakeCursor(fetchall_result=[(11, 3), (22, 1)])
    _patch_get_connection(monkeypatch, warehouse_reconcile, cursor)

    result = warehouse_reconcile.scan_timeframe("M5")

    assert result.tf_code == "M5"
    assert result.missing_before == 4
    assert result.symbols_affected == [11, 22]
    assert result.error is None


def test_scan_timeframe_unknown_code_reports_error_not_zero():
    result = warehouse_reconcile.scan_timeframe("NOT_A_TF")
    assert result.error is not None
    # An unreadable/unknown timeframe must not be mistaken for "clean".
    assert warehouse_reconcile.total_missing([result]) == 1


def test_reconcile_timeframe_scan_only_does_not_call_etl(monkeypatch):
    cursor = _FakeCursor(fetchall_result=[(11, 2)])
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
            return _FakeConnection(_FakeCursor(fetchall_result=[(11, 2)]))
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
            return _FakeConnection(_FakeCursor(fetchall_result=[(11, 1), (22, 1)]))
        return _FakeConnection(_FakeCursor(fetchall_result=[(11, 1)]))

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


def test_total_missing_sums_across_timeframes(monkeypatch):
    ok_result = warehouse_reconcile.TimeframeReconcileResult(
        tf_code="M5", staging_table="SEN.TF_M5", missing_before=0, repaired=0, missing_after=0,
    )
    bad_result = warehouse_reconcile.TimeframeReconcileResult(
        tf_code="H1", staging_table="SEN.TF_H1", missing_before=3, repaired=0, missing_after=3,
    )
    assert warehouse_reconcile.total_missing([ok_result, bad_result]) == 3
