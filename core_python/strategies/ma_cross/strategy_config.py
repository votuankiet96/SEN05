# =============================================================================
# strategies/ma_cross/strategy_config.py  —  MA Cross Strategy Parameters
# =============================================================================
# Skeleton — điền thông số và symbols trước khi bắt đầu backtest.
#
# Tham khảo strategies/combo/strategy_config.py để biết đầy đủ cấu trúc.

# =============================================================================
# 1. STRATEGY IDENTITY
# =============================================================================
STRATEGY = {
    "name":    "MA_Cross",
    "version": "v1",
    # ── Tham số tín hiệu ─────────────────────────────────────────────────────
    "fast_ma": 10,       # TODO: điều chỉnh
    "slow_ma": 50,       # TODO: điều chỉnh
    "ktp":     2.0,      # TP = kTP × ATR
    "min_rr":  1.2,      # Minimum R:R ratio
    # ── Risk management ───────────────────────────────────────────────────────
    "risk_per_trade":      0.005,   # 0.5% equity per trade
    "ftmo_daily_limit":    0.05,    # 5% daily loss limit
    "ftmo_max_dd":         0.10,    # 10% max drawdown
    "trailing_activation": 1.0,     # Activate trailing SL at 1.0 × ATR profit
    "pending_ttl_bars":    3,       # Pending order TTL in bars
    "partial_tp_fraction": 0.0,     # 0.0 = no partial TP (simple full exit)
}


# =============================================================================
# 2. SCAN DEFAULTS
# =============================================================================
TIMEFRAME      = 'H4'
DEFAULT_N_BARS = 100
INDICATOR_COLS = ['fast_ma', 'slow_ma', 'atr']   # TODO: cập nhật sau khi định nghĩa indicators


# =============================================================================
# 3. SYMBOLS  (TODO: thêm symbols cần trade)
# =============================================================================
SYMBOLS = {
    # Ví dụ:
    # "US30": {
    #     "symbol_id": 10,
    #     "label": "US30 (Dow Jones)",
    #     "x": 13.0,
    #     "session_hours_utc": [],
    #     "contract_value": 1.0,
    #     "point_size": 1.0,
    #     "spread_pts": 2.0,
    #     "commission_per_lot": 3.5,
    #     "slippage_pts": 0.5,
    #     "swap_long_per_lot_per_day": -3.5,
    #     "swap_short_per_lot_per_day": 1.2,
    # },
}


# =============================================================================
# 4. INDICATOR DEFAULTS
# =============================================================================
def get_indicator_params() -> dict:
    """Trả về bộ tham số chỉ báo mặc định cho MA Cross."""
    return {
        "FAST_MA":  STRATEGY["fast_ma"],
        "SLOW_MA":  STRATEGY["slow_ma"],
        "ATR_PERIOD": 14,
        "KTP":      STRATEGY["ktp"],
        "MIN_RR":   STRATEGY["min_rr"],
    }


# =============================================================================
# 5. PER-SYMBOL HELPERS
# =============================================================================
def get_symbol_params(sym_key: str) -> dict:
    """Trả về bộ tham số tối ưu hoá đầy đủ cho một symbol."""
    if sym_key not in SYMBOLS:
        raise KeyError(
            f"MA Cross symbol '{sym_key}' is not configured. "
            "Add it to strategies.ma_cross.strategy_config.SYMBOLS before backtesting."
        )
    sym = SYMBOLS[sym_key]
    p   = get_indicator_params()
    return {
        'ktp':                 sym.get('ktp', STRATEGY['ktp']),
        'x':                   sym['x'],
        'trailing_activation': sym.get('trailing_activation', STRATEGY['trailing_activation']),
        'session_hours_utc':   sym.get('session_hours_utc', []),
        'swap_long_per_lot_per_day':  sym.get('swap_long_per_lot_per_day', 0.0),
        'swap_short_per_lot_per_day': sym.get('swap_short_per_lot_per_day', 0.0),
    }
