"""Command-line entry point for DP Program V3."""

from __future__ import annotations

import argparse
import json
import logging
from typing import Sequence

from .configuration import ConfigError, load_config
from .engine.auth import AuthError, auth_status, browser_status, ensure_authenticated
from .engine.backfill import run_backfill
from .engine.live import run_live_cycle
from .log import configure_logging, log_event
from .engine.runtime import (
    instance_lock,
    record_service_failure,
    request_stop,
    run_service,
    service_status,
)
from .engine.spool import pending_status
from .engine.sql_connector import check_connection


def build_parser() -> argparse.ArgumentParser:
    """Build the small V3 CLI."""
    parser = argparse.ArgumentParser(description="DP Program V3 TradingView OHLCV engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backfill = subparsers.add_parser("backfill", help="run one daily backfill pass")
    backfill.add_argument("--symbol", help="symbol or EXCHANGE:SYMBOL")
    backfill.add_argument("--timeframe", help="timeframe code such as M5")
    backfill.add_argument("--bars", type=int, help="override requested bars")

    live = subparsers.add_parser("live", help="run one finite live cycle")
    live.add_argument("--symbol", help="symbol or EXCHANGE:SYMBOL")
    live.add_argument("--timeframe", help="timeframe code such as M5")

    subparsers.add_parser("check-sql", help="verify SQL connectivity and contract")
    subparsers.add_parser("run", help="run the single-instance production service")
    stop = subparsers.add_parser("stop", help="request a graceful service stop")
    stop.add_argument("--wait-seconds", type=int, default=300)
    subparsers.add_parser("status", help="show durable service state")
    subparsers.add_parser("doctor", help="run read-only production readiness checks")
    subparsers.add_parser("settings", help="show secret-free effective settings")
    auth = subparsers.add_parser("auth", help="inspect or refresh TradingView auth")
    auth.add_argument("action", choices=("status", "refresh"))
    return parser


def _doctor(config: dict) -> dict:
    sql = check_connection(config)
    authentication = auth_status(config)
    browser = browser_status()
    spool = pending_status(config)
    contract = {
        "symbols": len(config["data"]["symbols"]),
        "live_symbols": sum(bool(item["live"]) for item in config["data"]["symbols"]),
        "timeframes": len(config["data"]["timeframes"]),
        "live_pairs": sum(bool(item["live"]) for item in config["data"]["symbols"])
        * len(config["data"]["timeframes"]),
    }
    ok = (
        bool(sql.get("ok"))
        and bool(authentication.get("ok"))
        and bool(browser.get("ok"))
        and spool["corrupt"] == 0
    )
    return {
        "ok": ok,
        "sql": sql,
        "auth": authentication,
        "chromium": browser,
        "spool": spool,
        "contract": contract,
    }


def _settings(config: dict) -> dict:
    return {
        "ok": True,
        "config_path": config["app"]["config_path"],
        "runtime_dir": config["app"]["runtime_dir"],
        "single_config_file": True,
        "symbols": len(config["data"]["symbols"]),
        "live_symbols": sum(bool(item["live"]) for item in config["data"]["symbols"]),
        "timeframes": len(config["data"]["timeframes"]),
        "live_interval_minutes": config["live"]["interval_minutes"],
        "backfill_lookback_days": config["backfill"]["lookback_days"],
        "backfill_policy_epoch_utc": config["backfill"]["policy_epoch_utc"],
        "backfill_schedule_utc": config["service"]["backfill_schedule_utc"],
        "sql_database": config["sql_server"]["database"],
        "sql_contract_version": config["sql_server"]["contract_version"],
        "discord_enabled": config["discord"]["enabled"],
        "discord_webhook_configured": bool(config["discord"]["webhook_url"]),
        "authenticated_only": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Load config and dispatch one V3 command."""
    parser = build_parser()
    args = parser.parse_args(argv)
    config = None
    try:
        config = load_config()
        writes = args.command in {"run", "backfill", "live"} or (
            args.command == "auth" and args.action == "refresh"
        )
        if writes:
            configure_logging(config)
        else:
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        if args.command == "backfill":
            with instance_lock(config):
                summary = run_backfill(
                    config,
                    symbol=args.symbol,
                    timeframe=args.timeframe,
                    bars=args.bars,
                )
        elif args.command == "live":
            with instance_lock(config):
                summary = run_live_cycle(
                    config,
                    symbol=args.symbol,
                    timeframe=args.timeframe,
                )
        elif args.command == "check-sql":
            summary = check_connection(config)
        elif args.command == "run":
            try:
                summary = run_service(config)
            except ValueError as exc:
                raise RuntimeError("service runtime validation failed") from exc
        elif args.command == "stop":
            summary = request_stop(config, wait_seconds=args.wait_seconds)
        elif args.command == "status":
            summary = service_status(config)
        elif args.command == "doctor":
            summary = _doctor(config)
        elif args.command == "settings":
            summary = _settings(config)
        elif args.action == "status":
            summary = auth_status(config)
        else:
            with instance_lock(config):
                refreshed = ensure_authenticated(config, force=True)
            summary = {
                "ok": True,
                "refreshed": True,
                "source": refreshed["source"],
                "auth": auth_status(config),
            }
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        if (
            summary.get("ok") is False
            or int(summary.get("failed", 0)) > 0
            or int(summary.get("deferred", 0)) > 0
        ):
            return 1
        return 0
    except (ConfigError, ValueError) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        log_event(
            logging.getLogger(__name__),
            logging.INFO,
            "SHUTDOWN_REQUESTED",
            "NONE",
            component="cli",
            command=args.command,
            reason="keyboard_interrupt",
        )
        return 130
    except Exception as exc:
        is_auth = isinstance(exc, AuthError)
        is_service = args.command == "run"
        if is_service and config is not None:
            try:
                record_service_failure(config, exc)
            except Exception:
                pass
        log_event(
            logging.getLogger(__name__),
            logging.CRITICAL if is_service else logging.ERROR,
            "AUTH_UNAVAILABLE" if is_auth else (
                "SERVICE_FAILED" if is_service else "COMMAND_FAILED"
            ),
            "CRITICAL" if is_service or is_auth else "HIGH",
            component="auth" if is_auth else ("runtime" if is_service else "cli"),
            command=args.command,
            error_type=type(exc).__name__,
            error=exc,
            action="service stopped; operator review required" if is_service else (
                "operator authentication required" if is_auth else "command stopped"
            ),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
