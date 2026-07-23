"""Failure-path tests for the bounded ordinary Discord sender."""

from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace

import pytest

from core_engine.util.notify import discord
from core_engine.util.logkit.sink import SinkQueueHandler


def _item(*, level: str = "ERROR", content_hash: str = "same") -> discord._DiscordSendItem:
    return discord._DiscordSendItem(
        payload={"content": "test"},
        kind="alert",
        level=level,
        meta={
            "feature": "system",
            "result": "failed",
            "title": "test alert",
            "content_hash": content_hash,
        },
    )


@pytest.fixture
def isolated_sender(monkeypatch):
    senders: list[discord._DiscordSender] = []
    activities: list[tuple[str, dict]] = []

    def make_sender(maxsize: int = 8) -> discord._DiscordSender:
        sender = discord._DiscordSender(maxsize=maxsize)
        senders.append(sender)
        monkeypatch.setattr(discord, "_discord_sender", sender)
        return sender

    monkeypatch.setattr(
        discord,
        "_record_discord_activity",
        lambda action, **kwargs: activities.append((action, kwargs)),
    )
    monkeypatch.setattr(discord, "_ensure_discord_logger", lambda: None)
    with discord._discord_dedupe_lock:
        discord._discord_last_sent.clear()
        discord._discord_suppressed.clear()
    with discord._discord_circuit_lock:
        monkeypatch.setattr(discord, "_discord_failure_count", 0)
        monkeypatch.setattr(discord, "_discord_circuit_open_until", 0.0)

    yield make_sender, activities

    for sender in senders:
        assert sender.close(timeout=2.0)
    with discord._discord_dedupe_lock:
        discord._discord_last_sent.clear()
        discord._discord_suppressed.clear()


def test_failed_delivery_does_not_suppress_the_next_duplicate(isolated_sender, monkeypatch):
    make_sender, activities = isolated_sender
    make_sender()
    outcomes = iter([False, True])
    calls: list[str] = []

    def fake_post(_payload, *, kind, level, meta):
        calls.append(meta["content_hash"])
        return next(outcomes)

    monkeypatch.setattr(discord, "_post_payload", fake_post)

    discord._start_sender(**_item().__dict__)
    discord._start_sender(**_item().__dict__)

    assert discord.flush_pending(timeout=1.0) is True
    assert calls == ["same", "same"]
    assert not any(action == "discord.suppressed" for action, _ in activities)

    # Only the confirmed second delivery opens the dedupe window.
    discord._start_sender(**_item().__dict__)
    assert discord.flush_pending(timeout=1.0) is True
    assert calls == ["same", "same"]
    assert any(action == "discord.suppressed" for action, _ in activities)


def test_sender_uses_one_fixed_worker_for_all_payloads(isolated_sender, monkeypatch):
    make_sender, _activities = isolated_sender
    make_sender(maxsize=32)
    worker_ids: list[int] = []

    def fake_post(_payload, *, kind, level, meta):
        worker_ids.append(threading.get_ident())
        return True

    monkeypatch.setattr(discord, "_post_payload", fake_post)

    for index in range(20):
        item = _item(level="INFO", content_hash=str(index))
        discord._start_sender(**item.__dict__)

    assert discord.flush_pending(timeout=1.0) is True
    assert len(worker_ids) == 20
    assert len(set(worker_ids)) == 1


def test_bounded_queue_rejects_overflow_and_records_it(isolated_sender, monkeypatch):
    make_sender, activities = isolated_sender
    make_sender(maxsize=1)
    entered = threading.Event()
    release = threading.Event()

    def blocked_post(_payload, *, kind, level, meta):
        entered.set()
        assert release.wait(timeout=1.0)
        return True

    monkeypatch.setattr(discord, "_post_payload", blocked_post)

    discord._start_sender(**_item(level="INFO", content_hash="one").__dict__)
    assert entered.wait(timeout=1.0)
    discord._start_sender(**_item(level="INFO", content_hash="two").__dict__)
    discord._start_sender(**_item(level="INFO", content_hash="three").__dict__)

    rejected = [detail for action, detail in activities if action == "discord.queue_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["detail"]["reason"] == "queue_full"
    release.set()
    assert discord.flush_pending(timeout=1.0) is True


def test_worker_start_failure_is_caught_and_recorded(isolated_sender, monkeypatch):
    _make_sender, activities = isolated_sender

    class BrokenSender:
        def submit(self, _item):
            raise RuntimeError("cannot start thread")

    monkeypatch.setattr(discord, "_discord_sender", BrokenSender())
    monkeypatch.setattr(discord, "_log_sender_exception", lambda *_args, **_kwargs: None)

    discord._start_sender(**_item().__dict__)

    rejected = [detail for action, detail in activities if action == "discord.queue_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["detail"]["reason"] == "queue_unavailable"


def test_flush_pending_obeys_one_total_timeout(isolated_sender, monkeypatch):
    make_sender, _activities = isolated_sender
    make_sender()
    entered = threading.Event()
    release = threading.Event()

    def blocked_post(_payload, *, kind, level, meta):
        entered.set()
        assert release.wait(timeout=1.0)
        return True

    monkeypatch.setattr(discord, "_post_payload", blocked_post)
    discord._start_sender(**_item(level="INFO").__dict__)
    assert entered.wait(timeout=1.0)

    assert discord.flush_pending(timeout=0.01) is False
    release.set()
    assert discord.flush_pending(timeout=1.0) is True


def test_post_payload_returns_false_when_trust_setup_fails(isolated_sender, monkeypatch):
    _make_sender, activities = isolated_sender
    monkeypatch.setattr(
        "core_engine.other.tls.ensure_system_truststore",
        lambda: (_ for _ in ()).throw(RuntimeError("trust unavailable")),
    )

    assert discord._post_payload(**_item().__dict__) is False
    failed = [detail for action, detail in activities if action == "discord.failed"]
    assert len(failed) == 1
    assert failed[0]["detail"]["attempts"] == 0
    assert failed[0]["detail"]["error"] == "RuntimeError"


def test_post_payload_returns_true_only_for_confirmed_discord_status(
    isolated_sender, monkeypatch
):
    _make_sender, _activities = isolated_sender
    monkeypatch.setattr(
        "core_engine.other.tls.ensure_system_truststore",
        lambda: True,
    )
    monkeypatch.setitem(
        sys.modules,
        "requests",
        SimpleNamespace(
            post=lambda *_args, **_kwargs: SimpleNamespace(status_code=204),
        ),
    )

    assert discord._post_payload(**_item().__dict__) is True


def test_activity_log_failure_after_http_success_does_not_retry_delivery(
    isolated_sender, monkeypatch
):
    _make_sender, _activities = isolated_sender
    posts: list[int] = []
    monkeypatch.setattr(
        "core_engine.other.tls.ensure_system_truststore",
        lambda: True,
    )
    monkeypatch.setitem(
        sys.modules,
        "requests",
        SimpleNamespace(
            post=lambda *_args, **_kwargs: posts.append(1) or SimpleNamespace(status_code=204),
        ),
    )
    monkeypatch.setattr(
        discord,
        "_record_discord_activity",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("log unavailable")),
    )
    monkeypatch.setattr(discord, "_log_sender_exception", lambda *_args, **_kwargs: None)

    assert discord._post_payload(**_item().__dict__) is True
    assert posts == [1]


def test_discord_delivery_uses_the_single_canonical_alerts_sink(monkeypatch):
    original_handlers = list(discord.logger.handlers)
    monkeypatch.setattr(discord, "_discord_logger_configured", False)
    monkeypatch.setenv("DP_PROCESS_ROLE", "live")
    discord._ensure_discord_logger()
    assert discord.logger.handlers == original_handlers
    handlers = [handler for handler in discord.logger.handlers if isinstance(handler, SinkQueueHandler)]
    assert len(handlers) == 1
    assert handlers[0].stream == "alerts"


def test_dedupe_memory_is_ttl_and_size_bounded(isolated_sender, monkeypatch):
    _make_sender, _activities = isolated_sender
    now = time.time()
    monkeypatch.setattr(discord, "_discord_last_dedupe_cleanup", 0.0)
    discord._discord_last_sent.clear()
    discord._discord_suppressed.clear()
    for index in range(discord.DISCORD_DEDUPE_MAX_KEYS + 100):
        key = f"old-{index}"
        discord._discord_last_sent[key] = now - discord.DISCORD_DEDUPE_WINDOW_SEC - 10
        discord._discord_suppressed[key] = 1

    discord._duplicate_suppression(
        kind="alert",
        level="ERROR",
        meta={"feature": "live", "result": "failed", "content_hash": "current"},
    )

    assert len(discord._discord_last_sent) <= discord.DISCORD_DEDUPE_MAX_KEYS
    assert len(discord._discord_suppressed) <= discord.DISCORD_DEDUPE_MAX_KEYS
