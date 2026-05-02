# =============================================================================
# strategies/combo/parameters.py  —  Combo v2 strategy source-of-truth
# Version           : 1.0
# =============================================================================
# HƯỚNG DẪN QUẢN TRỊ NHANH
# Đây là file điều chỉnh tham số chiến lược chính — chỉnh ở đây trước tiên.
# config.py chỉ là compatibility wrapper cho import cũ.
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

# Canonical source of truth for all Combo strategy settings shared between:
#   - strategies.combo.signals
#   - strategies.combo.signals
#   - strategies.combo.signals
#   - strategies.combo.signals compatibility wrapper
#   - strategies.combo.scanner
#   - strategies.combo.symbol.*
#   - strategies.combo.portfolio.*
#   - notebooks / reports that analyze Combo
#
# HOW TO CUSTOMISE:
#   - Add/remove symbols in SYMBOLS dict
#   - Adjust x (breakout buffer in price points) per symbol
#   - Adjust session_hours_utc (list of UTC bar-open hours to scan;
#     empty list [] = scan all hours)
#   - Adjust indicator defaults in get_indicator_params()
# =============================================================================
from core_python.shared.market import audit_symbol_specs
from core_python.shared.market import (
    BROKER_PROFILES,
    DEFAULT_BROKER_PROFILE,
    DEFAULT_COSTS,
    get_broker_profile,
    get_cost_settings,
    get_symbol_config as get_shared_symbol_config,
)

from .universe import COMBO_SYMBOL_KEYS, get_combo_universe_metadata

__all__ = [
    "ACCOUNT_MODES",
    "BROKER_PROFILES",
    "COMBO_SYMBOL_PARAMS",
    "DEFAULT_BROKER_PROFILE",
    "DEFAULT_COSTS",
    "DEFAULT_N_BARS",
    "INDICATOR_COLS",
    "OPTIMIZATION",
    "SCANNER_DEFAULTS",
    "STRATEGY",
    "SYMBOLS",
    "TIMEFRAME",
    "TIMEFRAMES",
    "get_account_settings",
    "get_broker_profile",
    "get_combo_symbol_params",
    "get_cost_settings",
    "get_indicator_params",
    "get_symbol_config",
    "get_symbol_ktp",
    "get_symbol_params",
    "get_symbol_search_space",
    "summary",
    "validate_config",
]

# =============================================================================
# 1. STRATEGY IDENTITY (tham số nền của toàn chiến lược)
# =============================================================================
STRATEGY = {
    "name":    "Combo",
    "version": "v2",
    "ktp":     2.272,  # TP = kTP × ATR; default from the 5-step Fibonacci ladder
    "min_rr":  None,   # None = do not filter signals by R:R unless overridden
    # ── Backtest & FTMO risk management ───────────────────────────────────
    "risk_per_trade":       0.005,   # 0.5% equity per trade
    "ftmo_daily_limit":     0.05,    # 5% daily loss limit
    "ftmo_max_dd":          0.10,    # 10% max drawdown
    "trailing_activation":  1.0,     # Activate trailing SL at 1.0 × ATR profit
    "pending_ttl_bars":     3,       # Pending order TTL in bars
    "partial_tp_fraction":  0.5,     # Close 50% at fixed TP; trail remaining 50%
}


ACCOUNT_MODES = {
    "standard": {
        "label": "Standard",
        "account_model": "retail",
        "risk_per_trade": STRATEGY["risk_per_trade"],
        # Practical defaults for non-prop testing: effectively unbounded by FTMO rules.
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


# =============================================================================
# 1b. SCAN / BACKTEST DEFAULTS (mặc định chạy hệ thống)
# =============================================================================
TIMEFRAMES     = ['H2', 'H3', 'H4']        # Combo-supported primary timeframes
TIMEFRAME      = 'H4'                     # Default primary timeframe for all scans
DEFAULT_N_BARS = 100                       # Default number of bars to scan
INDICATOR_COLS = ['ma', 'atr', 'macd_h']   # Required indicator columns after warmup




OPTIMIZATION = {
    "symbol": {
        # kTP — chuỗi Fibonacci trong safe range [1.5, 3.5]
        "ktp_values":    [1.618, 2.0, 2.272, 2.618, 3.0],
        # None = RR filter off by default. Provide numeric values in overrides
        # when an optimization run should search an explicit R:R threshold.
        "min_rr_values": [None],
        # x fallback offset (dùng khi chưa có fill-rate analysis per-symbol)
        "x_offsets":     [-2.0, 0.0, 2.0],
        "max_bars":      80000,
        "score_column":  "sharpe",
    },
    "portfolio": {
        "top_k_per_symbol": 5,
        "score_column": "score",
    },
}


SCANNER_DEFAULTS = {
    "timeframe": TIMEFRAME,
    "n_bars": DEFAULT_N_BARS,
    "entry_line_bars": 8,
    "show_rejected": True,
    "show_ma": True,
    "show_entry_lines": True,
}


# =============================================================================
# 2. SYMBOLS — 11 instruments scanned on H4
#
#   symbol_id        : must match SymbolID in DWH.dbo.Symbol / config.py
#   label            : display name for dashboard and Telegram messages
#   x                : breakout buffer in price points
#                      entry = bar_high + x  (BUY)  /  bar_low - x  (SELL)
#                      (*) = placeholder — will be optimized via walk-forward
#   session_hours_utc: list of H4 bar-start UTC hours to scan for signals
#                      ([] = no filter, scan all hours)
# =============================================================================

COMBO_SYMBOL_PARAMS = {
    "US30": {
        "x": 10.0,
        "session_hours_utc": [],
    },
    "US500": {
        "x": 1.0,
        "session_hours_utc": [],
    },
    "US100": {
        "x": 5.0,
        "session_hours_utc": [],
    },
    "DE40": {
        "x": 5.0,
        "session_hours_utc": [],
    },
    "UK100": {
        "x": 5.0,
        "session_hours_utc": [],
    },
    "FR40": {
        "x": 5.0,
        "session_hours_utc": [],
    },
    "SP35": {
        "x": 5.0,
        "session_hours_utc": [],
    },
    "HK50": {
        "x": 15.0,
        "session_hours_utc": [],
    },
    "J225": {
        "x": 15.0,
        "session_hours_utc": [],
    },
    "GOLD": {
        "x": 0.5,
        "session_hours_utc": [],
    },
    "BTCUSD": {
        "x": 50.0,
        "session_hours_utc": [],
    },
}


def get_combo_symbol_params(sym_key: str) -> dict:
    """Return Combo-owned per-symbol strategy parameters without shared metadata."""
    if sym_key not in COMBO_SYMBOL_PARAMS:
        raise KeyError(f"Symbol '{sym_key}' not found. Available: {list(COMBO_SYMBOL_PARAMS)}")
    params = COMBO_SYMBOL_PARAMS[sym_key]
    return {
        "x": params["x"],
        "session_hours_utc": list(params.get("session_hours_utc", [])),
    }


def _build_combo_symbols() -> dict[str, dict]:
    """Build the public Combo symbol universe with Combo-owned strategy params."""
    metadata = get_combo_universe_metadata()
    return {
        symbol: {
            **metadata[symbol],
            **get_combo_symbol_params(symbol),
        }
        for symbol in COMBO_SYMBOL_KEYS
    }


SYMBOLS = _build_combo_symbols()


def get_symbol_config(sym_key: str, broker_profile: str | None = None) -> dict:
    """Return shared broker metadata overlaid with Combo-owned symbol params."""
    if sym_key not in COMBO_SYMBOL_PARAMS:
        raise KeyError(f"Symbol '{sym_key}' not found. Available: {list(COMBO_SYMBOL_PARAMS)}")
    return {
        **get_shared_symbol_config(sym_key, broker_profile=broker_profile),
        **get_combo_symbol_params(sym_key),
    }



# =============================================================================
# 3. INDICATOR DEFAULTS
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




def get_account_settings(mode: str = "standard", overrides: dict | None = None) -> dict:
    """Resolve account-mode specific capital/risk controls.

    Architecture note
    -----------------
    We model FTMO vs standard as configuration, not as separate engines. This
    keeps execution logic single-sourced in `shared.execution`.
    """
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
    # Keep legacy keys so old code paths still work.
    resolved["ftmo_daily_limit"] = resolved["daily_loss_limit"]
    resolved["ftmo_max_dd"] = resolved["max_drawdown_limit"]

    if overrides:
        resolved.update(overrides)
        if "daily_loss_limit" in overrides:
            resolved["ftmo_daily_limit"] = resolved["daily_loss_limit"]
        if "max_drawdown_limit" in overrides:
            resolved["ftmo_max_dd"] = resolved["max_drawdown_limit"]
    return resolved


# =============================================================================
# 4. PER-SYMBOL HELPERS
# =============================================================================
def get_symbol_ktp(sym_key: str) -> float:
    """Lấy kTP theo symbol: ưu tiên giá trị override của symbol, nếu không có thì dùng global."""
    return SYMBOLS.get(sym_key, {}).get('ktp', STRATEGY['ktp'])




def get_symbol_params(sym_key: str, broker_profile: str | None = None) -> dict:
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
    sym = get_symbol_config(sym_key, broker_profile=broker_profile)
    owned = get_combo_symbol_params(sym_key)
    p   = get_indicator_params()
    return {
        'symbol_id':           sym['symbol_id'],
        'label':               sym['label'],
        'group':               sym.get('group', ''),
        'broker_symbol':       sym.get('broker_symbol', sym_key),
        'broker_profile':      sym.get('broker_profile', ''),
        'broker_label':        sym.get('broker_label', ''),
        'broker_platform':     sym.get('broker_platform', ''),
        'spec_verified':       sym.get('spec_verified', False),
        'ktp':                 sym.get('ktp',                STRATEGY['ktp']),
        'x':                   owned['x'],
        'trailing_activation': sym.get('trailing_activation', STRATEGY['trailing_activation']),
        'ma_period':           sym.get('ma_period',           p['MA_PERIOD']),
        'session_hours_utc':   owned['session_hours_utc'],
        'contract_value':      sym.get('contract_value', 1.0),
        'point_size':          sym.get('point_size', 1.0),
        'spread_pts':          sym.get('spread_pts', 0.0),
        'commission_per_lot':  sym.get('commission_per_lot', DEFAULT_COSTS['commission_per_lot']),
        'slippage_pts':        sym.get('slippage_pts', DEFAULT_COSTS['slippage_pts']),
        'min_lot_size':        sym.get('min_lot_size', 0.01),
        'max_lot_size':        sym.get('max_lot_size', 100.0),
        'lot_step':            sym.get('lot_step', sym.get('min_lot_size', 0.01)),
        'swap_long_per_lot_per_day':  sym.get('swap_long_per_lot_per_day', 0.0),
        'swap_short_per_lot_per_day': sym.get('swap_short_per_lot_per_day', 0.0),
    }




def get_symbol_search_space(
    sym_key: str,
    overrides: dict | None = None,
    broker_profile: str | None = None,
) -> dict:
    """Build the per-symbol search space for the optimizer.

    ktp  : chuỗi Fibonacci cố định, không phụ thuộc vào giá trị hiện tại.
    x    : fallback offset quanh giá trị hiện tại; nếu symbol có x_search_space
           được set thì dùng đó (kết quả từ analyze_x_fill_rate).
    min_rr: mặc định [None] để tắt lọc RR; truyền override số nếu muốn lọc.
    """
    current = get_symbol_params(sym_key, broker_profile=broker_profile)
    base = OPTIMIZATION["symbol"]

    # x: ưu tiên x_search_space per-symbol nếu có, fallback về offset-based
    sym_raw = SYMBOLS.get(sym_key, {})
    if sym_raw.get("x_search_space"):
        x_values = sorted(float(v) for v in sym_raw["x_search_space"] if v > 0)
    else:
        x_values = sorted({
            round(max(0.1, current["x"] + offset), 2)
            for offset in base["x_offsets"]
        })

    search = {
        "ktp":    list(base["ktp_values"]),
        "x":      x_values,
        "min_rr": list(base["min_rr_values"]),
    }
    if overrides:
        search.update(overrides)
    return search


# =============================================================================
# 5. SUMMARY — human-readable overview of current config
# =============================================================================
def summary() -> str:
    """Xuất chuỗi tóm tắt cấu hình hiện tại để kiểm tra nhanh trước khi chạy scan/backtest."""
    p = get_indicator_params()
    lines = [
        f"Strategy     : {STRATEGY['name']} {STRATEGY['version']}",
        f"Symbols      : {', '.join(SYMBOLS.keys())}",
        f"MA period    : {p['MA_PERIOD']}",
        f"MACD         : ({p['MACD_FAST']}, {p['MACD_SLOW']}, {p['MACD_SIGNAL']})",
        f"ATR period   : {p['ATR_PERIOD']}",
        f"kTP          : {p['KTP']}",
        f"Min R:R      : {p['MIN_RR'] if p['MIN_RR'] is not None else 'off'}",
        f"Account modes : {', '.join(ACCOUNT_MODES.keys())}",
        f"Broker profiles: {', '.join(BROKER_PROFILES.keys())}",
        f"Default broker : {DEFAULT_BROKER_PROFILE}",
    ]
    return "\n".join(lines)


# =============================================================================
# 6. CONFIG AUDIT — báo cáo các giá trị placeholder cần điền
# =============================================================================
def validate_config(broker_profile: str | None = None) -> dict:
    """Kiểm tra config và báo cáo các trường chưa được điền đầy đủ.

    Dùng trước khi chạy backtest thật để tránh kết quả sai vì
    placeholder (swap=0, x quá nhỏ/lớn, lot bounds không hợp lệ).

    Returns
    -------
    dict gồm:
    - warnings : list[str] — các cảnh báo cần xem xét nhưng không chặn chạy
    - errors   : list[str] — các lỗi nghiêm trọng cần sửa trước khi tin kết quả
    - ok       : bool — True nếu không có errors
    """
    warnings: list[str] = []
    errors:   list[str] = []

    merged_symbols = {
        sym_key: get_symbol_params(sym_key, broker_profile=broker_profile)
        for sym_key in SYMBOLS
    }
    broker_audit = audit_symbol_specs(merged_symbols)
    warnings.extend(broker_audit["warnings"])
    errors.extend(broker_audit["errors"])

    for sym_key, sym in merged_symbols.items():
        # ── Swap values ─────────────────────────────────────────────────────
        swap_long  = sym.get("swap_long_per_lot_per_day", 0.0)
        swap_short = sym.get("swap_short_per_lot_per_day", 0.0)
        if swap_long == 0.0 and swap_short == 0.0:
            # Crypto (BTCUSD) có thể thực sự = 0; index thì không
            if sym.get("group") in ("US", "EU", "ASIA", "METAL"):
                warnings.append(
                    f"{sym_key}: swap_long/short đều = 0.0 — "
                    "cần verify từ broker (backtest sẽ bỏ qua swap cost)."
                )

        # ── Lot size bounds ─────────────────────────────────────────────────
        min_lot = sym.get("min_lot_size")
        max_lot = sym.get("max_lot_size")
        if min_lot is None:
            warnings.append(f"{sym_key}: thiếu min_lot_size — sẽ dùng default 0.01.")
        if max_lot is None:
            warnings.append(f"{sym_key}: thiếu max_lot_size — sẽ dùng default 100.0.")
        if min_lot and max_lot and min_lot >= max_lot:
            errors.append(
                f"{sym_key}: min_lot_size ({min_lot}) >= max_lot_size ({max_lot})."
            )
        lot_step = sym.get("lot_step")
        if lot_step and min_lot and lot_step > max_lot:
            errors.append(
                f"{sym_key}: lot_step ({lot_step}) > max_lot_size ({max_lot})."
            )

        # ── x (breakout buffer) ─────────────────────────────────────────────
        x = sym.get("x", 0)
        if x <= 0:
            errors.append(f"{sym_key}: x = {x} — phải > 0 để tạo pending order hợp lệ.")

        # ── contract_value ───────────────────────────────────────────────────
        cv = sym.get("contract_value", 0)
        if cv <= 0:
            errors.append(
                f"{sym_key}: contract_value = {cv} — lot sizing sẽ bị vô hiệu."
            )

    return {
        "warnings": warnings,
        "errors":   errors,
        "ok":       len(errors) == 0,
    }
