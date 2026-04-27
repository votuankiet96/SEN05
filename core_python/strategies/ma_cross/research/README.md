# MA Cross Research Notebooks

This folder mirrors the Combo research workflow, but every notebook is scoped
to the MA Cross market-order strategy.

## Notebook Map

1. `01_symbol_backtest.ipynb`
   - Baseline one symbol across M20/M30/M45.
   - Uses `run_timeframe_matrix()` and the production MA Cross backtest path.

2. `02_symbol_optimize.ipynb`
   - Conservative full-engine grid search.
   - Tests fast/slow MA, SMA/EMA, ATR stop/TP, and timeframe candidates.

3. `03_portfolio_backtest.ipynb`
   - Equal-sleeve portfolio run across selected symbols.
   - Combines symbol equity and checks portfolio-level FTMO-style limits.

4. `04_portfolio_walkforward.ipynb`
   - Explicit IS/OOS date-window validation.
   - Does not optimize inside each window; it checks parameter stability.

5. `05_chart_and_signal_scanner.ipynb`
   - Signal scanner and visual MA/price inspection.
   - Visual only; it is not a replacement for cost-aware backtesting.

6. `06_ftmo_vs_standard.ipynb`
   - Compares the same symbol/timeframe under `standard` and `ftmo` account
     modes.

## Research Rules

- Signal logic must come from `strategies.ma_cross.logic`.
- Strategy defaults and search space must come from `strategies.ma_cross.config`.
- Execution must go through `shared.execution_market` via
  `strategies.ma_cross.symbol.backtest`.
- Treat results as research until data quality, broker specs, OOS,
  walk-forward, and Monte Carlo have been reviewed.
