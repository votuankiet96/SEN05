# MA Cross / US500.cash / M20 -- tick backtest 01/01-28/02/2026

Standalone cTrader CLI 5.9.0.38, FTMO Platform / 7563609, USD 10,000, --data-mode=Ticks.
No source/algo/GUI change. Part of the 2026-09-05 fidelity batch
(see research/reports/signal-trace-batch-2026-09-05-summary.md).

## Input
- Signal CSV cols: bartime,atr,signal  (SHA-256 in input.json).
- KslLevel=2 -> SL = 1.0 x ATR ; KtpLevel=4 -> TP = 2.618 x ATR ; RiskPercent 1% ; MaxMarginPercent 50% Equity ; 3 FTMO guards OFF.
- Execution: Market order, fills at first available tick after bartime+timeframe.

## Performance (report.json -- reference only, single 2-month window)
- Net P/L: -1616.86  (ROI -16.17%) ; Profit factor 0.62
- Closed trades: 82  (15 win / 67 loss) ; elapsed ~57s

## Signal -> execution reconciliation (log <-> events.json <-> OnStop summary: 100% match)
- Signals in window: 86 ; placed OK: 82 ; reject/fail: 0
- same-direction-skipped = 4 ; reversed = 6 ; margin-capped = 55 ; margin-blocked = 0
- Outcome breakdown: MarginCapped=50 Placed=26 Reversal+MarginCapped=5 SkippedSameDirection=4 Reversal+Placed=1
- SL/TP of every placed order matches Entry +/- {1.0|2.618} x ATR vs bot-logged values. No wrong direction/entry.

## Artifacts
report.json / report.html / events.json / log.txt / bot-log.txt / params.cbotset /
gui-instance-parameters.cbotset / run.ps1 / arguments.json / input.json / run-summary.json.
Native copy from GUI instance: 9acbcae3-3ed8-471b-9452-6e68f7d052c6.
Signal-by-signal file: research/reports/macross-US500-m20-jan-feb2026-signal-trace-2026-09-05.csv
