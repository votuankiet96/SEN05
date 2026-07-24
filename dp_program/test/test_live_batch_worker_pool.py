"""Tests for the High-11 redesign: connection groups now run on one
persistent worker thread per group (started once in main() via
BatchFetcher.start_worker()) instead of _run_batch spawning a fresh
batch-g{id} (+ ws-g{id} inside fetch()) thread pair on every scheduled
cycle. A group whose worker is still busy at BATCH_GROUP_JOIN_TIMEOUT_SEC
for GROUP_WEDGE_HARD_DEADLINE_BATCHES consecutive cycles - i.e. still
wedged even after fetch()'s own forced-socket-close timeout path already
ran - is now treated as unrecoverable in-process: the live child recycles
itself via the supervisor's existing restart/backoff machinery (the only
way to guarantee a truly wedged native thread/socket is reclaimed),
instead of the previous behavior of logging an error and silently
carrying the leaked thread forever.

No real TradingView/websocket connection is used: BatchFetcher.fetch is
monkeypatched to a fast, deterministic stub.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from core_engine.core.live import engine as live_engine
from core_engine.core.live import fetcher
from core_engine.core.live.fetcher import BatchFetcher, _claim_ws_callback_stall_fault
from core_engine.core.live.engine import (
    LiveProcessRecycleRequired,
    _classify_group_batch_outcomes,
)


@pytest.fixture
def isolated_shutdown(monkeypatch):
    """A fresh, per-test _shutdown Event so a worker thread started here
    cannot bleed into (or be affected by) other tests sharing the module-
    level singleton, and is guaranteed stoppable at teardown."""
    event = threading.Event()
    monkeypatch.setattr(live_engine, "_shutdown", event)
    monkeypatch.setattr(fetcher, "_shutdown", event)
    yield event
    event.set()


def _start_group_with_stub_fetch(monkeypatch, group_id: int, fetch_stub):
    monkeypatch.setattr(BatchFetcher, "fetch", fetch_stub)
    g = BatchFetcher(group_id, [])
    g.start_worker()
    return g


# --- persistent worker: no thread churn across cycles ----------------------


def test_worker_thread_is_created_once_and_reused_across_many_batches(monkeypatch, isolated_shutdown):
    call_count = {"n": 0}

    def _fast_success_fetch(self, batch_id, timeout=None):
        call_count["n"] += 1
        return True

    g = _start_group_with_stub_fetch(monkeypatch, 7, _fast_success_fetch)
    first_worker_thread = g._worker_thread
    assert first_worker_thread is not None
    assert first_worker_thread.name == "worker-g7"

    for batch_id in range(1, 6):
        g.request_batch(batch_id)
        assert g._batch_complete.wait(timeout=2), f"batch {batch_id} never completed"

    # Five scheduled cycles handled, but still the exact same Thread
    # object - no batch-g{id}/ws-g{id} thread pair was created per cycle.
    assert g._worker_thread is first_worker_thread
    assert call_count["n"] == 5
    assert sum(1 for t in threading.enumerate() if t.name == "worker-g7") == 1


def test_request_batch_after_previous_completion_does_not_overlap(monkeypatch, isolated_shutdown):
    # A slow fetch() must fully finish (and the worker must go idle again)
    # before the NEXT request_batch() cycle's fetch() call starts - proving
    # there is no possibility of two concurrent fetch() calls for the same
    # group, which the old spawn-a-new-thread-every-cycle design could not
    # guarantee once a previous cycle's thread ran past its join deadline.
    in_flight = {"count": 0, "max_concurrent": 0}
    lock = threading.Lock()

    def _tracking_fetch(self, batch_id, timeout=None):
        with lock:
            in_flight["count"] += 1
            in_flight["max_concurrent"] = max(in_flight["max_concurrent"], in_flight["count"])
        time.sleep(0.05)
        with lock:
            in_flight["count"] -= 1
        return True

    g = _start_group_with_stub_fetch(monkeypatch, 1, _tracking_fetch)

    for batch_id in range(1, 4):
        g.request_batch(batch_id)
        assert g._batch_complete.wait(timeout=2)

    assert in_flight["max_concurrent"] == 1


def test_abandoned_batch_cancels_retry_backoff_and_never_retries_late(monkeypatch, isolated_shutdown):
    first_attempt_finished = threading.Event()
    calls = []

    def _incomplete_fetch(self, batch_id, timeout=None):
        calls.append(batch_id)
        first_attempt_finished.set()
        return False

    monkeypatch.setattr(fetcher, "BATCH_MAX_RETRIES", 3)
    monkeypatch.setattr(fetcher, "RECONNECT_BASE_SEC", 60)
    g = _start_group_with_stub_fetch(monkeypatch, 4, _incomplete_fetch)

    assert g.request_batch(17) is True
    assert first_attempt_finished.wait(timeout=2)
    g.abandon_batch(17)

    assert g._batch_complete.wait(timeout=2)
    assert calls == [17]
    assert g._busy is False


def test_busy_worker_rejects_new_batch_without_overwriting_pending_id(monkeypatch, isolated_shutdown):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def _blocked_fetch(self, batch_id, timeout=None):
        calls.append(batch_id)
        entered.set()
        release.wait(timeout=2)
        return True

    g = _start_group_with_stub_fetch(monkeypatch, 6, _blocked_fetch)
    assert g.request_batch(1) is True
    assert entered.wait(timeout=2)

    assert g.request_batch(2) is False
    assert g._pending_batch_id == 1

    release.set()
    assert g._batch_complete.wait(timeout=2)
    assert calls == [1]


def test_worker_survives_fetch_exception_and_keeps_answering_future_batches(monkeypatch, isolated_shutdown):
    attempts = {"n": 0}

    def _flaky_fetch(self, batch_id, timeout=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("simulated transport failure")
        return True

    # BATCH_MAX_RETRIES defaults to >=1; force a single attempt per
    # request so the first request_batch() call observes the exception
    # directly rather than being swallowed by _fetch_with_retry's own
    # retry loop.
    monkeypatch.setattr(fetcher, "BATCH_MAX_RETRIES", 1)

    g = _start_group_with_stub_fetch(monkeypatch, 2, _flaky_fetch)

    g.request_batch(1)
    assert g._batch_complete.wait(timeout=2)
    assert g._worker_thread.is_alive()

    g.request_batch(2)
    assert g._batch_complete.wait(timeout=2)
    assert attempts["n"] == 2
    assert g._worker_thread.is_alive()


# --- stuck / hard-deadline classification -----------------------------------


def _fake_group(
    group_id: int,
    *,
    busy: bool,
    consecutive_stuck_batches: int = 0,
    requires_process_recycle: bool = False,
):
    return SimpleNamespace(
        group_id=group_id,
        _busy=busy,
        _requires_process_recycle=requires_process_recycle,
        _consecutive_stuck_batches=consecutive_stuck_batches,
    )


def test_classify_marks_idle_group_ok_and_resets_counter():
    g = _fake_group(1, busy=False, consecutive_stuck_batches=2)
    stuck, wedged = _classify_group_batch_outcomes([g], hard_deadline_batches=3)
    assert stuck == []
    assert wedged == []
    assert g._consecutive_stuck_batches == 0


def test_classify_marks_busy_group_stuck_but_not_wedged_before_threshold():
    g = _fake_group(5, busy=True, consecutive_stuck_batches=0)
    stuck, wedged = _classify_group_batch_outcomes([g], hard_deadline_batches=3)
    assert stuck == ["G5"]
    assert wedged == []
    assert g._consecutive_stuck_batches == 1


def test_classify_marks_unrequested_group_stuck_even_if_prior_worker_just_became_idle():
    g = _fake_group(5, busy=False, consecutive_stuck_batches=0)
    stuck, wedged = _classify_group_batch_outcomes(
        [g],
        hard_deadline_batches=3,
        unrequested_group_ids={5},
    )
    assert stuck == ["G5"]
    assert wedged == []
    assert g._consecutive_stuck_batches == 1


def test_classify_reaches_wedged_at_exact_threshold():
    g = _fake_group(5, busy=True, consecutive_stuck_batches=2)
    stuck, wedged = _classify_group_batch_outcomes([g], hard_deadline_batches=3)
    assert stuck == ["G5"]
    assert wedged == [5]
    assert g._consecutive_stuck_batches == 3


def test_classify_one_recovered_group_does_not_affect_a_still_stuck_group():
    recovered = _fake_group(1, busy=False, consecutive_stuck_batches=2)
    still_stuck = _fake_group(2, busy=True, consecutive_stuck_batches=2)
    stuck, wedged = _classify_group_batch_outcomes(
        [recovered, still_stuck], hard_deadline_batches=3
    )
    assert stuck == ["G2"]
    assert wedged == [2]
    assert recovered._consecutive_stuck_batches == 0
    assert still_stuck._consecutive_stuck_batches == 3


def test_classify_consecutive_counter_does_not_overflow_past_threshold_reporting():
    # Once past the threshold, the group keeps being reported wedged every
    # cycle it stays busy (not just once) - the caller decides how to act
    # each time, this helper does not suppress repeats.
    g = _fake_group(9, busy=True, consecutive_stuck_batches=5)
    stuck, wedged = _classify_group_batch_outcomes([g], hard_deadline_batches=3)
    assert wedged == [9]
    assert g._consecutive_stuck_batches == 6


def test_idle_worker_with_orphaned_websocket_is_not_mistaken_for_recovered():
    # Regression: fetch() returned and _worker_loop set _busy=False after
    # ws_thread survived force-close.  The old classifier reset the counter
    # forever, so every later batch could leak another ws-g{id} thread.
    g = _fake_group(4, busy=False, requires_process_recycle=True)

    for expected_count in (1, 2):
        stuck, wedged = _classify_group_batch_outcomes([g], hard_deadline_batches=3)
        assert stuck == ["G4"]
        assert wedged == []
        assert g._consecutive_stuck_batches == expected_count

    stuck, wedged = _classify_group_batch_outcomes([g], hard_deadline_batches=3)
    assert stuck == ["G4"]
    assert wedged == [4]
    assert g._consecutive_stuck_batches == 3


def test_orphaned_group_does_not_open_another_websocket(monkeypatch, isolated_shutdown):
    calls = []

    def _fetch(self, batch_id, timeout=None):
        calls.append(batch_id)
        return True

    g = _start_group_with_stub_fetch(monkeypatch, 3, _fetch)
    g._requires_process_recycle = True
    assert g.request_batch(99) is False
    assert calls == []


def test_fetch_marks_process_recycle_when_socket_thread_survives_force_close(
    monkeypatch, isolated_shutdown
):
    release_thread = threading.Event()

    class _RawSocket:
        def close(self):
            return None

    class _FakeWebSocketApp:
        def __init__(self, *_args, **_kwargs):
            self.keep_running = True
            self.sock = SimpleNamespace(sock=_RawSocket())

        def run_forever(self):
            # Simulate a native websocket read/callback that ignores both
            # keep_running=False and the forced raw-socket close.
            release_thread.wait(timeout=5)

        def close(self):
            return None

    monkeypatch.setattr(fetcher.websocket, "WebSocketApp", _FakeWebSocketApp)
    monkeypatch.setattr(fetcher, "WS_THREAD_JOIN_GRACE_SEC", 0.01)
    g = BatchFetcher(6, [])

    try:
        assert g.fetch(1, timeout=0.01) is False
        assert g._requires_process_recycle is True
    finally:
        release_thread.set()


def test_wedged_batch_raises_fatal_recycle_signal(monkeypatch, isolated_shutdown):
    done = threading.Event()
    done.set()
    group = SimpleNamespace(
        group_id=8,
        _busy=False,
        _requires_process_recycle=True,
        _consecutive_stuck_batches=2,
        _batch_complete=done,
        request_batch=lambda _batch_id: False,
        abandon_batch=lambda _batch_id: None,
    )

    monkeypatch.setattr(live_engine, "_tradingview_connectivity_ok", lambda: (True, "ok"))
    monkeypatch.setattr(live_engine, "_guest_mode_blocks_batch", lambda: False)
    monkeypatch.setattr(live_engine, "_spool_full_blocks_batch", lambda: False)
    monkeypatch.setattr(live_engine, "_init_batch_metrics", lambda _batch_id: None)
    monkeypatch.setattr(live_engine, "_start_candle_table", lambda _batch_id: None)
    monkeypatch.setattr(live_engine, "_write_live_state", lambda **_kwargs: None)
    monkeypatch.setattr(live_engine, "acquire_live_batch_window", lambda _logger: False)
    monkeypatch.setattr(live_engine, "release_live_batch_window", lambda _held: None)
    monkeypatch.setattr(live_engine, "_send_alert", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(live_engine, "GROUP_WEDGE_HARD_DEADLINE_BATCHES", 3)

    with pytest.raises(LiveProcessRecycleRequired, match="G8"):
        live_engine._run_batch([group])

    assert isolated_shutdown.is_set()


def test_controlled_socket_stall_marker_is_exact_and_one_shot(monkeypatch, tmp_path):
    monkeypatch.setattr(fetcher, "RUN_DIR", tmp_path)
    marker = tmp_path / "fault_inject_ws_callback_stall_g2.request"

    marker.write_text("not-authorized", encoding="utf-8")
    assert _claim_ws_callback_stall_fault(2) is None
    assert marker.exists()

    marker.write_text("STALL_ONCE\n", encoding="utf-8")
    evidence = _claim_ws_callback_stall_fault(2)
    assert evidence is not None
    assert evidence.exists()
    assert ".active." in evidence.name
    assert not marker.exists()
    assert _claim_ws_callback_stall_fault(2) is None
