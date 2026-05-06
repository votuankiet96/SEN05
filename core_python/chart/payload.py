"""
Xây dựng JSON payload cho Lightweight Charts từ DataFrame chiến lược đã enriched.

Mô tả:
    Nhận DataFrame đã qua đầy đủ 3 bước (add_indicators → detect_signals → add_levels),
    chuyển đổi sang cấu trúc JSON mà frontend Lightweight Charts hiểu được.

Đầu ra:
    Dict JSON với cấu trúc:
    {
        "meta":    {strategy, strategyLabel, symbol, tf, bars},
        "params":  {tham số chiến lược đã validate},
        "candles": [{time, open, high, low, close}],
        "overlays": [{key, label, color, data}],   ← Đường MA chồng lên giá
        "panels":   [{key, label, type, data}],    ← Bảng phụ MACD/ATR
        "markers":  [{time, position, color, shape, text}],  ← Mũi tên BUY/SELL
        "levels":   [{kind, label, timeStart, timeEnd, price, color, style}],
        "signals":  [{bartime, side, entry, sl, tp, rr, atr, ...}],
        "stats":    {total, buy, sell, last}
    }

Giả định giao dịch:
    - Timestamps phải là Unix UTC integer (yêu cầu của Lightweight Charts).
    - Bar đang mở (chưa đóng) có thể xuất hiện trong kết quả.
    - Tối đa MAX_SIGNAL_ROWS dòng trong bảng tín hiệu (lấy N dòng cuối).
"""

from __future__ import annotations

from typing import Any

import pandas as pd


# Số dòng tối đa hiển thị trong bảng tín hiệu — tránh render quá nhiều DOM elements
MAX_SIGNAL_ROWS = 120


def _ts(value: object) -> int:
    """
    Chuyển đổi timestamp sang Unix integer UTC (yêu cầu của Lightweight Charts).

    Args:
        value: pd.Timestamp, datetime, hoặc chuỗi ISO có thể parse được.

    Returns:
        Unix timestamp dạng int (giây). UTC-naive input được localize về UTC.
    """
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return int(ts.timestamp())


def _num(value: object, digits: int | None = None) -> float | None:
    """
    Chuyển đổi giá trị sang float, trả về None nếu NaN hoặc None.

    Args:
        value: Giá trị cần convert.
        digits: Số chữ số thập phân để làm tròn (None = không làm tròn).

    Returns:
        float hoặc None nếu giá trị không hợp lệ.
    """
    if value is None or pd.isna(value):
        return None
    out = float(value)
    return round(out, digits) if digits is not None else out


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    """
    Chuẩn hóa dict tham số để JSON serializable.

    Chuyển các giá trị không phải kiểu JSON primitive (str, bool, int, float, list, None)
    thành chuỗi để tránh lỗi JSON serialization.

    Args:
        params: Dict tham số chiến lược đã validate.

    Returns:
        Dict chỉ chứa kiểu JSON-safe.
    """
    clean: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            clean[key] = None
        elif isinstance(value, (str, bool, int, float, list)):
            clean[key] = value
        else:
            clean[key] = str(value)
    return clean


def _series(df: pd.DataFrame, column: str, *, color: str | None = None) -> list[dict[str, Any]]:
    """
    Trích xuất dữ liệu line series từ một cột DataFrame.

    Bỏ qua các dòng có giá trị NaN (Lightweight Charts không chấp nhận null trong series).

    Args:
        df: DataFrame chứa cột bartime và cột cần lấy.
        column: Tên cột giá trị.
        color: Màu hex tùy chọn — nếu có sẽ thêm vào từng điểm dữ liệu.

    Returns:
        List [{time: int, value: float, color?: str}] cho Lightweight Charts.
    """
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        value = _num(row.get(column))
        if value is None:
            continue
        item: dict[str, Any] = {"time": _ts(row["bartime"]), "value": value}
        if color:
            item["color"] = color
        rows.append(item)
    return rows


def _histogram(df: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    """
    Trích xuất dữ liệu histogram với màu xanh/đỏ tự động.

    Giá trị >= 0 → xanh lá (#22c55e), < 0 → đỏ (#ef4444).
    Bỏ qua dòng NaN.

    Args:
        df: DataFrame chứa cột bartime và cột histogram.
        column: Tên cột (thường là "macd_h").

    Returns:
        List [{time: int, value: float, color: str}].
    """
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        value = _num(row.get(column))
        if value is None:
            continue
        rows.append(
            {
                "time": _ts(row["bartime"]),
                "value": value,
                "color": "#22c55e" if value >= 0 else "#ef4444",
            }
        )
    return rows


def _candles(df: pd.DataFrame) -> list[dict[str, Any]]:
    """
    Trích xuất dữ liệu OHLCV dạng candlestick.

    Bỏ qua dòng có bartime NaN. Volume không được bao gồm
    (Lightweight Charts CandlestickSeries không dùng volume).

    Args:
        df: DataFrame với cột [bartime, open, high, low, close].

    Returns:
        List [{time, open, high, low, close}] sắp xếp theo thứ tự df.
    """
    return [
        {
            "time": _ts(row["bartime"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        }
        for _, row in df.iterrows()
        if pd.notna(row.get("bartime"))
    ]


def _markers(signals: pd.DataFrame) -> list[dict[str, Any]]:
    """
    Tạo marker mũi tên BUY/SELL cho biểu đồ giá.

    BUY → mũi tên xanh hướng lên dưới bar (belowBar).
    SELL → mũi tên đỏ hướng xuống trên bar (aboveBar).

    Args:
        signals: DataFrame chỉ gồm các dòng có signal != 0.

    Returns:
        List marker dict theo định dạng Lightweight Charts.
    """
    markers: list[dict[str, Any]] = []
    for _, row in signals.iterrows():
        direction = int(row["signal"])
        is_buy = direction == 1
        markers.append(
            {
                "time": _ts(row["bartime"]),
                "position": "belowBar" if is_buy else "aboveBar",
                "color": "#16a34a" if is_buy else "#dc2626",
                "shape": "arrowUp" if is_buy else "arrowDown",
                "text": "BUY" if is_buy else "SELL",
            }
        )
    return markers


def _levels(df: pd.DataFrame, signals: pd.DataFrame, params: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Tạo các đường mức Entry/SL/TP kéo dài ENTRY_LINE_BARS bar trên biểu đồ.

    Mỗi tín hiệu tạo ra tối đa 3 đường ngang:
    - entry_price: nét đứt màu trắng xám
    - sl_price:    nét chấm màu đỏ
    - tp_price:    nét chấm màu xanh

    Nếu SHOW_LEVELS = False trong params, trả về list rỗng.

    Args:
        df: DataFrame đầy đủ (dùng để tra cứu index và timestamp).
        signals: DataFrame chỉ gồm dòng có signal != 0.
        params: Dict tham số đã validate (cần SHOW_LEVELS, ENTRY_LINE_BARS).

    Returns:
        List level dict cho frontend render.

    Giả định giao dịch:
        entry_time xác định điểm bắt đầu đường mức. Nếu entry_time NaN,
        dùng bartime thay thế. Đường kéo dài ENTRY_LINE_BARS bar từ điểm đó.
    """
    if not bool(params.get("SHOW_LEVELS", True)):
        return []

    # Tạo map từ unix timestamp → row index để tra cứu nhanh vị trí bar
    index_by_time = {_ts(row["bartime"]): idx for idx, row in df.iterrows()}
    times = [_ts(value) for value in df["bartime"]]
    line_bars = int(params.get("ENTRY_LINE_BARS", 20))
    result: list[dict[str, Any]] = []

    for _, row in signals.iterrows():
        start_time = _ts(row["entry_time"] if pd.notna(row.get("entry_time")) else row["bartime"])
        start_idx = index_by_time.get(start_time)
        if start_idx is None:
            start_idx = index_by_time.get(_ts(row["bartime"]))
        if start_idx is None or not times:
            continue
        # Clamp điểm cuối đường level vào cuối DataFrame
        end_time = times[min(start_idx + line_bars, len(times) - 1)]

        direction = int(row["signal"])
        prefix = "BUY" if direction == 1 else "SELL"
        for key, kind, color, style in [
            ("entry_price", "entry", "#e5e7eb", "dashed"),
            ("sl_price", "sl", "#ef4444", "dotted"),
            ("tp_price", "tp", "#22c55e", "dotted"),
        ]:
            value = _num(row.get(key))
            if value is None:
                continue
            result.append(
                {
                    "kind": kind,
                    "label": f"{prefix} {kind.upper()}",
                    "timeStart": start_time,
                    "timeEnd": end_time,
                    "price": value,
                    "color": color,
                    "style": style,
                }
            )
    return result


def _signal_table(signals: pd.DataFrame, strategy: str) -> list[dict[str, Any]]:
    """
    Định dạng dữ liệu bảng tín hiệu cho frontend HTML table.

    Lấy tối đa MAX_SIGNAL_ROWS dòng cuối. Bao gồm các cột chung và cột
    riêng theo chiến lược (combo: ma, macd_h; ma_cross: fast_ma, slow_ma, ma_gap_atr).

    Args:
        signals: DataFrame chỉ gồm dòng signal != 0.
        strategy: "combo" hoặc "ma_cross" — xác định cột bổ sung.

    Returns:
        List dict mỗi phần tử là một dòng bảng tín hiệu.
    """
    rows: list[dict[str, Any]] = []
    for _, row in signals.tail(MAX_SIGNAL_ROWS).iterrows():
        direction = int(row["signal"])
        item: dict[str, Any] = {
            "bartime": pd.Timestamp(row["bartime"]).strftime("%Y-%m-%d %H:%M"),
            "side": "BUY" if direction == 1 else "SELL",
            "reason": row.get("signal_reason", ""),
            "entry": _num(row.get("entry_price"), 5),
            "sl": _num(row.get("sl_price"), 5),
            "tp": _num(row.get("tp_price"), 5),
            "rr": _num(row.get("risk_reward"), 2),
            "atr": _num(row.get("atr"), 5),
        }
        if strategy == "combo":
            item["ma"] = _num(row.get("ma"), 5)
            item["macd_h"] = _num(row.get("macd_h"), 5)
        elif strategy == "ma_cross":
            item["fast_ma"] = _num(row.get("fast_ma"), 5)
            item["slow_ma"] = _num(row.get("slow_ma"), 5)
            item["ma_gap_atr"] = _num(row.get("ma_gap_atr"), 4)
        rows.append(item)
    return rows


def _stats(signals: pd.DataFrame) -> dict[str, Any]:
    """
    Tính thống kê tổng hợp tín hiệu.

    Args:
        signals: DataFrame chỉ gồm dòng signal != 0.

    Returns:
        Dict: {total, buy, sell, last} — "last" là "BUY" hoặc "SELL".
        Nếu signals rỗng, trả về tất cả zero và last="-".
    """
    if signals.empty:
        return {"total": 0, "buy": 0, "sell": 0, "last": "-"}
    buy = int(signals["signal"].eq(1).sum())
    sell = int(signals["signal"].eq(-1).sum())
    last_signal = int(signals["signal"].iloc[-1])
    return {
        "total": int(len(signals)),
        "buy": buy,
        "sell": sell,
        "last": "BUY" if last_signal == 1 else "SELL",
    }


def build_chart_payload(
    df: pd.DataFrame,
    *,
    strategy: str,
    strategy_label: str,
    symbol: str,
    tf: str,
    bars: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """
    Tổng hợp toàn bộ JSON payload từ DataFrame chiến lược đã enriched.

    Đây là hàm chính của module — gọi tất cả hàm helper bên trên và
    trả về một dict duy nhất mà frontend app.js sẽ nhận từ /api/scan.

    Args:
        df: DataFrame đã qua add_indicators → detect_signals → add_levels.
        strategy: Key chiến lược ("combo" hoặc "ma_cross").
        strategy_label: Tên hiển thị ("Combo", "MA Cross").
        symbol: Mã symbol (uppercase).
        tf: Mã khung thời gian.
        bars: Số bar đã tải.
        params: Dict tham số đã validate (từ normalize_params).

    Returns:
        Dict JSON hoàn chỉnh gồm: meta, params, candles, overlays,
        panels, markers, levels, signals, stats.

    Giả định giao dịch:
        overlays và panels chỉ render nếu param SHOW_* tương ứng = True.
        Combo có thể có overlay MA và panel MACD + ATR.
        MA Cross có overlay fast_MA + slow_MA và panel ATR.
    """
    # Lọc chỉ các dòng có tín hiệu để dùng cho markers, levels, bảng tín hiệu
    signals = df[df["signal"].fillna(0).astype(int).ne(0)].copy()

    # --- Overlay (đường chồng lên biểu đồ giá) ---
    overlays: list[dict[str, Any]] = []
    if strategy == "combo" and params.get("SHOW_MA", True) and "ma" in df:
        overlays.append({"key": "ma", "label": f"MA {params['MA_PERIOD']}", "color": "#f59e0b", "data": _series(df, "ma")})
    if strategy == "ma_cross" and params.get("SHOW_MA", True):
        overlays.extend(
            [
                {"key": "fast_ma", "label": f"Fast {params['FAST_MA']}", "color": "#38bdf8", "data": _series(df, "fast_ma")},
                {"key": "slow_ma", "label": f"Slow {params['SLOW_MA']}", "color": "#f59e0b", "data": _series(df, "slow_ma")},
            ]
        )

    # --- Panel phụ (bảng chỉ báo bên dưới biểu đồ giá) ---
    panels: list[dict[str, Any]] = []
    if strategy == "combo" and params.get("SHOW_MACD", True) and "macd_h" in df:
        panels.append({"key": "macd", "label": "MACD Histogram", "type": "histogram", "data": _histogram(df, "macd_h")})
    if params.get("SHOW_ATR", True) and "atr" in df:
        panels.append({"key": "atr", "label": f"ATR {params['ATR_PERIOD']}", "type": "line", "color": "#a855f7", "data": _series(df, "atr")})

    return {
        "meta": {
            "strategy": strategy,
            "strategyLabel": strategy_label,
            "symbol": symbol,
            "tf": tf,
            "bars": bars,
        },
        "params": _clean_params(params),
        "candles": _candles(df),
        "overlays": overlays,
        "panels": panels,
        "markers": _markers(signals),
        "levels": _levels(df, signals, params),
        "signals": _signal_table(signals, strategy),
        "stats": _stats(signals),
    }
