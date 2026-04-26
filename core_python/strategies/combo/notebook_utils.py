"""Helper giao diện và phân tích dùng chung cho các notebook Combo.

1. Mục đích tổng quan
---------------------
Các notebook trong `strategies/combo/` có cùng một nhu cầu lặp lại:

- In cấu hình lần chạy theo cách dễ kiểm tra.
- Hiển thị KPI gọn, có thứ tự ưu tiên.
- Vẽ equity, drawdown, cumulative PnL và phân phối trade.
- So sánh kết quả theo symbol, theo account mode, theo window walk-forward.
- Xuất kết quả ra thư mục `reports/` khi cần lưu lại một lần chạy.

Nếu để từng notebook tự viết lại các đoạn này, notebook sẽ dài, khó đọc và dễ
lệch style. File này gom phần trình bày vào một nơi để notebook chỉ còn phần
"chọn tham số -> chạy pipeline -> gọi helper để xem kết quả".

2. Cách dùng nhanh
------------------
Trong notebook, sau khi bootstrap path:

```python
from strategies.combo.notebook_utils import (
    configure_notebook,
    show_run_config,
    show_kpi_dashboard,
    plot_equity_dashboard,
)

configure_notebook()
show_run_config("Backtest config", RUN_CONFIG)
```

3. Lưu ý thiết kế
-----------------
Các helper ở đây chỉ phục vụ hiển thị/phân tích. Chúng không thay đổi logic vào
lệnh, không sửa trade log và không tính lại kết quả backtest cốt lõi. Vì vậy có
thể cải thiện giao diện tại đây mà không ảnh hưởng strategy.
"""

from __future__ import annotations

import html
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping, MutableMapping

import numpy as np
import pandas as pd

from strategies.combo.symbol.selection import (
    OPTIMIZER_PARAM_COLS,
    annotate_optimizer_plateau,
    filter_optimizer_candidates,
    rank_optimizer_candidates,
    summarize_candidate_validation,
    summarize_optimizer_plateau,
    validate_symbol_candidates,
)

try:  # IPython chỉ chắc chắn có trong notebook; khi import bằng py_compile vẫn ổn.
    from IPython.display import HTML, Markdown, display
except Exception:  # pragma: no cover - fallback cho môi trường console thuần.
    HTML = Markdown = None

    def display(obj: Any) -> None:
        print(obj)


_ACTIVE_WIDGET_OUTPUT: Any | None = None


@contextmanager
def _widget_output_target(output: Any):
    """Route custom HTML helpers directly to a widget Output during callbacks."""
    global _ACTIVE_WIDGET_OUTPUT
    previous = _ACTIVE_WIDGET_OUTPUT
    _ACTIVE_WIDGET_OUTPUT = output
    try:
        yield
    finally:
        _ACTIVE_WIDGET_OUTPUT = previous


def _display_html(html_text: str) -> None:
    """Display HTML once, avoiding VS Code duplicate display publishers in widgets."""
    if HTML is None:
        print(html_text)
        return
    obj = HTML(html_text)
    target = _ACTIVE_WIDGET_OUTPUT
    if target is not None and hasattr(target, "append_display_data"):
        target.append_display_data(obj)
    else:
        display(obj)


def _display_obj(obj: Any) -> None:
    """Display any rich object once, routing widget callback output explicitly."""
    target = _ACTIVE_WIDGET_OUTPUT
    if target is not None and hasattr(target, "append_display_data"):
        html_text = _object_to_widget_html(obj)
        if html_text is not None:
            target.append_display_data(HTML(html_text) if HTML is not None else html_text)
        else:
            target.append_display_data(obj)
    else:
        display(obj)


def _show_plot(fig: Any) -> None:
    """Show a matplotlib figure through the active widget output when needed."""
    target = _ACTIVE_WIDGET_OUTPUT
    if target is not None and hasattr(target, "append_display_data"):
        html_text = _figure_to_widget_html(fig)
        if html_text and HTML is not None:
            target.append_display_data(HTML(html_text))
        else:
            target.append_display_data(fig)
        try:
            import matplotlib.pyplot as plt

            plt.close(fig)
        except Exception:
            pass
    else:
        try:
            import matplotlib.pyplot as plt

            plt.show()
        except Exception:
            display(fig)


def _object_to_widget_html(obj: Any) -> str | None:
    """Convert pandas display objects to HTML that VS Code widget outputs render reliably."""
    try:
        if obj.__class__.__name__ == "Styler" and hasattr(obj, "to_html"):
            return _wrap_table_html(obj.to_html())
        if isinstance(obj, pd.Series):
            return _wrap_table_html(obj.to_frame().to_html(border=0, classes="combo-table"))
        if isinstance(obj, pd.DataFrame):
            return _wrap_table_html(obj.to_html(border=0, classes="combo-table"))
    except Exception:
        return None
    return None


def _wrap_table_html(table_html: str) -> str:
    return f"""
    <div style="overflow-x:auto;margin:10px 0 14px 0;">
      <style>
        .combo-table {{
          border-collapse: collapse;
          width: auto;
          max-width: 100%;
          font-size: 13px;
          color: #111827;
        }}
        .combo-table th {{
          background: #E5E7EB;
          color: #111827;
          font-weight: 700;
          padding: 7px 10px;
          text-align: left;
          border-bottom: 1px solid #D1D5DB;
        }}
        .combo-table td {{
          padding: 7px 10px;
          border-bottom: 1px solid #F1F5F9;
          vertical-align: top;
        }}
        .combo-table tbody tr:nth-child(even) td {{
          background: #F8FAFC;
        }}
      </style>
      {table_html}
    </div>
    """


def _figure_to_widget_html(fig: Any) -> str | None:
    """Render matplotlib figures as inline PNG so widget output cannot drop them."""
    try:
        import base64
        from io import BytesIO

        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
        data = base64.b64encode(buf.getvalue()).decode("ascii")
        return (
            '<div style="overflow-x:auto;margin:12px 0 18px 0;">'
            f'<img src="data:image/png;base64,{data}" '
            'style="max-width:100%;height:auto;border:1px solid #E5E7EB;border-radius:6px;" />'
            "</div>"
        )
    except Exception:
        return None


KPI_ORDER = [
    "total_trades",
    "wins",
    "losses",
    "win_rate",
    "profit_factor",
    "total_pnl",
    "total_return",
    "annual_return",
    "max_drawdown",
    "max_drawdown_usd",
    "sharpe",
    "sortino",
    "calmar",
    "recovery_factor",
    "avg_win",
    "avg_loss",
    "avg_rr",
    "avg_r",
    "partial_tp_rate",
    "total_swap_cost",
]

MONEY_COLS = {
    "total_pnl",
    "max_drawdown_usd",
    "avg_win",
    "avg_loss",
    "commission",
    "swap_cost",
    "pnl_usd",
    "final_equity",
    "Total PnL",
    "Max DD USD",
    "Avg Win",
    "Avg Loss",
    "Commission",
    "Swap Cost",
    "PnL USD",
    "Equity",
    "Final Equity",
    "sum",
    "mean",
    "median",
    "std",
    "min",
    "max",
}

PCT_COLS = {
    "win_rate",
    "total_return",
    "annual_return",
    "max_drawdown",
    "partial_tp_rate",
    "ret",
    "maxdd",
    "Win Rate",
    "Total Return",
    "Annual Return",
    "Max Drawdown",
    "Partial TP Rate",
}

TRADE_COLUMN_LABELS = {
    "symbol": "Symbol",
    "entry_time": "Entry Time",
    "exit_time": "Exit Time",
    "direction": "Side",
    "entry": "Entry",
    "exit": "Exit",
    "sl_initial": "Initial SL",
    "tp": "TP / Partial TP",
    "pnl_usd": "PnL USD",
    "r_multiple": "R Multiple",
    "exit_reason": "Exit Reason",
    "partial_tp_hit": "Partial TP?",
    "commission": "Commission",
    "swap_cost": "Swap Cost",
    "equity": "Equity",
    "total_return": "Total Return",
    "max_drawdown": "Max Drawdown",
    "profit_factor": "Profit Factor",
    "sharpe": "Sharpe",
    "win_rate": "Win Rate",
    "trades": "Trades",
    "signals": "Signals",
    "final_equity": "Final Equity",
}

METRIC_EXPLANATIONS = [
    ("total_trades", "Số lệnh đã đóng", "Mẫu càng ít thì kết luận càng kém chắc."),
    ("win_rate", "Tỷ lệ thắng", "Không đủ để đánh giá một mình; cần đọc cùng avg win/loss và PF."),
    ("profit_factor", "Tổng lời / tổng lỗ tuyệt đối", "> 1 là có lợi thế trong mẫu backtest; càng cao càng tốt nhưng quá cao bất thường cần kiểm tra overfit."),
    ("total_return", "Lợi nhuận % trên vốn đầu kỳ", "Cho biết tăng trưởng tổng nhưng không nói rủi ro đi kèm."),
    ("max_drawdown", "Mức sụt giảm sâu nhất từ đỉnh equity", "Số âm; càng gần 0 càng dễ chịu. Đây là metric rủi ro quan trọng nhất."),
    ("sharpe", "Return điều chỉnh theo biến động", "Đọc tốt hơn khi số trade đủ lớn; > 1 thường đáng chú ý."),
    ("sortino", "Giống Sharpe nhưng chỉ phạt biến động âm", "Hữu ích khi equity có nhiều nhịp tăng mạnh."),
    ("avg_r", "R multiple trung bình", "Lãi/lỗ trung bình theo đơn vị rủi ro ban đầu mỗi trade."),
    ("partial_tp_rate", "Tỷ lệ lệnh từng chạm partial TP", "Cho biết setup có thường đi đủ xa để dời SL về breakeven không."),
]

GOOD_HIGH = {
    "profit_factor": 1.2,
    "sharpe": 1.0,
    "sortino": 1.0,
    "calmar": 1.0,
    "recovery_factor": 1.0,
    "win_rate": 50.0,
    "total_return": 0.0,
    "ret": 0.0,
}

GOOD_LOW = {
    "max_drawdown": -10.0,
    "maxdd": 10.0,
}


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
    """Hiển thị cấu hình lần chạy dưới dạng bảng 2 cột.

    Đây là bước nhỏ nhưng quan trọng: trước khi chạy backtest/optimizer, người
    dùng nhìn lại đúng symbol, account mode, khoảng ngày và số bar. Nó giúp tránh
    nhầm lẫn khi chạy nhiều thí nghiệm liên tiếp.
    """
    rows = [{"tham_so": k, "gia_tri": _short_repr(v)} for k, v in config.items()]
    frame = pd.DataFrame(rows)
    show_note(title, "Bảng dưới đây là cấu hình hiệu lực cho lần chạy hiện tại.")
    _display_obj(frame)
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
        "Đọc nhanh từ trên xuống: số lượng mẫu trade, khả năng sinh lời, mức "
        "drawdown, rồi các chỉ số chất lượng như Sharpe/Sortino/Avg R.",
    )
    if frame.empty:
        print("Không có metrics để hiển thị.")
        return frame

    display_frame = frame.copy()
    display_frame["value"] = display_frame.apply(
        lambda row: _format_metric_value_by_name(str(row["metric"]), row["value"]),
        axis=1,
    )
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
            "Hàng là năm, cột là tháng, giá trị là tổng PnL USD. Cột `Year Total` là tổng PnL của từng năm để đọc nhanh năm nào đóng góp chính.",
        )
        monthly_view = monthly.copy()
        monthly_view["Year Total"] = monthly_view.sum(axis=1)
        total_row = monthly_view.sum(axis=0)
        total_row.name = "All Years"
        monthly_view = pd.concat([monthly_view, total_row.to_frame().T])
        _display_obj(_style_dataframe_cells(monthly_view.style.format("{:,.2f}"), _style_pnl_cell))
        return monthly_view
    print("Không có monthly_pnl_table để hiển thị.")
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
        "Phần này dùng để soi chất lượng lệnh sau khi đã xem KPI tổng. Nếu KPI "
        "xấu, hãy bắt đầu từ `exit_reason`, direction và top loss để tìm nguyên nhân.",
    )
    if frame.empty:
        print("Không có trade để hiển thị.")
        return frame

    preferred = [
        "symbol", "entry_time", "exit_time", "direction", "entry", "exit",
        "sl_initial", "tp", "pnl_usd", "r_multiple", "exit_reason",
        "partial_tp_hit", "commission", "swap_cost", "equity",
    ]
    cols = [c for c in preferred if c in frame.columns]
    _display_obj(frame[cols].tail(tail).style.format(_table_formatters(frame[cols])))

    if "exit_reason" in frame.columns:
        show_note("Exit reason", "Đếm lý do thoát lệnh để biết chiến lược chủ yếu bị SL, reversal hay force-close.")
        _display_obj(frame.groupby("exit_reason").size().sort_values(ascending=False).to_frame("count"))

    if {"direction", "pnl_usd"}.issubset(frame.columns):
        show_note("PnL theo direction", "So sánh BUY và SELL để phát hiện một chiều giao dịch đang kéo kết quả xuống.")
        by_dir = frame.groupby("direction")["pnl_usd"].agg(["count", "sum", "mean"])
        _display_obj(by_dir.style.format({"sum": "{:,.2f}", "mean": "{:,.2f}"}))

    if "pnl_usd" in frame.columns:
        show_note("Top winners / losers", "Các lệnh cực trị thường tiết lộ điều kiện thị trường strategy thích hoặc ghét.")
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
        axes[0].set_title("Không có equity data")
        axes[1].set_title("Không có drawdown data")

    trades_df = trades_to_frame(trades)
    if "pnl_usd" in trades_df.columns and not trades_df.empty:
        trades_df["pnl_usd"].cumsum().reset_index(drop=True).plot(
            ax=axes[2], color="#6BCB77", lw=1.6,
        )
        axes[2].axhline(0, color="#999999", lw=0.8, alpha=0.7)
        axes[2].set_title("Cumulative trade PnL")
        axes[2].set_xlabel("Trade number")
    else:
        axes[2].set_title("Không có trade PnL")

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
            ax.set_title("Không có trade data")
        return fig, axes

    if "pnl_usd" in frame.columns:
        frame["pnl_usd"].plot(kind="hist", bins=40, ax=axes[0], color="#00D4FF", alpha=0.8)
        axes[0].axvline(0, color="#FFFFFF", lw=1, alpha=0.8)
        axes[0].set_title("Phân phối PnL USD")
    if "r_multiple" in frame.columns:
        frame["r_multiple"].plot(kind="hist", bins=40, ax=axes[1], color="#FFD93D", alpha=0.8)
        axes[1].axvline(0, color="#FFFFFF", lw=1, alpha=0.8)
        axes[1].set_title("Phân phối R multiple")
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
        print("Không có signal_data để vẽ.")
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
        print("Không có per-symbol metrics.")
        return frame
    show_note(
        "Per-symbol contribution",
        "Bảng này giúp thấy symbol nào có nhiều trade, lời/lỗ, drawdown hoặc Sharpe nổi bật.",
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
        axes[0].set_title("Không có combined equity")
        axes[1].set_title("Không có drawdown")

    eq_frame = getattr(portfolio_result, "equity_frame", pd.DataFrame())
    if isinstance(eq_frame, pd.DataFrame) and not eq_frame.empty:
        eq_frame.plot(ax=axes[2], lw=1.1, title="Per-symbol equity")
    else:
        axes[2].set_title("Không có per-symbol equity")

    sym_frame = per_symbol_metrics_frame(portfolio_result)
    pnl_col = "total_pnl" if "total_pnl" in sym_frame.columns else None
    if pnl_col:
        sym_frame.sort_values(pnl_col).plot(
            x="symbol", y=pnl_col, kind="barh", ax=axes[3], color="#6BCB77", legend=False,
            title="PnL contribution by symbol",
        )
        axes[3].axvline(0, color="#FFFFFF", lw=0.8, alpha=0.8)
    else:
        axes[3].set_title("Không có PnL contribution")

    for ax in axes:
        ax.grid(alpha=0.25)
    plt.tight_layout()
    _show_plot(fig)
    return fig, axes


def show_ftmo_check(metrics: Mapping[str, Any] | None) -> pd.DataFrame:
    """Hiển thị kết quả kiểm tra FTMO cấp portfolio nếu có."""
    check = (metrics or {}).get("ftmo_account_check")
    if not isinstance(check, Mapping):
        print("Không có ftmo_account_check trong metrics.")
        return pd.DataFrame()
    frame = pd.DataFrame([check]).T.rename(columns={0: "value"})
    show_note(
        "FTMO account check",
        "Đây là kiểm tra ở cấp tổng portfolio, khác với giới hạn từng sleeve symbol.",
    )
    _display_obj(frame)
    return frame


def plot_optimization_dashboard(grid: pd.DataFrame, *, score_col: str = "score", top_n: int = 20):
    """Vẽ dashboard cho kết quả grid search symbol."""
    import matplotlib.pyplot as plt

    if grid is None or grid.empty:
        print("Grid rỗng, không có gì để vẽ.")
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
        raise RuntimeError("Grid search không trả candidate nào.")
    best = grid.iloc[0]
    show_note(
        "Best candidate",
        "Dòng đầu tiên là bộ tham số optimizer đang xếp hạng cao nhất. Hãy luôn "
        "xác nhận lại bằng `run_symbol_backtest()` trước khi tin kết quả.",
    )
    _display_obj(best.to_frame("value"))
    _display_obj(grid.head(top_n))
    return best


def show_optimizer_filter_report(frame: pd.DataFrame, *, title: str = "Optimizer filter") -> pd.DataFrame:
    """Display pass/fail gates for optimizer candidates."""
    if frame is None or frame.empty:
        print("Không có candidate để hiển thị.")
        return pd.DataFrame()
    passed = int(frame.get("filter_pass", pd.Series(False, index=frame.index)).sum())
    show_note(
        title,
        f"{passed}/{len(frame)} candidates vượt qua bộ lọc trước khi rank. Nếu quá ít candidate pass, hãy nới lỏng ngưỡng hoặc mở rộng data.",
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
        print("Không có validation result để hiển thị.")
        return pd.DataFrame()
    show_note(
        title,
        "Bảng này dùng full backtest. Chọn final params từ đây, không chọn trực tiếp từ fast grid.",
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
    frame = pd.DataFrame([{"param": k, "value": v} for k, v in params.items()])
    show_note(title, "Đây là bộ tham số đã qua filter và full validation trong notebook hiện tại.")
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
        print("Walk-forward không có dòng kết quả.")
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
    frame = pd.DataFrame([rows]).T.rename(columns={0: "value"})
    show_note("Walk-forward stability summary", "Tóm tắt này trả lời chiến lược có ổn định qua nhiều OOS window không.")
    _display_obj(frame)
    return frame


def plot_scan_summary(summary_df: pd.DataFrame):
    """Vẽ summary scanner nhiều symbol."""
    import matplotlib.pyplot as plt

    if summary_df is None or summary_df.empty:
        print("Không có scanner summary.")
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
            ax.set_title(f"Thiếu cột {col}")
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


def build_symbol_backtest_widget(
    *,
    symbols: Mapping[str, Any],
    run_symbol_backtest: Any,
    default_config: Mapping[str, Any],
    global_ns: MutableMapping[str, Any] | None = None,
) -> Any:
    """Tạo bảng điều khiển dropdown cho notebook `01_symbol_backtest`.

    Làm gì
    ------
    Helper này gom toàn bộ logic tương tác ra khỏi notebook:

    - Chọn một symbol rồi bấm `Run selected symbol`.
    - Chọn nhiều symbol rồi bấm `Compare selected symbols`.
    - Tự cập nhật các biến quen thuộc trong notebook như `RUN_CONFIG`, `result`,
      `SYMBOL`, `ACCOUNT_MODE`, `symbol_compare_df`.

    Nhận input gì
    -------------
    `symbols`
        Dict symbol từ `strategies.combo.config.SYMBOLS`.

    `run_symbol_backtest`
        Hàm backtest symbol hiện tại.

    `default_config`
        RUN_CONFIG mặc định của notebook.

    `global_ns`
        Thường truyền `globals()` để callback cập nhật biến trong notebook.

    Trả output gì
    -------------
    Một widget `VBox` để `_display_obj()` trong notebook. Nếu `ipywidgets` không khả
    dụng, trả về chuỗi hướng dẫn fallback.
    """
    try:
        import ipywidgets as widgets
    except Exception as exc:  # pragma: no cover - chỉ xảy ra ngoài notebook.
        return f"Không thể tạo dropdown vì ipywidgets chưa sẵn sàng: {exc}"

    ns = global_ns if global_ns is not None else {}
    previous_dashboard = ns.get("_combo_symbol_backtest_dashboard")
    if previous_dashboard is not None and hasattr(previous_dashboard, "close"):
        try:
            previous_dashboard.close()
        except Exception:
            pass
    dashboard_token = object()
    ns["_combo_symbol_backtest_dashboard_token"] = dashboard_token
    symbol_keys = list(symbols.keys())
    default_symbol = default_config.get("symbol", symbol_keys[0] if symbol_keys else "")

    symbol_widget = widgets.Dropdown(
        options=symbol_keys,
        value=default_symbol if default_symbol in symbol_keys else symbol_keys[0],
        description="Symbol:",
        layout=widgets.Layout(width="260px"),
    )
    account_widget = widgets.Dropdown(
        options=["standard", "ftmo"],
        value=str(default_config.get("account_mode", "standard")),
        description="Account:",
        layout=widgets.Layout(width="260px"),
    )
    date_from_widget = widgets.Text(
        value=str(default_config.get("date_from") or ""),
        description="From:",
        placeholder="YYYY-MM-DD",
        layout=widgets.Layout(width="260px"),
    )
    date_to_widget = widgets.Text(
        value="" if default_config.get("date_to") is None else str(default_config.get("date_to")),
        description="To:",
        placeholder="YYYY-MM-DD hoặc để trống",
        layout=widgets.Layout(width="260px"),
    )
    balance_widget = widgets.FloatText(
        value=float(default_config.get("initial_balance", 100_000.0)),
        description="Balance:",
        layout=widgets.Layout(width="260px"),
    )
    max_bars_widget = widgets.IntText(
        value=int(default_config.get("max_bars", 50_000)),
        description="Max bars:",
        layout=widgets.Layout(width="260px"),
    )
    compare_symbols_widget = widgets.SelectMultiple(
        options=symbol_keys,
        value=tuple(symbol_keys[: min(5, len(symbol_keys))]),
        description="Compare:",
        rows=min(10, max(5, len(symbol_keys))),
        layout=widgets.Layout(width="340px"),
    )
    run_button = widgets.Button(
        description="Run selected symbol",
        button_style="success",
        icon="play",
        layout=widgets.Layout(width="210px"),
    )
    compare_button = widgets.Button(
        description="Compare selected symbols",
        button_style="info",
        icon="bar-chart",
        layout=widgets.Layout(width="230px"),
    )
    output = widgets.Output()
    compare_output = widgets.Output()
    state = {"run_active": False, "compare_active": False}

    def current_config() -> dict[str, Any]:
        return {
            "symbol": symbol_widget.value,
            "account_mode": account_widget.value,
            "initial_balance": float(balance_widget.value),
            "date_from": date_from_widget.value.strip() or None,
            "date_to": date_to_widget.value.strip() or None,
            "max_bars": int(max_bars_widget.value),
            "indicator_overrides": dict(default_config.get("indicator_overrides") or {}),
            "symbol_overrides": dict(default_config.get("symbol_overrides") or {}),
            "export_report": bool(default_config.get("export_report", False)),
        }

    def run_selected(_: Any = None) -> None:
        if ns.get("_combo_symbol_backtest_dashboard_token") is not dashboard_token:
            return
        if run_button.disabled or state["run_active"]:
            return
        state["run_active"] = True
        run_button.disabled = True
        run_button.description = "Đang chạy..."
        output.clear_output(wait=False)
        try:
            with output, _widget_output_target(output):
                cfg = current_config()
                ns["RUN_CONFIG"] = cfg
                show_run_config("Cấu hình đang chạy", cfg)
                show_note(
                    "Đang chạy backtest",
                    "Notebook sẽ load dữ liệu, tính tín hiệu Combo, chạy execution engine "
                    "và render báo cáo gọn ngay bên dưới.",
                )
                result = run_symbol_backtest(
                    cfg["symbol"],
                    init_eq=cfg["initial_balance"],
                    account_mode=cfg["account_mode"],
                    date_from=cfg["date_from"],
                    date_to=cfg["date_to"],
                    max_bars=cfg["max_bars"],
                    indicator_overrides=cfg["indicator_overrides"] or None,
                    symbol_overrides=cfg["symbol_overrides"] or None,
                )
                ns["result"] = result
                ns["SYMBOL"] = cfg["symbol"]
                ns["ACCOUNT_MODE"] = cfg["account_mode"]
                ns["INITIAL_BALANCE"] = cfg["initial_balance"]
                try:
                    render_symbol_backtest_report(result, cfg)
                except Exception as exc:
                    _display_html(f'<pre style="color:red">Lỗi khi render báo cáo:<br>{_escape(repr(exc))}</pre>')
        except Exception as exc:
            with output, _widget_output_target(output):
                _display_html(f'<pre style="color:red">Lỗi khi chạy backtest:<br>{_escape(repr(exc))}</pre>')
        finally:
            state["run_active"] = False
            run_button.disabled = False
            run_button.description = "Run selected symbol"

    def run_comparison(_: Any = None) -> None:
        if ns.get("_combo_symbol_backtest_dashboard_token") is not dashboard_token:
            return
        if compare_button.disabled or state["compare_active"]:
            return
        state["compare_active"] = True
        compare_button.disabled = True
        compare_button.description = "Đang chạy..."
        compare_output.clear_output(wait=False)
        try:
            with compare_output, _widget_output_target(compare_output):
                cfg = current_config()
                selected = list(compare_symbols_widget.value)
                if not selected:
                    print("Hãy chọn ít nhất một symbol để so sánh.")
                    return
                show_run_config("Cấu hình so sánh nhiều symbol", {**cfg, "symbols": selected})
                _display_symbol_parameter_overview(symbols, selected)
                rows = []
                results = {}
                for idx, sym in enumerate(selected, start=1):
                    print(f"[{idx}/{len(selected)}] Đang chạy {sym}...")
                    res = run_symbol_backtest(
                        sym,
                        init_eq=cfg["initial_balance"],
                        account_mode=cfg["account_mode"],
                        date_from=cfg["date_from"],
                        date_to=cfg["date_to"],
                        max_bars=cfg["max_bars"],
                    )
                    results[sym] = res
                    rows.append(symbol_result_row(sym, res))
                compare_df = pd.DataFrame(rows)
                ns["symbol_compare_results"] = results
                ns["symbol_compare_df"] = compare_df
                render_symbol_comparison(compare_df)
        except Exception as exc:
            with compare_output, _widget_output_target(compare_output):
                _display_html(f'<pre style="color:red">Lỗi khi so sánh:<br>{_escape(repr(exc))}</pre>')
        finally:
            state["compare_active"] = False
            compare_button.disabled = False
            compare_button.description = "Compare selected symbols"

    run_button.on_click(run_selected)
    compare_button.on_click(run_comparison)

    header = widgets.HTML(
        """
        <div style="padding:10px 12px;border:1px solid #30363D;border-radius:6px;background:#F8FAFC">
          <b>Symbol backtest control</b><br>
          <span style="font-size:12px;color:#374151">
          Chọn symbol và khoảng thời gian, bấm Run để xem báo cáo. Phần Compare dùng cùng cấu hình hiện tại để so sánh nhiều symbol.
          </span>
        </div>
        """
    )
    dashboard = widgets.VBox([
        header,
        widgets.HBox([symbol_widget, account_widget, date_from_widget, date_to_widget]),
        widgets.HBox([balance_widget, max_bars_widget, run_button]),
        output,
        widgets.HTML("<hr>"),
        widgets.HBox([compare_symbols_widget, compare_button]),
        compare_output,
    ])
    ns["_combo_symbol_backtest_dashboard"] = dashboard
    return dashboard


def render_symbol_backtest_report(result: Any, run_config: Mapping[str, Any] | None = None) -> pd.DataFrame:
    """Render báo cáo gọn và đẹp cho một `SymbolBacktestResult`.

    Báo cáo này được dùng trong widget 3B để tránh output quá dài và rời rạc.
    """
    metrics = getattr(result, "metrics", {}) or {}
    trades = getattr(result, "trades", [])
    equity = getattr(result, "equity", pd.Series(dtype=float))
    symbol = getattr(result, "symbol", (run_config or {}).get("symbol", ""))
    mode = getattr(result, "account_mode", (run_config or {}).get("account_mode", ""))
    loaded_rows = len(getattr(result, "raw_data", []))
    signal_data = getattr(result, "signal_data", pd.DataFrame())
    window_rows = _count_signal_window_rows(signal_data)
    signals = int((signal_data["signal"] != 0).sum()) if isinstance(signal_data, pd.DataFrame) and "signal" in signal_data.columns else 0
    final_equity = float(equity.iloc[-1]) if len(equity) else np.nan

    _display_result_header(
        title=f"{symbol} backtest result",
        subtitle=f"Account mode: {mode} | Date: {(run_config or {}).get('date_from')} -> {(run_config or {}).get('date_to') or 'latest'}",
        items={
            "Window rows": window_rows,
            "Loaded rows": loaded_rows,
            "Signals": signals,
            "Trades": len(trades),
            "Final equity": final_equity,
        },
    )
    _display_symbol_config_details(getattr(result, "symbol_config", {}), run_config or {}, title="Cấu hình giao dịch hiệu lực")
    _display_metric_cards(metrics)
    _display_metric_explanations(metrics)
    show_note(
        "Cách đọc nhanh",
        "Đầu tiên nhìn Profit Factor, Total Return, Max Drawdown và Sharpe. Sau đó xem equity/drawdown để biết lợi nhuận có mượt không, rồi mới soi trade log.",
    )
    show_kpi_dashboard(metrics, title=f"{symbol} KPI chi tiết")
    show_monthly_pnl(metrics, title=f"{symbol} monthly PnL")
    plot_equity_dashboard(equity, trades, title=f"{symbol} equity / drawdown / trade PnL")
    trade_frame = show_trade_summary_compact(trades, title=f"{symbol} trade summary")
    return trade_frame


def render_symbol_comparison(compare_df: pd.DataFrame) -> pd.DataFrame:
    """Render bảng so sánh nhiều symbol sau khi chạy batch trong widget."""
    if compare_df is None or compare_df.empty:
        print("Không có dữ liệu so sánh.")
        return pd.DataFrame()

    show_note(
        "Bảng so sánh symbol",
        "Tất cả symbol bên dưới được chạy cùng account mode, date range, initial balance và max_bars. Đây là cách nhìn nhanh config hiện tại hoạt động ra sao trên từng symbol.",
    )
    preferred = [
        "symbol", "window_rows", "raw_rows", "trades", "signals", "total_return", "max_drawdown",
        "profit_factor", "sharpe", "win_rate", "total_pnl", "final_equity",
    ]
    cols = [c for c in preferred if c in compare_df.columns]
    sort_cols = [c for c in ["sharpe", "profit_factor", "total_return"] if c in compare_df.columns]
    view = compare_df[cols].copy()
    if sort_cols:
        view = view.sort_values(sort_cols, ascending=False)
    display_view = _rename_columns_for_display(view)
    _display_obj(
        display_view.style
        .format(_table_formatters(display_view))
        .apply(_style_symbol_compare_row, axis=1)
        .hide(axis="index")
    )
    _display_compare_takeaways(compare_df)
    _plot_symbol_compare_dashboard(compare_df)
    return view


def _count_signal_window_rows(signal_data: Any) -> int:
    """Count bars inside the selected backtest date window."""
    if not isinstance(signal_data, pd.DataFrame) or signal_data.empty:
        return 0
    if "in_window" in signal_data.columns:
        return int(signal_data["in_window"].fillna(False).astype(bool).sum())
    return int(len(signal_data))


def symbol_result_row(symbol: str, result_obj: Any) -> dict[str, Any]:
    """Rút gọn `SymbolBacktestResult` thành một dòng dùng cho compare table."""
    signal_data = getattr(result_obj, "signal_data", pd.DataFrame())
    equity = getattr(result_obj, "equity", pd.Series(dtype=float))
    row = {
        "symbol": symbol,
        "account_mode": getattr(result_obj, "account_mode", ""),
        "window_rows": _count_signal_window_rows(signal_data),
        "raw_rows": len(getattr(result_obj, "raw_data", [])),
        "signals": int((signal_data["signal"] != 0).sum())
        if isinstance(signal_data, pd.DataFrame) and "signal" in signal_data.columns else 0,
        "trades": len(getattr(result_obj, "trades", [])),
        "final_equity": float(equity.iloc[-1]) if len(equity) else np.nan,
    }
    row.update({k: v for k, v in getattr(result_obj, "metrics", {}).items() if _is_scalar(v)})
    return row


def show_trade_summary_compact(
    trades: list[dict[str, Any]] | pd.DataFrame | None,
    *,
    title: str = "Trade summary",
    tail: int = 20,
) -> pd.DataFrame:
    """Hiển thị trade summary gọn cho output widget.

    Khác với `show_trade_explorer()`, hàm này chỉ hiển thị các lát cắt quan trọng
    nhất để output của dropdown không bị quá dài.
    """
    frame = trades_to_frame(trades)
    show_note(
        title,
        "Phần này trả lời: lệnh gần đây ra sao, PnL phân phối thế nào, lệnh thường thoát vì lý do gì, BUY hay SELL đóng góp tốt hơn.",
    )
    if frame.empty:
        print("Không có trade để hiển thị.")
        return frame

    _display_trade_descriptive_stats(frame)

    preferred = [
        "symbol", "entry_time", "exit_time", "direction", "entry", "exit",
        "pnl_usd", "r_multiple", "exit_reason", "partial_tp_hit",
        "commission", "swap_cost", "equity",
    ]
    cols = [c for c in preferred if c in frame.columns]
    display_frame = _rename_columns_for_display(frame[cols].tail(tail))
    show_note("Recent trades", f"{tail} lệnh gần nhất, đã đổi tên cột sang dạng dễ đọc hơn.")
    _display_obj(
        display_frame
        .style
        .format(_table_formatters(display_frame))
        .apply(_style_trade_row, axis=1)
        .hide(axis="index")
    )

    if "exit_reason" in frame.columns:
        by_exit = frame.groupby("exit_reason").size().sort_values(ascending=False).to_frame("Count")
        show_note("Exit reason breakdown", "Đếm lý do thoát lệnh. Nếu `SL` quá áp đảo, cần xem lại entry/SL hoặc điều kiện tín hiệu.")
        _display_obj(by_exit)
    if {"direction", "pnl_usd"}.issubset(frame.columns):
        by_dir = frame.groupby("direction")["pnl_usd"].agg(["count", "sum", "mean", "median"]).rename(
            columns={"count": "Trades", "sum": "Total PnL", "mean": "Avg PnL", "median": "Median PnL"}
        )
        show_note("PnL by side", "So sánh BUY và SELL để biết một chiều giao dịch có đang kéo kết quả xuống không.")
        _display_obj(by_dir.style.format(_table_formatters(by_dir)))
    return frame


def _display_symbol_config_details(
    symbol_config: Mapping[str, Any] | None,
    run_config: Mapping[str, Any] | None = None,
    *,
    title: str = "Cấu hình giao dịch từ dropdown",
) -> pd.DataFrame:
    """Hiển thị cấu hình quan trọng và công thức entry/SL/TP."""
    cfg = dict(symbol_config or {})
    run_config = run_config or {}
    x = cfg.get("x", run_config.get("x", "-"))
    ktp = cfg.get("ktp", run_config.get("ktp", "-"))
    ma_period = cfg.get("ma_period", "-")
    point_size = cfg.get("point_size", "-")
    contract_value = cfg.get("contract_value", "-")
    spread = cfg.get("spread_pts", "-")
    slippage = cfg.get("slippage_pts", "-")
    min_lot = cfg.get("min_lot_size", "-")
    max_lot = cfg.get("max_lot_size", "-")
    lot_step = cfg.get("lot_step", "-")

    rows = [
        ("x", _format_param_value(x), "Breakout buffer. BUY đặt entry cao hơn high thêm x; SELL đặt entry thấp hơn low trừ x."),
        ("ktp", _format_param_value(ktp), "Hệ số TP theo ATR. TP cách entry một khoảng ktp × ATR."),
        ("ma_period", _format_param_value(ma_period), "Chu kỳ MA dùng cho tín hiệu và trailing stop."),
        ("point_size", _format_param_value(point_size), "Quy đổi khoảng giá sang point để tính lot/PnL."),
        ("contract_value", _format_param_value(contract_value), "Giá trị mỗi point cho 1 lot."),
        ("spread_pts", _format_param_value(spread), "Spread broker, cộng vào chi phí khớp lệnh."),
        ("slippage_pts", _format_param_value(slippage), "Slippage nền; execution còn điều chỉnh động theo ATR/close."),
        ("lot range", f"{_format_param_value(min_lot)} -> {_format_param_value(max_lot)}, step {_format_param_value(lot_step)}", "Giới hạn lot hợp lệ khi position sizing."),
    ]
    frame = pd.DataFrame(rows, columns=["Tham số", "Giá trị", "Ý nghĩa"])

    if HTML is not None:
        _display_html(f"""
        <div style="border:1px solid #D1D5DB;border-radius:8px;background:#FFFFFF;padding:14px 16px;margin:12px 0;">
          <div style="font-size:19px;font-weight:800;color:#111827;margin-bottom:8px;">{_escape(title)}</div>
          <div style="font-size:13px;color:#374151;line-height:1.5;margin-bottom:10px;">
            Các giá trị dưới đây là phần quan trọng nhất quyết định cách engine đặt lệnh.
            Trong backtest thật, giá khớp còn bị điều chỉnh bởi spread và slippage.
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;font-size:13px;">
            <div style="padding:10px;border:1px solid #E5E7EB;border-radius:6px;background:#F9FAFB;">
              <b>BUY setup</b><br>
              Entry = bar high + x<br>
              Stop loss = bar low - x<br>
              Take profit = entry + ktp × ATR
            </div>
            <div style="padding:10px;border:1px solid #E5E7EB;border-radius:6px;background:#F9FAFB;">
              <b>SELL setup</b><br>
              Entry = bar low - x<br>
              Stop loss = bar high + x<br>
              Take profit = entry - ktp × ATR
            </div>
          </div>
        </div>
        """)
    else:
        print(title)
        print("BUY: entry = high + x, SL = low - x, TP = entry + ktp * ATR")
        print("SELL: entry = low - x, SL = high + x, TP = entry - ktp * ATR")

    _display_obj(
        frame.style
        .hide(axis="index")
        .set_properties(**{"text-align": "left", "white-space": "normal"})
        .set_table_styles([
            {"selector": "th", "props": [("text-align", "left")]},
            {"selector": "td:nth-child(2)", "props": [("font-family", "Consolas, monospace"), ("white-space", "nowrap")]},
        ])
    )
    return frame


def _display_symbol_parameter_overview(symbols: Mapping[str, Any], selected: list[str]) -> pd.DataFrame:
    """Hiển thị bảng x/ktp/lot/spread cho nhiều symbol trước khi compare."""
    rows = []
    for sym in selected:
        cfg = dict(symbols.get(sym, {}) or {})
        rows.append({
            "Symbol": sym,
            "x": cfg.get("x"),
            "ktp": cfg.get("ktp"),
            "ma_period": cfg.get("ma_period"),
            "spread_pts": cfg.get("spread_pts"),
            "slippage_pts": cfg.get("slippage_pts"),
            "lot_step": cfg.get("lot_step"),
            "contract_value": cfg.get("contract_value"),
        })
    frame = pd.DataFrame(rows)
    show_note(
        "Tham số chính theo từng symbol",
        "Khi compare nhiều symbol, mỗi symbol vẫn dùng `x`, `ktp`, spread, lot step riêng trong config hiện tại. Công thức chung: BUY entry = high + x, SL = low - x; SELL entry = low - x, SL = high + x.",
    )
    _display_obj(frame.style.format(_table_formatters(frame)).hide(axis="index"))
    return frame


def _display_metric_explanations(metrics: Mapping[str, Any] | None) -> None:
    """Hiển thị chú giải ý nghĩa KPI chính."""
    available = set((metrics or {}).keys())
    rows = [
        {"Metric": metric, "Ý nghĩa": meaning, "Cách đọc": note}
        for metric, meaning, note in METRIC_EXPLANATIONS
        if metric in available or metric in {"profit_factor", "max_drawdown", "sharpe"}
    ]
    if not rows:
        return
    frame = pd.DataFrame(rows)
    show_note(
        "Ý nghĩa các KPI chính",
        "Bảng này là chú giải nhanh để tránh đọc metric theo cảm tính. Không nên kết luận chỉ bằng một chỉ số đơn lẻ.",
    )
    _display_obj(frame.style.hide(axis="index"))


def _display_trade_descriptive_stats(frame: pd.DataFrame) -> pd.DataFrame:
    """Hiển thị thống kê mô tả của trade log."""
    stats: list[dict[str, Any]] = []
    if "pnl_usd" in frame.columns:
        pnl = pd.to_numeric(frame["pnl_usd"], errors="coerce").dropna()
        if not pnl.empty:
            stats.extend([
                {"Thống kê": "Tổng PnL", "Giá trị": pnl.sum(), "Ý nghĩa": "Tổng lợi nhuận ròng của toàn bộ trade log."},
                {"Thống kê": "PnL trung bình", "Giá trị": pnl.mean(), "Ý nghĩa": "Kỳ vọng trung bình mỗi lệnh theo USD."},
                {"Thống kê": "PnL trung vị", "Giá trị": pnl.median(), "Ý nghĩa": "Một lệnh điển hình lời/lỗ bao nhiêu, ít bị méo bởi outlier."},
                {"Thống kê": "Lệnh tốt nhất", "Giá trị": pnl.max(), "Ý nghĩa": "Trade lời lớn nhất."},
                {"Thống kê": "Lệnh xấu nhất", "Giá trị": pnl.min(), "Ý nghĩa": "Trade lỗ lớn nhất."},
                {"Thống kê": "Độ lệch chuẩn PnL", "Giá trị": pnl.std(), "Ý nghĩa": "PnL từng lệnh biến động mạnh hay nhẹ."},
            ])
    if "r_multiple" in frame.columns:
        r_values = pd.to_numeric(frame["r_multiple"], errors="coerce").dropna()
        if not r_values.empty:
            stats.append({"Thống kê": "Avg R", "Giá trị": r_values.mean(), "Ý nghĩa": "Lãi/lỗ trung bình theo đơn vị rủi ro ban đầu."})
    if "partial_tp_hit" in frame.columns and len(frame):
        stats.append({
            "Thống kê": "Partial TP rate",
            "Giá trị": float(frame["partial_tp_hit"].astype(bool).mean() * 100),
            "Ý nghĩa": "Tỷ lệ lệnh từng đi đủ xa để chốt một phần và dời SL.",
        })
    if not stats:
        return pd.DataFrame()
    stats_df = pd.DataFrame(stats)
    show_note("Thống kê mô tả trade log", "Phần này mô tả nhanh bản trade log đang nói gì trước khi đọc từng lệnh.")
    stats_display = stats_df.copy()
    stats_display["Giá trị"] = stats_display["Giá trị"].map(lambda v: _format_table_number(v, decimals=2))
    _display_obj(
        stats_display.style
        .hide(axis="index")
        .set_properties(**{"text-align": "left", "white-space": "normal"})
        .set_table_styles([{"selector": "th", "props": [("text-align", "left")]}])
    )
    return stats_df


def _display_result_header(title: str, subtitle: str, items: Mapping[str, Any]) -> None:
    """Hiển thị header báo cáo dạng card ngang."""
    if HTML is None:
        print(title)
        print(subtitle)
        print(items)
        return

    cards = "".join(
        f"""
        <div style="min-width:130px;padding:10px 12px;border:1px solid #E5E7EB;border-radius:6px;background:#FFFFFF;">
          <div style="font-size:11px;color:#6B7280;text-transform:uppercase;letter-spacing:.04em;">{_escape(str(k))}</div>
          <div style="font-size:18px;font-weight:700;color:#111827;margin-top:2px;">{_escape(_format_card_value(v))}</div>
        </div>
        """
        for k, v in items.items()
    )
    _display_html(f"""
    <div style="border:1px solid #D1D5DB;border-radius:8px;background:#F9FAFB;padding:14px 16px;margin:12px 0;">
      <div style="font-size:22px;font-weight:800;color:#111827;">{_escape(title)}</div>
      <div style="font-size:13px;color:#4B5563;margin:3px 0 12px 0;">{_escape(subtitle)}</div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;">{cards}</div>
    </div>
    """)


def _display_metric_cards(metrics: Mapping[str, Any] | None) -> None:
    """Hiển thị 6 KPI quan trọng nhất dạng card."""
    metrics = metrics or {}
    selected = [
        ("Trades", metrics.get("total_trades")),
        ("Win rate", metrics.get("win_rate"), "%"),
        ("Profit factor", metrics.get("profit_factor")),
        ("Return", metrics.get("total_return"), "%"),
        ("Max DD", metrics.get("max_drawdown"), "%"),
        ("Sharpe", metrics.get("sharpe")),
    ]
    if HTML is None:
        print(selected)
        return

    cards = []
    for label, value, *suffix in selected:
        suffix_txt = suffix[0] if suffix else ""
        color = _metric_card_color(label, value)
        cards.append(f"""
        <div style="flex:1;min-width:130px;padding:12px 14px;border-radius:7px;background:#FFFFFF;border:1px solid #E5E7EB;">
          <div style="font-size:12px;color:#6B7280;">{_escape(label)}</div>
          <div style="font-size:22px;font-weight:800;color:{color};">{_escape(_format_card_value(value, suffix_txt))}</div>
        </div>
        """)
    _display_html(f"""
    <div style="display:flex;gap:10px;flex-wrap:wrap;margin:10px 0 14px 0;">
      {''.join(cards)}
    </div>
    """)


def _display_compare_takeaways(compare_df: pd.DataFrame) -> None:
    """In ghi chú ngắn về symbol tốt/yếu nhất theo vài metric chính."""
    if compare_df.empty:
        return
    lines = []
    if "sharpe" in compare_df.columns and compare_df["sharpe"].notna().any():
        best = compare_df.loc[compare_df["sharpe"].idxmax()]
        lines.append(f"Sharpe tốt nhất: {best['symbol']} ({best['sharpe']}).")
    if "total_return" in compare_df.columns and compare_df["total_return"].notna().any():
        best = compare_df.loc[compare_df["total_return"].idxmax()]
        lines.append(f"Return tốt nhất: {best['symbol']} ({best['total_return']}%).")
    if "max_drawdown" in compare_df.columns and compare_df["max_drawdown"].notna().any():
        worst = compare_df.loc[compare_df["max_drawdown"].idxmin()]
        lines.append(f"Drawdown sâu nhất: {worst['symbol']} ({worst['max_drawdown']}%).")
    if lines:
        show_note("Nhận xét nhanh", " ".join(lines))


def _plot_symbol_compare_dashboard(compare_df: pd.DataFrame) -> None:
    """Vẽ chart so sánh nhiều symbol theo các KPI chính."""
    import matplotlib.pyplot as plt

    chart_cols = [c for c in ["total_return", "max_drawdown", "profit_factor", "sharpe"] if c in compare_df.columns]
    if not chart_cols:
        return
    fig, axes = plt.subplots(1, len(chart_cols), figsize=(5 * len(chart_cols), 4.2))
    if len(chart_cols) == 1:
        axes = [axes]
    for ax, col in zip(axes, chart_cols):
        view = compare_df[["symbol", col]].dropna().sort_values(col)
        color = "#FF6B6B" if col == "max_drawdown" else "#00D4FF"
        view.plot(x="symbol", y=col, kind="barh", ax=ax, legend=False, color=color, title=col)
        ax.grid(alpha=0.25)
    plt.tight_layout()
    _show_plot(fig)


def plot_mode_comparison(symbol_results: Mapping[str, Any], portfolio_results: Mapping[str, Any] | None = None):
    """Vẽ so sánh equity giữa các account mode."""
    import matplotlib.pyplot as plt

    nrows = 2 if portfolio_results else 1
    fig, axes = plt.subplots(nrows, 1, figsize=(16, 5 * nrows), sharex=False)
    if not isinstance(axes, np.ndarray):
        axes = np.array([axes])

    for mode, res in symbol_results.items():
        eq = _as_series(getattr(res, "equity", pd.Series(dtype=float)))
        if len(eq):
            eq.plot(ax=axes[0], lw=1.8, label=str(mode))
    axes[0].set_title("Symbol equity by account mode")
    axes[0].grid(alpha=0.25)
    axes[0].legend()

    if portfolio_results:
        for mode, res in portfolio_results.items():
            eq = _as_series(getattr(res, "combined_equity", pd.Series(dtype=float)))
            if len(eq):
                eq.plot(ax=axes[1], lw=1.8, label=str(mode))
        axes[1].set_title("Portfolio equity by account mode")
        axes[1].grid(alpha=0.25)
        axes[1].legend()

    plt.tight_layout()
    _show_plot(fig)
    return fig, axes


def export_result_bundle(
    name: str,
    *,
    metrics: Mapping[str, Any] | None = None,
    trades: list[dict[str, Any]] | pd.DataFrame | None = None,
    equity: pd.Series | pd.DataFrame | None = None,
    base_dir: str | Path = "reports/combo",
) -> Path:
    """Xuất metrics/trades/equity ra CSV để lưu lại một lần chạy.

    Hàm này không tự chạy trong notebook; bạn gọi ở cell cuối khi muốn lưu kết
    quả. Thư mục được tạo theo `base_dir/name`.
    """
    out_dir = Path(base_dir) / _safe_name(name)
    out_dir.mkdir(parents=True, exist_ok=True)

    if metrics:
        metrics_to_frame(metrics).to_csv(out_dir / "metrics.csv", index=False, encoding="utf-8-sig")
        monthly = metrics.get("monthly_pnl_table")
        if isinstance(monthly, pd.DataFrame) and not monthly.empty:
            monthly.to_csv(out_dir / "monthly_pnl.csv", encoding="utf-8-sig")

    trades_df = trades_to_frame(trades)
    if not trades_df.empty:
        trades_df.to_csv(out_dir / "trades.csv", index=False, encoding="utf-8-sig")

    if isinstance(equity, pd.Series):
        equity.rename("equity").to_csv(out_dir / "equity.csv", encoding="utf-8-sig")
    elif isinstance(equity, pd.DataFrame) and not equity.empty:
        equity.to_csv(out_dir / "equity.csv", encoding="utf-8-sig")

    print(f"Đã export kết quả vào: {out_dir}")
    return out_dir


def calc_drawdown(equity: pd.Series) -> pd.Series:
    """Tính drawdown phần trăm từ equity curve."""
    eq = _as_series(equity).dropna()
    if eq.empty:
        return pd.Series(dtype=float)
    peak = eq.cummax()
    return (eq / peak - 1.0) * 100.0


def _is_scalar(value: Any) -> bool:
    return not isinstance(value, (pd.DataFrame, pd.Series, dict, list, tuple, set))


def _escape(value: Any) -> str:
    """Escape HTML để text tiếng Việt hiển thị an toàn trong HTML snippets."""
    return html.escape(str(value), quote=True)


def _short_repr(value: Any) -> str:
    if isinstance(value, dict):
        return "{}" if not value else str(value)
    if isinstance(value, list):
        return ", ".join(map(str, value))
    if isinstance(value, (float, int, np.floating, np.integer)):
        return _format_table_number(value, decimals=2)
    return str(value)


def _format_card_value(value: Any, suffix: str = "") -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "-"
    if isinstance(value, (int, float, np.integer, np.floating)):
        v = float(value)
        if abs(v) >= 1000:
            text = f"{v:,.2f}"
        elif abs(v) >= 100:
            text = f"{v:,.1f}"
        else:
            text = f"{v:,.2f}"
        return f"{text}{suffix}"
    return str(value)


def _format_table_number(value: Any, *, decimals: int = 2, strip: bool = True) -> str:
    if value is None:
        return "-"
    if isinstance(value, (bool, np.bool_)):
        return str(bool(value))
    if not isinstance(value, (int, float, np.integer, np.floating)):
        return str(value)
    v = float(value)
    if np.isnan(v) or np.isinf(v):
        return "-"
    if abs(v - round(v)) < 1e-10:
        return f"{int(round(v)):,}"
    text = f"{v:,.{decimals}f}"
    if strip:
        text = text.rstrip("0").rstrip(".")
    return text


def _format_param_value(value: Any) -> str:
    return _format_table_number(value, decimals=4, strip=True)


def _metric_decimal_places(metric: str) -> int:
    metric_l = metric.lower()
    if metric_l in {"total_trades", "wins", "losses", "trades", "signals", "window_rows", "raw_rows"}:
        return 0
    if metric_l in {"profit_factor", "sharpe", "sortino", "avg_r", "r_multiple", "partial_tp_rate"}:
        return 2
    if metric_l in {"win_rate", "total_return", "annual_return", "max_drawdown"}:
        return 2
    if "pnl" in metric_l or "equity" in metric_l or "commission" in metric_l or "swap" in metric_l:
        return 2
    return 2


def _format_metric_value_by_name(metric: str, value: Any) -> str:
    if not isinstance(value, (float, int, np.floating, np.integer)):
        return str(value)
    return _format_table_number(value, decimals=_metric_decimal_places(metric), strip=False)


def _metric_card_color(label: str, value: Any) -> str:
    if not isinstance(value, (int, float, np.integer, np.floating)):
        return "#111827"
    v = float(value)
    if label in {"Profit factor", "Sharpe"}:
        return "#16A34A" if v >= 1 else "#DC2626"
    if label == "Return":
        return "#16A34A" if v > 0 else "#DC2626"
    if label == "Max DD":
        return "#16A34A" if v >= -10 else "#DC2626"
    return "#111827"


def _format_metric_value(value: Any) -> str:
    if isinstance(value, (float, int, np.floating, np.integer)):
        return _format_table_number(value, decimals=2, strip=False)
    return str(value)


def _style_metric_row(row: pd.Series) -> list[str]:
    metric = str(row.get("metric", ""))
    value = row.get("value")
    style = ""
    v = _coerce_float(value)
    if v is not None:
        if metric in GOOD_HIGH:
            style = "color:#6BCB77;font-weight:bold" if v >= GOOD_HIGH[metric] else "color:#FF6B6B"
        elif metric in GOOD_LOW:
            style = "color:#6BCB77;font-weight:bold" if v >= GOOD_LOW[metric] else "color:#FF6B6B"
        elif metric in {"total_pnl", "avg_win", "total_return", "ret"}:
            style = "color:#6BCB77;font-weight:bold" if v > 0 else "color:#FF6B6B"
    return ["", style]


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, (int, float, np.integer, np.floating)):
        v = float(value)
        return None if np.isnan(v) or np.isinf(v) else v
    try:
        cleaned = str(value).replace(",", "").replace("%", "").strip()
        if not cleaned or cleaned == "-":
            return None
        return float(cleaned)
    except Exception:
        return None


def _style_trade_row(row: pd.Series) -> list[str]:
    pnl = row.get("pnl_usd", row.get("PnL USD"))
    if isinstance(pnl, (int, float, np.integer, np.floating)):
        if pnl > 0:
            return ["background-color:#ECFDF5;color:#065F46"] * len(row)
        if pnl < 0:
            return ["background-color:#FEF2F2;color:#991B1B"] * len(row)
    return [""] * len(row)


def _style_symbol_compare_row(row: pd.Series) -> list[str]:
    ret = row.get("total_return", row.get("Total Return"))
    if isinstance(ret, (int, float, np.integer, np.floating)):
        if ret > 0:
            return ["background-color:#F0FDF4;color:#14532D"] * len(row)
        if ret < 0:
            return ["background-color:#FEF2F2;color:#7F1D1D"] * len(row)
    return [""] * len(row)


def _rename_columns_for_display(frame: pd.DataFrame) -> pd.DataFrame:
    """Đổi tên cột kỹ thuật sang nhãn dễ đọc khi hiển thị."""
    return frame.rename(columns={k: v for k, v in TRADE_COLUMN_LABELS.items() if k in frame.columns})


def _style_pnl_cell(value: Any) -> str:
    if isinstance(value, (int, float, np.floating, np.integer)):
        if value > 0:
            return "color:#6BCB77;font-weight:bold"
        if value < 0:
            return "color:#FF6B6B;font-weight:bold"
    return ""


def _style_dataframe_cells(styler: Any, func: Any) -> Any:
    """Áp style cho từng ô DataFrame, tương thích nhiều phiên bản pandas.

    Pandas mới dùng `Styler.map()`, trong khi pandas cũ dùng
    `Styler.applymap()`. Notebook của bạn đang chạy phiên bản không còn
    `applymap()`, nên helper này chọn API phù hợp tại runtime.
    """
    if hasattr(styler, "map"):
        return styler.map(func)
    return styler.applymap(func)


def _table_formatters(frame: pd.DataFrame) -> dict[str, Any]:
    fmts: dict[str, Any] = {}
    for col in frame.columns:
        col_l = str(col).lower()
        if col in MONEY_COLS or "pnl" in col_l or "equity" in col_l or "commission" in col_l or "swap" in col_l:
            fmts[col] = lambda v: _format_table_number(v, decimals=2, strip=False)
        elif col in PCT_COLS or "return" in col_l or "drawdown" in col_l or "rate" in col_l:
            fmts[col] = lambda v: _format_table_number(v, decimals=2, strip=False)
        elif col_l in {"trades", "signals", "wins", "losses", "count", "window rows", "loaded rows", "raw_rows", "window_rows"}:
            fmts[col] = lambda v: _format_table_number(v, decimals=0)
        elif col_l in {"profit factor", "profit_factor", "sharpe", "sortino", "r multiple", "r_multiple", "avg r", "avg_r"}:
            fmts[col] = lambda v: _format_table_number(v, decimals=2, strip=False)
        elif col_l in {"x", "ktp", "point_size", "contract_value", "spread_pts", "slippage_pts", "lot_step"}:
            fmts[col] = lambda v: _format_param_value(v)
        elif pd.api.types.is_float_dtype(frame[col]):
            fmts[col] = lambda v: _format_table_number(v, decimals=2)
    return fmts


def _as_series(value: Any) -> pd.Series:
    if isinstance(value, pd.Series):
        return value.sort_index()
    if value is None:
        return pd.Series(dtype=float)
    return pd.Series(value).sort_index()


def _time_index(df: pd.DataFrame) -> pd.Index:
    if isinstance(df.index, pd.DatetimeIndex):
        return df.index
    if "BarTime" in df.columns:
        return pd.to_datetime(df["BarTime"], errors="coerce")
    return df.index


def _first_existing(frame: pd.DataFrame, columns: list[str]) -> str:
    for col in columns:
        if col in frame.columns:
            return col
    return frame.columns[-1]


def _plot_pivot_heatmap(
    frame: pd.DataFrame,
    ax: Any,
    *,
    index: str,
    columns: str,
    values: str,
    title: str,
) -> None:
    if not {index, columns, values}.issubset(frame.columns):
        ax.set_title(f"Thiếu cột cho heatmap: {index}, {columns}, {values}")
        return
    pivot = frame.pivot_table(index=index, columns=columns, values=values, aggfunc="mean")
    im = ax.imshow(pivot.values, aspect="auto", cmap="viridis")
    ax.set_title(title)
    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels([str(x) for x in pivot.columns], rotation=45, ha="right")
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([str(x) for x in pivot.index])
    for y in range(pivot.shape[0]):
        for x in range(pivot.shape[1]):
            val = pivot.iat[y, x]
            if pd.notna(val):
                ax.text(x, y, f"{val:.2f}", ha="center", va="center", color="white", fontsize=8)
    ax.figure.colorbar(im, ax=ax, fraction=0.046, pad=0.04)


def _bar_if_exists(frame: pd.DataFrame, x: str | None, y: str, ax: Any, title: str, color: str) -> None:
    if y not in frame.columns:
        ax.set_title(f"Thiếu cột {y}")
        return
    frame.plot(x=x, y=y, kind="bar", ax=ax, color=color, legend=False, title=title)


def _safe_name(name: str) -> str:
    keep = [ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(name)]
    return "".join(keep).strip("_") or "run"
