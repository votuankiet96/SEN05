"""Historical tick helpers for cTrader Open API."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

MAX_TICK_REQUEST_MS = 7 * 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class DecodedHistoricalTick:
    timestamp_ms: int
    raw_price: int
    quote_type: str


def iter_tick_windows(
    from_timestamp_ms: int,
    to_timestamp_ms: int,
    max_window_ms: int = MAX_TICK_REQUEST_MS,
) -> Iterable[tuple[int, int]]:
    """Yield API windows whose span never exceeds cTrader's one-week limit."""
    start = int(from_timestamp_ms)
    end = int(to_timestamp_ms)
    if start > end:
        raise ValueError("from_timestamp_ms must be <= to_timestamp_ms")
    if max_window_ms <= 0:
        raise ValueError("max_window_ms must be positive")

    cursor = start
    while cursor <= end:
        window_end = min(cursor + max_window_ms, end)
        yield cursor, window_end
        cursor = window_end + 1


def _get_tick_timestamp(raw_tick: Any) -> int:
    return int(getattr(raw_tick, "timestamp"))


def _get_tick_price(raw_tick: Any) -> int:
    return int(getattr(raw_tick, "tick"))


def decode_delta_ticks(
    raw_ticks: Iterable[Any],
    quote_type: str,
    newest_first: bool = True,
) -> list[DecodedHistoricalTick]:
    """
    Decode cTrader historical tick timestamps.

    cTrader returns the first tick timestamp as an absolute millisecond value.
    Subsequent timestamps are deltas to the previous item. Historical tick
    arrays are documented as newest-first, so positive deltas move backward.
    """
    decoded: list[DecodedHistoricalTick] = []
    previous_timestamp_ms: int | None = None
    quote_type = quote_type.upper()

    for index, raw_tick in enumerate(raw_ticks):
        timestamp_value = _get_tick_timestamp(raw_tick)
        if index == 0:
            timestamp_ms = timestamp_value
        elif newest_first:
            timestamp_ms = int(previous_timestamp_ms) - abs(timestamp_value)
        else:
            timestamp_ms = int(previous_timestamp_ms) + abs(timestamp_value)

        decoded.append(
            DecodedHistoricalTick(
                timestamp_ms=timestamp_ms,
                raw_price=_get_tick_price(raw_tick),
                quote_type=quote_type,
            )
        )
        previous_timestamp_ms = timestamp_ms

    return decoded
