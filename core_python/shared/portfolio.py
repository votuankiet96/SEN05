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
