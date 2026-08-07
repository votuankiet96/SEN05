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
    run_backfill_service,
    run_live_service,
    service_status,
)
from .engine.spool import pending_status
from .engine.sql_connector import check_connection, fetch_universe, select_pairs


def build_parser() -> argparse.ArgumentParser:
    """Build the small V3 CLI."""
    # Khai báo các lệnh operator có thể gọi từ run_*.bat hoặc terminal.
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
    subparsers.add_parser("run-live", help="run the continuous single-instance live service")
    subparsers.add_parser("run-backfill", help="run the continuous single-instance backfill service")
    stop = subparsers.add_parser("stop", help="request a graceful service stop")
    stop.add_argument("--mode", choices=("live", "backfill"), required=True)
    stop.add_argument("--wait-seconds", type=int, default=300)
    status = subparsers.add_parser("status", help="show durable service state")
    status.add_argument("--mode", choices=("live", "backfill"), required=True)
    subparsers.add_parser("doctor", help="run read-only production readiness checks")
    subparsers.add_parser("settings", help="show secret-free effective settings")
    auth = subparsers.add_parser("auth", help="inspect or refresh TradingView auth")
    auth.add_argument("action", choices=("status", "refresh"))
    return parser


def _doctor(config: dict) -> dict:
    # Chạy các kiểm tra read-only để biết hệ thống sẵn sàng vận hành chưa.
    sql = check_connection(config)
    authentication = auth_status(config)
    browser = browser_status()
    spool = pending_status(config)
    symbols, timeframes = fetch_universe(config)
    live_pairs = select_pairs(config, live=True)
    backfill_pairs = select_pairs(config, live=False)
    contract = {
        "symbols": sum(bool(item["enabled"]) for item in symbols),
        "timeframes": len(timeframes),
        "live_symbols": len({pair[0]["symbol"] for pair in live_pairs}),
        "live_timeframes": len({pair[1]["code"] for pair in live_pairs}),
        "live_pairs": len(live_pairs),
        "backfill_pairs": len(backfill_pairs),
        "live_interval_minutes": config["live"]["interval_minutes"],
        "live_bars_per_request": config["live"]["bars_per_request"],
        "closed_candles_only": config["live"]["closed_candles_only"],
        "backfill_schedule_utc": config["backfill"]["schedule_utc"],
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
    # Trả cấu hình đã che secret, dùng để kiểm nhanh runtime đang dùng gì.
    symbols, timeframes = fetch_universe(config)
    live_pairs = select_pairs(config, live=True)
    backfill_pairs = select_pairs(config, live=False)
    return {
        "ok": True,
        "config_path": config["app"]["config_path"],
        "runtime_dir": config["app"]["runtime_dir"],
        "single_config_file": True,
        "symbols": sum(bool(item["enabled"]) for item in symbols),
        "timeframes": len(timeframes),
        "live_symbols": len({pair[0]["symbol"] for pair in live_pairs}),
        "live_timeframes": len({pair[1]["code"] for pair in live_pairs}),
        "live_pairs": len(live_pairs),
        "backfill_pairs": len(backfill_pairs),
        "live_interval_minutes": config["live"]["interval_minutes"],
        "live_bars_per_request": config["live"]["bars_per_request"],
        "closed_candles_only": config["live"]["closed_candles_only"],
        "backfill_lookback_days": config["backfill"]["lookback_days"],
        "backfill_run_on_start": config["backfill"]["run_on_start"],
        "backfill_schedule_utc": config["backfill"]["schedule_utc"],
        "sql_database": config["sql_server"]["database"],
        "sql_contract_version": config["sql_server"]["contract_version"],
        "discord_enabled": config["discord"]["enabled"],
        "discord_webhook_configured": bool(config["discord"]["webhook_url"]),
        "authenticated_only": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Load config and dispatch one V3 command."""
    # Điểm vào chính: load config, chọn log file theo vai trò, rồi chạy lệnh.
    parser = build_parser()
    args = parser.parse_args(argv)
    config = None
    try:
        config = load_config()
        writes = args.command in {"run-live", "run-backfill", "backfill", "live"} or (
            args.command == "auth" and args.action == "refresh"
        )
        if writes:
            # Lệnh có ghi dữ liệu dùng log riêng cho live/backfill.
            role = (
                "backfill" if args.command in {"run-backfill", "backfill"}
                else "live"
            )
            configure_logging(config, role=role)
        else:
            # Lệnh chỉ đọc/chẩn đoán dùng logging console đơn giản.
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        if args.command == "backfill":
            with instance_lock(config, "backfill"):
                summary = run_backfill(
                    config,
                    symbol=args.symbol,
                    timeframe=args.timeframe,
                    bars=args.bars,
                )
        elif args.command == "live":
            with instance_lock(config, "live"):
                summary = run_live_cycle(
                    config,
                    symbol=args.symbol,
                    timeframe=args.timeframe,
                )
        elif args.command == "check-sql":
            summary = check_connection(config)
        elif args.command == "run-live":
            try:
                summary = run_live_service(config)
            except ValueError as exc:
                raise RuntimeError("service runtime validation failed") from exc
        elif args.command == "run-backfill":
            try:
                summary = run_backfill_service(config)
            except ValueError as exc:
                raise RuntimeError("service runtime validation failed") from exc
        elif args.command == "stop":
            summary = request_stop(config, args.mode, wait_seconds=args.wait_seconds)
        elif args.command == "status":
            summary = service_status(config, args.mode)
        elif args.command == "doctor":
            summary = _doctor(config)
        elif args.command == "settings":
            summary = _settings(config)
        elif args.action == "status":
            summary = auth_status(config)
        else:
            with instance_lock(config, "auth"):
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
            # Exit code 1 giúp wrapper nhận biết lệnh đã có lỗi hoặc còn việc hoãn lại.
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
        is_service = args.command in {"run-live", "run-backfill"}
        if is_service and config is not None:
            try:
                record_service_failure(config, "live" if args.command == "run-live" else "backfill", exc)
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
