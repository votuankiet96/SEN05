"""Shared instrument, broker-profile, and execution-cost metadata.

This module is strategy-neutral. Strategies such as Combo, MA Cross, and AI
Trend should import symbol/broker metadata from here instead of importing from
one another.
"""

from __future__ import annotations

from typing import Any

from .broker import merge_broker_profile


DEFAULT_COSTS = {
    "slippage_pts":       2,       # Points slippage at fill
    "commission_per_lot": 3.5,     # USD per lot per side
}

DEFAULT_BROKER_PROFILE = "ftmo_2step_mt5"

BROKER_PROFILES = {
    "ftmo_2step_mt5": {
        "name": "ftmo_2step_mt5",
        "label": "FTMO 2-Step MT5",
        "platform": "MT5",
        "account_type": "2-Step",
        "verified": False,
        "defaults": {
            "lot_step": 0.01,
            "spec_verified": False,
        },
        "symbols": {
            "BTCUSD": {"lot_step": 0.001},
        },
    },
    "windsor_prime_mt5": {
        "name": "windsor_prime_mt5",
        "label": "Windsor Brokers Prime MT5",
        "platform": "MT5",
        "account_type": "Prime",
        "verified": False,
        "defaults": {
            "commission_per_lot": 0.0,
            "lot_step": 0.01,
            "spec_verified": False,
        },
        "symbols": {
            "GOLD": {"broker_symbol": "XAUUSD"},
            "BTCUSD": {"lot_step": 0.001},
        },
    },
}


SYMBOLS = {
    # ── US Markets ───────────────────────────────────────────────────────────
    "US30": {
        "symbol_id":         10,
        "label":             "US30 (Dow Jones)",
        "x":                 10.0,
        "session_hours_utc": [],
        "group":             "US",
        "contract_value":    1.0,
        "point_size":        1.0,
        "spread_pts":        2.0,
        "commission_per_lot": 3.5,
        "slippage_pts":      0.5,
        "min_lot_size":      0.01,
        "max_lot_size":      50.0,
        "swap_long_per_lot_per_day":  -3.5,   # verified from broker
        "swap_short_per_lot_per_day": 1.2,
    },
    "US500": {
        "symbol_id":         8,
        "label":             "US500 (S&P 500)",
        "x":                 1.0,
        "session_hours_utc": [],
        "group":             "US",
        "contract_value":    1.0,
        "point_size":        1.0,
        "spread_pts":        0.4,
        "commission_per_lot": 3.5,
        "slippage_pts":      0.1,
        "min_lot_size":      0.01,
        "max_lot_size":      50.0,
        "swap_long_per_lot_per_day":  0.0,   # TODO: verify from broker
        "swap_short_per_lot_per_day": 0.0,
    },
    "US100": {
        "symbol_id":         9,
        "label":             "US100 (NASDAQ 100)",
        "x":                 5.0,
        "session_hours_utc": [],
        "group":             "US",
        "contract_value":    1.0,
        "point_size":        1.0,
        "spread_pts":        1.5,
        "commission_per_lot": 3.5,
        "slippage_pts":      0.3,
        "min_lot_size":      0.01,
        "max_lot_size":      50.0,
        "swap_long_per_lot_per_day":  0.0,   # TODO: verify from broker
        "swap_short_per_lot_per_day": 0.0,
    },
    # ── European Markets ─────────────────────────────────────────────────────
    "DE40": {
        "symbol_id":         3,
        "label":             "DE40 (DAX 40)",
        "x":                 5.0,
        "session_hours_utc": [],
        "group":             "EU",
        "contract_value":    1.0,
        "point_size":        1.0,
        "spread_pts":        1.5,
        "commission_per_lot": 3.5,
        "slippage_pts":      0.5,
        "min_lot_size":      0.01,
        "max_lot_size":      50.0,
        "swap_long_per_lot_per_day":  0.0,   # TODO: verify from broker
        "swap_short_per_lot_per_day": 0.0,
    },
    "UK100": {
        "symbol_id":         7,
        "label":             "UK100 (FTSE 100)",
        "x":                 5.0,
        "session_hours_utc": [],
        "group":             "EU",
        "contract_value":    1.0,
        "point_size":        1.0,
        "spread_pts":        1.5,
        "commission_per_lot": 3.5,
        "slippage_pts":      0.5,
        "min_lot_size":      0.01,
        "max_lot_size":      50.0,
        "swap_long_per_lot_per_day":  0.0,   # TODO: verify from broker
        "swap_short_per_lot_per_day": 0.0,
    },
    "FR40": {
        "symbol_id":         2,
        "label":             "FR40 (CAC 40)",
        "x":                 5.0,             # (*) placeholder — run optimizer
        "session_hours_utc": [],
        "group":             "EU",
        "contract_value":    1.0,
        "point_size":        1.0,
        "spread_pts":        2.0,
        "commission_per_lot": 3.5,
        "slippage_pts":      0.5,
        "min_lot_size":      0.01,
        "max_lot_size":      50.0,
        "swap_long_per_lot_per_day":  0.0,   # TODO: verify from broker
        "swap_short_per_lot_per_day": 0.0,
    },
    "SP35": {
        "symbol_id":         6,
        "label":             "SP35 (IBEX 35)",
        "x":                 5.0,             # (*) placeholder — run optimizer
        "session_hours_utc": [],
        "group":             "EU",
        "contract_value":    1.0,
        "point_size":        1.0,
        "spread_pts":        8.0,
        "commission_per_lot": 3.5,
        "slippage_pts":      1.0,
        "min_lot_size":      0.01,
        "max_lot_size":      50.0,
        "swap_long_per_lot_per_day":  0.0,   # TODO: verify from broker
        "swap_short_per_lot_per_day": 0.0,
    },
    # ── Asian Markets ────────────────────────────────────────────────────────
    "HK50": {
        "symbol_id":         4,
        "label":             "HK50 (Hang Seng 50)",
        "x":                 15.0,
        "session_hours_utc": [],
        "group":             "ASIA",
        "contract_value":    1.0,
        "point_size":        1.0,
        "spread_pts":        5.0,
        "commission_per_lot": 3.5,
        "slippage_pts":      1.0,
        "min_lot_size":      0.01,
        "max_lot_size":      20.0,
        "swap_long_per_lot_per_day":  -4.2,  # verified from broker
        "swap_short_per_lot_per_day": 1.5,
    },
    "J225": {
        "symbol_id":         5,
        "label":             "J225 (Nikkei 225)",
        "x":                 15.0,
        "session_hours_utc": [],
        "group":             "ASIA",
        "contract_value":    1.0,
        "point_size":        1.0,
        "spread_pts":        8.0,
        "commission_per_lot": 3.5,
        "slippage_pts":      1.5,
        "min_lot_size":      0.01,
        "max_lot_size":      20.0,
        "swap_long_per_lot_per_day":  0.8,   # verified from broker
        "swap_short_per_lot_per_day": -2.1,
    },
    # ── Metals ───────────────────────────────────────────────────────────────
    "GOLD": {
        "symbol_id":         56,
        "label":             "GOLD (XAU/USD)",
        "x":                 0.5,             # (*) placeholder — run optimizer
        "session_hours_utc": [],
        "group":             "METAL",
        "contract_value":    1.0,
        "point_size":        1.0,
        "spread_pts":        0.3,
        "commission_per_lot": 3.5,
        "slippage_pts":      0.1,
        "min_lot_size":      0.01,
        "max_lot_size":      20.0,
        "swap_long_per_lot_per_day":  0.0,   # TODO: verify from broker
        "swap_short_per_lot_per_day": 0.0,
    },
    # ── Crypto ───────────────────────────────────────────────────────────────
    "BTCUSD": {
        "symbol_id":         81,
        "label":             "BTCUSD (Bitcoin)",
        "x":                 50.0,            # (*) placeholder — run optimizer
        "session_hours_utc": [],
        "group":             "CRYPTO",
        "contract_value":    1.0,
        "point_size":        1.0,
        "spread_pts":        5.0,
        "commission_per_lot": 3.5,
        "slippage_pts":      2.0,
        "min_lot_size":      0.001,           # BTC micro-lots
        "max_lot_size":      5.0,
        "swap_long_per_lot_per_day":  0.0,   # TODO: verify from broker (highly variable)
        "swap_short_per_lot_per_day": 0.0,
    },
}


def get_broker_profile(profile_key: str | None = None) -> dict[str, Any]:
    """Resolve a broker profile by key."""
    key = profile_key or DEFAULT_BROKER_PROFILE
    if key not in BROKER_PROFILES:
        raise KeyError(f"Unknown broker profile '{key}'. Available: {list(BROKER_PROFILES)}")
    return BROKER_PROFILES[key]


def get_symbol_config(sym_key: str, broker_profile: str | None = None) -> dict[str, Any]:
    """Return raw shared symbol config merged with default costs and broker profile."""
    if sym_key not in SYMBOLS:
        raise KeyError(f"Symbol '{sym_key}' not found. Available: {list(SYMBOLS)}")

    merged = {
        **DEFAULT_COSTS,
        **SYMBOLS[sym_key],
    }
    return merge_broker_profile(
        merged,
        get_broker_profile(broker_profile),
        symbol_key=sym_key,
    )


def get_base_symbol_params(sym_key: str, broker_profile: str | None = None) -> dict[str, Any]:
    """Resolve strategy-neutral execution params for one symbol."""
    sym = get_symbol_config(sym_key, broker_profile=broker_profile)
    return {
        "symbol_id": sym["symbol_id"],
        "label": sym["label"],
        "group": sym.get("group", ""),
        "broker_symbol": sym.get("broker_symbol", sym_key),
        "broker_profile": sym.get("broker_profile", ""),
        "broker_label": sym.get("broker_label", ""),
        "broker_platform": sym.get("broker_platform", ""),
        "spec_verified": sym.get("spec_verified", False),
        "session_hours_utc": sym.get("session_hours_utc", []),
        "contract_value": sym.get("contract_value", 1.0),
        "point_size": sym.get("point_size", 1.0),
        "spread_pts": sym.get("spread_pts", 0.0),
        "commission_per_lot": sym.get("commission_per_lot", DEFAULT_COSTS["commission_per_lot"]),
        "slippage_pts": sym.get("slippage_pts", DEFAULT_COSTS["slippage_pts"]),
        "min_lot_size": sym.get("min_lot_size", 0.01),
        "max_lot_size": sym.get("max_lot_size", 100.0),
        "lot_step": sym.get("lot_step", sym.get("min_lot_size", 0.01)),
        "swap_long_per_lot_per_day": sym.get("swap_long_per_lot_per_day", 0.0),
        "swap_short_per_lot_per_day": sym.get("swap_short_per_lot_per_day", 0.0),
    }


def get_cost_settings(
    sym_key: str,
    overrides: dict[str, Any] | None = None,
    broker_profile: str | None = None,
) -> dict[str, Any]:
    """Resolve execution costs for one symbol."""
    sym = get_symbol_config(sym_key, broker_profile=broker_profile)
    costs = {
        **DEFAULT_COSTS,
        "slippage_pts": sym.get("slippage_pts", DEFAULT_COSTS["slippage_pts"]),
        "commission_per_lot": sym.get("commission_per_lot", DEFAULT_COSTS["commission_per_lot"]),
    }
    if overrides:
        costs.update(overrides)
    return costs


__all__ = [
    "BROKER_PROFILES",
    "DEFAULT_BROKER_PROFILE",
    "DEFAULT_COSTS",
    "SYMBOLS",
    "get_base_symbol_params",
    "get_broker_profile",
    "get_cost_settings",
    "get_symbol_config",
]
