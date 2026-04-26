"""Portfolio-level walk-forward evaluation for Combo.

=======================================================================
MỤC ĐÍCH
=======================================================================

Kiểm tra xem bộ tham số đã tối ưu (từ single-symbol backtest) có thực
sự hoạt động tốt trong tương lai khi chạy cùng nhau ở cấp portfolio.

Mỗi "cửa sổ" gồm:
  IS (in-sample)  : giai đoạn dùng để tối ưu tham số (không re-optimize ở đây)
  OOS (out-of-sample): giai đoạn mới, chạy với params đã chọn → kết quả đáng tin

Engine dùng backtest_portfolio() — một tài khoản chung, daily stop và max DD
tính đúng ở cấp account, position size từ equity tổng hiện tại.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from shared.contracts import PortfolioWindowResult
from shared.execution import backtest_portfolio
from shared.portfolio import build_combined_equity, calc_portfolio_metrics

from ..config import SYMBOLS, get_account_settings, get_cost_settings, get_indicator_params
from ..symbol.backtest import build_symbol_signal_frame, load_backtest_full


def walk_forward_portfolio(
    symbol_params: dict[str, dict[str, Any]],
    *,
    account_mode: str = "standard",
    initial_balance: float = 100_000.0,
    is_bars: int = 5000,
    oos_bars: int = 1250,
    step_bars: int = 1250,
    indicator_overrides: dict[str, Any] | None = None,
    strategy_overrides: dict[str, Any] | None = None,
    costs: dict[str, Any] | None = None,
    broker_profile: str | None = None,
    tf: str = "H4",
    max_bars: int = 80000,
    allocations: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Chạy walk-forward OOS test ở cấp portfolio với engine tài khoản chung.

    Parameters
    ----------
    symbol_params
        Dict: symbol_key → dict tham số tối ưu của symbol đó.
        Là output của bước tối ưu từng symbol (single-symbol optimizer).
    allocations
        Tỷ lệ phân bổ vốn cho từng symbol. None = chia đều.
    is_bars / oos_bars / step_bars
        Độ dài cửa sổ IS, OOS và bước trượt (tính bằng số bar).

    Returns
    -------
    (df_windows, summary)
        df_windows : DataFrame mỗi hàng là một cửa sổ OOS — metrics chi tiết.
        summary    : dict tóm tắt toàn bộ walk-forward (avg sharpe, avg DD...).
    """
    if not symbol_params:
        return pd.DataFrame(), {}

    # ── Load full history cho tất cả symbol, lấy phần ngắn nhất chung ────────
    raw_cache: dict[str, pd.DataFrame] = {
        sym: load_backtest_full(SYMBOLS[sym]["symbol_id"], tf=tf, max_bars=max_bars)
        for sym in symbol_params
    }
    common_n = min(len(df) for df in raw_cache.values())
    if common_n < is_bars + oos_bars:
        return pd.DataFrame(), {}

    strategy_cfg = get_account_settings(account_mode, strategy_overrides)

    # Warmup đủ để indicator có state ổn định ngay đầu OOS
    _ind = get_indicator_params()
    _warmup = max(
        int(_ind.get("MACD_SLOW", 25)),
        int(_ind.get("MA_PERIOD", 20)),
        int(_ind.get("ATR_PERIOD", 5)),
    ) + 5

    rows: list[PortfolioWindowResult] = []
    step = 0

    while True:
        oos_start = is_bars + step * step_bars
        oos_end   = oos_start + oos_bars
        if oos_end > common_n:
            break

        # ── Build signal frames cho OOS window này ────────────────────────────
        signal_frames:  dict[str, pd.DataFrame] = {}
        symbol_configs: dict[str, dict]         = {}
        start_ts = end_ts = None

        for sym, params in symbol_params.items():
            # Lấy thêm warmup bars từ đuôi IS để indicator có đủ history
            warmup_from = max(0, oos_start - _warmup)
            aligned     = raw_cache[sym].tail(common_n)
            df_slice    = aligned.iloc[warmup_from:oos_end].copy()
            if df_slice.empty:
                continue

            oos_date_from = str(aligned.index[oos_start])
            oos_date_to   = str(aligned.index[oos_end - 1])

            df_sig, cfg, ind_params = build_symbol_signal_frame(
                sym,
                df_slice,
                date_from=oos_date_from,
                date_to=oos_date_to,
                indicator_overrides=indicator_overrides,
                symbol_overrides=params,
                broker_profile=broker_profile,
            )
            signal_frames[sym] = df_sig
            symbol_configs[sym] = {
                **cfg,
                **get_cost_settings(sym, costs, broker_profile=broker_profile),
            }
            # Ghi trailing_activation và min_rr vào strategy_cfg theo symbol
            strategy_cfg["trailing_activation"] = float(
                cfg.get("trailing_activation", strategy_cfg.get("trailing_activation", 1.0))
            )
            strategy_cfg["min_rr"] = float(
                ind_params.get("MIN_RR", strategy_cfg.get("min_rr", 1.25))
            )

            start_ts = oos_date_from if start_ts is None else min(start_ts, oos_date_from)
            end_ts   = oos_date_to   if end_ts   is None else max(end_ts,   oos_date_to)

        if not signal_frames:
            break

        # ── Chạy portfolio engine cho OOS window này ──────────────────────────
        _, _, sym_eq_series, trades_by_symbol = backtest_portfolio(
            signal_frames=signal_frames,
            symbol_configs=symbol_configs,
            initial_balance=initial_balance,
            allocations=allocations,
            strategy=strategy_cfg,
            costs=None,
        )

        _, combined_equity = build_combined_equity(
            sym_eq_series,
            fill_value={sym: initial_balance / len(symbol_params) for sym in signal_frames},
        )
        metrics = calc_portfolio_metrics(
            trades_by_symbol,
            sym_eq_series,
            fill_value={sym: initial_balance / len(symbol_params) for sym in signal_frames},
        )

        rows.append(PortfolioWindowResult(
            window=step,
            start=pd.Timestamp(start_ts),
            end=pd.Timestamp(end_ts),
            metrics=metrics,
            combined_equity=combined_equity,
        ))
        step += 1

    if not rows:
        return pd.DataFrame(), {}

    df = pd.DataFrame([
        {
            "window":    row.window,
            "oos_start": row.start,
            "oos_end":   row.end,
            **row.metrics,
        }
        for row in rows
    ])

    summary = {
        "n_windows":          len(rows),
        "profitable_windows": int((df.get("total_return", pd.Series(dtype=float)) > 0).sum()),
        "avg_total_return":   float(df.get("total_return",  pd.Series(dtype=float)).mean()),
        "avg_max_drawdown":   float(df.get("max_drawdown",  pd.Series(dtype=float)).mean()),
        "avg_sharpe":         float(df.get("sharpe",        pd.Series(dtype=float)).mean()),
    }
    return df, summary
