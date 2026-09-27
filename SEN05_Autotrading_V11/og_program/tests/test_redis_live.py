"""Focused tests for the OG signal path without a real Redis server."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from og_signal import live_worker, redis_listener, signal_publisher
from og_signal.redis_client import (
    normalize_app_config,
    pair_list_key,
    parse_pair_key,
    read_candles_from_redis,
)
from og_signal.signal_publisher import build_signal, publish_signal

# DP's stamps for the two bars in candle_rows(). The stamp naming the HASH is
# the only timestamp there is -- the HASH itself carries no bartime field.
BAR_STAMPS = ["2026-08-26 09:00:00", "2026-08-26 10:00:00"]
PAIR_KEY = "L_CANDLE_US30_H1"


class FakePipeline:
    """Mimics redis-py's non-transactional pipeline for HMGET batching."""

    NUMERIC_FIELDS = ("open", "high", "low", "close", "volume")

    def __init__(self, hashes: dict[str, list | None]):
        self.hashes = hashes
        self.queued: list[str] = []

    def hmget(self, key, *fields):
        # DP's HASH also holds timestamp/time_update (the current contract's
        # only other fields; datetime/source are gone). Asking for either
        # would feed text into the float coercion, so the reader must
        # request exactly the five numeric fields.
        assert tuple(fields) == self.NUMERIC_FIELDS, fields
        self.queued.append(key)

    def execute(self):
        return [self.hashes.get(key) for key in self.queued]


class FakeCandleRedis:
    """Mimics DP's layout: one pair LIST of stamps plus one HASH per bar."""

    def __init__(self, rows: list[dict], *, stamps: list[str] | None = None,
                 drop_hashes: tuple[str, ...] = ()):
        self.stamps = list(stamps if stamps is not None else BAR_STAMPS[: len(rows)])
        self.hashes: dict[str, list | None] = {}
        for stamp, row in zip(self.stamps, rows, strict=True):
            # The bar key is exactly the pair key plus ":" plus the stamp.
            key = f"{PAIR_KEY}:{stamp}"
            self.hashes[key] = (
                None if stamp in drop_hashes
                else [row["open"], row["high"], row["low"], row["close"], row["volume"]]
            )

    def lrange(self, key, start, _stop):
        # SYMBOL before TIMEFRAME: a swapped pair would silently read nothing.
        assert key == PAIR_KEY, key
        return self.stamps[start:] if start < 0 else self.stamps

    def pipeline(self, transaction=True):
        assert transaction is False
        return FakePipeline(self.hashes)


class FakeOutputRedis:
    """Emulates exactly what signal_publisher._PUBLISH_SIGNAL_SCRIPT does on
    real Redis (EXISTS the hash -> if new, HSET+EXPIRE+RPUSH+LTRIM+PUBLISH)
    without needing a real server or a Lua interpreter.
    """

    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.lists: dict[str, list[str]] = {}
        self.ttls: dict[str, int] = {}
        self.eval_calls: list[dict] = []
        self.published: list[tuple[str, str]] = []

    def eval(self, _script, numkeys, *keys_and_args):
        hash_key, list_key = keys_and_args[:numkeys]
        (
            stamp,
            retention_seconds,
            max_list_entries,
            event_channel,
            event_message,
            *field_values,
        ) = keys_and_args[numkeys:]
        self.eval_calls.append({"hash_key": hash_key, "list_key": list_key, "stamp": stamp})
        if hash_key in self.hashes:
            return 0
        self.hashes[hash_key] = dict(zip(field_values[0::2], field_values[1::2], strict=True))
        self.ttls[hash_key] = int(retention_seconds)
        entries = self.lists.setdefault(list_key, [])
        entries.append(stamp)
        del entries[: max(0, len(entries) - int(max_list_entries))]
        self.published.append((event_channel, event_message))
        return 1


def candle_rows() -> list[dict]:
    return [
        {
            "open": 100,
            "high": 103,
            "low": 99,
            "close": 102,
            "volume": 10,
        },
        {
            "open": 102,
            "high": 105,
            "low": 101,
            "close": 104,
            "volume": 20,
        },
    ]


def live_config() -> dict:
    return {
        "redis": {
            "enabled": True,
            "input": {
                "db": 0,
                "key_prefix": "L_CANDLE",
                "event_channel": "dp:events:candles",
                "snapshot_bars": 500,
                "process_on_startup": True,
            },
            "output": {
                "db": 1,
                "key_prefix": "L_SIGNAL",
                "event_channel": "og:events:signals",
                "retention_seconds": 604800,
                "retention_max_entries": 500,
            },
        },
        "live": {
            "enabled_strategies": ["combo", "ma_cross"],
            "log_level": "INFO",
            "discord": {
                "enabled": False,
                "webhook_url": "",
                "username": "OG Signal",
                "timeout_seconds": 10,
                "retry_count": 2,
                "retry_delay_seconds": 2,
            },
            "signal_validity": {
                "combo": {"mode": "next_bar", "valid_bars": 1},
                "ma_cross": {
                    "mode": "seconds_after_bar_close",
                    "seconds": 180,
                },
            },
        },
    }


def test_minimal_redis_config_requires_operator_input_and_output_keys():
    """db/key_prefix/event_channel/snapshot_bars/process_on_startup (input)
    and db/key_prefix/event_channel (output, event_channel added 2026-09-18)
    moved out of code defaults into required config.yaml entries -- a
    redis.input/redis.output missing any of them must raise, not silently
    fall back."""
    base = {"redis": {"enabled": True, "host": "127.0.0.1", "password": ""}}

    with pytest.raises(KeyError, match="redis.input.db"):
        normalize_app_config(base)

    with pytest.raises(KeyError, match="redis.output.key_prefix"):
        normalize_app_config(
            {
                **base,
                "redis": {
                    **base["redis"],
                    "input": {
                        "db": 0,
                        "key_prefix": "L_CANDLE",
                        "event_channel": "dp:events:candles",
                        "snapshot_bars": 100,
                        "process_on_startup": True,
                    },
                    "output": {"db": 1},
                },
            }
        )

    with pytest.raises(KeyError, match="redis.output.event_channel"):
        normalize_app_config(
            {
                **base,
                "redis": {
                    **base["redis"],
                    "input": {
                        "db": 0,
                        "key_prefix": "L_CANDLE",
                        "event_channel": "dp:events:candles",
                        "snapshot_bars": 100,
                        "process_on_startup": True,
                    },
                    "output": {"db": 1, "key_prefix": "L_SIGNAL"},
                },
            }
        )


def test_full_operator_redis_config_keeps_code_defaults_for_the_rest():
    """Once the 8 required keys are supplied, the remaining technical knobs
    (reconnect/timeout tuning, reconcile cadence, retention) still come from
    code defaults -- those were never moved to config.yaml."""
    config = normalize_app_config(
        {
            "redis": {
                "enabled": True,
                "host": "127.0.0.1",
                "password": "",
                "input": {
                    "db": 0,
                    "key_prefix": "L_CANDLE",
                    "event_channel": "dp:events:candles",
                    "snapshot_bars": 100,
                    "process_on_startup": True,
                },
                "output": {
                    "db": 1,
                    "key_prefix": "L_SIGNAL",
                    "event_channel": "og:events:signals",
                },
            }
        }
    )

    assert config["redis"]["port"] == 6379
    assert config["redis"]["socket_timeout_seconds"] == 5
    assert config["redis"]["input"]["reconcile_interval_seconds"] == 1800
    assert config["redis"]["output"]["retention_seconds"] == 604800
    assert config["redis"]["output"]["retention_max_entries"] == 500


def test_redis_enabled_requires_an_explicit_boolean():
    config = live_config()

    assert live_worker.redis_enabled(config) is True

    config["redis"]["enabled"] = False
    assert live_worker.redis_enabled(config) is False

    config["redis"]["enabled"] = "false"
    with pytest.raises(TypeError, match="redis.enabled"):
        live_worker.redis_enabled(config)


def test_main_exits_cleanly_when_redis_is_disabled(monkeypatch):
    config = live_config()
    config["redis"]["enabled"] = False
    monkeypatch.setattr(live_worker, "load_config", lambda: config)
    monkeypatch.setattr(
        live_worker,
        "run_forever",
        lambda _config: pytest.fail("run_forever must not start when Redis is disabled"),
    )

    assert live_worker.main() == 0


def signal_runner(strategy, *, symbol, tf, bars):
    result = bars.copy()
    result["signal"] = 0
    result["entry_price"] = float("nan")
    result["sl_price"] = float("nan")
    result["tp_price"] = float("nan")
    result["risk_reward"] = float("nan")
    result["atr"] = 2.5
    result["signal_reason"] = ""
    result.loc[result.index[-1], ["signal", "entry_price", "sl_price", "tp_price", "risk_reward"]] = [
        1,
        105.5,
        100.5,
        110.5,
        1.0,
    ]
    result.loc[result.index[-1], "signal_reason"] = f"{strategy} buy"
    return result


def test_read_candles_returns_sorted_core_compatible_frame():
    client = FakeCandleRedis(
        list(reversed(candle_rows())), stamps=list(reversed(BAR_STAMPS))
    )

    frame = read_candles_from_redis(
        client,
        symbol="US30",
        timeframe="H1",
        key_prefix="L_CANDLE",
        snapshot_bars=500,
    )

    assert frame["bartime"].is_monotonic_increasing
    assert frame["bartime"].dt.tz is None
    assert list(frame.columns) == ["bartime", "open", "high", "low", "close", "volume"]
    assert list(frame["bartime"]) == [
        pd.Timestamp("2026-08-26 09:00:00"),
        pd.Timestamp("2026-08-26 10:00:00"),
    ]


def test_pair_key_puts_symbol_before_timeframe():
    """DP's key is L_CANDLE_{SYMBOL}_{TIMEFRAME}. A swapped pair reads an
    empty list rather than failing, so the ordering is locked here on both
    sides."""
    assert pair_list_key("L_CANDLE", "US30", "H1") == "L_CANDLE_US30_H1"
    assert parse_pair_key("L_CANDLE_US30_H1", "L_CANDLE") == ("US30", "H1")
    # A pair LIST key has zero colons; a per-bar HASH key is that same key
    # plus ":<stamp>" and the stamp itself now has colons too, so a bar key
    # can carry several -- ANY colon means "this is a bar, not a pair" (the
    # opposite rule from the short-lived CANDLE: layout).
    assert parse_pair_key("L_CANDLE_US30_H1:2026-08-26 10:00:00", "L_CANDLE") is None
    assert parse_pair_key("L_CANDLE_US30", "L_CANDLE") is None
    assert parse_pair_key("OTHER_US30_H1", "L_CANDLE") is None


def test_read_candles_is_keyword_only_for_symbol_and_timeframe():
    """Positional (timeframe, symbol) was the old signature; keeping it callable
    would silently swap the pair instead of failing."""
    with pytest.raises(TypeError):
        read_candles_from_redis(FakeCandleRedis(candle_rows()), "H1", "US30")


def test_read_candles_allows_a_null_volume_row():
    """DP intentionally writes volume: null when a provider has none for a bar.

    volume is never read by core_python's indicators/strategies/levels, so a
    null there must not reject the whole snapshot the way a
    genuinely non-finite open/high/low/close does.
    """
    rows = candle_rows()
    rows[1]["volume"] = None

    frame = read_candles_from_redis(
        FakeCandleRedis(rows),
        symbol="US30",
        timeframe="H1",
        key_prefix="L_CANDLE",
        snapshot_bars=500,
    )

    assert len(frame) == 2
    assert pd.isna(frame.loc[1, "volume"])


def test_read_candles_allows_the_literal_string_null_volume():
    """DP writes volume as the literal text "null" (not a JSON null) when the
    provider has none -- float("null") would raise, so it must coerce to NaN."""
    rows = candle_rows()
    rows[1]["volume"] = "null"

    frame = read_candles_from_redis(
        FakeCandleRedis(rows),
        symbol="US30",
        timeframe="H1",
        key_prefix="L_CANDLE",
        snapshot_bars=500,
    )

    assert len(frame) == 2
    assert pd.isna(frame.loc[1, "volume"])
    assert frame.loc[1, "close"] == 104


def test_a_stamp_that_is_not_dp_format_is_rejected(caplog):
    """The stamp is the only timestamp, so anything that is not DP's exact
    fixed-width UTC format must fail loudly instead of becoming NaT quietly.

    Both previous contracts' formats (underscore date/time with dashes, and
    with no separators at all) are the likeliest mistakes, so that is what
    this feeds in."""
    client = FakeCandleRedis(
        candle_rows(), stamps=["2026-08-26_09-00-00", BAR_STAMPS[1]]
    )

    with pytest.raises(ValueError, match="contains invalid values"):
        read_candles_from_redis(
            client,
            symbol="US30",
            timeframe="H1",
            key_prefix="L_CANDLE",
            snapshot_bars=500,
        )

    assert "rejected: 1/2 rows invalid" in caplog.text


def test_read_candles_skips_a_stamp_whose_hash_was_evicted(caplog):
    """A read racing DP's eviction of the oldest bar sees a stamp in the pair
    list with no hash behind it -- drop that bar, keep the rest of the window."""
    frame = read_candles_from_redis(
        FakeCandleRedis(candle_rows(), drop_hashes=(BAR_STAMPS[0],)),
        symbol="US30",
        timeframe="H1",
        key_prefix="L_CANDLE",
        snapshot_bars=500,
    )

    assert len(frame) == 1
    assert frame.loc[0, "bartime"] == pd.Timestamp("2026-08-26 10:00:00")
    assert "have no hash" in caplog.text


def test_read_candles_still_rejects_non_finite_ohlc(caplog):
    """A non-finite open/high/low/close must still reject the whole snapshot,
    and the raw offending row must be logged (evidence for the next
    occurrence, per DP's request)."""
    rows = candle_rows()
    rows[1]["close"] = float("nan")

    with pytest.raises(ValueError, match="contains invalid values"):
        read_candles_from_redis(
            FakeCandleRedis(rows),
            symbol="US30",
            timeframe="H1",
            key_prefix="L_CANDLE",
            snapshot_bars=500,
        )

    assert "rejected: 1/2 rows invalid" in caplog.text


def test_combo_and_ma_cross_have_strategy_specific_windows():
    bartime = pd.Timestamp("2026-08-26 10:00:00", tz="UTC")

    combo_window = live_worker.signal_window(
        "combo", "H1", bartime, {"mode": "next_bar", "valid_bars": 1}
    )
    ma_window = live_worker.signal_window(
        "ma_cross",
        "M30",
        bartime,
        {"mode": "seconds_after_bar_close", "seconds": 180},
    )

    assert combo_window == (
        pd.Timestamp("2026-08-26 11:00:00", tz="UTC"),
        pd.Timestamp("2026-08-26 12:00:00", tz="UTC"),
    )
    assert ma_window == (
        pd.Timestamp("2026-08-26 10:30:00", tz="UTC"),
        pd.Timestamp("2026-08-26 10:33:00", tz="UTC"),
    )


def test_processes_only_latest_signal_and_publishes_once():
    input_client = FakeCandleRedis(candle_rows())
    output_client = FakeOutputRedis()

    created = live_worker.process_candle_update(
        input_client,
        output_client,
        timeframe="H1",
        symbol="US30",
        config=live_config(),
        now=pd.Timestamp("2026-08-26 11:30:00", tz="UTC"),
        strategy_runner=signal_runner,
    )
    duplicate = live_worker.process_candle_update(
        input_client,
        output_client,
        timeframe="H1",
        symbol="US30",
        config=live_config(),
        now=pd.Timestamp("2026-08-26 11:31:00", tz="UTC"),
        strategy_runner=signal_runner,
    )

    assert created == 1
    assert duplicate == 0
    hash_key = "L_SIGNAL_US30_H1_COMBO:2026-08-26 10:00:00"
    assert list(output_client.hashes) == [hash_key]
    assert output_client.lists["L_SIGNAL_US30_H1_COMBO"] == ["2026-08-26 10:00:00"]
    fields = output_client.hashes[hash_key]
    assert fields["tradeSide"] == "BUY"
    assert fields["orderType"] == "STOP"
    assert fields["stopPrice"] == "105.5"
    assert fields["expirationTimestamp"] == str(
        int(pd.Timestamp("2026-08-26 12:00:00", tz="UTC").timestamp())
    )
    assert "volume" not in fields
    assert "symbolId" not in fields
    assert output_client.ttls[hash_key] == 604800


def test_expired_signal_is_not_published():
    output_client = FakeOutputRedis()

    created = live_worker.process_candle_update(
        FakeCandleRedis(candle_rows()),
        output_client,
        timeframe="H1",
        symbol="US30",
        config=live_config(),
        now=pd.Timestamp("2026-08-26 12:00:00", tz="UTC"),
        strategy_runner=signal_runner,
    )

    assert created == 0
    assert output_client.hashes == {}


def test_publisher_publishes_exactly_once_per_signal():
    row = pd.Series(
        {
            "bartime": pd.Timestamp("2026-08-26 10:00:00", tz="UTC"),
            "signal": -1,
            "entry_price": 100,
            "sl_price": 105,
            "tp_price": 90,
            "risk_reward": 2,
            "atr": 5,
            "signal_reason": "sell",
        }
    )
    signal_id, stamp, payload = build_signal(
        strategy="combo",
        timeframe="H1",
        symbol="US30",
        row=row,
        valid_from=pd.Timestamp("2026-08-26 11:00:00", tz="UTC"),
        valid_until=pd.Timestamp("2026-08-26 12:00:00", tz="UTC"),
    )
    hash_fields = signal_publisher.to_hash_fields("combo", payload)
    client = FakeOutputRedis()
    publish_kwargs = {
        "key_prefix": "L_SIGNAL",
        "symbol": "US30",
        "timeframe": "H1",
        "strategy": "combo",
        "stamp": stamp,
        "hash_fields": hash_fields,
        "retention_seconds": 60,
        "max_list_entries": 500,
        "event_channel": "og:events:signals",
    }

    assert publish_signal(client, **publish_kwargs) is True
    assert publish_signal(client, **publish_kwargs) is False
    hash_key = "L_SIGNAL_US30_H1_COMBO:2026-08-26 10:00:00"
    assert client.ttls[hash_key] == 60
    assert client.hashes[hash_key]["clientOrderId"] == signal_id
    # Duplicate attempt did not append a 2nd entry to the LIST.
    assert client.lists["L_SIGNAL_US30_H1_COMBO"] == [stamp]
    # PUBLISH fires exactly once, only on the write that actually happened --
    # the duplicate attempt above must not announce a 2nd trigger for OF.
    assert client.published == [
        (
            "og:events:signals",
            json.dumps(
                {"symbol": "US30", "timeframe": "H1", "strategy": "combo", "stamp": stamp}
            ),
        )
    ]


def test_signal_payload_allows_order_levels_to_be_absent():
    row = pd.Series(
        {
            "bartime": pd.Timestamp("2026-08-26 10:00:00", tz="UTC"),
            "signal": 1,
            "entry_price": 105.5,
            "atr": 2.5,
            "signal_reason": "buy",
        }
    )

    _signal_id, _stamp, payload = build_signal(
        strategy="combo",
        timeframe="H1",
        symbol="US30",
        row=row,
        valid_from=pd.Timestamp("2026-08-26 11:00:00", tz="UTC"),
        valid_until=pd.Timestamp("2026-08-26 12:00:00", tz="UTC"),
    )

    assert payload["entry"] == 105.5
    assert payload["atr"] == 2.5
    assert {"sl", "tp", "risk_reward", "sl_dow", "ksl", "ktp"}.isdisjoint(payload)

    hash_fields = signal_publisher.to_hash_fields("combo", payload)
    assert {"stopLoss", "takeProfit", "ksl", "ktp"}.isdisjoint(hash_fields)


def test_to_hash_fields_market_order_has_no_stop_price():
    """ma_cross/ema_cross are MARKET orders -- no pending price to send."""
    row = pd.Series(
        {
            "bartime": pd.Timestamp("2026-08-26 10:00:00", tz="UTC"),
            "signal": 1,
            "entry_price": 105.5,
            "sl_price": 100.0,
            "tp_price": 110.0,
            "ksl": 1.618,
            "ktp": 2.618,
            "atr": 2.5,
            "signal_reason": "ma cross buy",
        }
    )
    _signal_id, _stamp, payload = build_signal(
        strategy="ma_cross",
        timeframe="M30",
        symbol="US30",
        row=row,
        valid_from=pd.Timestamp("2026-08-26 10:30:00", tz="UTC"),
        valid_until=pd.Timestamp("2026-08-26 10:33:00", tz="UTC"),
    )

    fields = signal_publisher.to_hash_fields("ma_cross", payload)

    assert fields["orderType"] == "MARKET"
    assert "stopPrice" not in fields
    assert fields["stopLoss"] == "100.0"
    assert fields["takeProfit"] == "110.0"
    # ksl/ktp let OF re-derive a relative stop/take-profit distance itself
    # (needed for ma_cross: the MARKET fill price differs from OG's
    # signal-time close, so the absolute stopLoss/takeProfit above are only
    # a reference, not necessarily what should be sent to cTrader).
    assert fields["ksl"] == "1.618"
    assert fields["ktp"] == "2.618"
    assert fields["symbol"] == "US30"
    assert fields["timeframe"] == "M30"
    assert fields["strategy"] == "ma_cross"
    assert fields["comment"] == "ma cross buy"
    assert "label" not in fields


def test_discord_is_sent_only_for_a_new_redis_signal(monkeypatch):
    config = live_config()
    config["live"]["discord"].update(
        {"enabled": True, "webhook_url": "https://discord.invalid/webhook"}
    )
    output_client = FakeOutputRedis()
    notifications = []

    def fake_send(payload, _discord_config):
        notifications.append(payload["signal_id"])
        return True, "sent"

    monkeypatch.setattr(live_worker, "send_discord_signal", fake_send)
    for minute in (30, 31):
        live_worker.process_candle_update(
            FakeCandleRedis(candle_rows()),
            output_client,
            timeframe="H1",
            symbol="US30",
            config=config,
            now=pd.Timestamp(f"2026-08-26 11:{minute}:00", tz="UTC"),
            strategy_runner=signal_runner,
        )

    assert notifications == ["combo:H1:US30:20260826T100000Z"]


def test_discord_message_and_webhook_payload(monkeypatch):
    payload = {
        "signal_id": "combo:H1:US30:20260826T100000Z",
        "strategy": "combo",
        "timeframe": "H1",
        "symbol": "US30",
        "bartime": "2026-08-26T10:00:00Z",
        "side": "BUY",
        "entry": 105.5,
        "sl": 100.5,
        "tp": 110.5,
        "risk_reward": 1.0,
        "atr": 2.5,
        "reason": "combo buy",
        "valid_until": "2026-08-26T12:00:00Z",
    }
    posted = []

    def fake_post(url, body, timeout_seconds):
        posted.append((url, body, timeout_seconds))
        return 204

    monkeypatch.setattr(signal_publisher, "_post_discord", fake_post)
    sent, detail = signal_publisher.send_discord_signal(
        payload,
        {
            "webhook_url": "https://discord.invalid/webhook",
            "username": "OG Signal",
            "timeout_seconds": 10,
            "retry_count": 0,
            "retry_delay_seconds": 0,
        },
    )

    assert (sent, detail) == (True, "sent")
    assert "**BUY | Combo | US30 H1**" in posted[0][1]["content"]
    assert posted[0][1]["username"] == "OG Signal"


class _FakePubSub:
    def __init__(self, messages):
        self.messages = list(messages)
        self.closed = False
        self.channel = None

    def subscribe(self, channel):
        self.channel = channel

    def get_message(self, **kwargs):
        return self.messages.pop(0) if self.messages else None

    def close(self):
        self.closed = True


def test_listener_reacts_to_a_candle_event():
    class StopListening(Exception):
        pass

    pubsub = _FakePubSub(
        [
            {"type": "subscribe"},
            {
                "type": "message",
                "channel": "dp:events:candles",
                "data": json.dumps(
                    {"symbol": "US30", "timeframe": "H1", "candles": [{"bartime": "x"}]}
                ),
            },
            None,
        ]
    )

    class FakeClient:
        def pubsub(self):
            return pubsub

    updates = []

    def on_update(timeframe, symbol):
        updates.append((timeframe, symbol))
        raise StopListening

    with pytest.raises(StopListening):
        redis_listener.listen_for_candle_events(
            FakeClient(),
            channel="dp:events:candles",
            on_update=on_update,
        )

    assert updates == [("H1", "US30")]
    assert pubsub.channel == "dp:events:candles"
    assert pubsub.closed is True


def test_listener_ignores_a_malformed_event_and_keeps_going():
    class StopListening(Exception):
        pass

    pubsub = _FakePubSub(
        [
            {"type": "subscribe"},
            {"type": "message", "channel": "dp:events:candles", "data": "not json"},
            {
                "type": "message",
                "channel": "dp:events:candles",
                "data": json.dumps({"symbol": "GOLD", "timeframe": "H4"}),
            },
        ]
    )

    class FakeClient:
        def pubsub(self):
            return pubsub

    updates = []

    def on_update(timeframe, symbol):
        updates.append((timeframe, symbol))
        raise StopListening

    with pytest.raises(StopListening):
        redis_listener.listen_for_candle_events(
            FakeClient(),
            channel="dp:events:candles",
            on_update=on_update,
        )

    assert updates == [("H4", "GOLD")]


def test_listener_calls_periodic_reconcile_without_any_message(monkeypatch):
    class StopListening(Exception):
        pass

    pubsub = _FakePubSub([{"type": "subscribe"}])

    class FakeClient:
        def pubsub(self):
            return pubsub

    clock = iter([0.0, 0.0, 5.0, 5.0])
    monkeypatch.setattr(redis_listener.time, "monotonic", lambda: next(clock))
    reconciled = []

    def on_reconcile():
        reconciled.append(True)
        raise StopListening

    with pytest.raises(StopListening):
        redis_listener.listen_for_candle_events(
            FakeClient(),
            channel="dp:events:candles",
            on_update=lambda *_: None,
            reconcile_interval_seconds=3,
            on_reconcile=on_reconcile,
        )

    assert reconciled == [True]
