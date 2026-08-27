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

# File này chạy backfill: lấy dữ liệu lịch sử và lấp phần SQL còn thiếu.
# Backfill chỉ lập kế hoạch và gọi TradingView. Việc kiểm nến và ghi SQL nằm ở `pipeline.py`.
# Luồng chính: chọn cặp, tính số nến, tải TradingView, rồi gửi sang pipeline.

# Các ngưỡng dưới đây giúp tránh request quá lớn hoặc cố chạy tiếp khi lỗi liên tục.


class CatchupWindowError(RuntimeError):
    """Raised rather than silently skipping the oldest part of a gap."""


@dataclass(frozen=True)
class BackfillPlan:
    """One historical series with adaptive and absolute request bounds."""

    # Một plan nói rõ cặp này cần lấy bao nhiêu nến và từ mốc nào tới mốc nào.
    # Nếu bắt buộc phủ đủ cửa sổ, thiếu nến thì không ghi SQL.

    bars: int
    max_bars: int
    window_start: datetime | None
    window_end: datetime
    complete_bootstrap: bool
    require_coverage: bool
    required_cursor: datetime | None


def _bounded(config: dict[str, Any], bars: int, context: str) -> int:
    # Nếu cần lấy quá nhiều nến trong một lần thì báo lỗi.
    # Không tự cắt bớt, vì cắt bớt có thể làm thiếu dữ liệu.
    maximum = int(config["backfill"]["max_bars_per_request"])
    if bars > maximum:
        raise CatchupWindowError(
            f"{context} requires {bars} bars and exceeds request cap {maximum}"
        )
    return max(1, bars)


def _bootstrap_complete(config: dict[str, Any], state: dict[str, Any], current: datetime) -> bool:
    """Complete once Fact_OHLCV's earliest bar reaches the lookback window (require_coverage already proved it)."""
    # Bootstrap xong khi SQL đã có dữ liệu chạm tới mốc lookback.
    # Hàm này chỉ đọc mốc nến cũ nhất trong SQL để quyết định cần chạy kiểu nào.
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
    # Lập kế hoạch cho một cặp symbol/timeframe.
    # Chưa đủ dữ liệu nền thì nhìn lại 60 ngày.
    # Đã có dữ liệu nền thì chỉ kéo phần mới, có lùi vài nến để tránh miss.
    current = utc(now or datetime.now(timezone.utc))
    maximum = int(config["backfill"]["max_bars_per_request"])
    if bars_override is not None:
        # Dùng khi chạy thủ công/debug. Vẫn phải nằm trong giới hạn an toàn.
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
        # Lần đầu cần nhìn lại đủ lookback_days.
        # Thị trường có ngày nghỉ nên không lấy theo lịch cứng từng phút.
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
    # Lùi lại vài nến quanh mốc mới nhất để bắt nến bị thiếu hoặc bị sửa.
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
    # Gom nhiều timeframe của cùng một symbol vào một lần gọi TradingView.
    # Vừa tiết kiệm kết nối, vừa không vượt giới hạn số nến.
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
            # Nếu cặp đầu nhóm lỗi, vẫn trả cặp đó để caller log rõ lỗi.
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
    # Runtime dùng hàm này để lấy nhóm kế tiếp đủ nhỏ để chạy an toàn.
    current = utc(now or datetime.now(timezone.utc))
    states = get_pair_states(config, pairs)
    return _next_group(config, pairs, states, current, bars_override)


def _empty_summary(pairs: int) -> dict[str, Any]:
    # Mẫu kết quả để runtime/log biết nhóm này chạy được bao nhiêu, lỗi bao nhiêu.
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
    # Chạy một nhóm cùng symbol.
    # Tải dữ liệu một lần từ TradingView, rồi ghi từng timeframe qua pipeline.
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
            # Lỗi lập plan chỉ đánh dấu cặp đó fail; cặp khác vẫn có thể chạy.
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
        # Nếu lần tải từ TradingView lỗi, cả nhóm bị đánh dấu fail.
        for (symbol, timeframe), _plan, _request in planned:
            summary["failed"] += 1
            summary["failed_pairs"].append(pair_key((symbol, timeframe)))
            log_pair_failure("backfill", symbol, timeframe, exc, stage="fetch")
        summary["group_failures"] = 1
        return summary
    try:
        connection = get_connection(config)
    except Exception as exc:
        # Không mở được SQL thì không ghi gì cả.
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
                # Lỗi riêng từng cặp sẽ được log riêng; cặp khác vẫn chạy tiếp.
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
    # Chạy một lượt backfill hữu hạn.
    # Chạy theo nhóm để tiết kiệm kết nối, nhưng ghi SQL từng cặp để dễ kiểm soát lỗi.
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
            # Nếu lỗi nhiều nhóm, dừng phần còn lại để lần sau chạy tiếp.
            summary["deferred_pairs"] = [pair_key(pair) for pair in remaining]
            summary["deferred"] = len(remaining)
            break
    return summary


def prioritize_backfill_pairs(
    config: dict[str, Any], pairs: list[Pair], *, now: datetime | None = None
) -> list[Pair]:
    """Put policy-pending bootstrap pairs before completed rolling work."""
    # Cặp chưa đủ dữ liệu nền được chạy trước.
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
    # Điểm vào của backfill.
    # Runtime gọi theo lịch; CLI có thể lọc symbol/timeframe khi cần kiểm tra.
    if not config["backfill"].get("enabled", True):
        raise RuntimeError("backfill is disabled in Config.yaml")
    pairs = select_pairs(
        config, live=False, symbol_filter=symbol, timeframe_filter=timeframe
    )
    return run_backfill_pairs(config, pairs, bars_override=bars)
