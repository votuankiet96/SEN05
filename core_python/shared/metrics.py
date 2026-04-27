# =============================================================================
# strategies/shared/metrics.py  —  Tính toán và báo cáo kết quả backtest
# =============================================================================
"""Tính bộ chỉ số hiệu suất và in báo cáo từ kết quả backtest.

Các hàm chính:
- _bars_per_year(): helper nội bộ, dùng để annualize Sharpe/Sortino.
- calc_metrics()  : tính toàn bộ KPI từ trade log + equity curve.
- in_bao_cao()    : in metrics dạng bảng ASCII ra console.

Module này không phụ thuộc vào bất kỳ strategy cụ thể nào.
"""
import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _bars_per_year(tf_code: str) -> int:
    """Map timeframe code -> số bar xấp xỉ mỗi năm."""
    mapping = {
        'M5': 252 * 288,
        'M15': 252 * 96,
        'M20': 252 * 72,
        'M30': 252 * 48,
        'M45': 252 * 32,
        'H1': 252 * 24,
        'H4': 252 * 6,
        'H6': 252 * 4,
        'H8': 252 * 3,
        'D1': 252,
    }
    return mapping.get((tf_code or 'H4').upper(), 252 * 6)


# ─────────────────────────────────────────────────────────────────────────────
# METRICS CALCULATION
# ─────────────────────────────────────────────────────────────────────────────

def calc_metrics(trades: list, eq_ts: pd.Series, tf_code: str = 'H4') -> dict:
    """
    Tính bộ chỉ số hiệu suất từ trade log và equity curve.

    Đây là lớp tổng kết cuối cùng để trình bày kết quả chiến lược.
    Nếu đổi công thức trong hàm này, dashboard và báo cáo sẽ đổi theo,
    dù phần mô phỏng lệnh giữ nguyên.

    Parameters
    ----------
    trades : danh sách lệnh từ backtest_symbol().
    eq_ts  : chuỗi equity theo thời gian.
    tf_code: timeframe code để annualize Sharpe/Sortino.

    Returns
    -------
    dict gồm toàn bộ metric:
    - sortino
    - recovery_factor
    - max_drawdown_usd
    - monthly_pnl_table (DataFrame pivot year x month)
    """
    if not trades:
        return {}

    df     = pd.DataFrame(trades)
    w_df   = df[df['pnl_usd'] > 0]
    l_df   = df[df['pnl_usd'] <= 0]
    gp     = w_df['pnl_usd'].sum() if len(w_df) else 0
    gl     = l_df['pnl_usd'].abs().sum() if len(l_df) else 0

    peak   = eq_ts.cummax()
    dd_pct_ts = (eq_ts - peak) / peak
    dd_usd_ts = eq_ts - peak

    total_ret  = (eq_ts.iloc[-1] / eq_ts.iloc[0] - 1) * 100
    years      = (eq_ts.index[-1] - eq_ts.index[0]).days / 365.25
    annual_ret = ((eq_ts.iloc[-1] / eq_ts.iloc[0]) ** (1 / max(years, 0.01)) - 1) * 100

    # Sharpe/Sortino tính trên trade-level returns để tránh inflate bởi các bar equity phẳng.
    n_trades   = len(df)
    eq0        = eq_ts.iloc[0] if eq_ts.iloc[0] != 0 else 1.0
    trade_rets = df['pnl_usd'] / eq0
    n_per_year = n_trades / max(years, 0.01)

    if n_trades < 10 or trade_rets.std() == 0:
        sharpe = 0.0
    else:
        sharpe = float(trade_rets.mean() / trade_rets.std() * np.sqrt(max(n_per_year, 1)))

    neg_rets = trade_rets[trade_rets < 0]
    if n_trades < 10 or len(neg_rets) < 2 or neg_rets.std() == 0:
        sortino = 0.0
    else:
        sortino = float(trade_rets.mean() / neg_rets.std() * np.sqrt(max(n_per_year, 1)))
    max_dd     = dd_pct_ts.min() * 100
    max_dd_usd = dd_usd_ts.min()
    calmar     = annual_ret / abs(max_dd) if max_dd else 0

    avg_win  = w_df['pnl_usd'].mean() if len(w_df) else 0
    avg_loss = l_df['pnl_usd'].mean() if len(l_df) else 0
    total_swap_cost = float(df['swap_cost'].sum()) if 'swap_cost' in df.columns else 0.0

    recovery_factor = (df['pnl_usd'].sum() / abs(max_dd_usd)
                       if max_dd_usd != 0 else 0)

    monthly_pnl_table = pd.DataFrame()
    if 'exit_time' in df.columns:
        _m = df.copy()
        _m['exit_time'] = pd.to_datetime(_m['exit_time'])
        _m['year'] = _m['exit_time'].dt.year
        _m['month'] = _m['exit_time'].dt.month
        monthly_pnl_table = (
            _m.pivot_table(index='year', columns='month', values='pnl_usd',
                           aggfunc='sum', fill_value=0.0)
            .sort_index()
        )

    # Thống kê partial TP, có tương thích ngược với log cũ chưa có cột này.
    partial_count = int(df['partial_tp_hit'].sum()) \
        if 'partial_tp_hit' in df.columns else 0

    return dict(
        total_trades=len(df),
        wins=len(w_df), losses=len(l_df),
        win_rate=round(len(w_df) / len(df) * 100, 1),
        profit_factor=round(gp / gl if gl else float('inf'), 2),
        total_pnl=round(df['pnl_usd'].sum(), 2),
        total_return=round(total_ret, 2),
        annual_return=round(annual_ret, 2),
        max_drawdown=round(max_dd, 2),
        max_drawdown_usd=round(float(max_dd_usd), 2),
        sharpe=round(sharpe, 2),
        sortino=round(sortino, 2),
        calmar=round(calmar, 2),
        recovery_factor=round(recovery_factor, 2),
        avg_win=round(avg_win, 2),
        avg_loss=round(avg_loss, 2),
        total_swap_cost=round(total_swap_cost, 2),
        avg_rr=round(abs(avg_win / avg_loss) if avg_loss else 0, 2),
        avg_r=round(df['r_multiple'].mean(), 3),
        partial_tp_trades=partial_count,
        partial_tp_rate=round(partial_count / len(df) * 100, 1),
        monthly_pnl_table=monthly_pnl_table,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ASCII REPORT PRINTER
# ─────────────────────────────────────────────────────────────────────────────

def in_bao_cao(metrics: dict) -> None:
    """In toàn bộ metrics dạng bảng ASCII thuần ra console."""
    if not metrics:
        print('+----------------------+----------------------+')
        print('| Metric               | Value                |')
        print('+----------------------+----------------------+')
        print('| (empty)              | no metrics           |')
        print('+----------------------+----------------------+')
        return

    rows = []
    for k, v in metrics.items():
        if k == 'monthly_pnl_table':
            continue
        if isinstance(v, float):
            rows.append((k, f'{v:.4f}'))
        else:
            rows.append((k, str(v)))

    key_w = max(len('Metric'), max((len(k) for k, _ in rows), default=0))
    val_w = max(len('Value'), max((len(v) for _, v in rows), default=0))

    line = '+' + '-' * (key_w + 2) + '+' + '-' * (val_w + 2) + '+'
    print(line)
    print(f"| {'Metric'.ljust(key_w)} | {'Value'.ljust(val_w)} |")
    print(line)
    for k, v in rows:
        print(f"| {k.ljust(key_w)} | {v.ljust(val_w)} |")
    print(line)

    monthly = metrics.get('monthly_pnl_table')
    if isinstance(monthly, pd.DataFrame) and not monthly.empty:
        print('\nMonthly PnL (year x month)')
        print(monthly.to_string())
