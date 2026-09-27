# Tick Program Architecture

## Purpose

tick_program backfills historical BID/ASK ticks from the cTrader Open API
into SQL Server for SEN05 AutoTrading. It has exactly one runtime mode:
`service` (24/7 scheduled backfill/repair). There is no realtime/live mode
and no dashboard — the program's only job is getting historical tick data
into `tick.<SYMBOL>` correctly and keeping it current.

## Control Flow

```text
python -m src service
  -> runtime.py            (supervisor + job schedule)
  -> backfill.py            (spawns "backfill" subprocess per batch)
       -> auth.py            (OAuth token, refreshed before each session)
       -> ctrader_client.py  (protobuf connect + auth chain + tick fetch)
       -> pipeline.py         (decode, BID/ASK merge, validation)
       -> spool.py            (durable SQLite outbox on SQL failure)
       -> sql_store.py        (dedup insert, IngestRun/IngestState)
       -> notify.py            (Discord + system log + health check)
```

Each batch's `backfill` subprocess opens one fresh cTrader connection and
exits. This is not incidental: Twisted's global reactor cannot be
restarted after `.stop()` (`ReactorNotRestartable`), so a fresh
interpreter per batch is the simplest correct way to run more than one
reactor session in a service's lifetime.

## Inputs

- `DWH.Dim_Symbol` (SQL, shared warehouse dimension table): the canonical
  symbol universe — `SymbolID`, `Symbol` (name), `AssetType`, `IsActive`.
  This is the only source of truth for a symbol's identity; this program
  never hardcodes a SymbolID or AssetType anywhere.
- `Config.yaml` (repo root, never committed — create it by hand, there is
  no template file): SQL Server, cTrader OAuth app credentials, Discord
  webhook, and backfill/service cadence. Its `symbols:` key is only an
  operator *selection* of which `DWH.Dim_Symbol.Symbol` names to backfill
  (a plain list of names) — not a data source. Every command that touches
  SQL resolves that list against `DWH.Dim_Symbol` via
  `sql_store.resolve_target_symbols()` and fails closed (hard error) if a
  requested name is missing or `IsActive=0` there, rather than silently
  using a stale or invented SymbolID. Top-level keys: `app` (schema),
  `sql_server`, `ctrader`, `discord`, `symbols`, `backfill`, `service`. See
  `src/configuration.py::load_settings` for every key it reads and
  its default if omitted.
- `runtime/cache/ctrader_ftmo_oauth.json`: OAuth token cache, refreshed
  automatically before it expires.
- `tick.SymbolMap` (SQL): resolved target symbol <-> cTrader symbol id
  mapping, written by `symbol-sync --apply`, read by every backfill run.

## Processing

- Backfill runs as exactly 3 jobs (`runtime.py`, `build_jobs`):
  - `recent-backfill` — every 30 min, last ~35 min, fixed window.
  - `daily-backfill` — hourly, last 24h, fixed window.
  - `gap-fill` — at startup and hourly. Not a fixed window: queries the
    real per-symbol watermark (`tick.IngestState.LastHistoricalTickTimeUtc`)
    and backfills from there to now, capped at `gap_fill_max_lookback_days`
    (default 30). Self-healing regardless of how long the service was
    down, instead of guessing a lookback that might be too short.
- Only one backfill job runs at a time; if several become due together,
  the widest-window one wins (`gap-fill` > `daily-backfill` > `recent-backfill`)
  and the others are marked covered once it succeeds (`_BACKFILL_JOB_PRIORITY`).
- Ticks are deduplicated by their own (`TickTimeUtc`, `Bid`, `Ask`) —
  `insert_ticks()` queries a batch's own time range for already-existing
  rows before inserting (safe even though scheduled windows deliberately
  overlap), backed by a small recent-window unique index as a last-resort
  guard against two processes racing on the same rows — see Storage below.
- If SQL Server is unreachable, ticks go to `runtime/spool/tick_overflow.db`
  (SQLite) instead of being lost; the `spool-drain` job retries them.
- Other scheduled jobs: `refresh-token` (30 min), `check` (5 min, Discord
  alert + auto-repair of dead `IngestRun` rows), `spool-drain` (10 min),
  `daily-health-summary` (23:45 UTC, unconditional status ping),
  `symbol-sync --apply` (startup + daily 00:20 UTC), `compress-ticks` and
  `refresh-dedup-window` (both daily — see Storage below).

## Storage

Every tick table (`tick.<SYMBOL>`) is a **Clustered Columnstore Index**,
not a rowstore table — every ingested tick is kept forever, nothing is
deleted for space reasons. See the header of
`scripts/sql/tickdata_setup.sql` for the full rationale and
`sql_store.py::compress_tick_table` / the `compress-ticks` job for the
periodic maintenance this needs (trickle inserts land in an uncompressed
columnstore "delta store" until forced/naturally compressed). This
replaced an earlier design (2026-08/09) that deleted settled ticks down to
zigzag price pivots to control disk use — abandoned because it silently
breaks backtest fidelity for any strategy logic finer than the pivot
threshold (e.g. a tight trailing stop), not because it didn't save space.

Dedup no longer relies on a full-history index either (2026-09c): a
unique B-tree index covering every row costs more than the columnstore
data it protects, since B-trees don't get columnstore's compression. See
the DEDUP ENFORCEMENT note in `scripts/sql/tickdata_setup.sql` and
`sql_store.py::insert_ticks`/`refresh_dedup_window` for the full design —
in short, the app checks each batch's own time range before inserting,
and only a small rolling few-days-wide index (rolled forward daily by
`refresh-dedup-window`) backs it up against a concurrency race.

## Outputs

- Tick tables: `tick.FR40` ... `tick.BTCUSD` (11 tables).
- Ingest audit trail: `tick.IngestRun` (one row per backfill session),
  `tick.IngestState` (latest tick per symbol).
- Heartbeat: `runtime/run/service_heartbeat.json`.
- Log: `runtime/logs/tick_engine.log`.

## Repo Layout

- `src/`: the 10-file engine (below).
- `Config.yaml`: real operator config (repo root, gitignored).
- `scripts/deploy_schema.py`: idempotent SQL schema deploy/health-check.
- `scripts/sql/tickdata_setup.sql`: SQL schema (tables, views, indexes).
- `scripts/install/install.ps1`: legacy Administrator installer for a
  Python-on-this-machine deploy (`python -m src service`) — ODBC driver,
  `Config.yaml` presence check, optional schema deploy, Scheduled Task
  registration. Superseded for new deploys by the .exe packaging below,
  kept for a source-only/dev-machine install.
- `scripts/windows/`: PyInstaller packaging for the standalone
  `tick_program.exe` (mirrors `dp_program_v3`'s `run_dp/dp_program.exe`
  pattern, added 2026-08-27):
  - `tick_program_entry.py`: the .exe's single entry point. No args +
    non-interactive (Task Scheduler) = run the supervisor; no args +
    real console (double-click) = interactive menu
    (`tick_program_menu.py`); a known CLI subcommand as the first arg
    (`check`, `backfill`, ...) dispatches straight to `src.__main__.main`
    -- this is also how the frozen exe re-invokes ITSELF for the existing
    subprocess-per-batch/per-job design (see `runtime.py::job_command`
    and `backfill.py::_run_batch_subprocess`, both frozen-aware); `
    --watchdog` runs one independent liveness check (heartbeat freshness +
    PID alive, no SQL/cTrader dependency) for a *second*, separate
    Scheduled Task -- closes the gap where a clean-but-nonzero service
    exit is never restarted by Task Scheduler's own crash-recovery policy.
  - `tick_program_menu.py`: the interactive console menu.
  - `tick_program_task_setup.py`: registers/removes the two Scheduled
    Tasks (`\SEN05\SEN05 Tick Program Engine` at startup + restart-on-crash,
    `\SEN05\SEN05 Tick Program Watchdog` every 5 minutes), elevating via
    UAC if needed.
  - `build_exe.ps1`: `pyinstaller --onefile` build, run from the repo
    root (`powershell -ExecutionPolicy Bypass -File
    scripts\windows\build_exe.ps1`); stages the built exe into a sibling
    `run_tick\` deployment folder (NOT part of this git repo, same as
    `dp_program_v3\run_dp\` is a sibling of `core_program\`, not nested
    inside it) alongside `Config.example.yaml`/`install.ps1`/`DEPLOY.md`/
    `sql\` — copy that whole folder to deploy elsewhere; see its
    `DEPLOY.md`. `--collect-all` is used for `ctrader_open_api`/`twisted`
    (reactor/protobuf module selection a plain import scan can miss) and
    `tzdata` (Windows has no OS-level IANA timezone database — the real
    cTrader trading-schedule check resolves `zoneinfo.ZoneInfo(...)` from
    this pip package's bundled data *files*, which a plain import scan
    would not bundle at all, silently degrading every symbol to the
    less-accurate `SESSION_RULES` fallback on a machine with no other
    Python around to notice against).
  - Rebuilding is required after any source change — the .exe is a static
    build, editing `src/` does not update it.
- `tests/`: 4 files covering merge/pagination correctness, spool durability,
  scheduler priority-gating, and notification recovery-once (plus
  `test_market_schedule.py`, added later).
- `docs/`: this file and the pre-refactor analysis (superseded, kept for history).

## File Responsibilities

`src/` is exactly 10 Python files, one responsibility each:

- `__main__.py`: CLI parsing/dispatch only.
- `configuration.py`: YAML config load, the one `Settings` object, paths, secret redaction.
- `runtime.py`: supervisor lifecycle (singleton PID, signals, heartbeat) + job schedule.
- `auth.py`: cTrader OAuth token exchange/refresh/cache.
- `ctrader_client.py`: SDK load, protobuf requests, connection + auth chain, paged tick fetch.
- `backfill.py`: symbol matching, one backfill session, batch planning + retry.
- `pipeline.py`: tick model, delta decode, BID/ASK merge, validation.
- `spool.py`: durable SQLite outbox, file-based job locks, PID helpers.
- `sql_store.py`: all SQL Server access.
- `notify.py`: Discord alerting, system log, heartbeat/progress state, health check.

`src/chart/` (added 2026-08-28) is a separate, opt-in, read-only tool — not
counted in the 10 above, same relationship `dp_program_v3`'s `util/chart/`
has to its own engine. `server.py` + a bundled `lightweight-charts.js` serve
a local http.server page plotting `tick.<SYMBOL>` Bid/Ask directly (no
candles, no aggregation) over a chosen time window. Run via `chart`
CLI subcommand, or menu option 6 once packaged as `tick_program.exe`.

## Safety Rules

- Never hardcode a SymbolID or AssetType. `Config.yaml` selects names;
  `DWH.Dim_Symbol` is always the source of identity (`sql_store.resolve_target_symbols`).
  A requested name that isn't there, or is `IsActive=0`, is a hard error —
  never a silently skipped symbol.
- Never advance `tick.IngestState` from an incomplete or failed batch.
- Never drop a tick on SQL failure — spool it, and alert once (throttled).
- Never treat a closed market session as a data gap (`notify.py`,
  `SESSION_RULES` — a fixed table derived from observed weekend gaps per
  symbol, not a learned model).
- Never modify SQL schema or credentials without explicit operator approval.
- `tickdata_setup.sql` no longer creates `SEN.ActiveTask`: it was never real
  shared SEN05 infrastructure, just a stale assumption carried over from an
  earlier draft. Checked directly against dp_program_v3 (the project that
  owns the SEN/DWH schemas) — its installer never creates that table, its
  test suite asserts it's absent, and its own docs say V3 uses an OS file
  lock instead. tick_program never read or wrote it either.
