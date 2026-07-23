# DP Program Logging Architecture

## Goals

The logging system has five operational goals:

1. Record the service continuously without slowing the data engines.
2. Reconstruct one live batch, historical run, failure or recovery by reference.
3. Stay readable for an operator without requiring Python knowledge.
4. Support deterministic filtering and review by Codex/Claude.
5. Keep the physical layout small and predictable.

Log messages, fields and command output are English and ASCII-safe. UTF-8 is
used on disk; untrusted line breaks and pipe characters are normalized so one
event always occupies one physical line.

## Physical layout

Only four active text logs exist:

| File | Owner and content |
|---|---|
| `runtime/logs/live.log` | Live WebSocket batches, validation, SQL/Redis delivery and live recovery |
| `runtime/logs/historical.log` | Historical pulls, gap repair, validation and historical SQL delivery |
| `runtime/logs/system.log` | Supervisor, scheduler, locks, process lifecycle and recovered crash evidence |
| `runtime/logs/alerts.log` | Mirror of every WARNING/ERROR/CRITICAL event and notification delivery state |

Rotated files are closed, gzip-compressed and moved to
`runtime/logs/archive/YYYY-MM-DD/`. Runtime state JSON, SQLite outboxes and
spool databases are not log files and remain under `runtime/run`,
`runtime/cache` and `runtime/spool`.

## Event pipeline

```text
domain logger
  -> level filter
  -> context and stable event fields
  -> bounded per-process queue
  -> one writer thread
  -> short cross-process lock
  -> canonical source log
  -> WARNING+ mirror to alerts.log
  -> CRITICAL durable SQLite outbox
  -> asynchronous Discord delivery
```

DEBUG/INFO/WARNING/ERROR normally use the bounded queue. A CRITICAL event is
different: its canonical line and SQLite outbox row are persisted before the
logging call returns. Network delivery never blocks the engine.

When the queue is full, the caller writes the record directly under the same
lock instead of dropping it. If canonical writing fails, the event goes to
`runtime/run/log_emergency/<role>.<pid>.log`; health fails closed while a
non-empty emergency file exists.

## Line format

```text
2026-07-23 03:32:23.900 UTC | INFO | DATABASE | COMPLETE | Main data store updated | OK | L-44 | {"event":"warehouse.fact.committed",...}
```

The columns are:

| Column | Operator meaning |
|---|---|
| UTC time | When the event happened |
| Level | DEBUG, INFO, WARNING, ERROR or CRITICAL |
| Area | LIVE, HISTORICAL, DATABASE, AUTH, SYSTEM, and so on |
| Stage | START, PROGRESS, COMPLETE, RECOVERY, FAILED, STOP or CRITICAL |
| Message | Short plain-English description |
| Result | OK, MONITORING, FAILED, ACTION REQUIRED, and so on |
| Reference | Batch/run/job/correlation identifier, or `-` |
| JSON | Stable fields for exact filtering and automated review |

The formatter centrally redacts webhook URLs, bearer credentials, tokens,
cookies, passwords and secrets. Safe state words such as `authenticated`,
`present` and `updated` remain visible.

## Operator commands

```powershell
python -m core_engine logs status
python -m core_engine logs watch
python -m core_engine logs find --since 2h --level WARNING
python -m core_engine logs trace --correlation-id <id>
python -m core_engine logs risks --since 24h
```

`logs risks` reports evidence with physical file and line number for recorded
failures, repeated warnings, incomplete operations, delivery count mismatch and
silent/missing streams. Mirrored alert records are deduplicated by `event_id`.

## Retention and health

- Rotation occurs at UTC day change or the configured `LOG_MAX_FILE_MB`.
- Default archive retention is `BACKEND_LOG_RETENTION_DAYS=30`.
- `LOG_DISK_BUDGET_MB` bounds current logs plus archives; cleanup removes only
  closed archives, oldest first.
- Current canonical logs and spool/outbox data are never deleted by retention.
- `doctor` validates active registries, canonical files, append access, size
  bounds, early crash capture, retention errors and emergency fallbacks.

## Source ownership

All production logging infrastructure is contained in six `util/logkit` files:

```text
__init__.py  bootstrap.py  core.py  formatter.py  query.py  sink.py
```

Alert delivery is contained in four `util/notify` files:

```text
__init__.py  critical_outbox.py  discord.py  transport.py
```

No domain module may create a `FileHandler`, rotate a log or append directly to
one of the four canonical logs.
