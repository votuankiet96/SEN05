"""Single-run execution engine."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import pandas as pd

from backtest_optimize.contracts import (
    AmbiguityPolicy,
    BacktestResult,
    ClusterResult,
    ClusterStatus,
    MarketSpec,
    RunAssumptions,
    SignalRow,
)
from backtest_optimize.execution.cost_model import apply_entry_slippage
from backtest_optimize.execution.position_manager import PositionManager, simulate_cluster
from backtest_optimize.execution.sizing import calculate_leg_sizing
from backtest_optimize.execution.sl_calculator import calculate_sl
from backtest_optimize.execution.tp_calculator import calculate_tp_levels
from backtest_optimize.io.market_data import (
    assert_signal_bartimes_exist,
    bars_from_time,
    get_next_open_bar,
    normalize_ohlcv_frame,
)
from backtest_optimize.io.signal_loader import to_signal_rows

logger = logging.getLogger(__name__)


def _coerce_market_spec(symbol: str, value: MarketSpec | dict[str, Any] | None) -> MarketSpec:
    if isinstance(value, MarketSpec):
        return value
    data = dict(value or {})
    data.setdefault("symbol", symbol)
    return MarketSpec(**data)


def _coerce_ambiguity_policy(value: AmbiguityPolicy | str | None) -> AmbiguityPolicy:
    if value is None:
        return AmbiguityPolicy.CONSERVATIVE
    if isinstance(value, AmbiguityPolicy):
        return value
    return AmbiguityPolicy(str(value))


def _coerce_signals(signals: pd.DataFrame | Sequence[SignalRow]) -> list[SignalRow]:
    if isinstance(signals, pd.DataFrame):
        return to_signal_rows(signals)
    return list(signals)


def run_single(
    *,
    signals: pd.DataFrame | Sequence[SignalRow],
    bars: pd.DataFrame,
    symbol: str,
    timeframe: str,
    market_spec: MarketSpec | dict[str, Any] | None = None,
    account_size: float = 10_000.0,
    risk_per_cluster: float = 0.01,
    sl_method: str = "atr_multiple",
    sl_params: dict[str, Any] | None = None,
    tp_method: str = "risk_multiple",
    tp_params: dict[str, Any] | None = None,
    leg_weights: Sequence[float] | None = None,
    ambiguity_policy: AmbiguityPolicy | str | None = None,
    management: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    raise_on_error: bool = False,
) -> BacktestResult:
    """Run a single-symbol, single-timeframe logic simulation."""
    symbol = symbol.strip().upper()
    timeframe = timeframe.strip().upper()
    policy = _coerce_ambiguity_policy(ambiguity_policy)
    spec = _coerce_market_spec(symbol, market_spec)
    normalized_bars = normalize_ohlcv_frame(bars)

    signal_rows = [
        signal
        for signal in _coerce_signals(signals)
        if signal.symbol.upper() == symbol and signal.timeframe.upper() == timeframe
    ]
    signal_rows.sort(key=lambda item: item.bartime)
    assert_signal_bartimes_exist(signal_rows, normalized_bars)

    assumptions = RunAssumptions(ambiguity_policy=policy)
    position_manager = PositionManager()
    results: list[ClusterResult] = []

    for signal in signal_rows:
        if position_manager.is_cluster_open(signal.symbol, signal.timeframe, signal.bartime):
            results.append(
                ClusterResult(
                    signal=signal,
                    status=ClusterStatus.SKIPPED,
                    skip_reason="cluster_open",
                )
            )
            continue

        try:
            entry_bar = get_next_open_bar(signal, normalized_bars)
            entry_price = apply_entry_slippage(entry_bar.open, signal.direction, spec)
            sl_result = calculate_sl(sl_method, signal, entry_price, normalized_bars, sl_params)
            tp_levels = calculate_tp_levels(
                tp_method,
                signal,
                entry_price,
                sl_result.price,
                normalized_bars,
                tp_params,
            )
            sizing = calculate_leg_sizing(
                tp_levels=tp_levels,
                entry_price=entry_price,
                sl_price=sl_result.price,
                market_spec=spec,
                account_size=account_size,
                risk_per_cluster=risk_per_cluster,
                leg_weights=leg_weights,
            )
            future = bars_from_time(normalized_bars, entry_bar.bartime)
            cluster = simulate_cluster(
                signal=signal,
                entry_time=entry_bar.bartime,
                entry_price=entry_price,
                future_bars=future,
                sl_result=sl_result,
                tp_levels=tp_levels,
                sizing=sizing,
                market_spec=spec,
                ambiguity_policy=policy,
                management=management,
            )
        except Exception as exc:
            logger.warning(
                "Signal %s %s %s skipped due to execution error: %s",
                signal.symbol,
                signal.timeframe,
                signal.bartime,
                exc,
                exc_info=True,
            )
            if raise_on_error:
                raise
            cluster = ClusterResult(
                signal=signal,
                status=ClusterStatus.SKIPPED,
                skip_reason=f"execution_error: {exc}",
            )

        position_manager.record_cluster(cluster)
        results.append(cluster)

    run_config = {
        "account_size": account_size,
        "risk_per_cluster": risk_per_cluster,
        "sl_method": sl_method,
        "sl_params": sl_params or {},
        "tp_method": tp_method,
        "tp_params": tp_params or {},
        "leg_weights": list(leg_weights) if leg_weights is not None else None,
        "ambiguity_policy": policy.value,
        "management": management or {},
        "raise_on_error": raise_on_error,
    }
    if config:
        run_config.update(config)

    return BacktestResult(
        symbol=symbol,
        timeframe=timeframe,
        assumptions=assumptions,
        market_spec=spec,
        clusters=tuple(results),
        config=run_config,
    )
