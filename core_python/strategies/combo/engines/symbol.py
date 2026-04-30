"""Single-symbol Combo pending-order backtest engine."""

from __future__ import annotations

import numpy as np
import pandas as pd

from core_python.shared.execution.primitives import calc_dynamic_slippage, swap_cost
from core_python.strategies.combo.engines.common import (
    _DEFAULT_COSTS,
    _DEFAULT_STRATEGY,
    _adverse_exit_price,
    _max_drawdown_breached,
    _resolve_strategy_limits,
    _risk_lot_size,
    build_pending_order,
    make_full_position,
)


def backtest_symbol(symbol: str, df: pd.DataFrame, cfg: dict,
                    init_eq: float, *,
                    strategy: dict | None = None,
                    costs: dict | None = None) -> tuple:
    """Backtest chi tiết cho một symbol và trả trade log đầy đủ.

    Làm gì
    ------
    Hàm này mô phỏng execution theo từng bar cho một symbol. Nó đọc cột `signal`
    trong `df`, tạo pending order, khớp lệnh nếu giá chạm entry, quản lý vị thế
    đang mở, tính PnL/chi phí và ghi lại equity theo thời gian.

    Cơ chế được mô phỏng:
    - Pending breakout order có thời hạn `pending_ttl_bars`.
    - Position sizing theo `risk_per_trade`.
    - Lot size đi qua shared sizing primitive để tôn trọng risk và broker lot bounds.
    - Spread, slippage động, commission và swap.
    - Partial TP tại TP đầu tiên.
    - Breakeven sau partial TP bằng cách dời SL về entry.
    - Trailing stop theo `ma`.
    - Reversal: nếu đang giữ lệnh mà có tín hiệu ngược, đóng lệnh cũ và có thể
      tạo pending order mới.
    - Daily loss stop và max drawdown stop.

    Nhận input gì
    -------------
    `symbol`
        Tên/key symbol, ví dụ `"US30"` hoặc `"EURUSD"`. Dùng để ghi vào trade
        log, không dùng để tự tra config.

    `df`
        `DataFrame` đã có dữ liệu giá, indicator và tín hiệu. Các cột quan trọng
        gồm `BarTime`, `open`, `high`, `low`, `close`, `ma`, `atr`, `signal`.

    `cfg`
        Cấu hình symbol/broker. Các key thường dùng gồm `x`, `contract_value`,
        `spread_pts`, `point_size`, `swap_long_per_lot_per_day`,
        `swap_short_per_lot_per_day`, `ktp`, `min_lot_size`, `max_lot_size`,
        `lot_step`.

    `init_eq`
        Vốn ban đầu cho symbol này.

    `strategy`
        Dict override tạm cho strategy, ví dụ `risk_per_trade`,
        `daily_loss_limit`, `trailing_activation`, `partial_tp_fraction`.

    `costs`
        Dict override tạm cho chi phí, ví dụ `slippage_pts`,
        `commission_per_lot`, `slippage_k`.

    Trả output gì
    -------------
    Tuple `(trades, eq_ts)`:

    - `trades`: list dict, mỗi dict là một lệnh đã đóng với entry/exit, PnL,
      commission, swap, lý do thoát lệnh và thông tin partial TP.
    - `eq_ts`: `pd.Series` equity curve theo thời gian.

    Tại sao cần nó
    --------------
    Đây là bản mô phỏng đầy đủ để debug, phân tích chất lượng chiến lược, vẽ
    equity curve và xuất báo cáo trade-by-trade.
    """
    # Merge default với override. Dict phía sau ghi đè dict phía trước, nên caller
    # có thể thay từng tham số mà không cần truyền đủ toàn bộ config.
    s = {**_DEFAULT_STRATEGY, **(strategy or {})}
    c = {**_DEFAULT_COSTS, **(costs or {})}

    # Đọc cấu hình symbol/broker. Dùng `.get()` với default để engine vẫn chạy
    # được trong môi trường research, nhưng kết quả sẽ thực tế hơn khi cfg đủ spec.
    x              = cfg['x']
    contract_val   = cfg.get('contract_value', 0)
    spread_pts     = cfg.get('spread_pts', 0)
    point_size     = cfg.get('point_size', 1.0)
    swap_long_day  = cfg.get('swap_long_per_lot_per_day', 0.0)
    swap_short_day = cfg.get('swap_short_per_lot_per_day', 0.0)
    sym_ktp        = cfg.get('ktp', s.get('ktp', 2.3))
    min_lot        = cfg.get('min_lot_size', 0.01)
    max_lot        = cfg.get('max_lot_size', 100.0)
    lot_step       = cfg.get('lot_step', min_lot)

    risk_pct     = s['risk_per_trade']
    daily_limit, max_dd = _resolve_strategy_limits(s)
    max_dd_mode  = s.get('max_drawdown_mode', _DEFAULT_STRATEGY["max_drawdown_mode"])
    trail_act    = s['trailing_activation']
    ttl          = s['pending_ttl_bars']
    partial_frac = s.get('partial_tp_fraction', 0.5)
    comm_per_lot = c['commission_per_lot']
    base_slip    = cfg.get('slippage_pts', c['slippage_pts'])

    def _dynamic_slippage(bar: pd.Series) -> float:
        """Wrapper nội bộ để áp dụng `base_slip` và `slippage_k` hiện tại."""
        return calc_dynamic_slippage(
            base_slippage=base_slip,
            atr=bar['atr'],
            close=bar['close'],
            k=c.get('slippage_k', 50),
        )

    # Các biến trạng thái chính của mô phỏng:
    # - `position`: vị thế đang mở, hoặc None.
    # - `pending`: lệnh chờ chưa khớp, hoặc None.
    # - `daily_pnl`: PnL trong ngày hiện tại để áp daily loss limit.
    # - `peak_eq`: đỉnh equity để tính trailing drawdown nếu cần.
    trades, eq_log = [], []
    equity         = init_eq
    peak_eq        = init_eq
    position       = None
    pending        = None
    daily_pnl      = 0.0
    current_day    = None
    daily_stop     = False
    bars           = df.reset_index()

    for i, bar in bars.iterrows():
        # Mỗi vòng lặp là một cây nến. Engine chỉ dùng thông tin OHLC của bar,
        # không biết thứ tự tick thật bên trong bar.
        bar_dt   = bar['BarTime']
        bar_date = bar_dt.date()
        slippage = _dynamic_slippage(bar)
        eq_value_for_log = None
        if bar_date != current_day:
            # Sang ngày mới thì reset daily PnL và cho phép giao dịch lại.
            current_day = bar_date
            daily_pnl   = 0.0
            daily_stop  = False
        if _max_drawdown_breached(equity, init_eq, peak_eq, max_dd, max_dd_mode):
            # Vi phạm max drawdown thì ghi equity hiện tại và dừng backtest symbol.
            eq_log.append((bar_dt, equity))
            break

        # Khớp pending order nếu có. Pending chỉ khớp khi chưa có position.
        if pending and not position:
            pending['ttl'] -= 1
            if pending['ttl'] < 0:
                pending = None
            else:
                d, entry = pending['direction'], pending['entry']
                filled = (d == 1 and bar['high'] >= entry) or \
                         (d == -1 and bar['low'] <= entry)
                if filled and not daily_stop:
                    # Giá khớp thực tế bị đẩy xấu đi bởi slippage + spread.
                    cost         = slippage + spread_pts
                    actual_entry = entry + d * cost
                    sl_dist      = abs(actual_entry - pending['sl'])
                    if sl_dist > 0:
                        risk_usd   = equity * risk_pct
                        commission = 0.0
                        lot_size   = 0.0
                        lot_size = _risk_lot_size(
                            risk_usd,
                            sl_dist,
                            point_size=point_size,
                            contract_value=contract_val,
                            commission_per_lot=comm_per_lot,
                            min_lot=min_lot,
                            max_lot=max_lot,
                            lot_step=lot_step,
                        )
                        commission = 2 * lot_size * comm_per_lot
                        if lot_size <= 0:
                            pending = None
                        else:
                            position = make_full_position(
                                direction=d,
                                entry=actual_entry,
                                sl=pending['sl'],
                                tp=pending['tp'],
                                atr=pending['atr'],
                                risk_usd=risk_usd,
                                sl_dist=sl_dist,
                                entry_time=bar_dt,
                                commission=commission,
                                lot_size=lot_size,
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
            if   d == 1  and bar['low']  <= sl: exit_p, exit_r = _adverse_exit_price(sl, d, spread_pts, slippage), 'SL'
            elif d == -1 and bar['high'] >= sl: exit_p, exit_r = _adverse_exit_price(sl, d, spread_pts, slippage), 'SL'

            # Phase 2 — Partial TP (chỉ chạy 1 lần đầu tiên khi chạm TP).
            elif not position['partial_tp_hit']:
                hit_tp = (d == 1 and bar['high'] >= tp) or \
                         (d == -1 and bar['low']  <= tp)
                if hit_tp:
                    # Chốt một phần vị thế tại TP đầu tiên, cộng PnL phần này
                    # ngay vào equity, rồi dời SL phần còn lại về entry.
                    half_exit   = _adverse_exit_price(tp, d, spread_pts, slippage)
                    r_h1        = (half_exit - entry) * d / sl_d
                    gross_h1    = partial_frac * r_h1 * risk
                    comm_h1     = partial_frac * comm
                    net_h1      = gross_h1 - comm_h1
                    position.update(
                        partial_tp_hit=True,
                        half1_exit=half_exit, half1_exit_time=bar_dt,
                        half1_pnl_gross=gross_h1, half1_commission=comm_h1,
                        sl=entry,
                        tp=float('inf') if d == 1 else float('-inf'),
                        trail_active=True,
                    )
                    equity    += net_h1
                    peak_eq    = max(peak_eq, equity)
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
                    # BUY chỉ nâng SL lên, SELL chỉ hạ SL xuống. Không bao giờ
                    # nới stop theo hướng bất lợi.
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
                # Swap tính theo số ngày giữ lệnh và lot size. Long/short dùng
                # rate khác nhau vì broker thường tính swap hai chiều khác nhau.
                swap_fee = swap_cost(
                    holding_days,
                    position.get('lot_size', 0.0),
                    d,
                    swap_long_day,
                    swap_short_day,
                )

                total_net  -= swap_fee
                close_net   = net_h2 - swap_fee
                equity     += close_net
                peak_eq     = max(peak_eq, equity)
                daily_pnl  += close_net

                trades.append(dict(
                    # Trade log cố tình lưu nhiều field để dashboard/notebook có
                    # thể phân tích sâu mà không cần chạy lại backtest.
                    symbol=symbol,
                    direction='BUY' if d == 1 else 'SELL',
                    lot_size=round(position.get('lot_size', 0.0), 2),
                    entry_time=position['entry_time'], exit_time=bar_dt,
                    entry=round(entry, 4), exit=round(exit_p, 4),
                    sl_initial=round(position['sl_at_entry'], 4), tp=tp_log,
                    r_multiple=round(r_mult, 3),
                    pnl_gross=round(total_gross, 2),
                    commission=round(total_comm, 2),
                    swap_cost=round(swap_fee, 2),
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
                # Reversal giả định đóng lệnh ở open của bar kế tiếp nếu còn dữ
                # liệu; nếu đang ở bar cuối thì đóng tại close hiện tại.
                d         = position['direction']
                half_frac = (1 - partial_frac) if position['partial_tp_hit'] else 1.0
                exit_bar  = bars.iloc[i + 1] if i + 1 < len(bars) else bar
                exit_time = exit_bar['BarTime']
                exit_slip = _dynamic_slippage(exit_bar) if i + 1 < len(bars) else slippage
                exit_px   = exit_bar['open'] if i + 1 < len(bars) else bar['close']
                ep_rev    = _adverse_exit_price(exit_px, d, spread_pts, exit_slip)
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
                holding_days = max((exit_time.date() - position['entry_time'].date()).days, 0)
                swap_fee = swap_cost(
                    holding_days,
                    position.get('lot_size', 0.0),
                    d,
                    swap_long_day,
                    swap_short_day,
                )

                total_net  -= swap_fee
                close_net   = net_h2 - swap_fee
                if exit_time != bar_dt:
                    eq_value_for_log = equity
                equity     += close_net
                peak_eq     = max(peak_eq, equity)
                daily_pnl  += close_net

                trades.append(dict(
                    symbol=symbol,
                    direction='BUY' if d == 1 else 'SELL',
                    lot_size=round(position.get('lot_size', 0.0), 2),
                    entry_time=position['entry_time'], exit_time=exit_time,
                    entry=round(position['entry'], 4), exit=round(ep_rev, 4),
                    sl_initial=round(position['sl_at_entry'], 4), tp=tp_log,
                    r_multiple=round(r_mult, 3),
                    pnl_gross=round(total_gross, 2),
                    commission=round(total_comm, 2),
                    swap_cost=round(swap_fee, 2),
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
                    # Sau khi đóng vì reversal, tạo pending mới theo hướng tín
                    # hiệu ngược. Pending vẫn cần bar sau để khớp như bình thường.
                    atr_v   = float(bar['atr'])
                    pending = build_pending_order(bar, sig, x, sym_ktp, atr_v, ttl)

        # ── Không có vị thế: nếu có signal thì tạo pending order mới ─────────
        if not position and not pending and not daily_stop:
            sig = int(bar['signal'])
            if sig != 0 and i + 1 < len(bars):
                atr_v   = float(bar['atr'])
                pending = build_pending_order(bar, sig, x, sym_ktp, atr_v, ttl)

        eq_log.append((bar_dt, equity if eq_value_for_log is None else eq_value_for_log))

    # ── Hết dữ liệu nhưng vẫn còn vị thế: force-close tại bar cuối ───────────
    if position and len(bars):
        # Backtest không được để position treo sau khi hết dữ liệu. Force-close
        # giúp equity cuối cùng phản ánh PnL chưa hiện thực hóa.
        bar       = bars.iloc[-1]
        d         = position['direction']
        exit_slip = _dynamic_slippage(bar)
        ep        = _adverse_exit_price(bar['close'], d, spread_pts, exit_slip)
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
        swap_fee = swap_cost(
            holding_days,
            position.get('lot_size', 0.0),
            d,
            swap_long_day,
            swap_short_day,
        )

        total_net -= swap_fee
        close_net  = net_h2 - swap_fee
        equity    += close_net
        peak_eq    = max(peak_eq, equity)

        trades.append(dict(
            symbol=symbol,
            direction='BUY' if d == 1 else 'SELL',
            entry_time=position['entry_time'], exit_time=bar['BarTime'],
            entry=round(position['entry'], 4), exit=round(ep, 4),
            sl_initial=round(position['sl_at_entry'], 4), tp=tp_log,
            r_multiple=round(r_mult, 3),
            pnl_gross=round(total_gross, 2),
            commission=round(total_comm, 2),
            swap_cost=round(swap_fee, 2),
            pnl_usd=round(total_net, 2), equity=round(equity, 2),
            exit_reason='FORCE_CLOSE',
            partial_tp_hit=position['partial_tp_hit'],
            half1_exit=position['half1_exit'],
            half1_exit_time=position['half1_exit_time'],
        ))
        if eq_log:
            eq_log[-1] = (eq_log[-1][0], equity)

    eq_ts = pd.Series(
        # Chuyển equity log thành Series thời gian để downstream có thể tính
        # drawdown, plot chart hoặc aggregate portfolio.
        [v for _, v in eq_log],
        index=pd.DatetimeIndex([t for t, _ in eq_log]),
    )
    return trades, eq_ts


# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO BACKTEST ENGINE — true single-account multi-symbol simulation
# ─────────────────────────────────────────────────────────────────────────────
