# =============================================================================
# strategies/combo/symbol/walkforward.py  —  IS/OOS walk-forward optimization
# =============================================================================
"""Kiểm định walk-forward out-of-sample (OOS) cho chiến lược Combo v2.

Các hàm chính:
- check_plateau_stability() : đánh giá best params có nằm trên vùng plateau
                               ổn định hay chỉ là spike cô lập.
- walk_forward_backtest()   : trượt cửa sổ IS→OOS, tối ưu trên IS, kiểm định
                               trên OOS, trả về thống kê tổng hợp.
"""
import numpy as np
import pandas as pd

from shared.execution import backtest_fast, backtest_symbol
from shared.metrics import calc_metrics

from ..config import (
    OPTIMIZATION,
    SYMBOLS,
    get_account_settings,
    get_cost_settings,
    get_indicator_params,
    get_symbol_params,
    get_symbol_search_space,
)
from ..logic import add_combo_indicators, detect_combo_signals, session_mask


# ─────────────────────────────────────────────────────────────────────────────
# PLATEAU STABILITY CHECK
# ─────────────────────────────────────────────────────────────────────────────

def check_plateau_stability(
    param_grid_results: dict,
    best_params: dict,
    best_sharpe: float,
    radius: int = 1,
    threshold_ratio: float = 0.5,
) -> dict:
    """Đánh giá best params có nằm trên vùng plateau ổn định hay chỉ là spike."""
    keys = ['ktp', 'x', 'min_rr']
    default = {
        'best_params': dict(best_params or {}),
        'best_sharpe': float(best_sharpe or 0.0),
        'neighbors_checked': 0,
        'neighbors_stable': 0,
        'stable_ratio': 0.0,
        'is_plateau': False,
        'warning': 'Không đủ dữ liệu lân cận để xác thực plateau.',
    }

    if not param_grid_results or not best_params:
        return default

    sharpe_map = {}
    points = []
    for k, v in param_grid_results.items():
        if isinstance(k, tuple) and len(k) == len(keys):
            point = {
                'ktp':    float(k[0]),
                'x':      float(k[1]),
                'min_rr': float(k[2]),
            }
        elif isinstance(k, dict):
            point = {
                'ktp':    float(k.get('ktp', 0.0)),
                'x':      float(k.get('x', 0.0)),
                'min_rr': float(k.get('min_rr', 0.0)),
            }
        else:
            continue

        sharpe_v = float(v.get('sharpe', 0.0)) if isinstance(v, dict) else float(v)
        key_t = (float(point['ktp']), float(point['x']), float(point['min_rr']))
        sharpe_map[key_t] = sharpe_v
        points.append(point)

    if not sharpe_map:
        return default

    neighbor_values = {}
    for k in keys:
        vals = sorted({float(p[k]) for p in points})
        if not vals:
            return default
        best_v = float(best_params.get(k, 0.0))
        if best_v in vals:
            idx = vals.index(best_v)
        else:
            idx = min(range(len(vals)), key=lambda i: abs(vals[i] - best_v))
        lo = max(0, idx - max(1, int(radius)))
        hi = min(len(vals), idx + max(1, int(radius)) + 1)
        neighbor_values[k] = vals[lo:hi]

    best_key = (
        float(best_params['ktp']),
        float(best_params['x']),
        float(best_params['min_rr']),
    )
    min_sharpe = float(best_sharpe) * float(threshold_ratio)
    neighbors_checked = 0
    neighbors_stable  = 0

    for ktp_v in neighbor_values['ktp']:
        for x_v in neighbor_values['x']:
            for rr_v in neighbor_values['min_rr']:
                combo = (float(ktp_v), float(x_v), float(rr_v))
                if combo == best_key:
                    continue
                sh = sharpe_map.get(combo)
                if sh is None:
                    continue
                neighbors_checked += 1
                if float(sh) > min_sharpe:
                    neighbors_stable += 1

    stable_ratio = (
        float(neighbors_stable) / float(neighbors_checked)
        if neighbors_checked > 0 else 0.0
    )
    is_plateau = stable_ratio >= 0.6

    return {
        'best_params': dict(best_params),
        'best_sharpe': float(best_sharpe),
        'neighbors_checked': int(neighbors_checked),
        'neighbors_stable': int(neighbors_stable),
        'stable_ratio': float(round(stable_ratio, 4)),
        'is_plateau': bool(is_plateau),
        'warning': '' if is_plateau else 'Best params có dấu hiệu spike, không nằm trên plateau ổn định.',
    }


# ─────────────────────────────────────────────────────────────────────────────
# WALK-FORWARD OUT-OF-SAMPLE TEST
# ─────────────────────────────────────────────────────────────────────────────

def walk_forward_backtest(
    symbol: str,
    df_ind: pd.DataFrame,
    cfg: dict,
    init_eq: float = 10_000.0,
    *,
    is_bars: int = 5000,
    oos_bars: int = 1250,
    step_bars: int = 1250,
    strategy: dict | None = None,
    costs: dict | None = None,
    broker_profile: str | None = None,
) -> tuple[pd.DataFrame, dict]:
    """
    Chạy kiểm định walk-forward out-of-sample (OOS) với tham số cố định.

    Cách hoạt động:
    - Trượt cửa sổ qua dữ liệu lịch sử.
    - Mỗi cửa sổ: optimize trên IS (grid search nhỏ), validate trên OOS.
    - Mục tiêu đo độ "bền" của bộ tham số qua nhiều pha thị trường.

    Parameters
    ----------
    symbol    : symbol cần test.
    df_ind    : dữ liệu đã có signal cho toàn dải thời gian.
    cfg       : cấu hình symbol.
    init_eq   : vốn khởi điểm cho từng cửa sổ OOS.
    is_bars   : số bar đệm trước cửa sổ OOS đầu tiên.
    oos_bars  : độ dài mỗi cửa sổ OOS.
    step_bars : độ trượt giữa hai cửa sổ liên tiếp.
    strategy  : override strategy cho toàn bộ quá trình.
    costs     : override phí cho toàn bộ quá trình.

    Returns
    -------
    (oos_df, summary)
        oos_df  : một dòng cho mỗi cửa sổ OOS + các KPI.
        summary : thống kê tổng hợp toàn bộ cửa sổ.
    """
    def _build_param_grid(sym_key: str) -> dict:
        base = get_symbol_search_space(sym_key, broker_profile=broker_profile)
        return {
            'ktp':    base['ktp'],
            'x':      base['x'],
            'min_rr': base['min_rr'],
        }

    n_bars  = len(df_ind)
    results = []
    step    = 0
    is_profit_total  = 0.0
    oos_profit_total = 0.0
    best_params_per_window = []

    required_cols = ['open', 'high', 'low', 'close']
    if not all(c in df_ind.columns for c in required_cols):
        return pd.DataFrame(), {}

    strategy_cfg = get_account_settings("standard", strategy)
    cost_cfg = get_cost_settings(symbol, costs, broker_profile=broker_profile)
    if "score_column" not in strategy_cfg:
        strategy_cfg["score_column"] = OPTIMIZATION["symbol"]["score_column"]

    # Base indicator params dùng chung cho cả IS và OOS
    base_ind_params = get_indicator_params()

    while True:
        oos_start = is_bars + step * step_bars
        oos_end   = oos_start + oos_bars
        if oos_end > n_bars:
            break

        is_slice  = df_ind.iloc[oos_start - is_bars:oos_start].copy()
        oos_slice = df_ind.iloc[oos_start:oos_end].copy()
        if len(is_slice) < 30 or len(oos_slice) < 10:
            break

        # Step 2 — Optimize trên IS bằng backtest_fast (maximize Sharpe)
        grid = _build_param_grid(symbol)
        best = None
        best_sharpe  = -np.inf
        grid_results = {}

        # MA cố định 20 — tính indicators một lần cho IS window
        is_raw = is_slice[['open', 'high', 'low', 'close']].copy()
        is_raw['volume'] = is_slice['volume'] if 'volume' in is_slice.columns else 0.0
        is_ind_local = add_combo_indicators(is_raw, {**base_ind_params, 'MA_PERIOD': 20})

        date_from_is = str(is_ind_local.index[0])
        date_to_is   = str(is_ind_local.index[-1])

        for ktp_v in grid['ktp']:
            for x_v in grid['x']:
                for rr_v in grid['min_rr']:
                    cfg_is = {**cfg, 'x': x_v}
                    fast_metrics = backtest_fast(
                        symbol,
                        is_ind_local,
                        cfg_is,
                        ktp=float(ktp_v),
                        x_actual=float(x_v),
                        trailing_act=float(strategy_cfg.get('trailing_activation', 1.0)),
                        date_from=date_from_is,
                        date_to=date_to_is,
                        init_eq=init_eq,
                        strategy={**strategy_cfg, 'min_rr': float(rr_v)},
                        costs=cost_cfg,
                    )
                    sh = float(fast_metrics.get('sharpe', 0.0))
                    grid_results[(float(ktp_v), float(x_v), float(rr_v))] = sh
                    if sh > best_sharpe:
                        best_sharpe = sh
                        best = {
                            'ktp':    float(ktp_v),
                            'x':      float(x_v),
                            'min_rr': float(rr_v),
                            'is_fast': fast_metrics,
                        }

        if best is None:
            break

        best_params = {
            'ktp':    best['ktp'],
            'x':      best['x'],
            'min_rr': best['min_rr'],
        }
        plateau = check_plateau_stability(
            param_grid_results=grid_results,
            best_params=best_params,
            best_sharpe=best_sharpe,
            radius=1,
        )
        if not plateau['is_plateau']:
            print(f"  ⚠️  Window {step}: tham số tốt nhất là SPIKE, không phải plateau")
            print(f"      stable_ratio = {plateau['stable_ratio']:.1%} < 60%")
            print("      → Kết quả OOS cửa sổ này kém tin cậy")

        best_params_per_window.append({
            'window':   step,
            'ktp':      best['ktp'],
            'x':        best['x'],
            'min_rr':   best['min_rr'],
            'is_sharpe': best_sharpe,
            'plateau':  plateau,
        })

        is_profit_total += init_eq * float(best['is_fast'].get('ret', 0.0)) / 100.0

        # Step 3 — Validate trên OOS với best_params từ IS
        # Tính warmup cần thiết để indicator (đặc biệt EMA/MACD) có đủ state
        p_local = {
            **base_ind_params,
            'MA_PERIOD': 20,
            'KTP': best['ktp'],
            'X':   best['x'],
        }
        _warmup = max(
            int(p_local.get('MACD_SLOW', 25)),
            int(p_local['MA_PERIOD']),
            int(p_local.get('ATR_PERIOD', 5)),
        ) + 5

        # Carry warmup bars từ IS tail vào OOS để indicator có đủ history
        warmup_from   = max(0, oos_start - _warmup)
        warmup_actual = oos_start - warmup_from  # bars thực sự carry được
        oos_with_warmup = df_ind.iloc[warmup_from:oos_end][['open', 'high', 'low', 'close']].copy()
        oos_with_warmup['volume'] = (
            df_ind.iloc[warmup_from:oos_end]['volume'].values
            if 'volume' in df_ind.columns else 0.0
        )
        oos_with_warmup = add_combo_indicators(oos_with_warmup, p_local)

        # Cắt bỏ warmup bars, chỉ giữ OOS bars thật
        oos_ind_local = oos_with_warmup.iloc[warmup_actual:].copy()
        oos_mask      = session_mask(oos_ind_local, cfg.get('session_hours_utc', []))
        oos_sig       = detect_combo_signals(oos_ind_local, oos_mask, sym_key=None, params=p_local)

        if len(oos_slice) < 10:
            break

        trades, eq_ts = backtest_symbol(
            symbol,
            oos_sig,
            {**cfg, 'x': best['x'], 'ktp': best['ktp']},
            init_eq,
            strategy={**strategy_cfg, 'min_rr': best['min_rr']},
            costs=cost_cfg,
        )

        metrics = calc_metrics(trades, eq_ts) if trades else {}
        oos_profit_total += float(metrics.get('total_pnl', 0.0)) if metrics else 0.0
        row = {
            'window':    step,
            'oos_start': oos_slice.index[0],
            'oos_end':   oos_slice.index[-1],
        }
        row.update({k: v for k, v in metrics.items() if k != 'monthly_pnl_table'})
        row['plateau'] = plateau
        results.append(row)
        step += 1

    if not results:
        return pd.DataFrame(), {}

    oos_df = pd.DataFrame(results)
    oos_df['oos_start'] = pd.to_datetime(oos_df['oos_start'])
    oos_df['oos_end']   = pd.to_datetime(oos_df['oos_end'])

    key_metrics = ['win_rate', 'profit_factor', 'total_return',
                   'max_drawdown', 'sharpe', 'calmar']
    profitable  = int((oos_df.get('total_return', pd.Series(dtype=float)) > 0).sum())

    summary = {
        'n_windows':         len(results),
        'total_trades':      int(oos_df.get('total_trades', pd.Series(0)).sum()),
        'profitable_windows': profitable,
        'pct_profitable':    round(profitable / len(results) * 100, 1),
        'is_profit_total':   round(float(is_profit_total), 2),
        'oos_profit_total':  round(float(oos_profit_total), 2),
        'oos_is_efficiency': round(float(oos_profit_total / is_profit_total), 4)
                             if is_profit_total != 0 else 0.0,
        'best_params_per_window': best_params_per_window,
    }
    summary['plateau_stable_windows'] = sum(
        1 for r in results if r.get('plateau', {}).get('is_plateau', True)
    )
    summary['plateau_stable_pct'] = (
        summary['plateau_stable_windows'] / summary['n_windows']
        if summary['n_windows'] > 0 else 0.0
    )
    summary['efficiency_status'] = (
        'ĐẠT (> 50%)'
        if summary['oos_is_efficiency'] is not None and summary['oos_is_efficiency'] > 0.5
        else 'CHƯA ĐẠT (< 50%)'
    )

    lines = [
        "=" * 55,
        "  KẾT QUẢ WALK-FORWARD",
        "=" * 55,
        f"  Số cửa sổ         : {summary['n_windows']}",
        f"  Cửa sổ có lãi     : {summary['profitable_windows']} ({summary['pct_profitable'] / 100:.1%})",
        f"  Tổng PnL IS        : {summary['is_profit_total']:+.2f}",
        f"  Tổng PnL OOS       : {summary['oos_profit_total']:+.2f}",
        f"  OOS/IS Efficiency  : {summary['oos_is_efficiency']:.1%} — {summary['efficiency_status']}",
        f"  Cửa sổ plateau ổn định: {summary['plateau_stable_windows']}/{summary['n_windows']} ({summary['plateau_stable_pct']:.1%})",
        "=" * 55,
    ]
    if summary['oos_is_efficiency'] is not None and summary['oos_is_efficiency'] < 0.5:
        lines += [
            "  ⚠️  CẢNH BÁO: OOS/IS < 50% — hệ thống có dấu hiệu overfit",
            "  → Thử giảm số tham số optimize hoặc tăng IS window size",
            "=" * 55,
        ]
    summary['report'] = "\n".join(lines)
    print(summary['report'])

    for m in key_metrics:
        if m in oos_df.columns:
            summary[f'{m}_mean']   = round(float(oos_df[m].mean()), 3)
            summary[f'{m}_median'] = round(float(oos_df[m].median()), 3)
            summary[f'{m}_worst']  = round(float(oos_df[m].min()), 3)

    return oos_df, summary
