import numpy as np
import pandas as pd

"""
Các hàm dùng chung cho scanner.

Mục tiêu:
- Gom các phép tính lõi (mask tín hiệu, hit SL/TP, record tín hiệu)
    vào một nơi để tránh lệch logic giữa các chế độ scan.

Tác động quản trị:
- Nếu thay đổi điều kiện tín hiệu ở đây, cả scanner thường và scanner đảo chiều
    sẽ đổi kết quả cùng lúc.
"""


def build_raw_signal_masks(
    df: pd.DataFrame,
    hours_utc: list,
    us30_uptrend: 'pd.Series | None' = None,
) -> tuple[pd.Series, pd.Series, bool]:
    """
    Tạo mask BUY/SELL thô dùng chung cho mọi scanner.

    Luật chính:
    - Lọc theo session giờ nếu có.
    - Chỉ xét bar có đủ ma/macd_h/atr (không NaN).
    - BUY khi có giao cắt theo hướng tăng + nến tăng + MACD dương.
    - SELL khi có giao cắt theo hướng giảm + nến giảm + MACD âm.

    us30_uptrend:
    - Nếu truyền vào, BUY chỉ giữ khi US30 đang uptrend.
    - SELL chỉ giữ khi US30 không uptrend.

    Trả về:
    - buy_raw, sell_raw, us30_filtered.
    """
    if hours_utc:
        sess = df['BarTime'].dt.hour.isin(hours_utc)
    else:
        sess = pd.Series(True, index=df.index)

    valid = sess & df['ma'].notna() & df['macd_h'].notna() & df['atr'].notna()
    prev_close = df['close'].shift(1)
    prev_ma    = df['ma'].shift(1)        # MA cùng thời điểm với nến trước — tránh look-ahead
    bull_candle = df['close'] > df['open']
    bear_candle = df['close'] < df['open']
    above_ma = df['close'] > df['ma']

    buy_raw  = valid & (prev_close <= prev_ma) & bull_candle & above_ma & (df['macd_h'] > 0)
    sell_raw = valid & (prev_close >= prev_ma) & bear_candle & (~above_ma) & (df['macd_h'] < 0)

    us30_filtered = us30_uptrend is not None
    if us30_filtered:
        us30_times = us30_uptrend.index.to_numpy()
        us30_values = us30_uptrend.values
        bar_times = df['BarTime'].to_numpy()
        positions = np.searchsorted(us30_times, bar_times, side='right') - 1
        aligned = pd.Series(
            np.where(positions >= 0, us30_values[positions.clip(min=0)], False),
            index=df.index,
            dtype=bool,
        )
        buy_raw = buy_raw & aligned
        sell_raw = sell_raw & (~aligned)

    return buy_raw, sell_raw, us30_filtered


def resolve_trade_hit(
    bar: pd.Series,
    direction: int,
    trade_sl: float,
    trade_tp: float,
) -> str | None:
    """
    Xác định trên bar hiện tại có chạm SL/TP không.

    Quy tắc ưu tiên:
    - Nếu cùng bar chạm cả SL và TP thì trả về SL để mô phỏng bảo thủ.

    Trả về:
    - 'TP', 'SL', hoặc None.
    """
    if direction == 1:
        hit_sl = bar['low'] <= trade_sl
        hit_tp = bar['high'] >= trade_tp
    else:
        hit_sl = bar['high'] >= trade_sl
        hit_tp = bar['low'] <= trade_tp

    if not (hit_sl or hit_tp):
        return None
    return 'TP' if (hit_tp and not hit_sl) else 'SL'


def build_signal_record(
    bar: pd.Series,
    direction: int,
    x: float,
    ktp: float,
    min_rr: float,
    *,
    us30_filtered: bool,
    extra: dict | None = None,
) -> dict:
    """
    Dựng một dòng kết quả tín hiệu với công thức chuẩn hóa.

    Hàm này tính nhất quán các trường:
    - entry, sl, tp
    - sl_dist, tp_dist, rr
    - pass_rr, outcome mặc định, cờ us30_filtered

    Tác động quản trị:
    - Mọi thay đổi công thức ở đây sẽ ảnh hưởng trực tiếp tín hiệu hiển thị,
      cảnh báo, và thống kê scan.
    """
    sl_dist = float(bar['high']) - float(bar['low']) + 2 * float(x)
    tp_dist = ktp * float(bar['atr'])
    rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0
    entry = float(bar['high']) + float(x) if direction == 1 else float(bar['low']) - float(x)
    sl = float(bar['low']) - float(x) if direction == 1 else float(bar['high']) + float(x)
    tp = entry + direction * ktp * float(bar['atr'])
    passes = rr >= min_rr

    record = {
        'bar_time': bar['BarTime'],
        'direction': 'BUY' if direction == 1 else 'SELL',
        'direction_int': direction,
        'outcome': 'Open' if passes else '—',
        'high': bar['high'],
        'low': bar['low'],
        'entry': round(entry, 4),
        'sl': round(sl, 4),
        'tp': round(tp, 4),
        'sl_dist': round(sl_dist, 4),
        'tp_dist': round(tp_dist, 4),
        'atr': round(bar['atr'], 4),
        'ma': round(bar['ma'], 4),
        'macd_h': round(bar['macd_h'], 6),
        'rr': rr,
        'pass_rr': passes,
        'us30_filtered': us30_filtered,
    }
    if extra:
        record.update(extra)
    return record


def build_pending_order(bar, direction: int, x: float, ktp: float,
                        atr: float, ttl: int) -> dict:
    """Tạo cấu trúc pending order dùng chung cho backtest đầy đủ và backtest nhanh.

    Parameters
    ----------
    bar       : dòng dữ liệu chứa high/low.
    direction : 1 (BUY) hoặc -1 (SELL).
    x         : breakout buffer theo point.
    ktp       : hệ số TP theo ATR.
    atr       : ATR hiện tại.
    ttl       : thời hạn pending tính theo số bar.

    Returns
    -------
    dict gồm: direction, entry, sl, tp, atr, ttl.

    Lưu ý quản trị:
    - Đây là single-source cho công thức pending order.
      Nếu cần đổi cách đặt entry/SL/TP, sửa duy nhất tại đây.
    """
    entry = float(bar['high']) + x if direction == 1 else float(bar['low']) - x
    sl    = float(bar['low'])  - x if direction == 1 else float(bar['high']) + x
    tp    = entry + direction * ktp * atr
    return dict(direction=direction, entry=entry, sl=sl, tp=tp,
                atr=atr, ttl=ttl)
