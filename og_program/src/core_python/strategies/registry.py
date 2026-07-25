"""
Registry (sổ đăng ký) các chiến lược giao dịch cho dashboard SEN05.

Mô tả:
    Mỗi chiến lược được mô tả bởi một StrategySpec — dataclass bất biến
    chứa metadata (key, label, params mặc định) và 4 callable thực thi pipeline.

    Thêm chiến lược mới:
        1. Tạo module strategies/<tên>/ với config.py, signals.py, levels.py.
        2. Đăng ký vào dict STRATEGIES bên dưới.

Đầu ra:
    STRATEGIES: dict[str, StrategySpec] — tra cứu theo key.
    get_strategy(key): StrategySpec hoặc raise KeyError.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import pandas as pd

from core_python.strategies.combo import config as combo_config
from core_python.strategies.combo.levels import add_combo_levels
from core_python.strategies.combo.signals import (
    add_combo_indicators,
    detect_combo_signals,
)
from core_python.strategies.ma_cross import config as ma_cross_config
from core_python.strategies.ma_cross.levels import add_ma_cross_levels
from core_python.strategies.ma_cross.signals import (
    add_ma_cross_indicators,
    detect_ma_cross_signals,
)


class NormalizeParamsFn(Protocol):
    """Merge defaults + symbol-specific params + user overrides into one validated dict."""

    def __call__(
        self, overrides: dict[str, Any] | None = None, symbol: str | None = None
    ) -> dict[str, Any]: ...


class AddIndicatorsFn(Protocol):
    """Add indicator columns to a raw OHLCV frame. Must not mutate the input."""

    def __call__(self, df: pd.DataFrame, params: dict[str, Any] | None = None) -> pd.DataFrame: ...


class DetectSignalsFn(Protocol):
    """Add signal columns to an indicator-enriched frame.

    `symbol` and `sess_mask` are accepted for interface parity across
    strategies even when a given strategy doesn't need them.
    """

    def __call__(
        self,
        df: pd.DataFrame,
        symbol: str | None = None,
        params: dict[str, Any] | None = None,
        sess_mask: pd.Series | None = None,
    ) -> pd.DataFrame: ...


class AddLevelsFn(Protocol):
    """Add entry/SL/TP columns to a signal-enriched frame. `params` is required here."""

    def __call__(
        self, df: pd.DataFrame, params: dict[str, Any], symbol: str | None = None
    ) -> pd.DataFrame: ...


@dataclass(frozen=True)
class StrategySpec:
    """
    Mô tả đầy đủ một chiến lược giao dịch — bất biến sau khi tạo.

    Thuộc tính:
        key:              Mã định danh duy nhất (ví dụ: "combo", "ma_cross").
        label:            Tên hiển thị trên UI (ví dụ: "Combo", "MA Cross").
        default_params:   Dict tham số mặc định khi không có override.
        param_fields:     Định nghĩa UI fields (type, min, max...) cho sidebar params.
        normalize_params: NormalizeParamsFn — (overrides, symbol) → dict tham số đã validate.
        add_indicators:   AddIndicatorsFn — (df, params) → df với cột chỉ báo thêm vào.
        detect_signals:   DetectSignalsFn — (df, symbol, params, sess_mask) → df với cột signal.
        add_levels:       AddLevelsFn — (df, params, symbol) → df với cột entry/SL/TP.
        supported_timeframes: Các TF được phép; rỗng nghĩa là dùng mọi TF hệ thống.
        default_timeframe: TF mặc định của strategy trên API/dashboard.
        symbol_defaults:  Tham số UI mặc định riêng theo symbol.

    Invariant:
        Pipeline phải gọi theo thứ tự:
        normalize_params → add_indicators → detect_signals → add_levels.
        Mỗi bước nhận output của bước trước làm input.

    Thêm chiến lược mới — checklist:
        1. Viết 4 hàm khớp đúng chữ ký của 4 Protocol trên (NormalizeParamsFn,
           AddIndicatorsFn, DetectSignalsFn, AddLevelsFn) trong
           strategies/<tên>/config.py, signals.py, levels.py.
        2. Không sửa DataFrame input tại chỗ (return bản copy).
        3. Đăng ký một StrategySpec mới vào dict STRATEGIES bên dưới.
    """
    key: str
    label: str
    default_params: dict[str, Any]
    param_fields: list[dict[str, Any]]
    normalize_params: NormalizeParamsFn
    add_indicators: AddIndicatorsFn
    detect_signals: DetectSignalsFn
    add_levels: AddLevelsFn
    recommended_timeframes: tuple[str, ...] = ()
    supported_timeframes: tuple[str, ...] = ()
    default_timeframe: str | None = None
    symbol_defaults: dict[str, dict[str, Any]] = field(default_factory=dict)


# Danh sách chiến lược được hỗ trợ. Key phải lowercase để match với query param.
STRATEGIES: dict[str, StrategySpec] = {
    "combo": StrategySpec(
        key="combo",
        label="Combo",
        default_params=combo_config.DEFAULT_PARAMS,
        param_fields=combo_config.PARAM_FIELDS,
        normalize_params=combo_config.normalize_params,
        add_indicators=add_combo_indicators,
        detect_signals=detect_combo_signals,
        add_levels=add_combo_levels,
        recommended_timeframes=combo_config.RECOMMENDED_TIMEFRAMES,
        symbol_defaults={
            symbol: {"X": value}
            for symbol, value in combo_config.SYMBOL_X.items()
        },
    ),
    "ma_cross": StrategySpec(
        key="ma_cross",
        label="MA Cross",
        default_params=ma_cross_config.DEFAULT_PARAMS,
        param_fields=ma_cross_config.PARAM_FIELDS,
        normalize_params=ma_cross_config.normalize_params,
        add_indicators=add_ma_cross_indicators,
        detect_signals=detect_ma_cross_signals,
        add_levels=add_ma_cross_levels,
        recommended_timeframes=ma_cross_config.RECOMMENDED_TIMEFRAMES,
        supported_timeframes=ma_cross_config.SUPPORTED_TIMEFRAMES,
        default_timeframe=ma_cross_config.DEFAULT_TIMEFRAME,
        symbol_defaults={
            symbol: {"X": value}
            for symbol, value in ma_cross_config.SYMBOL_X.items()
        },
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
