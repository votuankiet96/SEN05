"""Compatibility exports for price and slippage primitives."""

from core_python.shared.execution.primitives import (
    adverse_entry_price,
    adverse_exit_price,
    bar_time,
    calc_dynamic_slippage,
)


__all__ = [
    "adverse_entry_price",
    "adverse_exit_price",
    "bar_time",
    "calc_dynamic_slippage",
]
