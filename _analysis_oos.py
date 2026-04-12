"""
OOS 2025 analysis — tại sao kết quả yếu?
Chạy: .venv\Scripts\python.exe _analysis_oos.py
"""
import sys, warnings
import os as _os
sys.path.insert(0, '.')
sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'core_python'))
warnings.filterwarnings('ignore')

from strategies.combo.core.backtest_engine import (
    load_backtest_full, add_backtest_indicators,
    detect_signals, session_mask,
)
from strategies.combo.core.execution import backtest_symbol
from strategies.combo.core.metrics import calc_metrics
from strategies.combo.core.strategy_config import (
    SYMBOLS, get_indicator_params, DEFAULT_COSTS,
)
import pandas as pd


def slice_year(df, year):
    s = pd.Timestamp(f'{year}-01-01')
    e = pd.Timestamp(f'{year}-12-31 23:59:59')
    return df[(df.index >= s) & (df.index <= e)].copy()


def slice_period(df, start_str, end_str):
    s = pd.Timestamp(start_str)
    e = pd.Timestamp(end_str + ' 23:59:59')
    return df[(df.index >= s) & (df.index <= e)].copy()


def run_bt(name, cfg, df_slice):
    if len(df_slice) < 20:
        return None
    mask       = session_mask(df_slice, cfg['session_hours_utc'])
    df_s       = detect_signals(df_slice, mask, sym_key=name, params=ind)
    trades, eq = backtest_symbol(name, df_s, cfg, INIT_BAL, costs=DEFAULT_COSTS)
    return calc_metrics(trades, eq) if trades else None


ind      = get_indicator_params()
INIT_BAL = 25_000

# Pre-load and cache all data
print('Loading data...')
_cache = {}
for name, cfg in SYMBOLS.items():
    raw          = load_backtest_full(cfg['symbol_id'])
    df           = add_backtest_indicators(raw, ind).dropna()
    df.index     = pd.to_datetime(df.index)
    _cache[name] = df
    print(f'  {name}: {len(df)} bars | {df.index[0].date()} -> {df.index[-1].date()}')

# ─────────────────────────────────────────────────────────────────────────────
# BƯỚC 1 — Performance theo từng năm
# ─────────────────────────────────────────────────────────────────────────────
print()
print('BUOC 1 - Performance theo tung nam')
print('=' * 72)
print(f'{"Symbol":<8} {"2022":>14} {"2023":>14} {"2024":>14} {"2025":>14}')
print(f'{"":8} {"PF/PnL":>14} {"PF/PnL":>14} {"PF/PnL":>14} {"PF/PnL":>14}')
print('-' * 72)

for name, cfg in SYMBOLS.items():
    row = f'{name:<8}'
    for year in ['2022', '2023', '2024', '2025']:
        m = run_bt(name, cfg, slice_year(_cache[name], year))
        if m:
            cell = f'{m["profit_factor"]:.2f}/{m["total_pnl"]:+.0f}'
            row += f' {cell:>14}'
        else:
            row += f' {"N/A":>14}'
    print(row)

print('=' * 72)
print(f'Format: PF / PnL(USD)  |  Init balance = {INIT_BAL:,} per symbol')

# ─────────────────────────────────────────────────────────────────────────────
# BƯỚC 2 — Phân tích theo quý (2024 vs 2025)
# ─────────────────────────────────────────────────────────────────────────────
print()
print('BUOC 2 - Phan tich theo quy')
print('=' * 60)

quarters = {
    'Q1-2024': ('2024-01-01', '2024-03-31'),
    'Q2-2024': ('2024-04-01', '2024-06-30'),
    'Q3-2024': ('2024-07-01', '2024-09-30'),
    'Q4-2024': ('2024-10-01', '2024-12-31'),
    'Q1-2025': ('2025-01-01', '2025-03-31'),
    'Q2-2025': ('2025-04-01', '2025-06-30'),
    'Q3-2025': ('2025-07-01', '2025-09-30'),
    'Q4-2025': ('2025-10-01', '2025-12-31'),
}

for sym in list(SYMBOLS.keys()):
    cfg = SYMBOLS[sym]
    df  = _cache[sym]
    print(f'\n  {sym}')
    print(f'  {"Quarter":<10} {"Trades":<8} {"WR%":<7} {"PF":<6} {"PnL":>9}')
    print(f'  {"-"*44}')
    for qname, (start, end) in quarters.items():
        m = run_bt(sym, cfg, slice_period(df, start, end))
        if m:
            print(f'  {qname:<10} {m["total_trades"]:<8} {m["win_rate"]:<7.1f}'
                  f' {m["profit_factor"]:<6.2f} ${m["total_pnl"]:>+8,.0f}')
        else:
            print(f'  {qname:<10} 0 trades / N/A')

# ─────────────────────────────────────────────────────────────────────────────
# BƯỚC 3 — Volatility (ATR) theo năm
# ─────────────────────────────────────────────────────────────────────────────
print()
print()
print('BUOC 3 - Volatility (ATR trung binh) theo nam')
print('=' * 58)
print(f'{"Symbol":<8} {"2022":>8} {"2023":>8} {"2024":>8} {"2025":>8}  Trend 24->25')
print('-' * 58)

for name in SYMBOLS:
    df  = _cache[name]
    atr = {}
    for yr in ['2022', '2023', '2024', '2025']:
        sl     = slice_year(df, yr)
        atr[yr] = sl['atr'].mean() if len(sl) > 10 else float('nan')
    if atr['2024'] and atr['2025']:
        pct   = (atr['2025'] - atr['2024']) / atr['2024'] * 100
        arrow = f'+{pct:.0f}% up' if pct > 0 else f'{pct:.0f}% down'
    else:
        arrow = 'N/A'
    print(f'{name:<8} {atr["2022"]:>8.1f} {atr["2023"]:>8.1f}'
          f' {atr["2024"]:>8.1f} {atr["2025"]:>8.1f}  {arrow}')

print('=' * 58)
print('ATR tang = market volatile hon | ATR giam = market ranging/calmer')
