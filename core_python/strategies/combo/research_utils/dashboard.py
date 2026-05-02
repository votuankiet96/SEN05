"""Dashboard, optimizer, portfolio, and scanner displays for Combo research notebooks."""

from __future__ import annotations

from ._shared import *  # noqa: F401,F403

def configure_notebook(*, dark: bool = True, max_columns: int = 200) -> None:
    """Thiết lập style chung cho notebook.

    Làm gì
    ------
    Hàm này cấu hình warning, pandas display và matplotlib style. Mục tiêu là
    các notebook có giao diện nhất quán, bảng không bị cắt cột và chart dễ đọc.

    Nhận input gì
    -------------
    `dark`
        Nếu `True`, dùng matplotlib dark background.

    `max_columns`
        Số cột tối đa pandas hiển thị.

    Trả output gì
    -------------
    Không trả về gì. Hàm chỉ thay đổi setting hiển thị của session notebook.
    """
    import warnings

    import matplotlib.pyplot as plt

    warnings.filterwarnings("ignore")
    pd.set_option("display.max_columns", max_columns)
    pd.set_option("display.width", 220)
    pd.set_option("display.float_format", lambda v: f"{v:,.4f}")
    if dark:
        plt.style.use("dark_background")


def show_note(title: str, body: str) -> None:
    """Hiển thị một ghi chú Markdown ngắn trong notebook.

    Hàm này giúp notebook có phần bình giải rõ ràng ngay trước hoặc sau các bảng
    quan trọng. Nếu không chạy trong IPython, nó fallback sang `print`.
    """
    if HTML is not None:
        _display_html(f"""
        <div style="
            border: 1px solid #30363D;
            border-left: 4px solid #00D4FF;
            border-radius: 6px;
            padding: 10px 14px;
            margin: 10px 0 12px 0;
            background: #F8FAFC;
            color: #111827;
        ">
          <div style="font-size: 17px; font-weight: 700; margin-bottom: 4px;">
            {_escape(title)}
          </div>
          <div style="font-size: 13px; line-height: 1.45;">
            {_escape(body)}
          </div>
        </div>
        """)
    elif Markdown is not None:
        _display_obj(Markdown(f"### {title}\n\n{body}"))
    else:
        print(f"{title}\n{body}")


def show_run_config(title: str, config: Mapping[str, Any]) -> pd.DataFrame:
    """Display the effective run configuration as a compact two-column table."""
    rows = [{"Parameter": k, "Value": _short_repr(v)} for k, v in config.items()]
    frame = pd.DataFrame(rows)
    show_note(title, "Effective configuration for this run.")
    _display_obj(
        frame.style
        .hide(axis="index")
        .set_properties(**{"text-align": "left", "white-space": "pre-wrap"})
        .set_table_styles([
            {"selector": "th", "props": [("text-align", "left")]},
            {"selector": "td:nth-child(2)", "props": [("font-family", "Consolas, monospace")]},
        ])
    )
    return frame


def metrics_to_frame(metrics: Mapping[str, Any] | None) -> pd.DataFrame:
    """Chuyển dict metrics thành DataFrame dễ hiển thị.

    Hàm bỏ qua các object lớn hoặc nested như `monthly_pnl_table` và
    `ftmo_account_check`; các phần đó có helper riêng để hiển thị rõ hơn.
    """
    if not metrics:
        return pd.DataFrame(columns=["metric", "value"])

    rows: list[dict[str, Any]] = []
    used = set()
    for key in KPI_ORDER:
        if key in metrics and _is_scalar(metrics[key]):
            rows.append({"metric": key, "value": metrics[key]})
            used.add(key)
    for key, value in metrics.items():
        if key in used or key in {"monthly_pnl_table", "ftmo_account_check"}:
            continue
        if _is_scalar(value):
            rows.append({"metric": key, "value": value})
    return pd.DataFrame(rows)


def show_kpi_dashboard(metrics: Mapping[str, Any] | None, *, title: str = "KPI Dashboard") -> pd.DataFrame:
    """Hiển thị bảng KPI chính, có tô màu theo ngưỡng đơn giản.

    Làm gì
    ------
    Thay vì `_display_obj(pd.Series(metrics))`, helper này sắp xếp metric theo thứ tự
    đọc tự nhiên: số lệnh -> hiệu suất -> drawdown -> chất lượng trade. Các ô
    quan trọng được tô màu để nhìn nhanh tốt/xấu.
    """
    frame = metrics_to_frame(metrics)
    show_note(
        title,
        "Read from top to bottom: sample size, profitability, drawdown, then quality metrics such as Sharpe, Sortino, and Avg R.",
    )
    if frame.empty:
        print("No metrics to display.")
        return frame

    display_frame = frame.copy()
    display_frame["value"] = display_frame.apply(
        lambda row: _format_metric_value_by_name(str(row["metric"]), row["value"]),
        axis=1,
    )
    display_frame = display_frame.rename(columns={"metric": "Metric", "value": "Value"})
    styled = (
        display_frame.style
        .apply(_style_metric_row, axis=1)
        .hide(axis="index")
    )
    _display_obj(styled)
    return frame


def show_monthly_pnl(metrics: Mapping[str, Any] | None, *, title: str = "Monthly PnL") -> pd.DataFrame:
    """Hiển thị bảng PnL theo tháng nếu metrics có `monthly_pnl_table`."""
    monthly = (metrics or {}).get("monthly_pnl_table")
    if isinstance(monthly, pd.DataFrame) and not monthly.empty:
        show_note(
            title,
            "Rows are years, columns are months, and values are total PnL in USD. `Year Total` shows the contribution of each year.",
        )
        monthly_view = monthly.copy()
        monthly_view["Year Total"] = monthly_view.sum(axis=1)
        total_row = monthly_view.sum(axis=0)
        total_row.name = "All Years"
        monthly_view = pd.concat([monthly_view, total_row.to_frame().T])
        _display_obj(_style_dataframe_cells(monthly_view.style.format("{:,.2f}"), _style_pnl_cell))
        return monthly_view
    print("No monthly_pnl_table is available.")
    return pd.DataFrame()


def trades_to_frame(trades: list[dict[str, Any]] | pd.DataFrame | None) -> pd.DataFrame:
    """Chuẩn hóa trade log thành DataFrame đã parse datetime nếu có thể."""
    if trades is None:
        return pd.DataFrame()
    frame = trades.copy() if isinstance(trades, pd.DataFrame) else pd.DataFrame(trades)
    for col in ("entry_time", "exit_time", "half1_exit_time"):
        if col in frame.columns:
            frame[col] = pd.to_datetime(frame[col], errors="coerce")
    return frame


def show_trade_explorer(
    trades: list[dict[str, Any]] | pd.DataFrame | None,
    *,
    tail: int = 30,
    title: str = "Trade Explorer",
) -> pd.DataFrame:
    """Hiển thị trade log cùng các lát cắt giúp đọc hành vi strategy.

    Các bảng phụ trả lời nhanh:
    - Lệnh đóng vì lý do gì nhiều nhất?
    - BUY hay SELL đóng góp tốt hơn?
    - Những lệnh lời/lỗ lớn nhất là lệnh nào?
    """
    frame = trades_to_frame(trades)
    show_note(
        title,
        "Use this after the KPI table to inspect trade quality. If the headline metrics are weak, start with `exit_reason`, side, and the largest losses.",
    )
    if frame.empty:
        print("No trades to display.")
        return frame

    preferred = [
        "symbol", "entry_time", "exit_time", "direction", "entry", "exit",
        "sl_initial", "tp", "pnl_usd", "r_multiple", "exit_reason",
        "partial_tp_hit", "commission", "swap_cost", "equity",
    ]
    cols = [c for c in preferred if c in frame.columns]
    _display_obj(frame[cols].tail(tail).style.format(_table_formatters(frame[cols])))

    if "exit_reason" in frame.columns:
        show_note("Exit Reason", "Counts how trades were closed, highlighting whether exits are mostly stop losses, reversals, or forced closes.")
        _display_obj(frame.groupby("exit_reason").size().sort_values(ascending=False).to_frame("count"))

    if {"direction", "pnl_usd"}.issubset(frame.columns):
        show_note("PnL By Side", "Compares BUY and SELL trades to spot whether one side is dragging performance down.")
        by_dir = frame.groupby("direction")["pnl_usd"].agg(["count", "sum", "mean"])
        _display_obj(by_dir.style.format({"sum": "{:,.2f}", "mean": "{:,.2f}"}))

    if "pnl_usd" in frame.columns:
        show_note("Top Winners / Losers", "Extreme trades often reveal the market conditions the strategy likes or struggles with.")
        _display_obj(frame.nlargest(10, "pnl_usd")[cols].style.format(_table_formatters(frame[cols])))
        _display_obj(frame.nsmallest(10, "pnl_usd")[cols].style.format(_table_formatters(frame[cols])))
    return frame


def plot_equity_dashboard(
    equity: pd.Series | None,
    trades: list[dict[str, Any]] | pd.DataFrame | None = None,
    *,
    title: str = "Equity dashboard",
):
    """Vẽ equity, drawdown và cumulative trade PnL trên cùng một dashboard."""
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(3, 1, figsize=(16, 12), sharex=False)
    fig.suptitle(title, fontsize=14, fontweight="bold")

    eq = _as_series(equity)
    if len(eq):
        eq.plot(ax=axes[0], color="#00D4FF", lw=1.8)
        axes[0].set_title("Equity curve")
        axes[0].set_ylabel("Equity")

        dd = calc_drawdown(eq)
        dd.plot(ax=axes[1], color="#FF6B6B", lw=1.3)
        axes[1].fill_between(dd.index, dd.values, 0, color="#FF6B6B", alpha=0.20)
        axes[1].set_title("Drawdown (%)")
        axes[1].set_ylabel("%")
    else:
        axes[0].set_title("No equity data")
        axes[1].set_title("No drawdown data")

    trades_df = trades_to_frame(trades)
    if "pnl_usd" in trades_df.columns and not trades_df.empty:
        trades_df["pnl_usd"].cumsum().reset_index(drop=True).plot(
            ax=axes[2], color="#6BCB77", lw=1.6,
        )
        axes[2].axhline(0, color="#999999", lw=0.8, alpha=0.7)
        axes[2].set_title("Cumulative trade PnL")
        axes[2].set_xlabel("Trade number")
    else:
        axes[2].set_title("No trade PnL")

    for ax in axes:
        ax.grid(alpha=0.25)
    plt.tight_layout()
    _show_plot(fig)
    return fig, axes


def plot_trade_distribution(trades: list[dict[str, Any]] | pd.DataFrame | None):
    """Vẽ phân phối PnL và R-multiple của trade log."""
    import matplotlib.pyplot as plt

    frame = trades_to_frame(trades)
    fig, axes = plt.subplots(1, 2, figsize=(16, 5))
    if frame.empty:
        for ax in axes:
            ax.set_title("No trade data")
        return fig, axes

    if "pnl_usd" in frame.columns:
        frame["pnl_usd"].plot(kind="hist", bins=40, ax=axes[0], color="#00D4FF", alpha=0.8)
        axes[0].axvline(0, color="#FFFFFF", lw=1, alpha=0.8)
        axes[0].set_title("PnL USD Distribution")
    if "r_multiple" in frame.columns:
        frame["r_multiple"].plot(kind="hist", bins=40, ax=axes[1], color="#FFD93D", alpha=0.8)
        axes[1].axvline(0, color="#FFFFFF", lw=1, alpha=0.8)
        axes[1].set_title("R Multiple Distribution")
    for ax in axes:
        ax.grid(alpha=0.25)
    plt.tight_layout()
    _show_plot(fig)
    return fig, axes


def plot_price_with_trades(
    signal_data: pd.DataFrame,
    trades: list[dict[str, Any]] | pd.DataFrame | None,
    *,
    symbol: str = "",
    max_trades: int = 150,
):
    """Vẽ close/MA kèm entry và exit của các trade.

    Đây là chart kiểm tra trực quan quan trọng nhất sau backtest: nếu entry/exit
    nhìn vô lý trên chart, cần quay lại kiểm tra signal hoặc execution.
    """
    import matplotlib.pyplot as plt

    if signal_data is None or signal_data.empty:
        print("No signal_data to plot.")
        return None, None

    df = signal_data.copy()
    idx = _time_index(df)
    fig, ax = plt.subplots(figsize=(18, 7))

    if "close" in df.columns:
        ax.plot(idx, df["close"], color="#E6EDF3", lw=1.0, label="close")
    if "ma" in df.columns:
        ax.plot(idx, df["ma"], color="#FFD93D", lw=1.1, label="ma")

    frame = trades_to_frame(trades).tail(max_trades)
    if not frame.empty:
        buy = frame["direction"].astype(str).str.upper().eq("BUY") if "direction" in frame else pd.Series(False, index=frame.index)
        sell = frame["direction"].astype(str).str.upper().eq("SELL") if "direction" in frame else pd.Series(False, index=frame.index)
        if {"entry_time", "entry"}.issubset(frame.columns):
            ax.scatter(frame.loc[buy, "entry_time"], frame.loc[buy, "entry"], marker="^", s=55, color="#6BCB77", label="BUY entry")
            ax.scatter(frame.loc[sell, "entry_time"], frame.loc[sell, "entry"], marker="v", s=55, color="#FF6B6B", label="SELL entry")
        if {"exit_time", "exit"}.issubset(frame.columns):
            colors = frame.get("pnl_usd", pd.Series(0, index=frame.index)).apply(lambda v: "#00FF88" if v > 0 else "#FF4444")
            ax.scatter(frame["exit_time"], frame["exit"], marker="x", s=45, c=colors, label="exit")

    ax.set_title(f"{symbol} price + trade markers".strip())
    ax.grid(alpha=0.25)
    ax.legend(loc="best")
    plt.tight_layout()
    _show_plot(fig)
    return fig, ax


def per_symbol_metrics_frame(portfolio_result: Any) -> pd.DataFrame:
    """Tạo bảng metrics theo từng symbol từ `PortfolioBacktestResult`."""
    rows: list[dict[str, Any]] = []
    for sym, result in getattr(portfolio_result, "symbol_results", {}).items():
        row = {
            "symbol": sym,
            "raw_rows": len(getattr(result, "raw_data", [])),
            "signals": int((result.signal_data["signal"] != 0).sum())
            if hasattr(result, "signal_data") and "signal" in result.signal_data.columns else 0,
            "trades": len(getattr(result, "trades", [])),
            "final_equity": float(result.equity.iloc[-1]) if len(getattr(result, "equity", [])) else np.nan,
        }
        row.update({k: v for k, v in getattr(result, "metrics", {}).items() if _is_scalar(v)})
        rows.append(row)
    return pd.DataFrame(rows)


def show_portfolio_summary(portfolio_result: Any) -> pd.DataFrame:
    """Hiển thị KPI portfolio và bảng đóng góp theo symbol."""
    show_kpi_dashboard(getattr(portfolio_result, "metrics", {}), title="Portfolio KPI")
    frame = per_symbol_metrics_frame(portfolio_result)
    if frame.empty:
        print("No per-symbol metrics are available.")
        return frame
    show_note(
        "Per-Symbol Contribution",
        "This table shows which symbols contribute most by trade count, PnL, drawdown, or Sharpe.",
    )
    _display_obj(frame.style.format(_table_formatters(frame)))
    return frame


def plot_portfolio_dashboard(portfolio_result: Any):
    """Vẽ dashboard portfolio: combined equity, drawdown, per-symbol equity, PnL contribution."""
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(18, 12))
    axes = axes.ravel()

    combined = _as_series(getattr(portfolio_result, "combined_equity", pd.Series(dtype=float)))
    if len(combined):
        combined.plot(ax=axes[0], color="#00D4FF", lw=1.8, title="Combined portfolio equity")
        dd = calc_drawdown(combined)
        dd.plot(ax=axes[1], color="#FF6B6B", lw=1.3, title="Portfolio drawdown (%)")
        axes[1].fill_between(dd.index, dd.values, 0, color="#FF6B6B", alpha=0.20)
    else:
        axes[0].set_title("No combined equity")
        axes[1].set_title("No drawdown")

    eq_frame = getattr(portfolio_result, "equity_frame", pd.DataFrame())
    if isinstance(eq_frame, pd.DataFrame) and not eq_frame.empty:
        eq_frame.plot(ax=axes[2], lw=1.1, title="Per-symbol equity")
    else:
        axes[2].set_title("No per-symbol equity")

    sym_frame = per_symbol_metrics_frame(portfolio_result)
    pnl_col = "total_pnl" if "total_pnl" in sym_frame.columns else None
    if pnl_col:
        sym_frame.sort_values(pnl_col).plot(
            x="symbol", y=pnl_col, kind="barh", ax=axes[3], color="#6BCB77", legend=False,
            title="PnL contribution by symbol",
        )
        axes[3].axvline(0, color="#FFFFFF", lw=0.8, alpha=0.8)
    else:
        axes[3].set_title("No PnL contribution")

    for ax in axes:
        ax.grid(alpha=0.25)
    plt.tight_layout()
    _show_plot(fig)
    return fig, axes


def show_ftmo_check(metrics: Mapping[str, Any] | None) -> pd.DataFrame:
    """Hiển thị kết quả kiểm tra FTMO cấp portfolio nếu có."""
    check = (metrics or {}).get("ftmo_account_check")
    if not isinstance(check, Mapping):
        print("No ftmo_account_check is available in metrics.")
        return pd.DataFrame()
    frame = pd.DataFrame([check]).T.rename(columns={0: "Value"})
    show_note(
        "FTMO Account Check",
        "This is the account-level portfolio check, separate from each symbol sleeve's limits.",
    )
    _display_obj(frame)
    return frame


def plot_optimization_dashboard(grid: pd.DataFrame, *, score_col: str = "score", top_n: int = 20):
    """Vẽ dashboard cho kết quả grid search symbol."""
    import matplotlib.pyplot as plt

    if grid is None or grid.empty:
        print("Grid is empty; nothing to plot.")
        return None, None

    score_col = score_col if score_col in grid.columns else _first_existing(grid, ["sharpe", "profit_factor", "total_return"])
    fig, axes = plt.subplots(2, 2, figsize=(18, 12))
    axes = axes.ravel()

    top = grid.head(top_n).copy()
    top_label = top.apply(lambda r: f"k={r.get('ktp', np.nan):.2g}, x={r.get('x', np.nan):.2g}, ma={r.get('ma_period', np.nan):.0f}", axis=1)
    axes[0].barh(range(len(top)), top[score_col], color="#00D4FF")
    axes[0].set_yticks(range(len(top)))
    axes[0].set_yticklabels(top_label, fontsize=8)
    axes[0].invert_yaxis()
    axes[0].set_title(f"Top {top_n} candidates by {score_col}")

    if {"max_drawdown", "total_return"}.issubset(grid.columns):
        scatter = axes[1].scatter(
            grid["max_drawdown"].abs(), grid["total_return"],
            c=grid[score_col], cmap="viridis", s=35, alpha=0.8,
        )
        axes[1].set_xlabel("|Max drawdown| %")
        axes[1].set_ylabel("Total return %")
        axes[1].set_title("Return vs drawdown")
        fig.colorbar(scatter, ax=axes[1], label=score_col)

    _plot_pivot_heatmap(grid, axes[2], index="ktp", columns="x", values=score_col, title=f"{score_col}: ktp x x")
    _plot_pivot_heatmap(
        grid,
        axes[3],
        index="ma_period",
        columns="trailing_activation",
        values=score_col,
        title=f"{score_col}: MA x trailing",
    )

    for ax in axes:
        ax.grid(alpha=0.20)
    plt.tight_layout()
    _show_plot(fig)
    return fig, axes


def show_best_candidate(grid: pd.DataFrame, *, top_n: int = 15) -> pd.Series:
    """Hiển thị best candidate và bảng top N sau optimize."""
    if grid is None or grid.empty:
        raise RuntimeError("Grid search returned no candidates.")
    best = grid.iloc[0]
    show_note(
        "Best Candidate",
        "The first row is the optimizer's highest-ranked parameter set. Always confirm it with `run_symbol_backtest()` before relying on it.",
    )
    _display_obj(best.to_frame("value"))
    _display_obj(grid.head(top_n))
    return best


def show_optimizer_filter_report(frame: pd.DataFrame, *, title: str = "Optimizer filter") -> pd.DataFrame:
    """Display pass/fail gates for optimizer candidates."""
    if frame is None or frame.empty:
        print("No candidates to display.")
        return pd.DataFrame()
    passed = int(frame.get("filter_pass", pd.Series(False, index=frame.index)).sum())
    show_note(
        title,
        f"{passed}/{len(frame)} candidates passed the pre-ranking filter. If too few pass, loosen the thresholds or expand the data window.",
    )
    cols = [
        c for c in [
            *OPTIMIZER_PARAM_COLS,
            "filter_trades", "filter_pf", "filter_return", "filter_drawdown_abs",
            "filter_sharpe", "filter_pass", "robust_score", "is_plateau", "stable_ratio",
        ]
        if c in frame.columns
    ]
    _display_obj(frame[cols].head(30).style.format(_table_formatters(frame[cols])).hide(axis="index"))
    return frame


def show_candidate_validation_report(frame: pd.DataFrame, *, title: str = "Full validation candidates") -> pd.DataFrame:
    """Display full validation rows with fast-vs-full deltas when present."""
    if frame is None or frame.empty:
        print("No validation results to display.")
        return pd.DataFrame()
    show_note(
        title,
        "This table uses the full backtest. Select final params from this validation table, not directly from the fast grid.",
    )
    cols = [
        c for c in [
            "candidate_rank", *OPTIMIZER_PARAM_COLS, "robust_score",
            "total_trades", "profit_factor", "total_return", "max_drawdown", "sharpe",
            "full_trades", "fast_trades", "trades_gap",
            "full_pf", "fast_pf", "pf_gap",
            "full_return", "fast_return", "return_gap",
            "full_drawdown", "fast_drawdown", "drawdown_gap",
            "full_sharpe", "fast_sharpe", "sharpe_gap",
        ]
        if c in frame.columns
    ]
    _display_obj(frame[cols].style.format(_table_formatters(frame[cols])).hide(axis="index"))
    return frame


def show_selected_params(params: Mapping[str, Any], *, title: str = "Selected params") -> pd.DataFrame:
    """Display the final parameter block for copy/export."""
    frame = pd.DataFrame([{"Parameter": k, "Value": v} for k, v in params.items()])
    show_note(title, "These parameters passed the current notebook's filter and full-validation steps.")
    _display_obj(
        frame.style
        .hide(axis="index")
        .set_properties(**{"text-align": "left", "white-space": "normal"})
        .set_table_styles([{"selector": "th", "props": [("text-align", "left")]}])
    )
    return frame


def plot_walkforward_dashboard(wf_df: pd.DataFrame):
    """Vẽ dashboard stability cho kết quả walk-forward."""
    import matplotlib.pyplot as plt

    if wf_df is None or wf_df.empty:
        print("Walk-forward returned no result rows.")
        return None, None

    fig, axes = plt.subplots(2, 2, figsize=(18, 10))
    axes = axes.ravel()
    x = "window" if "window" in wf_df.columns else None

    _bar_if_exists(wf_df, x, "total_return", axes[0], "OOS return by window", "#6BCB77")
    _bar_if_exists(wf_df, x, "max_drawdown", axes[1], "OOS max drawdown by window", "#FF6B6B")
    _bar_if_exists(wf_df, x, "sharpe", axes[2], "OOS Sharpe by window", "#00D4FF")
    _bar_if_exists(wf_df, x, "profit_factor", axes[3], "OOS Profit Factor by window", "#FFD93D")
    for ax in axes:
        ax.grid(alpha=0.25)
    plt.tight_layout()
    _show_plot(fig)
    return fig, axes


def summarize_walkforward(wf_df: pd.DataFrame) -> pd.DataFrame:
    """Tạo bảng tóm tắt stability từ `wf_df`."""
    if wf_df is None or wf_df.empty:
        return pd.DataFrame()
    rows: dict[str, Any] = {"windows": len(wf_df)}
    if "total_return" in wf_df:
        rows["positive_windows"] = int((wf_df["total_return"] > 0).sum())
        rows["positive_window_pct"] = round(rows["positive_windows"] / len(wf_df) * 100, 1)
        rows["median_return"] = round(float(wf_df["total_return"].median()), 2)
        rows["worst_return"] = round(float(wf_df["total_return"].min()), 2)
    if "max_drawdown" in wf_df:
        rows["worst_drawdown"] = round(float(wf_df["max_drawdown"].min()), 2)
    if "sharpe" in wf_df:
        rows["median_sharpe"] = round(float(wf_df["sharpe"].median()), 2)
    frame = pd.DataFrame([rows]).T.rename(columns={0: "Value"})
    show_note("Walk-Forward Stability Summary", "This summary checks whether the strategy stays stable across out-of-sample windows.")
    _display_obj(frame)
    return frame


def plot_scan_summary(summary_df: pd.DataFrame):
    """Vẽ summary scanner nhiều symbol."""
    import matplotlib.pyplot as plt

    if summary_df is None or summary_df.empty:
        print("No scanner summary is available.")
        return None, None
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    cols = [
        ("n_pass", "Pass signals", "#00D4FF"),
        ("win_pct", "Win %", "#6BCB77"),
        ("avg_rr", "Average RR", "#FFD93D"),
    ]
    for ax, (col, title, color) in zip(axes, cols):
        if col in summary_df.columns:
            summary_df.sort_values(col).plot(x="symbol", y=col, kind="barh", ax=ax, color=color, legend=False, title=title)
        else:
            ax.set_title(f"Missing column: {col}")
        ax.grid(alpha=0.25)
    plt.tight_layout()
    _show_plot(fig)
    return fig, axes


def compare_metrics_frame(results: Mapping[str, Any]) -> pd.DataFrame:
    """Tạo bảng so sánh metrics cho dict kết quả theo mode/name."""
    rows = {}
    for name, result in results.items():
        metrics = getattr(result, "metrics", result)
        rows[name] = {k: v for k, v in (metrics or {}).items() if _is_scalar(v)}
    return pd.DataFrame(rows).T


__all__ = [
    "KPI_ORDER",
    "MONEY_COLS",
    "PCT_COLS",
    "TRADE_COLUMN_LABELS",
    "METRIC_EXPLANATIONS",
    "GOOD_HIGH",
    "GOOD_LOW",
    "configure_notebook",
    "show_note",
    "show_run_config",
    "metrics_to_frame",
    "show_kpi_dashboard",
    "show_monthly_pnl",
    "trades_to_frame",
    "show_trade_explorer",
    "plot_equity_dashboard",
    "plot_trade_distribution",
    "plot_price_with_trades",
    "per_symbol_metrics_frame",
    "show_portfolio_summary",
    "plot_portfolio_dashboard",
    "show_ftmo_check",
    "plot_optimization_dashboard",
    "show_best_candidate",
    "show_optimizer_filter_report",
    "show_candidate_validation_report",
    "show_selected_params",
    "plot_walkforward_dashboard",
    "summarize_walkforward",
    "plot_scan_summary",
    "compare_metrics_frame",
]
