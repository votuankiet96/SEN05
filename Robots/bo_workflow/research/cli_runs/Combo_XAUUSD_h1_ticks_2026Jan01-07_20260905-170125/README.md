# Combo / XAUUSD / H1 — tick backtest, 01–07 January 2026

Completed successfully on VM-BO20, 2026-09-05 local time, using standalone cTrader CLI 5.9.0.38 and the existing Combo.algo. No bot source, compiled algo, GUI settings or installed software was changed.

## Inputs

- Broker/account: FTMO Platform / 7563609; starting capital: USD 10,000.
- Data: `Ticks`; report confirms `tickDataFromServer`.
- Signal: `Z:\Desktop\og_program\runtime\exports\combo_GOLD_H1_full_history_signals.csv`.
- KSL: Fib1000 (1.0 × ATR); KTP: Fib2618 (2.618 × ATR).
- Risk: 1% balance; maximum margin: 50% equity. Position-management switches: disabled, matching the current test parameters.
- Commission: CLI default 0, automatic commission disabled; report includes USD -0.52 swaps. These results are an execution check, not a full-cost performance estimate.
- Exact CLI arguments, parameter file and input hashes are saved alongside this document. The password file was referenced by path only; its contents were never read or copied by the assistant.

## Result

- Elapsed time including polling and log-flush wait: 36.3 seconds.
- Five scheduled signals; five stop orders placed and filled; zero failed orders.
- CSV direction and entry matched all five order-creation events. SL matched entry ± ATR; TP matched entry ± 2.618 × ATR within symbol price rounding (0.005).
- Each order was placed within one second after its signal's H1 AvailableTime. Fill prices differ from requested stop prices; both are retained in `signal-trace.csv`.
- One reversal closed the old Buy before the new Sell filled. No overlapping positions in the trade history.
- Exit events: one reversal close, three stop losses, one take profit.
- No margin caps or blocks; no pending-order expiry.
- Five trades: one winner and four losers. Net P/L: USD -68.24; ending balance: USD 9,931.76; profit factor: 0.76; maximum equity drawdown: 3.050337%.
- Bot's first timestamp: 2026-01-01 23:05:00.002 UTC; final timestamp: 2026-01-07 23:59:56.002 UTC.

## CLI end-date behavior verified in this session

The preliminary run in `../Combo_XAUUSD_h1_ticks_2026week1_20260905-164918` passed `--end=08/01/2026 00:00`. Although report metadata described a 7d period ending at midnight, actual bot logs and trades extended through 2026-01-08 23:59:57.804.

This final run uses `--end=07/01/2026 23:59`. Its report stores endDate as January 7 midnight and labels the duration `6d`, but the bot log confirms processing through the end of January 7. The two runs demonstrate inclusive end-calendar-day behavior and loss of time-of-day in report metadata for this CLI path. The final run is the requested seven calendar dates, January 1–7. The CSV row at January 7 23:00 becomes available January 8 00:00 and is correctly outside this run.

## Artifacts

- `report.html`: native readable cTrader report.
- `report.json`: raw CLI report.
- `events.json`: native order, fill and exit events.
- `bot-log.txt`: native bot log; `log.txt`: CLI console log.
- `signal-trace.csv`: raw signal fields, expected protection, actual order/fill values and validation for all five signals.
- `run.ps1`: reproducible async runner; rerun only in a fresh directory to preserve these results.

Native results were copied from `C:\Users\Administrator\Documents\cAlgo\Data\cBots\Combo\1850549f-1889-4bbe-97a4-d42a5b73b159\Backtesting` after completion. No gold/XAUUSD backtest parameters or matching log were found in the currently retained Combo `*-Default/Backtesting` GUI folders, so the previous GUI failure remains undiagnosed. This successful CLI run confirms that the current algo, signal file, account access and tick-data path work for this period.
