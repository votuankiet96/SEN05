"""Single source of runtime configuration: YAML config, fixed technical
defaults, paths, and the one ``Settings`` object every other module reads
from.

``Config.yaml`` at the repo root is the *operator interaction surface*
only: SQL Server connection, cTrader credentials, Discord webhook, which
symbols to backfill, and the one cadence knob operators actually tune
(how often the recurring backfill runs). Everything else — timeouts,
retry counts, lookback windows, guardrail thresholds — is a fixed
technical default declared below as a plain constant. Change those by
editing code, not by adding another YAML key: they are implementation
detail, not something an operator should need to discover or touch.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# When frozen by PyInstaller, `__file__` resolves inside the temporary
# _MEIxxxxx extraction folder, not the directory the .exe actually lives
# in -- Config.yaml/runtime/ must sit next to the .exe, not in that throwaway
# temp dir. `sys.executable` is the .exe's own real path in a frozen build;
# in a normal (non-frozen) run it's the python.exe interpreter, so this must
# only take that branch when `sys.frozen` is actually set.
if getattr(sys, "frozen", False):
    APP_ROOT = Path(sys.executable).resolve().parent
else:
    APP_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = APP_ROOT / "Config.yaml"

RUNTIME_DIR = APP_ROOT / "runtime"
LOG_DIR = RUNTIME_DIR / "logs"
RUN_DIR = RUNTIME_DIR / "run"
CACHE_DIR = RUNTIME_DIR / "cache"
SPOOL_DIR = RUNTIME_DIR / "spool"

SUPERVISOR_PID = RUN_DIR / "supervisor.pid"
SUPERVISOR_STOP = RUN_DIR / "supervisor.stop"
SERVICE_HEARTBEAT = RUN_DIR / "service_heartbeat.json"
TOKEN_CACHE = CACHE_DIR / "ctrader_ftmo_oauth.json"
SPOOL_DB = SPOOL_DIR / "tick_overflow.db"
INCIDENT_STATE = CACHE_DIR / "tick_health_incident_state.json"
LOG_FILE = LOG_DIR / "tick_engine.log"


def ensure_runtime_dirs() -> None:
    for d in (RUNTIME_DIR, LOG_DIR, RUN_DIR, CACHE_DIR, SPOOL_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# YAML load
# ---------------------------------------------------------------------------


def _get(d: dict, path: str, default: Any = None) -> Any:
    node: Any = d
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node if node is not None else default


def _load_yaml() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


# ---------------------------------------------------------------------------
# Symbol identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetSymbol:
    symbol_id: int
    local_symbol: str
    asset_type: str


@dataclass(frozen=True)
class RemoteSymbol:
    ctrader_symbol_id: int
    symbol_name: str
    digits: int | None = None
    description: str | None = None
    enabled: bool | None = None
    pip_position: int | None = None


#: Fallback if Config.yaml omits `symbols:`. Names only — this is just the
#: default *selection*; SymbolID/AssetType are never hardcoded here, they
#: are resolved from DWH.Dim_Symbol at the point of use
#: (sql_store.resolve_target_symbols), the same way dp_program_v3 treats
#: its own Config.yaml as "operator parameter surface" and DWH.Dim_Symbol
#: as the canonical universe.
DEFAULT_SYMBOLS: tuple[str, ...] = (
    "FR40", "DE40", "HK50", "J225", "SP35", "UK100",
    "US500", "US100", "US30", "GOLD", "BTCUSD",
)


# ---------------------------------------------------------------------------
# Fixed technical defaults — not in Config.yaml on purpose. These are
# implementation tuning, not operator decisions. Edit here to change them.
# ---------------------------------------------------------------------------

_SCHEMA = "tick"
_TOKEN_REFRESH_SAFETY_SECONDS = 24 * 60 * 60

# Tick request tuning
_BATCH_SIZE = 500
_FLUSH_SECONDS = 1.0
_RESPONSE_TIMEOUT_SECONDS = 60.0
_MAX_QUOTE_SIDE_AGE_SECONDS = 900

# Service job cadence (everything except the primary backfill interval,
# which is the one knob operators set in Config.yaml)
_TOKEN_REFRESH_INTERVAL_SECONDS = 1800
_CHECK_INTERVAL_SECONDS = 300
_SPOOL_DRAIN_INTERVAL_SECONDS = 600
_AUTO_REPAIR_STALE_RUNS = True
_STALE_RUN_MIN_AGE_SECONDS = 300
_BACKFILL_DELAY_SECONDS = 120

# Backfill: exactly 3 jobs.
#   recent-backfill — small fixed window, runs often.
#   daily-backfill  — wider fixed window, runs hourly, catches what
#                      recent-backfill's shorter window might miss.
#   gap-fill        — not a fixed window at all: queries the real
#                      per-symbol watermark (tick.IngestState) and backfills
#                      from there to now, so it self-heals after *any*
#                      length of downtime instead of guessing a lookback.
_RECENT_BACKFILL_INTERVAL_SECONDS = 1800  # 30 minutes
_RECENT_BACKFILL_LOOKBACK_MINUTES = 35
_RECENT_BACKFILL_BATCH_MINUTES = 35
_DAILY_BACKFILL_INTERVAL_SECONDS = 3600  # hourly
# A short safety-net window beyond recent-backfill's own 35 min, not a full
# day: gap-fill already self-heals any *real* gap from the true watermark,
# so re-fetching a full 24h/24 batches here every hour was pure redundant
# work -- and long enough that it could monopolize the single active-backfill
# slot for 30-60+ minutes, starving recent-backfill and causing genuine
# freshness gaps (observed: ~57 min stale across all 11 symbols while a
# 24-batch daily-backfill run was still retrying its way through batch 18).
_DAILY_BACKFILL_LOOKBACK_MINUTES = 120  # 2 hours
_DAILY_BACKFILL_BATCH_MINUTES = 120  # one batch -- keeps each run short
_GAP_FILL_INTERVAL_SECONDS = 3600  # hourly, plus at startup
_GAP_FILL_MAX_LOOKBACK_DAYS = 30
_GAP_FILL_BATCH_MINUTES = 360
_DAILY_HEALTH_SUMMARY_UTC = "23:45"

# `check`'s stale-tick alert threshold. A single cTrader reconnect retry
# (observed: fails ~35-45s after ACCOUNT_AUTH_SENT, then succeeds on the
# next attempt) can itself push the "no fresh tick yet" gap past the raw
# recent-backfill interval -- alerting exactly at that interval would page
# on every ordinary retry instead of only on a real, sustained gap. The
# margin below is sized for two retried cycles plus their own overhead
# (observed worst case so far: ~2522s), not just one.
_CHECK_STALE_SAFETY_MARGIN_SECONDS = 900
_CHECK_STALE_SECONDS = _RECENT_BACKFILL_INTERVAL_SECONDS + _CHECK_STALE_SAFETY_MARGIN_SECONDS

# Completed/stale runtime/run/backfill_batches/*.json progress files are
# diagnostic only (tick.IngestRun in SQL is the durable audit trail) --
# pruned once a day so they don't accumulate forever.
_BACKFILL_PROGRESS_RETENTION_SECONDS = 7 * 24 * 60 * 60

# Per-batch request/retry tuning
_SCHEDULED_REQUEST_TIMEOUT_SECONDS = 120.0
_SCHEDULED_BATCH_TIMEOUT_SECONDS = 900
_SCHEDULED_BACKFILL_MAX_ATTEMPTS = 5
_SCHEDULED_BACKFILL_RETRY_SLEEP_SECONDS = 15.0
_SCHEDULED_BACKFILL_RETRY_SLEEP_MAX_SECONDS = 180.0

# Runtime guardrails
_HEARTBEAT_INTERVAL_SECONDS = 30
_HEARTBEAT_STALE_SECONDS = 180
_SCHEDULED_PROGRESS_STALE_SECONDS = 1800
_CHILD_IDLE_TIMEOUT_SECONDS = 1800
_DISCORD_STALE_MIN_SECONDS = 3600

# Columnstore maintenance (sql_store.py::compress_tick_table): force-closes
# and compresses any tick table's open/small columnstore rowgroups, since
# ordinary trickle inserts are far below the ~102,400-row threshold that
# would otherwise let SQL Server skip the delta store. Daily is plenty --
# this only speeds up when compression happens, it's not needed for
# correctness. Replaces the old thin-history job (2026-08/09): that job
# deleted settled ticks down to zigzag price pivots to save space, which
# this columnstore design no longer needs (2026-09) -- and thinning was a
# real backtest-fidelity risk (any strategy logic finer than its reversal
# threshold, e.g. a tight trailing stop, would replay wrong against
# thinned data). This design keeps every tick forever instead.
_COMPRESS_TICKS_INTERVAL_SECONDS = 24 * 60 * 60  # daily

# Dedup-window maintenance (sql_store.py::refresh_dedup_window): rolls the
# small filtered unique index -- (TickTimeUtc, Bid, Ask), WHERE TickTimeUtc
# >= a recent cutoff -- forward by re-creating it with today's cutoff. A
# filtered index's WHERE clause is fixed at creation time (SQL Server never
# moves it on its own), so without this daily job the index would just grow
# from its original creation date forever, defeating the point. Separate
# job from compress-ticks on purpose: one maintains the CCI's own rowgroup
# compression, this one maintains a completely different index's filter
# boundary -- unrelated concerns, kept unrelated.
#
# Why a *window* index instead of the full-history one it replaced (2026-09):
# duplicates can only arise from a fetch window being re-processed, and
# every routine job is itself bounded (recent-backfill 35 min, daily-
# backfill 2h, gap-fill capped at gap_fill_max_lookback_days) -- so a tick
# older than that can only collide if a human deliberately re-runs an
# overlapping ad-hoc historical backfill, which insert_ticks()'s own
# pre-check (querying the exact batch's time range before inserting)
# already catches without needing any index. The one index-worthy risk left
# is spool-drain and a live backfill racing to insert the same rows after a
# transient SQL failure -- always near "now", never in old history. Sized
# well beyond the longest observed SQL-outage staleness in this project's
# history (under 24h) for margin. See insert_ticks()'s own docstring for
# the full reasoning and DEDUP KEY note in tickdata_setup.sql for the
# measured storage impact.
_REFRESH_DEDUP_WINDOW_INTERVAL_SECONDS = 24 * 60 * 60  # daily
_DEDUP_WINDOW_RETAIN_DAYS = 7

# Daily `symbol-sync --apply`: keeps tick.SymbolMap's real cTrader-fetched
# trading schedule + holiday calendar current (is_market_closed()'s primary
# source, see notify.py). Runs at startup too so a fresh install/restart
# doesn't wait a full day for its first real schedule data.
_SYMBOL_SYNC_DAILY_UTC = "00:20"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    # SQL Server
    sql_server: str
    sql_database: str
    sql_driver: str
    sql_port: str
    sql_uid: str
    sql_pwd: str
    sql_encrypt: str
    sql_trust_server_cert: str
    schema: str

    # cTrader / OAuth
    env: str
    host: str
    port: int
    client_id: str
    client_secret: str
    access_token: str
    refresh_token: str
    access_token_expires_at_utc: datetime | None
    token_refresh_safety_seconds: int
    account_id: int | None
    trader_login: str
    redirect_uri: str
    oauth_scope: str

    # Symbols the operator wants backfilled, by name only. SymbolID/AssetType
    # are resolved from DWH.Dim_Symbol (sql_store.resolve_target_symbols),
    # never carried here — this is a selection, not a data source.
    symbols: tuple[str, ...]

    # Discord
    discord_webhook_url: str

    # Tick request tuning
    batch_size: int
    flush_seconds: float
    response_timeout_seconds: float
    max_quote_side_age_seconds: int

    # Scheduler cadence
    token_refresh_interval_seconds: float
    check_interval_seconds: float
    spool_drain_interval_seconds: float
    auto_repair_stale_runs: bool
    stale_run_min_age_seconds: int
    backfill_delay_seconds: int
    recent_backfill_interval_seconds: float
    recent_backfill_lookback_minutes: int
    recent_backfill_batch_minutes: int
    daily_backfill_interval_seconds: float
    daily_backfill_lookback_minutes: int
    daily_backfill_batch_minutes: int
    gap_fill_interval_seconds: float
    gap_fill_max_lookback_days: int
    gap_fill_batch_minutes: int
    daily_health_summary_utc: str
    check_stale_seconds: int
    backfill_progress_retention_seconds: int
    compress_ticks_interval_seconds: float
    refresh_dedup_window_interval_seconds: float
    dedup_window_retain_days: int
    symbol_sync_daily_utc: str
    scheduled_request_timeout_seconds: float
    scheduled_batch_timeout_seconds: int
    scheduled_backfill_max_attempts: int
    scheduled_backfill_retry_sleep_seconds: float
    scheduled_backfill_retry_sleep_max_seconds: float

    # Runtime guardrails
    heartbeat_interval_seconds: int
    heartbeat_stale_seconds: int
    scheduled_progress_stale_seconds: int
    child_idle_timeout_seconds: int
    discord_stale_min_seconds: int

    @property
    def missing_api_fields(self) -> tuple[str, ...]:
        required = {
            "ctrader.client_id": self.client_id,
            "ctrader.client_secret": self.client_secret,
            "ctrader.access_token": self.access_token,
            "ctrader.account_id": self.account_id,
        }
        return tuple(name for name, value in required.items() if not value)

    @property
    def endpoint_label(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def access_token_seconds_remaining(self) -> int | None:
        if self.access_token_expires_at_utc is None:
            return None
        return int((self.access_token_expires_at_utc - datetime.now(timezone.utc)).total_seconds())

    @property
    def should_refresh_access_token(self) -> bool:
        if not self.access_token and self.refresh_token:
            return True
        remaining = self.access_token_seconds_remaining
        return remaining is not None and remaining <= self.token_refresh_safety_seconds

    @property
    def spool_path(self) -> Path:
        return SPOOL_DB

    @property
    def log_path(self) -> Path:
        return LOG_FILE


def build_conn_str(settings: "Settings | None" = None) -> str:
    s = settings or load_settings()
    parts = [
        f"DRIVER={{{s.sql_driver}}}",
        f"SERVER={s.sql_server}" + (f",{s.sql_port}" if s.sql_port else ""),
        f"DATABASE={s.sql_database}",
        f"Encrypt={s.sql_encrypt}",
        f"TrustServerCertificate={s.sql_trust_server_cert}",
    ]
    if s.sql_uid:
        parts.append(f"UID={s.sql_uid}")
        if s.sql_pwd:
            parts.append(f"PWD={s.sql_pwd}")
    else:
        parts.append("Trusted_Connection=yes")
    return ";".join(parts)


def redact_operator_secrets(value: object) -> str:
    """Redact sensitive config values from text before printing or logging."""
    text = str(value)
    try:
        s = load_settings()
        secrets = {s.client_id, s.client_secret, s.access_token, s.refresh_token, s.sql_pwd, s.discord_webhook_url}
    except Exception:
        secrets = set()
    for secret in sorted((v for v in secrets if v), key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    return text


_DEMO_HOST = "demo.ctraderapi.com"
_LIVE_HOST = "live.ctraderapi.com"
_PROTOBUF_PORT = 5035


def _parse_account_id(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError("ctrader.account_id must be the numeric ctidTraderAccountId") from exc


def load_settings() -> Settings:
    """Load runtime settings: operator values from ``Config.yaml`` and the
    OAuth token cache, everything else from the fixed defaults above."""
    from src.auth import load_token_cache, parse_utc_datetime

    ensure_runtime_dirs()
    doc = _load_yaml()
    token_cache = load_token_cache()

    env = str(_get(doc, "ctrader.env", "demo"))
    if env not in {"demo", "live"}:
        raise ValueError("ctrader.env must be 'demo' or 'live'")

    account_id_raw = _get(doc, "ctrader.account_id", "") or token_cache.get("ctidTraderAccountId", "")
    symbols_raw = _get(doc, "symbols") or list(DEFAULT_SYMBOLS)
    symbols = tuple(str(item).strip().upper() for item in symbols_raw if str(item).strip())

    sql = doc.get("sql_server", {}) if isinstance(doc.get("sql_server"), dict) else {}

    return Settings(
        sql_server=str(sql.get("server", "")) or "localhost",
        sql_database=str(sql.get("database", "")) or "SEN05_AutoTrading",
        sql_driver=str(sql.get("driver", "")) or "ODBC Driver 18 for SQL Server",
        sql_port=str(sql.get("port", "")),
        sql_uid=str(sql.get("uid", "")),
        sql_pwd=str(sql.get("pwd", "")),
        sql_encrypt=str(sql.get("encrypt", "no")),
        sql_trust_server_cert=str(sql.get("trust_server_certificate", "yes")),
        schema=_SCHEMA,
        env=env,
        host=_DEMO_HOST if env == "demo" else _LIVE_HOST,
        port=_PROTOBUF_PORT,
        client_id=str(_get(doc, "ctrader.client_id", "")) or str(token_cache.get("client_id", "")),
        client_secret=str(_get(doc, "ctrader.client_secret", "")) or str(token_cache.get("client_secret", "")),
        access_token=str(_get(doc, "ctrader.access_token", "")) or str(token_cache.get("accessToken", "")),
        refresh_token=str(_get(doc, "ctrader.refresh_token", "")) or str(token_cache.get("refreshToken", "")),
        access_token_expires_at_utc=parse_utc_datetime(token_cache.get("expires_at_utc")),
        token_refresh_safety_seconds=_TOKEN_REFRESH_SAFETY_SECONDS,
        account_id=_parse_account_id(account_id_raw),
        trader_login=str(_get(doc, "ctrader.trader_login", "")) or str(token_cache.get("traderLogin", "")),
        redirect_uri=str(token_cache.get("redirect_uri") or _get(doc, "ctrader.redirect_uri", "http://localhost:8765/callback")),
        oauth_scope=str(token_cache.get("scope") or _get(doc, "ctrader.oauth_scope", "accounts")),
        symbols=symbols,
        discord_webhook_url=str(_get(doc, "discord.webhook_url", "")),
        batch_size=_BATCH_SIZE,
        flush_seconds=_FLUSH_SECONDS,
        response_timeout_seconds=_RESPONSE_TIMEOUT_SECONDS,
        max_quote_side_age_seconds=_MAX_QUOTE_SIDE_AGE_SECONDS,
        token_refresh_interval_seconds=_TOKEN_REFRESH_INTERVAL_SECONDS,
        check_interval_seconds=_CHECK_INTERVAL_SECONDS,
        spool_drain_interval_seconds=_SPOOL_DRAIN_INTERVAL_SECONDS,
        auto_repair_stale_runs=_AUTO_REPAIR_STALE_RUNS,
        stale_run_min_age_seconds=_STALE_RUN_MIN_AGE_SECONDS,
        backfill_delay_seconds=_BACKFILL_DELAY_SECONDS,
        recent_backfill_interval_seconds=_RECENT_BACKFILL_INTERVAL_SECONDS,
        recent_backfill_lookback_minutes=_RECENT_BACKFILL_LOOKBACK_MINUTES,
        recent_backfill_batch_minutes=_RECENT_BACKFILL_BATCH_MINUTES,
        daily_backfill_interval_seconds=_DAILY_BACKFILL_INTERVAL_SECONDS,
        daily_backfill_lookback_minutes=_DAILY_BACKFILL_LOOKBACK_MINUTES,
        daily_backfill_batch_minutes=_DAILY_BACKFILL_BATCH_MINUTES,
        gap_fill_interval_seconds=_GAP_FILL_INTERVAL_SECONDS,
        gap_fill_max_lookback_days=_GAP_FILL_MAX_LOOKBACK_DAYS,
        gap_fill_batch_minutes=_GAP_FILL_BATCH_MINUTES,
        daily_health_summary_utc=_DAILY_HEALTH_SUMMARY_UTC,
        check_stale_seconds=_CHECK_STALE_SECONDS,
        backfill_progress_retention_seconds=_BACKFILL_PROGRESS_RETENTION_SECONDS,
        compress_ticks_interval_seconds=_COMPRESS_TICKS_INTERVAL_SECONDS,
        refresh_dedup_window_interval_seconds=_REFRESH_DEDUP_WINDOW_INTERVAL_SECONDS,
        dedup_window_retain_days=_DEDUP_WINDOW_RETAIN_DAYS,
        symbol_sync_daily_utc=_SYMBOL_SYNC_DAILY_UTC,
        scheduled_request_timeout_seconds=_SCHEDULED_REQUEST_TIMEOUT_SECONDS,
        scheduled_batch_timeout_seconds=_SCHEDULED_BATCH_TIMEOUT_SECONDS,
        scheduled_backfill_max_attempts=_SCHEDULED_BACKFILL_MAX_ATTEMPTS,
        scheduled_backfill_retry_sleep_seconds=_SCHEDULED_BACKFILL_RETRY_SLEEP_SECONDS,
        scheduled_backfill_retry_sleep_max_seconds=_SCHEDULED_BACKFILL_RETRY_SLEEP_MAX_SECONDS,
        heartbeat_interval_seconds=_HEARTBEAT_INTERVAL_SECONDS,
        heartbeat_stale_seconds=_HEARTBEAT_STALE_SECONDS,
        scheduled_progress_stale_seconds=_SCHEDULED_PROGRESS_STALE_SECONDS,
        child_idle_timeout_seconds=_CHILD_IDLE_TIMEOUT_SECONDS,
        discord_stale_min_seconds=_DISCORD_STALE_MIN_SECONDS,
    )


def sanitize_ssl_keylogfile() -> None:
    """Remove TLS keylog targets from the environment (can leak secrets, breaks some AV setups)."""
    value = os.environ.pop("SSLKEYLOGFILE", None)
    if value:
        os.environ["TICK_ENGINE_SSLKEYLOGFILE_IGNORED"] = value
