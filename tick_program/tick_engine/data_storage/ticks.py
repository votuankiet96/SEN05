"""Tick models, price conversion and historical tick decoding."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from tick_engine.data_storage.symbols import RemoteSymbol, TargetSymbol

CTRADER_PRICE_SCALE = Decimal("100000")
MAX_TICK_REQUEST_MS = 7 * 24 * 60 * 60 * 1000
UTC = timezone.utc


def price_from_raw(raw_price: int | None, digits: int | None = None) -> Decimal | None:
    if raw_price is None:
        return None
    price = Decimal(int(raw_price)) / CTRADER_PRICE_SCALE
    if digits is not None and digits >= 0:
        quantum = Decimal(1).scaleb(-digits)
        price = price.quantize(quantum)
    return price


def utc_from_millis(timestamp_ms: int) -> datetime:
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=UTC)


def millis_from_utc(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.astimezone(UTC).timestamp() * 1000)


def _decimal_to_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _decimal_from_text(value: str | None) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(value)


def is_valid_raw_price(raw_price: int | None) -> bool:
    return raw_price is not None and int(raw_price) > 0


def build_event_hash(
    local_symbol: str,
    ctrader_symbol_id: int,
    source_timestamp_ms: int,
    bid_raw: int | None,
    ask_raw: int | None,
    quote_type: str,
    source_mode: str,
) -> bytes:
    _ = source_mode
    payload = "|".join(
        [
            local_symbol.upper(),
            str(int(ctrader_symbol_id)),
            str(int(source_timestamp_ms)),
            "" if bid_raw is None else str(int(bid_raw)),
            "" if ask_raw is None else str(int(ask_raw)),
            quote_type.upper(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).digest()


@dataclass(frozen=True)
class TickRecord:
    symbol_id: int
    local_symbol: str
    ctrader_symbol_id: int
    ctrader_symbol_name: str
    tick_time_utc: datetime
    source_timestamp_ms: int
    bid_raw: int | None
    ask_raw: int | None
    bid: Decimal | None
    ask: Decimal | None
    bid_updated: bool
    ask_updated: bool
    quote_type: str
    source_mode: str
    session_close_raw: int | None = None
    session_close: Decimal | None = None
    is_technical_event: bool = False
    received_at_utc: datetime | None = None
    ingest_run_id: str | None = None
    event_hash: bytes | None = None

    def __post_init__(self) -> None:
        if self.received_at_utc is None:
            object.__setattr__(self, "received_at_utc", datetime.now(UTC))
        if self.tick_time_utc.tzinfo is None:
            object.__setattr__(self, "tick_time_utc", self.tick_time_utc.replace(tzinfo=UTC))
        if self.received_at_utc and self.received_at_utc.tzinfo is None:
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
                    self.quote_type,
                    self.source_mode,
                ),
            )

    @classmethod
    def from_historical_tick(
        cls,
        target: TargetSymbol,
        remote: RemoteSymbol,
        quote_type: str,
        source_timestamp_ms: int,
        raw_price: int,
        received_at_utc: datetime | None = None,
        ingest_run_id: str | None = None,
    ) -> "TickRecord":
        quote_type = quote_type.upper()
        if not is_valid_raw_price(raw_price):
            raise ValueError(f"invalid {quote_type} historical tick price: {raw_price!r}")
        bid_raw = int(raw_price) if quote_type == "BID" else None
        ask_raw = int(raw_price) if quote_type == "ASK" else None
        return cls(
            symbol_id=target.symbol_id,
            local_symbol=target.local_symbol,
            ctrader_symbol_id=remote.ctrader_symbol_id,
            ctrader_symbol_name=remote.symbol_name,
            tick_time_utc=utc_from_millis(source_timestamp_ms),
            source_timestamp_ms=int(source_timestamp_ms),
            bid_raw=bid_raw,
            ask_raw=ask_raw,
            bid=price_from_raw(bid_raw, remote.digits),
            ask=price_from_raw(ask_raw, remote.digits),
            bid_updated=bid_raw is not None,
            ask_updated=ask_raw is not None,
            quote_type=quote_type,
            source_mode="HISTORICAL",
            received_at_utc=received_at_utc or datetime.now(UTC),
            ingest_run_id=ingest_run_id,
        )

    @classmethod
    def from_historical_quote(
        cls,
        target: TargetSymbol,
        remote: RemoteSymbol,
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
            bid=price_from_raw(int(bid_raw), remote.digits),
            ask=price_from_raw(int(ask_raw), remote.digits),
            bid_updated=bool(bid_updated),
            ask_updated=bool(ask_updated),
            quote_type="QUOTE",
            source_mode="HISTORICAL",
            received_at_utc=received_at_utc or datetime.now(UTC),
            ingest_run_id=ingest_run_id,
        )

    def to_db_params(
        self,
        *,
        include_ctrader_symbol_id: bool = False,
        insert_shape: str = "slim",
    ) -> tuple[Any, ...]:
        base = (
            self.symbol_id,
            self.tick_time_utc.astimezone(UTC).replace(tzinfo=None),
            self.bid,
            self.ask,
            self.received_at_utc.astimezone(UTC).replace(tzinfo=None),
            self.event_hash,
        )
        if insert_shape == "source_mode":
            return (
                self.symbol_id,
                self.tick_time_utc.astimezone(UTC).replace(tzinfo=None),
                self.bid,
                self.ask,
                self.received_at_utc.astimezone(UTC).replace(tzinfo=None),
                self.source_mode,
                self.event_hash,
            )
        if insert_shape == "slim" and not include_ctrader_symbol_id:
            return base
        if insert_shape == "legacy_id" or include_ctrader_symbol_id:
            return (
                self.symbol_id,
                int(self.ctrader_symbol_id),
                self.tick_time_utc.astimezone(UTC).replace(tzinfo=None),
                self.bid,
                self.ask,
                self.received_at_utc.astimezone(UTC).replace(tzinfo=None),
                self.source_mode,
                self.event_hash,
            )
        if insert_shape == "full_legacy":
            return (
                self.symbol_id,
                int(self.ctrader_symbol_id),
                self.ctrader_symbol_name,
                self.tick_time_utc.astimezone(UTC).replace(tzinfo=None),
                int(self.source_timestamp_ms),
                self.bid_raw,
                self.ask_raw,
                self.bid,
                self.ask,
                bool(self.bid_updated),
                bool(self.ask_updated),
                self.quote_type,
                self.source_mode,
                self.session_close_raw,
                self.session_close,
                bool(self.is_technical_event),
                self.received_at_utc.astimezone(UTC).replace(tzinfo=None),
                self.ingest_run_id,
                self.event_hash,
            )
        return base

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "symbol_id": self.symbol_id,
            "local_symbol": self.local_symbol,
            "ctrader_symbol_id": self.ctrader_symbol_id,
            "ctrader_symbol_name": self.ctrader_symbol_name,
            "tick_time_utc": self.tick_time_utc.astimezone(UTC).isoformat(),
            "source_timestamp_ms": self.source_timestamp_ms,
            "bid_raw": self.bid_raw,
            "ask_raw": self.ask_raw,
            "bid": _decimal_to_text(self.bid),
            "ask": _decimal_to_text(self.ask),
            "bid_updated": self.bid_updated,
            "ask_updated": self.ask_updated,
            "quote_type": self.quote_type,
            "source_mode": self.source_mode,
            "session_close_raw": self.session_close_raw,
            "session_close": _decimal_to_text(self.session_close),
            "is_technical_event": self.is_technical_event,
            "received_at_utc": self.received_at_utc.astimezone(UTC).isoformat()
            if self.received_at_utc
            else None,
            "ingest_run_id": self.ingest_run_id,
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
            bid_raw=data.get("bid_raw"),
            ask_raw=data.get("ask_raw"),
            bid=_decimal_from_text(data.get("bid")),
            ask=_decimal_from_text(data.get("ask")),
            bid_updated=bool(data["bid_updated"]),
            ask_updated=bool(data["ask_updated"]),
            quote_type=data["quote_type"],
            source_mode=data["source_mode"],
            session_close_raw=data.get("session_close_raw"),
            session_close=_decimal_from_text(data.get("session_close")),
            is_technical_event=bool(data.get("is_technical_event", False)),
            received_at_utc=datetime.fromisoformat(data["received_at_utc"]).astimezone(UTC)
            if data.get("received_at_utc")
            else None,
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
    """Decode cTrader historical tick timestamp and price deltas."""
    decoded: list[DecodedHistoricalTick] = []
    previous_timestamp_ms: int | None = None
    previous_raw_price: int | None = None
    quote_type = quote_type.upper()

    for index, raw_tick in enumerate(raw_ticks):
        timestamp_value = _get_tick_timestamp(raw_tick)
        price_value = _get_tick_price(raw_tick)
        if index == 0:
            timestamp_ms = timestamp_value
            raw_price = price_value
        elif newest_first:
            timestamp_ms = int(previous_timestamp_ms) - abs(timestamp_value)
            raw_price = int(previous_raw_price) + price_value
        else:
            timestamp_ms = int(previous_timestamp_ms) + abs(timestamp_value)
            raw_price = int(previous_raw_price) - price_value

        if is_valid_raw_price(raw_price):
            decoded.append(
                DecodedHistoricalTick(
                    timestamp_ms=timestamp_ms,
                    raw_price=raw_price,
                    quote_type=quote_type,
                )
            )
        previous_timestamp_ms = timestamp_ms
        previous_raw_price = raw_price

    return decoded
