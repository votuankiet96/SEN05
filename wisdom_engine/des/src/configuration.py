"""
Tham số chiến lược + sổ đăng ký nối pipeline cho des (mô phỏng engine OG).

Mô tả:
    File này KHÔNG đọc dữ liệu và KHÔNG tính chỉ báo. Nó CHỈ ĐỌC + VALIDATE
    tham số, không tự quyết định giá trị, và không giữ giá trị mặc định nào
    trùng với des_config.yaml:
    1. Tham số của từng chiến lược (Combo, MA Cross, MACD Break) — bắt buộc có trong
       des_config.yaml (mục `strategies:`), đây là khu vực operator chỉnh trực
       tiếp (danh sách symbol, chu kỳ chỉ báo, session hours...).
       Thiếu bất kỳ key nào sẽ raise lỗi rõ ràng ngay khi import — không có
       fallback âm thầm nào trong code. PARAM_FIELDS (giới hạn min/max cho
       validate input, không phải giá trị vận hành) + normalize_*_params()
       merge+validate vẫn là code. strategies/<tên>.py và entries.py đều KHÔNG
       giữ tham số nào — chỉ nhận dict params đã hoàn chỉnh từ đây.
    2. Sổ đăng ký chiến lược (StrategySpec/STRATEGIES/run_strategy) — nối
       indicator.py (chỉ báo) + strategies/<tên>.py (tín hiệu BUY/SELL) +
       entries.py (entry) thành 1 pipeline hoàn chỉnh, tra theo key
       ("combo"/"ma_cross"/"macd_break").

    Bộ lọc xu hướng KNN (tham chiếu khung lớn) có sẵn nhưng mặc định TẮT
    (``trend_filter_enabled: false`` trong des_config.yaml). Khi tắt, luồng
    live chạy 1 timeframe duy nhất, không đọc nến khung lớn.

Đầu ra:
    normalize_combo_params(overrides, symbol, tf), normalize_ma_cross_params(...),
    normalize_macd_break_params(...).
    STRATEGIES: dict[str, StrategySpec] — tra cứu theo key.
    get_strategy(key): StrategySpec hoặc raise KeyError.
    run_strategy(key, symbol, tf, bars, overrides): chạy trọn pipeline 1
        chiến lược (thứ tự xem StrategySpec.Invariant bên dưới).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from wisdom_engine.des.src.indicator import (
    add_combo_indicators,
    add_ma_cross_indicators,
    add_macd_break_indicators,
    merge_trend_reference,
    timeframe_minutes,
)
from wisdom_engine.des.src.rules.entries import (
    add_combo_entries,
    add_ma_cross_entries,
    add_macd_break_entries,
)
from wisdom_engine.des.src.rules.trend_tf import (
    build_trend_frame,
    max_trend_warmup_bars,
    parse_methods,
    uses_entry_timeframe,
    uses_first_aligned_per_run,
)
from wisdom_engine.des.src.strategies.combo import detect_combo_signals
from wisdom_engine.des.src.strategies.ma_cross import detect_ma_cross_signals
from wisdom_engine.des.src.strategies.macd_break import detect_macd_break_signals

# des_config.yaml parse đúng 1 lần cho cả tiến trình. Thiếu file = raise ngay,
# không trả dict rỗng: tiến trình live không có config thì không có gì để chạy.
CONFIG_PATH = Path(__file__).resolve().parents[1] / "des_config.yaml"
with CONFIG_PATH.open("r", encoding="utf-8") as _config_file:
    CONFIG: dict[str, Any] = yaml.safe_load(_config_file) or {}


def require(cfg: dict[str, Any], key: str, path: str) -> Any:
    """Đọc bắt buộc 1 key — không có giá trị mặc định nào trong code.
    Thiếu key sẽ raise ngay, chỉ rõ đúng đường dẫn cần điền.
    """
    if key not in cfg:
        raise KeyError(f"des_config.yaml thiếu '{path}'")
    return cfg[key]


_STRATEGIES_CFG: dict[str, Any] = require(CONFIG, "strategies", "strategies")
_COMBO_CFG: dict[str, Any] = require(_STRATEGIES_CFG, "combo", "strategies.combo")
_MA_CROSS_CFG: dict[str, Any] = require(_STRATEGIES_CFG, "ma_cross", "strategies.ma_cross")
_MACD_BREAK_CFG: dict[str, Any] = require(_STRATEGIES_CFG, "macd_break", "strategies.macd_break")


# =============================================================================
# 1. Tham số chiến lược — giá trị bắt buộc lấy từ des_config.yaml (mục
#    `strategies:`) — không có bản sao/fallback nào trong code. Chỉ
#    PARAM_FIELDS (schema validate: min/max) và normalize_*_params (hàm
#    merge+validate) là code, vì đó là cấu trúc hệ thống chứ không phải
#    giá trị vận hành.
# =============================================================================


def _to_int(value: object, default: int, min_value: int, max_value: int) -> int:
    """
    Chuyển input sang số nguyên và kẹp trong khoảng an toàn.

    Nếu input sai kiểu hoặc bỏ trống, dùng default đã lấy từ des_config.yaml.
    """
    try:
        parsed = int(float(value)) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(parsed, max_value))


def _field_range(fields: list[dict[str, Any]], key: str) -> tuple[float, float]:
    """Lấy (min, max) của 1 field trong PARAM_FIELDS — nguồn duy nhất cho
    giới hạn clamp, tránh lặp lại số min/max ở normalize_*_params.
    """
    for item in fields:
        if item["key"] == key:
            return item["min"], item["max"]
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
    Lấy 3 thông tin điều khiển trend từ config của một strategy. Khung trend KHÔNG có giá trị mặc định chung:
    mỗi (symbol, khung vào lệnh) có khung riêng trong trend_tf_by_symbol.
    """
    return {
        "TREND_FILTER_ENABLED": _to_bool(
            require(strategy_cfg, "trend_filter_enabled", f"{path}.trend_filter_enabled"),
            False,
        ),
        "TREND_METHODS": parse_methods(
            require(strategy_cfg, "trend_methods", f"{path}.trend_methods"),
            label=f"{path}.trend_methods",
        ),
        "ENTRY_TF": entry_tf,
    }


def _resolve_trend_methods(raw: dict[str, Any], defaults: dict[str, Any]) -> tuple[str, ...]:
    """
    Khối xác nhận xu hướng được bật: override (list hoặc chuỗi cách nhau bằng dấu phẩy) nếu có, trống/thiếu = mặc định config.
    """
    raw_methods = raw.get("TREND_METHODS")
    if raw_methods is None or raw_methods == "":
        return defaults["TREND_METHODS"]
    return parse_methods(raw_methods)


def _normalize_trend_reference_params(
    raw: dict[str, Any],
    defaults: dict[str, Any],
    *,
    trend_timeframes: tuple[str, ...],
    entry_timeframes: tuple[str, ...],
) -> dict[str, Any]:
    """
    Chuẩn hóa phần trend reference chung cho mọi strategy.

    Chi tiết công thức của từng khối xác nhận xu hướng (vd KNN) không đi qua
    đây; chúng nằm trong trend_methods/<tên>.py và chỉ được dùng lúc dựng
    khung trend (rules/trend_tf.build_trend_frame).

    TREND_METHODS: tên các khối được bật; override có thể là list hoặc chuỗi
    cách nhau bằng dấu phẩy ("knn,<khối khác>"), trống/thiếu = mặc định config.

    TREND_TF: đè tường minh hoặc ô của (symbol, khung vào lệnh) trong trend_tf_by_symbol; không có thì để trống ("")
    — nơi nào thật sự cần khung trend (chạy có trend) phải tự báo lỗi, không có khung mặc định chung. Riêng khi mọi
    khối đang bật tính trên CHÍNH khung của tín hiệu (chính sách same_timeframe ở rules/trend_tf.POLICY, vd breakout) thì TREND_TF = khung vào lệnh và chỉ
    phải thuộc khung của chiến lược, không bị giới hạn trong trend_timeframes (H1–H4 chỉ áp cho khối cần khung lớn hơn).
    """
    trend_methods = _resolve_trend_methods(raw, defaults)
    allowed_trend_tfs = entry_timeframes if uses_entry_timeframe(trend_methods) else trend_timeframes

    trend_tf = str(raw.get("TREND_TF") or "").strip()  # đã tra theo ô (hoặc = khung vào lệnh) ở _raw_params_with_cell_trend_tf

    return {
        "TREND_FILTER_ENABLED": _to_bool(
            raw.get("TREND_FILTER_ENABLED"),
            defaults["TREND_FILTER_ENABLED"],
        ),
        "TREND_METHODS": trend_methods,
        "TREND_TF": _to_choice(trend_tf, "", allowed_trend_tfs, "TREND_TF") if trend_tf else "",
        "ENTRY_TF": _to_choice(
            raw.get("ENTRY_TF"),
            defaults["ENTRY_TF"],
            entry_timeframes,
            "ENTRY_TF",
        ),
    }


def check_trend_timeframe(entry_tf: str, trend_tf: str) -> None:
    """
    Khung trend phải LỚN HƠN HOẶC BẰNG khung tín hiệu (quy tắc chốt 2026-10-16, CLAUDE.md mục 6).

    Lý do: nến trend luôn là nến đã đóng hẳn TRƯỚC khi nến tín hiệu mở (indicator.merge_trend_reference),
    nên trend "có trước" tín hiệu, không chồng thời gian với nến tín hiệu và không có look-ahead. Khung
    trend NHỎ hơn khung tín hiệu thì hoặc trễ một nến tín hiệu (quy tắc ghép hiện tại), hoặc phải dùng nến
    nhỏ nằm trong lòng nến tín hiệu (trend trùng thông tin với tín hiệu, bộ lọc gần như vô hiệu) — bị cấm.
    Khung bằng nhau vẫn được: lấy nến ngay trước nến tín hiệu.

    Raises:
        ValueError: khung trend nhỏ hơn khung tín hiệu, hoặc mã khung không hợp lệ.
    """
    entry_code = str(entry_tf).strip().upper()
    trend_code = str(trend_tf).strip().upper()
    if timeframe_minutes(trend_code) < timeframe_minutes(entry_code):
        raise ValueError(
            f"TREND_TF {trend_code} must not be smaller than the entry timeframe {entry_code} "
            f"(trend timeframe must be >= entry timeframe)."
        )


def _strategy_trend_tf_by_symbol(
    strategy_cfg: dict[str, Any],
    path: str,
    *,
    symbols: tuple[str, ...],
    entry_timeframes: tuple[str, ...],
    trend_timeframes: tuple[str, ...],
) -> dict[str, dict[str, str]]:
    """
    Đọc strategies.<tên>.trend_tf_by_symbol (BẮT BUỘC): khung trend riêng cho từng (symbol, khung vào lệnh).

    Dạng: trend_tf_by_symbol: {BTCUSD: {H1: H4, H2: H2, ...}, ...}. Mỗi symbol vận hành (strategies.combo.symbols) phải
    có đủ MỌI khung vào lệnh của chiến lược; không có khung mặc định chung để dự phòng. Kiểm hết lúc nạp config và báo
    MỌI ô sai hoặc thiếu trong một lần: symbol lạ (ngoài strategies.combo.symbols), khung vào lệnh ngoài tập chiến lược
    chạy, khung trend ngoài `trend_timeframes`, khung trend nhỏ hơn khung vào lệnh (check_trend_timeframe), hoặc thiếu ô.
    """
    raw = require(strategy_cfg, "trend_tf_by_symbol", f"{path}.trend_tf_by_symbol")
    if not isinstance(raw, dict):
        raise TypeError(f"des_config.yaml: '{path}.trend_tf_by_symbol' must map symbol -> {{entry timeframe: trend timeframe}}.")
    known_symbols = {str(symbol).upper() for symbol in symbols}
    problems: list[str] = []
    result: dict[str, dict[str, str]] = {}
    declared: dict[str, set[str]] = {}
    for symbol_key, per_timeframe in raw.items():
        symbol = str(symbol_key).strip().upper()
        if symbol not in known_symbols:
            problems.append(f"{symbol}: unknown symbol (valid: {', '.join(sorted(known_symbols))})")
            continue
        if not isinstance(per_timeframe, dict):
            problems.append(f"{symbol}: must map entry timeframe -> trend timeframe")
            continue
        declared[symbol] = {str(entry_key).strip().upper() for entry_key in per_timeframe}
        cells: dict[str, str] = {}
        for entry_key, trend_value in per_timeframe.items():
            entry_tf = str(entry_key).strip().upper()
            trend_tf = str(trend_value).strip().upper()
            if entry_tf not in entry_timeframes:
                problems.append(f"{symbol} {entry_tf}: entry timeframe not in {', '.join(entry_timeframes)}")
            elif trend_tf not in trend_timeframes:
                problems.append(f"{symbol} {entry_tf}: trend timeframe {trend_tf} not in {', '.join(trend_timeframes)}")
            else:
                try:
                    check_trend_timeframe(entry_tf, trend_tf)
                except ValueError as exc:
                    problems.append(f"{symbol} {entry_tf}: {exc}")
                else:
                    cells[entry_tf] = trend_tf
        result[symbol] = cells
    for symbol in sorted(known_symbols):
        missing = [entry_tf for entry_tf in entry_timeframes if entry_tf not in declared.get(symbol, set())]
        if missing:
            problems.append(f"{symbol}: no trend timeframe for entry timeframe(s) {', '.join(missing)}")
    if problems:
        raise ValueError(f"des_config.yaml: invalid '{path}.trend_tf_by_symbol' — " + "; ".join(problems))
    return result


def resolve_trend_tf(
    by_symbol: dict[str, dict[str, str]],
    symbol: object,
    entry_tf: object,
) -> str:
    """
    Khung trend cho (symbol, khung vào lệnh): ô khai trong trend_tf_by_symbol. Không có ô (symbol ngoài 11 symbol
    vận hành) thì trả "" = chưa xác định — KHÔNG có khung mặc định chung; nơi nào cần khung trend phải tự báo lỗi.
    """
    cells = by_symbol.get(str(symbol or "").strip().upper(), {})
    return cells.get(str(entry_tf or "").strip().upper(), "")


def _raw_params_with_cell_trend_tf(
    defaults: dict[str, Any],
    overrides: dict[str, Any] | None,
    by_symbol: dict[str, dict[str, str]],
    symbol: object,
    entry_tf: object,
) -> dict[str, Any]:
    """
    defaults < overrides như cũ, nhưng khung trend tra theo (symbol, khung vào lệnh) trong trend_tf_by_symbol.
    TREND_TF người dùng truyền (CLI --trend-tf, dashboard, run_strategy_with_trend_reference) vẫn thắng.

    Ngoại lệ: khi mọi khối đang bật tính trên CHÍNH khung của tín hiệu (chính sách same_timeframe ở rules/trend_tf.POLICY, vd breakout) thì khung trend LÀ
    khung vào lệnh (không tra bảng); TREND_TF truyền vào mà khác khung vào lệnh là lỗi.
    """
    given = overrides or {}
    raw = {**defaults, **given}
    methods = _resolve_trend_methods(raw, defaults)
    if uses_entry_timeframe(methods):
        entry = str(entry_tf or "").strip().upper()
        explicit = str(given.get("TREND_TF") or "").strip().upper()
        if explicit and explicit != entry:
            raise ValueError(
                f"Trend method(s) {', '.join(methods)} always use the entry timeframe {entry}; TREND_TF {explicit} is not allowed."
            )
        raw["TREND_TF"] = entry
    elif not str(given.get("TREND_TF") or "").strip():
        raw["TREND_TF"] = resolve_trend_tf(by_symbol, symbol, entry_tf)
    return raw


def _strategy_alternating_default(strategy_cfg: dict[str, Any], path: str) -> dict[str, Any]:
    """
    Lấy cờ lọc lặp cùng chiều của một strategy từ config
    (strategies.<tên>.alternating_filter_enabled). Logic lọc nằm ở
    alternating.py; ở đây chỉ đọc/chuẩn hóa cờ.
    """
    return {
        "ALTERNATING_FILTER_ENABLED": _to_bool(
            require(strategy_cfg, "alternating_filter_enabled", f"{path}.alternating_filter_enabled"),
            False,
        ),
    }


def _normalize_alternating_param(raw: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """
    Chuẩn hóa cờ lọc lặp cùng chiều: override > default từ config.
    """
    return {
        "ALTERNATING_FILTER_ENABLED": _to_bool(
            raw.get("ALTERNATING_FILTER_ENABLED"),
            defaults["ALTERNATING_FILTER_ENABLED"],
        ),
    }


# --- Combo ---

# Khung thời gian Combo chạy trên luồng live — không phải giới hạn cứng của
# thuật toán, nhưng live_worker dùng đúng danh sách này để định tuyến candle
# event. Từ des_config.yaml: strategies.combo.recommended_timeframes.
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

def _operating_symbols(strategy_cfg: dict[str, Any], path: str) -> tuple[str, ...]:
    """Đọc `<path>.symbols`: danh sách tên SYMBOL (theo SQL/DFS) không rỗng và không trùng; chuẩn hoá thành chữ hoa."""
    raw = require(strategy_cfg, "symbols", f"{path}.symbols")
    if not isinstance(raw, (list, tuple)) or not raw:
        raise TypeError(f"des_config.yaml: '{path}.symbols' must be a non-empty list of symbol names.")
    symbols = tuple(str(item).strip().upper() for item in raw)
    if any(not symbol for symbol in symbols):
        raise ValueError(f"des_config.yaml: '{path}.symbols' contains an empty symbol name.")
    duplicated = sorted({symbol for symbol in symbols if symbols.count(symbol) > 1})
    if duplicated:
        raise ValueError(f"des_config.yaml: '{path}.symbols' lists a symbol twice: {', '.join(duplicated)}.")
    return symbols


# Vũ trụ vận hành của des, từ des_config.yaml: strategies.combo.symbols (tên SYMBOL theo SQL/DFS). Dùng chung cho CẢ BA
# chiến lược: live_worker (định tuyến sự kiện nến), chart_simul (danh mục) và bảng trend_tf_by_symbol (phải đủ ô cho từng
# symbol). Entry của combo không còn đệm theo symbol (2026-10-21, đồng bộ we_core): BUY = high, SELL = low của nến tín hiệu.
OPERATING_SYMBOLS: tuple[str, ...] = _operating_symbols(_COMBO_CFG, "strategies.combo")

# Khung trend riêng cho từng (symbol, khung vào lệnh), từ des_config.yaml:
# strategies.combo.trend_tf_by_symbol (BẮT BUỘC, đủ mọi symbol × khung vào lệnh).
COMBO_TREND_TF_BY_SYMBOL: dict[str, dict[str, str]] = _strategy_trend_tf_by_symbol(
    _COMBO_CFG,
    "strategies.combo",
    symbols=OPERATING_SYMBOLS,
    entry_timeframes=COMBO_RECOMMENDED_TIMEFRAMES,
    trend_timeframes=COMBO_TREND_TIMEFRAMES,
)

# Giờ UTC được phép giao dịch cho từng symbol, từ des_config.yaml:
# strategies.combo.session_hours_utc. Symbol không liệt kê (hoặc để trống) =
# giao dịch mọi giờ (không lọc theo session).
COMBO_SESSION_HOURS_UTC: dict[str, list[int]] = {
    str(symbol).upper(): [int(h) for h in (hours or [])]
    for symbol, hours in require(
        _COMBO_CFG, "session_hours_utc", "strategies.combo.session_hours_utc"
    ).items()
}

# Tham số mặc định — cũng là nguồn allowlist cho override.
COMBO_DEFAULT_PARAMS: dict[str, Any] = {
    "MA_PERIOD": int(require(_COMBO_CFG, "ma_period", "strategies.combo.ma_period")),
    "MACD_FAST": int(require(_COMBO_CFG, "macd_fast", "strategies.combo.macd_fast")),
    "MACD_SLOW": int(require(_COMBO_CFG, "macd_slow", "strategies.combo.macd_slow")),
    "MACD_SIGNAL": int(require(_COMBO_CFG, "macd_signal", "strategies.combo.macd_signal")),
    "ATR_PERIOD": int(require(_COMBO_CFG, "atr_period", "strategies.combo.atr_period")),
    "SESSION_HOURS_UTC": [],
    **_strategy_trend_defaults(_COMBO_CFG, "strategies.combo", entry_tf=COMBO_DEFAULT_TIMEFRAME),
    **_strategy_alternating_default(_COMBO_CFG, "strategies.combo"),
}

# Định nghĩa field cho từng tham số (type/min/max) — dùng làm allowlist khi
# lọc override key hợp lệ. Đây là giới hạn AN TOÀN đầu vào (validation),
# không phải giá trị vận hành — không nằm trong des_config.yaml.
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
    {"key": "SESSION_HOURS_UTC", "label": "UTC Hours", "type": "text"},
    {"key": "TREND_FILTER_ENABLED", "label": "Trend Filter", "type": "bool"},
    {"key": "TREND_TF", "label": "Trend TF", "type": "text"},
    {"key": "ALTERNATING_FILTER_ENABLED", "label": "Alternating Filter", "type": "bool"},
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
    """Tham số riêng theo symbol cho Combo: giờ giao dịch (session_hours_utc; rỗng = mọi giờ)."""
    key = str(symbol or "").strip().upper()
    return {"session_hours_utc": list(COMBO_SESSION_HOURS_UTC.get(key, []))}


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
        symbol: Mã symbol để lấy session_hours và khung trend riêng.
        tf: Timeframe đang chạy (khung vào lệnh) — cùng với symbol để tra khung trend mặc định
            trong trend_tf_by_symbol (TREND_TF truyền trong overrides vẫn thắng).

    Returns:
        Dict tham số đã validate đầy đủ — an toàn để truyền vào pipeline.
    """
    raw = _raw_params_with_cell_trend_tf(COMBO_DEFAULT_PARAMS, overrides, COMBO_TREND_TF_BY_SYMBOL, symbol, tf)
    d = COMBO_DEFAULT_PARAMS
    f = COMBO_PARAM_FIELDS
    symbol_params = _combo_symbol_params(symbol)

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
        **_normalize_alternating_param(raw, d),
    }


# --- MA Cross ---

# MA Cross chỉ được phép chạy trên các timeframe đã chốt trong des_config.yaml:
# strategies.ma_cross.supported_timeframes.
MA_CROSS_SUPPORTED_TIMEFRAMES: tuple[str, ...] = tuple(
    str(tf).upper()
    for tf in require(
        _MA_CROSS_CFG, "supported_timeframes", "strategies.ma_cross.supported_timeframes"
    )
)
# MA Cross không có khái niệm "recommended" tách rời "supported" — tập TF
# chạy live đúng bằng tập TF được hỗ trợ, không lặp lại danh sách này trong
# des_config.yaml.
MA_CROSS_RECOMMENDED_TIMEFRAMES: tuple[str, ...] = MA_CROSS_SUPPORTED_TIMEFRAMES
MA_CROSS_DEFAULT_TIMEFRAME = str(
    require(_MA_CROSS_CFG, "default_timeframe", "strategies.ma_cross.default_timeframe")
).upper()
MA_CROSS_TREND_TIMEFRAMES: tuple[str, ...] = _trend_timeframes(
    _MA_CROSS_CFG,
    "strategies.ma_cross",
)
# Khung trend riêng cho từng (symbol, khung vào lệnh): strategies.ma_cross.trend_tf_by_symbol (bắt buộc, đủ ô).
MA_CROSS_TREND_TF_BY_SYMBOL: dict[str, dict[str, str]] = _strategy_trend_tf_by_symbol(
    _MA_CROSS_CFG,
    "strategies.ma_cross",
    symbols=OPERATING_SYMBOLS,
    entry_timeframes=MA_CROSS_SUPPORTED_TIMEFRAMES,
    trend_timeframes=MA_CROSS_TREND_TIMEFRAMES,
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
    **_strategy_alternating_default(_MA_CROSS_CFG, "strategies.ma_cross"),
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
    {"key": "ALTERNATING_FILTER_ENABLED", "label": "Alternating Filter", "type": "bool"},
]


def normalize_ma_cross_params(
    overrides: dict[str, Any] | None = None,
    symbol: str | None = None,
    tf: str | None = None,
) -> dict[str, Any]:
    """
    Gộp tham số MA Cross thành bộ tham số cuối cùng, đã validate.

    Thứ tự ưu tiên:
        1. Default trong des_config.yaml.
        2. Override truyền vào.

    Hàm này cũng kiểm tra các quan hệ bắt buộc:
        - FAST_MA phải nhỏ hơn SLOW_MA.
        - MACD_FAST phải nhỏ hơn MACD_SLOW.

    symbol/tf (khung vào lệnh) dùng để tra khung trend mặc định trong trend_tf_by_symbol;
    TREND_TF truyền trong overrides vẫn thắng.
    """
    raw = _raw_params_with_cell_trend_tf(
        MA_CROSS_DEFAULT_PARAMS, overrides, MA_CROSS_TREND_TF_BY_SYMBOL, symbol, tf
    )
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
        "MACD_SIGNAL": _to_int(
            raw.get("MACD_SIGNAL"), d["MACD_SIGNAL"], *_field_range(f, "MACD_SIGNAL")
        ),
        "ATR_PERIOD": _to_int(
            raw.get("ATR_PERIOD"), d["ATR_PERIOD"], *_field_range(f, "ATR_PERIOD")
        ),
        **_normalize_trend_reference_params(
            raw,
            d,
            trend_timeframes=MA_CROSS_TREND_TIMEFRAMES,
            entry_timeframes=MA_CROSS_SUPPORTED_TIMEFRAMES,
        ),
        **_normalize_alternating_param(raw, d),
    }


# --- MACD Break ---

# MACD Break chỉ được phép chạy trên các timeframe đã chốt trong des_config.yaml:
# strategies.macd_break.supported_timeframes.
MACD_BREAK_SUPPORTED_TIMEFRAMES: tuple[str, ...] = tuple(
    str(tf).upper()
    for tf in require(
        _MACD_BREAK_CFG, "supported_timeframes", "strategies.macd_break.supported_timeframes"
    )
)
# Như MA Cross: tập TF chạy live đúng bằng tập TF được hỗ trợ.
MACD_BREAK_RECOMMENDED_TIMEFRAMES: tuple[str, ...] = MACD_BREAK_SUPPORTED_TIMEFRAMES
MACD_BREAK_DEFAULT_TIMEFRAME = str(
    require(_MACD_BREAK_CFG, "default_timeframe", "strategies.macd_break.default_timeframe")
).upper()
MACD_BREAK_TREND_TIMEFRAMES: tuple[str, ...] = _trend_timeframes(
    _MACD_BREAK_CFG,
    "strategies.macd_break",
)
# Khung trend riêng cho từng (symbol, khung vào lệnh): strategies.macd_break.trend_tf_by_symbol (bắt buộc, đủ ô).
MACD_BREAK_TREND_TF_BY_SYMBOL: dict[str, dict[str, str]] = _strategy_trend_tf_by_symbol(
    _MACD_BREAK_CFG,
    "strategies.macd_break",
    symbols=OPERATING_SYMBOLS,
    entry_timeframes=MACD_BREAK_SUPPORTED_TIMEFRAMES,
    trend_timeframes=MACD_BREAK_TREND_TIMEFRAMES,
)

MACD_BREAK_DEFAULT_PARAMS: dict[str, Any] = {
    "LOOKBACK_BARS": int(
        require(_MACD_BREAK_CFG, "lookback_bars", "strategies.macd_break.lookback_bars")
    ),
    "MACD_FAST": int(require(_MACD_BREAK_CFG, "macd_fast", "strategies.macd_break.macd_fast")),
    "MACD_SLOW": int(require(_MACD_BREAK_CFG, "macd_slow", "strategies.macd_break.macd_slow")),
    "MACD_SIGNAL": int(
        require(_MACD_BREAK_CFG, "macd_signal", "strategies.macd_break.macd_signal")
    ),
    "ATR_PERIOD": int(require(_MACD_BREAK_CFG, "atr_period", "strategies.macd_break.atr_period")),
    **_strategy_trend_defaults(
        _MACD_BREAK_CFG,
        "strategies.macd_break",
        entry_tf=MACD_BREAK_DEFAULT_TIMEFRAME,
    ),
    **_strategy_alternating_default(_MACD_BREAK_CFG, "strategies.macd_break"),
}

MACD_BREAK_PARAM_FIELDS: list[dict[str, Any]] = [
    # max 300 (cùng dải với we_core): nến cuối chỉ có prior_high_close khi cửa sổ
    # des đọc (redis.input.snapshot_bars, hiện 500) có ít nhất LOOKBACK_BARS + 1
    # nến — lookback dài hơn số nến đọc thì chiến lược im lặng không bao giờ bắn.
    {
        "key": "LOOKBACK_BARS",
        "label": "Lookback (bars)",
        "type": "number",
        "min": 2,
        "max": 300,
        "step": 1,
    },
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
    {"key": "ALTERNATING_FILTER_ENABLED", "label": "Alternating Filter", "type": "bool"},
]


def normalize_macd_break_params(
    overrides: dict[str, Any] | None = None,
    symbol: str | None = None,
    tf: str | None = None,
) -> dict[str, Any]:
    """
    Gộp tham số MACD Break (default từ des_config.yaml + override), validate và
    kẹp theo PARAM_FIELDS. MACD_FAST phải nhỏ hơn MACD_SLOW.

    symbol/tf (khung vào lệnh) dùng để tra khung trend mặc định trong trend_tf_by_symbol;
    TREND_TF truyền trong overrides vẫn thắng.
    """
    raw = _raw_params_with_cell_trend_tf(
        MACD_BREAK_DEFAULT_PARAMS, overrides, MACD_BREAK_TREND_TF_BY_SYMBOL, symbol, tf
    )
    d = MACD_BREAK_DEFAULT_PARAMS
    f = MACD_BREAK_PARAM_FIELDS

    macd_fast = _to_int(raw.get("MACD_FAST"), d["MACD_FAST"], *_field_range(f, "MACD_FAST"))
    macd_slow = _to_int(raw.get("MACD_SLOW"), d["MACD_SLOW"], *_field_range(f, "MACD_SLOW"))
    if macd_fast >= macd_slow:
        raise ValueError("MACD_FAST must be smaller than MACD_SLOW")

    return {
        "LOOKBACK_BARS": _to_int(
            raw.get("LOOKBACK_BARS"), d["LOOKBACK_BARS"], *_field_range(f, "LOOKBACK_BARS")
        ),
        "MACD_FAST": macd_fast,
        "MACD_SLOW": macd_slow,
        "MACD_SIGNAL": _to_int(
            raw.get("MACD_SIGNAL"), d["MACD_SIGNAL"], *_field_range(f, "MACD_SIGNAL")
        ),
        "ATR_PERIOD": _to_int(
            raw.get("ATR_PERIOD"), d["ATR_PERIOD"], *_field_range(f, "ATR_PERIOD")
        ),
        **_normalize_trend_reference_params(
            raw,
            d,
            trend_timeframes=MACD_BREAK_TREND_TIMEFRAMES,
            entry_timeframes=MACD_BREAK_SUPPORTED_TIMEFRAMES,
        ),
        **_normalize_alternating_param(raw, d),
    }


# =============================================================================
# 2. Sổ đăng ký chiến lược — nối indicator.py với từng file strategies/<tên>.py
#    thành 1 pipeline hoàn chỉnh, tra theo key ("combo"/"ma_cross"/"macd_break").
# =============================================================================


@dataclass(frozen=True)
class StrategySpec:
    """
    Mô tả đầy đủ một chiến lược giao dịch — bất biến sau khi tạo.

    Thuộc tính:
        key:              Mã định danh duy nhất ("combo", "ma_cross", "macd_break").
        normalize_params: (overrides, symbol, tf) -> dict tham số đã validate.
        add_indicators:   (df, params) -> df với cột chỉ báo thêm vào.
        detect_signals:   (df, symbol, params) -> df với cột signal.
        add_entries:       (df, params, symbol) -> df với cột entry.
        recommended_timeframes: Các TF chạy trên luồng live.
        supported_timeframes:   Các TF được phép; rỗng = không giới hạn cứng.
        trend_tf_by_symbol:     Khung trend riêng cho từng (symbol, khung vào lệnh) từ config
                                (strategies.<tên>.trend_tf_by_symbol, bắt buộc và đủ ô).
                                normalize_params đã tra sẵn.

    Invariant:
        Pipeline phải gọi theo thứ tự:
        normalize_params → add_indicators → detect_signals → add_entries.
        Mỗi bước nhận output của bước trước làm input.

    Thêm chiến lược mới — checklist:
        1. Viết add_<tên>_indicators (chỉ tính chỉ báo, không có default
           ngầm) trong indicator.py.
        2. Viết detect_<tên>_signals (CHỈ quyết định khi nào/hướng nào,
           không entry/level) trong 1 file strategies/<tên>.py mới —
           không giữ tham số, không sửa DataFrame input tại chỗ.
        3. Viết add_<tên>_entries (entry) trong entries.py.
        4. Thêm mục `strategies.<tên>` vào des_config.yaml (giá trị thật) +
           viết default_params/param_fields/normalize_params ở đây, theo
           đúng mẫu Combo/MA Cross bên trên.
        5. Đăng ký một StrategySpec mới vào dict STRATEGIES bên dưới.
        6. Thêm tên chiến lược vào `live.enabled_strategies` trong des_config.yaml.
    """

    key: str
    normalize_params: Callable[..., dict[str, Any]]
    add_indicators: Callable[..., pd.DataFrame]
    detect_signals: Callable[..., pd.DataFrame]
    add_entries: Callable[..., pd.DataFrame]
    recommended_timeframes: tuple[str, ...] = ()
    supported_timeframes: tuple[str, ...] = ()
    trend_tf_by_symbol: dict[str, dict[str, str]] = field(default_factory=dict)


# Danh sách chiến lược được hỗ trợ. Key dùng lowercase.
STRATEGIES: dict[str, StrategySpec] = {
    "combo": StrategySpec(
        key="combo",
        normalize_params=normalize_combo_params,
        add_indicators=add_combo_indicators,
        detect_signals=detect_combo_signals,
        add_entries=add_combo_entries,
        recommended_timeframes=COMBO_RECOMMENDED_TIMEFRAMES,
        trend_tf_by_symbol=COMBO_TREND_TF_BY_SYMBOL,
    ),
    "ma_cross": StrategySpec(
        key="ma_cross",
        normalize_params=normalize_ma_cross_params,
        add_indicators=add_ma_cross_indicators,
        detect_signals=detect_ma_cross_signals,
        add_entries=add_ma_cross_entries,
        recommended_timeframes=MA_CROSS_RECOMMENDED_TIMEFRAMES,
        supported_timeframes=MA_CROSS_SUPPORTED_TIMEFRAMES,
        trend_tf_by_symbol=MA_CROSS_TREND_TF_BY_SYMBOL,
    ),
    "macd_break": StrategySpec(
        key="macd_break",
        normalize_params=normalize_macd_break_params,
        add_indicators=add_macd_break_indicators,
        detect_signals=detect_macd_break_signals,
        add_entries=add_macd_break_entries,
        recommended_timeframes=MACD_BREAK_RECOMMENDED_TIMEFRAMES,
        supported_timeframes=MACD_BREAK_SUPPORTED_TIMEFRAMES,
        trend_tf_by_symbol=MACD_BREAK_TREND_TF_BY_SYMBOL,
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
        key: Mã chiến lược ("combo", "ma_cross", "macd_break").
        symbol: Mã symbol — dùng để resolve symbol-specific params (session hours, khung trend).
        tf: Mã timeframe — bị từ chối nếu ngoài spec.supported_timeframes.
        bars: DataFrame OHLCV [bartime, open, high, low, close, volume],
            đã sort tăng dần (output của candle_reader.read_candles_from_redis).
        overrides: Tham số override.

    Returns:
        DataFrame OHLCV gốc + cột chỉ báo + signal + entry.

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
    enriched = spec.add_entries(enriched, params, symbol)
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
        trend_bars -> các khối xác nhận xu hướng đang bật -> trend_bias
        entry_bars -> entry indicators/raw_signal
        merge trend ĐÃ ĐÓNG -> filter signal -> entries

    Gọi hàm này nghĩa là trend filter BẬT: nó tự đặt TREND_FILTER_ENABLED=True
    trong overrides, nên `signal` chỉ còn lại khi cùng chiều `trend_bias`.
    Khung trend phải >= khung tín hiệu (check_trend_timeframe), nếu không raise ValueError
    trước khi tính bất cứ gì. live_worker gọi run_strategy cho nguồn raw (không lọc trend) và
    hàm này cho từng nguồn lọc trend (knn, breakout, zigzag, tstat; mỗi nguồn một db, redis.outputs.<nguồn>).
    """
    spec = get_strategy(key)
    symbol = str(symbol).strip().upper()
    entry_tf = str(entry_tf).strip().upper()
    trend_tf = str(trend_tf).strip().upper()
    if spec.supported_timeframes and entry_tf not in spec.supported_timeframes:
        allowed = ", ".join(spec.supported_timeframes)
        raise ValueError(f"Strategy '{spec.key}' supports only these timeframes: {allowed}.")
    check_trend_timeframe(entry_tf, trend_tf)

    trend_overrides = {
        **(overrides or {}),
        "TREND_FILTER_ENABLED": True,
        "TREND_TF": trend_tf,
        "ENTRY_TF": entry_tf,
    }
    params = spec.normalize_params(trend_overrides, symbol, entry_tf)

    entry = spec.add_indicators(entry_bars, params)
    trend = build_trend_frame(trend_bars, params)
    merged = merge_trend_reference(entry, trend)
    enriched = spec.detect_signals(merged, symbol=symbol, params=params)
    enriched = spec.add_entries(enriched, params, symbol)
    return enriched


# Tra cứu của từng strategy — live_worker cần biết (a) cờ trend_filter_enabled (ở des phải false), (b) khung trend và
# chính sách dời tín hiệu của từng NGUỒN lọc (knn / breakout / zigzag / tstat) cho mỗi kênh. Đọc từ chính *_DEFAULT_PARAMS đã
# resolve và từ bảng trend_tf_by_symbol, nên des_config.yaml vẫn là nguồn sự thật duy nhất và không có bản sao giá trị
# nào ở live_worker.
_DEFAULT_PARAMS_BY_KEY: dict[str, dict[str, Any]] = {
    "combo": COMBO_DEFAULT_PARAMS,
    "ma_cross": MA_CROSS_DEFAULT_PARAMS,
    "macd_break": MACD_BREAK_DEFAULT_PARAMS,
}


def trend_filter_enabled_for(key: str) -> bool:
    """Cờ ``trend_filter_enabled`` của 1 strategy trong des_config.yaml.

    Nghĩa y như ở we_core: chỉ là mặc định của đường 1 khung (run_strategy), nên phải là ``false`` cho cả combo,
    ma_cross và macd_break (live_worker từ chối khởi động nếu true). Các đầu ra lọc trend (knn, breakout, zigzag, tstat) KHÔNG
    phụ thuộc cờ: chúng luôn chạy run_strategy_with_trend_reference() với khối của nguồn đó.
    """
    return bool(_DEFAULT_PARAMS_BY_KEY[get_strategy(key).key]["TREND_FILTER_ENABLED"])


@dataclass(frozen=True)
class TrendSource:
    """
    Cách chạy MỘT nguồn lọc trend (knn / breakout / zigzag / tstat) cho MỘT kênh (chiến lược, symbol, khung vào lệnh).

    trend_tf:       khung nến trend (knn, zigzag và tstat: ô trend_tf_by_symbol; breakout: chính khung vào lệnh); ``""`` nếu kênh
                    không có ô trong bảng (symbol ngoài vũ trụ vận hành) — live_worker bỏ qua nguồn đó và ghi log lỗi.
    shifts_signals: khối có chính sách first_aligned_per_run (breakout, tstat): tín hiệu đã lọc có thể nằm ở nến không có tín hiệu
                    thô nên live_worker phải tính ở MỌI sự kiện của kênh; khối khác chỉ tính khi nến cuối có tín hiệu thô
                    (tín hiệu đã lọc luôn ⊆ tín hiệu thô).
    warmup_bars:    số nến khung trend tối thiểu khối đòi (POLICY.trend_warmup_bars; zigzag 3000); 0 = không đòi thêm.
    """

    trend_tf: str
    shifts_signals: bool
    warmup_bars: int


def trend_source_for(key: str, symbol: str, timeframe: str, method: str) -> TrendSource:
    """Khung trend + chính sách của khối ``method`` cho kênh (chiến lược, symbol, khung vào lệnh)."""
    spec = get_strategy(key)
    trend_tf = str(spec.normalize_params({"TREND_METHODS": method}, symbol, timeframe)["TREND_TF"])
    return TrendSource(trend_tf, uses_first_aligned_per_run((method,)), max_trend_warmup_bars((method,)))


def alternating_filter_for(key: str) -> bool | None:
    """Trả về cờ ``alternating_filter_enabled`` của 1 strategy, hoặc ``None``
    nếu strategy đó không có cờ này (hiện cả combo, ma_cross và macd_break đều có).

    live_worker ghi giá trị này vào log khởi động: bật/tắt làm đổi số tín hiệu
    nhưng không đổi tên key nào ở db đầu ra, nên phải tra được từ log.
    """
    params = _DEFAULT_PARAMS_BY_KEY[get_strategy(key).key]
    if "ALTERNATING_FILTER_ENABLED" not in params:
        return None
    return bool(params["ALTERNATING_FILTER_ENABLED"])
