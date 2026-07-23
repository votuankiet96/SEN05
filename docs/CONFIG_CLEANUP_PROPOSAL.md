# Operator Config Surface — Audit & Cleanup Proposal

Date: 2026-07-23
Baseline: `f26d3b6b` (branch `refactor/v2-streamline`), working tree has ~20
uncommitted files from an in-progress, unrelated logging refactor
(`settings/operational.py`, `util/logkit/*`, `util/coordination/locks.py`,
`util/notify/critical_outbox.py`, `util/redis_io/candle_snapshot.py`,
`util/supervisor/*`, `core_engine/__init__.py`). Line numbers below are as of
that working tree snapshot, not the committed HEAD. **Re-verify line numbers
against current code before implementing anything here** — this document is a
proposal for review, not an already-applied change.

Status: **proposal only, nothing in this document has been applied to the
codebase.**

## 1. Purpose

`config/dp_provider.env` (the real, git-ignored operator file) and
`config/dp_provider.env.example` (the tracked template) have accumulated three
kinds of content mixed together with no clear boundary:

1. Genuine operator-tunable settings (credentials, per-deployment values,
   real operational levers).
2. Settings that read as operator-configurable but are actually deep
   implementation detail, dead, or silently overridden by internal code —
   i.e. exposed for no real reason.
3. Leftover text (headers, comments, section ordering) in the real
   `dp_provider.env` file from a prior config layout that no longer matches
   reality.

This document separates the three, with evidence, so a decision can be made
about what to keep, what to fold into code as fixed defaults, and what to
delete outright.

## 2. Method

Every field of every dataclass in `src/core_engine/settings/operational.py`
(`DatabaseSettings`, `TradingViewSettings`, `HistoricalSettings`,
`LiveSettings`, `NotificationSettings`, `CandleSnapshotSettings`,
`StorageSettings`, `LoggingSettings`, `BackendSettings`) was cross-referenced
against the rest of `src/core_engine` to confirm it is actually read
somewhere, not just declared. Three access patterns were checked, since a
naive grep for `OBJECT.field_name` alone misses two of them:

- Direct attribute access, e.g. `DB.retry_count`.
- Local aliasing, e.g. `core/live/engine.py` does
  `_live_settings = LIVE` and then `_live_settings.shutdown_poll_sec` —
  caught by grepping for `_\w*settings\w* = (DB|TRADINGVIEW|...)` first.
- Dynamic access, e.g. `util/health.py` does
  `getattr(BACKEND, "disk_warn_free_gb", 5.0)` — caught by grepping the bare
  field name across the tree, not just `OBJECT.field_name`.

A field is only classified "dead" below if none of the three patterns turned
up a consumer anywhere outside `settings/operational.py` itself.

## 3. Findings

### 3.1 Confirmed dead — zero consumers anywhere

| Field | Env var | Evidence |
|---|---|---|
| `TradingViewSettings.timezone` | `TV_WS_TIMEZONE` | Reads the same env var as `LiveSettings.timezone`, but only the `LiveSettings` copy is consumed (`core/live/fetcher.py:59`). The `TradingViewSettings` copy has no consumer anywhere in `src/`. |
| `StorageSettings.redis_init_candles` | `REDIS_INIT_CANDLES` | Docstring claims "see live/engine.py's init-candles startup step" — that step does not exist anywhere in the codebase. Field is defined and never read again. |

Recommendation: delete both fields and their env vars. No behavior change —
neither is read today.

### 3.2 Duplicate parsing — not dead, but redundant

| Env var | Issue |
|---|---|
| `BACKEND_LOG_RETENTION_DAYS` | Resolved once as `LoggingSettings.retention_days`; sink, health and supervisor now consume the same value. |

Recommendation: keep one canonical field (suggest `BackendSettings.log_retention_days`,
since supervisor/health own retention execution) and have `LoggingSettings`
reference it instead of re-parsing the env var.

### 3.3 Config-shaped but actually internal plumbing

| Env var | Issue |
|---|---|
| `DP_HISTORICAL_CANCEL_FILE` | Listed in `.env.example` under "Historical pipeline" as if operator-settable. In practice `util/supervisor/engine.py:581` unconditionally sets this env var on every historical child spawn (`env["DP_HISTORICAL_CANCEL_FILE"] = str(HISTORICAL_CANCEL_FILE)`), overwriting anything an operator put in `dp_provider.env`. Under normal supervised operation, an operator override here is silently ignored. It is genuinely used (`core/historical/runtime_support.py:56` reads it), just not as an operator-facing knob — it's an internal supervisor→child handoff channel implemented via env var. |
| `SQL_TDS_VERSION` / `tds_version` | Only read inside the `driver == "freetds"` branch of `build_conn_str()` (`settings/operational.py`). Production always uses `SQL_DRIVER=ODBC Driver 18 for SQL Server`, so this branch is never exercised today. Needs a decision, not a mechanical fix: is FreeTDS still a real deployment target, or is this a leftover from an earlier/alternate driver setup that can be removed along with the branch? |

Recommendation: remove `DP_HISTORICAL_CANCEL_FILE` from `.env.example` (keep
the internal constant/mechanism as-is, just stop presenting it as operator
config). `SQL_TDS_VERSION`/FreeTDS needs an explicit decision before any
action.

### 3.4 Legacy text/structure in the real `config/dp_provider.env` file

Not code-level findings — these are artifacts in the operator file's own
text, independent of what any variable does:

- Lines 40-43: header `# Development-only switches. Keep both empty/0 in
  operator deployments.` immediately followed by real production values
  (`HISTORICAL_PROVIDER`, `STAGING_CLEANUP_*`, `N_BARS_*`, ...) — mislabeled;
  these are not dev-only switches.
- Line 44: comment `# Advanced OHLCV tuning (migrated from legacy per-group
  config)` — refers to a config system that no longer exists; stale and
  confusing today.
- Lines 31-34: heading `# Schedule, UTC` with nothing under it — dead
  section header.
- Section order and grouping in the real file no longer match
  `dp_provider.env.example`, which has since been reorganized into clearer
  groups (App root, SQL, TradingView, Notifications, Logging, Backend
  supervisor, Historical pipeline, Live fetch, OG candle snapshot, Database
  connector). The real file was never resynced to that structure.
- `DP_CANDLE_PUBSUB_ENABLED=true` in the real file, while `.env.example`
  documents this as "keep disabled until the OG subscriber is ready and
  end-to-end testing is planned" (default `false`). Needs confirmation this
  is an intentional current state, not a leftover from testing.
- `LIVE_ASSET_TYPES` and `EXPECTED_LIVE_SYMBOLS` — the live-symbol-count
  drift guard — are not pinned explicitly in the real file at all; the
  guard currently runs entirely on the code-level default (`Indice,Metal,
  Crypto` / `11`). The code's own comments recommend confirming the count
  before changing `instruments.py`, but there is no anchor for that in the
  operator-facing file today.

## 4. Full field classification

Every field confirmed live (i.e. not in section 3.1) is classified as either
**Keep** (stays operator-facing in `dp_provider.env`) or **Move** (relocate
to a fixed value in code; operators have no realistic reason to change it
per deployment). This is a judgment call about operational relevance, not a
correctness finding — flag any row for discussion.

### SQL Server (`DatabaseSettings`)

| Env var | Recommendation | Reason |
|---|---|---|
| SQL_SERVER, SQL_DATABASE, SQL_PORT, SQL_UID, SQL_PWD | Keep | Address/credential, varies by environment |
| SQL_DRIVER, SQL_ENCRYPT, SQL_TRUST_SERVER_CERT | Keep | Connection/TLS policy depends on infrastructure |
| SQL_TDS_VERSION | Pending (3.3) | Only relevant if FreeTDS stays a supported driver |
| SQL_HEALTH_TIMEOUT_SECONDS, SQL_COMMAND_TIMEOUT_SECONDS, SQL_LOCK_TIMEOUT_MS | Keep | Safety thresholds, depend on real network/DB latency |
| DB_RETRY_COUNT, DB_RETRY_DELAY_SEC | Keep | Retry policy, real lever during DB instability |

### TradingView (`TradingViewSettings`, `timezone` excluded — dead, see 3.1)

| Env var | Recommendation | Reason |
|---|---|---|
| TV_AUTH_TOKEN, TV_COOKIE, TV_USERNAME, TV_PASSWORD, TV_2FA_SECRET, TV_CAPTCHA_API_KEY | Keep | Credentials |
| TV_CAPTCHA_SERVICE, TV_BROWSER_PROFILE_DIR, TV_AUTH_HEADLESS_FRESH_LOGIN | Keep | Vendor choice / machine path / real operating mode |
| TV_AUTH_CONNECTIVITY_PREFLIGHT | Keep | Meaningful on/off toggle |
| TV_WS_HISTORY_ENDPOINT, TV_WS_HISTORY_TIMEOUT_SEC | Keep | Endpoint/timeout may need changing if TradingView infra changes |
| TV_TOKEN_PROACTIVE_REFRESH_SEC, TV_TOKEN_PROACTIVE_RETRY_SEC, TV_AUTH_REFRESH_COOLDOWN_SEC, TV_AUTH_TRANSIENT_COOLDOWN_SEC | Move | Internal token-refresh mechanics |
| TV_WS_HISTORY_REQUEST_MORE_ROUNDS, TV_WS_HISTORY_REQUEST_MORE_BARS | Move | Internal pagination parameters |

### Historical (`HistoricalSettings`)

| Env var | Recommendation | Reason |
|---|---|---|
| HISTORICAL_PROVIDER | Keep | Architecture choice, even if single-valued today |
| HISTORICAL_DROP_OPEN_LAST_BAR, PIPELINE_HOLE_LOOKBACK_DAYS, PIPELINE_MAX_CONSECUTIVE_FAIL | Keep | Real business/safety decisions |
| PIPELINE_RETRY_DELAYS | Keep | Operational lever during prolonged TV outages |
| STAGING_CLEANUP_BATCH_ROWS, STAGING_CLEANUP_PAUSE_SEC, STAGING_CLEANUP_MAX_ROWS_PER_RUN, STAGING_CLEANUP_MAX_ROWS_PER_TABLE, STAGING_CLEANUP_MAX_SECONDS, STAGING_CLEANUP_CHECKPOINT | Keep | Safety valve to reduce DB load on demand; already clamped for operator use |
| PIPELINE_SAFETY_FACTOR, PIPELINE_MIN_PULL_BARS | Move | Internal sizing math |
| TV_WS_REPLAY_ENABLED, TV_WS_REPLAY_TFS, TV_WS_REPLAY_ENDPOINT, TV_WS_REPLAY_START_DATE | Keep | Feature scope, real business decision |
| TV_WS_REPLAY_TIMEOUT_SEC, TV_WS_REPLAY_MAX_WINDOWS_PER_PAIR | Keep | Network timeout + runaway-loop safety cap |
| TV_WS_REPLAY_WINDOW_BARS, TV_WS_REPLAY_STEP_BARS, TV_WS_REPLAY_ADVANCE_FACTOR | Move | Internal pagination math |

### Live fetch (`LiveSettings`)

| Env var | Recommendation | Reason |
|---|---|---|
| WS_LIVE_AUTO_START, LIVE_ASSET_TYPES, EXPECTED_LIVE_SYMBOLS | Keep | High-level scope/safety guard, documented in runbook |
| WS_LIVE_BATCH_INTERVAL_MIN, WS_LIVE_BATCH_FETCH_TIMEOUT_SEC | Keep | Real-time SLA, business decision |
| WS_LIVE_BATCH_GROUP_JOIN_TIMEOUT_SEC, WS_LIVE_GROUP_WEDGE_HARD_DEADLINE_BATCHES | Keep | Wedge-recycle safety mechanism, must stay operator-visible |
| WS_LIVE_BATCH_MAX_RETRIES, WS_LIVE_MAX_MISS_RETRIES, WS_LIVE_MAX_BACKLOG_BATCHES | Keep | Retry/backlog policy, incident-time lever |
| WS_LIVE_SYMBOLS_PER_CONN | Keep | Documented WS session-load control |
| WS_LIVE_N_BARS, WS_LIVE_N_BARS_BACKLOG | Keep | Directly tied to backlog-recovery behavior |
| WS_LIVE_MAX_SPOOL_ROWS, WS_LIVE_OVERFLOW_BUFFER_MAX | Keep | Data-loss safety valve during DB outage |
| WS_LIVE_ETL_DIRECT_RETRIES | Keep | Retry policy for DB blips |
| TV_WS_GUEST_POLICY, TV_WS_GUEST_PAUSE_SEC, TV_WS_RATE_LIMIT_COOLDOWN_SEC, TV_WS_FORBIDDEN_COOLDOWN_SEC | Keep | Real incident-response policy toward TradingView |
| TV_WS_CONNECTIVITY_PREFLIGHT | Keep | Meaningful toggle |
| TV_WS_TIMEZONE | Keep | Deployment parameter (currently always Etc/UTC) |
| WS_LIVE_SHUTDOWN_POLL_SEC, WS_LIVE_WS_THREAD_JOIN_GRACE_SEC | Move | Internal shutdown polling |
| WS_LIVE_RECONNECT_BASE_SEC, WS_LIVE_RECONNECT_MAX_SEC | Move | Internal backoff |
| WS_LIVE_STATE_HEARTBEAT_SEC | Move | Internal heartbeat cadence |
| TV_WS_PREFLIGHT_REQUIRE_HEADLESS | Move | Internal auth-flow detail |
| TV_WS_CONNECTIVITY_TIMEOUT_SEC, TV_WS_CONNECTIVITY_COOLDOWN_SEC | Move | Internal timeout |
| WS_LIVE_DB_QUEUE_MAXSIZE | Move | Internal buffer size |
| WS_LIVE_SESSION_THROTTLE_SEC | Move | Internal pacing |
| WS_LIVE_STATUS_INTERVAL_SEC | Move | Internal report cadence, cosmetic |
| WS_LIVE_ETL_DIRECT_RETRY_DELAY_SEC, WS_LIVE_ETL_DEFERRED_RETRY_COOLDOWN_SEC | Move | Internal backoff |

### Notifications (`NotificationSettings`)

| Env var | Recommendation | Reason |
|---|---|---|
| DISCORD_WEBHOOK_URL | Keep | Credential |
| DISCORD_DEDUPE_WINDOW_SEC | Keep | Directly affects alert-fatigue/missed-repeat tradeoff operators feel |
| DISCORD_SEND_ATTEMPTS, DISCORD_TIMEOUT_CONNECT_SEC, DISCORD_TIMEOUT_READ_SEC, DISCORD_CIRCUIT_FAILURES, DISCORD_CIRCUIT_COOLDOWN_SEC | Move | Internal HTTP/circuit-breaker tuning |

### OG Candle Snapshot (`CandleSnapshotSettings`)

| Env var | Recommendation | Reason |
|---|---|---|
| CANDLE_SNAPSHOT_ENABLED, DP_CANDLE_PUBSUB_ENABLED | Keep | Feature toggle, changed this session |
| OG_REDIS_HOST, OG_REDIS_PORT, OG_REDIS_USERNAME, OG_REDIS_PASSWORD, OG_REDIS_DB | Keep | Credential/deployment target |
| CANDLE_SNAPSHOT_STATE_PREFIX, CANDLE_SNAPSHOT_EVENT_STREAM, DP_CANDLE_PUBSUB_CHANNEL, DP_CANDLE_PUBSUB_SCHEMA_VERSION | Keep | Cross-system contract with OG — any change must be coordinated externally |
| CANDLE_SNAPSHOT_EVENT_MAXLEN, CANDLE_SNAPSHOT_BARS, CANDLE_SNAPSHOT_QUEUE_MAXSIZE, CANDLE_SNAPSHOT_TIMEOUT_SEC, CANDLE_SNAPSHOT_CIRCUIT_COOLDOWN_SEC | Move | Internal buffer/timeout tuning |

### Storage / Logging / Backend

| Env var | Recommendation | Reason |
|---|---|---|
| DP_STORAGE_MODE | Keep | High-level storage architecture decision |
| REDIS_INIT_CANDLES | Delete (3.1) | Dead |
| LOG_LEVEL, BACKEND_LOG_RETENTION_DAYS, LOG_MAX_FILE_MB, LOG_DISK_BUDGET_MB | Keep | Real ops levers, covered in logging refactor docs |
| LOG_QUEUE_SIZE, LOG_QUEUE_WAIT_MS | Move | Internal log queue tuning |
| BACKEND_DISK_WARN_FREE_GB, BACKEND_DISK_FAIL_FREE_GB | Keep | Safety threshold, depends on real host disk size |
| BACKEND_HEALTH_INTERVAL_SEC, BACKEND_DB_HEALTH_INTERVAL_SEC | Keep | Real detection-speed vs. overhead tradeoff |
| BACKEND_LIVE_RESTART_ON_EXIT, BACKEND_LIVE_RESTART_ON_STALE, BACKEND_LIVE_STALE_MINUTES, BACKEND_LIVE_MAX_RESTARTS_PER_HOUR, BACKEND_LIVE_RESTART_COOLDOWN_SEC | Keep | Real restart policy, incident-time lever |
| HISTORICAL_BACKFILL_ENABLED, HISTORICAL_BACKFILL_UTC, HISTORICAL_BACKFILL_MODE, HISTORICAL_BACKFILL_ARGS, HISTORICAL_START_ON_BACKEND_START, HISTORICAL_MAX_RUNTIME_MINUTES | Keep | Schedule and business decisions |
| BACKEND_SHUTDOWN_GRACE_SEC | Keep | Affects downtime during restarts |
| HISTORICAL_START_DELAY_SEC | Keep | Real resource-contention lever at boot |
| BACKEND_HISTORICAL_RETRY_BASE_SEC, BACKEND_HISTORICAL_RETRY_MAX_SEC | Move | Internal backoff |
| BACKEND_STATUS_JSON_INDENT | Move | Purely cosmetic |

## 5. Proposed action plan (pending decision, not yet started)

1. Delete `TradingViewSettings.timezone` and `StorageSettings.redis_init_candles`
   (dead fields, section 3.1) plus their env vars.
2. Completed: consolidated `BACKEND_LOG_RETENTION_DAYS` parsing into one
   field (section 3.2).
3. Remove `DP_HISTORICAL_CANCEL_FILE` from `.env.example`'s operator-facing
   surface; keep the internal mechanism unchanged (section 3.3).
4. Decide on `SQL_TDS_VERSION`/FreeTDS support before touching it (section 3.3).
5. Move the ~30 fields marked "Move" (section 4) from env-configurable to
   fixed constants in code — no behavior change, only removes the ability to
   override them via `dp_provider.env`.
6. Rewrite `config/dp_provider.env`'s section headers/order to match
   `config/dp_provider.env.example`, and remove the stale text noted in 3.4.
7. Confirm whether `DP_CANDLE_PUBSUB_ENABLED=true` in production is
   intentional (business decision, not a code question).
8. Explicitly pin `LIVE_ASSET_TYPES` and `EXPECTED_LIVE_SYMBOLS` in the real
   `dp_provider.env`, matching the safety practice the code's own comments
   already recommend.

## 6. Cautions for whoever implements this

- This is a relocation/pruning exercise, not a tuning exercise: unless a
  specific value is explicitly called out for change (items 4 and 7 above),
  every "Move" field should keep its exact current default when it becomes a
  fixed constant. No behavior should change as a side effect of this cleanup.
- `settings/operational.py` is mid-edit from an unrelated, already
  in-progress logging refactor (see baseline note at the top). Diff against
  current code before touching this file, not against the line numbers
  quoted here.
- Do not implement this from this document alone — re-verify every "dead"
  and "duplicate" claim against live code first, the same way this document
  was produced (grep the object AND check for local aliases/`getattr` before
  concluding something is unused).
