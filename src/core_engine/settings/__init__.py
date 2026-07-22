"""Settings facade for DP Program.

Source of truth:
- Operator-editable runtime values live in `config/dp_provider.env`, loaded
  and typed by `core_engine.settings.operational`.
- Fixed, code-level domain tables (timeframe/interval maps, default bar
  counts) live in `core_engine.settings.system`.
- Instrument/timeframe definitions live in `core_engine.settings.instruments`.

Everything else in the codebase should import from `core_engine.settings`
(this module) rather than reaching into the submodules directly, so the
internal split can keep evolving without touching every call site.
"""

from __future__ import annotations

from core_engine.settings.instruments import (
    DIRECT_TFS,
    SYMBOLS,
    TF_DISPLAY_ORDER,
    TF_MINUTES,
    WEEKEND_CLOSED,
)
from core_engine.settings.operational import (
    ACTIVITY_LOG,
    APP_ROOT,
    AUTH_LOG,
    BACKEND,
    BACKEND_CHILD_STDOUT_LOG,
    BACKEND_LOG,
    BACKEND_STATE,
    BACKEND_STOP_FILE,
    CACHE_DIR,
    CANDLE_SNAPSHOT,
    DATA_WAREHOUSE_LOG,
    DB,
    DISCORD_LOG,
    ENV_FILE,
    HISTORICAL,
    HISTORICAL_CANCEL_FILE,
    HISTORICAL_QUEUE_FILE,
    HISTORICAL_SUMMARY_LOG,
    LIVE,
    LIVE_SUMMARY_LOG,
    LOG_DIR,
    LOGGING,
    NOTIFICATION,
    OPERATION_LOG_DIR,
    PIPELINE_LOG,
    RUNTIME_DIR,
    RUN_DIR,
    SPOOL_DIR,
    STORAGE,
    SYSTEM_LOG_DIR,
    TRADINGVIEW,
    VERIFIED_MARKET_GAPS,
    WS_LIVE_LOG,
    WS_LIVE_PID,
    WS_LIVE_REPORT_LOG,
    WS_LIVE_STATE,
    WS_OVERFLOW_SPOOL,
    build_conn_str,
    ensure_runtime_dirs,
    env_int,
    env_str,
)
from core_engine.settings.system import (
    DEFAULT_N_BARS,
    OVERNIGHT_GAP_MINUTES,
    SYMBOL_OVERNIGHT_MINS,
    TF_STAGING,
    get_historical_timeframes,
)

__all__ = [
    "ACTIVITY_LOG",
    "APP_ROOT",
    "AUTH_LOG",
    "BACKEND",
    "BACKEND_CHILD_STDOUT_LOG",
    "BACKEND_LOG",
    "BACKEND_STATE",
    "BACKEND_STOP_FILE",
    "CACHE_DIR",
    "CANDLE_SNAPSHOT",
    "DATA_WAREHOUSE_LOG",
    "DB",
    "DEFAULT_N_BARS",
    "DIRECT_TFS",
    "DISCORD_LOG",
    "ENV_FILE",
    "HISTORICAL",
    "HISTORICAL_CANCEL_FILE",
    "HISTORICAL_QUEUE_FILE",
    "HISTORICAL_SUMMARY_LOG",
    "LIVE",
    "LIVE_SUMMARY_LOG",
    "LOGGING",
    "LOG_DIR",
    "NOTIFICATION",
    "OPERATION_LOG_DIR",
    "OVERNIGHT_GAP_MINUTES",
    "PIPELINE_LOG",
    "RUNTIME_DIR",
    "RUN_DIR",
    "SPOOL_DIR",
    "STORAGE",
    "SYMBOLS",
    "SYMBOL_OVERNIGHT_MINS",
    "SYSTEM_LOG_DIR",
    "TF_DISPLAY_ORDER",
    "TF_MINUTES",
    "TF_STAGING",
    "TRADINGVIEW",
    "VERIFIED_MARKET_GAPS",
    "WEEKEND_CLOSED",
    "WS_LIVE_LOG",
    "WS_LIVE_PID",
    "WS_LIVE_REPORT_LOG",
    "WS_LIVE_STATE",
    "WS_OVERFLOW_SPOOL",
    "build_conn_str",
    "ensure_runtime_dirs",
    "env_int",
    "env_str",
    "get_historical_timeframes",
]
