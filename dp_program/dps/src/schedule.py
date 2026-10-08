"""Lịch phát: từ nến SQL đến chuỗi mốc (Tick) tất định. Hàm thuần, không I/O.

Quy tắc: một nến chỉ được phát khi đã ĐÓNG, tức giờ phát = BarTime + Minutes (đúng quy tắc của
`pipeline.validate_candles` ở hệ thật). Đồng hồ ảo nhảy từ mốc phát này sang mốc kế tiếp (next-event);
mọi nến cùng giờ phát nằm trong một Tick, sắp theo (khung nhỏ -> lớn, symbol) để thứ tự luôn như nhau.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import groupby
from typing import Callable, Iterable, Iterator

from runtime import Candle, DpsError, Tick

DEFAULT_WINDOW = timedelta(days=7)
# fetch(after, upto) trả mọi nến có giờ phát trong khoảng (after, upto].
FetchWindow = Callable[[datetime, datetime], Iterable[Candle]]


class ScheduleError(DpsError):
    """Nguồn nến trả dữ liệu sai hợp đồng (nến ngoài cửa sổ hoặc trùng lặp)."""


def release_time(candle: Candle) -> datetime:
    return candle.bar_time + timedelta(minutes=candle.pair.minutes)


def order_key(candle: Candle) -> tuple[datetime, int, int]:
    # Thứ tự toàn phần: giờ phát, rồi khung nhỏ trước, rồi theo SymbolID. Mỗi nến có khóa duy nhất.
    return (release_time(candle), candle.pair.minutes, candle.pair.symbol_id)


def iter_ticks(
    fetch: FetchWindow, start: datetime, end: datetime, *,
    window: timedelta = DEFAULT_WINDOW, first_seq: int = 1,
) -> Iterator[Tick]:
    """Sinh các Tick có giờ phát trong (start, end], tăng dần. Đọc nguồn theo từng cửa sổ giờ phát để bộ nhớ không đổi."""
    if window <= timedelta(0):
        raise ValueError("window must be positive")
    seq, after = first_seq, start
    while after < end:
        upto = min(after + window, end)
        for release, group in groupby(sorted(fetch(after, upto), key=order_key), key=release_time):
            members = tuple(group)
            if not after < release <= upto:
                raise ScheduleError(f"candle released at {release} lies outside the window ({after}, {upto}]")
            if len({(c.pair.symbol_id, c.pair.timeframe_id) for c in members}) != len(members):
                raise ScheduleError(f"duplicate candle in the tick released at {release}")
            yield Tick(seq, release, members)
            seq += 1
        after = upto


def _tick_text(tick: Tick) -> str:
    lines = [f"T|{tick.seq}|{tick.release:%Y-%m-%d %H:%M:%S}"]
    lines.extend(
        f"C|{c.pair.symbol}|{c.pair.timeframe}|{c.bar_time:%Y-%m-%d %H:%M:%S}|{c.open:f}|{c.high:f}|{c.low:f}|{c.close:f}"
        for c in tick.candles
    )
    return "\n".join(lines) + "\n"


class ScheduleDigest:
    """Băm SHA-256 tích lũy của lịch phát: cùng dữ liệu + cùng tham số thì cùng mã (chứng minh tái lập)."""

    def __init__(self) -> None:
        self._hash = hashlib.sha256()

    def update(self, tick: Tick) -> str:
        """Nạp một Tick và trả mã băm tích lũy tới mốc đó (dùng làm checkpoint)."""
        self._hash.update(_tick_text(tick).encode("utf-8"))
        return self._hash.hexdigest()

    def hexdigest(self) -> str:
        return self._hash.hexdigest()


@dataclass(slots=True)
class PlanStats:
    """Thống kê của một lịch phát (dùng cho `plan` và kiểm thử)."""

    ticks: int = 0
    candles: int = 0
    max_per_tick: int = 0
    first: datetime | None = None
    last: datetime | None = None
    digest: ScheduleDigest = field(default_factory=ScheduleDigest)

    def add(self, tick: Tick) -> None:
        self.ticks += 1
        self.candles += len(tick.candles)
        self.max_per_tick = max(self.max_per_tick, len(tick.candles))
        self.first = self.first or tick.release
        self.last = tick.release
        self.digest.update(tick)

    @property
    def hexdigest(self) -> str:
        return self.digest.hexdigest()
