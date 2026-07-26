"""Gap/staleness detection algorithm: which symbol/timeframe pairs need a
historical backfill, and why.

Split out of the old runtime_support.py: this half owns the actual decision
logic (adjacent-bar staleness, internal timeline holes, recurring-closure and
weekend-session exclusion, the verified-gap cache). CLI parsing, run scope,
and cancellation plumbing live in run_control.py instead.
"""

from __future__ import annotations

import json
import logging
import math
from collections import Counter
from datetime import datetime, timedelta

from core_engine.shared.freshness import stale_after_minutes
from core_engine.shared.warehouse.reader import get_internal_gaps
from core_engine.shared.warehouse.validation import utc_naive_now
from core_engine.core.historical.reporter import historical_scan_summary_block, log_historical_block
from core_engine.util.logkit import operation_line
from core_engine.settings import (
    DEFAULT_N_BARS,
    DIRECT_TFS,
    HISTORICAL,
    OVERNIGHT_GAP_MINUTES,
    SYMBOLS,
    SYMBOL_OVERNIGHT_MINS,
    TF_MINUTES,
    VERIFIED_MARKET_GAPS,
    WEEKEND_CLOSED,
)

HOLE_LOOKBACK_DAYS = HISTORICAL.hole_lookback_days
VERIFIED_GAP_CACHE_VERSION = 3
VERIFIED_GAP_CACHE_TTL_HOURS = 24


def now_utc() -> datetime:
    return utc_naive_now()


def fmt_gap(hours: float) -> str:
    if hours < 1:
        return f"{hours * 60:.0f}m"
    if hours < 48:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def trading_hours_in_gap(start: datetime, end: datetime) -> float:
    total_hours = max(0.0, (end - start).total_seconds() / 3600)
    full_weeks = int(total_hours // 168)
    weekend_h = full_weeks * 48.0

    t = start + timedelta(weeks=full_weeks)
    walked = 0.0
    remaining = total_hours - full_weeks * 168
    while walked < remaining and t < end:
        if t.weekday() >= 5:
            weekend_h += 1.0
        t += timedelta(hours=1)
        walked += 1.0
    return max(0.0, total_hours - weekend_h)


def _gap_schedule_signature(start: datetime, end: datetime, gap_minutes: int) -> tuple[int, int, int, int, int, int, int]:
    """Return the weekly wall-clock signature of one adjacent-bar gap.

    Capital.com instruments have provider-specific daily and weekly closures.
    A simple ``weekday >= 5`` calculation cannot describe those sessions: for
    example, many Friday-close/Sunday-open gaps include several Friday evening
    hours and were consequently reported as market-open holes.  Exact recurring
    signatures let the warehouse data itself prove a scheduled closure without
    teaching this service a brittle, manually maintained exchange calendar.

    The duration is deliberately part of the signature.  If even one bar is
    missing immediately before or after a normal closure, the duration changes
    and that anomalous window remains eligible for repair.
    """

    return (
        start.weekday(),
        start.hour,
        start.minute,
        end.weekday(),
        end.hour,
        end.minute,
        int(gap_minutes),
    )


def recurring_market_closure_signatures(
    gaps: list[tuple[datetime, datetime, int]],
    *,
    minimum_occurrences: int = 2,
) -> set[tuple[int, int, int, int, int, int, int]]:
    """Identify exact gap shapes that recur on the weekly market schedule.

    Two occurrences are required so a one-off provider outage or a genuine
    missing-data window is never suppressed merely because it resembles a
    closure.  DST changes naturally create a second signature and each shape
    must independently recur before it is trusted.
    """

    if minimum_occurrences < 2:
        raise ValueError("minimum_occurrences must be at least 2")
    counts = Counter(_gap_schedule_signature(start, end, minutes) for start, end, minutes in gaps)
    return {signature for signature, count in counts.items() if count >= minimum_occurrences}


def is_expected_weekend_session_closure(
    gap_start: datetime,
    gap_end: datetime,
    *,
    tf_minutes: int,
    asset_type: str,
) -> bool:
    """Recognise only the unambiguous Friday-evening FX weekend boundary.

    ``gap_start`` is the timestamp of the last existing candle, so the
    first actually-missing instant is one timeframe later.  Capital.com's
    weekday FX feed closes on Friday evening and resumes Sunday evening,
    but the exact first/last bar varies slightly by pair and DST.  Treating
    only this narrow Friday-evening -> Sunday-evening shape as a closure
    removes false GO-gate gaps without hiding a weekday/provider outage.
    """

    if asset_type != "FOREX" or tf_minutes <= 0:
        return False
    first_missing = gap_start + timedelta(minutes=tf_minutes)
    if first_missing.weekday() != 4 or gap_end.weekday() != 6:
        return False
    first_missing_minute = first_missing.hour * 60 + first_missing.minute
    reopen_minute = gap_end.hour * 60 + gap_end.minute
    return (
        first_missing_minute >= 18 * 60
        and 20 * 60 <= reopen_minute <= 23 * 60 + 59
        and timedelta(hours=36) <= gap_end - gap_start <= timedelta(hours=60)
    )


def gap_threshold_minutes(sym: dict, tf_code: str) -> int:
    """Raw adjacent-bar threshold shared by discovery and re-verification."""
    tf_mins = int(TF_MINUTES[tf_code])
    asset_type = sym["asset_type"]
    tv_sym = sym["tv_symbol"]
    overnight = int(SYMBOL_OVERNIGHT_MINS.get(tv_sym, OVERNIGHT_GAP_MINUTES.get(asset_type, 0)))
    return stale_after_minutes(tf_mins, overnight)


def calc_gap_n_bars(gap_hours: float, tf_code: str, asset_type: str) -> int:
    tf_mins = TF_MINUTES[tf_code]
    trading_ratio = 5 / 7 if asset_type in WEEKEND_CLOSED else 1.0
    bars_needed = (gap_hours * 60 / tf_mins) * trading_ratio
    n = max(HISTORICAL.min_pull_bars, math.ceil(bars_needed * HISTORICAL.safety_factor))
    return min(n, DEFAULT_N_BARS.get(tf_code, 10000))


def find_stale_pairs(latest: dict, *, tf_filter: set[str] | None = None, symbols: list[dict] | None = None) -> list[dict]:
    now = now_utc()
    stale: list[dict] = []
    symbol_list = symbols if symbols is not None else SYMBOLS
    active_tf_filter = set(tf_filter or set())

    for sym in symbol_list:
        sid = sym["symbol_id"]
        asset_type = sym["asset_type"]
        selected_tfs = [tf for tf in DIRECT_TFS if not active_tf_filter or tf in active_tf_filter]
        for tf_code in selected_tfs:
            tf_mins = TF_MINUTES[tf_code]
            last_bar = latest.get((sid, tf_code))
            if last_bar is None:
                stale.append({"sym": sym, "tf_code": tf_code, "last_bar": None, "gap_hours": DEFAULT_N_BARS[tf_code] * tf_mins / 60, "n_bars": DEFAULT_N_BARS[tf_code], "reason": "MISS"})
                continue
            if getattr(last_bar, "tzinfo", None):
                last_bar = last_bar.replace(tzinfo=None)
            gap_min = (now - last_bar).total_seconds() / 60
            gap_hours = gap_min / 60
            if gap_min <= gap_threshold_minutes(sym, tf_code):
                continue
            if asset_type in WEEKEND_CLOSED:
                trading_h = trading_hours_in_gap(last_bar, now)
                threshold_h = (5 * 24.0 if tf_code == "W" else tf_mins / 60.0) * 2
                if trading_h <= threshold_h:
                    continue
            stale.append({"sym": sym, "tf_code": tf_code, "last_bar": last_bar, "gap_hours": round(gap_hours, 1), "n_bars": calc_gap_n_bars(gap_hours, tf_code, asset_type), "reason": "STALE"})
    stale.sort(key=lambda item: (item["reason"] != "MISS", -item["gap_hours"]))
    return stale


def flatten_verified_gap_windows(verified_gaps: dict) -> set:
    windows = set()
    for (symbol_id, tf_code), ranges in (verified_gaps or {}).items():
        for gap_start, gap_end in ranges:
            windows.add((symbol_id, tf_code, gap_start, gap_end))
    return windows


def load_verified_gaps() -> dict:
    try:
        data = json.loads(VERIFIED_MARKET_GAPS.read_text(encoding="utf-8"))
        # Versions before v3 did not require a TradingView response to
        # contain both boundary bars before an unresolved window could be
        # classified as unavailable at the source. They cannot safely
        # suppress current repair scans.
        if data.get("verification_version") != VERIFIED_GAP_CACHE_VERSION:
            return {}
        saved = datetime.fromisoformat(data["verified_at"])
        if (now_utc() - saved).total_seconds() > VERIFIED_GAP_CACHE_TTL_HOURS * 3600:
            return {}
        if "windows" not in data:
            return {}
        result: dict = {}
        for entry in data["windows"]:
            key = (entry[0], entry[1])
            window = (datetime.fromisoformat(entry[2]), datetime.fromisoformat(entry[3]))
            result.setdefault(key, []).append(window)
        return result
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        return {}


def save_verified_gaps(windows: set, logger: logging.Logger) -> None:
    data = {
        "verification_version": VERIFIED_GAP_CACHE_VERSION,
        "verified_at": now_utc().isoformat(),
        "windows": sorted([[sid, tfc, gs.isoformat(), ge.isoformat()] for sid, tfc, gs, ge in windows]),
    }
    try:
        VERIFIED_MARKET_GAPS.parent.mkdir(parents=True, exist_ok=True)
        VERIFIED_MARKET_GAPS.write_text(json.dumps(data, indent=2), encoding="utf-8")
        unique_pairs = {(s, t) for s, t, _, _ in windows}
        logger.info(
            "%s",
            operation_line(
                "HISTORICAL",
                "Verified market gaps saved",
                windows=len(windows),
                pairs=len(unique_pairs),
                result="saved",
            ),
        )
    except OSError as exc:
        logger.warning("%s", operation_line("HISTORICAL", "Verified market gaps save failed", reason=exc, result="warning"))


def find_hole_pairs(
    stale: list,
    logger: logging.Logger,
    verified_gaps: dict | None = None,
    lookback_days: int = HOLE_LOOKBACK_DAYS,
    symbols: list[dict] | None = None,
    tf_filter: set[str] | None = None,
) -> list:
    tf_codes = [tf for tf in DIRECT_TFS if not tf_filter or tf in tf_filter]
    try:
        raw = get_internal_gaps(tf_codes, lookback_days=lookback_days)
    except Exception as exc:
        # A scan failure is NOT the same as "scanned and found nothing" -
        # logging it as "clean" or returning no work would let a transient
        # SQL error mask real gaps while the job reports success. Log the
        # context, then fail the historical run so the supervisor's retry
        # state machine and operator alerting can take over.
        logger.error(
            "%s",
            operation_line(
                "HISTORICAL",
                "Internal gap scan failed",
                lookback_days=lookback_days,
                reason=exc,
                result="failed_run",
            ),
        )
        raise
    if not raw:
        log_historical_block(
            logger,
            logging.INFO,
            historical_scan_summary_block(raw_gap_windows=0, result="clean"),
        )
        logger.info(
            "%s",
            operation_line(
                "HISTORICAL",
                "scan_summary",
                raw_gap_windows=0,
                upgraded_pairs=0,
                new_pairs=0,
                gap_repair_pairs=0,
                result="clean",
            ),
        )
        return []

    now = now_utc()
    stale_index = {(x["sym"]["symbol_id"], x["tf_code"]): i for i, x in enumerate(stale)}
    sym_map = {s["symbol_id"]: s for s in (symbols or SYMBOLS)}
    new_holes = []
    n_raw = sum(len(v) for v in raw.values())
    n_excluded = 0
    n_upgraded = 0
    n_new = 0
    n_skip_verified = 0

    for (sym_id, tf_code), gaps in raw.items():
        tf_mins = TF_MINUTES.get(tf_code)
        sym = sym_map.get(sym_id)
        if tf_mins is None or sym is None:
            continue
        verified_windows = (verified_gaps or {}).get((sym_id, tf_code), [])
        asset_type = sym["asset_type"]
        threshold = gap_threshold_minutes(sym, tf_code)
        recurring_closures = recurring_market_closure_signatures(gaps)

        real_gaps = []
        for gap_start, gap_end, gap_raw_min in gaps:
            if any(vs <= gap_start and gap_end <= ve for vs, ve in verified_windows):
                n_skip_verified += 1
                continue
            if _gap_schedule_signature(gap_start, gap_end, gap_raw_min) in recurring_closures:
                n_excluded += 1
                continue
            if is_expected_weekend_session_closure(
                gap_start,
                gap_end,
                tf_minutes=tf_mins,
                asset_type=asset_type,
            ):
                n_excluded += 1
                continue
            trading_min = trading_hours_in_gap(gap_start, gap_end) * 60 if asset_type in WEEKEND_CLOSED else float(gap_raw_min)
            if trading_min > threshold:
                real_gaps.append((gap_start, gap_end, trading_min))
            else:
                n_excluded += 1

        if not real_gaps:
            continue
        earliest_start = min(g[0] for g in real_gaps)
        hole_hours = (now - earliest_start).total_seconds() / 3600
        n_bars_needed = calc_gap_n_bars(hole_hours, tf_code, asset_type)
        key = (sym_id, tf_code)
        if key in stale_index:
            idx = stale_index[key]
            if stale[idx]["n_bars"] < n_bars_needed:
                stale[idx]["n_bars"] = n_bars_needed
            if "HOLE" not in str(stale[idx].get("reason", "")):
                stale[idx]["reason"] += "+HOLE"
            stale[idx].setdefault("gap_windows", []).extend((gs, ge) for gs, ge, _ in real_gaps)
            n_upgraded += 1
        else:
            new_holes.append({
                "sym": sym,
                "tf_code": tf_code,
                "last_bar": earliest_start,
                "gap_hours": round(hole_hours, 1),
                "n_bars": n_bars_needed,
                "reason": "HOLE",
                "gap_windows": [(gs, ge) for gs, ge, _ in real_gaps],
            })
            n_new += 1

    scan_result = "queued" if n_new or n_upgraded else "clean"
    log_historical_block(
        logger,
        logging.INFO,
        historical_scan_summary_block(
            raw_gap_windows=n_raw,
            non_trading_windows=n_excluded,
            verified_skipped=n_skip_verified,
            upgraded_pairs=n_upgraded,
            new_pairs=n_new,
            result=scan_result,
        ),
    )
    logger.info(
        "%s",
        operation_line(
            "HISTORICAL",
            "scan_summary",
            raw_gap_windows=n_raw,
            non_trading_windows=n_excluded,
            verified_skipped=n_skip_verified,
            upgraded_pairs=n_upgraded,
            new_pairs=n_new,
            gap_repair_pairs=n_upgraded + n_new,
            result=scan_result,
        ),
    )
    return new_holes
