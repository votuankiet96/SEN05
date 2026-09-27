"""
Tham số chiến lược + sổ đăng ký nối pipeline cho order_gateway.

Mô tả:
    File này KHÔNG đọc dữ liệu và KHÔNG tính chỉ báo. Nó CHỈ ĐỌC + VALIDATE
    tham số, không tự quyết định giá trị, và không giữ giá trị mặc định nào
    trùng với og_config.yaml:
    1. Tham số của từng chiến lược (Combo, MA Cross) — bắt buộc có trong
       og_config.yaml (mục `strategies:`), đây là khu vực operator chỉnh trực
       tiếp (X buffer entry theo symbol, chu kỳ chỉ báo, session hours...).
       Thiếu bất kỳ key nào sẽ raise lỗi rõ ràng ngay khi import — không có
       fallback âm thầm nào trong code. PARAM_FIELDS (giới hạn min/max cho
       validate input, không phải giá trị vận hành) + normalize_*_params()
       merge+validate vẫn là code. strategies/<tên>.py và levels.py đều KHÔNG
       giữ tham số nào — chỉ nhận dict params đã hoàn chỉnh từ đây.
    2. Sổ đăng ký chiến lược (StrategySpec/STRATEGIES/run_strategy) — nối
       indicator.py (chỉ báo) + strategies/<tên>.py (tín hiệu BUY/SELL) +
       levels.py (entry/SL/TP) thành 1 pipeline hoàn chỉnh, tra theo key
       ("combo"/"ma_cross").

    order_gateway KHÔNG có trend filter: bộ lọc xu hướng KNN chỉ tồn tại ở
    strategy_lab (nghiên cứu/backtest). Luồng live chạy 1 timeframe duy nhất,
    không có tham chiếu khung lớn.

Đầu ra:
    normalize_combo_params(overrides, symbol, tf), normalize_ma_cross_params(...).
    STRATEGIES: dict[str, StrategySpec] — tra cứu theo key.
    get_strategy(key): StrategySpec hoặc raise KeyError.
    run_strategy(key, symbol, tf, bars, overrides): chạy trọn pipeline 1
        chiến lược (thứ tự xem StrategySpec.Invariant bên dưới).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from order_gateway.src.config import CONFIG, require
from order_gateway.src.indicator import (
    add_combo_indicators,
    add_knn_trend_indicators,
    add_ma_cross_indicators,
    merge_trend_reference,
)
from order_gateway.src.levels import (
    KSL_LEVELS,
    KTP_LEVELS,
    add_combo_levels,
    add_ma_cross_levels,
    decode_level,
)
from order_gateway.src.strategies.combo import detect_combo_signals
from order_gateway.src.strategies.ma_cross import detect_ma_cross_signals
from order_gateway.src.strategies.trend import knn_trend_indicator_params

_STRATEGIES_CFG: dict[str, Any] = require(CONFIG, "strategies", "strategies")
_COMBO_CFG: dict[str, Any] = require(_STRATEGIES_CFG, "combo", "strategies.combo")
_MA_CROSS_CFG: dict[str, Any] = require(_STRATEGIES_CFG, "ma_cross", "strategies.ma_cross")


# =============================================================================
# 1. Bảng KSL/KTP theo (strategy, symbol, timeframe) — order_gateway/src/ksl_ktp.csv.
#
#    Đây là kết quả walk-forward optimize (giá trị thay đổi mỗi lần optimize
#    lại), khác bản chất với strategies:* trong og_config.yaml (tham số vận
#    hành ổn định) -- nên nằm ở file CSV riêng, không lẫn vào og_config.yaml.
#    Cả combo/ma_cross đều dùng bảng này.
#
#    Mỗi ô ksl_code/ktp_code là 1 mã như "KSL0618" -- không phải chỉ số thứ
#    tự -- xem levels.py:decode_level cho lý do.
# =============================================================================

_KSL_KTP_CSV_PATH = Path(__file__).resolve().parent / "ksl_ktp.csv"
_VALID_STRATEGY_KEYS = ("combo", "ma_cross")


def _load_ksl_ktp_table(path: Path) -> dict[tuple[str, str, str], tuple[str, str]]:
    """Đọc ksl_ktp.csv thành dict tra theo (strategy, symbol, timeframe).

    Validate ngay lúc import (không cần Redis, hermetic):
        - strategy phải là 1 trong các key đã đăng ký.
        - không có dòng (strategy, symbol, timeframe) trùng nhau.
        - ksl_code/ktp_code phải giải mã được đúng 1 trong 10 mức đã chốt
          (decode_level tự raise nếu không).
    """
    if not path.exists():
        raise FileNotFoundError(f"Thiếu {path} -- bảng KSL/KTP theo symbol/timeframe.")

    table: dict[tuple[str, str, str], tuple[str, str]] = {}
    frame = pd.read_csv(path, dtype=str)
    required_columns = {"strategy", "symbol", "timeframe", "ksl_code", "ktp_code"}
    missing_columns = required_columns - set(frame.columns)
    if missing_columns:
        raise ValueError(f"{path} thiếu cột: {', '.join(sorted(missing_columns))}")

    for row in frame.itertuples(index=False):
        strategy = str(row.strategy).strip().lower()
        symbol = str(row.symbol).strip().upper()
        timeframe = str(row.timeframe).strip().upper()
        ksl_code = str(row.ksl_code).strip().upper()
        ktp_code = str(row.ktp_code).strip().upper()

        if strategy not in _VALID_STRATEGY_KEYS:
            raise ValueError(
                f"{path}: strategy '{strategy}' không phải 1 trong {_VALID_STRATEGY_KEYS}"
            )
        key = (strategy, symbol, timeframe)
        if key in table:
            raise ValueError(f"{path}: dòng trùng (strategy,symbol,timeframe) = {key}")
        # Validate decode được ngay lúc import -- gõ sai 1 chữ số bị chặn ở
        # đây, không đợi tới lúc có signal thật mới lộ ra.
        decode_level(ksl_code, "KSL", KSL_LEVELS)
        decode_level(ktp_code, "KTP", KTP_LEVELS)

        table[key] = (ksl_code, ktp_code)
    return table


_KSL_KTP_TABLE: dict[tuple[str, str, str], tuple[str, str]] = _load_ksl_ktp_table(
    _KSL_KTP_CSV_PATH
)


def _resolve_ksl_ktp(strategy: str, symbol: str, timeframe: str) -> tuple[float, float]:
    """Tra (strategy, symbol, timeframe) -> (KSL, KTP) đã giải mã thành số thật.

    Không có fallback nào -- thiếu dòng trong ksl_ktp.csv sẽ raise KeyError
    rõ ràng ngay lúc chạy, không âm thầm dùng 0 (KSL/KTP=0 nghĩa là SL/TP
    trùng giá vào lệnh, dính ngay lập tức -- nguy hiểm hơn hẳn việc dừng
    hệ thống lại để điền cho đủ bảng).
    """
    key = (strategy.strip().lower(), symbol.strip().upper(), timeframe.strip().upper())
    row = _KSL_KTP_TABLE.get(key)
    if row is None:
        raise KeyError(
            f"ksl_ktp.csv thiếu KSL/KTP cho strategy={key[0]} symbol={key[1]} "
            f"timeframe={key[2]}"
        )
    ksl_code, ktp_code = row
    return decode_level(ksl_code, "KSL", KSL_LEVELS), decode_level(ktp_code, "KTP", KTP_LEVELS)


# =============================================================================
# 2. Tham số chiến lược — giá trị bắt buộc lấy từ og_config.yaml (mục
#    `strategies:`) — không có bản sao/fallback nào trong code. Chỉ
#    PARAM_FIELDS (schema validate: min/max) và normalize_*_params (hàm
#    merge+validate) là code, vì đó là cấu trúc hệ thống chứ không phải
#    giá trị vận hành.
# =============================================================================


def _to_int(value: object, default: int, min_value: int, max_value: int) -> int:
    """
    Chuyển input sang số nguyên và kẹp trong khoảng an toàn.

    Nếu input sai kiểu hoặc bỏ trống, dùng default đã lấy từ og_config.yaml.
    """
    try:
        parsed = int(float(value)) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(parsed, max_value))


def _to_float(value: object, default: float, min_value: float, max_value: float) -> float:
    """
    Chuyển input sang số thực và kẹp trong khoảng an toàn.
    """
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(parsed, max_value))


def _field_range(fields: list[dict[str, Any]], key: str) -> tuple[float, float]:
    """Lấy (min, max) của 1 field trong PARAM_FIELDS — nguồn duy nhất cho
    giới hạn clamp, tránh lặp lại số min/max ở normalize_*_params.
    """
    for field in fields:
        if field["key"] == key:
            return field["min"], field["max"]
    raise KeyError(f"No PARAM_FIELDS entry for '{key}'")


def _to_bool(value: object, default: bool) -> bool:
    """
    Chuyển input sang bool cho các công tắc strategy.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _to_choice(value: object, default: str, allowed: tuple[str, ...], key: str) -> str:
    """
    Chuẩn hoá một lựa chọn thuộc danh sách cố định.
    """
    candidate = str(value or default).strip().upper()
    if candidate not in allowed:
        raise ValueError(f"{key} must be one of: {', '.join(allowed)}")
    return candidate


def _trend_timeframes(strategy_cfg: dict[str, Any], path: str) -> tuple[str, ...]:
    """
    Đọc danh sách timeframe trend được phép cho một strategy.
    """
    return tuple(
        str(tf).upper()
        for tf in require(strategy_cfg, "trend_timeframes", f"{path}.trend_timeframes")
    )


def _strategy_trend_defaults(
    strategy_cfg: dict[str, Any],
    path: str,
    *,
    entry_tf: str,
) -> dict[str, Any]:
    """
    Lấy 4 thông tin điều khiển trend từ config của một strategy.
    """
    return {
        "TREND_FILTER_ENABLED": _to_bool(
            require(strategy_cfg, "trend_filter_enabled", f"{path}.trend_filter_enabled"),
            False,
        ),
        "TREND_TYPE": str(require(strategy_cfg, "trend_type", f"{path}.trend_type")).lower(),
        "TREND_TF": str(
            require(strategy_cfg, "default_trend_tf", f"{path}.default_trend_tf")
        ).upper(),
        "ENTRY_TF": entry_tf,
    }


def _normalize_trend_reference_params(
    raw: dict[str, Any],
    defaults: dict[str, Any],
    *,
    trend_timeframes: tuple[str, ...],
    entry_timeframes: tuple[str, ...],
) -> dict[str, Any]:
    """
    Chuẩn hóa phần trend reference chung cho mọi strategy.

    Chi tiết công thức KNN không đi qua đây; chúng nằm trong
    strategies/trend.py và chỉ được ghép vào lúc tính trend indicator.
    """
    trend_type = str(raw.get("TREND_TYPE", defaults["TREND_TYPE"])).strip().lower()
    if trend_type != "knn":
        raise ValueError("TREND_TYPE must be 'knn'")

    return {
        "TREND_FILTER_ENABLED": _to_bool(
            raw.get("TREND_FILTER_ENABLED"),
            defaults["TREND_FILTER_ENABLED"],
        ),
        "TREND_TYPE": trend_type,
        "TREND_TF": _to_choice(
            raw.get("TREND_TF"), defaults["TREND_TF"], trend_timeframes, "TREND_TF"
        ),
        "ENTRY_TF": _to_choice(
            raw.get("ENTRY_TF"),
            defaults["ENTRY_TF"],
            entry_timeframes,
            "ENTRY_TF",
        ),
    }


# --- Combo ---

# Khung thời gian Combo chạy trên luồng live — không phải giới hạn cứng của
# thuật toán, nhưng live_worker dùng đúng danh sách này để định tuyến candle
# event. Từ og_config.yaml: strategies.combo.recommended_timeframes.
COMBO_RECOMMENDED_TIMEFRAMES: tuple[str, ...] = tuple(
    str(tf).upper()
    for tf in require(
        _COMBO_CFG, "recommended_timeframes", "strategies.combo.recommended_timeframes"
    )
)

# TF dùng làm ENTRY_TF mặc định khi không truyền gì (đường trend reference).
COMBO_DEFAULT_TIMEFRAME = str(
    require(_COMBO_CFG, "default_timeframe", "strategies.combo.default_timeframe")
).upper()
# Các TF khung lớn được phép chọn làm trend reference cho Combo.
COMBO_TREND_TIMEFRAMES: tuple[str, ...] = _trend_timeframes(_COMBO_CFG, "strategies.combo")

# Buffer X cho từng symbol (đơn vị: điểm), từ og_config.yaml:
# strategies.combo.symbol_x. X được cộng vào đỉnh/đáy bar khi xác định
# Entry — giá trị nhỏ hơn cho symbol có spread hẹp, lớn hơn cho symbol
# biến động mạnh.
COMBO_SYMBOL_X: dict[str, float] = {
    str(symbol).upper(): float(x)
    for symbol, x in require(_COMBO_CFG, "symbol_x", "strategies.combo.symbol_x").items()
}

# Giờ UTC được phép giao dịch cho từng symbol, từ og_config.yaml:
# strategies.combo.session_hours_utc. Symbol không liệt kê (hoặc để trống) =
# giao dịch mọi giờ (không lọc theo session).
COMBO_SESSION_HOURS_UTC: dict[str, list[int]] = {
    str(symbol).upper(): [int(h) for h in (hours or [])]
    for symbol, hours in require(
        _COMBO_CFG, "session_hours_utc", "strategies.combo.session_hours_utc"
    ).items()
}

# Tham số mặc định — cũng là nguồn allowlist cho override.
# X=None vì giá trị thực lấy từ COMBO_SYMBOL_X theo symbol được chọn.
COMBO_DEFAULT_PARAMS: dict[str, Any] = {
    "MA_PERIOD": int(require(_COMBO_CFG, "ma_period", "strategies.combo.ma_period")),
    "MACD_FAST": int(require(_COMBO_CFG, "macd_fast", "strategies.combo.macd_fast")),
    "MACD_SLOW": int(require(_COMBO_CFG, "macd_slow", "strategies.combo.macd_slow")),
    "MACD_SIGNAL": int(require(_COMBO_CFG, "macd_signal", "strategies.combo.macd_signal")),
    "ATR_PERIOD": int(require(_COMBO_CFG, "atr_period", "strategies.combo.atr_period")),
    "X": None,
    "SESSION_HOURS_UTC": [],
    **_strategy_trend_defaults(_COMBO_CFG, "strategies.combo", entry_tf=COMBO_DEFAULT_TIMEFRAME),
}

# Định nghĩa field cho từng tham số (type/min/max) — dùng làm allowlist khi
# lọc override key hợp lệ. Đây là giới hạn AN TOÀN đầu vào (validation),
# không phải giá trị vận hành — không nằm trong og_config.yaml.
COMBO_PARAM_FIELDS: list[dict[str, Any]] = [
    {"key": "MA_PERIOD", "label": "MA", "type": "number", "min": 2, "max": 500, "step": 1},
    {"key": "MACD_FAST", "label": "MACD Fast", "type": "number", "min": 1, "max": 200, "step": 1},
    {"key": "MACD_SLOW", "label": "MACD Slow", "type": "number", "min": 2, "max": 300, "step": 1},
    {
        "key": "MACD_SIGNAL",
        "label": "MACD Signal",
        "type": "number",
        "min": 1,
        "max": 200,
        "step": 1,
    },
    {"key": "ATR_PERIOD", "label": "ATR", "type": "number", "min": 2, "max": 200, "step": 1},
    {"key": "X", "label": "X Buffer", "type": "number", "min": 0, "max": 1_000_000, "step": 0.01},
    {"key": "SESSION_HOURS_UTC", "label": "UTC Hours", "type": "text"},
    {"key": "TREND_FILTER_ENABLED", "label": "Trend Filter", "type": "bool"},
    {"key": "TREND_TF", "label": "Trend TF", "type": "text"},
]


def _parse_session_hours(value: object, default: list[int]) -> list[int]:
    """
    Parse danh sách giờ UTC từ chuỗi hoặc list.

    Input có thể là:
        - List/tuple/set số nguyên: [9, 10, 14, 15]
        - Chuỗi phân cách bằng dấu phẩy, chấm phẩy, hoặc space: "9,10,14 15"
        - Chuỗi rỗng: trả về []

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


def _combo_symbol_params(symbol: str | None) -> dict[str, Any]:
    """Tham số riêng theo symbol cho Combo: buffer X và giờ giao dịch.

    Symbol không có trong COMBO_SYMBOL_X sẽ dùng X = 0.0.
    """
    key = str(symbol or "").strip().upper()
    return {
        "x": float(COMBO_SYMBOL_X.get(key, 0.0)),
        "session_hours_utc": list(COMBO_SESSION_HOURS_UTC.get(key, [])),
    }


def normalize_combo_params(
    overrides: dict[str, Any] | None = None,
    symbol: str | None = None,
    tf: str | None = None,
) -> dict[str, Any]:
    """
    Merge tham số mặc định, tham số theo symbol và override thành dict hợp lệ.

    Thứ tự ưu tiên: COMBO_DEFAULT_PARAMS < symbol defaults < overrides.
    Tất cả giá trị được validate và clamp vào giới hạn an toàn.

    Args:
        overrides: Dict tham số chỉ định thêm. Có thể None — khi đó chỉ dùng
            defaults.
        symbol: Mã symbol để lấy X, session_hours, và KSL/KTP.
        tf: Timeframe đang chạy — cần để tra đúng dòng KSL/KTP trong
            ksl_ktp.csv (KSL/KTP khác nhau theo từng timeframe, không chỉ
            theo symbol). Không có fallback nếu thiếu (symbol, tf) trong
            bảng — xem _resolve_ksl_ktp.

    Returns:
        Dict tham số đã validate đầy đủ — an toàn để truyền vào pipeline.
    """
    raw = {**COMBO_DEFAULT_PARAMS, **(overrides or {})}
    d = COMBO_DEFAULT_PARAMS
    f = COMBO_PARAM_FIELDS
    symbol_params = _combo_symbol_params(symbol)
    ksl, ktp = _resolve_ksl_ktp("combo", str(symbol or ""), str(tf or ""))

    return {
        "MA_PERIOD": _to_int(raw.get("MA_PERIOD"), d["MA_PERIOD"], *_field_range(f, "MA_PERIOD")),
        "MACD_FAST": _to_int(raw.get("MACD_FAST"), d["MACD_FAST"], *_field_range(f, "MACD_FAST")),
        "MACD_SLOW": _to_int(raw.get("MACD_SLOW"), d["MACD_SLOW"], *_field_range(f, "MACD_SLOW")),
        "MACD_SIGNAL": _to_int(
            raw.get("MACD_SIGNAL"), d["MACD_SIGNAL"], *_field_range(f, "MACD_SIGNAL")
        ),
        "ATR_PERIOD": _to_int(
            raw.get("ATR_PERIOD"), d["ATR_PERIOD"], *_field_range(f, "ATR_PERIOD")
        ),
        # X: override > symbol default (từ COMBO_SYMBOL_X) > 0.0
        "X": _to_float(raw.get("X"), symbol_params["x"], *_field_range(f, "X")),
        "SESSION_HOURS_UTC": _parse_session_hours(
            raw.get("SESSION_HOURS_UTC"),
            symbol_params["session_hours_utc"],
        ),
        # KSL/KTP: tra thẳng từ ksl_ktp.csv theo (symbol, tf) -- không qua
        # override/clamp như các tham số kỹ thuật khác, vì đây không phải
        # input tự do mà là kết quả optimize đã chốt cho đúng cặp này.
        "KSL": ksl,
        "KTP": ktp,
        **_normalize_trend_reference_params(
            raw,
            d,
            trend_timeframes=COMBO_TREND_TIMEFRAMES,
            entry_timeframes=COMBO_RECOMMENDED_TIMEFRAMES,
        ),
    }


# --- MA Cross ---

# MA Cross chỉ được phép chạy trên các timeframe đã chốt trong og_config.yaml:
# strategies.ma_cross.supported_timeframes.
MA_CROSS_SUPPORTED_TIMEFRAMES: tuple[str, ...] = tuple(
    str(tf).upper()
    for tf in require(
        _MA_CROSS_CFG, "supported_timeframes", "strategies.ma_cross.supported_timeframes"
    )
)
# MA Cross không có khái niệm "recommended" tách rời "supported" — tập TF
# chạy live đúng bằng tập TF được hỗ trợ, không lặp lại danh sách này trong
# og_config.yaml.
MA_CROSS_RECOMMENDED_TIMEFRAMES: tuple[str, ...] = MA_CROSS_SUPPORTED_TIMEFRAMES
MA_CROSS_DEFAULT_TIMEFRAME = str(
    require(_MA_CROSS_CFG, "default_timeframe", "strategies.ma_cross.default_timeframe")
).upper()
MA_CROSS_TREND_TIMEFRAMES: tuple[str, ...] = _trend_timeframes(
    _MA_CROSS_CFG,
    "strategies.ma_cross",
)

MA_CROSS_DEFAULT_PARAMS: dict[str, Any] = {
    "FAST_MA": int(require(_MA_CROSS_CFG, "fast_ma", "strategies.ma_cross.fast_ma")),
    "SLOW_MA": int(require(_MA_CROSS_CFG, "slow_ma", "strategies.ma_cross.slow_ma")),
    "MACD_FAST": int(require(_MA_CROSS_CFG, "macd_fast", "strategies.ma_cross.macd_fast")),
    "MACD_SLOW": int(require(_MA_CROSS_CFG, "macd_slow", "strategies.ma_cross.macd_slow")),
    "MACD_SIGNAL": int(require(_MA_CROSS_CFG, "macd_signal", "strategies.ma_cross.macd_signal")),
    "ATR_PERIOD": int(require(_MA_CROSS_CFG, "atr_period", "strategies.ma_cross.atr_period")),
    **_strategy_trend_defaults(
        _MA_CROSS_CFG,
        "strategies.ma_cross",
        entry_tf=MA_CROSS_DEFAULT_TIMEFRAME,
    ),
}

MA_CROSS_PARAM_FIELDS: list[dict[str, Any]] = [
    {"key": "FAST_MA", "label": "Fast SMA", "type": "number", "min": 1, "max": 300, "step": 1},
    {"key": "SLOW_MA", "label": "Slow SMA", "type": "number", "min": 2, "max": 500, "step": 1},
    {"key": "MACD_FAST", "label": "MACD Fast", "type": "number", "min": 1, "max": 200, "step": 1},
    {"key": "MACD_SLOW", "label": "MACD Slow", "type": "number", "min": 2, "max": 300, "step": 1},
    {
        "key": "MACD_SIGNAL",
        "label": "MACD Signal",
        "type": "number",
        "min": 1,
        "max": 200,
        "step": 1,
    },
    {"key": "ATR_PERIOD", "label": "ATR", "type": "number", "min": 2, "max": 200, "step": 1},
    {"key": "TREND_FILTER_ENABLED", "label": "Trend Filter", "type": "bool"},
    {"key": "TREND_TF", "label": "Trend TF", "type": "text"},
]


def normalize_ma_cross_params(
    overrides: dict[str, Any] | None = None,
    symbol: str | None = None,
    tf: str | None = None,
) -> dict[str, Any]:
    """
    Gộp tham số MA Cross thành bộ tham số cuối cùng, đã validate.

    Thứ tự ưu tiên:
        1. Default trong og_config.yaml.
        2. Override truyền vào.

    Hàm này cũng kiểm tra các quan hệ bắt buộc:
        - FAST_MA phải nhỏ hơn SLOW_MA.
        - MACD_FAST phải nhỏ hơn MACD_SLOW.

    symbol/tf cần để tra KSL/KTP trong ksl_ktp.csv, xem normalize_combo_params.
    """
    raw = {**MA_CROSS_DEFAULT_PARAMS, **(overrides or {})}
    d = MA_CROSS_DEFAULT_PARAMS
    f = MA_CROSS_PARAM_FIELDS
    ksl, ktp = _resolve_ksl_ktp("ma_cross", str(symbol or ""), str(tf or ""))

    fast_ma = _to_int(raw.get("FAST_MA"), d["FAST_MA"], *_field_range(f, "FAST_MA"))
    slow_ma = _to_int(raw.get("SLOW_MA"), d["SLOW_MA"], *_field_range(f, "SLOW_MA"))
    if fast_ma >= slow_ma:
        raise ValueError("FAST_MA must be smaller than SLOW_MA")

    macd_fast = _to_int(raw.get("MACD_FAST"), d["MACD_FAST"], *_field_range(f, "MACD_FAST"))
    macd_slow = _to_int(raw.get("MACD_SLOW"), d["MACD_SLOW"], *_field_range(f, "MACD_SLOW"))
    if macd_fast >= macd_slow:
        raise ValueError("MACD_FAST must be smaller than MACD_SLOW")

    return {
        "FAST_MA": fast_ma,
        "SLOW_MA": slow_ma,
        "MACD_FAST": macd_fast,
        "MACD_SLOW": macd_slow,
        "MACD_SIGNAL": _to_int(
            raw.get("MACD_SIGNAL"), d["MACD_SIGNAL"], *_field_range(f, "MACD_SIGNAL")
        ),
        "ATR_PERIOD": _to_int(
            raw.get("ATR_PERIOD"), d["ATR_PERIOD"], *_field_range(f, "ATR_PERIOD")
        ),
        "KSL": ksl,
        "KTP": ktp,
        **_normalize_trend_reference_params(
            raw,
            d,
            trend_timeframes=MA_CROSS_TREND_TIMEFRAMES,
            entry_timeframes=MA_CROSS_SUPPORTED_TIMEFRAMES,
        ),
    }


# =============================================================================
# 3. Sổ đăng ký chiến lược — nối indicator.py với từng file strategies/<tên>.py
#    thành 1 pipeline hoàn chỉnh, tra theo key ("combo"/"ma_cross").
# =============================================================================


@dataclass(frozen=True)
class StrategySpec:
    """
    Mô tả đầy đủ một chiến lược giao dịch — bất biến sau khi tạo.

    Thuộc tính:
        key:              Mã định danh duy nhất ("combo", "ma_cross").
        normalize_params: (overrides, symbol, tf) -> dict tham số đã validate.
        add_indicators:   (df, params) -> df với cột chỉ báo thêm vào.
        detect_signals:   (df, symbol, params) -> df với cột signal.
        add_levels:       (df, params, symbol) -> df với cột entry/SL/TP.
        recommended_timeframes: Các TF chạy trên luồng live.
        supported_timeframes:   Các TF được phép; rỗng = không giới hạn cứng.

    Invariant:
        Pipeline phải gọi theo thứ tự:
        normalize_params → add_indicators → detect_signals → add_levels.
        Mỗi bước nhận output của bước trước làm input.

    Thêm chiến lược mới — checklist:
        1. Viết add_<tên>_indicators (chỉ tính chỉ báo, không có default
           ngầm) trong indicator.py.
        2. Viết detect_<tên>_signals (CHỈ quyết định khi nào/hướng nào,
           không entry/level) trong 1 file strategies/<tên>.py mới —
           không giữ tham số, không sửa DataFrame input tại chỗ.
        3. Viết add_<tên>_levels (entry/SL/TP) trong levels.py.
        4. Thêm mục `strategies.<tên>` vào og_config.yaml (giá trị thật) +
           viết default_params/param_fields/normalize_params ở đây, theo
           đúng mẫu Combo/MA Cross bên trên.
        5. Thêm dòng KSL/KTP cho mọi (symbol, timeframe) vào ksl_ktp.csv và
           thêm key vào _VALID_STRATEGY_KEYS.
        6. Đăng ký một StrategySpec mới vào dict STRATEGIES bên dưới.
        7. Thêm `live.signal_validity.<tên>` + `live.enabled_strategies`
           trong og_config.yaml.
    """

    key: str
    normalize_params: Callable[..., dict[str, Any]]
    add_indicators: Callable[..., pd.DataFrame]
    detect_signals: Callable[..., pd.DataFrame]
    add_levels: Callable[..., pd.DataFrame]
    recommended_timeframes: tuple[str, ...] = ()
    supported_timeframes: tuple[str, ...] = ()


# Danh sách chiến lược được hỗ trợ. Key dùng lowercase.
STRATEGIES: dict[str, StrategySpec] = {
    "combo": StrategySpec(
        key="combo",
        normalize_params=normalize_combo_params,
        add_indicators=add_combo_indicators,
        detect_signals=detect_combo_signals,
        add_levels=add_combo_levels,
        recommended_timeframes=COMBO_RECOMMENDED_TIMEFRAMES,
    ),
    "ma_cross": StrategySpec(
        key="ma_cross",
        normalize_params=normalize_ma_cross_params,
        add_indicators=add_ma_cross_indicators,
        detect_signals=detect_ma_cross_signals,
        add_levels=add_ma_cross_levels,
        recommended_timeframes=MA_CROSS_RECOMMENDED_TIMEFRAMES,
        supported_timeframes=MA_CROSS_SUPPORTED_TIMEFRAMES,
    ),
}


def get_strategy(key: str) -> StrategySpec:
    """
    Tra cứu chiến lược theo key (không phân biệt hoa/thường).

    Raises:
        KeyError: Nếu key không tồn tại trong STRATEGIES.
    """
    normalized = str(key).strip().lower()
    if normalized not in STRATEGIES:
        raise KeyError(f"Unknown strategy '{key}'. Available: {', '.join(STRATEGIES)}")
    return STRATEGIES[normalized]


def run_strategy(
    key: str,
    *,
    symbol: str,
    tf: str,
    bars: pd.DataFrame,
    overrides: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """
    Chạy trọn pipeline của một chiến lược trên OHLCV đã load sẵn (theo đúng
    thứ tự invariant của StrategySpec).

    Args:
        key: Mã chiến lược ("combo", "ma_cross").
        symbol: Mã symbol — dùng để resolve symbol-specific params (X, KSL/KTP).
        tf: Mã timeframe — bị từ chối nếu ngoài spec.supported_timeframes.
        bars: DataFrame OHLCV [bartime, open, high, low, close, volume],
            đã sort tăng dần (output của candle_reader.read_candles_from_redis).
        overrides: Tham số override.

    Returns:
        DataFrame OHLCV gốc + cột chỉ báo + signal + entry/SL/TP.

    Raises:
        KeyError: Chiến lược không tồn tại.
        ValueError: tf ngoài spec.supported_timeframes.
    """
    spec = get_strategy(key)
    symbol = str(symbol).strip().upper()
    tf = str(tf).strip().upper()
    if spec.supported_timeframes and tf not in spec.supported_timeframes:
        allowed = ", ".join(spec.supported_timeframes)
        raise ValueError(f"Strategy '{spec.key}' supports only these timeframes: {allowed}.")

    params = spec.normalize_params(overrides, symbol, tf)
    if "ENTRY_TF" in params:
        params = {**params, "ENTRY_TF": tf}
    enriched = spec.add_indicators(bars, params)
    enriched = spec.detect_signals(enriched, symbol=symbol, params=params)
    enriched = spec.add_levels(enriched, params, symbol)
    return enriched


def run_strategy_with_trend_reference(
    key: str,
    *,
    symbol: str,
    entry_tf: str,
    entry_bars: pd.DataFrame,
    trend_tf: str,
    trend_bars: pd.DataFrame,
    overrides: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """
    Chạy strategy entry với trend reference khung lớn đã load sẵn.

    Hàm này giữ pipeline single-timeframe (run_strategy) không bị chồng chéo,
    đồng thời cung cấp đường chạy đúng bản chất khi bật trend filter:
        trend_bars -> KNN trend_bias
        entry_bars -> entry indicators/raw_signal
        merge trend ĐÃ ĐÓNG -> filter signal -> levels

    Gọi hàm này nghĩa là trend filter BẬT: nó tự đặt TREND_FILTER_ENABLED=True
    trong overrides, nên `signal` chỉ còn lại khi cùng chiều `trend_bias`.
    live_worker chọn giữa run_strategy và hàm này dựa trên
    trend_reference_for() bên dưới.
    """
    spec = get_strategy(key)
    symbol = str(symbol).strip().upper()
    entry_tf = str(entry_tf).strip().upper()
    trend_tf = str(trend_tf).strip().upper()
    if spec.supported_timeframes and entry_tf not in spec.supported_timeframes:
        allowed = ", ".join(spec.supported_timeframes)
        raise ValueError(f"Strategy '{spec.key}' supports only these timeframes: {allowed}.")

    trend_overrides = {
        **(overrides or {}),
        "TREND_FILTER_ENABLED": True,
        "TREND_TF": trend_tf,
        "ENTRY_TF": entry_tf,
    }
    # KSL/KTP tra theo entry_tf (khung thực sự vào lệnh), không phải trend_tf
    # (chỉ là tham chiếu lọc hướng, không phải khung đặt lệnh).
    params = spec.normalize_params(trend_overrides, symbol, entry_tf)
    if params.get("TREND_TYPE") != "knn":
        raise ValueError("Only KNN trend reference is implemented.")

    entry = spec.add_indicators(entry_bars, params)
    trend = add_knn_trend_indicators(trend_bars, knn_trend_indicator_params(params))
    merged = merge_trend_reference(entry, trend)
    enriched = spec.detect_signals(merged, symbol=symbol, params=params)
    enriched = spec.add_levels(enriched, params, symbol)
    return enriched


# Tra cứu công tắc trend của từng strategy — live_worker cần biết (a) có bật
# hay không, (b) phải đọc thêm nến khung nào từ Redis DB0. Đọc từ chính
# *_DEFAULT_PARAMS đã resolve, nên og_config.yaml vẫn là nguồn sự thật duy
# nhất và không có bản sao giá trị nào ở live_worker.
_DEFAULT_PARAMS_BY_KEY: dict[str, dict[str, Any]] = {
    "combo": COMBO_DEFAULT_PARAMS,
    "ma_cross": MA_CROSS_DEFAULT_PARAMS,
}


def trend_reference_for(key: str) -> tuple[bool, str]:
    """Trả về ``(trend_filter_enabled, trend_timeframe)`` cho 1 strategy.

    Mặc định trong og_config.yaml là ``trend_filter_enabled: false`` cho cả
    combo và ma_cross — khi đó live_worker chạy run_strategy() 1 timeframe y
    như trước. Đổi thành true (rồi restart service) sẽ khiến live_worker đọc
    thêm nến ``trend_timeframe`` của cùng symbol từ DB0 và chỉ publish tín
    hiệu cùng chiều trend khung lớn.
    """
    params = _DEFAULT_PARAMS_BY_KEY[get_strategy(key).key]
    return bool(params["TREND_FILTER_ENABLED"]), str(params["TREND_TF"])
