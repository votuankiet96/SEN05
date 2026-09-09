# MA Cross / GER40.cash / M30 -- tick backtest 01/01-28/02/2026

Standalone cTrader CLI 5.9.0.38, FTMO Platform / 7563609, USD 10,000, --data-mode=Ticks.
No source/algo/GUI change. Part of the 2026-09-05 fidelity batch
(see research/reports/signal-trace-batch-2026-09-05-summary.md).

## Input
- Signal CSV cols: bartime,atr,signal  (SHA-256 in input.json).
- KslLevel=2 -> SL = 1.0 x ATR ; KtpLevel=4 -> TP = 2.618 x ATR ; RiskPercent 1% ; MaxMarginPercent 50% Equity ; 3 FTMO guards OFF.
- Execution: Market order, fills at first available tick after bartime+timeframe.

## Performance (report.json -- reference only, single 2-month window)
- Net P/L: -1363.41  (ROI -13.63%) ; Profit factor 0.63
- Closed trades: 56  (11 win / 45 loss) ; elapsed ~57s

## Signal -> execution reconciliation (log <-> events.json <-> OnStop summary: 100% match)
- Signals in window: 56 ; placed OK: 56 ; reject/fail: 0
- same-direction-skipped = 0 ; reversed = 3 ; margin-capped = 21 ; margin-blocked = 0
- Outcome breakdown: Placed=33 MarginCapped=20 Reversal+MarginCapped=1 Reversal+Placed=2
- SL/TP of every placed order matches Entry +/- {1.0|2.618} x ATR vs bot-logged values. No wrong direction/entry.

## Artifacts
report.json / report.html / events.json / log.txt / bot-log.txt / params.cbotset /
gui-instance-parameters.cbotset / run.ps1 / arguments.json / input.json / run-summary.json.
Native copy from GUI instance: 38d21e13-8310-4c4b-a012-2c04d4ac1abf.
Signal-by-signal file: research/reports/macross-DE40-m30-jan-feb2026-signal-trace-2026-09-05.csv
