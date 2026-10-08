"""Hàm dựng dữ liệu giả dùng chung cho các test."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from runtime import Candle, Pair
from schedule import release_time


def pair(symbol: str = "GOLD", timeframe: str = "M5", minutes: int = 5, symbol_id: int = 56, timeframe_id: int = 1) -> Pair:
    return Pair(symbol, symbol_id, timeframe, timeframe_id, minutes)


M5 = pair()
M15 = pair(timeframe="M15", minutes=15, timeframe_id=3)


def at(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M")


def candle(p: Pair, open_time: str, price: str = "100.00") -> Candle:
    value = Decimal(price)
    return Candle(p, at(open_time), value, value, value, value)


def series(p: Pair, first_open: str, count: int, price: str = "100.00") -> list[Candle]:
    """`count` nến liên tiếp của một pair, mỗi nến cách nhau đúng độ dài khung."""
    start = at(first_open)
    return [candle(p, (start + timedelta(minutes=p.minutes * i)).strftime("%Y-%m-%d %H:%M"), price) for i in range(count)]


def fetch_from(candles: list[Candle]):
    """Nguồn giả cho schedule.iter_ticks: trả nến có giờ phát trong (after, upto]."""
    return lambda after, upto: [c for c in candles if after < release_time(c) <= upto]
