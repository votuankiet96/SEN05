"""
Sinh signal_id xác định (idempotent) cho một tín hiệu chiến lược.

Mô tả:
    signal_id là hash SHA-256 (rút gọn) từ nội dung xác định tín hiệu, KHÔNG
    phải số ngẫu nhiên hay timestamp phát sinh — nên gửi lại/trùng lặp (retry
    outbox, cả candle_snapshot_consumer lẫn safety_net_poller cùng thấy 1
    tín hiệu) luôn cho ra cùng một signal_id, cho phép state.py dedup chính xác.
"""

from __future__ import annotations

import hashlib

import pandas as pd


def build_signal_id(strategy: str, symbol: str, tf: str, bar_time: object, direction: int) -> str:
    """
    Args:
        strategy: Mã chiến lược (vd. "combo").
        symbol: Mã TradingView (vd. "US30").
        tf: Mã khung thời gian (vd. "H1").
        bar_time: Timestamp của bar phát sinh tín hiệu (bất kỳ dạng
            pandas.Timestamp có thể parse được).
        direction: 1 (BUY) hoặc -1 (SELL).

    Returns:
        Chuỗi hex 24 ký tự, xác định duy nhất bởi (strategy, symbol, tf,
        bar_time, direction).
    """
    bar_time_key = pd.Timestamp(bar_time).isoformat()
    raw = f"{strategy}|{symbol}|{tf}|{bar_time_key}|{int(direction)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
