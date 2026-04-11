# =============================================================================
# strategy/strategy_config.py  —  Combo v2 Strategy Parameters
# Version           : 1.0
# =============================================================================
# HƯỚNG DẪN QUẢN TRỊ NHANH
# Đây là file điều chỉnh tham số chiến lược chính — chỉnh ở đây trước tiên.
#
# Các thông số quan trọng nhất:
# - STRATEGY["ktp"]            : hệ số nhân khoảng cách TP (Take Profit)
# - STRATEGY["min_rr"]         : ngưỡng lọc R:R tối thiểu
# - STRATEGY["risk_per_trade"] : tỷ lệ rủi ro mỗi lệnh
# - SYMBOLS[*]["x"]            : breakout buffer theo từng tài sản (4 chỉ số)
# - SYMBOLS[*]["session_hours_utc"] : lọc giờ trading session
#
# Nguyên tắc thực hành:
# - Ưu tiên điều chỉnh ở đây trước khi đụng vào code logic scanner/backtest.
# - Mỗi lần chỉnh chỉ 1-2 tham số, ghi lại trước/sau để dễ audit KPI.

# Single source of truth for all strategy settings shared between:
#   07_backtest.ipynb          — portfolio backtest
#   08_wf_optimizer.ipynb      — walk-forward parameter optimization
#   09_scanner_research.ipynb  — interactive signal research
#   10_signal_dashboard.py     — Streamlit signal dashboard
#   scan_pipeline.py           — scan helpers
#   backtest_engine.py         — backtest / optimize helpers
#
# HOW TO CUSTOMISE:
#   - Add/remove symbols in SYMBOLS dict
#   - Adjust x (breakout buffer in price points) per symbol
#   - Adjust session_hours_utc (list of UTC bar-open hours to scan;
#     empty list [] = scan all hours)
#   - Set us_macro_filter=True for indices correlated with US30
#   - Adjust indicator defaults in get_indicator_params()
# =============================================================================

# =============================================================================
# 1. STRATEGY IDENTITY (tham số nền của toàn chiến lược)
# =============================================================================
STRATEGY = {
    "name":    "Combo",
    "version": "v2",
    "ktp":     2.3,    # TP = kTP × ATR  (default — can be overridden per symbol)
    "min_rr":  1.25,   # Minimum R:R ratio to pass
    # ── Backtest & FTMO risk management ───────────────────────────────────
    "risk_per_trade":       0.005,   # 0.5% equity per trade
    "ftmo_daily_limit":     0.05,    # 5% daily loss limit
    "ftmo_max_dd":          0.10,    # 10% max drawdown
    "trailing_activation":  1.0,     # Activate trailing SL at 1.0 × ATR profit
    "pending_ttl_bars":     3,       # Pending order TTL in bars
    "partial_tp_fraction":  0.5,     # Close 50% at fixed TP; trail remaining 50%
}


# =============================================================================
# 1b. SCAN / BACKTEST DEFAULTS (mặc định chạy hệ thống)
# =============================================================================
TIMEFRAME      = 'H4'                     # Primary timeframe for all scans
DEFAULT_N_BARS = 100                       # Default number of bars to scan
INDICATOR_COLS = ['ma', 'atr', 'macd_h']   # Required indicator columns after warmup

DEFAULT_COSTS = {
    "slippage_pts":       2,       # Points slippage at fill
    "commission_per_lot": 3.5,     # USD per lot per side
}


# =============================================================================
# 2. SYMBOLS — 4 indices scanned on H4: US30, UK100, HK50, J225
#
#   symbol_id        : must match SymbolID in DWH.Dim_Symbol / config.py
#   label            : display name for dashboard and Telegram messages
#   x                : breakout buffer in price points
#                      entry = bar_high + x  (BUY)  /  bar_low - x  (SELL)
#                      x < 2 → displayed with 1 decimal in _fmt_num()
#   session_hours_utc: list of H4 bar-start UTC hours to scan for signals
#                      ([] = no filter, scan all hours)
#   us_macro_filter  : True = only take BUY when US30 is above its MA,
#                      only take SELL when US30 is below its MA
# =============================================================================
SYMBOLS = {
    # ── US Markets ───────────────────────────────────────────────────────────
    "US30": {
        "symbol_id":         10,
        "label":             "US30 (Dow Jones)",
        "x":                 13.0,
        "ktp": 2.8,
        "trailing_activation": 1.25,
        "ma_period": 25,
        "session_hours_utc": [],               # Session filter disabled — all H4 bars active
        "us_macro_filter":   False,          # US30 IS the macro reference
        "group":             "US",
        "contract_value":    1.0,
        "point_size":        1.0,
        "spread_pts":        2.0,
        "commission_per_lot": 3.5,
        "slippage_pts":      0.5,
        "swap_long_per_lot_per_day":  -3.5,
        "swap_short_per_lot_per_day": 1.2,
    },
    # ── European Markets ─────────────────────────────────────────────────────
    "UK100": {
        "symbol_id":         7,
        "label":             "UK100 (FTSE 100)",
        "x":                 3.0,
        "ktp": 1.8,
        "trailing_activation": 0.5,
        "ma_period": 15,
        "session_hours_utc": [],               # Session filter disabled — all H4 bars active
        "us_macro_filter":   False,
        "group":             "EU",
        "contract_value":    1.0,
        "point_size":        1.0,
        "spread_pts":        1.0,
        "commission_per_lot": 3.5,
        "slippage_pts":      0.3,
        "swap_long_per_lot_per_day":  -2.8,
        "swap_short_per_lot_per_day": 0.9,
    },
    # ── Asian Markets ────────────────────────────────────────────────────────
    "HK50": {
        "symbol_id":         4,
        "label":             "HK50 (Hang Seng 50)",
        "x":                 8.0,
        "ktp": 2.2,
        "trailing_activation": 1.5,
        "ma_period": 25,
        "session_hours_utc": [],               # Session filter disabled — all H4 bars active
        "us_macro_filter":   False,
        "group":             "ASIA",
        "contract_value":    1.0,
        "point_size":        1.0,
        "spread_pts":        5.0,
        "commission_per_lot": 3.5,
        "slippage_pts":      1.0,
        "swap_long_per_lot_per_day":  -4.2,
        "swap_short_per_lot_per_day": 1.5,
    },
    "J225": {
        "symbol_id":         5,
        "label":             "J225 (Nikkei 225)",
        "x":                 8.0,
        "ktp": 1.8,
        "trailing_activation": 0.5,
        "ma_period": 15,
        "session_hours_utc": [],               # Session filter disabled — all H4 bars active
        "us_macro_filter":   False,
        "group":             "ASIA",
        "contract_value":    1.0,
        "point_size":        1.0,
        "spread_pts":        8.0,
        "commission_per_lot": 3.5,
        "slippage_pts":      1.5,
        "swap_long_per_lot_per_day":  0.8,
        "swap_short_per_lot_per_day": -2.1,
    },
}


# =============================================================================
# 3. US30 REFERENCE — mốc tham chiếu cho macro trend filter
# =============================================================================
US30_KEY       = "US30"            # key in SYMBOLS dict
US30_SYMBOL_ID = 10                # DWH.Dim_Symbol SymbolID for US30


# =============================================================================
# 4. SYMBOLS THAT USE THE US30 MACRO FILTER
#    Auto-derived from SYMBOLS dict — do not edit manually.
# =============================================================================
US_FILTERED = [k for k, v in SYMBOLS.items() if v.get("us_macro_filter")]


# =============================================================================
# 5. INDICATOR DEFAULTS
#    get_indicator_params() returns a dict consumed by add_indicators(),
#    scan_signals(), and the dashboard sidebar defaults.
#    Dashboard sidebar sliders let users override these at runtime.
# =============================================================================
def get_indicator_params() -> dict:
    """
    Trả về bộ tham số chỉ báo mặc định cho Combo v2.

    Đây là nguồn tham số mặc định cho scanner, dashboard và backtest.
    Khi cần tinh chỉnh độ nhạy tín hiệu, chỉnh tại đây trước.
    """
    return {
        # Trend MA (Simple Moving Average)
        "MA_PERIOD":    20,

        # MACD (Exponential Moving Averages)
        "MACD_FAST":    5,
        "MACD_SLOW":    25,
        "MACD_SIGNAL":  5,

        # ATR (Average True Range) — used for TP distance and R:R calculation
        "ATR_PERIOD":   5,

        # TP = KTP × ATR above/below entry price  — single source: STRATEGY dict
        "KTP":          STRATEGY["ktp"],

        # Minimum Risk:Reward ratio for a signal to "pass" and be alerted
        "MIN_RR":       STRATEGY["min_rr"],
    }


# =============================================================================
# 6. PER-SYMBOL HELPERS
# =============================================================================
def get_symbol_ktp(sym_key: str) -> float:
    """Lấy kTP theo symbol: ưu tiên giá trị override của symbol, nếu không có thì dùng global."""
    return SYMBOLS.get(sym_key, {}).get('ktp', STRATEGY['ktp'])


def calc_dynamic_slippage(base_slippage: float, atr: float, close: float,
                          k: float = 50) -> float:
    """Tính slippage động theo biến động ATR tương đối.

    Parameters
    ----------
    base_slippage : float
        Slippage nền (point) dùng khi biến động ở mức bình thường.
    atr : float
        Giá trị ATR tại thời điểm khớp lệnh.
    close : float
        Giá đóng cửa hiện tại, dùng để chuẩn hóa ATR theo tỷ lệ ATR/close.
    k : float, default=50
        Hệ số khuếch đại cho thành phần biến động.

    Returns
    -------
    float
        Slippage động theo công thức:
        base_slippage * (1 + k * clip(atr / close, 0.0005, 0.01)).
    """
    if close is None or close == 0:
        return float(base_slippage)

    vol_ratio = atr / close
    vol_ratio = min(max(vol_ratio, 0.0005), 0.01)
    return float(base_slippage) * (1 + k * vol_ratio)


def get_symbol_params(sym_key: str) -> dict:
    """
    Trả về bộ tham số tối ưu hoá đầy đủ cho một symbol.

    Quy tắc ưu tiên:
    - Nếu symbol có override riêng thì dùng override.
    - Nếu không, fallback về STRATEGY hoặc indicator defaults.

    Keys returned:
        ktp                 — hệ số TP theo ATR
        x                   — breakout buffer theo point
        trailing_activation — ngưỡng kích hoạt trailing
        ma_period           — chu kỳ MA xu hướng
        swap_long_per_lot_per_day  — phí/credit swap cho lệnh LONG theo mỗi lot mỗi ngày
        swap_short_per_lot_per_day — phí/credit swap cho lệnh SHORT theo mỗi lot mỗi ngày
    """
    sym = SYMBOLS.get(sym_key, {})
    p   = get_indicator_params()
    return {
        'ktp':                 sym.get('ktp',                STRATEGY['ktp']),
        'x':                   sym['x'],
        'trailing_activation': sym.get('trailing_activation', STRATEGY['trailing_activation']),
        'ma_period':           sym.get('ma_period',           p['MA_PERIOD']),
        'session_hours_utc':   sym.get('session_hours_utc',  []),
        'swap_long_per_lot_per_day':  sym.get('swap_long_per_lot_per_day', 0.0),
        'swap_short_per_lot_per_day': sym.get('swap_short_per_lot_per_day', 0.0),
    }


# =============================================================================
# 7. SUMMARY — human-readable overview of current config
# =============================================================================
def summary() -> str:
    """Xuất chuỗi tóm tắt cấu hình hiện tại để kiểm tra nhanh trước khi chạy scan/backtest."""
    p = get_indicator_params()
    lines = [
        f"Strategy     : {STRATEGY['name']} {STRATEGY['version']}",
        f"Symbols      : {', '.join(SYMBOLS.keys())}",
        f"US30 filter  : {', '.join(US_FILTERED) if US_FILTERED else 'none'}",
        f"MA period    : {p['MA_PERIOD']}",
        f"MACD         : ({p['MACD_FAST']}, {p['MACD_SLOW']}, {p['MACD_SIGNAL']})",
        f"ATR period   : {p['ATR_PERIOD']}",
        f"kTP          : {p['KTP']}",
        f"Min R:R      : {p['MIN_RR']}",
    ]
    return "\n".join(lines)
