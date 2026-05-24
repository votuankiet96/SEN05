"""Cost and price-adjustment helpers."""

from __future__ import annotations

from backtest_optimize.contracts import Direction, MarketSpec


def apply_entry_slippage(price: float, direction: Direction, market_spec: MarketSpec) -> float:
    """Apply adverse fixed entry slippage in price units."""
    slip = market_spec.slippage_buffer_pips * market_spec.pip_size
    if direction == Direction.BUY:
        return float(price) + slip
    return float(price) - slip


def round_turn_cost_currency(lot: float, market_spec: MarketSpec) -> float:
    """Return commission plus spread buffer cost for a full open-close leg."""
    commission = 2.0 * market_spec.commission_per_lot_per_side * float(lot)
    spread = market_spec.spread_buffer_pips * market_spec.pip_value_per_lot * float(lot)
    return float(commission + spread)


def risk_currency_per_lot(entry_price: float, sl_price: float, market_spec: MarketSpec) -> float:
    """Return stop-loss currency risk for one lot."""
    distance = abs(float(entry_price) - float(sl_price))
    if distance <= 0:
        raise ValueError("SL distance must be positive.")
    if market_spec.pip_size <= 0:
        raise ValueError("pip_size must be positive.")
    return distance / market_spec.pip_size * market_spec.pip_value_per_lot


def cost_r(cost_currency: float, risk_amount: float) -> float:
    """Convert currency cost to R units for one leg."""
    if risk_amount <= 0:
        raise ValueError("risk_amount must be positive.")
    return float(cost_currency) / float(risk_amount)


def price_exit_r(
    *,
    direction: Direction,
    entry_price: float,
    exit_price: float,
    sl_price: float,
) -> float:
    """Convert an exit price into gross R for one leg."""
    risk_distance = abs(float(entry_price) - float(sl_price))
    if risk_distance <= 0:
        raise ValueError("SL distance must be positive.")

    if direction == Direction.BUY:
        return (float(exit_price) - float(entry_price)) / risk_distance
    return (float(entry_price) - float(exit_price)) / risk_distance
