"""Tests for core_engine.logkit.critical_outbox.CriticalAlertOutbox - the
P0-6 fix: a CRITICAL alert now persists to SQLite before delivery is
attempted, is only acked (deleted) on a real send success, and stays
pending (retried by drain()) rather than vanishing on failure.
"""

from __future__ import annotations

from core_engine.logkit.critical_outbox import CriticalAlertOutbox


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
        "core_engine.logkit.critical_outbox.ensure_system_truststore",
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
