# MA Cross / US100.cash / M10 -- tick backtest 01/01-28/02/2026

Standalone cTrader CLI 5.9.0.38, FTMO Platform / 7563609, USD 10,000, --data-mode=Ticks.
No source/algo/GUI change. Part of the 2026-09-05 fidelity batch
(see research/reports/signal-trace-batch-2026-09-05-summary.md).

## Input
- Signal CSV cols: bartime,atr,signal  (SHA-256 in input.json).
- KslLevel=2 -> SL = 1.0 x ATR ; KtpLevel=4 -> TP = 2.618 x ATR ; RiskPercent 1% ; MaxMarginPercent 50% Equity ; 3 FTMO guards OFF.
- Execution: Market order, fills at first available tick after bartime+timeframe.

## Performance (report.json -- reference only, single 2-month window)
- Net P/L: 4491.54  (ROI 44.92%) ; Profit factor 1.54
- Closed trades: 130  (50 win / 80 loss) ; elapsed ~87s

## Signal -> execution reconciliation (log <-> events.json <-> OnStop summary: 100% match)
- Signals in window: 130 ; placed OK: 130 ; reject/fail: 0
- same-direction-skipped = 0 ; reversed = 2 ; margin-capped = 84 ; margin-blocked = 0
- Outcome breakdown: MarginCapped=83 Placed=45 Reversal+Placed=1 Reversal+MarginCapped=1
- SL/TP of every placed order matches Entry +/- {1.0|2.618} x ATR vs bot-logged values. No wrong direction/entry.

## Artifacts
report.json / report.html / events.json / log.txt / bot-log.txt / params.cbotset /
gui-instance-parameters.cbotset / run.ps1 / arguments.json / input.json / run-summary.json.
Native copy from GUI instance: 1c956802-0545-438c-a0bb-901580272ac2.
Signal-by-signal file: research/reports/macross-US100-m10-jan-feb2026-signal-trace-2026-09-05.csv
