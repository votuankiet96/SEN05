"""Portfolio helpers shared across strategies.

Architecture role
-----------------
- These helpers sit between many single-symbol backtests and one portfolio view.
- They intentionally know nothing about Combo-specific signal logic.

Upstream dependencies
---------------------
- symbol-level runners supply `trades_by_symbol` and `equity_by_symbol`.

Downstream dependencies
-----------------------
- `strategies.combo.portfolio.backtest`
- `strategies.combo.portfolio.walkforward`
- any future multi-strategy portfolio layer
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .metrics import calc_metrics


def equity_frame_from_dict(
    equity_by_symbol: dict[str, pd.Series],
    *,
    fill_value: float | dict[str, float] | None = None,
) -> pd.DataFrame:
    """Align equity curves from multiple symbols into one DataFrame.

    `fill_value` is important because symbols rarely open trades on the same bar;
    portfolio math needs seeded capital before the first trade appears.
    """
    if not equity_by_symbol:
        return pd.DataFrame()

    frame = pd.DataFrame(equity_by_symbol).sort_index()
    if fill_value is None:
        return frame.ffill()

    if isinstance(fill_value, dict):
        for symbol, seed in fill_value.items():
            if symbol in frame.columns:
                frame[symbol] = frame[symbol].ffill().fillna(seed)
        return frame

    return frame.ffill().fillna(fill_value)


def build_combined_equity(
    equity_by_symbol: dict[str, pd.Series],
    *,
    fill_value: float | dict[str, float] | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """Build a combined portfolio equity curve from symbol equity curves."""
    frame = equity_frame_from_dict(equity_by_symbol, fill_value=fill_value)
    if frame.empty:
        return frame, pd.Series(dtype=float)
    return frame, frame.sum(axis=1)


def combine_trade_logs(trades_by_symbol: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Flatten trade logs from many symbols into one time-sorted list."""
    combined: list[dict[str, Any]] = []
    for trades in trades_by_symbol.values():
        combined.extend(trades)

    if not combined:
        return combined

    return sorted(
        combined,
        key=lambda row: (
            pd.Timestamp(row.get("entry_time")) if row.get("entry_time") is not None else pd.Timestamp.min,
            str(row.get("symbol", "")),
        ),
    )


def calc_portfolio_metrics(
    trades_by_symbol: dict[str, list[dict[str, Any]]],
    equity_by_symbol: dict[str, pd.Series],
    *,
    tf_code: str = "H4",
    fill_value: float | dict[str, float] | None = None,
) -> dict[str, Any]:
    """Calculate portfolio-level metrics from symbol trades and equity curves.

    We reuse the same `calc_metrics()` engine used for a single symbol so the KPI
    vocabulary stays consistent across symbol and portfolio reports.
    """
    _, combined_equity = build_combined_equity(equity_by_symbol, fill_value=fill_value)
    if combined_equity.empty:
        return {}
    all_trades = combine_trade_logs(trades_by_symbol)
    return calc_metrics(all_trades, combined_equity, tf_code=tf_code)


def check_portfolio_ftmo(
    combined_equity: pd.Series,
    initial_balance: float,
    *,
    daily_loss_limit: float = 0.05,
    max_dd_limit: float = 0.10,
    max_dd_mode: str = "fixed_initial",
) -> dict[str, Any]:
    """Check FTMO rule compliance at the total portfolio (account) level.

    Mỗi sleeve bị giám sát độc lập bởi execution engine. Hàm này kiểm tra
    xem **tổng portfolio** có vi phạm giới hạn FTMO không — điều mà execution
    engine ở cấp sleeve không thể thấy.

    Quy tắc FTMO được áp dụng:
    - Daily Loss Limit: tổng lỗ trong một ngày ≤ daily_loss_limit × initial_balance
    - Max Drawdown   : peak-to-trough (trailing) ≤ max_dd_limit × peak equity

    Parameters
    ----------
    max_dd_mode       : "fixed_initial" for FTMO 2-Step, or "trailing_peak"
                         for rules that trail from account peak equity.
    combined_equity   : chuỗi equity tổng hợp toàn portfolio.
    initial_balance   : vốn ban đầu toàn portfolio (không chia sleeve).
    daily_loss_limit  : ngưỡng lỗ ngày (mặc định 5%).
    max_dd_limit      : ngưỡng drawdown tối đa (mặc định 10%).

    Returns
    -------
    dict gồm:
    - ftmo_pass            : True nếu không vi phạm bất kỳ quy tắc nào.
    - breach_reason        : 'daily_loss', 'max_dd', 'both', hoặc None.
    - breach_date          : ngày vi phạm đầu tiên (daily loss), hoặc None.
    - max_daily_loss_pct   : lỗ ngày lớn nhất (% initial_balance, dương = lỗ).
    - n_daily_breach_days  : số ngày vượt ngưỡng lỗ ngày.
    - max_dd_pct           : max drawdown trailing (%, dương = lỗ).
    - max_dd_usd           : max drawdown tuyệt đối ($).
    """
    if combined_equity.empty or initial_balance <= 0:
        return {
            "ftmo_pass": None,
            "breach_reason": "no_data",
            "breach_date": None,
            "max_daily_loss_pct": 0.0,
            "n_daily_breach_days": 0,
            "max_dd_pct": 0.0,
            "max_dd_usd": 0.0,
        }

    daily_limit_usd = initial_balance * daily_loss_limit

    # ── Daily loss check ────────────────────────────────────────────────────
    # Lấy equity cuối ngày (resample D, last giá trị có dữ liệu)
    daily_eq  = combined_equity.resample("D").last().dropna()
    daily_pnl = daily_eq.diff()
    # Ngày đầu tiên: PnL = equity - initial_balance
    if len(daily_eq):
        daily_pnl.iloc[0] = daily_eq.iloc[0] - initial_balance

    worst_day_pnl  = float(daily_pnl.min())  # most negative (hoặc 0 nếu không lỗ)
    max_daily_loss_pct = max(-worst_day_pnl / initial_balance, 0.0)

    daily_breached_mask = daily_pnl < -daily_limit_usd
    n_daily_breach      = int(daily_breached_mask.sum())
    daily_breach_date   = (
        str(daily_breached_mask[daily_breached_mask].index[0].date())
        if n_daily_breach > 0 else None
    )

    # ── Max drawdown check (trailing peak-to-trough) ───────────────────────
    eq_arr = combined_equity.values.astype(float)
    if max_dd_mode in {"trailing_peak", "peak"}:
        running_peak = np.maximum.accumulate(eq_arr)
    # Đảm bảo running_peak không nhỏ hơn initial_balance (khởi điểm)
        running_peak = np.maximum(running_peak, initial_balance)
        dd_usd_arr   = running_peak - eq_arr
        dd_pct_arr   = dd_usd_arr / running_peak

        max_dd_pct = float(dd_pct_arr.max())
        max_dd_usd = float(dd_usd_arr.max())
        dd_violated = max_dd_pct > max_dd_limit
    else:
        loss_usd_arr = np.maximum(initial_balance - eq_arr, 0.0)
        loss_pct_arr = loss_usd_arr / initial_balance
        max_dd_pct = float(loss_pct_arr.max())
        max_dd_usd = float(loss_usd_arr.max())
        dd_floor = initial_balance * (1 - max_dd_limit)
        dd_violated = bool((eq_arr <= dd_floor).any())

    # ── Breach assessment ───────────────────────────────────────────────────
    daily_violated = n_daily_breach > 0

    if daily_violated and dd_violated:
        breach_reason = "both"
    elif daily_violated:
        breach_reason = "daily_loss"
    elif dd_violated:
        breach_reason = "max_dd"
    else:
        breach_reason = None

    return {
        "ftmo_pass":           not (daily_violated or dd_violated),
        "breach_reason":       breach_reason,
        "breach_date":         daily_breach_date,
        "max_daily_loss_pct":  round(max_daily_loss_pct * 100, 2),   # percent
        "n_daily_breach_days": n_daily_breach,
        "max_dd_pct":          round(max_dd_pct * 100, 2),           # percent
        "max_dd_usd":          round(max_dd_usd, 2),
    }
