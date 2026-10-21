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
    các hàm còn lại cần thêm cột signal, và tuỳ hàm cần thêm entry_price/
    signal_reason).

Đầu ra:
    candlestick_points(df)   -> [{time, open, high, low, close}]
    signal_markers(df)       -> [{time, position, color, shape, text}]
    line_points(df, col)     -> [{time, value, color?}]
    histogram_points(df,col) -> [{time, value, color}]
    signal_table_rows(df)    -> [{time, bartime, side, entry, sl, tp, rr, reason}]
    stats_summary(df)        -> {total, buy, sell, last}

Hiệu năng:
    Bốn hàm dựng chuỗi dài (candlestick_points, line_points, histogram_points,
    trend_line_points) xử lý theo cột bằng numpy, không lặp từng dòng bằng
    iterrows — vòng lặp cũ chiếm 64–90% thời gian một lần quét của dashboard.
    Kết quả giống hệt bản lặp từng dòng (từng giá trị, từng khoá, cùng thứ
    tự); test khoá điều này bằng bản lặp cũ ở tests/payload_reference.py.
    Các hàm chỉ chạy trên vài dòng tín hiệu (signal_markers, signal_table_rows,
    trend_bias_markers) giữ nguyên cách lặp từng dòng.
"""

from __future__ import annotations

from typing import Any

import numpy as np
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


def _epoch_seconds(values: pd.Series) -> list[int]:
    """
    Cột thời gian -> list Unix giây UTC, từng phần tử bằng to_unix_ts().

    Cột datetime64 (naive coi như UTC, hoặc có timezone) đổi một lượt bằng numpy;
    phần lẻ giây bị cắt, như int() trong to_unix_ts() với thời điểm từ 1970 trở đi
    (bar của hệ thống này luôn tròn phút, từ 2017). Kiểu khác (object, chuỗi...) và
    cột có NaT đi đường từng phần tử như cũ, nên NaT vẫn ném ValueError.
    """
    if not pd.api.types.is_datetime64_any_dtype(values) or values.isna().any():
        return [to_unix_ts(value) for value in values]
    if values.dt.tz is not None:
        values = values.dt.tz_convert("UTC").dt.tz_localize(None)
    return values.to_numpy().astype("datetime64[s]").astype("int64").tolist()


def _floats(df: pd.DataFrame, column: str) -> np.ndarray | None:
    """Cột -> mảng float64 (None/NA thành NaN); None nếu df không có cột đó."""
    if column not in df.columns:
        return None
    return df[column].to_numpy(dtype="float64", na_value=np.nan)


def _round2(values: np.ndarray, digits: int = 2) -> list[float]:
    """
    Làm tròn `digits` chữ số (mặc định 2), cùng quy tắc với _to_number(), bằng round() của Python.
    `digits` lớn hơn 2 dành cho symbol giá ~1 như forex (configuration.price_decimals).

    KHÔNG đổi thành np.round: thuật toán khác nên lệch ở ca sát nửa
    (round(2.675, 2) = 2.67 còn np.round ra 2.68), và dashboard đã hiển thị
    giá trị của round().
    """
    return [round(value, digits) for value in values.tolist()]


def _column_points(df: pd.DataFrame, column: str, digits: int = 2) -> tuple[list[int], list[float], np.ndarray] | None:
    """
    Các điểm (thời gian, giá trị đã làm tròn) của một cột, bỏ dòng NaN, kèm mặt
    nạ dòng được giữ (để ghép thêm cột khác cùng dòng). None nếu không có cột
    hoặc không còn điểm nào.
    """
    values = _floats(df, column)
    if values is None:
        return None
    keep = ~np.isnan(values)
    if not keep.any():
        return None
    return _epoch_seconds(df["bartime"][keep]), _round2(values[keep], digits), keep


def candlestick_points(df: pd.DataFrame, digits: int = 2) -> list[dict[str, Any]]:
    """
    Trích dữ liệu nến OHLC cho Lightweight Charts CandlestickSeries.

    Bỏ qua dòng có bartime NaN. Volume không được bao gồm (Candlestick
    series không dùng volume).

    Args:
        df: DataFrame với cột [bartime, open, high, low, close].
        digits: Số chữ số thập phân khi làm tròn giá (mặc định 2).

    Returns:
        List [{time, open, high, low, close}] theo đúng thứ tự df.
    """
    if "bartime" not in df.columns:
        return []
    valid = df["bartime"].notna().to_numpy()
    if not valid.any():
        return []
    rows = df if valid.all() else df[valid]
    # Làm tròn 2 chữ số (chốt 2026-09-23), đồng nhất với mọi giá trị số khác
    # trên dashboard (xem _to_number()).
    opens, highs, lows, closes = (
        _round2(rows[column].to_numpy(dtype="float64"), digits) for column in ("open", "high", "low", "close")
    )
    return [
        {"time": t, "open": o, "high": h, "low": lo, "close": c}
        for t, o, h, lo, c in zip(_epoch_seconds(rows["bartime"]), opens, highs, lows, closes, strict=True)
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


def line_points(df: pd.DataFrame, column: str, *, color: str | None = None, digits: int = 2) -> list[dict[str, Any]]:
    """
    Trích dữ liệu line series cho các đường overlay MA/Fast/Slow SMA.

    Bỏ qua dòng NaN của cột. Args:
        df: DataFrame chứa cột bartime và `column`.
        column: Tên cột giá trị (vd "ma", "fast_ma", "slow_ma").
        color: Màu hex tuỳ chọn — nếu có sẽ thêm vào từng điểm.

    Returns:
        List [{time, value, color?}] cho Lightweight Charts addLineSeries.
    """
    points = _column_points(df, column, digits)
    if points is None:
        return []
    times, values, _ = points
    if color:
        return [{"time": t, "value": v, "color": color} for t, v in zip(times, values, strict=True)]
    return [{"time": t, "value": v} for t, v in zip(times, values, strict=True)]


def histogram_points(df: pd.DataFrame, column: str, digits: int = 2) -> list[dict[str, Any]]:
    """
    Trích dữ liệu histogram (panel MACD Histogram) — màu xanh/đỏ theo dấu.

    Dấu xét trên giá trị ĐÃ làm tròn 2 chữ số (-0.004 thành -0.0, vẫn tính là >= 0).

    Args:
        df: DataFrame chứa cột bartime và `column` (thường "macd_h").
        column: Tên cột giá trị.

    Returns:
        List [{time, value, color}], bỏ qua dòng NaN.
    """
    points = _column_points(df, column, digits)
    if points is None:
        return []
    times, values, _ = points
    return [
        {"time": t, "value": v, "color": UP_COLOR if v >= 0 else DOWN_COLOR}
        for t, v in zip(times, values, strict=True)
    ]


def trend_line_points(
    df: pd.DataFrame,
    column: str,
    *,
    bias_column: str = "trend_bias",
    digits: int = 2,
) -> list[dict[str, Any]]:
    """
    Trích line series của một khối xác nhận xu hướng, tô màu từng điểm theo
    cột hướng bias_column (mặc định trend_bias đã ghép các khối).

    1 = xanh, -1 = đỏ, 0/NaN = xám. Không dùng cột signal để tránh lẫn
    marker trend với marker vào lệnh.
    """
    if bias_column not in df.columns:
        return line_points(df, column, digits=digits)

    points = _column_points(df, column, digits)
    if points is None:
        return []
    times, values, keep = points
    # Hướng cũng xét sau khi làm tròn 2 chữ số (0.999 thành 1.0 tính là 1); NaN không bằng 1 hay -1 nên ra xám.
    biases = _round2(_floats(df, bias_column)[keep])
    return [
        {"time": t, "value": v, "color": UP_COLOR if b == 1 else DOWN_COLOR if b == -1 else "#94a3b8"}
        for t, v, b in zip(times, values, biases, strict=True)
    ]


def trend_bias_markers(df: pd.DataFrame) -> list[dict[str, Any]]:
    """
    Tạo marker khi trend (đã ghép các khối xác nhận) đổi bias trên chart khung lớn.

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


def signal_table_rows(df: pd.DataFrame, digits: int = 2) -> list[dict[str, Any]]:
    """
    Trích bảng danh sách tín hiệu cho dashboard.

    Args:
        df: DataFrame đã qua configuration.run_strategy().

    Returns:
        List [{time, bartime, side, entry, reason}], theo thứ tự df.
    """
    output: list[dict[str, Any]] = []
    for _, row in _signal_rows(df).iterrows():
        is_buy = int(row["signal"]) == 1
        output.append(
            {
                "time": to_unix_ts(row["bartime"]),
                "bartime": pd.Timestamp(row["bartime"]).strftime("%Y-%m-%d %H:%M"),
                "side": "BUY" if is_buy else "SELL",
                # 2 chữ số (chốt 2026-09-23, đồng nhất với _to_number() mặc định).
                "entry": _to_number(row.get("entry_price"), digits),
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
