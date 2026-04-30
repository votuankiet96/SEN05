"""Parameter source of truth for the MA Cross market-order strategy.

MA Cross owns its signal/risk defaults in this module. Shared instrument,
broker, and cost metadata comes from ``core_python.shared.market`` so this
strategy does not depend on the Combo package.
"""

from __future__ import annotations

from core_python.shared.market import (
    BROKER_PROFILES,
    DEFAULT_BROKER_PROFILE,
    DEFAULT_COSTS,
    SYMBOLS,
    get_base_symbol_params,
    get_cost_settings,
)


STRATEGY = {
    "name": "MA_Cross",
    "version": "v1.0",
    "order_type": "market",
    "ma_type": "sma",
    "fast_ma": 10,
    "slow_ma": 20,
    "atr_period": 14,
    # Signal confirmed on bar close, order simulated at next bar open.
    "entry_timing": "next_bar_open",
    "reverse_on_opposite": True,
    # Protective stop is mandatory for risk-based lot sizing.
    "atr_stop_mult": 2.0,
    "atr_tp_mult": 2.0,
    "partial_tp_fraction": 0.5,
    "trailing_activation": 1.0,
    "trailing_column": "slow_ma",
    "risk_per_trade": 0.005,
    "ftmo_daily_limit": 0.05,
    "ftmo_max_dd": 0.10,
}

ACCOUNT_MODES = {
    "standard": {
        "label": "Standard",
        "account_model": "retail",
        "risk_per_trade": STRATEGY["risk_per_trade"],
        "daily_loss_limit": 1.0,
        "max_drawdown_limit": 1.0,
        "max_drawdown_mode": "fixed_initial",
        "partial_tp_fraction": STRATEGY["partial_tp_fraction"],
        "trailing_activation": STRATEGY["trailing_activation"],
    },
    "ftmo": {
        "label": "FTMO 2-Step",
        "account_model": "ftmo_2step",
        "risk_per_trade": STRATEGY["risk_per_trade"],
        "daily_loss_limit": STRATEGY["ftmo_daily_limit"],
        "max_drawdown_limit": STRATEGY["ftmo_max_dd"],
        "max_drawdown_mode": "fixed_initial",
        "partial_tp_fraction": STRATEGY["partial_tp_fraction"],
        "trailing_activation": STRATEGY["trailing_activation"],
    },
}


TIMEFRAMES = ["M20", "M30", "M45"]
TIMEFRAME = "M30"
DEFAULT_N_BARS = 1000
INDICATOR_COLS = ["fast_ma", "slow_ma", "atr"]


OPTIMIZATION = {
    "symbol": {
        "fast_ma_values": [8, 10, 12],
        "slow_ma_values": [18, 20, 24, 30],
        "atr_stop_mult_values": [1.5, 2.0, 2.5],
        "atr_tp_mult_values": [0.0, 2.0, 3.0],
        "timeframes": TIMEFRAMES,
        "max_bars": 80000,
        "score_column": "sharpe",
    },
    "portfolio": {
        "top_k_per_symbol": 3,
        "score_column": "score",
    },
}


def get_indicator_params() -> dict:
    """Return default indicator and signal parameters."""
    return {
        "MA_TYPE": STRATEGY["ma_type"],
        "FAST_MA": STRATEGY["fast_ma"],
        "SLOW_MA": STRATEGY["slow_ma"],
        "ATR_PERIOD": STRATEGY["atr_period"],
    }


def get_account_settings(mode: str = "standard", overrides: dict | None = None) -> dict:
    """Resolve account/risk settings for market-order execution."""
    if mode not in ACCOUNT_MODES:
        raise KeyError(f"Unknown account mode '{mode}'. Available: {list(ACCOUNT_MODES)}")

    resolved = {
        **STRATEGY,
        **ACCOUNT_MODES[mode],
        "risk_per_trade": STRATEGY["risk_per_trade"],
        "reverse_on_opposite": STRATEGY["reverse_on_opposite"],
        "atr_stop_mult": STRATEGY["atr_stop_mult"],
        "atr_tp_mult": STRATEGY["atr_tp_mult"],
        "partial_tp_fraction": STRATEGY["partial_tp_fraction"],
        "trailing_activation": STRATEGY["trailing_activation"],
        "trailing_column": STRATEGY["trailing_column"],
    }
    resolved["daily_loss_limit"] = resolved.get(
        "daily_loss_limit",
        resolved.get("ftmo_daily_limit", STRATEGY["ftmo_daily_limit"]),
    )
    resolved["max_drawdown_limit"] = resolved.get(
        "max_drawdown_limit",
        resolved.get("ftmo_max_dd", STRATEGY["ftmo_max_dd"]),
    )
    resolved["max_drawdown_mode"] = resolved.get("max_drawdown_mode", "fixed_initial")
    resolved["ftmo_daily_limit"] = resolved["daily_loss_limit"]
    resolved["ftmo_max_dd"] = resolved["max_drawdown_limit"]

    if overrides:
        resolved.update(overrides)
        if "daily_loss_limit" in overrides:
            resolved["ftmo_daily_limit"] = resolved["daily_loss_limit"]
        if "max_drawdown_limit" in overrides:
            resolved["ftmo_max_dd"] = resolved["max_drawdown_limit"]
    return resolved


def get_symbol_params(sym_key: str, broker_profile: str | None = None) -> dict:
    """Resolve symbol execution config for MA Cross."""
    cfg = get_base_symbol_params(sym_key, broker_profile=broker_profile)
    cfg["order_type"] = STRATEGY["order_type"]
    return cfg


def get_symbol_search_space(
    sym_key: str,
    overrides: dict | None = None,
    broker_profile: str | None = None,
) -> dict:
    """Return a conservative optimizer search space."""
    if sym_key not in SYMBOLS:
        raise KeyError(f"Unknown symbol '{sym_key}'. Available: {list(SYMBOLS)}")
    _ = broker_profile
    search = {
        "fast_ma": list(OPTIMIZATION["symbol"]["fast_ma_values"]),
        "slow_ma": list(OPTIMIZATION["symbol"]["slow_ma_values"]),
        "atr_stop_mult": list(OPTIMIZATION["symbol"]["atr_stop_mult_values"]),
        "atr_tp_mult": list(OPTIMIZATION["symbol"]["atr_tp_mult_values"]),
        "timeframe": list(OPTIMIZATION["symbol"]["timeframes"]),
    }
    if overrides:
        search.update(overrides)
    return search


def summary() -> str:
    p = get_indicator_params()
    return "\n".join([
        f"Strategy      : {STRATEGY['name']} {STRATEGY['version']}",
        f"Order type    : {STRATEGY['order_type']} at {STRATEGY['entry_timing']}",
        f"MA            : {p['MA_TYPE'].upper()}{p['FAST_MA']}/{p['SLOW_MA']}",
        f"ATR stop      : {STRATEGY['atr_stop_mult']} x ATR{p['ATR_PERIOD']}",
        f"Partial TP    : {STRATEGY['partial_tp_fraction']:.0%} at "
        f"{STRATEGY['atr_tp_mult']} x ATR",
        f"Trailing      : {STRATEGY['trailing_column']} after "
        f"{STRATEGY['trailing_activation']} x ATR",
        f"Timeframes    : {', '.join(TIMEFRAMES)}",
        f"Risk          : {STRATEGY['risk_per_trade']:.2%} per trade",
        f"Symbols       : {', '.join(SYMBOLS.keys())}",
        f"Default broker: {DEFAULT_BROKER_PROFILE}",
    ])


__all__ = [
    "ACCOUNT_MODES",
    "BROKER_PROFILES",
    "DEFAULT_BROKER_PROFILE",
    "DEFAULT_COSTS",
    "STRATEGY",
    "TIMEFRAME",
    "TIMEFRAMES",
    "DEFAULT_N_BARS",
    "INDICATOR_COLS",
    "OPTIMIZATION",
    "SYMBOLS",
    "get_account_settings",
    "get_cost_settings",
    "get_indicator_params",
    "get_symbol_params",
    "get_symbol_search_space",
    "summary",
]
