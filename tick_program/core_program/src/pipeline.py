"""Tick model, historical decode, and BID/ASK merge/validation.

Owns the shape of a tick record end to end: price scaling, delta-tick
decoding from the cTrader wire format, merging BID/ASK streams into
two-sided quotes, and the outlier/crossed/spread guards applied before a
record is allowed into the batcher.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from statistics import median
from typing import Any

UTC = timezone.utc
CTRADER_PRICE_SCALE = Decimal("100000")
MAX_TICK_REQUEST_MS = 7 * 24 * 60 * 60 * 1000
QUOTE_OUTLIER_RATIO = Decimal("5")
QUOTE_MAX_SPREAD_BPS = Decimal("1000")


def price_from_raw(raw_price: int | None) -> Decimal | None:
    """Full-precision price from cTrader's raw relative-price integer.

    Deliberately does NOT round to a symbol's display `digits` (a UI/broker
    display convention, per ProtoOASymbol's own doc: "Number of price digits
    to be displayed" -- not a limit on the real precision cTrader's tick feed
    carries). Rounding here would silently discard genuine sub-pip price
    movement for any symbol whose digits is coarser than the /100000 scale,
    which is exactly the kind of fidelity loss this store is designed to
    avoid (see the Clustered Columnstore storage note in
    scripts/sql/tickdata_setup.sql for the same principle applied to raw
    tick retention).
    """
    if raw_price is None:
        return None
    return Decimal(int(raw_price)) / CTRADER_PRICE_SCALE


def utc_from_millis(timestamp_ms: int) -> datetime:
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=UTC)


def millis_from_utc(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.astimezone(UTC).timestamp() * 1000)


def is_valid_raw_price(raw_price: int | None) -> bool:
    return raw_price is not None and int(raw_price) > 0


def _decimal_to_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _decimal_from_text(value: str | None) -> Decimal | None:
    return None if value in (None, "") else Decimal(value)


def build_event_hash(
    local_symbol: str,
    ctrader_symbol_id: int,
    source_timestamp_ms: int,
    bid_raw: int | None,
    ask_raw: int | None,
) -> bytes:
    payload = "|".join(
        [
            local_symbol.upper(),
            str(int(ctrader_symbol_id)),
            str(int(source_timestamp_ms)),
            "" if bid_raw is None else str(int(bid_raw)),
            "" if ask_raw is None else str(int(ask_raw)),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).digest()


@dataclass(frozen=True)
class TickRecord:
    """One two-sided historical quote, ready for SQL insert or spool storage."""

    symbol_id: int
    local_symbol: str
    ctrader_symbol_id: int
    ctrader_symbol_name: str
    tick_time_utc: datetime
    source_timestamp_ms: int
    bid_raw: int
    ask_raw: int
    bid: Decimal
    ask: Decimal
    bid_updated: bool
    ask_updated: bool
    received_at_utc: datetime | None = None
    ingest_run_id: str | None = None
    event_hash: bytes | None = None

    def __post_init__(self) -> None:
        if self.received_at_utc is None:
            object.__setattr__(self, "received_at_utc", datetime.now(UTC))
        if self.tick_time_utc.tzinfo is None:
            object.__setattr__(self, "tick_time_utc", self.tick_time_utc.replace(tzinfo=UTC))
        if self.received_at_utc.tzinfo is None:
            object.__setattr__(self, "received_at_utc", self.received_at_utc.replace(tzinfo=UTC))
        if self.event_hash is None:
            object.__setattr__(
                self,
                "event_hash",
                build_event_hash(
                    self.local_symbol,
                    self.ctrader_symbol_id,
                    self.source_timestamp_ms,
                    self.bid_raw,
                    self.ask_raw,
                ),
            )

    @classmethod
    def from_historical_quote(
        cls,
        target: Any,
        remote: Any,
        source_timestamp_ms: int,
        bid_raw: int,
        ask_raw: int,
        *,
        bid_updated: bool,
        ask_updated: bool,
        received_at_utc: datetime | None = None,
        ingest_run_id: str | None = None,
    ) -> "TickRecord":
        if not is_valid_raw_price(bid_raw):
            raise ValueError(f"invalid historical quote bid price: {bid_raw!r}")
        if not is_valid_raw_price(ask_raw):
            raise ValueError(f"invalid historical quote ask price: {ask_raw!r}")
        return cls(
            symbol_id=target.symbol_id,
            local_symbol=target.local_symbol,
            ctrader_symbol_id=remote.ctrader_symbol_id,
            ctrader_symbol_name=remote.symbol_name,
            tick_time_utc=utc_from_millis(source_timestamp_ms),
            source_timestamp_ms=int(source_timestamp_ms),
            bid_raw=int(bid_raw),
            ask_raw=int(ask_raw),
            bid=price_from_raw(int(bid_raw)),
            ask=price_from_raw(int(ask_raw)),
            bid_updated=bool(bid_updated),
            ask_updated=bool(ask_updated),
            received_at_utc=received_at_utc or datetime.now(UTC),
            ingest_run_id=ingest_run_id,
        )

    def sql_dedup_key(self) -> tuple[datetime, Decimal | None, Decimal | None]:
        """(TickTimeUtc, Bid, Ask) in the same naive-UTC shape SQL Server's
        own dedup index compares on -- see sql_store.py::insert_ticks and
        its _DEDUP_COLUMNS."""
        return (self.tick_time_utc.astimezone(UTC).replace(tzinfo=None), self.bid, self.ask)

    def to_db_params(self) -> tuple[Any, ...]:
        """Params for the SQL slim insert shape: SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc.

        event_hash is NOT part of this (SQL Server no longer stores it --
        dedup there is enforced by a unique index on (TickTimeUtc, Bid, Ask)
        instead, see sql_store.py::insert_ticks). event_hash stays a
        TickRecord field purely for the cheap in-memory/spool dedup uses
        below (seen_hashes in merge_historical_quote_ticks, the SQLite
        spool's own unique key) -- those never touch SQL Server's schema.
        """
        return (
            self.symbol_id,
            self.tick_time_utc.astimezone(UTC).replace(tzinfo=None),
            self.bid,
            self.ask,
            self.received_at_utc.astimezone(UTC).replace(tzinfo=None),
        )

    def to_json_dict(self) -> dict[str, Any]:
        """Round-trip shape used by the SQLite spool; see from_json_dict."""
        d = self.__dict__
        return {
            **{k: d[k] for k in ("symbol_id", "local_symbol", "ctrader_symbol_id",
                                  "ctrader_symbol_name", "source_timestamp_ms",
                                  "bid_raw", "ask_raw", "bid_updated", "ask_updated",
                                  "ingest_run_id")},
            "tick_time_utc": self.tick_time_utc.astimezone(UTC).isoformat(),
            "bid": _decimal_to_text(self.bid),
            "ask": _decimal_to_text(self.ask),
            "received_at_utc": self.received_at_utc.astimezone(UTC).isoformat() if self.received_at_utc else None,
            "event_hash": self.event_hash.hex() if self.event_hash else None,
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> "TickRecord":
        return cls(
            symbol_id=int(data["symbol_id"]),
            local_symbol=data["local_symbol"],
            ctrader_symbol_id=int(data["ctrader_symbol_id"]),
            ctrader_symbol_name=data["ctrader_symbol_name"],
            tick_time_utc=datetime.fromisoformat(data["tick_time_utc"]).astimezone(UTC),
            source_timestamp_ms=int(data["source_timestamp_ms"]),
            bid_raw=int(data["bid_raw"]),
            ask_raw=int(data["ask_raw"]),
            bid=_decimal_from_text(data.get("bid")),
            ask=_decimal_from_text(data.get("ask")),
            bid_updated=bool(data["bid_updated"]),
            ask_updated=bool(data["ask_updated"]),
            received_at_utc=datetime.fromisoformat(data["received_at_utc"]).astimezone(UTC)
            if data.get("received_at_utc") else None,
            ingest_run_id=data.get("ingest_run_id"),
            event_hash=bytes.fromhex(data["event_hash"]) if data.get("event_hash") else None,
        )


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
    start, end = int(from_timestamp_ms), int(to_timestamp_ms)
    if start > end:
        raise ValueError("from_timestamp_ms must be <= to_timestamp_ms")
    if max_window_ms <= 0:
        raise ValueError("max_window_ms must be positive")
    cursor = start
    while cursor <= end:
        window_end = min(cursor + max_window_ms, end)
        yield cursor, window_end
        cursor = window_end + 1


def decode_delta_ticks(raw_ticks: Iterable[Any], quote_type: str) -> list[DecodedHistoricalTick]:
    """Decode cTrader historical tick timestamp/price deltas (newest-first wire format)."""
    decoded: list[DecodedHistoricalTick] = []
    previous_timestamp_ms: int | None = None
    previous_raw_price: int | None = None
    quote_type = quote_type.upper()

    for index, raw_tick in enumerate(raw_ticks):
        timestamp_value = int(getattr(raw_tick, "timestamp"))
        price_value = int(getattr(raw_tick, "tick"))
        if index == 0:
            timestamp_ms, raw_price = timestamp_value, price_value
        else:
            timestamp_ms = int(previous_timestamp_ms) - abs(timestamp_value)
            raw_price = int(previous_raw_price) + price_value

        if is_valid_raw_price(raw_price):
            decoded.append(DecodedHistoricalTick(timestamp_ms, raw_price, quote_type))
        previous_timestamp_ms, previous_raw_price = timestamp_ms, raw_price

    return decoded


@dataclass(frozen=True)
class QuoteMergeStats:
    bid_ticks: int
    ask_ticks: int
    dropped_bid_outliers: int
    dropped_ask_outliers: int
    dropped_unseeded: int
    dropped_stale_side: int
    dropped_crossed: int
    dropped_wide_spread: int
    dropped_duplicate_quote: int

    @property
    def dropped_total(self) -> int:
        return (
            self.dropped_bid_outliers
            + self.dropped_ask_outliers
            + self.dropped_unseeded
            + self.dropped_stale_side
            + self.dropped_crossed
            + self.dropped_wide_spread
            + self.dropped_duplicate_quote
        )


def _filter_side_outliers(ticks: list[DecodedHistoricalTick]) -> tuple[list[DecodedHistoricalTick], int]:
    if len(ticks) < 5:
        return ticks, 0
    raw_prices = [int(t.raw_price) for t in ticks if int(t.raw_price) > 0]
    if len(raw_prices) < 5:
        return ticks, 0
    median_raw = Decimal(int(median(raw_prices)))
    if median_raw <= 0:
        return ticks, 0
    lower, upper = median_raw / QUOTE_OUTLIER_RATIO, median_raw * QUOTE_OUTLIER_RATIO
    kept, dropped = [], 0
    for t in ticks:
        price = Decimal(int(t.raw_price))
        if price < lower or price > upper:
            dropped += 1
            continue
        kept.append(t)
    return kept, dropped


def _quote_spread_bps(bid: Decimal, ask: Decimal) -> Decimal | None:
    mid = (bid + ask) / Decimal("2")
    if mid <= 0:
        return None
    return ((ask - bid) / mid) * Decimal("10000")


def merge_historical_quote_ticks(
    target: Any,
    remote: Any,
    bid_ticks: list[DecodedHistoricalTick],
    ask_ticks: list[DecodedHistoricalTick],
    *,
    ingest_run_id: str,
    max_side_age_seconds: int = 900,
) -> tuple[list[TickRecord], QuoteMergeStats]:
    """Merge cTrader BID/ASK history streams into forward-filled two-sided quotes."""
    original_bid_count, original_ask_count = len(bid_ticks), len(ask_ticks)
    bid_ticks, dropped_bid_outliers = _filter_side_outliers(bid_ticks)
    ask_ticks, dropped_ask_outliers = _filter_side_outliers(ask_ticks)

    by_timestamp: dict[int, dict[str, list[int]]] = defaultdict(lambda: {"bid_raw": [], "ask_raw": []})
    for t in sorted(reversed(bid_ticks), key=lambda item: item.timestamp_ms):
        by_timestamp[int(t.timestamp_ms)]["bid_raw"].append(int(t.raw_price))
    for t in sorted(reversed(ask_ticks), key=lambda item: item.timestamp_ms):
        by_timestamp[int(t.timestamp_ms)]["ask_raw"].append(int(t.raw_price))

    records: list[TickRecord] = []
    last_bid_raw = last_ask_raw = None
    last_bid_ts = last_ask_ts = None
    dropped_unseeded = dropped_stale_side = dropped_crossed = 0
    dropped_wide_spread = dropped_duplicate_quote = 0
    seen_hashes: set[bytes] = set()
    max_side_age_ms = max(0, int(max_side_age_seconds)) * 1000

    for timestamp_ms in sorted(by_timestamp):
        sides = by_timestamp[timestamp_ms]
        bid_values, ask_values = sides["bid_raw"], sides["ask_raw"]
        for i in range(max(len(bid_values), len(ask_values))):
            bid_updated, ask_updated = i < len(bid_values), i < len(ask_values)
            if bid_updated:
                last_bid_raw, last_bid_ts = bid_values[i], timestamp_ms
            if ask_updated:
                last_ask_raw, last_ask_ts = ask_values[i], timestamp_ms
            if last_bid_raw is None or last_ask_raw is None:
                dropped_unseeded += 1
                continue
            if (
                last_bid_ts is None
                or last_ask_ts is None
                or timestamp_ms - last_bid_ts > max_side_age_ms
                or timestamp_ms - last_ask_ts > max_side_age_ms
            ):
                dropped_stale_side += 1
                continue

            record = TickRecord.from_historical_quote(
                target, remote, timestamp_ms, last_bid_raw, last_ask_raw,
                bid_updated=bid_updated, ask_updated=ask_updated, ingest_run_id=ingest_run_id,
            )
            if record.ask < record.bid:
                dropped_crossed += 1
                continue
            spread_bps = _quote_spread_bps(record.bid, record.ask)
            if spread_bps is not None and spread_bps > QUOTE_MAX_SPREAD_BPS:
                dropped_wide_spread += 1
                continue
            event_hash = bytes(record.event_hash or b"")
            if event_hash in seen_hashes:
                dropped_duplicate_quote += 1
                continue
            seen_hashes.add(event_hash)
            records.append(record)

    return records, QuoteMergeStats(
        bid_ticks=original_bid_count,
        ask_ticks=original_ask_count,
        dropped_bid_outliers=dropped_bid_outliers,
        dropped_ask_outliers=dropped_ask_outliers,
        dropped_unseeded=dropped_unseeded,
        dropped_stale_side=dropped_stale_side,
        dropped_crossed=dropped_crossed,
        dropped_wide_spread=dropped_wide_spread,
        dropped_duplicate_quote=dropped_duplicate_quote,
    )
