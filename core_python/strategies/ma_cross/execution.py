"""Execution boundary cho MA Cross strategy.

MA Cross là market-order next-bar strategy. Không dùng custom execution engines.
Engine thực nằm ở core_python.shared.execution.engines.market.

Module này là boundary tường rõ ràng: mọi import engine bên trong ma_cross
đều đi qua đây thay vì import trực tiếp từ shared path.
"""

from __future__ import annotations

from core_python.shared.execution.engines.market import backtest_market_symbol
from .execution_basket import backtest_basket_reversal_symbol

__all__ = ["backtest_basket_reversal_symbol", "backtest_market_symbol"]
