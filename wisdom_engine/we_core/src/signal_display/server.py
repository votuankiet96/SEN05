"""
Dashboard sống cho wisdom_engine: Flask app phục vụ trang tương tác + API scan.

Mô tả:
    File DUY NHẤT trong dashboard sống biết cả tầng dữ liệu/chiến lược
    (wisdom_engine.we_core.src.db_connector, wisdom_engine.we_core.src.configuration) lẫn tầng hiển thị
    (payload.py, live_page.py)
    — đúng vai trò "wiring" mà cli.py đang làm cho chế độ ghi file, chỉ khác
    là phục vụ qua HTTP thay vì ghi ra đĩa. Tái dùng validate/clamp dùng
    chung từ cli.py (resolve_symbol_tf, clamp_bars) — không import ngược lại
    từ cli.py sang đây.

    CSV export ở đây CỐ Ý tối giản và đang cùng khuôn với export_cli.py.
    Nếu sau này muốn CSV đầy đủ hơn (reason, level tham chiếu...), nên mở rộng
    cả export_cli.py và endpoint /api/export cùng lúc để dashboard và terminal
    không lệch nhau.

Chạy:
    python -m wisdom_engine.we_core.src.signal_display.server --port 8517
"""

from __future__ import annotations

import argparse
import gzip
from threading import Lock
from typing import Any

import pandas as pd
from flask import Flask, Response, jsonify, request
from wisdom_engine.we_core.src.configuration import (
    DEFAULT_SYMBOL,
    N_BARS,
    OPERATING_SYMBOLS,
    STRATEGIES,
    WARMUP_BARS,
    check_trend_timeframe,
    get_strategy,
    price_decimals,
    run_strategy,
    run_strategy_with_trend_reference,
)
from wisdom_engine.we_core.src.db_connector import (
    load,
    load_range_with_warmup,
    symbols,
    tf_minutes,
)
from wisdom_engine.we_core.src.indicator import timeframe_minutes
from wisdom_engine.we_core.src.rules.trend_tf import (
    bias_column,
    build_trend_frame,
    get_method,
    is_same_timeframe,
    method_names,
)
from wisdom_engine.we_core.src.signal_display.cli import (
    BARS_MAX,
    BARS_MIN,
    clamp_bars,
    resolve_symbol_tf,
)
from wisdom_engine.we_core.src.signal_display.live_page import render_dashboard_html
from wisdom_engine.we_core.src.signal_display.payload import (
    candlestick_points,
    heikin_ashi_points,
    histogram_points,
    line_points,
    signal_markers,
    signal_table_rows,
    stats_summary,
    trend_bias_markers,
    trend_line_points,
)
from wisdom_engine.we_core.src.we_signal.export_cli import to_csv as signal_csv

DEFAULT_HOST = "127.0.0.1"
# 8517, không phải 8516 -- 8516 là cổng dashboard order_gateway (xem
# CLAUDE.md mục 7). 2 hệ thống có thể chạy dashboard cùng lúc trên cùng VM,
# cố ý tách cổng để không đụng nhau.
DEFAULT_PORT = 8517
TREND_MODE_NO_TREND = "no_trend"
TREND_MODE_FILTER = "trend_filter"
# Khóa này giúp các request dashboard không bắn nhiều truy vấn SQL đồng thời.
# Khi dashboard auto-refresh hoặc nhiều tab mở cùng lúc, từng request sẽ vào
# hàng đợi ngắn thay vì cùng lúc tranh kết nối SQL.
_DB_LOCK = Lock()

# Phản hồi JSON/HTML từ 1 KB trở lên được nén gzip khi trình duyệt cho phép. Đo trên payload thật
# (2026-10-15): /api/scan 500 nến 95 KB -> 16 KB, 2.000 nến có trend 486 KB -> 81 KB, 20.000 nến
# 4,8 MB -> 0,76 MB, trang chủ 194 KB -> 59 KB; nén mức 5 tốn ~1 ms (500 nến), ~7 ms (2.000), ~74 ms
# (20.000); mức 9 chậm gấp 4–5 lần mà chỉ nhỉnh thêm ~3%. Có ích khi mở dashboard qua mạng/đường hầm;
# truy cập cùng máy (127.0.0.1) không thấy khác biệt.
_GZIP_MIMETYPES = frozenset({"application/json", "text/html"})
_GZIP_MIN_BYTES = 1024
_GZIP_LEVEL = 5

# Cột overlay riêng theo từng strategy: (cột df, mẫu label, màu). Label được
# format bằng params đã normalize (vd "MA {MA_PERIOD}" -> "MA 20").
_STRATEGY_OVERLAYS: dict[str, list[tuple[str, str, str]]] = {
    "combo": [("ma", "MA {MA_PERIOD}", "#f59e0b")],
    "ma_cross": [
        ("fast_ma", "Fast {FAST_MA}", "#38bdf8"),
        ("slow_ma", "Slow {SLOW_MA}", "#f59e0b"),
    ],
    "macd_break": [
        ("prior_high_close", "High close {LOOKBACK_BARS}", "#22c55e"),
        ("prior_low_close", "Low close {LOOKBACK_BARS}", "#ef4444"),
    ],
}


_TREND_CONTROL_KEYS = {
    "trend_mode",
    "trend_methods",
    "trend_tf",
    "TREND_FILTER_ENABLED",
    "TREND_METHODS",
    "TREND_TF",
    "ENTRY_TF",
}


def _parse_bars(raw: str | None) -> int:
    """Parse tham số bars từ query string, kẹp qua cli.clamp_bars()."""
    try:
        value = int(raw) if raw else N_BARS
    except (TypeError, ValueError):
        value = N_BARS
    return clamp_bars(value)


def _parse_before(raw: str | None) -> pd.Timestamp | None:
    """Parse Unix UTC cursor của request lịch sử; rỗng nghĩa là scan mới nhất."""
    if raw is None or not str(raw).strip():
        return None
    try:
        seconds = int(raw)
        if seconds <= 0:
            raise ValueError
        return pd.to_datetime(seconds, unit="s", utc=True).tz_localize(None)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("before must be a positive Unix timestamp") from exc


def _parse_range_time(raw: str | None, key: str) -> pd.Timestamp | None:
    """Parse mốc thời gian range từ input HTML/API thành UTC-naive Timestamp."""
    if raw is None or not str(raw).strip():
        return None
    try:
        ts = pd.Timestamp(str(raw).strip())
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{key} must be a valid datetime") from exc
    if pd.isna(ts):
        raise ValueError(f"{key} must be a valid datetime")
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts


def _parse_time_window(
    from_raw: str | None,
    to_raw: str | None,
    range_raw: str | None = None,
) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """
    Parse cặp thời gian range của dashboard.

    UI gửi `from_time/to_time` dạng text nhập tay. API vẫn nhận `time_range` dạng:
        2026-01-01 00:00 -> 2026-01-02 00:00

    `time_range` chỉ là tương thích thêm cho request ngoài UI.
    """
    range_text = str(range_raw or "").strip()
    if range_text:
        if str(from_raw or "").strip() or str(to_raw or "").strip():
            raise ValueError("time_range cannot be combined with from_time/to_time")
        from_raw, to_raw = _split_time_range(range_text)

    from_time = _parse_range_time(from_raw, "from_time")
    to_time = _parse_range_time(to_raw, "to_time")
    if (from_time is None) != (to_time is None):
        raise ValueError("from_time and to_time must be set together")
    if from_time is not None and to_time is not None and from_time > to_time:
        raise ValueError("from_time must be earlier than or equal to to_time")
    return from_time, to_time


def _split_time_range(raw: str) -> tuple[str, str]:
    """Tách 1 ô text range thành 2 mốc thời gian."""
    separators = ("->", "..", "|", ",")
    for separator in separators:
        if separator in raw:
            left, right = raw.split(separator, 1)
            break
    else:
        marker = " to "
        lowered = raw.lower()
        if marker not in lowered:
            raise ValueError("time_range must contain '->' between from and to")
        index = lowered.index(marker)
        left, right = raw[:index], raw[index + len(marker) :]

    left, right = left.strip(), right.strip()
    if not left or not right:
        raise ValueError("time_range must contain both from and to")
    return left, right


def _parse_trend_mode(raw: str | None) -> str:
    """
    Chuẩn hóa mode trend của dashboard.

    no_trend: chạy chart/signal như strategy gốc.
    trend_filter: dùng trend reference khung lớn để lọc signal.
    """
    value = str(raw or "").strip().lower().replace("-", "_")
    if value in {"", "0", "false", "no", "none", "off", "nt", TREND_MODE_NO_TREND}:
        return TREND_MODE_NO_TREND
    if value in {"1", "true", "yes", "on", "trend", "tf", TREND_MODE_FILTER}:
        return TREND_MODE_FILTER
    raise ValueError("trend_mode must be 'no_trend' or 'trend_filter'")


def _clean_strategy_overrides(overrides: dict[str, str] | None) -> dict[str, str]:
    """
    Bỏ các key điều khiển trend/display khỏi override tham số strategy.

    Trend mode là lựa chọn của dashboard, không phải field nhập tự do trong
    nhóm tham số indicator/entry.
    """
    return {
        key: value
        for key, value in (overrides or {}).items()
        if key not in _TREND_CONTROL_KEYS and str(value).strip()
    }


def _trend_query_bars(*, entry_bars: int, entry_tf: str, trend_tf: str) -> int:
    """
    Ước lượng số nến trend cần load để phủ cùng cửa sổ thời gian entry chart.
    """
    entry_minutes = timeframe_minutes(entry_tf)
    trend_minutes = timeframe_minutes(trend_tf)
    display_bars = max(1, -(-entry_bars * entry_minutes // trend_minutes))
    return clamp_bars(display_bars + WARMUP_BARS)


def _filter_time_window(
    df: pd.DataFrame,
    *,
    from_time: pd.Timestamp | None,
    to_time: pd.Timestamp | None,
) -> pd.DataFrame:
    """Chỉ giữ phần DataFrame nằm trong range người dùng chọn."""
    if from_time is None or to_time is None or df.empty or "bartime" not in df.columns:
        return df.reset_index(drop=True)
    bartime = pd.to_datetime(df["bartime"])
    return df.loc[bartime.ge(from_time) & bartime.le(to_time)].reset_index(drop=True)


def _trim_trend_for_entry(trend_df: pd.DataFrame, entry_df: pd.DataFrame) -> pd.DataFrame:
    """
    Giữ phần trend gần vùng entry chart để panel khung lớn không dài quá mức.
    """
    if trend_df.empty or entry_df.empty or "bartime" not in trend_df.columns or "bartime" not in entry_df.columns:
        return trend_df.reset_index(drop=True)

    entry_start = pd.to_datetime(entry_df["bartime"]).min()
    entry_end = pd.to_datetime(entry_df["bartime"]).max()
    trend_time = pd.to_datetime(trend_df["bartime"])
    visible = trend_df.loc[trend_time.le(entry_end)].copy()
    if visible.empty:
        return trend_df.tail(BARS_MIN).reset_index(drop=True)

    if "trend_close_time" in visible.columns:
        trend_close = pd.to_datetime(visible["trend_close_time"])
        overlap = visible.loc[trend_close.ge(entry_start)].copy()
        if not overlap.empty:
            return overlap.reset_index(drop=True)

    return visible.tail(BARS_MIN).reset_index(drop=True)


def _build_trend_chart(
    trend_df: pd.DataFrame,
    *,
    symbol: str,
    trend_tf: str,
    trend_methods: tuple[str, ...],
) -> dict[str, Any]:
    """
    Chuyển DataFrame trend đã enrich thành payload chart riêng cho dashboard.

    Mỗi khối xác nhận xu hướng đang bật tự khai báo đường vẽ của nó (OVERLAYS trong
    trend_methods/<tên>.py); marker đổi hướng dùng cột trend_bias đã ghép các khối.
    """
    label = f"{symbol} {trend_tf} {'+'.join(name.upper() for name in trend_methods)} Trend"
    digits = price_decimals(symbol)
    overlays = []
    for name in trend_methods:
        for line in get_method(name).OVERLAYS:
            column = line["column"]
            data = (
                trend_line_points(trend_df, column, bias_column=bias_column(name), digits=digits)
                if line["colored_by_bias"]
                else line_points(trend_df, column, digits=digits)
            )
            overlays.append({"key": column, "label": line["label"], "color": line["color"], "data": data})
    return {
        "key": "trend",
        "label": label,
        "tf": trend_tf,
        "candles": heikin_ashi_points(trend_df, digits=digits),  # pane xu hướng vẽ nến Heikin Ashi (chỉ là cách vẽ); chart vào lệnh giữ nến thường
        "candleStyle": "heikin_ashi",
        "overlays": overlays,
        "markers": trend_bias_markers(trend_df),
        "panels": [],
    }


def _build_scan_payload(
    df,
    *,
    strategy_key: str,
    strategy_label: str,
    symbol: str,
    tf: str,
    bars: int,
    params: dict[str, Any],
    has_more: bool,
    trend_mode: str,
    trend_methods: str,
    trend_tf: str,
    trend_chart: dict[str, Any] | None,
    from_time: pd.Timestamp | None,
    to_time: pd.Timestamp | None,
) -> dict[str, Any]:
    """
    Lắp payload JSON đầy đủ cho /api/scan từ DataFrame đã chạy strategy.

    Payload này là "ngôn ngữ chung" giữa backend và dashboard:
        - candles: dữ liệu nến để vẽ chart giá.
        - overlays: đường indicator nằm trên chart giá, ví dụ MA/SMA.
        - panels: indicator nằm ở chart phụ, hiện chỉ có MACD Histogram.
        - markers: mũi tên BUY/SELL.
        - signals: bảng tín hiệu bên dưới chart.
        - stats: thống kê nhanh số lượng BUY/SELL.
    """
    digits = price_decimals(symbol)
    candles = candlestick_points(df, digits=digits)
    overlays = [
        {"key": column, "label": label.format(**params), "color": color, "data": line_points(df, column, digits=digits)}
        for column, label, color in _STRATEGY_OVERLAYS.get(strategy_key, [])
    ]
    panels = []
    if "macd_h" in df.columns:
        panels.append(
            {
                "key": "macd",
                "label": "MACD Histogram",
                "type": "histogram",
                "data": histogram_points(df, "macd_h", digits=digits),
            }
        )
    return {
        "meta": {
            "strategy": strategy_key,
            "strategyLabel": strategy_label,
            "symbol": symbol,
            "priceDigits": digits,
            "tf": tf,
            "bars": bars,
            "hasMore": has_more,
            "trendMode": trend_mode,
            "trendMethods": trend_methods,
            "trendTf": trend_tf,
            "timeMode": "range" if from_time is not None and to_time is not None else "latest",
            "fromTime": from_time.strftime("%Y-%m-%d %H:%M") if from_time is not None else "",
            "toTime": to_time.strftime("%Y-%m-%d %H:%M") if to_time is not None else "",
        },
        "trendChart": trend_chart,
        "candles": candles,
        "overlays": overlays,
        "panels": panels,
        "markers": signal_markers(df),
        "signals": signal_table_rows(df, digits=digits),
        "stats": stats_summary(df),
    }


def _load_and_run(
    strategy: str,
    symbol: str,
    tf: str,
    bars: int,
    overrides: dict[str, str] | None,
    before: pd.Timestamp | None = None,
    trend_mode: str = TREND_MODE_NO_TREND,
    trend_methods: str = "",
    trend_tf: str = "",
    from_time: pd.Timestamp | None = None,
    to_time: pd.Timestamp | None = None,
):
    """
    Validate request + load OHLCV + chạy strategy — dùng chung cho scan/export.

    Đây là lõi backend của dashboard. Cả nút Refresh và nút Export đều đi
    qua hàm này để bảo đảm dữ liệu chart và dữ liệu CSV được tính cùng cách.

    Raises:
        KeyError: strategy hoặc symbol không tồn tại.
        ValueError: timeframe ngoài supported_timeframes của strategy.
    """
    spec = get_strategy(strategy)
    symbol, tf = resolve_symbol_tf(strategy, symbol, tf)
    # Kiểm tf hợp lệ TRƯỚC khi gọi normalize_params() -- từ khi KSL/KTP tra
    # theo (symbol, tf), normalize_params có thể tự raise KeyError cho tf
    # sai, che mất đúng ValueError "supports only..." mà caller mong đợi.
    if spec.supported_timeframes and tf not in spec.supported_timeframes:
        allowed = ", ".join(spec.supported_timeframes)
        raise ValueError(f"Strategy '{spec.key}' supports only these timeframes: {allowed}.")
    bars = clamp_bars(bars)
    query_bars = bars if before is None else bars * 2
    strategy_overrides = _clean_strategy_overrides(overrides)
    trend_mode = _parse_trend_mode(trend_mode)
    use_range = from_time is not None and to_time is not None
    if use_range and before is not None:
        raise ValueError("before cannot be combined with from_time/to_time")

    if trend_mode == TREND_MODE_NO_TREND:
        run_overrides = {**strategy_overrides, "TREND_FILTER_ENABLED": False}
        params = spec.normalize_params(run_overrides, symbol, tf)
        with _DB_LOCK:
            if use_range:
                raw = load_range_with_warmup(symbol, tf, from_time, to_time, WARMUP_BARS)
            elif before is None:
                raw = load(symbol, tf, query_bars)
            else:
                raw = load(symbol, tf, query_bars, before=before)
            enriched = run_strategy(spec.key, symbol=symbol, tf=tf, bars=raw, overrides=run_overrides)
        has_more = False if use_range else len(raw) >= bars if before is None else len(raw) > bars
        if before is not None and has_more:
            enriched = enriched.iloc[-bars:].reset_index(drop=True)
        enriched = _filter_time_window(enriched, from_time=from_time, to_time=to_time)
        return spec, symbol, tf, bars, enriched, has_more, params, None

    trend_request = {**strategy_overrides, "TREND_FILTER_ENABLED": True, "ENTRY_TF": tf}
    if trend_methods:
        trend_request["TREND_METHODS"] = trend_methods
    if trend_tf:
        trend_request["TREND_TF"] = trend_tf
    params = spec.normalize_params(trend_request, symbol, tf)
    trend_methods_used = tuple(params["TREND_METHODS"])
    trend_tf = str(params["TREND_TF"])
    if not trend_tf:
        raise ValueError(f"No trend timeframe configured for {symbol} {tf}; choose Trend TF.")
    check_trend_timeframe(tf, trend_tf)  # khung trend >= khung tín hiệu; báo lỗi TRƯỚC khi tải SQL
    trend_query_bars = _trend_query_bars(entry_bars=query_bars, entry_tf=tf, trend_tf=trend_tf)

    with _DB_LOCK:
        if use_range:
            raw = load_range_with_warmup(symbol, tf, from_time, to_time, WARMUP_BARS)
            trend_raw = load_range_with_warmup(symbol, trend_tf, from_time, to_time, WARMUP_BARS)
        elif before is None:
            raw = load(symbol, tf, query_bars)
            trend_raw = load(symbol, trend_tf, trend_query_bars)
        else:
            raw = load(symbol, tf, query_bars, before=before)
            trend_raw = load(symbol, trend_tf, trend_query_bars, before=before)
        enriched = run_strategy_with_trend_reference(
            spec.key,
            symbol=symbol,
            entry_tf=tf,
            entry_bars=raw,
            trend_tf=trend_tf,
            trend_bars=trend_raw,
            overrides=trend_request,
        )
        trend_enriched = build_trend_frame(trend_raw, params)
    has_more = False if use_range else len(raw) >= bars if before is None else len(raw) > bars
    if before is not None and has_more:
        enriched = enriched.iloc[-bars:].reset_index(drop=True)
    enriched = _filter_time_window(enriched, from_time=from_time, to_time=to_time)
    trend_chart = _build_trend_chart(
        _trim_trend_for_entry(trend_enriched, enriched),
        symbol=symbol,
        trend_tf=trend_tf,
        trend_methods=trend_methods_used,
    )
    return spec, symbol, tf, bars, enriched, has_more, params, trend_chart


def run_scan(
    *,
    strategy: str,
    symbol: str,
    tf: str,
    bars: int,
    overrides: dict[str, str] | None = None,
    before: pd.Timestamp | None = None,
    trend_mode: str = TREND_MODE_NO_TREND,
    trend_methods: str = "",
    trend_tf: str = "",
    from_time: pd.Timestamp | None = None,
    to_time: pd.Timestamp | None = None,
) -> dict[str, Any]:
    """
    Chạy 1 lượt scan đầy đủ cho dashboard.

    Luồng xử lý:
        1. Lấy lựa chọn hiện tại từ dashboard.
        2. Load dữ liệu nến từ SQL Server.
        3. Chạy strategy.
        4. Chuyển kết quả thành JSON để trình duyệt vẽ chart.
    """
    spec, symbol, tf, bars, enriched, has_more, params, trend_chart = _load_and_run(
        strategy,
        symbol,
        tf,
        bars,
        overrides,
        before,
        trend_mode,
        trend_methods,
        trend_tf,
        from_time,
        to_time,
    )
    parsed_trend_mode = _parse_trend_mode(trend_mode)
    return _build_scan_payload(
        enriched,
        strategy_key=spec.key,
        strategy_label=spec.label,
        symbol=symbol,
        tf=tf,
        bars=bars,
        params=params,
        has_more=has_more,
        trend_mode=parsed_trend_mode,
        trend_methods="+".join(params["TREND_METHODS"]) if parsed_trend_mode == TREND_MODE_FILTER else "",
        trend_tf=str(params["TREND_TF"]) if parsed_trend_mode == TREND_MODE_FILTER else "",
        trend_chart=trend_chart,
        from_time=from_time,
        to_time=to_time,
    )


def export_csv(
    *,
    strategy: str,
    symbol: str,
    tf: str,
    bars: int,
    overrides: dict[str, str] | None = None,
    trend_mode: str = TREND_MODE_NO_TREND,
    trend_methods: str = "",
    trend_tf: str = "",
    from_time: pd.Timestamp | None = None,
    to_time: pd.Timestamp | None = None,
) -> tuple[str, str]:
    """
    Chạy strategy rồi trả CSV text và tên file cho nút Export trên dashboard.

    Chỉ các bar có tín hiệu mới được đưa vào CSV. Schema được dùng chung với
    export_cli.py: MA Cross có bartime/atr/signal; Combo thêm entry pending.
    """
    spec, symbol, tf, bars, enriched, _has_more, params, _trend_chart = _load_and_run(
        strategy,
        symbol,
        tf,
        bars,
        overrides,
        trend_mode=trend_mode,
        trend_methods=trend_methods,
        trend_tf=trend_tf,
        from_time=from_time,
        to_time=to_time,
    )
    parsed_trend_mode = _parse_trend_mode(trend_mode)
    suffix = "_nt" if parsed_trend_mode == TREND_MODE_NO_TREND else f"_tf_{'+'.join(params['TREND_METHODS'])}_{params['TREND_TF']}"
    if from_time is not None and to_time is not None:
        window = f"{from_time:%Y%m%d_%H%M}_{to_time:%Y%m%d_%H%M}"
    else:
        window = f"latest_{bars}bars"
    filename = f"{spec.key}_{symbol}_{tf}_{window}_signals{suffix}.csv"
    return signal_csv(enriched, strategy=spec.key, decimals=price_decimals(symbol)), filename


_REQUEST_KEYS = {"strategy", "symbol", "tf", "bars", "before", "from_time", "to_time", "time_range"} | _TREND_CONTROL_KEYS


def _extract_overrides(args) -> dict[str, str]:
    """
    Tách tham số strategy override khỏi query string.

    Dashboard gửi chung mọi thứ trên URL, ví dụ:
        strategy=ma_cross&symbol=US30&tf=M30&bars=500&FAST_MA=13

    Hàm này bỏ các key request chuẩn (strategy/symbol/tf/bars/before), phần còn lại
    được xem là tham số riêng của strategy.

    Không cần allowlist ở đây vì normalize_params() của mỗi strategy chỉ đọc
    đúng key nó biết; key lạ bị bỏ qua an toàn.
    """
    return {k: v for k, v in args.items() if k not in _REQUEST_KEYS and str(v).strip()}


def _strategy_trend_timeframes(spec, ordered_timeframes: list[str]) -> list[str]:
    """
    Suy ra danh sách trend TF hợp lệ qua normalize_params của strategy, cho khối cần KHUNG THAM CHIẾU riêng (vd knn).
    Phải chọn khối đó tường minh: khối mặc định trong config (breakout, tính trên chính khung của tín hiệu) không có khung
    tham chiếu nên sẽ từ chối mọi khung khác khung vào lệnh, và danh sách này dùng cho ô Trend TF khi người dùng chọn knn.
    """
    entry_tf = spec.default_timeframe or (spec.supported_timeframes or spec.recommended_timeframes or ordered_timeframes)[0]
    reference_methods = [name for name in method_names() if not is_same_timeframe(name)]
    allowed: list[str] = []
    for tf in ordered_timeframes:
        try:
            spec.normalize_params(
                {"TREND_FILTER_ENABLED": True, "TREND_METHODS": ",".join(reference_methods[:1]), "TREND_TF": tf, "ENTRY_TF": entry_tf},
                DEFAULT_SYMBOL,
                entry_tf,
            )
        except (KeyError, ValueError):
            continue
        allowed.append(tf)
    return allowed


def _gzip_response(response: Response) -> Response:
    """
    Nén gzip phản hồi JSON/HTML lớn nếu trình duyệt chấp nhận (Accept-Encoding: gzip).

    Không nén: phản hồi lỗi (status khác 200), đã mã hoá sẵn, dạng luồng, hoặc dưới
    _GZIP_MIN_BYTES. CSV tải về của /api/export cố ý để nguyên. Mọi phản hồi thuộc kiểu có
    thể nén đều ghi Vary: Accept-Encoding để cache trung gian không lẫn bản nén với bản thô.
    """
    if response.mimetype not in _GZIP_MIMETYPES:
        return response
    response.vary.add("Accept-Encoding")
    if (
        response.status_code != 200
        or response.direct_passthrough
        or "Content-Encoding" in response.headers
        or not request.accept_encodings["gzip"]
    ):
        return response
    body = response.get_data()
    if len(body) < _GZIP_MIN_BYTES:
        return response
    response.set_data(gzip.compress(body, compresslevel=_GZIP_LEVEL, mtime=0))
    response.headers["Content-Encoding"] = "gzip"
    return response


def _operational_symbols() -> list[str]:
    """
    Symbol hiện trong ô chọn của dashboard: giao của DWH.Dim_Symbol với vũ trụ vận hành
    (khoá strategies.combo.symbols — đúng tập worker Redis chạy, mục 7 CLAUDE.md).

    DWH còn 26 cặp forex mà worker không chạy hết (không có tín hiệu để đối chiếu): chỉ 4 cặp EURUSD, USDJPY,
    GBPUSD, USDCAD (từ 2026-10-20) nằm trong vũ trụ vận hành. Giá của chúng làm tròn
    theo configuration.price_decimals (5 chữ số, JPY 3) ở payload và trục giá của chart, nên không còn phẳng
    như khi làm tròn 2 chữ số. Chỉ giới hạn danh sách trên trang: /api/scan vẫn nhận mọi symbol hợp lệ nếu
    gọi thẳng bằng URL (symbol ngoài vũ trụ vận hành vẫn làm tròn 2 chữ số).
    """
    return sorted(symbol for symbol in symbols() if symbol in OPERATING_SYMBOLS)


def create_app() -> Flask:
    app = Flask(__name__)
    app.after_request(_gzip_response)

    @app.get("/")
    def index():
        tf_map = tf_minutes()
        ordered_timeframes = sorted(tf_map, key=tf_map.get)
        strategies = {
            key: {
                "label": spec.label,
                "recommendedTimeframes": list(spec.recommended_timeframes),
                "supportedTimeframes": list(spec.supported_timeframes),
                "defaultTimeframe": spec.default_timeframe,
                "paramFields": spec.param_fields,
                "defaultParams": spec.default_params,
                "trendTimeframes": _strategy_trend_timeframes(spec, ordered_timeframes),
                "trendTfBySymbol": spec.trend_tf_by_symbol,
                "defaultTrendMethods": ",".join(spec.default_params.get("TREND_METHODS", ("knn",))),
            }
            for key, spec in STRATEGIES.items()
        }
        html = render_dashboard_html(
            strategies=strategies,
            symbols=_operational_symbols(),
            timeframes=ordered_timeframes,
            trend_methods=list(method_names()),
            same_timeframe_trend_methods=[name for name in method_names() if is_same_timeframe(name)],
            default_strategy="combo",
            default_symbol=DEFAULT_SYMBOL,
            default_bars=N_BARS,
            bars_min=BARS_MIN,
            bars_max=BARS_MAX,
        )
        return Response(html, mimetype="text/html")

    @app.get("/api/scan")
    def api_scan():
        try:
            from_time, to_time = _parse_time_window(
                request.args.get("from_time"),
                request.args.get("to_time"),
                request.args.get("time_range"),
            )
            payload = run_scan(
                strategy=request.args.get("strategy", "combo"),
                symbol=request.args.get("symbol", DEFAULT_SYMBOL),
                tf=request.args.get("tf", ""),
                bars=_parse_bars(request.args.get("bars")),
                overrides=_extract_overrides(request.args),
                before=_parse_before(request.args.get("before")),
                trend_mode=request.args.get("trend_mode", TREND_MODE_NO_TREND),
                trend_methods=request.args.get("trend_methods", ""),
                trend_tf=request.args.get("trend_tf", ""),
                from_time=from_time,
                to_time=to_time,
            )
            return jsonify(payload)
        except (KeyError, ValueError) as exc:
            return jsonify({"error": str(exc).strip('"')}), 400
        except Exception as exc:  # noqa: BLE001 -- Biên HTTP: trả lỗi JSON gọn cho dashboard.
            return jsonify({"error": str(exc)}), 500

    @app.get("/api/export")
    def api_export():
        try:
            from_time, to_time = _parse_time_window(
                request.args.get("from_time"),
                request.args.get("to_time"),
                request.args.get("time_range"),
            )
            csv_text, filename = export_csv(
                strategy=request.args.get("strategy", "combo"),
                symbol=request.args.get("symbol", DEFAULT_SYMBOL),
                tf=request.args.get("tf", ""),
                bars=_parse_bars(request.args.get("bars")),
                overrides=_extract_overrides(request.args),
                trend_mode=request.args.get("trend_mode", TREND_MODE_NO_TREND),
                trend_methods=request.args.get("trend_methods", ""),
                trend_tf=request.args.get("trend_tf", ""),
                from_time=from_time,
                to_time=to_time,
            )
            return Response(
                csv_text,
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )
        except (KeyError, ValueError) as exc:
            return jsonify({"error": str(exc).strip('"')}), 400
        except Exception as exc:  # noqa: BLE001 -- Biên HTTP: trả lỗi JSON gọn cho dashboard.
            return jsonify({"error": str(exc)}), 500

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    return app


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the wisdom_engine live dashboard.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    print(f"wisdom_engine dashboard: http://{args.host}:{args.port}")
    create_app().run(host=args.host, port=args.port, debug=args.debug, use_reloader=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
