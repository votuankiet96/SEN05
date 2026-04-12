# =============================================================================
# strategies/combo/__init__.py  —  Combo v2 public API
# =============================================================================
# Re-export toàn bộ public API từ core/ để backward-compatible:
#   from strategies.combo import scan_pipeline, strategy_config ...
#
# Nếu muốn import trực tiếp từ module cụ thể, dùng:
#   from strategies.combo.core.strategy_config import STRATEGY
# =============================================================================
from .core import (  # noqa: F401 — re-export intentional
    backtest_engine,
    reversal_scanner,
    scan_pipeline,
    strategy_config,
    theme,
)
