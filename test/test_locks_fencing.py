"""Tests for the P0-7 lock-fencing fix in core_engine.coordination.locks.

Split-brain scenario (Codex, round-2 audit): process A loses its DB
connection for longer than the lock TTL, process B legitimately acquires
the now-expired lock, then A reconnects and calls renew()/release() on the
same TaskName - which used to match B's row just as well as A's, since
renew()/release() only ever filtered by TaskName. A could then either
silently "renew" a lock it no longer holds, or delete B's active lock out
from under it.

A fake in-memory SQL Server stand-in (enough of SEN.ActiveTask's INSERT/
UPDATE/DELETE/SELECT surface to exercise LockCoordinator's real SQL text)
is used here so this runs in the no-DB test suite while still exercising
the actual WHERE-clause logic, not a mocked-away version of it.
"""

from __future__ import annotations

import time
import threading

import pytest

import core_engine.coordination.locks as locks_module
from core_engine.coordination.locks import LockCoordinator, LockLease, LockRecord
from core_engine.historical import runtime_support


class _FakeActiveTaskDB:
    """Minimal in-memory stand-in for SEN.ActiveTask with the OwnerId/Fence
    columns already migrated in (see scripts/sql/09_migration_lock_fencing.sql) -
    used to test the "fencing active" path end to end."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self._fence_seq = 0

    def new_connection(self):
        return _FakeConnection(self)


class _FakeConnection:
    def __init__(self, db: _FakeActiveTaskDB):
        self.db = db
        self.closed = False

    def cursor(self):
        return _FakeCursor(self.db)

    def commit(self):
        pass

    def close(self):
        self.closed = True


class _FakeCursor:
    def __init__(self, db: _FakeActiveTaskDB):
        self.db = db
        self.rowcount = 0
        self._last_result = None

    def execute(self, sql, params=()):
        sql_norm = " ".join(sql.split())
        params = tuple(params)

        if "sys.columns" in sql_norm:
            self._last_result = [(2,)]  # OwnerId and Fence both exist
            return self

        if sql_norm.startswith("DELETE FROM SEN.ActiveTask WHERE TaskName = ? AND ExpiresAt <= SYSUTCDATETIME()"):
            task_name = params[0]
            row = self.db.rows.get(task_name)
            self.rowcount = 0
            if row and row["expires_at"] <= time.time():
                del self.db.rows[task_name]
                self.rowcount = 1
            return self

        if sql_norm.startswith("INSERT INTO SEN.ActiveTask"):
            task_name = params[0]
            ttl_min = params[1]
            has_owner = "OwnerId" in sql_norm
            owner_id = params[-1] if has_owner else None
            if task_name in self.db.rows:
                raise _DuplicateKeyError(f"duplicate key for {task_name}")
            self.db._fence_seq += 1
            self.db.rows[task_name] = {
                "expires_at": time.time() + ttl_min * 60,
                "owner_id": owner_id,
                "fence": self.db._fence_seq,
                "payload": params[2] if len(params) > (3 if has_owner else 2) else None,
            }
            self.rowcount = 1
            return self

        if sql_norm.startswith("SELECT Fence FROM SEN.ActiveTask"):
            task_name, owner_id = params
            row = self.db.rows.get(task_name)
            self._last_result = (
                [(row["fence"],)] if row and row["owner_id"] == owner_id else []
            )
            return self

        if sql_norm.startswith("UPDATE SEN.ActiveTask SET ExpiresAt"):
            # params order: [ttl_min, (payload,) task_name,
            #                (owner_prefix,) (owner_id, fence)]
            ttl_min = params[0]
            index = 1
            has_payload_update = ", Payload = ?" in sql_norm
            if has_payload_update:
                index += 1
            task_name = params[index]
            index += 1
            owner_prefix = None
            if "Payload LIKE ?" in sql_norm:
                owner_prefix = params[index]
                index += 1
            has_fence = "OwnerId = ? AND Fence = ?" in sql_norm
            owner_id = params[index] if has_fence else None
            fence = params[index + 1] if has_fence else None
            row = self.db.rows.get(task_name)
            self.rowcount = 0
            if row and row["expires_at"] > time.time():
                if owner_prefix and not str(row.get("payload") or "").startswith(owner_prefix[:-1]):
                    self.rowcount = 0
                elif has_fence and (
                    row["owner_id"] != owner_id or row["fence"] != fence
                ):
                    self.rowcount = 0
                else:
                    row["expires_at"] = time.time() + ttl_min * 60
                    self.rowcount = 1
            return self

        if sql_norm.startswith("DELETE FROM SEN.ActiveTask WHERE TaskName = ? AND OwnerId = ? AND Fence = ?"):
            task_name, owner_id, fence = params
            row = self.db.rows.get(task_name)
            self.rowcount = 0
            if row and row["owner_id"] == owner_id and row["fence"] == fence:
                del self.db.rows[task_name]
                self.rowcount = 1
            return self

        if sql_norm.startswith("DELETE FROM SEN.ActiveTask WHERE TaskName = ?"):
            task_name = params[0]
            self.rowcount = 1 if task_name in self.db.rows else 0
            self.db.rows.pop(task_name, None)
            return self

        raise AssertionError(f"unhandled fake SQL: {sql_norm}")

    def fetchone(self):
        if self._last_result:
            return self._last_result[0]
        return None


class _DuplicateKeyError(Exception):
    pass


@pytest.fixture
def db():
    return _FakeActiveTaskDB()


def _coordinator(db: _FakeActiveTaskDB) -> LockCoordinator:
    import logging

    log = logging.getLogger("test_locks_fencing")
    log.addHandler(logging.NullHandler())
    return LockCoordinator(connection_factory=db.new_connection, logger=log)


def test_two_processes_get_different_owner_ids(db):
    a = _coordinator(db)
    b = _coordinator(db)
    assert a.owner_id != b.owner_id


def test_each_acquisition_records_a_monotonic_fence(db):
    a = _coordinator(db)
    b = _coordinator(db)
    assert a.acquire("ws_live_runtime", ttl_min=60) is True
    first_fence = a._owned_fences["ws_live_runtime"]
    db.rows["ws_live_runtime"]["expires_at"] = time.time() - 1
    assert b.acquire("ws_live_runtime", ttl_min=60) is True

    assert b._owned_fences["ws_live_runtime"] > first_fence


def test_stale_owner_cannot_renew_after_another_process_took_over(db):
    a = _coordinator(db)
    b = _coordinator(db)

    assert a.acquire("ws_live_runtime", ttl_min=60) is True

    # Simulate the lock expiring (A lost its connection for too long) and
    # B legitimately taking over.
    db.rows["ws_live_runtime"]["expires_at"] = time.time() - 1
    assert b.acquire("ws_live_runtime", ttl_min=60) is True

    # A reconnects and tries to renew what it believes is still its lock.
    renewed = a.renew("ws_live_runtime", ttl_min=60)

    assert renewed is False, "a stale owner's renew() must not succeed once another process holds the lock"
    # B's lock must be untouched.
    assert db.rows["ws_live_runtime"]["owner_id"] == b.owner_id


def test_stale_owner_cannot_release_another_processs_lock(db):
    a = _coordinator(db)
    b = _coordinator(db)

    assert a.acquire("ws_live_runtime", ttl_min=60) is True
    db.rows["ws_live_runtime"]["expires_at"] = time.time() - 1
    assert b.acquire("ws_live_runtime", ttl_min=60) is True

    released = a.release("ws_live_runtime")

    assert released is False, "a stale owner's release() must not delete another process's active lock"
    assert "ws_live_runtime" in db.rows
    assert db.rows["ws_live_runtime"]["owner_id"] == b.owner_id


def test_legitimate_owner_can_renew_and_release_its_own_lock(db):
    a = _coordinator(db)
    assert a.acquire("ws_live_runtime", ttl_min=60) is True

    assert a.renew("ws_live_runtime", ttl_min=60) is True
    assert a.release("ws_live_runtime") is True
    assert "ws_live_runtime" not in db.rows


def test_owner_prefix_targeted_release_bypasses_owner_id_fencing_by_design(db):
    # cleanup_stale_lock's administrative release (operator/self-healing
    # force-cleanup of a KNOWN stale lock) intentionally does not filter
    # by this process's own owner_id, since the row being removed
    # deliberately belongs to a different (dead) process.
    a = _coordinator(db)
    b = _coordinator(db)
    assert a.acquire("tv_historical_job", ttl_min=240, payload="run=abc123;kind=historical_job") is True
    db.rows["tv_historical_job"]["expires_at"] = time.time() - 1  # now stale

    released = b.release("tv_historical_job", owner_prefix="run=abc123;")

    assert released is True
    assert "tv_historical_job" not in db.rows


def test_release_record_cannot_delete_a_replacement_generation(db):
    a = _coordinator(db)
    b = _coordinator(db)
    assert a.acquire("tv_live_batch", ttl_min=10, payload="kind=live_batch") is True
    old = dict(db.rows["tv_live_batch"])
    record = LockRecord(
        task_name="tv_live_batch",
        started_at=None,
        expires_at=None,
        payload="kind=live_batch",
        owner_id=old["owner_id"],
        fence=old["fence"],
    )
    db.rows["tv_live_batch"]["expires_at"] = time.time() - 1
    assert b.acquire("tv_live_batch", ttl_min=10, payload="kind=live_batch") is True

    assert a.release_record(record) is False
    assert db.rows["tv_live_batch"]["owner_id"] == b.owner_id


def test_fencing_falls_back_gracefully_when_column_not_migrated_yet(db):
    # Simulate an unmigrated database: sys.columns lookup finds nothing.
    class _NoOwnerColumnDB(_FakeActiveTaskDB):
        pass

    unmigrated = _NoOwnerColumnDB()

    real_execute = _FakeCursor.execute

    def _execute_no_owner_column(self, sql, params=()):
        if "sys.columns" in " ".join(sql.split()):
            self._last_result = [(0,)]
            return self
        return real_execute(self, sql, params)

    orig = _FakeCursor.execute
    _FakeCursor.execute = _execute_no_owner_column
    try:
        a = _coordinator(unmigrated)
        assert a.acquire("ws_live_runtime", ttl_min=60) is True
        assert a._supports_owner_fencing.__self__ is a  # sanity: still a bound method
        assert a.renew("ws_live_runtime", ttl_min=60) is True
        assert a.release("ws_live_runtime") is True
    finally:
        _FakeCursor.execute = orig


def test_transient_capability_query_failure_is_not_cached_as_unfenced(db):
    coordinator = _coordinator(db)
    real_execute = _FakeCursor.execute
    calls = {"metadata": 0}

    def _flaky_execute(self, sql, params=()):
        if "sys.columns" in " ".join(sql.split()):
            calls["metadata"] += 1
            if calls["metadata"] == 1:
                raise RuntimeError("transient metadata timeout")
        return real_execute(self, sql, params)

    orig = _FakeCursor.execute
    _FakeCursor.execute = _flaky_execute
    try:
        assert coordinator.acquire("ws_live_runtime", ttl_min=60) is False
        assert coordinator._owner_fencing_available is None
        assert coordinator.acquire("ws_live_runtime", ttl_min=60) is True
        assert coordinator._owned_fences["ws_live_runtime"] >= 1
    finally:
        _FakeCursor.execute = orig


def test_historical_checkpoint_stops_after_lease_loss(db, monkeypatch):
    coordinator = _coordinator(db)
    lease = LockLease(
        task_name="tv_historical_job",
        owner="historical-pipeline",
        run_id="lost123",
        owner_prefix="run=lost123;",
        stop_event=threading.Event(),
        lost_event=threading.Event(),
        fence=42,
    )
    lease.lost_event.set()
    coordinator._local_historical_lease = lease
    monkeypatch.setattr(locks_module, "_DEFAULT", coordinator)

    with pytest.raises(
        runtime_support.HistoricalPullCancelled,
        match="lock lease lost",
    ):
        runtime_support.raise_if_cancelled(coordinator.logger, "before-write")
