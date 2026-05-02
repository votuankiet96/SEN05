"""Post-backtest analytics: metrics, portfolio aggregation, and robustness checks."""

from __future__ import annotations

# =============================================================================
# strategies/shared/metrics.py  —  Tính toán và báo cáo kết quả backtest
# =============================================================================
"""Tính bộ chỉ số hiệu suất và in báo cáo từ kết quả backtest.

Các hàm chính:
- _bars_per_year(): helper nội bộ, dùng để annualize Sharpe/Sortino.
- calc_metrics()  : tính toàn bộ KPI từ trade log + equity curve.
- in_bao_cao()    : in metrics dạng bảng ASCII ra console.

Module này không phụ thuộc vào bất kỳ strategy cụ thể nào.
"""
import numpy as np
import pandas as pd

from core_python.shared.timeframes import bars_per_year


# ─────────────────────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _bars_per_year(tf_code: str) -> int:
    """Map timeframe code -> số bar xấp xỉ mỗi năm."""
    return bars_per_year(tf_code)


TRADE_LEVEL_SHARPE_METHOD = "trade_level_fixed_initial_equity"


def _timestamp_for_metric_window(value, index: pd.Index) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if isinstance(index, pd.DatetimeIndex):
        if index.tz is not None and ts.tzinfo is None:
            ts = ts.tz_localize(index.tz)
        elif index.tz is None and ts.tzinfo is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        elif index.tz is not None and ts.tzinfo is not None:
            ts = ts.tz_convert(index.tz)
    return ts


def _metric_window_bounds(
    eq_ts: pd.Series,
    window_start=None,
    window_end=None,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    eq_start = pd.Timestamp(eq_ts.index[0])
    eq_end = pd.Timestamp(eq_ts.index[-1])
    start = (
        _timestamp_for_metric_window(window_start, eq_ts.index)
        if window_start is not None else eq_start
    )
    end = (
        _timestamp_for_metric_window(window_end, eq_ts.index)
        if window_end is not None else eq_end
    )
    if start > eq_end or end < eq_start:
        raise ValueError(
            "Metric window does not overlap equity series: "
            f"window_start={start}, window_end={end}, equity_start={eq_start}, equity_end={eq_end}"
        )
    return start, end


def calc_trade_level_return_stats(
    trade_pnls,
    *,
    initial_equity: float,
    window_start,
    window_end,
    min_trades: int = 10,
) -> dict:
    pnl = pd.Series(trade_pnls, dtype=float).dropna()
    equity_base = float(initial_equity) if float(initial_equity) != 0 else 1.0
    years_span = max((pd.Timestamp(window_end) - pd.Timestamp(window_start)).days / 365.25, 0.01)
    trade_rets = pnl / equity_base
    n_trades = len(trade_rets)
    n_per_year = n_trades / years_span

    if n_trades < min_trades or trade_rets.std() == 0:
        sharpe = 0.0
    else:
        sharpe = float(trade_rets.mean() / trade_rets.std() * np.sqrt(max(n_per_year, 1)))

    neg_rets = trade_rets[trade_rets < 0]
    if n_trades < min_trades or len(neg_rets) < 2 or neg_rets.std() == 0:
        sortino = 0.0
    else:
        sortino = float(trade_rets.mean() / neg_rets.std() * np.sqrt(max(n_per_year, 1)))

    return {
        "sharpe": sharpe,
        "sortino": sortino,
        "sharpe_method": TRADE_LEVEL_SHARPE_METHOD,
        "sharpe_years_span": years_span,
    }


# ─────────────────────────────────────────────────────────────────────────────
# METRICS CALCULATION
# ─────────────────────────────────────────────────────────────────────────────

def calc_metrics(
    trades: list,
    eq_ts: pd.Series,
    tf_code: str = 'H4',
    *,
    window_start=None,
    window_end=None,
    initial_equity: float | None = None,
) -> dict:
    """
    Tính bộ chỉ số hiệu suất từ trade log và equity curve.

    Đây là lớp tổng kết cuối cùng để trình bày kết quả chiến lược.
    Nếu đổi công thức trong hàm này, dashboard và báo cáo sẽ đổi theo,
    dù phần mô phỏng lệnh giữ nguyên.

    Parameters
    ----------
    trades : danh sách lệnh từ backtest_symbol().
    eq_ts  : chuỗi equity theo thời gian.
    tf_code: timeframe code để annualize Sharpe/Sortino.

    Returns
    -------
    dict gồm toàn bộ metric:
    - sortino
    - recovery_factor
    - max_drawdown_usd
    - monthly_pnl_table (DataFrame pivot year x month)
    """
    if not trades:
        return {}

    df     = pd.DataFrame(trades)
    w_df   = df[df['pnl_usd'] > 0]
    l_df   = df[df['pnl_usd'] <= 0]
    gp     = w_df['pnl_usd'].sum() if len(w_df) else 0
    gl     = l_df['pnl_usd'].abs().sum() if len(l_df) else 0

    peak   = eq_ts.cummax()
    dd_pct_ts = (eq_ts - peak) / peak
    dd_usd_ts = eq_ts - peak

    total_ret  = (eq_ts.iloc[-1] / eq_ts.iloc[0] - 1) * 100
    metric_start, metric_end = _metric_window_bounds(eq_ts, window_start, window_end)
    years      = (metric_end - metric_start).days / 365.25
    annual_ret = ((eq_ts.iloc[-1] / eq_ts.iloc[0]) ** (1 / max(years, 0.01)) - 1) * 100

    # Sharpe/Sortino tính trên trade-level returns để tránh inflate bởi các bar equity phẳng.
    eq0 = float(initial_equity if initial_equity is not None else eq_ts.iloc[0])
    trade_stats = calc_trade_level_return_stats(
        df['pnl_usd'],
        initial_equity=eq0,
        window_start=metric_start,
        window_end=metric_end,
    )
    sharpe = trade_stats["sharpe"]
    sortino = trade_stats["sortino"]
    max_dd     = dd_pct_ts.min() * 100
    max_dd_usd = dd_usd_ts.min()
    calmar     = annual_ret / abs(max_dd) if max_dd else 0

    avg_win  = w_df['pnl_usd'].mean() if len(w_df) else 0
    avg_loss = l_df['pnl_usd'].mean() if len(l_df) else 0
    total_swap_cost = float(df['swap_cost'].sum()) if 'swap_cost' in df.columns else 0.0

    recovery_factor = (df['pnl_usd'].sum() / abs(max_dd_usd)
                       if max_dd_usd != 0 else 0)

    monthly_pnl_table = pd.DataFrame()
    if 'exit_time' in df.columns:
        _m = df.copy()
        _m['exit_time'] = pd.to_datetime(_m['exit_time'])
        _m['year'] = _m['exit_time'].dt.year
        _m['month'] = _m['exit_time'].dt.month
        monthly_pnl_table = (
            _m.pivot_table(index='year', columns='month', values='pnl_usd',
                           aggfunc='sum', fill_value=0.0)
            .sort_index()
        )

    # Thống kê partial TP, có tương thích ngược với log cũ chưa có cột này.
    partial_count = int(df['partial_tp_hit'].sum()) \
        if 'partial_tp_hit' in df.columns else 0

    return dict(
        total_trades=len(df),
        equity_model=eq_ts.attrs.get("equity_model", "unspecified"),
        wins=len(w_df), losses=len(l_df),
        win_rate=round(len(w_df) / len(df) * 100, 1),
        profit_factor=round(gp / gl if gl else float('inf'), 2),
        total_pnl=round(df['pnl_usd'].sum(), 2),
        total_return=round(total_ret, 2),
        annual_return=round(annual_ret, 2),
        max_drawdown=round(max_dd, 2),
        max_drawdown_usd=round(float(max_dd_usd), 2),
        sharpe=round(sharpe, 2),
        sharpe_method=trade_stats["sharpe_method"],
        sharpe_years_span=round(trade_stats["sharpe_years_span"], 4),
        sortino=round(sortino, 2),
        calmar=round(calmar, 2),
        recovery_factor=round(recovery_factor, 2),
        avg_win=round(avg_win, 2),
        avg_loss=round(avg_loss, 2),
        total_swap_cost=round(total_swap_cost, 2),
        avg_rr=round(abs(avg_win / avg_loss) if avg_loss else 0, 2),
        avg_r=round(df['r_multiple'].mean(), 3),
        partial_tp_trades=partial_count,
        partial_tp_rate=round(partial_count / len(df) * 100, 1),
        monthly_pnl_table=monthly_pnl_table,
    )


# ─────────────────────────────────────────────────────────────────────────────
# ASCII REPORT PRINTER
# ─────────────────────────────────────────────────────────────────────────────

def in_bao_cao(metrics: dict) -> None:
    """In toàn bộ metrics dạng bảng ASCII thuần ra console."""
    if not metrics:
        print('+----------------------+----------------------+')
        print('| Metric               | Value                |')
        print('+----------------------+----------------------+')
        print('| (empty)              | no metrics           |')
        print('+----------------------+----------------------+')
        return

    rows = []
    for k, v in metrics.items():
        if k == 'monthly_pnl_table':
            continue
        if isinstance(v, float):
            rows.append((k, f'{v:.4f}'))
        else:
            rows.append((k, str(v)))

    key_w = max(len('Metric'), max((len(k) for k, _ in rows), default=0))
    val_w = max(len('Value'), max((len(v) for _, v in rows), default=0))

    line = '+' + '-' * (key_w + 2) + '+' + '-' * (val_w + 2) + '+'
    print(line)
    print(f"| {'Metric'.ljust(key_w)} | {'Value'.ljust(val_w)} |")
    print(line)
    for k, v in rows:
        print(f"| {k.ljust(key_w)} | {v.ljust(val_w)} |")
    print(line)

    monthly = metrics.get('monthly_pnl_table')
    if isinstance(monthly, pd.DataFrame) and not monthly.empty:
        print('\nMonthly PnL (year x month)')
        print(monthly.to_string())


# -----------------------------------------------------------------------------
# Portfolio analytics
# -----------------------------------------------------------------------------

"""Portfolio helpers shared across strategies.

=======================================================================
FILE NÀY LÀM GÌ?
=======================================================================

File này gom kết quả backtest của nhiều symbol riêng lẻ lại thành
một bức tranh tổng thể của cả portfolio.

Hãy hình dung: bạn đã chạy backtest riêng cho 37 symbol (US30, EURUSD,
GOLD...). Mỗi symbol có đường vốn riêng, danh sách lệnh riêng. File này
là "người gom hàng" — ghép tất cả lại, tính chỉ số tổng, kiểm tra xem
toàn bộ tài khoản có vi phạm quy tắc FTMO không.

=======================================================================
TẠI SAO CẦN FILE RIÊNG CHO PORTFOLIO?
=======================================================================

Các hàm backtest từng symbol không biết gì về nhau. Chúng không biết:
  - Vốn tổng cả portfolio đang ở đâu
  - Hôm nay cả portfolio đã lỗ bao nhiêu %
  - Drawdown tổng tài khoản là bao nhiêu (không phải từng symbol)

FTMO và các prop firm chấm điểm theo TÀI KHOẢN TỔNG, không phải từng
symbol. Nên phải gom lại mới kiểm tra được.

=======================================================================
VỊ TRÍ TRONG HỆ THỐNG
=======================================================================

  Đầu vào  →  Kết quả backtest từng symbol (trades + equity mỗi symbol)
  Đầu ra   →  Equity tổng portfolio, metrics tổng, kiểm tra FTMO
  Dùng bởi →  strategies.combo.portfolio.backtest
               strategies.combo.portfolio.walkforward
               Bất kỳ layer nào cần nhìn đa symbol
"""

from typing import Any

import numpy as np
import pandas as pd



def equity_frame_from_dict(
    equity_by_symbol: dict[str, pd.Series],
    *,
    fill_value: float | dict[str, float] | None = None,
) -> pd.DataFrame:
    """Căn chỉnh đường vốn của nhiều symbol về cùng một bảng thời gian.

    =======================================================================
    VẤN ĐỀ CẦN GIẢI QUYẾT
    =======================================================================

    Mỗi symbol có đường vốn riêng, và chúng KHÔNG đồng bộ nhau:
      - US30 có lệnh vào lúc 08:00, EURUSD có lệnh vào lúc 14:00
      - Những khoảng không có lệnh → equity Series có lỗ hổng (NaN)

    Muốn tính equity tổng portfolio (cộng tất cả lại theo từng mốc giờ),
    cần điền vào chỗ trống trước — không thể cộng NaN.

    =======================================================================
    CÁCH XỬ LÝ Ô TRỐNG (fill_value)
    =======================================================================

    fill_value=None (mặc định)
        Dùng forward-fill: ô trống = giá trị của mốc thời gian trước đó.
        Ý nghĩa: "vốn symbol này không thay đổi từ lệnh cuối đến giờ".
        Thích hợp khi các symbol đã có ít nhất một lệnh từ đầu.

    fill_value=100_000.0 (một số)
        Điền NaN bằng số đó TRƯỚC KHI có lệnh đầu tiên (các khoảng trống
        mà forward-fill chưa lấp được).
        Ý nghĩa: "trước khi vào lệnh, vốn symbol này là X".
        Quan trọng để tính equity portfolio ngay từ đầu, không bị NaN.

    fill_value={"US30": 50000, "EURUSD": 30000} (dict)
        Mỗi symbol có vốn khởi đầu khác nhau — dùng khi phân bổ vốn
        không đều giữa các symbol.

    =======================================================================
    PARAMETERS
    =======================================================================

    equity_by_symbol
        Dict ánh xạ symbol_key → pd.Series (đường vốn theo thời gian).
        Ví dụ: {"US30": Series(...), "EURUSD": Series(...)}

    Returns
    -------
    pd.DataFrame
        Mỗi cột là một symbol, mỗi hàng là một mốc thời gian.
        Đã điền đầy đủ, không còn NaN (trừ khi fill_value=None và symbol
        chưa có lệnh nào từ đầu).
    """
    if not equity_by_symbol:
        return pd.DataFrame()

    # Ghép tất cả Series vào DataFrame rồi sắp xếp theo thời gian.
    frame = pd.DataFrame(equity_by_symbol).sort_index()

    if fill_value is None:
        # Forward-fill: mỗi ô trống lấy giá trị của hàng trước đó.
        return frame.ffill()

    if isinstance(fill_value, dict):
        # Forward-fill trước, sau đó điền phần đầu (trước lệnh đầu tiên)
        # bằng vốn khởi đầu riêng của từng symbol.
        for symbol, seed in fill_value.items():
            if symbol in frame.columns:
                frame[symbol] = frame[symbol].ffill().fillna(seed)
        return frame

    # Forward-fill trước, sau đó điền phần đầu bằng cùng một giá trị.
    return frame.ffill().fillna(fill_value)


def build_combined_equity(
    equity_by_symbol: dict[str, pd.Series],
    *,
    fill_value: float | dict[str, float] | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """Tính đường vốn TỔNG HỢP của cả portfolio.

    =======================================================================
    MỤC ĐÍCH
    =======================================================================

    Từ đường vốn riêng của từng symbol, hàm này tạo ra hai thứ:
      1. Bảng vốn chi tiết (equity_frame): mỗi cột = một symbol
      2. Đường vốn tổng (combined_equity): cộng tất cả cột lại

    Đường vốn tổng là thứ quan trọng nhất để đánh giá portfolio — nó
    phản ánh tổng tài khoản đang ở đâu tại mọi thời điểm.

    =======================================================================
    PARAMETERS
    =======================================================================

    equity_by_symbol
        Dict ánh xạ symbol_key → pd.Series đường vốn từng symbol.

    fill_value
        Xem giải thích đầy đủ trong equity_frame_from_dict().

    Returns
    -------
    (equity_frame, combined_equity)
        equity_frame    : pd.DataFrame — bảng vốn chi tiết từng symbol.
        combined_equity : pd.Series   — tổng vốn portfolio theo thời gian.
    """
    frame = equity_frame_from_dict(equity_by_symbol, fill_value=fill_value)
    if frame.empty:
        return frame, pd.Series(dtype=float)

    # Cộng theo hàng (axis=1) → tổng vốn tại mỗi mốc thời gian.
    return frame, frame.sum(axis=1)


def combine_trade_logs(
    trades_by_symbol: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Gom danh sách lệnh của nhiều symbol thành một danh sách duy nhất.

    =======================================================================
    MỤC ĐÍCH
    =======================================================================

    Sau khi chạy backtest nhiều symbol, mỗi symbol có danh sách lệnh riêng.
    Hàm này gộp tất cả lại và sắp xếp theo thời gian vào lệnh.

    Kết quả dùng để:
      - Chạy Monte Carlo trên toàn bộ lịch sử giao dịch portfolio
      - Tính metrics tổng hợp (profit factor, win rate chung...)
      - In báo cáo lệnh thống nhất

    =======================================================================
    PARAMETERS
    =======================================================================

    trades_by_symbol
        Dict ánh xạ symbol_key → list lệnh.
        Mỗi lệnh là một dict chứa: entry_time, exit_time, entry, exit,
        pnl, exit_reason, symbol...

    Returns
    -------
    list[dict]
        Tất cả lệnh gộp lại, sắp xếp tăng dần theo entry_time.
        Nếu hai lệnh cùng entry_time (lệnh của hai symbol vào cùng bar),
        sắp xếp thêm theo tên symbol để thứ tự ổn định và tái lập được.
    """
    combined: list[dict[str, Any]] = []
    for trades in trades_by_symbol.values():
        combined.extend(trades)

    if not combined:
        return combined

    return sorted(
        combined,
        key=lambda row: (
            # Lệnh không có entry_time (bất thường) → đẩy xuống cuối danh sách.
            pd.Timestamp(row.get("entry_time")) if row.get("entry_time") is not None
            else pd.Timestamp.min,
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
    """Tính bộ chỉ số hiệu suất cho cả PORTFOLIO (không phải từng symbol).

    =======================================================================
    MỤC ĐÍCH
    =======================================================================

    Sau khi có đường vốn tổng và danh sách lệnh tổng, hàm này tính
    các chỉ số hiệu suất: Sharpe, Sortino, max drawdown, win rate,
    profit factor... — nhưng ở cấp độ TOÀN PORTFOLIO.

    Điểm quan trọng: dùng lại chính hàm calc_metrics() đã dùng cho
    từng symbol đơn lẻ. Nghĩa là cách tính Sharpe, drawdown... hoàn
    toàn nhất quán giữa báo cáo symbol và báo cáo portfolio.

    =======================================================================
    PARAMETERS
    =======================================================================

    trades_by_symbol
        Dict lệnh theo từng symbol — sẽ được gộp trước khi tính.

    equity_by_symbol
        Dict đường vốn theo từng symbol — sẽ được cộng lại thành tổng.

    tf_code
        Mã timeframe ("H4", "M30"...) — dùng để tính số lệnh/năm cho
        annualization Sharpe đúng. Mặc định "H4".

    fill_value
        Xem giải thích đầy đủ trong equity_frame_from_dict().

    Returns
    -------
    dict
        Bộ KPI tổng portfolio. Trả về dict rỗng nếu không có dữ liệu.
    """
    _, combined_equity = build_combined_equity(equity_by_symbol, fill_value=fill_value)
    if combined_equity.empty:
        return {}

    all_trades = combine_trade_logs(trades_by_symbol)

    # Tái sử dụng calc_metrics() để đảm bảo cùng công thức tính với báo cáo
    # từng symbol — không phát sinh sự khác biệt khó giải thích.
    return calc_metrics(all_trades, combined_equity, tf_code=tf_code)


def check_portfolio_ftmo(
    combined_equity: pd.Series,
    initial_balance: float,
    *,
    daily_loss_limit: float = 0.05,
    max_dd_limit: float = 0.10,
    max_dd_mode: str = "fixed_initial",
) -> dict[str, Any]:
    """Kiểm tra xem cả portfolio có vi phạm quy tắc FTMO không.

    =======================================================================
    BỐI CẢNH — FTMO LÀ GÌ?
    =======================================================================

    FTMO là prop firm (công ty cấp vốn giao dịch). Họ đặt ra hai giới
    hạn cứng — vi phạm một trong hai là mất tài khoản ngay lập tức:

      Quy tắc 1 — Daily Loss Limit (5%):
        Tổng lỗ trong một ngày ≤ 5% vốn ban đầu.
        Ví dụ: vốn 100.000$ → không được lỗ quá 5.000$ trong một ngày.

      Quy tắc 2 — Max Drawdown (10%):
        Tổng tài khoản không được xuống quá 10% so với mốc tính.
        Ví dụ: vốn 100.000$ → tài khoản không được xuống dưới 90.000$.

    =======================================================================
    TẠI SAO PHẢI KIỂM TRA Ở CẤP PORTFOLIO?
    =======================================================================

    Execution engine kiểm soát từng symbol riêng lẻ. Nó không thể biết
    được tổng tài khoản đang ở đâu — nó chỉ thấy "sleeve" của mình.

    Ví dụ: US30 lỗ 3%, EURUSD lỗ 3% trong cùng một ngày → từng symbol
    đều dưới 5%, nhưng TÀI KHOẢN TỔNG lỗ 6% → vi phạm FTMO.
    Hàm này phát hiện trường hợp đó.

    =======================================================================
    HAI CHẾ ĐỘ TÍNH MAX DRAWDOWN (max_dd_mode)
    =======================================================================

    "fixed_initial" (mặc định, dùng cho FTMO 2-Step):
        Mốc tham chiếu cố định = vốn ban đầu.
        Drawdown = vốn ban đầu - equity hiện tại (nếu đang âm).
        Vi phạm khi: equity < initial_balance × (1 - max_dd_limit).
        Ví dụ: vốn 100.000$ → vi phạm khi equity < 90.000$.

    "trailing_peak" (dùng cho một số broker/rule khác):
        Mốc tham chiếu = đỉnh equity cao nhất từ trước đến nay.
        Drawdown = (đỉnh - hiện tại) / đỉnh.
        Nghiêm ngặt hơn vì đỉnh tăng theo thời gian — càng thắng nhiều,
        ngưỡng drawdown cho phép càng cao hơn.

    =======================================================================
    PARAMETERS
    =======================================================================

    combined_equity
        Đường vốn tổng hợp toàn portfolio (từ build_combined_equity()).

    initial_balance
        Vốn ban đầu tổng portfolio — không phải vốn từng symbol.

    daily_loss_limit
        Giới hạn lỗ ngày tính theo tỷ lệ. Mặc định 0.05 = 5%.

    max_dd_limit
        Giới hạn drawdown tối đa tính theo tỷ lệ. Mặc định 0.10 = 10%.

    max_dd_mode
        Cách tính drawdown: "fixed_initial" hoặc "trailing_peak".

    =======================================================================
    RETURNS
    =======================================================================

    Dict gồm các key:

    ftmo_pass
        True nếu KHÔNG vi phạm cả hai quy tắc. False nếu vi phạm ít nhất một.

    breach_reason
        "daily_loss"  → chỉ vi phạm giới hạn lỗ ngày
        "max_dd"      → chỉ vi phạm max drawdown
        "both"        → vi phạm cả hai
        None          → không vi phạm gì

    breach_date
        Ngày vi phạm daily loss đầu tiên (dạng chuỗi "YYYY-MM-DD").
        None nếu không vi phạm daily loss.

    max_daily_loss_pct
        Lỗ ngày lớn nhất đã xảy ra, tính bằng % vốn ban đầu.
        Số dương = lỗ. Ví dụ: 4.8 = lỗ 4.8% trong ngày tệ nhất.

    n_daily_breach_days
        Số ngày đã vượt ngưỡng daily loss trong toàn bộ backtest.

    max_dd_pct
        Max drawdown của cả portfolio, tính bằng % (số dương = lỗ).

    max_dd_usd
        Max drawdown tuyệt đối tính bằng đô la.
    """
    if combined_equity.empty or initial_balance <= 0:
        return {
            "ftmo_pass":           None,
            "breach_reason":       "no_data",
            "breach_date":         None,
            "max_daily_loss_pct":  0.0,
            "n_daily_breach_days": 0,
            "max_dd_pct":          0.0,
            "max_dd_usd":          0.0,
        }

    daily_limit_usd = initial_balance * daily_loss_limit

    # ── Kiểm tra Daily Loss ──────────────────────────────────────────────────
    # Resample về cuối ngày (lấy giá trị equity cuối cùng trong ngày).
    daily_eq  = combined_equity.resample("D").last().dropna()
    # Tính chênh lệch vốn mỗi ngày (dương = lãi, âm = lỗ).
    daily_pnl = daily_eq.diff()
    # Ngày đầu tiên không có ngày trước để diff → tính so với vốn ban đầu.
    if len(daily_eq):
        daily_pnl.iloc[0] = daily_eq.iloc[0] - initial_balance

    worst_day_pnl      = float(daily_pnl.min())            # Số âm nhất = ngày lỗ nhất
    max_daily_loss_pct = max(-worst_day_pnl / initial_balance, 0.0)

    daily_breached_mask = daily_pnl < -daily_limit_usd
    n_daily_breach      = int(daily_breached_mask.sum())
    daily_breach_date   = (
        str(daily_breached_mask[daily_breached_mask].index[0].date())
        if n_daily_breach > 0 else None
    )

    # ── Kiểm tra Max Drawdown ────────────────────────────────────────────────
    eq_arr = combined_equity.values.astype(float)

    if max_dd_mode in {"trailing_peak", "peak"}:
        # Mốc tham chiếu = đỉnh cao nhất từng đạt được (tăng dần theo thời gian).
        running_peak = np.maximum.accumulate(eq_arr)
        # Đảm bảo peak không nhỏ hơn vốn ban đầu — tránh drawdown tính sai
        # khi portfolio chưa đạt đỉnh nào cao hơn vốn ban đầu.
        running_peak = np.maximum(running_peak, initial_balance)
        dd_usd_arr   = running_peak - eq_arr
        dd_pct_arr   = dd_usd_arr / running_peak

        max_dd_pct  = float(dd_pct_arr.max())
        max_dd_usd  = float(dd_usd_arr.max())
        dd_violated = max_dd_pct > max_dd_limit
    else:
        # Mốc tham chiếu cố định = vốn ban đầu (chế độ FTMO 2-Step).
        # Chỉ tính drawdown khi equity xuống dưới vốn ban đầu.
        loss_usd_arr = np.maximum(initial_balance - eq_arr, 0.0)
        loss_pct_arr = loss_usd_arr / initial_balance
        max_dd_pct   = float(loss_pct_arr.max())
        max_dd_usd   = float(loss_usd_arr.max())
        # Vi phạm khi equity chạm hoặc xuống dưới sàn: initial × (1 - limit).
        dd_floor    = initial_balance * (1 - max_dd_limit)
        dd_violated = bool((eq_arr <= dd_floor).any())

    # ── Kết luận vi phạm ────────────────────────────────────────────────────
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
        "max_daily_loss_pct":  round(max_daily_loss_pct * 100, 2),  # đổi ra %
        "n_daily_breach_days": n_daily_breach,
        "max_dd_pct":          round(max_dd_pct * 100, 2),           # đổi ra %
        "max_dd_usd":          round(max_dd_usd, 2),
    }


# -----------------------------------------------------------------------------
# Monte Carlo robustness
# -----------------------------------------------------------------------------

"""Monte Carlo simulation utilities for trade-level robustness testing.

=======================================================================
FILE NÀY LÀM GÌ?
=======================================================================

File này kiểm tra xem chiến lược có thực sự ổn định, hay chỉ may mắn
vì gặp đúng chuỗi lệnh thuận lợi trong lịch sử.

Ý tưởng rất đơn giản:
  Lấy danh sách lệnh lịch sử → xáo trộn thứ tự nhiều lần → mỗi lần
  xáo được một "phiên bản song song" của chiến lược → xem toàn bộ
  phiên bản đó hoạt động như thế nào.

Ví dụ thực tế:
  Chiến lược thực hiện 200 lệnh. Nếu 10 lệnh thắng lớn đầu tiên xảy
  ra ở cuối thay vì đầu, drawdown sẽ lớn hơn bao nhiêu? Monte Carlo
  trả lời câu hỏi đó bằng cách thử 1000 hoán vị khác nhau.

=======================================================================
TẠI SAO CẦN MONTE CARLO?
=======================================================================

Backtest thông thường chỉ cho một kết quả duy nhất — đúng với chuỗi
lệnh lịch sử đó. Nhưng trong tương lai, thứ tự lệnh thắng/thua sẽ
khác. Monte Carlo mô phỏng hàng nghìn thứ tự có thể xảy ra, từ đó
trả lời:
  - Xác suất drawdown vượt 20% là bao nhiêu %?
  - Sharpe ratio thực sự nằm trong khoảng nào (không phải 1 con số)?
  - Kịch bản xấu nhất trông như thế nào?

=======================================================================
FILE NÀY ĐỘC LẬP — KHÔNG PHỤ THUỘC VÀO CHIẾN LƯỢC CỤ THỂ NÀO
=======================================================================

Chỉ cần truyền vào danh sách lãi/lỗ từng lệnh (trade_pnls) là chạy
được. Không quan tâm đó là chiến lược Combo hay AI Trend.
"""

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .reporting import DARK


def run_monte_carlo(
    trade_pnls: list[float],
    n_iter: int = 1000,
    dd_threshold: float = 0.20,
    initial_balance: float = 0.0,
    trades_per_year: float | None = None,
    random_seed: int | None = None,
) -> dict:
    """Chạy mô phỏng Monte Carlo trên chuỗi lãi/lỗ từng lệnh.

    =======================================================================
    CÁCH HOẠT ĐỘNG
    =======================================================================

    1. Nhận danh sách PnL lịch sử: [+120, -80, +200, -50, ...]
    2. Xáo trộn thứ tự n_iter lần (mặc định 1000 lần)
    3. Mỗi lần xáo → vẽ một đường vốn (equity curve) mới
    4. Từ 1000 đường vốn → tính phân vị P5/P50/P95, drawdown, Sharpe

    Kết quả P5 = đường vốn xấu thứ 5% → kịch bản tệ nhưng không phải
    tệ nhất. P95 = kịch bản tốt. P50 = trung vị, thực tế nhất.

    =======================================================================
    PARAMETERS
    =======================================================================

    trade_pnls
        Danh sách lãi/lỗ từng lệnh theo thứ tự lịch sử gốc.
        Ví dụ: [120.5, -80.0, 200.3, -45.2, ...]
        Đơn vị tùy ý (USD, pip, %) — miễn là nhất quán.

    n_iter
        Số lần xáo trộn và mô phỏng. Mặc định 1000.
        Tăng lên 5000–10000 để kết quả chính xác hơn, nhưng chạy lâu hơn.

    dd_threshold
        Ngưỡng drawdown để tính xác suất vượt. Mặc định 0.20 = 20%.
        Ví dụ: prob_exceed_dd = 0.15 → 15% kịch bản có drawdown > 20%.

    initial_balance
        Số vốn ban đầu. Nên truyền vào số thực (ví dụ: 100_000.0).

        Tại sao quan trọng?
        Nếu để mặc định = 0, các lệnh đầu tiên có peak equity gần 0,
        dẫn đến drawdown bị phóng to rất lớn một cách giả tạo.
        Ví dụ: lệnh đầu lãi +100$, lệnh hai lỗ -50$ → drawdown thực là
        50/100 = 50%, nhưng nếu equity bắt đầu từ 0 thì tính sai hoàn toàn.

    trades_per_year
        Số lệnh trung bình mỗi năm — dùng để tính Sharpe ratio đúng.

        Tại sao không dùng con số cố định 252?
        252 là số ngày giao dịch/năm — đúng cho daily returns.
        Chiến lược H4 trung bình ~200 lệnh/năm, không phải 252.
        Dùng sai → Sharpe bị phóng to ~38 lần so với thực tế.
        Nếu không truyền vào → mặc định dùng 252 (không chính xác).

    random_seed
        Seed cho bộ sinh số ngẫu nhiên. Truyền vào một số cố định
        (ví dụ: 42) để kết quả tái lập được — hữu ích khi debug hoặc
        so sánh hai chiến lược trên cùng tập mô phỏng.

    =======================================================================
    RETURNS
    =======================================================================

    Trả về dict với các key:

    equity_p5 / equity_p50 / equity_p95
        Ba đường vốn theo phân vị 5%, 50%, 95% — tính trên 1000 lần mô phỏng.
        Bắt đầu từ initial_balance (nếu có).

    prob_exceed_dd
        Xác suất (0.0 → 1.0) drawdown vượt ngưỡng dd_threshold.
        Ví dụ: 0.12 = 12% khả năng drawdown > 20%.

    sharpe_ci_low / sharpe_ci_high
        Khoảng tin cậy 95% của Sharpe ratio.
        Ví dụ: [0.8, 1.6] → Sharpe thực sự nằm trong khoảng này
        với xác suất 95%, không phải một con số duy nhất.

    max_drawdowns / sharpe_samples / equity_paths
        Dữ liệu thô của 1000 lần mô phỏng — dùng để vẽ biểu đồ phân phối.
    """
    arr = np.asarray(trade_pnls, dtype=float)

    # Trường hợp không có lệnh nào → trả về dict rỗng, không crash.
    if arr.size == 0:
        empty = np.array([], dtype=float)
        return {
            "equity_p5":             empty,
            "equity_p50":            empty,
            "equity_p95":            empty,
            "prob_exceed_dd":        0.0,
            "sharpe_ci_low":         0.0,
            "sharpe_ci_high":        0.0,
            "max_drawdowns":         empty,
            "sharpe_samples":        empty,
            "sharpe_annualization":  float(trades_per_year) if trades_per_year else 252.0,
            "equity_paths":          np.empty((0, 0), dtype=float),
        }

    n_trades       = arr.size
    equity_paths   = np.zeros((n_iter, n_trades), dtype=float)
    max_drawdowns  = np.zeros(n_iter, dtype=float)
    sharpe_samples = np.zeros(n_iter, dtype=float)

    # Dùng default_rng thay vì np.random.seed() vì an toàn hơn trong môi
    # trường đa luồng và cho phép tái lập kết quả khi truyền random_seed.
    rng = np.random.default_rng(random_seed)

    # Hệ số annualize Sharpe: sqrt(số lệnh/năm).
    # Nếu không biết trades_per_year, dùng 252 — nhưng sẽ không chính xác
    # với chiến lược H4 (nên truyền giá trị thực từ caller).
    sharpe_annualization = float(trades_per_year) if trades_per_year else 252.0

    # -----------------------------------------------------------------------
    # TẠI SAO DÙNG BLOCK BOOTSTRAP THAY VÌ RANDOM SHUFFLE THÔNG THƯỜNG?
    # -----------------------------------------------------------------------
    # Random shuffle hoàn toàn (np.random.permutation) phá vỡ tương quan
    # tự nhiên trong chuỗi lệnh: streaks thắng/thua liên tiếp, hiệu ứng
    # mùa vụ, xu hướng thị trường. Kết quả là confidence interval quá hẹp,
    # trông có vẻ "ổn định" hơn thực tế.
    #
    # Block bootstrap giữ lại các chuỗi con liên tiếp (block) nguyên vẹn.
    # Kích thước block = sqrt(n_trades) là công thức kinh nghiệm cân bằng
    # giữa: giữ đủ correlation vs đủ ngẫu nhiên để mô phỏng đa dạng.
    # Ví dụ: 200 lệnh → block_size = 14 → giữ chuỗi 14 lệnh liên tiếp.
    block_size = max(1, int(np.sqrt(n_trades)))
    # Tạo thêm 1 block dự phòng để sau khi ghép luôn đủ n_trades phần tử.
    n_blocks = int(np.ceil(n_trades / block_size)) + 1

    for i in range(n_iter):
        # Chọn ngẫu nhiên n_blocks điểm bắt đầu trong mảng PnL gốc.
        starts = rng.integers(0, n_trades, size=n_blocks)

        # Tạo index cho từng block bằng modulo để không bao giờ vượt biên
        # mảng (block cuối sẽ "cuộn vòng" về đầu mảng thay vì bị cắt ngắn).
        indices = np.concatenate(
            [np.arange(s, s + block_size) % n_trades for s in starts]
        )[:n_trades]  # Cắt đúng n_trades phần tử.

        shuffled = arr[indices]

        # Equity bắt đầu từ initial_balance để tính drawdown đúng.
        # Xem giải thích chi tiết ở phần Parameters → initial_balance.
        equity = initial_balance + np.cumsum(shuffled)
        equity_paths[i, :] = equity

        # Trailing peak không được nhỏ hơn initial_balance — tránh drawdown
        # âm vô nghĩa khi equity tăng từ đầu và chưa bao giờ xuống dưới vốn.
        running_peak = np.maximum.accumulate(equity)
        running_peak = np.maximum(running_peak, max(initial_balance, 1e-12))
        dd = (running_peak - equity) / running_peak
        max_drawdowns[i] = float(np.max(dd))

        # Sharpe của lần mô phỏng này: mean/std × sqrt(trades_per_year).
        std = shuffled.std(ddof=1) if shuffled.size > 1 else 0.0
        sharpe_samples[i] = (
            (shuffled.mean() / std) * np.sqrt(max(sharpe_annualization, 1.0))
            if std > 0
            else 0.0
        )

    # Tính phân vị trên 1000 equity curve → 3 đường đại diện.
    equity_p5  = np.percentile(equity_paths,  5, axis=0)
    equity_p50 = np.percentile(equity_paths, 50, axis=0)
    equity_p95 = np.percentile(equity_paths, 95, axis=0)

    # Tỷ lệ lần mô phỏng có max drawdown vượt ngưỡng → xác suất rủi ro.
    prob_exceed_dd = float(np.mean(max_drawdowns > dd_threshold))

    # Khoảng tin cậy 95% của Sharpe: cắt 2.5% hai đầu phân phối.
    sharpe_ci_low, sharpe_ci_high = np.percentile(sharpe_samples, [2.5, 97.5])

    return {
        "equity_p5":             equity_p5,
        "equity_p50":            equity_p50,
        "equity_p95":            equity_p95,
        "prob_exceed_dd":        prob_exceed_dd,
        "sharpe_ci_low":         float(sharpe_ci_low),
        "sharpe_ci_high":        float(sharpe_ci_high),
        "max_drawdowns":         max_drawdowns,
        "sharpe_samples":        sharpe_samples,
        "sharpe_annualization":  sharpe_annualization,
        "equity_paths":          equity_paths,
    }


def plot_monte_carlo(mc_result: dict) -> None:
    """Vẽ 3 biểu đồ tóm tắt kết quả Monte Carlo.

    =======================================================================
    MỤC ĐÍCH
    =======================================================================

    Sau khi chạy run_monte_carlo(), hàm này chuyển kết quả số thành 3
    biểu đồ trực quan để đánh giá nhanh độ ổn định của chiến lược.

    =======================================================================
    3 BIỂU ĐỒ
    =======================================================================

    Biểu đồ 1 — Đường vốn theo phân vị (Equity Percentiles):
        Vẽ 3 đường P5 (đỏ), P50 (xanh lá), P95 (xanh dương).
        Đọc như thế nào: Khoảng cách giữa P5 và P95 càng hẹp → chiến
        lược càng ổn định, ít phụ thuộc vào may mắn thứ tự lệnh.

    Biểu đồ 2 — Phân phối Max Drawdown:
        Histogram drawdown tối đa của 1000 lần mô phỏng.
        Đọc như thế nào: Đỉnh cột càng gần 0 càng tốt. Nếu phần lớn
        cột nằm bên phải 20% → rủi ro cao, nên giảm risk/trade.

    Biểu đồ 3 — Phân phối Sharpe Ratio + khoảng tin cậy 95%:
        Histogram Sharpe của 1000 lần mô phỏng, có vạch CI low/high.
        Đọc như thế nào: Nếu CI low < 0 → có kịch bản Sharpe âm → chiến
        lược không đủ ổn định để live trade.

    =======================================================================
    PARAMETERS
    =======================================================================

    mc_result
        Dict trả về từ run_monte_carlo(). Truyền thẳng vào mà không cần
        xử lý thêm.

    Returns
    -------
    None — hiển thị biểu đồ trực tiếp (plt.show()), không trả về gì.
    """
    equity_p5      = np.asarray(mc_result.get("equity_p5",      []), dtype=float)
    equity_p50     = np.asarray(mc_result.get("equity_p50",     []), dtype=float)
    equity_p95     = np.asarray(mc_result.get("equity_p95",     []), dtype=float)
    max_drawdowns  = np.asarray(mc_result.get("max_drawdowns",  []), dtype=float)
    sharpe_samples = np.asarray(mc_result.get("sharpe_samples", []), dtype=float)

    # Màu sắc theo dark theme chung của hệ thống.
    bg     = DARK["bg"]
    panel  = DARK["panel"]
    border = DARK["border"]
    text   = DARK["text"]

    fig, axes = plt.subplots(1, 3, figsize=(18, 5), facecolor=bg)
    for ax in axes:
        ax.set_facecolor(panel)
        ax.tick_params(colors=text)
        for spine in ax.spines.values():
            spine.set_color(border)

    # ── Biểu đồ 1: Đường vốn P5 / P50 / P95 ────────────────────────────────
    x = np.arange(len(equity_p50))
    axes[0].plot(x, equity_p5,  color="#FF6B6B", linewidth=1.5, label="P5  (xấu)")
    axes[0].plot(x, equity_p50, color="#6BCB77", linewidth=2.0, label="P50 (trung vị)")
    axes[0].plot(x, equity_p95, color="#00D4FF", linewidth=1.5, label="P95 (tốt)")
    axes[0].set_title("Đường vốn theo phân vị Monte Carlo", color=text)
    axes[0].set_xlabel("Số lệnh", color=text)
    axes[0].set_ylabel("Vốn", color=text)
    axes[0].grid(color=border, alpha=0.5)
    axes[0].legend(facecolor=panel, edgecolor=border, labelcolor=text)

    # ── Biểu đồ 2: Phân phối Max Drawdown ───────────────────────────────────
    if max_drawdowns.size:
        axes[1].hist(max_drawdowns, bins=40, color="#845EC2", alpha=0.8)
    axes[1].set_title("Phân phối Max Drawdown", color=text)
    axes[1].set_xlabel("Max Drawdown", color=text)
    axes[1].set_ylabel("Số lần xuất hiện", color=text)
    axes[1].grid(color=border, alpha=0.5)

    # ── Biểu đồ 3: Phân phối Sharpe + khoảng tin cậy 95% ───────────────────
    if sharpe_samples.size:
        axes[2].hist(sharpe_samples, bins=40, color="#FFD93D", alpha=0.85)
        ci_low  = mc_result.get("sharpe_ci_low",  0.0)
        ci_high = mc_result.get("sharpe_ci_high", 0.0)
        # Vạch đứt đỏ = giới hạn dưới CI, vạch đứt xanh = giới hạn trên CI.
        axes[2].axvline(ci_low,  color="#FF6B6B", linestyle="--", linewidth=1.5,
                        label=f"CI thấp:  {ci_low:.2f}")
        axes[2].axvline(ci_high, color="#00D4FF", linestyle="--", linewidth=1.5,
                        label=f"CI cao: {ci_high:.2f}")
        axes[2].legend(facecolor=panel, edgecolor=border, labelcolor=text)
    axes[2].set_title("Phân phối Sharpe Ratio", color=text)
    axes[2].set_xlabel("Sharpe", color=text)
    axes[2].set_ylabel("Số lần xuất hiện", color=text)
    axes[2].grid(color=border, alpha=0.5)

    fig.suptitle("Monte Carlo — Kiểm tra độ ổn định chiến lược", color=text, fontsize=14)
    fig.tight_layout()
    plt.show()
