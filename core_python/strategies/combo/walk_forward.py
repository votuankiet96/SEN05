# =============================================================================
# strategies/combo/walk_forward.py  —  IS/OOS Walk-forward optimization
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

from ..shared.execution_engine import backtest_fast, backtest_symbol
from ..shared.metrics import calc_metrics
from .strategy_config import (
    STRATEGY,
    SYMBOLS,
    get_indicator_params,
    get_symbol_params,
)
from .signal_logic import add_combo_indicators, detect_combo_signals, session_mask


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
    keys = ['ktp', 'x', 'trailing_activation', 'ma_period']
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
                'ktp': float(k[0]),
                'x': float(k[1]),
                'trailing_activation': float(k[2]),
                'ma_period': int(k[3]),
            }
        elif isinstance(k, dict):
            point = {
                'ktp': float(k.get('ktp', 0.0)),
                'x': float(k.get('x', 0.0)),
                'trailing_activation': float(k.get('trailing_activation', 0.0)),
                'ma_period': int(k.get('ma_period', 0)),
            }
        else:
            continue

        sharpe_v = float(v.get('sharpe', 0.0)) if isinstance(v, dict) else float(v)
        key_t = (
            float(point['ktp']),
            float(point['x']),
            float(point['trailing_activation']),
            int(point['ma_period']),
        )
        sharpe_map[key_t] = sharpe_v
        points.append(point)

    if not sharpe_map:
        return default

    neighbor_values = {}
    for k in keys:
        vals = sorted({float(p[k]) if k != 'ma_period' else int(p[k]) for p in points})
        if not vals:
            return default
        best_v = best_params.get(k)
        if k == 'ma_period':
            best_v = int(best_v)
        else:
            best_v = float(best_v)
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
        float(best_params['trailing_activation']),
        int(best_params['ma_period']),
    )
    min_sharpe = float(best_sharpe) * float(threshold_ratio)
    neighbors_checked = 0
    neighbors_stable  = 0

    for ktp_v in neighbor_values['ktp']:
        for x_v in neighbor_values['x']:
            for tr_v in neighbor_values['trailing_activation']:
                for ma_v in neighbor_values['ma_period']:
                    combo = (float(ktp_v), float(x_v), float(tr_v), int(ma_v))
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
    def _build_param_grid(sym_key: str, cfg_local: dict) -> dict:
        base = get_symbol_params(sym_key) if sym_key in SYMBOLS else {
            'ktp': STRATEGY['ktp'],
            'x': cfg_local.get('x', 1.0),
            'trailing_activation': STRATEGY['trailing_activation'],
            'ma_period': get_indicator_params()['MA_PERIOD'],
        }

        ktp0 = float(base['ktp'])
        x0   = float(base['x'])
        tr0  = float(base['trailing_activation'])
        ma0  = int(base['ma_period'])

        return {
            'ktp':     sorted({round(ktp0 * 0.9, 2), round(ktp0, 2), round(ktp0 * 1.1, 2)}),
            'x':       sorted({round(max(0.1, x0 - 2.0), 2), round(x0, 2), round(x0 + 2.0, 2)}),
            'trailing': sorted({round(max(0.1, tr0 * 0.75), 2), round(tr0, 2), round(tr0 * 1.25, 2)}),
            'ma_period': sorted({max(5, ma0 - 5), ma0, ma0 + 5}),
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
        grid = _build_param_grid(symbol, cfg)
        best = None
        best_sharpe  = -np.inf
        grid_results = {}

        for ma_p in grid['ma_period']:
            is_raw = is_slice[['open', 'high', 'low', 'close']].copy()
            is_raw['volume'] = is_slice['volume'] if 'volume' in is_slice.columns else 0.0
            is_ind_local = add_combo_indicators(is_raw, {'MA_PERIOD': int(ma_p)})

            date_from_is = str(is_ind_local.index[0])
            date_to_is   = str(is_ind_local.index[-1])

            for ktp_v in grid['ktp']:
                for x_v in grid['x']:
                    for tr_v in grid['trailing']:
                        cfg_is = {**cfg, 'x': x_v}
                        fast_metrics = backtest_fast(
                            symbol,
                            is_ind_local,
                            cfg_is,
                            ktp=float(ktp_v),
                            x_actual=float(x_v),
                            trailing_act=float(tr_v),
                            date_from=date_from_is,
                            date_to=date_to_is,
                            init_eq=init_eq,
                            strategy={**(strategy or {}), 'trailing_activation': float(tr_v)},
                            costs=costs,
                        )
                        sh = float(fast_metrics.get('sharpe', 0.0))
                        grid_results[(float(ktp_v), float(x_v), float(tr_v), int(ma_p))] = sh
                        if sh > best_sharpe:
                            best_sharpe = sh
                            best = {
                                'ktp': float(ktp_v),
                                'x': float(x_v),
                                'trailing_activation': float(tr_v),
                                'ma_period': int(ma_p),
                                'is_fast': fast_metrics,
                            }

        if best is None:
            break

        best_params = {
            'ktp': best['ktp'],
            'x': best['x'],
            'trailing_activation': best['trailing_activation'],
            'ma_period': best['ma_period'],
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
            'window': step,
            'ktp': best['ktp'],
            'x': best['x'],
            'trailing_activation': best['trailing_activation'],
            'ma_period': best['ma_period'],
            'is_sharpe': best_sharpe,
            'plateau': plateau,
        })

        is_profit_total += init_eq * float(best['is_fast'].get('ret', 0.0)) / 100.0

        # Step 3 — Validate trên OOS với best_params từ IS
        oos_raw = oos_slice[['open', 'high', 'low', 'close']].copy()
        oos_raw['volume'] = oos_slice['volume'] if 'volume' in oos_slice.columns else 0.0
        oos_ind_local = add_combo_indicators(oos_raw, {'MA_PERIOD': int(best['ma_period'])})
        oos_mask      = session_mask(oos_ind_local, cfg.get('session_hours_utc', []))

        # FIX: truyền X optimized vào params để detect_combo_signals dùng đúng x
        p_local = get_indicator_params()
        p_local['KTP'] = best['ktp']
        p_local['X']   = best['x']   # override breakout buffer cho RR filter
        oos_sig = detect_combo_signals(oos_ind_local, oos_mask, sym_key=None, params=p_local)

        if len(oos_slice) < 10:
            break

        trades, eq_ts = backtest_symbol(
            symbol,
            oos_sig,
            {**cfg, 'x': best['x'], 'ktp': best['ktp']},
            init_eq,
            strategy={**(strategy or {}), 'trailing_activation': best['trailing_activation']},
            costs=costs,
        )

        metrics = calc_metrics(trades, eq_ts) if trades else {}
        oos_profit_total += float(metrics.get('total_pnl', 0.0)) if metrics else 0.0
        row = {
            'window':    step,
            'oos_start': oos_slice.index[0],
            'oos_end':   oos_slice.index[-1],
        }
        row.update(metrics)
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
        if summary.get('oos_is_efficiency') and summary['oos_is_efficiency'] > 0.5
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
    if summary.get('oos_is_efficiency') and summary['oos_is_efficiency'] < 0.5:
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
