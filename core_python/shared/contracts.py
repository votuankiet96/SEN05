"""Typed result contracts shared across symbol/portfolio workflows.

Architecture role
-----------------
- This file is the narrow contract layer between execution code and research/UI.
- It keeps backtest returns predictable so notebooks, reports, and dashboards do
  not need to guess the shape of returned objects.

Upstream dependencies
---------------------
- `shared.execution` / strategy runners build these objects.

Downstream dependencies
-----------------------
- `strategies.combo.symbol.*`
- `strategies.combo.portfolio.*`
- future reporting / chart / notebook layers

Change impact
-------------
- Renaming fields here is a breaking change for every consumer that reads
  `result.metrics`, `result.equity`, `result.symbol_results`, or window outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass(slots=True)
class SymbolBacktestResult:
    """Normalized return object for a single-symbol backtest run.

    Notes
    -----
    - `raw_data` is the pre-indicator OHLCV frame.
    - `signal_data` is the enriched frame after indicators and signal generation.
    - `symbol_config` is the effective per-symbol runtime config actually used.
    - `strategy_settings` is the resolved capital/risk profile, including
      account-mode effects such as FTMO vs standard.
    """

    symbol: str
    raw_data: pd.DataFrame
    signal_data: pd.DataFrame
    trades: list[dict[str, Any]]
    equity: pd.Series
    metrics: dict[str, Any]
    symbol_config: dict[str, Any]
    strategy_settings: dict[str, Any]
    account_mode: str


@dataclass(slots=True)
class PortfolioBacktestResult:
    """Normalized return object for a portfolio backtest run.

    Notes
    -----
    - `symbol_results` keeps the full child results so portfolio analysis can
      drill down without re-running each symbol.
    - `equity_frame` is the aligned equity table per symbol.
    - `combined_equity` is the aggregated portfolio curve derived from that table.
    """

    symbol_results: dict[str, SymbolBacktestResult]
    trades: list[dict[str, Any]]
    equity_frame: pd.DataFrame
    combined_equity: pd.Series
    metrics: dict[str, Any]
    account_mode: str
    symbol_keys: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PortfolioWindowResult:
    """One rolling OOS window in a portfolio walk-forward test.

    This contract exists so walk-forward code can return both summary metrics and
    the combined equity path for each OOS window without inventing ad-hoc dicts.
    """

    window: int
    start: pd.Timestamp
    end: pd.Timestamp
    metrics: dict[str, Any]
    combined_equity: pd.Series
