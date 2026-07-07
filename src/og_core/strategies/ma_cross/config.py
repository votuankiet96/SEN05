"""
Tham số cấu hình và validation cho chiến lược MA Cross.

Mô tả:
    Chiến lược MA Cross phát tín hiệu khi đường MA nhanh cắt qua đường MA chậm.
    Entry giả định ở giá mở cửa bar tiếp theo (thay vì breakout như Combo).

    normalize_params() đảm bảo FAST_MA < SLOW_MA và tất cả giá trị hợp lệ.

Đầu ra:
    DEFAULT_PARAMS: dict tham số mặc định.
    PARAM_FIELDS: định nghĩa UI fields cho sidebar.
    normalize_params(overrides, symbol): dict tham số đã validate.
"""

from __future__ import annotations

from typing import Any


# --- Tham số chỉ báo mặc định ---
MA_TYPE = "sma"         # Loại MA: "sma" (simple) hoặc "ema" (exponential)
FAST_MA = 10            # Chu kỳ MA nhanh
SLOW_MA = 20            # Chu kỳ MA chậm (phải > FAST_MA)
ATR_PERIOD = 14         # Chu kỳ ATR để tính SL/TP
ATR_STOP_MULT = 2.0     # Hệ số nhân ATR cho Stop Loss (SL = Entry ± ATR_STOP_MULT × ATR)
ATR_TP_MULT = 2.0       # Hệ số nhân ATR cho Take Profit (0 = không đặt TP)
SPREAD_PTS = 0.0        # Spread (điểm) — thêm vào entry price khi tính chi phí
SLIPPAGE_PTS = 0.0      # Slippage (điểm) — thêm vào entry price khi tính chi phí
SESSION_HOURS_UTC: list[int] = []  # Giờ UTC cho phép (rỗng = tất cả giờ)
ENTRY_LINE_BARS = 2     # Số bar kéo dài đường Entry/SL/TP trên biểu đồ

# --- Toggle hiển thị trên biểu đồ ---
SHOW_MA = True
SHOW_ATR = True
SHOW_LEVELS = True

# Tham số mặc định gửi về frontend khi khởi tạo form.
DEFAULT_PARAMS: dict[str, Any] = {
    "MA_TYPE": MA_TYPE,
    "FAST_MA": FAST_MA,
    "SLOW_MA": SLOW_MA,
    "ATR_PERIOD": ATR_PERIOD,
    "ATR_STOP_MULT": ATR_STOP_MULT,
    "ATR_TP_MULT": ATR_TP_MULT,
    "SPREAD_PTS": SPREAD_PTS,
    "SLIPPAGE_PTS": SLIPPAGE_PTS,
    "SESSION_HOURS_UTC": [],
    "ENTRY_LINE_BARS": 2,
    "SHOW_MA": SHOW_MA,
    "SHOW_ATR": SHOW_ATR,
    "SHOW_LEVELS": SHOW_LEVELS,
}

# Định nghĩa UI fields cho sidebar — frontend dùng để render input controls.
PARAM_FIELDS: list[dict[str, Any]] = [
    {"key": "MA_TYPE", "label": "MA Type", "type": "select", "options": ["sma", "ema"]},
    {"key": "FAST_MA", "label": "Fast MA", "type": "number", "min": 1, "max": 300, "step": 1},
    {"key": "SLOW_MA", "label": "Slow MA", "type": "number", "min": 2, "max": 500, "step": 1},
    {"key": "ATR_PERIOD", "label": "ATR", "type": "number", "min": 2, "max": 300, "step": 1},
    {"key": "ATR_STOP_MULT", "label": "SL ATR", "type": "number", "min": 0.1, "max": 20, "step": 0.1},
    {"key": "ATR_TP_MULT", "label": "TP ATR", "type": "number", "min": 0, "max": 20, "step": 0.1},
    {"key": "SPREAD_PTS", "label": "Spread", "type": "number", "min": 0, "max": 1_000_000, "step": 0.01},
    {"key": "SLIPPAGE_PTS", "label": "Slippage", "type": "number", "min": 0, "max": 1_000_000, "step": 0.01},
    {"key": "SESSION_HOURS_UTC", "label": "UTC Hours", "type": "text"},
    {"key": "ENTRY_LINE_BARS", "label": "Level Bars", "type": "number", "min": 1, "max": 300, "step": 1},
    {"key": "SHOW_MA", "label": "Show MA", "type": "bool"},
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


def _parse_session_hours(value: object, default: list[int]) -> list[int]:
    """
    Parse danh sách giờ UTC từ chuỗi hoặc list.

    Input có thể là list số nguyên hoặc chuỗi phân cách bằng dấu phẩy/chấm phẩy/space.
    Chuỗi rỗng trả về [].

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
    Trả về tham số chỉ báo mặc định dùng cho add_ma_cross_indicators().

    Returns:
        Dict gồm MA_TYPE, FAST_MA, SLOW_MA, ATR_PERIOD.
    """
    return {
        "MA_TYPE": MA_TYPE,
        "FAST_MA": FAST_MA,
        "SLOW_MA": SLOW_MA,
        "ATR_PERIOD": ATR_PERIOD,
    }


def normalize_params(overrides: dict[str, Any] | None = None, symbol: str | None = None) -> dict[str, Any]:
    """
    Merge tham số mặc định và override của người dùng thành dict hợp lệ.

    Kiểm tra thêm: FAST_MA phải nhỏ hơn SLOW_MA (raise ValueError nếu vi phạm).
    MA Cross không có tham số theo symbol (symbol được bỏ qua).

    Args:
        overrides: Dict tham số người dùng chỉ định. Có thể là None.
        symbol: Không sử dụng — chỉ để thống nhất interface với StrategySpec.

    Returns:
        Dict tham số đã validate đầy đủ — an toàn để truyền vào pipeline.

    Raises:
        ValueError: Nếu MA_TYPE không hợp lệ hoặc FAST_MA >= SLOW_MA.
    """
    _ = symbol  # MA Cross không có tham số riêng theo symbol
    raw = {**DEFAULT_PARAMS, **(overrides or {})}
    ma_type = str(raw.get("MA_TYPE", MA_TYPE)).lower().strip()
    if ma_type not in {"sma", "ema"}:
        raise ValueError("MA_TYPE must be 'sma' or 'ema'")

    fast = _to_int(raw.get("FAST_MA"), FAST_MA, 1, 300)
    slow = _to_int(raw.get("SLOW_MA"), SLOW_MA, 2, 500)
    # Đảm bảo fast < slow — điều kiện bắt buộc cho crossover có ý nghĩa
    if fast >= slow:
        raise ValueError("FAST_MA must be smaller than SLOW_MA")

    return {
        "MA_TYPE": ma_type,
        "FAST_MA": fast,
        "SLOW_MA": slow,
        "ATR_PERIOD": _to_int(raw.get("ATR_PERIOD"), ATR_PERIOD, 2, 300),
        "ATR_STOP_MULT": _to_float(raw.get("ATR_STOP_MULT"), ATR_STOP_MULT, 0.1, 20.0),
        "ATR_TP_MULT": _to_float(raw.get("ATR_TP_MULT"), ATR_TP_MULT, 0.0, 20.0),
        "SPREAD_PTS": _to_float(raw.get("SPREAD_PTS"), SPREAD_PTS, 0.0, 1_000_000.0),
        "SLIPPAGE_PTS": _to_float(raw.get("SLIPPAGE_PTS"), SLIPPAGE_PTS, 0.0, 1_000_000.0),
        "SESSION_HOURS_UTC": _parse_session_hours(raw.get("SESSION_HOURS_UTC"), SESSION_HOURS_UTC),
        "ENTRY_LINE_BARS": _to_int(raw.get("ENTRY_LINE_BARS"), ENTRY_LINE_BARS, 1, 300),
        "SHOW_MA": _to_bool(raw.get("SHOW_MA"), SHOW_MA),
        "SHOW_ATR": _to_bool(raw.get("SHOW_ATR"), SHOW_ATR),
        "SHOW_LEVELS": _to_bool(raw.get("SHOW_LEVELS"), SHOW_LEVELS),
    }
