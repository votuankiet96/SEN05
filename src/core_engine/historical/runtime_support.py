"""Runtime helpers for the historical OHLCV pull engine."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from core_engine.tradingview import history_client as tv_history
from core_engine.warehouse.repository import get_internal_gaps
from core_engine.reporting.historical_reporter import historical_scan_summary_block, log_historical_block
from core_engine.logkit.formatters import operation_line
from core_engine.settings import (
    DEFAULT_N_BARS,
    DIRECT_TFS,
    HISTORICAL,
    HISTORICAL_CANCEL_FILE,
    OVERNIGHT_GAP_MINUTES,
    SYMBOLS,
    SYMBOL_OVERNIGHT_MINS,
    TF_MINUTES,
    VERIFIED_MARKET_GAPS,
    WEEKEND_CLOSED,
)

EXIT_TV_UNAVAILABLE = 3
MAX_CONSECUTIVE_FAIL = HISTORICAL.max_consecutive_fail
HOLE_LOOKBACK_DAYS = HISTORICAL.hole_lookback_days
PREFLIGHT_PROBE_BARS = 5
PREFLIGHT_TIMEOUT_SEC = 30
PREFLIGHT_SYMBOL_LIMIT = 4
PREFLIGHT_RETRIES = 1
PREFLIGHT_PREFERRED_SYMBOLS = ("US500", "EURUSD", "BTCUSD", "FR40")


class HistoricalPullCancelled(Exception):
    """Raised when the operator requested a cooperative historical stop."""


def utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def now_utc() -> datetime:
    return utc_naive_now()


def cancel_file_path() -> str:
    configured = os.environ.get("DP_HISTORICAL_CANCEL_FILE", "").strip()
    return configured or str(HISTORICAL_CANCEL_FILE)


def cancel_requested() -> bool:
    path = cancel_file_path()
    return bool(path and os.path.exists(path))


def raise_if_cancelled(logger: logging.Logger, where: str = "") -> None:
    if not cancel_requested():
        return
    suffix = f" ({where})" if where else ""
    logger.warning("[CANCEL] Historical pull cancellation requested%s; stopping at safe checkpoint.", suffix)
    raise HistoricalPullCancelled("historical pull cancelled by operator")


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


def calc_gap_n_bars(gap_hours: float, tf_code: str, asset_type: str) -> int:
    tf_mins = TF_MINUTES[tf_code]
    trading_ratio = 5 / 7 if asset_type in WEEKEND_CLOSED else 1.0
    bars_needed = (gap_hours * 60 / tf_mins) * trading_ratio
    n = max(HISTORICAL.min_pull_bars, math.ceil(bars_needed * HISTORICAL.safety_factor))
    return min(n, DEFAULT_N_BARS.get(tf_code, 10000))


def sleep_for(tv_symbol: str) -> None:
    import time

    delay = 10.0 if str(tv_symbol).upper() == "GOLD" else 5.0
    time.sleep(delay)


def _csv_set(value: str | None) -> set[str]:
    if value is None:
        return set()
    if value.strip().lower() in {"", "-", "none", "off", "no"}:
        return set()
    return {item.strip().upper() for item in value.split(",") if item.strip()}


def apply_replay_cli_options(
    args: Any,
    *,
    valid_tfs: set[str],
    set_runtime: Callable[[str, Any], None],
) -> list[str]:
    changes: list[str] = []
    if args.replay != "config":
        enabled = args.replay == "on"
        set_runtime("TV_WS_REPLAY_ENABLED", enabled)
        changes.append(f"enabled={'yes' if enabled else 'no'}")

    if args.replay_tfs is not None:
        replay_tfs = _csv_set(args.replay_tfs)
        invalid = sorted(replay_tfs - valid_tfs)
        if invalid:
            raise ValueError("Invalid --replay-tfs value(s): " + ",".join(invalid))
        set_runtime("TV_WS_REPLAY_TFS", replay_tfs)
        changes.append("tfs=" + (",".join(sorted(replay_tfs)) if replay_tfs else "-"))

    for arg_name, runtime_name, label in (
        ("replay_endpoint", "TV_WS_REPLAY_ENDPOINT", "endpoint"),
        ("replay_start_date", "TV_WS_REPLAY_START_DATE", "start"),
    ):
        value = getattr(args, arg_name, None)
        if value:
            set_runtime(runtime_name, str(value).lower() if "endpoint" in arg_name else value)
            changes.append(f"{label}={value}")

    for arg_name, runtime_name, label in (
        ("replay_max_windows", "TV_WS_REPLAY_MAX_WINDOWS_PER_PAIR", "max_windows"),
        ("replay_window_bars", "TV_WS_REPLAY_WINDOW_BARS", "window_bars"),
        ("replay_step_bars", "TV_WS_REPLAY_STEP_BARS", "step_bars"),
        ("replay_timeout_sec", "TV_WS_REPLAY_TIMEOUT_SEC", "timeout_sec"),
    ):
        value = getattr(args, arg_name, None)
        if value is not None:
            if value <= 0:
                raise ValueError(f"--{arg_name.replace('_', '-')} must be greater than 0")
            set_runtime(runtime_name, value)
            changes.append(f"{label}={value:g}" if isinstance(value, float) else f"{label}={value}")
    return changes


def build_parser(default_hole_lookback_days: int) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEN05 historical OHLCV pull engine")
    parser.add_argument("--mode", choices=["auto", "full", "gap", "reset"], default="auto")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--symbols", type=str, default=None)
    parser.add_argument("--reset", action="store_true", default=False)
    parser.add_argument("--yes", action="store_true", default=False)
    parser.add_argument("--timeframes", type=str, default=None)
    parser.add_argument("--asset-type", type=str, default=None)
    parser.add_argument("--force-unlock", action="store_true", default=False)
    parser.add_argument("--on-conflict", choices=["skip", "wait", "replace"], default="skip")
    parser.add_argument("--replay", choices=["config", "on", "off"], default="config")
    parser.add_argument("--replay-tfs", type=str, default=None)
    parser.add_argument("--replay-endpoint", type=str, default=None)
    parser.add_argument("--replay-start-date", type=str, default=None)
    parser.add_argument("--replay-max-windows", type=int, default=None)
    parser.add_argument("--replay-window-bars", type=int, default=None)
    parser.add_argument("--replay-step-bars", type=int, default=None)
    parser.add_argument("--replay-timeout-sec", type=float, default=None)
    parser.add_argument("--hole-lookback-days", type=int, default=default_hole_lookback_days)
    return parser


@dataclass(frozen=True)
class ScopeEvent:
    action: str
    amount: str = ""
    status: str = ""


@dataclass(frozen=True)
class ScopeResult:
    symbols: list[dict[str, Any]]
    timeframe_filter: set[str]
    events: list[ScopeEvent] = field(default_factory=list)
    error_action: str | None = None
    error_amount: str = ""
    error_status: str = ""

    @property
    def ok(self) -> bool:
        return self.error_action is None


def _csv_upper(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip().upper() for item in value.split(",") if item.strip()}


def _csv_exact(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


def has_any_filter(*, symbols_csv: str | None, asset_type_csv: str | None, timeframes_csv: str | None) -> bool:
    return bool(symbols_csv or asset_type_csv or timeframes_csv)


def resolve_scope(
    symbols: list[dict[str, Any]],
    *,
    asset_type_csv: str | None = None,
    symbols_csv: str | None = None,
    timeframes_csv: str | None = None,
) -> ScopeResult:
    selected = list(symbols)
    events: list[ScopeEvent] = []

    if asset_type_csv:
        asset_filter = _csv_exact(asset_type_csv)
        selected = [s for s in selected if s.get("asset_type") in asset_filter]
        if not selected:
            return ScopeResult(selected, set(), error_action="asset_filter", error_amount=asset_type_csv, error_status="no_matching_symbols")
        events.append(ScopeEvent("asset_type", asset_type_csv, f"symbols {len(selected):,}"))

    if symbols_csv:
        symbol_filter = _csv_upper(symbols_csv)
        selected = [s for s in selected if str(s.get("tv_symbol", "")).upper() in symbol_filter]
        if not selected:
            return ScopeResult(selected, set(), error_action="symbol_filter", error_amount=symbols_csv, error_status="no_matching_symbols")
        events.append(ScopeEvent("symbols", f"count {len(selected):,}", ",".join(str(s.get("tv_symbol", "")) for s in selected)))

    timeframe_filter = _csv_upper(timeframes_csv)
    if timeframe_filter:
        invalid_tfs = sorted(timeframe_filter - set(DIRECT_TFS))
        if invalid_tfs:
            return ScopeResult(
                selected,
                set(),
                error_action="timeframe_filter",
                error_amount=",".join(invalid_tfs),
                error_status="invalid_timeframe",
            )
        events.append(ScopeEvent("timeframes", status=",".join(sorted(timeframe_filter))))
    return ScopeResult(selected, timeframe_filter, events)


def symbol_candidates(symbols: list[dict]) -> list[dict]:
    if not symbols:
        return []
    by_symbol = {str(sym.get("tv_symbol", "")).strip().upper(): sym for sym in symbols if str(sym.get("tv_symbol", "")).strip()}
    chosen: list[dict] = []
    seen: set[str] = set()

    def add(sym: dict | None) -> None:
        if not sym:
            return
        key = str(sym.get("tv_symbol", "")).strip().upper()
        if key and key not in seen:
            chosen.append(sym)
            seen.add(key)

    for preferred in PREFLIGHT_PREFERRED_SYMBOLS:
        add(by_symbol.get(preferred))
    for sym in symbols:
        add(sym)
        if len(chosen) >= PREFLIGHT_SYMBOL_LIMIT:
            break
    return chosen[:PREFLIGHT_SYMBOL_LIMIT]


def tv_probe(logger: logging.Logger, *, symbols: list[dict], direct_tfs: set[str]) -> tuple[bool, str]:
    candidates = symbol_candidates(symbols)
    if not candidates:
        return True, "no symbols configured"
    probe_tf = "H1" if "H1" in direct_tfs else next(iter(direct_tfs), "H1")
    attempts: list[str] = []
    for sym in candidates:
        for attempt in range(1, PREFLIGHT_RETRIES + 1):
            try:
                res = tv_history.fetch_history(
                    symbol=sym["tv_symbol"],
                    exchange=sym["tv_exchange"],
                    tf_code=probe_tf,
                    n_bars=PREFLIGHT_PROBE_BARS,
                    logger=logger,
                    timeout_sec=float(PREFLIGHT_TIMEOUT_SEC),
                )
            except Exception as exc:
                attempts.append(f"{sym['tv_symbol']} {probe_tf}: probe error={exc} attempt={attempt}/{PREFLIGHT_RETRIES}")
                continue
            status = str(getattr(res, "status", "") or "unknown")
            returned = int(getattr(res, "returned", 0) or 0)
            endpoint = str(getattr(res, "endpoint", "") or "-")
            error = str(getattr(res, "error", "") or "").strip()
            detail = f"{sym['tv_symbol']} {probe_tf}: status={status}, returned={returned}, endpoint={endpoint}"
            if error:
                detail += f", error={error[:180]}"
            if returned > 0:
                return True, detail
            attempts.append(detail)
    return False, "; ".join(attempts[: PREFLIGHT_SYMBOL_LIMIT * PREFLIGHT_RETRIES]) or "no TradingView probe returned bars"


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
            if asset_type in WEEKEND_CLOSED:
                trading_h = trading_hours_in_gap(last_bar, now)
                threshold_h = (5 * 24.0 if tf_code == "W" else tf_mins / 60.0) * 2
                if trading_h <= threshold_h:
                    continue
            elif gap_min <= tf_mins * 2:
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
        saved = datetime.fromisoformat(data["verified_at"])
        if (now_utc() - saved).days > 30:
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
    raw = get_internal_gaps(tf_codes, lookback_days=lookback_days)
    if not raw:
        log_historical_block(
            logger,
            logging.INFO,
            historical_scan_summary_block(raw_gap_windows=0, result="clean"),
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
        threshold = tf_mins * 3
        tv_sym = sym["tv_symbol"]
        overnight = SYMBOL_OVERNIGHT_MINS.get(tv_sym, OVERNIGHT_GAP_MINUTES.get(asset_type, 0))
        if overnight > 0:
            threshold = max(threshold, overnight + tf_mins)

        real_gaps = []
        for gap_start, gap_end, gap_raw_min in gaps:
            if any(vs <= gap_start and gap_end <= ve for vs, ve in verified_windows):
                n_skip_verified += 1
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

    log_historical_block(
        logger,
        logging.INFO,
        historical_scan_summary_block(
            raw_gap_windows=n_raw,
            non_trading_windows=n_excluded,
            verified_skipped=n_skip_verified,
            upgraded_pairs=n_upgraded,
            new_pairs=n_new,
            result="queued" if n_new or n_upgraded else "clean",
        ),
    )
    return new_holes
