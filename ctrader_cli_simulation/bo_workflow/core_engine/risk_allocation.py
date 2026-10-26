"""Phân bổ rủi ro cho portfolio nhiều channel (một tài khoản chung).

Hạn mức tổng = tổng `risk_percent` mỗi CỤM lệnh của các channel, tức tổng rủi ro nếu mọi channel cùng có một cụm
mở và cùng chạm SL. Cộng thẳng được vì mỗi channel (chiến lược + symbol + khung) chỉ có tối đa 1 cụm mở cùng lúc
(BacktestBot.ReconcileExistingExposure bỏ tín hiệu cùng hướng, tín hiệu ngược hướng đóng cụm cũ trước khi mở cụm mới).

Chế độ chia đều: mỗi channel = hạn mức / N, làm tròn XUỐNG `decimals` chữ số để tổng không bao giờ vượt hạn mức.
Chế độ tự nhập: `validate_manual_risks` chỉ kiểm tổng. Cả hai đều trả `risk_percent` tường minh từng channel —
đúng contract hiện có của `channels` (core không biết hạn mức, nên `run_key` không đổi).

Dùng `Decimal` (qua `str`) để 2/5 ra đúng 0.4 và 0.1 + 0.2 ra đúng 0.3, không dính sai số số thực.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any, Iterable

# Số chữ số thập phân của risk_percent mỗi channel; config.yaml `backtest.portfolio.risk_decimals` ghi lại giá trị này.
DEFAULT_RISK_DECIMALS = 4
MAX_RISK_DECIMALS = 8


def _positive_decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number, not a boolean")
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} must be a finite number > 0, got {value!r}") from None
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{name} must be a finite number > 0, got {value!r}")
    return number


def validated_total_percent(value: Any) -> float:
    """Hạn mức tổng hợp lệ (số hữu hạn > 0) dưới dạng float."""
    return float(_positive_decimal(value, "total_percent"))


def validated_decimals(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_RISK_DECIMALS:
        raise ValueError(f"decimals must be an integer between 0 and {MAX_RISK_DECIMALS}, got {value!r}")
    return value


def _channel_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"channel_count must be an integer >= 1, got {value!r}")
    return value


def allocate_equal_risk(total_percent: Any, channel_count: int, *, decimals: int = DEFAULT_RISK_DECIMALS) -> float:
    """`risk_percent` mỗi channel khi chia đều `total_percent` cho `channel_count` channel, làm tròn xuống."""
    total = _positive_decimal(total_percent, "total_percent")
    count = _channel_count(channel_count)
    places = validated_decimals(decimals)
    each = (total / count).quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN)
    if each <= 0:
        raise ValueError(
            f"total_percent {total_percent!r} is too small to split across {count} channels "
            f"with {places} decimals; raise the limit or reduce the number of channels"
        )
    return float(each)


def validate_manual_risks(risks: Iterable[Any], total_percent: Any) -> float:
    """Kiểm từng `risk_percent` tự nhập (> 0) và tổng ≤ `total_percent`; trả tổng đang dùng."""
    limit = _positive_decimal(total_percent, "total_percent")
    values = [_positive_decimal(risk, f"risks[{index}]") for index, risk in enumerate(risks)]
    if not values:
        raise ValueError("risks must contain at least one channel")
    used = sum(values, Decimal(0))
    if used > limit:
        raise ValueError(f"manual risks add up to {used}%, above the total limit of {limit}%")
    return float(used)
