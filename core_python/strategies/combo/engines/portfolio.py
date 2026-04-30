"""Portfolio-level Combo pending-order backtest engine."""

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
    scheduled_realized: list[tuple[pd.Timestamp, str, float]] = []
    peak_concurrent_positions = 0
    peak_open_risk_usd = 0.0
    peak_open_risk_pct_of_equity = 0.0

    # ── Helper: lấy cost params của symbol ────────────────────────────────────
    def _sym_costs(sym: str) -> dict:
        base = symbol_configs.get(sym, {})
        over = (costs or {}).get(sym, {})
        return {**_DEFAULT_COSTS, **base, **over}

    def _update_open_risk_metrics() -> None:
        nonlocal peak_concurrent_positions
        nonlocal peak_open_risk_usd
        nonlocal peak_open_risk_pct_of_equity

        open_positions = [pos for pos in positions.values() if pos]
        open_count = len(open_positions)
        open_risk = 0.0
        for pos in open_positions:
            remaining_frac = (1.0 - partial_frac) if pos.get("partial_tp_hit") else 1.0
            open_risk += float(pos.get("risk_usd", 0.0)) * max(remaining_frac, 0.0)

        equity_base = max(float(account_equity), 1e-12)
        open_risk_pct = open_risk / equity_base * 100.0

        peak_concurrent_positions = max(peak_concurrent_positions, open_count)
        peak_open_risk_usd = max(peak_open_risk_usd, open_risk)
        peak_open_risk_pct_of_equity = max(peak_open_risk_pct_of_equity, open_risk_pct)

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

        if scheduled_realized:
            due = [item for item in scheduled_realized if item[0] <= ts]
            scheduled_realized = [item for item in scheduled_realized if item[0] > ts]
            for _, due_sym, due_pnl in due:
                account_equity += due_pnl
                account_peak_eq = max(account_peak_eq, account_equity)
                account_daily_pnl += due_pnl
                symbol_pnl[due_sym] += due_pnl
            _update_open_risk_metrics()

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
        sym_strategy = dict((s.get("_per_symbol") or {}).get(sym, {}))
        risk_pct_sym = float(sym_strategy.get("risk_per_trade", risk_pct))

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
                        risk_usd   = account_equity * alloc[sym] * risk_pct_sym
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
                            pendings[sym] = None
                        else:
                            positions[sym] = make_full_position(
                                direction=d,
                                entry=actual_entry,
                                sl=pendings[sym]["sl"],
                                tp=pendings[sym]["tp"],
                                atr=pendings[sym]["atr"],
                                risk_usd=risk_usd,
                                sl_dist=sl_dist,
                                entry_time=ts,
                                commission=commission,
                                lot_size=lot_size,
                            )
                            pendings[sym] = None
                            _update_open_risk_metrics()

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
            if   d ==  1 and float(bar["low"])  <= sl: exit_p, exit_r = _adverse_exit_price(sl, d, spread_pts, slippage), "SL"
            elif d == -1 and float(bar["high"]) >= sl: exit_p, exit_r = _adverse_exit_price(sl, d, spread_pts, slippage), "SL"

            # Phase 2 — Partial TP (chỉ lần đầu)
            elif not pos["partial_tp_hit"]:
                hit_tp = (d ==  1 and float(bar["high"]) >= tp) or \
                         (d == -1 and float(bar["low"])  <= tp)
                if hit_tp:
                    half_exit = _adverse_exit_price(tp, d, spread_pts, slippage)
                    r_h1     = (half_exit - entry) * d / sl_d
                    gross_h1 = partial_frac * r_h1 * risk
                    comm_h1  = partial_frac * comm
                    net_h1   = gross_h1 - comm_h1
                    pos.update(
                        partial_tp_hit=True,
                        half1_exit=half_exit, half1_exit_time=ts,
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
                    _update_open_risk_metrics()

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
                swap_fee = swap_cost(
                    holding_days,
                    pos.get("lot_size", 0.0),
                    d,
                    swap_long,
                    swap_short,
                )
                total_net   -= swap_fee
                close_net    = net_h2 - swap_fee

                account_equity    += close_net
                account_peak_eq    = max(account_peak_eq, account_equity)
                account_daily_pnl += close_net
                symbol_pnl[sym]   += close_net

                trades_by_symbol[sym].append(dict(
                    symbol=sym, direction="BUY" if d == 1 else "SELL",
                    lot_size=round(pos.get("lot_size", 0.0), 2),
                    entry_time=pos["entry_time"], exit_time=ts,
                    entry=round(entry, 4), exit=round(exit_p, 4),
                    sl_initial=round(pos["sl_at_entry"], 4), tp=tp_log,
                    r_multiple=round(r_mult, 3),
                    pnl_gross=round(total_gross, 2), commission=round(total_comm, 2),
                    swap_cost=round(swap_fee, 2), pnl_usd=round(total_net, 2),
                    equity=round(account_equity, 2), exit_reason=exit_r,
                    partial_tp_hit=pos["partial_tp_hit"],
                    half1_exit=pos["half1_exit"], half1_exit_time=pos["half1_exit_time"],
                ))
                positions[sym] = None
                _update_open_risk_metrics()
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
                ep_rev   = _adverse_exit_price(exit_px, d, spread_pts, exit_slip_rev)

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
                swap_fee = swap_cost(
                    holding_days,
                    pos.get("lot_size", 0.0),
                    d,
                    swap_long,
                    swap_short,
                )
                total_net   -= swap_fee
                close_net    = net_h2 - swap_fee

                # Log equity trước khi đóng (vì close thực sự tại bar kế tiếp)
                if exit_ts_rev != ts:
                    eq_pre_close = sym_eq_seed + symbol_pnl[sym]
                    scheduled_realized.append((exit_ts_rev, sym, close_net))
                else:
                    account_equity    += close_net
                    account_peak_eq    = max(account_peak_eq, account_equity)
                    account_daily_pnl += close_net
                    symbol_pnl[sym]   += close_net

                trades_by_symbol[sym].append(dict(
                    symbol=sym, direction="BUY" if d == 1 else "SELL",
                    lot_size=round(pos.get("lot_size", 0.0), 2),
                    entry_time=pos["entry_time"], exit_time=exit_ts_rev,
                    entry=round(pos["entry"], 4), exit=round(ep_rev, 4),
                    sl_initial=round(pos["sl_at_entry"], 4), tp=tp_log,
                    r_multiple=round(r_mult, 3),
                    pnl_gross=round(total_gross, 2), commission=round(total_comm, 2),
                    swap_cost=round(swap_fee, 2), pnl_usd=round(total_net, 2),
                    equity=round(account_equity, 2), exit_reason="REVERSED",
                    partial_tp_hit=pos["partial_tp_hit"],
                    half1_exit=pos["half1_exit"], half1_exit_time=pos["half1_exit_time"],
                ))
                positions[sym] = None
                _update_open_risk_metrics()
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
        half_frac    = (1 - partial_frac) if pos["partial_tp_hit"] else 1.0
        cfg          = _sym_costs(sym)
        spread_fc    = float(cfg.get("spread_pts", 0.0))
        slip_fc      = calc_dynamic_slippage(
            float(cfg.get("slippage_pts", _DEFAULT_COSTS["slippage_pts"])),
            float(last_bar.get("atr") or 0.0),
            float(last_bar.get("close") or 0.0),
            k=float(cfg.get("slippage_k", 50)),
        )
        ep           = _adverse_exit_price(float(last_bar["close"]), d, spread_fc, slip_fc)
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
        swap_fee = swap_cost(
            holding_days,
            pos.get("lot_size", 0.0),
            d,
            swap_long_fc,
            swap_short_fc,
        )
        total_net   -= swap_fee
        close_net    = net_h2 - swap_fee

        account_equity  += close_net
        account_peak_eq  = max(account_peak_eq, account_equity)
        symbol_pnl[sym] += close_net

        trades_by_symbol[sym].append(dict(
            symbol=sym, direction="BUY" if d == 1 else "SELL",
            lot_size=round(pos.get("lot_size", 0.0), 2),
            entry_time=pos["entry_time"], exit_time=exit_ts_fc,
            entry=round(pos["entry"], 4), exit=round(ep, 4),
            sl_initial=round(pos["sl_at_entry"], 4), tp=tp_log,
            r_multiple=round(r_mult, 3),
            pnl_gross=round(total_gross, 2), commission=round(total_comm, 2),
            swap_cost=round(swap_fee, 2), pnl_usd=round(total_net, 2),
            equity=round(account_equity, 2), exit_reason="FORCE_CLOSE",
            partial_tp_hit=pos["partial_tp_hit"],
            half1_exit=pos["half1_exit"], half1_exit_time=pos["half1_exit_time"],
        ))
        positions[sym] = None
        _update_open_risk_metrics()
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
        frame = pd.DataFrame(symbol_equity_series).sort_index()
        for sym in syms:
            if sym in frame.columns:
                frame[sym] = frame[sym].ffill().fillna(initial_balance * alloc[sym])
        symbol_equity_series = {sym: frame[sym].rename(sym) for sym in syms if sym in frame.columns}
        account_equity_series = frame.sum(axis=1)
        account_equity_series.name = "account"
    else:
        account_equity_series = pd.Series(dtype=float)
    account_equity_series.attrs.update(
        {
            "peak_concurrent_positions": int(peak_concurrent_positions),
            "peak_open_risk_usd": float(peak_open_risk_usd),
            "peak_open_risk_pct_of_equity": float(peak_open_risk_pct_of_equity),
        }
    )

    all_trades = sorted(
        [t for trades in trades_by_symbol.values() for t in trades],
        key=lambda t: (pd.Timestamp(t["entry_time"]), t["symbol"]),
    )

    return all_trades, account_equity_series, symbol_equity_series, trades_by_symbol


# ─────────────────────────────────────────────────────────────────────────────
# FAST BACKTEST (optimizer — metrics only, no trade log)
# ─────────────────────────────────────────────────────────────────────────────
