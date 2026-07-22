"""Tests for the pure TradingView WebSocket wire-format helpers.

These cover core_engine.shared.tradingview.protocol, which every live/historical
data path depends on to talk to TradingView - a regression here would
silently corrupt every bar the service fetches.
"""

from __future__ import annotations

import json

from core_engine.shared.tradingview import protocol


def test_gen_session_id_has_prefix_and_length():
    sid = protocol.gen_session_id("cs")
    assert sid.startswith("cs_")
    assert len(sid) == len("cs_") + 12


def test_gen_session_id_is_unique_across_calls():
    ids = {protocol.gen_session_id("cs") for _ in range(50)}
    assert len(ids) == 50


class _FakeWS:
    def __init__(self, raw=None):
        self.sent = []
        self.raw = raw

    def send(self, payload):
        self.sent.append(payload)

    def recv(self):
        return self.raw


def test_send_tv_message_frames_with_length_prefix():
    ws = _FakeWS()
    protocol.send_tv_message(ws, ["set_auth_token", "tok123"])
    assert len(ws.sent) == 1
    frame = ws.sent[0]
    assert frame.startswith("~m~")
    length_str, _, rest = frame[3:].partition("~m~")
    length = int(length_str)
    payload = rest
    assert len(payload) == length
    decoded = json.loads(payload)
    assert decoded == {"m": "set_auth_token", "p": ["tok123"]}


def _frame(method, params):
    payload = json.dumps({"m": method, "p": params})
    return f"~m~{len(payload)}~m~{payload}"


def test_parse_packets_splits_single_frame():
    raw = _frame("qsd", ["a", "b"])
    packets = protocol.parse_packets(raw)
    assert len(packets) == 1
    assert json.loads(packets[0]) == {"m": "qsd", "p": ["a", "b"]}


def test_parse_packets_splits_multiple_concatenated_frames():
    raw = _frame("m1", [1]) + _frame("m2", [2]) + _frame("m3", [3])
    packets = protocol.parse_packets(raw)
    assert [json.loads(p)["m"] for p in packets] == ["m1", "m2", "m3"]


def test_parse_packets_returns_standalone_heartbeat():
    # Regression test: TradingView pings arrive as a bare `~h~<n>` with no
    # `~m~` length-prefix framing. protocol.parse_packets used to only
    # recognize `~m~`-framed messages, so a standalone heartbeat matched
    # nothing and was silently dropped - which meant live/engine.py's
    # BatchFetcher (which echoes `~h~` pings back to keep the WebSocket
    # alive) never actually received one to echo. Fixed to match the
    # heartbeat-aware parsing tv_history.py already had.
    packets = protocol.parse_packets("~h~42")
    assert packets == ["~h~42"]


def test_parse_packets_returns_heartbeat_trailing_a_data_frame():
    raw = _frame("qsd", ["a"]) + "~h~99"
    packets = protocol.parse_packets(raw)
    assert packets[-1] == "~h~99"


def test_parse_packets_does_not_swallow_a_data_frame_following_a_heartbeat():
    # Round-2 audit finding (Codex), verified here before fixing: a bare
    # heartbeat is not necessarily alone in a raw WS receive buffer - a
    # heartbeat can legitimately arrive bundled with a following framed
    # data message in the same TCP read. The old implementation appended
    # raw[pos:] (everything from the heartbeat to the END of the buffer)
    # as the heartbeat "packet" and then broke out of the loop entirely,
    # silently discarding any framed message that followed the heartbeat
    # in the same buffer instead of parsing it separately.
    raw = "~h~42" + _frame("qsd", ["a"])
    packets = protocol.parse_packets(raw)

    assert "~h~42" in packets
    data_packets = [p for p in packets if not p.startswith("~h~")]
    assert len(data_packets) == 1, f"expected the framed message to survive, got packets={packets!r}"
    assert json.loads(data_packets[0]) == {"m": "qsd", "p": ["a"]}


def test_parse_packets_does_not_swallow_a_frame_following_a_mid_buffer_heartbeat():
    # A stricter version of the same scenario: heartbeat sandwiched between
    # two framed messages must not swallow the SECOND one either.
    raw = _frame("m1", [1]) + "~h~7" + _frame("m2", [2])
    packets = protocol.parse_packets(raw)

    data_packets = [p for p in packets if not p.startswith("~h~")]
    assert [json.loads(p)["m"] for p in data_packets] == ["m1", "m2"], (
        f"expected both framed messages around the heartbeat to survive, got packets={packets!r}"
    )
    assert "~h~7" in packets


def test_parse_packets_handles_empty_and_malformed_input():
    assert protocol.parse_packets("") == []
    assert protocol.parse_packets("not a tv frame at all") == []


def test_receive_data_packets_echoes_every_heartbeat_and_returns_only_data():
    ws = _FakeWS((_frame("m1", [1]) + "~h~7" + _frame("m2", [2])).encode())

    packets = protocol.receive_data_packets(ws)

    assert [json.loads(packet)["m"] for packet in packets] == ["m1", "m2"]
    assert ws.sent == ["~m~4~m~~h~7"]


def test_bars_to_df_converts_ohlcv_rows():
    bars = [
        {"v": [1700000000, 1.1, 1.2, 1.0, 1.15, 100.0]},
        {"v": [1700000060, 1.15, 1.25, 1.1, 1.2, 200.0]},
    ]
    df = protocol.bars_to_df(bars)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 2
    assert df.iloc[0]["open"] == 1.1
    assert df.iloc[1]["volume"] == 200.0
    assert df.index.name is None


def test_bars_to_df_skips_short_rows_and_handles_nan_volume():
    nan = float("nan")
    bars = [
        {"v": [1700000000, 1.1, 1.2, 1.0]},  # too short, dropped
        {"v": [1700000060, 1.1, 1.2, 1.0, 1.15, nan]},
    ]
    df = protocol.bars_to_df(bars)
    assert len(df) == 1
    assert df.iloc[0]["volume"] is None


def test_bars_to_df_empty_input_returns_empty_dataframe():
    df = protocol.bars_to_df([])
    assert df.empty


def test_headers_get_dict_case_insensitive():
    headers = {"Retry-After": "30", "Content-Type": "text/plain"}
    assert protocol.headers_get(headers, "retry-after") == "30"
    assert protocol.headers_get(headers, "RETRY-AFTER") == "30"
    assert protocol.headers_get(headers, "missing") is None


def test_headers_get_list_of_colon_strings():
    headers = ["Retry-After: 15", "X-Other: value"]
    assert protocol.headers_get(headers, "retry-after") == "15"


def test_headers_get_none_input():
    assert protocol.headers_get(None, "retry-after") is None


def test_retry_after_seconds_numeric_header_clamped_to_max():
    headers = {"Retry-After": "9999"}
    assert protocol.retry_after_seconds(headers, default_seconds=5, max_seconds=60) == 60


def test_retry_after_seconds_falls_back_to_default_without_header():
    assert protocol.retry_after_seconds({}, default_seconds=42, max_seconds=100) == 42


def test_extract_ws_status_from_status_code_attr():
    class Err:
        status_code = 429
        resp_headers = {"Retry-After": "5"}

    status, headers = protocol.extract_ws_status(Err())
    assert status == 429
    assert headers == {"Retry-After": "5"}


def test_extract_ws_status_from_text_fallback():
    status, _ = protocol.extract_ws_status(Exception("Handshake status 403 Forbidden"))
    assert status == 403


def test_extract_ws_status_none_when_undetectable():
    status, _ = protocol.extract_ws_status(Exception("connection reset"))
    assert status is None


def test_extract_ws_close_code_from_attr():
    class Err:
        close_status_code = 1000

    assert protocol.extract_ws_close_code(Err()) == 1000


def test_extract_ws_close_code_ignores_http_status_range():
    class Err:
        code = 429  # outside the 1000-4999 WS close-code range

    assert protocol.extract_ws_close_code(Err()) is None


def test_extract_ws_close_code_none_for_unrelated_error():
    assert protocol.extract_ws_close_code(Exception("timeout")) is None


TOKEN_KEYWORDS = ("unauthorized", "auth_error", "not_authorized")


def test_classify_ws_error_normal_close():
    class Err:
        close_status_code = 1000

    kind, code, cooldown = protocol.classify_ws_error(
        Err(),
        reconnect_max_sec=300,
        rate_limit_cooldown_sec=300,
        forbidden_cooldown_sec=900,
        token_expiry_keywords=TOKEN_KEYWORDS,
    )
    assert (kind, code, cooldown) == ("normal_close", 1000, 0)


def test_classify_ws_error_rate_limit_status():
    class Err:
        status_code = 429
        resp_headers = {}

    kind, code, cooldown = protocol.classify_ws_error(
        Err(),
        reconnect_max_sec=300,
        rate_limit_cooldown_sec=120,
        forbidden_cooldown_sec=900,
        token_expiry_keywords=TOKEN_KEYWORDS,
    )
    assert kind == "rate_limit"
    assert code == 429
    assert cooldown == 120


def test_classify_ws_error_forbidden_status_gets_cooldown():
    class Err:
        status_code = 403
        resp_headers = {}

    kind, code, cooldown = protocol.classify_ws_error(
        Err(),
        reconnect_max_sec=300,
        rate_limit_cooldown_sec=300,
        forbidden_cooldown_sec=900,
        token_expiry_keywords=TOKEN_KEYWORDS,
    )
    assert kind == "auth"
    assert code == 403
    assert cooldown == 900


def test_classify_ws_error_unauthorized_status_no_cooldown():
    class Err:
        status_code = 401
        resp_headers = {}

    kind, code, cooldown = protocol.classify_ws_error(
        Err(),
        reconnect_max_sec=300,
        rate_limit_cooldown_sec=300,
        forbidden_cooldown_sec=900,
        token_expiry_keywords=TOKEN_KEYWORDS,
    )
    assert kind == "auth"
    assert code == 401
    assert cooldown == 0


def test_classify_ws_error_server_error_status():
    class Err:
        status_code = 502
        resp_headers = {}

    kind, code, cooldown = protocol.classify_ws_error(
        Err(),
        reconnect_max_sec=300,
        rate_limit_cooldown_sec=300,
        forbidden_cooldown_sec=900,
        token_expiry_keywords=TOKEN_KEYWORDS,
    )
    assert (kind, code, cooldown) == ("server", 502, 0)


def test_classify_ws_error_token_expiry_keyword_in_text():
    kind, _, cooldown = protocol.classify_ws_error(
        Exception("session unauthorized, please log in again"),
        reconnect_max_sec=300,
        rate_limit_cooldown_sec=300,
        forbidden_cooldown_sec=900,
        token_expiry_keywords=TOKEN_KEYWORDS,
    )
    assert kind == "auth"
    assert cooldown == 0


def test_classify_ws_error_unclassified_falls_back_to_network():
    kind, code, cooldown = protocol.classify_ws_error(
        Exception("connection reset by peer"),
        reconnect_max_sec=300,
        rate_limit_cooldown_sec=300,
        forbidden_cooldown_sec=900,
        token_expiry_keywords=TOKEN_KEYWORDS,
    )
    assert (kind, code, cooldown) == ("network", None, 0)
