"""tick_engine CLI + service dispatcher.

Routes argv to one of two faces:
  python -m tick_engine service [--dry-run]   — 24/7 supervisor + scheduler
  python -m tick_engine <subcommand>           — operator CLI (backfill, check, ...)
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path

logger = logging.getLogger(__name__)
RESET_TICK_DATA_CONFIRM = "RESET_TICK_DATA"

_REQUIRED_TABLES = [
    "tick.SymbolMap", "tick.IngestRun", "tick.IngestState",
    "tick.FR40", "tick.DE40", "tick.HK50", "tick.J225", "tick.SP35",
    "tick.UK100", "tick.US500", "tick.US100", "tick.US30", "tick.GOLD", "tick.BTCUSD",
    "SEN.ActiveTask",
]
_REQUIRED_VIEWS = [
    "tick.v_IngestHealth", "tick.v_LatestQuote",
    "tick.v_FR40_Quote", "tick.v_DE40_Quote", "tick.v_HK50_Quote",
    "tick.v_J225_Quote", "tick.v_SP35_Quote", "tick.v_UK100_Quote",
    "tick.v_US500_Quote", "tick.v_US100_Quote", "tick.v_US30_Quote",
    "tick.v_GOLD_Quote", "tick.v_BTCUSD_Quote",
]


def _schema_preflight(schema: str) -> None:
    """Check that required DB objects exist; exit with a clear message if not."""
    from tick_engine.data_storage.db_connector import get_connection
    from tick_engine.env_safety import redact_operator_secrets

    try:
        conn = get_connection()
        conn.autocommit = True
        cursor = conn.cursor()
    except Exception as exc:
        print(
            f"\n[STARTUP] Cannot connect to SQL Server: {redact_operator_secrets(exc)}",
            file=sys.stderr,
        )
        print("[STARTUP] Check SQL_SERVER / SQL_UID / SQL_PWD in your .env file.", file=sys.stderr)
        sys.exit(1)

    try:
        missing: list[str] = []
        for obj in _REQUIRED_TABLES:
            cursor.execute(
                "SELECT CASE WHEN OBJECT_ID(?, 'U') IS NOT NULL THEN 1 ELSE 0 END", (obj,)
            )
            if cursor.fetchone()[0] != 1:
                missing.append(f"table {obj}")
        for obj in _REQUIRED_VIEWS:
            cursor.execute(
                "SELECT CASE WHEN OBJECT_ID(?, 'V') IS NOT NULL THEN 1 ELSE 0 END", (obj,)
            )
            if cursor.fetchone()[0] != 1:
                missing.append(f"view  {obj}")
    finally:
        conn.close()

    if missing:
        print(
            f"\n[STARTUP] Schema not ready — {len(missing)} missing object(s):",
            file=sys.stderr,
        )
        for item in missing:
            print(f"  MISSING {item}", file=sys.stderr)
        print(
            "\n[STARTUP] Run the setup script before starting tick_engine:\n"
            "  sqlcmd -S <server> -d SEN05_AutoTrading -i sql/tickdata_setup.sql\n"
            "  -- or --\n"
            "  python initial_setup/deploy_schema.py",
            file=sys.stderr,
        )
        sys.exit(2)


def _setup_logging(log_path: Path | None = None) -> None:
    """Attach tick-only handlers without hijacking the process root logger."""
    job_log_path = os.environ.get("TICK_ENGINE_JOB_LOG", "").strip()
    if job_log_path:
        log_path = Path(job_log_path)
    disable_file_log = os.environ.get("TICK_ENGINE_DISABLE_FILE_LOG", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if disable_file_log:
        log_path = None

    logger_names = (
        "tick_engine",
    )
    formatter = logging.Formatter(
        "%(asctime)sZ | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime

    def _new_handlers() -> list[logging.Handler]:
        handlers: list[logging.Handler] = [logging.StreamHandler()]
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(
                logging.handlers.RotatingFileHandler(
                    log_path,
                    maxBytes=10_000_000,
                    backupCount=5,
                    encoding="utf-8",
                )
            )
        for handler in handlers:
            handler.setFormatter(formatter)
            setattr(handler, "_tick_engine_handler", True)
        return handlers

    for logger_name in logger_names:
        lg = logging.getLogger(logger_name)
        for handler in list(lg.handlers):
            if getattr(handler, "_tick_engine_handler", False):
                lg.removeHandler(handler)
                handler.close()
        for handler in _new_handlers():
            lg.addHandler(handler)
        lg.setLevel(logging.INFO)
        lg.propagate = False


def _parse_datetime_ms(value: str) -> int:
    """Parse UTC ISO text or a millisecond timestamp for historical backfill."""
    raw = value.strip()
    if raw.isdigit():
        return int(raw)
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def _safe_token_payload(payload: dict[str, object]) -> dict[str, object]:
    secret_keys = {
        "accessToken",
        "refreshToken",
        "client_secret",
        "clientSecret",
    }
    safe: dict[str, object] = {}
    for key, value in payload.items():
        if key in secret_keys:
            safe[f"{key}_set"] = bool(value)
        elif "token" in key.lower() and value:
            safe[f"{key}_set"] = True
        else:
            safe[key] = value
    return safe


def _print_token_payload(payload: dict[str, object]) -> None:
    print(json.dumps(_safe_token_payload(payload), indent=2, sort_keys=True))


def _resolve_client_credentials(args: argparse.Namespace, settings) -> tuple[str, str]:
    client_id = getattr(args, "client_id", None) or settings.client_id
    client_secret = getattr(args, "client_secret", None) or settings.client_secret
    if not client_id:
        client_id = input("CTRADER_CLIENT_ID: ").strip()
    if not client_secret:
        client_secret = getpass("CTRADER_CLIENT_SECRET: ").strip()
    return client_id, client_secret


def _maybe_save_selected_account(
    accounts: list[dict[str, object]], account_id: int | None, trader_login: str | None
) -> None:
    from tick_engine.utils_support.token_store import update_cached_account

    if account_id is None and not trader_login:
        return
    selected = None
    for account in accounts:
        if account_id is not None and int(account["ctidTraderAccountId"]) == account_id:
            selected = account
            break
        if trader_login and str(account.get("traderLogin", "")) == str(trader_login):
            selected = account
            break
    if selected is None:
        wanted = account_id if account_id is not None else trader_login
        raise ValueError(f"Could not find granted cTrader account matching {wanted!r}")
    update_cached_account(
        int(selected["ctidTraderAccountId"]),
        trader_login=str(selected.get("traderLogin", "")) or None,
    )
    print(
        "saved_account_id="
        f"{selected['ctidTraderAccountId']} traderLogin={selected.get('traderLogin', '')}"
    )


def _run_spool_drain_command(settings, store, args: argparse.Namespace) -> int:
    from tick_engine.data_storage.spool import TickSpool
    from tick_engine.utils_support.lock_coord import exclusive_job_lock, raise_if_cancelled

    batch_size = int(args.batch_size or settings.batch_size)
    if batch_size <= 0:
        raise ValueError("--batch-size must be greater than 0")
    max_records = int(args.max_records)
    if max_records < 0:
        raise ValueError("--max-records must be >= 0")

    with exclusive_job_lock("spool-drain", label="spool-drain"):
        raise_if_cancelled()
        spool = TickSpool(settings.spool_path)
        total_inserted = 0
        total_processed = 0
        total_deleted = 0
        batches = 0
        print(f"spool_path={settings.spool_path}")
        print(f"spool_before={spool.count()}")
        while True:
            raise_if_cancelled()
            limit = batch_size
            if max_records:
                remaining = max_records - total_processed
                if remaining <= 0:
                    break
                limit = min(limit, remaining)
            batch = spool.read_batch(limit)
            if not batch:
                break
            max_seq = batch[-1][0]
            records = [record for _seq, record in batch]
            inserted = store.insert_ticks(records)
            deleted = spool.delete_through(max_seq)
            total_inserted += inserted
            total_processed += len(records)
            total_deleted += deleted
            batches += 1
            logger.info(
                "spool-drain batch=%s processed=%s inserted=%s deleted=%s max_seq=%s",
                batches,
                len(records),
                inserted,
                deleted,
                max_seq,
            )
            print(
                f"batch={batches} processed={len(records)} inserted={inserted} "
                f"deleted={deleted} total_processed={total_processed} total_inserted={total_inserted}"
            )
        print(f"spool_after={spool.count()}")
        print(f"processed={total_processed}")
        print(f"inserted={total_inserted}")
        print(f"deleted={total_deleted}")
    return 0


def _tick_service_status() -> tuple[bool, int | None]:
    from tick_engine.settings import SUPERVISOR_PID
    from tick_engine.utils_support.proc_utils import is_pid_alive, is_tick_engine_process

    try:
        pid = int(SUPERVISOR_PID.read_text(encoding="utf-8").strip())
    except Exception:
        return False, None
    if pid > 0 and is_pid_alive(pid) and is_tick_engine_process(pid):
        return True, pid
    return False, pid if pid > 0 else None


def _reset_blockers() -> list[str]:
    from tick_engine.utils_support.lock_coord import job_lock_status

    blockers: list[str] = []
    service_running, service_pid = _tick_service_status()
    if service_running:
        blockers.append(f"backfill service is running pid={service_pid}")
    for resource in ("ctrader-history", "spool-drain"):
        status = job_lock_status(resource)
        if status.get("active"):
            blockers.append(
                f"{resource} lock active owner={status.get('owner')} pid={status.get('pid')}"
            )
    return blockers


def _stats_total_rows(stats: dict[str, dict[str, object]]) -> int:
    return sum(int(item.get("rows") or 0) for item in stats.values())


def _fmt_tick_dt(value: object) -> str:
    return str(value) if value else "-"


def _print_tick_stats(stats: dict[str, dict[str, object]]) -> None:
    print(f"{'Symbol':<8} {'Rows':>14} {'FirstTickUtc':<24} {'LastTickUtc':<24}")
    print("-" * 74)
    for symbol, item in sorted(stats.items()):
        print(
            f"{symbol:<8} {int(item.get('rows') or 0):>14,} "
            f"{_fmt_tick_dt(item.get('first_tick_utc')):<24} "
            f"{_fmt_tick_dt(item.get('last_tick_utc')):<24}"
        )
    print("-" * 74)
    print(f"{'TOTAL':<8} {_stats_total_rows(stats):>14,}")


def _run_reset_tick_data_command(settings, store, args: argparse.Namespace) -> int:
    from tick_engine.data_storage.spool import TickSpool

    spool = TickSpool(settings.spool_path)
    stats = store.tick_row_stats_by_symbol()
    blockers = _reset_blockers()

    print("reset_tick_data_scope=tick tables + IngestState + SQLite spool")
    print("keeps=SymbolMap, IngestRun audit history, OAuth token cache, config")
    print(f"spool_path={settings.spool_path}")
    print(f"spool_count={spool.count()}")
    print()
    _print_tick_stats(stats)

    if blockers:
        print()
        print("reset_blocked_by:")
        for blocker in blockers:
            print(f"  - {blocker}")
        if not args.dry_run:
            return 75

    if args.dry_run:
        print()
        print("dry_run=true; no SQL rows or spool rows were deleted")
        print(f"to_execute_reset_pass=--confirm {RESET_TICK_DATA_CONFIRM}")
        return 0

    if args.confirm != RESET_TICK_DATA_CONFIRM:
        print(
            f"reset refused: pass --confirm {RESET_TICK_DATA_CONFIRM} to delete tick data",
            file=sys.stderr,
        )
        return 2

    result = store.reset_tick_data()
    spool_deleted = 0 if args.keep_spool else spool.clear()
    total_rows = int(result.get("total_rows") or 0)
    logger.warning(
        "RESET_TICK_DATA DONE | rows_deleted=%s | spool_deleted=%s | ingest_state_rows_reset=%s | truncate_used=%s",
        f"{total_rows:,}",
        f"{spool_deleted:,}",
        result.get("ingest_state_rows_reset"),
        result.get("truncate_used"),
    )
    print()
    print("reset_tick_data_done=true")
    print(f"rows_deleted={total_rows}")
    print(f"spool_deleted={spool_deleted}")
    print(f"ingest_state_rows_reset={result.get('ingest_state_rows_reset')}")
    print(f"truncate_used={result.get('truncate_used')}")
    return 0


@contextmanager
def _exclusive_job_lock_or_wait(
    resource: str,
    *,
    label: str,
    wait_seconds: int = 0,
):
    """Acquire a coarse job lock, optionally waiting so dashboard jobs can queue."""
    from tick_engine.utils_support.lock_coord import (
        JobLockConflict,
        exclusive_job_lock,
        raise_if_cancelled,
    )

    wait_seconds = max(0, int(wait_seconds or 0))
    deadline = time.monotonic() + wait_seconds
    started_waiting = time.monotonic()
    next_log = 0.0
    while True:
        raise_if_cancelled()
        try:
            with exclusive_job_lock(resource, label=label) as lock:
                waited = int(time.monotonic() - started_waiting)
                if waited > 0:
                    msg = f"{label} lock acquired after waiting {waited}s"
                    logger.info(msg)
                    print(msg)
                yield lock
                return
        except JobLockConflict as exc:
            if wait_seconds <= 0 or time.monotonic() >= deadline:
                raise
            now = time.monotonic()
            if now >= next_log:
                remaining = int(max(0, deadline - now))
                msg = f"{label} queued: waiting for {resource} lock ({exc}); remaining={remaining}s"
                logger.warning(msg)
                print(msg, file=sys.stderr)
                next_log = now + 30.0
            time.sleep(min(5.0, max(0.5, deadline - time.monotonic())))


def build_parser() -> argparse.ArgumentParser:
    """Build the operator CLI without importing the optional cTrader SDK."""
    parser = argparse.ArgumentParser(description="SEN05 cTrader FTMO tick provider")
    sub = parser.add_subparsers(dest="command", required=True)

    service_p = sub.add_parser("service", help="Run 24/7 supervisor + scheduler")
    service_p.add_argument("--dry-run", action="store_true", help="Print schedule and exit without spawning")
    sub.add_parser("show-config", help="Print non-secret runtime config")
    sub.add_parser("token-status", help="Print non-secret local OAuth token cache status")
    sub.add_parser("auth-url", help="Print cTrader OAuth authorization URL")

    exchange = sub.add_parser("exchange-code", help="Exchange OAuth code for token JSON")
    exchange.add_argument("--code", required=True)
    exchange.add_argument("--client-id")
    exchange.add_argument("--client-secret")
    exchange.add_argument("--save", action="store_true")

    oauth = sub.add_parser("oauth-login", help="Run local browser OAuth flow and save token cache")
    oauth.add_argument("--client-id")
    oauth.add_argument("--client-secret")
    oauth.add_argument("--redirect-uri")
    oauth.add_argument("--scope")
    oauth.add_argument("--timeout", type=int, default=120)
    oauth.add_argument("--no-browser", action="store_true")
    oauth.add_argument("--save-account-id", type=int)
    oauth.add_argument("--save-matching-login")

    refresh = sub.add_parser("refresh-token", help="Refresh access token from cached/env refresh token")
    refresh.add_argument("--client-id")
    refresh.add_argument("--client-secret")
    refresh.add_argument("--save", action="store_true")
    refresh.add_argument("--force", action="store_true")

    account_list = sub.add_parser("account-list", help="List cTrader accounts granted to CTRADER_ACCESS_TOKEN")
    account_list.add_argument("--save-account-id", type=int)
    account_list.add_argument("--save-matching-login")
    sub.add_parser("auth-check", help="Verify cTrader application + account auth without touching SQL")

    symbol_sync = sub.add_parser("symbol-sync", help="Fetch and match cTrader symbols")
    symbol_sync.add_argument("--apply", action="store_true")

    backfill = sub.add_parser("backfill", help="Backfill historical ticks")
    backfill.add_argument("--from", dest="from_value", required=True)
    backfill.add_argument("--to", dest="to_value", required=True)
    backfill.add_argument("--symbols", nargs="*")
    backfill.add_argument("--request-timeout", type=float)
    backfill.add_argument("--timeout", type=int)
    backfill.add_argument("--no-notify", action="store_true")
    backfill.add_argument("--wait-lock-seconds", type=int, default=0)

    batched_backfill = sub.add_parser(
        "backfill-batched",
        help="Run manual historical backfill as short reconnecting batches",
    )
    batched_backfill.add_argument("--from", dest="from_value", required=True)
    batched_backfill.add_argument("--to", dest="to_value", required=True)
    batched_backfill.add_argument("--symbols", nargs="*")
    batched_backfill.add_argument("--batch-minutes", type=int, default=60)
    batched_backfill.add_argument("--overlap-seconds", type=int, default=60)
    batched_backfill.add_argument("--wait-lock-seconds", type=int, default=300)
    batched_backfill.add_argument("--request-timeout", type=float)
    batched_backfill.add_argument("--timeout-per-batch", type=int)
    batched_backfill.add_argument("--sleep-seconds", type=float, default=0.0)
    batched_backfill.add_argument("--max-attempts", type=int, default=3)
    batched_backfill.add_argument("--retry-sleep-seconds", type=float, default=10.0)
    batched_backfill.add_argument("--retry-sleep-max-seconds", type=float, default=90.0)
    batched_backfill.add_argument("--notify-per-batch", action="store_true")
    batched_backfill.add_argument("--notify-summary", action="store_true")
    batched_backfill.add_argument("--no-notify-success-summary", action="store_true")
    batched_backfill.add_argument("--progress-file")
    batched_backfill.add_argument("--dry-run", action="store_true")

    history_depth = sub.add_parser("history-depth", help="Probe deepest available historical tick depth")
    history_depth.add_argument("--symbols", nargs="*")
    history_depth.add_argument("--max-days", type=int, default=20000)
    history_depth.add_argument("--to", dest="to_value")
    history_depth.add_argument("--probe-window-days", type=int, default=7)
    history_depth.add_argument("--timeout", type=int, default=300)
    history_depth.add_argument("--request-timeout", type=float)
    history_depth.add_argument("--json", action="store_true")

    check = sub.add_parser("check", help="Run read-only tick health checks")
    check.add_argument("--stale-seconds", type=int)
    check.add_argument("--json", action="store_true")
    check.add_argument("--notify", action="store_true")
    check.add_argument("--notify-summary", action="store_true")
    check.add_argument("--auto-repair-stale-runs", action="store_true")
    check.add_argument("--stale-run-min-age-seconds", type=int, default=300)

    datacheck_p = sub.add_parser("datacheck", help="Start read-only local tick data viewer (http://localhost:8060)")
    datacheck_p.add_argument("--host", default="127.0.0.1")
    datacheck_p.add_argument("--port", type=int, default=8060)
    datacheck_p.add_argument("--open-browser", action="store_true")

    repair = sub.add_parser("repair-stale-runs", help="Mark dead local RUNNING IngestRun rows as INTERRUPTED")
    repair.add_argument("--lookback-days", type=int, default=30)
    repair.add_argument("--min-age-seconds", type=int, default=0)
    repair.add_argument("--json", action="store_true")

    profile = sub.add_parser("build-activity-profile", help="Build learned tick activity profile")
    profile.add_argument("--lookback-days", type=int, default=30)
    profile.add_argument("--bucket-minutes", type=int, default=15)
    profile.add_argument("--active-min-ratio", type=float, default=0.25)
    profile.add_argument("--min-active-ticks", type=int, default=1)
    profile.add_argument("--json", action="store_true")

    spool_status = sub.add_parser("spool-status", help="Show local tick SQLite spool backlog")
    spool_status.add_argument("--json", action="store_true")

    spool_drain = sub.add_parser("spool-drain", help="Drain queued SQLite tick spool rows into SQL")
    spool_drain.add_argument("--max-records", type=int, default=5000)
    spool_drain.add_argument("--batch-size", type=int, default=None)

    reset_tick_data = sub.add_parser(
        "reset-tick-data",
        help="Danger: clear all per-symbol tick rows and reset ingest state",
    )
    reset_tick_data.add_argument("--dry-run", action="store_true")
    reset_tick_data.add_argument("--confirm")
    reset_tick_data.add_argument("--keep-spool", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    from tick_engine.env_safety import sanitize_ssl_keylogfile
    from tick_engine.utils_support.runtime import load_settings

    sanitize_ssl_keylogfile()

    parser = build_parser()
    args = parser.parse_args(argv)

    # --- service mode: delegate to BackendSupervisor ---
    if args.command in {"service", "start"}:
        from tick_engine.backend_engine import main as service_main
        dry_run_args = ["--dry-run"] if getattr(args, "dry_run", False) else []
        return int(service_main(dry_run_args) or 0)

    settings = load_settings()
    _setup_logging(settings.log_path)

    if args.command == "show-config":
        missing = ",".join(settings.missing_api_fields) or "none"
        sym_list = ",".join(s.local_symbol for s in settings.symbols)
        rows = [
            ("--- Environment ---", "", ""),
            ("env",                      settings.env,                                       "demo = cTrader Demo API  |  live = cTrader Live API environment"),
            ("endpoint",                 settings.endpoint_label,                            "WebSocket address used for one-shot cTrader history jobs"),
            ("schema",                   settings.schema,                                    "SQL Server schema where tick tables are stored"),
            ("--- Symbols ---", "", ""),
            ("symbols",                  sym_list,                                           f"{len(settings.symbols)} instruments being tracked"),
            ("missing_api_fields",       missing,                                            "none = all cTrader credentials are present"),
            ("--- cTrader Request Tuning ---", "", ""),
            ("response_timeout_seconds", f"{settings.response_timeout_seconds:g}",          "Per-request timeout for cTrader historical calls"),
            ("--- Runtime Paths ---", "", ""),
            ("spool_path",               str(settings.spool_path),                           "SQLite overflow buffer when SQL Server is unreachable"),
            ("log_path",                 str(settings.log_path),                             "Default manual CLI log file"),
        ]
        col_key = max(len(r[0]) for r in rows if r[1]) + 2
        col_val = max(len(r[1]) for r in rows if r[1]) + 2
        for key, val, desc in rows:
            if not val:
                print(f"\n{key}")
            else:
                print(f"  {key:<{col_key}}{val:<{col_val}}# {desc}")
        print()
        return 0

    if args.command == "token-status":
        from tick_engine.utils_support.token_store import token_status
        print(json.dumps(token_status(), indent=2, sort_keys=True))
        return 0

    if args.command == "auth-url":
        from tick_engine.utils_support.auth import build_authorization_url, redact_authorization_url
        if not settings.client_id:
            print("CTRADER_CLIENT_ID is required", file=sys.stderr)
            return 2
        print(
            redact_authorization_url(
                build_authorization_url(
                    settings.client_id,
                    settings.redirect_uri,
                    settings.oauth_scope,
                )
            )
        )
        return 0

    if args.command == "exchange-code":
        from tick_engine.utils_support.auth import CTraderAuthError, exchange_code_for_token
        from tick_engine.utils_support.token_store import save_token_cache
        client_id, client_secret = _resolve_client_credentials(args, settings)
        try:
            payload = exchange_code_for_token(
                client_id,
                client_secret,
                args.code,
                settings.redirect_uri,
            )
        except CTraderAuthError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.save:
            path = save_token_cache(
                payload,
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=settings.redirect_uri,
                scope=settings.oauth_scope,
            )
            print(f"saved_token_cache={path}")
        else:
            _print_token_payload(payload)
        return 0

    if args.command == "oauth-login":
        from tick_engine.historical_pulling import fetch_account_list
        from tick_engine.utils_support.auth import CTraderAuthError, run_local_oauth_login
        from tick_engine.utils_support.token_store import save_token_cache
        client_id, client_secret = _resolve_client_credentials(args, settings)
        redirect_uri = args.redirect_uri or settings.redirect_uri
        scope = args.scope or settings.oauth_scope
        try:
            payload = run_local_oauth_login(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                scope=scope,
                timeout_seconds=args.timeout,
                open_browser=not args.no_browser,
            )
        except CTraderAuthError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        path = save_token_cache(
            payload,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=scope,
        )
        print(f"saved_token_cache={path}")
        refreshed_settings = load_settings()
        if args.save_account_id is not None or args.save_matching_login:
            accounts = fetch_account_list(refreshed_settings)
            print(json.dumps(accounts, indent=2, sort_keys=True))
            _maybe_save_selected_account(accounts, args.save_account_id, args.save_matching_login)
        else:
            print("Run account-list next to choose the correct ctidTraderAccountId.")
        return 0

    if args.command == "refresh-token":
        from tick_engine.utils_support.auth import CTraderAuthError, refresh_access_token
        from tick_engine.utils_support.token_store import save_token_cache
        client_id, client_secret = _resolve_client_credentials(args, settings)
        if not settings.refresh_token:
            print("CTRADER_REFRESH_TOKEN or cached refreshToken is required", file=sys.stderr)
            return 2
        if not args.force and not settings.should_refresh_access_token:
            remaining = settings.access_token_seconds_remaining
            ttl = "unknown" if remaining is None else f"{remaining}s"
            print(
                f"token_refresh_skipped ttl={ttl} "
                f"safety={settings.token_refresh_safety_seconds}s (use --force to override)"
            )
            return 0
        try:
            payload = refresh_access_token(
                client_id,
                client_secret,
                settings.refresh_token,
            )
        except CTraderAuthError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.save:
            path = save_token_cache(
                payload,
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=settings.redirect_uri,
                scope=settings.oauth_scope,
                account_id=settings.account_id,
                trader_login=settings.trader_login,
            )
            print(f"saved_token_cache={path}")
        else:
            _print_token_payload(payload)
        return 0

    if args.command == "account-list":
        from tick_engine.historical_pulling import fetch_account_list
        try:
            accounts = fetch_account_list(settings)
        except Exception as exc:
            print(f"account-list failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(accounts, indent=2, sort_keys=True))
        _maybe_save_selected_account(
            accounts,
            getattr(args, "save_account_id", None),
            getattr(args, "save_matching_login", None),
        )
        return 0

    if args.command == "auth-check":
        from tick_engine.historical_pulling import verify_account_auth
        try:
            result = verify_account_auth(settings)
        except Exception as exc:
            print(f"auth-check failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "datacheck":
        from tick_engine.tick_datacheck.server import run_server as _dc_run
        return _dc_run(args.host, args.port, open_browser=args.open_browser)

    if args.command == "backfill-batched":
        from tick_engine.historical_pulling import run_batched_backfill

        try:
            from_ms = _parse_datetime_ms(args.from_value)
            to_ms = _parse_datetime_ms(args.to_value)
            if from_ms > to_ms:
                parser.error("--from must be <= --to")
            return run_batched_backfill(
                from_ms=from_ms,
                to_ms=to_ms,
                symbols=args.symbols,
                batch_minutes=args.batch_minutes,
                overlap_seconds=args.overlap_seconds,
                wait_lock_seconds=args.wait_lock_seconds,
                request_timeout=args.request_timeout,
                timeout_per_batch=args.timeout_per_batch,
                sleep_seconds=args.sleep_seconds,
                max_attempts=args.max_attempts,
                retry_sleep_seconds=args.retry_sleep_seconds,
                retry_sleep_max_seconds=args.retry_sleep_max_seconds,
                notify_per_batch=args.notify_per_batch,
                notify_summary=args.notify_summary,
                notify_success_summary=not args.no_notify_success_summary,
                progress_path=Path(args.progress_file) if args.progress_file else None,
                dry_run=args.dry_run,
            )
        except ValueError as exc:
            parser.error(str(exc))

    # Schema pre-flight: detect missing tables/views before any DB operation.
    _schema_preflight(settings.schema)

    from tick_engine.data_storage.store_sql import TickSqlStore

    store = TickSqlStore(
        settings.schema,
        settings.symbols,
        environment=settings.env,
        account_id=settings.account_id,
    )

    if args.command == "reset-tick-data":
        return _run_reset_tick_data_command(settings, store, args)

    if args.command == "repair-stale-runs":
        if args.lookback_days <= 0:
            parser.error("--lookback-days must be greater than 0")
        if args.min_age_seconds < 0:
            parser.error("--min-age-seconds must be >= 0")
        updated = store.mark_stale_runs_interrupted(
            lookback_days=args.lookback_days,
            min_age_seconds=args.min_age_seconds,
        )
        if args.json:
            print(json.dumps({"interrupted": updated, "lookback_days": args.lookback_days, "min_age_seconds": args.min_age_seconds}))
        else:
            print(f"interrupted={updated}")
        return 0

    if args.command == "check":
        from tick_engine.settings import TICK_SCHEDULED_PROGRESS_STALE_SECONDS
        from tick_engine.reporting.notification_policy import (
            build_tick_check_summary_notification,
            build_tick_check_notification,
            update_tick_check_incident_state,
            write_tick_check_notification_policy_event,
        )
        from tick_engine.reporting.notifications import flush_notifications, notify_tick_report
        from tick_engine.utils_support.health import TickCheckFinding, run_tick_check
        from tick_engine.utils_support.service_state import mark_stale_backfill_progress

        repaired = 0
        progress_repaired = mark_stale_backfill_progress(TICK_SCHEDULED_PROGRESS_STALE_SECONDS)
        if args.stale_run_min_age_seconds < 0:
            parser.error("--stale-run-min-age-seconds must be >= 0")
        if args.auto_repair_stale_runs:
            repaired = store.mark_stale_runs_interrupted(
                min_age_seconds=args.stale_run_min_age_seconds
            )
        report = run_tick_check(settings, store, stale_seconds=args.stale_seconds)
        report.data["auto_repair"] = {
            "enabled": bool(args.auto_repair_stale_runs),
            "stale_runs_interrupted": repaired,
            "stale_batch_progress": progress_repaired,
            "min_age_seconds": args.stale_run_min_age_seconds,
        }
        if progress_repaired:
            report.findings.insert(
                0,
                TickCheckFinding(
                    "INFO",
                    "stale_progress_repaired",
                    f"Auto-repaired {progress_repaired} stale batch progress file(s) before health check.",
                ),
            )
        if repaired:
            report.findings.insert(
                0,
                TickCheckFinding(
                    "INFO",
                    "stale_runs_repaired",
                    f"Auto-repaired {repaired} stale RUNNING IngestRun row(s) before health check.",
                ),
            )
        if args.notify:
            decision = build_tick_check_notification(report)
            write_tick_check_notification_policy_event(decision)
            recovery = update_tick_check_incident_state(report, decision)
            selected_decision = decision if decision.notify else recovery
            if selected_decision is None and args.notify_summary:
                selected_decision = build_tick_check_summary_notification(report)
            if selected_decision is not None and selected_decision.notify:
                notify_tick_report(
                    selected_decision.level,
                    selected_decision.title,
                    conclusion=selected_decision.conclusion,
                    action=selected_decision.action,
                    details=selected_decision.details,
                    technical=selected_decision.technical,
                    throttle_key=selected_decision.throttle_key,
                    throttle_seconds=selected_decision.throttle_seconds,
                )
                flush_notifications()
        if args.json:
            print(json.dumps(asdict(report), indent=2, sort_keys=True, default=str))
        else:
            print(report.to_text())
        return 1 if report.status == "ERROR" else 0

    if args.command == "spool-status":
        from tick_engine.data_storage.spool import TickSpool
        spool = TickSpool(settings.spool_path)
        payload = {"path": str(settings.spool_path), "count": spool.count()}
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"spool_path={payload['path']}")
            print(f"spool_count={payload['count']}")
        return 0

    if args.command == "spool-drain":
        from tick_engine.utils_support.lock_coord import CancelRequested, JobLockConflict
        try:
            return _run_spool_drain_command(settings, store, args)
        except ValueError as exc:
            parser.error(str(exc))
        except JobLockConflict as exc:
            print(f"spool-drain busy: {exc}", file=sys.stderr)
            return 75
        except CancelRequested as exc:
            logger.warning("spool-drain cancelled: %s", exc)
            print(f"cancelled={exc}")
            return 130

    if args.command == "build-activity-profile":
        from tick_engine.historical_pulling import build_activity_profile
        if args.lookback_days <= 0:
            parser.error("--lookback-days must be greater than 0")
        if args.bucket_minutes <= 0:
            parser.error("--bucket-minutes must be greater than 0")
        if args.active_min_ratio < 0 or args.active_min_ratio > 1:
            parser.error("--active-min-ratio must be between 0 and 1")
        if args.min_active_ticks <= 0:
            parser.error("--min-active-ticks must be greater than 0")
        profile = build_activity_profile(
            settings,
            store,
            lookback_days=args.lookback_days,
            bucket_minutes=args.bucket_minutes,
            active_min_ratio=args.active_min_ratio,
            min_active_ticks=args.min_active_ticks,
        )
        if args.json:
            print(json.dumps(profile, indent=2, sort_keys=True, default=str))
        else:
            symbols = profile.get("symbols", {})
            active = sum(int(item.get("active_buckets", 0)) for item in symbols.values())
            total = sum(int(item.get("total_buckets", 0)) for item in symbols.values())
            print(f"profile_path={profile.get('path')}")
            print(f"generated_at_utc={profile.get('generated_at_utc')}")
            print(f"lookback_days={profile.get('lookback_days')}")
            print(f"bucket_minutes={profile.get('bucket_minutes')}")
            print(f"symbols={len(symbols)} active_buckets={active}/{total}")
        return 0

    if args.command == "symbol-sync":
        from tick_engine.historical_pulling import sync_symbols
        try:
            lines = sync_symbols(settings, store if args.apply else None, apply=args.apply)
        except Exception as exc:
            print(f"symbol-sync failed: {exc}", file=sys.stderr)
            return 1
        for line in lines:
            print(line)
        return 0

    if args.command == "backfill":
        from tick_engine.historical_pulling import run_history_backfill
        from tick_engine.utils_support.lock_coord import (
            CancelRequested,
            JobLockConflict,
            raise_if_cancelled,
        )
        try:
            if args.wait_lock_seconds < 0:
                parser.error("--wait-lock-seconds must be >= 0")
            with _exclusive_job_lock_or_wait(
                "ctrader-history",
                label="backfill",
                wait_seconds=args.wait_lock_seconds,
            ):
                raise_if_cancelled()
                from_ms = _parse_datetime_ms(args.from_value)
                to_ms = _parse_datetime_ms(args.to_value)
                if from_ms > to_ms:
                    parser.error("--from must be <= --to")
                run_history_backfill(
                    settings,
                    store,
                    from_ms,
                    to_ms,
                    symbols=args.symbols,
                    request_timeout_seconds=args.request_timeout,
                    timeout_seconds=args.timeout,
                    notify=not args.no_notify,
                )
            return 0
        except ValueError as exc:
            parser.error(str(exc))
        except JobLockConflict as exc:
            print(f"backfill busy: {exc}", file=sys.stderr)
            return 75
        except CancelRequested as exc:
            logger.warning("backfill cancelled: %s", exc)
            print(f"cancelled={exc}")
            return 130

    if args.command == "history-depth":
        from tick_engine.historical_pulling import manual_line, probe_history_depth
        from tick_engine.utils_support.lock_coord import (
            CancelRequested,
            JobLockConflict,
            exclusive_job_lock,
            raise_if_cancelled,
        )
        try:
            with exclusive_job_lock("ctrader-history", label="history-depth"):
                raise_if_cancelled()
                results = probe_history_depth(
                    settings,
                    store,
                    symbols=args.symbols,
                    max_days=args.max_days,
                    to_timestamp_ms=_parse_datetime_ms(args.to_value) if args.to_value else None,
                    probe_window_days=args.probe_window_days,
                    timeout_seconds=args.timeout,
                    request_timeout_seconds=args.request_timeout,
                )
        except JobLockConflict as exc:
            print(manual_line("History", "busy", str(exc)), file=sys.stderr)
            return 75
        except CancelRequested as exc:
            logger.warning(manual_line("History", "cancelled", str(exc)))
            print(manual_line("History", "cancelled", str(exc)))
            return 130
        payload = [asdict(result) for result in results]
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        else:
            print("")
            for item in payload:
                print(
                    manual_line(
                        "History",
                        str(item["symbol"]),
                        f"depth_days={item['deepest_available_days']} | "
                        f"earliest={item['earliest_probe_from_utc']} | "
                        f"ctrader={item['ctrader_symbol_name']}({item['ctrader_symbol_id']})",
                    )
                )
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
