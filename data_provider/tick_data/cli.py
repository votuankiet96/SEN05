"""Command line entry point for the standalone cTrader/FTMO tick provider.

The CLI is the operator surface for this project sub-tree. It performs setup
steps such as OAuth, account selection and symbol sync, then runs controlled
smoke/live/backfill jobs. It must never print secrets except for explicit token
exchange commands where the operator asks to receive the raw payload.
"""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from getpass import getpass

from .auth import build_authorization_url, exchange_code_for_token, refresh_access_token, run_local_oauth_login
from .checker import run_tick_check
from .notify import flush_notifications, notify_tick_report
from .runtime import load_settings
from .service_jobs import fetch_account_list, probe_history_depth, run_history_backfill, sync_symbols
from .service_live import run_live_ingest
from .spool import TickSpool
from .store_sql import TickSqlStore
from .token_store import save_token_cache, token_status, update_cached_account


def _setup_logging(log_path=None) -> None:
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )


def _parse_datetime_ms(value: str) -> int:
    """Parse UTC ISO text or a millisecond timestamp for historical backfill."""
    raw = value.strip()
    if raw.isdigit():
        return int(raw)
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def _print_token_payload(payload: dict[str, object]) -> None:
    # Token values are intentionally printed only by explicit token commands,
    # so the user can place them in a local secret store or environment file.
    print(json.dumps(payload, indent=2, sort_keys=True))


def _resolve_client_credentials(args: argparse.Namespace, settings) -> tuple[str, str]:
    client_id = getattr(args, "client_id", None) or settings.client_id
    client_secret = getattr(args, "client_secret", None) or settings.client_secret
    if not client_id:
        client_id = input("CTRADER_CLIENT_ID: ").strip()
    if not client_secret:
        client_secret = getpass("CTRADER_CLIENT_SECRET: ").strip()
    return client_id, client_secret


def _maybe_save_selected_account(accounts: list[dict[str, object]], account_id: int | None, trader_login: str | None) -> None:
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


def build_parser() -> argparse.ArgumentParser:
    """Build the operator CLI without importing the optional cTrader SDK."""
    parser = argparse.ArgumentParser(description="SEN05 cTrader FTMO tick provider")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("show-config", help="Print non-secret runtime config")
    sub.add_parser("token-status", help="Print non-secret local OAuth token cache status")
    sub.add_parser("auth-url", help="Print cTrader OAuth authorization URL")

    exchange = sub.add_parser("exchange-code", help="Exchange OAuth code for token JSON")
    exchange.add_argument("--code", required=True)
    exchange.add_argument("--client-id")
    exchange.add_argument("--client-secret")
    exchange.add_argument("--save", action="store_true", help="Save token payload to local ignored cache")

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
    refresh.add_argument("--save", action="store_true", help="Save refreshed token payload to local ignored cache")

    account_list = sub.add_parser("account-list", help="List cTrader accounts granted to CTRADER_ACCESS_TOKEN")
    account_list.add_argument("--save-account-id", type=int)
    account_list.add_argument("--save-matching-login")

    symbol_sync = sub.add_parser("symbol-sync", help="Fetch and match cTrader symbols")
    symbol_sync.add_argument("--apply", action="store_true", help="Persist matches to tick.SymbolMap")

    live = sub.add_parser("live", help="Run continuous live tick ingest")
    live.add_argument(
        "--smoke-seconds",
        type=int,
        help="Run a capped non-production smoke test and stop automatically after N seconds",
    )

    backfill = sub.add_parser("backfill", help="Backfill historical ticks")
    backfill.add_argument("--from", dest="from_value", required=True, help="UTC ISO time or ms timestamp")
    backfill.add_argument("--to", dest="to_value", required=True, help="UTC ISO time or ms timestamp")
    backfill.add_argument("--symbols", nargs="*", help="Optional local symbol filter, e.g. US30 GOLD")
    backfill.add_argument(
        "--request-timeout",
        type=float,
        help="Per-request cTrader response timeout in seconds; defaults to runtime config",
    )

    history_depth = sub.add_parser("history-depth", help="Probe deepest available historical tick depth")
    history_depth.add_argument("--symbols", nargs="*", help="Optional local symbol filter, e.g. US30 GOLD")
    history_depth.add_argument("--max-days", type=int, default=20000, help="Maximum lookback to probe")
    history_depth.add_argument("--to", dest="to_value", help="UTC ISO time or ms timestamp; default now")
    history_depth.add_argument("--probe-window-days", type=int, default=7)
    history_depth.add_argument("--timeout", type=int, default=300)
    history_depth.add_argument(
        "--request-timeout",
        type=float,
        help="Per-request cTrader response timeout in seconds; defaults to runtime config",
    )
    history_depth.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    check = sub.add_parser("check", help="Run read-only tick_data health checks")
    check.add_argument("--stale-seconds", type=int, help="Override stale heartbeat threshold")
    check.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    check.add_argument("--notify", action="store_true", help="Send Discord notification when status is not OK")

    spool_status = sub.add_parser("spool-status", help="Show local tick SQLite spool backlog")
    spool_status.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings()
    _setup_logging(settings.log_path)

    if args.command == "show-config":
        print(f"env={settings.env}")
        print(f"endpoint={settings.endpoint_label}")
        print(f"schema={settings.schema}")
        print(f"symbols={','.join(symbol.local_symbol for symbol in settings.symbols)}")
        missing = ",".join(settings.missing_api_fields) or "none"
        print(f"missing_api_fields={missing}")
        print(f"response_timeout_seconds={settings.response_timeout_seconds:g}")
        print(f"heartbeat_log_seconds={settings.heartbeat_log_seconds}")
        print(f"discord_report_seconds={settings.discord_report_seconds}")
        print(f"spool_path={settings.spool_path}")
        return 0

    if args.command == "token-status":
        print(json.dumps(token_status(), indent=2, sort_keys=True))
        return 0

    if args.command == "auth-url":
        if not settings.client_id:
            print("CTRADER_CLIENT_ID is required", file=sys.stderr)
            return 2
        print(
            build_authorization_url(
                settings.client_id,
                settings.redirect_uri,
                settings.oauth_scope,
            )
        )
        return 0

    if args.command == "exchange-code":
        client_id, client_secret = _resolve_client_credentials(args, settings)
        payload = exchange_code_for_token(
            client_id,
            client_secret,
            args.code,
            settings.redirect_uri,
        )
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
        client_id, client_secret = _resolve_client_credentials(args, settings)
        redirect_uri = args.redirect_uri or settings.redirect_uri
        scope = args.scope or settings.oauth_scope
        payload = run_local_oauth_login(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=scope,
            timeout_seconds=args.timeout,
            open_browser=not args.no_browser,
        )
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
        client_id, client_secret = _resolve_client_credentials(args, settings)
        if not settings.refresh_token:
            print("CTRADER_REFRESH_TOKEN or cached refreshToken is required", file=sys.stderr)
            return 2
        payload = refresh_access_token(
            client_id,
            client_secret,
            settings.refresh_token,
        )
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
        accounts = fetch_account_list(settings)
        print(json.dumps(accounts, indent=2, sort_keys=True))
        _maybe_save_selected_account(
            accounts,
            getattr(args, "save_account_id", None),
            getattr(args, "save_matching_login", None),
        )
        return 0

    store = TickSqlStore(
        settings.schema,
        settings.symbols,
        environment=settings.env,
        account_id=settings.account_id,
    )

    if args.command == "check":
        report = run_tick_check(settings, store, stale_seconds=args.stale_seconds)
        if args.notify and report.status != "OK":
            top_findings = report.findings[:10]
            action = (
                "Action is required. Run tick_status.ps1 and open tick_log_viewer.bat for details."
                if report.status == "ERROR"
                else "Monitor it. If the warning repeats, check the dashboard and tick log."
            )
            notify_tick_report(
                "ERROR" if report.status == "ERROR" else "WARNING",
                f"Tick data check {report.status}",
                conclusion=(
                    "The checker found a problem in tick_data."
                    if report.status == "ERROR"
                    else "The checker found a warning, but tick_data is not in a hard failure state."
                ),
                action=action,
                details=[
                    ("Status", report.status),
                    ("Findings", len(report.findings)),
                ],
                technical=[(f"{item.severity} {item.code}", item.message) for item in top_findings],
                throttle_key=f"tick-check-{report.status.lower()}",
                throttle_seconds=300,
            )
            flush_notifications()
        if args.json:
            print(json.dumps(asdict(report), indent=2, sort_keys=True, default=str))
        else:
            print(report.to_text())
        return 1 if report.status == "ERROR" else 0

    if args.command == "spool-status":
        spool = TickSpool(settings.spool_path)
        payload = {"path": str(settings.spool_path), "count": spool.count()}
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"spool_path={payload['path']}")
            print(f"spool_count={payload['count']}")
        return 0

    if args.command == "symbol-sync":
        for line in sync_symbols(settings, store if args.apply else None, apply=args.apply):
            print(line)
        return 0

    if args.command == "live":
        if args.smoke_seconds is not None and args.smoke_seconds <= 0:
            parser.error("--smoke-seconds must be greater than 0")
        if args.smoke_seconds is not None:
            run_live_ingest(
                settings,
                store,
                run_label="SMOKE",
                note=(
                    "non-production controlled smoke test; "
                    f"duration_seconds={args.smoke_seconds}; no backfill"
                ),
                duration_seconds=args.smoke_seconds,
                handoff_existing=False,
            )
        else:
            run_live_ingest(settings, store)
        return 0

    if args.command == "backfill":
        run_history_backfill(
            settings,
            store,
            _parse_datetime_ms(args.from_value),
            _parse_datetime_ms(args.to_value),
            symbols=args.symbols,
            request_timeout_seconds=args.request_timeout,
        )
        return 0

    if args.command == "history-depth":
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
        payload = [asdict(result) for result in results]
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        else:
            for item in payload:
                print(
                    f"{item['symbol']}: deepest_available_days={item['deepest_available_days']} "
                    f"earliest_probe_from_utc={item['earliest_probe_from_utc']} "
                    f"ctrader={item['ctrader_symbol_name']}({item['ctrader_symbol_id']})"
                )
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
