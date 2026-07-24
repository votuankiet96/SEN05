"""Small, validated operator configuration surface.

Only deployment addresses, credentials, the historical schedule, and log
retention are read from ``config/dp_provider.env``. Fixed runtime mechanics
live in ``settings.internal``; domain contracts live in ``settings.system``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from core_engine.settings.internal import (
    BACKEND_POLICY,
    DATABASE_POLICY,
    HISTORICAL_POLICY,
    LIVE_POLICY,
    LOGGING_POLICY,
    NOTIFICATION_POLICY,
    SNAPSHOT_POLICY,
    TRADINGVIEW_POLICY,
)
from core_engine.settings.system import EXPECTED_LIVE_SYMBOLS, LIVE_ASSET_TYPES, STORAGE_MODE


# APP_ROOT is the source-checkout root containing config/, scripts/, and
# runtime/. DP_APP_ROOT remains available for an explicit alternate checkout.
_APP_ROOT_OVERRIDE = os.environ.get("DP_APP_ROOT", "").strip()
APP_ROOT = Path(_APP_ROOT_OVERRIDE) if _APP_ROOT_OVERRIDE else Path(__file__).resolve().parents[3]
CONFIG_DIR = APP_ROOT / "config"
ENV_FILE = CONFIG_DIR / "dp_provider.env"
ENV_EXAMPLE_FILE = CONFIG_DIR / "dp_provider.env.example"
RUNTIME_DIR = APP_ROOT / "runtime"
LOG_DIR = RUNTIME_DIR / "logs"
LOG_ARCHIVE_DIR = LOG_DIR / "archive"
CACHE_DIR = RUNTIME_DIR / "cache"
RUN_DIR = RUNTIME_DIR / "run"
SPOOL_DIR = RUNTIME_DIR / "spool"
LOG_LOCK_DIR = RUN_DIR / "log_locks"
LOG_EMERGENCY_DIR = RUN_DIR / "log_emergency"

LIVE_LOG = LOG_DIR / "live.log"
HISTORICAL_LOG = LOG_DIR / "historical.log"
SYSTEM_LOG = LOG_DIR / "system.log"
ALERTS_LOG = LOG_DIR / "alerts.log"
WS_LIVE_PID = RUN_DIR / "ws_live_runtime.pid"
WS_LIVE_STATE = RUN_DIR / "ws_live_state.json"
BACKEND_STATE = RUN_DIR / "backend_engine_state.json"
BACKEND_STOP_FILE = RUN_DIR / "backend_engine.stop"
HISTORICAL_CANCEL_FILE = RUN_DIR / "historical.cancel"
HISTORICAL_QUEUE_FILE = RUN_DIR / "historical_queue.jsonl"
WS_OVERFLOW_SPOOL = SPOOL_DIR / "overflow_spool.db"
VERIFIED_MARKET_GAPS = CACHE_DIR / "verified_market_gaps.json"

_OPERATOR_ENV_KEYS = frozenset(
    {
        "SQL_SERVER",
        "SQL_DATABASE",
        "SQL_PORT",
        "SQL_UID",
        "SQL_PWD",
        "TV_AUTH_TOKEN",
        "TV_COOKIE",
        "TV_USERNAME",
        "TV_PASSWORD",
        "TV_2FA_SECRET",
        "TV_BROWSER_PROFILE_DIR",
        "TV_AUTH_HEADLESS_FRESH_LOGIN",
        "TV_CAPTCHA_SERVICE",
        "TV_CAPTCHA_API_KEY",
        "HISTORICAL_BACKFILL_ENABLED",
        "HISTORICAL_BACKFILL_UTC",
        "DISCORD_WEBHOOK_URL",
        "LOG_LEVEL",
        "LOG_RETENTION_DAYS",
        "LOG_DISK_BUDGET_MB",
        "OG_REDIS_HOST",
        "OG_REDIS_PORT",
        "OG_REDIS_USERNAME",
        "OG_REDIS_PASSWORD",
        "OG_REDIS_DB",
    }
)
_BOOL_ENV_KEYS = {
    "TV_AUTH_HEADLESS_FRESH_LOGIN",
    "HISTORICAL_BACKFILL_ENABLED",
}
_INTEGER_ENV_RANGES = {
    "SQL_PORT": (1, 65_535, True),
    "LOG_RETENTION_DAYS": (1, 3_650, False),
    "LOG_DISK_BUDGET_MB": (100, 1_000_000, False),
    "OG_REDIS_PORT": (1, 65_535, False),
    "OG_REDIS_DB": (0, 1_000_000, False),
}
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_SCHEDULE_SLOT = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def inspect_operator_config(path: Path = ENV_FILE) -> dict[str, object]:
    """Validate the operator file without exposing any configured value."""
    issues: list[str] = []
    keys: list[str] = []
    values: dict[str, str] = {}
    if not path.exists():
        return {
            "ok": False,
            "path": str(path),
            "keys": [],
            "issues": [f"Operator config file does not exist: {path}"],
        }

    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8-sig", errors="replace").splitlines(),
        start=1,
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            issues.append(f"Line {line_number} is not KEY=value.")
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key in values:
            issues.append(f"{key} appears more than once (line {line_number}).")
            continue
        keys.append(key)
        values[key] = value
        if key not in _OPERATOR_ENV_KEYS:
            issues.append(
                f"{key} is not operator-editable; remove it from dp_provider.env."
            )

    for key in _BOOL_ENV_KEYS & values.keys():
        if values[key].lower() not in {"0", "1", "true", "false", "yes", "no", "on", "off"}:
            issues.append(f"{key} must be true/false or 1/0.")

    for key, (minimum, maximum, allow_empty) in _INTEGER_ENV_RANGES.items():
        if key not in values:
            continue
        raw = values[key]
        if allow_empty and not raw:
            continue
        try:
            number = int(raw)
        except ValueError:
            issues.append(f"{key} must be an integer.")
            continue
        if not minimum <= number <= maximum:
            issues.append(f"{key} must be between {minimum} and {maximum}.")

    if "LOG_LEVEL" in values and values["LOG_LEVEL"].upper() not in _LOG_LEVELS:
        issues.append("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL.")

    if "HISTORICAL_BACKFILL_UTC" in values:
        slots = [part.strip() for part in values["HISTORICAL_BACKFILL_UTC"].split(",") if part.strip()]
        if not slots or any(_SCHEDULE_SLOT.fullmatch(slot) is None for slot in slots):
            issues.append(
                "HISTORICAL_BACKFILL_UTC must contain UTC times in HH:MM format."
            )
        elif len(slots) != len(set(slots)):
            issues.append("HISTORICAL_BACKFILL_UTC contains a duplicate time.")

    return {
        "ok": not issues,
        "path": str(path),
        "keys": sorted(keys),
        "key_count": len(keys),
        "issues": issues,
    }


def read_env_values(path: Path = ENV_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
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
    port: str = env_str("SQL_PORT", "")
    uid: str = env_str("SQL_UID", "")
    pwd: str = env_str("SQL_PWD", "")
    driver: str = DATABASE_POLICY.driver
    encrypt: str = DATABASE_POLICY.encrypt
    trust_server_cert: str = DATABASE_POLICY.trust_server_cert
    health_timeout_seconds: int = DATABASE_POLICY.health_timeout_seconds
    retry_count: int = DATABASE_POLICY.retry_count
    retry_delay_sec: float = DATABASE_POLICY.retry_delay_sec
    command_timeout_seconds: int = DATABASE_POLICY.command_timeout_seconds
    lock_timeout_ms: int = DATABASE_POLICY.lock_timeout_ms


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
    token_proactive_refresh_sec: int = TRADINGVIEW_POLICY.token_proactive_refresh_sec
    token_proactive_retry_sec: int = TRADINGVIEW_POLICY.token_proactive_retry_sec
    auth_refresh_cooldown_sec: int = TRADINGVIEW_POLICY.auth_refresh_cooldown_sec
    auth_transient_cooldown_sec: int = TRADINGVIEW_POLICY.auth_transient_cooldown_sec
    auth_connectivity_preflight: bool = TRADINGVIEW_POLICY.auth_connectivity_preflight
    history_endpoint: str = TRADINGVIEW_POLICY.history_endpoint
    history_request_more_rounds: int = TRADINGVIEW_POLICY.history_request_more_rounds
    history_request_more_bars: int = TRADINGVIEW_POLICY.history_request_more_bars
    history_timeout_sec: float = TRADINGVIEW_POLICY.history_timeout_sec


@dataclass(frozen=True)
class HistoricalSettings:
    staging_cleanup_batch_rows: int = HISTORICAL_POLICY.staging_cleanup_batch_rows
    staging_cleanup_pause_sec: float = HISTORICAL_POLICY.staging_cleanup_pause_sec
    staging_cleanup_max_rows_per_run: int = HISTORICAL_POLICY.staging_cleanup_max_rows_per_run
    staging_cleanup_max_rows_per_table: int = HISTORICAL_POLICY.staging_cleanup_max_rows_per_table
    staging_cleanup_max_seconds: float = HISTORICAL_POLICY.staging_cleanup_max_seconds
    staging_cleanup_checkpoint: bool = HISTORICAL_POLICY.staging_cleanup_checkpoint
    drop_open_last_bar: bool = HISTORICAL_POLICY.drop_open_last_bar
    hole_lookback_days: int = HISTORICAL_POLICY.hole_lookback_days
    max_consecutive_fail: int = HISTORICAL_POLICY.max_consecutive_fail
    retry_delays: tuple[int, ...] = HISTORICAL_POLICY.retry_delays
    safety_factor: float = HISTORICAL_POLICY.safety_factor
    min_pull_bars: int = HISTORICAL_POLICY.min_pull_bars
    replay_enabled: bool = HISTORICAL_POLICY.replay_enabled
    replay_tfs: frozenset[str] = HISTORICAL_POLICY.replay_tfs
    replay_endpoint: str = HISTORICAL_POLICY.replay_endpoint
    replay_start_date: str = HISTORICAL_POLICY.replay_start_date
    replay_window_bars: int = HISTORICAL_POLICY.replay_window_bars
    replay_step_bars: int = HISTORICAL_POLICY.replay_step_bars
    replay_max_windows_per_pair: int = HISTORICAL_POLICY.replay_max_windows_per_pair
    replay_advance_factor: float = HISTORICAL_POLICY.replay_advance_factor
    replay_timeout_sec: float = HISTORICAL_POLICY.replay_timeout_sec


@dataclass(frozen=True)
class LiveSettings:
    auto_start: bool = LIVE_POLICY.auto_start
    asset_types: tuple[str, ...] = LIVE_ASSET_TYPES
    expected_symbol_count: int = EXPECTED_LIVE_SYMBOLS
    batch_interval_min: int = LIVE_POLICY.batch_interval_min
    shutdown_poll_sec: int = LIVE_POLICY.shutdown_poll_sec
    batch_fetch_timeout_sec: int = LIVE_POLICY.batch_fetch_timeout_sec
    ws_thread_join_grace_sec: int = LIVE_POLICY.ws_thread_join_grace_sec
    batch_group_join_timeout_sec: int = LIVE_POLICY.batch_group_join_timeout_sec
    batch_max_retries: int = LIVE_POLICY.batch_max_retries
    group_wedge_hard_deadline_batches: int = LIVE_POLICY.group_wedge_hard_deadline_batches
    n_bars: int = LIVE_POLICY.n_bars
    n_bars_backlog: int = LIVE_POLICY.n_bars_backlog
    max_backlog_batches: int = LIVE_POLICY.max_backlog_batches
    max_miss_retries: int = LIVE_POLICY.max_miss_retries
    symbols_per_conn: int = LIVE_POLICY.symbols_per_conn
    reconnect_base_sec: int = LIVE_POLICY.reconnect_base_sec
    reconnect_max_sec: int = LIVE_POLICY.reconnect_max_sec
    state_heartbeat_sec: int = LIVE_POLICY.state_heartbeat_sec
    guest_policy: str = LIVE_POLICY.guest_policy
    guest_pause_sec: int = LIVE_POLICY.guest_pause_sec
    rate_limit_cooldown_sec: int = LIVE_POLICY.rate_limit_cooldown_sec
    forbidden_cooldown_sec: int = LIVE_POLICY.forbidden_cooldown_sec
    preflight_require_headless: bool = LIVE_POLICY.preflight_require_headless
    connectivity_preflight: bool = LIVE_POLICY.connectivity_preflight
    connectivity_timeout_sec: float = LIVE_POLICY.connectivity_timeout_sec
    connectivity_cooldown_sec: int = LIVE_POLICY.connectivity_cooldown_sec
    db_queue_maxsize: int = LIVE_POLICY.db_queue_maxsize
    overflow_buffer_max: int = LIVE_POLICY.overflow_buffer_max
    session_throttle_sec: float = LIVE_POLICY.session_throttle_sec
    max_spool_rows: int = LIVE_POLICY.max_spool_rows
    status_interval_sec: int = LIVE_POLICY.status_interval_sec
    etl_direct_retries: int = LIVE_POLICY.etl_direct_retries
    etl_direct_retry_delay_sec: float = LIVE_POLICY.etl_direct_retry_delay_sec
    etl_deferred_retry_cooldown_sec: int = LIVE_POLICY.etl_deferred_retry_cooldown_sec
    timezone: str = TRADINGVIEW_POLICY.timezone

    def __post_init__(self) -> None:
        if self.guest_policy not in {"allow", "pause", "abort"}:
            object.__setattr__(self, "guest_policy", "pause")


@dataclass(frozen=True)
class NotificationSettings:
    discord_webhook_url: str = env_str("DISCORD_WEBHOOK_URL", "")
    discord_send_attempts: int = NOTIFICATION_POLICY.send_attempts
    discord_timeout_connect_sec: float = NOTIFICATION_POLICY.timeout_connect_sec
    discord_timeout_read_sec: float = NOTIFICATION_POLICY.timeout_read_sec
    discord_circuit_failures: int = NOTIFICATION_POLICY.circuit_failures
    discord_circuit_cooldown_sec: int = NOTIFICATION_POLICY.circuit_cooldown_sec
    discord_dedupe_window_sec: int = NOTIFICATION_POLICY.dedupe_window_sec


@dataclass(frozen=True)
class CandleSnapshotSettings:
    redis_host: str = env_str("OG_REDIS_HOST", "")
    redis_port: int = env_int("OG_REDIS_PORT", 6379, minimum=1, maximum=65535)
    redis_username: str = env_str("OG_REDIS_USERNAME", "")
    redis_password: str = env_str("OG_REDIS_PASSWORD", "")
    redis_db: int = env_int("OG_REDIS_DB", 0, minimum=0)
    enabled: bool = SNAPSHOT_POLICY.enabled
    state_prefix: str = SNAPSHOT_POLICY.state_prefix
    event_stream: str = SNAPSHOT_POLICY.event_stream
    event_maxlen: int = SNAPSHOT_POLICY.event_maxlen
    bars_per_snapshot: int = SNAPSHOT_POLICY.bars_per_snapshot
    queue_maxsize: int = SNAPSHOT_POLICY.queue_maxsize
    timeout_sec: float = SNAPSHOT_POLICY.timeout_sec
    circuit_cooldown_sec: int = SNAPSHOT_POLICY.circuit_cooldown_sec
    pubsub_enabled: bool = SNAPSHOT_POLICY.pubsub_enabled
    pubsub_channel: str = SNAPSHOT_POLICY.pubsub_channel
    pubsub_schema_version: int = SNAPSHOT_POLICY.pubsub_schema_version


@dataclass(frozen=True)
class StorageSettings:
    mode: str = STORAGE_MODE


@dataclass(frozen=True)
class LoggingSettings:
    level: str = env_str("LOG_LEVEL", "INFO").upper()
    retention_days: int = env_int("LOG_RETENTION_DAYS", 30, minimum=1)
    disk_budget_mb: int = env_int("LOG_DISK_BUDGET_MB", 2048, minimum=100)
    queue_size: int = LOGGING_POLICY.queue_size
    queue_wait_ms: int = LOGGING_POLICY.queue_wait_ms
    max_file_mb: int = LOGGING_POLICY.max_file_mb
    live_log: Path = LIVE_LOG
    historical_log: Path = HISTORICAL_LOG
    system_log: Path = SYSTEM_LOG
    alerts_log: Path = ALERTS_LOG


@dataclass(frozen=True)
class BackendSettings:
    historical_backfill_enabled: bool = env_bool("HISTORICAL_BACKFILL_ENABLED", True)
    historical_backfill_utc: str = env_str("HISTORICAL_BACKFILL_UTC", "11:00,22:00")
    health_interval_sec: int = BACKEND_POLICY.health_interval_sec
    disk_warn_free_gb: float = BACKEND_POLICY.disk_warn_free_gb
    disk_fail_free_gb: float = BACKEND_POLICY.disk_fail_free_gb
    db_health_interval_sec: int = BACKEND_POLICY.db_health_interval_sec
    live_auto_start: bool = LIVE_POLICY.auto_start
    live_restart_on_exit: bool = BACKEND_POLICY.live_restart_on_exit
    live_restart_on_stale: bool = BACKEND_POLICY.live_restart_on_stale
    live_stale_minutes: int = BACKEND_POLICY.live_stale_minutes
    live_max_restarts_per_hour: int = BACKEND_POLICY.live_max_restarts_per_hour
    live_restart_cooldown_sec: int = BACKEND_POLICY.live_restart_cooldown_sec
    historical_start_on_backend_start: bool = BACKEND_POLICY.historical_start_on_backend_start
    historical_start_delay_sec: int = BACKEND_POLICY.historical_start_delay_sec
    historical_backfill_mode: str = BACKEND_POLICY.historical_backfill_mode
    historical_backfill_args: str = BACKEND_POLICY.historical_backfill_args
    historical_max_runtime_minutes: int = BACKEND_POLICY.historical_max_runtime_minutes
    historical_retry_base_sec: int = BACKEND_POLICY.historical_retry_base_sec
    historical_retry_max_sec: int = BACKEND_POLICY.historical_retry_max_sec
    shutdown_grace_sec: int = BACKEND_POLICY.shutdown_grace_sec
    status_json_indent: int = BACKEND_POLICY.status_json_indent

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
    server = f"{db.server},{db.port}" if db.port else db.server
    base = (
        f"DRIVER={{{db.driver}}};"
        f"SERVER={server};"
        f"DATABASE={target_db};"
        f"Encrypt={db.encrypt};"
        f"TrustServerCertificate={db.trust_server_cert};"
    )
    if db.uid and db.pwd:
        return base + f"UID={db.uid};PWD={db.pwd};"
    return base + "Trusted_Connection=yes;"


def ensure_runtime_dirs() -> None:
    for path in (
        LOG_DIR,
        LOG_ARCHIVE_DIR,
        LOG_LOCK_DIR,
        LOG_EMERGENCY_DIR,
        CACHE_DIR,
        RUN_DIR,
        SPOOL_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
