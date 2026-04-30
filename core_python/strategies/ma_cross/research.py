from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from .config import summary
from .portfolio.backtest import run_portfolio_backtest
from .symbol.matrix import run_timeframe_matrix
from .symbol.optimize import (
    metric_row,
    metrics_frame,
    rank_grid,
    run_symbol_grid_search,
    select_top_candidates,
)
from .symbol.walkforward import simple_walkforward

try:
    from IPython.display import Markdown, display
except Exception:  # pragma: no cover - console fallback.
    Markdown = None

    def display(obj: Any) -> None:
        print(obj)


def configure_notebook() -> None:
    pd.set_option("display.max_columns", 80)
    pd.set_option("display.width", 160)
    pd.set_option("display.float_format", lambda x: f"{x:,.4f}")


def show_note(text: str) -> None:
    if Markdown is None:
        print(text)
    else:
        display(Markdown(text))


def show_strategy_summary() -> None:
    show_note("```text\n" + summary() + "\n```")


def show_run_config(title: str, config: Mapping[str, Any]) -> None:
    show_note(f"### {title}")
    display(pd.Series(dict(config), name="value").to_frame())


def export_research_bundle(
    bundle: Mapping[str, Any],
    *,
    name: str,
    output_dir: str | Path = "output/ma_cross/research",
) -> Path:
    out = Path(output_dir) / name
    out.mkdir(parents=True, exist_ok=True)
    for key, value in bundle.items():
        if isinstance(value, pd.DataFrame):
            value.to_csv(out / f"{key}.csv", index=True)
        elif isinstance(value, pd.Series):
            value.to_csv(out / f"{key}.csv", header=True)
    return out


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
