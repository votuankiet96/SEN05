"""Tests for core_engine.util.notify.critical_outbox.CriticalAlertOutbox - the
P0-6 fix: a CRITICAL alert now persists to SQLite before delivery is
attempted, is only acked (deleted) on a real send success, and stays
pending (retried by drain()) rather than vanishing on failure.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from core_engine.util.notify.critical_outbox import (
    CriticalAlertOutbox,
    _CriticalDeliveryDispatcher,
    CriticalOutboxStorageError,
)


def _outbox(tmp_path) -> CriticalAlertOutbox:
    ob = CriticalAlertOutbox(
        db_path=tmp_path / "outbox.db",
        status_log_path=tmp_path / "critical_undelivered.log",
    )
    ob.init()
    return ob


def test_record_and_send_success_acks_and_leaves_no_pending_rows(tmp_path, monkeypatch):
    ob = _outbox(tmp_path)
    monkeypatch.setattr(ob, "send_one", lambda message: True)

    ok = ob.record_and_send("hello")

    assert ok is True
    assert ob.status()["pending_count"] == 0


def test_critical_handler_persists_then_returns_without_waiting_for_network(
    tmp_path, monkeypatch
):
    from core_engine.util.logkit import get_logger
    from core_engine.util.notify import critical_outbox

    ob = _outbox(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def stalled_send(_message):
        entered.set()
        release.wait(timeout=2)
        return True

    monkeypatch.setattr(ob, "send_one", stalled_send)
    monkeypatch.setattr(critical_outbox, "_OUTBOX", ob)
    monkeypatch.setattr(critical_outbox, "_DISPATCHER", _CriticalDeliveryDispatcher())
    started = time.monotonic()
    get_logger(
        "critical_outbox_fault_probe",
        stream="alerts",
        console=False,
    ).critical("durable critical")
    elapsed = time.monotonic() - started

    assert elapsed < 0.2
    # The SQLite row must already exist when logger.critical() returns.
    assert ob.status()["pending_count"] == 1
    assert entered.wait(timeout=1)
    assert ob.status()["pending_count"] == 1
    release.set()
    deadline = time.time() + 2
    while time.time() < deadline and ob.status()["pending_count"]:
        time.sleep(0.01)
    assert ob.status()["pending_count"] == 0


def test_record_and_send_failure_keeps_the_row_pending(tmp_path, monkeypatch):
    ob = _outbox(tmp_path)
    monkeypatch.setattr(ob, "send_one", lambda message: False)

    ok = ob.record_and_send("hello")

    assert ok is False
    status = ob.status()
    assert status["pending_count"] == 1
    assert status["oldest_pending_age_seconds"] is not None


def test_drain_retries_pending_rows_and_acks_on_success(tmp_path, monkeypatch):
    ob = _outbox(tmp_path)
    monkeypatch.setattr(ob, "send_one", lambda message: False)
    ob.record_and_send("first")
    ob.record_and_send("second")
    assert ob.status()["pending_count"] == 2

    monkeypatch.setattr(ob, "send_one", lambda message: True)
    sent = ob.drain()

    assert sent == 2
    assert ob.status()["pending_count"] == 0


def test_drain_leaves_still_failing_rows_pending(tmp_path, monkeypatch):
    ob = _outbox(tmp_path)
    monkeypatch.setattr(ob, "send_one", lambda message: False)
    ob.record_and_send("stuck")

    sent = ob.drain()

    assert sent == 0
    assert ob.status()["pending_count"] == 1


def test_status_reports_last_success_time_after_a_successful_send(tmp_path, monkeypatch):
    ob = _outbox(tmp_path)
    monkeypatch.setattr(ob, "send_one", lambda message: True)
    ob.record_and_send("hello")

    assert ob.status()["last_success_at"] is not None


def test_delivery_metadata_survives_process_restart(tmp_path, monkeypatch):
    ob = _outbox(tmp_path)
    monkeypatch.setattr(ob, "send_one", lambda message: True)
    ob.record_and_send("delivered")
    first_status = ob.status()

    reopened = CriticalAlertOutbox(ob.db_path, ob.status_log_path)
    reopened.init()

    assert reopened.status()["last_success_at"] == first_status["last_success_at"]


def test_failure_metadata_survives_process_restart(tmp_path, monkeypatch):
    ob = _outbox(tmp_path)
    monkeypatch.setattr(ob, "send_one", lambda message: False)
    ob.record_and_send("undelivered")

    reopened = CriticalAlertOutbox(ob.db_path, ob.status_log_path)
    reopened.init()
    status = reopened.status()

    assert status["last_failure_at"] is not None
    assert status["last_failure_error"] == "delivery failed"
    assert status["pending_count"] == 1


def test_send_one_returns_false_without_network_when_webhook_not_configured(tmp_path, monkeypatch):
    from types import SimpleNamespace

    ob = _outbox(tmp_path)
    monkeypatch.setattr(
        "core_engine.settings.NOTIFICATION", SimpleNamespace(discord_webhook_url=""),
    )
    assert ob.send_one("hello") is False


def test_send_one_activates_system_trust_before_http_post(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    ob = _outbox(tmp_path)
    events = []
    monkeypatch.setattr(
        "core_engine.settings.NOTIFICATION",
        SimpleNamespace(discord_webhook_url="https://discord.invalid/test"),
    )
    monkeypatch.setattr(
        "core_engine.other.tls.ensure_system_truststore",
        lambda: events.append("trust") or True,
    )
    monkeypatch.setitem(
        sys.modules,
        "requests",
        SimpleNamespace(
            post=lambda *_args, **_kwargs: events.append("post") or SimpleNamespace(status_code=204)
        ),
    )

    assert ob.send_one("hello") is True
    assert events == ["trust", "post"]


def test_persist_survives_across_instances_pointed_at_the_same_file(tmp_path):
    ob = _outbox(tmp_path)
    row_id = ob.persist("durable message")
    assert row_id is not None

    reopened = CriticalAlertOutbox(db_path=ob.db_path, status_log_path=ob.status_log_path)
    reopened.init()
    rows = reopened.pending()
    assert len(rows) == 1
    assert rows[0][0] == row_id
    assert rows[0][1] == "durable message"


def test_ack_removes_only_the_specified_row(tmp_path):
    ob = _outbox(tmp_path)
    id1 = ob.persist("one")
    id2 = ob.persist("two")

    ob.ack(id1)

    remaining = ob.pending()
    assert len(remaining) == 1
    assert remaining[0][0] == id2


def test_corrupt_database_is_explicitly_unhealthy_not_empty(tmp_path):
    db_path = tmp_path / "corrupt.db"
    db_path.write_bytes(b"this is not a sqlite database")
    ob = CriticalAlertOutbox(db_path, tmp_path / "status.log")

    status = ob.status()

    assert status["healthy"] is False
    assert status["pending_count"] is None
    assert status["storage_error"]
    with pytest.raises(CriticalOutboxStorageError):
        ob.pending()


def test_persist_and_ack_storage_errors_are_not_silently_swallowed(tmp_path):
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"broken")
    broken = CriticalAlertOutbox(corrupt, tmp_path / "status.log")
    with pytest.raises(CriticalOutboxStorageError, match="persist"):
        broken.persist("must not vanish")

    ob = _outbox(tmp_path / "valid")
    row_id = ob.persist("durable")
    # Closing every connection before replacement makes this a deterministic
    # simulation of an unreadable/corrupt store at acknowledgement time.
    ob.db_path.write_bytes(b"broken after delivery")
    with pytest.raises(CriticalOutboxStorageError, match="ack"):
        ob.ack(row_id)


def test_concurrent_drains_claim_row_once_across_instances(tmp_path, monkeypatch):
    first = _outbox(tmp_path)
    second = CriticalAlertOutbox(first.db_path, first.status_log_path)
    second.init()
    first.persist("only once")

    entered = threading.Event()
    release = threading.Event()
    deliveries: list[str] = []
    deliveries_lock = threading.Lock()

    def slow_send(message):
        with deliveries_lock:
            deliveries.append(message)
        entered.set()
        assert release.wait(timeout=5)
        return True

    monkeypatch.setattr(first, "send_one", slow_send)
    monkeypatch.setattr(second, "send_one", slow_send)
    results: list[int] = []
    t1 = threading.Thread(target=lambda: results.append(first.drain(limit=1)))
    t1.start()
    assert entered.wait(timeout=5)
    t2 = threading.Thread(target=lambda: results.append(second.drain(limit=1)))
    t2.start()
    t2.join(timeout=5)
    release.set()
    t1.join(timeout=5)

    assert not t1.is_alive() and not t2.is_alive()
    assert sorted(results) == [0, 1]
    assert deliveries == ["only once"]
    assert first.status()["pending_count"] == 0


def test_drain_cannot_duplicate_record_and_send_row(tmp_path, monkeypatch):
    producer = _outbox(tmp_path)
    drainer = CriticalAlertOutbox(producer.db_path, producer.status_log_path)
    drainer.init()
    persisted = threading.Event()
    allow_producer_claim = threading.Event()
    drainer_sending = threading.Event()
    release_delivery = threading.Event()
    deliveries: list[str] = []
    original_persist = producer.persist

    def paused_persist(message):
        row_id = original_persist(message)
        persisted.set()
        assert allow_producer_claim.wait(timeout=5)
        return row_id

    def slow_send(message):
        deliveries.append(message)
        drainer_sending.set()
        assert release_delivery.wait(timeout=5)
        return True

    monkeypatch.setattr(producer, "persist", paused_persist)
    monkeypatch.setattr(producer, "send_one", slow_send)
    monkeypatch.setattr(drainer, "send_one", slow_send)
    producer_result: list[bool] = []
    drain_result: list[int] = []
    producer_thread = threading.Thread(
        target=lambda: producer_result.append(producer.record_and_send("race"))
    )
    producer_thread.start()
    assert persisted.wait(timeout=5)

    drain_thread = threading.Thread(target=lambda: drain_result.append(drainer.drain(limit=1)))
    drain_thread.start()
    assert drainer_sending.wait(timeout=5)
    allow_producer_claim.set()
    producer_thread.join(timeout=5)
    release_delivery.set()
    drain_thread.join(timeout=5)

    assert producer_result == [False]
    assert drain_result == [1]
    assert deliveries == ["race"]
    assert producer.status()["pending_count"] == 0


def test_expired_claim_is_recovered_after_sender_crash(tmp_path, monkeypatch):
    crashed = CriticalAlertOutbox(
        tmp_path / "outbox.db", tmp_path / "status.log", lease_seconds=1
    )
    crashed.init()
    row_id = crashed.persist("recover me")
    assert crashed._claim(limit=1, row_id=row_id)

    recovered = CriticalAlertOutbox(crashed.db_path, crashed.status_log_path)
    recovered.init()
    monkeypatch.setattr(recovered, "send_one", lambda _message: True)
    assert recovered.drain(limit=1) == 0

    with sqlite3.connect(crashed.db_path) as con:
        con.execute("UPDATE critical_alerts SET leased_until = 0 WHERE id = ?", (row_id,))
        con.commit()

    assert recovered.drain(limit=1) == 1
    assert recovered.status()["pending_count"] == 0


def test_delivery_ledger_is_bounded(tmp_path, monkeypatch):
    ob = CriticalAlertOutbox(
        tmp_path / "outbox.db",
        tmp_path / "status.log",
        delivery_ledger_limit=3,
    )
    ob.init()
    monkeypatch.setattr(ob, "send_one", lambda _message: True)

    for index in range(8):
        assert ob.record_and_send(f"message-{index}") is True

    status = ob.status()
    assert status["delivery_ledger_count"] == 3
    with sqlite3.connect(ob.db_path) as con:
        messages = [
            row[0]
            for row in con.execute(
                "SELECT message FROM critical_delivery_ledger ORDER BY id"
            )
        ]
    assert messages == ["message-5", "message-6", "message-7"]
