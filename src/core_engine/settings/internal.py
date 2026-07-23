"""Fixed runtime policy for DP Program.

These values are implementation decisions, not operator configuration. Change
them through a reviewed code commit with tests. Deployment-specific addresses,
credentials, schedules, and log-retention policy remain in ``operational.py``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DatabasePolicy:
    driver: str = "ODBC Driver 18 for SQL Server"
    encrypt: str = "no"
    trust_server_cert: str = "yes"
    health_timeout_seconds: int = 30
    command_timeout_seconds: int = 30
    lock_timeout_ms: int = 15_000
    retry_count: int = 3
    retry_delay_sec: float = 5.0


@dataclass(frozen=True)
class TradingViewPolicy:
    token_proactive_refresh_sec: int = 900
    token_proactive_retry_sec: int = 600
    auth_refresh_cooldown_sec: int = 900
    auth_transient_cooldown_sec: int = 300
    auth_connectivity_preflight: bool = True
    auth_connect_timeout_sec: int = 2
    auth_read_timeout_sec: int = 4
    auth_refresh_lock_stale_sec: int = 20 * 60
    auth_refresh_peer_wait_sec: int = 90
    history_endpoint: str = "prodata"
    history_request_more_rounds: int = 5
    history_request_more_bars: int = 50_000
    history_timeout_sec: float = 45.0
    timezone: str = "Etc/UTC"


@dataclass(frozen=True)
class HistoricalPolicy:
    staging_cleanup_batch_rows: int = 1_000
    staging_cleanup_pause_sec: float = 0.20
    staging_cleanup_max_rows_per_run: int = 20_000
    staging_cleanup_max_rows_per_table: int = 5_000
    staging_cleanup_max_seconds: float = 30.0
    staging_cleanup_checkpoint: bool = False
    drop_open_last_bar: bool = True
    hole_lookback_days: int = 60
    max_consecutive_fail: int = 8
    retry_delays: tuple[int, ...] = (10, 30, 60)
    safety_factor: float = 1.5
    min_pull_bars: int = 10
    replay_enabled: bool = True
    replay_tfs: frozenset[str] = frozenset({"M5", "M10", "M15", "M20", "M30", "M45", "H1"})
    replay_endpoint: str = "prodata"
    replay_start_date: str = "1970-01-01"
    replay_window_bars: int = 5_000
    replay_step_bars: int = 5_000
    replay_max_windows_per_pair: int = 1_000
    replay_advance_factor: float = 1.25
    replay_timeout_sec: float = 30.0


@dataclass(frozen=True)
class LivePolicy:
    auto_start: bool = True
    batch_interval_min: int = 5
    shutdown_poll_sec: int = 2
    batch_fetch_timeout_sec: int = 120
    ws_thread_join_grace_sec: int = 10
    batch_group_join_timeout_sec: int = 135
    batch_max_retries: int = 3
    group_wedge_hard_deadline_batches: int = 3
    n_bars: int = 5
    n_bars_backlog: int = 30
    max_backlog_batches: int = 12
    max_miss_retries: int = 5
    symbols_per_conn: int = 10
    reconnect_base_sec: int = 30
    reconnect_max_sec: int = 300
    state_heartbeat_sec: int = 15
    guest_policy: str = "pause"
    guest_pause_sec: int = 300
    rate_limit_cooldown_sec: int = 300
    forbidden_cooldown_sec: int = 900
    preflight_require_headless: bool = False
    connectivity_preflight: bool = True
    connectivity_timeout_sec: float = 5.0
    connectivity_cooldown_sec: int = 300
    db_queue_maxsize: int = 2_000
    overflow_buffer_max: int = 500
    session_throttle_sec: float = 0.15
    max_spool_rows: int = 100_000
    status_interval_sec: int = 3_600
    etl_direct_retries: int = 3
    etl_direct_retry_delay_sec: float = 1.5
    etl_deferred_retry_cooldown_sec: int = 60


@dataclass(frozen=True)
class NotificationPolicy:
    send_attempts: int = 2
    timeout_connect_sec: float = 2.0
    timeout_read_sec: float = 4.0
    circuit_failures: int = 3
    circuit_cooldown_sec: int = 300
    dedupe_window_sec: int = 600


@dataclass(frozen=True)
class SnapshotPolicy:
    enabled: bool = False
    state_prefix: str = "dp:candle_snapshot:latest"
    event_stream: str = "dp:candle_snapshot:events"
    event_maxlen: int = 10_000
    bars_per_snapshot: int = 500
    queue_maxsize: int = 1_000
    timeout_sec: float = 0.3
    circuit_cooldown_sec: int = 30
    pubsub_enabled: bool = False
    pubsub_channel: str = "dp:pubsub:candle_snapshot:events"
    pubsub_schema_version: int = 1


@dataclass(frozen=True)
class LoggingPolicy:
    queue_size: int = 10_000
    queue_wait_ms: int = 20
    max_file_mb: int = 25


@dataclass(frozen=True)
class BackendPolicy:
    health_interval_sec: int = 30
    disk_warn_free_gb: float = 5.0
    disk_fail_free_gb: float = 1.0
    db_health_interval_sec: int = 900
    live_restart_on_exit: bool = True
    live_restart_on_stale: bool = True
    live_stale_minutes: int = 15
    live_max_restarts_per_hour: int = 3
    live_restart_cooldown_sec: int = 60
    historical_start_on_backend_start: bool = True
    historical_start_delay_sec: int = 15
    historical_backfill_mode: str = "gap"
    historical_backfill_args: str = ""
    historical_max_runtime_minutes: int = 360
    historical_retry_base_sec: int = 300
    historical_retry_max_sec: int = 1_800
    shutdown_grace_sec: int = 240
    status_json_indent: int = 2


DATABASE_POLICY = DatabasePolicy()
TRADINGVIEW_POLICY = TradingViewPolicy()
HISTORICAL_POLICY = HistoricalPolicy()
LIVE_POLICY = LivePolicy()
NOTIFICATION_POLICY = NotificationPolicy()
SNAPSHOT_POLICY = SnapshotPolicy()
LOGGING_POLICY = LoggingPolicy()
BACKEND_POLICY = BackendPolicy()

