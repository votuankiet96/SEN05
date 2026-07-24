"""Shared contracts for the strategy-neutral execution logic lab.

The contracts intentionally encode assumptions that must not be left to
individual modules:

- Bar timestamps are UTC-naive bar-open timestamps.
- Signal at bar i enters at open of bar i+1.
- Conservative ambiguity handling is the default.
- Existing clusters are updated/closed before new signals on the same bar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import pandas as pd

TIME_CONVENTION = "UTC-naive bar-open timestamps"
PROCESSING_ORDER = (
    "For each bar, update/close existing clusters before evaluating new signals "
    "on that same bar."
)


def utc_now_naive() -> datetime:
    """Return a UTC timestamp stored as naive for JSON/CSV compatibility."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Direction(str, Enum):
    """Trade direction."""

    BUY = "BUY"
    SELL = "SELL"


class AmbiguityPolicy(str, Enum):
    """How to resolve a bar that touches both SL and TP."""

    CONSERVATIVE = "conservative"
    OPTIMISTIC = "optimistic"
    SKIP = "skip"
    MARK_AMBIGUOUS = "mark_ambiguous"


class ExitReason(str, Enum):
    """Reason a leg or cluster closed."""

    TP = "tp"
    SL = "sl"
    END_OF_DATA = "end_of_data"
    AMBIGUOUS = "ambiguous"
    SKIPPED = "skipped"
    REVERSE_SIGNAL = "reverse_signal"
    PENDING_EXPIRED = "pending_expired"
    PENDING_CANCELLED = "pending_cancelled"


class ClusterStatus(str, Enum):
    """Cluster-level lifecycle status."""

    ACCEPTED = "accepted"
    SKIPPED = "skipped"
    OPEN_AT_END = "open_at_end"
    CLOSED = "closed"
    AMBIGUOUS = "ambiguous"
    REVERSED = "reversed"
    PENDING_EXPIRED = "pending_expired"
    PENDING_CANCELLED = "pending_cancelled"
    PENDING_UNFILLED_AT_END = "pending_unfilled_at_end"


class OrderStatus(str, Enum):
    """Lifecycle state for simulated orders."""

    PENDING = "pending"
    FILLED = "filled"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class MarketSpec:
    """Instrument metadata needed for sizing and costs."""

    symbol: str
    pip_size: float = 0.0001
    pip_value_per_lot: float = 10.0
    min_lot: float = 0.01
    lot_step: float = 0.01
    commission_per_lot_per_side: float = 0.0
    spread_buffer_pips: float = 0.0
    slippage_buffer_pips: float = 0.0


@dataclass(frozen=True)
class RunAssumptions:
    """Assumptions captured with each run for auditability."""

    time_convention: str = TIME_CONVENTION
    ohlcv_source: str = "core_python.data.loader"
    signal_source: str = "raw_signals"
    ambiguity_policy: AmbiguityPolicy = AmbiguityPolicy.CONSERVATIVE
    processing_order: str = PROCESSING_ORDER
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SignalRow:
    """Normalized signal row consumed by the execution lab."""

    bartime: pd.Timestamp
    symbol: str
    timeframe: str
    direction: Direction
    atr: float | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Bar:
    """Normalized OHLCV bar."""

    bartime: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


@dataclass(frozen=True)
class SLResult:
    """Stop-loss calculation result."""

    price: float
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    source: str = "calculated"


@dataclass(frozen=True)
class TPLevel:
    """Take-profit target for one or more legs."""

    level: int
    price: float
    weight: float = 1.0
    label: str | None = None


@dataclass(frozen=True)
class LegSizing:
    """Sizing decision for one position leg."""

    leg_id: int
    risk_fraction: float
    risk_amount: float
    raw_lot: float
    lot: float
    skipped: bool = False
    skip_reason: str | None = None


@dataclass
class PositionLeg:
    """State and result for a single leg inside a cluster."""

    leg_id: int
    direction: Direction
    entry_time: pd.Timestamp
    entry_price: float
    sl_price: float
    tp_level: TPLevel
    sizing: LegSizing
    current_sl: float | None = None
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: ExitReason | None = None
    r_result: float = 0.0
    cost_r: float = 0.0
    ambiguous: bool = False


@dataclass(frozen=True)
class ClusterResult:
    """Result of applying execution logic to one signal."""

    signal: SignalRow
    status: ClusterStatus
    entry_time: pd.Timestamp | None = None
    entry_price: float | None = None
    sl_price: float | None = None
    tp_levels: tuple[TPLevel, ...] = field(default_factory=tuple)
    legs: tuple[PositionLeg, ...] = field(default_factory=tuple)
    exit_time: pd.Timestamp | None = None
    r_result: float = 0.0
    gross_r: float = 0.0
    cost_r: float = 0.0
    bar_mae_r: float | None = None
    bar_mfe_r: float | None = None
    holding_bars: int = 0
    ambiguous_bars: int = 0
    skip_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BacktestResult:
    """Single-run result for one symbol/timeframe."""

    symbol: str
    timeframe: str
    assumptions: RunAssumptions
    market_spec: MarketSpec
    clusters: tuple[ClusterResult, ...]
    config: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now_naive)

    @property
    def signal_count(self) -> int:
        return len(self.clusters)

    @property
    def accepted_count(self) -> int:
        return sum(c.status != ClusterStatus.SKIPPED for c in self.clusters)

    @property
    def skipped_count(self) -> int:
        return sum(c.status == ClusterStatus.SKIPPED for c in self.clusters)
