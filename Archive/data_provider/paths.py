"""Shared filesystem paths for the data provider package."""

from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent

SQL_DIR = PACKAGE_ROOT / "sql"
DASHBOARD_DIR = PACKAGE_ROOT / "dashboard"
DASHBOARD_STATIC_DIR = DASHBOARD_DIR / "static"

RUNTIME_DIR = PACKAGE_ROOT / "runtime"
LOG_DIR = RUNTIME_DIR / "logs"
CACHE_DIR = RUNTIME_DIR / "cache"
SPOOL_DIR = RUNTIME_DIR / "spool"
RUN_DIR = RUNTIME_DIR / "run"

PIPELINE_LOG = LOG_DIR / "pipeline.log"
CHECKER_LOG = LOG_DIR / "checker.log"
WS_LIVE_LOG = LOG_DIR / "ws_live.log"
WS_LIVE_PID = RUN_DIR / "ws_live_runtime.pid"
TV_TOKEN_CACHE = CACHE_DIR / "tv_token_cache.json"
WS_OVERFLOW_SPOOL = SPOOL_DIR / "overflow_spool.db"
VERIFIED_MARKET_GAPS = CACHE_DIR / "verified_market_gaps.json"


def ensure_runtime_dirs() -> None:
    """Create local runtime directories used by logs, cache, spool, and pid files."""
    for path in (LOG_DIR, CACHE_DIR, SPOOL_DIR, RUN_DIR):
        path.mkdir(parents=True, exist_ok=True)
