# KNN Combo Strategy Architecture

`knn_combo` is a standalone visual signal strategy.

It combines:

- Higher-timeframe KNN trend bias from the AI Trend Navigator indicator.
- Lower-timeframe Combo-style raw signals from MA crossover, candle direction,
  MACD histogram, and ATR warmup.

It does not calculate entry price, stop-loss, take-profit, position sizing, or
live execution instructions.

## Time Semantics

Entry bars can only use a trend bar after that trend bar is closed:

```text
trend_close_time <= entry_bartime
```

This keeps lower-timeframe signals from seeing an unfinished H3-or-higher trend
bar.

## Signal Rule

```text
raw BUY:
  close crosses above MA
  candle is bullish
  MACD histogram > 0

raw SELL:
  close crosses below MA
  candle is bearish
  MACD histogram < 0

first qualifying entry bar after a new KNN bullish trend:
  close is above MA
  candle is bullish
  MACD histogram > 0

first qualifying entry bar after a new KNN bearish trend:
  close is below MA
  candle is bearish
  MACD histogram < 0
```

The KNN trend gate then filters raw signals:

```text
KNN bullish -> allow BUY only
KNN bearish -> allow SELL only
KNN neutral -> block by default
missing closed trend -> block
```

ATR defaults to 5 and is displayed on the entry chart. It is required for
indicator warmup consistency but is not used to calculate trade levels.
