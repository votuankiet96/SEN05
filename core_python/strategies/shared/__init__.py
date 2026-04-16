# =============================================================================
# strategies/shared/__init__.py  —  Shared utilities cho toàn bộ strategies
# =============================================================================
# Mọi module ở đây đều zero-coupling với bất kỳ strategy cụ thể nào.
# Mỗi strategy (combo, ma_cross, ...) có thể import trực tiếp từ đây.

from .metrics import calc_metrics, in_bao_cao, _bars_per_year
from .monte_carlo import run_monte_carlo, plot_monte_carlo
from .theme import (
    SIGNAL, DARK, EQUITY_COLORS, METRIC_CSS, NUM_FMT, METRICS_FMT,
    style_signal_row, style_reversal_row, style_combined_row,
    dark_table_props, setup_dark_axes, setup_dark_figure, color_pf,
)
from .execution_engine import (
    build_pending_order,
    calc_dynamic_slippage,
    backtest_symbol,
    backtest_fast,
)
