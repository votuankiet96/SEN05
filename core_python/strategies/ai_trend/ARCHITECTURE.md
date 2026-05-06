# AI Trend Strategy Architecture

Scope: dashboard signal strategy for symbols with available trend and entry data.
Default dashboard pairing is H3/M45. The dashboard allows Trend TF H1-H4 and
Entry TF M45 or lower supported intraday frames.

```text
Selected symbol Trend TF candles
  -> AI Trend Navigator / KNN
  -> trend bias: bullish when KNN line is green, bearish when KNN line is red
  -> no automatic markers on the trend chart

Selected symbol Entry TF candles
  -> EMA 13 / EMA 34
  -> optional Dow wave pivots: HH, HL, LH, LL
  -> merge latest closed trend bias by entry open time
  -> one BUY/SELL marker at the first entry bar in each trend segment
     where EMA 13/34 are already aligned with the trend bias

Dashboard
  -> top chart: Trend TF candles + KNN classifier + average KNN
  -> lower chart: Entry TF candles + EMA 13 + EMA 34 + Dow wave + BUY/SELL markers
  -> click any trend bar to draw a vertical mark and focus the matching entry window
  -> double-click the trend chart to clear the manual mark
```

The merge uses trend close time `<=` entry bar open time. The final entry candle
inside the just-closed trend candle is not eligible for a new entry signal,
because its chart marker would appear before the trend confirmation.

Linked marking uses:

```text
Trend clicked time  = trend bar open time
Trend confirmation  = trend bar close time
Entry marker focus  = first entry bar with open time >= trend close time
Entry visible range = M45_CONTEXT_BARS around the marker, default 45 bars
                      with about 1/3 before and 2/3 after the marker
```

Phase 1 intentionally does not calculate entry, stop-loss, or take-profit
levels. Those columns remain empty to keep the common strategy contract stable.

Signal rule:

```text
For each continuous trend green-line segment:
  mark the first entry bar with open time >= trend close where EMA13 > EMA34.

For each continuous trend red-line segment:
  mark the first entry bar with open time >= trend close where EMA13 < EMA34.

If the entry TF never aligns before trend bias changes, no signal is produced.
```

Dow wave overlay:

```text
DOW_PIVOT_LEFT / DOW_PIVOT_RIGHT define swing high/low confirmation; default 3/5.
DOW_MIN_ATR_MULT can filter out small swings; default 0.5 means half an ATR.
SHOW_M45_DOW toggles the thin dashed zigzag wave.
SHOW_M45_DOW_LABELS toggles HH/HL/LH/LL labels; default is off.

Dow wave is visual-only in this phase and does not change BUY/SELL logic.
```

Telegram alerts currently use the production H3/M45 contract:

```text
H3 trend-change alert:
  sent after a closed H3 bar changes KNN bias to bullish or bearish.

M45 entry alert:
  sent after the first closed M45 bar that starts at or after the H3 close
  aligns with the H3 bias.
```
