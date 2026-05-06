# AI Trend Strategy Architecture

Scope: dashboard signal strategy for symbols with available H3 and M45 data.

```text
Selected symbol H3 candles
  -> AI Trend Navigator / KNN
  -> h3_bias: bullish when KNN line is green, bearish when KNN line is red
  -> no automatic markers on the H3 chart

Selected symbol M45 candles
  -> EMA 13 / EMA 34
  -> optional Dow wave pivots: HH, HL, LH, LL
  -> merge latest closed H3 bias by M45 open time
  -> one BUY/SELL marker at the first M45 bar in each H3 trend segment
     where EMA 13/34 are already aligned with the H3 bias

Dashboard
  -> top chart: H3 candles + KNN classifier + average KNN
  -> lower chart: M45 candles + EMA 13 + EMA 34 + Dow wave + BUY/SELL markers
  -> click any H3 bar to draw a vertical mark and focus the matching M45 window
  -> double-click the H3 chart to clear the manual mark
```

The merge uses H3 close time `<=` M45 bar open time. The final M45 candle inside
the just-closed H3 candle is not eligible for a new entry signal, because its
chart marker would appear before the H3 confirmation.

Linked marking uses:

```text
H3 clicked time     = H3 bar open time
H3 confirmation     = H3 close time
M45 marker focus    = first M45 bar with open time >= H3 close time
M45 visible range   = M45_CONTEXT_BARS around the marker, default 45 bars
                      with about 1/3 before and 2/3 after the marker
```

Phase 1 intentionally does not calculate entry, stop-loss, or take-profit
levels. Those columns remain empty to keep the common strategy contract stable.

Signal rule:

```text
For each continuous H3 green-line segment:
  mark the first M45 bar with open time >= H3 close where EMA13 > EMA34.

For each continuous H3 red-line segment:
  mark the first M45 bar with open time >= H3 close where EMA13 < EMA34.

If M45 never aligns before H3 bias changes, no signal is produced.
```

Dow wave overlay:

```text
DOW_PIVOT_LEFT / DOW_PIVOT_RIGHT define swing high/low confirmation; default 3/5.
DOW_MIN_ATR_MULT can filter out small swings; default 0.5 means half an ATR.
SHOW_M45_DOW toggles the thin dashed zigzag wave.
SHOW_M45_DOW_LABELS toggles HH/HL/LH/LL labels; default is off.

Dow wave is visual-only in this phase and does not change BUY/SELL logic.
```

Telegram alerts use the same contract:

```text
H3 trend-change alert:
  sent after a closed H3 bar changes KNN bias to bullish or bearish.

M45 entry alert:
  sent after the first closed M45 bar that starts at or after the H3 close
  aligns with the H3 bias.
```
