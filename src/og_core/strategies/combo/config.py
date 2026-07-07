"""
Tham số cấu hình và validation cho chiến lược Combo.

Mô tả:
    Định nghĩa tham số mặc định, giới hạn hợp lệ, và các hàm parse/validate
    dùng cho chiến lược Combo (MA + MACD Histogram + ATR breakout).

    Người dùng có thể override bất kỳ tham số nào qua UI hoặc CLI.
    normalize_params() đảm bảo tất cả giá trị nằm trong giới hạn an toàn.

Đầu ra:
    DEFAULT_PARAMS: dict tham số mặc định.
    PARAM_FIELDS: định nghĩa UI fields cho sidebar.
    normalize_params(overrides, symbol): dict tham số đã validate và merge.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from og_core.config import TF_MINUTES


# --- Tham số chỉ báo mặc định ---
MA_PERIOD = 20       # Chu kỳ Simple Moving Average
MACD_FAST = 5        # EMA nhanh của MACD
MACD_SLOW = 25       # EMA chậm của MACD
MACD_SIGNAL = 5      # EMA đường signal của MACD
ATR_PERIOD = 5       # Chu kỳ Average True Range (Wilder)
KTP = 2.272          # Hệ số nhân ATR để tính khoảng cách Take Profit
MIN_RR = None        # Legacy/report param; not a Combo V0 raw-signal condition
ENTRY_LINE_BARS = 2  # Số bar kéo dài đường Entry/SL/TP trên biểu đồ

# --- Toggle hiển thị trên biểu đồ ---
# HTF settings are retained for backward-compatible code paths, but hidden from
# the Combo V0 dashboard while the original lecture logic is being used.
HTF_TREND_ENABLED = False
HTF_TF = "D1"
HTF_TF_OPTIONS = ("H4", "H6", "H8", "D1", "W")
HTF_BARS = 300
HTF_FAST_EMA = 20
HTF_SLOW_EMA = 50
HTF_SLOPE_LOOKBACK = 3
HTF_SLOPE_MIN = 0.0
HTF_NEUTRAL_BLOCK = True
SHOW_HTF_TREND = True
SHOW_FILTERED_SIGNALS = True

SHOW_MA = True
SHOW_MACD = True
SHOW_ATR = True
SHOW_LEVELS = True

# Buffer X cho từng symbol (đơn vị: điểm).
# X được cộng vào đỉnh/đáy bar khi xác định Entry và SL.
# Giá trị nhỏ hơn cho symbol có spread hẹp, lớn hơn cho symbol biến động mạnh.
SYMBOL_X = {
    "US30": 10.0,
    "US500": 1.0,
    "US100": 5.0,
    "DE40": 5.0,
    "UK100": 5.0,
    "FR40": 5.0,
    "SP35": 5.0,
    "HK50": 15.0,
    "J225": 15.0,
    "GOLD": 0.5,
    "BTCUSD": 50.0,
}

# Giờ UTC được phép giao dịch cho từng symbol.
# Danh sách rỗng = giao dịch mọi giờ (không lọc theo session).
SESSION_HOURS_UTC = {symbol: [] for symbol in SYMBOL_X}

# Tham số mặc định gửi về frontend khi khởi tạo form.
# X=None vì giá trị thực lấy từ SYMBOL_X theo symbol được chọn.
DEFAULT_PARAMS: dict[str, Any] = {
    "MA_PERIOD": MA_PERIOD,
    "MACD_FAST": MACD_FAST,
    "MACD_SLOW": MACD_SLOW,
    "MACD_SIGNAL": MACD_SIGNAL,
    "ATR_PERIOD": ATR_PERIOD,
    "KTP": KTP,
    "MIN_RR": MIN_RR,
    "X": None,
    "SESSION_HOURS_UTC": [],
    "ENTRY_LINE_BARS": 2,
    "HTF_TREND_ENABLED": HTF_TREND_ENABLED,
    "HTF_TF": HTF_TF,
    "HTF_BARS": HTF_BARS,
    "HTF_FAST_EMA": HTF_FAST_EMA,
    "HTF_SLOW_EMA": HTF_SLOW_EMA,
    "HTF_SLOPE_LOOKBACK": HTF_SLOPE_LOOKBACK,
    "HTF_SLOPE_MIN": HTF_SLOPE_MIN,
    "HTF_NEUTRAL_BLOCK": HTF_NEUTRAL_BLOCK,
    "SHOW_HTF_TREND": SHOW_HTF_TREND,
    "SHOW_FILTERED_SIGNALS": SHOW_FILTERED_SIGNALS,
    "SHOW_MA": SHOW_MA,
    "SHOW_MACD": SHOW_MACD,
    "SHOW_ATR": SHOW_ATR,
    "SHOW_LEVELS": SHOW_LEVELS,
}

# Định nghĩa UI fields cho sidebar — frontend dùng để render input controls.
PARAM_FIELDS: list[dict[str, Any]] = [
    {"key": "MA_PERIOD", "label": "MA", "type": "number", "min": 2, "max": 500, "step": 1},
    {"key": "MACD_FAST", "label": "MACD Fast", "type": "number", "min": 1, "max": 200, "step": 1},
    {"key": "MACD_SLOW", "label": "MACD Slow", "type": "number", "min": 2, "max": 300, "step": 1},
    {"key": "MACD_SIGNAL", "label": "MACD Signal", "type": "number", "min": 1, "max": 200, "step": 1},
    {"key": "ATR_PERIOD", "label": "ATR", "type": "number", "min": 2, "max": 200, "step": 1},
    {"key": "X", "label": "X Buffer", "type": "number", "min": 0, "max": 1_000_000, "step": 0.01},
    {"key": "KTP", "label": "KTP", "type": "number", "min": 0.1, "max": 20, "step": 0.001},
    {"key": "SESSION_HOURS_UTC", "label": "UTC Hours", "type": "text"},
    {"key": "ENTRY_LINE_BARS", "label": "Level Bars", "type": "number", "min": 1, "max": 300, "step": 1},
    {"key": "SHOW_MA", "label": "Show MA", "type": "bool"},
    {"key": "SHOW_MACD", "label": "Show MACD", "type": "bool"},
    {"key": "SHOW_ATR", "label": "Show ATR", "type": "bool"},
    {"key": "SHOW_LEVELS", "label": "Show Levels", "type": "bool"},
]


def _to_bool(value: object, default: bool) -> bool:
    """Parse bool từ string/int; trả về default nếu None."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value: object, default: int, min_value: int, max_value: int) -> int:
    """Parse int, clamp vào [min_value, max_value], fallback về default nếu lỗi."""
    try:
        parsed = int(float(value)) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(parsed, max_value))


def _to_float(value: object, default: float, min_value: float, max_value: float) -> float:
    """Parse float, clamp vào [min_value, max_value], fallback về default nếu lỗi."""
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(parsed, max_value))


def _to_choice(value: object, default: str, allowed: Sequence[str]) -> str:
    """Parse an uppercase choice and reject unsupported values."""
    candidate = str(value or default).strip().upper()
    allowed_values = tuple(str(item).upper() for item in allowed)
    if candidate not in set(allowed_values):
        raise ValueError(f"Unsupported timeframe '{candidate}'. Allowed: {', '.join(allowed_values)}")
    if candidate not in TF_MINUTES:
        raise ValueError(f"Unsupported timeframe '{candidate}'.")
    return candidate


def _to_optional_float(
    value: object,
    default: float | None,
    min_value: float,
    max_value: float,
) -> float | None:
    """
    Parse float tùy chọn — trả về None nếu value là None/""/"none"/"null"/"off".

    Dùng cho legacy MIN_RR/report params. MIN_RR không gate raw signal Combo V0.
    """
    if value is None:
        return default
    raw = str(value).strip().lower()
    if raw in {"", "none", "null", "off"}:
        return None
    return _to_float(value, float(default if default is not None else min_value), min_value, max_value)


def _parse_session_hours(value: object, default: list[int]) -> list[int]:
    """
    Parse danh sách giờ UTC từ chuỗi hoặc list.

    Input có thể là:
        - List/tuple/set số nguyên: [9, 10, 14, 15]
        - Chuỗi phân cách bằng dấu phẩy, chấm phẩy, hoặc space: "9,10,14 15"
        - Chuỗi rỗng: trả về []

    Args:
        value: Giá trị input từ user.
        default: Giá trị mặc định nếu value là None.

    Returns:
        List giờ UTC đã sắp xếp tăng dần, không trùng.

    Raises:
        ValueError: Nếu có giờ ngoài phạm vi 0-23.
    """
    if value is None:
        return list(default)
    if isinstance(value, (list, tuple, set)):
        parts = value
    else:
        raw = str(value).strip()
        if raw == "":
            return []
        parts = raw.replace(";", ",").replace(" ", ",").split(",")

    hours: set[int] = set()
    for part in parts:
        if str(part).strip() == "":
            continue
        hour = int(part)
        if hour < 0 or hour > 23:
            raise ValueError("SESSION_HOURS_UTC must contain UTC hours in range 0..23")
        hours.add(hour)
    return sorted(hours)


def get_indicator_params() -> dict:
    """
    Trả về tham số chỉ báo mặc định dùng cho add_combo_indicators().

    Returns:
        Dict gồm MA_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL, ATR_PERIOD, KTP, MIN_RR.
        MIN_RR được giữ để tương thích, không phải điều kiện raw signal Combo V0.
    """
    return {
        "MA_PERIOD": MA_PERIOD,
        "MACD_FAST": MACD_FAST,
        "MACD_SLOW": MACD_SLOW,
        "MACD_SIGNAL": MACD_SIGNAL,
        "ATR_PERIOD": ATR_PERIOD,
        "KTP": KTP,
        "MIN_RR": MIN_RR,
    }


def get_symbol_params(symbol: str | None) -> dict:
    """
    Trả về tham số riêng theo symbol: buffer X và giờ giao dịch.

    Symbol không có trong SYMBOL_X sẽ dùng X = 0.0.

    Args:
        symbol: Mã TradingView (không phân biệt hoa/thường).

    Returns:
        Dict: {"x": float, "session_hours_utc": list[int]}.
    """
    key = str(symbol or "").strip().upper()
    return {
        "x": float(SYMBOL_X.get(key, 0.0)),
        "session_hours_utc": list(SESSION_HOURS_UTC.get(key, [])),
    }


def normalize_params(overrides: dict[str, Any] | None = None, symbol: str | None = None) -> dict[str, Any]:
    """
    Merge tham số mặc định, tham số theo symbol và override của người dùng thành dict hợp lệ.

    Thứ tự ưu tiên: DEFAULT_PARAMS < symbol defaults < overrides.
    Tất cả giá trị được validate và clamp vào giới hạn an toàn.

    Args:
        overrides: Dict tham số người dùng chỉ định (từ URL query hoặc CLI).
                   Có thể là None — khi đó chỉ dùng defaults.
        symbol: Mã symbol để lấy X và session_hours mặc định.

    Returns:
        Dict tham số đã validate đầy đủ — an toàn để truyền vào pipeline.
    """
    raw = {**DEFAULT_PARAMS, **(overrides or {})}
    symbol_params = get_symbol_params(symbol)
    htf_fast = _to_int(raw.get("HTF_FAST_EMA"), HTF_FAST_EMA, 1, 500)
    htf_slow = _to_int(raw.get("HTF_SLOW_EMA"), HTF_SLOW_EMA, 2, 1000)
    if htf_fast >= htf_slow:
        raise ValueError("HTF_FAST_EMA must be smaller than HTF_SLOW_EMA")
    return {
        "MA_PERIOD": _to_int(raw.get("MA_PERIOD"), MA_PERIOD, 2, 500),
        "MACD_FAST": _to_int(raw.get("MACD_FAST"), MACD_FAST, 1, 200),
        "MACD_SLOW": _to_int(raw.get("MACD_SLOW"), MACD_SLOW, 2, 300),
        "MACD_SIGNAL": _to_int(raw.get("MACD_SIGNAL"), MACD_SIGNAL, 1, 200),
        "ATR_PERIOD": _to_int(raw.get("ATR_PERIOD"), ATR_PERIOD, 2, 200),
        "KTP": _to_float(raw.get("KTP"), KTP, 0.1, 20.0),
        "MIN_RR": _to_optional_float(raw.get("MIN_RR"), MIN_RR, 0.0, 20.0),
        # X: override > symbol default (từ SYMBOL_X) > 0.0
        "X": _to_float(raw.get("X"), symbol_params["x"], 0.0, 1_000_000.0),
        "SESSION_HOURS_UTC": _parse_session_hours(
            raw.get("SESSION_HOURS_UTC"),
            symbol_params["session_hours_utc"],
        ),
        "ENTRY_LINE_BARS": _to_int(raw.get("ENTRY_LINE_BARS"), ENTRY_LINE_BARS, 1, 300),
        "HTF_TREND_ENABLED": _to_bool(raw.get("HTF_TREND_ENABLED"), HTF_TREND_ENABLED),
        "HTF_TF": _to_choice(raw.get("HTF_TF"), HTF_TF, HTF_TF_OPTIONS),
        "HTF_BARS": _to_int(raw.get("HTF_BARS"), HTF_BARS, 50, 20000),
        "HTF_FAST_EMA": htf_fast,
        "HTF_SLOW_EMA": htf_slow,
        "HTF_SLOPE_LOOKBACK": _to_int(raw.get("HTF_SLOPE_LOOKBACK"), HTF_SLOPE_LOOKBACK, 1, 100),
        "HTF_SLOPE_MIN": _to_float(raw.get("HTF_SLOPE_MIN"), HTF_SLOPE_MIN, 0.0, 1_000_000.0),
        "HTF_NEUTRAL_BLOCK": _to_bool(raw.get("HTF_NEUTRAL_BLOCK"), HTF_NEUTRAL_BLOCK),
        "SHOW_HTF_TREND": _to_bool(raw.get("SHOW_HTF_TREND"), SHOW_HTF_TREND),
        "SHOW_FILTERED_SIGNALS": _to_bool(raw.get("SHOW_FILTERED_SIGNALS"), SHOW_FILTERED_SIGNALS),
        "SHOW_MA": _to_bool(raw.get("SHOW_MA"), SHOW_MA),
        "SHOW_MACD": _to_bool(raw.get("SHOW_MACD"), SHOW_MACD),
        "SHOW_ATR": _to_bool(raw.get("SHOW_ATR"), SHOW_ATR),
        "SHOW_LEVELS": _to_bool(raw.get("SHOW_LEVELS"), SHOW_LEVELS),
    }
