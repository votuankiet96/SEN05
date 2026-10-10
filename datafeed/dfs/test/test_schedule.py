"""Lịch phát: quy tắc giờ phát, thứ tự tất định, mỗi nến đúng một lần, cửa sổ đọc, mã băm."""
from __future__ import annotations

import random
from datetime import timedelta

import pytest

from schedule import PlanStats, ScheduleDigest, ScheduleError, iter_ticks, release_time
from support import M5, M15, at, candle, fetch_from, pair, series


def labels(tick):
    return [f"{c.pair.timeframe}@{c.bar_time:%H:%M}" for c in tick.candles]


def test_operator_example_m5_plus_m15():
    candles = series(M5, "2026-09-30 13:00", 9) + series(M15, "2026-09-30 13:00", 3)
    ticks = list(iter_ticks(fetch_from(candles), at("2026-09-30 13:00"), at("2026-09-30 13:45")))
    assert [t.release.strftime("%H:%M") for t in ticks] == ["13:05", "13:10", "13:15", "13:20", "13:25", "13:30", "13:35", "13:40", "13:45"]
    assert [t.seq for t in ticks] == list(range(1, 10))
    assert labels(ticks[0]) == ["M5@13:00"] and labels(ticks[1]) == ["M5@13:05"]
    assert labels(ticks[2]) == ["M5@13:10", "M15@13:00"]      # tick 3: M5 và M15 cùng một lượt, khung nhỏ trước
    assert labels(ticks[5]) == ["M5@13:25", "M15@13:15"]
    assert labels(ticks[8]) == ["M5@13:40", "M15@13:30"]


def test_boundaries_start_is_exclusive_and_end_is_inclusive():
    candles = series(M5, "2026-09-30 13:00", 6)               # giờ phát 13:05 .. 13:30
    ticks = list(iter_ticks(fetch_from(candles), at("2026-09-30 13:05"), at("2026-09-30 13:20")))
    assert [t.release.strftime("%H:%M") for t in ticks] == ["13:10", "13:15", "13:20"]


def test_order_inside_a_tick_is_total_and_independent_of_input_order():
    other = pair("DE40", "M5", 5, symbol_id=3)
    candles = [candle(M5, "2026-09-30 13:10"), candle(M15, "2026-09-30 13:00"), candle(other, "2026-09-30 13:10")]
    expected = None
    for seed in range(20):
        shuffled = candles[:]
        random.Random(seed).shuffle(shuffled)
        (tick,) = list(iter_ticks(fetch_from(shuffled), at("2026-09-30 13:10"), at("2026-09-30 13:15")))
        names = [(c.pair.minutes, c.pair.symbol_id) for c in tick.candles]
        assert names == [(5, 3), (5, 56), (15, 56)]            # khung nhỏ -> lớn, rồi SymbolID
        expected = expected or tick
        assert tick == expected


def test_every_candle_released_exactly_once_at_its_close_time():
    rng = random.Random(1)
    pairs = [pair(s, f"M{m}", m, symbol_id=i, timeframe_id=j) for i, s in enumerate(("A", "B", "C"), 1) for j, m in enumerate((5, 15, 45), 1)]
    candles = []
    for p in pairs:
        candles += [c for c in series(p, "2026-09-30 00:00", 24 * 60 // p.minutes) if rng.random() > 0.3]   # có lỗ hổng như thị trường nghỉ
    start, end = at("2026-09-30 02:00"), at("2026-09-30 20:00")
    ticks = list(iter_ticks(fetch_from(candles), start, end, window=timedelta(hours=3)))
    released = [c for t in ticks for c in t.candles]
    assert sorted(map(id, released)) == sorted(id(c) for c in candles if start < release_time(c) <= end)
    assert all(release_time(c) == t.release for t in ticks for c in t.candles)
    assert all(a.release < b.release for a, b in zip(ticks, ticks[1:]))
    assert [t.seq for t in ticks] == list(range(1, len(ticks) + 1))


def test_window_size_does_not_change_the_schedule():
    candles = series(M5, "2026-09-29 20:00", 400) + series(M15, "2026-09-29 20:00", 130)
    digests = set()
    for window in (timedelta(minutes=5), timedelta(hours=1), timedelta(days=1), timedelta(days=7)):
        stats = PlanStats()
        for tick in iter_ticks(fetch_from(candles), at("2026-09-29 20:00"), at("2026-09-30 22:00"), window=window):
            stats.add(tick)
        digests.add((stats.hexdigest, stats.ticks, stats.candles))
    assert len(digests) == 1


def test_source_returning_candles_outside_the_window_is_rejected():
    stray = candle(M5, "2026-09-30 18:00")
    with pytest.raises(ScheduleError, match="outside the window"):
        list(iter_ticks(lambda a, b: [stray], at("2026-09-30 13:00"), at("2026-09-30 14:00")))


def test_duplicate_candle_is_rejected():
    twin = candle(M5, "2026-09-30 13:00")
    with pytest.raises(ScheduleError, match="duplicate"):
        list(iter_ticks(lambda a, b: [twin, twin], at("2026-09-30 13:00"), at("2026-09-30 14:00")))


def test_window_must_be_positive():
    with pytest.raises(ValueError):
        list(iter_ticks(fetch_from([]), at("2026-09-30 13:00"), at("2026-09-30 14:00"), window=timedelta(0)))


def test_digest_is_stable_sensitive_and_prefix_consistent():
    base = series(M5, "2026-09-30 13:00", 6)
    changed = base[:3] + [candle(M5, "2026-09-30 13:15", "101.00")] + base[4:]

    def running(candles):
        digest, out = ScheduleDigest(), []
        for tick in iter_ticks(fetch_from(candles), at("2026-09-30 13:00"), at("2026-09-30 13:30")):
            out.append(digest.update(tick))
        return out

    a, again, b = running(base), running(base), running(changed)
    assert a == again                                          # tái lập
    assert a[:3] == b[:3] and a[3] != b[3] and a[-1] != b[-1]  # đổi một giá thì mã băm lệch từ mốc đó trở đi


def test_plan_stats_counts():
    candles = series(M5, "2026-09-30 13:00", 6) + series(M15, "2026-09-30 13:00", 2)
    stats = PlanStats()
    for tick in iter_ticks(fetch_from(candles), at("2026-09-30 13:00"), at("2026-09-30 13:30")):
        stats.add(tick)
    assert (stats.ticks, stats.candles, stats.max_per_tick) == (6, 8, 2)
    assert (stats.first, stats.last) == (at("2026-09-30 13:05"), at("2026-09-30 13:30"))
