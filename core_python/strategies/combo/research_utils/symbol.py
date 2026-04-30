"""Symbol-level interactive reports for Combo research notebooks."""

from __future__ import annotations

from ._shared import *  # noqa: F401,F403
from .dashboard import (
    show_note,
    show_run_config,
    show_kpi_dashboard,
    show_monthly_pnl,
    plot_equity_dashboard,
    show_trade_explorer,
    trades_to_frame,
    compare_metrics_frame,
)

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
        Dict symbol from the canonical Combo parameters module
        (`strategies.combo.config.SYMBOLS`).

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



__all__ = [
    "build_symbol_backtest_widget",
    "render_symbol_backtest_report",
    "render_symbol_comparison",
    "_count_signal_window_rows",
    "symbol_result_row",
    "show_trade_summary_compact",
    "plot_mode_comparison",
]
