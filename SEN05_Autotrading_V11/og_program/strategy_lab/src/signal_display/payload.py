"""
Chuyển DataFrame OHLCV đã enrich thành dữ liệu JSON-safe cho chart.

Mô tả:
    File này CHỈ làm một việc: nhận DataFrame đã qua
    configuration.run_strategy() (có cột signal), trả về các cấu trúc
    dữ liệu JSON-safe thuần (list[dict]/dict) mà renderer.py (snapshot tĩnh)
    hoặc live_page.py + server.py (dashboard sống) sẽ dùng — không tự render
    HTML, không tự ghi file, không gọi SQL, không import renderer.py/
    live_page.py/server.py (file này là lá trong cây phụ thuộc).

Đầu vào:
    pd.DataFrame OHLCV đã enrich (bắt buộc có bartime/open/high/low/close;
    các hàm còn lại cần thêm cột signal, và tuỳ hàm cần thêm entry_time/
    entry_price/sl_price/tp_price/risk_reward/sl_dow/signal_reason).

Đầu ra:
    candlestick_points(df)   -> [{time, open, high, low, close}]
    signal_markers(df)       -> [{time, position, color, shape, text}]
    line_points(df, col)     -> [{time, value, color?}]
    histogram_points(df,col) -> [{time, value, color}]
    signal_table_rows(df)    -> [{time, bartime, side, entry, sl, tp, rr, reason}]
    stats_summary(df)        -> {total, buy, sell, last}
"""

from __future__ import annotations

from typing import Any

import pandas as pd

BUY_COLOR = "#16a34a"
SELL_COLOR = "#dc2626"
UP_COLOR = "#22c55e"
DOWN_COLOR = "#ef4444"


def to_unix_ts(value: object) -> int:
    """
    Chuyển timestamp sang Unix UTC integer (yêu cầu của Lightweight Charts).

    Args:
        value: pd.Timestamp, datetime, hoặc chuỗi có thể parse được.

    Returns:
        Unix timestamp (giây). UTC-naive input được localize về UTC;
        input đã có timezone được convert về UTC trước khi lấy epoch.
    """
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return int(ts.timestamp())


def candlestick_points(df: pd.DataFrame) -> list[dict[str, Any]]:
    """
    Trích dữ liệu nến OHLC cho Lightweight Charts CandlestickSeries.

    Bỏ qua dòng có bartime NaN. Volume không được bao gồm (Candlestick
    series không dùng volume).

    Args:
        df: DataFrame với cột [bartime, open, high, low, close].

    Returns:
        List [{time, open, high, low, close}] theo đúng thứ tự df.
    """
    return [
        {
            "time": to_unix_ts(row["bartime"]),
            # Làm tròn 2 chữ số (chốt 2026-09-23), đồng nhất với mọi giá trị
            # số khác trên dashboard (xem _to_number()).
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
        }
        for _, row in df.iterrows()
        if pd.notna(row.get("bartime"))
    ]


def signal_markers(df: pd.DataFrame) -> list[dict[str, Any]]:
    """
    Trích mũi tên BUY/SELL cho Lightweight Charts series.setMarkers().

    Chỉ dựa vào cột "signal" (+1 = BUY, -1 = SELL, 0 = không có tín hiệu) —
	    không đụng tới entry/level hay bất kỳ cột nào khác.

    Args:
        df: DataFrame đã qua configuration.run_strategy() — cần cột
            bartime và signal.

    Returns:
        List [{time, position, color, shape, text}] cho các bar có tín hiệu,
        sắp theo đúng thứ tự df (đã tăng dần theo bartime).
    """
    if "signal" not in df.columns:
        return []
    signals = df[df["signal"].fillna(0).astype(int).ne(0)]

    output: list[dict[str, Any]] = []
    for _, row in signals.iterrows():
        is_buy = int(row["signal"]) == 1
        output.append(
            {
                "time": to_unix_ts(row["bartime"]),
                "position": "belowBar" if is_buy else "aboveBar",
                "color": BUY_COLOR if is_buy else SELL_COLOR,
                "shape": "arrowUp" if is_buy else "arrowDown",
                "text": "BUY" if is_buy else "SELL",
            }
        )
    return output


def _to_number(value: object, digits: int | None = 2) -> float | None:
    """Chuyển sang float, trả về None nếu NaN/None; làm tròn theo `digits`
    (mặc định 2 chữ số thập phân, chốt 2026-09-23 -- áp dụng cho mọi giá trị
    hiển thị trên dashboard: MA/EMA/MACD Histogram/ATR overlay qua
    line_points()/histogram_points()/trend_line_points() đều gọi hàm này
    KHÔNG truyền digits nên tự động nhận default mới, không cần sửa từng
    hàm). Truyền digits=None nếu có nơi thật sự cần giữ nguyên full
    precision (hiện chưa có nơi nào cần)."""
    if value is None or pd.isna(value):
        return None
    out = float(value)
    return round(out, digits) if digits is not None else out


def _signal_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Trả về các dòng có tín hiệu (signal != 0); rỗng nếu thiếu cột signal."""
    if "signal" not in df.columns:
        return df.iloc[0:0]
    return df[df["signal"].fillna(0).astype(int).ne(0)]


def line_points(df: pd.DataFrame, column: str, *, color: str | None = None) -> list[dict[str, Any]]:
    """
    Trích dữ liệu line series cho các đường overlay MA/Fast/Slow SMA.

    Bỏ qua dòng NaN của cột. Args:
        df: DataFrame chứa cột bartime và `column`.
        column: Tên cột giá trị (vd "ma", "fast_ma", "slow_ma").
        color: Màu hex tuỳ chọn — nếu có sẽ thêm vào từng điểm.

    Returns:
        List [{time, value, color?}] cho Lightweight Charts addLineSeries.
    """
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        value = _to_number(row.get(column))
        if value is None:
            continue
        item: dict[str, Any] = {"time": to_unix_ts(row["bartime"]), "value": value}
        if color:
            item["color"] = color
        rows.append(item)
    return rows


def histogram_points(df: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    """
    Trích dữ liệu histogram (panel MACD Histogram) — màu xanh/đỏ theo dấu.

    Args:
        df: DataFrame chứa cột bartime và `column` (thường "macd_h").
        column: Tên cột giá trị.

    Returns:
        List [{time, value, color}], bỏ qua dòng NaN.
    """
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        value = _to_number(row.get(column))
        if value is None:
            continue
        rows.append(
            {
                "time": to_unix_ts(row["bartime"]),
                "value": value,
                "color": UP_COLOR if value >= 0 else DOWN_COLOR,
            }
        )
    return rows


def trend_line_points(
    df: pd.DataFrame,
    column: str,
    *,
    bias_column: str = "trend_bias",
) -> list[dict[str, Any]]:
    """
    Trích line series cho KNN trend, tô màu từng điểm theo trend_bias.

    1 = xanh, -1 = đỏ, 0/NaN = xám. Không dùng cột signal để tránh lẫn
    marker trend với marker vào lệnh.
    """
    if bias_column not in df.columns:
        return line_points(df, column)

    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        value = _to_number(row.get(column))
        if value is None:
            continue
        bias = _to_number(row.get(bias_column))
        color = UP_COLOR if bias == 1 else DOWN_COLOR if bias == -1 else "#94a3b8"
        rows.append({"time": to_unix_ts(row["bartime"]), "value": value, "color": color})
    return rows


def trend_bias_markers(df: pd.DataFrame) -> list[dict[str, Any]]:
    """
    Tạo marker khi KNN trend đổi bias trên chart khung lớn.

    Marker này chỉ diễn giải trạng thái trend, không phải tín hiệu BUY/SELL.
    """
    if "trend_bias" not in df.columns:
        return []

    trend_bias = pd.to_numeric(df["trend_bias"], errors="coerce").fillna(0).astype(int)
    changed = trend_bias.ne(trend_bias.shift(1))
    rows = df.loc[changed].copy()

    markers: list[dict[str, Any]] = []
    for _, row in rows.iterrows():
        bias = int(row.get("trend_bias", 0))
        if bias == 1:
            markers.append(
                {
                    "time": to_unix_ts(row["bartime"]),
                    "position": "belowBar",
                    "color": UP_COLOR,
                    "shape": "arrowUp",
                    "text": "TREND UP",
                }
            )
        elif bias == -1:
            markers.append(
                {
                    "time": to_unix_ts(row["bartime"]),
                    "position": "aboveBar",
                    "color": DOWN_COLOR,
                    "shape": "arrowDown",
                    "text": "TREND DOWN",
                }
            )
        else:
            markers.append(
                {
                    "time": to_unix_ts(row["bartime"]),
                    "position": "inBar",
                    "color": "#94a3b8",
                    "shape": "circle",
                    "text": "NEUTRAL",
                }
            )
    return markers


def signal_table_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """
    Trích bảng danh sách tín hiệu cho dashboard.

    Args:
        df: DataFrame đã qua configuration.run_strategy().

    Returns:
        List [{time, bartime, side, entry, sl, tp, rr, sl_dow, reason}], theo thứ tự df.
    """
    output: list[dict[str, Any]] = []
    for _, row in _signal_rows(df).iterrows():
        is_buy = int(row["signal"]) == 1
        output.append(
            {
                "time": to_unix_ts(row["bartime"]),
                "bartime": pd.Timestamp(row["bartime"]).strftime("%Y-%m-%d %H:%M"),
                "side": "BUY" if is_buy else "SELL",
                # 2 chữ số cho mọi trường (chốt 2026-09-23, đồng nhất với
                # _to_number() mặc định) -- trước đó entry/sl/tp/sl_dow dùng
                # 5 chữ số riêng lẻ, không khớp với rr (2 chữ số).
                "entry": _to_number(row.get("entry_price")),
                "sl": _to_number(row.get("sl_price")),
                "tp": _to_number(row.get("tp_price")),
                "rr": _to_number(row.get("risk_reward")),
                "sl_dow": _to_number(row.get("sl_dow")),
                "reason": row.get("signal_reason", ""),
            }
        )
    return output


def stats_summary(df: pd.DataFrame) -> dict[str, Any]:
    """
    Trả về tóm tắt số lượng tín hiệu.

    Kèm rawTotal/rawBuy/rawSell (chốt 2026-09-25) -- đếm cột "raw_signal"
    (tín hiệu TRƯỚC khi lọc trend, luôn có sẵn trong df bất kể trend đang
    bật hay tắt -- không cần chạy lại pipeline lần 2) để dashboard so sánh
    mức trend filter đã lọc mất bao nhiêu, cả số tuyệt đối lẫn tương đối.
    Nếu df thiếu cột "raw_signal" (gọi hàm này với DataFrame chưa qua
    detect_signals()), rawTotal/rawBuy/rawSell trùng total/buy/sell --
    coi như không có gì để so sánh, không raise lỗi.

    Args:
        df: DataFrame đã qua configuration.run_strategy().

    Returns:
        {total, buy, sell, last, rawTotal, rawBuy, rawSell} — last là "-"
        nếu không có tín hiệu nào.
    """
    signals = _signal_rows(df)
    if signals.empty:
        result = {"total": 0, "buy": 0, "sell": 0, "last": "-"}
    else:
        buy = int(signals["signal"].eq(1).sum())
        sell = int(signals["signal"].eq(-1).sum())
        last_signal = int(signals["signal"].iloc[-1])
        result = {
            "total": len(signals),
            "buy": buy,
            "sell": sell,
            "last": "BUY" if last_signal == 1 else "SELL",
        }

    if "raw_signal" in df.columns:
        raw = pd.to_numeric(df["raw_signal"], errors="coerce").fillna(0).astype(int)
        result["rawTotal"] = int(raw.ne(0).sum())
        result["rawBuy"] = int(raw.eq(1).sum())
        result["rawSell"] = int(raw.eq(-1).sum())
    else:
        result["rawTotal"] = result["total"]
        result["rawBuy"] = result["buy"]
        result["rawSell"] = result["sell"]
    return result
