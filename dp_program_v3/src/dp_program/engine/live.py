"""Live-tail planning, bounded symbol batches, pending, and recovery."""
from __future__ import annotations
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from . import Pair, pair_key, select_pairs
from .auth import AuthError
from ..log import log_event
from .pipeline import fetch_and_store, log_pair_failure, utc
from .sql_connector import get_connection, get_pair_states
from .websocket import FetchRequest, fetch_candles_batch, request_key

LOGGER = logging.getLogger(__name__)
_MAX_CONSECUTIVE_GROUP_FAILURES = 2
_MAX_TOTAL_GROUP_FAILURES = 3
_CYCLE_BUDGET_SECONDS = 120


class CatchupWindowError(RuntimeError):
    """Raised rather than advancing across an oversized gap."""


class MissingWatermarkError(RuntimeError):
    """Raised when live cannot prove a durable SQL starting point."""


@dataclass(frozen=True)
class LivePlan:
    """One live request derived from the durable Fact watermark."""

    bars: int
    max_bars: int
    window_start: datetime
    window_end: datetime
    require_coverage: bool
    required_cursor: datetime


def plan_live(
    config: dict[str, Any],
    timeframe: dict[str, Any],
    latest: datetime | None,
    *,
    now: datetime | None = None,
    catch_up: bool = False,
) -> LivePlan:
    """Use a small healthy tail and bounded Fact-watermark recovery."""
    current = utc(now or datetime.now(timezone.utc))
    if latest is None:
        raise MissingWatermarkError("live requires a Fact watermark; run backfill first")
    durable_latest = utc(latest)
    if durable_latest > current:
        raise CatchupWindowError("Fact watermark is ahead of the current UTC time")
    minutes = int(timeframe["minutes"])
    overlap = int(config["backfill"]["overlap_bars"])
    start = durable_latest - timedelta(minutes=max(0, overlap - 1) * minutes)
    if catch_up:
        bars = math.ceil((current - start).total_seconds() / (minutes * 60)) + 1
    else:
        bars = max(int(config["live"]["bars_per_request"]), overlap) + 2
    maximum = int(config["backfill"]["max_bars_per_request"])
    if bars > maximum:
        raise CatchupWindowError(
            f"live catch-up requires {bars} bars and exceeds request cap {maximum}"
        )
    max_bars = min(maximum, bars + overlap + 2)
    return LivePlan(max(1, bars), max_bars, start, current, True, durable_latest)


def _ordered_pairs(pairs: list[Pair], prior_order: list[str]) -> list[Pair]:
    pending = set(prior_order)
    priority = {key: index for index, key in enumerate(prior_order)}
    return sorted(
        pairs,
        key=lambda pair: (
            pair_key(pair) not in pending,
            priority.get(pair_key(pair), len(priority)),
        ),
    )


def _request_groups(
    config: dict[str, Any],
    planned: list[tuple[Pair, LivePlan, FetchRequest]],
) -> list[list[tuple[Pair, LivePlan, FetchRequest]]]:
    cap = int(config["backfill"]["max_bars_per_request"])
    by_symbol: dict[tuple[str, str], list[tuple[Pair, LivePlan, FetchRequest]]] = {}
    for item in planned:
        symbol = item[0][0]
        by_symbol.setdefault(
            (symbol["exchange"], symbol["symbol"]), []
        ).append(item)
    groups = []
    for items in by_symbol.values():
        current, initial, maximum = [], 0, 0
        for item in items:
            request = item[2]
            if current and (
                initial + request.bars > cap
                or maximum + request.max_bars > cap
            ):
                groups.append(current)
                current, initial, maximum = [], 0, 0
            current.append(item)
            initial += request.bars
            maximum += request.max_bars
        if current:
            groups.append(current)
    return groups


def run_live_pairs(
    config: dict[str, Any],
    pairs: list[Pair],
    *,
    pending_pairs: Iterable[str] | None = None,
    now: datetime | None = None,
    stop_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Fetch per-symbol batches, then deliver each pair serially."""
    current = utc(now or datetime.now(timezone.utc))
    selected = {pair_key(pair) for pair in pairs}
    prior_order = [key for key in (pending_pairs or ()) if key in selected]
    prior_pending = set(prior_order)
    ordered = _ordered_pairs(pairs, prior_order)
    states = get_pair_states(config, ordered)
    pending, processed = set(prior_pending), set()
    recovered: list[str] = []
    failed_pairs: list[str] = []
    summary: dict[str, Any] = {
        "pairs": len(ordered), "ok": 0, "failed": 0, "affected": 0,
        "group_failures": 0,
    }
    planned: list[tuple[Pair, LivePlan, FetchRequest]] = []
    for symbol, timeframe in ordered:
        display = pair_key((symbol, timeframe))
        try:
            plan = plan_live(
                config, timeframe,
                states[(int(symbol["symbol_id"]), timeframe["code"])]["latest"],
                now=current, catch_up=display in prior_pending,
            )
            planned.append(((symbol, timeframe), plan, FetchRequest(
                symbol, timeframe, plan.bars, plan.max_bars, plan.window_start
            )))
        except Exception as exc:
            summary["failed"] += 1
            failed_pairs.append(display)
            pending.add(display)
            processed.add(display)
            log_pair_failure("live", symbol, timeframe, exc)
    groups = _request_groups(config, planned)
    started = time.monotonic()
    consecutive = total_group_failures = 0
    circuit_open = False
    for group_index, group in enumerate(groups):
        if (stop_requested and stop_requested()) or (
            group_index and time.monotonic() - started >= _CYCLE_BUDGET_SECONDS
        ):
            break
        group_failed = False
        requests = [request for _pair, _plan, request in group]
        try:
            fetched = fetch_candles_batch(config, requests)
        except AuthError:
            raise
        except Exception as exc:
            group_failed = True
            for (symbol, timeframe), _plan, _request in group:
                display = pair_key((symbol, timeframe))
                summary["failed"] += 1
                failed_pairs.append(display)
                pending.add(display)
                processed.add(display)
                log_pair_failure("live", symbol, timeframe, exc, stage="fetch")
        else:
            try:
                connection = get_connection(config)
            except Exception as exc:
                group_failed = True
                for (symbol, timeframe), _plan, _request in group:
                    display = pair_key((symbol, timeframe))
                    summary["failed"] += 1
                    failed_pairs.append(display)
                    pending.add(display)
                    processed.add(display)
                    log_pair_failure("live", symbol, timeframe, exc, stage="sql_compare")
            else:
                try:
                    for (symbol, timeframe), plan, request in group:
                        display = pair_key((symbol, timeframe))
                        try:
                            provider = fetched[request_key(request)]
                            result = fetch_and_store(
                                config, symbol, timeframe, workflow="live",
                                bars=provider.requested_bars,
                                window_start=plan.window_start,
                                window_end=plan.window_end,
                                require_coverage=plan.require_coverage,
                                required_cursor=plan.required_cursor,
                                provider_candles=provider.candles, now=current,
                                connection=connection,
                            )
                            summary["ok"] += 1
                            summary["affected"] += int(result["affected"])
                            if display in prior_pending:
                                recovered.append(display)
                            pending.discard(display)
                        except AuthError:
                            raise
                        except Exception as exc:
                            group_failed = True
                            summary["failed"] += 1
                            failed_pairs.append(display)
                            pending.add(display)
                            log_pair_failure("live", symbol, timeframe, exc)
                        processed.add(display)
                finally:
                    connection.close()
        if group_failed:
            consecutive += 1
            total_group_failures += 1
            summary["group_failures"] += 1
        else:
            consecutive = 0
        if (
            consecutive >= _MAX_CONSECUTIVE_GROUP_FAILURES
            or total_group_failures >= _MAX_TOTAL_GROUP_FAILURES
        ):
            circuit_open = True
            break
    deferred_pairs = [
        pair_key(pair) for pair in ordered if pair_key(pair) not in processed
    ]
    pending.update(deferred_pairs)
    if circuit_open:
        log_event(
            LOGGER, logging.ERROR, "LIVE_CIRCUIT_OPEN", "HIGH",
            component="live", consecutive_failures=consecutive,
            cycle_failures=total_group_failures,
            deferred_pairs=len(deferred_pairs),
            action="remaining symbol batches deferred to protect the provider account",
        )
    pending_order = list(dict.fromkeys(
        deferred_pairs + failed_pairs
        + [key for key in prior_order if key in pending]
        + sorted(pending)
    ))
    summary.update(
        failed_pairs=failed_pairs, deferred_pairs=deferred_pairs,
        deferred=len(deferred_pairs), pending_pairs=pending_order,
        recovered_pairs=recovered,
    )
    return summary


def run_live_cycle(
    config: dict[str, Any],
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
) -> dict[str, Any]:
    """Run exactly one finite live cycle."""
    if not config["live"].get("enabled", True):
        raise RuntimeError("live fetching is disabled in Config.yaml")
    pairs = select_pairs(
        config, live=True, symbol_filter=symbol, timeframe_filter=timeframe
    )
    return run_live_pairs(
        config, pairs, pending_pairs=[pair_key(pair) for pair in pairs]
    )
