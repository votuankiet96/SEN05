# =============================================================================
# strategy/reversal_scanner.py  —  Scanner tín hiệu đảo chiều cho Combo v2
# Shared by: 04_reversal_scanner.ipynb, scan_pipeline.py
# =============================================================================
# Điểm khác biệt so với scanner thường:
# - Đang giữ BUY mà gặp SELL signal: đóng BUY và mở SELL.
# - Đang giữ SELL mà gặp BUY signal: đóng SELL và mở BUY.
# - Cột is_reversal đánh dấu tín hiệu được tạo do đảo chiều.
# - outcome có thêm giá trị 'Reversed'.
#
# Data contract:
# - Input  : df dùng RangeIndex, có cột BarTime.
# - Output : DataFrame cùng schema scanner chuẩn + cột is_reversal.
# =============================================================================
import numpy as np
import pandas as pd

from ._scan_shared import (
    build_raw_signal_masks,
    build_signal_record,
    resolve_trade_hit,
)


def scan_signals_reversal(df: pd.DataFrame, cfg: dict, p: dict,
                          us30_uptrend: 'pd.Series | None' = None) -> pd.DataFrame:
    """
    Quét tín hiệu theo cơ chế đảo chiều cho Combo v2.

    Cách hoạt động tổng quát:
    1) Dùng build_raw_signal_masks() để lấy buy_raw/sell_raw.
    2) Duyệt từng bar theo thời gian.
    3) Nếu đang có lệnh: ưu tiên kiểm tra SL/TP trước.
    4) Nếu chưa đóng bởi SL/TP: kiểm tra tín hiệu ngược để đảo chiều.
    5) Dựng bản ghi tín hiệu bằng build_signal_record().

    Parameters
    ----------
    df           : dữ liệu OHLCV + indicator (RangeIndex, có cột BarTime).
    cfg          : config symbol (x, session_hours_utc, label...).
    p            : tham số scanner (KTP, MIN_RR).
    us30_uptrend : Series xu hướng US30 (tuỳ chọn) cho macro filter.

    Returns
    -------
    DataFrame một dòng cho mỗi tín hiệu thô,
    gồm schema chuẩn và thêm cột is_reversal.

    Tác động quản trị:
    - Đổi thứ tự ưu tiên giữa SL/TP/reversal trong hàm này sẽ đổi mạnh kết quả.
    - Đây là lõi nghiên cứu hành vi đảo chiều trên dashboard/notebook.
    """
    x = cfg['x']
    KTP = p['KTP']
    MIN_RR = p['MIN_RR']
    buy_raw, sell_raw, us30_filtered = build_raw_signal_masks(
        df,
        cfg['session_hours_utc'],
        us30_uptrend,
    )

    # ── Quét theo từng bar với luật đảo chiều ───────────────────────────────
    signals         = []
    in_trade        = False
    trade_d         = 0
    trade_sl        = 0.0
    trade_tp        = 0.0
    open_signal_idx = None

    for i, bar in df.iterrows():
        d           = 0
        is_reversal = False

        if in_trade:
            # ── Bước 1: ưu tiên kiểm tra SL/TP trước ──────────────────────
            outcome = resolve_trade_hit(bar, trade_d, trade_sl, trade_tp)
            if outcome is not None:
                in_trade = False
                if open_signal_idx is not None:
                    signals[open_signal_idx]['outcome'] = outcome
                    open_signal_idx = None
                continue

            # ── Bước 2: nếu chưa hit SL/TP thì xét tín hiệu đảo chiều ─────
            if buy_raw.iloc[i] and trade_d == -1:
                d           = 1
                is_reversal = True
                in_trade    = False
                if open_signal_idx is not None:
                    signals[open_signal_idx]['outcome'] = 'Reversed'
                    open_signal_idx = None
            elif sell_raw.iloc[i] and trade_d == 1:
                d           = -1
                is_reversal = True
                in_trade    = False
                if open_signal_idx is not None:
                    signals[open_signal_idx]['outcome'] = 'Reversed'
                    open_signal_idx = None
            else:
                continue
        else:
            # ── Không có lệnh mở: tìm tín hiệu mới ────────────────────────
            d = 1 if buy_raw.iloc[i] else (-1 if sell_raw.iloc[i] else 0)
            if d == 0:
                continue

        # ── Tính toán hình học lệnh và metadata tín hiệu ─────────────────
        record = build_signal_record(
            bar,
            d,
            x,
            KTP,
            MIN_RR,
            us30_filtered=us30_filtered,
            extra={'is_reversal': is_reversal},
        )
        passes = record['pass_rr']

        if passes:
            in_trade        = True
            trade_d         = d
            trade_sl        = record['sl']
            trade_tp        = record['tp']
            open_signal_idx = len(signals)

        signals.append(record)

    return pd.DataFrame(signals)
