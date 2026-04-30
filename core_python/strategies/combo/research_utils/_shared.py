"""Shared display primitives and formatting helpers for Combo research utilities."""

from __future__ import annotations

import html
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping, MutableMapping

import numpy as np
import pandas as pd

from core_python.strategies.combo.symbol.selection import (
    OPTIMIZER_PARAM_COLS,
    annotate_optimizer_plateau,
    filter_optimizer_candidates,
    rank_optimizer_candidates,
    summarize_candidate_validation,
    summarize_optimizer_plateau,
    validate_symbol_candidates,
)

try:
    from IPython.display import HTML, Markdown, display
except Exception:  # pragma: no cover
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

__all__ = [name for name in globals() if not name.startswith("__")]
