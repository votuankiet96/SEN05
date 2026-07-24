# DP Program Engineering Decisions

This document keeps durable engineering decisions that should survive beyond a
single runtime snapshot. It does not store audit transcripts, row counts, PIDs
or temporary incident timelines.

## SQL Server Is The Durable Source

`DWH.Fact_OHLCV` in SQL Server is the approved source for trading strategies,
recovery, reconciliation and audit. Live and historical writes flow through SQL
staging and `DWH.usp_LoadDirect`.

Implications:

- Redis/OG candle snapshots are optional best-effort handoff data.
- Redis Stream continuity is not proof that no candle was missed.
- On Redis recovery, DP Program reseeds bounded snapshots from SQL.
- Redis-only storage mode is rejected by both live and historical engines.

## Live Universe Is Fixed By Reviewed Code

The approved live universe is:

- asset types: `Indice`, `Metal`, `Crypto`;
- expected live symbols: `11`;
- direct timeframes: `15`;
- total live symbol/timeframe sessions: `165`.

The full instrument universe remains 37 symbols. FOREX is historical-only.
Changes to this contract require operator approval and test evidence.

## Scheduled Task Is The Current Wrapper

The VM-DP6 wrapper is Scheduled Task `\SEN05\SEN05 DP Program 24x7`.

The repository still contains legacy Windows Service/NSSM scripts under
`scripts/windows_service/`, but they are not part of the current production
deployment model. Do not run or revive them without a separate approved wrapper
migration.

## Runtime Snapshots Are Evidence, Not Architecture

PIDs, row counts, disk free, latest Fact watermark, log sizes, health status and
Scheduled Task state are timestamped evidence. They may be reported in audit or
handoff notes, but canonical architecture docs should describe how to verify
them rather than hard-code their values.

## Configuration Boundary

Configuration is split by ownership:

- `settings/system.py`: reviewed product/data contract.
- `settings/instruments.py`: symbols, timeframes and market metadata.
- `settings/internal.py`: fixed runtime mechanics.
- `settings/operational.py`: operator-facing env surface and validation.
- `config/dp_provider.env.example`: public template.
- `config/dp_provider.env`: private deployment file, never committed.

Unknown, duplicate or invalid operator env keys should fail before an engine
starts.

## Git History Owns Superseded Reports

Audit reports, refactor proposals, discussion notes and implementation reports
are useful while decisions are being made. Once their valid claims are moved
into canonical docs and the files have no live references, Git history is the
archive. Do not create an `archive/` folder just to preserve superseded
documents.

## Validation Gates For Documentation Changes

Documentation-only changes should not restart the engine. They should still run:

```powershell
python -m pytest test/
python -m core_engine settings --json
python -m core_engine doctor --json
python -m core_engine data-health --json
```

Use `data-health` warnings as operational findings, not as a reason to block a
documentation commit by default.
