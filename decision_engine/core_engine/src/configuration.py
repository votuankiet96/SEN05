"""
Tham số chiến lược + lưới ô theo bảng của we + sổ đăng ký nối pipeline cho DE, và điều phối tiến trình.

Mô tả:
    1. Tham số của từng chiến lược (Combo, MA Cross, EMA Reach, MACD Break, Ichimoku) — bắt buộc có trong de_config.yaml (mục `strategies:`), là khu vực operator
       chỉnh trực tiếp. Thiếu bất kỳ khoá nào sẽ raise lỗi rõ ràng ngay khi import — không có fallback âm thầm nào trong code. Giới hạn min/max (``bounds``) và các
       luật "nhỏ < lớn" (``ordered_pairs``) là code vì đó là validate đầu vào, không phải giá trị vận hành. strategies/<tên>.py và rules/entries.py KHÔNG giữ
       tham số nào — chỉ nhận dict params đã hoàn chỉnh từ ``StrategySpec.normalize_params``.
    2. Sổ đăng ký chiến lược (StrategySpec/STRATEGIES) — nối indicator.py (chỉ báo) + strategies/<tên>.py (tín hiệu BUY/SELL) + rules/entries.py (entry) thành một
       pipeline, tra theo key ("combo"/"ma_cross"/"ema_reach"/"macd_break"/"ichimoku"). Mỗi chiến lược là MỘT lời gọi ``_build_spec``.
    3. LƯỚI Ô (``build_cells``): mỗi ô là (symbol, chiến lược, khung vào lệnh = khung trigger) -- đủ 15 symbol x 5 chiến lược x 8 khung. Khung trend của ô tra bảng `strategies.<tên>.trend_tf_by_symbol`
       (bảng của we; khối same_timeframe như breakout, cloud tự suy khung từ khung vào lệnh); khung context khai ở bảng `context_tf_by_symbol` (bắt buộc đủ mọi symbol x khung; chủ hệ chốt 10/11/2026: bằng chính khung trigger). Luật khung: trigger <= context <= trend.
    4. Điều phối HAI loại tiến trình, dựng việc từ config trước khi chạm Redis (build_runner_job / build_publisher_job), vòng lặp sự kiện, run_runner / run_publisher:
         * runner (strategy_runner.py), mỗi nguồn nến một nhóm: nến AEN hoặc OG8 -> TRIGGER + CONTEXT (db0) và TREND (db1) cho MỌI (symbol, khung), không phụ thuộc cấu hình ô (process_frame);
         * publisher (signal_publisher.py): db0/db1 -> ghép theo luật nến đóng, chờ khi nến tham chiếu đóng đúng lúc chưa về -> lọc trend/dời, entry -> tín hiệu vào db2 (process_cell).
       Hai loại tiến trình chỉ trao đổi qua Redis; chết hay restart tiến trình này không kéo theo tiến trình kia.

Đầu ra:
    STRATEGIES: dict[str, StrategySpec] — tra cứu theo key; spec.normalize_params(overrides, symbol, tf) trả tham số đã validate.
    get_strategy(key): StrategySpec hoặc raise KeyError.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import redis
import yaml
from core_engine.src.data_engine import Pair
from core_engine.src.indicator import (
    add_combo_indicators,
    add_ema_reach_indicators,
    add_ichimoku_indicators,
    add_ma_cross_indicators,
    add_macd_break_indicators,
    merge_closed_reference,
    timeframe_minutes,
)
from core_engine.src.rules.entries import (
    add_combo_entries,
    add_ema_reach_entries,
    add_ichimoku_entries,
    add_ma_cross_entries,
    add_macd_break_entries,
)
from core_engine.src.rules.shift import UNLIMITED_SHIFT
from core_engine.src.rules.trend_tf import (
    entry_based_trend_tf,
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

LOGGER = logging.getLogger(__name__)

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


def _strategy_trend_defaults() -> dict[str, Any]:
    """Mặc định điều khiển trend của một chiến lược: cờ lọc luôn tắt (bản không lọc trend là bản thô) và CHƯA có khối nào -- khối trend của từng ô do ``build_cells`` chọn (``redis.trend_outputs`` trừ khối chiến lược loại)."""
    return {"TREND_FILTER_ENABLED": False, "TREND_METHODS": ()}


def _resolve_trend_methods(raw: dict[str, Any], defaults: dict[str, Any]) -> tuple[str, ...]:
    """
    Khối xác nhận xu hướng được bật: override (list hoặc chuỗi cách nhau bằng dấu phẩy) nếu có, trống/thiếu = mặc định (chưa có khối nào: bản không lọc trend).
    """
    raw_methods = raw.get("TREND_METHODS")
    if raw_methods is None or raw_methods == "" or (isinstance(raw_methods, (list, tuple)) and not raw_methods):
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

    Lý do: nến trend dùng cho một nến vào lệnh luôn là nến ĐÃ ĐÓNG không muộn hơn giờ đóng của nó (indicator.merge_closed_reference), nên trend "có trước" hoặc cùng lúc
    với tín hiệu và không có look-ahead. Khung trend NHỎ hơn khung tín hiệu thì nến trend nằm trọn trong lòng nến tín hiệu (trend trùng thông tin với tín hiệu, bộ lọc gần như
    vô hiệu) -- bị cấm. Khung bằng nhau vẫn được: nến trend đóng cùng lúc nến tín hiệu.

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


def _excluded_trend_methods(strategy_cfg: dict[str, Any], path: str) -> tuple[str, ...]:
    """
    Đọc strategies.<tên>.excluded_trend_methods (TUỲ CHỌN, mặc định rỗng): các khối trend KHÔNG được áp cho chiến lược này ở BẤT KỲ khung nào
    (chốt 2026-10-28, đồng bộ we_core/des cho macd_break + breakout: breakout tính hướng bằng chính điều kiện phá đỉnh/đáy close 100 nến + MACD
    mà macd_break dùng làm setup, nên lọc macd_break bằng breakout là lọc vòng tròn). Mỗi tên phải là khối đã đăng ký (rules/trend_tf.METHODS) và không
    trùng. Kế hoạch của một symbol chọn khối nằm trong danh sách này là lỗi (``StrategySpec.normalize_params``). Sai thì raise ngay lúc nạp config.
    """
    raw = strategy_cfg.get("excluded_trend_methods")
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise TypeError(f"de_config.yaml: '{path}.excluded_trend_methods' must be a list of trend method names.")
    if not raw:
        return ()
    return parse_methods(raw, label=f"{path}.excluded_trend_methods")


def _raw_params(defaults: dict[str, Any], overrides: dict[str, Any] | None, entry_tf: object) -> dict[str, Any]:
    """
    defaults < overrides. Khung trend KHÔNG có giá trị mặc định: người gọi truyền TREND_TF tường minh (bảng ``trend_tf_by_symbol``).

    Ngoại lệ: khi mọi khối đang bật tính trên CHÍNH khung của tín hiệu (chính sách same_timeframe ở rules/trend_tf.POLICY, vd breakout) thì khung trend LÀ
    khung vào lệnh, hoặc mức tối thiểu min_trend_tf nếu khung vào lệnh nhỏ hơn (cloud: M10 -> H1; entry_based_trend_tf);
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
        excluded_trend_methods: Khối trend KHÔNG được áp cho chiến lược này ở bất kỳ khung nào (kế hoạch chọn khối đó bị từ chối lúc nạp config).
        session_hours_utc:      Chỉ combo: symbol -> giờ UTC được giao dịch (rỗng/không khai = mọi giờ); None nếu chiến lược không có lọc session.

    Invariant:
        Pipeline phải gọi theo thứ tự:
        normalize_params → add_indicators → detect_signals → add_entries.
        Mỗi bước nhận output của bước trước làm input. Hai tiến trình chia đôi pipeline ở ranh giới sẵn có sau detect_signals: runner chạy add_indicators → detect_signals trên khung của chính
        nó (strategy_runner.trigger_table, trend tắt) và ghi tín hiệu thô (TRIGGER, chỉ nến có tín hiệu) + trạng thái từng nến (SETUP) vào db0; publisher ghép hai thứ đó lại, lọc trend/dời rồi chạy add_entries
        (configuration.process_cell).

    Thêm chiến lược mới — checklist:
        1. Viết add_<tên>_indicators trong indicator.py (chỉ tính chỉ báo, không có default ngầm).
        2. Viết detect_<tên>_signals trong strategies/<tên>.py (CHỈ quyết định khi nào/hướng nào) và add_<tên>_entries trong rules/entries.py.
        3. Thêm mục `strategies.<tên>` vào de_config.yaml (giá trị thật; tên khoá = tên tham số viết thường).
        4. Thêm MỘT lời gọi _build_spec vào STRATEGIES bên dưới: khai bounds, ordered_pairs, order_type "STOP" hoặc "MARKET".
        5. Thêm `live.signal_validity.<tên>` vào de_config.yaml (và bảng `trend_tf_by_symbol` của chiến lược nếu có trend); runner tự tính TRIGGER của chiến lược mới ở mọi (symbol, khung).
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

        Thứ tự ưu tiên: default_params < overrides. ``symbol``/``tf`` (khung vào lệnh) dùng để suy ra khung trend của khối same_timeframe và giờ session; TREND_TF của khối còn lại do kế hoạch truyền vào overrides.
        """
        defaults = self.default_params
        if self.uses_trend:
            raw = _raw_params(defaults, overrides, tf)
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
    ``trend`` False (ichimoku): không có phần trend nào."""
    path = f"strategies.{key}"
    cfg = require(_STRATEGIES_CFG, key, path)
    timeframes = tuple(str(tf).upper() for tf in require(cfg, timeframes_key, f"{path}.{timeframes_key}"))
    defaults: dict[str, Any] = {name: int(require(cfg, name.lower(), f"{path}.{name.lower()}")) for name in bounds}
    if session_hours:
        defaults["SESSION_HOURS_UTC"] = []
    if trend:
        defaults.update(_strategy_trend_defaults())
    defaults.update(_strategy_alternating_default(cfg, path))
    trend_parts: dict[str, Any] = {}
    if trend:
        trend_parts = {"trend_timeframes": _trend_timeframes(cfg, path), "excluded_trend_methods": _excluded_trend_methods(cfg, path)}
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


def alternating_filter_for(key: str) -> bool | None:
    """Trả về cờ ``alternating_filter_enabled`` của 1 strategy, hoặc ``None``
    nếu strategy đó không có cờ này (hiện cả combo, ma_cross, macd_break, ichimoku và ema_reach đều có).

    configuration.run_publisher ghi giá trị này vào log khởi động: bật/tắt làm đổi số tín hiệu
    nhưng không đổi tên key nào ở db đầu ra, nên phải tra được từ log.
    """
    params = get_strategy(key).default_params
    if "ALTERNATING_FILTER_ENABLED" not in params:
        return None
    return bool(params["ALTERNATING_FILTER_ENABLED"])


_REDIS_DEFAULTS: dict[str, Any] = {
    "port": 6379,
    "socket_timeout_seconds": 5,
    "socket_connect_timeout_seconds": 5,
    "healthcheck_interval_seconds": 30,
    "reconnect_delay_seconds": 3,
}
_REDIS_INPUT_DEFAULTS: dict[str, Any] = {"source_max_bars": 1200}
_MAX_SIGNAL_TTL_SECONDS = 604800  # 7 ngày: hạn lưu TỐI ĐA của một tín hiệu ở db2 (chủ hệ chốt 09/11/2026); khai quá thì từ chối lúc nạp config
_REDIS_OUTPUT_DEFAULTS: dict[str, Any] = {"retention_seconds": _MAX_SIGNAL_TTL_SECONDS, "retention_max_entries": 500}
_IDLE_WAIT_SECONDS = 1.0
TREND_LANE = "trend"  # tên làn runner chỉ tính TREND + CONTEXT (các làn runner khác mang khoá một chiến lược); xem build_runner_job


@dataclass(frozen=True)
class Cell:
    """MỘT ô của DE: (symbol, chiến lược, khung vào lệnh = khung trigger). Mỗi ô cho ra một bản thô và (chiến lược có trend) một bản lọc cho từng khối trend. Lưới đủ 15 symbol x 5 chiến lược x 8 khung."""

    symbol: str
    strategy: str
    entry_tf: str


@dataclass(frozen=True)
class TrendLeg:
    """Một bản lọc trend của một ô: khối ``method`` tính trên nến khung ``trend_tf`` (tra bảng ``trend_tf_by_symbol`` hoặc suy ra từ khung vào lệnh với khối same_timeframe)."""

    method: str
    trend_tf: str


@dataclass(frozen=True)
class CellPlan:
    """Cấu hình của một ô: khung context (khai ở bảng ``context_tf_by_symbol`` của chiến lược, hiện bằng khung trigger) và các bản lọc trend. Luật khung: trigger <= context <= trend."""

    cell: Cell
    context_tf: str
    legs: tuple[TrendLeg, ...]


@dataclass(frozen=True)
class RunnerJob:
    """Việc của MỘT tiến trình runner, suy ra từ de_config.yaml: nghe MỌI (symbol, khung) của một nguồn nến, với mỗi cặp tính TRIGGER và SETUP của các chiến lược trong ``strategies``, CONTEXT (khi ``with_context``) và TREND
    của các khối trong ``methods``, ghi vào db0 (SETUP) và db1 (TRIGGER, CONTEXT, TREND). Một tiến trình chạy trọn mọi vai trò (``strategies`` đủ 5, ``methods`` đủ, ``with_context``) hoặc chỉ MỘT làn: làn chiến lược
    (``strategies`` một phần tử, ``methods`` rỗng, không CONTEXT) hoặc làn trend (``strategies`` rỗng, mọi khối, có CONTEXT); xem ``build_runner_job``. Không phụ thuộc cấu hình ô nào.
    ``warmup_bars``: số nến ĐẦU của cửa sổ tính chỉ để chỉ báo kịp ấm; ``write_bars``: số hàng mới nhất được ghi (xem ``process_frame``).
    ``window_bars``: số nến cuối mà MỖI bảng được tính trên đó (= ``write_bars + warmup_bars``, hiện 500 + 300 = 800); ``trend_window_bars``: khối trend nào đòi nhiều hơn thì có số riêng (zigzag 3000 và breakout 1200, bị chặn ở số nến nguồn giữ, 1200).
    ``trend_needed``: các bộ (symbol, khối, khung) mà có ô đọc bảng TREND đó; runner chỉ tính và ghi các bảng này (knn, zigzag, tstat, cloud chỉ ở khung H; breakout ở mọi khung). None = tính mọi khối ở mọi khung.
    0 hoặc không khai ở ``window_bars`` / ``trend_window_bars`` = tính trên cả bảng nến đã đọc."""

    pairs: tuple[Pair, ...]
    strategies: tuple[str, ...]
    methods: tuple[str, ...]
    context: dict[str, int]
    source: dict[str, Any]
    sink: dict[str, Any]
    inputs_sink: dict[str, Any]
    process_on_startup: bool
    reconnect_delay_seconds: float
    log_level: str
    input_name: str = ""
    warmup_bars: int = 0
    write_bars: int = 0
    window_bars: int = 0
    trend_window_bars: dict[str, int] = field(default_factory=dict)
    trend_needed: frozenset[tuple[str, str, str]] | None = None
    with_context: bool = True


@dataclass(frozen=True)
class LegJob:
    """Một bản lọc trend của một ô ở publisher: khối, cặp TREND phải đọc, độ dài khung trend (để kiểm "đã về chưa"), và khối có DỜI tín hiệu không (chính sách ở rules/trend_tf.POLICY)."""

    method: str
    pair: Pair
    length: pd.Timedelta
    shifts: bool


@dataclass(frozen=True)
class CellJob:
    """Việc của publisher cho MỘT ô: cặp TRIGGER (sự kiện), SETUP (trạng thái từng nến) và CONTEXT phải đọc, các bản lọc trend, loại lệnh, hạn hiệu lực và tham số chuẩn hoá của chiến lược (cho entries)."""

    plan: CellPlan
    order_type: str
    validity: dict[str, Any]
    trigger: Pair
    setup: Pair
    context: Pair
    context_length: pd.Timedelta
    legs: tuple[LegJob, ...]
    params: dict[str, Any]


@dataclass(frozen=True)
class PublisherJob:
    """Việc của MỘT tiến trình publisher: mỗi ô một CellJob, các cặp cấu phần phải nghe (``routes`` đưa từng cặp về các ô dùng nó), nơi đọc db0/db1 và nơi ghi tín hiệu db2 (thô và từng khối trend)."""

    cells: dict[Cell, CellJob]
    pairs: tuple[Pair, ...]
    routes: dict[Pair, tuple[Cell, ...]]
    source: dict[str, Any]
    sink: dict[str, Any]
    filtered_sinks: dict[str, dict[str, Any]]
    process_on_startup: bool
    reconnect_delay_seconds: float
    log_level: str
    max_shift_bars: int  # live.max_shift_bars: số nến dời tối đa kể từ nến mở đợt (0 = không dời, 999 = không giới hạn)


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
    """Đọc và kiểm phần `redis:` của de_config.yaml (dùng chung cho runner và publisher). Sai thì KeyError/TypeError/ValueError nêu đúng đường dẫn.

    ``input_name`` chọn nguồn nến của nhóm runner: rỗng = ``redis.input`` (Core AEN, cùng Redis với đầu ra); tên khác = ``redis.input_sources.<tên>`` (Redis riêng và nhóm symbol riêng).
    Đầu ra luôn ở Redis của DE (cổng ``redis.output.port``) bất kể nguồn nến nằm đâu: SETUP ở db ``redis.setup.db`` (db0), TRIGGER/CONTEXT/TREND ở db ``redis.signal_inputs.db`` (db1), tín hiệu ở db ``redis.output.db`` (db1), hai db khác nhau; tín hiệu
    không lọc trend và tín hiệu đã lọc của từng khối cùng db1 với tiền tố khoá và kênh riêng (``redis.output`` / ``redis.trend_outputs``), khoá có tên symbol nên các nhóm không đụng nhau."""
    from core_engine.src.signal_publisher import timeframe_duration

    redis_cfg = require(config, "redis", "redis")
    for key in ("host", "password"):
        require(redis_cfg, key, f"redis.{key}")
    input_cfg = redis_cfg.get("input") or {}
    for key in ("db", "key_prefix", "snapshot_bars", "warmup_bars", "process_on_startup", "symbols", "timeframes"):
        require(input_cfg, key, f"redis.input.{key}")
    output_cfg = redis_cfg.get("output") or {}
    for key in ("db", "key_prefix", "channel_prefix", "port", "password"):
        require(output_cfg, key, f"redis.output.{key}")
    setup_cfg = redis_cfg.get("setup") or {}
    for key in ("db", "key_prefix", "window_bars", "retention_max_entries"):
        require(setup_cfg, key, f"redis.setup.{key}")
    inputs_cfg = redis_cfg.get("signal_inputs") or {}
    for key in ("db", "key_prefix"):
        require(inputs_cfg, key, f"redis.signal_inputs.{key}")
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
    warmup_bars = int(inp["warmup_bars"])
    if warmup_bars < 0:
        raise ValueError("redis.input.warmup_bars must not be negative")
    if snapshot_bars + warmup_bars > source_max_bars:
        raise ValueError(
            f"redis.input.snapshot_bars + warmup_bars ({snapshot_bars + warmup_bars}) exceeds redis.input.source_max_bars ({source_max_bars}): the candle source never holds that many candles"
        )
    if not str(out["key_prefix"]).strip():
        raise ValueError("redis.output.key_prefix must be configured")
    if not str(out["channel_prefix"]).strip():
        raise ValueError("redis.output.channel_prefix must be configured")
    if int(out["retention_seconds"]) <= 0:
        raise ValueError("redis.output.retention_seconds must be positive")
    if int(out["retention_seconds"]) > _MAX_SIGNAL_TTL_SECONDS:
        raise ValueError(f"redis.output.retention_seconds ({int(out['retention_seconds'])}) exceeds the 7-day maximum a signal may live in db2 ({_MAX_SIGNAL_TTL_SECONDS} seconds)")
    if int(out["retention_max_entries"]) <= 0:
        raise ValueError("redis.output.retention_max_entries must be positive")
    window_bars = int(setup_cfg["window_bars"])
    components_max = int(setup_cfg["retention_max_entries"])
    if window_bars <= 0:
        raise ValueError("redis.setup.window_bars must be positive")
    if components_max < window_bars:
        raise ValueError("redis.setup.retention_max_entries must be at least redis.setup.window_bars")
    databases = {"redis.setup.db": int(setup_cfg["db"]), "redis.signal_inputs.db": int(inputs_cfg["db"]), "redis.output.db": int(out["db"])}
    if len(set(databases.values())) != len(databases):
        raise ValueError(f"{', '.join(databases)} must be three different databases (same Redis, each output needs its own database): {databases}")

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
    feed_db = (feed["host"], feed["port"], int(inp["db"]))
    for label, output_db in (("signal output", int(out["db"])), ("setup", int(setup_cfg["db"])), ("signal_inputs", int(inputs_cfg["db"]))):
        if feed_db == (str(top["host"]), int(out["port"]), output_db):
            raise ValueError(f"Redis input and {label} must use different databases")
    timeouts = {
        "socket_timeout": float(top["socket_timeout_seconds"]),
        "connect_timeout": float(top["socket_connect_timeout_seconds"]),
        "healthcheck_interval": int(top["healthcheck_interval_seconds"]),
    }
    own = {"host": str(top["host"]), "port": int(out["port"]), "password": str(out["password"] or "")}
    return {
        "symbols": symbols,
        "timeframes": timeframes,
        "snapshot_bars": snapshot_bars,
        "warmup_bars": warmup_bars,
        "source_max_bars": source_max_bars,
        "process_on_startup": bool(inp["process_on_startup"]),
        "reconnect_delay": float(top["reconnect_delay_seconds"]),
        "trend_outputs": trend_outputs,
        "source": {**feed, "db": int(inp["db"]), "key_prefix": str(inp["key_prefix"]), **timeouts},
        "setup_sink": {
            **own, "db": int(setup_cfg["db"]), "key_prefix": str(setup_cfg["key_prefix"] or ""), "retention_max_entries": components_max, **timeouts,
        },
        "inputs_sink": {
            **own, "db": int(inputs_cfg["db"]), "key_prefix": str(inputs_cfg["key_prefix"] or ""), "retention_max_entries": components_max, **timeouts,
        },
        "reader_source": {
            **own, "db": int(setup_cfg["db"]), "key_prefix": str(setup_cfg["key_prefix"] or ""), "snapshot_bars": window_bars, "components": True,
            "role_db": {role: int(inputs_cfg["db"]) for role in ("TRIGGER", "CONTEXT", "TREND")}, **timeouts,
        },
        "sink": {
            **own, "db": int(out["db"]), "key_prefix": str(out["key_prefix"]), "channel_prefix": str(out["channel_prefix"]),
            "retention_seconds": int(out["retention_seconds"]), "retention_max_entries": int(out["retention_max_entries"]), **timeouts,
        },
    }


_CONTEXT_KEYS = ("macd_fast", "macd_slow", "macd_signal", "ma_period")


def context_params(config: dict[str, Any]) -> dict[str, int]:
    """Tham số CHUẨN của CONTEXT (``context:`` trong de_config.yaml): MACD (nhanh, chậm, signal) và chu kỳ MA, dùng chung cho mọi symbol, khung và chiến lược. Thiếu khoá thì KeyError nêu đường dẫn."""
    block = require(config, "context", "context")
    values = {key: int(require(block, key, f"context.{key}")) for key in _CONTEXT_KEYS}
    if any(value <= 0 for value in values.values()):
        raise ValueError("de_config.yaml: context.* must be positive integers")
    if values["macd_fast"] >= values["macd_slow"]:
        raise ValueError("de_config.yaml: context.macd_fast must be smaller than context.macd_slow")
    return values


def _tf_table(strategy_cfg: dict[str, Any], key: str, path: str, entry_timeframes: tuple[str, ...], *, complete: bool) -> dict[str, dict[str, str]]:
    """Đọc bảng ``symbol -> khung vào lệnh -> khung`` (``trend_tf_by_symbol`` hoặc ``context_tf_by_symbol``) của một chiến lược. ``complete``: bảng phải đủ MỌI symbol DE vận hành x MỌI khung vào lệnh
    của chiến lược (cả hai bảng đều bắt buộc đủ, không có giá trị mặc định ngầm). Sai thì raise nêu đúng đường dẫn."""
    raw = strategy_cfg.get(key)
    if raw is None:
        if complete:
            raise KeyError(f"de_config.yaml thiếu '{path}.{key}'")
        return {}
    if not isinstance(raw, dict):
        raise TypeError(f"de_config.yaml: '{path}.{key}' must map symbol -> {{entry timeframe: timeframe}}")
    table: dict[str, dict[str, str]] = {}
    for raw_symbol, row in raw.items():
        symbol = str(raw_symbol).strip().upper()
        if symbol not in OPERATING_SYMBOLS:
            raise ValueError(f"de_config.yaml: '{path}.{key}.{raw_symbol}': {symbol} is not an operating symbol (valid: {', '.join(OPERATING_SYMBOLS)})")
        if not isinstance(row, dict):
            raise TypeError(f"de_config.yaml: '{path}.{key}.{raw_symbol}' must map entry timeframe -> timeframe")
        table[symbol] = {str(tf).strip().upper(): str(value).strip().upper() for tf, value in row.items()}
        unknown = sorted(set(table[symbol]) - set(entry_timeframes))
        if unknown:
            raise ValueError(f"de_config.yaml: '{path}.{key}.{raw_symbol}' has entry timeframes the strategy does not run: {', '.join(unknown)}")
    if complete:
        for symbol in OPERATING_SYMBOLS:
            missing = [tf for tf in entry_timeframes if tf not in table.get(symbol, {})]
            if missing:
                raise KeyError(f"de_config.yaml thiếu '{path}.{key}.{symbol}' cho khung {', '.join(missing)}")
    return table


def build_cells(config: dict[str, Any]) -> dict[Cell, CellPlan]:
    """Dựng và kiểm MỌI ô (symbol DE vận hành x chiến lược x khung vào lệnh của chiến lược) theo bảng của we trong ``strategies.<tên>.trend_tf_by_symbol``.

    Bản lọc trend của một ô: mỗi khối khai ở ``redis.trend_outputs`` mà chiến lược không loại (``excluded_trend_methods``); khối same_timeframe (breakout, cloud) tự suy khung trend từ khung vào lệnh,
    khối còn lại (knn, zigzag, tstat) lấy khung trong bảng. Khung context lấy từ bảng ``context_tf_by_symbol`` (bắt buộc đủ, hiện bằng khung trigger). Luật khung: trigger <= context <= trend; mọi khung phải
    nằm trong ``redis.input.timeframes``; khung trend của khối cần khung lớn phải thuộc ``trend_timeframes`` của chiến lược. Chiến lược không có trend (ichimoku) chỉ có bản thô."""
    redis_cfg = require(config, "redis", "redis")
    timeframes = _lane_names(require(require(redis_cfg, "input", "redis.input"), "timeframes", "redis.input.timeframes"), "redis.input.timeframes")
    trend_names = tuple(str(name).strip().lower() for name in (redis_cfg.get("trend_outputs") or {}))
    strategies_cfg = config.get("strategies") or _STRATEGIES_CFG
    cells: dict[Cell, CellPlan] = {}
    for spec in STRATEGIES.values():
        path = f"strategies.{spec.key}"
        cfg = require(strategies_cfg, spec.key, path)
        outside = [tf for tf in spec.live_timeframes if tf not in timeframes]
        if outside:
            raise ValueError(f"de_config.yaml: '{path}' runs timeframes that are not in redis.input.timeframes: {', '.join(outside)}")
        trend_table = _tf_table(cfg, "trend_tf_by_symbol", path, spec.live_timeframes, complete=True) if spec.uses_trend else {}
        context_table = _tf_table(cfg, "context_tf_by_symbol", path, spec.live_timeframes, complete=True)
        for symbol in OPERATING_SYMBOLS:
            for entry_tf in spec.live_timeframes:
                context_tf = context_table.get(symbol, {}).get(entry_tf, entry_tf)
                where = f"{path} {symbol} {entry_tf}"
                if context_tf not in timeframes:
                    raise ValueError(f"de_config.yaml: {where}: context timeframe {context_tf} is not in redis.input.timeframes")
                if timeframe_minutes(context_tf) < timeframe_minutes(entry_tf):
                    raise ValueError(f"de_config.yaml: {where}: context timeframe {context_tf} must not be smaller than the trigger timeframe {entry_tf}")
                legs: list[TrendLeg] = []
                if spec.uses_trend:
                    table_tf = trend_table[symbol][entry_tf]
                    if table_tf not in spec.trend_timeframes:
                        raise ValueError(f"de_config.yaml: {where}: trend timeframe {table_tf} is not in {path}.trend_timeframes ({', '.join(spec.trend_timeframes)})")
                    for name in trend_names:
                        if name in spec.excluded_trend_methods:
                            continue
                        trend_tf = entry_based_trend_tf((name,), entry_tf) if uses_entry_timeframe((name,)) else table_tf
                        try:
                            check_trend_timeframe(entry_tf, trend_tf)
                        except ValueError as exc:
                            raise ValueError(f"de_config.yaml: {where}: {exc}") from exc
                        if trend_tf not in timeframes:
                            raise ValueError(f"de_config.yaml: {where}: trend timeframe {trend_tf} is not in redis.input.timeframes")
                        if timeframe_minutes(trend_tf) < timeframe_minutes(context_tf):
                            raise ValueError(f"de_config.yaml: {where}: trend timeframe {trend_tf} must not be smaller than the context timeframe {context_tf}")
                        legs.append(TrendLeg(name, trend_tf))
                cell = Cell(symbol, spec.key, entry_tf)
                cells[cell] = CellPlan(cell, context_tf, tuple(legs))
    return cells


def _wanted_symbols(symbols: Sequence[str] | None) -> set[str] | None:
    """Tập symbol người vận hành giới hạn tiến trình (``--symbols``), chữ hoa; rỗng/None = không giới hạn."""
    wanted = {str(symbol).strip().upper() for symbol in (symbols or ()) if str(symbol).strip()}
    return wanted or None


def build_runner_job(config: dict[str, Any], input_name: str = "", symbols: Sequence[str] | None = None, lane: str = "") -> RunnerJob:
    """Dựng và kiểm MỌI cấu hình của một tiến trình runner TRƯỚC khi chạm Redis; sai thì ValueError/KeyError/TypeError (người gọi ghi config_error), không có gì được ghi.

    ``input_name`` chọn nguồn nến của nhóm tiến trình (xem ``_parse_redis``); ``symbols`` (tuỳ chọn) giới hạn thêm để chia việc. Runner tính ĐỦ LƯỚI: mọi symbol của nguồn x mọi khung ``redis.input.timeframes``,
    mỗi cặp ra TRIGGER của mọi chiến lược và CONTEXT; TREND chỉ của những (symbol, khối, khung) mà có ô đọc (``trend_needed``, suy từ ``build_cells``: knn, zigzag, tstat, cloud chỉ ở khung H, breakout ở mọi khung). Mỗi bảng được TÍNH trên
    ``snapshot_bars + warmup_bars`` nến cuối (hiện 500 + 300 = 800); riêng khối trend đòi nhiều hơn thì có cửa sổ riêng (zigzag 3000 và breakout 1200, bị chặn ở số nến nguồn giữ, 1200), và số nến ĐỌC mỗi cặp là cửa sổ lớn nhất; chỉ ``snapshot_bars`` hàng mới nhất được ghi. Thứ tự quét lúc khởi động: khung LỚN trước, nhỏ sau.

    ``lane`` chia vai trò cho nhiều tiến trình cùng nguồn: rỗng = trọn mọi vai trò (một tiến trình làm hết); khoá một chiến lược = làn chiến lược (chỉ TRIGGER và SETUP của chiến lược đó, không CONTEXT, không TREND, nên chỉ đọc
    ``window_bars`` nến); ``TREND_LANE`` = làn trend (mọi khối TREND và CONTEXT, không chiến lược). Các làn độc lập nhau, chỉ trao đổi qua Redis: publisher chờ cấu phần nào chưa về rồi tính lại khi nó về."""
    if lane and lane != TREND_LANE and lane not in STRATEGIES:
        raise ValueError(f"lane '{lane}' is neither '{TREND_LANE}' nor a registered strategy ({', '.join(STRATEGIES)})")
    base = _parse_redis(config, input_name)
    live = require(config, "live", "live")
    wanted = _wanted_symbols(symbols)
    if wanted is not None and not wanted <= set(base["symbols"]):
        raise ValueError(f"symbols {', '.join(sorted(wanted - set(base['symbols'])))} are not served by this candle source")
    mine = [symbol for symbol in base["symbols"] if wanted is None or symbol in wanted]
    methods = tuple(base["trend_outputs"]) if lane in ("", TREND_LANE) else ()
    window = base["snapshot_bars"] + base["warmup_bars"]
    trend_windows = {method: max(window, min(max_trend_warmup_bars((method,)), base["source_max_bars"])) for method in methods}
    bars = max((window, *trend_windows.values()))  # số nến ĐỌC mỗi cặp (đủ cho khối đòi nhiều nhất); mỗi bảng chỉ TÍNH trên window / trend_windows[khối] nến cuối
    pairs = tuple(sorted((Pair(symbol, timeframe) for symbol in mine for timeframe in base["timeframes"]), key=lambda pair: (-timeframe_minutes(pair.timeframe), pair.symbol)))
    return RunnerJob(
        pairs=pairs,
        strategies=tuple(key for key in STRATEGIES if lane in ("", key)),
        methods=methods,
        context=context_params(config),
        source={**base["source"], "snapshot_bars": {pair: bars for pair in pairs}},
        sink=base["setup_sink"],
        inputs_sink=base["inputs_sink"],
        process_on_startup=base["process_on_startup"],
        reconnect_delay_seconds=base["reconnect_delay"],
        log_level=str(require(live, "log_level", "live.log_level")),
        input_name=input_name,
        warmup_bars=base["warmup_bars"],
        write_bars=base["snapshot_bars"],
        window_bars=window,
        trend_window_bars=trend_windows,
        trend_needed=frozenset((cell.symbol, leg.method, leg.trend_tf) for cell, plan in build_cells(config).items() for leg in plan.legs),
        with_context=lane in ("", TREND_LANE),
    )


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


def _max_shift_bars(live: dict[str, Any]) -> int:
    """Đọc ``live.max_shift_bars`` (bắt buộc): số nến dời tối đa kể từ nến mở đợt, áp chung cho mọi chiến lược và khối dời. 0 = không dời; n (1..998) = dời tối đa n nến; 999 = không giới hạn.
    Chỉ nhìn được trong cửa sổ 500 nến mà DE giữ (``redis.setup.window_bars``) nên giá trị từ 500 trở lên có tác dụng như 999."""
    value = require(live, "max_shift_bars", "live.max_shift_bars")
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= UNLIMITED_SHIFT:
        raise ValueError(f"de_config.yaml: 'live.max_shift_bars' must be an integer between 0 and {UNLIMITED_SHIFT} (0 = no shift, {UNLIMITED_SHIFT} = unlimited)")
    return value


def build_publisher_job(config: dict[str, Any], symbols: Sequence[str] | None = None, strategy: str = "") -> PublisherJob:
    """Dựng và kiểm MỌI cấu hình của một tiến trình publisher TRƯỚC khi chạm Redis; sai thì ValueError/KeyError/TypeError (người gọi ghi config_error), không có gì được ghi.
    Publisher đọc db0/db1 của DE nên không phụ thuộc nguồn nến: ``symbols`` và ``strategy`` (tuỳ chọn; rỗng = không giới hạn) chỉ để chia việc cho nhiều tiến trình, ``strategy`` giữ lại các ô của đúng một chiến lược.
    Mỗi chiến lược cần có hạn hiệu lực trong ``live.signal_validity``.
    Mỗi ô đọc ba thứ: TRIGGER của chiến lược ở khung trigger, CONTEXT ở khung context (mặc định chính khung trigger) và TREND của từng khối ở khung trend."""
    from core_engine.src.signal_publisher import (
        check_order_type,
        signal_window,
        strategy_token,
        timeframe_duration,
    )

    live = require(config, "live", "live")
    base = _parse_redis(config)
    decimals = _price_decimals(live)
    validity_cfg = require(live, "signal_validity", "live.signal_validity")
    plans = build_cells(config)
    wanted = _wanted_symbols(symbols)
    if wanted is not None and not wanted <= set(OPERATING_SYMBOLS):
        raise ValueError(f"no cells for symbols {', '.join(sorted(wanted - set(OPERATING_SYMBOLS)))}")
    if strategy and strategy not in STRATEGIES:
        raise ValueError(f"strategy '{strategy}' is not a registered strategy ({', '.join(STRATEGIES)})")
    jobs: dict[Cell, CellJob] = {}
    routes: dict[Pair, list[Cell]] = {}
    used: set[str] = set()
    for cell, plan in plans.items():
        if (wanted is not None and cell.symbol not in wanted) or (strategy and cell.strategy != strategy):
            continue
        spec = get_strategy(cell.strategy)
        if spec.key not in validity_cfg:
            raise KeyError(f"de_config.yaml thiếu 'live.signal_validity.{spec.key}'")
        validity = dict(validity_cfg[spec.key])
        signal_window(spec.key, cell.entry_tf, pd.Timestamp("2026-01-01", tz="UTC"), validity)
        trigger = Pair(cell.symbol, cell.entry_tf, "TRIGGER", strategy_token(spec.key))
        setup = Pair(cell.symbol, cell.entry_tf, "SETUP", strategy_token(spec.key))
        context = Pair(cell.symbol, plan.context_tf, "CONTEXT")
        legs = tuple(
            LegJob(leg.method, Pair(cell.symbol, leg.trend_tf, "TREND", leg.method.upper()), timeframe_duration(leg.trend_tf), uses_first_aligned_per_run((leg.method,)))
            for leg in plan.legs
        )
        jobs[cell] = CellJob(
            plan=plan,
            order_type=check_order_type(spec.order_type),
            validity=validity,
            trigger=trigger,
            setup=setup,
            context=context,
            context_length=timeframe_duration(plan.context_tf),
            legs=legs,
            params=spec.normalize_params(None, cell.symbol, cell.entry_tf),
        )
        for pair in (trigger, setup, context, *(leg.pair for leg in legs)):
            routes.setdefault(pair, []).append(cell)
        used.update(leg.method for leg in legs)
    if not jobs:
        raise ValueError("no cell to run for this publisher")
    return PublisherJob(
        cells=jobs,
        pairs=tuple(sorted(routes)),
        routes={pair: tuple(cells) for pair, cells in routes.items()},
        source=base["reader_source"],
        sink={**base["sink"], "price_decimals": decimals},
        filtered_sinks={
            name: {**base["sink"], "key_prefix": prefixes[0], "channel_prefix": prefixes[1], "source": name, "price_decimals": decimals}
            for name, prefixes in base["trend_outputs"].items()
            if name in used
        },
        process_on_startup=base["process_on_startup"],
        reconnect_delay_seconds=base["reconnect_delay"],
        log_level=str(require(live, "log_level", "live.log_level")),
        max_shift_bars=_max_shift_bars(live),
    )


def _last_bars(candles: pd.DataFrame, count: int) -> pd.DataFrame:
    """``count`` nến cuối của bảng nến (``count`` 0, hoặc bảng không dài hơn ``count``, thì giữ cả bảng)."""
    if not count or len(candles) <= count:
        return candles
    return candles.iloc[len(candles) - count:].reset_index(drop=True)


def _from_bar(table: pd.DataFrame, first: pd.Timestamp) -> pd.DataFrame:
    """Phần của bảng cấu phần từ nến ``first`` trở đi (bỏ các hàng ấm ở đầu, xem ``process_frame``)."""
    return table[table["bartime"] >= first]


def _isolated(label: str, pair: Pair, compute: Callable[[], pd.DataFrame]) -> pd.DataFrame | None:
    """Tính MỘT bảng cấu phần, cô lập lỗi riêng của nó (ghi log, trả None) để một khối hỏng không chặn các bảng còn lại của cặp; lỗi Redis vẫn nổi lên cho vòng nối lại."""
    try:
        return compute()
    except redis.RedisError:
        raise
    except Exception:
        LOGGER.exception("event=table_failed table=%s symbol=%s timeframe=%s", label, pair.symbol, pair.timeframe)
        return None


def process_frame(job: RunnerJob, engine: Any, writer: Any, inputs_writer: Any, pair: Pair) -> Any:
    """Luồng của MỘT cặp (symbol, khung) ở runner: bảng nến mới nhất (data_engine) -> tính cấu phần (strategy_runner) -> ghi db0 (``writer``: SETUP) và db1 (``inputs_writer``: TRIGGER, CONTEXT, TREND).

    Với MỖI cặp tính phần việc của làn (``build_runner_job``; tiến trình trọn mọi vai trò thì đủ): CONTEXT, TREND của mọi khối, rồi với từng chiến lược TRIGGER (CHỈ nến có tín hiệu thô, sự kiện trôi ra ngoài cửa sổ thì bỏ; cả ba ở db1)
    và SETUP (trạng thái từng nến, db0). CONTEXT ghi trước TREND vì rẻ và mọi ô đều chờ nó (TREND chỉ ô có bản lọc mới chờ). SETUP ghi CUỐI CÙNG: dấu chốt của làn chiến lược, publisher thấy nến mới ở SETUP thì TRIGGER của nến đó (nếu có)
    đã có. CONTEXT và TREND chỉ chắc chắn đã có khi một tiến trình làm trọn mọi vai trò; khi chúng do làn trend ghi thì có thể về sau SETUP, và publisher chờ rồi tính lại khi chúng về (``process_cell``).
    Cửa sổ: ``job.warmup_bars`` nến ĐẦU của cửa sổ tính chỉ để chỉ báo (và trí nhớ của lọc xen kẽ) kịp ấm; mỗi bảng được tính lại từ đầu trên ``job.window_bars`` nến cuối của bảng nến đã đọc (zigzag: ``job.trend_window_bars``,
    dài hơn) chứ không nhớ trạng thái giữa hai lượt, nhưng chỉ ghi ``job.write_bars`` nến mới nhất (và không bao giờ ghi hàng thuộc phần ấm),
    nên hàng nào trong db0/db1 cũng đã ấm. Vì thế lúc khởi động lại, ``write_bars`` (hiện 500) nến gần nhất được xét lại để xem nến nào có tín hiệu, còn lúc chạy thường mỗi nến mới đóng thêm đúng một dòng vào mỗi danh sách theo nến
    (và một dòng TRIGGER nếu nến đó có tín hiệu). Cặp có không quá ``warmup_bars`` nến thì chưa đủ nền: bỏ lượt này và cảnh báo ``warmup_incomplete``. Trả về ``Outcome("written", số mốc ghi mới)``,
    hoặc None nếu nguồn chưa có nến của cặp (coi như không có, không cảnh báo) hoặc cặp chưa đủ nến warm-up."""
    from core_engine.src import strategy_runner
    from core_engine.src.signal_publisher import Outcome, strategy_token

    candles = engine.snapshot(pair)
    if candles is None or candles.empty:
        return None
    if len(candles) <= job.warmup_bars:
        LOGGER.warning("event=warmup_incomplete symbol=%s timeframe=%s candles=%d warmup_bars=%d", pair.symbol, pair.timeframe, len(candles), job.warmup_bars)
        return None
    first = candles["bartime"].iloc[max(job.warmup_bars, len(candles) - job.write_bars)]
    symbol, timeframe = pair.symbol, pair.timeframe
    written = 0
    calc_bars = _last_bars(candles, job.window_bars)
    if job.with_context:
        table = _isolated("context", pair, lambda: strategy_runner.context_table(calc_bars, job.context, timeframe))
        if table is not None:
            written += inputs_writer.write(symbol, "CONTEXT", timeframe, _from_bar(table, first))
    for method in job.methods:
        if job.trend_needed is not None and (symbol, method, timeframe) not in job.trend_needed:
            continue  # không ô nào đọc bảng TREND này (vd knn ở M10): không tính, không ghi
        bars = _last_bars(candles, job.trend_window_bars.get(method, job.window_bars))
        table = _isolated(f"trend_{method}", pair, lambda method=method, bars=bars: strategy_runner.trend_table(bars, method, timeframe))
        if table is not None:
            written += inputs_writer.write(symbol, "TREND", timeframe, _from_bar(table, first), qualifier=method)
    for key in job.strategies:
        spec = get_strategy(key)
        if timeframe not in spec.live_timeframes:
            continue
        params = spec.normalize_params(None, symbol, timeframe)
        table = _isolated(f"trigger_{key}", pair, lambda spec=spec, params=params: strategy_runner.trigger_table(spec, calc_bars, params, symbol, timeframe))
        if table is not None:
            window, token = _from_bar(table, first), strategy_token(key)
            written += inputs_writer.write(symbol, "TRIGGER", timeframe, strategy_runner.trigger_events(window), qualifier=token)
            inputs_writer.drop_before(symbol, "TRIGGER", timeframe, first, qualifier=token)
            written += writer.write(symbol, "SETUP", timeframe, strategy_runner.setup_view(window), qualifier=token)
    return Outcome("written", written)


def _waiting(job: CellJob, latest_bar: pd.Series, now: pd.Timestamp, what: str) -> None:
    """Ghi nhận publisher đang CHỜ một nến tham chiếu (``what``: context hoặc trend_<khối>) của nến trigger mới nhất. Còn trong hạn hiệu lực thì chỉ là DEBUG (bình thường: nến tham chiếu đóng cùng lúc
    thường về chậm vài giây tới vài phút); đã quá hạn mà vẫn chưa đủ thì WARNING, vì tín hiệu của nến này (nếu có) đã mất."""
    from core_engine.src.signal_publisher import as_utc, signal_window

    cell = job.plan.cell
    _, valid_until = signal_window(cell.strategy, cell.entry_tf, latest_bar["bartime"], job.validity)
    if as_utc(now) >= valid_until:
        LOGGER.warning("event=reference_wait_expired symbol=%s strategy=%s timeframe=%s waiting_for=%s bartime=%s", cell.symbol, cell.strategy, cell.entry_tf, what, latest_bar["bartime"])
    else:
        LOGGER.debug("event=reference_waiting symbol=%s strategy=%s timeframe=%s waiting_for=%s bartime=%s", cell.symbol, cell.strategy, cell.entry_tf, what, latest_bar["bartime"])


def _signal_frame(spec: StrategySpec, params: dict[str, Any], table: pd.DataFrame, symbol: str, *, trend: pd.DataFrame | None = None, method: str = "", max_shift_bars: int = UNLIMITED_SHIFT) -> pd.DataFrame:
    """Từ bảng của ô dựng bảng tín hiệu để ghi: bản THÔ (``trend`` None) là chính ``signal`` lấy từ TRIGGER; bản LỌC ghép ``trend_bias`` của nến trend đã đóng gần nhất rồi lọc bằng khối ``method``
    (cùng hàm ``apply_trend_filter`` và cùng chính sách dời của khối như chiến lược vẫn dùng: ``setup`` của SETUP là hai mặt nạ buy/sell mà phần dời cần). Sau cùng ``add_entries`` thêm ``entry_price``.
    Lý do (``signal_reason``) lấy từ nến thô mở đợt (nến dời mượn lý do đó) cộng hậu tố trend khi là bản lọc."""
    from core_engine.src.rules.trend_tf import TREND_SUFFIX, apply_trend_filter

    frame = table.rename(columns={"signal": "raw_signal"})
    frame["raw_signal"] = frame["raw_signal"].fillna(0).astype(int)
    if trend is None:
        frame["signal"] = frame["raw_signal"]
        suffix = ""
    else:
        frame = merge_closed_reference(frame, trend, ["trend_bias"], ref_close_name="trend_close_time")
        frame = apply_trend_filter(
            frame, {"TREND_FILTER_ENABLED": True, "TREND_METHODS": (method,), "MAX_SHIFT_BARS": max_shift_bars}, buy_setup=frame["setup"].eq(1), sell_setup=frame["setup"].eq(-1)
        )
        suffix = TREND_SUFFIX
    reasons = frame["reason"].where(frame["raw_signal"].ne(0)).ffill().fillna("")
    frame["signal_reason"] = (reasons + suffix).where(frame["signal"].ne(0), "")
    return spec.add_entries(frame, params, symbol)


def process_cell(job: PublisherJob, engine: Any, publisher: Any, cell: Cell, now: pd.Timestamp | None = None, *, filtered: dict[str, Any] | None = None) -> Any:
    """Luồng của MỘT ô ở publisher: SETUP + TRIGGER + CONTEXT + TREND mới nhất (data_engine) -> ghép theo luật nến đóng (signal_publisher) -> lọc trend/dời, entry -> ghi db2.

    Chạy lại ở MỖI sự kiện của bất kỳ cấu phần nào của ô (nên "chờ" không cần hàng chờ): chưa đủ thì bỏ lượt này, đủ rồi thì quyết định và ghi; ghi lại cùng mốc là bản trùng nên vô hại, và nến trigger đã quá
    hạn hiệu lực thì tự bị ``publish_latest`` bỏ.
      * Nến mới nhất là nến mới nhất của SETUP (có dòng cho mọi nến, ghi cuối lượt). TRIGGER chỉ có dòng ở nến có tín hiệu nên KHÔNG có TRIGGER là bình thường (cửa sổ nến không có tín hiệu nào): khi đó mọi
        nến coi như không có tín hiệu thô.
      * Chưa có CONTEXT của nến mới nhất (khung context = khung trigger), hoặc nến context lớn hơn đóng đúng lúc mà chưa về: CHƯA quyết định gì. CONTEXT được đọc và ghép vào bảng nhưng HIỆN CHƯA
        làm điều kiện nào (tín hiệu đã có đủ điều kiện của chính khung trigger); đổi ``context_tf_by_symbol`` sang khung lớn hơn thì cùng đường đọc này dùng khung đó.
      * Bản thô (không lọc trend) ghi trước: nến mới nhất có ``signal`` != 0. Bản lọc của từng khối chỉ chạy khi nến trend cần dùng đã về (``reference_ready``), và khối không dời chỉ xét khi nến mới nhất
        có tín hiệu thô; khối dời xét ở mọi nến vì tín hiệu dời nằm ở nến không có tín hiệu thô.
    Trả về ``Outcome`` (trạng thái của bản thô, số tín hiệu ghi mới cộng cả các bản lọc), hoặc None nếu chưa đủ để quyết định."""
    from core_engine.src.signal_publisher import (
        Outcome,
        assemble_cell_table,
        entry_complete,
        reference_ready,
    )

    cell_job = job.cells[cell]
    setup = engine.snapshot(cell_job.setup)
    if setup is None or setup.empty:
        return None
    context = engine.snapshot(cell_job.context)
    if context is None or context.empty:
        return None
    events = engine.snapshot(cell_job.trigger)
    current_time = now if now is not None else pd.Timestamp.now(tz="UTC")
    latest_bar = setup.iloc[-1]
    entry_close = latest_bar["close_time"]
    same_frame = cell_job.context.timeframe == cell.entry_tf
    if not (entry_complete(setup, context) if same_frame else reference_ready(context, entry_close, cell_job.context_length)):
        _waiting(cell_job, latest_bar, current_time, "context")
        return None
    table = assemble_cell_table(setup, events, context, same_frame=same_frame)
    latest_bar = table.iloc[-1]
    spec = get_strategy(cell.strategy)
    outcome = publisher.publish_latest(
        strategy=cell.strategy, order_type=cell_job.order_type, validity=cell_job.validity, timeframe=cell.entry_tf, symbol=cell.symbol,
        latest=_signal_frame(spec, cell_job.params, table, cell.symbol).iloc[-1], now=current_time,
    )
    published = outcome.published
    for leg in cell_job.legs:
        trend = engine.snapshot(leg.pair)
        if trend is None or not reference_ready(trend, entry_close, leg.length):
            _waiting(cell_job, latest_bar, current_time, f"trend_{leg.method}")
            continue
        if not leg.shifts and int(latest_bar["signal"]) == 0:
            continue
        result = (filtered or {})[leg.method].publish_latest(
            strategy=cell.strategy, order_type=cell_job.order_type, validity=cell_job.validity, timeframe=cell.entry_tf, symbol=cell.symbol,
            latest=_signal_frame(spec, cell_job.params, table, cell.symbol, trend=trend, method=leg.method, max_shift_bars=job.max_shift_bars).iloc[-1], now=current_time,
        )
        published += result.published
    return Outcome(outcome.state, published)


def _describe(key: Any) -> str:
    """Nhãn của một việc cho dòng log lỗi: cặp (symbol, khung[, vai trò]) của runner, hoặc ô (symbol, chiến lược, khung) của publisher."""
    if isinstance(key, Cell):
        return f"symbol={key.symbol} strategy={key.strategy} timeframe={key.entry_tf}"
    if isinstance(key, Pair):
        return f"symbol={key.symbol} timeframe={key.timeframe}" + (f" role={key.role}" if key.role else "")
    return f"symbol={key}"


def _guarded(step: Callable[[], Any], key: Any) -> Any:
    """Chạy một bước của một việc, cô lập lỗi riêng của việc đó (ghi log, trả None) nhưng để redis.RedisError nổi lên cho vòng nối lại."""
    try:
        return step()
    except redis.RedisError:
        raise
    except Exception:
        LOGGER.exception("event=process_failed %s", _describe(key))
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
    keys: Sequence[Any] | None = None,
    route: Callable[[Any], Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    """Vòng lặp sự kiện chung của mọi loại tiến trình. Sự kiện keyspace chỉ đánh dấu việc "bẩn"; vòng lặp lấy từng việc bẩn và chạy ``step`` trên dữ liệu mới nhất, nên nhiều sự kiện của
    cùng một việc (đợt ghi bù) gộp thành một lần xử lý. Giữa hai việc luôn hút tiếp event đang chờ. Lúc khởi động quét toàn bộ việc MỘT lần
    (``process_on_startup``) để bù những gì đã tới lúc tiến trình tắt; không có nhịp quét định kỳ. Chạy mãi; test dừng bằng ``should_stop``.

    Mặc định mỗi cặp engine nghe là một việc (runner: quét theo ``keys`` = thứ tự khung lớn trước). ``route`` đổi cặp của sự kiện thành CÁC khoá việc (publisher: một cặp TREND đánh dấu mọi ô dùng nó, nhiều cặp cấu phần
    của cùng một ô cùng đánh dấu một việc, nên một lượt ghi của runner chỉ kích một lần quyết định mỗi ô)."""
    order = tuple(engine.pairs if keys is None else keys)
    to_keys = route if route is not None else (lambda pair: (pair,))
    dirty: dict[Any, None] = {}
    sweep: _Sweep | None = None

    def begin_sweep() -> None:
        nonlocal sweep
        LOGGER.info("event=reconcile_start db=%s pairs=%d", job.source["db"], len(order))
        sweep = _Sweep(order, clock())
        for key in order:
            dirty.setdefault(key)

    if job.process_on_startup:
        begin_sweep()
    while not should_stop():
        for pair in engine.poll_events(0.0 if dirty else _IDLE_WAIT_SECONDS):
            for key in to_keys(pair):
                dirty.setdefault(key)
        if not dirty:
            continue

        key = next(iter(dirty))
        del dirty[key]
        outcome = step(key)
        if sweep is not None:
            sweep.done(key, outcome)
            if not sweep.pending:
                LOGGER.info("event=reconcile_end pairs=%d published=%d duration_s=%.1f", sweep.total, sweep.published, clock() - sweep.started)
                from core_engine.src import strategy_runner

                stats = strategy_runner.memo_stats()
                if stats:
                    LOGGER.info("event=memo_stats hits=%d misses=%d size=%d", stats["hits"], stats["misses"], stats["size"])
                sweep = None


def run_runner_lane(
    job: RunnerJob,
    engine: Any,
    writer: Any,
    inputs_writer: Any,
    *,
    clock: Callable[[], float] = time.monotonic,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    """Vòng lặp sự kiện của một tiến trình runner (xem ``_run_loop``): mỗi cặp (symbol, khung) bẩn chạy ``process_frame``; quét khởi động theo thứ tự khung lớn trước."""
    _run_loop(job, engine, lambda pair: _guarded(lambda: process_frame(job, engine, writer, inputs_writer, pair), pair), keys=job.pairs, clock=clock, should_stop=should_stop)


def run_publisher_lane(
    job: PublisherJob,
    engine: Any,
    publisher: Any,
    *,
    filtered: dict[str, Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
    should_stop: Callable[[], bool] = lambda: False,
) -> None:
    """Vòng lặp sự kiện của một tiến trình publisher (xem ``_run_loop``): mỗi ô bẩn chạy ``process_cell``; sự kiện của bất kỳ cấu phần nào của ô đều đánh dấu ô đó."""
    _run_loop(
        job,
        engine,
        lambda cell: _guarded(lambda: process_cell(job, engine, publisher, cell, filtered=filtered), cell),
        keys=tuple(job.cells),
        route=lambda pair: job.routes.get(pair, ()),
        clock=clock,
        should_stop=should_stop,
    )


def _run_runner_forever(job: RunnerJob) -> None:
    """Nối Redis, đăng ký nghe đúng các cặp nến, rồi chạy vòng lặp sự kiện mãi mãi. Việc nối và nối lại khi mất Redis (sau ``reconnect_delay_seconds``) do ``redis_connector.keep_connected`` làm."""
    from core_engine.src.data_engine import DataEngine
    from core_engine.src.redis_connector import keep_connected
    from core_engine.src.strategy_runner import ComponentWriter

    def connect(register: Callable[[Any], Any]) -> tuple[Any, Any, Any]:
        engine = register(DataEngine(**job.source, pairs=job.pairs))
        writer = register(ComponentWriter(**job.sink))
        inputs_writer = register(ComponentWriter(**job.inputs_sink))
        engine.ping()
        writer.ping()
        inputs_writer.ping()
        LOGGER.info("event=redis_connected input_db=%s components_db=%s trend_db=%s", job.source["db"], job.sink["db"], job.inputs_sink["db"])
        flags = engine.check_keyspace_flags()
        subscribed = engine.subscribe()
        LOGGER.info("event=subscribed db=%s key_prefix=%s notify_flags=%s pairs=%d", job.source["db"], job.source["key_prefix"], flags, subscribed)
        return engine, writer, inputs_writer

    keep_connected(connect, lambda parts: run_runner_lane(job, parts[0], parts[1], parts[2]), retry_delay_seconds=job.reconnect_delay_seconds)


def _run_publisher_forever(job: PublisherJob) -> None:
    """Như ``_run_runner_forever`` cho publisher: nối db0 và db1 của DE (nghe và đọc cấu phần) và db2 (ghi tín hiệu thô, cùng từng khối trend), nghe các cặp cấu phần của mọi ô, chạy vòng lặp mãi mãi.
    Redis đầu ra phải bật lớp 'K' + 'h' của notify-keyspace-events (thiếu thì ``check_keyspace_flags`` báo RuntimeError rõ ràng ngay lúc khởi động)."""
    from core_engine.src.data_engine import DataEngine
    from core_engine.src.redis_connector import keep_connected
    from core_engine.src.signal_publisher import RawSignalWriter

    def connect(register: Callable[[Any], Any]) -> tuple[Any, Any, dict[str, Any]]:
        engine = register(DataEngine(**job.source, pairs=job.pairs))
        publisher = register(RawSignalWriter(**job.sink))
        filtered = {method: register(RawSignalWriter(**params)) for method, params in job.filtered_sinks.items()}
        engine.ping()
        publisher.ping()
        for writer in filtered.values():
            writer.ping()
        LOGGER.info("event=redis_connected components_db=%s trend_db=%s signals_db=%s", job.source["db"], job.source["role_db"]["TREND"], job.sink["db"])
        flags = engine.check_keyspace_flags()
        subscribed = engine.subscribe()
        LOGGER.info("event=subscribed db=%s key_prefix=%r notify_flags=%s pairs=%d", job.source["db"], job.source["key_prefix"], flags, subscribed)
        return engine, publisher, filtered

    keep_connected(
        connect, lambda parts: run_publisher_lane(job, parts[0], parts[1], filtered=parts[2]), retry_delay_seconds=job.reconnect_delay_seconds
    )


def _redis_enabled(config: dict[str, Any]) -> bool:
    enabled = require(config["redis"], "enabled", "redis.enabled")
    if not isinstance(enabled, bool):
        raise TypeError("redis.enabled must be true or false")
    return enabled


def _lane_name(role: str, input_name: str, symbols: Sequence[str] | None, part: str = "") -> str:
    """Tên tiến trình trong log (cũng là tên file log ``de_run_<tên>.log``): vai trò, rồi làn (khoá chiến lược hoặc ``trend``, nếu tiến trình chỉ chạy một làn), rồi nguồn nến (runner), rồi các symbol nếu tiến trình bị giới hạn.
    Chỉ chữ thường, số và gạch dưới."""
    wanted = sorted(_wanted_symbols(symbols) or ())
    return "_".join(name for name in (role, part, input_name, *(symbol.lower() for symbol in wanted)) if name)


def run_runner(input_name: str = "", symbols: Sequence[str] | None = None, lane: str = "") -> int:
    """Chạy một tiến trình runner (gọi từ main.py); ``input_name`` chọn nguồn nến (rỗng = redis.input, tên khác = redis.input_sources.<tên>), ``symbols`` (tuỳ chọn) giới hạn symbol để chia việc, ``lane`` (tuỳ chọn) giới hạn
    vai trò: khoá một chiến lược hoặc ``trend`` (xem ``build_runner_job``; rỗng = trọn mọi vai trò).
    File log ``de_run_runner[_<làn>][_<nguồn>][_<symbol>...].log``. Trả về mã thoát: 0 khi dừng êm hoặc redis.enabled=false, 1 khi cấu hình sai (đã ghi event=config_error)."""
    from core_engine.src.log import configure_logging, set_log_level

    name = _lane_name("runner", input_name, symbols, lane)
    configure_logging("INFO", name)
    try:
        job = build_runner_job(CONFIG, input_name, symbols, lane)
        set_log_level(job.log_level)
        LOGGER.info(
            "event=startup role=runner log_level=%s input_db=%s components_db=%s trend_db=%s pairs=%d strategies=%d trend_methods=%d warmup_bars=%d write_bars=%d lane=%s",
            job.log_level, job.source["db"], job.sink["db"], job.inputs_sink["db"], len(job.pairs), len(job.strategies), len(job.methods), job.warmup_bars, job.write_bars, name,
        )
        LOGGER.info("event=input_source name=%s host=%s port=%s symbols=%d", input_name or "default", job.source["host"], job.source["port"], len({pair.symbol for pair in job.pairs}))
        if not _redis_enabled(CONFIG):
            LOGGER.info("event=disabled reason=redis_disabled_by_config")
            return 0
        from core_engine.src import strategy_runner

        strategy_runner.enable_memo(256)
        LOGGER.info("event=memo enabled=true maxsize=256")
        _run_runner_forever(job)
    except (KeyboardInterrupt, SystemExit):
        LOGGER.info("event=stopped")
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        LOGGER.critical("event=config_error error=%s", exc)
        return 1
    return 0


def run_publisher(symbols: Sequence[str] | None = None, strategy: str = "") -> int:
    """Chạy một tiến trình publisher (gọi từ main.py); ``symbols`` và ``strategy`` (tuỳ chọn) giới hạn symbol / chiến lược để chia việc. File log ``de_run_publisher[_<chiến lược>][_<symbol>...].log``.
    Mã thoát như ``run_runner``."""
    from core_engine.src.log import configure_logging, set_log_level

    lane = _lane_name("publisher", "", symbols, strategy)
    configure_logging("INFO", lane)
    try:
        job = build_publisher_job(CONFIG, symbols, strategy)
        set_log_level(job.log_level)
        LOGGER.info(
            "event=startup role=publisher log_level=%s components_db=%s trend_db=%s signals_db=%s cells=%d pairs=%d max_shift_bars=%d lane=%s",
            job.log_level, job.source["db"], job.source["role_db"]["TREND"], job.sink["db"], len(job.cells), len(job.pairs), job.max_shift_bars, lane,
        )
        for key in STRATEGIES:
            cells = [cell_job for cell, cell_job in job.cells.items() if cell.strategy == key]
            if cells:
                LOGGER.info("event=strategy strategy=%s cells=%d legs=%d alternating_filter=%s", key, len(cells), sum(len(cell_job.legs) for cell_job in cells), alternating_filter_for(key))
        if not _redis_enabled(CONFIG):
            LOGGER.info("event=disabled reason=redis_disabled_by_config")
            return 0
        _run_publisher_forever(job)
    except (KeyboardInterrupt, SystemExit):
        LOGGER.info("event=stopped")
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        LOGGER.critical("event=config_error error=%s", exc)
        return 1
    return 0
