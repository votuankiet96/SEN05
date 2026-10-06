"""read_candles_from_redis trên list newest-first như Core AEN ghi (LPUSH) -- hermetic."""

from __future__ import annotations

import pandas as pd

from order_gateway.src.redis_io.candle_reader import (
    candle_key,
    pair_list_key,
    read_candles_from_redis,
)


class _FakePipeline:
    def __init__(self, hashes: dict[str, dict[str, str]]) -> None:
        self._hashes = hashes
        self._calls: list[tuple[str, tuple[str, ...]]] = []

    def hmget(self, key: str, *fields: str) -> None:
        self._calls.append((key, fields))

    def execute(self) -> list[list[str | None]]:
        return [[self._hashes.get(key, {}).get(f) for f in fields] for key, fields in self._calls]


class _FakeRedis:
    def __init__(self, lists: dict[str, list[str]], hashes: dict[str, dict[str, str]]) -> None:
        self._lists = lists
        self._hashes = hashes

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self._lists.get(key, [])
        n = len(items)
        start = max(start + n, 0) if start < 0 else start
        end = end + n if end < 0 else min(end, n - 1)
        return items[start : end + 1] if start <= end else []

    def pipeline(self, **_: object) -> _FakePipeline:
        return _FakePipeline(self._hashes)


def _newest_first_redis(n: int) -> _FakeRedis:
    list_key = pair_list_key("L_CANDLE", "GER40", "M30")
    stamps = [
        (pd.Timestamp("2026-09-28 00:00:00") + pd.Timedelta(minutes=30 * i)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        for i in range(n)
    ][::-1]
    hashes = {
        candle_key(list_key, s): {
            "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": "10",
        }
        for s in stamps
    }
    return _FakeRedis({list_key: stamps}, hashes)


def test_reads_newest_bars_ascending_from_newest_first_list():
    frame = read_candles_from_redis(
        _newest_first_redis(10), symbol="GER40", timeframe="M30",
        key_prefix="L_CANDLE", snapshot_bars=3,
    )
    assert list(frame["bartime"].dt.strftime("%H:%M")) == ["03:30", "04:00", "04:30"]


def test_shorter_list_yields_all_bars():
    frame = read_candles_from_redis(
        _newest_first_redis(2), symbol="GER40", timeframe="M30",
        key_prefix="L_CANDLE", snapshot_bars=500,
    )
    assert len(frame) == 2


def test_duplicate_stamp_is_dropped_not_raised(caplog):
    list_key = pair_list_key("L_CANDLE", "GER40", "M30")
    stamps = ["2026-09-28 01:00:00", "2026-09-28 00:30:00", "2026-09-28 00:30:00", "2026-09-28 00:00:00"]
    hashes = {
        candle_key(list_key, s): {"open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": "10"}
        for s in stamps
    }
    redis_client = _FakeRedis({list_key: stamps}, hashes)

    with caplog.at_level("WARNING"):
        frame = read_candles_from_redis(
            redis_client, symbol="GER40", timeframe="M30",
            key_prefix="L_CANDLE", snapshot_bars=500,
        )

    assert list(frame["bartime"].dt.strftime("%H:%M")) == ["00:00", "00:30", "01:00"]
    assert "event=candle_stamp_duplicate" in caplog.text
    assert "2026-09-28 00:30:00" in caplog.text
