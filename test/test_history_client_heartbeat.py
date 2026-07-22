"""Tests for the Medium-14 fix in core_engine.shared.tradingview.history_client:
drain_until_complete() (used by fetch_history/crawl_replay_history) used to
only echo a heartbeat when it was the raw receive buffer's own prefix,
silently skipping (not echoing) a heartbeat found later via _parse_packets
- e.g. one bundled after a framed data message in the same TCP read. It
also relied on parse_packets() itself, which had a related bug (fixed in
tradingview/protocol.py - see test_protocol.py's "does_not_swallow" tests):
a bare heartbeat used to swallow everything after it in the same buffer,
including a following framed message.

This integration-level test drives the real fetch_history() with a fake
WebSocket connection whose recv() returns a single buffer containing a
heartbeat bundled BEFORE the series_completed frame, in one TCP read - the
exact "heartbeat swallows/hides a following message" scenario. Both the
message must still be recognized (proving parse_packets didn't swallow it)
and the heartbeat must be echoed back (proving drain_until_complete didn't
skip it just because it was not at the very start of the buffer).
"""

from __future__ import annotations

import json

import pytest

from core_engine.shared.tradingview import history_client


def _frame(method, params):
    payload = json.dumps({"m": method, "p": params})
    return f"~m~{len(payload)}~m~{payload}"


class _FakeWS:
    def __init__(self, recv_sequence):
        self._recv_sequence = list(recv_sequence)
        self.sent = []
        self._timeout = None

    def settimeout(self, value):
        self._timeout = value

    def send(self, payload):
        self.sent.append(payload)

    def recv(self):
        if not self._recv_sequence:
            raise TimeoutError("no more scripted messages")
        return self._recv_sequence.pop(0)

    def close(self):
        pass


@pytest.fixture
def patched_connection(monkeypatch):
    created = {}

    def _fake_create_connection(*args, **kwargs):
        return created["ws"]

    monkeypatch.setattr(history_client.websocket, "create_connection", _fake_create_connection)
    monkeypatch.setattr(history_client, "_current_auth", lambda: ("tok", "cookie"))

    def _install(ws):
        created["ws"] = ws
        return ws

    return _install


def test_heartbeat_bundled_before_completion_frame_is_echoed_and_not_swallowed(
    patched_connection,
):
    # One single TCP read containing: heartbeat, then the completion frame
    # right after it, in the SAME buffer - the exact scenario the old
    # raw.startswith("~h~") + old parse_packets both mishandled together.
    # series_completed with empty params matches "not params" in fetch_history.
    bundled = "~h~123" + _frame("series_completed", [])
    ws = _FakeWS(recv_sequence=[bundled])
    patched_connection(ws)

    result = history_client.fetch_history(
        symbol="EURUSD", exchange="OANDA", tf_code="M5", n_bars=10, timeout_sec=2.0,
    )

    assert result.status == "completed", (
        f"series_completed must still be recognized even though a heartbeat preceded it "
        f"in the same buffer (status={result.status!r})"
    )
    heartbeat_echoes = [s for s in ws.sent if "~h~123" in s]
    assert len(heartbeat_echoes) == 1, f"heartbeat must be echoed back exactly once, sent={ws.sent!r}"


def test_heartbeat_bundled_after_a_data_frame_is_still_echoed(patched_connection):
    # Heartbeat AFTER a (non-terminal) data frame in the same buffer, then
    # completion in a second read - proves the mid/rear heartbeat position
    # does not get skipped by drain_until_complete's echo check.
    first = _frame("qsd", ["noise"]) + "~h~55"
    second = _frame("series_completed", [])
    ws = _FakeWS(recv_sequence=[first, second])
    patched_connection(ws)

    result = history_client.fetch_history(
        symbol="EURUSD", exchange="OANDA", tf_code="M5", n_bars=10, timeout_sec=2.0,
    )

    assert result.status == "completed"
    heartbeat_echoes = [s for s in ws.sent if "~h~55" in s]
    assert len(heartbeat_echoes) == 1, f"heartbeat must be echoed, sent={ws.sent!r}"
