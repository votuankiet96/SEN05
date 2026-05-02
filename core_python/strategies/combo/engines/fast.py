"""Fast Combo pending-order backtest engine for optimizers."""

from __future__ import annotations

import numpy as np
import pandas as pd

from core_python.shared.analytics import calc_trade_level_return_stats
from core_python.shared.execution.primitives import calc_dynamic_slippage, swap_cost
from core_python.strategies.combo.engines.common import (
    _DEFAULT_COSTS,
    _DEFAULT_STRATEGY,
    _adverse_exit_price,
    _max_drawdown_breached,
    _resolve_strategy_limits,
    _risk_lot_size,
    build_pending_order,
    make_fast_position,
)


def _timestamp_for_index(value, index: pd.Index) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if isinstance(index, pd.DatetimeIndex):
        if index.tz is not None and ts.tzinfo is None:
            ts = ts.tz_localize(index.tz)
        elif index.tz is None and ts.tzinfo is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        elif index.tz is not None and ts.tzinfo is not None:
            ts = ts.tz_convert(index.tz)
    return ts


def backtest_fast(symbol: str, df_ind: pd.DataFrame, cfg: dict,
                  ktp: float, x_actual: float, trailing_act: float,
                  date_from: str, date_to: str, init_eq: float, *,
                  strategy: dict | None = None,
                  costs: dict | None = None,
                  tf: str = 'H4') -> dict:
    """Backtest rút gọn để optimizer/grid search chạy nhanh.

    Safety note: this is a fast approximation for grid-search ranking, not the
    ground-truth result. Final candidates should be validated and re-ranked with
    `backtest_symbol()` before being used for OOS conclusions.

    Làm gì
    ------
    Hàm này mô phỏng cùng logic giao dịch cốt lõi với `backtest_symbol()` nhưng
    lược bỏ trade log chi tiết. Nó chỉ giữ PnL từng trade và các biến trạng thái
    cần thiết để tính KPI cuối cùng.

    Khác biệt chính so với `backtest_symbol()`:
    - Tín hiệu BUY/SELL được tính inline bằng vectorized mask.
    - Không tạo `trades` chi tiết.
    - Không trả equity curve đầy đủ.
    - Output là dict KPI để optimizer chấm điểm bộ tham số.

    Nhận input gì
    -------------
    `symbol`
        Tên/key symbol, chủ yếu phục vụ log/debug nếu cần.

    `df_ind`
        `DataFrame` đã có indicator: `ma`, `prev_ma`, `macd_h`, `atr`,
        `prev_close`, cùng OHLC và index thời gian.

    `cfg`
        Cấu hình symbol/broker.

    `ktp`
        Hệ số TP theo ATR do optimizer đang thử.

    `x_actual`
        Breakout buffer do optimizer đang thử.

    `trailing_act`
        Ngưỡng kích hoạt trailing stop do optimizer đang thử.

    `date_from`, `date_to`
        Khoảng thời gian window test. Hàm chỉ xét tín hiệu nằm trong khoảng này.

    `init_eq`
        Vốn ban đầu.

    `strategy`, `costs`
        Override strategy/cost tương tự `backtest_symbol()`.

    `tf`
        Timeframe code. Tham số hiện chưa dùng trực tiếp trong công thức ở hàm
        này, nhưng giữ lại để tương thích API và có thể dùng cho annualization.

    Trả output gì
    -------------
    Dict KPI gồm:

    - `trades`: số lệnh đã đóng.
    - `pf`: profit factor, giới hạn hiển thị tối đa 9.9.
    - `ret`: phần trăm lợi nhuận.
    - `maxdd`: max drawdown phần trăm.
    - `score`: điểm tổng hợp dùng cho optimizer.
    - `sharpe`: Sharpe ước tính theo số lệnh/năm.

    Tại sao cần nó
    --------------
    Grid search có thể chạy hàng nghìn bộ tham số. Nếu mỗi lần đều lưu trade log
    đầy đủ như `backtest_symbol()`, chi phí thời gian và bộ nhớ sẽ lớn. Bản fast
    giữ logic đủ giống để ranking tham số, nhưng tối giản output.
    """
    # Merge default với override giống full backtest để hai chế độ dùng chung hệ
    # tham số nền.
    s = {**_DEFAULT_STRATEGY, **(strategy or {})}
    c = {**_DEFAULT_COSTS, **(costs or {})}

    hours        = cfg.get('session_hours_utc', [])
    min_rr       = s.get('min_rr')
    risk_pct     = s['risk_per_trade']
    daily_limit, max_dd = _resolve_strategy_limits(s)
    max_dd_mode  = s.get('max_drawdown_mode', _DEFAULT_STRATEGY["max_drawdown_mode"])
    ttl          = s['pending_ttl_bars']

    base_slip    = cfg.get('slippage_pts', c['slippage_pts'])

    def _dynamic_slippage(bar: pd.Series) -> float:
        return calc_dynamic_slippage(
            base_slippage=base_slip,
            atr=bar['atr'],
            close=bar['close'],
            k=c.get('slippage_k', 50),
        )

    comm_per_lot   = c['commission_per_lot']
    contract_val   = cfg.get('contract_value', 0)
    spread_pts     = cfg.get('spread_pts', 0)
    point_size     = cfg.get('point_size', 1.0)
    partial_frac   = s.get('partial_tp_fraction', 0.5)
    swap_long_day  = cfg.get('swap_long_per_lot_per_day', 0.0)
    swap_short_day = cfg.get('swap_short_per_lot_per_day', 0.0)
    min_lot        = cfg.get('min_lot_size', 0.01)
    max_lot        = cfg.get('max_lot_size', 100.0)
    lot_step       = cfg.get('lot_step', min_lot)

    # Tạo mask tín hiệu nhanh bằng pandas vectorization. Phần này thay cho việc
    # đọc sẵn cột `signal`, giúp optimizer thử trực tiếp `ktp`, `x_actual`,
    # `trailing_act` mà giảm overhead.
    sess = pd.Series(df_ind.index.hour.isin(hours), index=df_ind.index) \
           if hours else pd.Series(True, index=df_ind.index)
    start_ts = (
        _timestamp_for_index(date_from, df_ind.index)
        if date_from is not None else df_ind.index.min()
    )
    end_ts = (
        _timestamp_for_index(date_to, df_ind.index)
        if date_to is not None else df_ind.index.max()
    )
    if pd.isna(start_ts):
        start_ts = df_ind.index.min()
    if pd.isna(end_ts):
        end_ts = df_ind.index.max()
    in_w = ((df_ind.index >= start_ts) &
            (df_ind.index <= end_ts))
    valid = (sess & in_w &
             df_ind['ma'].notna() & df_ind['prev_ma'].notna() &
             df_ind['macd_h'].notna() & df_ind['atr'].notna())

    cross_up   = (df_ind['prev_close'] <= df_ind['prev_ma']) & (df_ind['close'] > df_ind['ma'])
    cross_down = (df_ind['prev_close'] >= df_ind['prev_ma']) & ~(df_ind['close'] > df_ind['ma'])

    sl_dist_all = df_ind['high'] - df_ind['low'] + 2 * x_actual
    tp_dist_all = ktp * df_ind['atr']
    rr_all      = (tp_dist_all / sl_dist_all.replace(0, np.nan)).fillna(0)
    rr_filter_enabled = min_rr is not None and not pd.isna(min_rr)
    rr_ok       = pd.Series(True, index=df_ind.index) if not rr_filter_enabled else rr_all >= float(min_rr)

    # Điều kiện BUY/SELL thô:
    # - Giá cắt MA theo hướng tương ứng.
    # - Nến xác nhận cùng chiều.
    # - MACD histogram cùng chiều.
    # - Risk/reward đạt ngưỡng tối thiểu.
    buy_raw  = (valid & cross_up   & (df_ind['close'] > df_ind['open']) &
                (df_ind['macd_h'] > 0) & rr_ok)
    sell_raw = (valid & cross_down & (df_ind['close'] < df_ind['open']) &
                (df_ind['macd_h'] < 0) & rr_ok)

    # Vòng lặp bar-by-bar với trạng thái tối giản. Thay vì lưu trade dict, hàm
    # chỉ lưu `combined` PnL của mỗi lệnh vào `trades_pnl`.
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
        # Reset daily stop khi sang ngày mới, giống full backtest.
        bar_dt = bar['BarTime']
        bdate  = bar_dt.date()
        slippage = _dynamic_slippage(bar)
        if bdate != cur_day:
            cur_day   = bdate
            daily_pnl = 0.0
            d_stop    = False
        if _max_drawdown_breached(equity, init_eq, peak_eq, max_dd, max_dd_mode):
            break

        # Khớp pending nếu giá của bar chạm entry trước khi TTL hết hạn.
        if pending and not position:
            pending['ttl'] -= 1
            if pending['ttl'] < 0:
                pending = None
            else:
                d, entry = pending['d'], pending['entry']
                filled = (d == 1 and bar['high'] >= entry) or \
                         (d == -1 and bar['low'] <= entry)
                if filled and not d_stop:
                    # Fast mode vẫn tính spread, slippage, lot size và commission
                    # để KPI không bị quá lạc quan.
                    cost    = slippage + spread_pts
                    act_e   = entry + d * cost
                    sl_dist = abs(act_e - pending['sl'])
                    if sl_dist > 0:
                        risk_usd = equity * risk_pct
                        lots = _risk_lot_size(
                            risk_usd,
                            sl_dist,
                            point_size=point_size,
                            contract_value=contract_val,
                            commission_per_lot=comm_per_lot,
                            min_lot=min_lot,
                            max_lot=max_lot,
                            lot_step=lot_step,
                        )
                        if lots <= 0:
                            pending = None
                        else:
                            comm = 2 * lots * comm_per_lot
                            position = make_fast_position(
                                direction=d,
                                entry=act_e,
                                sl=pending['sl'],
                                tp=pending['tp'],
                                atr=pending['atr'],
                                risk_usd=risk_usd,
                                sl_dist=sl_dist,
                                commission=comm,
                                lot_size=lots,
                                entry_date=bdate,
                            )
                            pending = None

        # Quản lý vị thế đang mở: SL ưu tiên, rồi partial TP, rồi trailing stop.
        if position:
            d   = position['d']
            sl  = position['sl']
            tp  = position['tp']
            ep  = None

            if   d == 1  and bar['low']  <= sl: ep = _adverse_exit_price(sl, d, spread_pts, slippage)
            elif d == -1 and bar['high'] >= sl: ep = _adverse_exit_price(sl, d, spread_pts, slippage)

            elif not position['partial_tp_hit']:
                hit_tp = (d == 1 and bar['high'] >= tp) or \
                         (d == -1 and bar['low']  <= tp)
                if hit_tp:
                    # Partial TP trong fast mode chỉ lưu net PnL phần 1, không lưu
                    # chi tiết exit_time/exit_price như full mode.
                    half_exit = _adverse_exit_price(tp, d, spread_pts, slippage)
                    r_h1      = (half_exit - position['entry']) * d / position['sl_dist']
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
                # Khi vị thế đóng, chỉ append tổng PnL của trade vào `trades_pnl`.
                half_frac  = (1 - partial_frac) if position['partial_tp_hit'] else 1.0
                r_h2       = (ep - position['entry']) * d / position['sl_dist']
                gross_h2   = half_frac * r_h2 * position['risk']
                comm_h2    = half_frac * position['comm']
                holding    = max((bdate - position['entry_date']).days, 0)
                swap_fee = swap_cost(holding, position['lot_size'], d, swap_long_day, swap_short_day)
                net_h2     = gross_h2 - comm_h2 - swap_fee
                combined   = position['half1_pnl'] + net_h2 if position['partial_tp_hit'] \
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

        # Reversal trong chế độ fast: đóng vị thế hiện tại khi tín hiệu ngược xuất
        # hiện, sau đó có thể tạo pending mới cho hướng ngược.
        if position and not d_stop:
            sig = 1 if buy_raw.iloc[i] else (-1 if sell_raw.iloc[i] else 0)
            if sig != 0 and sig != position['d']:
                d_cur      = position['d']
                half_frac  = (1 - partial_frac) if position['partial_tp_hit'] else 1.0
                exit_bar   = bars.iloc[i + 1] if i + 1 < len(bars) else bar
                exit_slip  = _dynamic_slippage(exit_bar) if i + 1 < len(bars) else slippage
                exit_px    = exit_bar['open'] if i + 1 < len(bars) else bar['close']
                ep_rev     = _adverse_exit_price(exit_px, d_cur, spread_pts, exit_slip)
                r_h2       = (ep_rev - position['entry']) * d_cur / position['sl_dist']
                gross_h2   = half_frac * r_h2 * position['risk']
                comm_h2    = half_frac * position['comm']
                holding    = max((exit_bar['BarTime'].date() - position['entry_date']).days, 0)
                swap_fee = swap_cost(holding, position['lot_size'], d_cur, swap_long_day, swap_short_day)
                net_h2     = gross_h2 - comm_h2 - swap_fee
                combined   = position['half1_pnl'] + net_h2 if position['partial_tp_hit'] \
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

        # Không có vị thế và không có pending: tạo pending mới nếu bar hiện tại có
        # tín hiệu hợp lệ.
        if not position and not pending and not d_stop and i + 1 < len(bars):
            sig = 1 if buy_raw.iloc[i] else (-1 if sell_raw.iloc[i] else 0)
            if sig != 0:
                atr_v   = float(bar['atr'])
                pending = build_pending_order(bar, sig, x_actual, ktp, atr_v, ttl)
                pending['d'] = pending.pop('direction')

    # Force-close vị thế còn mở khi hết window để đồng bộ với `backtest_symbol()`.
    if position and len(bars):
        bar       = bars.iloc[-1]
        d         = position['d']
        exit_slip = _dynamic_slippage(bar)
        ep        = _adverse_exit_price(float(bar['close']), d, spread_pts, exit_slip)
        half_frac = (1 - partial_frac) if position['partial_tp_hit'] else 1.0
        r_h2      = (ep - position['entry']) * d / position['sl_dist']
        gross_h2  = half_frac * r_h2 * position['risk']
        comm_h2   = half_frac * position['comm']
        holding   = max((bar['BarTime'].date() - position['entry_date']).days, 0)
        swap_fee = swap_cost(holding, position['lot_size'], d, swap_long_day, swap_short_day)
        net_h2    = gross_h2 - comm_h2 - swap_fee
        combined  = position['half1_pnl'] + net_h2 if position['partial_tp_hit'] else net_h2
        equity   += net_h2
        peak_eq   = max(peak_eq, equity)
        running_maxdd = max(running_maxdd,
                            (peak_eq - equity) / peak_eq * 100 if peak_eq > 0 else 0.0)
        trades_pnl.append(combined)

    # Tính KPI rút gọn cho optimizer. Nếu không có trade nào, trả bộ điểm 0 để
    # optimizer tự loại bộ tham số này.
    empty_stats = calc_trade_level_return_stats(
        [],
        initial_equity=init_eq,
        window_start=start_ts,
        window_end=end_ts,
    )
    if not trades_pnl:
        return dict(
            trades=0, pf=0, ret=0, maxdd=0, score=0, sharpe=0,
            sharpe_method=empty_stats["sharpe_method"],
            sharpe_years_span=round(empty_stats["sharpe_years_span"], 4),
        )

    wins = [p for p in trades_pnl if p > 0]
    loss = [p for p in trades_pnl if p <= 0]
    gp   = sum(wins) if wins else 0
    gl   = sum(abs(p) for p in loss) if loss else 0
    pf   = gp / gl if gl > 0 else float('inf')
    ret  = (equity / init_eq - 1) * 100
    maxdd = running_maxdd
    score = pf * np.sqrt(max(ret, 0)) / max(maxdd, 1) if pf > 1 and ret > 0 else 0

    # Sharpe annualized theo số lệnh/năm. Ở fast mode, equity chỉ được dựng từ
    # chuỗi PnL của các trade, nên annualization dùng số trade trên số năm của
    # window test thay vì dùng số bar/năm.
    trade_stats = calc_trade_level_return_stats(
        trades_pnl,
        initial_equity=init_eq,
        window_start=start_ts,
        window_end=end_ts,
    )
    sharpe = trade_stats["sharpe"]

    return dict(
        trades=len(trades_pnl), pf=round(min(pf, 9.9), 2),
        ret=round(ret, 2), maxdd=round(maxdd, 2), score=round(score, 4),
        sharpe=round(sharpe, 4),
        sharpe_method=trade_stats["sharpe_method"],
        sharpe_years_span=round(trade_stats["sharpe_years_span"], 4),
    )
