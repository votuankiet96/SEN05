from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import redis as redis_lib

from dp_program import configuration
from dp_program.engine import (
    backfill,
    pipeline,
    sql_connector,
    websocket,
)
from dp_program.engine.sql_connector import candle_signature


@pytest.fixture(autouse=True)
def _reset_backfill_redis_state():
    """Backfill's Redis client/circuit are module globals; isolate tests."""
    backfill._redis_client = None
    backfill._redis_circuit_open_until = 0.0
    yield
    backfill._redis_client = None
    backfill._redis_circuit_open_until = 0.0


class _FakeRedisClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.published: list[tuple[str, str]] = []

    def publish(self, channel: str, payload: str) -> None:
        if self.fail:
            raise redis_lib.RedisError("boom")
        self.published.append((channel, payload))


def _symbol(symbol_id: int = 56, name: str = "GOLD") -> dict:
    return {
        "symbol_id": symbol_id,
        "exchange": "CAPITALCOM",
        "symbol": name,
        "asset_type": "Metal",
        "enabled": True,
        "live": True,
    }


def _timeframe(minutes: int = 5, code: str = "M5") -> dict:
    return {
        "code": code,
        "interval": str(minutes),
        "minutes": minutes,
        "staging_table": f"SEN.TF_{code}",
        "default_bars": 99_999,
    }


def _candle(timestamp: datetime, close: str = "101") -> dict:
    return {
        "symbol_id": 56,
        "exchange": "CAPITALCOM",
        "symbol": "GOLD",
        "timeframe": "M5",
        "timestamp": timestamp,
        "open": Decimal("100"),
        "high": Decimal("102"),
        "low": Decimal("99"),
        "close": Decimal(close),
        "volume": Decimal("12.5"),
    }


def _series_packet(timestamp: datetime) -> str:
    message = {
        "m": "timescale_update",
        "p": [
            "cs_test",
            {
                "s1": {
                    "s": [
                        {
                            "v": [
                                timestamp.timestamp(),
                                100,
                                102,
                                99,
                                101,
                                12.5,
                            ]
                        }
                    ]
                }
            },
        ],
    }
    payload = json.dumps(message, separators=(",", ":"))
    return f"~m~{len(payload)}~m~{payload}"


def _packet(message: dict) -> str:
    payload = json.dumps(message, separators=(",", ":"))
    return f"~m~{len(payload)}~m~{payload}"


def _batch_results(
    requests: list[websocket.FetchRequest],
) -> dict[tuple[int, str], websocket.FetchResult]:
    return {
        websocket.request_key(request): websocket.FetchResult(
            [], request.bars, 0
        )
        for request in requests
    }


class _FakeConnection:
    """Stand in for a pooled SQL connection without touching real SQL Server."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_batch_routes_interleaved_timeframes_over_one_socket(monkeypatch) -> None:
    config = configuration.load_config()
    config["tradingview"]["retry_count"] = 1
    m5 = _timeframe()
    m10 = _timeframe(10, "M10")
    timestamp = datetime(2026, 7, 28, 4, 0, tzinfo=timezone.utc)
    sessions = iter(("cs_1", "cs_2"))
    response = "".join(
        (
            _packet(
                {
                    "m": "timescale_update",
                    "p": [
                        "cs_2",
                        {"s2": {"s": [{"v": [timestamp.timestamp(), 200, 202, 199, 201, 2]}]}},
                    ],
                }
            ),
            _packet(
                {
                    "m": "timescale_update",
                    "p": [
                        "cs_1",
                        {"s1": {"s": [{"v": [timestamp.timestamp(), 100, 102, 99, 101, 1]}]}},
                    ],
                }
            ),
            _packet({"m": "series_completed", "p": ["cs_2", "s2"]}),
            _packet({"m": "series_completed", "p": ["cs_1", "s1"]}),
        )
    )

    class Socket:
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.closed = False

        def settimeout(self, _seconds):
            pass

        def send(self, message):
            self.sent.append(message)

        def recv(self):
            return response

        def close(self):
            self.closed = True

    socket = Socket()
    monkeypatch.setattr(websocket, "ensure_authenticated", lambda *_a, **_k: {})
    monkeypatch.setattr(websocket, "_session_id", lambda _prefix: next(sessions))
    monkeypatch.setattr(
        websocket.websocket, "create_connection", lambda *_a, **_k: socket
    )
    requests = [
        websocket.FetchRequest(_symbol(), m5, 3, 3, None),
        websocket.FetchRequest(_symbol(), m10, 3, 3, None),
    ]

    result = websocket.fetch_candles_batch(config, requests)

    methods = [
        json.loads(websocket.split_messages(message)[0])["m"] for message in socket.sent
    ]
    assert methods.count("set_auth_token") == 1
    assert methods.count("chart_create_session") == 2
    assert methods.count("resolve_symbol") == 2
    assert methods.count("create_series") == 2
    assert result[(56, "M5")].candles[0]["close"] == Decimal("101")
    assert result[(56, "M10")].candles[0]["close"] == Decimal("201")
    assert result[(56, "M10")].candles[0]["timeframe"] == "M10"
    assert socket.closed


def test_request_more_data_reuses_socket_and_requires_exact_completion(
    monkeypatch,
) -> None:
    oldest = datetime(2026, 7, 28, 3, 50, tzinfo=timezone.utc)
    responses = iter(
        (
            _series_packet(oldest + timedelta(minutes=10))
            + _packet({"m": "series_completed", "p": ["cs_test", "s1"]}),
            _series_packet(oldest)
            + _packet({"m": "series_completed", "p": ["cs_test", "s1"]}),
        )
    )

    class Socket:
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.closed = False

        def settimeout(self, _seconds):
            pass

        def send(self, message):
            self.sent.append(message)

        def recv(self):
            return next(responses)

        def close(self):
            self.closed = True

    socket = Socket()
    monkeypatch.setattr(
        websocket.websocket, "create_connection", lambda *_a, **_k: socket
    )
    monkeypatch.setattr(websocket, "_session_id", lambda _prefix: "cs_test")
    request = websocket.FetchRequest(
        _symbol(), _timeframe(), 2, 5, oldest
    )

    results, _metrics = websocket._fetch_batch_once(
        {
            "websocket_url": "wss://example",
            "timeout_seconds": 1,
            "auth_token": "x",
        },
        [request],
        5,
    )

    messages = [
        json.loads(websocket.split_messages(message)[0])
        for message in socket.sent
    ]
    extensions = [
        message for message in messages if message["m"] == "request_more_data"
    ]
    result = results[(56, "M5")]
    assert extensions == [
        {"m": "request_more_data", "p": ["cs_test", "s1", 3]}
    ]
    assert result.requested_bars == 5
    assert result.extension_rounds == 1
    assert [candle["timestamp"] for candle in result.candles] == [
        oldest,
        oldest + timedelta(minutes=10),
    ]
    assert socket.closed


def test_explicit_provider_rejection_is_not_retried(monkeypatch) -> None:
    config = configuration.load_config()
    attempts = []
    monkeypatch.setattr(websocket, "ensure_authenticated", lambda *_a, **_k: {})

    def reject(*_args):
        attempts.append(True)
        raise websocket.ProviderRequestError("provider request limit")

    monkeypatch.setattr(websocket, "_fetch_batch_once", reject)
    request = websocket.FetchRequest(_symbol(), _timeframe(), 3, 3)

    with pytest.raises(RuntimeError, match="provider request limit"):
        websocket.fetch_candles_batch(config, [request])

    assert len(attempts) == 1


def test_pipeline_accepts_prefetched_batch_without_second_network_fetch(
    monkeypatch,
) -> None:
    config = configuration.load_config()
    now = datetime(2026, 7, 28, 4, 5, tzinfo=timezone.utc)
    candle = _candle(now - timedelta(minutes=5))
    monkeypatch.setattr(
        pipeline,
        "fetch_candles",
        lambda *_a: pytest.fail("prefetched delivery must not open another socket"),
    )
    monkeypatch.setattr(pipeline, "fetch_existing_candles", lambda *_a, **_k: {})
    monkeypatch.setattr(pipeline, "enqueue", lambda *_a: None)
    monkeypatch.setattr(pipeline, "ack", lambda *_a: None)
    monkeypatch.setattr(
        pipeline,
        "bulk_upsert_candles",
        lambda _c, _tf, candles, **_k: {
            "input": len(candles),
            "staged_inserted": len(candles),
            "staged_updated": 0,
            "fact_inserted": len(candles),
            "fact_updated": 0,
            "affected": len(candles),
            "skipped": 0,
        },
    )

    result = pipeline.fetch_and_store(
        config,
        _symbol(),
        _timeframe(),
        workflow="live",
        bars=5,
        provider_candles=[candle],
        now=now,
    )

    assert result["received"] == result["affected"] == 1


def test_partial_fetch_without_series_completed_is_rejected(monkeypatch) -> None:
    class Socket:
        closed = False

        def settimeout(self, _seconds):
            pass

        def send(self, _message):
            pass

        def recv(self):
            return _series_packet(datetime(2026, 7, 28, 4, 0, tzinfo=timezone.utc))

        def close(self):
            self.closed = True

    socket = Socket()
    clock = iter((0.0, 0.0, 0.0, 0.0, 2.0))
    monkeypatch.setattr(websocket.websocket, "create_connection", lambda *_a, **_k: socket)
    monkeypatch.setattr(websocket.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(websocket, "_session_id", lambda _prefix: "cs_test")
    request = websocket.FetchRequest(_symbol(), _timeframe(), 3, 3)

    with pytest.raises(websocket.IncompleteFetchError, match="series=M5"):
        websocket._fetch_batch_once(
            {"websocket_url": "wss://example", "timeout_seconds": 1, "auth_token": "x"},
            [request],
            20_000,
        )
    assert socket.closed


def test_malformed_provider_bar_is_rejected_not_silently_dropped() -> None:
    message = {
        "m": "timescale_update",
        "p": ["cs_test", {"s1": {"s": [{"v": [1_700_000_000, 100]}]}}],
    }
    with pytest.raises(websocket.InvalidCandleError, match="fewer than six"):
        websocket.parse_series_message(message, _symbol(), _timeframe())


@pytest.mark.parametrize(
    "message",
    (
        "{not-json}",
        [],
        {"m": "timescale_update", "p": "invalid"},
        {"m": "timescale_update", "p": ["cs_test", {}]},
        {"m": "timescale_update", "p": ["cs_test", {"s1": []}]},
        {"m": "timescale_update", "p": ["cs_test", {"s1": {}}]},
        {"m": "timescale_update", "p": ["cs_test", {"s1": {"s": {}}}]},
        {"m": "timescale_update", "p": ["cs_test", {"s1": {"s": ""}}]},
        {"m": "timescale_update", "p": ["cs_test", {"s1": {"s": 0}}]},
        {"m": "timescale_update", "p": ["cs_test", {"s1": {"s": None}}]},
    ),
)
def test_malformed_provider_data_messages_fail_closed(message) -> None:
    with pytest.raises(websocket.MalformedResponseError):
        websocket.parse_series_message(message, _symbol(), _timeframe())


def test_malformed_packet_before_completion_rejects_the_whole_fetch(monkeypatch) -> None:
    completed = json.dumps(
        {"m": "series_completed", "p": ["cs_test", "s1"]},
        separators=(",", ":"),
    )
    malformed = "{not-json}"
    response = (
        _series_packet(datetime(2026, 7, 28, 4, 0, tzinfo=timezone.utc))
        + f"~m~{len(malformed)}~m~{malformed}"
        + f"~m~{len(completed)}~m~{completed}"
    )

    class Socket:
        closed = False

        def settimeout(self, _seconds):
            pass

        def send(self, _message):
            pass

        def recv(self):
            return response

        def close(self):
            self.closed = True

    socket = Socket()
    monkeypatch.setattr(websocket.websocket, "create_connection", lambda *_a, **_k: socket)
    monkeypatch.setattr(websocket, "_session_id", lambda _prefix: "cs_test")
    request = websocket.FetchRequest(_symbol(), _timeframe(), 3, 3)
    with pytest.raises(websocket.MalformedResponseError, match="invalid JSON"):
        websocket._fetch_batch_once(
            {"websocket_url": "wss://example", "timeout_seconds": 1, "auth_token": "x"},
            [request],
            20_000,
        )
    assert socket.closed


def test_malformed_data_shape_before_completion_rejects_the_whole_fetch(
    monkeypatch,
) -> None:
    malformed = json.dumps(
        {"m": "timescale_update", "p": ["cs_test", {"s1": {"s": {}}}]},
        separators=(",", ":"),
    )
    completed = json.dumps(
        {"m": "series_completed", "p": ["cs_test", "s1"]},
        separators=(",", ":"),
    )
    response = (
        _series_packet(datetime(2026, 7, 28, 4, 0, tzinfo=timezone.utc))
        + f"~m~{len(malformed)}~m~{malformed}"
        + f"~m~{len(completed)}~m~{completed}"
    )

    class Socket:
        def settimeout(self, _seconds):
            pass

        def send(self, _message):
            pass

        def recv(self):
            return response

        def close(self):
            pass

    monkeypatch.setattr(
        websocket.websocket, "create_connection", lambda *_a, **_k: Socket()
    )
    monkeypatch.setattr(websocket, "_session_id", lambda _prefix: "cs_test")
    request = websocket.FetchRequest(_symbol(), _timeframe(), 3, 3)
    with pytest.raises(websocket.MalformedResponseError, match="collection"):
        websocket._fetch_batch_once(
            {"websocket_url": "wss://example", "timeout_seconds": 1, "auth_token": "x"},
            [request],
            20_000,
        )


@pytest.mark.parametrize(
    "raw",
    (
        "~m~20~m~{}",
        "~m~bad~m~{}",
        "~m~2",
        "unknown",
        b"\xff",
    ),
)
def test_malformed_provider_frames_fail_closed(raw) -> None:
    with pytest.raises(websocket.MalformedResponseError):
        websocket.split_messages(raw)


def test_empty_websocket_response_is_treated_as_a_closed_connection() -> None:
    with pytest.raises(websocket.IncompleteFetchError, match="closed"):
        websocket.split_messages("")


def test_incomplete_fetch_never_reaches_spool_or_sql(monkeypatch) -> None:
    config = configuration.load_config()
    calls: list[str] = []
    monkeypatch.setattr(
        pipeline,
        "fetch_candles",
        lambda *_a: (_ for _ in ()).throw(websocket.IncompleteFetchError("partial")),
    )
    monkeypatch.setattr(pipeline, "enqueue", lambda *_a: calls.append("spool"))
    monkeypatch.setattr(
        pipeline, "fetch_existing_candles", lambda *_a, **_k: calls.append("compare")
    )
    monkeypatch.setattr(
        pipeline, "bulk_upsert_candles", lambda *_a, **_k: calls.append("write")
    )

    with pytest.raises(pipeline.PipelineError):
        pipeline.fetch_and_store(
            config,
            _symbol(),
            _timeframe(),
            workflow="live",
            bars=3,
        )
    assert calls == []


@pytest.mark.parametrize(
    ("minutes", "code"),
    [
        (5, "M5"),
        (10, "M10"),
        (15, "M15"),
        (20, "M20"),
        (30, "M30"),
        (45, "M45"),
        (60, "H1"),
        (90, "M90"),
        (120, "H2"),
        (180, "H3"),
        (240, "H4"),
        (360, "H6"),
        (480, "H8"),
        (1440, "D1"),
        (10080, "W"),
    ],
)
def test_bootstrap_plan_scans_60_days_with_bounded_initial_fetch(
    minutes: int, code: str
) -> None:
    config = configuration.load_config()
    now = datetime(2026, 7, 28, 4, 3, tzinfo=timezone.utc)
    plan = backfill.plan_backfill(
        config,
        _symbol(),
        _timeframe(minutes, code),
        {"earliest": None, "latest": now - timedelta(minutes=5)},
        now=now,
    )
    maximum = math.ceil(60 * 24 * 60 / minutes) + 3
    assert plan.window_start == now - timedelta(days=60)
    assert plan.window_end == now
    assert plan.bars == math.ceil(maximum * 0.75)
    assert plan.max_bars == maximum
    assert plan.complete_bootstrap
    assert plan.require_coverage
    assert plan.required_cursor == now - timedelta(minutes=5)
    assert plan.max_bars <= config["backfill"]["max_bars_per_request"]


def test_rolling_backfill_uses_tail_plus_overlap_not_60_days() -> None:
    config = configuration.load_config()
    now = datetime(2026, 7, 28, 4, 3, tzinfo=timezone.utc)
    latest = datetime(2026, 7, 28, 3, 55, tzinfo=timezone.utc)
    plan = backfill.plan_backfill(
        config,
        _symbol(),
        _timeframe(),
        {"earliest": now - timedelta(days=65), "latest": latest},
        now=now,
    )
    assert plan.window_start == latest - timedelta(minutes=10)
    assert plan.bars == 5
    assert plan.max_bars == 5
    assert not plan.complete_bootstrap
    assert plan.require_coverage
    assert plan.required_cursor == latest


def test_catchup_over_cap_fails_closed() -> None:
    config = configuration.load_config()
    now = datetime(2026, 7, 28, 4, 0, tzinfo=timezone.utc)
    latest = now - timedelta(minutes=5 * 20_001)
    with pytest.raises(backfill.CatchupWindowError, match="exceeds"):
        backfill.plan_backfill(
            config,
            _symbol(),
            _timeframe(),
            {"earliest": now - timedelta(days=65), "latest": latest},
            now=now,
        )


def test_future_fact_watermark_fails_closed_for_backfill() -> None:
    config = configuration.load_config()
    now = datetime(2026, 7, 28, 4, 0, tzinfo=timezone.utc)
    future = now + timedelta(minutes=5)
    with pytest.raises(backfill.CatchupWindowError, match="ahead"):
        backfill.plan_backfill(
            config,
            _symbol(),
            _timeframe(),
            {"latest": future},
            now=now,
        )


def test_pipeline_filters_exact_window_and_classifies_provider_delta(
    tmp_path, monkeypatch
) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    start = datetime(2026, 7, 25, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=20)
    before = _candle(start - timedelta(minutes=5))
    same = _candle(start)
    changed = _candle(start + timedelta(minutes=5), close="101.5")
    missing = _candle(start + timedelta(minutes=10))
    open_bar = _candle(end)
    provider = [before, same, changed, missing, open_bar]
    existing = {
        same["timestamp"].replace(tzinfo=None): candle_signature(same),
        changed["timestamp"].replace(tzinfo=None): candle_signature(
            _candle(changed["timestamp"], close="101")
        ),
    }
    delivered: list[dict] = []
    monkeypatch.setattr(pipeline, "fetch_candles", lambda *_a: provider)
    monkeypatch.setattr(pipeline, "fetch_existing_candles", lambda *_a, **_k: existing)
    monkeypatch.setattr(pipeline, "enqueue", lambda _c, candles: delivered.extend(candles))
    monkeypatch.setattr(pipeline, "ack", lambda *_a: None)
    monkeypatch.setattr(
        pipeline,
        "bulk_upsert_candles",
        lambda _c, _tf, candles, **_k: {
            "input": len(candles),
            "staged_inserted": 0,
            "staged_updated": 0,
            "fact_inserted": 1,
            "fact_updated": 1,
            "affected": 2,
            "skipped": max(0, len(candles) - 2),
        },
    )

    result = pipeline.fetch_and_store(
        config,
        _symbol(),
        _timeframe(),
        workflow="backfill",
        bars=10,
        window_start=start,
        window_end=end,
        require_coverage=True,
        now=end,
    )

    assert result["provider_observed"] == 3
    assert result["missing_before"] == 1
    assert result["changed_before"] == 1
    assert result["unchanged_before"] == 1
    assert [item["timestamp"] for item in delivered] == [
        same["timestamp"],
        changed["timestamp"],
        missing["timestamp"],
    ]


def test_provider_absence_on_weekend_is_not_a_gap(monkeypatch) -> None:
    config = configuration.load_config()
    friday = _candle(datetime(2026, 7, 24, 21, 0, tzinfo=timezone.utc))
    monday = _candle(datetime(2026, 7, 27, 0, 0, tzinfo=timezone.utc))
    existing = {
        item["timestamp"].replace(tzinfo=None): candle_signature(item)
        for item in (friday, monday)
    }
    writes: list[object] = []
    monkeypatch.setattr(pipeline, "fetch_candles", lambda *_a: [friday, monday])
    monkeypatch.setattr(pipeline, "fetch_existing_candles", lambda *_a, **_k: existing)
    monkeypatch.setattr(pipeline, "enqueue", lambda *_a: writes.append("spool"))
    monkeypatch.setattr(
        pipeline, "bulk_upsert_candles", lambda *_a, **_k: writes.append("sql")
    )

    result = pipeline.fetch_and_store(
        config,
        _symbol(),
        _timeframe(),
        workflow="backfill",
        bars=10,
        window_start=friday["timestamp"],
        window_end=monday["timestamp"] + timedelta(minutes=5),
        require_coverage=True,
        now=monday["timestamp"] + timedelta(minutes=5),
    )
    assert result["missing_before"] == 0
    assert result["changed_before"] == 0
    assert writes == []


def test_short_bootstrap_coverage_does_not_write_or_complete(monkeypatch) -> None:
    config = configuration.load_config()
    start = datetime(2026, 5, 29, 4, 0, tzinfo=timezone.utc)
    calls: list[str] = []
    monkeypatch.setattr(
        pipeline,
        "fetch_candles",
        lambda *_a: [_candle(start + timedelta(days=1))],
    )
    monkeypatch.setattr(pipeline, "fetch_existing_candles", lambda *_a, **_k: calls.append("read"))
    monkeypatch.setattr(pipeline, "enqueue", lambda *_a: calls.append("spool"))
    monkeypatch.setattr(
        pipeline, "bulk_upsert_candles", lambda *_a, **_k: calls.append("write")
    )

    with pytest.raises(pipeline.PipelineError, match="coverage"):
        pipeline.fetch_and_store(
            config,
            _symbol(),
            _timeframe(),
            workflow="backfill",
            bars=17_283,
            window_start=start,
            window_end=start + timedelta(days=60),
            require_coverage=True,
            now=start + timedelta(days=60),
        )
    assert calls == []


def test_response_ending_before_fact_cursor_is_rejected(monkeypatch) -> None:
    config = configuration.load_config()
    start = datetime(2026, 7, 28, 3, 45, tzinfo=timezone.utc)
    cursor = start + timedelta(minutes=10)
    calls: list[str] = []
    monkeypatch.setattr(pipeline, "fetch_candles", lambda *_a: [_candle(start)])
    monkeypatch.setattr(
        pipeline, "fetch_existing_candles", lambda *_a, **_k: calls.append("compare")
    )
    monkeypatch.setattr(pipeline, "enqueue", lambda *_a: calls.append("spool"))
    monkeypatch.setattr(
        pipeline, "bulk_upsert_candles", lambda *_a, **_k: calls.append("write")
    )
    with pytest.raises(pipeline.PipelineError, match="cursor"):
        pipeline.fetch_and_store(
            config,
            _symbol(),
            _timeframe(),
            workflow="live",
            bars=5,
            window_start=start,
            window_end=cursor + timedelta(minutes=5),
            require_coverage=True,
            required_cursor=cursor,
            now=cursor + timedelta(minutes=5),
        )
    assert calls == []


def test_zero_delta_window_does_not_call_bulk_upsert(monkeypatch) -> None:
    config = configuration.load_config()
    start = datetime(2026, 5, 29, 4, 0, tzinfo=timezone.utc)
    inside = _candle(start + timedelta(minutes=5))
    provider = [_candle(start - timedelta(minutes=5)), inside]
    existing = {
        inside["timestamp"].replace(tzinfo=None): candle_signature(inside)
    }
    monkeypatch.setattr(pipeline, "fetch_candles", lambda *_a: provider)
    monkeypatch.setattr(pipeline, "fetch_existing_candles", lambda *_a, **_k: existing)
    monkeypatch.setattr(pipeline, "enqueue", lambda *_a: pytest.fail("no candle spool"))
    monkeypatch.setattr(
        pipeline,
        "bulk_upsert_candles",
        lambda *_a, **_k: pytest.fail("zero-delta window must not call bulk_upsert_candles"),
    )

    result = pipeline.fetch_and_store(
        config,
        _symbol(),
        _timeframe(),
        workflow="backfill",
        bars=17_283,
        window_start=start,
        window_end=start + timedelta(days=60),
        require_coverage=True,
        now=start + timedelta(days=60),
    )
    assert result["delivery_input"] == 0


def test_bootstrap_group_reserves_capacity_for_same_socket_extension(
    monkeypatch,
) -> None:
    config = configuration.load_config()
    symbol = _symbol()
    timeframes = [
        _timeframe(minutes, code)
        for minutes, code in (
            (5, "M5"), (10, "M10"), (15, "M15"), (20, "M20"), (30, "M30"),
            (45, "M45"), (60, "H1"), (90, "M90"), (120, "H2"), (180, "H3"),
            (240, "H4"), (360, "H6"), (480, "H8"), (1440, "D1"), (10080, "W"),
        )
    ]
    pairs = [(symbol, timeframe) for timeframe in timeframes]
    latest = datetime(2026, 7, 28, 3, 55, tzinfo=timezone.utc)
    monkeypatch.setattr(
        backfill,
        "get_pair_states",
        lambda *_a: {
            (56, timeframe["code"]): {
                "earliest": None,
                "latest": latest,
            }
            for timeframe in timeframes
        },
    )
    now = datetime(2026, 7, 28, 4, 0, tzinfo=timezone.utc)

    first = backfill.next_backfill_group(config, pairs, now=now)
    second = backfill.next_backfill_group(config, pairs[len(first) :], now=now)

    assert [timeframe["code"] for _symbol_value, timeframe in first] == ["M5"]
    assert [timeframe["code"] for _symbol_value, timeframe in second] == [
        "M10",
        "M15",
        "M20",
    ]


def test_backfill_circuit_defers_after_two_transport_group_failures(
    monkeypatch,
) -> None:
    config = configuration.load_config()
    names = [(56, "GOLD"), (81, "BTCUSD"), (8, "US500"), (9, "US100")]
    pairs = [(_symbol(symbol_id, name), _timeframe()) for symbol_id, name in names]
    latest = datetime(2026, 7, 28, 3, 55, tzinfo=timezone.utc)
    monkeypatch.setattr(
        backfill,
        "get_pair_states",
        lambda *_a: {
            (symbol_id, "M5"): {
                "earliest": datetime(2020, 1, 1, tzinfo=timezone.utc),
                "latest": latest,
            }
            for symbol_id, _name in names
        },
    )
    visited: list[int] = []

    def fail(_config, requests):
        visited.append(int(requests[0].symbol["symbol_id"]))
        raise websocket.IncompleteFetchError("provider unavailable")

    monkeypatch.setattr(backfill, "fetch_candles_batch", fail)
    monkeypatch.setattr(
        backfill,
        "fetch_and_store",
        lambda *_a, **_k: pytest.fail("failed transport must not reach SQL"),
    )

    result = backfill.run_backfill_pairs(
        config, pairs, now=latest + timedelta(minutes=5)
    )

    assert visited == [56, 81]
    assert result["failed"] == result["group_failures"] == 2
    assert result["deferred_pairs"] == [
        "CAPITALCOM:US500/M5",
        "CAPITALCOM:US100/M5",
    ]


def test_sql_failure_after_spool_retains_provider_window(tmp_path, monkeypatch) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    candle = _candle(datetime(2026, 7, 28, 3, 55, tzinfo=timezone.utc))
    monkeypatch.setattr(pipeline, "fetch_candles", lambda *_a: [candle])
    monkeypatch.setattr(pipeline, "fetch_existing_candles", lambda *_a, **_k: {})
    monkeypatch.setattr(
        pipeline,
        "bulk_upsert_candles",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("SQL unavailable")),
    )
    with pytest.raises(pipeline.PipelineError) as failure:
        pipeline.fetch_and_store(
            config,
            _symbol(),
            _timeframe(),
            workflow="live",
            bars=3,
            now=datetime(2026, 7, 28, 4, 3, tzinfo=timezone.utc),
        )
    assert failure.value.stage == "sql_delivery"
    assert pipeline.enqueue.__module__.endswith("spool")
    from dp_program.engine import spool

    assert spool.pending_status(config)["pending"] == 1


def test_ack_failure_after_commit_leaves_replayable_spool(tmp_path, monkeypatch) -> None:
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    candle = _candle(datetime(2026, 7, 28, 3, 55, tzinfo=timezone.utc))
    monkeypatch.setattr(pipeline, "fetch_candles", lambda *_a: [candle])
    monkeypatch.setattr(pipeline, "fetch_existing_candles", lambda *_a, **_k: {})
    monkeypatch.setattr(
        pipeline,
        "bulk_upsert_candles",
        lambda *_a, **_k: {
            "input": 1,
            "staged_inserted": 1,
            "staged_updated": 0,
            "fact_inserted": 1,
            "fact_updated": 0,
            "affected": 1,
            "skipped": 0,
        },
    )
    monkeypatch.setattr(
        pipeline, "ack", lambda *_a: (_ for _ in ()).throw(PermissionError("locked"))
    )
    with pytest.raises(pipeline.PipelineError) as failure:
        pipeline.fetch_and_store(
            config,
            _symbol(),
            _timeframe(),
            workflow="live",
            bars=3,
            now=datetime(2026, 7, 28, 4, 3, tzinfo=timezone.utc),
        )
    assert failure.value.stage == "spool_ack"
    from dp_program.engine import spool

    assert spool.pending_status(config)["pending"] == 1


def test_series_completion_must_match_registered_session_and_series() -> None:
    lookup = {("cs_active", "s1"): {}}
    assert websocket._route(
        {"m": "series_completed", "p": ["cs_active", "s1"]},
        lookup,
    ) == ("cs_active", "s1")
    with pytest.raises(websocket.MalformedResponseError, match="unknown series"):
        websocket._route(
            {"m": "series_completed", "p": ["cs_other", "s1"]},
            lookup,
        )


def test_pipeline_tags_each_candle_with_its_comparison_signature(
    monkeypatch, tmp_path
) -> None:
    """candle_signature() must be computed once per candle, not once for
    comparison and again inside bulk_upsert_candles()."""
    config = configuration.load_config()
    config["app"]["runtime_dir"] = str(tmp_path)
    candle = _candle(datetime(2026, 7, 28, 3, 55, tzinfo=timezone.utc))
    signature_calls: list[datetime] = []
    real_signature = candle_signature

    def counting_signature(value):
        signature_calls.append(value["timestamp"])
        return real_signature(value)

    delivered: list[dict] = []
    monkeypatch.setattr(pipeline, "fetch_candles", lambda *_a: [candle])
    monkeypatch.setattr(pipeline, "candle_signature", counting_signature)
    monkeypatch.setattr(pipeline, "fetch_existing_candles", lambda *_a, **_k: {})
    monkeypatch.setattr(pipeline, "enqueue", lambda *_a: None)
    monkeypatch.setattr(pipeline, "ack", lambda *_a: None)
    monkeypatch.setattr(
        pipeline,
        "bulk_upsert_candles",
        lambda _c, _tf, candles, **_k: delivered.extend(candles) or {
            "input": len(candles), "staged_inserted": 0, "staged_updated": 0,
            "fact_inserted": 0, "fact_updated": 0, "affected": len(candles), "skipped": 0,
        },
    )

    pipeline.fetch_and_store(
        config, _symbol(), _timeframe(), workflow="live", bars=3,
        now=datetime(2026, 7, 28, 4, 3, tzinfo=timezone.utc),
    )

    assert signature_calls == [candle["timestamp"]]
    assert delivered[0]["_signature"] == real_signature(candle)


def test_backfill_group_reuses_one_connection_across_its_pairs(monkeypatch) -> None:
    config = configuration.load_config()
    symbol = _symbol()
    pairs = [(symbol, _timeframe()), (symbol, _timeframe(10, "M10"))]
    latest = datetime(2026, 7, 28, 3, 55, tzinfo=timezone.utc)
    monkeypatch.setattr(
        backfill,
        "get_pair_states",
        lambda *_a: {
            (56, "M5"): {"earliest": datetime(2020, 1, 1, tzinfo=timezone.utc), "latest": latest},
            (56, "M10"): {"earliest": datetime(2020, 1, 1, tzinfo=timezone.utc), "latest": latest},
        },
    )
    monkeypatch.setattr(
        backfill, "fetch_candles_batch", lambda _config, requests: _batch_results(requests)
    )
    opened: list[_FakeConnection] = []

    def fake_get_connection(_config):
        connection = _FakeConnection()
        opened.append(connection)
        return connection

    monkeypatch.setattr(backfill, "get_connection", fake_get_connection)
    seen: list[object] = []
    monkeypatch.setattr(
        backfill,
        "fetch_and_store",
        lambda *_a, **kwargs: seen.append(kwargs["connection"]) or {"affected": 0},
    )

    backfill.run_backfill_pairs(config, pairs, now=latest + timedelta(minutes=5))

    assert len(opened) == 1
    assert seen == [opened[0], opened[0]]
    assert opened[0].closed is True


def test_backfill_publishes_redis_event_only_for_pairs_with_new_sql_rows(
    monkeypatch,
) -> None:
    config = configuration.load_config()
    config["redis"]["enabled"] = True
    symbol = _symbol()
    pairs = [(symbol, _timeframe()), (symbol, _timeframe(10, "M10"))]
    latest = datetime(2026, 7, 28, 3, 55, tzinfo=timezone.utc)
    monkeypatch.setattr(
        backfill,
        "get_pair_states",
        lambda *_a: {
            (56, "M5"): {"earliest": datetime(2020, 1, 1, tzinfo=timezone.utc), "latest": latest},
            (56, "M10"): {"earliest": datetime(2020, 1, 1, tzinfo=timezone.utc), "latest": latest},
        },
    )
    monkeypatch.setattr(
        backfill, "fetch_candles_batch", lambda _config, requests: _batch_results(requests)
    )
    monkeypatch.setattr(backfill, "get_connection", lambda _c: _FakeConnection())
    monkeypatch.setattr(
        backfill,
        "fetch_and_store",
        lambda _c, _s, timeframe, **_k: {"affected": 1 if timeframe["code"] == "M5" else 0},
    )
    client = _FakeRedisClient()
    monkeypatch.setattr(backfill, "_get_redis_client", lambda _settings: client)

    backfill.run_backfill_pairs(config, pairs, now=latest + timedelta(minutes=5))

    assert len(client.published) == 1
    channel, payload = client.published[0]
    assert channel == "dp:events:backfill"
    assert json.loads(payload) == {"symbol": "GOLD", "timeframe": "M5"}


def test_backfill_redis_publish_failure_does_not_fail_the_sql_write(monkeypatch) -> None:
    config = configuration.load_config()
    config["redis"]["enabled"] = True
    symbol = _symbol()
    pairs = [(symbol, _timeframe())]
    latest = datetime(2026, 7, 28, 3, 55, tzinfo=timezone.utc)
    monkeypatch.setattr(
        backfill,
        "get_pair_states",
        lambda *_a: {(56, "M5"): {"earliest": datetime(2020, 1, 1, tzinfo=timezone.utc), "latest": latest}},
    )
    monkeypatch.setattr(
        backfill, "fetch_candles_batch", lambda _config, requests: _batch_results(requests)
    )
    monkeypatch.setattr(backfill, "get_connection", lambda _c: _FakeConnection())
    monkeypatch.setattr(backfill, "fetch_and_store", lambda *_a, **_k: {"affected": 1})
    monkeypatch.setattr(backfill, "_get_redis_client", lambda _settings: _FakeRedisClient(fail=True))

    result = backfill.run_backfill_pairs(config, pairs, now=latest + timedelta(minutes=5))

    assert result["ok"] == 1
    assert result["failed"] == 0
    assert backfill._redis_circuit_open_until > 0.0


def test_backfill_skips_redis_publish_when_redis_disabled(monkeypatch) -> None:
    config = configuration.load_config()
    config["redis"]["enabled"] = False
    symbol = _symbol()
    pairs = [(symbol, _timeframe())]
    latest = datetime(2026, 7, 28, 3, 55, tzinfo=timezone.utc)
    monkeypatch.setattr(
        backfill,
        "get_pair_states",
        lambda *_a: {(56, "M5"): {"earliest": datetime(2020, 1, 1, tzinfo=timezone.utc), "latest": latest}},
    )
    monkeypatch.setattr(
        backfill, "fetch_candles_batch", lambda _config, requests: _batch_results(requests)
    )
    monkeypatch.setattr(backfill, "get_connection", lambda _c: _FakeConnection())
    monkeypatch.setattr(backfill, "fetch_and_store", lambda *_a, **_k: {"affected": 1})

    def _fail_if_called(_settings):
        pytest.fail("redis client must not be constructed when redis.enabled=false")

    monkeypatch.setattr(backfill, "_get_redis_client", _fail_if_called)

    result = backfill.run_backfill_pairs(config, pairs, now=latest + timedelta(minutes=5))

    assert result["ok"] == 1


def test_backfill_event_carries_delivered_candle_scope_for_strategy_lab(
    monkeypatch,
) -> None:
    """strategy_lab needs to know WHICH candles changed, not just that some did,
    so it can recompute a narrow window instead of the whole pair's history."""
    config = configuration.load_config()
    config["redis"]["enabled"] = True
    symbol = _symbol()
    pairs = [(symbol, _timeframe())]
    latest = datetime(2026, 7, 28, 3, 55, tzinfo=timezone.utc)
    monkeypatch.setattr(
        backfill,
        "get_pair_states",
        lambda *_a: {(56, "M5"): {"earliest": datetime(2020, 1, 1, tzinfo=timezone.utc), "latest": latest}},
    )
    monkeypatch.setattr(
        backfill, "fetch_candles_batch", lambda _config, requests: _batch_results(requests)
    )
    monkeypatch.setattr(backfill, "get_connection", lambda _c: _FakeConnection())
    # Out-of-order on purpose: from/to must come from min()/max() over the
    # delivered candles, not from the first/last list entry.
    delivered = [
        _candle(datetime(2026, 7, 28, 3, 50, tzinfo=timezone.utc)),
        _candle(datetime(2026, 7, 27, 16, 0, tzinfo=timezone.utc)),
        _candle(datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)),
    ]
    monkeypatch.setattr(
        backfill, "fetch_and_store",
        lambda *_a, **_k: {"affected": 3, "delivered_candles": delivered},
    )
    client = _FakeRedisClient()
    monkeypatch.setattr(backfill, "_get_redis_client", lambda _settings: client)

    backfill.run_backfill_pairs(config, pairs, now=latest + timedelta(minutes=5))

    assert len(client.published) == 1
    _channel, payload = client.published[0]
    assert json.loads(payload) == {
        "symbol": "GOLD", "timeframe": "M5",
        "candle_count": 3,
        "from": "2026-07-27 16:00:00", "to": "2026-07-28 08:00:00",
    }


def test_backfill_event_omits_scope_fields_when_delivered_candles_is_empty(
    monkeypatch,
) -> None:
    """Defensive fallback: affected > 0 but delivered_candles missing/empty
    must still publish the old 2-field shape, not a partial/broken one."""
    config = configuration.load_config()
    config["redis"]["enabled"] = True
    symbol = _symbol()
    pairs = [(symbol, _timeframe())]
    latest = datetime(2026, 7, 28, 3, 55, tzinfo=timezone.utc)
    monkeypatch.setattr(
        backfill,
        "get_pair_states",
        lambda *_a: {(56, "M5"): {"earliest": datetime(2020, 1, 1, tzinfo=timezone.utc), "latest": latest}},
    )
    monkeypatch.setattr(
        backfill, "fetch_candles_batch", lambda _config, requests: _batch_results(requests)
    )
    monkeypatch.setattr(backfill, "get_connection", lambda _c: _FakeConnection())
    # affected > 0 without delivered_candles at all -- should not happen per
    # pipeline.py today, but the publish path must degrade safely if it did.
    monkeypatch.setattr(backfill, "fetch_and_store", lambda *_a, **_k: {"affected": 1})
    client = _FakeRedisClient()
    monkeypatch.setattr(backfill, "_get_redis_client", lambda _settings: client)

    backfill.run_backfill_pairs(config, pairs, now=latest + timedelta(minutes=5))

    _channel, payload = client.published[0]
    assert json.loads(payload) == {"symbol": "GOLD", "timeframe": "M5"}
