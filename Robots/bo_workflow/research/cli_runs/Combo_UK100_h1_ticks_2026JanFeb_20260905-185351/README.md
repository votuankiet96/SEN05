# Combo / UK100.cash / H1 -- tick backtest 01/01-28/02/2026

Standalone cTrader CLI 5.9.0.38, FTMO Platform / 7563609, USD 10,000, --data-mode=Ticks.
No source/algo/GUI change. Part of the 2026-09-05 fidelity batch
(see research/reports/signal-trace-batch-2026-09-05-summary.md).

## Input
- Signal CSV cols: bartime,atr,entry,signal  (SHA-256 in input.json).
- KslLevel=2 -> SL = 1.0 x ATR ; KtpLevel=4 -> TP = 2.618 x ATR ; RiskPercent 1% ; MaxMarginPercent 50% Equity ; 3 FTMO guards OFF.
- Execution: Pending Stop order, 3-bar lifetime (expires if price never touches entry).

## Performance (report.json -- reference only, single 2-month window)
- Net P/L: 150.47  (ROI 1.5%) ; Profit factor 1.04
- Closed trades: 50  (15 win / 35 loss) ; elapsed ~47s

## Signal -> execution reconciliation (log <-> events.json <-> OnStop summary: 100% match)
- Signals in window: 78 ; placed OK: 72 ; reject/fail: 0
- pending-expired = 15 (cancelled after 3 bars, price never hit entry)
- same-direction-skipped = 6 ; reversed = 9 ; margin-capped = 12 ; margin-blocked = 0
- Outcome breakdown: Placed+Expired=11 Placed=40 MarginCapped=9 SkippedSameDirection=6 MarginCapped+Expired=3 Reversal+Placed=8 Reversal+Placed+Expired=1
- SL/TP of every placed order matches Entry +/- {1.0|2.618} x ATR vs bot-logged values. No wrong direction/entry.

## Artifacts
report.json / report.html / events.json / log.txt / bot-log.txt / params.cbotset /
gui-instance-parameters.cbotset / run.ps1 / arguments.json / input.json / run-summary.json.
Native copy from GUI instance: 9461d3f4-77d3-4898-9ef8-fa15177be8a6.
Signal-by-signal file: research/reports/combo-UK100-jan-feb2026-signal-trace-2026-09-05.csv
