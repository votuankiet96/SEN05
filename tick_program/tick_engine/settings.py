"""Central settings for tick_engine: paths, env constants, and helpers.

Merged from:
  tick_program/engine/engine_config.py
  tick_program/engine/paths.py
  tick_program/paths.py
  tick_program/config.py (build_conn_str only)
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from tick_engine.env_utils import env_flag, env_float, env_int

# ---------------------------------------------------------------------------
# App root and env file
# ---------------------------------------------------------------------------

APP_ROOT = Path(__file__).resolve().parents[1]  # project root (contains tick_engine/, config/, sql/)

_ENV_FILE = APP_ROOT / "config" / "tick_program.env"
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE, override=False)

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

RUNTIME_DIR = APP_ROOT / "runtime"
LOG_DIR = RUNTIME_DIR / "logs"
RUN_DIR = RUNTIME_DIR / "run"
CACHE_DIR = RUNTIME_DIR / "cache"
SPOOL_DIR = RUNTIME_DIR / "spool"
SQL_DIR = APP_ROOT / "sql"

# Well-known files
SUPERVISOR_PID = RUN_DIR / "supervisor.pid"
SUPERVISOR_STOP = RUN_DIR / "supervisor.stop"
SERVICE_HEARTBEAT = RUN_DIR / "service_heartbeat.json"
TV_TOKEN_CACHE = CACHE_DIR / "ctrader_ftmo_oauth.json"
WS_OVERFLOW_SPOOL = SPOOL_DIR / "tick_overflow.db"
VERIFIED_MARKET_GAPS = CACHE_DIR / "verified_market_gaps.json"
GAP_FILL_LAST_RUN = CACHE_DIR / "gap_fill_last_run.txt"
DEEP_REPAIR_LAST_RUN = CACHE_DIR / "deep_repair_last_run.txt"

# Operator log files (terminal-facing, intentionally small set)
SYSTEM_LOG = LOG_DIR / "system.log"
OPERATION_LOG = LOG_DIR / "operation.log"
MANUAL_LOG = LOG_DIR / "manual.log"

# Backward-compatible names used by older modules.
SUPERVISOR_LOG = OPERATION_LOG
AUTH_LOG = SYSTEM_LOG
HISTORY_LOG = MANUAL_LOG


def ensure_runtime_dirs() -> None:
    for d in (
        RUNTIME_DIR,
        LOG_DIR,
        RUN_DIR,
        CACHE_DIR,
        SPOOL_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# SQL Server
# ---------------------------------------------------------------------------

SQL_SERVER = os.environ.get("SQL_SERVER", "localhost")
SQL_DATABASE = os.environ.get("SQL_DATABASE", "SEN05_AutoTrading")
SQL_DRIVER = os.environ.get("SQL_DRIVER", "ODBC Driver 18 for SQL Server")
SQL_PORT = os.environ.get("SQL_PORT", "")
SQL_TDS_VERSION = os.environ.get("SQL_TDS_VERSION", "7.4")
SQL_UID = os.environ.get("SQL_UID", "")
SQL_PWD = os.environ.get("SQL_PWD", "")
SQL_ENCRYPT = os.environ.get("SQL_ENCRYPT", "no")
SQL_TRUST_SERVER_CERT = os.environ.get("SQL_TRUST_SERVER_CERT", "yes")
SQL_HEALTH_TIMEOUT_SECONDS = env_int("SQL_HEALTH_TIMEOUT_SECONDS", 30)


def build_conn_str() -> str:
    parts = [
        f"DRIVER={{{SQL_DRIVER}}}",
        f"SERVER={SQL_SERVER}" + (f",{SQL_PORT}" if SQL_PORT else ""),
        f"DATABASE={SQL_DATABASE}",
        f"Encrypt={SQL_ENCRYPT}",
        f"TrustServerCertificate={SQL_TRUST_SERVER_CERT}",
    ]
    if SQL_TDS_VERSION:
        parts.append(f"TDS_Version={SQL_TDS_VERSION}")
    if SQL_UID:
        parts.append(f"UID={SQL_UID}")
        if SQL_PWD:
            parts.append(f"PWD={SQL_PWD}")
    else:
        parts.append("Trusted_Connection=yes")
    return ";".join(parts)


# ---------------------------------------------------------------------------
# cTrader / FTMO OAuth
# ---------------------------------------------------------------------------

CTRADER_ENV = os.environ.get("CTRADER_ENV", "demo")
CTRADER_CLIENT_ID = os.environ.get("CTRADER_CLIENT_ID", "")
CTRADER_CLIENT_SECRET = os.environ.get("CTRADER_CLIENT_SECRET", "")
CTRADER_ACCESS_TOKEN = os.environ.get("CTRADER_ACCESS_TOKEN", "")
CTRADER_REFRESH_TOKEN = os.environ.get("CTRADER_REFRESH_TOKEN", "")
CTRADER_ACCOUNT_ID = os.environ.get("CTRADER_ACCOUNT_ID", "")
CTRADER_TRADER_LOGIN = os.environ.get("CTRADER_TRADER_LOGIN", "")
CTRADER_REDIRECT_URI = os.environ.get("CTRADER_REDIRECT_URI", "http://localhost:8765/callback")
CTRADER_OAUTH_SCOPE = os.environ.get("CTRADER_OAUTH_SCOPE", "accounts")

# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# ---------------------------------------------------------------------------
# cTrader historical request tuning
# ---------------------------------------------------------------------------

TICK_SCHEMA = os.environ.get("CTRADER_FTMO_TICK_SCHEMA", "tick")
TICK_BATCH_SIZE = env_int("CTRADER_FTMO_TICK_BATCH_SIZE", 500)
TICK_FLUSH_SECONDS = env_float("CTRADER_FTMO_TICK_FLUSH_SECONDS", 1.0)
TICK_RESPONSE_TIMEOUT_SECONDS = env_float("CTRADER_FTMO_TICK_RESPONSE_TIMEOUT_SECONDS", 60.0)
TICK_MAX_QUOTE_SIDE_AGE_SECONDS = env_int(
    "CTRADER_FTMO_TICK_MAX_QUOTE_SIDE_AGE_SECONDS", 900
)
TICK_SCHEDULED_REQUEST_TIMEOUT_SECONDS = env_float(
    "CTRADER_FTMO_TICK_SCHEDULED_REQUEST_TIMEOUT_SECONDS",
    max(90.0, TICK_RESPONSE_TIMEOUT_SECONDS),
)
TICK_SCHEDULED_BATCH_TIMEOUT_SECONDS = env_int(
    "CTRADER_FTMO_TICK_SCHEDULED_BATCH_TIMEOUT_SECONDS", 900
)
TICK_SCHEDULED_BACKFILL_MAX_ATTEMPTS = env_int(
    "CTRADER_FTMO_TICK_SCHEDULED_BACKFILL_MAX_ATTEMPTS", 3
)
TICK_SCHEDULED_BACKFILL_RETRY_SLEEP_SECONDS = env_float(
    "CTRADER_FTMO_TICK_SCHEDULED_BACKFILL_RETRY_SLEEP_SECONDS", 15.0
)
TICK_SCHEDULED_BACKFILL_RETRY_SLEEP_MAX_SECONDS = env_float(
    "CTRADER_FTMO_TICK_SCHEDULED_BACKFILL_RETRY_SLEEP_MAX_SECONDS", 90.0
)

# ---------------------------------------------------------------------------
# First-run seed
# ---------------------------------------------------------------------------

TICK_FIRST_RUN_AUTO_START = env_flag("CTRADER_FTMO_TICK_FIRST_RUN_AUTO_START", default=False)
TICK_FIRST_RUN_BACKFILL_DAYS = env_int("CTRADER_FTMO_TICK_FIRST_RUN_BACKFILL_DAYS", 30)

# ---------------------------------------------------------------------------
# Gap fill
# ---------------------------------------------------------------------------

TICK_STARTUP_GAP_FILL_ENABLED = env_flag(
    "CTRADER_FTMO_TICK_STARTUP_GAP_FILL_ENABLED", default=True
)
TICK_GAP_FILL_LOOKBACK_MINUTES = env_int("CTRADER_FTMO_TICK_GAP_FILL_LOOKBACK_MINUTES", 2880)
TICK_GAP_FILL_DELAY_SECONDS = env_int("CTRADER_FTMO_TICK_GAP_FILL_DELAY_SECONDS", 60)
TICK_GAP_FILL_NOTIFY = env_flag("CTRADER_FTMO_TICK_GAP_FILL_NOTIFY", default=True)
TICK_GAP_FILL_TIMEOUT_SECONDS = env_int("CTRADER_FTMO_TICK_GAP_FILL_TIMEOUT_SECONDS", 7200)

# ---------------------------------------------------------------------------
# Narrow repair
# ---------------------------------------------------------------------------

TICK_NARROW_REPAIR_ENABLED = env_flag("CTRADER_FTMO_TICK_NARROW_REPAIR_ENABLED", default=True)
TICK_NARROW_REPAIR_INTERVAL_MINUTES = env_int(
    "CTRADER_FTMO_TICK_NARROW_REPAIR_INTERVAL_MINUTES", 60
)
TICK_NARROW_REPAIR_LOOKBACK_MINUTES = env_int(
    "CTRADER_FTMO_TICK_NARROW_REPAIR_LOOKBACK_MINUTES", 180
)

# ---------------------------------------------------------------------------
# Service supervisor + scheduler intervals
# ---------------------------------------------------------------------------

TICK_TOKEN_REFRESH_INTERVAL_SECONDS = env_int("CTRADER_FTMO_TICK_TOKEN_REFRESH_INTERVAL_SECONDS", 1800)
TICK_CHECK_INTERVAL_SECONDS = env_int("CTRADER_FTMO_TICK_CHECK_INTERVAL_SECONDS", 300)
TICK_SPOOL_DRAIN_INTERVAL_SECONDS = env_int("CTRADER_FTMO_TICK_SPOOL_DRAIN_INTERVAL_SECONDS", 600)
TICK_AUTO_REPAIR_STALE_RUNS_ENABLED = env_flag("CTRADER_FTMO_TICK_AUTO_REPAIR_STALE_RUNS_ENABLED", default=True)
TICK_STALE_RUN_MIN_AGE_SECONDS = env_int("CTRADER_FTMO_TICK_STALE_RUN_MIN_AGE_SECONDS", 300)
TICK_DAILY_GAP_FILL_UTC = os.environ.get("CTRADER_FTMO_TICK_DAILY_GAP_FILL_UTC", "06:30").strip()
TICK_DAILY_MAINTAIN_UTC = os.environ.get("CTRADER_FTMO_TICK_DAILY_MAINTAIN_UTC", "03:00").strip()
TICK_DAILY_HEALTH_SUMMARY_UTC = os.environ.get(
    "CTRADER_FTMO_TICK_DAILY_HEALTH_SUMMARY_UTC", "23:45"
).strip()

# ---------------------------------------------------------------------------
# Backfill-only production cadence
# ---------------------------------------------------------------------------

TICK_BACKFILL_DELAY_SECONDS = env_int("CTRADER_FTMO_TICK_BACKFILL_DELAY_SECONDS", 120)
TICK_STARTUP_CATCHUP_ENABLED = env_flag("CTRADER_FTMO_TICK_STARTUP_CATCHUP_ENABLED", default=True)
TICK_STARTUP_CATCHUP_LOOKBACK_MINUTES = env_int(
    "CTRADER_FTMO_TICK_STARTUP_CATCHUP_LOOKBACK_MINUTES", 180
)
TICK_FREQUENT_BACKFILL_INTERVAL_SECONDS = env_int(
    "CTRADER_FTMO_TICK_FREQUENT_BACKFILL_INTERVAL_SECONDS", 600
)
TICK_FREQUENT_BACKFILL_LOOKBACK_MINUTES = env_int(
    "CTRADER_FTMO_TICK_FREQUENT_BACKFILL_LOOKBACK_MINUTES", 60
)
TICK_FREQUENT_BACKFILL_BATCH_MINUTES = env_int(
    "CTRADER_FTMO_TICK_FREQUENT_BACKFILL_BATCH_MINUTES", 60
)
TICK_HOURLY_REPAIR_ENABLED = env_flag("CTRADER_FTMO_TICK_HOURLY_REPAIR_ENABLED", default=True)
TICK_HOURLY_REPAIR_INTERVAL_SECONDS = env_int(
    "CTRADER_FTMO_TICK_HOURLY_REPAIR_INTERVAL_SECONDS", 3600
)
TICK_HOURLY_REPAIR_LOOKBACK_MINUTES = env_int(
    "CTRADER_FTMO_TICK_HOURLY_REPAIR_LOOKBACK_MINUTES", 360
)
TICK_HOURLY_REPAIR_BATCH_MINUTES = env_int(
    "CTRADER_FTMO_TICK_HOURLY_REPAIR_BATCH_MINUTES", 60
)
TICK_DAILY_DEEP_REPAIR_UTC = os.environ.get(
    "CTRADER_FTMO_TICK_DAILY_DEEP_REPAIR_UTC", "22:30"
).strip()
TICK_DAILY_DEEP_REPAIR_LOOKBACK_MINUTES = env_int(
    "CTRADER_FTMO_TICK_DAILY_DEEP_REPAIR_LOOKBACK_MINUTES", 1440
)
TICK_DAILY_DEEP_REPAIR_BATCH_MINUTES = env_int(
    "CTRADER_FTMO_TICK_DAILY_DEEP_REPAIR_BATCH_MINUTES", 60
)
TICK_DAILY_DEEP_REPAIR_TIMEOUT_SECONDS = env_int(
    "CTRADER_FTMO_TICK_DAILY_DEEP_REPAIR_TIMEOUT_SECONDS", 7200
)
TICK_DAILY_DEEP_REPAIR_COOLDOWN_HOURS = env_float(
    "CTRADER_FTMO_TICK_DAILY_DEEP_REPAIR_COOLDOWN_HOURS", 4.0
)

# ---------------------------------------------------------------------------
# Production runtime guardrails
# ---------------------------------------------------------------------------

TICK_SERVICE_HEARTBEAT_INTERVAL_SECONDS = env_int(
    "CTRADER_FTMO_TICK_SERVICE_HEARTBEAT_INTERVAL_SECONDS", 30
)
TICK_SERVICE_HEARTBEAT_STALE_SECONDS = env_int(
    "CTRADER_FTMO_TICK_SERVICE_HEARTBEAT_STALE_SECONDS", 180
)
TICK_SCHEDULED_PROGRESS_STALE_SECONDS = env_int(
    "CTRADER_FTMO_TICK_SCHEDULED_PROGRESS_STALE_SECONDS", 1800
)
TICK_CHILD_IDLE_TIMEOUT_SECONDS = env_int(
    "CTRADER_FTMO_TICK_CHILD_IDLE_TIMEOUT_SECONDS", 1800
)

# ---------------------------------------------------------------------------
# Symbols universe (tick.SymbolMap target set — 11 instruments)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Discord notification policy
# ---------------------------------------------------------------------------

TICK_DISCORD_STALE_MIN_SECONDS = env_int(
    "CTRADER_FTMO_TICK_DISCORD_STALE_MIN_SECONDS", 3600
)
TICK_DISCORD_SESSION_GRACE_BUCKETS = env_int(
    "CTRADER_FTMO_TICK_DISCORD_SESSION_GRACE_BUCKETS", 1
)
TICK_DISCORD_SUPPRESS_WHILE_DATA_JOB_RUNNING = env_flag(
    "CTRADER_FTMO_TICK_DISCORD_SUPPRESS_WHILE_DATA_JOB_RUNNING", default=True
)

SYMBOLS: list[dict] = [
    {"symbol_id": 2, "tv_symbol": "FR40", "asset_type": "Indice"},
    {"symbol_id": 3, "tv_symbol": "DE40", "asset_type": "Indice"},
    {"symbol_id": 4, "tv_symbol": "HK50", "asset_type": "Indice"},
    {"symbol_id": 5, "tv_symbol": "J225", "asset_type": "Indice"},
    {"symbol_id": 6, "tv_symbol": "SP35", "asset_type": "Indice"},
    {"symbol_id": 7, "tv_symbol": "UK100", "asset_type": "Indice"},
    {"symbol_id": 8, "tv_symbol": "US500", "asset_type": "Indice"},
    {"symbol_id": 9, "tv_symbol": "US100", "asset_type": "Indice"},
    {"symbol_id": 10, "tv_symbol": "US30", "asset_type": "Indice"},
    {"symbol_id": 56, "tv_symbol": "GOLD", "asset_type": "Metal"},
    {"symbol_id": 81, "tv_symbol": "BTCUSD", "asset_type": "Crypto"},
]
