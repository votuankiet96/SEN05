"""
Tham số chiến lược + sổ đăng ký nối pipeline cho DE, và điều phối tiến trình.

Mô tả:
    1. Tham số của từng chiến lược (Combo, MA Cross, EMA Reach, MACD Break, Ichimoku) — bắt buộc có trong de_config.yaml (mục `strategies:`), là khu vực operator
       chỉnh trực tiếp. Thiếu bất kỳ khoá nào sẽ raise lỗi rõ ràng ngay khi import — không có fallback âm thầm nào trong code. Giới hạn min/max (``bounds``) và các
       luật "nhỏ < lớn" (``ordered_pairs``) là code vì đó là validate đầu vào, không phải giá trị vận hành. strategies/<tên>.py và rules/entries.py KHÔNG giữ
       tham số nào — chỉ nhận dict params đã hoàn chỉnh từ ``StrategySpec.normalize_params``.
    2. Sổ đăng ký chiến lược (StrategySpec/STRATEGIES) — nối indicator.py (chỉ báo) + strategies/<tên>.py (tín hiệu BUY/SELL) + rules/entries.py (entry) thành một
       pipeline, tra theo key ("combo"/"ma_cross"/"ema_reach"/"macd_break"/"ichimoku"). Mỗi chiến lược là MỘT lời gọi ``_build_spec``.
    3. Điều phối một tiến trình: dựng việc từ config (build_lane_job / build_trend_job), vòng lặp sự kiện, run / run_trend.

    Nguồn raw luôn chạy KHÔNG lọc trend (TREND_FILTER_ENABLED mặc định False); nhánh lọc chạy riêng theo từng khối trend.
    Ichimoku không có trend (trend_off_timeframes của nó = toàn bộ khung hỗ trợ): chỉ nguồn raw.

Đầu ra:
    STRATEGIES: dict[str, StrategySpec] — tra cứu theo key; spec.normalize_params(overrides, symbol, tf) trả tham số đã validate.
    get_strategy(key): StrategySpec hoặc raise KeyError.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import redis
import yaml
from core_engine.src.indicator import (
    add_combo_indicators,
    add_ema_reach_indicators,
    add_ichimoku_indicators,
    add_ma_cross_indicators,
    add_macd_break_indicators,
    timeframe_minutes,
)
from core_engine.src.rules.entries import (
    add_combo_entries,
    add_ema_reach_entries,
    add_ichimoku_entries,
    add_ma_cross_entries,
    add_macd_break_entries,
)
from core_engine.src.rules.trend_tf import (
    entry_based_trend_tf,
    get_method,
    max_trend_warmup_bars,
    method_names,
    parse_methods,
    uses_entry_timeframe,
    uses_first_aligned_per_run,
)
from core_engine.src.strategies.combo import detect_combo_signals
from core_engine.src.strategies.ema_reach import detect_ema_reach_signals
from core_engine.src.strategies.ichimoku import detect_ichimoku_signals
from core_engine.src.strategies.ma_cross import detect_ma_cross_signals
from core_engine.src.strategies.macd_break import detect_macd_break_signals

CONFIG_PATH = Path(__file__).resolve().parents[1] / "de_config.yaml"
with CONFIG_PATH.open("r", encoding="utf-8") as _config_file:
    CONFIG: dict[str, Any] = yaml.safe_load(_config_file) or {}


def require(cfg: dict[str, Any], key: str, path: str) -> Any:
    """Đọc bắt buộc 1 key — không có giá trị mặc định nào trong code.
    Thiếu key sẽ raise ngay, chỉ rõ đúng đường dẫn cần điền.
    """
    if key not in cfg:
        raise KeyError(f"de_config.yaml thiếu '{path}'")
    return cfg[key]


_STRATEGIES_CFG: dict[str, Any] = require(CONFIG, "strategies", "strategies")


def _to_int(value: object, default: int, min_value: int, max_value: int) -> int:
    """
    Chuyển input sang số nguyên và kẹp trong khoảng an toàn.

    Nếu input sai kiểu hoặc bỏ trống, dùng default đã lấy từ de_config.yaml.
    """
    try:
        parsed = int(float(value)) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(parsed, max_value))




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


def _strategy_trend_defaults(strategy_cfg: dict[str, Any], path: str) -> dict[str, Any]:
    """Mặc định điều khiển trend của một chiến lược: cờ lọc luôn tắt (nguồn raw không lọc trend) và các khối trend mặc định."""
    return {
        "TREND_FILTER_ENABLED": False,
        "TREND_METHODS": parse_methods(
            require(strategy_cfg, "trend_methods", f"{path}.trend_methods"),
            label=f"{path}.trend_methods",
        ),
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
    excluded_methods: tuple[str, ...] = (),
    path: str = "",
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
    khối đang bật tính trên CHÍNH khung của tín hiệu (chính sách same_timeframe ở rules/trend_tf.POLICY, vd breakout) thì TREND_TF = khung vào lệnh
    (hoặc mức tối thiểu min_trend_tf nếu khung vào lệnh nhỏ hơn, vd cloud: M10 -> H1, xem entry_based_trend_tf) và chỉ phải thuộc khung của
    chiến lược hoặc trend_timeframes, không bị giới hạn trong trend_timeframes (H1–H4 chỉ áp cho khối cần khung lớn hơn).

    excluded_methods (strategies.<tên>.excluded_trend_methods): khối KHÔNG được áp cho chiến lược này; TREND_METHODS (override) có
    khối đó là ValueError, báo trước mọi việc khác.
    """
    trend_methods = _resolve_trend_methods(raw, defaults)
    banned = [name for name in trend_methods if name in excluded_methods]
    if banned:
        raise ValueError(
            f"Trend method(s) {', '.join(banned)} cannot be used with this strategy ({path}.excluded_trend_methods: {', '.join(excluded_methods)})."
        )
    if uses_entry_timeframe(trend_methods):
        allowed_trend_tfs = tuple(dict.fromkeys((*entry_timeframes, *trend_timeframes)))
    else:
        allowed_trend_tfs = trend_timeframes

    trend_tf = str(raw.get("TREND_TF") or "").strip()

    return {
        "TREND_FILTER_ENABLED": _to_bool(
            raw.get("TREND_FILTER_ENABLED"),
            defaults["TREND_FILTER_ENABLED"],
        ),
        "TREND_METHODS": trend_methods,
        "TREND_TF": _to_choice(trend_tf, "", allowed_trend_tfs, "TREND_TF") if trend_tf else "",
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


def _trend_off_timeframes(
    strategy_cfg: dict[str, Any],
    path: str,
    *,
    supported: tuple[str, ...],
) -> tuple[str, ...]:
    """
    Đọc strategies.<tên>.trend_off_timeframes (BẮT BUỘC; [] = lọc trend ở mọi khung): các khung vào lệnh mà chiến lược
    KHÔNG dùng bộ lọc trend nào (chốt 2026-10-28, đồng bộ we_core: macd_break khung lớn tự nó đã xác nhận xu hướng). Ở các khung này
    chỉ nguồn raw phát tín hiệu; MỌI nguồn lọc trend (knn, breakout, zigzag, tstat, cloud) bỏ qua, nên không có LIST/khoá nào của
    chiến lược này ở khung đó trong các nguồn trend. Mỗi khung phải thuộc `supported_timeframes` của chiến lược và không lặp.
    """
    raw = require(strategy_cfg, "trend_off_timeframes", f"{path}.trend_off_timeframes")
    if not isinstance(raw, (list, tuple)):
        raise TypeError(f"de_config.yaml: '{path}.trend_off_timeframes' must be a list of timeframes ([] = trend filter on every timeframe).")
    off = tuple(str(tf).strip().upper() for tf in raw)
    unknown = [tf for tf in off if tf not in supported]
    duplicated = sorted({tf for tf in off if off.count(tf) > 1})
    if unknown or duplicated:
        details = []
        if unknown:
            details.append(f"not in supported_timeframes ({', '.join(supported)}): {', '.join(unknown)}")
        if duplicated:
            details.append(f"listed twice: {', '.join(duplicated)}")
        raise ValueError(f"de_config.yaml: invalid '{path}.trend_off_timeframes' — " + "; ".join(details))
    return off


def _excluded_trend_methods(strategy_cfg: dict[str, Any], path: str) -> tuple[str, ...]:
    """
    Đọc strategies.<tên>.excluded_trend_methods (TUỲ CHỌN, mặc định rỗng): các khối trend KHÔNG được áp cho chiến lược này ở BẤT KỲ khung nào
    (chốt 2026-10-28, đồng bộ we_core/des cho macd_break + breakout: breakout tính hướng bằng chính điều kiện phá đỉnh/đáy close 100 nến + MACD
    mà macd_break dùng làm setup, nên lọc macd_break bằng breakout là lọc vòng tròn). Mỗi tên phải là khối đã đăng ký (rules/trend_tf.METHODS),
    không trùng và KHÔNG nằm trong `trend_methods` mặc định của chính chiến lược. Khối bị loại thì DE không có nguồn đó cho chiến lược
    (không tính, không ghi khoá/kênh), đè TREND_METHODS bằng khối đó là lỗi. Sai thì raise ngay lúc nạp config.
    """
    raw = strategy_cfg.get("excluded_trend_methods")
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise TypeError(f"de_config.yaml: '{path}.excluded_trend_methods' must be a list of trend method names.")
    if not raw:
        return ()
    excluded = parse_methods(raw, label=f"{path}.excluded_trend_methods")
    default_methods = parse_methods(require(strategy_cfg, "trend_methods", f"{path}.trend_methods"), label=f"{path}.trend_methods")
    overlap = [name for name in excluded if name in default_methods]
    if overlap:
        raise ValueError(f"de_config.yaml: '{path}.excluded_trend_methods' lists {', '.join(overlap)}, which '{path}.trend_methods' uses by default.")
    return excluded


def _strategy_trend_tf_by_symbol(
    strategy_cfg: dict[str, Any],
    path: str,
    *,
    symbols: tuple[str, ...],
    entry_timeframes: tuple[str, ...],
    trend_timeframes: tuple[str, ...],
    trend_off_timeframes: tuple[str, ...] = (),
) -> dict[str, dict[str, str]]:
    """
    Đọc strategies.<tên>.trend_tf_by_symbol (BẮT BUỘC): khung trend riêng cho từng (symbol, khung vào lệnh).

    Dạng: trend_tf_by_symbol: {BTCUSD: {H1: H4, H2: H2, ...}, ...}. Mỗi symbol vận hành (redis.input.symbols) phải
    có đủ MỌI khung vào lệnh của chiến lược CÒN lọc trend (trừ `trend_off_timeframes`); không có khung mặc định chung để
    dự phòng. Kiểm hết lúc nạp config và báo MỌI ô sai hoặc thiếu trong một lần: symbol lạ (ngoài redis.input.symbols),
    khung vào lệnh ngoài tập chiến lược chạy, ô của khung đã tắt trend (không dùng tới nên không được khai, tránh giá trị
    chết trong config), khung trend ngoài `trend_timeframes`, khung trend nhỏ hơn khung vào lệnh (check_trend_timeframe),
    hoặc thiếu ô.
    """
    raw = require(strategy_cfg, "trend_tf_by_symbol", f"{path}.trend_tf_by_symbol")
    if not isinstance(raw, dict):
        raise TypeError(f"de_config.yaml: '{path}.trend_tf_by_symbol' must map symbol -> {{entry timeframe: trend timeframe}}.")
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
            if entry_tf in trend_off_timeframes:
                problems.append(f"{symbol} {entry_tf}: trend filter is off for this entry timeframe (trend_off_timeframes) — remove the cell")
            elif entry_tf not in entry_timeframes:
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
        missing = [
            entry_tf
            for entry_tf in entry_timeframes
            if entry_tf not in trend_off_timeframes and entry_tf not in declared.get(symbol, set())
        ]
        if missing:
            problems.append(f"{symbol}: no trend timeframe for entry timeframe(s) {', '.join(missing)}")
    if problems:
        raise ValueError(f"de_config.yaml: invalid '{path}.trend_tf_by_symbol' — " + "; ".join(problems))
    return result


def _raw_params_with_cell_trend_tf(
    defaults: dict[str, Any],
    overrides: dict[str, Any] | None,
    by_symbol: dict[str, dict[str, str]],
    symbol: object,
    entry_tf: object,
) -> dict[str, Any]:
    """
    defaults < overrides như cũ, nhưng khung trend tra theo (symbol, khung vào lệnh) trong trend_tf_by_symbol.
    TREND_TF người dùng truyền (CLI --trend-tf, dashboard) vẫn thắng.

    Ngoại lệ: khi mọi khối đang bật tính trên CHÍNH khung của tín hiệu (chính sách same_timeframe ở rules/trend_tf.POLICY, vd breakout) thì khung trend LÀ
    khung vào lệnh (không tra bảng), hoặc mức tối thiểu min_trend_tf nếu khung vào lệnh nhỏ hơn (cloud: M10 -> H1; entry_based_trend_tf);
    TREND_TF truyền vào mà khác khung suy ra đó là lỗi.
    """
    given = overrides or {}
    raw = {**defaults, **given}
    methods = _resolve_trend_methods(raw, defaults)
    if uses_entry_timeframe(methods):
        entry = str(entry_tf or "").strip().upper()
        derived = entry_based_trend_tf(methods, entry)
        explicit = str(given.get("TREND_TF") or "").strip().upper()
        if explicit and explicit != derived:
            basis = (
                f"the entry timeframe {entry}"
                if derived == entry
                else f"the timeframe {derived} for entry timeframe {entry} (never smaller than {derived})"
            )
            raise ValueError(f"Trend method(s) {', '.join(methods)} always use {basis}; TREND_TF {explicit} is not allowed.")
        raw["TREND_TF"] = derived
    elif not str(given.get("TREND_TF") or "").strip():
        raw["TREND_TF"] = by_symbol.get(str(symbol or "").strip().upper(), {}).get(str(entry_tf or "").strip().upper(), "")
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




def _lane_names(raw: Any, path: str) -> tuple[str, ...]:
    """Đọc một danh sách tên (symbol hoặc mã khung): không rỗng, không có tên rỗng, không trùng; chuẩn hoá chữ hoa."""
    if not isinstance(raw, (list, tuple)) or not raw:
        raise TypeError(f"de_config.yaml: '{path}' must be a non-empty list.")
    names = tuple(str(item).strip().upper() for item in raw)
    if any(not name for name in names):
        raise ValueError(f"de_config.yaml: '{path}' contains an empty name.")
    duplicated = sorted({name for name in names if names.count(name) > 1})
    if duplicated:
        raise ValueError(f"de_config.yaml: '{path}' lists a name twice: {', '.join(duplicated)}.")
    return names


_INPUT_SOURCE_KEYS = ("host", "port", "password", "db", "key_prefix", "symbols")


def _input_source_blocks(redis_cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Đọc ``redis.input_sources`` (tuỳ chọn): các nguồn nến THÊM ngoài ``redis.input`` (nguồn mặc định, Core AEN). Trả về tên (chữ thường + số, vì tên là hậu tố của tên tiến trình,
    vd ``de-signal@combo.og8``) -> khối cấu hình."""
    raw = redis_cfg.get("input_sources") or {}
    if not isinstance(raw, dict):
        raise TypeError("de_config.yaml: 'redis.input_sources' must map source name -> {host, password, db, key_prefix, symbols}")
    blocks: dict[str, dict[str, Any]] = {}
    for raw_name, block in raw.items():
        name = str(raw_name).strip().lower()
        if not re.fullmatch(r"[a-z0-9]+", name):
            raise ValueError(f"de_config.yaml: 'redis.input_sources.{raw_name}': a source name uses lowercase letters and digits only (it ends the process name, e.g. de-signal@combo.og8)")
        if name in blocks:
            raise ValueError(f"de_config.yaml: 'redis.input_sources' lists source '{name}' twice")
        if not isinstance(block, dict):
            raise TypeError(f"de_config.yaml: 'redis.input_sources.{name}' must be a mapping of {', '.join(_INPUT_SOURCE_KEYS)}")
        blocks[name] = block
    return blocks


def _all_symbols(redis_cfg: dict[str, Any]) -> tuple[str, ...]:
    """Mọi symbol DE vận hành: ``redis.input.symbols`` rồi ``redis.input_sources.<tên>.symbols`` của từng nguồn nến thêm. Mỗi symbol chỉ có MỘT nguồn nến: hai nguồn cùng nuôi một symbol
    là cấu hình sai (cùng khoá đầu ra, hai nhóm tiến trình tranh nhau ghi), nên ValueError ngay lúc nạp."""
    groups = [("redis.input", require(redis_cfg, "input", "redis.input"))]
    groups += [(f"redis.input_sources.{name}", block) for name, block in _input_source_blocks(redis_cfg).items()]
    owner: dict[str, str] = {}
    for path, block in groups:
        for symbol in _lane_names(require(block, "symbols", f"{path}.symbols"), f"{path}.symbols"):
            if symbol in owner:
                raise ValueError(f"de_config.yaml: symbol {symbol} is listed by both '{owner[symbol]}' and '{path}': each symbol needs exactly one candle source")
            owner[symbol] = path
    return tuple(owner)


OPERATING_SYMBOLS: tuple[str, ...] = _all_symbols(require(CONFIG, "redis", "redis"))







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































@dataclass(frozen=True)
class StrategySpec:
    """
    Mô tả đầy đủ một chiến lược giao dịch — bất biến sau khi tạo.

    Thuộc tính:
        key:                    Mã định danh duy nhất ("combo", "ma_cross", "macd_break", "ichimoku", "ema_reach").
        add_indicators:         (df, params) -> df với cột chỉ báo thêm vào.
        detect_signals:         (df, symbol, params) -> df với cột signal.
        add_entries:            (df, params, symbol) -> df với cột entry.
        order_type:             Loại lệnh OE đặt cho tín hiệu: "STOP" (lệnh chờ tại mức entry, combo) hoặc "MARKET" (khớp ngay, entry là close). CỐ ĐỊNH theo
                                chiến lược, không phải núm vặn của operator; bắt buộc khai, chiến lược mới quên khai thì lỗi ngay lúc dựng.
        bounds:                 Tham số số nguyên -> (min, max): giới hạn an toàn khi chuẩn hoá (thứ tự khai là thứ tự trong kết quả).
        default_params:         Tham số mặc định đọc từ de_config.yaml (strategies.<key>); cũng là tập khoá mà normalize_params nhận từ overrides.
        ordered_pairs:          Các cặp (nhỏ, lớn) phải thoả nhỏ < lớn sau khi kẹp, ví dụ ("FAST_MA", "SLOW_MA").
        recommended_timeframes: Các khung chạy trên luồng live.
        supported_timeframes:   Các khung được phép chạy; rỗng = không giới hạn cứng.
        trend_timeframes:       Khung lớn được chọn làm khung trend (rỗng nếu chiến lược không có trend).
        trend_off_timeframes:   Khung vào lệnh KHÔNG dùng bộ lọc trend nào: chỉ nguồn raw phát ở các khung này (``trend_enabled_for`` trả False).
        trend_tf_by_symbol:     Khung trend riêng cho từng (symbol, khung vào lệnh) từ strategies.<tên>.trend_tf_by_symbol; normalize_params đã tra sẵn.
        excluded_trend_methods: Khối trend KHÔNG được áp cho chiến lược này ở bất kỳ khung nào (xem trend_method_applies).
        session_hours_utc:      Chỉ combo: symbol -> giờ UTC được giao dịch (rỗng/không khai = mọi giờ); None nếu chiến lược không có lọc session.

    Invariant:
        Pipeline phải gọi theo thứ tự:
        normalize_params → add_indicators → detect_signals → add_entries.
        Mỗi bước nhận output của bước trước làm input.

    Thêm chiến lược mới — checklist:
        1. Viết add_<tên>_indicators trong indicator.py (chỉ tính chỉ báo, không có default ngầm).
        2. Viết detect_<tên>_signals trong strategies/<tên>.py (CHỈ quyết định khi nào/hướng nào) và add_<tên>_entries trong rules/entries.py.
        3. Thêm mục `strategies.<tên>` vào de_config.yaml (giá trị thật; tên khoá = tên tham số viết thường).
        4. Thêm MỘT lời gọi _build_spec vào STRATEGIES bên dưới: khai bounds, ordered_pairs, order_type "STOP" hoặc "MARKET".
        5. Thêm tên chiến lược vào `live.enabled_strategies` trong de_config.yaml.
    """

    key: str
    add_indicators: Callable[..., pd.DataFrame]
    detect_signals: Callable[..., pd.DataFrame]
    add_entries: Callable[..., pd.DataFrame]
    order_type: str
    bounds: dict[str, tuple[int, int]]
    default_params: dict[str, Any]
    ordered_pairs: tuple[tuple[str, str], ...] = ()
    recommended_timeframes: tuple[str, ...] = ()
    supported_timeframes: tuple[str, ...] = ()
    trend_timeframes: tuple[str, ...] = ()
    trend_off_timeframes: tuple[str, ...] = ()
    trend_tf_by_symbol: dict[str, dict[str, str]] = field(default_factory=dict)
    excluded_trend_methods: tuple[str, ...] = ()
    session_hours_utc: dict[str, list[int]] | None = None

    @property
    def live_timeframes(self) -> tuple[str, ...]:
        """Các khung mà dịch vụ live xử lý cho chiến lược này: khung khuyến nghị nếu có, không thì mọi khung được hỗ trợ."""
        return self.recommended_timeframes or self.supported_timeframes

    @property
    def uses_trend(self) -> bool:
        """True nếu chiến lược có phần cấu hình trend (ichimoku thì không)."""
        return "TREND_METHODS" in self.default_params

    def normalize_params(self, overrides: dict[str, Any] | None = None, symbol: str | None = None, tf: str | None = None) -> dict[str, Any]:
        """
        Gộp tham số mặc định với overrides thành bộ tham số hợp lệ: số nguyên bị kẹp vào ``bounds``, các cặp ``ordered_pairs`` phải thoả nhỏ < lớn.

        Thứ tự ưu tiên: default_params < overrides. ``symbol``/``tf`` (khung vào lệnh) dùng để tra khung trend và giờ session; TREND_TF truyền trong overrides vẫn thắng.
        """
        defaults = self.default_params
        if self.uses_trend:
            raw = _raw_params_with_cell_trend_tf(defaults, overrides, self.trend_tf_by_symbol, symbol, tf)
        else:
            raw = {**defaults, **(overrides or {})}
        params = {name: _to_int(raw.get(name), defaults[name], low, high) for name, (low, high) in self.bounds.items()}
        for smaller, larger in self.ordered_pairs:
            if params[smaller] >= params[larger]:
                raise ValueError(f"{smaller} must be smaller than {larger}")
        if self.session_hours_utc is not None:
            params["SESSION_HOURS_UTC"] = _parse_session_hours(raw.get("SESSION_HOURS_UTC"), self.session_hours_utc.get(str(symbol or "").strip().upper(), []))
        if self.uses_trend:
            params.update(
                _normalize_trend_reference_params(
                    raw,
                    defaults,
                    trend_timeframes=self.trend_timeframes,
                    entry_timeframes=self.recommended_timeframes,
                    excluded_methods=self.excluded_trend_methods,
                    path=f"strategies.{self.key}",
                )
            )
        params.update(_normalize_alternating_param(raw, defaults))
        return params


def _build_spec(
    key: str,
    add_indicators: Callable[..., pd.DataFrame],
    detect_signals: Callable[..., pd.DataFrame],
    add_entries: Callable[..., pd.DataFrame],
    order_type: str,
    bounds: dict[str, tuple[int, int]],
    *,
    timeframes_key: str = "supported_timeframes",
    ordered_pairs: tuple[tuple[str, str], ...] = (),
    trend: bool = True,
    session_hours: bool = False,
) -> StrategySpec:
    """Dựng StrategySpec từ mục ``strategies.<key>`` của de_config.yaml (bắt buộc đủ khoá, thiếu thì KeyError nêu đúng đường dẫn).

    ``timeframes_key`` là khoá chứa danh sách khung: ``supported_timeframes`` (giới hạn cứng, cũng là khung live) hoặc ``recommended_timeframes`` (chỉ là khung live, không giới hạn cứng).
    ``trend`` False (ichimoku): không có phần trend nào, mọi khung là khung chỉ-raw."""
    path = f"strategies.{key}"
    cfg = require(_STRATEGIES_CFG, key, path)
    timeframes = tuple(str(tf).upper() for tf in require(cfg, timeframes_key, f"{path}.{timeframes_key}"))
    defaults: dict[str, Any] = {name: int(require(cfg, name.lower(), f"{path}.{name.lower()}")) for name in bounds}
    if session_hours:
        defaults["SESSION_HOURS_UTC"] = []
    if trend:
        defaults.update(_strategy_trend_defaults(cfg, path))
    defaults.update(_strategy_alternating_default(cfg, path))
    trend_parts: dict[str, Any] = {"trend_off_timeframes": timeframes}
    if trend:
        trend_timeframes = _trend_timeframes(cfg, path)
        trend_off = _trend_off_timeframes(cfg, path, supported=timeframes)
        trend_parts = {
            "trend_timeframes": trend_timeframes,
            "trend_off_timeframes": trend_off,
            "trend_tf_by_symbol": _strategy_trend_tf_by_symbol(
                cfg, path, symbols=OPERATING_SYMBOLS, entry_timeframes=timeframes, trend_timeframes=trend_timeframes, trend_off_timeframes=trend_off
            ),
            "excluded_trend_methods": _excluded_trend_methods(cfg, path),
        }
    sessions = None
    if session_hours:
        sessions = {str(symbol).upper(): [int(h) for h in (hours or [])] for symbol, hours in require(cfg, "session_hours_utc", f"{path}.session_hours_utc").items()}
    return StrategySpec(
        key=key,
        add_indicators=add_indicators,
        detect_signals=detect_signals,
        add_entries=add_entries,
        order_type=order_type,
        bounds=bounds,
        default_params=defaults,
        ordered_pairs=ordered_pairs,
        recommended_timeframes=timeframes,
        supported_timeframes=timeframes if timeframes_key == "supported_timeframes" else (),
        session_hours_utc=sessions,
        **trend_parts,
    )


STRATEGIES: dict[str, StrategySpec] = {
    spec.key: spec
    for spec in (
        _build_spec(
            "combo", add_combo_indicators, detect_combo_signals, add_combo_entries, "STOP",
            {"MA_PERIOD": (2, 500), "MACD_FAST": (1, 200), "MACD_SLOW": (2, 300), "MACD_SIGNAL": (1, 200), "ATR_PERIOD": (2, 200)},
            timeframes_key="recommended_timeframes", session_hours=True,
        ),
        _build_spec(
            "ma_cross", add_ma_cross_indicators, detect_ma_cross_signals, add_ma_cross_entries, "MARKET",
            {"FAST_MA": (1, 300), "SLOW_MA": (2, 500), "MACD_FAST": (1, 200), "MACD_SLOW": (2, 300), "MACD_SIGNAL": (1, 200), "ATR_PERIOD": (2, 200)},
            ordered_pairs=(("FAST_MA", "SLOW_MA"), ("MACD_FAST", "MACD_SLOW")),
        ),
        _build_spec(
            "ema_reach", add_ema_reach_indicators, detect_ema_reach_signals, add_ema_reach_entries, "MARKET",
            {"FAST_EMA": (1, 300), "SLOW_EMA": (2, 300), "LOOKBACK_BARS": (2, 300), "MACD_FAST": (1, 200), "MACD_SLOW": (2, 300), "MACD_SIGNAL": (1, 200), "ATR_PERIOD": (2, 200)},
            ordered_pairs=(("FAST_EMA", "SLOW_EMA"), ("MACD_FAST", "MACD_SLOW")),
        ),
        _build_spec(
            "macd_break", add_macd_break_indicators, detect_macd_break_signals, add_macd_break_entries, "MARKET",
            {"LOOKBACK_BARS": (2, 300), "MACD_FAST": (1, 200), "MACD_SLOW": (2, 300), "MACD_SIGNAL": (1, 200), "ATR_PERIOD": (2, 200)},
            ordered_pairs=(("MACD_FAST", "MACD_SLOW"),),
        ),
        _build_spec(
            "ichimoku", add_ichimoku_indicators, detect_ichimoku_signals, add_ichimoku_entries, "MARKET",
            {"TENKAN_PERIOD": (2, 100), "KIJUN_PERIOD": (2, 200), "SENKOU_B_PERIOD": (2, 300), "DISPLACEMENT": (1, 100), "ATR_PERIOD": (2, 200)},
            trend=False,
        ),
    )
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




def trend_enabled_for(key: str, timeframe: str) -> bool:
    """
    True nếu chiến lược ``key`` còn dùng bộ lọc trend ở khung vào lệnh ``timeframe``; False nếu khung đó nằm trong
    `trend_off_timeframes` của chiến lược (macd_break H1–H4, mọi khung của ichimoku): chỉ nguồn raw phát, build_lane_job không gán
    nguồn lọc trend nào ở khung đó (không đọc nến trend, không tính, không ghi LIST/khoá nào).
    """
    return str(timeframe).strip().upper() not in get_strategy(key).trend_off_timeframes


def trend_method_applies(key: str, method: str) -> bool:
    """
    False nếu khối trend `method` nằm trong strategies.<tên>.excluded_trend_methods của chiến lược (macd_break + breakout từ 2026-10-28):
    khối đó không được áp cho chiến lược ở bất kỳ khung nào nên DE KHÔNG có nguồn đó cho chiến lược (không tính, không ghi). True ở mọi
    trường hợp khác (câu hỏi "khung này có lọc trend không" thuộc trend_enabled_for).
    """
    return str(method).strip().lower() not in get_strategy(key).excluded_trend_methods


@dataclass(frozen=True)
class TrendSource:
    """
    Cách chạy MỘT nguồn lọc trend (knn / breakout / zigzag / tstat / cloud) cho MỘT kênh (chiến lược, symbol, khung vào lệnh).

    trend_tf:       khung nến trend (knn, zigzag và tstat: ô trend_tf_by_symbol; breakout: chính khung vào lệnh; cloud: chính khung vào lệnh
                    nhưng không nhỏ hơn H1); ``""`` nếu kênh
                    không có ô trong bảng (symbol ngoài vũ trụ vận hành) — build_lane_job / build_trend_job dừng ngay lúc khởi động vì cấu hình sai.
    shifts_signals: khối có chính sách first_aligned_per_run (breakout, tstat, cloud): tín hiệu đã lọc có thể nằm ở nến không có tín hiệu
                    thô nên process_pair phải xét ở MỌI sự kiện của kênh; khối khác chỉ tính khi nến cuối có tín hiệu thô
                    (tín hiệu đã lọc luôn ⊆ tín hiệu thô).
    warmup_bars:    số nến khung trend tối thiểu khối đòi (POLICY.trend_warmup_bars; zigzag 3000); 0 = không đòi thêm.
    """

    trend_tf: str
    shifts_signals: bool
    warmup_bars: int


def trend_source_for(key: str, symbol: str, timeframe: str, method: str) -> TrendSource:
    """Khung trend + chính sách của khối ``method`` cho kênh (chiến lược, symbol, khung vào lệnh).

    Kênh nằm trong `trend_off_timeframes` của chiến lược (xem trend_enabled_for) không có nguồn trend nào: raise ValueError. Người gọi
    phải hỏi trend_enabled_for trước (build_lane_job / build_trend_job làm vậy ở mọi chỗ gọi), để lỗi cấu hình trend không bao giờ ảnh hưởng nguồn raw.
    """
    spec = get_strategy(key)
    if not trend_enabled_for(key, timeframe):
        raise ValueError(f"Strategy '{spec.key}' does not use a trend filter on timeframe {str(timeframe).strip().upper()} (trend_off_timeframes).")
    trend_tf = str(spec.normalize_params({"TREND_METHODS": method}, symbol, timeframe)["TREND_TF"])
    return TrendSource(trend_tf, uses_first_aligned_per_run((method,)), max_trend_warmup_bars((method,)))


def alternating_filter_for(key: str) -> bool | None:
    """Trả về cờ ``alternating_filter_enabled`` của 1 strategy, hoặc ``None``
    nếu strategy đó không có cờ này (hiện cả combo, ma_cross, macd_break, ichimoku và ema_reach đều có).

    configuration.run ghi giá trị này vào log khởi động: bật/tắt làm đổi số tín hiệu
    nhưng không đổi tên key nào ở db đầu ra, nên phải tra được từ log.
    """
    params = get_strategy(key).default_params
    if "ALTERNATING_FILTER_ENABLED" not in params:
        return None
    return bool(params["ALTERNATING_FILTER_ENABLED"])


LOGGER = logging.getLogger(__name__)

_REDIS_DEFAULTS: dict[str, Any] = {
    "port": 6379,
    "socket_timeout_seconds": 5,
    "socket_connect_timeout_seconds": 5,
    "healthcheck_interval_seconds": 30,
    "reconnect_delay_seconds": 3,
}
_REDIS_INPUT_DEFAULTS: dict[str, Any] = {"source_max_bars": 1200}
_REDIS_OUTPUT_DEFAULTS: dict[str, Any] = {"retention_seconds": 604800, "retention_max_entries": 500}
_REDIS_TREND_OUTPUT_DEFAULTS: dict[str, Any] = {"retention_seconds": 604800, "retention_max_entries": 1500}
_IDLE_WAIT_SECONDS = 1.0


@dataclass(frozen=True)
class TrendCell:
    """Một khối trend áp cho một ô (symbol, khung vào lệnh) của chiến lược: đọc xu hướng khung nào, bao nhiêu mốc, chạy chiến lược lọc trend với tham số nào, và khối có dời tín hiệu không."""

    method: str
    trend_tf: str
    shifts_signals: bool
    marks: int
    overrides: dict[str, Any]


@dataclass(frozen=True)
class LaneJob:
    """Việc của MỘT tiến trình chiến lược, suy ra từ de_config.yaml: chiến lược nào, nghe cặp nào, ghi ở đâu, hạn hiệu lực bao lâu, ô nào lọc trend nguồn nào."""

    strategy: str
    order_type: str
    validity: dict[str, Any]
    pairs: tuple[tuple[str, str], ...]
    source: dict[str, Any]
    sink: dict[str, Any]
    process_on_startup: bool
    reconnect_delay_seconds: float
    log_level: str
    outputs: tuple[tuple[str, str, str], ...] = ()
    trend_cells: dict[tuple[str, str], tuple[TrendCell, ...]] = field(default_factory=dict)
    trend_source: dict[str, Any] | None = None
    filtered_sinks: dict[str, dict[str, Any]] = field(default_factory=dict)
    input_name: str = ""


@dataclass(frozen=True)
class TrendJob:
    """Việc của MỘT tiến trình trend: khối nào, tính cho những khung trend nào (suy ra từ MỌI chiến lược dùng khối đó)."""

    method: str
    pairs: tuple[tuple[str, str], ...]
    source: dict[str, Any]
    sink: dict[str, Any]
    frame_params: dict[tuple[str, str], dict[str, Any]]
    process_on_startup: bool
    reconnect_delay_seconds: float
    log_level: str
    input_name: str = ""


def _input_source(redis_cfg: dict[str, Any], name: str) -> dict[str, Any]:
    """Khối cấu hình đã kiểm của nguồn nến thêm ``name``: đủ host/password/db/key_prefix/symbols, không có khoá lạ (timeframes, snapshot_bars... kế thừa redis.input, không đè được ở đây)."""
    blocks = _input_source_blocks(redis_cfg)
    if name not in blocks:
        raise ValueError(f"input source '{name}' is not declared in redis.input_sources (declared: {', '.join(blocks) or 'none'})")
    block = blocks[name]
    path = f"redis.input_sources.{name}"
    for key in ("host", "password", "db", "key_prefix", "symbols"):
        require(block, key, f"{path}.{key}")
    unknown = sorted(str(key) for key in set(block) - set(_INPUT_SOURCE_KEYS))
    if unknown:
        raise ValueError(f"de_config.yaml: '{path}' has unknown keys: {', '.join(unknown)} (allowed: {', '.join(_INPUT_SOURCE_KEYS)}; everything else is inherited from redis.input)")
    return block


def _parse_redis(config: dict[str, Any], input_name: str = "") -> dict[str, Any]:
    """Đọc và kiểm phần `redis:` của de_config.yaml (dùng chung cho tiến trình chiến lược và tiến trình trend). Sai thì KeyError/TypeError/ValueError nêu đúng đường dẫn.

    ``input_name`` chọn nguồn nến của nhóm tiến trình: rỗng = ``redis.input`` (Core AEN, cùng Redis với đầu ra); tên khác = ``redis.input_sources.<tên>`` (Redis riêng và nhóm symbol riêng).
    Đầu ra (db raw, trend, lọc) luôn ở Redis của DE bất kể nguồn nến nằm đâu, nên các nhóm khác nguồn ghi chung một bố cục khoá (khoá có symbol nên không đụng nhau)."""
    from core_engine.src.signal_publisher import timeframe_duration

    redis_cfg = require(config, "redis", "redis")
    for key in ("host", "password"):
        require(redis_cfg, key, f"redis.{key}")
    input_cfg = redis_cfg.get("input") or {}
    for key in ("db", "key_prefix", "snapshot_bars", "process_on_startup", "symbols", "timeframes"):
        require(input_cfg, key, f"redis.input.{key}")
    output_cfg = redis_cfg.get("output") or {}
    for key in ("db", "key_prefix", "channel_prefix", "port", "password"):
        require(output_cfg, key, f"redis.output.{key}")
    trend_cfg = redis_cfg.get("trend_outputs") or {}
    if not isinstance(trend_cfg, dict):
        raise TypeError("de_config.yaml: 'redis.trend_outputs' must map source name -> {key_prefix, channel_prefix}")

    top = {**_REDIS_DEFAULTS, **redis_cfg}
    inp = {**_REDIS_INPUT_DEFAULTS, **input_cfg}
    out = {**_REDIS_OUTPUT_DEFAULTS, **output_cfg}
    feed = {"host": str(top["host"]), "port": int(top["port"]), "password": str(top["password"] or "")}
    symbols_path = "redis.input.symbols"
    if input_name:
        block = _input_source(redis_cfg, input_name)
        feed = {"host": str(block["host"]), "port": int(block.get("port", _REDIS_DEFAULTS["port"])), "password": str(block["password"] or "")}
        inp = {**inp, "db": block["db"], "key_prefix": block["key_prefix"], "symbols": block["symbols"]}
        symbols_path = f"redis.input_sources.{input_name}.symbols"
    symbols = _lane_names(inp["symbols"], symbols_path)
    timeframes = _lane_names(inp["timeframes"], "redis.input.timeframes")
    for timeframe in timeframes:
        timeframe_duration(timeframe)
    snapshot_bars = int(inp["snapshot_bars"])
    if snapshot_bars <= 0:
        raise ValueError("redis.input.snapshot_bars must be positive")
    source_max_bars = int(inp["source_max_bars"])
    if source_max_bars <= 0:
        raise ValueError("redis.input.source_max_bars must be positive")
    if not str(out["key_prefix"]).strip():
        raise ValueError("redis.output.key_prefix must be configured")
    if not str(out["channel_prefix"]).strip():
        raise ValueError("redis.output.channel_prefix must be configured")
    if int(out["retention_seconds"]) <= 0:
        raise ValueError("redis.output.retention_seconds must be positive")
    if int(out["retention_max_entries"]) <= 0:
        raise ValueError("redis.output.retention_max_entries must be positive")

    registered = method_names()
    trend_outputs: dict[str, tuple[str, str]] = {}
    for raw_name, source_cfg in trend_cfg.items():
        name = str(raw_name).strip().lower()
        if name == "raw":
            raise ValueError(f"de_config.yaml: 'redis.trend_outputs.{raw_name}' is not a trend source: 'raw' is the unfiltered output, configured in redis.output")
        if name not in registered:
            raise ValueError(f"de_config.yaml: 'redis.trend_outputs.{name}' is not a trend source (valid: {', '.join(registered)})")
        source_cfg = source_cfg or {}
        for key in ("key_prefix", "channel_prefix"):
            require(source_cfg, key, f"redis.trend_outputs.{name}.{key}")
            if not str(source_cfg[key]).strip():
                raise ValueError(f"redis.trend_outputs.{name}.{key} must be configured")
        trend_outputs[name] = (str(source_cfg["key_prefix"]), str(source_cfg["channel_prefix"]))
    for index, field_name in enumerate(("key_prefix", "channel_prefix")):
        seen: dict[str, str] = {}
        for source, prefixes in (("raw", (str(out["key_prefix"]), str(out["channel_prefix"]))), *trend_outputs.items()):
            prefix = prefixes[index].strip()
            if prefix in seen:
                raise ValueError(f"redis outputs '{seen[prefix]}' and '{source}' share the same {field_name} '{prefix}': each source needs its own")
            seen[prefix] = source

    trend_out_cfg = redis_cfg.get("trend_output") or {}
    for key in ("db", "key_prefix"):
        require(trend_out_cfg, key, f"redis.trend_output.{key}")
    trend_out = {**_REDIS_TREND_OUTPUT_DEFAULTS, **trend_out_cfg}
    if not str(trend_out["key_prefix"]).strip():
        raise ValueError("redis.trend_output.key_prefix must be configured")
    if int(trend_out["db"]) == int(out["db"]):
        raise ValueError("redis.trend_output.db must differ from redis.output.db (same Redis, each output needs its own database)")
    if int(trend_out["retention_seconds"]) <= 0 or int(trend_out["retention_max_entries"]) <= 0:
        raise ValueError("redis.trend_output retention values must be positive")
    filtered_cfg = redis_cfg.get("filtered_output") or {}
    require(filtered_cfg, "db", "redis.filtered_output.db")
    filtered_db = int(filtered_cfg["db"])
    if filtered_db in (int(out["db"]), int(trend_out["db"])):
        raise ValueError("redis.filtered_output.db must differ from redis.output.db and redis.trend_output.db (same Redis, each output needs its own database)")
    feed_db = (feed["host"], feed["port"], int(inp["db"]))
    for label, output_db in (("output", int(out["db"])), ("trend output", int(trend_out["db"])), ("filtered output", filtered_db)):
        if feed_db == (str(top["host"]), int(out["port"]), output_db):
            raise ValueError(f"Redis input and {label} must use different databases")
    timeouts = {
        "socket_timeout": float(top["socket_timeout_seconds"]),
        "connect_timeout": float(top["socket_connect_timeout_seconds"]),
        "healthcheck_interval": int(top["healthcheck_interval_seconds"]),
    }
    return {
        "symbols": symbols,
        "timeframes": timeframes,
        "snapshot_bars": snapshot_bars,
        "source_max_bars": source_max_bars,
        "process_on_startup": bool(inp["process_on_startup"]),
        "reconnect_delay": float(top["reconnect_delay_seconds"]),
        "trend_outputs": trend_outputs,
        "outputs": (
            ("raw", str(out["key_prefix"]), str(out["channel_prefix"])),
            *((name, prefixes[0], prefixes[1]) for name, prefixes in trend_outputs.items()),
        ),
        "source": {**feed, "db": int(inp["db"]), "key_prefix": str(inp["key_prefix"]), **timeouts},
        "sink": {
            "host": str(top["host"]), "port": int(out["port"]), "password": str(out["password"] or ""), "db": int(out["db"]),
            "key_prefix": str(out["key_prefix"]), "channel_prefix": str(out["channel_prefix"]),
            "retention_seconds": int(out["retention_seconds"]), "retention_max_entries": int(out["retention_max_entries"]), **timeouts,
        },
        "trend_sink": {
            "host": str(top["host"]), "port": int(out["port"]), "password": str(out["password"] or ""), "db": int(trend_out["db"]),
            "key_prefix": str(trend_out["key_prefix"]), "retention_seconds": int(trend_out["retention_seconds"]),
            "retention_max_entries": int(trend_out["retention_max_entries"]), **timeouts,
        },
        "trend_source": {
            "host": str(top["host"]), "port": int(out["port"]), "password": str(out["password"] or ""), "db": int(trend_out["db"]),
            "key_prefix": str(trend_out["key_prefix"]), **timeouts,
        },
        "filtered_sink": {
            "host": str(top["host"]), "port": int(out["port"]), "password": str(out["password"] or ""), "db": filtered_db,
            "retention_seconds": int(out["retention_seconds"]), "retention_max_entries": int(out["retention_max_entries"]), **timeouts,
        },
    }


def _trend_bars(base: dict[str, Any], trend: TrendSource) -> int:
    """Số nến khung trend mà một khối tính trên: khối đòi bao nhiêu (zigzag 3000) nhưng không quá số nến nguồn thật sự giữ, và không ít hơn snapshot_bars (như bản cũ)."""
    return max(base["snapshot_bars"], min(trend.warmup_bars, base["source_max_bars"]))


def _method_params_repr(method: str, params: dict[str, Any]) -> str:
    """Tham số kỹ thuật của khối ``method`` (knn, tstat, zigzag có; breakout và cloud không) dưới dạng chuỗi so sánh được; rỗng nếu khối không có tham số."""
    extractor = getattr(get_method(method), f"{method}_trend_indicator_params", None)
    return "" if extractor is None else repr(sorted(extractor(params).items()))


def _trend_overrides(source: str, trend_tf: str) -> dict[str, Any]:
    """Bộ tham số đè để chiến lược chạy CÓ lọc trend bằng đúng một khối: dùng cho cả tính xu hướng (``_trend_params``) lẫn nhánh lọc của tiến trình chiến lược (``TrendCell.overrides``)."""
    return {"TREND_METHODS": source, "TREND_FILTER_ENABLED": True, "TREND_TF": trend_tf}


def _trend_params(spec: StrategySpec, source: str, symbol: str, timeframe: str, trend_tf: str) -> dict[str, Any]:
    """Tham số chuẩn hoá của một khối trend ở một ô (cùng cách chuẩn hoá của chiến lược), để dựng tham số tính trend."""
    return spec.normalize_params(_trend_overrides(source, trend_tf), symbol, timeframe)


def _checked_trend_source(spec: StrategySpec, base: dict[str, Any], source: str, symbol: str, timeframe: str) -> TrendSource:
    trend = trend_source_for(spec.key, symbol, timeframe, source)
    check_trend_timeframe(timeframe, trend.trend_tf)
    if trend.trend_tf not in base["timeframes"]:
        raise ValueError(f"{spec.key} {symbol} {timeframe}: trend timeframe {trend.trend_tf} of source '{source}' is not in redis.input.timeframes")
    return trend


def _lane_trend_cells(spec: StrategySpec, base: dict[str, Any], pairs: tuple[tuple[str, str], ...]) -> dict[tuple[str, str], tuple[TrendCell, ...]]:
    """Bảng "ô -> các khối trend áp cho ô" của một chiến lược, dựng một lần lúc khởi động bằng đúng các hàm mà phía trend dùng (``trend_enabled_for``, ``trend_method_applies``, ``_checked_trend_source``),
    nên hai bên luôn khớp nhau: ô thuộc khung tắt trend, hoặc khối bị loại khỏi chiến lược, không có nhánh lọc. Sai cấu hình (khung trend nhỏ hơn khung vào lệnh, tham số không hợp lệ) thì ValueError ngay."""
    cells: dict[tuple[str, str], tuple[TrendCell, ...]] = {}
    for symbol, timeframe in pairs:
        if not trend_enabled_for(spec.key, timeframe):
            continue
        found: list[TrendCell] = []
        for method in base["trend_outputs"]:
            if not trend_method_applies(spec.key, method):
                continue
            trend = _checked_trend_source(spec, base, method, symbol, timeframe)
            overrides = _trend_overrides(method, trend.trend_tf)
            spec.normalize_params(overrides, symbol, timeframe)
            marks = math.ceil(base["snapshot_bars"] * timeframe_minutes(timeframe) / timeframe_minutes(trend.trend_tf)) + 2
            found.append(TrendCell(method, trend.trend_tf, trend.shifts_signals, marks, overrides))
        if found:
            cells[(symbol, timeframe)] = tuple(found)
    return cells


def _price_decimals(live: dict[str, Any]) -> dict[str, int]:
    """Đọc ``live.price_decimals`` (tuỳ chọn): symbol -> số lẻ làm tròn entry/atr của tín hiệu (forex cần 5 hoặc 3; symbol không khai dùng ``DEFAULT_PRICE_DECIMALS``).
    Symbol không thuộc DE hoặc số lẻ ngoài 0..8 thì ValueError, để gõ sai tên symbol không âm thầm làm tròn forex về 2 số lẻ."""
    raw = live.get("price_decimals") or {}
    if not isinstance(raw, dict):
        raise TypeError("de_config.yaml: 'live.price_decimals' must map symbol -> number of decimals")
    decimals: dict[str, int] = {}
    for raw_symbol, digits in raw.items():
        symbol = str(raw_symbol).strip().upper()
        if symbol not in OPERATING_SYMBOLS:
            raise ValueError(f"de_config.yaml: 'live.price_decimals.{raw_symbol}' is not an operating symbol")
        if symbol in decimals:
            raise ValueError(f"de_config.yaml: 'live.price_decimals' lists symbol {symbol} twice")
        if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 8:
            raise ValueError(f"de_config.yaml: 'live.price_decimals.{raw_symbol}' must be an integer between 0 and 8")
        decimals[symbol] = digits
    return decimals


def build_lane_job(config: dict[str, Any], strategy: str, input_name: str = "") -> LaneJob:
    """Dựng và kiểm MỌI cấu hình của một tiến trình chiến lược TRƯỚC khi chạm Redis; sai thì ValueError/KeyError/TypeError (người gọi ghi config_error), không có gì được ghi.
    ``input_name`` là nguồn nến của nhóm tiến trình (xem ``_parse_redis``): cặp của tiến trình chỉ gồm symbol của nguồn đó."""
    from core_engine.src.signal_publisher import check_order_type, signal_window

    spec = get_strategy(strategy)
    live = require(config, "live", "live")
    enabled = [get_strategy(str(item)).key for item in require(live, "enabled_strategies", "live.enabled_strategies")]
    if spec.key not in enabled:
        raise ValueError(f"strategy '{spec.key}' is not in live.enabled_strategies ({', '.join(enabled)})")
    base = _parse_redis(config, input_name)
    decimals = _price_decimals(live)

    if not spec.live_timeframes:
        raise ValueError(f"Strategy '{spec.key}' has no live timeframes")
    validity_cfg = require(live, "signal_validity", "live.signal_validity")
    if spec.key not in validity_cfg:
        raise KeyError(f"de_config.yaml thiếu 'live.signal_validity.{spec.key}'")
    validity = dict(validity_cfg[spec.key])
    signal_window(spec.key, spec.live_timeframes[0], pd.Timestamp("2026-01-01", tz="UTC"), validity)
    order = check_order_type(spec.order_type)

    pairs = tuple((symbol, timeframe) for timeframe in spec.live_timeframes if timeframe in base["timeframes"] for symbol in base["symbols"])
    trend_cells = _lane_trend_cells(spec, base, pairs)
    used = {cell.method for cells in trend_cells.values() for cell in cells}
    return LaneJob(
        strategy=spec.key,
        order_type=order,
        validity=validity,
        pairs=pairs,
        source={**base["source"], "snapshot_bars": base["snapshot_bars"]},
        sink={**base["sink"], "price_decimals": decimals},
        process_on_startup=base["process_on_startup"],
        reconnect_delay_seconds=base["reconnect_delay"],
        log_level=str(require(live, "log_level", "live.log_level")),
        outputs=tuple(output for output in base["outputs"] if output[0] == "raw" or output[0] in used),
        trend_cells=trend_cells,
        trend_source=base["trend_source"] if trend_cells else None,
        filtered_sinks={
            name: {**base["filtered_sink"], "key_prefix": prefixes[0], "channel_prefix": prefixes[1], "source": name, "price_decimals": decimals}
            for name, prefixes in base["trend_outputs"].items()
            if name in used
        },
        input_name=input_name,
    )


def build_trend_job(config: dict[str, Any], method: str, input_name: str = "") -> TrendJob:
    """Dựng và kiểm việc của một tiến trình trend. Khung trend cần tính suy ra từ MỌI chiến lược đang bật dùng khối này (bảng khung trend, `trend_off`, khối bị loại) -- cùng nguồn sự thật
    với phía chiến lược, nên hai bên luôn khớp nhau. Một khung mà hai chiến lược đòi tính khác nhau (tham số khối, số nến) thì ValueError: một kết quả trend không phục vụ được cả hai.
    ``input_name`` là nguồn nến của nhóm tiến trình (xem ``_parse_redis``): khung cần tính chỉ gồm symbol của nguồn đó."""
    method = str(method).strip().lower()
    base = _parse_redis(config, input_name)
    live = require(config, "live", "live")
    if method not in base["trend_outputs"]:
        raise ValueError(f"trend source '{method}' is not declared in redis.trend_outputs")
    enabled = [get_strategy(str(item)).key for item in require(live, "enabled_strategies", "live.enabled_strategies")]
    frames: dict[tuple[str, str], dict[str, Any]] = {}
    for key in enabled:
        spec = get_strategy(key)
        if not trend_method_applies(key, method):
            continue
        for timeframe in spec.live_timeframes:
            if timeframe not in base["timeframes"] or not trend_enabled_for(key, timeframe):
                continue
            for symbol in base["symbols"]:
                trend = _checked_trend_source(spec, base, method, symbol, timeframe)
                params = _trend_params(spec, method, symbol, timeframe, trend.trend_tf)
                found = {"params": params, "repr": _method_params_repr(method, params), "bars": _trend_bars(base, trend), "who": f"{key} {timeframe}"}
                frame = (symbol, trend.trend_tf)
                known = frames.setdefault(frame, found)
                if known["repr"] != found["repr"] or known["bars"] != found["bars"]:
                    raise ValueError(
                        f"trend source '{method}' frame {symbol}/{trend.trend_tf} would be computed two different ways ({known['who']} vs {found['who']}): one trend result cannot serve both"
                    )
    if not frames:
        raise ValueError(f"no enabled strategy uses trend source '{method}'")
    bars_values = {item["bars"] for item in frames.values()}
    if len(bars_values) != 1:
        raise ValueError(f"trend source '{method}' needs different candle counts for different frames: {sorted(bars_values)}")
    bars = bars_values.pop()
    return TrendJob(
        method=method,
        pairs=tuple(sorted(frames)),
        source={**base["source"], "snapshot_bars": bars},
        sink=base["trend_sink"],
        frame_params={frame: item["params"] for frame, item in frames.items()},
        process_on_startup=base["process_on_startup"],
        reconnect_delay_seconds=base["reconnect_delay"],
        log_level=str(require(live, "log_level", "live.log_level")),
        input_name=input_name,
    )


def _publish_filtered(job: LaneJob, cell: TrendCell, candles: pd.DataFrame, pair: Any, current_time: pd.Timestamp, trend_reader: Any, writer: Any) -> int:
    """Nhánh 2 của MỘT khối trend ở một cặp: đọc xu hướng của khối (db trend) -> chạy chiến lược có lọc trend (ghép xu hướng, lọc, dời tín hiệu) -> ghi dòng cuối vào db lọc.
    Trả về số tín hiệu ghi mới (0 hoặc 1). Chưa có xu hướng nào của khối (tiến trình trend chưa ghi, hoặc mọi mốc đã hết hạn) thì cảnh báo và bỏ khối này lần này, không dùng gì thay thế."""
    from core_engine.src import strategy_runner

    table = trend_reader.read(cell.method, pair.symbol, cell.trend_tf, cell.marks)
    if table.empty:
        LOGGER.warning(
            "event=trend_missing strategy=%s symbol=%s timeframe=%s trend=%s trend_timeframe=%s", job.strategy, pair.symbol, pair.timeframe, cell.method, cell.trend_tf,
        )
        return 0
    frame = strategy_runner.run_strategy(
        job.strategy, symbol=pair.symbol, tf=pair.timeframe, bars=candles, overrides=cell.overrides, trend=table,
    )
    outcome = writer.publish_latest(
        strategy=job.strategy,
        order_type=job.order_type,
        validity=job.validity,
        timeframe=pair.timeframe,
        symbol=pair.symbol,
        latest=frame.iloc[-1],
        now=current_time,
    )
    return outcome.published


def process_pair(
    job: LaneJob, engine: Any, publisher: Any, pair: Any, now: pd.Timestamp | None = None, *, trend_reader: Any = None, filtered: dict[str, Any] | None = None
) -> Any:
    """Luồng của MỘT cặp: bảng nến mới nhất (data_engine) -> chiến lược KHÔNG xét xu hướng (strategy_runner) -> hai nhánh.

    Nhánh 1 (như cũ): kiểm hạn rồi ghi tín hiệu thô (``publisher``, db0). Ghi TRƯỚC, độc lập với xu hướng nên lỗi hay thiếu xu hướng không ảnh hưởng nó.
    Nhánh 2: với từng khối trend áp cho ô này (``job.trend_cells``), ghi tín hiệu đã lọc trend của khối vào db lọc bằng bộ ghi ``filtered[khối]``, xu hướng đọc bằng ``trend_reader``.
    Khối không dời tín hiệu chỉ xét khi nến mới nhất có tín hiệu thô (tín hiệu lọc của nó luôn là một phần của tín hiệu thô); khối dời xét ở MỌI nến mới vì tín hiệu dời nằm ở nến không có tín hiệu thô.
    Lỗi riêng của một khối (không phải mất Redis) được ghi log rồi bỏ khối đó, các khối khác vẫn chạy.

    Trả về ``strategy_runner.Outcome`` (trạng thái của nhánh 1, số tín hiệu ghi mới cộng cả nhánh 2), hoặc None nếu db0 chưa có nến nào của cặp (coi như không có, không cảnh báo).
    """
    from core_engine.src import strategy_runner

    candles = engine.snapshot(pair)
    if candles is None or candles.empty:
        return None
    current_time = now if now is not None else pd.Timestamp.now(tz="UTC")
    frame = strategy_runner.run_strategy(job.strategy, symbol=pair.symbol, tf=pair.timeframe, bars=candles)
    latest = frame.iloc[-1]
    outcome = publisher.publish_latest(
        strategy=job.strategy,
        order_type=job.order_type,
        validity=job.validity,
        timeframe=pair.timeframe,
        symbol=pair.symbol,
        latest=latest,
        now=current_time,
    )
    cells = job.trend_cells.get((pair.symbol, pair.timeframe), ())
    if not cells:
        return outcome
    published = outcome.published
    for cell in cells:
        if not cell.shifts_signals and int(latest["raw_signal"]) == 0:
            continue
        try:
            published += _publish_filtered(job, cell, candles, pair, current_time, trend_reader, (filtered or {})[cell.method])
        except redis.RedisError:
            raise
        except Exception:
            LOGGER.exception("event=process_failed symbol=%s timeframe=%s trend=%s", pair.symbol, pair.timeframe, cell.method)
    return strategy_runner.Outcome(outcome.state, published)


def process_trend_pair(job: TrendJob, engine: Any, output: Any, pair: Any) -> Any:
    """Luồng của MỘT khung trend: bảng nến mới nhất (data_engine) -> dòng thời gian hướng trend (trend_filtering) -> ghi List–Hash vào db trend (``TrendOutput``).
    Trả về số nến trend ghi mới; None nếu db0 chưa có nến của khung."""
    from core_engine.src import strategy_runner, trend_filtering

    candles = engine.snapshot(pair)
    if candles is None or candles.empty:
        return None
    timeline = trend_filtering.compute_timeline(candles, job.frame_params[(pair.symbol, pair.timeframe)])
    return strategy_runner.Outcome("written", output.write(job.method, pair.symbol, pair.timeframe, timeline))


def _guarded(step: Callable[[], Any], pair: Any) -> Any:
    """Chạy một bước của một cặp, cô lập lỗi riêng của cặp (ghi log, trả None) nhưng để redis.RedisError nổi lên cho vòng nối lại."""
    try:
        return step()
    except redis.RedisError:
        raise
    except Exception:
        LOGGER.exception("event=process_failed symbol=%s timeframe=%s", pair.symbol, pair.timeframe)
        return None


class _Sweep:
    """Một lượt quét toàn bộ cặp của tiến trình (chỉ lúc khởi động): chờ xử lý xong mọi cặp rồi mới ghi event=reconcile_end."""

    def __init__(self, pairs: Any, started: float) -> None:
        self.pending = set(pairs)
        self.total = len(self.pending)
        self.published = 0
        self.started = started

    def done(self, pair: Any, outcome: Any) -> None:
        self.pending.discard(pair)
        if outcome is not None:
            self.published += outcome.published


def _run_loop(
    job: Any,
    engine: Any,
    step: Callable[[Any], Any],
    *,
    clock: Callable[[], float] = time.monotonic,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    """Vòng lặp sự kiện chung của mọi loại tiến trình. Sự kiện keyspace chỉ đánh dấu cặp "bẩn"; vòng lặp lấy từng cặp bẩn và chạy ``step`` trên nến mới nhất, nên nhiều sự kiện của
    cùng một cặp (đợt ghi bù) gộp thành một lần xử lý. Giữa hai cặp luôn hút tiếp event đang chờ. Lúc khởi động quét toàn bộ cặp MỘT lần
    (``process_on_startup``) để bù nến đã tới lúc tiến trình tắt; không có nhịp quét định kỳ. Chạy mãi; test dừng bằng ``should_stop``."""
    pairs = engine.pairs
    dirty: dict[Any, None] = {}
    sweep: _Sweep | None = None

    def begin_sweep() -> None:
        nonlocal sweep
        LOGGER.info("event=reconcile_start db=%s pairs=%d", job.source["db"], len(pairs))
        sweep = _Sweep(pairs, clock())
        for pair in pairs:
            dirty.setdefault(pair)

    if job.process_on_startup:
        begin_sweep()
    while not should_stop():
        for pair in engine.poll_events(0.0 if dirty else _IDLE_WAIT_SECONDS):
            dirty.setdefault(pair)
        if not dirty:
            continue

        pair = next(iter(dirty))
        del dirty[pair]
        outcome = step(pair)
        if sweep is not None:
            sweep.done(pair, outcome)
            if not sweep.pending:
                LOGGER.info("event=reconcile_end pairs=%d published=%d duration_s=%.1f", sweep.total, sweep.published, clock() - sweep.started)
                from core_engine.src import strategy_runner

                stats = strategy_runner.memo_stats()
                if stats:
                    LOGGER.info("event=memo_stats hits=%d misses=%d size=%d", stats["hits"], stats["misses"], stats["size"])
                sweep = None


def run_lane(
    job: LaneJob,
    engine: Any,
    publisher: Any,
    *,
    trend_reader: Any = None,
    filtered: dict[str, Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    """Vòng lặp sự kiện của một tiến trình chiến lược (xem ``_run_loop``): mỗi cặp bẩn chạy ``process_pair`` (nhánh 1 qua ``publisher``, nhánh 2 qua ``trend_reader`` và ``filtered``)."""
    _run_loop(
        job, engine, lambda pair: _guarded(lambda: process_pair(job, engine, publisher, pair, trend_reader=trend_reader, filtered=filtered), pair), clock=clock, should_stop=should_stop,
    )


def run_trend_lane(
    job: TrendJob,
    engine: Any,
    output: Any,
    *,
    clock: Callable[[], float] = time.monotonic,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    """Vòng lặp sự kiện của một tiến trình trend (xem ``_run_loop``): mỗi khung trend bẩn chạy ``process_trend_pair``."""
    _run_loop(job, engine, lambda pair: _guarded(lambda: process_trend_pair(job, engine, output, pair), pair), clock=clock, should_stop=should_stop)


def _run_forever(job: LaneJob) -> None:
    """Nối Redis, đăng ký nghe đúng các cặp, rồi chạy vòng lặp sự kiện mãi mãi. Việc nối và nối lại khi mất Redis (sau ``reconnect_delay_seconds``) do ``redis_connector.keep_connected`` làm."""
    from core_engine.src.data_engine import DataEngine
    from core_engine.src.redis_connector import keep_connected
    from core_engine.src.strategy_runner import RawSignalWriter
    from core_engine.src.trend_filtering import TrendReader

    def connect(register: Callable[[Any], Any]) -> tuple[Any, Any, Any, dict[str, Any]]:
        engine = register(DataEngine(**job.source, pairs=job.pairs))
        publisher = register(RawSignalWriter(**job.sink))
        trend_reader = register(TrendReader(**job.trend_source)) if job.trend_source else None
        filtered = {method: register(RawSignalWriter(**params)) for method, params in job.filtered_sinks.items()}
        engine.ping()
        publisher.ping()
        if trend_reader is not None:
            trend_reader.ping()
        for writer in filtered.values():
            writer.ping()
        if trend_reader is None:
            LOGGER.info("event=redis_connected input_db=%s output_db=%s", job.source["db"], job.sink["db"])
        else:
            LOGGER.info(
                "event=redis_connected input_db=%s output_db=%s trend_db=%s filtered_db=%s",
                job.source["db"], job.sink["db"], job.trend_source["db"], next(iter(job.filtered_sinks.values()))["db"],
            )
        flags = engine.check_keyspace_flags()
        subscribed = engine.subscribe()
        LOGGER.info("event=subscribed db=%s key_prefix=%s notify_flags=%s pairs=%d", job.source["db"], job.source["key_prefix"], flags, subscribed)
        return engine, publisher, trend_reader, filtered

    keep_connected(
        connect, lambda parts: run_lane(job, parts[0], parts[1], trend_reader=parts[2], filtered=parts[3]), retry_delay_seconds=job.reconnect_delay_seconds,
    )


def _run_trend_forever(job: TrendJob) -> None:
    """Như ``_run_forever`` cho tiến trình trend: nối db0 (nến khung trend) và db trend của Redis đầu ra, nghe đúng các khung trend, chạy vòng lặp mãi mãi."""
    from core_engine.src.data_engine import DataEngine
    from core_engine.src.redis_connector import keep_connected
    from core_engine.src.trend_filtering import TrendOutput

    def connect(register: Callable[[Any], Any]) -> tuple[Any, Any]:
        engine = register(DataEngine(**job.source, pairs=job.pairs))
        output = register(TrendOutput(**job.sink))
        engine.ping()
        output.ping()
        LOGGER.info("event=redis_connected input_db=%s output_db=%s", job.source["db"], job.sink["db"])
        flags = engine.check_keyspace_flags()
        subscribed = engine.subscribe()
        LOGGER.info("event=subscribed db=%s key_prefix=%s notify_flags=%s pairs=%d", job.source["db"], job.source["key_prefix"], flags, subscribed)
        return engine, output

    keep_connected(connect, lambda parts: run_trend_lane(job, parts[0], parts[1]), retry_delay_seconds=job.reconnect_delay_seconds)


def _redis_enabled(config: dict[str, Any]) -> bool:
    enabled = require(config["redis"], "enabled", "redis.enabled")
    if not isinstance(enabled, bool):
        raise TypeError("redis.enabled must be true or false")
    return enabled


def run(strategy: str, input_name: str = "") -> int:
    """Chạy một tiến trình chiến lược (gọi từ main.py); ``input_name`` chọn nguồn nến (rỗng = redis.input, tên khác = redis.input_sources.<tên>, file log ``de_run_<chiến lược>_<tên>.log``).
    Trả về mã thoát: 0 khi dừng êm hoặc redis.enabled=false, 1 khi cấu hình sai (đã ghi event=config_error)."""
    from core_engine.src.log import configure_logging, set_log_level

    lane = f"{strategy}_{input_name}" if input_name else strategy
    configure_logging("INFO", lane)
    try:
        job = build_lane_job(CONFIG, strategy, input_name)
        set_log_level(job.log_level)
        LOGGER.info(
            "event=startup strategies=%s log_level=%s redis_input_db=%s redis_output_db=%s output_sources=%s lane=%s",
            job.strategy, job.log_level, job.source["db"], job.sink["db"], ",".join(name for name, _, _ in job.outputs), lane,
        )
        LOGGER.info("event=input_source name=%s host=%s port=%s symbols=%d", input_name or "default", job.source["host"], job.source["port"], len({symbol for symbol, _ in job.pairs}))
        for name, key_prefix, channel_prefix in job.outputs:
            LOGGER.info("event=output_source source=%s key_prefix=%s channel_prefix=%s", name, key_prefix, channel_prefix)
        if job.trend_cells:
            LOGGER.info(
                "event=trend_cells cells=%d trend_db=%s filtered_db=%s",
                len(job.trend_cells), job.trend_source["db"], next(iter(job.filtered_sinks.values()))["db"],
            )
        alternating = alternating_filter_for(job.strategy)
        if alternating is not None:
            LOGGER.info("event=alternating_filter strategy=%s enabled=%s", job.strategy, alternating)
        if not _redis_enabled(CONFIG):
            LOGGER.info("event=disabled reason=redis_disabled_by_config")
            return 0
        from core_engine.src import strategy_runner

        strategy_runner.enable_memo(256)
        LOGGER.info("event=memo enabled=true maxsize=256")
        _run_forever(job)
    except (KeyboardInterrupt, SystemExit):
        LOGGER.info("event=stopped")
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        LOGGER.critical("event=config_error error=%s", exc)
        return 1
    return 0


def run_trend(method: str, input_name: str = "") -> int:
    """Chạy một tiến trình trend (gọi từ main.py), mã thoát như ``run``. File log riêng ``de_run_trend_<khối>.log`` (nguồn nến thêm: ``de_run_trend_<khối>_<tên>.log``)."""
    from core_engine.src.log import configure_logging, set_log_level

    name = f"trend_{str(method).strip().lower()}"
    if input_name:
        name = f"{name}_{input_name}"
    configure_logging("INFO", name)
    try:
        job = build_trend_job(CONFIG, method, input_name)
        set_log_level(job.log_level)
        LOGGER.info(
            "event=startup trend=%s log_level=%s redis_input_db=%s redis_output_db=%s frames=%d bars=%d lane=%s",
            job.method, job.log_level, job.source["db"], job.sink["db"], len(job.pairs), job.source["snapshot_bars"], name,
        )
        LOGGER.info("event=input_source name=%s host=%s port=%s symbols=%d", input_name or "default", job.source["host"], job.source["port"], len({symbol for symbol, _ in job.pairs}))
        LOGGER.info("event=trend_output key_prefix=%s", job.sink["key_prefix"])
        if not _redis_enabled(CONFIG):
            LOGGER.info("event=disabled reason=redis_disabled_by_config")
            return 0
        _run_trend_forever(job)
    except (KeyboardInterrupt, SystemExit):
        LOGGER.info("event=stopped")
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        LOGGER.critical("event=config_error error=%s", exc)
        return 1
    return 0
