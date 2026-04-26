# AI Trend / KNN + EMA Pullback Strategy

Experimental trend-following strategy:

1. `H3` AI Trend / KNN defines directional bias.
2. `M30` EMA15/EMA30 defines the executable pullback/reclaim setup.
3. Shared execution places pending orders from the signal bar high/low plus the
   symbol-specific breakout buffer `x`.

This is a new strategy branch, not a small Combo v2 parameter tweak.

## Signal Rules

Long bias:

- Closed H3 AI KNN line is above its average line.
- H3 AI KNN line slope is up.

Short bias:

- Closed H3 AI KNN line is below its average line.
- H3 AI KNN line slope is down.

Long M30 trigger:

- H3 bias is long.
- EMA15 > EMA30.
- EMA30 slope is up, if `REQUIRE_EMA_SLOPE=True`.
- Price recently pulled back to EMA15/EMA30.
- Current M30 candle reclaims EMA15 and closes bullish.
- Distance from EMA30 is not too stretched.
- Estimated R:R passes `MIN_RR`.

Short trigger is symmetric.

## Lookahead Handling

H3 features are shifted by one H3 bar duration before they are merged onto M30.
For example, an H3 bar that opens at `09:00` is only visible to M30 logic from
`12:00` onward. This avoids using an unfinished higher-timeframe candle.

## Important Files

- `config.py`: strategy defaults and symbol/cost/account adapters.
- `logic.py`: indicator build and signal rules.
- `backtest.py`: DB loaders and shared execution entry points.

## Example

Run from a context where `core_python` is on `PYTHONPATH`:

```python
from strategies.ai_trend.backtest import run_symbol_backtest

result = run_symbol_backtest(
    "US30",
    account_mode="standard",
    max_bars=20000,
)

print(result.metrics)
```

## Research Notes

- First version uses signal-bar high/low for SL because it reuses
  `shared.execution`. A future version can add a custom execution layer for
  swing-high/swing-low stops.
- Do not judge the strategy from chart appearance. Validate with cost-aware
  backtest, out-of-sample, walk-forward, and Monte Carlo.
- Compare against these baselines:
  - M30 EMA15/30 without H3 KNN filter.
  - H3 KNN filter with simple EMA cross entry.
  - H3 KNN filter with pullback/reclaim entry.

