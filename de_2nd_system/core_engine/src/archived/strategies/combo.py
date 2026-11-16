"""
Chiến lược Combo — CHỈ logic quyết định tín hiệu BUY/SELL.

Mô tả:
    Combo V0 = MA + MACD Histogram + ATR breakout. File này có đúng 1 việc:
    phát hiện tín hiệu BUY/SELL (detect_combo_signals) — quyết định KHI NÀO
    và HƯỚNG NÀO, không quyết định vào giá bao nhiêu.

    Không giữ tham số mặc định, không tự merge/validate tham số, không tính
    chỉ báo, và KHÔNG tính entry/level:
    - Tham số (MA_PERIOD...) sống ở
      configuration.py (normalize_combo_params) — hàm ở đây luôn nhận
      `params` là dict ĐÃ HOÀN CHỈNH, không tự suy ra default nào.
    - Chỉ báo (ma/macd_h/atr) tính ở indicator.py (add_combo_indicators),
      chạy trước detect_combo_signals() trong pipeline (xem
      configuration.py: run_strategy()).
    - Entry/SL/TP (add_combo_entries) — câu hỏi "vào giá nào" tách riêng ở
      entries.py, chạy SAU detect_combo_signals() trong cùng pipeline.
      Không phải việc của file này.

Đầu ra:
    detect_combo_signals(df, symbol, params): df + cột raw_signal (tín hiệu thô sau lọc lặp cùng chiều),
    signal_reason, buy_mask và sell_mask. Lọc theo trend và context là việc của signal_publisher.
"""

from __future__ import annotations

import pandas as pd
from core_engine.src.rules.alternating import apply_signal_filters

# signal_reason -> field `comment` của HASH trên Redis db đầu ra, OF đọc nguyên văn. Đổi chuỗi ở đây là đổi
# dữ liệu OF nhận được.
_BUY_REASON = "bullish candle crossing above MA, MACD histogram > 0"
_SELL_REASON = "bearish candle crossing below MA, MACD histogram < 0"


def detect_combo_signals(
    df: pd.DataFrame,
    symbol: str | None = None,
    params: dict | None = None,
) -> pd.DataFrame:
    """
    Phát hiện tín hiệu BUY/SELL theo logic chiến lược Combo.

    Điều kiện BUY (signal = +1):
        1. close > open                           ← Nến tăng (bullish candle)
        2. close > ma                             ← Close nằm trên MA20
        3. prev_close < ma                        ← Bar trước đóng cửa dưới MA (xác nhận vừa cắt lên)
        4. macd_h > 0                             ← MACD histogram dương
        5. tín hiệu gần nhất trước đó (nếu có) không phải BUY  ← lọc lặp cùng chiều (khi cờ bật)

    Điều kiện SELL (signal = -1): ngược lại với BUY (điều kiện 5: tín hiệu gần
    nhất trước đó không phải SELL).

    Lọc lặp cùng chiều (alternating.py; khôi phục 2026-10-11 theo yêu
    cầu người dùng, cho cả WE lẫn DE; từ 2026-10-14 bật/tắt bằng cờ
    `strategies.combo.alternating_filter_enabled` trong de_config.yaml, mặc định
    bật): chỉ bắn tín hiệu ĐẦU TIÊN khi hướng đổi chiều. Nhiều bar liên tiếp cùng
    thỏa BUY thì chỉ bar BUY đầu tiên bắn signal = 1; các BUY sau bị giữ 0 cho tới
    khi xuất hiện một setup SELL (và ngược lại). Cơ chế này có từ 2026-08-26
    (commit 50a2643), bị bỏ 2026-09-11 (commit c74ffa9) vì hệ này không theo dõi
    lệnh thật nên giả định "còn đang ở trạng thái BUY tới khi có SELL" có thể bỏ
    lỡ tín hiệu thật sau khi lệnh cũ đã bị dừng lỗ; nay dựng lại để không còn
    nhiều tín hiệu cùng chiều. OF vẫn là nơi quyết định vào lệnh ở tín hiệu nào.
    Cờ tắt thì bar nào có setup cũng bắn.

    Lọc lặp cùng chiều do rules/alternating.apply_signal_filters làm; chiến lược chỉ truyền setup và câu lý do của mình.
    Riêng combo, setup là SỰ KIỆN (nến cắt MA, một nến) nên truyền hold_valid=valid: hold_valid chỉ quyết định mặt nạ
    "nến được phép bắn" (cột setup ở SETUP). Với khối trend có chính sách first_aligned_per_run (breakout, tstat, cloud)
    publisher dùng mặt nạ đó để bắn dời tín hiệu thô bị trend loại ngay nến trend đổi đúng chiều, không cần nến setup mới
    và không giới hạn tuổi. Các điều cần biết khác (một setup bị trend loại vẫn chiếm "lượt đầu"; trạng thái chỉ nhớ
    trong cửa sổ đang tính, ở DE là 500 nến mỗi sự kiện) nằm ở docstring của alternating.py.

    Điều kiện tiên quyết (valid mask):
        - Tất cả chỉ báo không phải NaN (qua warmup period, kể cả prev_close).

    Args:
        df: DataFrame đã qua indicator.add_combo_indicators().
        symbol: Không dùng trực tiếp — symbol params đã merged vào params.
        params: Dict tham số đã validate đầy đủ (từ
            configuration.normalize_combo_params).

    Returns:
        Bản sao df với các cột bổ sung: raw_signal, signal_reason, buy_mask, sell_mask.
    """
    _ = symbol
    out = df.copy()

    # Mask "valid" đảm bảo tất cả chỉ báo đã qua warmup
    valid = (
        out["ma"].notna()
        & out["macd_h"].notna()
        & out["open"].notna()
        & out["close"].notna()
        & out["prev_close"].notna()
    )

    # =========================================================================
    # QUY TẮC CHIẾN LƯỢC COMBO — chỉ sửa 2 khối điều kiện dưới đây khi đổi ý
    # tưởng chiến lược. Ngoài chúng chỉ còn lọc lặp cùng chiều
    # (alternating.py, cờ trong config) quyết định "khi nào mua/bán" —
    # tham số và entry/level đều ở file khác (xem docstring đầu file).
    # =========================================================================
    # Combo V0 BUY setup: bullish candle vừa cắt lên MA20 (prev_close dưới MA,
    # close hiện tại trên MA — xác nhận động lực tăng, không phải đã ở trên
    # MA từ trước), MACD histogram dương.
    buy_cond = (
        valid
        & (out["close"] > out["open"])
        & (out["close"] > out["ma"])
        & (out["prev_close"] < out["ma"])
        & (out["macd_h"] > 0)
    )
    # Combo V0 SELL setup: đối xứng — bearish candle vừa cắt xuống MA20.
    sell_cond = (
        valid
        & (out["close"] < out["open"])
        & (out["close"] < out["ma"])
        & (out["prev_close"] > out["ma"])
        & (out["macd_h"] < 0)
    )
    # =========================================================================

    # Setup của combo là SỰ KIỆN (nến cắt MA, chỉ ở một nến): sau lúc trend đổi giá thường nằm hẳn một phía MA nên không còn
    # nến setup nào để bắn (đo 2026-10-22: 2.460 đợt, +18% tín hiệu bị mất). Vì vậy truyền hold_valid=valid: tín hiệu thô bị
    # trend loại được GIỮ tới hết đợt (tới khi có setup ngược) và bắn ngay nến trend đổi đúng chiều, không giới hạn tuổi
    # (người dùng chốt); valid vẫn áp (chỉ báo đủ).
    return apply_signal_filters(
        out,
        params,
        buy_setup=buy_cond,
        sell_setup=sell_cond,
        buy_reason=_BUY_REASON,
        sell_reason=_SELL_REASON,
        hold_valid=valid,
    )
