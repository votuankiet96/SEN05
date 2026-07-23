# Logging Refactor Implementation Report

Date: 2026-07-23
Baseline: `refactor/v2-streamline` at `f26d3b6b`

## Scope

The former PID-scoped, component-specific log families were replaced by the
four-stream architecture defined in `docs/LOGGING_ARCHITECTURE.md`. This was a
structural refactor of logging and alerting; data fetch, validation, warehouse,
spool, lock-fencing and scheduling behavior were not intentionally changed.

## Implemented changes

- Replaced legacy log path constants with `LIVE_LOG`, `HISTORICAL_LOG`,
  `SYSTEM_LOG` and `ALERTS_LOG`.
- Replaced seven legacy `logkit` modules with six focused modules.
- Added one bounded queue and one writer thread per process.
- Added Windows-safe cross-process append and rotation with gzip archives.
- Added 30-day age retention and a configurable aggregate disk budget.
- Added structured operator columns plus JSON fields and correlation context.
- Mirrored WARNING and above to `alerts.log`.
- Kept CRITICAL HTTP delivery asynchronous while making the disk/outbox
  persistence point synchronous and durable.
- Routed supervisor child stderr, activity, lock, auth, warehouse, Redis,
  dashboard, live and historical events through the centralized API.
- Replaced summary JSONL logs with structured terminal events plus canonical
  runtime state JSON.
- Added early crash capture and post-crash ingestion into `system.log`.
- Added `logs status/watch/find/trace/risks` operator commands.
- Added centralized credential redaction.
- Removed all legacy logging modules and implementation-specific tests.

## Independent audit corrections

The implementation intentionally differs from the earlier seven-file proposal:

1. `auth.log` was not retained. Auth events route to the owning live,
   historical or system stream and carry `area=AUTH`.
2. Summary JSONL files were not retained. Their machine-readable content is a
   structured completion event; current state stays in
   `ws_live_state.json`/`historical_last_run.json`.
3. PID fallback logs were not retained. Writers use an OS lock and
   open-write-close calls. A true sink failure uses one explicit emergency
   path and makes health fail closed.
4. Bare loggers in live runtime, historical source fallback, data health and
   lock coordination were connected to the centralized API.
5. WARNING/ERROR alert mirrors carry a stable `event_id`, so queries do not
   count the same event twice.

## Verification gates

- Architecture test: exactly six production `logkit` modules.
- Architecture test: no domain creates `FileHandler` or writes canonical logs.
- Two-process test: 200 concurrent lines, no lost lines and no PID split.
- Rotation test: closed file moves to a gzip archive.
- Retention test: expired archives are deleted and state is recorded.
- Failure test: sink errors use a durable emergency file and never crash data code.
- Crash test: exception before ordinary logger initialization is captured.
- Durability test: CRITICAL is in SQLite when `logger.critical()` returns.
- Query test: mirrored alerts deduplicate and evidence includes file/line.
- Formatter tests: one physical line, parseable fields, context and redaction.

The final test count and VM-DP6 runtime evidence are recorded in the deployment
handoff for the commit that contains this report.
