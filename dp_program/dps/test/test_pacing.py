"""Điều tốc: deadline tuyệt đối (không trôi), bù khi trễ, tốc độ tối đa, tỷ lệ thời gian, ngắt khi dừng."""
from __future__ import annotations

import pytest

from runtime import DpsError, estimate_seconds, make_pacer
from support import at


class FakeTime:
    def __init__(self, now: float = 100.0) -> None:
        self.now, self.sleeps = now, []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def build(mode, delay=1.0, speed=300.0, now=100.0):
    fake = FakeTime(now)
    return fake, make_pacer(mode, delay, speed, clock=fake.clock, sleep=fake.sleep)


NEVER = lambda: False  # noqa: E731


def test_delay_mode_is_anchored_to_absolute_deadlines():
    fake, pacer = build("delay", delay=1.0)
    pacer.begin(at("2026-09-30 13:00"))
    assert pacer.wait(at("2026-09-30 13:05"), NEVER) == pytest.approx(0.0, abs=1e-9)
    assert fake.now == pytest.approx(101.0)                      # tick 1 lúc t0 + 1 giây (đúng ví dụ của operator)
    fake.now += 0.4                                              # xử lý/ghi mất 0.4 giây
    pacer.wait(at("2026-09-30 13:10"), NEVER)
    assert fake.now == pytest.approx(102.0)                      # tick 2 vẫn đúng t0 + 2: không cộng dồn trôi


def test_delay_mode_reports_lag_and_does_not_sleep_when_late():
    fake, pacer = build("delay", delay=1.0)
    pacer.begin(at("2026-09-30 13:00"))
    fake.now = 105.0
    assert pacer.wait(at("2026-09-30 13:05"), NEVER) == pytest.approx(4.0)
    assert fake.sleeps == []


def test_zero_delay_is_maximum_speed():
    fake, pacer = build("delay", delay=0.0)
    pacer.begin(at("2026-09-30 13:00"))
    for _ in range(100):
        pacer.wait(at("2026-09-30 13:05"), NEVER)
    assert fake.sleeps == []


def test_speed_mode_keeps_the_time_ratio_including_market_closures():
    fake, pacer = build("speed", speed=300.0)
    pacer.begin(at("2026-10-02 20:55"))
    pacer.wait(at("2026-10-02 21:00"), NEVER)
    assert fake.now == pytest.approx(101.0)                      # 5 phút ảo = 1 giây thật
    pacer.wait(at("2026-10-04 22:00"), NEVER)                    # sau khoảng nghỉ cuối tuần 49 giờ
    assert fake.now == pytest.approx(100.0 + (49 * 3600 + 5 * 60) / 300.0)


def test_stop_interrupts_the_wait_within_one_slice():
    fake, pacer = build("delay", delay=10.0)
    pacer.begin(at("2026-09-30 13:00"))
    calls = iter([False, False, True])
    assert pacer.wait(at("2026-09-30 13:05"), lambda: next(calls)) is None
    assert fake.now - 100.0 <= 0.5                               # dừng sau tối đa vài lát sleep, không chờ hết 10 giây


def test_begin_resets_the_tick_counter():
    fake, pacer = build("delay", delay=1.0)
    pacer.begin(at("2026-09-30 13:00"))
    pacer.wait(at("2026-09-30 13:05"), NEVER)
    pacer.begin(at("2026-09-30 13:05"))                          # chạy tiếp từ checkpoint
    start = fake.now
    pacer.wait(at("2026-09-30 13:10"), NEVER)
    assert fake.now - start == pytest.approx(1.0)


def test_unknown_mode_is_rejected():
    with pytest.raises(DpsError):
        make_pacer("warp", 1.0, 1.0)


def test_estimate_seconds():
    start = at("2026-09-30 13:00")
    assert estimate_seconds("delay", 0.1, 300.0, 92507, start, at("2026-10-04 13:00")) == pytest.approx(9250.7)
    assert estimate_seconds("speed", 0.1, 300.0, 5, start, at("2026-09-30 14:00")) == pytest.approx(12.0)
    assert estimate_seconds("speed", 0.1, 300.0, 0, start, None) == 0.0


def test_delay_mode_does_not_burst_after_a_long_stall():
    fake, pacer = build("delay", delay=1.0)
    pacer.begin(at("2026-09-30 13:00"))
    pacer.wait(at("2026-09-30 13:05"), NEVER)                    # tick 1 đúng hạn (t = 101)
    fake.now = 161.0                                             # đứng hình 60 giây (Redis chậm, VM bị treo...)
    assert pacer.wait(at("2026-09-30 13:10"), NEVER) == pytest.approx(59.0)      # vẫn báo độ trễ thật
    before = fake.now
    pacer.wait(at("2026-09-30 13:15"), NEVER)
    assert fake.now - before == pytest.approx(1.0)               # mốc kế tiếp chờ đủ 1 giây, không phát bù 59 mốc liền
    pacer.wait(at("2026-09-30 13:20"), NEVER)
    assert fake.now - before == pytest.approx(2.0)


def test_speed_mode_does_not_burst_after_a_long_stall():
    fake, pacer = build("speed", speed=300.0)
    pacer.begin(at("2026-09-30 13:00"))
    pacer.wait(at("2026-09-30 13:05"), NEVER)                    # 1 giây thật
    fake.now += 120.0                                            # đứng hình 2 phút
    pacer.wait(at("2026-09-30 13:10"), NEVER)
    before = fake.now
    pacer.wait(at("2026-09-30 13:15"), NEVER)
    assert fake.now - before == pytest.approx(1.0)               # 5 phút ảo vẫn là 1 giây thật


def test_a_small_lag_is_still_caught_up_without_resync():
    fake, pacer = build("delay", delay=1.0)
    pacer.begin(at("2026-09-30 13:00"))
    fake.now = 103.0                                             # trễ 2 giây (dưới ngưỡng): vẫn bù để giữ đúng nhịp tuyệt đối
    pacer.wait(at("2026-09-30 13:05"), NEVER)
    pacer.wait(at("2026-09-30 13:10"), NEVER)
    assert fake.sleeps == []
