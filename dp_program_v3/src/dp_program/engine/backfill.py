"""Historical 60-day bootstrap, rolling planning, and bounded symbol batches."""
from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from .sql_connector import Pair, pair_key, select_pairs
from .auth import AuthError
from .pipeline import fetch_and_store, log_pair_failure, utc
from .sql_connector import get_connection, get_pair_states
from .websocket import FetchRequest, fetch_candles_batch, request_key

_SOFT_BATCH_BARS = 15_000
_MAX_CONSECUTIVE_GROUP_FAILURES = 2
_MAX_TOTAL_GROUP_FAILURES = 3


class CatchupWindowError(RuntimeError):
    """Raised rather than silently skipping the oldest part of a gap."""


@dataclass(frozen=True)
class BackfillPlan:
    """One historical series with adaptive and absolute request bounds."""

    bars: int
    max_bars: int
    window_start: datetime | None
    window_end: datetime
    complete_bootstrap: bool
    require_coverage: bool
    required_cursor: datetime | None


def _bounded(config: dict[str, Any], bars: int, context: str) -> int:
    maximum = int(config["backfill"]["max_bars_per_request"])
    if bars > maximum:
        raise CatchupWindowError(
            f"{context} requires {bars} bars and exceeds request cap {maximum}"
        )
    return max(1, bars)


def _bootstrap_complete(config: dict[str, Any], state: dict[str, Any], current: datetime) -> bool:
    """Complete once Fact_OHLCV's earliest bar reaches the lookback window (require_coverage already proved it)."""
    earliest = state.get("earliest")
    threshold = current - timedelta(days=int(config["backfill"]["lookback_days"]))
    return earliest is not None and utc(earliest) <= threshold


def plan_backfill(
    config: dict[str, Any],
    symbol: dict[str, Any],
    timeframe: dict[str, Any],
    state: dict[str, Any],
    *,
    now: datetime | None = None,
    bars_override: int | None = None,
) -> BackfillPlan:
    """Plan exact coverage while avoiding calendar-sized initial over-fetch."""
    current = utc(now or datetime.now(timezone.utc))
    maximum = int(config["backfill"]["max_bars_per_request"])
    if bars_override is not None:
        if bars_override <= 0 or bars_override > maximum:
            raise CatchupWindowError(
                f"manual request {bars_override} exceeds valid range 1..{maximum}"
            )
        return BackfillPlan(
            int(bars_override), int(bars_override), None, current,
            False, False, None,
        )
    complete = _bootstrap_complete(config, state, current)
    latest = state.get("latest")
    durable_latest = None if latest is None else utc(latest)
    if durable_latest is not None and durable_latest > current:
        raise CatchupWindowError("Fact watermark is ahead of the current UTC time")
    minutes = int(timeframe["minutes"])
    if not complete or durable_latest is None:
        start = current - timedelta(days=int(config["backfill"]["lookback_days"]))
        calendar_bars = math.ceil(
            (current - start).total_seconds() / (minutes * 60)
        ) + int(config["backfill"]["overlap_bars"])
        safe_max = _bounded(config, calendar_bars, "bootstrap")
        initial = safe_max if symbol.get("asset_type") == "Crypto" else math.ceil(
            safe_max * 0.75
        )
        return BackfillPlan(
            max(1, initial), safe_max, start, current, True, True, durable_latest
        )
    overlap = int(config["backfill"]["overlap_bars"])
    start = durable_latest - timedelta(minutes=max(0, overlap - 1) * minutes)
    bars = _bounded(
        config,
        math.ceil((current - start).total_seconds() / (minutes * 60)) + 1,
        "rolling catch-up",
    )
    return BackfillPlan(
        bars, bars, start, current, False, True, durable_latest
    )


def _next_group(
    config: dict[str, Any],
    pairs: list[Pair],
    states: dict[tuple[int, str], dict[str, Any]],
    current: datetime,
    bars_override: int | None,
) -> list[Pair]:
    if not pairs:
        return []
    symbol_id = int(pairs[0][0]["symbol_id"])
    hard = int(config["backfill"]["max_bars_per_request"])
    group: list[Pair] = []
    initial_total = maximum_total = 0
    for symbol, timeframe in pairs:
        if int(symbol["symbol_id"]) != symbol_id:
            break
        try:
            plan = plan_backfill(
                config, symbol, timeframe,
                states[(symbol_id, timeframe["code"])],
                now=current, bars_override=bars_override,
            )
        except Exception:
            return group or [(symbol, timeframe)]
        exceeds = group and (
            initial_total + plan.bars > _SOFT_BATCH_BARS
            or maximum_total + plan.max_bars > hard
        )
        if exceeds:
            break
        group.append((symbol, timeframe))
        initial_total += plan.bars
        maximum_total += plan.max_bars
    return group


def next_backfill_group(
    config: dict[str, Any],
    pairs: list[Pair],
    *,
    now: datetime | None = None,
    bars_override: int | None = None,
) -> list[Pair]:
    """Select the first same-symbol group safe for one finite socket."""
    current = utc(now or datetime.now(timezone.utc))
    states = get_pair_states(config, pairs)
    return _next_group(config, pairs, states, current, bars_override)


def _empty_summary(pairs: int) -> dict[str, Any]:
    return {
        "pairs": pairs, "ok": 0, "failed": 0, "affected": 0,
        "completed_bootstraps": 0, "failed_pairs": [],
        "deferred": 0, "deferred_pairs": [], "group_failures": 0,
    }


def _run_group(
    config: dict[str, Any],
    group: list[Pair],
    states: dict[tuple[int, str], dict[str, Any]],
    current: datetime,
    bars_override: int | None,
) -> dict[str, Any]:
    summary = _empty_summary(len(group))
    planned: list[tuple[Pair, BackfillPlan, FetchRequest]] = []
    for symbol, timeframe in group:
        try:
            plan = plan_backfill(
                config, symbol, timeframe,
                states[(int(symbol["symbol_id"]), timeframe["code"])],
                now=current, bars_override=bars_override,
            )
            planned.append(((symbol, timeframe), plan, FetchRequest(
                symbol, timeframe, plan.bars, plan.max_bars,
                plan.window_start if plan.require_coverage else None,
            )))
        except Exception as exc:
            summary["failed"] += 1
            summary["failed_pairs"].append(pair_key((symbol, timeframe)))
            log_pair_failure("backfill", symbol, timeframe, exc)
    if not planned:
        summary["group_failures"] = int(bool(summary["failed"]))
        return summary
    try:
        fetched = fetch_candles_batch(
            config, [request for _pair, _plan, request in planned]
        )
    except AuthError:
        raise
    except Exception as exc:
        for (symbol, timeframe), _plan, _request in planned:
            summary["failed"] += 1
            summary["failed_pairs"].append(pair_key((symbol, timeframe)))
            log_pair_failure("backfill", symbol, timeframe, exc, stage="fetch")
        summary["group_failures"] = 1
        return summary
    try:
        connection = get_connection(config)
    except Exception as exc:
        for (symbol, timeframe), _plan, _request in planned:
            summary["failed"] += 1
            summary["failed_pairs"].append(pair_key((symbol, timeframe)))
            log_pair_failure("backfill", symbol, timeframe, exc, stage="sql_compare")
        summary["group_failures"] = 1
        return summary
    try:
        for (symbol, timeframe), plan, request in planned:
            try:
                provider = fetched[request_key(request)]
                result = fetch_and_store(
                    config, symbol, timeframe, workflow="backfill",
                    bars=provider.requested_bars,
                    window_start=plan.window_start, window_end=plan.window_end,
                    require_coverage=plan.require_coverage,
                    required_cursor=plan.required_cursor,
                    provider_candles=provider.candles, now=current,
                    connection=connection,
                )
                summary["ok"] += 1
                summary["affected"] += int(result["affected"])
                summary["completed_bootstraps"] += int(plan.complete_bootstrap)
            except AuthError:
                raise
            except Exception as exc:
                summary["failed"] += 1
                summary["failed_pairs"].append(pair_key((symbol, timeframe)))
                log_pair_failure("backfill", symbol, timeframe, exc)
    finally:
        connection.close()
    summary["group_failures"] = int(bool(summary["failed"]))
    return summary


def run_backfill_pairs(
    config: dict[str, Any],
    pairs: list[Pair],
    *,
    bars_override: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run bounded groups; every durable delivery remains pair-serial."""
    current = utc(now or datetime.now(timezone.utc))
    states = get_pair_states(config, pairs)
    summary = _empty_summary(len(pairs))
    remaining = list(pairs)
    consecutive = total_failures = 0
    while remaining:
        group = _next_group(
            config, remaining, states, current, bars_override
        )
        part = _run_group(
            config, group, states, current, bars_override
        )
        remaining = remaining[len(group):]
        for key in (
            "ok", "failed", "affected", "completed_bootstraps", "group_failures"
        ):
            summary[key] += int(part[key])
        summary["failed_pairs"].extend(part["failed_pairs"])
        if part["group_failures"]:
            consecutive += 1
            total_failures += 1
        else:
            consecutive = 0
        if (
            consecutive >= _MAX_CONSECUTIVE_GROUP_FAILURES
            or total_failures >= _MAX_TOTAL_GROUP_FAILURES
        ):
            summary["deferred_pairs"] = [pair_key(pair) for pair in remaining]
            summary["deferred"] = len(remaining)
            break
    return summary


def prioritize_backfill_pairs(
    config: dict[str, Any], pairs: list[Pair], *, now: datetime | None = None
) -> list[Pair]:
    """Put policy-pending bootstrap pairs before completed rolling work."""
    current = utc(now or datetime.now(timezone.utc))
    states = get_pair_states(config, pairs)
    return sorted(pairs, key=lambda pair: _bootstrap_complete(
        config, states[(int(pair[0]["symbol_id"]), pair[1]["code"])], current))


def run_backfill(
    config: dict[str, Any],
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    bars: int | None = None,
) -> dict[str, Any]:
    """Run one finite historical generation."""
    if not config["backfill"].get("enabled", True):
        raise RuntimeError("backfill is disabled in Config.yaml")
    pairs = select_pairs(
        config, live=False, symbol_filter=symbol, timeframe_filter=timeframe
    )
    return run_backfill_pairs(config, pairs, bars_override=bars)
