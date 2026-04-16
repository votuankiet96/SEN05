# =============================================================================
# strategies/shared/execution_engine.py  —  Generic bar-by-bar execution engine
# =============================================================================
"""Engine mô phỏng giao dịch bar-by-bar dùng chung cho mọi strategy.

Module này không import bất kỳ strategy-specific config nào.
Mọi tham số đều được truyền qua `strategy` / `costs` / `cfg` dict.

Hai chế độ:
- backtest_symbol(): backtest đầy đủ với trade log chi tiết.
- backtest_fast()  : backtest rút gọn cho grid-search (chỉ trả KPI).

Helper functions cũng được export:
- build_pending_order(): tạo pending order từ bar data.
- calc_dynamic_slippage(): tính slippage động theo ATR/close.
"""
import numpy as np
import pandas as pd

from .metrics import _bars_per_year


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL DEFAULTS
# Strategy-specific configs sẽ override các giá trị này qua `strategy` param.
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_STRATEGY = {
    "risk_per_trade":       0.005,
    "ftmo_daily_limit":     0.05,
    "ftmo_max_dd":          0.10,
    "trailing_activation":  1.0,
    "pending_ttl_bars":     3,
    "partial_tp_fraction":  0.5,
}

_DEFAULT_COSTS = {
    "slippage_pts":       2,
    "commission_per_lot": 3.5,
}


# ─────────────────────────────────────────────────────────────────────────────
# ORDER UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def build_pending_order(bar, direction: int, x: float, ktp: float,
                        atr: float, ttl: int) -> dict:
    """Tạo cấu trúc pending order từ bar data.

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
    """
    entry = float(bar['high']) + x if direction == 1 else float(bar['low']) - x
    sl    = float(bar['low'])  - x if direction == 1 else float(bar['high']) + x
    tp    = entry + direction * ktp * atr
    return dict(direction=direction, entry=entry, sl=sl, tp=tp,
                atr=atr, ttl=ttl)


def calc_dynamic_slippage(base_slippage: float, atr: float, close: float,
                          k: float = 50) -> float:
    """Tính slippage động theo biến động ATR tương đối.

    Parameters
    ----------
    base_slippage : float
        Slippage nền (point) dùng khi biến động ở mức bình thường.
    atr : float
        Giá trị ATR tại thời điểm khớp lệnh.
    close : float
        Giá đóng cửa hiện tại, dùng để chuẩn hóa ATR theo tỷ lệ ATR/close.
    k : float, default=50
        Hệ số khuếch đại cho thành phần biến động.

    Returns
    -------
    float
        base_slippage * (1 + k * clip(atr / close, 0.0005, 0.01)).
    """
    if close is None or close == 0:
        return float(base_slippage)
    vol_ratio = atr / close
    vol_ratio = min(max(vol_ratio, 0.0005), 0.01)
    return float(base_slippage) * (1 + k * vol_ratio)


# ─────────────────────────────────────────────────────────────────────────────
# FULL BACKTEST ENGINE (trade log + equity curve)
# ─────────────────────────────────────────────────────────────────────────────

def backtest_symbol(symbol: str, df: pd.DataFrame, cfg: dict,
                    init_eq: float, *,
                    strategy: dict | None = None,
                    costs: dict | None = None) -> tuple:
    """
    Backtest chi tiết theo từng bar cho 1 symbol, có trade log đầy đủ.

    Đặc tính mô phỏng:
    - Partial TP: chốt 1 phần vị thế ở TP cố định (partial_tp_fraction).
    - Breakeven: sau partial TP, SL dời ngay về điểm vào.
    - Trailing: kích hoạt theo luật trailing_activation.
    - Reversal: đang giữ lệnh mà xuất hiện tín hiệu ngược thì đóng/mở đảo chiều.

    Parameters
    ----------
    symbol   : key symbol (vd: "US30") — dùng trong trade log, không tra cứu config.
    df       : dữ liệu đã có cột signal (1/-1/0) + chỉ báo (ma, atr, high, low, close, open, BarTime).
    cfg      : cấu hình symbol gồm:
               x, contract_value, spread_pts, point_size,
               swap_long_per_lot_per_day, swap_short_per_lot_per_day, ktp.
    init_eq  : vốn khởi điểm của symbol.
    strategy : ghi đè tạm các tham số strategy (risk_per_trade, trailing_activation, ...).
    costs    : ghi đè tạm các tham số chi phí (slippage_pts, commission_per_lot, ...).

    Returns
    -------
    (trades, eq_ts)
    - trades: nhật ký giao dịch chi tiết từng lệnh.
    - eq_ts : chuỗi equity theo thời gian.
    """
    s = {**_DEFAULT_STRATEGY, **(strategy or {})}
    c = {**_DEFAULT_COSTS, **(costs or {})}

    x              = cfg['x']
    contract_val   = cfg.get('contract_value', 0)
    spread_pts     = cfg.get('spread_pts', 0)
    point_size     = cfg.get('point_size', 1.0)
    swap_long_day  = cfg.get('swap_long_per_lot_per_day', 0.0)
    swap_short_day = cfg.get('swap_short_per_lot_per_day', 0.0)
    sym_ktp        = cfg.get('ktp', s.get('ktp', 2.3))

    risk_pct     = s['risk_per_trade']
    daily_limit  = s['ftmo_daily_limit']
    max_dd       = s['ftmo_max_dd']
    trail_act    = s['trailing_activation']
    ttl          = s['pending_ttl_bars']
    partial_frac = s.get('partial_tp_fraction', 0.5)
    comm_per_lot = c['commission_per_lot']

    def _dynamic_slippage(bar: pd.Series) -> float:
        return calc_dynamic_slippage(
            base_slippage=c['slippage_pts'],
            atr=bar['atr'],
            close=bar['close'],
            k=c.get('slippage_k', 50),
        )

    trades, eq_log = [], []
    equity         = init_eq
    position       = None
    pending        = None
    daily_pnl      = 0.0
    current_day    = None
    daily_stop     = False
    bars           = df.reset_index()

    for i, bar in bars.iterrows():
        bar_dt   = bar['BarTime']
        bar_date = bar_dt.date()
        slippage = _dynamic_slippage(bar)
        if bar_date != current_day:
            current_day = bar_date
            daily_pnl   = 0.0
            daily_stop  = False
        if equity <= init_eq * (1 - max_dd):
            eq_log.append((bar_dt, equity))
            break

            # ── Khớp pending order (nếu có) ───────────────────────────────────
        if pending and not position:
            pending['ttl'] -= 1
            if pending['ttl'] < 0:
                pending = None
            else:
                d, entry = pending['direction'], pending['entry']
                filled = (d == 1 and bar['high'] >= entry) or \
                         (d == -1 and bar['low'] <= entry)
                if filled and not daily_stop:
                    cost         = slippage + spread_pts
                    actual_entry = entry + d * cost
                    sl_dist      = abs(actual_entry - pending['sl'])
                    if sl_dist > 0:
                        risk_usd   = equity * risk_pct
                        commission = 0.0
                        lot_size   = 0.0
                        if contract_val > 0:
                            lot_size   = risk_usd / ((sl_dist / point_size) * contract_val)
                            commission = 2 * lot_size * comm_per_lot
                        position = dict(
                            direction=d, entry=actual_entry,
                            sl=pending['sl'],
                            sl_at_entry=pending['sl'],
                            tp=pending['tp'],
                            tp_at_entry=pending['tp'],
                            atr=pending['atr'], risk_usd=risk_usd,
                            sl_dist=sl_dist, entry_time=bar_dt,
                            trail_active=False, commission=commission,
                            lot_size=lot_size,
                            partial_tp_hit=False,
                            half1_exit=None, half1_exit_time=None,
                            half1_pnl_gross=0.0, half1_commission=0.0,
                        )
                        pending = None

        # ── Quản lý vị thế đang mở ─────────────────────────────────────────
        if position:
            d     = position['direction']
            sl    = position['sl']
            tp    = position['tp']
            entry = position['entry']
            risk  = position['risk_usd']
            sl_d  = position['sl_dist']
            comm  = position['commission']
            exit_p = exit_r = None

            # Phase 1 — SL ưu tiên: nếu quét trúng SL thì đóng toàn bộ.
            if   d == 1  and bar['low']  <= sl: exit_p, exit_r = sl - slippage, 'SL'
            elif d == -1 and bar['high'] >= sl: exit_p, exit_r = sl + slippage, 'SL'

            # Phase 2 — Partial TP (chỉ chạy 1 lần đầu tiên khi chạm TP).
            elif not position['partial_tp_hit']:
                hit_tp = (d == 1 and bar['high'] >= tp) or \
                         (d == -1 and bar['low']  <= tp)
                if hit_tp:
                    r_h1        = (tp - entry) * d / sl_d
                    gross_h1    = partial_frac * r_h1 * risk
                    comm_h1     = partial_frac * comm
                    net_h1      = gross_h1 - comm_h1
                    position.update(
                        partial_tp_hit=True,
                        half1_exit=tp, half1_exit_time=bar_dt,
                        half1_pnl_gross=gross_h1, half1_commission=comm_h1,
                        sl=entry,
                        tp=float('inf') if d == 1 else float('-inf'),
                        trail_active=True,
                    )
                    equity    += net_h1
                    daily_pnl += net_h1
                    if daily_pnl <= -(init_eq * daily_limit):
                        daily_stop = True

            # Phase 3 — Trailing stop theo MA.
            if exit_p is None:
                if not position['trail_active']:
                    unrealized = (bar['close'] - entry) * d
                    if unrealized >= position['atr'] * trail_act:
                        position['trail_active'] = True
                if position['trail_active'] and not np.isnan(bar['ma']):
                    new_sl = bar['ma']
                    if   d == 1  and new_sl > position['sl']:
                        position['sl'] = new_sl
                    elif d == -1 and new_sl < position['sl']:
                        position['sl'] = new_sl

            # Phase 4 — Ghi nhận đóng lệnh và lưu trade log.
            if exit_p is not None:
                half_frac = (1 - partial_frac) if position['partial_tp_hit'] else 1.0
                r_h2      = (exit_p - entry) * d / sl_d
                gross_h2  = half_frac * r_h2 * risk
                comm_h2   = half_frac * comm
                net_h2    = gross_h2 - comm_h2

                if position['partial_tp_hit']:
                    total_gross = position['half1_pnl_gross'] + gross_h2
                    total_comm  = position['half1_commission'] + comm_h2
                    r_h1        = (position['half1_exit'] - entry) * d / sl_d
                    r_mult      = partial_frac * r_h1 + (1 - partial_frac) * r_h2
                    tp_log      = round(position['half1_exit'], 4)
                else:
                    total_gross = gross_h2
                    total_comm  = comm_h2
                    r_mult      = r_h2
                    tp_log      = round(position['tp_at_entry'], 4)

                total_net  = total_gross - total_comm
                holding_days = max((bar_dt.date() - position['entry_time'].date()).days, 0)
                swap_cost = (holding_days * swap_long_day * position.get('lot_size', 0.0)
                             if d == 1
                             else holding_days * swap_short_day * position.get('lot_size', 0.0))

                total_net  -= swap_cost
                close_net   = net_h2 - swap_cost
                equity     += close_net
                daily_pnl  += close_net

                trades.append(dict(
                    symbol=symbol,
                    direction='BUY' if d == 1 else 'SELL',
                    entry_time=position['entry_time'], exit_time=bar_dt,
                    entry=round(entry, 4), exit=round(exit_p, 4),
                    sl_initial=round(position['sl_at_entry'], 4), tp=tp_log,
                    r_multiple=round(r_mult, 3),
                    pnl_gross=round(total_gross, 2),
                    commission=round(total_comm, 2),
                    swap_cost=round(swap_cost, 2),
                    pnl_usd=round(total_net, 2), equity=round(equity, 2),
                    exit_reason=exit_r,
                    partial_tp_hit=position['partial_tp_hit'],
                    half1_exit=position['half1_exit'],
                    half1_exit_time=position['half1_exit_time'],
                ))
                position = None
                if daily_pnl <= -(init_eq * daily_limit):
                    daily_stop = True

        # ── Reversal: đang có lệnh mà xuất hiện tín hiệu ngược chiều ───────
        if position and not daily_stop:
            sig = int(bar['signal'])
            if sig != 0 and sig != position['direction']:
                d         = position['direction']
                half_frac = (1 - partial_frac) if position['partial_tp_hit'] else 1.0
                ep_rev    = bar['close'] - d * slippage
                comm      = position['commission']

                r_h2      = (ep_rev - position['entry']) * d / position['sl_dist']
                gross_h2  = half_frac * r_h2 * position['risk_usd']
                comm_h2   = half_frac * comm
                net_h2    = gross_h2 - comm_h2

                if position['partial_tp_hit']:
                    total_gross = position['half1_pnl_gross'] + gross_h2
                    total_comm  = position['half1_commission'] + comm_h2
                    r_h1        = (position['half1_exit'] - position['entry']) * d / position['sl_dist']
                    r_mult      = partial_frac * r_h1 + (1 - partial_frac) * r_h2
                    tp_log      = round(position['half1_exit'], 4)
                else:
                    total_gross = gross_h2
                    total_comm  = comm_h2
                    r_mult      = r_h2
                    tp_log      = round(position['tp_at_entry'], 4)

                total_net  = total_gross - total_comm
                holding_days = max((bar_dt.date() - position['entry_time'].date()).days, 0)
                swap_cost = (holding_days * swap_long_day * position.get('lot_size', 0.0)
                             if d == 1
                             else holding_days * swap_short_day * position.get('lot_size', 0.0))

                total_net  -= swap_cost
                close_net   = net_h2 - swap_cost
                equity     += close_net
                daily_pnl  += close_net

                trades.append(dict(
                    symbol=symbol,
                    direction='BUY' if d == 1 else 'SELL',
                    entry_time=position['entry_time'], exit_time=bar_dt,
                    entry=round(position['entry'], 4), exit=round(ep_rev, 4),
                    sl_initial=round(position['sl_at_entry'], 4), tp=tp_log,
                    r_multiple=round(r_mult, 3),
                    pnl_gross=round(total_gross, 2),
                    commission=round(total_comm, 2),
                    swap_cost=round(swap_cost, 2),
                    pnl_usd=round(total_net, 2), equity=round(equity, 2),
                    exit_reason='REVERSED',
                    partial_tp_hit=position['partial_tp_hit'],
                    half1_exit=position['half1_exit'],
                    half1_exit_time=position['half1_exit_time'],
                ))
                position = None
                if daily_pnl <= -(init_eq * daily_limit):
                    daily_stop = True

                if not daily_stop and i + 1 < len(bars):
                    atr_v   = float(bar['atr'])
                    pending = build_pending_order(bar, sig, x, sym_ktp, atr_v, ttl)

        # ── Không có vị thế: nếu có signal thì tạo pending order mới ─────────
        if not position and not pending and not daily_stop:
            sig = int(bar['signal'])
            if sig != 0 and i + 1 < len(bars):
                atr_v   = float(bar['atr'])
                pending = build_pending_order(bar, sig, x, sym_ktp, atr_v, ttl)

        eq_log.append((bar_dt, equity))

    # ── Hết dữ liệu nhưng vẫn còn vị thế: force-close tại bar cuối ───────────
    if position and len(bars):
        bar       = bars.iloc[-1]
        d         = position['direction']
        ep        = bar['close']
        comm      = position['commission']
        half_frac = (1 - partial_frac) if position['partial_tp_hit'] else 1.0

        r_h2     = (ep - position['entry']) * d / position['sl_dist']
        gross_h2 = half_frac * r_h2 * position['risk_usd']
        comm_h2  = half_frac * comm
        net_h2   = gross_h2 - comm_h2

        if position['partial_tp_hit']:
            total_gross = position['half1_pnl_gross'] + gross_h2
            total_comm  = position['half1_commission'] + comm_h2
            r_h1        = (position['half1_exit'] - position['entry']) * d / position['sl_dist']
            r_mult      = partial_frac * r_h1 + (1 - partial_frac) * r_h2
            tp_log      = round(position['half1_exit'], 4)
        else:
            total_gross = gross_h2
            total_comm  = comm_h2
            r_mult      = r_h2
            tp_log      = round(position['tp_at_entry'], 4)

        total_net = total_gross - total_comm
        holding_days = max((bar['BarTime'].date() - position['entry_time'].date()).days, 0)
        swap_cost = (holding_days * swap_long_day * position.get('lot_size', 0.0)
                     if d == 1
                     else holding_days * swap_short_day * position.get('lot_size', 0.0))

        total_net -= swap_cost
        close_net  = net_h2 - swap_cost
        equity    += close_net

        trades.append(dict(
            symbol=symbol,
            direction='BUY' if d == 1 else 'SELL',
            entry_time=position['entry_time'], exit_time=bar['BarTime'],
            entry=round(position['entry'], 4), exit=round(ep, 4),
            sl_initial=round(position['sl_at_entry'], 4), tp=tp_log,
            r_multiple=round(r_mult, 3),
            pnl_gross=round(total_gross, 2),
            commission=round(total_comm, 2),
            swap_cost=round(swap_cost, 2),
            pnl_usd=round(total_net, 2), equity=round(equity, 2),
            exit_reason='FORCE_CLOSE',
            partial_tp_hit=position['partial_tp_hit'],
            half1_exit=position['half1_exit'],
            half1_exit_time=position['half1_exit_time'],
        ))
        if eq_log:
            eq_log[-1] = (eq_log[-1][0], equity)

    eq_ts = pd.Series(
        [v for _, v in eq_log],
        index=pd.DatetimeIndex([t for t, _ in eq_log]),
    )
    return trades, eq_ts


# ─────────────────────────────────────────────────────────────────────────────
# FAST BACKTEST (optimizer — metrics only, no trade log)
# ─────────────────────────────────────────────────────────────────────────────

def backtest_fast(symbol: str, df_ind: pd.DataFrame, cfg: dict,
                  ktp: float, x_actual: float, trailing_act: float,
                  date_from: str, date_to: str, init_eq: float, *,
                  strategy: dict | None = None,
                  costs: dict | None = None,
                  tf: str = 'H4') -> dict:
    """
    Backtest rút gọn để optimizer chạy nhanh (grid search).

    Khác với backtest_symbol:
    - Không lưu trade log chi tiết.
    - Chỉ trả về các KPI lõi để chấm điểm tham số.
    - Signal được tính inline để giảm overhead.

    Parameters
    ----------
    symbol      : key symbol (dùng cho log nếu cần).
    df_ind      : dữ liệu đã có indicators + signal columns.
    cfg         : cấu hình symbol.
    ktp         : hệ số TP theo ATR (override cho optimizer).
    x_actual    : breakout buffer (override cho optimizer).
    trailing_act: trailing activation (override cho optimizer).
    date_from   : mốc bắt đầu window test (inclusive).
    date_to     : mốc kết thúc window test (inclusive).
    init_eq     : vốn khởi điểm.
    strategy    : override strategy params.
    costs       : override cost params.
    tf          : timeframe code để annualize Sharpe đúng cách.
    """
    s = {**_DEFAULT_STRATEGY, **(strategy or {})}
    c = {**_DEFAULT_COSTS, **(costs or {})}

    hours        = cfg.get('session_hours_utc', [])
    min_rr       = s.get('min_rr', 1.25)
    risk_pct     = s['risk_per_trade']
    daily_limit  = s['ftmo_daily_limit']
    max_dd       = s['ftmo_max_dd']
    ttl          = s['pending_ttl_bars']

    def _dynamic_slippage(bar: pd.Series) -> float:
        return calc_dynamic_slippage(
            base_slippage=c['slippage_pts'],
            atr=bar['atr'],
            close=bar['close'],
            k=c.get('slippage_k', 50),
        )

    comm_per_lot = c['commission_per_lot']
    contract_val = cfg.get('contract_value', 0)
    spread_pts   = cfg.get('spread_pts', 0)
    point_size   = cfg.get('point_size', 1.0)
    partial_frac = s.get('partial_tp_fraction', 0.5)

    # ── Tạo mask tín hiệu nhanh (vectorized) ───────────────────────────────
    sess = pd.Series(df_ind.index.hour.isin(hours), index=df_ind.index) \
           if hours else pd.Series(True, index=df_ind.index)
    in_w = ((df_ind.index >= pd.Timestamp(date_from)) &
            (df_ind.index <= pd.Timestamp(date_to)))
    valid = (sess & in_w &
             df_ind['ma'].notna() & df_ind['prev_ma'].notna() &
             df_ind['macd_h'].notna() & df_ind['atr'].notna())

    cross_up   = (df_ind['prev_close'] <= df_ind['prev_ma']) & (df_ind['close'] > df_ind['ma'])
    cross_down = (df_ind['prev_close'] >= df_ind['prev_ma']) & ~(df_ind['close'] > df_ind['ma'])

    sl_dist_all = df_ind['high'] - df_ind['low'] + 2 * x_actual
    tp_dist_all = ktp * df_ind['atr']
    rr_all      = (tp_dist_all / sl_dist_all.replace(0, np.nan)).fillna(0)
    rr_ok       = rr_all >= min_rr

    buy_raw  = (valid & cross_up   & (df_ind['close'] > df_ind['open']) &
                (df_ind['macd_h'] > 0) & rr_ok)
    sell_raw = (valid & cross_down & (df_ind['close'] < df_ind['open']) &
                (df_ind['macd_h'] < 0) & rr_ok)

    # ── Vòng lặp bar-by-bar với trạng thái tối giản ────────────────────────
    trades_pnl = []
    equity     = init_eq
    position   = None
    pending    = None
    daily_pnl  = 0.0
    cur_day    = None
    d_stop     = False
    peak_eq       = init_eq
    running_maxdd = 0.0
    bars          = df_ind.reset_index()

    for i, bar in bars.iterrows():
        bar_dt = bar['BarTime']
        bdate  = bar_dt.date()
        slippage = _dynamic_slippage(bar)
        if bdate != cur_day:
            cur_day   = bdate
            daily_pnl = 0.0
            d_stop    = False
        if equity <= init_eq * (1 - max_dd):
            break

        # ── Khớp pending ────────────────────────────────────────────────────
        if pending and not position:
            pending['ttl'] -= 1
            if pending['ttl'] < 0:
                pending = None
            else:
                d, entry = pending['d'], pending['entry']
                filled = (d == 1 and bar['high'] >= entry) or \
                         (d == -1 and bar['low'] <= entry)
                if filled and not d_stop:
                    cost    = slippage + spread_pts
                    act_e   = entry + d * cost
                    sl_dist = abs(act_e - pending['sl'])
                    if sl_dist > 0:
                        risk_usd = equity * risk_pct
                        comm = 0.0
                        if contract_val > 0:
                            lots = risk_usd / ((sl_dist / point_size) * contract_val)
                            comm = 2 * lots * comm_per_lot
                        position = dict(
                            d=d, entry=act_e,
                            sl=pending['sl'], tp=pending['tp'],
                            atr=pending['atr'], risk=risk_usd,
                            sl_dist=sl_dist, trail=False, comm=comm,
                            partial_tp_hit=False, half1_pnl=0.0,
                        )
                        pending = None

        # ── Quản lý vị thế đang mở ──────────────────────────────────────────
        if position:
            d   = position['d']
            sl  = position['sl']
            tp  = position['tp']
            ep  = None

            if   d == 1  and bar['low']  <= sl: ep = sl - slippage
            elif d == -1 and bar['high'] >= sl: ep = sl + slippage

            elif not position['partial_tp_hit']:
                hit_tp = (d == 1 and bar['high'] >= tp) or \
                         (d == -1 and bar['low']  <= tp)
                if hit_tp:
                    r_h1      = (tp - position['entry']) * d / position['sl_dist']
                    gross_h1  = partial_frac * r_h1 * position['risk']
                    comm_h1   = partial_frac * position['comm']
                    net_h1    = gross_h1 - comm_h1
                    position.update(
                        partial_tp_hit=True, half1_pnl=net_h1,
                        sl=position['entry'],
                        tp=float('inf') if d == 1 else float('-inf'),
                        trail=True,
                    )
                    equity    += net_h1
                    daily_pnl += net_h1
                    peak_eq    = max(peak_eq, equity)
                    running_maxdd = max(running_maxdd,
                                        (peak_eq - equity) / peak_eq * 100 if peak_eq > 0 else 0.0)
                    if daily_pnl <= -(init_eq * daily_limit):
                        d_stop = True

            if ep is None:
                if not position['trail']:
                    unreal = (bar['close'] - position['entry']) * position['d']
                    if unreal >= position['atr'] * trailing_act:
                        position['trail'] = True
                if position['trail'] and not np.isnan(bar['ma']):
                    new_sl = bar['ma']
                    if   d == 1  and new_sl > position['sl']:
                        position['sl'] = new_sl
                    elif d == -1 and new_sl < position['sl']:
                        position['sl'] = new_sl

            if ep is not None:
                half_frac = (1 - partial_frac) if position['partial_tp_hit'] else 1.0
                r_h2      = (ep - position['entry']) * d / position['sl_dist']
                gross_h2  = half_frac * r_h2 * position['risk']
                comm_h2   = half_frac * position['comm']
                net_h2    = gross_h2 - comm_h2
                combined  = position['half1_pnl'] + net_h2 if position['partial_tp_hit'] \
                            else net_h2
                equity    += net_h2
                daily_pnl += net_h2
                peak_eq    = max(peak_eq, equity)
                running_maxdd = max(running_maxdd,
                                    (peak_eq - equity) / peak_eq * 100 if peak_eq > 0 else 0.0)
                trades_pnl.append(combined)
                position   = None
                if daily_pnl <= -(init_eq * daily_limit):
                    d_stop = True

        # ── Reversal trong chế độ fast ──────────────────────────────────────
        if position and not d_stop:
            sig = 1 if buy_raw.iloc[i] else (-1 if sell_raw.iloc[i] else 0)
            if sig != 0 and sig != position['d']:
                d_cur     = position['d']
                half_frac = (1 - partial_frac) if position['partial_tp_hit'] else 1.0
                ep_rev    = bar['close'] - d_cur * slippage
                r_h2      = (ep_rev - position['entry']) * d_cur / position['sl_dist']
                gross_h2  = half_frac * r_h2 * position['risk']
                comm_h2   = half_frac * position['comm']
                net_h2    = gross_h2 - comm_h2
                combined  = position['half1_pnl'] + net_h2 if position['partial_tp_hit'] \
                            else net_h2
                equity    += net_h2
                daily_pnl += net_h2
                peak_eq    = max(peak_eq, equity)
                running_maxdd = max(running_maxdd,
                                    (peak_eq - equity) / peak_eq * 100 if peak_eq > 0 else 0.0)
                trades_pnl.append(combined)
                position   = None
                if daily_pnl <= -(init_eq * daily_limit):
                    d_stop = True
                if not d_stop and i + 1 < len(bars):
                    atr_v   = float(bar['atr'])
                    pending = build_pending_order(bar, sig, x_actual, ktp, atr_v, ttl)
                    pending['d'] = pending.pop('direction')

        # ── Không có vị thế: tạo pending mới khi có tín hiệu ────────────────
        if not position and not pending and not d_stop and i + 1 < len(bars):
            sig = 1 if buy_raw.iloc[i] else (-1 if sell_raw.iloc[i] else 0)
            if sig != 0:
                atr_v   = float(bar['atr'])
                pending = build_pending_order(bar, sig, x_actual, ktp, atr_v, ttl)
                pending['d'] = pending.pop('direction')

    # ── Tính KPI rút gọn cho optimizer ───────────────────────────────────────
    if not trades_pnl:
        return dict(trades=0, pf=0, ret=0, maxdd=0, score=0, sharpe=0)

    wins = [p for p in trades_pnl if p > 0]
    loss = [p for p in trades_pnl if p <= 0]
    gp   = sum(wins) if wins else 0
    gl   = sum(abs(p) for p in loss) if loss else 0
    pf   = gp / gl if gl > 0 else float('inf')
    ret  = (equity / init_eq - 1) * 100
    maxdd = running_maxdd
    score = pf * np.sqrt(max(ret, 0)) / max(maxdd, 1) if pf > 1 and ret > 0 else 0

    # Sharpe annualized theo số lệnh/năm (không dùng bars_per_year)
    eq_fast   = pd.Series([init_eq] + list(init_eq + np.cumsum(trades_pnl)))
    rets_fast = eq_fast.pct_change().dropna()
    if len(rets_fast) > 1 and rets_fast.std() > 0:
        # Ước tính số lệnh mỗi năm từ độ dài df_ind
        years_span = max(
            (df_ind.index[-1] - df_ind.index[0]).days / 365.25, 0.1
        ) if len(df_ind) > 1 else 1.0
        n_per_year = len(trades_pnl) / years_span
        sharpe = float(rets_fast.mean() / rets_fast.std() * np.sqrt(max(n_per_year, 1)))
    else:
        sharpe = 0.0

    return dict(
        trades=len(trades_pnl), pf=round(min(pf, 9.9), 2),
        ret=round(ret, 2), maxdd=round(maxdd, 2), score=round(score, 4),
        sharpe=round(sharpe, 4),
    )
