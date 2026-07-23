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
    ALERTS_LOG,
    APP_ROOT,
    BACKEND,
    BACKEND_STATE,
    BACKEND_STOP_FILE,
    CACHE_DIR,
    CANDLE_SNAPSHOT,
    DB,
    ENV_FILE,
    HISTORICAL,
    HISTORICAL_CANCEL_FILE,
    HISTORICAL_LOG,
    HISTORICAL_QUEUE_FILE,
    LIVE,
    LIVE_LOG,
    LOG_ARCHIVE_DIR,
    LOG_DIR,
    LOG_EMERGENCY_DIR,
    LOG_LOCK_DIR,
    LOGGING,
    NOTIFICATION,
    RUNTIME_DIR,
    RUN_DIR,
    SPOOL_DIR,
    STORAGE,
    SYSTEM_LOG,
    TRADINGVIEW,
    VERIFIED_MARKET_GAPS,
    WS_LIVE_PID,
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
    "ALERTS_LOG",
    "APP_ROOT",
    "BACKEND",
    "BACKEND_STATE",
    "BACKEND_STOP_FILE",
    "CACHE_DIR",
    "CANDLE_SNAPSHOT",
    "DB",
    "DEFAULT_N_BARS",
    "DIRECT_TFS",
    "ENV_FILE",
    "HISTORICAL",
    "HISTORICAL_CANCEL_FILE",
    "HISTORICAL_QUEUE_FILE",
    "HISTORICAL_LOG",
    "LIVE",
    "LIVE_LOG",
    "LOG_ARCHIVE_DIR",
    "LOGGING",
    "LOG_DIR",
    "LOG_EMERGENCY_DIR",
    "LOG_LOCK_DIR",
    "NOTIFICATION",
    "OVERNIGHT_GAP_MINUTES",
    "RUNTIME_DIR",
    "RUN_DIR",
    "SPOOL_DIR",
    "STORAGE",
    "SYMBOLS",
    "SYMBOL_OVERNIGHT_MINS",
    "SYSTEM_LOG",
    "TF_DISPLAY_ORDER",
    "TF_MINUTES",
    "TF_STAGING",
    "TRADINGVIEW",
    "VERIFIED_MARKET_GAPS",
    "WEEKEND_CLOSED",
    "WS_LIVE_PID",
    "WS_LIVE_STATE",
    "WS_OVERFLOW_SPOOL",
    "build_conn_str",
    "ensure_runtime_dirs",
    "env_int",
    "env_str",
    "get_historical_timeframes",
]
