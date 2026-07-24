"""Tests for core_engine.core.live.outbox.LiveOutbox - the durable write-ahead
outbox every live OHLCV candle passes through before it is ever placed on
the in-memory dispatch queue (see live/engine.py's _enqueue_or_buffer).

Round-2 audit finding (Codex): the previous design only used the spool as a
last-resort RAM-overflow buffer, and its flush_to_queue() deleted a row the
instant it was handed to the in-memory queue - before that data was
actually durable in SQL Server. A crash between that delete and a
successful staging commit lost the candle permanently, and a write that
failed after retries was simply task_done()'d and dropped with no spool
fallback at all.

This file fault-injects the crash points the user asked to cover, against
the real ack boundary: a row is only acked (deleted) once BOTH
insert_staging_batch AND run_etl_direct have succeeded for it - not after
staging alone. A row that is durably staged but still awaiting its Fact
commit sits in a third state, 'staged' (see LiveOutbox's module docstring),
which is excluded from lease_batch() so it is never re-staged, but is also
NOT acked - so a crash at any point up through a successful Fact commit is
recoverable:
  1. after the SQLite commit, before RAM enqueue -> test_kill_after_persist_before_lease_survives_restart
  2. after dequeue/lease, before staging succeeds -> test_kill_after_lease_before_ack_is_recoverable_on_restart
  3. after a successful staging commit, before mark_staged() itself runs -> test_kill_after_staging_success_before_mark_staged_is_safe_to_reprocess
  4. "during the stored procedure" (staging -> Fact ETL) -> test_kill_during_etl_leaves_row_staged_and_recoverable
  5. after Fact commit, before ack -> test_kill_after_fact_commit_before_ack_recovers_via_restart_reset

An earlier version of this design acked right after the staging write
succeeded, delegating the staging->Fact gap to the in-memory _deferred_etl
retry loop plus the reconcile-fact CLI as a secondary safety net. That was
rejected on review: reconcile-fact is an operator-triggered/periodic check,
not automatic durability, and it left the live path with nearly the same
staging-to-Fact crash gap that the P0-2 historical-side fix had just
closed. The current design closes it directly instead.
"""

from __future__ import annotations

import logging
import pickle
import sqlite3
import subprocess
import sys

import pandas as pd
import pytest

from core_engine.core.live.outbox import PAYLOAD_MARKER, LiveOutbox


@pytest.fixture
def spool(tmp_path):
    log = logging.getLogger("test_spool")
    log.addHandler(logging.NullHandler())
    s = LiveOutbox(tmp_path / "spool.db", max_rows=3, logger=log)
    s.init()
    return s


def _sample_item(batch_id=1, symbol_id=101, tf_code="M5"):
    df = pd.DataFrame(
        {"open": [1.0], "close": [1.1]},
        index=[pd.Timestamp("2026-07-22 00:00:00", tz="UTC")],
    )
    return (batch_id, symbol_id, tf_code, "SEN.TF_M5", "EURUSD", df)


def _reopen(spool: LiveOutbox) -> LiveOutbox:
    """Simulate a process restart: a fresh LiveOutbox instance pointed at
    the same on-disk file, with no in-memory state carried over."""
    log = logging.getLogger("test_spool_reopened")
    log.addHandler(logging.NullHandler())
    fresh = LiveOutbox(spool.path, max_rows=spool.max_rows, logger=log)
    fresh.init()
    return fresh


def test_init_is_idempotent(spool):
    spool.init()
    spool.init()
    assert spool.count() == 0


# --- fault injection point 1: crash after SQLite commit, before enqueue ---


def test_kill_after_persist_before_lease_survives_restart(spool):
    row_id = spool.persist_pending(_sample_item(symbol_id=101))
    assert row_id is not None
    assert spool.count() == 1

    # "Crash" here: nothing further happens to this process. A fresh
    # instance (simulated restart) must still see and be able to lease it.
    restarted = _reopen(spool)
    leased = restarted.lease_batch()

    assert len(leased) == 1
    got_row_id, (batch_id, symbol_id, tf_code, staging_table, tv_symbol, df) = leased[0]
    assert got_row_id == row_id
    assert (symbol_id, tf_code, staging_table, tv_symbol) == (101, "M5", "SEN.TF_M5", "EURUSD")
    assert list(df["open"]) == [1.0]
    # Exactly one copy - no duplicate row was created by the "restart".
    assert restarted.count() == 1


# --- fault injection point 2: crash after lease, before ack -------------


def test_kill_after_lease_before_ack_is_recoverable_on_restart(spool):
    row_id = spool.persist_pending(_sample_item(symbol_id=202))
    leased = spool.lease_batch()
    assert len(leased) == 1
    assert leased[0][0] == row_id

    # "Crash" here: ack() never runs. A fresh process resets the orphaned
    # lease immediately and must recover the SAME row without duplicating
    # it. The still-running process must not reclaim an expired lease,
    # because the original delivery may merely be waiting in its RAM queue.
    assert spool.lease_batch(lease_seconds=-1) == []
    restarted = _reopen(spool)
    leased_again = restarted.lease_batch()
    assert len(leased_again) == 1
    assert leased_again[0][0] == row_id
    assert restarted.count() == 1  # still exactly one row, not duplicated


def test_lease_does_not_hand_out_a_row_whose_lease_has_not_expired(spool):
    # A genuinely in-flight lease (not yet timed out) must not be handed to
    # a second worker concurrently - that would cause double-processing.
    spool.persist_pending(_sample_item(symbol_id=303))
    first = spool.lease_batch(lease_seconds=120)
    assert len(first) == 1

    second = spool.lease_batch(lease_seconds=120)
    assert second == []  # lease still active; nothing to hand out


# --- fault injection point 3: staging succeeded, crash before mark_staged -


def test_kill_after_staging_success_before_mark_staged_is_safe_to_reprocess(spool):
    """Models: insert_staging_batch() committed in SQL Server, but the
    process died before live/engine.py's _db_worker reached
    _spool.mark_staged(). On restart the row is still 'leased' (its lease
    expires) and gets leased again, effectively "re-staged" -
    insert_staging_batch's MERGE ... WHEN MATCHED AND (values differ) makes
    re-submitting the same unchanged bars a safe no-op, so re-processing
    after this exact crash point is idempotent, not just "not lost"."""
    row_id = spool.persist_pending(_sample_item(symbol_id=404))
    leased = spool.lease_batch(lease_seconds=-1)  # already-expired lease
    assert leased[0][0] == row_id
    # (staging insert would happen here in live/engine.py; simulate the
    # crash by never calling mark_staged())

    restarted = _reopen(spool)
    recovered = restarted.lease_batch()
    assert len(recovered) == 1
    assert recovered[0][0] == row_id
    assert restarted.count() == 1


# --- fault injection point 4: crash during/after ETL, before ack ---------


def test_mark_staged_removes_row_from_lease_pool_without_deleting_it(spool):
    row_id = spool.persist_pending(_sample_item())
    spool.lease_batch()
    spool.mark_staged(row_id)

    assert spool.count() == 1  # still durable, not acked
    assert spool.count_unstaged() == 0  # but no longer in the lease pool
    assert spool.count_staged_pending_fact() == 1
    assert spool.lease_batch(lease_seconds=-1) == [], "a staged row must never be re-leased"


def test_kill_during_etl_leaves_row_staged_and_recoverable(spool):
    """Models: run_etl_direct() itself failed or the process died mid-call
    (the "during the stored procedure" crash point). The row stays
    'staged' - not lost, not silently re-staged, just waiting - and a
    fresh process picks it back up via the pending-reset in init()."""
    row_id = spool.persist_pending(_sample_item(symbol_id=505))
    spool.lease_batch()
    spool.mark_staged(row_id)
    # (run_etl_direct() would be attempted here and fail/crash; no
    # ack_staged_for_key() call happens)

    restarted = _reopen(spool)  # init() resets leftover 'staged' rows
    assert restarted.count_staged_pending_fact() == 0
    recovered = restarted.lease_batch()
    assert len(recovered) == 1
    assert recovered[0][0] == row_id
    assert restarted.count() == 1  # still exactly one copy


# --- fault injection point 5: Fact commit succeeded, crash before ack ----


def test_kill_after_fact_commit_before_ack_recovers_via_restart_reset(spool):
    """Models: run_etl_direct() actually committed to Fact_OHLCV, but the
    process died before ack_staged_for_key() ran. The row is still
    'staged' on disk. On restart it is reset to pending and re-enters the
    pipeline - insert_staging_batch's MERGE is a no-op for the unchanged
    row, and run_etl_direct's NOT EXISTS check means re-running ETL for
    already-committed data inserts/updates nothing (it is not "lost", it
    is a harmless redundant confirmation, not a duplicate)."""
    row_id = spool.persist_pending(_sample_item(symbol_id=606))
    spool.lease_batch()
    spool.mark_staged(row_id)
    # (run_etl_direct() succeeds here in the real flow; crash happens
    # before the exact staged-id snapshot is acknowledged)

    restarted = _reopen(spool)
    recovered = restarted.lease_batch()
    assert len(recovered) == 1
    assert recovered[0][0] == row_id


def test_ack_staged_snapshot_deletes_all_matching_rows_at_once(spool):
    # Two bars for the same symbol/timeframe both deferred while staged
    # (e.g. stuck behind a warehouse maintenance lock); one successful
    # run_etl_direct() call migrates both, so both must be acked together.
    id_a = spool.persist_pending(_sample_item(symbol_id=101, tf_code="M5"))
    id_b = spool.persist_pending(_sample_item(symbol_id=101, tf_code="M5"))
    spool.lease_batch()
    spool.mark_staged(id_a)
    spool.mark_staged(id_b)

    row_ids, _ = spool.staged_snapshot_for_key(101, "M5", "SEN.TF_M5", "EURUSD")
    deleted = spool.ack_staged_ids(row_ids)

    assert deleted == 2
    assert spool.count() == 0


def test_ack_staged_snapshot_does_not_touch_other_keys(spool):
    id_a = spool.persist_pending(_sample_item(symbol_id=101, tf_code="M5"))
    id_b = spool.persist_pending(_sample_item(symbol_id=202, tf_code="M15"))
    spool.lease_batch()
    spool.mark_staged(id_a)
    spool.mark_staged(id_b)

    row_ids, _ = spool.staged_snapshot_for_key(101, "M5", "SEN.TF_M5", "EURUSD")
    deleted = spool.ack_staged_ids(row_ids)

    assert deleted == 1
    assert spool.count() == 1
    remaining = spool.lease_batch(lease_seconds=-1)
    assert remaining == [], "the other row is still 'staged', not back in the lease pool"
    assert spool.count_staged_pending_fact() == 1


def test_ack_deletes_the_row_so_it_is_never_leased_again(spool):
    row_id = spool.persist_pending(_sample_item())
    spool.lease_batch()
    spool.ack(row_id)

    assert spool.count() == 0
    assert spool.lease_batch(lease_seconds=-1) == []


def test_release_for_retry_keeps_row_available_without_duplicating(spool):
    row_id = spool.persist_pending(_sample_item())
    spool.lease_batch()
    spool.release_for_retry(row_id, error="insert_staging_batch failed after retries")

    assert spool.count() == 1  # not dropped
    leased = spool.lease_batch()
    assert len(leased) == 1
    assert leased[0][0] == row_id  # same row, not a duplicate


# --- outbox-full behavior: pause, never silently drop --------------------


def test_persist_pending_returns_none_when_full_without_losing_anything(spool):
    for i in range(3):
        assert spool.persist_pending(_sample_item(symbol_id=100 + i)) is not None
    assert spool.count() == 3

    # max_rows=3: a 4th item cannot be accepted durably. The caller
    # (live/engine.py) must treat None as "pause new batches", not as "drop
    # this one" - persist_pending itself must not have partially inserted
    # anything.
    result = spool.persist_pending(_sample_item(symbol_id=999))
    assert result is None
    assert spool.count() == 3


def test_lease_batch_quarantines_corrupt_payload(spool):
    import sqlite3
    from contextlib import closing

    row_id = spool.persist_pending(_sample_item())
    with closing(sqlite3.connect(spool.path)) as con:
        con.execute("UPDATE spool SET bar_data = ?", (b"not a pickle blob",))
        con.commit()

    leased = spool.lease_batch()
    assert leased == []
    assert spool.count() == 0  # corrupt row removed from the live table, not left stuck forever

    with closing(sqlite3.connect(spool.path)) as con:
        quarantined = con.execute(
            "SELECT COUNT(*) FROM spool_quarantine WHERE original_id = ?", (row_id,)
        ).fetchone()[0]
    assert quarantined == 1


def test_decode_payload_rejects_wrong_version():
    bad_blob = pickle.dumps(
        {PAYLOAD_MARKER: True, "version": 999, "kind": "ohlcv_frame", "data": None},
        protocol=pickle.HIGHEST_PROTOCOL,
    )
    with pytest.raises(ValueError, match="unsupported spool payload version"):
        LiveOutbox._decode_payload(bad_blob, payload_version=999)


def test_decode_payload_rejects_legacy_bare_object_at_version_0():
    df = pd.DataFrame({"open": [3.0]})
    bare_blob = pickle.dumps(df, protocol=pickle.HIGHEST_PROTOCOL)
    with pytest.raises(ValueError, match="missing spool payload envelope"):
        LiveOutbox._decode_payload(bare_blob, payload_version=0)


# --- cleanup_old must never expire an un-acked row ------------------------


def test_cleanup_old_never_deletes_staged_rows_regardless_of_age(spool):
    row_id = spool.persist_pending(_sample_item())
    spool.lease_batch()
    spool.mark_staged(row_id)
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(spool.path)) as con:
        con.execute("UPDATE spool SET created_at = datetime('now', '-30 days') WHERE id=?", (row_id,))
        con.commit()

    deleted = spool.cleanup_old(hours=48)
    assert deleted == 0
    assert spool.count() == 1  # still there - a row awaiting Fact commit is never age-expired


def test_cleanup_old_never_deletes_pending_or_leased_rows_regardless_of_age(spool):
    row_id = spool.persist_pending(_sample_item())
    import sqlite3
    from contextlib import closing

    # Backdate the row to look 30 days old.
    with closing(sqlite3.connect(spool.path)) as con:
        con.execute("UPDATE spool SET created_at = datetime('now', '-30 days') WHERE id=?", (row_id,))
        con.commit()

    deleted = spool.cleanup_old(hours=48)
    assert deleted == 0
    assert spool.count() == 1  # still there - only quarantine entries expire by age


def test_cleanup_old_prunes_stale_quarantine_entries(spool):
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(spool.path)) as con:
        con.execute(
            "INSERT INTO spool_quarantine "
            "(original_id, symbol_id, tf_code, staging_table, tv_symbol, error, created_at, quarantined_at) "
            "VALUES (1, 101, 'M5', 'SEN.TF_M5', 'EURUSD', 'x', "
            "datetime('now', '-72 hours'), datetime('now', '-72 hours'))"
        )
        con.commit()

    deleted = spool.cleanup_old(hours=48)
    assert deleted == 1


def test_count_returns_none_when_db_file_is_unreachable(tmp_path):
    log = logging.getLogger("test_spool_missing")
    log.addHandler(logging.NullHandler())
    # A directory path where a file is expected makes sqlite3.connect fail.
    bogus_path = tmp_path / "not_a_real_dir" / "sub" / "spool.db"
    s = LiveOutbox(bogus_path, max_rows=10, logger=log)
    assert s.count() is None


def test_health_snapshot_reports_count_and_oldest_row_age(spool):
    row_id = spool.persist_pending(_sample_item())
    assert row_id is not None
    with sqlite3.connect(spool.path) as con:
        con.execute(
            "UPDATE spool SET created_at = datetime('now', '-20 minutes') WHERE id=?",
            (row_id,),
        )
        con.commit()

    count, oldest_age_seconds = spool.health_snapshot()

    assert count == 1
    assert oldest_age_seconds is not None
    assert 19 * 60 <= oldest_age_seconds <= 21 * 60


def test_health_snapshot_is_empty_without_pending_rows(spool):
    assert spool.health_snapshot() == (0, None)


def test_restart_immediately_recovers_leased_and_staged_rows(spool):
    leased_id = spool.persist_pending(_sample_item(symbol_id=701))
    staged_id = spool.persist_pending(_sample_item(symbol_id=702))
    assert spool.claim_for_dispatch(leased_id) is True
    assert spool.claim_for_dispatch(staged_id) is True
    spool.mark_staged(staged_id)

    restarted = _reopen(spool)
    recovered = restarted.lease_batch()

    assert {row_id for row_id, _item in recovered} == {leased_id, staged_id}


def test_process_death_mid_state_transaction_rolls_back_and_restart_recovers(spool):
    """Exercise a real process death between the state UPDATE and COMMIT.

    SQLite must either commit the whole ``leased -> staged`` transition or
    retain the previous ``leased`` row.  ``LiveOutbox.init()`` deliberately
    resets both possible states, so neither side of that atomic boundary can
    strand or lose the candle.
    """
    row_id = spool.persist_pending(_sample_item(symbol_id=705))
    assert spool.claim_for_dispatch(row_id) is True

    crash_script = """
import os
import sqlite3
import sys

con = sqlite3.connect(sys.argv[1])
con.execute("BEGIN IMMEDIATE")
con.execute(
    "UPDATE spool SET status='staged', lease_id=NULL, lease_until=NULL "
    "WHERE id=? AND status='leased'",
    (int(sys.argv[2]),),
)
os._exit(91)
"""
    result = subprocess.run(
        [sys.executable, "-c", crash_script, str(spool.path), str(row_id)],
        check=False,
        timeout=10,
    )
    assert result.returncode == 91

    with sqlite3.connect(spool.path) as con:
        status = con.execute("SELECT status FROM spool WHERE id=?", (row_id,)).fetchone()[0]
    assert status == "leased", "the uncommitted staged transition must be rolled back atomically"

    restarted = _reopen(spool)
    recovered = restarted.lease_batch()
    assert [recovered_id for recovered_id, _item in recovered] == [row_id]


def test_claim_for_dispatch_prevents_recovery_queue_from_leasing_same_row(spool):
    row_id = spool.persist_pending(_sample_item(symbol_id=703))

    assert spool.claim_for_dispatch(row_id) is True
    assert spool.lease_batch(lease_seconds=-1) == []


def test_ack_exact_staged_ids_does_not_ack_row_staged_after_etl_snapshot(spool):
    first = spool.persist_pending(_sample_item(symbol_id=704))
    second = spool.persist_pending(_sample_item(symbol_id=704))
    assert spool.claim_for_dispatch(first) is True
    spool.mark_staged(first)

    # This is the exact set that existed immediately before the successful
    # ETL call began. A concurrent worker stages another row only after the
    # SP's source scan, so key-wide ack would incorrectly delete it.
    covered_ids, _ = spool.staged_snapshot_for_key(704, "M5", "SEN.TF_M5", "EURUSD")
    assert spool.claim_for_dispatch(second) is True
    spool.mark_staged(second)

    assert spool.ack_staged_ids(covered_ids) == 1
    assert spool.count_staged_pending_fact() == 1
    remaining_ids, _ = spool.staged_snapshot_for_key(704, "M5", "SEN.TF_M5", "EURUSD")
    assert remaining_ids == [second]


def test_staged_snapshot_uses_earliest_timestamp_from_exact_covered_rows(spool):
    newer = pd.DataFrame(
        {"open": [2.0], "close": [2.1]},
        index=[pd.Timestamp("2026-07-22 02:05:00", tz="UTC")],
    )
    older = pd.DataFrame(
        {"open": [1.0], "close": [1.1]},
        index=[pd.Timestamp("2026-07-21 21:10:00")],
    )
    newer_id = spool.persist_pending(
        (1, 704, "M5", "SEN.TF_M5", "EURUSD", newer)
    )
    older_id = spool.persist_pending(
        (1, 704, "M5", "SEN.TF_M5", "EURUSD", older)
    )
    assert spool.claim_for_dispatch(newer_id) is True
    spool.mark_staged(newer_id)
    assert spool.claim_for_dispatch(older_id) is True
    spool.mark_staged(older_id)

    row_ids, from_time = spool.staged_snapshot_for_key(
        704, "M5", "SEN.TF_M5", "EURUSD"
    )

    assert row_ids == [newer_id, older_id]
    assert from_time == "2026-07-21 21:10:00"
