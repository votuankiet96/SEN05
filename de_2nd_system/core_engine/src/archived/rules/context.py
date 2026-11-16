"""Luật CONTEXT: điều kiện CHỈ BÁO của chiến lược, đọc ở KHUNG CONTEXT (bằng hoặc lớn hơn khung trigger).

Tín hiệu của một ô chỉ bắn khi đủ ba thứ cùng đúng: TRIGGER (điều kiện giá ở khung trigger, sau lọc trùng), CONTEXT (file này) và TREND (trend_methods/trend_tf.py). Khung context khai ở
``strategies.<tên>.timeframes_by_symbol`` ("context-trend"); đổi khung đó thì chỉ báo được đọc ở khung mới và tín hiệu đổi theo, không phải sửa mã. Khung context bằng khung trigger thì các
điều kiện dưới đây luôn đúng ở mọi nến mà chiến lược đã bắn (cùng giá trị MACD/MA với chính chiến lược) nên kết quả không đổi.

Chiến lược khai MỖI điều kiện nó cần bằng tên (``StrategySpec.context_checks``); điều kiện lấy các cột CONTEXT đã ghép vào bảng ô bằng luật nến đóng (signal_publisher.assemble_cell_table):

  * ``macd``: MUA khi MACD histogram của nến context > 0, BÁN khi < 0 (``context_macd_h``).
  * ``ma``:   MUA khi giá đóng nến context > MA của khung đó, BÁN khi < (``context_close``, ``context_ma``).

Thiếu dữ liệu (NaN, chưa có nến context nào) thì điều kiện SAI: không bắn. Chiến lược không khai điều kiện nào (ichimoku, không có chỉ báo MACD/MA) thì luôn đúng.

Khi nào xét và áp vào đâu cũng là luật của file này, không phải của chỗ gọi: ``context_gates`` trả hai mặt nạ MUA/BÁN theo từng nến (chiến lược có setup là TRẠNG THÁI xét ở từng nến; chiến lược có setup là SỰ KIỆN ở một nến
xét ở nến MỞ ĐỢT rồi giữ tới hết đợt), ``gate_raw_signal`` giữ tín hiệu thô chỉ ở nến mà mặt nạ cho phép hướng đó. ``signal_publisher.signal_frame`` chỉ gọi hai hàm này.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

import pandas as pd

# Mỗi điều kiện trả hai mặt nạ (đúng cho MUA, đúng cho BÁN) trên bảng ô.
_CHECKS: dict[str, Callable[[pd.DataFrame], tuple[pd.Series, pd.Series]]] = {
    "macd": lambda table: (table["context_macd_h"] > 0, table["context_macd_h"] < 0),
    "ma": lambda table: (table["context_close"] > table["context_ma"], table["context_close"] < table["context_ma"]),
}

CHECK_NAMES: tuple[str, ...] = tuple(_CHECKS)


def parse_checks(value: Iterable[str], *, label: str = "context_checks") -> tuple[str, ...]:
    """Tên điều kiện hợp lệ (``CHECK_NAMES``), không trùng; sai tên là lỗi ngay lúc dựng chiến lược."""
    names = tuple(value)
    unknown = [name for name in names if name not in _CHECKS]
    if unknown or len(set(names)) != len(names):
        raise ValueError(f"{label} must be unique names from {CHECK_NAMES}, got {names}")
    return names


def context_masks(table: pd.DataFrame, checks: Iterable[str]) -> tuple[pd.Series, pd.Series]:
    """Hai mặt nạ bool theo từng nến: (CONTEXT cho phép MUA, CONTEXT cho phép BÁN), là AND của mọi điều kiện ``checks``. ``checks`` rỗng thì cả hai luôn đúng."""
    buy = pd.Series(True, index=table.index)
    sell = pd.Series(True, index=table.index)
    for name in parse_checks(checks):
        buy_ok, sell_ok = _CHECKS[name](table)
        buy &= buy_ok.fillna(False)
        sell &= sell_ok.fillna(False)
    return buy, sell


def context_gates(table: pd.DataFrame, checks: Iterable[str], *, at_run_start: bool) -> tuple[pd.Series, pd.Series]:
    """Hai mặt nạ bool theo từng nến (CONTEXT cho phép MUA, CONTEXT cho phép BÁN) dùng làm cổng cho tín hiệu của ô.

    ``at_run_start`` False (setup là TRẠNG THÁI): xét ở TỪNG nến, đúng ``context_masks``. True (setup là SỰ KIỆN ở một nến, ``StrategySpec.context_at_run_start``): chỉ xét ở nến MỞ ĐỢT (nến có ``raw_signal``
    khác 0) rồi GIỮ kết quả đó tới nến mở đợt kế tiếp; nến trước nến mở đợt đầu tiên chưa thuộc đợt nào nên không được phép bắn. Khi ``at_run_start`` bảng phải có cột ``raw_signal``."""
    buy, sell = context_masks(table, checks)
    if at_run_start:
        opens_run = table["raw_signal"].ne(0)
        buy = buy.where(opens_run).ffill().fillna(False).astype(bool)
        sell = sell.where(opens_run).ffill().fillna(False).astype(bool)
    return buy, sell


def gate_raw_signal(raw_signal: pd.Series, buy: pd.Series, sell: pd.Series) -> pd.Series:
    """Bản THÔ của tín hiệu: giữ ``raw_signal`` (+1 MUA, -1 BÁN) ở nến mà CONTEXT cho phép đúng hướng đó (``buy`` cho MUA, ``sell`` cho BÁN), nến còn lại về 0."""
    return raw_signal.where((raw_signal.eq(1) & buy) | (raw_signal.eq(-1) & sell), 0)
