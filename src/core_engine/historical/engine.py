"""Historical OHLCV pull engine for the refactored DP backend.

This module is intentionally independent from the legacy backend package.
It owns the historical modes that matter for the backend:
- initial full pull
- replay-assisted full pull
- daily backfill / gap repair
- scoped emergency reset
"""

from __future__ import annotations

import atexit
import json
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from core_engine.exit_codes import EXIT_CANCELLED, EXIT_LOCK_CONFLICT
from core_engine.tradingview import auth as tv_auth
from core_engine.reporting.historical_reporter import fmt_int
from core_engine.warehouse.maintenance import purge_staging
from core_engine.warehouse.reader import get_latest_bars
from core_engine.warehouse.connection import test_connection
from core_engine.historical import pipeline as _pipeline
from core_engine.historical.pipeline import (
    WAREHOUSE_MAINTENANCE_LOCK,
    _hlog,
    _reporter,
    _selected_timeframes,
    logger,
    replay_runtime,
    run_backfill,
    run_full_load,
    run_reset_scope,
)
from core_engine.historical.runtime_support import (
    EXIT_TV_UNAVAILABLE,
    HOLE_LOOKBACK_DAYS,
    HistoricalPullCancelled,
    apply_replay_cli_options,
    build_parser,
    resolve_scope,
    tv_probe,
)
from core_engine.coordination.locks import (
    HISTORICAL_JOB_LOCK,
    acquire,
    acquire_historical_job,
    cleanup_expired,
    fetch_lock,
    is_locked,
    release,
    release_historical_job,
)
from core_engine.reporting.discord import QUICK_COMMANDS_HINT, notify_historical_event, flush_pending
from core_engine.settings import (
    BACKEND,
    DIRECT_TFS,
    HISTORICAL_CANCEL_FILE,
    HISTORICAL_SUMMARY_LOG,
    PIPELINE_LOG,
    SYMBOLS,
    get_historical_timeframes,
)


RUN_SUMMARY_FILE = str(HISTORICAL_SUMMARY_LOG)




def _cleanup_orphan_warehouse_maintenance(
    reason: str,
    *,
    allow_after_historical_lock: bool = False,
) -> None:
    """Clear an old warehouse lock only when no historical job owns it."""
    record = fetch_lock(WAREHOUSE_MAINTENANCE_LOCK, active_only=True)
    if record is None:
        return
    payload = record.payload or ""
    if allow_after_historical_lock and "kind=warehouse_write" in payload:
        return
    if is_locked(HISTORICAL_JOB_LOCK) and not allow_after_historical_lock:
        return
    logger.warning(
        "%s",
        _hlog(
            "Old warehouse maintenance lock cleared",
            reason=reason,
            previous_payload=(payload or "-")[:120],
            result="cleared",
        ),
    )
    release(WAREHOUSE_MAINTENANCE_LOCK)




def _write_run_summary(mode: str, started: datetime, elapsed: float, stats: dict[str, Any]) -> None:
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "started": started.isoformat(),
        "elapsed_seconds": round(elapsed, 3),
        "stats": stats,
    }
    try:
        os.makedirs(os.path.dirname(RUN_SUMMARY_FILE), exist_ok=True)
        with open(RUN_SUMMARY_FILE, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")
    except OSError as exc:
        logger.warning("%s", _hlog("Run summary write failed", reason=exc, result="warning"))


def _stats_for_operator(stats: dict[str, Any]) -> str:
    labels = {
        "queued": "Pairs selected",
        "ok": "Pairs completed",
        "fail": "Pairs failed",
        "inserted": "Saved rows",
        "preview_fact": "Rows that would be deleted from main table",
        "preview_staging": "Rows that would be deleted from temporary tables",
        "deleted_fact": "Rows deleted from main table",
        "deleted_staging": "Rows deleted from temporary tables",
        "cleanup_warning": "Post-run cleanup warning",
    }
    lines = []
    for key, label in labels.items():
        if key in stats:
            value = stats.get(key)
            if isinstance(value, (int, float)):
                value = fmt_int(value)
            lines.append(f"- {label}: {value}")
    if not lines:
        lines = [f"- {key}: {value}" for key, value in sorted(stats.items())]
    return "\n".join(lines)


_REPLAY_NAME_TO_ATTR = {
    "TV_WS_REPLAY_ENABLED": "enabled",
    "TV_WS_REPLAY_TFS": "tfs",
    "TV_WS_REPLAY_ENDPOINT": "endpoint",
    "TV_WS_REPLAY_START_DATE": "start_date",
    "TV_WS_REPLAY_WINDOW_BARS": "window_bars",
    "TV_WS_REPLAY_STEP_BARS": "step_bars",
    "TV_WS_REPLAY_MAX_WINDOWS_PER_PAIR": "max_windows_per_pair",
    "TV_WS_REPLAY_TIMEOUT_SEC": "timeout_sec",
}


def _set_replay_runtime(name: str, value: Any) -> None:
    attr = _REPLAY_NAME_TO_ATTR.get(name)
    if attr:
        setattr(replay_runtime, attr, value)
    if name == "TV_WS_REPLAY_ENABLED":
        _reporter.replay_enabled = bool(value)
    if name == "TV_WS_REPLAY_TFS":
        _reporter.replay_tfs = {str(tf).upper() for tf in value}


def _apply_replay_cli_options(args: Any) -> list[str]:
    valid_tfs = {tf for _, tf, _, _ in get_historical_timeframes()}
    return apply_replay_cli_options(args, valid_tfs=valid_tfs, set_runtime=_set_replay_runtime)


def detect_mode() -> str:
    latest = get_latest_bars()
    return "full" if not latest else "gap"


def _auth_connection() -> tuple[SimpleNamespace, str]:
    token, source = tv_auth.bootstrap(logger)
    safe_source = tv_auth.safe_auth_source_label(source)
    tv_auth.set_current_token(token)
    tv = SimpleNamespace(token=token)
    logger.info("%s", _hlog("TradingView login ready", source=safe_source, result="ready"))
    return tv, safe_source




def _describe_historical_owner() -> dict[str, Any]:
    record = fetch_lock(HISTORICAL_JOB_LOCK, active_only=True)
    if not record:
        return {"active": False}
    meta = record.meta
    return {
        "active": True,
        "pid": meta.get("pid"),
        "owner": meta.get("owner"),
        "started": meta.get("started"),
        "heartbeat": meta.get("heartbeat"),
        "expires_at": str(record.expires_at or ""),
    }


def _request_historical_cancel(reason: str) -> None:
    HISTORICAL_CANCEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORICAL_CANCEL_FILE.write_text(
        json.dumps(
            {
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _clear_historical_cancel(reason: str) -> None:
    try:
        HISTORICAL_CANCEL_FILE.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("%s", _hlog("Cancel signal cleanup failed", after=reason, reason=exc, result="warning"))


def _acquire_historical_or_report(owner: str, args: Any, *, duration_min: int) -> Any:
    detail = _describe_historical_owner()
    if detail.get("active") and args.on_conflict == "replace":
        logger.warning("%s", _hlog("Existing historical job found", policy="replace", active_pid=detail.get("pid"), result="requesting_safe_stop"))
        logger.warning(
            "Operator note: the old historical job will stop at the next safe checkpoint. "
            "It may finish the current symbol/timeframe and write those rows before the new job starts."
        )
        _request_historical_cancel(f"replace_requested_by_{owner}")
        notify_historical_event(
            severity="WARNING",
            title="Historical job replacement requested",
            summary=(
                "A new historical request asked the currently running historical job to stop safely "
                "before the new job starts. The old job may finish its current symbol/timeframe first."
            ),
            current_state={
                "new_owner": owner,
                "old_pid": detail.get("pid") or "-",
                "old_owner": detail.get("owner") or "-",
            },
            data_result="The new job will wait until the old job reaches a safe checkpoint and releases its lock.",
            health_risk="Medium. Historical coverage may remain partial until the replacement job finishes.",
            recommended_action="Watch historical_pulling.log until the old job stops and the new job starts.",
            trace={"lock": HISTORICAL_JOB_LOCK, "cancel_file": str(HISTORICAL_CANCEL_FILE)},
            result="stopping",
        )

    wait_timeout = 0.0
    poll_sec = 1.0
    if args.on_conflict == "wait":
        wait_timeout = 30 * 60.0
        poll_sec = 15.0
    elif args.on_conflict == "replace":
        wait_timeout = float(BACKEND.shutdown_grace_sec + 60)
        poll_sec = 2.0

    lease = acquire_historical_job(
        owner,
        logger,
        duration_min=duration_min,
        wait_timeout_sec=wait_timeout,
        poll_sec=poll_sec,
    )
    if lease is None:
        detail = _describe_historical_owner()
        logger.warning("%s", _hlog("Historical start skipped", active_pid=detail.get("pid"), active_owner=detail.get("owner"), result="already_running"))
        notify_historical_event(
            severity="WARNING",
            title="Historical start skipped",
            summary="A historical pulling request was not started because another historical job is already running.",
            current_state={
                "requested_owner": owner,
                "active_pid": detail.get("pid") or "-",
                "active_owner": detail.get("owner") or "-",
                "policy": args.on_conflict,
            },
            data_result="No new historical writes were started by this duplicate request.",
            health_risk="Low. The system prevented overlapping historical jobs.",
            recommended_action="Let the active job finish, queue the new request from the launcher, or choose replace intentionally.",
            trace={"lock": HISTORICAL_JOB_LOCK},
            result="skipped",
        )
        flush_pending()
    else:
        _clear_historical_cancel(f"{owner}_lock_acquired")
    return lease












def main(argv: list[str] | None = None) -> int:
    started = datetime.now(timezone.utc)
    parser = build_parser(HOLE_LOOKBACK_DAYS)
    args = parser.parse_args(argv)

    scope = resolve_scope(
        SYMBOLS,
        asset_type_csv=args.asset_type,
        symbols_csv=args.symbols,
        timeframes_csv=args.timeframes,
    )
    if not scope.ok:
        logger.error("%s", _hlog("Invalid run scope", action=scope.error_action, detail=scope.error_amount, status=scope.error_status, result="failed"))
        return 2

    _pipeline._TF_FILTER = set(scope.timeframe_filter)

    try:
        replay_changes = _apply_replay_cli_options(args)
    except ValueError as exc:
        logger.error("%s", _hlog("Replay setting rejected", reason=exc, result="failed"))
        return 2

    if args.force_unlock:
        release("tv_historical_job")
    cleanup_expired()
    if not _describe_historical_owner().get("active"):
        _clear_historical_cancel("historical_start_no_active_job")

    if not test_connection():
        return 4

    mode = args.mode if args.mode != "auto" else detect_mode()
    _reporter.start(
        mode=mode,
        started=started,
        symbols=len(scope.symbols),
        timeframes=len(_selected_timeframes(_pipeline._TF_FILTER)),
        lookback_days=args.hole_lookback_days,
        dry_run=args.dry_run,
        scope_events=scope.events,
        replay_changes=replay_changes,
    )

    if mode == "reset":
        stats: dict[str, int]
        historical_lease = None
        maintenance_acquired = False
        try:
            if not args.dry_run and not (args.reset and args.yes):
                stats = run_reset_scope(
                    symbols=scope.symbols,
                    dry_run=True,
                    confirmed=False,
                )
                logger.error(
                    "%s",
                    _hlog(
                        "Reset stopped after preview",
                        preview=stats,
                        required_confirmation="--reset --yes",
                        result="not_deleted",
                    ),
                )
                return 2
            if not args.dry_run:
                _cleanup_orphan_warehouse_maintenance("historical_reset_start")
                historical_lease = _acquire_historical_or_report(
                    "historical-reset",
                    args,
                    duration_min=60,
                )
                if historical_lease is None:
                    return EXIT_LOCK_CONFLICT
                atexit.register(release_historical_job, historical_lease, "historical-reset", logger)
                _cleanup_orphan_warehouse_maintenance(
                    "historical_reset_lock_acquired",
                    allow_after_historical_lock=True,
                )
                maintenance_acquired = acquire(WAREHOUSE_MAINTENANCE_LOCK, duration_min=60)
                if not maintenance_acquired:
                    logger.error("%s", _hlog("Reset could not enter maintenance mode", lock=WAREHOUSE_MAINTENANCE_LOCK, result="failed"))
                    return EXIT_LOCK_CONFLICT
            stats = run_reset_scope(
                symbols=scope.symbols,
                dry_run=args.dry_run,
                confirmed=bool(args.reset and args.yes),
            )
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            _write_run_summary(mode, started, elapsed, stats)
            _reporter.run_summary(mode=mode, elapsed_seconds=elapsed, stats=stats, dry_run=args.dry_run)
            if args.dry_run:
                notify_historical_event(
                    severity="WARNING",
                    title="Historical reset preview",
                    summary="The reset command ran in preview mode only. No OHLCV rows were deleted.",
                    current_state={"mode": mode, "dry_run": True, "confirmed_delete": False},
                    data_result=_stats_for_operator(stats),
                    health_risk="Low. This was a preview and did not modify the warehouse.",
                    recommended_action="Review the preview numbers. Run with --reset --yes only if you intentionally want to delete this scope.",
                    trace={"scope_symbols": len(scope.symbols), "runtime_summary": "runtime/run/historical_last_run.json"},
                    result="warning",
                )
            else:
                notify_historical_event(
                    severity="WARNING",
                    title="Historical reset completed",
                    summary="Historical OHLCV rows were deleted for the confirmed reset scope.",
                    current_state={"mode": mode, "dry_run": False, "confirmed_delete": True},
                    data_result=_stats_for_operator(stats),
                    health_risk="Medium. The selected data range now needs a fresh historical pull before it is complete again.",
                    recommended_action="Run the matching historical pull/backfill after confirming the reset scope is correct.",
                    trace={"scope_symbols": len(scope.symbols), "runtime_summary": "runtime/run/historical_last_run.json"},
                    result="completed",
                )
            return 0
        except ValueError as exc:
            logger.error("%s", _hlog("Historical reset refused", reason=exc, result="stopped"))
            return 2
        except Exception as exc:
            logger.exception("%s", _hlog("Historical reset failed", reason=exc, result="failed"))
            notify_historical_event(
                severity="ERROR",
                title="Historical reset failed",
                summary="The reset command did not finish.",
                current_state={"mode": mode, "dry_run": args.dry_run},
                data_result="No successful reset result was recorded.",
                health_risk="Medium. The warehouse may still be unchanged, but the intended maintenance action did not complete.",
                reason=str(exc),
                recommended_action="Check runtime/logs/operation/historical_pulling.log, fix the reported cause, then rerun the reset if still needed.",
                trace={"pipeline_log": str(PIPELINE_LOG)},
                result="failed",
            )
            return 1
        finally:
            if maintenance_acquired:
                release(WAREHOUSE_MAINTENANCE_LOCK)
            if not args.dry_run:
                release_historical_job(historical_lease, "historical-reset", logger)
            flush_pending()

    ok, detail = tv_probe(logger, symbols=scope.symbols, direct_tfs=DIRECT_TFS)
    if not ok:
        logger.error("%s", _hlog("TradingView connection check failed", reason=detail, result="failed"))
        notify_historical_event(
            severity="ERROR",
            title="Historical pull could not reach TradingView",
            summary="The historical job stopped before pulling data because TradingView was not reachable.",
            current_state={"mode": mode, "symbols": len(scope.symbols)},
            data_result="No historical candles were fetched in this run.",
            health_risk="High for backfill coverage. Missing ranges will remain until TradingView access recovers.",
            reason=detail,
            recommended_action=f"Check network and TradingView login, then rerun historical pulling. {QUICK_COMMANDS_HINT}",
            trace={"pipeline_log": str(PIPELINE_LOG)},
            result="failed",
        )
        flush_pending()
        return EXIT_TV_UNAVAILABLE
    logger.info("%s", _hlog("TradingView connection check passed", result="ready"))

    tv, auth_mode = _auth_connection()
    if auth_mode == "guest" or getattr(tv, "token", None) == tv_auth.GUEST_TOKEN:
        logger.warning("%s", _hlog("TradingView limited login mode", risk="history_depth_may_be_limited", result="warning"))

    maintenance_scope = mode in {"full", "gap"}
    historical_lease = None
    if not args.dry_run:
        if maintenance_scope:
            _cleanup_orphan_warehouse_maintenance("historical_pipeline_start")
        historical_lease = _acquire_historical_or_report(
            "historical-pipeline",
            args,
            duration_min=240,
        )
        if historical_lease is None:
            return EXIT_LOCK_CONFLICT
        atexit.register(release_historical_job, historical_lease, "historical-pipeline", logger)
        if maintenance_scope:
            _cleanup_orphan_warehouse_maintenance(
                "historical_pipeline_lock_acquired",
                allow_after_historical_lock=True,
            )

    stats: dict[str, int]
    try:
        if mode == "full":
            stats = run_full_load(tv, symbols=scope.symbols, dry_run=args.dry_run)
        else:
            stats = run_backfill(
                tv,
                symbols=scope.symbols,
                dry_run=args.dry_run,
                hole_lookback_days=args.hole_lookback_days,
            )
        if not args.dry_run:
            purged = purge_staging(days_to_keep=7)
            cleanup_error = purged.get("__error__") if isinstance(purged, dict) else None
            if cleanup_error:
                stats["cleanup_warning"] = f"Temporary table cleanup failed: {cleanup_error}"
                logger.warning(
                    "%s",
                    _hlog(
                        "Temporary table cleanup failed",
                        reason=cleanup_error,
                        result="warning",
                    ),
                )
            else:
                logger.info("%s", _hlog("Temporary table cleanup completed", detail=purged, result="completed"))
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        _write_run_summary(mode, started, elapsed, stats)
        _reporter.run_summary(mode=mode, elapsed_seconds=elapsed, stats=stats, dry_run=args.dry_run)
        has_warning = stats.get("fail", 0) != 0 or bool(stats.get("cleanup_warning"))
        notify_historical_event(
            severity="WARNING" if has_warning else "SUCCESS",
            title="Historical pull completed",
            summary="The historical OHLCV job finished and wrote its run summary.",
            current_state={"mode": mode, "duration_seconds": f"{elapsed:.0f}", "dry_run": args.dry_run},
            data_result=_stats_for_operator(stats),
            health_risk=(
                "Medium. Data pull completed, but temporary table cleanup needs operator review."
                if stats.get("cleanup_warning")
                else "Low. No failed symbol/timeframe pairs were reported."
                if stats.get("fail", 0) == 0
                else "Medium. Some pairs failed and may still need repair."
            ),
            recommended_action=(
                "Check SQL Server transaction log/database maintenance, then rerun status or cleanup later."
                if stats.get("cleanup_warning")
                else
                "No action needed unless counts look unexpected."
                if stats.get("fail", 0) == 0
                else "Review runtime/logs/operation/historical_pulling.log and rerun gap repair for failed pairs."
            ),
            trace={"pipeline_log": str(PIPELINE_LOG), "runtime_summary": "runtime/run/historical_last_run.json"},
            result="warning" if has_warning else "completed",
        )
        return 0 if stats.get("fail", 0) == 0 else 1
    except HistoricalPullCancelled as exc:
        logger.warning("%s", _hlog("Historical pull stopped safely", reason=exc, result="stopped"))
        notify_historical_event(
            severity="WARNING",
            title="Historical pull stopped safely",
            summary="The historical job received a cooperative stop/handoff request and exited without a forced kill.",
            current_state={"mode": mode, "dry_run": args.dry_run},
            data_result="Partial results may already be saved for pairs completed before the stop.",
            health_risk="Medium. Remaining gaps may still need a later backfill.",
            reason=str(exc),
            recommended_action="Rerun gap repair when the current maintenance window is clear.",
            trace={"pipeline_log": str(PIPELINE_LOG)},
            result="stopped",
        )
        return EXIT_CANCELLED
    except Exception as exc:
        logger.exception("%s", _hlog("Historical pull failed", reason=exc, result="failed"))
        notify_historical_event(
            severity="ERROR",
            title="Historical pull failed",
            summary="The historical OHLCV job stopped with an error before completing its requested scope.",
            current_state={"mode": mode, "dry_run": args.dry_run},
            data_result="The run did not complete. Some pairs may have been saved before the failure.",
            health_risk="High for data coverage. Missing or stale ranges may remain.",
            reason=str(exc),
            recommended_action=f"Open runtime/logs/operation/historical_pulling.log, fix the cause, then rerun gap repair. {QUICK_COMMANDS_HINT}",
            trace={"pipeline_log": str(PIPELINE_LOG)},
            result="failed",
        )
        return 1
    finally:
        if not args.dry_run:
            release_historical_job(historical_lease, "historical-pipeline", logger)
        flush_pending()


if __name__ == "__main__":
    sys.exit(main())
