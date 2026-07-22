"""Integration-level checks for live engine's staging -> Fact ack boundary.

The lower-level spool tests cannot prove where ``_db_worker`` calls ack. These
tests execute one real worker iteration with fault-injected SQL functions so a
regression that moves ack before ``run_etl_direct`` fails deterministically.
"""

from __future__ import annotations

import queue
import threading
from datetime import datetime, timedelta, timezone

import pandas as pd

from core_engine.core.live import db_worker


class _RecordingSpool:
    def __init__(self, events):
        self.events = events

    def count_unstaged(self):
        return 0

    def count(self):
        return 0

    def mark_staged(self, row_id):
        self.events.append(("mark_staged", row_id))

    def staged_snapshot_for_key(self, *_key):
        self.events.append(("snapshot",))
        return [77], "2026-07-21 10:00:00"

    def ack_staged_ids(self, row_ids):
        self.events.append(("ack", tuple(row_ids)))
        return len(row_ids)

    def release_for_retry(self, row_id, **_kwargs):
        self.events.append(("release", row_id))


def _one_valid_frame():
    ts = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
    return pd.DataFrame(
        {"open": [1.0], "high": [1.2], "low": [0.9], "close": [1.1], "volume": [10.0]},
        index=[ts],
    )


def _run_one_worker_item(monkeypatch, *, etl_raises: bool):
    events = []
    work_queue = queue.Queue()
    work_queue.put((77, 1, 704, "M5", "SEN.TF_M5", "EURUSD", _one_valid_frame()))
    shutdown = threading.Event()
    shutdown.set()

    monkeypatch.setattr(db_worker, "_db_queue", work_queue)
    monkeypatch.setattr(db_worker, "_shutdown", shutdown)
    monkeypatch.setattr(db_worker, "_spool", _RecordingSpool(events))
    monkeypatch.setattr(db_worker, "_flush_overflow_to_queue", lambda: None)
    monkeypatch.setattr(db_worker, "_write_defer_lock_active", lambda: False)
    monkeypatch.setattr(db_worker, "validate_ohlcv_df", lambda df, *_a, **_k: (df, {}))
    monkeypatch.setattr(
        db_worker,
        "insert_staging_batch",
        lambda *_a, **_k: events.append(("staging_commit",)) or 1,
    )

    def _etl(*_args, **kwargs):
        events.append(("fact_commit_attempt", kwargs.get("from_time")))
        if etl_raises:
            raise RuntimeError("fault injected inside usp_LoadDirect")
        return 1

    monkeypatch.setattr(db_worker, "_run_etl_direct_with_retry", _etl)
    monkeypatch.setattr(db_worker, "_record_db_result", lambda *_a, **_k: None)
    monkeypatch.setattr(db_worker, "_set_committed_watermark", lambda *_a, **_k: None)
    monkeypatch.setattr(db_worker, "publish_candle_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(db_worker, "_log_candle_row", lambda *_a, **_k: None)
    monkeypatch.setattr(db_worker, "_increment_data_error_counter", lambda: None)
    monkeypatch.setattr(db_worker, "_deferred_etl", {})
    monkeypatch.setattr(db_worker, "_deferred_etl_next_attempt", {})
    monkeypatch.setattr(db_worker, "_etl_item_meta", {})
    monkeypatch.setattr(db_worker, "_etl_wakeup", threading.Event())
    monkeypatch.setattr(db_worker, "_db_worker_done", threading.Event())

    db_worker._db_worker()
    db_worker._etl_worker()
    return events


def test_db_worker_acks_only_after_fact_commit(monkeypatch):
    events = _run_one_worker_item(monkeypatch, etl_raises=False)

    names = [event[0] for event in events]
    assert names.index("staging_commit") < names.index("mark_staged")
    assert names.index("snapshot") < names.index("fact_commit_attempt") < names.index("ack")
    assert ("fact_commit_attempt", "2026-07-21 10:00:00") in events


def test_fault_inside_etl_never_acks_staged_outbox_row(monkeypatch):
    events = _run_one_worker_item(monkeypatch, etl_raises=True)

    assert any(event[0] == "fact_commit_attempt" for event in events)
    assert not any(event[0] == "ack" for event in events)


def test_staging_writer_never_waits_for_fact_loader(monkeypatch):
    """A wedged M10 Fact call must not head-of-line block later staging."""
    events = []
    work_queue = queue.Queue()
    work_queue.put((77, 1, 704, "M10", "SEN.TF_M10", "US500", _one_valid_frame()))
    work_queue.put((78, 1, 705, "M5", "SEN.TF_M5", "FR40", _one_valid_frame()))
    shutdown = threading.Event()
    shutdown.set()

    class _TwoRowSpool(_RecordingSpool):
        def staged_snapshot_for_key(self, _symbol_id, tf_code, *_rest):
            row_id = 77 if tf_code == "M10" else 78
            self.events.append(("snapshot", row_id))
            return [row_id], "2026-07-21 10:00:00"

    monkeypatch.setattr(db_worker, "_db_queue", work_queue)
    monkeypatch.setattr(db_worker, "_shutdown", shutdown)
    monkeypatch.setattr(db_worker, "_spool", _TwoRowSpool(events))
    monkeypatch.setattr(db_worker, "_flush_overflow_to_queue", lambda: None)
    monkeypatch.setattr(db_worker, "validate_ohlcv_df", lambda df, *_a, **_k: (df, {}))
    monkeypatch.setattr(
        db_worker,
        "insert_staging_batch",
        lambda *_a, **_k: events.append(("staging_commit",)) or 1,
    )
    monkeypatch.setattr(
        db_worker,
        "_run_etl_direct_with_retry",
        lambda *_a, **_k: events.append(("fact_commit_attempt",)) or 1,
    )
    monkeypatch.setattr(db_worker, "_write_defer_lock_active", lambda: False)
    monkeypatch.setattr(db_worker, "_record_db_result", lambda *_a, **_k: None)
    monkeypatch.setattr(db_worker, "_set_committed_watermark", lambda *_a, **_k: None)
    monkeypatch.setattr(db_worker, "publish_candle_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(db_worker, "_log_candle_row", lambda *_a, **_k: None)
    monkeypatch.setattr(db_worker, "_deferred_etl", {})
    monkeypatch.setattr(db_worker, "_deferred_etl_next_attempt", {})
    monkeypatch.setattr(db_worker, "_etl_item_meta", {})
    monkeypatch.setattr(db_worker, "_etl_wakeup", threading.Event())
    monkeypatch.setattr(db_worker, "_db_worker_done", threading.Event())

    db_worker._db_worker()
    assert [event[0] for event in events].count("staging_commit") == 2
    assert not any(event[0] == "fact_commit_attempt" for event in events)

    db_worker._etl_worker()
    assert [event[0] for event in events].count("fact_commit_attempt") == 2


def test_fact_key_scheduler_skips_cooling_key_instead_of_head_of_line_blocking(monkeypatch):
    slow = (8, "M10", "SEN.TF_M10", "US500")
    healthy = (2, "M5", "SEN.TF_M5", "FR40")
    monkeypatch.setattr(db_worker, "_deferred_etl", {slow: 10.0, healthy: 20.0})
    monkeypatch.setattr(
        db_worker, "_deferred_etl_next_attempt", {slow: 160.0, healthy: 0.0}
    )
    monkeypatch.setattr(db_worker.time, "monotonic", lambda: 100.0)

    key, max_ts, _wait = db_worker._take_ready_fact_key()

    assert key == healthy
    assert max_ts == 20.0
    assert slow in db_worker._deferred_etl


def test_fact_worker_can_limit_hot_path_to_one_sql_attempt(monkeypatch):
    attempts = []
    monkeypatch.setattr(db_worker, "_shutdown", threading.Event())

    def _fail(*_args, **_kwargs):
        attempts.append(1)
        raise RuntimeError("fault-injected SQL timeout")

    monkeypatch.setattr(db_worker, "run_etl_direct", _fail)

    try:
        db_worker._run_etl_direct_with_retry(
            8,
            "M10",
            "SEN.TF_M10",
            "US500",
            context="deferred",
            from_time="2026-07-22 04:00:00",
            max_attempts=1,
        )
    except RuntimeError as exc:
        assert "fault-injected" in str(exc)
    else:
        raise AssertionError("fault injection must escape after one attempt")

    assert len(attempts) == 1


def test_clean_worker_exit_with_empty_queue_is_not_a_shutdown_critical(monkeypatch, caplog):
    """The worker is designed to exit itself after shutdown+drain. Guard
    the production smoke regression where that normal state emitted a
    false CRITICAL despite pending_items=0."""
    monkeypatch.setattr(db_worker.logger, "propagate", True)
    with caplog.at_level("CRITICAL", logger=db_worker.logger.name):
        unsafe = db_worker._report_db_worker_stopped_at_shutdown(0)

    assert unsafe is False
    assert caplog.records == []


def test_dead_worker_with_pending_queue_remains_shutdown_critical(monkeypatch, caplog):
    monkeypatch.setattr(db_worker.logger, "propagate", True)
    with caplog.at_level("CRITICAL", logger=db_worker.logger.name):
        unsafe = db_worker._report_db_worker_stopped_at_shutdown(3)

    assert unsafe is True
    assert any(
        "pending_items=3" in record.getMessage()
        for record in caplog.records
    )
