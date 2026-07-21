"""Historical OHLCV pipeline: fetch, write, retry, and the three mode runners.

Split out of historical/engine.py. This module owns the actual data
movement (TradingView fetch -> validate -> stage -> ETL) and the three
mode runners (run_full_load, run_backfill, run_reset_scope) that
engine.py's main() dispatches to based on --mode. Nothing here calls back
into engine.py: CLI parsing, lock/cancel-file ownership, and the top-level
main() sequence stay there.

replay_runtime replaces a `globals()[name] = value` pattern that used to
let CLI --replay-* flags override this module's own settings-derived
constants at runtime. That pattern only worked because everything lived
in one file; now that the read side (here) and the write side
(engine.py's _set_replay_runtime) are in different modules, the override
has to target a shared mutable object's attributes instead of rebinding
a name - see ReplayRuntimeOptions below.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pandas as pd

from core_engine.tradingview import auth as tv_auth
from core_engine.tradingview import history_client as tv_history
from core_engine.warehouse.maintenance import preview_ohlcv_reset_scope, reset_ohlcv_scope
from core_engine.warehouse.reader import fact_covers_window, get_latest_bars
from core_engine.warehouse.writer import insert_staging_batch, run_etl_direct
from core_engine.warehouse.validation import validate_ohlcv_df
from core_engine.historical.runtime_support import (
    HOLE_LOOKBACK_DAYS,
    HistoricalPullCancelled,
    MAX_CONSECUTIVE_FAIL,
    find_hole_pairs,
    find_stale_pairs,
    flatten_verified_gap_windows,
    fmt_gap,
    gap_threshold_minutes,
    load_verified_gaps,
    raise_if_cancelled,
    save_verified_gaps,
    sleep_for,
)
from core_engine.coordination.locks import (
    DP_PROGRAM_LOCK,
    LIVE_RUNTIME_LOCK,
    acquire,
    cleanup_stale_lock,
    fetch_lock,
    is_locked,
    release,
    wait_for_historical_slot,
)
from core_engine.reporting.historical_reporter import HistoricalReporter, fmt_int
from core_engine.logkit.factory import get_logger
from core_engine.logkit.formatters import operation_line
from core_engine.settings import (
    HISTORICAL,
    PIPELINE_LOG,
    TF_MINUTES,
    TF_STAGING,
    TRADINGVIEW,
    VERIFIED_MARKET_GAPS,
    get_historical_timeframes,
)


RESULT_ERROR = -1
RESULT_TV_EMPTY = -2
WAREHOUSE_MAINTENANCE_LOCK = "warehouse_maintenance"
WAREHOUSE_WRITE_LOCK_TTL_MIN = 15
WAREHOUSE_WRITE_LOCK_WAIT_SEC = 120.0
WAREHOUSE_WRITE_LOCK_POLL_SEC = 5.0

logger = get_logger(
    "historical_pulling",
    str(PIPELINE_LOG),
    rotating=True,
    utc=True,
    pipe_format=True,
    normalize_prefixes=True,
)


@dataclass
class ReplayRuntimeOptions:
    """Mutable replay settings, overridable at runtime by CLI --replay-* flags.

    Defaults come from HISTORICAL (config/dp_provider.env); engine.py's
    _set_replay_runtime mutates the single shared `replay_runtime`
    instance's attributes in place after parsing CLI args, and every
    function below reads through that same instance - so the override is
    visible regardless of which module reads it.
    """

    enabled: bool = HISTORICAL.replay_enabled
    tfs: set[str] = field(default_factory=lambda: set(HISTORICAL.replay_tfs))
    endpoint: str = HISTORICAL.replay_endpoint
    start_date: str = HISTORICAL.replay_start_date
    window_bars: int = HISTORICAL.replay_window_bars
    step_bars: int = HISTORICAL.replay_step_bars
    max_windows_per_pair: int = HISTORICAL.replay_max_windows_per_pair
    timeout_sec: float = HISTORICAL.replay_timeout_sec


replay_runtime = ReplayRuntimeOptions()

_reporter = HistoricalReporter(
    logger,
    replay_enabled=replay_runtime.enabled,
    replay_tfs=set(replay_runtime.tfs),
)

_TF_FILTER: set[str] = set()


def _hlog(event: str, *details: str, **fields: Any) -> str:
    return operation_line("HISTORICAL", event, *details, **fields)


_warehouse_write_lock_depth = 0

def _warehouse_lock_payload(owner: str) -> str:
    return (
        f"kind=warehouse_write;"
        f"owner={owner};"
        f"pid={os.getpid()};"
        f"started={datetime.now(timezone.utc).isoformat()}"
    )

class _WarehouseWriteSlot:
    """Short DB write window so live fetching is not blocked by long backfills."""

    def __init__(self, owner: str) -> None:
        self.owner = owner
        self.acquired = False

    def __enter__(self) -> "_WarehouseWriteSlot":
        global _warehouse_write_lock_depth
        raise_if_cancelled(logger, f"warehouse-write:{self.owner}:before-lock")
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
                # The historical lease heartbeat can fail while this thread
                # waits for the short warehouse lock. Recheck immediately
                # before any staging/Fact write begins.
                try:
                    raise_if_cancelled(logger, f"warehouse-write:{self.owner}:acquired")
                except BaseException:
                    release(WAREHOUSE_MAINTENANCE_LOCK)
                    self.acquired = False
                    _warehouse_write_lock_depth = 0
                    raise
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

def _refresh_mid_run(tv: SimpleNamespace) -> bool:
    ok = tv_auth.refresh_mid_run(tv, logger)
    if ok:
        logger.info("%s", _hlog("TradingView login refreshed", result="success"))
    else:
        logger.error("%s", _hlog("TradingView login refresh failed", result="failed"))
    return ok

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
    if not token or token == tv_auth.GUEST_TOKEN:
        refreshed_token, source = tv_auth.resolve_auth_token(logger)
        if refreshed_token and refreshed_token != tv_auth.GUEST_TOKEN:
            token = refreshed_token
            tv.token = refreshed_token
            tv_auth.set_current_token(refreshed_token)
            logger.info(
                "Historical auth promoted from limited access to %s.",
                tv_auth.safe_auth_source_label(source),
            )
    result = tv_history.fetch_history(
        symbol=sym["tv_symbol"],
        exchange=sym["tv_exchange"],
        tf_code=tf_code,
        n_bars=n_bars,
        logger=logger,
        timeout_sec=TRADINGVIEW.history_timeout_sec,
        token=token,
        endpoint=TRADINGVIEW.history_endpoint,
        # A targeted gap repair already calculated the exact number of bars
        # needed for its window. Applying the full-load request-more policy
        # here can turn a 15-bar repair into hundreds of thousands of bars
        # and can reach dates outside the warehouse calendar. Full/replay
        # loads keep the configured deep-history behaviour.
        request_more_rounds=(
            TRADINGVIEW.history_request_more_rounds if allow_replay else 0
        ),
        request_more_bars=TRADINGVIEW.history_request_more_bars,
    )
    frames: list[pd.DataFrame | None] = [result.df]
    notes = [f"history:{result.status}:{result.returned}"]

    replay_enabled = bool(allow_replay and replay_runtime.enabled and tf_code.upper() in replay_runtime.tfs)
    if replay_enabled:
        if result.df is not None and not result.df.empty:
            end_before = result.df.index.min().to_pydatetime()
        else:
            end_before = datetime.now(timezone.utc)
        replay = tv_history.crawl_replay_history(
            symbol=sym["tv_symbol"],
            exchange=sym["tv_exchange"],
            tf_code=tf_code,
            start_utc=replay_runtime.start_date,
            end_before_utc=end_before,
            endpoint=replay_runtime.endpoint,
            window_bars=replay_runtime.window_bars,
            step_bars=replay_runtime.step_bars,
            max_windows=replay_runtime.max_windows_per_pair,
            advance_factor=HISTORICAL.replay_advance_factor,
            timeout_sec=replay_runtime.timeout_sec,
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
        # Always call ETL, even when this exact pull re-staged rows that
        # already matched (staged == 0, MERGE affected nothing). A prior
        # run can have crashed after insert_staging_batch committed but
        # before run_etl_direct ran (or before it succeeded) - the staged
        # row is then permanently IsProcessed=1 with unchanged values, so
        # `staged` will read 0 on every future pull of the same bars and a
        # short-circuit here would mean run_etl_direct is never called
        # again for that stuck row. from_time scopes the SP's NOT EXISTS
        # scan to this pull's own window instead of the full history.
        from_time = clean_df.index.min().strftime("%Y-%m-%d %H:%M:%S") if not clean_df.empty else None
        inserted = run_etl_direct(
            sym["symbol_id"],
            tf_code,
            staging,
            source="historical_pulling",
            symbol=sym["tv_symbol"],
            from_time=from_time,
        )
    return int(inserted)


def pull_and_store(
    tv: SimpleNamespace,
    sym: dict[str, Any],
    tf_code: str,
    n_bars: int,
    *,
    skip_etl: bool = False,
    allow_replay: bool = True,
) -> int:
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
    except HistoricalPullCancelled:
        # Lease loss/operator cancellation is control flow, not a pair-level
        # retryable data error. Propagate it to engine.main so no later write
        # runs under a lock this process no longer owns.
        raise
    except Exception as exc:
        logger.exception("%s", _hlog("Pair write failed", symbol=sym["tv_symbol"], timeframe=tf_code, reason=exc, result="failed"))
        return RESULT_ERROR

def pull_with_retry(
    tv: SimpleNamespace,
    sym: dict[str, Any],
    tf_code: str,
    n_bars: int,
    *,
    max_retries: int = 3,
    allow_replay: bool = True,
) -> int:
    result = pull_and_store(tv, sym, tf_code, n_bars, allow_replay=allow_replay)
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
        result = pull_and_store(tv, sym, tf_code, n_bars, allow_replay=allow_replay)
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


def _selected_timeframes(tf_filter: set[str] | None = None) -> list[tuple[str, str, str, int]]:
    active = set(tf_filter or set())
    return [(i, tf, stg, n) for i, tf, stg, n in get_historical_timeframes() if not active or tf in active]


def run_full_load(tv: SimpleNamespace, *, symbols: list[dict[str, Any]], dry_run: bool = False) -> dict[str, int]:
    stats = {"ok": 0, "fail": 0, "inserted": 0}
    pairs_total = len(symbols)
    tfs = _selected_timeframes(_TF_FILTER)
    # stats["fail"] is the TRUE total across the whole run and is never
    # reset - it is what the caller (historical/engine.py) uses to decide
    # whether the run actually succeeded. consecutive_fail is a separate,
    # local counter that DOES reset after a successful mid-run auth
    # refresh - it only exists to decide when to trigger that refresh.
    # Before this split, both concerns shared stats["fail"], so a mid-run
    # refresh silently erased the run's true failure count and the final
    # exit code/summary could report "success" despite real pair failures.
    consecutive_fail = 0
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
            result = pull_with_retry(tv, sym, tf_code, n_bars, allow_replay=True)
            _reporter.pair_result(index, pairs_total, sym["tv_symbol"], tf_code, result)
            if result >= 0:
                stats["ok"] += 1
                stats["inserted"] += max(0, result)
                consecutive_fail = 0
            else:
                stats["fail"] += 1
                consecutive_fail += 1
                if consecutive_fail >= MAX_CONSECUTIVE_FAIL and _refresh_mid_run(tv):
                    consecutive_fail = 0
                elif consecutive_fail >= MAX_CONSECUTIVE_FAIL:
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

    # See run_full_load's comment on the same split: stats["fail"] is the
    # true cumulative total for reporting; consecutive_fail drives only
    # the mid-run-refresh trigger and resets on any success, so crossing
    # the threshold once does not keep re-triggering a refresh on every
    # single failure for the rest of the run.
    consecutive_fail = 0
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
            allow_replay=False,
        )
        _reporter.pair_result(index, len(stale), sym["tv_symbol"], tf_code, result)
        if result >= 0:
            stats["ok"] += 1
            stats["inserted"] += max(0, result)
            consecutive_fail = 0
            if result == 0:
                # "0 rows affected" alone is not proof this window is
                # actually covered - it could equally mean TradingView
                # legitimately had nothing there, or the pull genuinely
                # found nothing to write. Re-query Fact_OHLCV before
                # caching the window as verified-clean, so a future scan
                # is not permanently blinded to a gap that was never
                # really filled (see warehouse.reader.fact_covers_window).
                for gap_start, gap_end in item.get("gap_windows", []) or []:
                    try:
                        covered = fact_covers_window(
                            sym["symbol_id"],
                            tf_code,
                            gap_start,
                            gap_end,
                            max_gap_minutes=gap_threshold_minutes(sym, tf_code),
                        )
                    except Exception as exc:
                        logger.warning(
                            "%s",
                            _hlog(
                                "Gap re-verification failed - not caching as verified",
                                symbol=sym["tv_symbol"],
                                timeframe=tf_code,
                                reason=exc,
                                result="skipped",
                            ),
                        )
                        continue
                    if covered:
                        verified_windows.add((sym["symbol_id"], tf_code, gap_start, gap_end))
                    else:
                        logger.debug(
                            "%s",
                            _hlog(
                                "Gap window still not covered after repair - not caching as verified",
                                symbol=sym["tv_symbol"],
                                timeframe=tf_code,
                                result="unresolved",
                            ),
                        )
        else:
            stats["fail"] += 1
            consecutive_fail += 1
            if consecutive_fail >= MAX_CONSECUTIVE_FAIL:
                if _refresh_mid_run(tv):
                    consecutive_fail = 0
                else:
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
