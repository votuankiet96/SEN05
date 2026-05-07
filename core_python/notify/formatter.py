"""
Định dạng tin nhắn tín hiệu cho Telegram (HTML parse mode).

Mô tả:
    Chuyển đổi một dòng tín hiệu (pd.Series) thành chuỗi HTML có emoji,
    phù hợp với Telegram Bot API parse_mode="HTML".

    Các thẻ HTML được dùng: <b> (bold), <code> (monospace).
    Các ký tự đặc biệt trong signal_reason được escape bằng html.escape()
    để tránh lỗi parse nếu reason chứa '<', '>', '&'.

Đầu ra:
    Chuỗi HTML sẵn sàng gửi qua Telegram API.

Lưu ý:
    Module này chỉ định dạng — không gửi. Việc gửi do notifier.py đảm nhiệm.
"""

from __future__ import annotations

import html

import pandas as pd


def format_signal_message(
    row: pd.Series,
    *,
    strategy_label: str,
    symbol: str,
    tf: str,
) -> str:
    """
    Tạo tin nhắn HTML có emoji cho một tín hiệu giao dịch.

    Cấu trúc tin nhắn:
        🟢 <b>BUY</b> — Combo <b>US30</b> H1    (hoặc 🔴 SELL)

        ⏰ 2024-01-15 09:00 UTC
        📍 Entry: <code>44250.00</code>
        🛑 SL:    <code>44100.00</code>
        🎯 TP:    <code>44700.00</code>
        ⚖️ R:R:   <code>3.18</code>     (nếu có)
        📊 ATR:   <code>125.5</code>    (nếu có)

        💬 close crossed above MA, bullish candle, MACD histogram > 0  (nếu có)

    Args:
        row: pd.Series một dòng tín hiệu đã enriched. Cần các key:
             signal (int), bartime (datetime), entry_price, sl_price, tp_price,
             risk_reward, atr, signal_reason.
        strategy_label: Tên chiến lược hiển thị (ví dụ: "Combo", "MA Cross").
        symbol: Mã symbol (ví dụ: "US30") — tự động uppercase.
        tf: Mã khung thời gian (ví dụ: "H1") — tự động uppercase.

    Returns:
        Chuỗi HTML có thể gửi thẳng vào Telegram với parse_mode="HTML".

    Giả định giao dịch:
        bartime là UTC-naive — chuỗi format thêm " UTC" để người đọc biết timezone.
        Giá trị None/NaN được hiển thị là "-" (không crash).
    """
    direction = "BUY" if int(row["signal"]) == 1 else "SELL"
    icon = "🟢" if direction == "BUY" else "🔴"
    signal_time = pd.Timestamp(row["bartime"]).strftime("%Y-%m-%d %H:%M")

    lines = [
        f"{icon} <b>{direction}</b> — {strategy_label} <b>{symbol.upper()}</b> {tf.upper()}",
        "",
        f"⏰ {signal_time} UTC",
        f"📍 Entry: <code>{_fmt(row.get('entry_price'))}</code>",
        f"🛑 SL:    <code>{_fmt(row.get('sl_price'))}</code>",
        f"🎯 TP:    <code>{_fmt(row.get('tp_price'))}</code>",
    ]
    rr = row.get("risk_reward")
    if pd.notna(rr):
        lines.append(f"⚖️ R:R:   <code>{_fmt(rr, 2)}</code>")
    atr = row.get("atr")
    if pd.notna(atr):
        lines.append(f"📊 ATR:   <code>{_fmt(atr)}</code>")

    # Escape HTML để tránh lỗi nếu signal_reason chứa ký tự đặc biệt (<, >, &)
    reason = html.escape(str(row.get("signal_reason") or "").strip())
    if reason:
        lines.extend(["", f"💬 {reason}"])
    return "\n".join(lines)


def format_combo_raw_signal_message(
    row: pd.Series,
    *,
    strategy_label: str,
    symbol: str,
    tf: str,
) -> str:
    """Format a Combo signal for Discord without pending entry/SL/TP levels."""
    direction = "BUY" if int(row["signal"]) == 1 else "SELL"
    signal_time = pd.Timestamp(row["bartime"]).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"Combo RAW SIGNAL | {symbol.upper()} {tf.upper()} | {direction}",
        f"Strategy: {strategy_label}",
        f"Bartime: {signal_time} UTC",
    ]

    ohlc = []
    for name in ("open", "high", "low", "close"):
        value = row.get(name)
        if pd.notna(value):
            ohlc.append(f"{name.upper()}={_fmt(value)}")
    if ohlc:
        lines.append("OHLC: " + " | ".join(ohlc))

    indicator_parts = []
    for label, column in (("MA", "ma"), ("MACD_H", "macd_h"), ("ATR", "atr")):
        value = row.get(column)
        if pd.notna(value):
            indicator_parts.append(f"{label}={_fmt(value)}")
    if indicator_parts:
        lines.append("Raw: " + " | ".join(indicator_parts))

    reason = str(row.get("signal_reason") or "").strip()
    if reason:
        lines.append(f"Reason: {reason}")
    return "\n".join(lines)


def _fmt(value: object, digits: int = 5) -> str:
    """
    Định dạng số thập phân, bỏ trailing zeros và dấu chấm thừa.

    Ví dụ:
        44250.0    → "44250"
        3.18000    → "3.18"
        0.50000    → "0.5"
        None / NaN → "-"

    Args:
        value: Giá trị số cần format.
        digits: Số chữ số thập phân tối đa (mặc định 5).

    Returns:
        Chuỗi đã định dạng, hoặc "-" nếu giá trị không hợp lệ.
    """
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
