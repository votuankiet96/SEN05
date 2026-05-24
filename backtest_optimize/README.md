# backtest_optimize

`backtest_optimize` is a strategy-neutral execution logic lab.

It is not a market-realistic backtest engine. The current data does not include
tick order, true variable spread, liquidity, partial fills, or cTrader execution
state. Results from this package should be used to decide whether an entry,
SL/TP, sizing, and position-management design is worth validating in cTrader.

## Scope

This package may read existing project data, but it must not modify other
folders.

Read-only inputs:

- `raw_signals/`: signal CSV files produced by the existing system.
- `core_python.data.loader`: optional OHLCV adapter, read-only.

Out of scope:

- Signal generation.
- Indicator calculation for creating new signals.
- Market-state classification.
- Live trading.
- Replacing cTrader backtests.

## Core Assumptions

- Bar timestamps are UTC-naive bar-open timestamps, matching
  `core_python.data.loader`.
- Signals are decisions made on bar `i`.
- Entry is simulated at the open of bar `i+1`.
- If a bar touches both SL and TP, the default ambiguity policy is
  `conservative`.
- For every bar, existing clusters are considered closed/updated before a new
  signal on the same bar is evaluated.
- Lot sizing rounds down to the configured lot step. If the rounded lot is below
  the configured minimum lot, the leg or cluster is skipped.
- Costs are explicit inputs. Commission defaults to zero but is part of every
  run config and snapshot.

## Folder Layout

```text
backtest_optimize/
  README.md
  contracts.py

  io/
    signal_loader.py
    market_data.py

  execution/
    sl_calculator.py
    tp_calculator.py
    cost_model.py
    sizing.py
    position_manager.py
    engine.py

  analysis/
    metrics.py
    optimize.py
    walkforward.py
    monte_carlo.py
    versioning.py

  notebooks/
  configs/
  outputs/
```

## Metrics Philosophy

Primary metrics are cluster-level and R-based:

- R expectancy per accepted cluster.
- R distribution.
- TP/SL hit behavior.
- `bar_mae_r` and `bar_mfe_r` as bar-level approximations.
- Ambiguity rate.
- Conflict skip rate.
- Holding bars.
- Stability across parameter neighborhoods and walk-forward windows.

Absolute net profit, profit factor, and market-realistic maximum drawdown are
not primary decision metrics in this package.

## Reproducibility

Every saved run should capture:

- Full config.
- Signal file path and MD5 hash.
- Market data source identifier.
- Ambiguity policy.
- Cost model.
- Result summary.
- Git commit hash when available.
- Timestamp.
