"""Research utilities package cho MA Cross notebooks.

Gom tất cả helper functions cần thiết cho Jupyter research workflow.
"""

from __future__ import annotations

from ._shared import show_note
from .dashboard import configure_notebook, show_run_config, show_strategy_summary
from .export import export_research_bundle
from .symbol import (
    metric_row,
    metrics_frame,
    rank_grid,
    run_portfolio_backtest,
    run_symbol_grid_search,
    run_timeframe_matrix,
    select_top_candidates,
    simple_walkforward,
)

__all__ = [
    "configure_notebook",
    "export_research_bundle",
    "metric_row",
    "metrics_frame",
    "rank_grid",
    "run_portfolio_backtest",
    "run_symbol_grid_search",
    "run_timeframe_matrix",
    "select_top_candidates",
    "show_note",
    "show_run_config",
    "show_strategy_summary",
    "simple_walkforward",
]
