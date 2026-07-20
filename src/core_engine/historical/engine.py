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
import logging
import os
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pandas as pd

from core_engine.tradingview import auth as tv_auth
from core_engine.tradingview import history_client as tv_history
from core_engine.warehouse.repository import (
    delete_fact_bars_range,
    delete_staging_bars,
    get_fact_bar_window_context,
    get_latest_bars,
    get_staging_bar_window,
    insert_staging_batch,
    preview_ohlcv_reset_scope,
    purge_staging,
    reset_ohlcv_scope,
    run_etl_direct,
)
from core_engine.warehouse.validation import validate_ohlcv_df
from core_engine.warehouse.connection import test_connection
from core_engine.historical.runtime_support import (
    EXIT_TV_UNAVAILABLE,
    HOLE_LOOKBACK_DAYS,
    MAX_CONSECUTIVE_FAIL,
    HistoricalPullCancelled,
    apply_replay_cli_options,
    build_parser,
    find_hole_pairs,
    find_stale_pairs,
    flatten_verified_gap_windows,
    fmt_gap,
    has_any_filter,
    load_verified_gaps,
    raise_if_cancelled,
    resolve_scope,
    save_verified_gaps,
    sleep_for,
    tv_probe,
)
from core_engine.coordination.locks import (
    DP_PROGRAM_LOCK,
    HISTORICAL_JOB_LOCK,
    HistoricalJobHandoffRequested,
    LIVE_RUNTIME_LOCK,
    acquire,
    acquire_historical_job,
    cleanup_expired,
    cleanup_stale_lock,
    fetch_lock,
    is_locked,
    release,
    release_historical_job,
    renew,
    wait_for_historical_slot,
)
from core_engine.reporting.historical_reporter import HistoricalReporter, fmt_int
from core_engine.logkit.factory import setup_logger
from core_engine.logkit.formatters import operation_line
from core_engine.reporting.discord import QUICK_COMMANDS_HINT, notify_historical_event, tg_flush
from core_engine.settings import (
    BACKEND,
    DIRECT_TFS,
    HISTORICAL,
    HISTORICAL_CANCEL_FILE,
    HISTORICAL_SUMMARY_LOG,
    HISTORICAL_PROVIDER,
    PIPELINE_LOG,
    SYMBOLS,
    TF_STAGING,
    TF_MINUTES,
    TRADINGVIEW,
    TV_WS_HISTORY_FALLBACK_ENDPOINTS,
    TV_WS_HISTORY_REQUEST_MORE_BARS,
    TV_WS_HISTORY_REQUEST_MORE_ROUNDS,
    TV_WS_REPLAY_ENABLED,
    TV_WS_REPLAY_ENDPOINT,
    TV_WS_REPLAY_MAX_WINDOWS_PER_PAIR,
    TV_WS_REPLAY_START_DATE,
    TV_WS_REPLAY_STEP_BARS,
    TV_WS_REPLAY_TFS,
    TV_WS_REPLAY_TIMEOUT_SEC,
    TV_WS_REPLAY_WINDOW_BARS,
    VERIFIED_MARKET_GAPS,
    get_historical_timeframes,
    get_tf_interval_map,
)


RESULT_ERROR = -1
RESULT_TV_EMPTY = -2
WAREHOUSE_MAINTENANCE_LOCK = "warehouse_maintenance"
WAREHOUSE_WRITE_LOCK_TTL_MIN = 15
WAREHOUSE_WRITE_LOCK_WAIT_SEC = 120.0
WAREHOUSE_WRITE_LOCK_POLL_SEC = 5.0
TF_INTERVAL = get_tf_interval_map()
RUN_SUMMARY_FILE = str(HISTORICAL_SUMMARY_LOG)

logger = setup_logger(
    "historical_pulling",
    str(PIPELINE_LOG),
    rotating=True,
    utc=True,
    pipe_format=True,
    normalize_prefixes=True,
)
_reporter = HistoricalReporter(
    logger,
    replay_enabled=TV_WS_REPLAY_ENABLED,
    replay_tfs=set(TV_WS_REPLAY_TFS),
)
_TF_FILTER: set[str] = set()
_warehouse_write_lock_depth = 0


def _hlog(event: str, *details: str, **fields: Any) -> str:
    return operation_line("HISTORICAL", event, *details, **fields)


def _fmt_ts(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return value.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)


def _warehouse_lock_payload(owner: str) -> str:
    return (
        f"kind=warehouse_write;"
        f"owner={owner};"
        f"pid={os.getpid()};"
        f"started={datetime.now(timezone.utc).isoformat()}"
    )


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


class _WarehouseWriteSlot:
    """Short DB write window so live fetching is not blocked by long backfills."""

    def __init__(self, owner: str) -> None:
        self.owner = owner
        self.acquired = False

    def __enter__(self) -> "_WarehouseWriteSlot":
        global _warehouse_write_lock_depth
        if _warehouse_write_lock_depth > 0:
            _warehouse_write_lock_depth += 1
            return self

        wait_for_historical_slot(self.owner, logger)
        deadline = time.monotonic() + WAREHOUSE_WRITE_LOCK_WAIT_SEC
        attempt = 0
        while True:
            attempt += 1
            if acquire(
                WAREHOUSE_MAINTENANCE_LOCK,
                duration_min=WAREHOUSE_WRITE_LOCK_TTL_MIN,
                payload=_warehouse_lock_payload(self.owner),
            ):
                self.acquired = True
                _warehouse_write_lock_depth = 1
                return self

            record = fetch_lock(WAREHOUSE_MAINTENANCE_LOCK, active_only=True)
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "warehouse maintenance lock is still active; "
                    "historical write stopped so live fetching can stay prioritized"
                )
            if attempt == 1 or attempt % 6 == 0:
                logger.warning(
                    "%s",
                    _hlog(
                        "Historical write waiting for warehouse lock",
                        owner=self.owner,
                        active_payload=((record.payload if record else "") or "-")[:120],
                        result="waiting",
                    ),
                )
            wait_for_historical_slot(self.owner, logger)
            time.sleep(WAREHOUSE_WRITE_LOCK_POLL_SEC)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        global _warehouse_write_lock_depth
        if _warehouse_write_lock_depth > 0:
            _warehouse_write_lock_depth -= 1
        if self.acquired and _warehouse_write_lock_depth <= 0:
            _warehouse_write_lock_depth = 0
            release(WAREHOUSE_MAINTENANCE_LOCK)


def _warehouse_write_slot(owner: str) -> _WarehouseWriteSlot:
    return _WarehouseWriteSlot(owner)


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


def _set_replay_runtime(name: str, value: Any) -> None:
    globals()[name] = value
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


def _refresh_mid_run(tv: SimpleNamespace) -> bool:
    ok = tv_auth.refresh_mid_run(tv, logger)
    if ok:
        logger.info("%s", _hlog("TradingView login refreshed", result="success"))
    else:
        logger.error("%s", _hlog("TradingView login refresh failed", result="failed"))
    return ok


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
        tg_flush()
    else:
        _clear_historical_cancel(f"{owner}_lock_acquired")
    return lease


def _combine_frames(*frames: pd.DataFrame | None) -> pd.DataFrame | None:
    valid = [df for df in frames if df is not None and not df.empty]
    if not valid:
        return None
    combined = pd.concat(valid)
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined.sort_index()


def _drop_unclosed_history_bars(
    df: pd.DataFrame | None,
    *,
    sym: dict[str, Any],
    tf_code: str,
) -> tuple[pd.DataFrame | None, str]:
    """Remove TradingView bars whose timeframe has not closed yet."""
    if df is None or df.empty or not HISTORICAL.drop_open_last_bar:
        return df, ""

    tf_minutes = int(TF_MINUTES.get(tf_code.upper(), 0) or 0)
    if tf_minutes <= 0:
        return df, ""

    try:
        idx_utc = pd.DatetimeIndex(df.index)
        if idx_utc.tz is None:
            idx_utc = idx_utc.tz_localize("UTC")
        else:
            idx_utc = idx_utc.tz_convert("UTC")

        close_times = idx_utc + pd.Timedelta(minutes=tf_minutes)
        now_utc = pd.Timestamp.now(tz="UTC")
        closed_mask = close_times <= now_utc
    except Exception as exc:
        logger.warning(
            "%s",
            _hlog(
                "Open candle filter check failed",
                symbol=sym.get("tv_symbol", "?"),
                timeframe=tf_code,
                reason=exc,
                result="kept_original_rows",
            ),
        )
        return df, "open_bar_filter:error"

    dropped = int((~closed_mask).sum())
    if dropped <= 0:
        return df, ""

    dropped_index = idx_utc[~closed_mask]
    latest_open = dropped_index.max().strftime("%Y-%m-%d %H:%M UTC")
    filtered = df.loc[closed_mask].copy()
    logger.debug(
        "%s",
        _hlog(
            "Open candle removed",
            symbol=sym.get("tv_symbol", "?"),
            timeframe=tf_code,
            removed=dropped,
            latest_open_bar=latest_open,
            kept_rows=len(filtered),
            original_rows=len(df),
            result="closed_candles_only",
        ),
    )
    return filtered, f"open_bar_filter:dropped={dropped}:latest={latest_open}"


def _fetch_history_frame(
    tv: SimpleNamespace,
    sym: dict[str, Any],
    tf_code: str,
    n_bars: int,
    *,
    allow_replay: bool,
) -> tuple[pd.DataFrame | None, str]:
    token = getattr(tv, "token", None)
    result = tv_history.fetch_history(
        symbol=sym["tv_symbol"],
        exchange=sym["tv_exchange"],
        tf_code=tf_code,
        n_bars=n_bars,
        logger=logger,
        timeout_sec=TRADINGVIEW.history_timeout_sec,
        token=token,
        endpoint=TRADINGVIEW.history_endpoint,
        request_more_rounds=TV_WS_HISTORY_REQUEST_MORE_ROUNDS,
        request_more_bars=TV_WS_HISTORY_REQUEST_MORE_BARS,
    )
    frames: list[pd.DataFrame | None] = [result.df]
    notes = [f"history:{result.status}:{result.returned}"]

    replay_enabled = bool(allow_replay and TV_WS_REPLAY_ENABLED and tf_code.upper() in TV_WS_REPLAY_TFS)
    if replay_enabled:
        if result.df is not None and not result.df.empty:
            end_before = result.df.index.min().to_pydatetime()
        else:
            end_before = datetime.now(timezone.utc)
        replay = tv_history.crawl_replay_history(
            symbol=sym["tv_symbol"],
            exchange=sym["tv_exchange"],
            tf_code=tf_code,
            start_utc=TV_WS_REPLAY_START_DATE,
            end_before_utc=end_before,
            endpoint=TV_WS_REPLAY_ENDPOINT,
            window_bars=TV_WS_REPLAY_WINDOW_BARS,
            step_bars=TV_WS_REPLAY_STEP_BARS,
            max_windows=TV_WS_REPLAY_MAX_WINDOWS_PER_PAIR,
            advance_factor=HISTORICAL.replay_advance_factor,
            timeout_sec=TV_WS_REPLAY_TIMEOUT_SEC,
            logger=logger,
        )
        frames.insert(0, replay.df)
        notes.append(f"replay:{replay.status}:{replay.returned}/{replay.windows}w")

    if result.df is None and result.error:
        notes.append(f"error:{result.error[:160]}")
    combined = _combine_frames(*frames)
    combined, filter_note = _drop_unclosed_history_bars(combined, sym=sym, tf_code=tf_code)
    if filter_note:
        notes.append(filter_note)
    return combined, " ".join(notes)


def _write_ohlcv_frame(
    df: pd.DataFrame | None,
    sym: dict[str, Any],
    tf_code: str,
    *,
    skip_etl: bool = False,
) -> int:
    if df is None or df.empty:
        return RESULT_TV_EMPTY
    staging = TF_STAGING.get(tf_code)
    if not staging:
        logger.error("%s", _hlog("Temporary table missing", timeframe=tf_code, result="failed"))
        return RESULT_ERROR

    clean_df, _ = validate_ohlcv_df(df, sym["tv_symbol"], tf_code, logger)
    if clean_df is None or clean_df.empty:
        return RESULT_TV_EMPTY

    with _warehouse_write_slot(f"historical-write:{sym['tv_symbol']}:{tf_code}"):
        staged = insert_staging_batch(
            clean_df,
            sym["symbol_id"],
            staging,
            source="historical_pulling",
            symbol=sym["tv_symbol"],
            tf_code=tf_code,
        )
        if skip_etl:
            return staged
        if staged <= 0:
            return 0
        inserted = run_etl_direct(
            sym["symbol_id"],
            tf_code,
            staging,
            source="historical_pulling",
            symbol=sym["tv_symbol"],
        )
    return int(inserted)


def pull_and_store(
    tv: SimpleNamespace,
    sym: dict[str, Any],
    tf_code: str,
    n_bars: int,
    interval: str,
    *,
    skip_etl: bool = False,
    allow_replay: bool = True,
) -> int:
    del interval  # interval is kept in the signature for pipeline compatibility.
    df, note = _fetch_history_frame(tv, sym, tf_code, n_bars, allow_replay=allow_replay)
    if df is None or df.empty:
        logger.warning(
            "%s",
            _hlog("TradingView returned no rows", symbol=sym["tv_symbol"], timeframe=tf_code, detail=note, result="empty"),
        )
        return RESULT_TV_EMPTY
    try:
        inserted = _write_ohlcv_frame(df, sym, tf_code, skip_etl=skip_etl)
        logger.debug(
            "%s",
            _hlog(
                "Pair write completed",
                symbol=sym["tv_symbol"],
                timeframe=tf_code,
                rows_received=len(df),
                rows_saved=inserted,
                tradingview_note=note[:120],
                result="completed" if inserted >= 0 else "failed",
            ),
        )
        return inserted
    except Exception as exc:
        logger.exception("%s", _hlog("Pair write failed", symbol=sym["tv_symbol"], timeframe=tf_code, reason=exc, result="failed"))
        return RESULT_ERROR


def pull_with_retry(
    tv: SimpleNamespace,
    sym: dict[str, Any],
    tf_code: str,
    n_bars: int,
    interval: str,
    *,
    max_retries: int = 3,
    allow_replay: bool = True,
) -> int:
    result = pull_and_store(tv, sym, tf_code, n_bars, interval, allow_replay=allow_replay)
    delays = tuple(HISTORICAL.retry_delays or (10, 30, 60))
    for attempt in range(1, max_retries + 1):
        if result >= 0:
            break
        delay = delays[min(attempt - 1, len(delays) - 1)]
        logger.warning(
            "%s",
            _hlog(
                "Pair retry scheduled",
                symbol=sym["tv_symbol"],
                timeframe=tf_code,
                attempt=f"{attempt}/{max_retries}",
                wait_seconds=delay,
                result="retrying",
            ),
        )
        time.sleep(delay)
        result = pull_and_store(tv, sym, tf_code, n_bars, interval, allow_replay=allow_replay)
    if result < 0:
        logger.error(
            "%s",
            _hlog(
                "Pair failed after retries",
                symbol=sym["tv_symbol"],
                timeframe=tf_code,
                attempts=max_retries + 1,
                result="failed",
            ),
        )
    return result


def repull_full_symbol(tv: SimpleNamespace, sym: dict[str, Any], tf_code: str, interval: str) -> tuple[int, int]:
    staging = TF_STAGING.get(tf_code)
    if not staging:
        logger.error("%s", _hlog("Repair temporary table missing", symbol=sym["tv_symbol"], timeframe=tf_code, result="failed"))
        return 0, RESULT_ERROR
    with _warehouse_write_slot(f"historical-repair-stage-clear:{sym['tv_symbol']}:{tf_code}"):
        delete_staging_bars(
            sym["symbol_id"],
            staging,
            source="historical_repair",
            symbol=sym["tv_symbol"],
            tf_code=tf_code,
        )
    staged = pull_and_store(
        tv,
        sym,
        tf_code,
        next(n for i, t, s, n in get_historical_timeframes() if t == tf_code),
        interval,
        skip_etl=True,
        allow_replay=False,
    )
    if staged < 0:
        return 0, staged
    stage_first, stage_last, stage_count = get_staging_bar_window(sym["symbol_id"], staging)
    if stage_count <= 0 or stage_first is None or stage_last is None:
        return 0, RESULT_TV_EMPTY
    before = get_fact_bar_window_context(sym["symbol_id"], tf_code, stage_first, stage_last)
    logger.info(
        "%s",
        _hlog(
            "Repair window prepared",
            symbol=sym["tv_symbol"],
            timeframe=tf_code,
            window=f"{_fmt_ts(stage_first)} -> {_fmt_ts(stage_last)}",
            temporary_rows=stage_count,
            existing_rows=before.get("window_count", 0),
            result="ready",
        ),
    )
    with _warehouse_write_slot(f"historical-repair-fact-replace:{sym['tv_symbol']}:{tf_code}"):
        deleted = delete_fact_bars_range(
            sym["symbol_id"],
            tf_code,
            stage_first,
            stage_last,
            source="historical_repair",
            symbol=sym["tv_symbol"],
        )
        inserted = run_etl_direct(
            sym["symbol_id"],
            tf_code,
            staging,
            source="historical_repair",
            symbol=sym["tv_symbol"],
        )
    return deleted, inserted


def _selected_timeframes(tf_filter: set[str] | None = None) -> list[tuple[str, str, str, int]]:
    active = set(tf_filter or set())
    return [(i, tf, stg, n) for i, tf, stg, n in get_historical_timeframes() if not active or tf in active]


def run_full_load(tv: SimpleNamespace, *, symbols: list[dict[str, Any]], dry_run: bool = False) -> dict[str, int]:
    stats = {"ok": 0, "fail": 0, "inserted": 0}
    pairs_total = len(symbols)
    tfs = _selected_timeframes(_TF_FILTER)
    for step_idx, (interval, tf_code, staging, n_bars) in enumerate(tfs, start=1):
        raise_if_cancelled(logger, f"full:{tf_code}")
        _reporter.tf_header(step_idx, len(tfs), tf_code, n_bars, pairs_total, staging)
        _reporter.pair_flow_header("PAIR FLOW")
        for index, sym in enumerate(symbols, start=1):
            raise_if_cancelled(logger, f"full:{sym['tv_symbol']}:{tf_code}")
            _reporter.pair_start(index, pairs_total, sym["tv_symbol"], tf_code, f"pull {fmt_int(n_bars)}")
            if dry_run:
                _reporter.pair_dry_run(index, pairs_total, sym["tv_symbol"], tf_code)
                stats["ok"] += 1
                continue
            wait_for_historical_slot("historical-full", logger)
            result = pull_with_retry(tv, sym, tf_code, n_bars, interval, allow_replay=True)
            _reporter.pair_result(index, pairs_total, sym["tv_symbol"], tf_code, result)
            if result >= 0:
                stats["ok"] += 1
                stats["inserted"] += max(0, result)
            else:
                stats["fail"] += 1
                if stats["fail"] >= MAX_CONSECUTIVE_FAIL and _refresh_mid_run(tv):
                    stats["fail"] = 0
                elif stats["fail"] >= MAX_CONSECUTIVE_FAIL:
                    raise RuntimeError("too many consecutive historical pull failures")
            sleep_for(sym["tv_symbol"])
        _reporter.tf_summary(tf_code, stats["ok"], stats["fail"], stats["inserted"])
    return stats


def run_backfill(
    tv: SimpleNamespace,
    *,
    symbols: list[dict[str, Any]],
    dry_run: bool = False,
    hole_lookback_days: int = HOLE_LOOKBACK_DAYS,
) -> dict[str, int]:
    latest = get_latest_bars()
    stale = find_stale_pairs(latest, tf_filter=_TF_FILTER, symbols=symbols)
    verified_gaps = load_verified_gaps()
    verified_windows = flatten_verified_gap_windows(verified_gaps)
    stale.extend(
        find_hole_pairs(
            stale,
            logger,
            verified_gaps=verified_gaps,
            lookback_days=max(1, int(hole_lookback_days or HOLE_LOOKBACK_DAYS)),
            symbols=symbols,
            tf_filter=_TF_FILTER,
        )
    )
    stats = {"queued": len(stale), "ok": 0, "fail": 0, "inserted": 0}
    if not stale:
        logger.info("%s", _hlog("Backfill scan completed", pairs_needing_repair=0, result="no_action_needed"))
        return stats

    _reporter.pair_flow_header("PAIR FLOW")
    for index, item in enumerate(stale, start=1):
        raise_if_cancelled(logger, f"backfill:{index}/{len(stale)}")
        sym = item["sym"]
        tf_code = item["tf_code"]
        n_bars = int(item["n_bars"])
        reason = str(item.get("reason", "STALE"))
        _reporter.pair_start(
            index,
            len(stale),
            sym["tv_symbol"],
            tf_code,
            f"pull {fmt_int(n_bars)}",
            range_=f"gap {fmt_gap(float(item.get('gap_hours') or 0))}",
            status=reason,
        )
        if dry_run:
            _reporter.pair_dry_run(index, len(stale), sym["tv_symbol"], tf_code)
            stats["ok"] += 1
            continue
        wait_for_historical_slot("historical-backfill", logger)
        result = pull_with_retry(
            tv,
            sym,
            tf_code,
            n_bars,
            TF_INTERVAL[tf_code],
            allow_replay=False,
        )
        _reporter.pair_result(index, len(stale), sym["tv_symbol"], tf_code, result)
        if result >= 0:
            stats["ok"] += 1
            stats["inserted"] += max(0, result)
            if result == 0:
                for gap_start, gap_end in item.get("gap_windows", []) or []:
                    verified_windows.add((sym["symbol_id"], tf_code, gap_start, gap_end))
        else:
            stats["fail"] += 1
            if stats["fail"] >= MAX_CONSECUTIVE_FAIL and not _refresh_mid_run(tv):
                raise RuntimeError("too many consecutive backfill failures")
        sleep_for(sym["tv_symbol"])

    if verified_windows:
        save_verified_gaps(verified_windows, logger)
    return stats


def run_reset_scope(*, symbols: list[dict[str, Any]], dry_run: bool, confirmed: bool) -> dict[str, int]:
    tfs = [tf_code for _, tf_code, _, _ in _selected_timeframes(_TF_FILTER)]
    symbol_ids = [int(sym["symbol_id"]) for sym in symbols]
    target = f"{len(symbol_ids)} symbol(s) x {len(tfs)} timeframe(s)"
    scope_label = f"{len(symbol_ids)} symbol(s), {len(tfs)} timeframe(s)"
    if len(symbols) == 1 and len(tfs) == 1:
        scope_label = f"{symbols[0]['tv_symbol']} {tfs[0]}"

    if not dry_run and confirmed:
        active_blockers = []
        cleanup_stale_lock(DP_PROGRAM_LOCK, stale_after_sec=10**9)
        cleanup_stale_lock(LIVE_RUNTIME_LOCK, stale_after_sec=10**9)
        if is_locked(DP_PROGRAM_LOCK):
            active_blockers.append("DP Program supervisor")
        if is_locked(LIVE_RUNTIME_LOCK):
            active_blockers.append("live fetching")
        if active_blockers:
            raise ValueError(
                "reset refused because "
                + " and ".join(active_blockers)
                + " is active; run Graceful Stop first, then run reset again"
            )

    preview = preview_ohlcv_reset_scope(symbol_ids, tfs, scope_label=scope_label)
    logger.warning(
        "%s",
        _hlog(
            "Reset preview completed",
            target=target,
            main_rows=fmt_int(int(preview.get("fact_rows", 0))),
            temporary_rows=fmt_int(int(preview.get("staging_total", 0))),
            result="preview_only" if dry_run else "pending_confirmation",
        ),
    )

    if dry_run:
        return {
            "preview_fact": int(preview.get("fact_rows", 0)),
            "preview_staging": int(preview.get("staging_total", 0)),
            "deleted_fact": 0,
            "deleted_staging": 0,
        }

    if not confirmed:
        raise ValueError("reset requires both --reset and --yes; rerun with --dry-run first")

    wait_for_historical_slot("historical-reset", logger)
    deleted = reset_ohlcv_scope(symbol_ids, tfs, scope_label=scope_label)
    try:
        if VERIFIED_MARKET_GAPS.exists():
            VERIFIED_MARKET_GAPS.unlink()
            logger.warning("%s", _hlog("Verified gap cache cleared", path=VERIFIED_MARKET_GAPS, result="cleared"))
    except OSError as exc:
        logger.warning("%s", _hlog("Verified gap cache clear failed", reason=exc, result="warning"))

    return {
        "preview_fact": int(preview.get("fact_rows", 0)),
        "preview_staging": int(preview.get("staging_total", 0)),
        "deleted_fact": int(deleted.get("fact_rows", 0)),
        "deleted_staging": int(deleted.get("staging_total", 0)),
    }


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

    global _TF_FILTER
    _TF_FILTER = set(scope.timeframe_filter)

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
        timeframes=len(_selected_timeframes(_TF_FILTER)),
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
                    return 5
                atexit.register(release_historical_job, historical_lease, "historical-reset", logger)
                _cleanup_orphan_warehouse_maintenance(
                    "historical_reset_lock_acquired",
                    allow_after_historical_lock=True,
                )
                maintenance_acquired = acquire(WAREHOUSE_MAINTENANCE_LOCK, duration_min=60)
                if not maintenance_acquired:
                    logger.error("%s", _hlog("Reset could not enter maintenance mode", lock=WAREHOUSE_MAINTENANCE_LOCK, result="failed"))
                    return 5
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
            tg_flush()

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
        tg_flush()
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
            return 5
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
    except (HistoricalPullCancelled, HistoricalJobHandoffRequested) as exc:
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
        return 130
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
        tg_flush()


if __name__ == "__main__":
    sys.exit(main())
