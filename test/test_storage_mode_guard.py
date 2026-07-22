"""Storage-mode startup guards.

``redis`` is unsupported because both engines require SQL as the durable
warehouse. ``both`` is supported: SQL remains authoritative while the live
engine additionally publishes best-effort Redis candle snapshots. Production
currently reaches ``both`` through the backward-compatible
``CANDLE_SNAPSHOT_ENABLED=1`` fallback, so rejecting it would stop both workers
immediately after deploy.
"""

from __future__ import annotations

import dataclasses

from core_engine.settings import STORAGE


def _storage_with(mode: str):
    return dataclasses.replace(STORAGE, mode=mode)


def test_live_engine_refuses_to_start_for_unsupported_storage_mode(monkeypatch):
    import core_engine.core.live.engine as live_engine

    monkeypatch.setattr(live_engine, "STORAGE", _storage_with("redis"))
    assert live_engine.main() == 1


def _assert_live_mode_reaches_database_guard(monkeypatch, mode: str):
    import core_engine.core.live.engine as live_engine

    monkeypatch.setattr(live_engine, "STORAGE", _storage_with(mode))
    calls = []
    monkeypatch.setattr(live_engine, "test_connection", lambda: calls.append("db") or False)
    assert live_engine.main() == 1
    assert calls == ["db"], f"{mode} must pass the storage guard and reach the SQL preflight"


def test_live_engine_proceeds_past_the_guard_for_sql_mode(monkeypatch):
    _assert_live_mode_reaches_database_guard(monkeypatch, "sql")


def test_live_engine_proceeds_past_the_guard_for_both_mode(monkeypatch):
    _assert_live_mode_reaches_database_guard(monkeypatch, "both")


def test_historical_engine_refuses_to_start_for_redis_only_mode(monkeypatch):
    import core_engine.core.historical.engine as historical_engine

    monkeypatch.setattr(historical_engine, "STORAGE", _storage_with("redis"))
    assert historical_engine.main([]) == 1


def test_historical_engine_allows_both_mode_and_reaches_database_guard(monkeypatch):
    import core_engine.core.historical.engine as historical_engine

    calls = []
    monkeypatch.setattr(historical_engine, "STORAGE", _storage_with("both"))
    monkeypatch.setattr(historical_engine, "test_connection", lambda: calls.append("db") or False)

    assert historical_engine.main([]) == 4
    assert calls == ["db"]
