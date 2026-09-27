"""Unit tests for read-only Redis research tools, using no real Redis."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from og_signal.redis_client import pair_list_key, parse_pair_key
from research import audit_dashboard, redis_sql_audit
from research.redis_event_observer import parse_event_payload, read_snapshot_after_event
from research.redis_signal_audit import audit_signal_pair, parse_signal_list_key
from research.redis_snapshot_audit import audit_snapshot_key
from research.redis_sql_audit import (
    add_audit_indicators,
    audit_pair,
    compare_sql_redis_frames,
    read_redis_for_sql_window,
    select_pairs,
)

# DP's stamp for the 2026-08-26 10:00 UTC bar, and the pair key it hangs off.
CANDLE_STAMP = "2026-08-26 10:00:00"
NEXT_STAMP = "2026-08-26 11:00:00"
PAIR_KEY = "L_CANDLE_US30_H1"


class FakePipeline:
    def __init__(self, hashes):
        self.hashes = hashes
        self.queued: list[tuple[str, str, tuple[str, ...]]] = []

    def hgetall(self, key):
        self.queued.append(("hgetall", key, ()))

    def hmget(self, key, *fields):
        self.queued.append(("hmget", key, fields))

    def execute(self):
        results = []
        for operation, key, fields in self.queued:
            values = self.hashes.get(key, {})
            if operation == "hgetall":
                results.append(values)
            else:
                results.append([values.get(field) for field in fields])
        return results


class FakeSnapshotRedis:
    """Mimics DP's layout: one pair LIST of stamps plus one HASH per bar."""

    def __init__(self, bars, *, key_type="list", drop_hashes=()):
        # bars: {stamp: {field: value}}
        self.stamps = list(bars)
        self.hashes = {
            f"{PAIR_KEY}:{stamp}": ({} if stamp in drop_hashes else dict(fields))
            for stamp, fields in bars.items()
        }
        self.key_type = key_type

    def type(self, _key):
        return self.key_type

    def lrange(self, key, _start, _stop):
        assert key == PAIR_KEY, key
        return list(self.stamps)

    def lindex(self, key, _index):
        assert key == PAIR_KEY, key
        return self.stamps[-1] if self.stamps else None

    def llen(self, _key):
        return len(self.stamps)

    def exists(self, key):
        return 1 if self.hashes.get(key) else 0

    def pipeline(self, transaction=True):
        assert transaction is False
        return FakePipeline(self.hashes)


SIGNAL_LIST_KEY = "L_SIGNAL_US30_H1_COMBO"


class FakeSignalPipeline:
    def __init__(self, hashes, ttls):
        self.hashes = hashes
        self.ttls = ttls
        self.queued: list[tuple[str, str]] = []

    def hgetall(self, key):
        self.queued.append(("hgetall", key))

    def ttl(self, key):
        self.queued.append(("ttl", key))

    def execute(self):
        results = []
        for operation, key in self.queued:
            results.append(self.hashes.get(key, {}) if operation == "hgetall" else self.ttls.get(key, -2))
        return results


class FakeSignalRedis:
    """Mimics OG's own DB1 layout: one (symbol,timeframe,strategy) LIST of
    signal stamps plus one HASH per signal -- same List+Hash shape as
    FakeSnapshotRedis above, just for DB1 instead of DB0.
    """

    def __init__(self, signals, *, list_key=SIGNAL_LIST_KEY, key_type="list", drop_hashes=(), ttls=None):
        # signals: {stamp: {field: value}}
        self.stamps = list(signals)
        self.hashes = {
            f"{list_key}:{stamp}": ({} if stamp in drop_hashes else dict(fields))
            for stamp, fields in signals.items()
        }
        self.ttl_values = ttls or dict.fromkeys(self.hashes, 604800)
        self.key_type = key_type

    def type(self, _key):
        return self.key_type

    def lrange(self, _key, _start, _stop):
        return list(self.stamps)

    def pipeline(self, transaction=True):
        assert transaction is False
        return FakeSignalPipeline(self.hashes, self.ttl_values)


def candle_fields(*, low="99", high="103", stamp=CANDLE_STAMP):
    """One bar HASH exactly as DP writes it (2026-09-15 05:37 UTC contract):
    six flat string fields, no `volume` at all any more.

    `timestamp` is not OHLCV -- it is present here on purpose so the audit
    is exercised against the real shape: extra non-price fields must be
    tolerated, and the text ones must never reach a float(). `timestamp`
    equals the stamp naming the key (both come from the same bar open time);
    there is no `bartime` field.
    """
    return {
        "timestamp": stamp,
        "open": "100",
        "high": high,
        "low": low,
        "close": "102",
        "time_update": "2026-08-26 10:00:04",
    }


SIGNAL_STAMP = "2026-08-26 10:00:00"


def signal_fields(**overrides):
    """One signal HASH exactly as og_signal.signal_publisher.to_hash_fields
    writes it for a combo (STOP) signal."""
    fields = {
        "symbol": "US30",
        "timeframe": "H1",
        "strategy": "combo",
        "bartime": "2026-08-26T10:00:00Z",
        "tradeSide": "BUY",
        "orderType": "STOP",
        "stopPrice": "102.5",
        "stopLoss": "99.5",
        "takeProfit": "108.5",
        "expirationTimestamp": "1787745600",  # 2026-08-26T12:00:00Z
        "clientOrderId": "combo:H1:US30:20260826T100000Z",
        "comment": "combo buy",
        "atr": "1.5",
        "valid_from": "2026-08-26T11:00:00Z",
    }
    fields.update(overrides)
    return fields


def test_snapshot_key_parser_accepts_only_the_pair_list_format():
    assert parse_pair_key("L_CANDLE_BTCUSD_M30", "L_CANDLE") == ("BTCUSD", "M30")
    assert pair_list_key("L_CANDLE", "BTCUSD", "M30") == "L_CANDLE_BTCUSD_M30"
    # A per-bar HASH key carries a colon and is never a pair list.
    assert parse_pair_key("L_CANDLE_BTCUSD_M30:20260826_100000", "L_CANDLE") is None
    assert parse_pair_key("L_CANDLE_BTCUSD", "L_CANDLE") is None
    assert parse_pair_key("OTHER_BTCUSD_M30", "L_CANDLE") is None


def test_snapshot_audit_passes_valid_ohlcv_and_warns_only_for_short_snapshot():
    client = FakeSnapshotRedis({CANDLE_STAMP: candle_fields()})
    report = audit_snapshot_key(
        client,
        PAIR_KEY,
        key_prefix="L_CANDLE",
        snapshot_bars=1,
        now=datetime(2026, 8, 26, 11, tzinfo=timezone.utc),
    )
    assert report["status"] == "PASS"
    assert report["bars"] == 1
    assert report["symbol"] == "US30"
    assert report["timeframe"] == "H1"
    assert report["last_bartime"] == "2026-08-26T10:00:00Z"

    short = audit_snapshot_key(
        client,
        PAIR_KEY,
        key_prefix="L_CANDLE",
        snapshot_bars=500,
    )
    assert short["status"] == "WARN"
    assert "does not prove missing history" in short["warnings"][0]


def test_snapshot_audit_rejects_an_orphan_stamp_and_invalid_ohlc():
    orphan = audit_snapshot_key(
        FakeSnapshotRedis({CANDLE_STAMP: candle_fields()}, drop_hashes=(CANDLE_STAMP,)),
        PAIR_KEY,
        key_prefix="L_CANDLE",
        snapshot_bars=1,
    )
    assert orphan["status"] == "FAIL"
    assert any("no candle hash" in error for error in orphan["errors"])

    bad_ohlc = audit_snapshot_key(
        FakeSnapshotRedis({CANDLE_STAMP: candle_fields(low="101")}),
        PAIR_KEY,
        key_prefix="L_CANDLE",
        snapshot_bars=1,
    )
    assert bad_ohlc["status"] == "FAIL"
    assert any("low is above" in error for error in bad_ohlc["errors"])


def test_snapshot_audit_rejects_a_stamp_that_is_not_dp_format():
    """The stamp is the bar's only timestamp, so a malformed one is a fault."""
    report = audit_snapshot_key(
        FakeSnapshotRedis({"2026-08-26_10:00:00": candle_fields()}),
        PAIR_KEY,
        key_prefix="L_CANDLE",
        snapshot_bars=1,
    )
    assert report["status"] == "FAIL"
    assert any("stamp must match" in error for error in report["errors"])


def test_parse_event_payload_accepts_a_well_formed_dp_message():
    payload = json.dumps(
        {"symbol": "US30", "timeframe": "H1", "candles": [candle_fields()]}
    )
    assert parse_event_payload(payload) == {
        "symbol": "US30", "timeframe": "H1", "candle_count": 1,
    }


def test_parse_event_payload_rejects_malformed_or_incomplete_data():
    assert parse_event_payload("not json") is None
    assert parse_event_payload(json.dumps({"symbol": "US30"})) is None
    assert parse_event_payload(json.dumps(["not", "an", "object"])) is None


def test_read_snapshot_after_event_reports_the_newest_bar_from_its_stamp():
    client = FakeSnapshotRedis(
        {CANDLE_STAMP: candle_fields(), NEXT_STAMP: candle_fields(stamp=NEXT_STAMP)}
    )

    report = read_snapshot_after_event(
        client, key_prefix="L_CANDLE", timeframe="H1", symbol="US30",
    )
    assert report["status"] == "PASS"
    assert report["bars"] == 2
    # Rendered from the stamp naming the bar -- the only timestamp there is.
    assert report["last_bartime"] == "2026-08-26T11:00:00Z"


def test_read_snapshot_after_event_fails_on_empty_pair_list():
    report = read_snapshot_after_event(
        FakeSnapshotRedis({}), key_prefix="L_CANDLE", timeframe="H1", symbol="US30",
    )
    assert report["status"] == "FAIL"
    assert "empty" in report["message"]


def test_read_snapshot_after_event_fails_when_newest_stamp_has_no_hash():
    report = read_snapshot_after_event(
        FakeSnapshotRedis({CANDLE_STAMP: candle_fields()}, drop_hashes=(CANDLE_STAMP,)),
        key_prefix="L_CANDLE",
        timeframe="H1",
        symbol="US30",
    )
    assert report["status"] == "FAIL"
    assert "no candle hash" in report["message"]


def test_signal_key_parser_accepts_only_the_list_format():
    assert parse_signal_list_key(SIGNAL_LIST_KEY, "L_SIGNAL") == ("US30", "H1", "combo")
    # ma_cross/ema_cross carry their own underscore -- must not be split naively.
    assert parse_signal_list_key("L_SIGNAL_US30_M30_MA_CROSS", "L_SIGNAL") == (
        "US30", "M30", "ma_cross",
    )
    # A per-signal HASH key carries a colon and is never a list.
    assert parse_signal_list_key(f"{SIGNAL_LIST_KEY}:{SIGNAL_STAMP}", "L_SIGNAL") is None
    assert parse_signal_list_key("L_SIGNAL_US30", "L_SIGNAL") is None


def test_signal_audit_passes_a_well_formed_combo_signal():
    report = audit_signal_pair(
        FakeSignalRedis({SIGNAL_STAMP: signal_fields()}),
        SIGNAL_LIST_KEY,
        key_prefix="L_SIGNAL",
        now=datetime(2026, 8, 26, 11, 30, tzinfo=timezone.utc),
    )
    assert report["status"] == "PASS"
    assert report["symbol"] == "US30"
    assert report["timeframe"] == "H1"
    assert report["strategy"] == "combo"
    assert report["signals"] == 1
    assert report["expired_by_valid_until"] == 0


def test_signal_audit_rejects_client_order_id_disagreeing_with_bartime():
    report = audit_signal_pair(
        FakeSignalRedis({SIGNAL_STAMP: signal_fields(clientOrderId="wrong")}),
        SIGNAL_LIST_KEY,
        key_prefix="L_SIGNAL",
    )
    assert report["status"] == "FAIL"
    assert any("clientOrderId does not match" in error for error in report["errors"])


def test_signal_audit_accepts_a_market_order_without_stop_price():
    """ma_cross/ema_cross are MARKET orders -- no stopPrice expected."""
    list_key = "L_SIGNAL_US30_M30_MA_CROSS"
    fields = signal_fields(
        timeframe="M30", strategy="ma_cross", orderType="MARKET",
        clientOrderId="ma_cross:M30:US30:20260826T100000Z",
    )
    del fields["stopPrice"]

    report = audit_signal_pair(
        FakeSignalRedis({SIGNAL_STAMP: fields}, list_key=list_key),
        list_key,
        key_prefix="L_SIGNAL",
        now=datetime(2026, 8, 26, 11, 30, tzinfo=timezone.utc),
    )

    assert report["status"] == "PASS"


def test_signal_audit_rejects_a_stop_order_missing_stop_price():
    fields = signal_fields()
    del fields["stopPrice"]

    report = audit_signal_pair(
        FakeSignalRedis({SIGNAL_STAMP: fields}),
        SIGNAL_LIST_KEY,
        key_prefix="L_SIGNAL",
    )
    assert report["status"] == "FAIL"
    assert any("stopPrice is missing" in error for error in report["errors"])


def test_signal_audit_validates_numeric_fields_when_present():
    report = audit_signal_pair(
        FakeSignalRedis({SIGNAL_STAMP: signal_fields(stopLoss="not-a-number")}),
        SIGNAL_LIST_KEY,
        key_prefix="L_SIGNAL",
    )
    assert report["status"] == "FAIL"
    assert any("stopLoss must be numeric" in error for error in report["errors"])


def test_expired_signal_is_reported_without_becoming_a_schema_failure():
    report = audit_signal_pair(
        FakeSignalRedis({SIGNAL_STAMP: signal_fields()}),
        SIGNAL_LIST_KEY,
        key_prefix="L_SIGNAL",
        now=datetime(2026, 8, 26, 13, tzinfo=timezone.utc),
    )
    assert report["status"] == "PASS"
    assert report["expired_by_valid_until"] == 1


def test_signal_audit_rejects_a_hash_with_no_ttl():
    report = audit_signal_pair(
        FakeSignalRedis(
            {SIGNAL_STAMP: signal_fields()},
            ttls={f"{SIGNAL_LIST_KEY}:{SIGNAL_STAMP}": -1},
        ),
        SIGNAL_LIST_KEY,
        key_prefix="L_SIGNAL",
    )
    assert report["status"] == "FAIL"
    assert any("no TTL" in error for error in report["errors"])


def ohlcv_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "bartime": pd.to_datetime(
                ["2026-08-26T10:00:00Z", "2026-08-26T11:00:00Z"], utc=True
            ),
            "open": [100.0, 102.0],
            "high": [103.0, 105.0],
            "low": [99.0, 101.0],
            "close": [102.0, 104.0],
            "volume": [10.0, 12.0],
        }
    )


def test_redis_sql_audit_detects_timestamp_and_ohlcv_differences():
    sql_frame = ohlcv_frame()
    exact = compare_sql_redis_frames(sql_frame, ohlcv_frame())
    assert exact["status"] == "PASS"
    assert exact["overlap_rows"] == 2

    changed = ohlcv_frame()
    changed.loc[0, "close"] = 102.5
    mismatch = compare_sql_redis_frames(sql_frame, changed)
    assert mismatch["status"] == "FAIL"
    assert mismatch["ohlcv_mismatches"]["close"] == 1
    assert mismatch["total_indicator_mismatches"] > 0

    missing_bar = compare_sql_redis_frames(sql_frame, ohlcv_frame().iloc[:1])
    assert missing_bar["status"] == "FAIL"
    assert missing_bar["missing_in_redis"] == 1


def test_audit_indicators_call_the_neutral_core_indicator_set():
    bars = pd.concat([ohlcv_frame()] * 25, ignore_index=True)
    bars["bartime"] = pd.date_range("2026-01-01", periods=len(bars), freq="30min")
    output = add_audit_indicators(bars)
    assert {"ma20", "macd_hist", "atr"}.issubset(output)
    assert output["ma20"].iloc[:19].isna().all()
    assert output["ma20"].iloc[19:].notna().all()


def test_explicit_redis_sql_scope_is_not_tied_to_strategy_configuration():
    pairs = select_pairs(
        object(),
        key_prefix="L_CANDLE",
        symbols=["BTCUSD", "HK50"],
        timeframes=["M30", "H4"],
        scan_all=False,
    )
    assert pairs == [
        ("BTCUSD", "H4"),
        ("BTCUSD", "M30"),
        ("HK50", "H4"),
        ("HK50", "M30"),
    ]


def test_redis_window_is_read_from_sql_timestamps_only():
    client = FakeSnapshotRedis(
        {CANDLE_STAMP: candle_fields(), NEXT_STAMP: candle_fields(stamp=NEXT_STAMP)}
    )
    frame, issues = read_redis_for_sql_window(
        client,
        symbol="US30",
        timeframe="H1",
        sql_frame=ohlcv_frame(),
        key_prefix="L_CANDLE",
    )
    assert len(frame) == 2
    assert issues == []
    assert frame["bartime"].tolist() == ohlcv_frame()["bartime"].dt.tz_localize(None).tolist()


def test_redis_window_reports_a_sql_timestamp_missing_from_the_pair_index():
    client = FakeSnapshotRedis({CANDLE_STAMP: candle_fields()})
    frame, issues = read_redis_for_sql_window(
        client,
        symbol="US30",
        timeframe="H1",
        sql_frame=ohlcv_frame(),
        key_prefix="L_CANDLE",
    )
    assert len(frame) == 1
    assert len(issues) == 1
    assert issues[0]["bartime"] == "2026-08-26T11:00:00Z"
    assert issues[0]["redis_value"] == "missing from the pair list"


def test_pair_audit_reads_sql_before_redis(monkeypatch):
    calls: list[str] = []

    def load_sql(_symbol, _timeframe, _n_bars):
        calls.append("sql")
        return ohlcv_frame()

    def load_redis(_client, **_kwargs):
        calls.append("redis")
        return ohlcv_frame(), []

    monkeypatch.setattr(redis_sql_audit.db_connector, "load", load_sql)
    monkeypatch.setattr(redis_sql_audit, "read_redis_for_sql_window", load_redis)

    report = audit_pair(
        object(),
        symbol="US30",
        timeframe="H1",
        n_bars=2,
        key_prefix="L_CANDLE",
    )
    assert calls == ["sql", "redis"]
    assert report["status"] == "PASS"


def test_dashboard_runs_full_instant_audits_and_leaves_event_optional(monkeypatch):
    sql_report = {
        "status": "PASS",
        "summary": {
            "pairs": 165,
            "sql_candles": 16500,
            "redis_candles": 16500,
            "missing_in_redis": 0,
            "ohlcv_mismatches": 0,
            "indicator_mismatches": 0,
        },
    }
    commands: list[tuple[str, list[str]]] = []

    monkeypatch.setattr(audit_dashboard, "run_audit", lambda **_kwargs: sql_report)

    def run_cli(script, arguments, *, timeout):
        del timeout
        commands.append((script, arguments))
        return {"status": "PASS", "summary": "status=PASS"}

    monkeypatch.setattr(audit_dashboard, "_run_cli", run_cli)
    result = audit_dashboard.run_dashboard()

    assert commands == [
        ("redis_snapshot_audit.py", ["--all"]),
        ("redis_signal_audit.py", ["--all"]),
    ]
    assert result["tools"][-1]["status"] == "SKIP"
    assert result["sql_redis"] is sql_report


def test_redis_sql_cli_uses_full_scope_when_no_filters_are_given(monkeypatch):
    received: dict = {}
    report = {
        "status": "PASS",
        "summary": {
            "pairs": 0,
            "sql_candles": 0,
            "redis_candles": 0,
            "missing_in_redis": 0,
            "ohlcv_mismatches": 0,
            "indicator_mismatches": 0,
        },
        "comparisons": [],
    }

    def run_audit(**kwargs):
        received.update(kwargs)
        return report

    monkeypatch.setattr(redis_sql_audit, "run_audit", run_audit)
    assert redis_sql_audit.main([]) == 0
    assert received["scan_all"] is True
