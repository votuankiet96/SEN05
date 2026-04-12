# =============================================================================
# strategy/scan_pipeline.py  —  Pipeline quét tín hiệu cho Combo v2
# Used by: 04_reversal_scanner.ipynb, signal_dashboard.py
#          (and future: backtest, optimizer as signal source)
# =============================================================================
# HƯỚNG DẪN QUẢN TRỊ NHANH
# File này điều phối toàn bộ luồng scan:
# load data → add indicators → chạy scanner → tính stats.
#
# Có thể điều chỉnh ở đây (rủi ro thấp đến trung bình):
# - Hành vi batch scan
# - Progress callback và cách đóng gói kết quả
# - Các bước chuẩn bị dữ liệu
#
# Giữ nguyên output schema, vì dashboard và notebook phụ thuộc vào
# đúng tên key/tên cột được trả về.

"""
Mô-đun điều phối scan dùng chung cho notebook và dashboard.

Vai trò chính:
1) Chuẩn hoá quy trình load dữ liệu + thêm chỉ báo + làm sạch dữ liệu.
2) Gọi scanner đúng cách cho 1 symbol hoặc nhiều symbol.
3) Tính thống kê kết quả scan theo schema nhất quán.

Lợi ích quản trị:
- Tránh copy/paste pipeline ở nhiều nơi.
- Khi cần chỉnh luồng scan, chỉ sửa một điểm trung tâm.
"""
import pandas as pd

from modules.data_loader import load_ohlcv as _load_ohlcv_raw
from modules.indicators import add_indicators as _add_indicators

from .reversal_scanner import scan_signals_reversal
from .strategy_config import (
    DEFAULT_N_BARS,
    INDICATOR_COLS,
    SYMBOLS,
    TIMEFRAME,
    US30_SYMBOL_ID,
    US_FILTERED,
)


def _resolve_us30_uptrend(symbol_keys: list[str], n_bars: int,
                          params: dict, tf: str) -> pd.Series | None:
    """Nạp xu hướng US30 một lần khi có ít nhất một symbol cần macro filter."""
    if any(sym in US_FILTERED for sym in symbol_keys):
        return build_us30_uptrend(n_bars, params, tf)
    return None


def _run_scan_with_scanner(symbol_key: str, n_bars: int, params: dict,
                           scanner, us30_uptrend: pd.Series | None = None,
                           tf: str | None = None) -> dict:
    """
    Wrapper scan cho 1 symbol.

    Quy trình:
    - resolve timeframe
    - prepare_data
    - gắn US30 filter nếu symbol cần
    - gọi scanner được truyền vào
    """
    tf = tf or TIMEFRAME
    cfg = SYMBOLS[symbol_key]
    df_scan = prepare_data(symbol_key, n_bars, params, tf)
    us30_f = us30_uptrend if symbol_key in US_FILTERED else None
    sigs = scanner(df_scan, cfg, params, us30_f)
    return {'df_scan': df_scan, 'signals_df': sigs, 'cfg': cfg}


def _run_multi_scan_with_scanner(symbol_keys: list[str], n_bars: int,
                                 params: dict, scanner,
                                 tf: str | None = None,
                                 progress_cb=None) -> dict:
    """
    Wrapper scan cho nhiều symbol.

    Lưu ý quản trị:
    - US30 trend được nạp một lần dùng chung để tiết kiệm thời gian.
    - progress_cb giúp dashboard hiển thị tiến độ theo symbol.
    """
    tf = tf or TIMEFRAME
    us30_uptrend = _resolve_us30_uptrend(symbol_keys, n_bars, params, tf)

    results = {}
    total = len(symbol_keys)
    for i, sym in enumerate(symbol_keys):
        if progress_cb:
            progress_cb(i, total, sym)
        results[sym] = _run_scan_with_scanner(sym, n_bars, params, scanner, us30_uptrend, tf)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# US30 MACRO FILTER
# ─────────────────────────────────────────────────────────────────────────────

def build_us30_uptrend(n_bars: int, params: dict,
                       tf: str | None = None) -> pd.Series:
    """
    Nạp dữ liệu US30, tính chỉ báo, trả về Series bool theo BarTime.

    True  = US30 close > MA (xem là xu hướng tăng)
    False = ngược lại

    Series này được dùng để lọc tín hiệu cho các symbol bật us_macro_filter.
    """
    tf = tf or TIMEFRAME
    df_us30  = _load_ohlcv_raw(US30_SYMBOL_ID, n_bars, tf, handle_missing='drop')
    df_us30i = _add_indicators(df_us30, params).dropna(subset=INDICATOR_COLS[:1])
    return pd.Series(
        (df_us30i['close'] > df_us30i['ma']).values,
        index=df_us30i['BarTime'],
    )


# ─────────────────────────────────────────────────────────────────────────────
# DATA PREPARATION
# ─────────────────────────────────────────────────────────────────────────────

def prepare_data(symbol_key: str, n_bars: int, params: dict,
                 tf: str | None = None) -> pd.DataFrame:
    """
        Chuẩn bị dữ liệu trước khi scan:
        - load OHLCV
        - thêm indicator
        - loại dòng warmup còn NaN
        - cắt tail theo n_bars

        Trả về DataFrame sạch, RangeIndex, sẵn sàng cho scanner và chart.

        Tác động quản trị:
        - Nếu sửa danh sách INDICATOR_COLS hoặc bước dropna,
            số lượng bar hợp lệ cho scan sẽ thay đổi.
    """
    tf      = tf or TIMEFRAME
    cfg     = SYMBOLS[symbol_key]
    df_raw  = _load_ohlcv_raw(cfg['symbol_id'], n_bars, tf, handle_missing='drop')
    df_ind  = _add_indicators(df_raw, params)
    df_scan = (df_ind
               .dropna(subset=INDICATOR_COLS)
               .tail(n_bars)
               .reset_index(drop=True))
    return df_scan


# ─────────────────────────────────────────────────────────────────────────────
# REVERSAL SCAN  (single & multi)
# ─────────────────────────────────────────────────────────────────────────────

def run_reversal_scan(symbol_key: str, n_bars: int, params: dict,
                      us30_uptrend: pd.Series | None = None,
                      tf: str | None = None) -> dict:
    """
    Chạy full pipeline scan đảo chiều cho 1 symbol.

    Trả về dict gồm: df_scan, signals_df, cfg.
    """
    return _run_scan_with_scanner(
        symbol_key, n_bars, params, scan_signals_reversal, us30_uptrend, tf,
    )


def run_multi_reversal_scan(symbol_keys: list[str], n_bars: int, params: dict,
                            tf: str | None = None,
                            progress_cb=None) -> dict:
    """Scan nhiều symbol bằng reversal logic, trả về kết quả theo từng symbol."""
    return _run_multi_scan_with_scanner(
        symbol_keys, n_bars, params, scan_signals_reversal, tf, progress_cb,
    )


def calc_reversal_stats(signals_df: pd.DataFrame) -> dict:
    """
        Tính thống kê tổng hợp cho kết quả reversal scan.

        Trả về dict gồm: n_total, n_pass, n_rejected, n_buy, n_sell,
        avg_rr, n_tp, n_sl, n_open, n_reversed, n_reversal_signals, win_pct.

        Lưu ý:
        - win_pct được tính trên số lệnh đã đóng theo mẫu:
            TP / (TP + SL + Reversed).
        - Open chưa đóng không đi vào mẫu số win_pct.
    """
    if signals_df.empty:
        return dict(n_total=0, n_pass=0, n_rejected=0, n_buy=0, n_sell=0,
                    avg_rr=0.0, n_tp=0, n_sl=0, n_open=0,
                    n_reversed=0, n_reversal_signals=0, win_pct=0.0)

    n_total = len(signals_df)
    n_pass  = int(signals_df['pass_rr'].sum())
    n_buy   = int((signals_df['direction'] == 'BUY').sum())
    n_sell  = int((signals_df['direction'] == 'SELL').sum())
    avg_rr  = float(signals_df.loc[signals_df['pass_rr'], 'rr'].mean()) \
              if n_pass > 0 else 0.0

    ps           = signals_df[signals_df['pass_rr']] if n_pass > 0 else pd.DataFrame()
    n_tp         = int((ps['outcome'] == 'TP').sum())       if not ps.empty else 0
    n_sl         = int((ps['outcome'] == 'SL').sum())       if not ps.empty else 0
    n_open       = int((ps['outcome'] == 'Open').sum())     if not ps.empty else 0
    n_reversed   = int((ps['outcome'] == 'Reversed').sum()) if not ps.empty else 0
    n_rev_sigs   = int(signals_df['is_reversal'].sum())

    n_closed = n_tp + n_sl + n_reversed
    win_pct  = round(100 * n_tp / n_closed, 1) if n_closed > 0 else 0.0

    return dict(
        n_total=n_total, n_pass=n_pass, n_rejected=n_total - n_pass,
        n_buy=n_buy, n_sell=n_sell, avg_rr=round(avg_rr, 2),
        n_tp=n_tp, n_sl=n_sl, n_open=n_open,
        n_reversed=n_reversed, n_reversal_signals=n_rev_sigs,
        win_pct=win_pct,
    )
