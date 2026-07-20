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

import core_engine.live.engine as live_engine


class _RecordingSpool:
    def __init__(self, events):
        self.events = events

    def count_unstaged(self):
        return 0

    def mark_staged(self, row_id):
        self.events.append(("mark_staged", row_id))

    def staged_ids_for_key(self, *_key):
        self.events.append(("snapshot",))
        return [77]

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

    monkeypatch.setattr(live_engine, "_db_queue", work_queue)
    monkeypatch.setattr(live_engine, "_shutdown", shutdown)
    monkeypatch.setattr(live_engine, "_spool", _RecordingSpool(events))
    monkeypatch.setattr(live_engine, "_flush_overflow_to_queue", lambda: None)
    monkeypatch.setattr(live_engine, "_write_defer_lock_active", lambda: False)
    monkeypatch.setattr(live_engine, "validate_ohlcv_df", lambda df, *_a, **_k: (df, {}))
    monkeypatch.setattr(
        live_engine,
        "insert_staging_batch",
        lambda *_a, **_k: events.append(("staging_commit",)) or 1,
    )

    def _etl(*_args, **_kwargs):
        events.append(("fact_commit_attempt",))
        if etl_raises:
            raise RuntimeError("fault injected inside usp_LoadDirect")
        return 1

    monkeypatch.setattr(live_engine, "_run_etl_direct_with_retry", _etl)
    monkeypatch.setattr(live_engine, "_record_db_result", lambda *_a, **_k: None)
    monkeypatch.setattr(live_engine, "_record_etl_direct_error", lambda *_a, **_k: None)
    monkeypatch.setattr(live_engine, "_set_committed_watermark", lambda *_a, **_k: None)
    monkeypatch.setattr(live_engine, "publish_candle_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(live_engine, "_log_candle_row", lambda *_a, **_k: None)
    monkeypatch.setattr(live_engine, "_increment_data_error_counter", lambda: None)
    monkeypatch.setattr(live_engine, "_deferred_etl", {})
    monkeypatch.setattr(live_engine, "_deferred_etl_next_attempt", {})

    live_engine._db_worker()
    return events


def test_db_worker_acks_only_after_fact_commit(monkeypatch):
    events = _run_one_worker_item(monkeypatch, etl_raises=False)

    names = [event[0] for event in events]
    assert names.index("staging_commit") < names.index("mark_staged")
    assert names.index("snapshot") < names.index("fact_commit_attempt") < names.index("ack")


def test_fault_inside_etl_never_acks_staged_outbox_row(monkeypatch):
    events = _run_one_worker_item(monkeypatch, etl_raises=True)

    assert any(event[0] == "fact_commit_attempt" for event in events)
    assert not any(event[0] == "ack" for event in events)
