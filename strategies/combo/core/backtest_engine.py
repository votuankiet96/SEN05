# =============================================================================
# strategy/backtest_engine.py  —  Engine backtest và tối ưu cho Combo v2
# Dùng bởi: 07_backtest.ipynb, 08_wf_optimizer.ipynb
# =============================================================================
# HƯỚNG DẪN QUẢN TRỊ NHANH (cho người vận hành)
# File này là "máy mô phỏng" của dự án: mọi quy tắc vào lệnh/thoát lệnh/rủi ro
# đều đi qua đây. Vì vậy nếu sửa nhầm, toàn bộ KPI (WR, PF, Return, MaxDD...)
# có thể thay đổi mạnh dù dữ liệu đầu vào không đổi.
#
# Nên ưu tiên chỉnh ở strategy_config.py trước:
# - risk_per_trade, min_rr, ktp, session_hours_utc, trailing_activation...
#
# Chỉ sửa trực tiếp file này khi bạn thực sự muốn đổi "luật chơi":
# - Cơ chế khớp pending order
# - Thứ tự ưu tiên SL/TP/trailing/reversal
# - Cách tính phí, drawdown, score, Sharpe/Calmar
#
# Nguyên tắc quản trị an toàn:
# - Sửa nhỏ từng bước, chạy lại backtest và so sánh KPI trước/sau.
# - Không trộn nhiều thay đổi logic trong cùng một lần commit.

"""
Mô-đun backtest dùng chung cho notebook backtest và notebook optimizer.

Mục tiêu:
1) Loại bỏ việc copy/paste logic mô phỏng lệnh ở nhiều nơi.
2) Đảm bảo khi đổi 1 luật giao dịch, toàn hệ thống phản ánh nhất quán.
3) Tách rõ 2 chế độ:
   - backtest_symbol(): đầy đủ trade log, dùng để phân tích chi tiết.
   - backtest_fast(): tối giản để chạy grid-search nhanh.

Cách dùng:
    from backtest_engine import (
        load_backtest_data, add_backtest_indicators,
        session_mask, detect_signals,
        backtest_symbol, backtest_fast,
        walk_forward_backtest, calc_metrics,
    )
"""
import numpy as np
import pandas as pd

from modules.db_connector import get_connection
from modules.indicators import add_indicators as _add_base_indicators

from .strategy_config import (
    STRATEGY,
    SYMBOLS,
    TIMEFRAME,
    get_indicator_params,
    get_symbol_ktp,
)

# ─────────────────────────────────────────────────────────────────────────────
# 1. DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_backtest_data(symbol_id: int, date_to: str | None = None,
                       tf: str | None = None, max_bars: int = 60000,
                       warmup: int | None = None) -> pd.DataFrame:
    """
        Tải dữ liệu OHLCV phục vụ backtest.

        File trả về có index là BarTime và cột chuẩn hoá chữ thường:
        open, high, low, close, volume.

        Tác động quản trị:
        - max_bars càng lớn: mô phỏng dài hơn nhưng tốn thời gian hơn.
        - warmup giúp chỉ báo ổn định trước khi vào vùng test chính.
            Nếu warmup quá thấp, tín hiệu đầu kỳ có thể sai lệch.

    Parameters
    ----------
        date_to  : mốc thời gian trên cùng để cắt dữ liệu.
        tf       : timeframe, mặc định lấy từ strategy_config.TIMEFRAME.
        max_bars : số bar tối đa cho vùng test (chưa tính warmup).
        warmup   : số bar đệm cho chỉ báo; None = tự tính theo MA/MACD/ATR.
    """
    tf = tf or TIMEFRAME
    if warmup is None:
        p = get_indicator_params()
        warmup = max(p['MA_PERIOD'], p['MACD_SLOW'], p['ATR_PERIOD']) * 4
    total = max_bars + warmup

    conn = get_connection()
    try:
        date_clause = "AND f.BarTime <= ?" if date_to else ""
        query = f'''
            SELECT TOP ({total})
                   f.BarTime,
                   f.OpenPrice  AS [open],
                   f.HighPrice  AS [high],
                   f.LowPrice   AS [low],
                   f.ClosePrice AS [close],
                   f.Volume     AS [volume]
            FROM   DWH.Fact_OHLCV f
            JOIN   DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
            WHERE  f.SymbolID = ?
              AND  tf.Code    = ?
              {date_clause}
            ORDER  BY f.BarTime DESC
        '''
        params = (symbol_id, tf, date_to) if date_to else (symbol_id, tf)
        df = pd.read_sql(query, conn, params=params,
                         parse_dates=['BarTime'], index_col='BarTime')
    finally:
        conn.close()
    return df.sort_index()


def load_backtest_full(symbol_id: int, tf: str | None = None,
                       max_bars: int = 80000) -> pd.DataFrame:
    """Tải gần như toàn bộ dữ liệu cho 1 mã (thường dùng pre-cache cho optimizer)."""
    return load_backtest_data(symbol_id, date_to=None, tf=tf,
                              max_bars=max_bars, warmup=0)


# ─────────────────────────────────────────────────────────────────────────────
# 2. INDICATORS
# ─────────────────────────────────────────────────────────────────────────────

def add_backtest_indicators(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
        Thêm chỉ báo chiến lược + cột phụ trợ riêng cho backtest.

        Những gì hàm này làm:
        - Gọi add_indicators() để tạo ma, macd_h, atr.
        - Tạo thêm prev_close, prev_ma để phát hiện crossover đúng quy tắc.
        - Giữ tương thích cả DataFrame có DateTimeIndex và dạng thường.

        Tác động quản trị:
        - Thay đổi cách tính chỉ báo ở đây sẽ ảnh hưởng đồng thời tín hiệu,
            điểm vào lệnh, và toàn bộ KPI downstream.

    Parameters
    ----------
        params : dict tham số chỉ báo. Cho phép truyền thiếu một phần;
                         phần còn lại sẽ lấy từ default.
    """
        # Ghép params đầy đủ: cho phép optimizer chỉ truyền MA_PERIOD.
    defaults = get_indicator_params()
    full_p   = {**defaults, **params} if isinstance(params, dict) else defaults

        # _add_base_indicators kỳ vọng BarTime là cột thường,
        # nên tạm reset index rồi set lại sau.
    has_dt_index = isinstance(df.index, pd.DatetimeIndex)
    if has_dt_index:
        df_flat = df.reset_index()
    else:
        df_flat = df

    df_out = _add_base_indicators(df_flat, full_p)

    # Khôi phục DateTimeIndex nếu dữ liệu ban đầu dùng kiểu này.
    if has_dt_index:
        df_out = df_out.set_index('BarTime')

    # Cột bắt buộc cho luật crossover trong backtest.
    df_out['prev_close'] = df_out['close'].shift(1)
    df_out['prev_ma']    = df_out['ma'].shift(1)
    return df_out


# ─────────────────────────────────────────────────────────────────────────────
# 3. SESSION FILTER
# ─────────────────────────────────────────────────────────────────────────────

def session_mask(df: pd.DataFrame, hours_utc: list) -> pd.Series:
    """
    Tạo mask theo phiên giao dịch (UTC).

    - Nếu hours_utc rỗng: cho phép toàn bộ bar.
    - Nếu có giá trị: chỉ bar có giờ nằm trong danh sách mới được xét tín hiệu.

    Tác động quản trị:
    - Đây là công tắc lọc thời gian quan trọng. Chỉnh sai có thể làm số lệnh
      thay đổi rất lớn, kéo theo WR/PF/Return thay đổi mạnh.
    """
    if not hours_utc:
        return pd.Series(True, index=df.index)
    return pd.Series(df.index.hour.isin(hours_utc), index=df.index)


# ─────────────────────────────────────────────────────────────────────────────
# 4. SIGNAL DETECTION (vectorised — for the full backtest)
# ─────────────────────────────────────────────────────────────────────────────

def detect_signals(df: pd.DataFrame, sess_mask: pd.Series,
                   sym_key: str | None = None,
                   params: dict | None = None) -> pd.DataFrame:
    """
        Phát hiện tín hiệu theo kiểu vectorized cho backtest đầy đủ.

        Luật tín hiệu cốt lõi:
        - BUY: nến cắt lên MA + nến tăng + MACD histogram dương + RR đạt ngưỡng.
        - SELL: nến cắt xuống MA + nến giảm + MACD histogram âm + RR đạt ngưỡng.

        Lưu ý đồng bộ quan trọng:
        - SELL dùng điều kiện close <= ma (viết dưới dạng ~(close > ma))
            để khớp với scanner, tránh lệch tín hiệu biên.

    Parameters
    ----------
        df        : dữ liệu có index thời gian + cột chỉ báo.
        sess_mask : mask từ session_mask().
        sym_key   : mã symbol để lấy x/ktp đúng theo cấu hình.
        params    : tham số chỉ báo và ngưỡng RR.

        Trả về DataFrame đã thêm:
        - signal: 1 (BUY), -1 (SELL), 0 (không có tín hiệu)
        - rr: tỷ lệ Reward/Risk ước tính cho từng bar
    """
    df = df.copy()
    p  = params or get_indicator_params()
    x   = SYMBOLS[sym_key]['x'] if sym_key else 0
    ktp = get_symbol_ktp(sym_key) if sym_key else p.get('KTP', STRATEGY['ktp'])
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


# ─────────────────────────────────────────────────────────────────────────────
# Re-exports: để các file import từ backtest_engine vẫn hoạt động đầy đủ
# ─────────────────────────────────────────────────────────────────────────────
from .execution import backtest_symbol, backtest_fast
from .walk_forward import walk_forward_backtest, check_plateau_stability
from .metrics import calc_metrics, in_bao_cao
