# DP Program V3 architecture

DP Program V3 is a standalone TradingView OHLCV provider. Its runtime imports
only the `dp_program` package under `src/`; it has no code, config, path, process,
or service dependency on V2 or `core_engine`.

## Stable data contract

- 37 enabled symbols and 15 direct timeframes.
- Live: 11 Indice, Metal, and Crypto symbols; 165 pairs per cycle.
- FOREX is historical only.
- Live interval: five minutes; only closed candles are accepted.
- Staging: `SEN.TF_*`, keyed by `(SymbolID, BarTime)`.
- Fact: `DWH.Fact_OHLCV`, keyed by
  `(SymbolID, TimeframeID, BarTime)`.
- Loader: `DWH.usp_LoadDirect`, contract version 4.
- SQL Server is the durable warehouse source of truth.

## Data and control flow

```text
Windows Scheduled Task
  -> python -m dp_program run
  -> engine/runtime.py: single-instance lock, schedule, and state
     -> engine/live.py: Fact-watermark live planning and pending recovery
     -> engine/backfill.py: exact bootstrap and rolling-tail planning
  -> engine/pipeline.py: shared validation, comparison, and delivery
  -> engine/auth.py: authenticated account JWT, cookie, and headless Chromium
  -> engine/websocket.py: one bounded same-symbol multi-series WebSocket batch
  -> engine/spool.py: atomic durable candle files
  -> engine/sql_connector.py: transaction, staging merge, loader v4, Fact commit
  -> spool acknowledgement

All components -> log.py: bounded one-line logs, risk, and masking
engine/runtime.py -> dp_program/util/discord_report.py: optional lifecycle reports

Manual operator -> dp_program/util/chart/server.py
                -> engine/sql_connector.py read-only Fact query
                -> bundled offline lightweight-charts.js
```

Live and backfill share the finite `fetch_and_store()` delivery pipeline.
Their planners build bounded same-symbol groups, `websocket.py` fetches each
group over one authenticated socket, and the prefetched series then pass
serially through the shared validation/spool/SQL path. TradingView currently
rejects a second series in one chart session, so each timeframe uses its own
logical chart session on that shared socket. Authentication and the physical
WebSocket handshake still occur only once per group.

`runtime.py` is the only owner of continuous execution. The service writes a
fresh `starting` state before dependency checks, waits up to five minutes for
SQL Server during host startup, then runs a live cycle immediately and every
five minutes. Backfill work is queued at startup and at 11:00/22:00 UTC, then
processed one bounded group at a time between live cycles. A due
schedule slot is claimed and coalesced when an existing generation is still
running, so the same 555-pair generation is not duplicated. This
keeps one writer and gives live work priority. A new backfill pair starts only
when more than the configured safety guard remains before the next live cycle;
an in-flight pair is allowed to finish so its SQL transaction is never
preempted.

Each historical pair has durable policy completion in
`SEN.DP_BackfillState`. Completion timestamps older than the fixed policy epoch
remain present but are treated as pending, so no destructive state reset is
required. Bootstrap has an exact 60-day maximum bound. Non-Crypto series start
with an adaptive 75% calendar estimate; Crypto starts with the full 24x7 bound.
If the earliest returned candle has not reached the target, bounded
`request_more_data` commands extend only that series on the same socket. Every
series needs its own matching `series_completed`, older progress, and final
coverage before delivery. Provider candles are then filtered to the exact
window. The state timestamp and Fact load commit in one SQL transaction. After
completion, backfill requests only the tail from the latest Fact candle plus
three committed overlap candles. Manual `--bars` overrides never mark a
pending bootstrap complete.

Live reads all 165 Fact watermarks in one SQL query at cycle start. Each pair
uses its watermark as a durable cursor. Healthy pairs request five bars. The
15 timeframes for one symbol normally share one physical socket, reducing a
healthy cycle from 165 handshakes to 11. Startup and pending pairs calculate a
dynamic catch-up window from the Fact watermark; aggregate request limits can
split a symbol into more than one safe group. An incomplete, malformed, or
truncated response invalidates the whole transport group before spool/SQL, and
every affected pair remains pending. Coverage must reach the overlap start and
retain the existing Fact cursor. Pending order rotates deferred work ahead of
repeated failures, while a bounded cycle budget plus consecutive and total
group-failure caps prevent a provider outage from holding the service loop for
all 165 pairs. Runtime state controls recovery
priority/window size, but SQL remains the recovery source of truth. A missing
watermark or request exceeding the 20,000-bar cap fails closed.

Gap accounting is `provider timestamps - Fact timestamps`. The engine does not
construct a generic market calendar, so weekends, holidays, and provider
session closures are not false missing-candle alerts.

## Authentication

Production never uses `unauthorized_user_token`. A JWT is usable only when it
has a TradingView account identity and more than 60 seconds remaining.
Credentials are resolved in this order:

1. runtime auth cache;
2. private `Config.yaml` token/cookie;
3. HTTP refresh with session cookie;
4. persistent headless Chromium profile;
5. TradingView username/password login;
6. fresh headless Chromium login, with optional TOTP.

Refresh begins proactively before the JWT expires. The renewed JWT and cookie
are atomically stored in `runtime/cache/tradingview_auth.json`. A failed
refresh also persists its retry deadline there, so Scheduled Task restarts
cannot cause a one-minute authentication storm. All refresh paths failing
raises an authentication error; the data path does not fall back to guest
access.

## Durability and recovery

When comparison finds missing or changed Fact values, every candle in the exact
filtered provider window is written atomically under `runtime/spool/pending`
before SQL delivery. Loader v4 reads persistent staging rows from `FromTime`,
so staging only delta rows could expose stale staging values; full-window
staging is the required safety context. A no-delta window skips candle writes,
while a bootstrap may still atomically advance policy state. A file is removed
only after staging, loader, exact Fact verification, and transaction commit.
Startup and every live cycle replay pending files idempotently.

`runtime/run/engine.lock` is an OS-level exclusive lock. It prevents the
service and manual write commands from running concurrently on one host.
`runtime/run/stop.request` is intentionally durable across automatic restarts;
only an explicit operator start clears it.
`runtime/run/state.json` is the secret-free heartbeat and operational state.
`runtime/logs/dp_program.log` is size-rotated and retained in bounded backups.
Each new log line has stable `component`, `event`, `risk`, and `pid` fields.
Success is summarized at workflow level; per-pair live success is DEBUG so the
normal INFO log does not grow with 165 lines per cycle. Exceptions are bounded,
made single-line, and redacted before logging.
Runtime JSON is observability only; SQL owns bootstrap completion across
process, host, and repository-copy restarts.

## Source ownership

- `configuration.py`: only `Config.yaml` reader and owner of the fixed data/SQL
  contract plus technical protocol/runtime defaults. YAML contains only
  operator credentials, connectivity, paths, workflow switches, lookback, and
  schedules; technical keys are rejected rather than overridden.
- `engine/__init__.py`: public pair type, reviewed selection, and stable keys.
- `engine/auth.py`: TradingView account auth and Chromium headless renewal.
- `engine/websocket.py`: finite physical connection, multi-session routing,
  per-series completion, same-socket extension, parsing, and retry bounds.
- `engine/live.py`: Fact-watermark planning, same-symbol grouping, pending, and
  recovery.
- `engine/backfill.py`: exact bootstrap/rolling policy and safe historical grouping.
- `engine/pipeline.py`: shared validation, provider/Fact comparison, and delivery.
- `engine/spool.py`: durable local delivery outbox.
- `engine/sql_connector.py`: only SQL Server interface.
- `log.py`: application-wide logging owner; one-line format, risk labels, secret
  masking, UTC timestamps, and bounded file rotation.
- `engine/runtime.py`: only continuous runtime owner; production lifecycle, lock,
  priority, schedule, state, and the sole Discord reporter lifecycle hook.
- `__main__.py`: CLI composition root.
- `dp_program/util/discord_report.py`: optional bounded Discord queue, risk-to-embed
  formatting, redaction, retry, deduplication, and delivery. It has no schedule,
  data-fetch, or SQL responsibility and cannot run as a separate service.
- `dp_program/util/chart/server.py`: manual local HTTP/UI adapter. It validates operator
  input and renders committed candles using the bundled offline JavaScript
  asset; it owns no data collection or SQL statements.

The engine contains exactly nine Python source files. Cross-cutting logging
lives at the package root with the CLI and central configuration rather than
creating a tenth engine owner. Every Python file is capped at 300 lines. The
two optional utility files are isolated under `util`. Only `engine/runtime.py` imports the
Discord utility; no other engine component depends on `util`. Chart SQL remains owned by
`engine/sql_connector.py` through the dedicated parameterized read-only function.

Runtime files, browser profiles, logs, and private `Config.yaml` are ignored by
Git. `Config.example.yaml` is the sanitized handoff template; the engine never
loads it automatically.
`scripts/sql/00_run_all.sql` is the canonical idempotent warehouse installer.
It creates only the objects required by V3; superseded migrations remain in
Git history and are not shipped as runnable deployment files.
