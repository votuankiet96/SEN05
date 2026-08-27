"""Live-tail planning, bounded symbol batches, pending, and recovery."""
from __future__ import annotations
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from .sql_connector import Pair, pair_key, select_pairs
from .auth import AuthError
from ..log import log_event
from .pipeline import fetch_and_store, log_pair_failure, utc
from .sql_connector import get_connection, get_pair_states
from .websocket import FetchRequest, fetch_candles_batch, request_key
from ..util.redis_publisher import publish_candle_update

LOGGER = logging.getLogger(__name__)
_MAX_CONSECUTIVE_GROUP_FAILURES = 2
_MAX_TOTAL_GROUP_FAILURES = 3
_CYCLE_BUDGET_SECONDS = 120

# File này chạy live: lấy nến mới theo chu kỳ ngắn.
# Live chỉ chạy cho cặp đã có dữ liệu nền trong SQL. Nếu chưa có, backfill phải chạy trước.
# Nếu lần này lấy thiếu nến, hệ thống ghi nhớ cặp đó để lần sau kéo bù.

# Các ngưỡng dưới đây giúp dừng sớm khi lỗi liên tục, tránh đánh quá nhiều vào TradingView.


class CatchupWindowError(RuntimeError):
    """Raised rather than advancing across an oversized gap."""


class MissingWatermarkError(RuntimeError):
    """Raised when live cannot prove a durable SQL starting point."""


@dataclass(frozen=True)
class LivePlan:
    """One live request derived from the durable Fact watermark."""

    # Một plan nói rõ lần live này cần lấy bao nhiêu nến.
    # Mốc bắt đầu luôn dựa trên nến mới nhất đã lưu trong SQL.
    # Nếu response không chứa lại mốc đó, hệ thống không ghi để tránh mất gap.

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
    # Lập kế hoạch cho một cặp trong một cycle live.
    # Bình thường lấy ít nến. Nếu cặp đang pending thì lấy rộng hơn để bù.
    # Nếu không biết bắt đầu từ đâu, live báo lỗi để backfill xử lý trước.
    current = utc(now or datetime.now(timezone.utc))
    if latest is None:
        raise MissingWatermarkError("live requires a Fact watermark; run backfill first")
    durable_latest = utc(latest)
    if durable_latest > current:
        raise CatchupWindowError("Fact watermark is ahead of the current UTC time")
    minutes = int(timeframe["minutes"])
    overlap = int(config["backfill"]["overlap_bars"])
    # Lùi lại vài nến để bắt nến bị thiếu hoặc bị sửa.
    start = durable_latest - timedelta(minutes=max(0, overlap - 1) * minutes)
    tail = max(int(config["live"]["bars_per_request"]), overlap) + 2
    required = (
        math.ceil((current - start).total_seconds() / (minutes * 60)) + 1
        if catch_up else tail
    )
    maximum = int(config["backfill"]["max_bars_per_request"])
    if required > maximum:
        raise CatchupWindowError(
            f"live catch-up requires {required} bars and exceeds request cap {maximum}"
        )
    max_bars = min(maximum, max(tail, required + overlap + 2))
    return LivePlan(tail, max_bars, start, current, True, durable_latest)


def _ordered_pairs(pairs: list[Pair], prior_order: list[str]) -> list[Pair]:
    # Cặp còn pending từ cycle trước được chạy trước.
    # Nhờ vậy phần có nguy cơ thiếu dữ liệu được ưu tiên bù.
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
    # Gom nhiều timeframe của cùng một symbol vào một lần gọi TradingView.
    # Làm vậy giảm số lần kết nối nhưng vẫn giữ request trong giới hạn an toàn.
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
    # Chạy một cycle live hữu hạn.
    # Luồng: đọc mốc SQL, lập kế hoạch, tải từ TradingView, ghi SQL, rồi trả danh sách còn pending.
    planning_started = time.monotonic()
    current = utc(now or datetime.now(timezone.utc))
    selected = {pair_key(pair) for pair in pairs}
    prior_order = [key for key in (pending_pairs or ()) if key in selected]
    prior_pending = set(prior_order)
    # Chỉ giữ pending nếu cặp đó vẫn còn trong danh sách live hiện tại.
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
            # Cặp pending lấy rộng hơn; cặp bình thường lấy ít nến.
            plan = plan_live(
                config, timeframe,
                states[(int(symbol["symbol_id"]), timeframe["code"])]["latest"],
                now=current, catch_up=display in prior_pending,
            )
            planned.append(((symbol, timeframe), plan, FetchRequest(
                symbol, timeframe, plan.bars, plan.max_bars, plan.window_start
            )))
        except Exception as exc:
            # Không lập được plan thì giữ cặp này cho cycle sau.
            summary["failed"] += 1
            failed_pairs.append(display)
            pending.add(display)
            processed.add(display)
            log_pair_failure("live", symbol, timeframe, exc)
    groups = _request_groups(config, planned)
    timings = {
        "planning_seconds": time.monotonic() - planning_started,
        "fetch_seconds": 0.0, "connection_seconds": 0.0,
        "pipeline_seconds": 0.0, "max_pair_seconds": 0.0,
    }

    def timed(key: str, action: Callable[[], Any]) -> Any:
        # Đo thời gian từng bước để log biết chậm ở đâu.
        stage_started = time.monotonic()
        try:
            return action()
        finally:
            timings[key] += time.monotonic() - stage_started
    budget_started = time.monotonic()
    consecutive = total_group_failures = 0
    circuit_open = False
    for group_index, group in enumerate(groups):
        if (stop_requested and stop_requested()) or (
            group_index and time.monotonic() - budget_started >= _CYCLE_BUDGET_SECONDS
        ):
            # Nếu đang stop hoặc hết thời gian cycle, phần chưa chạy để lại cho cycle sau.
            break
        group_failed = False
        requests = [request for _pair, _plan, request in group]
        try:
            fetched = timed(
                "fetch_seconds", lambda: fetch_candles_batch(config, requests)
            )
        except AuthError:
            raise
        except Exception as exc:
            # Tải lỗi thì cả nhóm vào pending để lần sau thử lại.
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
                connection = timed("connection_seconds", lambda: get_connection(config))
            except Exception as exc:
                # Không mở được SQL thì không ghi gì; cả nhóm chờ lần sau.
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
                        pair_started = time.monotonic()
                        try:
                            # Pipeline sẽ kiểm nến trước khi ghi SQL.
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
                            if int(result["affected"]) > 0:
                                publish_candle_update(
                                    config, symbol["symbol_id"], symbol["symbol"], timeframe["code"],
                                )
                            if display in prior_pending:
                                recovered.append(display)
                            pending.discard(display)
                        except AuthError:
                            raise
                        except Exception as exc:
                            # Lỗi riêng cặp nào thì cặp đó vào pending.
                            group_failed = True
                            summary["failed"] += 1
                            failed_pairs.append(display)
                            pending.add(display)
                            log_pair_failure("live", symbol, timeframe, exc)
                        finally:
                            pair_seconds = time.monotonic() - pair_started
                            timings["pipeline_seconds"] += pair_seconds
                            timings["max_pair_seconds"] = max(
                                timings["max_pair_seconds"], pair_seconds
                            )
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
            # Lỗi nhiều nhóm thì dừng sớm để bảo vệ tài khoản.
            circuit_open = True
            break
    deferred_pairs = [
        pair_key(pair) for pair in ordered if pair_key(pair) not in processed
    ]
    # Cặp chưa kịp chạy hoặc chạy lỗi đều được đưa vào pending.
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
    # Giữ thứ tự pending ổn định để cycle sau ưu tiên đúng.
    summary.update(
        failed_pairs=failed_pairs, deferred_pairs=deferred_pairs,
        deferred=len(deferred_pairs), pending_pairs=pending_order,
        recovered_pairs=recovered,
        timings={key: round(value, 3) for key, value in timings.items()},
    )
    return summary


def run_live_cycle(
    config: dict[str, Any],
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
) -> dict[str, Any]:
    """Run exactly one finite live cycle."""
    # Điểm vào khi chạy một live cycle thủ công.
    # Runtime 24/7 gọi hàm thấp hơn để giữ pending qua nhiều cycle.
    if not config["live"].get("enabled", True):
        raise RuntimeError("live fetching is disabled in Config.yaml")
    pairs = select_pairs(
        config, live=True, symbol_filter=symbol, timeframe_filter=timeframe
    )
    return run_live_pairs(
        config, pairs, pending_pairs=[pair_key(pair) for pair in pairs]
    )
