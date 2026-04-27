"""Configuration for the AI Trend / KNN + M30 EMA pullback strategy.

The strategy is intentionally separate from Combo v2. Shared instrument and
broker metadata comes from ``shared.instruments``; signal logic and indicator
defaults live here.
"""

from __future__ import annotations

from shared.instruments import (
    BROKER_PROFILES,
    DEFAULT_BROKER_PROFILE,
    DEFAULT_COSTS,
    SYMBOLS,
    get_base_symbol_params,
    get_cost_settings,
)


STRATEGY = {
    "name": "AI_Trend_KNN_EMA",
    "version": "v0.1",
    # Timeframes
    "trend_timeframe": "H3",
    "entry_timeframe": "M30",
    # Execution/risk. Shared execution uses ktp*ATR for TP and signal-bar
    # high/low +/- x for pending entry/SL.
    "ktp": 2.0,
    "min_rr": 1.20,
    "risk_per_trade": 0.005,
    "ftmo_daily_limit": 0.05,
    "ftmo_max_dd": 0.10,
    "trailing_activation": 1.0,
    "pending_ttl_bars": 3,
    "partial_tp_fraction": 0.5,
}

ACCOUNT_MODES = {
    "standard": {
        "label": "Standard",
        "account_model": "retail",
        "risk_per_trade": STRATEGY["risk_per_trade"],
        "daily_loss_limit": 1.0,
        "max_drawdown_limit": 1.0,
        "max_drawdown_mode": "fixed_initial",
        "pending_ttl_bars": STRATEGY["pending_ttl_bars"],
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
        "pending_ttl_bars": STRATEGY["pending_ttl_bars"],
        "partial_tp_fraction": STRATEGY["partial_tp_fraction"],
        "trailing_activation": STRATEGY["trailing_activation"],
    },
}


TREND_TIMEFRAME = STRATEGY["trend_timeframe"]
ENTRY_TIMEFRAME = STRATEGY["entry_timeframe"]
TIMEFRAME = ENTRY_TIMEFRAME
DEFAULT_N_BARS = 500


INDICATOR_COLS = [
    "h3_ai_knn",
    "h3_ai_avg",
    "h3_bias",
    "ema_fast",
    "ema_slow",
    "atr",
]


def get_indicator_params() -> dict:
    """Return default indicator and signal parameters."""
    return {
        # H3 AI Trend/KNN parameters. Defaults mirror the TradingView script.
        "AI_PRICE_VALUE": "hl2",
        "AI_TARGET_VALUE": "Price Action",
        "AI_MA_LEN": 5,
        "AI_TARGET_LEN": 5,
        "AI_K": 3,
        "AI_SMOOTHING_PERIOD": 50,
        "AI_CONFIRM_BARS": 1,
        # M30 entry trigger.
        "EMA_FAST": 15,
        "EMA_SLOW": 30,
        "ATR_PERIOD": 14,
        "TRAIL_MA": "ema_slow",
        "PULLBACK_LOOKBACK": 3,
        "REQUIRE_RECLAIM_CROSS": True,
        "REQUIRE_EMA_SLOPE": True,
        "MIN_EMA_GAP_ATR": 0.05,
        "MAX_ENTRY_DISTANCE_ATR": 1.50,
        # Risk filters.
        "KTP": STRATEGY["ktp"],
        "MIN_RR": STRATEGY["min_rr"],
    }


def get_account_settings(mode: str = "standard", overrides: dict | None = None) -> dict:
    """Resolve account-mode settings using this strategy's risk defaults."""
    if mode not in ACCOUNT_MODES:
        raise KeyError(f"Unknown account mode '{mode}'. Available: {list(ACCOUNT_MODES)}")

    resolved = {
        **STRATEGY,
        **ACCOUNT_MODES[mode],
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
    """Resolve symbol execution config with AI Trend strategy defaults."""
    cfg = get_base_symbol_params(sym_key, broker_profile=broker_profile)
    raw = SYMBOLS[sym_key]

    if "ktp" not in raw:
        cfg["ktp"] = STRATEGY["ktp"]
    cfg["x"] = raw.get("x", 0.0)
    cfg["ma_period"] = raw.get("ma_period", 0)
    cfg["trailing_activation"] = raw.get(
        "ai_trend_trailing_activation",
        STRATEGY["trailing_activation"],
    )
    return cfg


def summary() -> str:
    p = get_indicator_params()
    return "\n".join([
        f"Strategy  : {STRATEGY['name']} {STRATEGY['version']}",
        f"Trend TF  : {TREND_TIMEFRAME} AI Trend/KNN",
        f"Entry TF  : {ENTRY_TIMEFRAME} EMA{p['EMA_FAST']}/EMA{p['EMA_SLOW']}",
        f"AI params : ma={p['AI_MA_LEN']} target={p['AI_TARGET_LEN']} "
        f"k={p['AI_K']} smooth={p['AI_SMOOTHING_PERIOD']}",
        f"Risk      : {STRATEGY['risk_per_trade']:.2%} per trade",
        f"Symbols   : {', '.join(SYMBOLS.keys())}",
        f"Default broker: {DEFAULT_BROKER_PROFILE}",
    ])


__all__ = [
    "ACCOUNT_MODES",
    "BROKER_PROFILES",
    "DEFAULT_BROKER_PROFILE",
    "DEFAULT_COSTS",
    "STRATEGY",
    "TREND_TIMEFRAME",
    "ENTRY_TIMEFRAME",
    "TIMEFRAME",
    "DEFAULT_N_BARS",
    "INDICATOR_COLS",
    "SYMBOLS",
    "get_account_settings",
    "get_cost_settings",
    "get_indicator_params",
    "get_symbol_params",
    "summary",
]
