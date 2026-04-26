# =============================================================================
# strategies/shared/execution_engine.py  -  Generic bar-by-bar execution engine
# =============================================================================
"""Engine mô phỏng khớp lệnh bar-by-bar dùng chung cho mọi strategy.

1. Mục đích tổng quan
---------------------
File này là phần "execution engine" của hệ thống backtest. Nó không tạo tín hiệu
giao dịch; nó nhận dữ liệu đã có `signal` và mô phỏng quá trình đặt lệnh, khớp
lệnh, quản lý vị thế, tính PnL, commission, swap, drawdown và equity curve.

Bài toán file này giải quyết:
- Biến tín hiệu BUY/SELL từ strategy thành giao dịch mô phỏng cụ thể.
- Áp dụng cơ chế pending order, SL, TP, partial TP, breakeven, trailing stop và
  reversal theo từng cây nến.
- Tính kích thước lot theo risk, rồi làm tròn về lot hợp lệ của broker.
- Tính chi phí giao dịch gồm spread, slippage động, commission và swap.
- Dừng giao dịch khi chạm daily loss limit hoặc max drawdown limit.

2. Diễn giải từng nhóm chức năng
--------------------------------
- `_DEFAULT_STRATEGY`, `_DEFAULT_COSTS`: bộ tham số mặc định nếu caller không
  truyền override.
- `_resolve_strategy_limits()`: đọc giới hạn daily loss/max drawdown, đồng thời
  hỗ trợ tên key cũ để không phá code cũ.
- `_max_drawdown_breached()`: kiểm tra tài khoản có vi phạm max drawdown chưa.
- `build_pending_order()`: tạo lệnh chờ từ bar hiện tại và hướng tín hiệu.
- `calc_dynamic_slippage()`: tăng slippage khi thị trường biến động mạnh.
- `backtest_symbol()`: backtest đầy đủ, trả trade log và equity curve.
- `backtest_fast()`: backtest rút gọn cho optimizer/grid search, chỉ trả KPI.

3. Luồng hoạt động tổng quát
----------------------------
1. Caller chuẩn bị `df` đã có indicator và `signal`.
2. Caller truyền `cfg` symbol, vốn ban đầu `init_eq`, optional `strategy` và
   `costs`.
3. Engine đi từng bar theo thứ tự thời gian.
4. Nếu có pending order, engine kiểm tra bar hiện tại có khớp lệnh không.
5. Nếu có vị thế, engine kiểm tra SL, partial TP, trailing stop và reversal.
6. Khi vị thế đóng, engine tính PnL, commission, swap, cập nhật equity.
7. Nếu chạm giới hạn rủi ro, engine dừng hoặc không mở thêm lệnh mới.
8. Cuối dữ liệu, nếu vẫn còn vị thế, engine force-close tại bar cuối.

4. Điểm kỹ thuật đáng chú ý
---------------------------
Module này cố tình không import config riêng của strategy. Mọi thứ đi qua `cfg`,
`strategy` và `costs`, giúp cùng một engine dùng được cho nhiều strategy/broker.

Engine mô phỏng theo bar nên không biết thứ tự tick thật bên trong mỗi cây nến.
Vì vậy code chọn một số quy tắc bảo thủ, ví dụ SL được ưu tiên trước partial TP
khi cùng một bar có thể chạm nhiều mức giá.

`backtest_symbol()` giữ đầy đủ trade log để debug và phân tích. `backtest_fast()`
lược bỏ trade log để chạy tối ưu tham số nhanh hơn.
"""
import numpy as np
import pandas as pd

from .broker import round_lot_size
from .metrics import _bars_per_year


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL DEFAULTS
# Strategy-specific configs sẽ override các giá trị này qua `strategy` param.
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_STRATEGY = {
    # Mức rủi ro cho mỗi trade, tính theo equity hiện tại. 0.005 = 0.5%.
    "risk_per_trade":       0.005,
    # Nếu PnL trong ngày giảm quá ngưỡng này so với init_eq, engine không mở thêm
    # lệnh mới trong ngày đó.
    "daily_loss_limit":     0.05,
    # Giới hạn sụt giảm tài khoản tổng thể. Cách tính phụ thuộc
    # `max_drawdown_mode`.
    "max_drawdown_limit":   0.10,
    "max_drawdown_mode":    "fixed_initial",
    # Backward-compatible aliases still accepted by _resolve_strategy_limits().
    "ftmo_daily_limit":     0.05,
    "ftmo_max_dd":          0.10,
    # Khi lệnh lời ít nhất `trailing_activation * atr`, trailing stop bắt đầu
    # hoạt động.
    "trailing_activation":  1.0,
    # Pending order hết hạn sau số bar này nếu chưa khớp.
    "pending_ttl_bars":     3,
    # Tỷ lệ đóng ở partial TP. 0.5 nghĩa là đóng nửa vị thế ở TP đầu tiên.
    "partial_tp_fraction":  0.5,
}

_DEFAULT_COSTS = {
    # Slippage nền tính bằng point. Có thể bị tăng bởi calc_dynamic_slippage().
    "slippage_pts":       2,
    # Commission một chiều cho mỗi lot. Engine nhân 2 để tính vào/ra lệnh.
    "commission_per_lot": 3.5,
}


def _resolve_strategy_limits(strategy_cfg: dict) -> tuple[float, float]:
    """Lấy daily loss limit và max drawdown limit từ `strategy_cfg`.

    Làm gì
    ------
    Hàm này đọc 2 giới hạn rủi ro quan trọng:

    - `daily_loss_limit`: mức lỗ tối đa trong ngày.
    - `max_drawdown_limit`: mức sụt giảm tối đa của tài khoản.

    Nó cũng hỗ trợ tên key cũ `ftmo_daily_limit` và `ftmo_max_dd` để các config
    cũ vẫn chạy được.

    Nhận input gì
    -------------
    `strategy_cfg`: dict cấu hình strategy sau khi đã merge default và override.

    Trả output gì
    -------------
    Tuple `(daily_limit, max_dd)`, cả hai đều là `float`.

    Tại sao cần nó
    --------------
    Logic backtest phía dưới chỉ muốn dùng một tên chuẩn. Hàm này gom việc tương
    thích key cũ/key mới vào một nơi.
    """
    daily_limit = strategy_cfg.get(
        "daily_loss_limit",
        strategy_cfg.get("ftmo_daily_limit", _DEFAULT_STRATEGY["daily_loss_limit"]),
    )
    max_dd = strategy_cfg.get(
        "max_drawdown_limit",
        strategy_cfg.get("ftmo_max_dd", _DEFAULT_STRATEGY["max_drawdown_limit"]),
    )
    return float(daily_limit), float(max_dd)


def _max_drawdown_breached(
    equity: float,
    init_eq: float,
    peak_eq: float,
    max_dd: float,
    mode: str = "fixed_initial",
) -> bool:
    """Kiểm tra equity có vi phạm luật max drawdown hay chưa.

    Làm gì
    ------
    Hàm này trả `True` nếu equity hiện tại đã thấp hơn ngưỡng drawdown cho phép.

    Nhận input gì
    -------------
    `equity`
        Equity hiện tại.

    `init_eq`
        Vốn ban đầu của backtest.

    `peak_eq`
        Equity cao nhất từng đạt được trong quá trình chạy.

    `max_dd`
        Mức drawdown tối đa, ví dụ `0.10` nghĩa là 10%.

    `mode`
        `"fixed_initial"` so với vốn ban đầu, giống luật maximum loss cố định.
        `"trailing_peak"` hoặc `"peak"` so với đỉnh equity đã đạt được.

    Trả output gì
    -------------
    `bool`: `True` nếu đã vi phạm giới hạn, ngược lại `False`.

    Tại sao cần nó
    --------------
    Nhiều account mode có luật drawdown khác nhau. Tách thành helper giúp cả
    `backtest_symbol()` và `backtest_fast()` dùng chung một logic.
    """
    if max_dd >= 1:
        return False
    if mode in {"trailing_peak", "peak"}:
        return equity <= peak_eq * (1 - max_dd)
    return equity <= init_eq * (1 - max_dd)


# ─────────────────────────────────────────────────────────────────────────────
# ORDER UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def build_pending_order(bar, direction: int, x: float, ktp: float,
                        atr: float, ttl: int) -> dict:
    """Tạo pending order từ dữ liệu của một bar.

    Làm gì
    ------
    Hàm này biến một tín hiệu BUY/SELL thành lệnh chờ breakout:

    - BUY: entry nằm trên `high` của bar hiện tại thêm buffer `x`.
    - SELL: entry nằm dưới `low` của bar hiện tại trừ buffer `x`.
    - SL đặt ở phía đối diện của bar, cũng cộng/trừ buffer `x`.
    - TP tính theo `entry + direction * ktp * atr`.

    Nhận input gì
    -------------
    `bar`
        Một dòng dữ liệu nến, cần có ít nhất `high` và `low`.

    `direction`
        `1` là BUY, `-1` là SELL.

    `x`
        Breakout buffer, dùng để tránh vào lệnh ngay sát đỉnh/đáy bar.

    `ktp`
        Hệ số take-profit theo ATR.

    `atr`
        ATR hiện tại, dùng để tính khoảng TP.

    `ttl`
        Số bar lệnh chờ còn hiệu lực.

    Trả output gì
    -------------
    Dict gồm `direction`, `entry`, `sl`, `tp`, `atr`, `ttl`.

    Tại sao cần nó
    --------------
    Cả full backtest và fast backtest đều cần tạo pending order cùng một kiểu.
    Helper này tránh lặp công thức entry/SL/TP ở nhiều nơi.
    """
    entry = float(bar['high']) + x if direction == 1 else float(bar['low']) - x
    sl    = float(bar['low'])  - x if direction == 1 else float(bar['high']) + x
    tp    = entry + direction * ktp * atr
    return dict(direction=direction, entry=entry, sl=sl, tp=tp,
                atr=atr, ttl=ttl)


def calc_dynamic_slippage(base_slippage: float, atr: float, close: float,
                          k: float = 50) -> float:
    """Tính slippage động theo mức biến động tương đối của thị trường.

    Làm gì
    ------
    Hàm này tăng slippage khi ATR lớn so với giá đóng cửa. Công thức dùng tỷ lệ
    `atr / close`, sau đó clip vào khoảng `[0.0005, 0.01]` để slippage không quá
    thấp hoặc quá cao bất thường.

    Nhận input gì
    -------------
    `base_slippage`
        Slippage nền, tính bằng point.

    `atr`
        Average True Range tại bar hiện tại.

    `close`
        Giá đóng cửa hiện tại, dùng làm mẫu số để chuẩn hóa ATR.

    `k`
        Hệ số khuếch đại ảnh hưởng của biến động. Mặc định `50`.

    Trả output gì
    -------------
    Slippage đã điều chỉnh:
    `base_slippage * (1 + k * clipped_atr_ratio)`.

    Tại sao cần nó
    --------------
    Trong thực tế, thị trường biến động mạnh thường khớp lệnh xấu hơn. Slippage
    động giúp backtest bớt lạc quan so với dùng một con số cố định.
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
    """Backtest chi tiết cho một symbol và trả trade log đầy đủ.

    Làm gì
    ------
    Hàm này mô phỏng execution theo từng bar cho một symbol. Nó đọc cột `signal`
    trong `df`, tạo pending order, khớp lệnh nếu giá chạm entry, quản lý vị thế
    đang mở, tính PnL/chi phí và ghi lại equity theo thời gian.

    Cơ chế được mô phỏng:
    - Pending breakout order có thời hạn `pending_ttl_bars`.
    - Position sizing theo `risk_per_trade`.
    - Lot size được làm tròn bằng `round_lot_size()`.
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
                        if contract_val > 0:
                            # Position sizing: số lot sao cho nếu hit SL thì lỗ
                            # xấp xỉ `equity * risk_pct`, sau đó làm tròn theo
                            # rule của broker.
                            lot_size   = risk_usd / ((sl_dist / point_size) * contract_val)
                            lot_size   = round_lot_size(
                                lot_size,
                                min_lot=min_lot,
                                max_lot=max_lot,
                                lot_step=lot_step,
                            )
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
                    # Chốt một phần vị thế tại TP đầu tiên, cộng PnL phần này
                    # ngay vào equity, rồi dời SL phần còn lại về entry.
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
                swap_cost = (holding_days * swap_long_day * position.get('lot_size', 0.0)
                             if d == 1
                             else holding_days * swap_short_day * position.get('lot_size', 0.0))

                total_net  -= swap_cost
                close_net   = net_h2 - swap_cost
                equity     += close_net
                peak_eq     = max(peak_eq, equity)
                daily_pnl  += close_net

                trades.append(dict(
                    # Trade log cố tình lưu nhiều field để dashboard/notebook có
                    # thể phân tích sâu mà không cần chạy lại backtest.
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
                # Reversal giả định đóng lệnh ở open của bar kế tiếp nếu còn dữ
                # liệu; nếu đang ở bar cuối thì đóng tại close hiện tại.
                d         = position['direction']
                half_frac = (1 - partial_frac) if position['partial_tp_hit'] else 1.0
                exit_bar  = bars.iloc[i + 1] if i + 1 < len(bars) else bar
                exit_time = exit_bar['BarTime']
                exit_slip = _dynamic_slippage(exit_bar) if i + 1 < len(bars) else slippage
                exit_px   = exit_bar['open'] if i + 1 < len(bars) else bar['close']
                ep_rev    = exit_px - d * exit_slip
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
                swap_cost = (holding_days * swap_long_day * position.get('lot_size', 0.0)
                             if d == 1
                             else holding_days * swap_short_day * position.get('lot_size', 0.0))

                total_net  -= swap_cost
                close_net   = net_h2 - swap_cost
                if exit_time != bar_dt:
                    eq_value_for_log = equity
                equity     += close_net
                peak_eq     = max(peak_eq, equity)
                daily_pnl  += close_net

                trades.append(dict(
                    symbol=symbol,
                    direction='BUY' if d == 1 else 'SELL',
                    entry_time=position['entry_time'], exit_time=exit_time,
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
        # Chuyển equity log thành Series thời gian để downstream có thể tính
        # drawdown, plot chart hoặc aggregate portfolio.
        [v for _, v in eq_log],
        index=pd.DatetimeIndex([t for t, _ in eq_log]),
    )
    return trades, eq_ts


# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO BACKTEST ENGINE — true single-account multi-symbol simulation
# ─────────────────────────────────────────────────────────────────────────────

def backtest_portfolio(
    symbol_frames: dict[str, pd.DataFrame],
    symbol_configs: dict[str, dict],
    initial_balance: float = 100_000.0,
    *,
    allocations: dict[str, float] | None = None,
    strategy: dict | None = None,
    costs: dict[str, dict] | None = None,
) -> tuple[list[dict], pd.Series, dict[str, pd.Series], dict[str, list]]:
    """Mô phỏng portfolio thực tế: một tài khoản, nhiều symbol, chia sẻ vốn chung.

    =======================================================================
    TẠI SAO CẦN ENGINE NÀY (thay vì chạy từng symbol rồi cộng lại)?
    =======================================================================

    Trong backtest_symbol(), mỗi symbol chạy độc lập với init_eq riêng.
    Kết quả đúng cho việc tối ưu tham số từng symbol, nhưng SAI khi mô
    phỏng portfolio thực tế vì:

    1. Daily loss limit tính sai: mỗi symbol dừng khi tự nó lỗ 5% slice,
       trong khi FTMO quan tâm tổng account lỗ 5%.
    2. Max drawdown tính sai: tương tự — dừng quá sớm theo slice.
    3. Position size không phản ánh vốn tổng hiện tại: nếu account tổng
       đang lời, position lẽ ra lớn hơn (và ngược lại khi lỗ).
    4. Không có cross-symbol equity feedback: symbol A lời không ảnh hưởng
       size của symbol B.

    Engine này xử lý tất cả bằng cách:
    - Duyệt TẤT CẢ bars của mọi symbol theo thứ tự thời gian chung
    - Một biến account_equity duy nhất — mọi lệnh đều cập nhật biến này
    - Daily stop và max DD kiểm tra trên account tổng → dừng tất cả symbol
    - Position size = account_equity × allocation[sym] × risk_pct

    =======================================================================
    PARAMETERS
    =======================================================================

    symbol_frames
        Dict signal frames đã có indicator + cột signal. Key = symbol key.
        Là output của build_symbol_signal_frame() cho từng symbol.

    symbol_configs
        Dict cấu hình broker/cost cho từng symbol. Key = symbol key.

    initial_balance
        Vốn ban đầu của cả tài khoản. Mặc định $100,000.

    allocations
        Tỷ lệ phân bổ vốn cho từng symbol. None = chia đều.
        Ví dụ: {"US30": 0.3, "EURUSD": 0.2, ...}
        Tổng sẽ được normalize về 1.0 tự động.

    strategy
        Override strategy params (risk_per_trade, daily_loss_limit, ...).

    costs
        Dict of dict: per-symbol cost overrides.
        Ví dụ: {"US30": {"commission_per_lot": 5.0}, ...}

    =======================================================================
    RETURNS
    =======================================================================

    Tuple (all_trades, account_equity_series, symbol_equity_series, trades_by_symbol)

    all_trades
        Tất cả lệnh đã đóng, sắp xếp theo entry_time.

    account_equity_series
        Đường vốn tổng tài khoản theo thời gian (pd.Series).

    symbol_equity_series
        Dict: symbol → pd.Series equity của riêng symbol đó.
        Tổng của tất cả = account_equity_series.

    trades_by_symbol
        Dict: symbol → list lệnh của symbol đó.
    """
    if not symbol_frames:
        return [], pd.Series(dtype=float), {}, {}

    syms = list(symbol_frames.keys())
    n    = len(syms)

    # ── Resolve allocations, normalize về 1.0 ─────────────────────────────────
    if allocations is None:
        alloc = {sym: 1.0 / n for sym in syms}
    else:
        raw_total = sum(allocations.get(sym, 0.0) for sym in syms)
        if raw_total <= 0:
            alloc = {sym: 1.0 / n for sym in syms}
        else:
            alloc = {sym: allocations.get(sym, 0.0) / raw_total for sym in syms}

    # ── Resolve strategy params ────────────────────────────────────────────────
    s            = {**_DEFAULT_STRATEGY, **(strategy or {})}
    daily_limit, max_dd = _resolve_strategy_limits(s)
    max_dd_mode  = str(s.get("max_drawdown_mode", _DEFAULT_STRATEGY["max_drawdown_mode"]))
    risk_pct     = float(s["risk_per_trade"])
    trail_act    = float(s["trailing_activation"])
    ttl          = int(s["pending_ttl_bars"])
    partial_frac = float(s.get("partial_tp_fraction", 0.5))

    # ── Build per-symbol bar lists để có thể look-ahead khi reversal ──────────
    # Mỗi symbol cần biết bar kế tiếp (T+1) để tính giá thoát lệnh reversal.
    symbol_bars: dict[str, list[dict]] = {}
    for sym, df in symbol_frames.items():
        bars_df = df.reset_index() if df.index.name == "BarTime" else df.copy()
        if "BarTime" not in bars_df.columns:
            bars_df = bars_df.reset_index()
        symbol_bars[sym] = bars_df.to_dict("records")

    # ── Build unified timeline: (timestamp, symbol, bar_index) ────────────────
    # Tất cả bars của mọi symbol gom vào một list, sort theo thời gian.
    # Cùng timestamp → sort theo tên symbol để kết quả tái lập được.
    events: list[tuple] = []
    for sym, bars in symbol_bars.items():
        for idx, bar in enumerate(bars):
            ts = pd.Timestamp(bar["BarTime"])
            events.append((ts, sym, idx))
    events.sort(key=lambda e: (e[0], e[1]))

    # ── Shared account state (1 biến duy nhất cho cả portfolio) ───────────────
    account_equity     = float(initial_balance)
    account_peak_eq    = float(initial_balance)
    account_daily_pnl  = 0.0
    account_daily_stop = False   # True khi daily loss limit bị chạm
    account_stopped    = False   # True khi max DD bị chạm — dừng vĩnh viễn
    current_day        = None

    # ── Per-symbol state ───────────────────────────────────────────────────────
    positions:        dict[str, dict | None]   = {sym: None for sym in syms}
    pendings:         dict[str, dict | None]   = {sym: None for sym in syms}
    symbol_pnl:       dict[str, float]         = {sym: 0.0  for sym in syms}
    symbol_eq_logs:   dict[str, list[tuple]]   = {sym: []   for sym in syms}
    trades_by_symbol: dict[str, list[dict]]    = {sym: []   for sym in syms}

    # ── Helper: lấy cost params của symbol ────────────────────────────────────
    def _sym_costs(sym: str) -> dict:
        base = symbol_configs.get(sym, {})
        over = (costs or {}).get(sym, {})
        return {**_DEFAULT_COSTS, **base, **over}

    # ── Main event loop ────────────────────────────────────────────────────────
    for ts, sym, idx in events:
        sym_eq_seed = initial_balance * alloc[sym]

        # Khi account đã dừng vĩnh viễn (max DD), vẫn log equity để Series
        # không bị lỗ hổng — equity không thay đổi nữa.
        if account_stopped:
            symbol_eq_logs[sym].append((ts, sym_eq_seed + symbol_pnl[sym]))
            continue

        bar          = symbol_bars[sym][idx]
        bar_count    = len(symbol_bars[sym])
        bar_date     = ts.date()
        eq_pre_close = None   # dùng cho reversal: log equity trước khi đóng

        # ── Reset daily state khi sang ngày mới ─────────────────────────────
        if bar_date != current_day:
            current_day        = bar_date
            account_daily_pnl  = 0.0
            account_daily_stop = False

        # ── Kiểm tra max DD ở cấp account (dừng tất cả symbol) ──────────────
        if _max_drawdown_breached(account_equity, initial_balance,
                                   account_peak_eq, max_dd, max_dd_mode):
            account_stopped = True
            symbol_eq_logs[sym].append((ts, sym_eq_seed + symbol_pnl[sym]))
            continue

        # ── Resolve cost params cho symbol này ──────────────────────────────
        cfg          = _sym_costs(sym)
        spread_pts   = float(cfg.get("spread_pts",            0.0))
        comm_per_lot = float(cfg.get("commission_per_lot",    _DEFAULT_COSTS["commission_per_lot"]))
        contract_val = float(cfg.get("contract_value",        1.0))
        point_size   = float(cfg.get("point_size",            1.0))
        min_lot      = float(cfg.get("min_lot_size",          0.01))
        max_lot      = float(cfg.get("max_lot_size",          100.0))
        lot_step     = float(cfg.get("lot_step",              0.01))
        swap_long    = float(cfg.get("swap_long_per_lot_per_day",  0.0))
        swap_short   = float(cfg.get("swap_short_per_lot_per_day", 0.0))
        sym_ktp      = float(cfg.get("ktp",                   s.get("ktp", 2.3)))
        x            = float(cfg.get("x",                     0.0))
        base_slip    = float(cfg.get("slippage_pts",           _DEFAULT_COSTS["slippage_pts"]))
        slip_k       = float(cfg.get("slippage_k",            50))

        atr_val   = float(bar.get("atr")   or 1.0)
        close_val = float(bar.get("close") or 1.0)
        slippage  = calc_dynamic_slippage(base_slip, atr_val, close_val, k=slip_k)

        # ── Fill pending order ────────────────────────────────────────────────
        if pendings[sym] and not positions[sym]:
            pendings[sym]["ttl"] -= 1
            if pendings[sym]["ttl"] < 0:
                pendings[sym] = None
            else:
                d        = pendings[sym]["direction"]
                entry_px = pendings[sym]["entry"]
                filled   = (d ==  1 and float(bar["high"]) >= entry_px) or \
                           (d == -1 and float(bar["low"])  <= entry_px)
                if filled and not account_daily_stop:
                    cost         = slippage + spread_pts
                    actual_entry = entry_px + d * cost
                    sl_dist      = abs(actual_entry - pendings[sym]["sl"])
                    if sl_dist > 0:
                        # Position size từ ACCOUNT equity hiện tại × allocation × risk
                        risk_usd   = account_equity * alloc[sym] * risk_pct
                        lot_size   = 0.0
                        commission = 0.0
                        if contract_val > 0:
                            lot_size   = risk_usd / ((sl_dist / point_size) * contract_val)
                            lot_size   = round_lot_size(lot_size, min_lot=min_lot,
                                                        max_lot=max_lot, lot_step=lot_step)
                            commission = 2 * lot_size * comm_per_lot
                        positions[sym] = dict(
                            direction=d, entry=actual_entry,
                            sl=pendings[sym]["sl"], sl_at_entry=pendings[sym]["sl"],
                            tp=pendings[sym]["tp"], tp_at_entry=pendings[sym]["tp"],
                            atr=pendings[sym]["atr"], risk_usd=risk_usd,
                            sl_dist=sl_dist, entry_time=ts,
                            trail_active=False, commission=commission,
                            lot_size=lot_size, partial_tp_hit=False,
                            half1_exit=None, half1_exit_time=None,
                            half1_pnl_gross=0.0, half1_commission=0.0,
                        )
                        pendings[sym] = None

        # ── Manage open position ──────────────────────────────────────────────
        if positions[sym]:
            pos    = positions[sym]
            d      = pos["direction"]
            entry  = pos["entry"]
            sl     = pos["sl"]
            tp     = pos["tp"]
            risk   = pos["risk_usd"]
            sl_d   = pos["sl_dist"]
            comm   = pos["commission"]
            exit_p = exit_r = None

            # Phase 1 — SL ưu tiên
            if   d ==  1 and float(bar["low"])  <= sl: exit_p, exit_r = sl - slippage, "SL"
            elif d == -1 and float(bar["high"]) >= sl: exit_p, exit_r = sl + slippage, "SL"

            # Phase 2 — Partial TP (chỉ lần đầu)
            elif not pos["partial_tp_hit"]:
                hit_tp = (d ==  1 and float(bar["high"]) >= tp) or \
                         (d == -1 and float(bar["low"])  <= tp)
                if hit_tp:
                    r_h1     = (tp - entry) * d / sl_d
                    gross_h1 = partial_frac * r_h1 * risk
                    comm_h1  = partial_frac * comm
                    net_h1   = gross_h1 - comm_h1
                    pos.update(
                        partial_tp_hit=True,
                        half1_exit=tp, half1_exit_time=ts,
                        half1_pnl_gross=gross_h1, half1_commission=comm_h1,
                        sl=entry,
                        tp=float("inf") if d == 1 else float("-inf"),
                        trail_active=True,
                    )
                    account_equity    += net_h1
                    account_peak_eq    = max(account_peak_eq, account_equity)
                    account_daily_pnl += net_h1
                    symbol_pnl[sym]   += net_h1
                    if account_daily_pnl <= -(initial_balance * daily_limit):
                        account_daily_stop = True

            # Phase 3 — Trailing stop theo MA
            if exit_p is None:
                if not pos["trail_active"]:
                    if (float(bar["close"]) - entry) * d >= pos["atr"] * trail_act:
                        pos["trail_active"] = True
                ma_val = bar.get("ma")
                if pos["trail_active"] and ma_val is not None and not np.isnan(float(ma_val)):
                    new_sl = float(ma_val)
                    if   d ==  1 and new_sl > pos["sl"]: pos["sl"] = new_sl
                    elif d == -1 and new_sl < pos["sl"]: pos["sl"] = new_sl

            # Phase 4 — Đóng lệnh và ghi trade log
            if exit_p is not None:
                half_frac    = (1 - partial_frac) if pos["partial_tp_hit"] else 1.0
                r_h2         = (exit_p - entry) * d / sl_d
                gross_h2     = half_frac * r_h2 * risk
                comm_h2      = half_frac * comm
                net_h2       = gross_h2 - comm_h2
                if pos["partial_tp_hit"]:
                    total_gross = pos["half1_pnl_gross"] + gross_h2
                    total_comm  = pos["half1_commission"] + comm_h2
                    r_h1        = (pos["half1_exit"] - entry) * d / sl_d
                    r_mult      = partial_frac * r_h1 + (1 - partial_frac) * r_h2
                    tp_log      = round(pos["half1_exit"], 4)
                else:
                    total_gross = gross_h2
                    total_comm  = comm_h2
                    r_mult      = r_h2
                    tp_log      = round(pos["tp_at_entry"], 4)
                total_net    = total_gross - total_comm
                holding_days = max((ts.date() - pos["entry_time"].date()).days, 0)
                swap_cost    = holding_days * (swap_long if d == 1 else swap_short) * pos.get("lot_size", 0.0)
                total_net   -= swap_cost
                close_net    = net_h2 - swap_cost

                account_equity    += close_net
                account_peak_eq    = max(account_peak_eq, account_equity)
                account_daily_pnl += close_net
                symbol_pnl[sym]   += close_net

                trades_by_symbol[sym].append(dict(
                    symbol=sym, direction="BUY" if d == 1 else "SELL",
                    entry_time=pos["entry_time"], exit_time=ts,
                    entry=round(entry, 4), exit=round(exit_p, 4),
                    sl_initial=round(pos["sl_at_entry"], 4), tp=tp_log,
                    r_multiple=round(r_mult, 3),
                    pnl_gross=round(total_gross, 2), commission=round(total_comm, 2),
                    swap_cost=round(swap_cost, 2), pnl_usd=round(total_net, 2),
                    equity=round(account_equity, 2), exit_reason=exit_r,
                    partial_tp_hit=pos["partial_tp_hit"],
                    half1_exit=pos["half1_exit"], half1_exit_time=pos["half1_exit_time"],
                ))
                positions[sym] = None
                if account_daily_pnl <= -(initial_balance * daily_limit):
                    account_daily_stop = True

        # ── Reversal: tín hiệu ngược chiều khi đang giữ lệnh ─────────────────
        if positions[sym] and not account_daily_stop:
            sig = int(bar.get("signal", 0))
            pos = positions[sym]
            if sig != 0 and sig != pos["direction"]:
                d         = pos["direction"]
                half_frac = (1 - partial_frac) if pos["partial_tp_hit"] else 1.0

                # Look-ahead trong bar list của chính symbol này (không phải timeline chung)
                next_bar      = symbol_bars[sym][idx + 1] if idx + 1 < bar_count else None
                exit_bar_rev  = next_bar if next_bar else bar
                exit_ts_rev   = pd.Timestamp(exit_bar_rev["BarTime"])
                exit_slip_rev = calc_dynamic_slippage(
                    base_slip,
                    float(exit_bar_rev.get("atr") or atr_val),
                    float(exit_bar_rev.get("close") or close_val),
                    k=slip_k,
                ) if next_bar else slippage
                exit_px  = float(exit_bar_rev["open"]) if next_bar else float(bar["close"])
                ep_rev   = exit_px - d * exit_slip_rev

                r_h2     = (ep_rev - pos["entry"]) * d / pos["sl_dist"]
                gross_h2 = half_frac * r_h2 * pos["risk_usd"]
                comm_h2  = half_frac * pos["commission"]
                net_h2   = gross_h2 - comm_h2
                if pos["partial_tp_hit"]:
                    total_gross = pos["half1_pnl_gross"] + gross_h2
                    total_comm  = pos["half1_commission"] + comm_h2
                    r_h1        = (pos["half1_exit"] - pos["entry"]) * d / pos["sl_dist"]
                    r_mult      = partial_frac * r_h1 + (1 - partial_frac) * r_h2
                    tp_log      = round(pos["half1_exit"], 4)
                else:
                    total_gross = gross_h2
                    total_comm  = comm_h2
                    r_mult      = r_h2
                    tp_log      = round(pos["tp_at_entry"], 4)
                total_net    = total_gross - total_comm
                holding_days = max((exit_ts_rev.date() - pos["entry_time"].date()).days, 0)
                swap_cost    = holding_days * (swap_long if d == 1 else swap_short) * pos.get("lot_size", 0.0)
                total_net   -= swap_cost
                close_net    = net_h2 - swap_cost

                # Log equity trước khi đóng (vì close thực sự tại bar kế tiếp)
                if exit_ts_rev != ts:
                    eq_pre_close = sym_eq_seed + symbol_pnl[sym]
                account_equity    += close_net
                account_peak_eq    = max(account_peak_eq, account_equity)
                account_daily_pnl += close_net
                symbol_pnl[sym]   += close_net

                trades_by_symbol[sym].append(dict(
                    symbol=sym, direction="BUY" if d == 1 else "SELL",
                    entry_time=pos["entry_time"], exit_time=exit_ts_rev,
                    entry=round(pos["entry"], 4), exit=round(ep_rev, 4),
                    sl_initial=round(pos["sl_at_entry"], 4), tp=tp_log,
                    r_multiple=round(r_mult, 3),
                    pnl_gross=round(total_gross, 2), commission=round(total_comm, 2),
                    swap_cost=round(swap_cost, 2), pnl_usd=round(total_net, 2),
                    equity=round(account_equity, 2), exit_reason="REVERSED",
                    partial_tp_hit=pos["partial_tp_hit"],
                    half1_exit=pos["half1_exit"], half1_exit_time=pos["half1_exit_time"],
                ))
                positions[sym] = None
                if account_daily_pnl <= -(initial_balance * daily_limit):
                    account_daily_stop = True
                # Tạo pending mới theo hướng ngược
                if not account_daily_stop and idx + 1 < bar_count:
                    pendings[sym] = build_pending_order(bar, sig, x, sym_ktp, atr_val, ttl)

        # ── Tín hiệu mới → tạo pending order ─────────────────────────────────
        if not positions[sym] and not pendings[sym] and not account_daily_stop:
            sig = int(bar.get("signal", 0))
            if sig != 0 and idx + 1 < bar_count:
                pendings[sym] = build_pending_order(bar, sig, x, sym_ktp, atr_val, ttl)

        # ── Ghi equity log cho symbol này tại bar này ─────────────────────────
        # eq_pre_close: dùng khi reversal — log equity TRƯỚC khi đóng lệnh,
        # vì thực sự lệnh đóng ở open của bar kế tiếp, không phải bar này.
        logged_eq = eq_pre_close if eq_pre_close is not None else (sym_eq_seed + symbol_pnl[sym])
        symbol_eq_logs[sym].append((ts, logged_eq))

    # ── Force-close các vị thế còn mở sau khi hết dữ liệu ────────────────────
    for sym in syms:
        if not positions[sym] or not symbol_bars[sym]:
            continue
        pos          = positions[sym]
        last_bar     = symbol_bars[sym][-1]
        d            = pos["direction"]
        ep           = float(last_bar["close"])
        half_frac    = (1 - partial_frac) if pos["partial_tp_hit"] else 1.0
        cfg          = _sym_costs(sym)
        swap_long_fc = float(cfg.get("swap_long_per_lot_per_day",  0.0))
        swap_short_fc= float(cfg.get("swap_short_per_lot_per_day", 0.0))

        r_h2     = (ep - pos["entry"]) * d / pos["sl_dist"]
        gross_h2 = half_frac * r_h2 * pos["risk_usd"]
        comm_h2  = half_frac * pos["commission"]
        net_h2   = gross_h2 - comm_h2
        if pos["partial_tp_hit"]:
            total_gross = pos["half1_pnl_gross"] + gross_h2
            total_comm  = pos["half1_commission"] + comm_h2
            r_h1        = (pos["half1_exit"] - pos["entry"]) * d / pos["sl_dist"]
            r_mult      = partial_frac * r_h1 + (1 - partial_frac) * r_h2
            tp_log      = round(pos["half1_exit"], 4)
        else:
            total_gross = gross_h2
            total_comm  = comm_h2
            r_mult      = r_h2
            tp_log      = round(pos["tp_at_entry"], 4)
        total_net    = total_gross - total_comm
        exit_ts_fc   = pd.Timestamp(last_bar["BarTime"])
        holding_days = max((exit_ts_fc.date() - pos["entry_time"].date()).days, 0)
        swap_cost    = holding_days * (swap_long_fc if d == 1 else swap_short_fc) * pos.get("lot_size", 0.0)
        total_net   -= swap_cost
        close_net    = net_h2 - swap_cost

        account_equity  += close_net
        account_peak_eq  = max(account_peak_eq, account_equity)
        symbol_pnl[sym] += close_net

        trades_by_symbol[sym].append(dict(
            symbol=sym, direction="BUY" if d == 1 else "SELL",
            entry_time=pos["entry_time"], exit_time=exit_ts_fc,
            entry=round(pos["entry"], 4), exit=round(ep, 4),
            sl_initial=round(pos["sl_at_entry"], 4), tp=tp_log,
            r_multiple=round(r_mult, 3),
            pnl_gross=round(total_gross, 2), commission=round(total_comm, 2),
            swap_cost=round(swap_cost, 2), pnl_usd=round(total_net, 2),
            equity=round(account_equity, 2), exit_reason="FORCE_CLOSE",
            partial_tp_hit=pos["partial_tp_hit"],
            half1_exit=pos["half1_exit"], half1_exit_time=pos["half1_exit_time"],
        ))
        positions[sym] = None
        # Cập nhật điểm cuối của equity log để phản ánh PnL force-close
        if symbol_eq_logs[sym]:
            symbol_eq_logs[sym][-1] = (
                symbol_eq_logs[sym][-1][0],
                initial_balance * alloc[sym] + symbol_pnl[sym],
            )

    # ── Build output Series ───────────────────────────────────────────────────
    symbol_equity_series: dict[str, pd.Series] = {}
    for sym in syms:
        logs = symbol_eq_logs[sym]
        if logs:
            symbol_equity_series[sym] = pd.Series(
                [v for _, v in logs],
                index=pd.DatetimeIndex([t for t, _ in logs]),
                name=sym,
            )
        else:
            symbol_equity_series[sym] = pd.Series(
                [initial_balance * alloc[sym]],
                index=pd.DatetimeIndex([pd.Timestamp("1970-01-01")]),
                name=sym,
            )

    # Account equity = sum của tất cả symbol equity (đã chia sẻ đúng)
    if symbol_equity_series:
        frame = pd.DataFrame(symbol_equity_series).sort_index().ffill()
        account_equity_series = frame.sum(axis=1)
        account_equity_series.name = "account"
    else:
        account_equity_series = pd.Series(dtype=float)

    all_trades = sorted(
        [t for trades in trades_by_symbol.values() for t in trades],
        key=lambda t: (pd.Timestamp(t["entry_time"]), t["symbol"]),
    )

    return all_trades, account_equity_series, symbol_equity_series, trades_by_symbol


# ─────────────────────────────────────────────────────────────────────────────
# FAST BACKTEST (optimizer — metrics only, no trade log)
# ─────────────────────────────────────────────────────────────────────────────

def backtest_fast(symbol: str, df_ind: pd.DataFrame, cfg: dict,
                  ktp: float, x_actual: float, trailing_act: float,
                  date_from: str, date_to: str, init_eq: float, *,
                  strategy: dict | None = None,
                  costs: dict | None = None,
                  tf: str = 'H4') -> dict:
    """Backtest rút gọn để optimizer/grid search chạy nhanh.

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
    min_rr       = s.get('min_rr', 1.25)
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
    start_ts = pd.Timestamp(date_from) if date_from is not None else df_ind.index.min()
    end_ts = pd.Timestamp(date_to) if date_to is not None else df_ind.index.max()
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
    rr_ok       = rr_all >= min_rr

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
                        comm = 0.0
                        lots = 0.0
                        if contract_val > 0:
                            lots = risk_usd / ((sl_dist / point_size) * contract_val)
                            lots = round_lot_size(
                                lots,
                                min_lot=min_lot,
                                max_lot=max_lot,
                                lot_step=lot_step,
                            )
                            comm = 2 * lots * comm_per_lot
                        position = dict(
                            d=d, entry=act_e,
                            sl=pending['sl'], tp=pending['tp'],
                            atr=pending['atr'], risk=risk_usd,
                            sl_dist=sl_dist, trail=False, comm=comm,
                            partial_tp_hit=False, half1_pnl=0.0,
                            lot_size=lots, entry_date=bdate,
                        )
                        pending = None

        # Quản lý vị thế đang mở: SL ưu tiên, rồi partial TP, rồi trailing stop.
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
                    # Partial TP trong fast mode chỉ lưu net PnL phần 1, không lưu
                    # chi tiết exit_time/exit_price như full mode.
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
                # Khi vị thế đóng, chỉ append tổng PnL của trade vào `trades_pnl`.
                half_frac  = (1 - partial_frac) if position['partial_tp_hit'] else 1.0
                r_h2       = (ep - position['entry']) * d / position['sl_dist']
                gross_h2   = half_frac * r_h2 * position['risk']
                comm_h2    = half_frac * position['comm']
                holding    = max((bdate - position['entry_date']).days, 0)
                swap_cost  = holding * (swap_long_day if d == 1 else swap_short_day) * position['lot_size']
                net_h2     = gross_h2 - comm_h2 - swap_cost
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
                ep_rev     = exit_px - d_cur * exit_slip
                r_h2       = (ep_rev - position['entry']) * d_cur / position['sl_dist']
                gross_h2   = half_frac * r_h2 * position['risk']
                comm_h2    = half_frac * position['comm']
                holding    = max((exit_bar['BarTime'].date() - position['entry_date']).days, 0)
                swap_cost  = holding * (swap_long_day if d_cur == 1 else swap_short_day) * position['lot_size']
                net_h2     = gross_h2 - comm_h2 - swap_cost
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
        ep        = float(bar['close'])
        half_frac = (1 - partial_frac) if position['partial_tp_hit'] else 1.0
        r_h2      = (ep - position['entry']) * d / position['sl_dist']
        gross_h2  = half_frac * r_h2 * position['risk']
        comm_h2   = half_frac * position['comm']
        holding   = max((bar['BarTime'].date() - position['entry_date']).days, 0)
        swap_cost = holding * (swap_long_day if d == 1 else swap_short_day) * position['lot_size']
        net_h2    = gross_h2 - comm_h2 - swap_cost
        combined  = position['half1_pnl'] + net_h2 if position['partial_tp_hit'] else net_h2
        equity   += net_h2
        peak_eq   = max(peak_eq, equity)
        running_maxdd = max(running_maxdd,
                            (peak_eq - equity) / peak_eq * 100 if peak_eq > 0 else 0.0)
        trades_pnl.append(combined)

    # Tính KPI rút gọn cho optimizer. Nếu không có trade nào, trả bộ điểm 0 để
    # optimizer tự loại bộ tham số này.
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

    # Sharpe annualized theo số lệnh/năm. Ở fast mode, equity chỉ được dựng từ
    # chuỗi PnL của các trade, nên annualization dùng số trade trên số năm của
    # window test thay vì dùng số bar/năm.
    eq_fast   = pd.Series([init_eq] + list(init_eq + np.cumsum(trades_pnl)))
    rets_fast = eq_fast.pct_change().dropna()
    if len(rets_fast) > 1 and len(trades_pnl) >= 10 and rets_fast.std() > 0:
        # Dùng đúng date_from/date_to của window test, không phải toàn bộ df_ind cache
        years_span = max((end_ts - start_ts).days / 365.25, 0.1)
        n_per_year = len(trades_pnl) / years_span
        sharpe = float(rets_fast.mean() / rets_fast.std() * np.sqrt(max(n_per_year, 1)))
    else:
        sharpe = 0.0

    return dict(
        trades=len(trades_pnl), pf=round(min(pf, 9.9), 2),
        ret=round(ret, 2), maxdd=round(maxdd, 2), score=round(score, 4),
        sharpe=round(sharpe, 4),
    )
