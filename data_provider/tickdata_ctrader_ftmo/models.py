"""Shared data models for the cTrader FTMO tick provider."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

CTRADER_PRICE_SCALE = Decimal("100000")
UTC = timezone.utc


def price_from_raw(raw_price: int | None, digits: int | None = None) -> Decimal | None:
    """Convert cTrader integer price to Decimal using the documented 100000 scale."""
    if raw_price is None:
        return None

    price = Decimal(int(raw_price)) / CTRADER_PRICE_SCALE
    if digits is not None and digits >= 0:
        quantum = Decimal(1).scaleb(-digits)
        price = price.quantize(quantum)
    return price


def utc_from_millis(timestamp_ms: int) -> datetime:
    """Return a UTC datetime from a millisecond timestamp."""
    return datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=UTC)


def millis_from_utc(value: datetime) -> int:
    """Return a millisecond timestamp for a datetime."""
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


def _get_optional_proto_field(message: Any, name: str) -> Any:
    try:
        if not message.HasField(name):
            return None
    except Exception:
        pass
    return getattr(message, name, None)


def build_event_hash(
    local_symbol: str,
    ctrader_symbol_id: int,
    source_timestamp_ms: int,
    bid_raw: int | None,
    ask_raw: int | None,
    quote_type: str,
    source_mode: str,
) -> bytes:
    """Stable idempotency key for duplicate live/history writes."""
    _ = source_mode  # SourceMode is stored separately; it should not split one market tick into two events.
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
class TargetSymbol:
    """One SEN05 symbol targeted for tick ingestion."""

    symbol_id: int
    local_symbol: str
    asset_type: str
    table: str

    @property
    def table_name(self) -> str:
        if "." in self.table:
            return self.table.rsplit(".", 1)[1]
        return self.local_symbol


@dataclass(frozen=True)
class RemoteSymbol:
    """cTrader symbol metadata discovered from a specific account."""

    ctrader_symbol_id: int
    symbol_name: str
    digits: int | None = None
    description: str | None = None
    enabled: bool | None = None
    base_asset_id: int | None = None
    quote_asset_id: int | None = None
    pip_position: int | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class SymbolMatch:
    """Match decision between a local symbol and an account-scoped cTrader symbol."""

    target: TargetSymbol
    remote: RemoteSymbol | None
    score: int
    status: str
    reason: str
    alternatives: tuple[RemoteSymbol, ...] = ()

    @property
    def is_usable(self) -> bool:
        return self.status == "MATCHED" and self.remote is not None


@dataclass(frozen=True)
class TickRecord:
    """A normalized tick row ready for SQL Server or local spool."""

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
            object.__setattr__(
                self,
                "received_at_utc",
                self.received_at_utc.replace(tzinfo=UTC),
            )
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
    def from_live_spot(
        cls,
        target: TargetSymbol,
        remote: RemoteSymbol,
        event: Any,
        received_at_utc: datetime | None = None,
        ingest_run_id: str | None = None,
    ) -> TickRecord:
        """Build a record from ProtoOASpotEvent-like data."""
        received_at_utc = received_at_utc or datetime.now(UTC)
        bid_raw = _get_optional_proto_field(event, "bid")
        ask_raw = _get_optional_proto_field(event, "ask")
        session_close_raw = _get_optional_proto_field(event, "sessionClose")

        source_timestamp_ms = (
            _get_optional_proto_field(event, "timestamp")
            or _get_optional_proto_field(event, "tickTimestamp")
            or _get_optional_proto_field(event, "time")
            or millis_from_utc(received_at_utc)
        )
        bid_updated = bid_raw is not None
        ask_updated = ask_raw is not None
        if bid_updated and ask_updated:
            quote_type = "BOTH"
        elif bid_updated:
            quote_type = "BID"
        elif ask_updated:
            quote_type = "ASK"
        else:
            quote_type = "TECHNICAL"

        return cls(
            symbol_id=target.symbol_id,
            local_symbol=target.local_symbol,
            ctrader_symbol_id=remote.ctrader_symbol_id,
            ctrader_symbol_name=remote.symbol_name,
            tick_time_utc=utc_from_millis(int(source_timestamp_ms)),
            source_timestamp_ms=int(source_timestamp_ms),
            bid_raw=int(bid_raw) if bid_raw is not None else None,
            ask_raw=int(ask_raw) if ask_raw is not None else None,
            bid=price_from_raw(bid_raw, remote.digits),
            ask=price_from_raw(ask_raw, remote.digits),
            bid_updated=bid_updated,
            ask_updated=ask_updated,
            quote_type=quote_type,
            source_mode="LIVE",
            session_close_raw=int(session_close_raw) if session_close_raw is not None else None,
            session_close=price_from_raw(session_close_raw, remote.digits),
            is_technical_event=quote_type == "TECHNICAL",
            received_at_utc=received_at_utc,
            ingest_run_id=ingest_run_id,
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
    ) -> TickRecord:
        """Build a record from ProtoOATickDataReq BID or ASK output."""
        quote_type = quote_type.upper()
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

    def to_db_params(self) -> tuple[Any, ...]:
        """Return params in the order used by TickSqlStore.INSERT_COLUMNS."""
        return (
            self.symbol_id,
            self.ctrader_symbol_id,
            self.ctrader_symbol_name,
            self.tick_time_utc.astimezone(UTC).replace(tzinfo=None),
            self.source_timestamp_ms,
            self.bid_raw,
            self.ask_raw,
            self.bid,
            self.ask,
            int(self.bid_updated),
            int(self.ask_updated),
            self.quote_type,
            self.source_mode,
            self.session_close_raw,
            self.session_close,
            int(self.is_technical_event),
            self.received_at_utc.astimezone(UTC).replace(tzinfo=None),
            self.ingest_run_id,
            self.event_hash,
        )

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
    def from_json_dict(cls, data: dict[str, Any]) -> TickRecord:
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
