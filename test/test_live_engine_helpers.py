"""Targeted tests for the testable pieces of core_engine.core.live.engine.

live/engine.py is the live OHLCV fetch engine: ~3,900 lines with heavy
module-level shared state (queues, locks, a disk spool) built for a 24/7
process, not for unit testing. Importing it already has side effects (it
creates its own log files and a SQLite spool file under runtime/), which
the import-all smoke test already exercises.

These tests target two things that are safe and valuable to pin down
before attempting a further split of this module:

1. Pure helper functions with no shared-state dependency
   (_is_token_error, _fmt_bar_time_utc).
2. _enqueue_or_buffer's queued -> buffered -> spooled -> rejected ladder -
   the last line of defense against silently dropping a live bar. A past
   bug here (`enqueue_status is False`, fixed separately) shows this path
   is easy to get subtly wrong, so it is worth a regression test. The
   module-level queue/buffer/spool it touches are monkeypatched to small,
   temp-file-backed stand-ins so the test drives all four outcomes
   without needing 2000+ real items or a shared spool file.
"""

from __future__ import annotations

import logging
import queue as queue_mod
from unittest.mock import patch

import pytest

from core_engine.core.live import engine as live_engine
from core_engine.core.live import delivery
from core_engine.core.live import fetcher
from core_engine.core.live.outbox import LiveOutbox


def test_is_token_error_matches_configured_keywords():
    assert fetcher._is_token_error("error", "session is unauthorized") is True
    assert fetcher._is_token_error("critical_error", "auth_error occurred") is True


def test_is_token_error_ignores_non_error_message_types():
    assert fetcher._is_token_error("info", "unauthorized") is False


def test_is_token_error_false_when_keyword_absent():
    assert fetcher._is_token_error("error", "connection reset") is False


def test_live_main_notification_trace_paths_are_bound():
    # main() builds startup/shutdown notification traces after background
    # workers start. A missing import here raises before the smoke timer and
    # leaves the non-daemon DB worker alive after the main stack unwinds.
    assert live_engine.WS_LIVE_LOG.name == "live_fetching.log"
    assert live_engine.WS_LIVE_STATE.name == "ws_live_state.json"


def test_fmt_bar_time_utc_formats_epoch_seconds():
    # 2024-01-01 00:00:00 UTC
    assert delivery._fmt_bar_time_utc(1704067200) == "00:00 UTC"


def test_fmt_bar_time_utc_falls_back_to_str_on_bad_input():
    assert delivery._fmt_bar_time_utc("not-a-timestamp") == "not-a-timestamp"


@pytest.fixture
def isolated_write_path(tmp_path, monkeypatch):
    """Give _enqueue_or_buffer small, private stand-ins for its shared state.

    Swaps the module-level _db_queue/_overflow_buf/_spool for tiny,
    test-owned equivalents so the queued/buffered/spooled/rejected ladder
    can be driven end-to-end without needing thousands of real items or
    touching the process-wide spool file.
    """
    small_queue: queue_mod.Queue = queue_mod.Queue(maxsize=1)
    small_overflow: list = []
    log = logging.getLogger("test_live_engine_spool")
    log.addHandler(logging.NullHandler())
    small_spool = LiveOutbox(tmp_path / "test_spool.db", max_rows=1, logger=log)
    small_spool.init()

    monkeypatch.setattr(delivery, "_db_queue", small_queue)
    monkeypatch.setattr(delivery, "_overflow_buf", small_overflow)
    monkeypatch.setattr(delivery, "_spool", small_spool)
    monkeypatch.setattr(delivery, "OVERFLOW_BUFFER_MAX", 1)
    monkeypatch.setattr(delivery, "MAX_SPOOL_ROWS", 1)
    monkeypatch.setattr(live_engine, "_spool", small_spool)
    monkeypatch.setattr(live_engine, "MAX_SPOOL_ROWS", 1)
    monkeypatch.setattr(live_engine._runtime, "spool_full_pause", False)

    with patch.object(delivery, "_send_alert"):
        yield small_queue, small_overflow, small_spool


def _item(tag="A"):
    return (1, 101, "M5", "SEN.TF_M5", f"EURUSD-{tag}", None)


def test_enqueue_or_buffer_queues_when_db_queue_has_room(isolated_write_path):
    small_queue, _overflow, _spool = isolated_write_path
    status = delivery._enqueue_or_buffer(_item(), group_id=1, tv_symbol="EURUSD", tf_code="M5")
    assert status == "queued"
    assert small_queue.qsize() == 1


def test_enqueue_or_buffer_falls_back_to_overflow_buffer_when_queue_full(isolated_write_path):
    small_queue, overflow, _spool = isolated_write_path
    small_queue.put_nowait(_item("fill"))  # maxsize=1, so the queue is now full

    status = delivery._enqueue_or_buffer(_item("B"), group_id=1, tv_symbol="EURUSD", tf_code="M5")
    assert status == "buffered"
    assert len(overflow) == 1


def test_enqueue_or_buffer_falls_back_to_disk_spool_when_overflow_full(isolated_write_path):
    small_queue, overflow, spool = isolated_write_path
    small_queue.put_nowait(_item("fill"))  # queue full
    overflow.append(_item("fill"))  # OVERFLOW_BUFFER_MAX=1, so buffer is now full too

    status = delivery._enqueue_or_buffer(_item("C"), group_id=1, tv_symbol="EURUSD", tf_code="M5")
    assert status == "spooled"
    assert spool.count() == 1


def test_enqueue_or_buffer_rejects_when_every_layer_is_full(isolated_write_path):
    small_queue, overflow, spool = isolated_write_path
    small_queue.put_nowait(_item("fill"))  # queue full
    overflow.append(_item("fill"))  # buffer full
    spool.persist_pending(_item("fill"))  # MAX_SPOOL_ROWS=1, so the disk spool is now full too

    status = delivery._enqueue_or_buffer(_item("D"), group_id=1, tv_symbol="EURUSD", tf_code="M5")
    assert status == "rejected"


def test_enqueue_or_buffer_never_returns_a_bare_bool():
    # Regression guard for a fixed bug where a caller checked
    # `enqueue_status is False`, which could never be true since this
    # function's return type is str, not bool - the check was dead code.
    import inspect

    sig = inspect.signature(delivery._enqueue_or_buffer)
    assert sig.return_annotation in (str, "str")


def test_batch_is_blocked_proactively_when_spool_has_no_full_batch_capacity(isolated_write_path):
    _queue, _overflow, spool = isolated_write_path
    assert live_engine._runtime.spool_full_pause is False
    assert spool.persist_pending(_item("fill")) is not None

    # The old implementation returned False here until an enqueue was
    # actually rejected. Admission control must pause before accepting it.
    assert live_engine._spool_full_blocks_batch() is True
    assert live_engine._runtime.spool_full_pause is True


def test_batch_resumes_after_reserved_spool_capacity_returns(isolated_write_path):
    _queue, _overflow, spool = isolated_write_path
    row_id = spool.persist_pending(_item("fill"))
    live_engine._runtime.spool_full_pause = True
    spool.ack(row_id)

    assert live_engine._spool_full_blocks_batch() is False
    assert live_engine._runtime.spool_full_pause is False
