# MA Cross 10/20 - Market Order Strategy

This strategy is a simple MA crossover system designed for M20, M30, and M45.
It is intentionally separate from Combo because Combo uses pending breakout
orders, while MA Cross uses market orders.

## Role in the System

MA Cross owns only strategy-specific behavior:

- MA10/MA20 indicator calculation.
- BUY/SELL crossover signal detection.
- Strategy defaults such as timeframe list and ATR stop multiple.
- Symbol-level backtest orchestration.

The strategy reuses shared infrastructure:

- `shared.data` for OHLCV loading.
- `shared.execution_market` for next-bar-open market execution.
- `shared.metrics` for performance metrics.
- `shared.broker` for lot rounding.
- `shared.instruments` for symbol, broker profile, and cost metadata.
- `shared.contracts` for standard result objects.

## Signal Rules

Default rules:

- Fast MA = SMA10.
- Slow MA = SMA20.
- BUY when fast MA crosses above slow MA on a closed bar.
- SELL when fast MA crosses below slow MA on a closed bar.
- Signal at bar T can only be executed at bar T+1 open.

The default MA type is SMA. EMA can be tested by passing
`indicator_overrides={"MA_TYPE": "ema"}`.

## Execution Rules

MA Cross uses `shared.execution_market.backtest_market_symbol()`.

- Entry is market at next bar open after signal confirmation.
- Entry includes spread and dynamic slippage.
- Protective SL is ATR-based by default: `2.0 * ATR14`.
- Partial TP closes 50% at `2.0 * ATR14`.
- After partial TP, SL is moved to breakeven and trailing is enabled.
- Trailing also activates if unrealized profit reaches `1.0 * ATR14`.
- Trailing follows `slow_ma` by default and only moves in the trade direction.
- Opposite signal closes/reverses at next bar open.
- If SL and TP are both hit in one OHLC bar, SL wins conservatively.
- Commission, swap, lot sizing, daily loss and max drawdown are included.

## Default Timeframes

- `M20`: computed from M5, must be checked for complete 4-bar aggregation.
- `M30`: direct TradingView timeframe.
- `M45`: direct TradingView timeframe, watch for anchor/DST issues.

## Recommended Validation

Do not trust a single beautiful backtest. Validate in this order:

1. Check M20/M30/M45 data quality.
2. Run symbol backtest on each timeframe.
3. Compare SMA vs EMA only after baseline is known.
4. Run conservative optimizer with small search space.
5. Validate out-of-sample and walk-forward.
6. Run Monte Carlo on trade PnL before considering live use.

## Example

```python
from strategies.ma_cross import run_symbol_backtest

result = run_symbol_backtest(
    "US30",
    tf="M30",
    account_mode="standard",
    max_bars=20000,
)

print(result.metrics)
```

## Chart

Run the interactive chart:

```powershell
.\.venv\Scripts\python.exe core_python\strategies\ma_cross\ma_chart.py --port 8514
```

Then open:

```text
http://127.0.0.1:8514
```

The chart shows candles, fast/slow MA, ATR, BUY/SELL markers, and estimated
next-open Entry/SL/TP levels. It is visual research only, not a replacement for
the cost-aware backtest.

## Live Readiness Warning

This strategy is not live-ready until broker specs, timeframe data quality,
out-of-sample behavior, walk-forward stability, and execution assumptions have
all been verified.
