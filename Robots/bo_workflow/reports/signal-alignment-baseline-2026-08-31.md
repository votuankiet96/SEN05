# Signal-alignment baseline — 2026-08-31

Baseline captured before adding exact-first/missing-bar fallback scheduling to
`Combo.cs` and `MA Cross.cs`. The cTrader `Backtesting` folders are overwritten
by later runs, so each baseline was copied to `ArchivedRuns` together with the
exact source file used to build it.

## Combo — US30.cash/H4

- Archive: `C:\Users\Administrator\Documents\cAlgo\Data\cBots\Combo\1bae1864-5246-4220-b6c4-e8fd3dbaae4e-Default\ArchivedRuns\US30_H4_PreSignalAlignment_20260831-0706`
- Source snapshot: `Combo.before.cs`
- Source SHA-256: `645BAE5BE9AAD03FD6C9E29FF56FC8CC9A1EC62AF3900F360FFF4E83B1222237`
- Raw-events SHA-256: `DBEFB462409D9B8A543F67E4AB5F90C8ED4E02BC613373757C7E45460EDCF901`
- Period/data: 2024-01-01 through 2026-08-26, server tick data, UTC, initial balance $10,000, leverage 1:30.
- Inputs: `US30.cash/H4`, Combo US30/H4 full-history CSV, KSL=Fib0618, KTP=Fib1618, risk=1% balance, all FTMO guards disabled.
- Signal/order lifecycle: 214 in-range CSV signals; 176 exact cTrader H4 matches; 176 pending orders accepted; 136 filled; 39 cancelled after three bars; 1 still pending at test end; 93 SL; 43 TP.
- Result: ending balance $10,834.30; net +$834.30; ROI +8.34%; profit factor 1.09; 136 trades; max balance DD 16.056563%; max equity DD 18.001940%.

## MA Cross — US30.cash/M30

- Archive: `C:\Users\Administrator\Documents\cAlgo\Data\cBots\MA Cross\a08e1adc-bff4-4dc4-8156-9993c09b0ecb-Default\ArchivedRuns\US30_M30_PreSignalAlignment_20260831-0705`
- Source snapshot: `MACross.before.cs`
- Source SHA-256: `B7CF2A3233A82181E67951148F7D7D88D67B05E0ABCCAA74F8144AB71B5092FB`
- Raw-events SHA-256: `9BD591B40335F7309ABC1EDC50336287BF3AD994AEFC401DE261BE571F61E963`
- Period/data: 2024-01-01 through 2026-08-30, server tick data, UTC, initial balance $10,000, leverage 1:30.
- Inputs: `US30.cash/M30`, MA Cross US30/M30 full-history CSV, KSL=Fib0618, KTP=Fib1618, risk=1% balance, all FTMO guards disabled.
- Signal/order lifecycle: 911 in-range CSV signals; 897 exact cTrader M30 matches and market-order attempts; 503 accepted; 394 rejected with `NoMoney`/`NOT_ENOUGH_MARGIN_BALANCE`; 14 had no exact cTrader bar; 366 SL; 136 TP; 1 position closed at test end.
- Result: ending balance $7,996.48; net -$2,003.52; ROI -20.04%; profit factor 0.94; 503 trades; max balance DD 35.726369%; max equity DD 36.468021%.

## Signal-alignment source used for post-build validation

- `Combo.cs` SHA-256: `BC52B7E119292733DC6380E05505C65FCE6E8C47EF63F771E0B30298299E19A6`
- `MA Cross.cs` SHA-256: `3491C4380421510ABF9938889CA81D68250BEE61AE1F975A15C803C963C3B60F`
- Both bots preserve exact `bartime` matching and add an optional, exact-first fallback only when that UTC `bartime` does not exist in the broker's chart bars.
- The new `Enable Missing-Bar Fallback` parameter defaults to `true`; setting it to `false` restores exact-only scheduling without removing code.
- The user built both hashes in cTrader IDE and completed the two post-change backtests documented below.

## Required before/after comparison

For each new run, compare at least: in-range CSV signals, exact matches,
fallback-aligned signals, order attempts, broker accepts/rejects and error type,
fills, expirations/cancellations, SL/TP/forced closes, entry direction/price,
SL/TP distance, requested risk, net profit, ROI, profit factor, trades and both
balance/equity drawdown. A performance change alone is not enough to validate
the scheduler; every newly recovered signal must be traceable from CSV through
the cTrader log and event lifecycle.

## Post-build validation — US30 comparison

Both candidate sources were built by the user in cTrader IDE and backtested
with the same symbol, timeframe, CSV, KSL/KTP, 1% balance risk, testing period,
and disabled FTMO guards as their respective baselines. The only added input
was `Enable Missing-Bar Fallback=true`.

### Scheduler and order lifecycle

| Strategy | Before processed | After exact | After fallback | Attempts after | Accepted after | Filled/positions after | Rejected/cancelled after |
|---|---:|---:|---:|---:|---:|---:|---:|
| Combo US30/H4 | 176 | 176 | 38 | 214 | 214 pending orders | 164 fills | 49 cancelled; 1 pending at end |
| MA Cross US30/M30 | 897 | 897 | 14 | 911 | 509 market positions | 509 positions | 402 `NoMoney` rejects |

There was exactly one attempt per processed CSV `bartime`: 214/214 unique for
Combo and 911/911 unique for MA Cross. Direct CSV-to-log reconciliation found
zero missing rows, zero direction mismatches, and zero SL/TP mismatches. Combo
also had zero CSV-entry-price mismatches.

The old exact path was preserved at the lifecycle level:

- Combo exact subset remained 176 pending orders, 136 fills, 39 cancellations,
  one pending order at the end, 93 SL and 43 TP.
- MA Cross exact subset remained 897 attempts, 503 accepts, 394 `NoMoney`
  rejects, 366 SL, 136 TP and one end-of-test close.

All differences in order counts therefore trace to the newly recovered rows:

- Combo fallback: 38 pending orders; 28 filled and 10 cancelled; six TP and
  22 SL. Direct fallback net result was -$704.36 including -$52.54 swaps.
- MA Cross fallback: 14 market attempts; six accepted and eight rejected with
  `NoMoney`; one TP and five SL. Direct fallback net result was -$212.55.

Fallback execution also matched the intended first-tradable-tick rule. Combo
executed 33/38 rows at the nominal source close and five rows five minutes
later. MA Cross delays were 5–275 minutes because no FTMO tick existed at the
source nominal close; seven of 14 were five minutes late. No fixed UTC/DST
offset was applied.

### Account results

| Metric | Combo before | Combo after | MA before | MA after |
|---|---:|---:|---:|---:|
| Net profit | +$834.30 | +$36.76 | -$2,003.52 | -$2,168.86 |
| ROI | +8.34% | +0.37% | -20.04% | -21.69% |
| Profit factor | 1.09 | 1.00 | 0.94 | 0.93 |
| Trades | 136 | 164 | 503 | 509 |
| Winning / losing trades | 43 / 93 | 49 / 115 | 138 / 365 | 139 / 370 |
| Max balance drawdown | 16.056563% | 24.978789% | 35.726369% | 33.767355% |
| Max equity drawdown | 18.001940% | 26.116860% | 36.468021% | 34.521820% |

Because sizing is recalculated from current balance, fallback results alter the
volume of later exact trades. For Combo, the total change was -$797.54:
-$704.36 came directly from fallback trades and -$93.18 from the resulting
balance/volume path of the unchanged exact subset. For MA Cross, fallback
trades contributed -$212.55 while the changed balance/volume path improved the
exact subset by $47.21, giving a total change of -$165.34. Drawdown changes
must therefore not be interpreted as an isolated scheduler performance metric.

### Validation conclusion and archives

The exact-first/missing-bar fallback scheduler operated as designed in both
runs: it recovered every in-period missing-bar signal once, preserved the old
exact lifecycle, and did not alter direction, Combo entry, or static SL/TP.
The recovered rows were unprofitable in these samples, which is a strategy/data
result rather than evidence of a time-alignment defect.

- Combo post-run archive: `C:\Users\Administrator\Documents\cAlgo\Data\cBots\Combo\1bae1864-5246-4220-b6c4-e8fd3dbaae4e-Default\ArchivedRuns\US30_H4_PostSignalAlignment_20260831-2346`
- MA Cross post-run archive: `C:\Users\Administrator\Documents\cAlgo\Data\cBots\MA Cross\a08e1adc-bff4-4dc4-8156-9993c09b0ecb-Default\ArchivedRuns\US30_M30_PostSignalAlignment_20260831-2353`

## Additional MA Cross baseline — BTCUSD/M30

- Archive: `C:\Users\Administrator\Documents\cAlgo\Data\cBots\MA Cross\fe2b7666-43b9-458f-b715-0618994986ec-Default\ArchivedRuns\BTCUSD_M30_PreSignalAlignment_20260831-1932`
- Period/data: 2024-01-01 through 2026-08-30, server tick data, UTC, initial balance $10,000, account leverage 1:30.
- Inputs: BTCUSD/M30 CSV, KSL=KTP=Fib0618, risk=1% balance, all FTMO guards disabled.
- CSV/run reconciliation: 965 valid rows total; 140 before the test; 825 in the test period; 793 exact cTrader M30 matches; 32 missing cTrader bars (27 Saturday and 5 Sunday).
- Execution: 793 processed signals; 786 market orders rejected with `NOT_ENOUGH_MARGIN_BALANCE`; one signal calculated below the 0.01 BTC minimum; only six orders were accepted, producing three TP and three SL.
- Result: ending balance $9,953.33; net -$46.27; ROI -0.46%; profit factor 0.85; six trades; max balance DD 1.1301%; max equity DD 2.10021%.
- Important: this run started before the fallback source edit and used the legacy exact-only compiled `.algo`. Its archive includes that compiled artifact and the matching pre-edit source snapshot.
