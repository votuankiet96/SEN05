"""Dựng JSON cho trang chart_simul từ DataFrame và kết quả đối chiếu -- hàm thuần.

Mô tả:
    File này CHỈ biến dữ liệu đã có thành cấu trúc JSON mà trang chart vẽ: nến,
    đường chỉ báo trên giá, panel MACD histogram, các "dấu" tín hiệu và bảng tín
    hiệu. Không đọc Redis, không chạy chiến lược, không quyết định trạng thái
    đối chiếu (verify.py làm), không biết Flask.

    Các hàm dựng điểm (nến / đường / histogram) viết lại gọn từ ý tưởng của
    nhóm signal_display bên WE -- cố ý KHÔNG import từ WE vì des và WE là hai hệ
    riêng. Khác bản WE: điểm thiếu giá trị (NaN) vẫn xuất ra dạng `{time}` (điểm
    trống) để mọi series có đúng số điểm như nến -- nhờ vậy hai chart (giá và
    MACD) khớp chỉ số logic khi đồng bộ trục thời gian.

Đầu vào:
    ViewData: DataFrame đã chạy chiến lược (đúng vùng hiển thị), các Check, HASH
    tín hiệu des đã ghi, field kỳ vọng và field lệch.

    Trang trend (/trend) dùng lại build_payload rồi thêm phần xu hướng: pane khung trend (nến +
    đường của các khối đang bật, tô màu theo hướng) và các tín hiệu BỊ trend loại (build_trend_payload).

Đầu ra:
    dict JSON: candles / overlays / panels / marks / signals / stats; trang multi-timeframe
    (mtf_pane) chỉ cần nến và dấu vân tay; trang trend thêm trend / stats.filtered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
from wisdom_engine.des.src.chart_simul.verify import Check, count_by_status
from wisdom_engine.des.src.redis_io.candle_reader import STAMP_FORMAT

UP_COLOR = "#22c55e"
DOWN_COLOR = "#ef4444"
NEUTRAL_COLOR = "#94a3b8"
SIDE_TEXT = {1: "BUY", -1: "SELL"}
TREND_TEXT = {1: "UP", -1: "DOWN", 0: "NEUTRAL"}
# Trạng thái hiển thị (không do verify.py quyết): tín hiệu thô bị bộ lọc trend loại, des KHÔNG ghi vào db đã lọc.
STATUS_FILTERED = "filtered"

_EPOCH = pd.Timestamp("1970-01-01")
_ONE_SECOND = pd.Timedelta(seconds=1)
_MINUTE_FORMAT = "%Y-%m-%d %H:%M"

# Đường vẽ chồng lên chart giá: (cột, nhãn có {THAM_SỐ}, màu). Cột do
# indicator.py của des tính; nhãn lấy tham số thật từ des_config.yaml.
OVERLAYS: dict[str, list[tuple[str, str, str]]] = {
    "combo": [("ma", "MA {MA_PERIOD}", "#f59e0b")],
    "ma_cross": [
        ("fast_ma", "Fast {FAST_MA}", "#38bdf8"),
        ("slow_ma", "Slow {SLOW_MA}", "#f59e0b"),
    ],
    "macd_break": [
        ("prior_high_close", "Prior high close ({LOOKBACK_BARS})", "#38bdf8"),
        ("prior_low_close", "Prior low close ({LOOKBACK_BARS})", "#f59e0b"),
    ],
}

# Các số mà luật vào lệnh của từng chiến lược dựa vào -- hiện trong chi tiết tín
# hiệu để người xem tự kiểm luật bằng mắt (cột của chính DataFrame chiến lược).
RULE_INPUTS: dict[str, list[tuple[str, str]]] = {
    "combo": [
        ("open", "Open"),
        ("close", "Close"),
        ("ma", "MA"),
        ("prev_close", "Prev close"),
        ("macd_h", "MACD hist"),
        ("atr", "ATR"),
    ],
    "ma_cross": [
        ("fast_ma", "Fast"),
        ("slow_ma", "Slow"),
        ("macd_h", "MACD hist"),
        ("atr", "ATR"),
    ],
    "macd_break": [
        ("close", "Close"),
        ("prior_high_close", "Prior high close"),
        ("prior_low_close", "Prior low close"),
        ("macd_h", "MACD hist"),
        ("atr", "ATR"),
    ],
}


@dataclass(frozen=True)
class ViewData:
    """Mọi thứ build_payload cần để dựng một khung nhìn (đã cắt đúng vùng hiển thị)."""

    strategy: str
    symbol: str
    tf: str
    tf_minutes: int
    frame: pd.DataFrame
    params: dict[str, Any]
    checks: list[Check]
    published: dict[str, dict[str, str]]
    expected: dict[pd.Timestamp, dict[str, str]]
    diffs: dict[pd.Timestamp, list[str]]


@dataclass(frozen=True)
class TrendViewData:
    """Phần xu hướng của trang trend: khung nhìn đã đối chiếu + khung trend và cách vẽ nó."""

    view: ViewData  # frame đã chạy đường 2 khung (có raw_signal / signal / trend_bias), đối chiếu với db đã lọc trend
    trend_tf: str
    trend_frame: pd.DataFrame  # nến khung trend đã cắt vùng hiển thị + cột của các khối + trend_bias
    trend_specs: list[dict[str, Any]]  # OVERLAYS của các khối đang bật: column / label / color / colored_by_bias
    trend_label: str  # vd "KNN"
    check_from: pd.Timestamp | None  # nến sớm nhất được đối chiếu (None: des chưa ghi gì)


def unix_time(time: pd.Timestamp) -> int:
    """Giờ UTC-naive -> Unix giây (định dạng Lightweight Charts yêu cầu)."""
    return int((pd.Timestamp(time) - _EPOCH) // _ONE_SECOND)


def unix_times(times: pd.Series) -> list[int]:
    """Cả một cột giờ -> list Unix giây."""
    return [int(value) for value in (times - _EPOCH) // _ONE_SECOND]


def _number(value: Any, digits: int = 2) -> float | None:
    """Ép về float làm tròn; None nếu thiếu / NaN / không phải số."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else round(number, digits)


def candle_points(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """[{time, open, high, low, close}] cho CandlestickSeries."""
    rows = zip(
        unix_times(frame["bartime"]),
        frame["open"],
        frame["high"],
        frame["low"],
        frame["close"],
        strict=True,
    )
    return [
        {
            "time": time,
            "open": round(float(open_), 2),
            "high": round(float(high), 2),
            "low": round(float(low), 2),
            "close": round(float(close), 2),
        }
        for time, open_, high, low, close in rows
    ]


def line_points(frame: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    """[{time, value}] cho LineSeries; thiếu giá trị -> điểm trống {time}."""
    points: list[dict[str, Any]] = []
    for time, raw in zip(unix_times(frame["bartime"]), frame[column], strict=True):
        value = _number(raw)
        points.append({"time": time} if value is None else {"time": time, "value": value})
    return points


def histogram_points(frame: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    """[{time, value, color}] cho HistogramSeries (xanh khi >= 0, đỏ khi < 0)."""
    points: list[dict[str, Any]] = []
    for time, raw in zip(unix_times(frame["bartime"]), frame[column], strict=True):
        value = _number(raw, 4)
        if value is None:
            points.append({"time": time})
        else:
            points.append(
                {"time": time, "value": value, "color": UP_COLOR if value >= 0 else DOWN_COLOR}
            )
    return points


def overlay_series(
    frame: pd.DataFrame, strategy: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    """Các đường chỉ báo chồng lên chart giá của chiến lược (rỗng nếu chưa khai báo)."""
    return [
        {
            "key": column,
            "label": label.format(**params),
            "color": color,
            "data": line_points(frame, column),
        }
        for column, label, color in OVERLAYS.get(strategy, [])
        if column in frame.columns
    ]


def marks(checks: list[Check], visible_times: set[int]) -> list[dict[str, Any]]:
    """Các dấu tín hiệu nằm trong vùng đang vẽ, theo thứ tự thời gian."""
    items = [
        {"time": unix_time(check.time), "side": SIDE_TEXT[check.side], "status": check.status}
        for check in sorted(checks, key=lambda item: item.time)
    ]
    return [item for item in items if item["time"] in visible_times]


def rule_inputs(row: pd.Series, strategy: str) -> list[dict[str, Any]]:
    """Giá trị các cột mà luật vào lệnh dựa vào, tại đúng nến này."""
    return [
        {"label": label, "value": _number(row.get(column), 4)}
        for column, label in RULE_INPUTS.get(strategy, [])
        if column in row.index
    ]


def _price_pair(fields: dict[str, str] | None) -> dict[str, float | None] | None:
    """{stop, atr} đọc từ field HASH; None nếu chưa có HASH."""
    if fields is None:
        return None
    return {"stop": _number(fields.get("stopPrice")), "atr": _number(fields.get("atr"))}


def _signal_row(data: ViewData, check: Check, row: pd.Series) -> dict[str, Any]:
    des_fields = data.published.get(check.time.strftime(STAMP_FORMAT))
    chart_fields = data.expected.get(check.time)
    shown = des_fields or chart_fields or {}
    close_time = check.time + pd.Timedelta(minutes=data.tf_minutes)
    return {
        "time": unix_time(check.time),
        "bar": check.time.strftime(_MINUTE_FORMAT),
        "close": close_time.strftime(_MINUTE_FORMAT),
        "side": SIDE_TEXT[check.side],
        "status": check.status,
        "published": des_fields is not None,
        "orderType": shown.get("orderType"),
        "stop": _number(shown.get("stopPrice")),
        "atr": _number(shown.get("atr")),
        "reason": shown.get("comment", ""),
        "des": _price_pair(des_fields),
        "chart": _price_pair(chart_fields),
        "diff": data.diffs.get(check.time, []),
        "inputs": rule_inputs(row, data.strategy),
    }


def signal_rows(data: ViewData) -> list[dict[str, Any]]:
    """Bảng tín hiệu (mới nhất trước) cho mọi Check nằm trong vùng đang vẽ."""
    index_by_time = {pd.Timestamp(time): i for i, time in enumerate(data.frame["bartime"])}
    rows = []
    for check in sorted(data.checks, key=lambda item: item.time, reverse=True):
        index = index_by_time.get(check.time)
        if index is not None:
            rows.append(_signal_row(data, check, data.frame.iloc[index]))
    return rows


def build_payload(data: ViewData) -> dict[str, Any]:
    """Phần dữ liệu của khung nhìn; meta (đồng hồ DFS, mốc, ...) do server thêm."""
    frame = data.frame
    panels = []
    if "macd_h" in frame.columns:
        panels.append(
            {
                "key": "macd",
                "label": "MACD Histogram",
                "type": "histogram",
                "data": histogram_points(frame, "macd_h"),
            }
        )
    return {
        "candles": candle_points(frame),
        "overlays": overlay_series(frame, data.strategy, data.params),
        "panels": panels,
        "marks": marks(data.checks, set(unix_times(frame["bartime"]))),
        "signals": signal_rows(data),
        "stats": {**count_by_status(data.checks), "fieldDiff": len(data.diffs)},
    }


# ----------------------------------------------------------------- trang trend
def trend_text(bias: Any) -> str:
    """Hướng trend dạng chữ: UP / DOWN / NEUTRAL (NaN hay giá trị lạ coi như NEUTRAL)."""
    number = _number(bias, 0)
    return TREND_TEXT.get(int(number) if number is not None else 0, "NEUTRAL")


def trend_line_points(
    frame: pd.DataFrame, column: str, *, bias_column: str = "trend_bias"
) -> list[dict[str, Any]]:
    """[{time, value, color}] cho đường xu hướng: xanh khi hướng +1, đỏ khi -1, xám khi 0/thiếu.

    Màu theo cột hướng ĐÃ ghép các khối (trend_bias), không theo tín hiệu vào lệnh. Thiếu giá trị -> điểm trống.
    """
    points: list[dict[str, Any]] = []
    for time, raw, bias in zip(
        unix_times(frame["bartime"]), frame[column], frame[bias_column], strict=True
    ):
        value = _number(raw)
        if value is None:
            points.append({"time": time})
            continue
        direction = _number(bias, 0)
        color = UP_COLOR if direction == 1 else DOWN_COLOR if direction == -1 else NEUTRAL_COLOR
        points.append({"time": time, "value": value, "color": color})
    return points


def trend_overlays(frame: pd.DataFrame, specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Các đường của khối xu hướng trên pane khung trend, theo khai báo OVERLAYS của từng khối."""
    series: list[dict[str, Any]] = []
    for spec in specs:
        column = spec["column"]
        if column not in frame.columns:
            continue
        colored = spec.get("colored_by_bias") and "trend_bias" in frame.columns
        series.append(
            {
                "key": column,
                "label": spec["label"],
                "color": spec["color"],
                "data": trend_line_points(frame, column) if colored else line_points(frame, column),
            }
        )
    return series


def trend_change_marks(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Điểm trend đổi sang UP/DOWN trong vùng đang vẽ (nến đầu vùng không tính: chưa biết hướng trước đó)."""
    if "trend_bias" not in frame.columns or frame.empty:
        return []
    bias = pd.to_numeric(frame["trend_bias"], errors="coerce").fillna(0).astype(int)
    previous = bias.shift(1)
    changed = previous.notna() & bias.ne(previous) & bias.ne(0)
    return [
        {"time": time, "bias": int(direction)}
        for time, direction in zip(unix_times(frame["bartime"][changed]), bias[changed], strict=True)
    ]


def trend_pane(data: TrendViewData) -> dict[str, Any]:
    """Pane khung trend: nến + đường của các khối (tô màu theo hướng) + điểm đổi hướng."""
    frame = data.trend_frame
    return {
        "tf": data.trend_tf,
        "label": data.trend_label,
        "current": trend_text(frame["trend_bias"].iloc[-1]) if len(frame) else "NEUTRAL",
        "candles": candle_points(frame),
        "overlays": trend_overlays(frame, data.trend_specs),
        "marks": trend_change_marks(frame),
    }


def _filtered_row(data: ViewData, row: pd.Series, trend_tf: str) -> dict[str, Any]:
    """Dòng bảng cho tín hiệu thô bị trend loại (des không ghi nó vào db đã lọc). `trend_tf` = khung trend, KHÔNG phải khung vào lệnh."""
    time = pd.Timestamp(row["bartime"])
    side = SIDE_TEXT[1 if int(row["raw_signal"]) > 0 else -1]
    trend = trend_text(row.get("trend_bias"))
    return {
        "time": unix_time(time),
        "bar": time.strftime(_MINUTE_FORMAT),
        "close": (time + pd.Timedelta(minutes=data.tf_minutes)).strftime(_MINUTE_FORMAT),
        "side": side,
        "status": STATUS_FILTERED,
        "published": False,
        "orderType": None,
        "stop": None,
        "atr": _number(row.get("atr")),
        "reason": f"{side} setup dropped: trend {trend_tf} filter reads {trend}",
        "des": None,
        "chart": None,
        "diff": [],
        "inputs": rule_inputs(row, data.strategy),
        "trend": trend,
    }


def filtered_rows(data: TrendViewData) -> list[dict[str, Any]]:
    """Tín hiệu thô (raw_signal) mà bộ lọc trend loại (signal = 0), chỉ trong vùng des đã có thể ghi (từ check_from).

    Bỏ nến đã có Check (đã ghi hoặc đang đối chiếu): một nến không thể vừa "được ghi" vừa "bị loại" trên cùng một dấu.
    """
    view = data.view
    if data.check_from is None or view.frame.empty:
        return []
    frame = view.frame
    raw = pd.to_numeric(frame["raw_signal"], errors="coerce").fillna(0).astype(int)
    kept = pd.to_numeric(frame["signal"], errors="coerce").fillna(0).astype(int)
    taken = {check.time for check in view.checks}
    dropped = frame[
        raw.ne(0)
        & kept.eq(0)
        & (pd.to_datetime(frame["bartime"]) >= data.check_from)
        & ~frame["bartime"].isin(taken)
    ]
    return [_filtered_row(view, row, data.trend_tf) for _, row in dropped.iterrows()]


def build_trend_payload(data: TrendViewData) -> dict[str, Any]:
    """Khung nhìn của trang trend: build_payload của đường 2 khung + pane trend + tín hiệu bị trend loại.

    Mỗi dòng bảng có thêm `trend` (hướng trend khung lớn tại nến đó); dấu `filtered` và dòng `filtered` nằm
    chung với dấu/dòng đối chiếu, sắp theo thời gian như trang chính.
    """
    base = build_payload(data.view)
    dropped = filtered_rows(data)
    bias_by_time = dict(
        zip(unix_times(data.view.frame["bartime"]), data.view.frame["trend_bias"], strict=True)
    )
    signals = [{**row, "trend": trend_text(bias_by_time.get(row["time"]))} for row in base["signals"]]
    signals = sorted([*signals, *dropped], key=lambda item: item["time"], reverse=True)
    dropped_marks = [{"time": row["time"], "side": row["side"], "status": STATUS_FILTERED} for row in dropped]
    return {
        **base,
        "marks": sorted([*base["marks"], *dropped_marks], key=lambda item: item["time"]),
        "signals": signals,
        "stats": {**base["stats"], STATUS_FILTERED: len(dropped)},
        "trend": trend_pane(data),
    }


def mtf_pane(tf: str, tf_minutes: int, frame: pd.DataFrame) -> dict[str, Any]:
    """Một khung của trang multi-timeframe: CHỈ nến + dấu vân tay (stamp nến mới nhất).

    `head` đúng định dạng stamp của key DFS để trình duyệt gửi lại nguyên văn, và
    server so với stamp đầu LIST. Khung chưa có nến nào (DFS chưa phát tới) vẫn
    ra đủ khoá, `candles` rỗng.
    """
    if frame.empty:
        return {
            "tf": tf,
            "tfSeconds": tf_minutes * 60,
            "head": "",
            "lastCandle": None,
            "count": 0,
            "candles": [],
        }
    last = pd.Timestamp(frame["bartime"].iloc[-1])
    return {
        "tf": tf,
        "tfSeconds": tf_minutes * 60,
        "head": last.strftime(STAMP_FORMAT),
        "lastCandle": last.strftime(_MINUTE_FORMAT),
        "count": len(frame),
        "candles": candle_points(frame),
    }
