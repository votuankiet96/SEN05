"""
Lọc lặp cùng chiều dùng chung cho các chiến lược entry (combo, macd_break, ma_cross).

Module này không tự tạo tín hiệu vào lệnh. Nó nhận 2 mask setup (BUY/SELL) từ
một strategy entry và, nếu người vận hành bật cờ `alternating_filter_enabled`
trong config, chỉ giữ setup ĐẦU TIÊN mỗi khi hướng đổi chiều: nhiều bar liên
tiếp cùng thỏa BUY thì chỉ bar BUY đầu tiên bắn signal = 1, các BUY sau bị giữ
0 cho tới khi xuất hiện một setup SELL (và ngược lại). Bar không có setup không
đổi trạng thái.

Cờ tắt thì bar nào có setup cũng bắn (hành vi trước khi có bộ lọc). Cờ nằm ở
`strategies.<tên>.alternating_filter_enabled` trong de_config.yaml, được
configuration.py đọc thành params["ALTERNATING_FILTER_ENABLED"].

Ba điều cần biết:
    - Bộ lọc chỉ nhớ HƯỚNG CỦA SETUP GẦN NHẤT, không biết lệnh thật còn mở hay
      đã dính SL (hệ này không theo dõi lệnh thật): sau khi lệnh cũ đã dừng lỗ
      mà setup cùng chiều xuất hiện lại thì vẫn bị chặn cho tới khi có setup
      ngược chiều.
    - Bộ lọc này chạy TRƯỚC lọc trend (signal_publisher.apply_trend_filter, ở khâu
      publisher): raw_signal đã được lọc xen kẽ rồi publisher mới lọc trend, nên
      tín hiệu lọc ⊆ raw_signal (trừ phần dời); hệ quả là một setup bị trend loại
      vẫn chiếm "lượt đầu tiên" của hướng đó.
    - Trạng thái chỉ nhớ trong cửa sổ dữ liệu đang tính: bar có setup đầu tiên
      của cửa sổ luôn bắn. Mỗi sự kiện DE đọc 500 nến; chỉ báo chưa hợp lệ ở đầu
      cửa sổ nên "trí nhớ" thực là khoảng 481 nến (combo), 467 (ma_cross), 400
      (macd_break, mất LOOKBACK_BARS = 100 nến đầu). Bộ lọc nhìn mọi setup THÔ nên
      cần nhớ khoảng giữa hai setup thô liên tiếp cùng chiều: đo 2026-10-16 trên
      we_core (11 symbol, từ 2022) lớn nhất 150 (combo), 72 (ma_cross), 288
      (macd_break) — đều nằm gọn trong cửa sổ 500 của DE.

"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core_engine.src.rules.shift import allowed_masks


def alternating_filter_enabled(params: dict | None) -> bool:
    """
    Trả về trạng thái bật/tắt lọc lặp cùng chiều từ params đã normalize.

    Thiếu key thì coi là tắt.
    """
    if not params:
        return False
    value = params.get("ALTERNATING_FILTER_ENABLED", False)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def alternating_signals(buy_setup: pd.Series, sell_setup: pd.Series) -> pd.Series:
    """
    Chỉ phát tín hiệu đầu tiên khi trạng thái đổi chiều.

    Ví dụ:
        Nếu nhiều bar liên tiếp đều thỏa BUY, chỉ bar BUY đầu tiên được bắn
        signal = 1. Các bar BUY sau đó bị giữ signal = 0 cho tới khi xuất
        hiện một setup SELL. Cơ chế này giúp tránh spam nhiều tín hiệu cùng
        chiều trong một pha thị trường.
    """
    buy_flags = buy_setup.fillna(False).astype(bool).tolist()
    sell_flags = sell_setup.fillna(False).astype(bool).tolist()
    state = 0
    values: list[int] = []
    for is_buy, is_sell in zip(buy_flags, sell_flags, strict=True):
        signal = 0
        if is_buy and state != 1:
            signal = 1
            state = 1
        elif is_sell and state != -1:
            signal = -1
            state = -1
        values.append(signal)
    return pd.Series(values, index=buy_setup.index, dtype="int64")


def apply_alternating_filter(
    buy_setup: pd.Series,
    sell_setup: pd.Series,
    params: dict | None,
) -> pd.Series:
    """
    Tạo raw_signal (+1 BUY, -1 SELL, 0 không có) từ 2 mask setup.

    Cờ bật: lọc lặp cùng chiều (alternating_signals).
    Cờ tắt: bar nào có setup cũng bắn.
    """
    if alternating_filter_enabled(params):
        return alternating_signals(buy_setup, sell_setup)
    buy = buy_setup.fillna(False).astype(bool).to_numpy()
    sell = sell_setup.fillna(False).astype(bool).to_numpy()
    values = np.where(buy, 1, np.where(sell, -1, 0))
    return pd.Series(values, index=buy_setup.index, dtype="int64")


def apply_signal_filters(
    df: pd.DataFrame,
    params: dict | None,
    *,
    buy_setup: pd.Series,
    sell_setup: pd.Series,
    buy_reason: str,
    sell_reason: str,
    hold_valid: pd.Series | None = None,
) -> pd.DataFrame:
    """
    Phần việc chung của mọi chiến lược entry: từ hai mask setup ra tín hiệu thô, câu lý do và hai mặt nạ "nến được phép bắn". Chiến lược chỉ truyền setup và câu lý do của mình.

    Lọc lặp cùng chiều (``apply_alternating_filter``, cờ trong config) chạy trên MỌI setup và cho ``raw_signal``, đúng cột TRIGGER. Lọc theo trend và context KHÔNG nằm ở đây: publisher làm ở
    ``signal_publisher.signal_frame``, chạy sau trên chính tín hiệu thô này.

    Args:
        df: khung của chiến lược (đã có nến + chỉ báo; không bị sửa tại chỗ).
        params: tham số đã chuẩn hoá (ALTERNATING_FILTER_ENABLED...).
        buy_setup, sell_setup: hai mask setup BUY/SELL của chiến lược, đã gồm điều kiện hợp lệ (chỉ báo đủ dữ liệu...).
        buy_reason, sell_reason: câu lý do ghi vào signal_reason ở nến có tín hiệu thô.
        hold_valid: None = setup là TRẠNG THÁI (còn đúng ở nhiều nến liền nhau, như ma_cross và macd_break): nến được phép bắn là chính nến còn setup. Có giá trị = setup là SỰ KIỆN ở đúng một
            nến (như combo, nến cắt MA) nên sau lúc trend đổi không còn nến setup nào: tín hiệu thô bị trend loại được GIỮ tới hết đợt (tới khi có setup ngược) và được phép bắn ở mọi nến mà
            hold_valid đúng (chỉ báo đủ dữ liệu), không giới hạn tuổi (người dùng chốt 2026-10-22). Chỉ có tác dụng với khối có chính sách first_aligned_per_run.

    Returns:
        Bản sao df với các cột raw_signal, signal_reason, buy_mask, sell_mask (theo thứ tự này). strategy_runner ghi hai mặt nạ vào TRIGGER (cột setup) để DE dời đúng mà không phải chạy lại chiến lược.
    """
    raw_signal = apply_alternating_filter(buy_setup, sell_setup, params)
    out = df.assign(raw_signal=raw_signal)

    buy_mask, sell_mask = allowed_masks(raw_signal, buy_setup, sell_setup, hold_valid)
    out["signal_reason"] = ""
    out.loc[raw_signal.eq(1), "signal_reason"] = buy_reason
    out.loc[raw_signal.eq(-1), "signal_reason"] = sell_reason
    out["buy_mask"] = pd.Series(buy_mask, index=out.index).fillna(False).astype(bool)
    out["sell_mask"] = pd.Series(sell_mask, index=out.index).fillna(False).astype(bool)
    return out
