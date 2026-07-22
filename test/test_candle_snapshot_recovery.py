"""Tests for the High-12 mitigation: CandleSnapshotPublisher now fires an
optional on_recovered callback when the Redis circuit breaker closes again
after being open, so live/engine.py can re-seed every live symbol/
timeframe and OG catches up on any snapshot_updated events it missed
during the outage. This is a light mitigation, not a full durable outbox -
see the module docstring in redis_io/candle_snapshot.py for why.
"""

from __future__ import annotations

from core_engine.util.redis_io.candle_snapshot import CandleSnapshotPublisher


def test_mark_recovered_fires_callback_only_when_circuit_was_open():
    pub = CandleSnapshotPublisher()
    calls = []
    pub.on_recovered = lambda: calls.append(1)

    pub._mark_recovered()  # circuit was never open - must not fire
    assert calls == []

    pub._circuit_open_until = 999999999999.0  # simulate an open circuit
    pub._mark_recovered()
    assert calls == [1]


def test_mark_recovered_swallows_callback_exceptions():
    pub = CandleSnapshotPublisher()
    pub._circuit_open_until = 999999999999.0

    def _boom():
        raise RuntimeError("seed failed")

    pub.on_recovered = _boom
    pub._mark_recovered()  # must not raise


def test_mark_recovered_is_a_no_op_without_a_registered_callback():
    pub = CandleSnapshotPublisher()
    pub._circuit_open_until = 999999999999.0
    assert pub.on_recovered is None
    pub._mark_recovered()  # must not raise
