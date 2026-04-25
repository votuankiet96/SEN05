# =============================================================================
# strategies/combo/logic.py  —  Signal logic riêng cho Combo v2
# =============================================================================
"""Toàn bộ logic phát hiện tín hiệu của chiến lược Combo v2.

Architecture role:
- Đây là nơi duy nhất chứa luật tín hiệu của Combo.
- Scanner, symbol backtest, optimizer và walk-forward đều phải gọi lại từ đây.
- Nếu sửa điều kiện BUY/SELL ở đây, toàn bộ pipeline nghiên cứu sẽ đổi theo.

Public API:
    add_combo_indicators(df, params)       → DataFrame với ma, macd_h, atr, prev_close, prev_ma
    detect_combo_signals(df, mask, ...)    → DataFrame với cột signal (1/-1/0)
    session_mask(df, hours_utc)            → Series[bool]
    build_raw_signal_masks(df, ...)        → (buy_raw, sell_raw, us30_filtered)
    resolve_trade_hit(bar, d, sl, tp)      → 'TP' | 'SL' | None
    build_signal_record(bar, d, ...)       → dict tín hiệu chuẩn
    scan_signals_reversal(df, cfg, p, ...) → DataFrame tín hiệu đảo chiều
"""
import numpy as np
import pandas as pd

from modules.indicators import add_indicators as _add_base_indicators

from .config import (
    STRATEGY,
    SYMBOLS,
    get_indicator_params,
    get_symbol_ktp,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. INDICATOR SETUP
# ─────────────────────────────────────────────────────────────────────────────

def add_combo_indicators(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Thêm chỉ báo chiến lược Combo + cột phụ trợ cho backtest.

    - Gọi modules.indicators.add_indicators() để tạo ma, macd_h, atr.
    - Tạo thêm prev_close, prev_ma để phát hiện crossover đúng quy tắc.
    - Tương thích cả DataFrame có DateTimeIndex và dạng thường.

    Architectural impact
    --------------------
    - Thay đổi cột sinh ra ở đây sẽ ảnh hưởng scanner, optimizer và execution.
    - `prev_close` / `prev_ma` là contract bắt buộc cho luật crossover hiện tại.
    """
    defaults = get_indicator_params()
    full_p   = {**defaults, **params} if isinstance(params, dict) else defaults

    has_dt_index = isinstance(df.index, pd.DatetimeIndex)
    if has_dt_index:
        df_flat = df.reset_index()
    else:
        df_flat = df

    df_out = _add_base_indicators(df_flat, full_p)

    if has_dt_index:
        df_out = df_out.set_index('BarTime')

    df_out['prev_close'] = df_out['close'].shift(1)
    df_out['prev_ma']    = df_out['ma'].shift(1)
    return df_out


# Alias để tương thích ngược với code cũ dùng tên add_backtest_indicators
add_backtest_indicators = add_combo_indicators


# ─────────────────────────────────────────────────────────────────────────────
# 2. SESSION MASK
# ─────────────────────────────────────────────────────────────────────────────

def session_mask(df: pd.DataFrame, hours_utc: list) -> pd.Series:
    """
    Tạo mask theo phiên giao dịch (UTC).

    - Nếu hours_utc rỗng: cho phép toàn bộ bar.
    - Nếu có giá trị: chỉ bar có giờ nằm trong danh sách mới được xét.
    """
    if not hours_utc:
        return pd.Series(True, index=df.index)
    return pd.Series(df.index.hour.isin(hours_utc), index=df.index)


# ─────────────────────────────────────────────────────────────────────────────
# 3. SIGNAL DETECTION (vectorized — cho backtest đầy đủ)
# ─────────────────────────────────────────────────────────────────────────────

def detect_combo_signals(df: pd.DataFrame, sess_mask: pd.Series,
                         sym_key: str | None = None,
                         params: dict | None = None) -> pd.DataFrame:
    """
    Phát hiện tín hiệu theo kiểu vectorized cho backtest đầy đủ.

    Luật tín hiệu cốt lõi Combo v2:
    - BUY : nến cắt lên MA + nến tăng + MACD histogram dương + RR đạt ngưỡng.
    - SELL: nến cắt xuống MA + nến giảm + MACD histogram âm + RR đạt ngưỡng.

    Parameters
    ----------
    df        : dữ liệu có index thời gian + cột chỉ báo.
    sess_mask : mask từ session_mask().
    sym_key   : mã symbol để lấy x/ktp đúng theo cấu hình SYMBOLS.
    params    : tham số chỉ báo và ngưỡng RR. Có thể chứa key 'X' để
                override breakout buffer (dùng trong walk-forward optimizer).

    Trả về DataFrame đã thêm:
    - signal: 1 (BUY), -1 (SELL), 0 (không có tín hiệu)
    - rr    : tỷ lệ Reward/Risk ước tính cho từng bar
    """
    df = df.copy()
    p  = params or get_indicator_params()

    # x: ưu tiên params['X'] (optimizer override), rồi SYMBOLS config
    if params and 'X' in params:
        x = float(params['X'])
    elif sym_key and sym_key in SYMBOLS:
        x = SYMBOLS[sym_key]['x']
    else:
        x = 0

    ktp    = get_symbol_ktp(sym_key) if sym_key else p.get('KTP', STRATEGY['ktp'])
    min_rr = p.get('MIN_RR', STRATEGY['min_rr'])

    valid = (sess_mask & df.get('in_window', pd.Series(True, index=df.index)) &
             df['ma'].notna() & df['prev_ma'].notna() &
             df['macd_h'].notna() & df['atr'].notna())

    cross_up   = (df['prev_close'] <= df['prev_ma']) & (df['close'] > df['ma'])
    cross_down = (df['prev_close'] >= df['prev_ma']) & ~(df['close'] > df['ma'])

    buy_cond  = valid & cross_up   & (df['close'] > df['open']) & (df['macd_h'] > 0)
    sell_cond = valid & cross_down & (df['close'] < df['open']) & (df['macd_h'] < 0)

    sl_dist = df['high'] - df['low'] + 2 * x
    tp_dist = ktp * df['atr']
    rr      = tp_dist / sl_dist.replace(0, np.nan)
    rr_ok   = rr >= min_rr

    df['signal'] = 0
    df.loc[buy_cond  & rr_ok, 'signal'] =  1
    df.loc[sell_cond & rr_ok, 'signal'] = -1
    df['rr'] = rr.round(2)
    return df


# Alias để tương thích ngược
detect_signals = detect_combo_signals


# ─────────────────────────────────────────────────────────────────────────────
# 4. RAW SIGNAL MASKS (cho scanner)
# ─────────────────────────────────────────────────────────────────────────────

def build_raw_signal_masks(
    df: pd.DataFrame,
    hours_utc: list,
) -> tuple[pd.Series, pd.Series]:
    """
    Tạo mask BUY/SELL thô dùng chung cho mọi scanner.

    Luật:
    - Lọc theo session giờ nếu có.
    - Chỉ xét bar có đủ ma/macd_h/atr (không NaN).
    - BUY khi có giao cắt theo hướng tăng + nến tăng + MACD dương.
    - SELL khi có giao cắt theo hướng giảm + nến giảm + MACD âm.

    Trả về (buy_raw, sell_raw).
    """
    if hours_utc:
        sess = df['BarTime'].dt.hour.isin(hours_utc)
    else:
        sess = pd.Series(True, index=df.index)

    valid = sess & df['ma'].notna() & df['macd_h'].notna() & df['atr'].notna()
    prev_close  = df['close'].shift(1)
    prev_ma     = df['ma'].shift(1)
    bull_candle = df['close'] > df['open']
    bear_candle = df['close'] < df['open']
    above_ma    = df['close'] > df['ma']

    buy_raw  = valid & (prev_close <= prev_ma) & bull_candle & above_ma & (df['macd_h'] > 0)
    sell_raw = valid & (prev_close >= prev_ma) & bear_candle & (~above_ma) & (df['macd_h'] < 0)

    return buy_raw, sell_raw


# ─────────────────────────────────────────────────────────────────────────────
# 5. TRADE HIT RESOLVER (cho scanner)
# ─────────────────────────────────────────────────────────────────────────────

def resolve_trade_hit(
    bar: pd.Series,
    direction: int,
    trade_sl: float,
    trade_tp: float,
) -> 'str | None':
    """
    Xác định trên bar hiện tại có chạm SL/TP không.

    Quy tắc ưu tiên:
    - Nếu cùng bar chạm cả SL và TP thì trả về SL (bảo thủ).

    Trả về 'TP', 'SL', hoặc None.
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


# ─────────────────────────────────────────────────────────────────────────────
# 6. SIGNAL RECORD BUILDER (cho scanner)
# ─────────────────────────────────────────────────────────────────────────────

def build_signal_record(
    bar: pd.Series,
    direction: int,
    x: float,
    ktp: float,
    min_rr: float,
    *,
    extra: dict | None = None,
) -> dict:
    """
    Dựng một dòng kết quả tín hiệu với công thức chuẩn hóa.

    Tính nhất quán các trường: entry, sl, tp, sl_dist, tp_dist, rr, pass_rr.
    """
    sl_dist = float(bar['high']) - float(bar['low']) + 2 * float(x)
    tp_dist = ktp * float(bar['atr'])
    rr      = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0
    entry   = float(bar['high']) + float(x) if direction == 1 else float(bar['low']) - float(x)
    sl      = float(bar['low'])  - float(x) if direction == 1 else float(bar['high']) + float(x)
    tp      = entry + direction * ktp * float(bar['atr'])
    passes  = rr >= min_rr

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
    }
    if extra:
        record.update(extra)
    return record


# ─────────────────────────────────────────────────────────────────────────────
# 7. REVERSAL SCANNER
# ─────────────────────────────────────────────────────────────────────────────

def scan_signals_reversal(df: pd.DataFrame, cfg: dict, p: dict) -> pd.DataFrame:
    """
    Quét tín hiệu theo cơ chế đảo chiều cho Combo v2.

    Cách hoạt động:
    1) Dùng build_raw_signal_masks() để lấy buy_raw/sell_raw.
    2) Duyệt từng bar theo thời gian.
    3) Nếu đang có lệnh: ưu tiên kiểm tra SL/TP trước.
    4) Nếu chưa đóng bởi SL/TP: kiểm tra tín hiệu ngược để đảo chiều.
    5) Dựng bản ghi tín hiệu bằng build_signal_record().

    Parameters
    ----------
    df  : dữ liệu OHLCV + indicator (RangeIndex, có cột BarTime).
    cfg : config symbol (x, session_hours_utc, label...).
    p   : tham số scanner (KTP, MIN_RR).

    Returns
    -------
    DataFrame một dòng cho mỗi tín hiệu, có thêm cột is_reversal.
    """
    x      = cfg['x']
    KTP    = p['KTP']
    MIN_RR = p['MIN_RR']
    buy_raw, sell_raw = build_raw_signal_masks(
        df,
        cfg['session_hours_utc'],
    )

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
            outcome = resolve_trade_hit(bar, trade_d, trade_sl, trade_tp)
            if outcome is not None:
                in_trade = False
                if open_signal_idx is not None:
                    signals[open_signal_idx]['outcome'] = outcome
                    open_signal_idx = None
                continue

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
            d = 1 if buy_raw.iloc[i] else (-1 if sell_raw.iloc[i] else 0)
            if d == 0:
                continue

        record = build_signal_record(
            bar, d, x, KTP, MIN_RR,
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
