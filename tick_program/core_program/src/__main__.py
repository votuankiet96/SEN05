"""tick_program CLI + service dispatcher.

  python -m src service [--dry-run]   — 24/7 supervisor + scheduler
  python -m src <subcommand>           — operator CLI (backfill, check, ...)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from getpass import getpass

RESET_TICK_DATA_CONFIRM = "RESET_TICK_DATA"

# Objects this program reads or writes.
_REQUIRED_TABLES = [
    "tick.SymbolMap", "tick.IngestRun", "tick.IngestState",
    "tick.FR40", "tick.DE40", "tick.HK50", "tick.J225", "tick.SP35",
    "tick.UK100", "tick.US500", "tick.US100", "tick.US30", "tick.GOLD", "tick.BTCUSD",
]


def _schema_preflight(schema: str) -> None:
    from src.configuration import redact_operator_secrets
    from src.sql_store import get_connection

    try:
        conn = get_connection()
        conn.autocommit = True
        cursor = conn.cursor()
    except Exception as exc:
        print(f"\n[STARTUP] Cannot connect to SQL Server: {redact_operator_secrets(exc)}", file=sys.stderr)
        print("[STARTUP] Check sql_server.* in Config.yaml.", file=sys.stderr)
        sys.exit(1)
    try:
        missing = []
        for obj in _REQUIRED_TABLES:
            cursor.execute("SELECT CASE WHEN OBJECT_ID(?, 'U') IS NOT NULL THEN 1 ELSE 0 END", (obj,))
            if cursor.fetchone()[0] != 1:
                missing.append(obj)
    finally:
        conn.close()
    if missing:
        print(f"\n[STARTUP] Schema not ready — {len(missing)} missing table(s):", file=sys.stderr)
        for item in missing:
            print(f"  MISSING {item}", file=sys.stderr)
        print("\n[STARTUP] Run: sqlcmd -S <server> -d <database> -i scripts/sql/tickdata_setup.sql", file=sys.stderr)
        sys.exit(2)


def _parse_datetime_ms(value: str) -> int:
    raw = value.strip()
    if raw.isdigit():
        return int(raw)
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def _resolve_client_credentials(args: argparse.Namespace, settings) -> tuple[str, str]:
    client_id = getattr(args, "client_id", None) or settings.client_id
    client_secret = getattr(args, "client_secret", None) or settings.client_secret
    if not client_id:
        client_id = input("ctrader.client_id: ").strip()
    if not client_secret:
        client_secret = getpass("ctrader.client_secret: ").strip()
    return client_id, client_secret


def _maybe_save_selected_account(accounts: list[dict], account_id: int | None, trader_login: str | None) -> None:
    from src.auth import update_cached_account

    if account_id is None and not trader_login:
        return
    selected = next(
        (a for a in accounts if (account_id is not None and int(a["ctidTraderAccountId"]) == account_id)
         or (trader_login and str(a.get("traderLogin", "")) == str(trader_login))),
        None,
    )
    if selected is None:
        raise ValueError(f"Could not find granted cTrader account matching {account_id if account_id is not None else trader_login!r}")
    update_cached_account(int(selected["ctidTraderAccountId"]), trader_login=str(selected.get("traderLogin", "")) or None)
    print(f"saved_account_id={selected['ctidTraderAccountId']} traderLogin={selected.get('traderLogin', '')}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEN05 cTrader FTMO tick backfill provider")
    sub = parser.add_subparsers(dest="command", required=True)

    service_p = sub.add_parser("service", help="Run 24/7 supervisor + scheduler")
    service_p.add_argument("--dry-run", action="store_true")
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

    account_list = sub.add_parser("account-list", help="List cTrader accounts granted to the access token")
    account_list.add_argument("--save-account-id", type=int)
    account_list.add_argument("--save-matching-login")
    sub.add_parser("auth-check", help="Verify cTrader application + account auth without touching SQL")

    symbol_sync = sub.add_parser("symbol-sync", help="Fetch and match cTrader symbols")
    symbol_sync.add_argument("--apply", action="store_true")

    backfill = sub.add_parser("backfill", help="Backfill historical ticks over one window (one cTrader connection)")
    backfill.add_argument("--from", dest="from_value", required=True)
    backfill.add_argument("--to", dest="to_value", required=True)
    backfill.add_argument("--symbols", nargs="*")
    backfill.add_argument("--request-timeout", type=float)
    backfill.add_argument("--timeout", type=int)
    backfill.add_argument("--no-notify", action="store_true")
    backfill.add_argument("--wait-lock-seconds", type=int, default=0)

    batched = sub.add_parser("backfill-batched", help="Run backfill as short reconnecting batches")
    batched.add_argument("--from", dest="from_value", required=True)
    batched.add_argument("--to", dest="to_value", required=True)
    batched.add_argument("--symbols", nargs="*")
    batched.add_argument("--batch-minutes", type=int, default=60)
    batched.add_argument("--overlap-seconds", type=int, default=60)
    batched.add_argument("--wait-lock-seconds", type=int, default=300)
    batched.add_argument("--request-timeout", type=float)
    batched.add_argument("--timeout-per-batch", type=int)
    batched.add_argument("--sleep-seconds", type=float, default=0.0)
    batched.add_argument("--max-attempts", type=int, default=3)
    batched.add_argument("--retry-sleep-seconds", type=float, default=10.0)
    batched.add_argument("--retry-sleep-max-seconds", type=float, default=90.0)
    batched.add_argument("--notify-summary", action="store_true")
    batched.add_argument("--no-notify-success-summary", action="store_true")
    batched.add_argument("--progress-file")
    batched.add_argument("--dry-run", action="store_true")

    gap_fill = sub.add_parser("gap-fill-backfill", help="Backfill from the real per-symbol watermark to now (self-healing, any gap size)")
    gap_fill.add_argument("--max-lookback-days", type=int, required=True)
    gap_fill.add_argument("--batch-minutes", type=int, default=360)
    gap_fill.add_argument("--overlap-seconds", type=int, default=60)
    gap_fill.add_argument("--delay-seconds", type=int, default=120)
    gap_fill.add_argument("--wait-lock-seconds", type=int, default=0)
    gap_fill.add_argument("--request-timeout", type=float)
    gap_fill.add_argument("--timeout-per-batch", type=int)
    gap_fill.add_argument("--max-attempts", type=int, default=5)
    gap_fill.add_argument("--retry-sleep-seconds", type=float, default=15.0)
    gap_fill.add_argument("--retry-sleep-max-seconds", type=float, default=180.0)
    gap_fill.add_argument("--notify-summary", action="store_true")

    check = sub.add_parser("check", help="Run read-only tick health checks")
    check.add_argument("--stale-seconds", type=int)
    check.add_argument("--json", action="store_true")
    check.add_argument("--notify", action="store_true")
    check.add_argument("--notify-summary", action="store_true")
    check.add_argument("--auto-repair-stale-runs", action="store_true")
    check.add_argument("--stale-run-min-age-seconds", type=int, default=300)

    repair = sub.add_parser("repair-stale-runs", help="Mark dead local RUNNING IngestRun rows as STOPPED")
    repair.add_argument("--lookback-days", type=int, default=30)
    repair.add_argument("--min-age-seconds", type=int, default=0)
    repair.add_argument("--json", action="store_true")

    spool_status = sub.add_parser("spool-status", help="Show local tick SQLite spool backlog")
    spool_status.add_argument("--json", action="store_true")

    spool_drain = sub.add_parser("spool-drain", help="Drain queued SQLite tick spool rows into SQL")
    spool_drain.add_argument("--max-records", type=int, default=5000)
    spool_drain.add_argument("--batch-size", type=int, default=None)

    compress = sub.add_parser(
        "compress-ticks",
        help="Force-compress tick tables' columnstore rowgroups (see sql_store.py::compress_tick_table)",
    )
    compress.add_argument("--symbols", nargs="*", help="Restrict to these local symbols only (default: all configured)")

    refresh_dedup = sub.add_parser(
        "refresh-dedup-window",
        help="Roll the recent-window dedup unique index forward (see sql_store.py::refresh_dedup_window)",
    )
    refresh_dedup.add_argument("--symbols", nargs="*", help="Restrict to these local symbols only (default: all configured)")
    refresh_dedup.add_argument("--retain-days", type=int, default=None, help="Default: settings.dedup_window_retain_days")

    reset = sub.add_parser("reset-tick-data", help="Danger: clear all tick rows and reset ingest state")
    reset.add_argument("--dry-run", action="store_true")
    reset.add_argument("--confirm")
    reset.add_argument("--keep-spool", action="store_true")

    chart = sub.add_parser("chart", help="Run the local read-only tick chart (http.server + lightweight-charts, no writes)")
    chart.add_argument("--host", default="127.0.0.1")
    chart.add_argument("--port", type=int, default=8060)
    chart.add_argument("--open-browser", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    from src.configuration import load_settings, sanitize_ssl_keylogfile

    sanitize_ssl_keylogfile()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in {"service", "start"}:
        from src.runtime import main as service_main

        return int(service_main(["--dry-run"] if getattr(args, "dry_run", False) else []) or 0)

    settings = load_settings()

    if args.command == "show-config":
        print(f"env               {settings.env}")
        print(f"endpoint          {settings.endpoint_label}")
        print(f"schema            {settings.schema}")
        print(f"symbols           {','.join(settings.symbols)}  (names only — resolved against DWH.Dim_Symbol per DB command)")
        print(f"missing_fields    {','.join(settings.missing_api_fields) or 'none'}")
        print(f"spool_path        {settings.spool_path}")
        print(f"log_path          {settings.log_path}")
        return 0

    if args.command == "token-status":
        from src.auth import token_status

        print(json.dumps(token_status(), indent=2, sort_keys=True))
        return 0

    if args.command == "auth-url":
        from src.auth import build_authorization_url, redact_authorization_url

        if not settings.client_id:
            print("ctrader.client_id is required", file=sys.stderr)
            return 2
        print(redact_authorization_url(build_authorization_url(settings.client_id, settings.redirect_uri, settings.oauth_scope)))
        return 0

    if args.command == "exchange-code":
        from src.auth import CTraderAuthError, exchange_code_for_token, save_token_cache

        client_id, client_secret = _resolve_client_credentials(args, settings)
        try:
            payload = exchange_code_for_token(client_id, client_secret, args.code, settings.redirect_uri)
        except CTraderAuthError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.save:
            path = save_token_cache(payload, client_id=client_id, client_secret=client_secret,
                                     redirect_uri=settings.redirect_uri, scope=settings.oauth_scope)
            print(f"saved_token_cache={path}")
        return 0

    if args.command == "oauth-login":
        from src.auth import CTraderAuthError, run_local_oauth_login, save_token_cache
        from src.ctrader_client import fetch_account_list

        client_id, client_secret = _resolve_client_credentials(args, settings)
        redirect_uri, scope = args.redirect_uri or settings.redirect_uri, args.scope or settings.oauth_scope
        try:
            payload = run_local_oauth_login(client_id, client_secret, redirect_uri, scope,
                                             timeout_seconds=args.timeout, open_browser=not args.no_browser)
        except CTraderAuthError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        path = save_token_cache(payload, client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri, scope=scope)
        print(f"saved_token_cache={path}")
        refreshed = load_settings()
        if args.save_account_id is not None or args.save_matching_login:
            accounts = fetch_account_list(refreshed)
            print(json.dumps(accounts, indent=2, sort_keys=True))
            _maybe_save_selected_account(accounts, args.save_account_id, args.save_matching_login)
        else:
            print("Run account-list next to choose the correct ctidTraderAccountId.")
        return 0

    if args.command == "refresh-token":
        from src.auth import CTraderAuthError, refresh_access_token, save_token_cache

        client_id, client_secret = _resolve_client_credentials(args, settings)
        if not settings.refresh_token:
            print("ctrader.refresh_token or cached refreshToken is required", file=sys.stderr)
            return 2
        if not args.force and not settings.should_refresh_access_token:
            print(f"token_refresh_skipped ttl={settings.access_token_seconds_remaining}s (use --force to override)")
            return 0
        try:
            payload = refresh_access_token(client_id, client_secret, settings.refresh_token)
        except CTraderAuthError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.save:
            path = save_token_cache(payload, client_id=client_id, client_secret=client_secret,
                                     redirect_uri=settings.redirect_uri, scope=settings.oauth_scope,
                                     account_id=settings.account_id, trader_login=settings.trader_login)
            print(f"saved_token_cache={path}")
        return 0

    if args.command == "account-list":
        from src.ctrader_client import fetch_account_list

        try:
            accounts = fetch_account_list(settings)
        except Exception as exc:
            print(f"account-list failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(accounts, indent=2, sort_keys=True))
        _maybe_save_selected_account(accounts, args.save_account_id, args.save_matching_login)
        return 0

    if args.command == "auth-check":
        from src.ctrader_client import verify_account_auth

        try:
            print(json.dumps(verify_account_auth(settings), indent=2, sort_keys=True))
        except Exception as exc:
            print(f"auth-check failed: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.command == "backfill-batched":
        from src.backfill import run_batched_backfill

        try:
            from_ms, to_ms = _parse_datetime_ms(args.from_value), _parse_datetime_ms(args.to_value)
            if from_ms > to_ms:
                parser.error("--from must be <= --to")
            return run_batched_backfill(
                from_ms=from_ms, to_ms=to_ms, symbols=args.symbols, batch_minutes=args.batch_minutes,
                overlap_seconds=args.overlap_seconds, wait_lock_seconds=args.wait_lock_seconds,
                request_timeout=args.request_timeout, timeout_per_batch=args.timeout_per_batch,
                sleep_seconds=args.sleep_seconds, max_attempts=args.max_attempts,
                retry_sleep_seconds=args.retry_sleep_seconds, retry_sleep_max_seconds=args.retry_sleep_max_seconds,
                notify_summary=args.notify_summary, notify_success_summary=not args.no_notify_success_summary,
                progress_path=__import__("pathlib").Path(args.progress_file) if args.progress_file else None,
                dry_run=args.dry_run,
            )
        except ValueError as exc:
            parser.error(str(exc))

    # Everything below needs the SQL tick schema.
    _schema_preflight(settings.schema)
    from src.sql_store import TickSqlStore, resolve_target_symbols

    targets = resolve_target_symbols(settings.symbols)
    store = TickSqlStore(settings.schema, targets, environment=settings.env, account_id=settings.account_id)

    if args.command == "gap-fill-backfill":
        from src.backfill import run_gap_fill_backfill

        return run_gap_fill_backfill(
            settings, store, max_lookback_days=args.max_lookback_days, batch_minutes=args.batch_minutes,
            overlap_seconds=args.overlap_seconds, delay_seconds=args.delay_seconds, wait_lock_seconds=args.wait_lock_seconds,
            request_timeout=args.request_timeout, timeout_per_batch=args.timeout_per_batch,
            max_attempts=args.max_attempts, retry_sleep_seconds=args.retry_sleep_seconds,
            retry_sleep_max_seconds=args.retry_sleep_max_seconds, notify_summary=args.notify_summary,
        )

    if args.command == "reset-tick-data":
        from src.spool import TickSpool

        spool = TickSpool(settings.spool_path)
        stats = store.tick_row_stats_by_symbol()
        blockers = []
        for resource in ("ctrader-history", "spool-drain"):
            from src.spool import job_lock_status

            status = job_lock_status(resource)
            if status.get("active"):
                blockers.append(f"{resource} lock active owner={status.get('owner')} pid={status.get('pid')}")
        print("reset_tick_data_scope=tick tables + IngestState + SQLite spool")
        print("keeps=SymbolMap, IngestRun audit history, OAuth token cache, config")
        print(f"spool_count={spool.count()}")
        for symbol, item in sorted(stats.items()):
            print(f"{symbol:<8} rows={int(item.get('rows') or 0):,}")
        if blockers:
            print("reset_blocked_by:")
            for b in blockers:
                print(f"  - {b}")
            if not args.dry_run:
                return 75
        if args.dry_run:
            print(f"dry_run=true; to execute: --confirm {RESET_TICK_DATA_CONFIRM}")
            return 0
        if args.confirm != RESET_TICK_DATA_CONFIRM:
            print(f"reset refused: pass --confirm {RESET_TICK_DATA_CONFIRM} to delete tick data", file=sys.stderr)
            return 2
        result = store.reset_tick_data()
        spool_deleted = 0 if args.keep_spool else spool.clear()
        print(f"rows_deleted={result.get('total_rows')}")
        print(f"spool_deleted={spool_deleted}")
        return 0

    if args.command == "repair-stale-runs":
        updated = store.mark_stale_runs_stopped(lookback_days=args.lookback_days, min_age_seconds=args.min_age_seconds)
        print(json.dumps({"stopped": updated}) if args.json else f"stopped={updated}")
        return 0

    if args.command == "check":
        from src.notify import (
            flush_notifications,
            mark_stale_backfill_progress,
            notify_check_result,
            notify_daily_summary,
            prune_backfill_progress,
            run_tick_check,
        )

        progress_repaired = mark_stale_backfill_progress(settings.scheduled_progress_stale_seconds)
        repaired = store.mark_stale_runs_stopped(min_age_seconds=args.stale_run_min_age_seconds) if args.auto_repair_stale_runs else 0
        report = run_tick_check(settings, store, stale_seconds=args.stale_seconds)
        report.data["auto_repair"] = {"stale_runs_interrupted": repaired, "stale_batch_progress": progress_repaired}
        if args.notify_summary:
            report.data["auto_repair"]["backfill_progress_pruned"] = prune_backfill_progress(
                settings.backfill_progress_retention_seconds
            )
        if args.notify:
            notify_check_result(report)
            if args.notify_summary:
                notify_daily_summary(report)
            flush_notifications()
        print(json.dumps(asdict(report), indent=2, sort_keys=True, default=str) if args.json else report.to_text())
        return 1 if report.status == "ERROR" else 0

    if args.command == "spool-status":
        from src.spool import TickSpool

        spool = TickSpool(settings.spool_path)
        payload = {"path": str(settings.spool_path), "count": spool.count(), "quarantine_count": spool.quarantine_count()}
        print(json.dumps(payload, indent=2, sort_keys=True) if args.json else "\n".join(f"{k}={v}" for k, v in payload.items()))
        return 0

    if args.command == "spool-drain":
        from src.spool import CancelRequested, JobLockConflict, TickSpool, exclusive_job_lock, raise_if_cancelled

        batch_size = int(args.batch_size or settings.batch_size)
        max_records = int(args.max_records)
        try:
            with exclusive_job_lock("spool-drain", label="spool-drain"):
                raise_if_cancelled()
                spool = TickSpool(settings.spool_path)
                total_inserted = total_processed = 0
                print(f"spool_before={spool.count()}")
                while True:
                    raise_if_cancelled()
                    limit = min(batch_size, max_records - total_processed) if max_records else batch_size
                    if max_records and limit <= 0:
                        break
                    batch = spool.read_batch(limit)
                    if not batch:
                        break
                    max_seq = batch[-1][0]
                    records = [r for _seq, r in batch]
                    inserted = store.insert_ticks(records)
                    spool.delete_through(max_seq)
                    total_inserted += inserted
                    total_processed += len(records)
                    print(f"batch processed={len(records)} inserted={inserted}")
                print(f"spool_after={spool.count()}")
                print(f"processed={total_processed}")
                print(f"inserted={total_inserted}")
        except JobLockConflict as exc:
            print(f"spool-drain busy: {exc}", file=sys.stderr)
            return 75
        except CancelRequested as exc:
            print(f"cancelled={exc}")
            return 130
        return 0

    if args.command == "compress-ticks":
        from src.spool import CancelRequested, JobLockConflict, exclusive_job_lock, raise_if_cancelled

        wanted = {s.upper() for s in args.symbols} if args.symbols else None
        run_targets = [t for t in targets if wanted is None or t.local_symbol.upper() in wanted]
        try:
            with exclusive_job_lock("compress-ticks", label="compress-ticks"):
                raise_if_cancelled()
                for target in run_targets:
                    raise_if_cancelled()
                    store.compress_tick_table(target.local_symbol)
                    print(f"{target.local_symbol:<8} compressed")
        except JobLockConflict as exc:
            print(f"compress-ticks busy: {exc}", file=sys.stderr)
            return 75
        except CancelRequested as exc:
            print(f"cancelled={exc}")
            return 130
        return 0

    if args.command == "refresh-dedup-window":
        from src.spool import CancelRequested, JobLockConflict, exclusive_job_lock, raise_if_cancelled

        retain_days = args.retain_days if args.retain_days is not None else settings.dedup_window_retain_days
        wanted = {s.upper() for s in args.symbols} if args.symbols else None
        run_targets = [t for t in targets if wanted is None or t.local_symbol.upper() in wanted]
        try:
            with exclusive_job_lock("refresh-dedup-window", label="refresh-dedup-window"):
                raise_if_cancelled()
                for target in run_targets:
                    raise_if_cancelled()
                    store.refresh_dedup_window(target.local_symbol, retain_days=retain_days)
                    print(f"{target.local_symbol:<8} dedup window refreshed (retain_days={retain_days})")
        except JobLockConflict as exc:
            print(f"refresh-dedup-window busy: {exc}", file=sys.stderr)
            return 75
        except CancelRequested as exc:
            print(f"cancelled={exc}")
            return 130
        return 0

    if args.command == "symbol-sync":
        from src.backfill import sync_symbols

        try:
            for line in sync_symbols(settings, store, apply=args.apply):
                print(line)
        except Exception as exc:
            print(f"symbol-sync failed: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.command == "backfill":
        from src.backfill import run_history_backfill
        from src.spool import CancelRequested, JobLockConflict, exclusive_job_lock, raise_if_cancelled

        try:
            from_ms, to_ms = _parse_datetime_ms(args.from_value), _parse_datetime_ms(args.to_value)
            if from_ms > to_ms:
                parser.error("--from must be <= --to")
            with exclusive_job_lock("ctrader-history", label="backfill", wait_seconds=args.wait_lock_seconds):
                raise_if_cancelled()
                run_history_backfill(settings, store, from_ms, to_ms, symbols=args.symbols,
                                      request_timeout_seconds=args.request_timeout, timeout_seconds=args.timeout,
                                      notify_enabled=not args.no_notify)
            return 0
        except JobLockConflict as exc:
            print(f"backfill busy: {exc}", file=sys.stderr)
            return 75
        except CancelRequested as exc:
            print(f"cancelled={exc}")
            return 130

    if args.command == "chart":
        from src.chart.server import run_server

        run_server(args.host, args.port, open_browser=args.open_browser)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
