# DP Program Logging Architecture

DP Program uses one logging system for live, historical, supervisor, warehouse,
auth, health and notification events. Domain code logs through
`core_engine.util.logkit`; it must not create its own file handlers or append
directly to log files.

## Goals

1. Keep the data path non-blocking for ordinary events.
2. Persist CRITICAL evidence before returning from the logging call.
3. Make operator review possible without Python knowledge.
4. Keep machine-readable fields available for exact filtering.
5. Keep the physical layout small and predictable.

## Active Logs

Only four active text logs are canonical:

| File | Owner and content |
|---|---|
| `runtime/logs/live.log` | Live WebSocket batches, validation, staging, ETL, Redis snapshot work and live recovery |
| `runtime/logs/historical.log` | Historical fetch, gap repair, reset, validation and warehouse delivery |
| `runtime/logs/system.log` | Supervisor, scheduler, locks, process lifecycle, terminal commands and recovered crash evidence |
| `runtime/logs/alerts.log` | WARNING/ERROR/CRITICAL mirror and notification delivery state |

Runtime state JSON, SQLite outboxes and live spool databases are not log files.
They remain under `runtime/run`, `runtime/cache` and `runtime/spool`.

## Event Pipeline

```text
domain logger
  -> log level filter
  -> context/correlation fields
  -> bounded per-process queue
  -> writer thread
  -> short cross-process append lock
  -> canonical source log
  -> WARNING+ mirror to alerts.log
  -> CRITICAL durable SQLite outbox
  -> asynchronous Discord delivery
```

DEBUG, INFO, WARNING and ERROR use the bounded queue under normal conditions.
When the queue is full, the caller writes directly under the same append lock
instead of silently dropping the event.

CRITICAL is different: the canonical line and CRITICAL outbox row are persisted
before the logging call returns. Network delivery never blocks the engine.

If canonical writing fails, the emergency path is
`runtime/run/log_emergency`. Health fails closed while unresolved emergency
evidence exists.

## Line Format

Each physical line has stable operator columns followed by JSON metadata:

```text
UTC time | LEVEL | AREA | STAGE | message | RESULT | REFERENCE | JSON
```

The formatter normalizes unsafe line breaks and pipe characters so one event is
one physical line. It redacts webhook URLs, bearer credentials, tokens,
cookies, passwords and secrets while keeping safe state words such as
`present`, `updated` and `authenticated`.

## Operator Queries

```powershell
python -m core_engine logs status
python -m core_engine logs watch
python -m core_engine logs find --since 2h --level WARNING
python -m core_engine logs trace --correlation-id <id>
python -m core_engine logs risks --since 24h
```

`logs risks` scans active and archived logs for failures, repeated warnings,
incomplete operations, delivery mismatches and silent/missing streams.

## Rotation And Retention

Rotation and retention are implemented in `util/logkit/sink.py`.

- Rotation is controlled by date and `LOG_MAX_FILE_MB`.
- Archive retention is controlled by `LOG_RETENTION_DAYS`.
- Archive disk budget is controlled by `LOG_DISK_BUDGET_MB`.
- Closed rotations are gzip-compressed under
  `runtime/logs/archive/YYYY-MM-DD/`.
- Retention removes only closed archives.
- Current logs, live spool and SQLite outboxes are never deleted by log
  retention.

`doctor --json` validates canonical sink status, append access, active
registries, emergency fallback state, retention errors and CRITICAL outbox
health.

## Alert Delivery

Ordinary Discord alerts are handled by `util.notify.discord`:

- outbound-only webhook transport;
- bounded process-local sender queue;
- delivery-time dedupe;
- retry and circuit breaker;
- non-secret status JSON under `runtime/run/notification_status`.

CRITICAL alert durability is handled by `util.notify.critical_outbox`:

- SQLite outbox under runtime cache;
- durable enqueue before return;
- background retry;
- status file `runtime/run/critical_outbox_status.json`.

Discord delivery proves alert transport only. It does not prove TradingView,
SQL or Fact delivery health.

## Source Ownership

Logging implementation:

```text
src/core_engine/util/logkit/
  __init__.py
  bootstrap.py
  core.py
  formatter.py
  query.py
  sink.py
```

Alert implementation:

```text
src/core_engine/util/notify/
  __init__.py
  critical_outbox.py
  discord.py
  transport.py
```
