"""
Dời tín hiệu: thuật toán của chính sách first_aligned_per_run (rules/trend_tf.POLICY), tách riêng khỏi bộ điều phối.

Bộ lọc trend thường chỉ xét tín hiệu đúng ở nến chiến lược phát ra (raw_signal): nến đó lệch trend thì tín hiệu mất, và vì
bộ lọc lặp cùng chiều (rules/alternating.py) chỉ cho bắn nến đầu của mỗi "đợt" nên nến đầu bị loại là mất cả đợt. Dời tín hiệu
giữ tín hiệu bị loại lại và bắn nó ở nến ĐẦU TIÊN trong đợt mà trend đã cùng chiều, nên tín hiệu đầu tiên sau khi trend xác nhận
không bị mất.

    Đợt = khoảng từ một nến có raw_signal != 0 (bộ lọc xen kẽ vừa bắn, hướng đổi chiều) tới ngay trước nến có raw_signal != 0
    kế tiếp. Mỗi đợt bắn TỐI ĐA một tín hiệu.

Hai bước, đều chỉ dùng numpy/pandas:
    - allowed_masks: nến nào trong đợt được phép bắn. Setup là TRẠNG THÁI (ma_cross, macd_break; hold_valid=None): chính các
      nến còn setup. Setup là SỰ KIỆN ở một nến (combo): tín hiệu thô bị loại được GIỮ tới hết đợt nên mọi nến mà hold_valid đúng
      (chỉ báo đủ dữ liệu, đúng giờ session) đều được phép, không giới hạn tuổi (người dùng chốt 2026-10-22).
    - first_aligned_signal: trong mỗi đợt chọn nến được phép đầu tiên có trend_bias cùng chiều.

File này KHÔNG quyết khối trend nào dời: việc đó là bảng POLICY ở rules/trend_tf.py (hiện breakout và tstat), nơi gọi hai hàm này.
Phụ thuộc: chỉ numpy, pandas (tầng đáy, không import gì trong src/).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def allowed_masks(
    raw_signal: pd.Series,
    buy_setup: pd.Series,
    sell_setup: pd.Series,
    hold_valid: pd.Series | None = None,
) -> tuple[pd.Series, pd.Series]:
    """
    Hai mask BUY/SELL "nến được phép bắn" cho first_aligned_signal.

    hold_valid=None: setup là TRẠNG THÁI nên mask chính là buy_setup/sell_setup. Có hold_valid: setup là SỰ KIỆN ở đúng một
    nến, nên mask là hướng của đợt đang giữ (raw_signal điền tiếp tới nến có raw_signal kế tiếp) và hold_valid đúng.
    """
    if hold_valid is None:
        return buy_setup, sell_setup
    held = raw_signal.where(raw_signal.ne(0)).ffill()
    return held.eq(1) & hold_valid, held.eq(-1) & hold_valid


def first_aligned_signal(
    raw_signal: pd.Series,
    trend_bias: pd.Series,
    buy_setup: pd.Series,
    sell_setup: pd.Series,
) -> pd.Series:
    """
    Mỗi đợt setup bắn đúng MỘT tín hiệu, ở nến setup ĐẦU TIÊN mà trend_bias đã cùng chiều.

    Đợt = khoảng từ một nến có raw_signal != 0 (bộ lọc xen kẽ vừa bắn, hướng đổi chiều) tới ngay trước nến có
    raw_signal != 0 kế tiếp; mọi nến có mask trong đợt cùng chiều với nến mở đợt (mask do allowed_masks quyết
    định: chính setup nếu setup là trạng thái, hoặc hướng của đợt đang giữ nếu setup là sự kiện). Nến đầu của đợt cùng
    chiều trend thì tín hiệu nằm đúng ở nến đó (như cách lọc thường); lệch trend thì tín hiệu dời tới nến setup đầu tiên
    trend đúng chiều, hoặc không có nếu trend không đúng chiều lúc nào trong đợt. Cờ lọc xen kẽ tắt thì mọi setup đều có
    raw_signal != 0, mỗi đợt chỉ một nến và kết quả == "setup cùng chiều trend". Tính theo vị trí (numpy), không
    phụ thuộc index.
    """
    buy = pd.Series(buy_setup).fillna(False).astype(bool).to_numpy()
    sell = pd.Series(sell_setup).fillna(False).astype(bool).to_numpy()
    setup = np.where(buy, 1, np.where(sell, -1, 0))
    aligned = (setup != 0) & (setup == trend_bias.to_numpy())
    run_id = (raw_signal.to_numpy() != 0).cumsum()
    position_in_run = pd.Series(aligned.astype(int)).groupby(run_id).cumsum().to_numpy()
    return pd.Series(np.where(aligned & (position_in_run == 1), setup, 0), index=raw_signal.index, dtype="int64")
