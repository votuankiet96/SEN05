from __future__ import annotations

import importlib

import pandas as pd

from core_python.shared.execution import (
    MarketOrderIntent,
    PendingOrderIntent,
    adverse_entry_price,
    fill_market_order,
    fill_pending_order,
    risk_sized_lots,
)
from core_python.strategies.combo.orders import create_pending_breakout_intent
from core_python.strategies.combo.engines.common import (
    FAST_POSITION_FIELDS,
    FULL_POSITION_FIELDS,
    make_fast_position,
    make_full_position,
)
from core_python.strategies.ma_cross.orders import create_market_next_open_intent


def test_shared_execution_package_exposes_new_architecture_modules() -> None:
    for module_name in [
        "core_python.shared.execution.types",
        "core_python.shared.execution.primitives",
        "core_python.shared.execution.prices",
        "core_python.shared.execution.engines.market",
    ]:
        importlib.import_module(module_name)


def test_combo_owns_pending_breakout_order_formula() -> None:
    bar = pd.Series(
        {"high": 106.0, "low": 95.0, "atr": 4.0, "signal": 1},
        name=pd.Timestamp("2024-01-01 04:00"),
    )

    intent = create_pending_breakout_intent(
        "US30",
        bar,
        x=10.0,
        ktp=2.3,
        ttl_bars=3,
    )

    assert isinstance(intent, PendingOrderIntent)
    assert intent.entry == 116.0
    assert intent.sl == 85.0
    assert intent.tp == 116.0 + 2.3 * 4.0
    assert intent.ttl_bars == 3
    assert intent.metadata["x"] == 10.0


def test_combo_position_factories_match_declared_schemas() -> None:
    fast_pos = make_fast_position(
        direction=1,
        entry=110.0,
        sl=100.0,
        tp=130.0,
        atr=5.0,
        risk_usd=500.0,
        sl_dist=10.0,
        commission=7.0,
        lot_size=1.0,
        entry_date=pd.Timestamp("2024-01-01").date(),
    )
    full_pos = make_full_position(
        direction=1,
        entry=110.0,
        sl=100.0,
        tp=130.0,
        atr=5.0,
        risk_usd=500.0,
        sl_dist=10.0,
        entry_time=pd.Timestamp("2024-01-01"),
        commission=7.0,
        lot_size=1.0,
    )

    assert set(fast_pos) == set(FAST_POSITION_FIELDS)
    assert set(full_pos) == set(FULL_POSITION_FIELDS)
    assert fast_pos["d"] == full_pos["direction"] == 1
    assert fast_pos["risk"] == full_pos["risk_usd"] == 500.0


def test_ma_cross_owns_market_next_open_order_policy() -> None:
    bar = pd.Series(
        {"open": 100.0, "atr": 2.0, "signal": -1},
        name=pd.Timestamp("2024-01-01 00:30"),
    )

    intent = create_market_next_open_intent("US30", bar)

    assert isinstance(intent, MarketOrderIntent)
    assert intent.direction == -1
    assert intent.execute_at == "next_open"
    assert intent.metadata["atr"] == 2.0


def test_fill_and_cost_helpers_are_strategy_neutral() -> None:
    bar = pd.Series(
        {"open": 110.0, "high": 120.0, "low": 100.0},
        name=pd.Timestamp("2024-01-01 01:00"),
    )
    pending = PendingOrderIntent(
        symbol="TEST",
        direction=1,
        created_time=pd.Timestamp("2024-01-01"),
        entry=115.0,
        sl=100.0,
        tp=130.0,
    )
    market = MarketOrderIntent(
        symbol="TEST",
        direction=1,
        created_time=pd.Timestamp("2024-01-01"),
    )

    pending_fill = fill_pending_order(bar, pending)
    market_fill = fill_market_order(bar, market)

    assert pending_fill is not None
    assert pending_fill.fill_price == 115.0
    assert market_fill.fill_price == 110.0
    assert adverse_entry_price(110.0, 1, spread=2.0, slippage=0.5) == 112.5


def test_risk_sizing_uses_shared_broker_lot_rules() -> None:
    lots, risk_usd = risk_sized_lots(
        equity=100_000.0,
        risk_per_trade=0.005,
        entry=110.0,
        sl=100.0,
        symbol_config={
            "point_size": 1.0,
            "contract_value": 1.0,
            "min_lot_size": 0.01,
            "max_lot_size": 50.0,
            "lot_step": 0.01,
        },
        commission_per_lot=0.0,
    )

    assert risk_usd == 500.0
    assert lots == 50.0


def test_risk_sizing_does_not_round_up_below_min_lot() -> None:
    lots, risk_usd = risk_sized_lots(
        equity=100.0,
        risk_per_trade=0.001,
        entry=100.0,
        sl=0.0,
        symbol_config={
            "point_size": 1.0,
            "contract_value": 1.0,
            "min_lot_size": 0.01,
            "max_lot_size": 50.0,
            "lot_step": 0.01,
        },
        commission_per_lot=0.0,
    )

    assert risk_usd == 0.1
    assert lots == 0.0
