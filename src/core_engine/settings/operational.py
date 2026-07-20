"""Operator-editable runtime configuration.

Everything here is either read from `config/dp_provider.env` (values an
operator is expected to tune per deployment: SQL credentials, TradingView
credentials, batch sizes, timeouts, feature toggles...) or is a filesystem
path derived from the repository root. It is the opposite of
`core_engine.settings.system`, which holds fixed, code-level domain tables
(timeframe maps, default bar counts) that are not meant to vary by
deployment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# APP_ROOT is the repository root (the directory that contains config/,
# scripts/, runtime/, and src/). This file lives at
# <root>/src/core_engine/settings/operational.py, four levels down.
# DP_APP_ROOT lets an operator override the root explicitly (e.g. when the
# package is installed elsewhere but should still read/write a fixed
# runtime/config location).
_APP_ROOT_OVERRIDE = os.environ.get("DP_APP_ROOT", "").strip()
APP_ROOT = Path(_APP_ROOT_OVERRIDE) if _APP_ROOT_OVERRIDE else Path(__file__).resolve().parents[3]
CONFIG_DIR = APP_ROOT / "config"
ENV_FILE = CONFIG_DIR / "dp_provider.env"
ENV_EXAMPLE_FILE = CONFIG_DIR / "dp_provider.env.example"
RUNTIME_DIR = APP_ROOT / "runtime"
LOG_DIR = RUNTIME_DIR / "logs"
SYSTEM_LOG_DIR = LOG_DIR / "system"
OPERATION_LOG_DIR = LOG_DIR / "operation"
CACHE_DIR = RUNTIME_DIR / "cache"
RUN_DIR = RUNTIME_DIR / "run"
SPOOL_DIR = RUNTIME_DIR / "spool"

BACKEND_LOG = SYSTEM_LOG_DIR / "system.log"
ACTIVITY_LOG = SYSTEM_LOG_DIR / "activity.log"
AUTH_LOG = SYSTEM_LOG_DIR / "auth.log"
DISCORD_LOG = SYSTEM_LOG_DIR / "discord.log"
# All supervised child processes (live + historical) share one debug-only
# stdout/stderr capture file; each process also owns its own real log file.
BACKEND_CHILD_STDOUT_LOG = SYSTEM_LOG_DIR / "subprocess_debug.log"
WS_LIVE_LOG = OPERATION_LOG_DIR / "live_fetching.log"
WS_LIVE_CANDLE_LOG = OPERATION_LOG_DIR / "live_candles.log"
WS_LIVE_REPORT_LOG = OPERATION_LOG_DIR / "live_reports.log"
PIPELINE_LOG = OPERATION_LOG_DIR / "historical_pulling.log"
DATA_WAREHOUSE_LOG = OPERATION_LOG_DIR / "data_warehouse.log"
LIVE_SUMMARY_LOG = OPERATION_LOG_DIR / "live_fetching_summary.jsonl"
HISTORICAL_SUMMARY_LOG = OPERATION_LOG_DIR / "historical_pulling_summary.jsonl"
WS_LIVE_PID = RUN_DIR / "ws_live_runtime.pid"
WS_LIVE_STATE = RUN_DIR / "ws_live_state.json"
BACKEND_STATE = RUN_DIR / "backend_engine_state.json"
BACKEND_STOP_FILE = RUN_DIR / "backend_engine.stop"
HISTORICAL_CANCEL_FILE = RUN_DIR / "historical.cancel"
HISTORICAL_QUEUE_FILE = RUN_DIR / "historical_queue.jsonl"
WS_OVERFLOW_SPOOL = SPOOL_DIR / "overflow_spool.db"
VERIFIED_MARKET_GAPS = CACHE_DIR / "verified_market_gaps.json"


def read_env_values(path: Path = ENV_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def load_env(path: Path = ENV_FILE, *, override: bool = False) -> None:
    """Load dotenv values without making python-dotenv mandatory."""
    try:
        from dotenv import load_dotenv

        load_dotenv(path, override=override)
        return
    except Exception:
        pass

    for key, value in read_env_values(path).items():
        if override or key not in os.environ:
            os.environ[key] = value


load_env()


def env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = env_str(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = env_str(name)
    if not raw:
        value = default
    else:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def env_float(
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    raw = env_str(name)
    if not raw:
        value = default
    else:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def env_csv(name: str, default: str = "") -> list[str]:
    raw = env_str(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class DatabaseSettings:
    server: str = env_str("SQL_SERVER", "localhost")
    database: str = env_str("SQL_DATABASE", "SEN05_AutoTrading")
    driver: str = env_str("SQL_DRIVER", "ODBC Driver 18 for SQL Server")
    port: str = env_str("SQL_PORT", "")
    tds_version: str = env_str("SQL_TDS_VERSION", "7.4")
    uid: str = env_str("SQL_UID", "")
    pwd: str = env_str("SQL_PWD", "")
    encrypt: str = env_str("SQL_ENCRYPT", "no")
    trust_server_cert: str = env_str("SQL_TRUST_SERVER_CERT", "yes")
    health_timeout_seconds: int = env_int("SQL_HEALTH_TIMEOUT_SECONDS", 30, minimum=1)
    retry_count: int = env_int("DB_RETRY_COUNT", 3, minimum=1)
    retry_delay_sec: float = env_float("DB_RETRY_DELAY_SEC", 5.0, minimum=0.0)
    # Per-statement execution timeout (pyodbc Connection.timeout) so a hung
    # statement cannot block a worker thread/shutdown indefinitely.
    command_timeout_seconds: int = env_int("SQL_COMMAND_TIMEOUT_SECONDS", 30, minimum=1)
    # SET LOCK_TIMEOUT in milliseconds: how long a statement waits on a row/key
    # lock (e.g. the UPDLOCK/HOLDLOCK guard in usp_LoadDirect) before SQL Server
    # raises error 1222 instead of blocking forever. -1 would mean "wait forever".
    lock_timeout_ms: int = env_int("SQL_LOCK_TIMEOUT_MS", 15_000, minimum=100)


@dataclass(frozen=True)
class TradingViewSettings:
    auth_token: str = env_str("TV_AUTH_TOKEN", "")
    cookie: str = env_str("TV_COOKIE", "")
    username: str = env_str("TV_USERNAME", "")
    password: str = env_str("TV_PASSWORD", "")
    two_fa_secret: str = env_str("TV_2FA_SECRET", "")
    captcha_api_key: str = env_str("TV_CAPTCHA_API_KEY", "")
    captcha_service: str = env_str("TV_CAPTCHA_SERVICE", "capsolver")
    browser_profile_dir: str = env_str("TV_BROWSER_PROFILE_DIR", "")
    headless_fresh_login: bool = env_bool("TV_AUTH_HEADLESS_FRESH_LOGIN", False)
    token_proactive_refresh_sec: int = env_int("TV_TOKEN_PROACTIVE_REFRESH_SEC", 900, minimum=60)
    token_proactive_retry_sec: int = env_int("TV_TOKEN_PROACTIVE_RETRY_SEC", 600, minimum=60)
    auth_refresh_cooldown_sec: int = env_int("TV_AUTH_REFRESH_COOLDOWN_SEC", 900, minimum=60)
    auth_transient_cooldown_sec: int = env_int("TV_AUTH_TRANSIENT_COOLDOWN_SEC", 300, minimum=30)
    auth_connectivity_preflight: bool = env_bool("TV_AUTH_CONNECTIVITY_PREFLIGHT", True)
    history_endpoint: str = env_str("TV_WS_HISTORY_ENDPOINT", "prodata").lower()
    history_fallback_endpoints: tuple[str, ...] = tuple(
        item.lower() for item in env_csv("TV_WS_HISTORY_FALLBACK_ENDPOINTS", "data")
    )
    history_request_more_rounds: int = env_int("TV_WS_HISTORY_REQUEST_MORE_ROUNDS", 5, minimum=0)
    history_request_more_bars: int = env_int("TV_WS_HISTORY_REQUEST_MORE_BARS", 50000, minimum=1)
    history_timeout_sec: float = env_float("TV_WS_HISTORY_TIMEOUT_SEC", 45.0, minimum=5.0)
    timezone: str = env_str("TV_WS_TIMEZONE", "Etc/UTC")


@dataclass(frozen=True)
class HistoricalSettings:
    provider: str = env_str("HISTORICAL_PROVIDER", "websocket").lower()
    staging_insert_chunk_rows: int = env_int("STAGING_INSERT_CHUNK_ROWS", 50000, minimum=1)
    staging_cleanup_batch_rows: int = env_int(
        "STAGING_CLEANUP_BATCH_ROWS", 5000, minimum=500, maximum=50000
    )
    staging_cleanup_pause_sec: float = env_float(
        "STAGING_CLEANUP_PAUSE_SEC", 0.05, minimum=0.0, maximum=5.0
    )
    staging_cleanup_max_rows_per_run: int = env_int(
        "STAGING_CLEANUP_MAX_ROWS_PER_RUN", 500000, minimum=0
    )
    staging_cleanup_checkpoint: bool = env_bool("STAGING_CLEANUP_CHECKPOINT", True)
    drop_open_last_bar: bool = env_bool("HISTORICAL_DROP_OPEN_LAST_BAR", True)
    hole_lookback_days: int = env_int("PIPELINE_HOLE_LOOKBACK_DAYS", 60, minimum=1)
    max_consecutive_fail: int = env_int("PIPELINE_MAX_CONSECUTIVE_FAIL", 8, minimum=1)
    retry_delays: tuple[int, ...] = tuple(
        int(item) for item in env_csv("PIPELINE_RETRY_DELAYS", "10,30,60") if item.isdigit()
    )
    safety_factor: float = env_float("PIPELINE_SAFETY_FACTOR", 1.5, minimum=1.0)
    min_pull_bars: int = env_int("PIPELINE_MIN_PULL_BARS", 10, minimum=1)
    fill_threshold: int = env_int("PIPELINE_FILL_THRESHOLD", 5, minimum=0)
    replay_enabled: bool = env_bool("TV_WS_REPLAY_ENABLED", True)
    replay_tfs: frozenset[str] = frozenset(
        item.upper() for item in env_csv("TV_WS_REPLAY_TFS", "M5,M10,M15,M20,M30,M45,H1")
    )
    replay_endpoint: str = env_str("TV_WS_REPLAY_ENDPOINT", "prodata").lower()
    replay_start_date: str = env_str("TV_WS_REPLAY_START_DATE", "1970-01-01")
    replay_window_bars: int = env_int("TV_WS_REPLAY_WINDOW_BARS", 5000, minimum=1)
    replay_step_bars: int = env_int("TV_WS_REPLAY_STEP_BARS", 5000, minimum=0)
    replay_max_windows_per_pair: int = env_int("TV_WS_REPLAY_MAX_WINDOWS_PER_PAIR", 1000, minimum=1)
    replay_advance_factor: float = env_float("TV_WS_REPLAY_ADVANCE_FACTOR", 1.25, minimum=0.25)
    replay_timeout_sec: float = env_float("TV_WS_REPLAY_TIMEOUT_SEC", 30.0, minimum=5.0)


@dataclass(frozen=True)
class LiveSettings:
    auto_start: bool = env_bool("WS_LIVE_AUTO_START", False)
    # Which instruments.py asset_type values live fetching watches over
    # WebSocket. Currently only 11 of 37 configured instruments (Indice,
    # Metal, Crypto) are live - the 26 FOREX symbols are historical-only,
    # refreshed on the HISTORICAL_BACKFILL_UTC schedule (default
    # 11:00/22:00 UTC), not in real time. This is a known, deliberate
    # scope limit from the initial build, not a bug - see round-2 audit
    # item P0-8 and docs/OPERATOR_RUNBOOK.md for the operational
    # consequence (FOREX freshness SLA is "next scheduled historical run",
    # not "next live batch"). Whether to extend live coverage to FOREX is
    # a business decision, not something this code should decide on its
    # own - see EXPECTED_LIVE_SYMBOL_COUNT below for a guard against this
    # set silently drifting out of sync with instruments.py instead.
    asset_types: tuple[str, ...] = tuple(
        item for item in env_csv("LIVE_ASSET_TYPES", "Indice,Metal,Crypto")
    )
    # Optional fail-fast guard: if set, live refuses to start unless
    # exactly this many symbols match asset_types. Left unset by default
    # (no enforcement) since the "correct" count depends on
    # instruments.py content that can legitimately change; an operator
    # who wants drift protection sets this once after confirming the
    # current count via `python -m core_engine settings`.
    expected_symbol_count: int = env_int("EXPECTED_LIVE_SYMBOLS", 0, minimum=0)
    batch_interval_min: int = env_int("WS_LIVE_BATCH_INTERVAL_MIN", 5, minimum=1)
    shutdown_poll_sec: int = env_int("WS_LIVE_SHUTDOWN_POLL_SEC", 2, minimum=1)
    batch_fetch_timeout_sec: int = env_int("WS_LIVE_BATCH_FETCH_TIMEOUT_SEC", 120, minimum=5)
    ws_thread_join_grace_sec: int = env_int("WS_LIVE_WS_THREAD_JOIN_GRACE_SEC", 10, minimum=1)
    batch_group_join_timeout_sec: int = env_int("WS_LIVE_BATCH_GROUP_JOIN_TIMEOUT_SEC", 135, minimum=5)
    batch_max_retries: int = env_int("WS_LIVE_BATCH_MAX_RETRIES", 3, minimum=0)
    n_bars: int = env_int("WS_LIVE_N_BARS", 5, minimum=1)
    n_bars_backlog: int = env_int("WS_LIVE_N_BARS_BACKLOG", 30, minimum=1)
    max_backlog_batches: int = env_int("WS_LIVE_MAX_BACKLOG_BATCHES", 12, minimum=0)
    max_miss_retries: int = env_int("WS_LIVE_MAX_MISS_RETRIES", 5, minimum=0)
    symbols_per_conn: int = env_int("WS_LIVE_SYMBOLS_PER_CONN", 10, minimum=1)
    reconnect_base_sec: int = env_int("WS_LIVE_RECONNECT_BASE_SEC", 30, minimum=0)
    reconnect_max_sec: int = env_int("WS_LIVE_RECONNECT_MAX_SEC", 300, minimum=1)
    state_heartbeat_sec: int = env_int("WS_LIVE_STATE_HEARTBEAT_SEC", 15, minimum=5)
    guest_policy: str = env_str("TV_WS_GUEST_POLICY", "pause").lower()
    guest_pause_sec: int = env_int("TV_WS_GUEST_PAUSE_SEC", 300, minimum=0)
    rate_limit_cooldown_sec: int = env_int("TV_WS_RATE_LIMIT_COOLDOWN_SEC", 300, minimum=0)
    forbidden_cooldown_sec: int = env_int("TV_WS_FORBIDDEN_COOLDOWN_SEC", 900, minimum=0)
    preflight_require_headless: bool = env_bool("TV_WS_PREFLIGHT_REQUIRE_HEADLESS", False)
    connectivity_preflight: bool = env_bool("TV_WS_CONNECTIVITY_PREFLIGHT", True)
    connectivity_timeout_sec: float = env_float("TV_WS_CONNECTIVITY_TIMEOUT_SEC", 5.0, minimum=1.0)
    connectivity_cooldown_sec: int = env_int("TV_WS_CONNECTIVITY_COOLDOWN_SEC", 300, minimum=30)
    db_queue_maxsize: int = env_int("WS_LIVE_DB_QUEUE_MAXSIZE", 2000, minimum=1)
    overflow_buffer_max: int = env_int("WS_LIVE_OVERFLOW_BUFFER_MAX", 500, minimum=0)
    session_throttle_sec: float = env_float("WS_LIVE_SESSION_THROTTLE_SEC", 0.15, minimum=0.0)
    max_spool_rows: int = env_int("WS_LIVE_MAX_SPOOL_ROWS", 100000, minimum=0)
    status_interval_sec: int = env_int("WS_LIVE_STATUS_INTERVAL_SEC", 3600, minimum=60)
    etl_direct_retries: int = env_int("WS_LIVE_ETL_DIRECT_RETRIES", 3, minimum=1, maximum=5)
    etl_direct_retry_delay_sec: float = env_float(
        "WS_LIVE_ETL_DIRECT_RETRY_DELAY_SEC", 1.5, minimum=0.0, maximum=30.0
    )
    etl_deferred_retry_cooldown_sec: int = env_int(
        "WS_LIVE_ETL_DEFERRED_RETRY_COOLDOWN_SEC", 60, minimum=5, maximum=3600
    )
    timezone: str = env_str("TV_WS_TIMEZONE", "Etc/UTC")

    def __post_init__(self) -> None:
        if self.guest_policy not in {"allow", "pause", "abort"}:
            object.__setattr__(self, "guest_policy", "pause")


@dataclass(frozen=True)
class NotificationSettings:
    discord_webhook_url: str = env_str("DISCORD_WEBHOOK_URL", "")
    discord_send_attempts: int = env_int("DISCORD_SEND_ATTEMPTS", 2, minimum=1, maximum=5)
    discord_timeout_connect_sec: float = env_float("DISCORD_TIMEOUT_CONNECT_SEC", 2.0, minimum=0.25)
    discord_timeout_read_sec: float = env_float("DISCORD_TIMEOUT_READ_SEC", 4.0, minimum=0.5)
    discord_circuit_failures: int = env_int("DISCORD_CIRCUIT_FAILURES", 3, minimum=1)
    discord_circuit_cooldown_sec: int = env_int("DISCORD_CIRCUIT_COOLDOWN_SEC", 300, minimum=30)
    discord_dedupe_window_sec: int = env_int("DISCORD_DEDUPE_WINDOW_SEC", 600, minimum=0)


@dataclass(frozen=True)
class CandleSnapshotSettings:
    enabled: bool = env_bool("CANDLE_SNAPSHOT_ENABLED", False)
    redis_host: str = env_str("OG_REDIS_HOST", "")
    redis_port: int = env_int("OG_REDIS_PORT", 6379, minimum=1, maximum=65535)
    redis_username: str = env_str("OG_REDIS_USERNAME", "")
    redis_password: str = env_str("OG_REDIS_PASSWORD", "")
    redis_db: int = env_int("OG_REDIS_DB", 0, minimum=0)
    state_prefix: str = env_str("CANDLE_SNAPSHOT_STATE_PREFIX", "dp:candle_snapshot:latest")
    event_stream: str = env_str("CANDLE_SNAPSHOT_EVENT_STREAM", "dp:candle_snapshot:events")
    event_maxlen: int = env_int("CANDLE_SNAPSHOT_EVENT_MAXLEN", 10000, minimum=1)
    bars_per_snapshot: int = env_int("CANDLE_SNAPSHOT_BARS", 500, minimum=50, maximum=5000)
    queue_maxsize: int = env_int("CANDLE_SNAPSHOT_QUEUE_MAXSIZE", 1000, minimum=1, maximum=50000)
    timeout_sec: float = env_float("CANDLE_SNAPSHOT_TIMEOUT_SEC", 0.3, minimum=0.05, maximum=5.0)
    circuit_cooldown_sec: int = env_int("CANDLE_SNAPSHOT_CIRCUIT_COOLDOWN_SEC", 30, minimum=1)
    pubsub_enabled: bool = env_bool("DP_CANDLE_PUBSUB_ENABLED", False)
    pubsub_channel: str = env_str("DP_CANDLE_PUBSUB_CHANNEL", "dp:pubsub:candle_snapshot:events")
    pubsub_schema_version: int = env_int("DP_CANDLE_PUBSUB_SCHEMA_VERSION", 1, minimum=1)


_STORAGE_MODES = {"sql", "redis", "both"}


def _resolve_storage_mode() -> str:
    """Resolve DP_STORAGE_MODE, falling back to CANDLE_SNAPSHOT_ENABLED.

    Existing deployments never set DP_STORAGE_MODE, so leaving it unset
    must reproduce today's behavior exactly: CANDLE_SNAPSHOT_ENABLED=0 ->
    SQL-only (the historical default), CANDLE_SNAPSHOT_ENABLED=1 -> both
    SQL and Redis (the existing candle-snapshot handoff to OG).
    """
    raw = env_str("DP_STORAGE_MODE").lower()
    if raw in _STORAGE_MODES:
        return raw
    return "both" if env_bool("CANDLE_SNAPSHOT_ENABLED", False) else "sql"


@dataclass(frozen=True)
class StorageSettings:
    mode: str = _resolve_storage_mode()
    # Only used to seed Redis when mode == "redis" and there is no SQL
    # Fact_OHLCV history to read a starting watermark from; see
    # live/engine.py's init-candles startup step.
    redis_init_candles: int = env_int("REDIS_INIT_CANDLES", 500, minimum=50, maximum=5000)

    def __post_init__(self) -> None:
        if self.mode not in _STORAGE_MODES:
            object.__setattr__(self, "mode", "sql")


@dataclass(frozen=True)
class LoggingSettings:
    level: str = env_str("LOG_LEVEL", "INFO").upper()
    pipeline_log: Path = PIPELINE_LOG
    live_log: Path = WS_LIVE_LOG
    live_candle_log: Path = WS_LIVE_CANDLE_LOG
    live_report_log: Path = WS_LIVE_REPORT_LOG


@dataclass(frozen=True)
class BackendSettings:
    health_interval_sec: int = env_int("BACKEND_HEALTH_INTERVAL_SEC", 30, minimum=5)
    disk_warn_free_gb: float = env_float("BACKEND_DISK_WARN_FREE_GB", 5.0, minimum=0.0)
    disk_fail_free_gb: float = env_float("BACKEND_DISK_FAIL_FREE_GB", 1.0, minimum=0.0)
    # Separate, much slower interval for the DB-inclusive health check
    # (Fact_OHLCV freshness + usp_LoadDirect contract). Deliberately not
    # tied to health_interval_sec: that one runs every few seconds and
    # must stay cheap (no DB round trip), while this one opens a real SQL
    # connection and is meant to catch a systemic ETL failure automatically
    # instead of only at supervisor startup - see round-2 audit BLOCKER-3.
    db_health_interval_sec: int = env_int("BACKEND_DB_HEALTH_INTERVAL_SEC", 900, minimum=60)
    live_auto_start: bool = env_bool("WS_LIVE_AUTO_START", False)
    live_restart_on_exit: bool = env_bool("BACKEND_LIVE_RESTART_ON_EXIT", True)
    live_restart_on_stale: bool = env_bool("BACKEND_LIVE_RESTART_ON_STALE", True)
    live_stale_minutes: int = env_int("BACKEND_LIVE_STALE_MINUTES", 15, minimum=1)
    live_max_restarts_per_hour: int = env_int("BACKEND_LIVE_MAX_RESTARTS_PER_HOUR", 3, minimum=0)
    live_restart_cooldown_sec: int = env_int("BACKEND_LIVE_RESTART_COOLDOWN_SEC", 60, minimum=0)
    historical_backfill_enabled: bool = env_bool("HISTORICAL_BACKFILL_ENABLED", True)
    historical_backfill_utc: str = env_str("HISTORICAL_BACKFILL_UTC", "11:00,22:00")
    historical_start_on_backend_start: bool = env_bool("HISTORICAL_START_ON_BACKEND_START", True)
    historical_start_delay_sec: int = env_int("HISTORICAL_START_DELAY_SEC", 15, minimum=0, maximum=3600)
    historical_backfill_mode: str = env_str("HISTORICAL_BACKFILL_MODE", "gap").lower()
    historical_backfill_args: str = env_str("HISTORICAL_BACKFILL_ARGS", "")
    historical_max_runtime_minutes: int = env_int("HISTORICAL_MAX_RUNTIME_MINUTES", 360, minimum=0)
    historical_retry_base_sec: int = env_int("BACKEND_HISTORICAL_RETRY_BASE_SEC", 300, minimum=30)
    historical_retry_max_sec: int = env_int("BACKEND_HISTORICAL_RETRY_MAX_SEC", 1800, minimum=60)
    shutdown_grace_sec: int = env_int("BACKEND_SHUTDOWN_GRACE_SEC", 240, minimum=5)
    log_retention_days: int = env_int("BACKEND_LOG_RETENTION_DAYS", 30, minimum=1)
    status_json_indent: int = env_int("BACKEND_STATUS_JSON_INDENT", 2, minimum=0, maximum=8)

    def __post_init__(self) -> None:
        if self.historical_backfill_mode not in {"auto", "full", "gap"}:
            object.__setattr__(self, "historical_backfill_mode", "gap")
        if self.disk_fail_free_gb > self.disk_warn_free_gb:
            object.__setattr__(self, "disk_fail_free_gb", self.disk_warn_free_gb)


DB = DatabaseSettings()
TRADINGVIEW = TradingViewSettings()
HISTORICAL = HistoricalSettings()
LIVE = LiveSettings()
NOTIFICATION = NotificationSettings()
CANDLE_SNAPSHOT = CandleSnapshotSettings()
STORAGE = StorageSettings()
LOGGING = LoggingSettings()
BACKEND = BackendSettings()


def build_conn_str(database: str | None = None, db: DatabaseSettings = DB) -> str:
    target_db = database or db.database
    if db.driver.lower() == "freetds":
        server_part = f"SERVER={db.server};"
        if db.port:
            server_part += f"PORT={db.port};"
        base = f"DRIVER={{{db.driver}}};{server_part}DATABASE={target_db};TDS_Version={db.tds_version};"
    else:
        base = (
            f"DRIVER={{{db.driver}}};"
            f"SERVER={db.server};"
            f"DATABASE={target_db};"
            f"Encrypt={db.encrypt};"
            f"TrustServerCertificate={db.trust_server_cert};"
        )
    if db.uid and db.pwd:
        return base + f"UID={db.uid};PWD={db.pwd};"
    return base + "Trusted_Connection=yes;"


def ensure_runtime_dirs() -> None:
    for path in (LOG_DIR, SYSTEM_LOG_DIR, OPERATION_LOG_DIR, CACHE_DIR, RUN_DIR, SPOOL_DIR):
        path.mkdir(parents=True, exist_ok=True)
