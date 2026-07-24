import threading

from core_engine.core.live import runtime


def _isolated_missing_state(monkeypatch):
    monkeypatch.setattr(runtime, "_backlog", {})
    monkeypatch.setattr(runtime, "_requires_backfill", set())
    monkeypatch.setattr(runtime, "_backlog_lock", threading.Lock())


def test_missing_pair_moves_from_retry_to_backfill_without_becoming_invisible(monkeypatch):
    _isolated_missing_state(monkeypatch)
    pair = (81, "M5")

    first = runtime.update_missing_pairs(set(), {pair}, alert_every=2, max_live_retries=2)
    second = runtime.update_missing_pairs(set(), {pair}, alert_every=2, max_live_retries=2)
    exhausted = runtime.update_missing_pairs(set(), {pair}, alert_every=2, max_live_retries=2)

    assert first.pending == ((pair, 1),)
    assert second.repeated_alerts == ((pair, 2),)
    assert exhausted.requires_backfill == ((pair, 3),)
    assert pair not in runtime._backlog
    assert pair in runtime._requires_backfill


def test_received_pair_clears_retry_and_backfill_state(monkeypatch):
    _isolated_missing_state(monkeypatch)
    pair = (81, "M5")
    runtime._backlog[pair] = 2
    runtime._requires_backfill.add(pair)

    update = runtime.update_missing_pairs({pair}, set(), alert_every=2, max_live_retries=3)

    assert update.recovered == (pair,)
    assert runtime._backlog == {}
    assert runtime._requires_backfill == set()
