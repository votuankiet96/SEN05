"""Command-line entrypoint for the SEN05 DP Program."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from typing import Any

from core_engine.supervisor import engine as backend_engine
from core_engine.exit_codes import EXIT_CANCELLED, EXIT_OK
from core_engine.logkit.activity import log_activity
from core_engine.tradingview import auth as tv_auth
from core_engine import health
from core_engine.settings import (
    APP_ROOT,
    BACKEND,
    CACHE_DIR,
    CANDLE_SNAPSHOT,
    DB,
    ENV_FILE,
    HISTORICAL,
    HISTORICAL_CANCEL_FILE,
    LIVE,
    LOGGING,
    LOG_DIR,
    OPERATION_LOG_DIR,
    NOTIFICATION,
    RUNTIME_DIR,
    SPOOL_DIR,
    SYSTEM_LOG_DIR,
    SYMBOLS,
    TF_DISPLAY_ORDER,
    TRADINGVIEW,
)
from core_engine.coordination.locks import (
    DP_PROGRAM_LOCK,
    HISTORICAL_JOB_LOCK,
    LIVE_RUNTIME_LOCK,
    cleanup_stale_lock,
    fetch_lock,
    local_pid_alive,
)


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")


def _print_auth_status(*, as_json: bool = False) -> int:
    status = tv_auth.browser_profile_status()
    safe = {
        "token": status.get("token"),
        "token_source": status.get("token_source"),
        "token_present": bool(status.get("token_present")),
        "cookie_present": bool(status.get("cookie_present")),
        "username_present": bool(status.get("username_present")),
        "password_present": bool(status.get("password_present")),
        "runtime_cache_present": bool(status.get("cache_present")),
        "browser_profile_present": bool(status.get("profile_present")),
        "browser_profile_dir": status.get("profile_dir"),
        "browser_cache_dir": status.get("browser_cache_dir"),
        "auth_lock": tv_auth.auth_refresh_lock_status(),
    }
    if as_json:
        print(json.dumps(safe, ensure_ascii=True, indent=BACKEND.status_json_indent))
    else:
        token = safe.get("token") or {}
        print("TradingView auth status")
        print(f"- token state         : {token.get('state')}")
        print(f"- token source        : {safe.get('token_source') or '-'}")
        print(f"- seconds remaining   : {token.get('seconds_remaining')}")
        print(f"- cookie present      : {safe.get('cookie_present')}")
        print(f"- username present    : {safe.get('username_present')}")
        print(f"- browser profile     : {safe.get('browser_profile_present')}")
        print(f"- runtime cache       : {safe.get('runtime_cache_present')}")
    state = str((safe.get("token") or {}).get("state") or "")
    return 0 if state in {"valid", "expiring_soon"} else 1


def _run_status(args: argparse.Namespace) -> int:
    report = health.collect_health(deep_auth=args.deep_auth, include_database=not args.no_db)
    if args.json:
        health.print_json(report)
    else:
        health.print_human(report)
    return 0 if report.get("status") != "fail" else 1


def _run_clean(args: argparse.Namespace) -> int:
    result = health.cleanup_old_runtime_files(days=args.days)
    if args.json:
        print(json.dumps(result, ensure_ascii=True, indent=BACKEND.status_json_indent))
    else:
        print(f"Runtime retention: {result['retention_days']} day(s)")
        print(f"Deleted files    : {len(result.get('deleted', []))}")
        print(f"Kept files       : {result.get('kept_count')}")
    return 0


def _run_data_health(args: argparse.Namespace) -> int:
    report = health.collect_data_health(lookback_days=args.lookback_days)
    health.print_data_health(report, as_json=args.json)
    return 0 if report.get("status") != "fail" else 1


def _run_reconcile_fact(args: argparse.Namespace) -> int:
    from core_engine.warehouse.reconcile import reconcile_all, total_missing

    tf_filter = None
    if args.timeframes:
        tf_filter = {tf.strip().upper() for tf in args.timeframes.split(",") if tf.strip()}

    count_unsupported = bool(getattr(args, "count_unsupported_as_missing", False))
    results = reconcile_all(apply=args.apply, tf_filter=tf_filter, count_unsupported_as_missing=count_unsupported)
    missing = total_missing(results)
    unsupported_rows = [r for r in results if not r.error and r.unsupported_calendar_count]
    unsupported_total = sum(r.unsupported_calendar_count for r in unsupported_rows)
    supported_missing = sum((r.supported_missing_after or 0) for r in results if not r.error)
    supported_mismatched = sum((r.supported_mismatched_after or 0) for r in results if not r.error)

    if args.json:
        payload = {
            "applied": bool(args.apply),
            "missing_count": missing,
            "counted_unsupported_as_missing": count_unsupported,
            "unsupported_calendar_total": unsupported_total,
            "supported_missing_fact_rows": supported_missing,
            "supported_mismatched_fact_rows": supported_mismatched,
            "unsupported_calendar_rows": unsupported_total,
            "timeframes": [
                {
                    "tf_code": r.tf_code,
                    "staging_table": r.staging_table,
                    "missing_before": r.missing_before,
                    "repaired": r.repaired,
                    "missing_after": r.missing_after,
                    "symbols_affected": r.symbols_affected,
                    "error": r.error,
                    "unsupported_calendar_count": r.unsupported_calendar_count,
                    "unsupported_calendar_symbols": r.unsupported_calendar_symbols,
                    "unsupported_calendar_range": r.unsupported_calendar_range,
                    "supported_missing_before": r.supported_missing_before,
                    "supported_mismatched_before": r.supported_mismatched_before,
                    "supported_missing_after": r.supported_missing_after,
                    "supported_mismatched_after": r.supported_mismatched_after,
                }
                for r in results
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        mode = "APPLY (re-running ETL for affected symbols)" if args.apply else "SCAN ONLY (pass --apply to repair)"
        print(f"Staging -> Fact reconciliation: {mode}")
        for r in results:
            if r.error:
                print(f"  {r.tf_code:<4} {r.staging_table:<12} ERROR: {r.error}")
                continue
            if r.missing_before == 0 and (r.missing_after or 0) == 0:
                continue
            print(
                f"  {r.tf_code:<4} {r.staging_table:<12} "
                f"missing_before={r.missing_before} repaired={r.repaired} "
                f"missing_after={r.missing_after} symbols={r.symbols_affected}"
            )
        if missing == 0:
            print("Result: missing_count = 0 across all checked timeframes.")
        else:
            print(f"Result: missing_count = {missing} - NOT safe to purge staging for affected tables yet.")
        print(
            "Fact-eligible detail: "
            f"supported_missing_fact_rows={supported_missing} "
            f"supported_mismatched_fact_rows={supported_mismatched} "
            f"unsupported_calendar_rows={unsupported_total}"
        )

        if unsupported_rows and not count_unsupported:
            print()
            print(
                f"NOTE: {unsupported_total} staging row(s) fall outside DWH.Dim_Date's covered "
                "calendar range and are therefore intentionally never inserted into Fact by "
                "usp_LoadDirect v3 (date-dimension fence). They are NOT counted in missing_count "
                "above and do NOT affect this command's exit code."
            )
            for r in unsupported_rows:
                lo, hi = r.unsupported_calendar_range or ("?", "?")
                print(
                    f"  {r.tf_code:<4} {r.staging_table:<12} "
                    f"unsupported={r.unsupported_calendar_count} range={lo} -> {hi} "
                    f"symbols={r.unsupported_calendar_symbols}"
                )
            print(
                "This is a data-scope decision, not something reconcile-fact decides "
                "automatically. Two options:"
            )
            print(
                "  1) Extend DWH.Dim_Date backward to cover this range, then re-run "
                "reconcile-fact --apply."
            )
            print(
                "  2) Treat these rows as permanently out of scope and purge them from "
                "staging (a dedicated, explicitly-confirmed action - the routine "
                "purge_staging maintenance job already skips them since they have no "
                "matching Fact row)."
            )
            print(
                "Pass --count-unsupported-as-missing to fold these back into missing_count "
                "and this command's exit code instead (reverts to strict pre-fence behavior)."
            )

    return 0 if missing == 0 else 1


def _collect_core_settings() -> dict[str, Any]:
    auth_status = tv_auth.browser_profile_status()
    token = auth_status.get("token") or {}
    live_symbols = [s for s in SYMBOLS if s["asset_type"] in set(LIVE.asset_types)]
    return {
        "program": {
            "name": "DP Program",
            "app_root": str(APP_ROOT),
            "env_file": str(ENV_FILE),
            "runtime_dir": str(RUNTIME_DIR),
        },
        "database": {
            "server": DB.server,
            "database": DB.database,
            "driver": DB.driver,
            "port": DB.port or "-",
            "auth_mode": "sql_login" if DB.uid else "windows_trusted",
            "encrypt": DB.encrypt,
            "trust_server_cert": DB.trust_server_cert,
            "retry_count": DB.retry_count,
            "retry_delay_sec": DB.retry_delay_sec,
        },
        "instruments": {
            "symbols_total": len(SYMBOLS),
            "live_symbols": len(live_symbols),
            "historical_symbols": len(SYMBOLS),
            "timeframes_total": len(TF_DISPLAY_ORDER),
            "timeframes": ",".join(TF_DISPLAY_ORDER),
        },
        "live_fetching": {
            "auto_start": LIVE.auto_start,
            "asset_types": ",".join(LIVE.asset_types),
            "expected_live_symbols": LIVE.expected_symbol_count,
            "resolved_live_symbols": len(live_symbols),
            "symbol_timeframe_sessions": len(live_symbols) * len(TF_DISPLAY_ORDER),
            "batch_interval_min": LIVE.batch_interval_min,
            "n_bars_per_request": LIVE.n_bars,
            "drop_open_bar_policy": "keeps only closed bars",
            "symbols_per_connection": LIVE.symbols_per_conn,
            "status_report_interval_sec": LIVE.status_interval_sec,
            "guest_policy": LIVE.guest_policy,
            "db_queue_maxsize": LIVE.db_queue_maxsize,
            "disk_spool_max_rows": LIVE.max_spool_rows,
        },
        "historical_pulling": {
            "default_provider": HISTORICAL.provider,
            "drop_open_last_bar": HISTORICAL.drop_open_last_bar,
            "hole_lookback_days": HISTORICAL.hole_lookback_days,
            "max_consecutive_fail": HISTORICAL.max_consecutive_fail,
            "replay_enabled": HISTORICAL.replay_enabled,
            "replay_timeframes": ",".join(sorted(HISTORICAL.replay_tfs)),
            "replay_endpoint": HISTORICAL.replay_endpoint,
        },
        "supervisor": {
            "live_auto_start": BACKEND.live_auto_start,
            "historical_schedule_enabled": BACKEND.historical_backfill_enabled,
            "historical_schedule_utc": BACKEND.historical_backfill_utc,
            "historical_start_on_start": BACKEND.historical_start_on_backend_start,
            "historical_start_delay_sec": BACKEND.historical_start_delay_sec,
            "historical_schedule_mode": BACKEND.historical_backfill_mode,
            "historical_retry_base_sec": BACKEND.historical_retry_base_sec,
            "historical_retry_max_sec": BACKEND.historical_retry_max_sec,
            "live_restart_on_exit": BACKEND.live_restart_on_exit,
            "live_restart_on_stale": BACKEND.live_restart_on_stale,
            "live_stale_minutes": BACKEND.live_stale_minutes,
            "live_max_restarts_per_hour": BACKEND.live_max_restarts_per_hour,
            "shutdown_grace_sec": BACKEND.shutdown_grace_sec,
            "log_retention_days": BACKEND.log_retention_days,
        },
        "tradingview_auth": {
            "token_state": token.get("state"),
            "token_source": auth_status.get("token_source") or "-",
            "token_present": bool(auth_status.get("token_present")),
            "cookie_present": bool(auth_status.get("cookie_present")),
            "username_present": bool(auth_status.get("username_present")),
            "password_present": bool(auth_status.get("password_present")),
            "browser_profile_present": bool(auth_status.get("profile_present")),
            "headless_fresh_login": TRADINGVIEW.headless_fresh_login,
            "connectivity_preflight": TRADINGVIEW.auth_connectivity_preflight,
        },
        "notifications": {
            "discord_configured": bool(NOTIFICATION.discord_webhook_url),
            "send_attempts": NOTIFICATION.discord_send_attempts,
            "dedupe_window_sec": NOTIFICATION.discord_dedupe_window_sec,
            "circuit_failures": NOTIFICATION.discord_circuit_failures,
            "circuit_cooldown_sec": NOTIFICATION.discord_circuit_cooldown_sec,
        },
        "candle_snapshot_redis": {
            "enabled": CANDLE_SNAPSHOT.enabled,
            "state_prefix": CANDLE_SNAPSHOT.state_prefix,
            "event_stream": CANDLE_SNAPSHOT.event_stream,
            "event_maxlen": CANDLE_SNAPSHOT.event_maxlen,
            "bars_per_snapshot": CANDLE_SNAPSHOT.bars_per_snapshot,
            "pubsub_enabled": CANDLE_SNAPSHOT.pubsub_enabled,
            "pubsub_channel": CANDLE_SNAPSHOT.pubsub_channel,
            "pubsub_schema_version": CANDLE_SNAPSHOT.pubsub_schema_version,
        },
        "runtime_paths": {
            "logs": str(LOG_DIR),
            "system_logs": str(SYSTEM_LOG_DIR),
            "operation_logs": str(OPERATION_LOG_DIR),
            "live_engine_log": str(LOGGING.live_log),
            "live_candle_log": str(LOGGING.live_candle_log),
            "live_report_log": str(LOGGING.live_report_log),
            "cache": str(CACHE_DIR),
            "spool": str(SPOOL_DIR),
        },
    }


def _print_core_settings(*, as_json: bool = False) -> int:
    data = _collect_core_settings()
    if as_json:
        print(json.dumps(data, ensure_ascii=True, indent=BACKEND.status_json_indent))
        return 0

    print("DP Program core settings")
    for group, values in data.items():
        print(f"\n[{group}]")
        for key, value in values.items():
            print(f"- {key:<28}: {value}")
    return 0


def _runtime_conflict_status(kind: str) -> dict[str, Any]:
    task_map = {
        "supervisor": DP_PROGRAM_LOCK,
        "live": LIVE_RUNTIME_LOCK,
        "historical": HISTORICAL_JOB_LOCK,
    }
    task_name = task_map[kind]
    record = fetch_lock(task_name, active_only=True)
    if not record:
        return {"kind": kind, "task_name": task_name, "active": False}
    meta = record.meta
    pid_text = meta.get("pid")
    pid_alive: bool | None = None
    same_host = False
    host = str(meta.get("host") or "").strip().lower()
    local_hosts = {
        str(os.environ.get("COMPUTERNAME") or "").strip().lower(),
        str(socket.gethostname() or "").strip().lower(),
    }
    local_hosts.discard("")
    if host:
        same_host = host in local_hosts
    if pid_text:
        try:
            pid_alive = local_pid_alive(int(pid_text))
        except Exception:
            pid_alive = None
    if same_host and pid_text and pid_alive is False:
        cleaned = cleanup_stale_lock(task_name, stale_after_sec=10**9)
        return {
            "kind": kind,
            "task_name": task_name,
            "active": False,
            "stale_cleaned": bool(cleaned),
            "stale_pid": pid_text,
            "stale_owner": meta.get("owner") or meta.get("kind"),
            "reason": "same-host PID is not running",
        }
    return {
        "kind": kind,
        "task_name": task_name,
        "active": True,
        "pid": meta.get("pid"),
        "pid_alive": pid_alive,
        "host": meta.get("host"),
        "owner": meta.get("owner") or meta.get("kind"),
        "started": meta.get("started"),
        "heartbeat": meta.get("heartbeat"),
        "expires_at": str(record.expires_at or ""),
    }


def _run_conflict_status(args: argparse.Namespace) -> int:
    data = _runtime_conflict_status(args.kind)
    if args.json:
        print(json.dumps(data, ensure_ascii=True, indent=BACKEND.status_json_indent))
    else:
        state = "active" if data.get("active") else "idle"
        print(f"{args.kind}: {state}")
        if data.get("active"):
            print(f"- pid       : {data.get('pid') or '-'}")
            print(f"- owner     : {data.get('owner') or '-'}")
            print(f"- started   : {data.get('started') or '-'}")
            print(f"- expires   : {data.get('expires_at') or '-'}")
    return 0


def _run_operator_decision(args: argparse.Namespace) -> int:
    detail = _runtime_conflict_status(args.kind)
    backend_engine.record_operator_decision(
        kind=args.kind,
        decision=args.decision,
        requested_title=args.requested_title,
        detail=detail,
    )
    return 0


def _terminal_completion_status(exit_code: int) -> tuple[str, str]:
    if exit_code == EXIT_OK:
        return "completed", "Operator terminal command finished."
    if exit_code == EXIT_CANCELLED:
        return "stopped", "Operator terminal command stopped after a Graceful Stop request."
    return "failed", "Operator terminal command finished with an error."


def _run_queue_historical(args: argparse.Namespace) -> int:
    raw_args = list(args.historical_args or [])
    if raw_args and raw_args[0] == "--":
        raw_args = raw_args[1:]
    job = backend_engine.queue_historical_job(
        raw_args,
        title=args.title,
        requested_by=args.requested_by,
    )
    if args.json:
        print(json.dumps(job, ensure_ascii=True, indent=BACKEND.status_json_indent))
    else:
        print(f"Queued historical job: {job['title']} ({job['id']})")
    return 0


def _print_stop_report(report: dict[str, Any], *, as_json: bool = False) -> int:
    if as_json:
        print(json.dumps(report, ensure_ascii=True, indent=BACKEND.status_json_indent))
        return 0

    print("DP Program graceful stop")
    print(f"- reason      : {report.get('reason')}")
    print(f"- requested at: {report.get('requested_at')}")
    print(f"- wait seconds: {report.get('wait_sec')}")
    print("")
    for item in report.get("targets", []):
        name = item.get("name") or item.get("kind") or "process"
        status = item.get("status") or "-"
        pid = item.get("pid") or "-"
        note = item.get("note") or item.get("message") or ""
        print(f"- {name:<22}: {status:<12} pid={pid} {note}".rstrip())
    print("")
    if report.get("still_running"):
        print("Result: some processes are still running. Check system.log and stop again if needed.")
        return 3
    print("Result: no active DP Program/live/historical process lock remains.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m core_engine",
        description="SEN05 DP Program terminal application",
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="start the DP Program supervisor")
    run.add_argument("--live", action="store_true", help="force live fetching on for this run")
    run.add_argument("--no-live", action="store_true", help="do not auto-start live fetching")
    run.add_argument("--schedule", action="store_true", help="force scheduled historical backfill on for this run")
    run.add_argument("--no-schedule", action="store_true", help="disable scheduled historical backfill")
    run.add_argument("--smoke-seconds", type=int, default=None, help="stop automatically after N seconds")
    run.add_argument("--replace", action="store_true", help="gracefully replace an active DP Program supervisor")

    status = sub.add_parser("status", help="show DP Program status and health")
    _add_json_flag(status)
    status.add_argument("--deep-auth", action="store_true", help="include TradingView network/browser auth diagnostics")
    status.add_argument("--no-db", action="store_true", help="skip SQL Server checks")

    doctor = sub.add_parser("doctor", help="run readiness checks before production operation")
    _add_json_flag(doctor)
    doctor.add_argument("--deep-auth", action="store_true", help="include TradingView network/browser auth diagnostics")
    doctor.add_argument("--no-db", action="store_true", help="skip SQL Server checks")

    data_health = sub.add_parser("data-health", help="show OHLCV freshness, coverage, and gap status")
    data_health.add_argument("--lookback-days", type=int, default=None, help="internal gap scan window")
    _add_json_flag(data_health)

    reconcile = sub.add_parser(
        "reconcile-fact",
        help="find (and optionally repair) staging rows that never reached DWH.Fact_OHLCV",
    )
    reconcile.add_argument(
        "--timeframes", default=None,
        help="comma-separated timeframe codes to check (default: all)",
    )
    reconcile.add_argument(
        "--apply", action="store_true",
        help="re-run ETL for affected symbols instead of only reporting the gap",
    )
    reconcile.add_argument(
        "--count-unsupported-as-missing", action="store_true",
        help=(
            "fold staging rows outside DWH.Dim_Date's covered calendar range back into "
            "missing_count/exit code (default: report them separately, since usp_LoadDirect "
            "v3 intentionally never inserts them)"
        ),
    )
    _add_json_flag(reconcile)

    stop = sub.add_parser("stop", help="request graceful DP Program/live/historical shutdown")
    stop.add_argument("--reason", default="operator", help="reason stored in runtime stop files")
    stop.add_argument("--wait-sec", type=int, default=60, help="seconds to wait for active processes to stop")
    stop.add_argument(
        "--force-after-grace",
        action="store_true",
        help="terminate same-machine processes that ignore the graceful stop request",
    )
    _add_json_flag(stop)

    live = sub.add_parser("live", help="run live fetching directly in the current terminal")
    live.add_argument("--smoke-seconds", type=int, default=None, help="stop live fetching after N seconds")
    live.add_argument("--replace", action="store_true", help="gracefully replace an active live fetching worker")

    sub.add_parser("historical", help="pass remaining arguments to historical_pulling.py")

    queue_hist = sub.add_parser("queue-historical", help="queue a historical job for the DP Program supervisor")
    queue_hist.add_argument("--title", default="Historical job")
    queue_hist.add_argument("--requested-by", default="operator")
    _add_json_flag(queue_hist)
    queue_hist.add_argument("historical_args", nargs=argparse.REMAINDER)

    conflict = sub.add_parser("conflict-status", help="show active process/lock for a runtime group")
    conflict.add_argument("--kind", choices=["supervisor", "live", "historical"], required=True)
    _add_json_flag(conflict)

    decision = sub.add_parser("operator-decision", help="record a duplicate-request operator decision")
    decision.add_argument("--kind", choices=["supervisor", "live", "historical"], required=True)
    decision.add_argument("--decision", choices=["skip", "replace", "queue"], required=True)
    decision.add_argument("--requested-title", required=True)

    auth = sub.add_parser("auth", help="TradingView auth helper commands")
    auth_sub = auth.add_subparsers(dest="auth_command")
    auth_status = auth_sub.add_parser("status", help="show non-secret TradingView auth status")
    _add_json_flag(auth_status)
    auth_diag = auth_sub.add_parser("diagnose", help="run TradingView auth/connectivity diagnostics")
    _add_json_flag(auth_diag)
    auth_login = auth_sub.add_parser("login", help="open browser login and cache TradingView credentials")
    auth_login.add_argument("--timeout-sec", type=int, default=900)

    clean = sub.add_parser("clean-runtime", help="delete old runtime logs/state according to retention")
    clean.add_argument("--days", type=int, default=None)
    _add_json_flag(clean)

    chart = sub.add_parser("chart-datacheck", help="run read-only Chart & Data Health local viewer")
    chart.add_argument("--host", default="127.0.0.1")
    chart.add_argument("--port", type=int, default=8050)
    chart.add_argument("--open-browser", action="store_true")

    settings = sub.add_parser("settings", help="show non-secret core settings")
    _add_json_flag(settings)
    return parser


def _dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.command == "run":
        live_enabled = True if args.live else (False if args.no_live else None)
        schedule_enabled = True if args.schedule else (False if args.no_schedule else None)
        return backend_engine.run_forever(
            live_enabled=live_enabled,
            schedule_enabled=schedule_enabled,
            smoke_seconds=args.smoke_seconds,
            conflict_policy="replace" if args.replace else "skip",
        )
    if args.command == "status":
        return _run_status(args)
    if args.command == "doctor":
        return _run_status(args)
    if args.command == "data-health":
        return _run_data_health(args)
    if args.command == "reconcile-fact":
        return _run_reconcile_fact(args)
    if args.command == "stop":
        report = backend_engine.request_stop(
            args.reason,
            wait_sec=args.wait_sec,
            force_after_grace=args.force_after_grace,
        )
        return _print_stop_report(report, as_json=args.json)
    if args.command == "live":
        from core_engine.live import engine as live_fetching

        return int(live_fetching.main(
            smoke_seconds=args.smoke_seconds,
            conflict_policy="replace" if args.replace else "skip",
        ) or 0)
    if args.command == "queue-historical":
        return _run_queue_historical(args)
    if args.command == "conflict-status":
        return _run_conflict_status(args)
    if args.command == "operator-decision":
        return _run_operator_decision(args)
    if args.command == "auth":
        if args.auth_command == "status":
            return _print_auth_status(as_json=args.json)
        if args.auth_command == "diagnose":
            report = tv_auth.diagnose_connectivity()
            if args.json:
                print(json.dumps(report, ensure_ascii=True, indent=BACKEND.status_json_indent))
            else:
                print(f"TradingView diagnose: {str(report.get('status')).upper()}")
                print(report.get("message"))
            return 0 if report.get("status") != "blocker" else 1
        if args.auth_command == "login":
            token, source = tv_auth.interactive_browser_login(timeout_sec=args.timeout_sec)
            safe_source = tv_auth.safe_auth_source_label(source)
            print(f"TradingView login completed. Source: {safe_source}. Token cached: {bool(token)}")
            return 0 if token else 1
        parser.error("auth requires one of: status, diagnose, login")
    if args.command == "clean-runtime":
        return _run_clean(args)
    if args.command == "chart-datacheck":
        from core_engine.dashboard import server as chart_server

        return chart_server.main(
            [
                "--host",
                args.host,
                "--port",
                str(args.port),
                *(["--open-browser"] if args.open_browser else []),
            ]
        )
    if args.command == "settings":
        return _print_core_settings(as_json=args.json)

    parser.print_help()
    return 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    started = time.time()
    command_label = argv[0] if argv else "help"
    log_activity(
        "terminal_command_started",
        component="terminal",
        status="started",
        message="Operator terminal command started.",
        command=" ".join(argv) if argv else "help",
    )
    exit_code = 1
    try:
        if argv and argv[0] == "historical":
            from core_engine.historical import engine as historical_pulling

            os.environ.setdefault("DP_HISTORICAL_CANCEL_FILE", str(HISTORICAL_CANCEL_FILE))
            exit_code = historical_pulling.main(argv[1:])
            return exit_code

        parser = build_parser()
        args = parser.parse_args(argv)
        exit_code = _dispatch(args, parser)
        return exit_code
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, int):
            exit_code = code
        elif code in (None, ""):
            exit_code = 0
        else:
            exit_code = 1
        raise
    except Exception as exc:
        log_activity(
            "terminal_command_failed",
            component="terminal",
            status="failed",
            message="Operator terminal command crashed before normal completion.",
            command=command_label,
            error=f"{type(exc).__name__}: {exc}",
            duration_seconds=round(time.time() - started, 3),
        )
        raise
    finally:
        status, message = _terminal_completion_status(exit_code)
        log_activity(
            "terminal_command_completed",
            component="terminal",
            status=status,
            message=message,
            command=command_label,
            exit_code=exit_code,
            duration_seconds=round(time.time() - started, 3),
        )
