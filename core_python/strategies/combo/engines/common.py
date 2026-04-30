"""Common helpers for Combo pending-order execution engines."""

from __future__ import annotations

from typing import TypedDict

from core_python.shared.execution.primitives import (
    adverse_exit_price,
    risk_sized_lots_from_distance,
)
from core_python.strategies.combo.orders import create_pending_breakout_intent


_DEFAULT_STRATEGY = {
    # Mức rủi ro cho mỗi trade, tính theo equity hiện tại. 0.005 = 0.5%.
    "risk_per_trade":       0.005,
    # Nếu PnL trong ngày giảm quá ngưỡng này so với init_eq, engine không mở thêm
    # lệnh mới trong ngày đó.
    "daily_loss_limit":     0.05,
    # Giới hạn sụt giảm tài khoản tổng thể. Cách tính phụ thuộc
    # `max_drawdown_mode`.
    "max_drawdown_limit":   0.10,
    "max_drawdown_mode":    "fixed_initial",
    # Backward-compatible aliases still accepted by _resolve_strategy_limits().
    "ftmo_daily_limit":     0.05,
    "ftmo_max_dd":          0.10,
    # Khi lệnh lời ít nhất `trailing_activation * atr`, trailing stop bắt đầu
    # hoạt động.
    "trailing_activation":  1.0,
    # Pending order hết hạn sau số bar này nếu chưa khớp.
    "pending_ttl_bars":     3,
    # Tỷ lệ đóng ở partial TP. 0.5 nghĩa là đóng nửa vị thế ở TP đầu tiên.
    "partial_tp_fraction":  0.5,
}

_DEFAULT_COSTS = {
    # Slippage nền tính bằng point. Có thể bị tăng bởi calc_dynamic_slippage().
    "slippage_pts":       2,
    # Commission một chiều cho mỗi lot. Engine nhân 2 để tính vào/ra lệnh.
    "commission_per_lot": 3.5,
}


class FastComboPosition(TypedDict):
    d: int
    entry: float
    sl: float
    tp: float
    atr: float
    risk: float
    sl_dist: float
    trail: bool
    comm: float
    partial_tp_hit: bool
    half1_pnl: float
    lot_size: float
    entry_date: object


class FullComboPosition(TypedDict):
    direction: int
    entry: float
    sl: float
    sl_at_entry: float
    tp: float
    tp_at_entry: float
    atr: float
    risk_usd: float
    sl_dist: float
    entry_time: object
    trail_active: bool
    commission: float
    lot_size: float
    partial_tp_hit: bool
    half1_exit: object
    half1_exit_time: object
    half1_pnl_gross: float
    half1_commission: float


FAST_POSITION_FIELDS = frozenset(FastComboPosition.__annotations__)
FULL_POSITION_FIELDS = frozenset(FullComboPosition.__annotations__)


def make_fast_position(
    *,
    direction: int,
    entry: float,
    sl: float,
    tp: float,
    atr: float,
    risk_usd: float,
    sl_dist: float,
    commission: float,
    lot_size: float,
    entry_date,
) -> FastComboPosition:
    return {
        "d": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "atr": atr,
        "risk": risk_usd,
        "sl_dist": sl_dist,
        "trail": False,
        "comm": commission,
        "partial_tp_hit": False,
        "half1_pnl": 0.0,
        "lot_size": lot_size,
        "entry_date": entry_date,
    }


def make_full_position(
    *,
    direction: int,
    entry: float,
    sl: float,
    tp: float,
    atr: float,
    risk_usd: float,
    sl_dist: float,
    entry_time,
    commission: float,
    lot_size: float,
) -> FullComboPosition:
    return {
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "sl_at_entry": sl,
        "tp": tp,
        "tp_at_entry": tp,
        "atr": atr,
        "risk_usd": risk_usd,
        "sl_dist": sl_dist,
        "entry_time": entry_time,
        "trail_active": False,
        "commission": commission,
        "lot_size": lot_size,
        "partial_tp_hit": False,
        "half1_exit": None,
        "half1_exit_time": None,
        "half1_pnl_gross": 0.0,
        "half1_commission": 0.0,
    }


def _resolve_strategy_limits(strategy_cfg: dict) -> tuple[float, float]:
    """Lấy daily loss limit và max drawdown limit từ `strategy_cfg`.

    Làm gì
    ------
    Hàm này đọc 2 giới hạn rủi ro quan trọng:

    - `daily_loss_limit`: mức lỗ tối đa trong ngày.
    - `max_drawdown_limit`: mức sụt giảm tối đa của tài khoản.

    Nó cũng hỗ trợ tên key cũ `ftmo_daily_limit` và `ftmo_max_dd` để các config
    cũ vẫn chạy được.

    Nhận input gì
    -------------
    `strategy_cfg`: dict cấu hình strategy sau khi đã merge default và override.

    Trả output gì
    -------------
    Tuple `(daily_limit, max_dd)`, cả hai đều là `float`.

    Tại sao cần nó
    --------------
    Logic backtest phía dưới chỉ muốn dùng một tên chuẩn. Hàm này gom việc tương
    thích key cũ/key mới vào một nơi.
    """
    daily_limit = strategy_cfg.get(
        "daily_loss_limit",
        strategy_cfg.get("ftmo_daily_limit", _DEFAULT_STRATEGY["daily_loss_limit"]),
    )
    max_dd = strategy_cfg.get(
        "max_drawdown_limit",
        strategy_cfg.get("ftmo_max_dd", _DEFAULT_STRATEGY["max_drawdown_limit"]),
    )
    return float(daily_limit), float(max_dd)


def _max_drawdown_breached(
    equity: float,
    init_eq: float,
    peak_eq: float,
    max_dd: float,
    mode: str = "fixed_initial",
) -> bool:
    """Kiểm tra equity có vi phạm luật max drawdown hay chưa.

    Làm gì
    ------
    Hàm này trả `True` nếu equity hiện tại đã thấp hơn ngưỡng drawdown cho phép.

    Nhận input gì
    -------------
    `equity`
        Equity hiện tại.

    `init_eq`
        Vốn ban đầu của backtest.

    `peak_eq`
        Equity cao nhất từng đạt được trong quá trình chạy.

    `max_dd`
        Mức drawdown tối đa, ví dụ `0.10` nghĩa là 10%.

    `mode`
        `"fixed_initial"` so với vốn ban đầu, giống luật maximum loss cố định.
        `"trailing_peak"` hoặc `"peak"` so với đỉnh equity đã đạt được.

    Trả output gì
    -------------
    `bool`: `True` nếu đã vi phạm giới hạn, ngược lại `False`.

    Tại sao cần nó
    --------------
    Nhiều account mode có luật drawdown khác nhau. Tách thành helper giúp cả
    `backtest_symbol()` và `backtest_fast()` dùng chung một logic.
    """
    if max_dd >= 1:
        return False
    if mode in {"trailing_peak", "peak"}:
        return equity <= peak_eq * (1 - max_dd)
    return equity <= init_eq * (1 - max_dd)


# ─────────────────────────────────────────────────────────────────────────────
# ORDER UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def build_pending_order(bar, direction: int, x: float, ktp: float,
                        atr: float, ttl: int) -> dict:
    """Tạo pending order từ dữ liệu của một bar.

    Làm gì
    ------
    Hàm này biến một tín hiệu BUY/SELL thành lệnh chờ breakout:

    - BUY: entry nằm trên `high` của bar hiện tại thêm buffer `x`.
    - SELL: entry nằm dưới `low` của bar hiện tại trừ buffer `x`.
    - SL đặt ở phía đối diện của bar, cũng cộng/trừ buffer `x`.
    - TP tính theo `entry + direction * ktp * atr`.

    Nhận input gì
    -------------
    `bar`
        Một dòng dữ liệu nến, cần có ít nhất `high` và `low`.

    `direction`
        `1` là BUY, `-1` là SELL.

    `x`
        Breakout buffer, dùng để tránh vào lệnh ngay sát đỉnh/đáy bar.

    `ktp`
        Hệ số take-profit theo ATR.

    `atr`
        ATR hiện tại, dùng để tính khoảng TP.

    `ttl`
        Số bar lệnh chờ còn hiệu lực.

    Trả output gì
    -------------
    Dict gồm `direction`, `entry`, `sl`, `tp`, `atr`, `ttl`.

    Tại sao cần nó
    --------------
    Cả full backtest và fast backtest đều cần tạo pending order cùng một kiểu.
    Helper này tránh lặp công thức entry/SL/TP ở nhiều nơi.
    """
    intent = create_pending_breakout_intent(
        "COMBO",
        bar,
        direction=direction,
        x=x,
        ktp=ktp,
        atr=atr,
        ttl_bars=ttl,
    )
    if intent is None:
        raise ValueError(f"Unsupported pending order direction: {direction!r}")
    return {
        "direction": intent.direction,
        "entry": intent.entry,
        "sl": intent.sl,
        "tp": intent.tp,
        "atr": intent.metadata["atr"],
        "ttl": intent.ttl_bars,
    }


def _risk_lot_size(
    risk_usd: float,
    sl_dist: float,
    *,
    point_size: float,
    contract_value: float,
    commission_per_lot: float,
    min_lot: float,
    max_lot: float,
    lot_step: float,
) -> float:
    return risk_sized_lots_from_distance(
        risk_usd,
        1.0,
        sl_dist,
        {
            "point_size": point_size,
            "contract_value": contract_value,
            "min_lot_size": min_lot,
            "max_lot_size": max_lot,
            "lot_step": lot_step,
        },
        commission_per_lot,
    )


def _adverse_exit_price(price: float, direction: int, spread_pts: float, slippage: float) -> float:
    return adverse_exit_price(price, direction, spread_pts, slippage)
