"""Portfolio-level backtest helpers for Combo.

=======================================================================
THAY ĐỔI SO VỚI PHIÊN BẢN CŨ
=======================================================================

Phiên bản cũ: chạy backtest độc lập cho từng symbol rồi cộng equity lại.
  → Daily loss / max DD tính theo từng slice nhỏ → dừng quá sớm
  → Position size không phản ánh vốn tổng hiện tại

Phiên bản mới: dùng backtest_portfolio() — một tài khoản, timeline chung.
  → Daily stop / max DD kiểm tra trên tổng account
  → Position size = account_equity × allocation × risk_pct
  → Cross-symbol equity feedback hoạt động đúng

backtest_symbol() và backtest_fast() KHÔNG bị thay đổi — vẫn dùng cho
tối ưu tham số từng symbol độc lập (mục đích khác, đúng với thiết kế).
"""

from __future__ import annotations

from typing import Any

from shared.contracts import PortfolioBacktestResult, SymbolBacktestResult
from shared.execution import backtest_portfolio
from shared.metrics import calc_metrics
from shared.portfolio import (
    build_combined_equity,
    calc_portfolio_metrics,
    check_portfolio_ftmo,
    combine_trade_logs,
)

from ..config import SYMBOLS, get_account_settings, get_cost_settings
from ..symbol.backtest import build_symbol_signal_frame, load_backtest_data


def run_portfolio_backtest(
    symbol_keys: list[str] | None = None,
    *,
    initial_balance: float = 100_000.0,
    account_mode: str = "standard",
    date_from: str | None = None,
    date_to: str | None = None,
    indicator_overrides: dict[str, Any] | None = None,
    symbol_overrides: dict[str, dict[str, Any]] | None = None,
    strategy_overrides: dict[str, Any] | None = None,
    costs: dict[str, Any] | None = None,
    broker_profile: str | None = None,
    max_bars: int = 60000,
    allocations: dict[str, float] | None = None,
) -> PortfolioBacktestResult:
    """Chạy portfolio backtest với engine một tài khoản chung.

    Luồng hoạt động:
      1. Load raw OHLCV và build signal frame cho từng symbol
      2. Gọi backtest_portfolio() — timeline chung, equity chung
      3. Đóng gói kết quả vào PortfolioBacktestResult

    Parameters
    ----------
    symbol_keys
        Danh sách symbol muốn backtest. None = dùng toàn bộ SYMBOLS.
        Đây là nhóm symbol đã được chọn sau bước tối ưu từng symbol.
    allocations
        Tỷ lệ phân bổ vốn. None = chia đều. Tổng tự động normalize về 1.0.
    """
    symbols = symbol_keys or list(SYMBOLS.keys())
    if not symbols:
        raise ValueError("run_portfolio_backtest() requires at least one symbol.")

    strategy_cfg = get_account_settings(account_mode, strategy_overrides)

    # ── Bước 1: Load data và build signal frames ───────────────────────────
    raw_frames:    dict[str, Any] = {}
    signal_frames: dict[str, Any] = {}
    symbol_configs: dict[str, dict] = {}

    for sym in symbols:
        sym_params = {
            **SYMBOLS[sym],
            **(symbol_overrides or {}).get(sym, {}),
        }
        df_raw = load_backtest_data(
            sym_params["symbol_id"],
            date_to=date_to,
            max_bars=max_bars,
        )
        df_sig, cfg, params = build_symbol_signal_frame(
            sym,
            df_raw,
            date_from=date_from,
            date_to=date_to,
            indicator_overrides=indicator_overrides,
            symbol_overrides=(symbol_overrides or {}).get(sym),
            broker_profile=broker_profile,
        )
        # Ghi trailing_activation và min_rr vào strategy_cfg theo từng symbol
        sym_strategy = {
            **strategy_cfg,
            "trailing_activation": float(cfg.get("trailing_activation",
                                                  strategy_cfg.get("trailing_activation", 1.0))),
            "min_rr": float(params.get("MIN_RR", strategy_cfg.get("min_rr", 1.25))),
        }
        raw_frames[sym]    = df_raw
        signal_frames[sym] = df_sig
        # Merge cost settings vào cfg để engine tự lấy được
        symbol_configs[sym] = {
            **cfg,
            **get_cost_settings(sym, costs, broker_profile=broker_profile),
        }

    # ── Bước 2: Chạy portfolio engine ──────────────────────────────────────
    all_trades, account_eq_series, sym_eq_series, trades_by_symbol = backtest_portfolio(
        signal_frames=signal_frames,
        symbol_configs=symbol_configs,
        initial_balance=initial_balance,
        allocations=allocations,
        strategy=sym_strategy,
        costs=None,  # costs đã được merge vào symbol_configs
    )

    # ── Bước 3: Build PortfolioBacktestResult ──────────────────────────────
    equity_frame, combined_equity = build_combined_equity(
        sym_eq_series,
        fill_value={sym: initial_balance / len(symbols) for sym in symbols},
    )

    # Build SymbolBacktestResult cho từng symbol (dùng để drill-down phân tích)
    symbol_results: dict[str, SymbolBacktestResult] = {}
    for sym in symbols:
        sym_trades = trades_by_symbol.get(sym, [])
        sym_equity = sym_eq_series.get(sym)
        sym_metrics = calc_metrics(sym_trades, sym_equity) if sym_equity is not None else {}
        symbol_results[sym] = SymbolBacktestResult(
            symbol=sym,
            raw_data=raw_frames[sym],
            signal_data=signal_frames[sym],
            trades=sym_trades,
            equity=sym_equity,
            metrics=sym_metrics,
            symbol_config=symbol_configs[sym],
            strategy_settings=sym_strategy,
            account_mode=account_mode,
        )

    metrics = calc_portfolio_metrics(
        trades_by_symbol,
        sym_eq_series,
        fill_value={sym: initial_balance / len(symbols) for sym in symbols},
    )

    # FTMO account-level check trên combined equity tổng
    if account_mode == "ftmo" and not combined_equity.empty:
        ftmo_cfg = get_account_settings("ftmo", strategy_overrides)
        metrics["ftmo_account_check"] = check_portfolio_ftmo(
            combined_equity,
            initial_balance,
            daily_loss_limit=float(ftmo_cfg.get("daily_loss_limit", 0.05)),
            max_dd_limit=float(ftmo_cfg.get("max_drawdown_limit", 0.10)),
            max_dd_mode=str(ftmo_cfg.get("max_drawdown_mode", "fixed_initial")),
        )

    return PortfolioBacktestResult(
        symbol_results=symbol_results,
        trades=all_trades,
        equity_frame=equity_frame,
        combined_equity=combined_equity,
        metrics=metrics,
        account_mode=account_mode,
        symbol_keys=symbols,
    )


def compare_account_modes(
    symbol_keys: list[str] | None = None,
    *,
    initial_balance: float = 100_000.0,
    date_from: str | None = None,
    date_to: str | None = None,
    indicator_overrides: dict[str, Any] | None = None,
    symbol_overrides: dict[str, dict[str, Any]] | None = None,
    strategy_overrides: dict[str, Any] | None = None,
    costs: dict[str, Any] | None = None,
    broker_profile: str | None = None,
    max_bars: int = 60000,
    allocations: dict[str, float] | None = None,
) -> dict[str, PortfolioBacktestResult]:
    """Chạy cùng portfolio dưới chế độ standard và FTMO để so sánh."""
    return {
        mode: run_portfolio_backtest(
            symbol_keys,
            initial_balance=initial_balance,
            account_mode=mode,
            date_from=date_from,
            date_to=date_to,
            indicator_overrides=indicator_overrides,
            symbol_overrides=symbol_overrides,
            strategy_overrides=strategy_overrides,
            costs=costs,
            broker_profile=broker_profile,
            max_bars=max_bars,
            allocations=allocations,
        )
        for mode in ("standard", "ftmo")
    }
