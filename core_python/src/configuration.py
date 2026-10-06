"""
Toàn bộ tham số chiến lược + sổ đăng ký nối pipeline cho core_python.

Mô tả:
    File này KHÔNG kết nối SQL Server và KHÔNG tính chỉ báo — 2 việc đó
    thuộc core_python/db_connector.py và core_python/indicator.py. File
    này CHỈ ĐỌC + VALIDATE tham số, không tự quyết định giá trị và KHÔNG
    giữ giá trị mặc định nào trùng với config.yaml:
    1. Default CLI (symbol/bars) — bắt buộc có trong config.yaml (mục
       `defaults:`).
    2. Tham số của từng chiến lược (Combo, MA Cross) — bắt buộc có trong
       config.yaml (mục `strategies:`, xem CLAUDE.md mục 5), đây là khu
       vực operator chỉnh trực tiếp (X buffer entry theo symbol, chu kỳ
       chỉ báo, trend switch...). Thiếu bất kỳ key nào trong config.yaml sẽ raise lỗi
       rõ ràng ngay khi import — không có fallback âm thầm nào trong code.
       PARAM_FIELDS (giới hạn min/max/label cho UI/validate input, không
       phải giá trị vận hành) + hàm normalize_*_params() merge+validate
       vẫn là code. strategies/<tên>.py và levels.py đều KHÔNG giữ tham số
       nào — chỉ nhận dict params đã hoàn chỉnh từ đây.
    3. Sổ đăng ký chiến lược (StrategySpec/STRATEGIES/run_strategy) — nối
       indicator.py (chỉ báo) + strategies/<tên>.py (tín hiệu BUY/SELL) +
       levels.py (entry) thành 1 pipeline hoàn chỉnh, tra theo key
       ("combo"/"ma_cross"). export_cli.py, signal_display/cli.py,
       signal_display/server.py đều cần thẳng phần này để biến
       `--strategy combo`/`--strategy ma_cross` thành đúng pipeline cần
       chạy.

Đầu ra:
    DEFAULT_SYMBOL, N_BARS.
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
import yaml
from core_python.src.indicator import (
    add_breakout_atr_indicators,
    add_combo_indicators,
    add_knn_trend_indicators,
    add_ma_cross_indicators,
    add_sma_trend_indicators,
    merge_trend_reference,
)
from core_python.src.levels import (
    add_breakout_atr_levels,
    add_combo_levels,
    add_ma_cross_levels,
    add_sma_trend_levels,
)
from core_python.src.strategies.breakout_atr import detect_breakout_atr_signals
from core_python.src.strategies.combo import detect_combo_signals
from core_python.src.strategies.ma_cross import detect_ma_cross_signals
from core_python.src.strategies.sma_trend import detect_sma_trend_signals
from core_python.src.strategies.trend import knn_trend_indicator_params

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "cp_config.yaml"


def _load_config() -> dict[str, Any]:
    """Đọc cp_config.yaml; trả về dict rỗng nếu file chưa tồn tại."""
    if not _CONFIG_PATH.exists():
        return {}
    with _CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _require(cfg: dict[str, Any], key: str, path: str) -> Any:
    """Đọc bắt buộc 1 key từ config.yaml — không có giá trị mặc định nào
    trong code. Thiếu key sẽ raise ngay, chỉ rõ đường dẫn cần điền.
    """
    if key not in cfg:
        raise KeyError(f"cp_config.yaml thiếu '{path}' — xem CLAUDE.md mục 5.")
    return cfg[key]


_CONFIG: dict[str, Any] = _load_config()
_DEFAULTS: dict[str, Any] = _require(_CONFIG, "defaults", "defaults")
_STRATEGIES_CFG: dict[str, Any] = _require(_CONFIG, "strategies", "strategies")
_COMBO_CFG: dict[str, Any] = _require(_STRATEGIES_CFG, "combo", "strategies.combo")
_MA_CROSS_CFG: dict[str, Any] = _require(_STRATEGIES_CFG, "ma_cross", "strategies.ma_cross")
_BREAKOUT_ATR_CFG: dict[str, Any] = _require(_STRATEGIES_CFG, "breakout_atr", "strategies.breakout_atr")
_SMA_TREND_CFG: dict[str, Any] = _require(_STRATEGIES_CFG, "sma_trend", "strategies.sma_trend")


# =============================================================================
# 1. Default CLI values — bắt buộc có trong config.yaml (mục `defaults:`).
# =============================================================================

DEFAULT_SYMBOL = str(_require(_DEFAULTS, "symbol", "defaults.symbol"))
N_BARS = int(_require(_DEFAULTS, "bars", "defaults.bars"))
# Số bar tải thêm TRƯỚC mốc `--from`/`from_time` để chỉ báo (SMA/MACD/ATR/
# KNN trend) kịp "ấm" trước khi vào đúng khoảng người dùng yêu cầu — xem
# db_connector.load_range_with_warmup(). Khác bản chất với
# `redis.input.snapshot_bars` (đó là "xin Redis bao nhiêu", key riêng, cố ý
# không gộp — xem CLAUDE.md mục 5).
WARMUP_BARS = int(_require(_DEFAULTS, "warmup_bars", "defaults.warmup_bars"))

# Mốc bắt đầu tính signal cho luồng Redis DB0 (redis_io/worker.py) -- chốt
# 2026-09-21, thuần lý do khối lượng dữ liệu (11 symbol x tối đa 8 timeframe
# nhân 2 chiến lược, full-history từ 2017 quá nặng để bootstrap lại mỗi lần
# start/reconnect). KHÔNG áp dụng cho export_cli.py -- CSV vẫn full-history
# mặc định trừ khi tự truyền --from, đây là snapshot cố định theo yêu cầu
# riêng từng lần, khác bản chất luồng Redis luôn sống/luôn cập nhật.
SIGNAL_START_DATE = str(_require(_DEFAULTS, "signal_start_date", "defaults.signal_start_date"))


def trim_to_requested_range(df: pd.DataFrame, date_from: str) -> pd.DataFrame:
    """Cắt bỏ các bar warmup (trước date_from) khỏi kết quả sau khi chỉ báo
    đã chạy xong trên khung đã nới rộng (load_range_with_warmup) -- output
    cuối cùng chỉ được chứa đúng khoảng yêu cầu, không lẫn tín hiệu thật nằm
    trong đoạn warmup. Dùng chung cho export_cli.py (CSV, date_from tuỳ chọn
    theo --from) và redis_io/worker.py (Redis DB0, date_from luôn là
    SIGNAL_START_DATE) -- sống ở đây (không phải export_cli.py) vì đây là
    phần bù cho load_range_with_warmup() (cũng không phải việc riêng của
    CSV), tránh worker.py phải import từ export_cli.py chỉ vì việc này.
    """
    if not date_from or df.empty:
        return df
    cutoff = pd.Timestamp(date_from)
    return df[df["bartime"] >= cutoff].reset_index(drop=True)


# =============================================================================
# 2. Tham số chiến lược — giá trị bắt buộc lấy từ config.yaml (mục
#    `strategies:`) — không có bản sao/fallback nào trong code. Chỉ
#    PARAM_FIELDS (schema validate/UI: min/max/label) và normalize_*_params
#    (hàm merge+validate) là code, vì đó là cấu trúc hệ thống chứ không
#    phải giá trị vận hành.
# =============================================================================


def _to_int(value: object, default: int, min_value: int, max_value: int) -> int:
    """
    Chuyển input sang số nguyên và kẹp trong khoảng an toàn.

    Nếu user nhập sai kiểu hoặc bỏ trống, dùng default đã lấy từ config.yaml.
    """
    try:
        parsed = int(float(value)) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(parsed, max_value))


def _to_float(value: object, default: float, min_value: float, max_value: float) -> float:
    """
    Chuyển input sang số thực và kẹp trong khoảng an toàn.

    Hàm này giúp dashboard/CLI không làm vỡ pipeline khi user nhập sai.
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
        for tf in _require(strategy_cfg, "trend_timeframes", f"{path}.trend_timeframes")
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
            _require(strategy_cfg, "trend_filter_enabled", f"{path}.trend_filter_enabled"),
            False,
        ),
        "TREND_TYPE": str(_require(strategy_cfg, "trend_type", f"{path}.trend_type")).lower(),
        "TREND_TF": str(_require(strategy_cfg, "default_trend_tf", f"{path}.default_trend_tf")).upper(),
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
        "TREND_TF": _to_choice(raw.get("TREND_TF"), defaults["TREND_TF"], trend_timeframes, "TREND_TF"),
        "ENTRY_TF": _to_choice(
            raw.get("ENTRY_TF"),
            defaults["ENTRY_TF"],
            entry_timeframes,
            "ENTRY_TF",
        ),
    }


# --- Combo ---

# Khung thời gian được gợi ý mặc định trên CLI cho Combo — không phải giới
# hạn cứng, db_connector.tf_minutes() vẫn cho phép chạy TF khác nếu chủ
# động chọn. Từ config.yaml: strategies.combo.recommended_timeframes.
COMBO_RECOMMENDED_TIMEFRAMES: tuple[str, ...] = tuple(
    str(tf).upper()
    for tf in _require(_COMBO_CFG, "recommended_timeframes", "strategies.combo.recommended_timeframes")
)

# TF dùng khi CLI/dashboard không truyền --tf cho Combo.
COMBO_DEFAULT_TIMEFRAME = str(
    _require(_COMBO_CFG, "default_timeframe", "strategies.combo.default_timeframe")
).upper()
COMBO_TREND_TIMEFRAMES: tuple[str, ...] = _trend_timeframes(_COMBO_CFG, "strategies.combo")

# Buffer X cho từng symbol (đơn vị: điểm), từ config.yaml:
# strategies.combo.symbol_x. X được cộng vào đỉnh/đáy bar khi xác định
# Entry — giá trị nhỏ hơn cho symbol có spread hẹp, lớn hơn cho symbol
# biến động mạnh.
COMBO_SYMBOL_X: dict[str, float] = {
    str(symbol).upper(): float(x)
    for symbol, x in _require(_COMBO_CFG, "symbol_x", "strategies.combo.symbol_x").items()
}

# Giờ UTC được phép giao dịch cho từng symbol, từ config.yaml:
# strategies.combo.session_hours_utc. Symbol không liệt kê (hoặc để trống) =
# giao dịch mọi giờ (không lọc theo session).
COMBO_SESSION_HOURS_UTC: dict[str, list[int]] = {
    str(symbol).upper(): [int(h) for h in (hours or [])]
    for symbol, hours in _require(_COMBO_CFG, "session_hours_utc", "strategies.combo.session_hours_utc").items()
}

# Tham số mặc định — cũng là nguồn allowlist cho override qua CLI (--param).
# X=None vì giá trị thực lấy từ COMBO_SYMBOL_X theo symbol được chọn.
COMBO_DEFAULT_PARAMS: dict[str, Any] = {
    "MA_PERIOD": int(_require(_COMBO_CFG, "ma_period", "strategies.combo.ma_period")),
    "MACD_FAST": int(_require(_COMBO_CFG, "macd_fast", "strategies.combo.macd_fast")),
    "MACD_SLOW": int(_require(_COMBO_CFG, "macd_slow", "strategies.combo.macd_slow")),
    "MACD_SIGNAL": int(_require(_COMBO_CFG, "macd_signal", "strategies.combo.macd_signal")),
    "ATR_PERIOD": int(_require(_COMBO_CFG, "atr_period", "strategies.combo.atr_period")),
    "X": None,
    "SESSION_HOURS_UTC": [],
    **_strategy_trend_defaults(_COMBO_CFG, "strategies.combo", entry_tf=COMBO_DEFAULT_TIMEFRAME),
}

# Định nghĩa field cho từng tham số (type/min/max) — dùng làm allowlist khi
# lọc override key hợp lệ từ CLI --param / query string dashboard. Đây là
# giới hạn AN TOÀN đầu vào (validation), không phải giá trị vận hành —
# không nằm trong config.yaml.
COMBO_PARAM_FIELDS: list[dict[str, Any]] = [
    {"key": "MA_PERIOD", "label": "MA", "type": "number", "min": 2, "max": 500, "step": 1},
    {"key": "MACD_FAST", "label": "MACD Fast", "type": "number", "min": 1, "max": 200, "step": 1},
    {"key": "MACD_SLOW", "label": "MACD Slow", "type": "number", "min": 2, "max": 300, "step": 1},
    {"key": "MACD_SIGNAL", "label": "MACD Signal", "type": "number", "min": 1, "max": 200, "step": 1},
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
    Merge tham số mặc định, tham số theo symbol và override của người dùng thành dict hợp lệ.

    Thứ tự ưu tiên: COMBO_DEFAULT_PARAMS < symbol defaults < overrides.
    Tất cả giá trị được validate và clamp vào giới hạn an toàn.

    Args:
        overrides: Dict tham số người dùng chỉ định (từ URL query hoặc CLI).
                   Có thể là None — khi đó chỉ dùng defaults.
        symbol: Mã symbol để lấy X, session_hours mặc định.
        tf: Timeframe đang chạy — nhận cho đồng nhất chữ ký normalize_params
            giữa 2 chiến lược (run_strategy() gọi cả 2 qua cùng 1 lời gọi),
            combo không dùng tới.

    Returns:
        Dict tham số đã validate đầy đủ — an toàn để truyền vào pipeline.
    """
    _ = tf
    raw = {**COMBO_DEFAULT_PARAMS, **(overrides or {})}
    d = COMBO_DEFAULT_PARAMS
    f = COMBO_PARAM_FIELDS
    symbol_params = _combo_symbol_params(symbol)

    return {
        "MA_PERIOD": _to_int(raw.get("MA_PERIOD"), d["MA_PERIOD"], *_field_range(f, "MA_PERIOD")),
        "MACD_FAST": _to_int(raw.get("MACD_FAST"), d["MACD_FAST"], *_field_range(f, "MACD_FAST")),
        "MACD_SLOW": _to_int(raw.get("MACD_SLOW"), d["MACD_SLOW"], *_field_range(f, "MACD_SLOW")),
        "MACD_SIGNAL": _to_int(raw.get("MACD_SIGNAL"), d["MACD_SIGNAL"], *_field_range(f, "MACD_SIGNAL")),
        "ATR_PERIOD": _to_int(raw.get("ATR_PERIOD"), d["ATR_PERIOD"], *_field_range(f, "ATR_PERIOD")),
        # X: override > symbol default (từ COMBO_SYMBOL_X) > 0.0
        "X": _to_float(raw.get("X"), symbol_params["x"], *_field_range(f, "X")),
        "SESSION_HOURS_UTC": _parse_session_hours(
            raw.get("SESSION_HOURS_UTC"),
            symbol_params["session_hours_utc"],
        ),
        **_normalize_trend_reference_params(
            raw,
            d,
            trend_timeframes=COMBO_TREND_TIMEFRAMES,
            entry_timeframes=COMBO_RECOMMENDED_TIMEFRAMES,
        ),
    }


# --- MA Cross ---

# MA Cross chỉ được phép chạy trên các timeframe đã chốt trong config.yaml:
# strategies.ma_cross.supported_timeframes.
MA_CROSS_SUPPORTED_TIMEFRAMES: tuple[str, ...] = tuple(
    str(tf).upper()
    for tf in _require(_MA_CROSS_CFG, "supported_timeframes", "strategies.ma_cross.supported_timeframes")
)
# MA Cross không có khái niệm "recommended" tách rời "supported" — khuyến
# nghị đúng bằng tập TF được hỗ trợ, không lặp lại danh sách này trong
# config.yaml.
MA_CROSS_RECOMMENDED_TIMEFRAMES: tuple[str, ...] = MA_CROSS_SUPPORTED_TIMEFRAMES
MA_CROSS_DEFAULT_TIMEFRAME = str(
    _require(_MA_CROSS_CFG, "default_timeframe", "strategies.ma_cross.default_timeframe")
).upper()
MA_CROSS_TREND_TIMEFRAMES: tuple[str, ...] = _trend_timeframes(
    _MA_CROSS_CFG,
    "strategies.ma_cross",
)

MA_CROSS_DEFAULT_PARAMS: dict[str, Any] = {
    "FAST_MA": int(_require(_MA_CROSS_CFG, "fast_ma", "strategies.ma_cross.fast_ma")),
    "SLOW_MA": int(_require(_MA_CROSS_CFG, "slow_ma", "strategies.ma_cross.slow_ma")),
    "MACD_FAST": int(_require(_MA_CROSS_CFG, "macd_fast", "strategies.ma_cross.macd_fast")),
    "MACD_SLOW": int(_require(_MA_CROSS_CFG, "macd_slow", "strategies.ma_cross.macd_slow")),
    "MACD_SIGNAL": int(_require(_MA_CROSS_CFG, "macd_signal", "strategies.ma_cross.macd_signal")),
    "ATR_PERIOD": int(_require(_MA_CROSS_CFG, "atr_period", "strategies.ma_cross.atr_period")),
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
    {"key": "MACD_SIGNAL", "label": "MACD Signal", "type": "number", "min": 1, "max": 200, "step": 1},
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
        1. Default trong config.yaml.
        2. Override từ CLI/dashboard.

    Hàm này cũng kiểm tra các quan hệ bắt buộc:
        - FAST_MA phải nhỏ hơn SLOW_MA.
        - MACD_FAST phải nhỏ hơn MACD_SLOW.

    symbol/tf nhận cho đồng nhất chữ ký normalize_params giữa 2 chiến lược
    (run_strategy() gọi cả 2 qua cùng 1 lời gọi) -- MA Cross không dùng tới.
    """
    _ = symbol, tf
    raw = {**MA_CROSS_DEFAULT_PARAMS, **(overrides or {})}
    d = MA_CROSS_DEFAULT_PARAMS
    f = MA_CROSS_PARAM_FIELDS

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
        "MACD_SIGNAL": _to_int(raw.get("MACD_SIGNAL"), d["MACD_SIGNAL"], *_field_range(f, "MACD_SIGNAL")),
        "ATR_PERIOD": _to_int(raw.get("ATR_PERIOD"), d["ATR_PERIOD"], *_field_range(f, "ATR_PERIOD")),
        **_normalize_trend_reference_params(
            raw,
            d,
            trend_timeframes=MA_CROSS_TREND_TIMEFRAMES,
            entry_timeframes=MA_CROSS_SUPPORTED_TIMEFRAMES,
        ),
    }


# -----------------------------------------------------------------------------
# Breakout ATR và SMA Trend — chiến lược nghiên cứu (research_notes S005), thêm
# 2026-10-04 để xem trên chart / export. Không có trend filter KNN: bật
# TREND_FILTER_ENABLED sẽ bị từ chối. Worker Redis DB0 không chạy 2 chiến lược
# này (worker chỉ gọi các key trong _STRATEGY_TIMEFRAMES của nó).
# -----------------------------------------------------------------------------


def _timeframes(strategy_cfg: dict[str, Any], key: str, path: str) -> tuple[str, ...]:
    """Đọc 1 danh sách timeframe bắt buộc từ config, chuẩn hoá viết hoa."""
    return tuple(str(tf).upper() for tf in _require(strategy_cfg, key, f"{path}.{key}"))


def _reject_trend_filter(raw: dict[str, Any], strategy_label: str) -> None:
    """Hai chiến lược nghiên cứu chưa có trend filter — báo lỗi rõ thay vì bỏ qua im lặng."""
    if _to_bool(raw.get("TREND_FILTER_ENABLED"), False):
        raise ValueError(f"{strategy_label} does not support the trend filter (use trend_mode=no_trend)")


BREAKOUT_ATR_SUPPORTED_TIMEFRAMES: tuple[str, ...] = _timeframes(
    _BREAKOUT_ATR_CFG, "supported_timeframes", "strategies.breakout_atr"
)
BREAKOUT_ATR_DEFAULT_TIMEFRAME = str(
    _require(_BREAKOUT_ATR_CFG, "default_timeframe", "strategies.breakout_atr.default_timeframe")
).upper()
BREAKOUT_ATR_DEFAULT_PARAMS: dict[str, Any] = {
    "LOOKBACK_BARS": int(_require(_BREAKOUT_ATR_CFG, "lookback_bars", "strategies.breakout_atr.lookback_bars")),
    "ATR_PERIOD": int(_require(_BREAKOUT_ATR_CFG, "atr_period", "strategies.breakout_atr.atr_period")),
    "ALLOW_SHORT": bool(_require(_BREAKOUT_ATR_CFG, "allow_short", "strategies.breakout_atr.allow_short")),
}
BREAKOUT_ATR_STATE_WARMUP_START = str(
    _require(_BREAKOUT_ATR_CFG, "state_warmup_start", "strategies.breakout_atr.state_warmup_start")
)
BREAKOUT_ATR_PARAM_FIELDS: list[dict[str, Any]] = [
    {"key": "LOOKBACK_BARS", "label": "Lookback (bars)", "type": "number", "min": 2, "max": 2000, "step": 1},
    {"key": "ATR_PERIOD", "label": "ATR", "type": "number", "min": 2, "max": 200, "step": 1},
    {"key": "ALLOW_SHORT", "label": "Allow SELL", "type": "bool"},
]


def normalize_breakout_atr_params(
    overrides: dict[str, Any] | None = None,
    symbol: str | None = None,
    tf: str | None = None,
) -> dict[str, Any]:
    """Gộp default (config.yaml) với override CLI/dashboard, kẹp theo PARAM_FIELDS."""
    _ = symbol, tf
    raw = {**BREAKOUT_ATR_DEFAULT_PARAMS, **(overrides or {})}
    _reject_trend_filter(raw, "Breakout ATR")
    d = BREAKOUT_ATR_DEFAULT_PARAMS
    f = BREAKOUT_ATR_PARAM_FIELDS
    return {
        "LOOKBACK_BARS": _to_int(raw.get("LOOKBACK_BARS"), d["LOOKBACK_BARS"], *_field_range(f, "LOOKBACK_BARS")),
        "ATR_PERIOD": _to_int(raw.get("ATR_PERIOD"), d["ATR_PERIOD"], *_field_range(f, "ATR_PERIOD")),
        "ALLOW_SHORT": _to_bool(raw.get("ALLOW_SHORT"), d["ALLOW_SHORT"]),
    }


SMA_TREND_SUPPORTED_TIMEFRAMES: tuple[str, ...] = _timeframes(
    _SMA_TREND_CFG, "supported_timeframes", "strategies.sma_trend"
)
SMA_TREND_DEFAULT_TIMEFRAME = str(
    _require(_SMA_TREND_CFG, "default_timeframe", "strategies.sma_trend.default_timeframe")
).upper()
SMA_TREND_DEFAULT_PARAMS: dict[str, Any] = {
    "SMA_PERIOD": int(_require(_SMA_TREND_CFG, "sma_period", "strategies.sma_trend.sma_period")),
    "ALLOW_SHORT": bool(_require(_SMA_TREND_CFG, "allow_short", "strategies.sma_trend.allow_short")),
}
SMA_TREND_STATE_WARMUP_START = str(
    _require(_SMA_TREND_CFG, "state_warmup_start", "strategies.sma_trend.state_warmup_start")
)
SMA_TREND_PARAM_FIELDS: list[dict[str, Any]] = [
    {"key": "SMA_PERIOD", "label": "SMA", "type": "number", "min": 2, "max": 2000, "step": 1},
    {"key": "ALLOW_SHORT", "label": "Allow SELL", "type": "bool"},
]


def normalize_sma_trend_params(
    overrides: dict[str, Any] | None = None,
    symbol: str | None = None,
    tf: str | None = None,
) -> dict[str, Any]:
    """Gộp default (config.yaml) với override CLI/dashboard, kẹp theo PARAM_FIELDS."""
    _ = symbol, tf
    raw = {**SMA_TREND_DEFAULT_PARAMS, **(overrides or {})}
    _reject_trend_filter(raw, "SMA Trend")
    d = SMA_TREND_DEFAULT_PARAMS
    f = SMA_TREND_PARAM_FIELDS
    return {
        "SMA_PERIOD": _to_int(raw.get("SMA_PERIOD"), d["SMA_PERIOD"], *_field_range(f, "SMA_PERIOD")),
        "ALLOW_SHORT": _to_bool(raw.get("ALLOW_SHORT"), d["ALLOW_SHORT"]),
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
        key:              Mã định danh duy nhất (ví dụ: "combo", "ma_cross").
        label:            Tên hiển thị (ví dụ: "Combo", "MA Cross").
        default_params:   Dict tham số mặc định khi không có override.
        param_fields:     Định nghĩa field (key/type/min/max) — allowlist override CLI.
        normalize_params: (overrides, symbol) -> dict tham số đã validate.
        add_indicators:   (df, params) -> df với cột chỉ báo thêm vào.
        detect_signals:   (df, symbol, params, sess_mask) -> df với cột signal.
        add_levels:       (df, params, symbol) -> df với cột entry và tham chiếu level.
        supported_timeframes: Các TF được phép; rỗng nghĩa là dùng mọi TF hệ thống.
        default_timeframe: TF mặc định của strategy trên CLI export.
        tuned_symbols: Symbol có tham số riêng theo strategy — dùng để
            dashboard ưu tiên nhóm symbol có cấu hình vận hành rõ ràng.
        state_warmup_start: Mốc ngày (vd "2025-01-01") cho chiến lược CÓ TRẠNG
            THÁI (giữ lệnh qua nhiều bar): chart nạp dữ liệu từ mốc này rồi mới
            cắt cửa sổ hiển thị, để tín hiệu không đổi theo cửa sổ đang xem.
            None = không áp dụng (combo/ma_cross).

    Invariant:
        Pipeline phải gọi theo thứ tự:
        normalize_params → add_indicators → detect_signals → add_levels.
        Mỗi bước nhận output của bước trước làm input.

    Thêm chiến lược mới — checklist:
        1. Viết add_<tên>_indicators (chỉ tính chỉ báo, không có default
           ngầm) trong core_python/indicator.py.
        2. Viết detect_<tên>_signals (CHỈ quyết định khi nào/hướng nào,
           không entry/level) trong 1 file strategies/<tên>.py mới —
           không giữ tham số, không sửa DataFrame input tại chỗ.
        3. Viết add_<tên>_levels (entry/tham chiếu level) trong
           core_python/levels.py — tách riêng khỏi strategies/<tên>.py.
        4. Thêm mục `strategies.<tên>` vào config.yaml (giá trị thật) +
           viết default_params/param_fields/normalize_params ở đây
           (configuration.py), theo đúng mẫu Combo/MA Cross bên trên.
        5. Đăng ký một StrategySpec mới vào dict STRATEGIES bên dưới.
    """

    key: str
    label: str
    default_params: dict[str, Any]
    param_fields: list[dict[str, Any]]
    normalize_params: Callable[..., dict[str, Any]]
    add_indicators: Callable[..., pd.DataFrame]
    detect_signals: Callable[..., pd.DataFrame]
    add_levels: Callable[..., pd.DataFrame]
    recommended_timeframes: tuple[str, ...] = ()
    supported_timeframes: tuple[str, ...] = ()
    default_timeframe: str | None = None
    tuned_symbols: tuple[str, ...] = ()
    state_warmup_start: str | None = None


# Danh sách chiến lược được hỗ trợ. Key dùng lowercase để match với query
# param từ dashboard/CLI.
STRATEGIES: dict[str, StrategySpec] = {
    "combo": StrategySpec(
        key="combo",
        label="Combo",
        default_params=COMBO_DEFAULT_PARAMS,
        param_fields=COMBO_PARAM_FIELDS,
        normalize_params=normalize_combo_params,
        add_indicators=add_combo_indicators,
        detect_signals=detect_combo_signals,
        add_levels=add_combo_levels,
        recommended_timeframes=COMBO_RECOMMENDED_TIMEFRAMES,
        default_timeframe=COMBO_DEFAULT_TIMEFRAME,
        tuned_symbols=tuple(COMBO_SYMBOL_X),
    ),
    "ma_cross": StrategySpec(
        key="ma_cross",
        label="MA Cross",
        default_params=MA_CROSS_DEFAULT_PARAMS,
        param_fields=MA_CROSS_PARAM_FIELDS,
        normalize_params=normalize_ma_cross_params,
        add_indicators=add_ma_cross_indicators,
        detect_signals=detect_ma_cross_signals,
        add_levels=add_ma_cross_levels,
        recommended_timeframes=MA_CROSS_RECOMMENDED_TIMEFRAMES,
        supported_timeframes=MA_CROSS_SUPPORTED_TIMEFRAMES,
        default_timeframe=MA_CROSS_DEFAULT_TIMEFRAME,
    ),
    "breakout_atr": StrategySpec(
        key="breakout_atr",
        label="Breakout ATR (research)",
        default_params=BREAKOUT_ATR_DEFAULT_PARAMS,
        param_fields=BREAKOUT_ATR_PARAM_FIELDS,
        normalize_params=normalize_breakout_atr_params,
        add_indicators=add_breakout_atr_indicators,
        detect_signals=detect_breakout_atr_signals,
        add_levels=add_breakout_atr_levels,
        recommended_timeframes=BREAKOUT_ATR_SUPPORTED_TIMEFRAMES,
        supported_timeframes=BREAKOUT_ATR_SUPPORTED_TIMEFRAMES,
        default_timeframe=BREAKOUT_ATR_DEFAULT_TIMEFRAME,
        state_warmup_start=BREAKOUT_ATR_STATE_WARMUP_START,
    ),
    "sma_trend": StrategySpec(
        key="sma_trend",
        label="SMA Trend (research)",
        default_params=SMA_TREND_DEFAULT_PARAMS,
        param_fields=SMA_TREND_PARAM_FIELDS,
        normalize_params=normalize_sma_trend_params,
        add_indicators=add_sma_trend_indicators,
        detect_signals=detect_sma_trend_signals,
        add_levels=add_sma_trend_levels,
        recommended_timeframes=SMA_TREND_SUPPORTED_TIMEFRAMES,
        supported_timeframes=SMA_TREND_SUPPORTED_TIMEFRAMES,
        default_timeframe=SMA_TREND_DEFAULT_TIMEFRAME,
        state_warmup_start=SMA_TREND_STATE_WARMUP_START,
    ),
}


def get_strategy(key: str) -> StrategySpec:
    """
    Tra cứu chiến lược theo key (không phân biệt hoa/thường).

    Args:
        key: Mã chiến lược (ví dụ: "combo", "COMBO", "ma_cross").

    Returns:
        StrategySpec tương ứng.

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
        symbol: Mã symbol — dùng để resolve symbol-specific params (X, ...).
        tf: Mã timeframe — bị từ chối nếu ngoài spec.supported_timeframes.
        bars: DataFrame OHLCV [bartime, open, high, low, close, volume],
            đã sort tăng dần (vd. output của db_connector.load()).
        overrides: Tham số người dùng override (vd. từ CLI --param).

    Returns:
        DataFrame OHLCV gốc + cột chỉ báo + signal + entry/tham chiếu level.

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
    params = {**params, "_RUN_TF": tf}
    if "ENTRY_TF" in params:
        params["ENTRY_TF"] = tf
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

    Hàm này giữ pipeline single-timeframe hiện tại không bị chồng chéo, đồng
    thời cung cấp đường chạy đúng bản chất cho các strategy cần trend filter:
        trend_bars -> KNN trend_bias
        entry_bars -> entry indicators/raw_signal
        merge trend đã đóng -> filter signal -> levels
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
    # entry_tf (khung thực sự vào lệnh), không phải trend_tf (chỉ là tham
    # chiếu lọc hướng, không phải khung đặt lệnh).
    params = spec.normalize_params(trend_overrides, symbol, entry_tf)
    params = {**params, "_RUN_TF": entry_tf}
    if params.get("TREND_TYPE") != "knn":
        raise ValueError("Only KNN trend reference is implemented.")

    entry = spec.add_indicators(entry_bars, params)
    trend = add_knn_trend_indicators(trend_bars, knn_trend_indicator_params(params))
    merged = merge_trend_reference(entry, trend)
    enriched = spec.detect_signals(merged, symbol=symbol, params=params)
    enriched = spec.add_levels(enriched, params, symbol)
    return enriched
