"""
Lọc lặp cùng chiều dùng chung cho các chiến lược entry (combo, macd_break, ma_cross từ 2026-10-16).

Module này không tự tạo tín hiệu vào lệnh. Nó nhận 2 mask setup (BUY/SELL) từ
một strategy entry và, nếu người vận hành bật cờ `alternating_filter_enabled`
trong config, chỉ giữ setup ĐẦU TIÊN mỗi khi hướng đổi chiều: nhiều bar liên
tiếp cùng thỏa BUY thì chỉ bar BUY đầu tiên bắn signal = 1, các BUY sau bị giữ
0 cho tới khi xuất hiện một setup SELL (và ngược lại). Bar không có setup không
đổi trạng thái.

Cờ tắt thì bar nào có setup cũng bắn (hành vi trước khi có bộ lọc). Cờ nằm ở
`strategies.<tên>.alternating_filter_enabled` trong we_config.yaml, được
configuration.py đọc thành params["ALTERNATING_FILTER_ENABLED"].

Ba điều cần biết:
    - Bộ lọc chỉ nhớ HƯỚNG CỦA SETUP GẦN NHẤT, không biết lệnh thật còn mở hay
      đã dính SL (hệ này không theo dõi lệnh thật): sau khi lệnh cũ đã dừng lỗ
      mà setup cùng chiều xuất hiện lại thì vẫn bị chặn cho tới khi có setup
      ngược chiều.
    - rules/trend_tf.apply_signal_filters gọi bộ lọc này TRƯỚC trend filter (đổi
      lại 2026-10-16 về thứ tự có từ trước 2026-10-15): raw_signal được lọc xen
      kẽ trên mọi setup rồi mới lọc trend. Hệ quả: một setup bị trend loại vẫn
      chiếm "lượt đầu tiên" của hướng đó, nên DB1 có thể có hai tín hiệu liền
      nhau cùng chiều (giữa chúng có setup ngược chiều bị trend loại). Khi trend
      tắt, signal == raw_signal. "Đợt" của chính sách first_aligned_per_run
      (rules/trend_tf.POLICY, hiện breakout và tstat) = khoảng giữa hai lần bộ
      lọc này bắn; chính sách đó quyết định một đợt có nến đầu lệch trend thì mất
      cả đợt hay vẫn bắn ở nến setup đầu tiên trend đã cùng chiều, và signal có
      còn ⊆ raw_signal hay không (xem docstring apply_signal_filters).
    - Trạng thái chỉ nhớ trong cửa sổ dữ liệu đang tính: bar có setup đầu tiên
      của cửa sổ luôn bắn. Cửa sổ làm mới của worker có 300 nến warm-up, nhưng
      chỉ phần warm-up mà chỉ báo đã hợp lệ mới "nhớ" được setup: combo nhớ 281
      nến, ma_cross 267, macd_break chỉ 200 (mất LOOKBACK_BARS = 100 nến đầu).
      Bộ lọc nhìn mọi setup THÔ nên "trí nhớ" cần là khoảng giữa hai setup thô
      liên tiếp cùng chiều: đo 2026-10-16 (11 symbol, từ 2022) lớn nhất 150
      nến (combo), 72 (ma_cross), 288 (macd_break). Chỉ macd_break có 34
      khoảng dài hơn 200 nến: một lần làm mới delta rơi đúng setup sau khoảng
      đó sẽ bắn tín hiệu mà tính lại từ đầu sẽ chặn (kiểm bằng cửa sổ 1 nến:
      lệch từ khoảng 201 nến, khớp ở ≤ 200), tín hiệu thừa nằm lại tới lần
      restart worker kế (xem CLAUDE.md mục 6).

Bản sao nguyên văn nằm ở des/src/rules/alternating.py (des là hệ con tách
biệt, không import we_core); sửa một bên phải sửa cả bên kia.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def alternating_filter_enabled(params: dict | None) -> bool:
    """
    Trả về trạng thái bật/tắt lọc lặp cùng chiều từ params đã normalize.

    Thiếu key thì coi là tắt (cùng quy ước với trend_filter_enabled).
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
