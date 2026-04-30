# =============================================================================
# strategies/combo/scanner.py  —  Pipeline quét tín hiệu cho Combo v2
# =============================================================================
"""
Mô-đun điều phối scan dùng chung cho notebook, dashboard, và chart research.

Vai trò chính:
1) Chuẩn hoá quy trình load dữ liệu + thêm chỉ báo + làm sạch dữ liệu.
2) Gọi scanner đúng cách cho 1 symbol hoặc nhiều symbol.
3) Tính thống kê kết quả scan theo schema nhất quán.

Architecture note:
- Current ownership: `strategies.combo.config` is the canonical Combo
  parameter source; `strategies.combo.universe` owns the Combo symbol universe;
  `strategies.combo.config` and `strategies.combo.signals` are compatibility
  wrappers.
- Indicator, signal, and rule helpers live in `indicators.py`, `signals.py`,
  and `rules.py`.
- File này không được tự định nghĩa lại signal rule.
- Nó chỉ phối hợp dữ liệu từ `shared.data` với Combo-owned helpers.
"""
import pandas as pd

from modules.chart_builder import build_reversal_chart
from core_python.shared.data import load_scan_ohlcv

from .signals import add_combo_indicators
from .config import (
    DEFAULT_N_BARS,
    INDICATOR_COLS,
    SCANNER_DEFAULTS,
    SYMBOLS,
    TIMEFRAME,
)
from .signals import scan_signals_reversal


def _run_scan_with_scanner(symbol_key: str, n_bars: int, params: dict,
                           scanner, tf: str | None = None) -> dict:
    """Wrapper scan cho 1 symbol."""
    tf      = tf or TIMEFRAME
    cfg     = SYMBOLS[symbol_key]
    df_scan = prepare_data(symbol_key, n_bars, params, tf)
    sigs    = scanner(df_scan, cfg, params)
    return {'df_scan': df_scan, 'signals_df': sigs, 'cfg': cfg}


def _run_multi_scan_with_scanner(symbol_keys: list[str], n_bars: int,
                                 params: dict, scanner,
                                 tf: str | None = None,
                                 progress_cb=None) -> dict:
    """Wrapper scan cho nhiều symbol."""
    tf      = tf or TIMEFRAME
    results = {}
    total   = len(symbol_keys)
    for i, sym in enumerate(symbol_keys):
        if progress_cb:
            progress_cb(i, total, sym)
        results[sym] = _run_scan_with_scanner(sym, n_bars, params, scanner, tf)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# DATA PREPARATION
# ─────────────────────────────────────────────────────────────────────────────

def prepare_data(symbol_key: str, n_bars: int, params: dict,
                 tf: str | None = None) -> pd.DataFrame:
    """
    Chuẩn bị dữ liệu trước khi scan:
    - load OHLCV
    - thêm indicator theo đúng logic Combo
    - loại dòng warmup còn NaN
    - cắt tail theo n_bars
    """
    tf      = tf or TIMEFRAME
    cfg     = SYMBOLS[symbol_key]
    warmup  = max(
        int(params.get("MA_PERIOD", 20)),
        int(params.get("MACD_SLOW", 25)),
        int(params.get("ATR_PERIOD", 5)),
    ) * 4
    df_raw  = load_scan_ohlcv(
        cfg["symbol_id"],
        n_bars=n_bars,
        tf_code=tf,
        warmup=warmup,
        handle_missing="drop",
    )
    df_ind  = add_combo_indicators(df_raw, params)
    df_scan = (df_ind
               .dropna(subset=INDICATOR_COLS)
               .tail(n_bars)
               .reset_index(drop=True))
    return df_scan


prepare_scan_data = prepare_data


# ─────────────────────────────────────────────────────────────────────────────
# REVERSAL SCAN  (single & multi)
# ─────────────────────────────────────────────────────────────────────────────

def run_reversal_scan(symbol_key: str, n_bars: int, params: dict,
                      tf: str | None = None) -> dict:
    """Chạy full pipeline scan đảo chiều cho 1 symbol."""
    return _run_scan_with_scanner(
        symbol_key, n_bars, params, scan_signals_reversal, tf,
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


def build_reversal_figure(
    symbol_key: str,
    scan_result: dict,
    params: dict,
):
    """Build a Plotly reversal chart for a scan result."""
    cfg = scan_result.get("cfg", SYMBOLS[symbol_key])
    return build_reversal_chart(
        scan_result["df_scan"],
        scan_result["signals_df"],
        cfg,
        symbol_key,
        {
            **SCANNER_DEFAULTS,
            **params,
        },
    )
