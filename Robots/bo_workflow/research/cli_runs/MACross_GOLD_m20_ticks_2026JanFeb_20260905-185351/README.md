# MA Cross / XAUUSD / M20 -- tick backtest 01/01-28/02/2026

Standalone cTrader CLI 5.9.0.38, FTMO Platform / 7563609, USD 10,000, --data-mode=Ticks.
No source/algo/GUI change. Part of the 2026-09-05 fidelity batch
(see research/reports/signal-trace-batch-2026-09-05-summary.md).

## Input
- Signal CSV cols: bartime,atr,signal  (SHA-256 in input.json).
- KslLevel=2 -> SL = 1.0 x ATR ; KtpLevel=4 -> TP = 2.618 x ATR ; RiskPercent 1% ; MaxMarginPercent 50% Equity ; 3 FTMO guards OFF.
- Execution: Market order, fills at first available tick after bartime+timeframe.

## Performance (report.json -- reference only, single 2-month window)
- Net P/L: -208.5  (ROI -2.08%) ; Profit factor 0.96
- Closed trades: 76  (20 win / 56 loss) ; elapsed ~77s

## Signal -> execution reconciliation (log <-> events.json <-> OnStop summary: 100% match)
- Signals in window: 77 ; placed OK: 76 ; reject/fail: 0
- same-direction-skipped = 1 ; reversed = 3 ; margin-capped = 5 ; margin-blocked = 0
- Outcome breakdown: Placed=71 Reversal+MarginCapped=3 MarginCapped=2 SkippedSameDirection=1
- SL/TP of every placed order matches Entry +/- {1.0|2.618} x ATR vs bot-logged values. No wrong direction/entry.

## Artifacts
report.json / report.html / events.json / log.txt / bot-log.txt / params.cbotset /
gui-instance-parameters.cbotset / run.ps1 / arguments.json / input.json / run-summary.json.
Native copy from GUI instance: 6b085c35-120c-494f-ad84-741d0ee885b6.
Signal-by-signal file: research/reports/macross-GOLD-m20-jan-feb2026-signal-trace-2026-09-05.csv
