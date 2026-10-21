"""
Toàn bộ tham số chiến lược + sổ đăng ký nối pipeline cho wisdom_engine.

Mô tả:
    File này KHÔNG kết nối SQL Server và KHÔNG tính chỉ báo — 2 việc đó
    thuộc wisdom_engine/db_connector.py và wisdom_engine/indicator.py. File
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
       vẫn là code. strategies/<tên>.py và entries.py đều KHÔNG giữ tham số
       nào — chỉ nhận dict params đã hoàn chỉnh từ đây.
    3. Sổ đăng ký chiến lược (StrategySpec/STRATEGIES/run_strategy) — nối
       indicator.py (chỉ báo) + strategies/<tên>.py (tín hiệu BUY/SELL) +
       entries.py (entry) thành 1 pipeline hoàn chỉnh, tra theo key
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from wisdom_engine.we_core.src.indicator import (
    add_combo_indicators,
    add_ma_cross_indicators,
    add_macd_break_indicators,
    merge_trend_reference,
    timeframe_minutes,
)
from wisdom_engine.we_core.src.rules.entries import (
    add_combo_entries,
    add_ma_cross_entries,
    add_macd_break_entries,
)
from wisdom_engine.we_core.src.rules.trend_tf import build_trend_frame, parse_methods
from wisdom_engine.we_core.src.strategies.combo import detect_combo_signals
from wisdom_engine.we_core.src.strategies.ma_cross import detect_ma_cross_signals
from wisdom_engine.we_core.src.strategies.macd_break import detect_macd_break_signals

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "we_config.yaml"


def _load_config() -> dict[str, Any]:
    """Đọc we_config.yaml; trả về dict rỗng nếu file chưa tồn tại."""
    if not _CONFIG_PATH.exists():
        return {}
    with _CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _require(cfg: dict[str, Any], key: str, path: str) -> Any:
    """Đọc bắt buộc 1 key từ config.yaml — không có giá trị mặc định nào
    trong code. Thiếu key sẽ raise ngay, chỉ rõ đường dẫn cần điền.
    """
    if key not in cfg:
        raise KeyError(f"we_config.yaml thiếu '{path}' — xem CLAUDE.md mục 5.")
    return cfg[key]


_CONFIG: dict[str, Any] = _load_config()
_DEFAULTS: dict[str, Any] = _require(_CONFIG, "defaults", "defaults")
_STRATEGIES_CFG: dict[str, Any] = _require(_CONFIG, "strategies", "strategies")
_COMBO_CFG: dict[str, Any] = _require(_STRATEGIES_CFG, "combo", "strategies.combo")
_MA_CROSS_CFG: dict[str, Any] = _require(_STRATEGIES_CFG, "ma_cross", "strategies.ma_cross")
_MACD_BREAK_CFG: dict[str, Any] = _require(_STRATEGIES_CFG, "macd_break", "strategies.macd_break")


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

# Số chữ số thập phân của atr/entry khi xuất CSV/Redis (export_cli.signal_frame). Mặc định 2; symbol giá ~1 (forex)
# cần nhiều hơn, khai báo ở defaults.price_decimals {SYMBOL: số chữ số} (khoá bắt buộc, có thể để {}).
DEFAULT_PRICE_DECIMALS = 2
PRICE_DECIMALS_BY_SYMBOL: dict[str, int] = {}
for _symbol, _digits in _require(_DEFAULTS, "price_decimals", "defaults.price_decimals").items():
    if not isinstance(_digits, int) or isinstance(_digits, bool) or not 0 <= _digits <= 8:
        raise ValueError(f"we_config.yaml: 'defaults.price_decimals.{_symbol}' must be an integer in 0..8, got {_digits!r}.")
    PRICE_DECIMALS_BY_SYMBOL[str(_symbol).strip().upper()] = _digits


def price_decimals(symbol: str | None) -> int:
    """Số chữ số thập phân của atr/entry khi xuất CSV/Redis cho một symbol (mặc định 2)."""
    return PRICE_DECIMALS_BY_SYMBOL.get(str(symbol or "").strip().upper(), DEFAULT_PRICE_DECIMALS)


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


def _to_optional_float(value: object, default: float | None, min_value: float, max_value: float) -> float | None:
    """Như _to_float nhưng cho phép "không đặt": value và default cùng None (hoặc value không đọc được mà default None) -> None."""
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    if parsed is None:
        return None
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
        for tf in _require(strategy_cfg, "trend_timeframes", f"{path}.trend_timeframes")
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
            _require(strategy_cfg, "trend_filter_enabled", f"{path}.trend_filter_enabled"),
            False,
        ),
        "TREND_METHODS": parse_methods(
            _require(strategy_cfg, "trend_methods", f"{path}.trend_methods"),
            label=f"{path}.trend_methods",
        ),
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

    Chi tiết công thức của từng khối xác nhận xu hướng (vd KNN) không đi qua
    đây; chúng nằm trong trend_methods/<tên>.py và chỉ được dùng lúc dựng
    khung trend (rules/trend_tf.build_trend_frame).

    TREND_METHODS: tên các khối được bật; override có thể là list hoặc chuỗi
    cách nhau bằng dấu phẩy ("knn,<khối khác>"), trống/thiếu = mặc định config.

    TREND_TF: đè tường minh hoặc ô của (symbol, khung vào lệnh) trong trend_tf_by_symbol; không có thì để trống ("")
    — nơi nào thật sự cần khung trend (chạy có trend) phải tự báo lỗi, không có khung mặc định chung.
    """
    raw_methods = raw.get("TREND_METHODS")
    trend_methods = (
        defaults["TREND_METHODS"]
        if raw_methods is None or raw_methods == ""
        else parse_methods(raw_methods)
    )

    trend_tf = str(raw.get("TREND_TF") or "").strip()  # đã tra theo ô ở _raw_params_with_cell_trend_tf

    return {
        "TREND_FILTER_ENABLED": _to_bool(
            raw.get("TREND_FILTER_ENABLED"),
            defaults["TREND_FILTER_ENABLED"],
        ),
        "TREND_METHODS": trend_methods,
        "TREND_TF": _to_choice(trend_tf, "", trend_timeframes, "TREND_TF") if trend_tf else "",
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

    Dạng: trend_tf_by_symbol: {BTCUSD: {H1: H4, H2: H2, ...}, ...}. Mỗi symbol vận hành (strategies.combo.symbol_x) phải
    có đủ MỌI khung vào lệnh của chiến lược; không có khung mặc định chung để dự phòng. Kiểm hết lúc nạp config và báo
    MỌI ô sai hoặc thiếu trong một lần: symbol lạ (ngoài strategies.combo.symbol_x), khung vào lệnh ngoài tập chiến lược
    chạy, khung trend ngoài `trend_timeframes`, khung trend nhỏ hơn khung vào lệnh (check_trend_timeframe), hoặc thiếu ô.
    """
    raw = _require(strategy_cfg, "trend_tf_by_symbol", f"{path}.trend_tf_by_symbol")
    if not isinstance(raw, dict):
        raise TypeError(f"we_config.yaml: '{path}.trend_tf_by_symbol' must map symbol -> {{entry timeframe: trend timeframe}}.")
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
        raise ValueError(f"we_config.yaml: invalid '{path}.trend_tf_by_symbol' — " + "; ".join(problems))
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
    """
    given = overrides or {}
    raw = {**defaults, **given}
    if not str(given.get("TREND_TF") or "").strip():
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
            _require(strategy_cfg, "alternating_filter_enabled", f"{path}.alternating_filter_enabled"),
            False,
        ),
    }


def _normalize_alternating_param(raw: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """
    Chuẩn hóa cờ lọc lặp cùng chiều: override (CLI/dashboard) > default từ config.
    """
    return {
        "ALTERNATING_FILTER_ENABLED": _to_bool(
            raw.get("ALTERNATING_FILTER_ENABLED"),
            defaults["ALTERNATING_FILTER_ENABLED"],
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

# Entry theo ATR cho nhóm symbol KHÔNG dùng X cố định, từ config.yaml: strategies.combo.symbol_entry_atr_pct
# (đơn vị %, vd 5.0 = 5% ATR). Với các symbol này entry = high + a% × ATR (BUY) / low − a% × ATR (SELL), ATR chu kỳ
# atr_period tại nến tín hiệu; symbol ở đây không được có trong symbol_x (mỗi symbol đúng MỘT kiểu đệm entry).
COMBO_SYMBOL_ENTRY_ATR_PCT: dict[str, float] = {
    str(symbol).strip().upper(): float(pct)
    for symbol, pct in _require(_COMBO_CFG, "symbol_entry_atr_pct", "strategies.combo.symbol_entry_atr_pct").items()
}
_both_entry_kinds = sorted(set(COMBO_SYMBOL_X) & set(COMBO_SYMBOL_ENTRY_ATR_PCT))
if _both_entry_kinds:
    raise ValueError(
        "we_config.yaml: symbol in both strategies.combo.symbol_x and symbol_entry_atr_pct "
        f"(choose one entry rule per symbol): {', '.join(_both_entry_kinds)}."
    )
_bad_entry_pct = sorted(symbol for symbol, pct in COMBO_SYMBOL_ENTRY_ATR_PCT.items() if not 0 < pct <= 1000)
if _bad_entry_pct:
    raise ValueError(f"we_config.yaml: strategies.combo.symbol_entry_atr_pct must be in (0, 1000]: {', '.join(_bad_entry_pct)}.")

# Vũ trụ vận hành = mọi symbol worker Redis chạy cho CẢ BA chiến lược: nhóm X cố định + nhóm entry theo ATR.
OPERATING_SYMBOLS: tuple[str, ...] = tuple(COMBO_SYMBOL_X) + tuple(COMBO_SYMBOL_ENTRY_ATR_PCT)

# Khung trend riêng cho từng (symbol, khung vào lệnh), từ config.yaml:
# strategies.combo.trend_tf_by_symbol (BẮT BUỘC, đủ mọi symbol × khung vào lệnh).
COMBO_TREND_TF_BY_SYMBOL: dict[str, dict[str, str]] = _strategy_trend_tf_by_symbol(
    _COMBO_CFG,
    "strategies.combo",
    symbols=OPERATING_SYMBOLS,
    entry_timeframes=COMBO_RECOMMENDED_TIMEFRAMES,
    trend_timeframes=COMBO_TREND_TIMEFRAMES,
)

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
    "ENTRY_ATR_PCT": None,
    "SESSION_HOURS_UTC": [],
    **_strategy_trend_defaults(_COMBO_CFG, "strategies.combo", entry_tf=COMBO_DEFAULT_TIMEFRAME),
    **_strategy_alternating_default(_COMBO_CFG, "strategies.combo"),
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
    {"key": "ENTRY_ATR_PCT", "label": "Entry % ATR", "type": "number", "min": 0, "max": 1000, "step": 0.1},
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

    Symbol không có trong COMBO_SYMBOL_X sẽ dùng X = 0.0. Symbol thuộc COMBO_SYMBOL_ENTRY_ATR_PCT có thêm
    entry_atr_pct (% ATR dùng thay X); symbol khác thì entry_atr_pct = None (dùng X).
    """
    key = str(symbol or "").strip().upper()
    return {
        "x": float(COMBO_SYMBOL_X.get(key, 0.0)),
        "entry_atr_pct": COMBO_SYMBOL_ENTRY_ATR_PCT.get(key),
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
        symbol: Mã symbol để lấy X, session_hours mặc định và khung trend riêng.
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
        "MACD_SIGNAL": _to_int(raw.get("MACD_SIGNAL"), d["MACD_SIGNAL"], *_field_range(f, "MACD_SIGNAL")),
        "ATR_PERIOD": _to_int(raw.get("ATR_PERIOD"), d["ATR_PERIOD"], *_field_range(f, "ATR_PERIOD")),
        # X: override > symbol default (từ COMBO_SYMBOL_X) > 0.0
        "X": _to_float(raw.get("X"), symbol_params["x"], *_field_range(f, "X")),
        # ENTRY_ATR_PCT: override > symbol default (COMBO_SYMBOL_ENTRY_ATR_PCT) > None. Khác None thì entry dùng
        # % × ATR và BỎ QUA X (rules/entries.add_combo_entries).
        "ENTRY_ATR_PCT": _to_optional_float(
            raw.get("ENTRY_ATR_PCT"), symbol_params["entry_atr_pct"], *_field_range(f, "ENTRY_ATR_PCT")
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
# Khung trend riêng cho từng (symbol, khung vào lệnh): strategies.ma_cross.trend_tf_by_symbol (bắt buộc, đủ ô).
MA_CROSS_TREND_TF_BY_SYMBOL: dict[str, dict[str, str]] = _strategy_trend_tf_by_symbol(
    _MA_CROSS_CFG,
    "strategies.ma_cross",
    symbols=OPERATING_SYMBOLS,
    entry_timeframes=MA_CROSS_SUPPORTED_TIMEFRAMES,
    trend_timeframes=MA_CROSS_TREND_TIMEFRAMES,
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
    **_strategy_alternating_default(_MA_CROSS_CFG, "strategies.ma_cross"),
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
        1. Default trong config.yaml.
        2. Override từ CLI/dashboard.

    Hàm này cũng kiểm tra các quan hệ bắt buộc:
        - FAST_MA phải nhỏ hơn SLOW_MA.
        - MACD_FAST phải nhỏ hơn MACD_SLOW.

    symbol/tf (khung vào lệnh) dùng để tra khung trend mặc định trong trend_tf_by_symbol;
    TREND_TF truyền trong overrides vẫn thắng.
    """
    raw = _raw_params_with_cell_trend_tf(MA_CROSS_DEFAULT_PARAMS, overrides, MA_CROSS_TREND_TF_BY_SYMBOL, symbol, tf)
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
        **_normalize_alternating_param(raw, d),
    }


# --- MACD Break ---

# MACD Break chỉ chạy trên các timeframe đã chốt trong config:
# strategies.macd_break.supported_timeframes.
MACD_BREAK_SUPPORTED_TIMEFRAMES: tuple[str, ...] = tuple(
    str(tf).upper()
    for tf in _require(_MACD_BREAK_CFG, "supported_timeframes", "strategies.macd_break.supported_timeframes")
)
MACD_BREAK_RECOMMENDED_TIMEFRAMES: tuple[str, ...] = MACD_BREAK_SUPPORTED_TIMEFRAMES
MACD_BREAK_DEFAULT_TIMEFRAME = str(
    _require(_MACD_BREAK_CFG, "default_timeframe", "strategies.macd_break.default_timeframe")
).upper()
MACD_BREAK_TREND_TIMEFRAMES: tuple[str, ...] = _trend_timeframes(_MACD_BREAK_CFG, "strategies.macd_break")
# Khung trend riêng cho từng (symbol, khung vào lệnh): strategies.macd_break.trend_tf_by_symbol (bắt buộc, đủ ô).
MACD_BREAK_TREND_TF_BY_SYMBOL: dict[str, dict[str, str]] = _strategy_trend_tf_by_symbol(
    _MACD_BREAK_CFG,
    "strategies.macd_break",
    symbols=OPERATING_SYMBOLS,
    entry_timeframes=MACD_BREAK_SUPPORTED_TIMEFRAMES,
    trend_timeframes=MACD_BREAK_TREND_TIMEFRAMES,
)

MACD_BREAK_DEFAULT_PARAMS: dict[str, Any] = {
    "LOOKBACK_BARS": int(_require(_MACD_BREAK_CFG, "lookback_bars", "strategies.macd_break.lookback_bars")),
    "MACD_FAST": int(_require(_MACD_BREAK_CFG, "macd_fast", "strategies.macd_break.macd_fast")),
    "MACD_SLOW": int(_require(_MACD_BREAK_CFG, "macd_slow", "strategies.macd_break.macd_slow")),
    "MACD_SIGNAL": int(_require(_MACD_BREAK_CFG, "macd_signal", "strategies.macd_break.macd_signal")),
    "ATR_PERIOD": int(_require(_MACD_BREAK_CFG, "atr_period", "strategies.macd_break.atr_period")),
    **_strategy_trend_defaults(
        _MACD_BREAK_CFG,
        "strategies.macd_break",
        entry_tf=MACD_BREAK_DEFAULT_TIMEFRAME,
    ),
    **_strategy_alternating_default(_MACD_BREAK_CFG, "strategies.macd_break"),
}

MACD_BREAK_PARAM_FIELDS: list[dict[str, Any]] = [
    # max = WARMUP_BARS: lookback dài hơn số nến warmup thì vài tín hiệu đầu
    # khoảng yêu cầu sẽ mất im lặng (prior_high_close vẫn NaN sau khi cắt warmup).
    {"key": "LOOKBACK_BARS", "label": "Lookback (bars)", "type": "number", "min": 2, "max": WARMUP_BARS, "step": 1},
    {"key": "MACD_FAST", "label": "MACD Fast", "type": "number", "min": 1, "max": 200, "step": 1},
    {"key": "MACD_SLOW", "label": "MACD Slow", "type": "number", "min": 2, "max": 300, "step": 1},
    {"key": "MACD_SIGNAL", "label": "MACD Signal", "type": "number", "min": 1, "max": 200, "step": 1},
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
    Gộp tham số MACD Break (default từ config.yaml + override CLI/dashboard),
    validate và kẹp theo PARAM_FIELDS. MACD_FAST phải nhỏ hơn MACD_SLOW.

    symbol/tf (khung vào lệnh) dùng để tra khung trend mặc định trong trend_tf_by_symbol;
    TREND_TF truyền trong overrides vẫn thắng.
    """
    raw = _raw_params_with_cell_trend_tf(MACD_BREAK_DEFAULT_PARAMS, overrides, MACD_BREAK_TREND_TF_BY_SYMBOL, symbol, tf)
    d = MACD_BREAK_DEFAULT_PARAMS
    f = MACD_BREAK_PARAM_FIELDS

    macd_fast = _to_int(raw.get("MACD_FAST"), d["MACD_FAST"], *_field_range(f, "MACD_FAST"))
    macd_slow = _to_int(raw.get("MACD_SLOW"), d["MACD_SLOW"], *_field_range(f, "MACD_SLOW"))
    if macd_fast >= macd_slow:
        raise ValueError("MACD_FAST must be smaller than MACD_SLOW")

    return {
        "LOOKBACK_BARS": _to_int(raw.get("LOOKBACK_BARS"), d["LOOKBACK_BARS"], *_field_range(f, "LOOKBACK_BARS")),
        "MACD_FAST": macd_fast,
        "MACD_SLOW": macd_slow,
        "MACD_SIGNAL": _to_int(raw.get("MACD_SIGNAL"), d["MACD_SIGNAL"], *_field_range(f, "MACD_SIGNAL")),
        "ATR_PERIOD": _to_int(raw.get("ATR_PERIOD"), d["ATR_PERIOD"], *_field_range(f, "ATR_PERIOD")),
        **_normalize_trend_reference_params(
            raw,
            d,
            trend_timeframes=MACD_BREAK_TREND_TIMEFRAMES,
            entry_timeframes=MACD_BREAK_SUPPORTED_TIMEFRAMES,
        ),
        **_normalize_alternating_param(raw, d),
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
        add_entries:       (df, params, symbol) -> df với cột entry và tham chiếu level.
        supported_timeframes: Các TF được phép; rỗng nghĩa là dùng mọi TF hệ thống.
        default_timeframe: TF mặc định của strategy trên CLI export.
        tuned_symbols: Symbol có tham số riêng theo strategy — dùng để
            dashboard ưu tiên nhóm symbol có cấu hình vận hành rõ ràng.
        trend_tf_by_symbol: Khung trend riêng cho từng (symbol, khung vào lệnh) từ config
            (strategies.<tên>.trend_tf_by_symbol, bắt buộc và đủ ô). normalize_params đã tra sẵn;
            dashboard dùng để hiện đúng khung trend mặc định của từng ô.

    Invariant:
        Pipeline phải gọi theo thứ tự:
        normalize_params → add_indicators → detect_signals → add_entries.
        Mỗi bước nhận output của bước trước làm input.

    Thêm chiến lược mới — checklist:
        1. Viết add_<tên>_indicators (chỉ tính chỉ báo, không có default
           ngầm) trong wisdom_engine/indicator.py.
        2. Viết detect_<tên>_signals (CHỈ quyết định khi nào/hướng nào,
           không entry/level) trong 1 file strategies/<tên>.py mới —
           không giữ tham số, không sửa DataFrame input tại chỗ.
        3. Viết add_<tên>_entries (entry/tham chiếu level) trong
           entries.py — tách riêng khỏi strategies/<tên>.py.
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
    add_entries: Callable[..., pd.DataFrame]
    recommended_timeframes: tuple[str, ...] = ()
    supported_timeframes: tuple[str, ...] = ()
    default_timeframe: str | None = None
    tuned_symbols: tuple[str, ...] = ()
    trend_tf_by_symbol: dict[str, dict[str, str]] = field(default_factory=dict)


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
        add_entries=add_combo_entries,
        recommended_timeframes=COMBO_RECOMMENDED_TIMEFRAMES,
        default_timeframe=COMBO_DEFAULT_TIMEFRAME,
        tuned_symbols=OPERATING_SYMBOLS,
        trend_tf_by_symbol=COMBO_TREND_TF_BY_SYMBOL,
    ),
    "ma_cross": StrategySpec(
        key="ma_cross",
        label="MA Cross",
        default_params=MA_CROSS_DEFAULT_PARAMS,
        param_fields=MA_CROSS_PARAM_FIELDS,
        normalize_params=normalize_ma_cross_params,
        add_indicators=add_ma_cross_indicators,
        detect_signals=detect_ma_cross_signals,
        add_entries=add_ma_cross_entries,
        recommended_timeframes=MA_CROSS_RECOMMENDED_TIMEFRAMES,
        supported_timeframes=MA_CROSS_SUPPORTED_TIMEFRAMES,
        default_timeframe=MA_CROSS_DEFAULT_TIMEFRAME,
        trend_tf_by_symbol=MA_CROSS_TREND_TF_BY_SYMBOL,
    ),
    "macd_break": StrategySpec(
        key="macd_break",
        label="MACD Break",
        default_params=MACD_BREAK_DEFAULT_PARAMS,
        param_fields=MACD_BREAK_PARAM_FIELDS,
        normalize_params=normalize_macd_break_params,
        add_indicators=add_macd_break_indicators,
        detect_signals=detect_macd_break_signals,
        add_entries=add_macd_break_entries,
        recommended_timeframes=MACD_BREAK_RECOMMENDED_TIMEFRAMES,
        supported_timeframes=MACD_BREAK_SUPPORTED_TIMEFRAMES,
        default_timeframe=MACD_BREAK_DEFAULT_TIMEFRAME,
        trend_tf_by_symbol=MACD_BREAK_TREND_TF_BY_SYMBOL,
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

    Hàm này giữ pipeline single-timeframe hiện tại không bị chồng chéo, đồng
    thời cung cấp đường chạy đúng bản chất cho các strategy cần trend filter:
        trend_bars -> các khối xác nhận xu hướng đang bật -> trend_bias
        entry_bars -> entry indicators/raw_signal
        merge trend đã đóng -> filter signal -> entries

    Khung trend phải >= khung tín hiệu (check_trend_timeframe), nếu không raise ValueError
    trước khi tính bất cứ gì.
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
    # entry_tf (khung thực sự vào lệnh), không phải trend_tf (chỉ là tham
    # chiếu lọc hướng, không phải khung đặt lệnh).
    params = spec.normalize_params(trend_overrides, symbol, entry_tf)
    params = {**params, "_RUN_TF": entry_tf}

    entry = spec.add_indicators(entry_bars, params)
    trend = build_trend_frame(trend_bars, params)
    merged = merge_trend_reference(entry, trend)
    enriched = spec.detect_signals(merged, symbol=symbol, params=params)
    enriched = spec.add_entries(enriched, params, symbol)
    return enriched
