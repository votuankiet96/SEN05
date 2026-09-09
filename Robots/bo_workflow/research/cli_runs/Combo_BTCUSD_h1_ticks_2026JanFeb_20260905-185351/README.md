# Combo / BTCUSD / H1 -- tick backtest 01/01-28/02/2026

Standalone cTrader CLI 5.9.0.38, FTMO Platform / 7563609, USD 10,000, --data-mode=Ticks.
No source/algo/GUI change. Part of the 2026-09-05 fidelity batch
(see research/reports/signal-trace-batch-2026-09-05-summary.md).

## Input
- Signal CSV cols: bartime,atr,entry,signal  (SHA-256 in input.json).
- KslLevel=2 -> SL = 1.0 x ATR ; KtpLevel=4 -> TP = 2.618 x ATR ; RiskPercent 1% ; MaxMarginPercent 50% Equity ; 3 FTMO guards OFF.
- Execution: Pending Stop order, 3-bar lifetime (expires if price never touches entry).

## Performance (report.json -- reference only, single 2-month window)
- Net P/L: -116.22  (ROI -1.16%) ; Profit factor 0.94
- Closed trades: 71  (21 win / 50 loss) ; elapsed ~118s

## Signal -> execution reconciliation (log <-> events.json <-> OnStop summary: 100% match)
- Signals in window: 113 ; placed OK: 107 ; reject/fail: 0
- pending-expired = 27 (cancelled after 3 bars, price never hit entry)
- same-direction-skipped = 6 ; reversed = 21 ; margin-capped = 104 ; margin-blocked = 0
- Outcome breakdown: MarginCapped=63 Reversal+MarginCapped=15 Reversal+MarginCapped+Expired=6 MarginCapped+Expired=20 SkippedSameDirection=6 Placed+Expired=1 Placed=2
- SL/TP of every placed order matches Entry +/- {1.0|2.618} x ATR vs bot-logged values. No wrong direction/entry.

## Artifacts
report.json / report.html / events.json / log.txt / bot-log.txt / params.cbotset /
gui-instance-parameters.cbotset / run.ps1 / arguments.json / input.json / run-summary.json.
Native copy from GUI instance: affc79ab-9f2d-41c3-b5e2-b669df120ed0.
Signal-by-signal file: research/reports/combo-BTCUSD-jan-feb2026-signal-trace-2026-09-05.csv
