"""Server chart_simul: Flask phục vụ trang chart sống của des (chỉ đọc Redis).

Mô tả:
    File DUY NHẤT trong chart_simul biết cả tầng dữ liệu (source.py), tầng đối
    chiếu (verify.py), chiến lược của des (configuration.run_strategy) lẫn tầng
    hiển thị (payload.py, live_page.py) -- vai trò "wiring". Các file kia đều là
    lá: không file nào import file khác trong chart_simul (trừ payload.py dùng
    kiểu Check của verify.py).

    Ba trang: "/" (một chiến lược × symbol × timeframe, có tín hiệu KHÔNG lọc trend và đối
    chiếu với db đầu ra của nguồn raw) gọi /api/view; "/trend" (cùng chiến lược × symbol × timeframe
    nhưng lọc trend bằng MỘT trong bốn khối -- knn, breakout, zigzag, tstat -- đối chiếu với db đầu ra
    của chính nguồn đó, có pane xu hướng) gọi /api/trend; "/mtf" (một symbol, tối đa 4 timeframe
    chỉ có nến, để kiểm tra DFS) gọi /api/mtf.

    Mỗi nhịp trình duyệt gọi /api/view. Server làm việc theo 2 bước:
      1. Hỏi nhẹ (đồng hồ DFS, nến mới nhất, số tín hiệu): trình duyệt gửi kèm
         "dấu vân tay" của khung nhìn nó đang có; nếu không đổi và không còn dấu
         "chờ" nào thì trả {"changed": false} -- gần như không tốn gì.
      2. Có đổi mới đọc đủ cửa sổ nến (tối đa 1200), chạy chiến lược bằng chính
         hàm của des, đối chiếu với tín hiệu ở db đầu ra rồi trả khung nhìn mới.

    Server không giữ trạng thái giữa các yêu cầu (trạng thái "chờ" đếm giờ nằm ở
    trình duyệt), nên chạy nhiều tab hay restart server đều an toàn.

Chạy (từ thư mục cha của repo):
    wisdom_engine/.venv/bin/python -m wisdom_engine.des.src.chart_simul.server --port 8518
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import redis
from flask import Flask, Response, jsonify, request
from wisdom_engine.des.src.chart_simul.live_page import render_page
from wisdom_engine.des.src.chart_simul.mtf_page import render_mtf_page
from wisdom_engine.des.src.chart_simul.payload import (
    TrendViewData,
    ViewData,
    build_payload,
    build_trend_payload,
    mtf_pane,
)
from wisdom_engine.des.src.chart_simul.source import (
    DFS_TIMEFRAMES,
    READ_BARS,
    Pair,
    RedisSource,
)
from wisdom_engine.des.src.chart_simul.trend_page import render_trend_page
from wisdom_engine.des.src.chart_simul.verify import (
    STATUS_MISSED,
    STATUS_OK,
    STATUS_PENDING,
    STATUS_WRONG_SIDE,
    Check,
    classify,
    count_by_status,
    expected_fields,
    field_diffs,
)
from wisdom_engine.des.src.configuration import (
    CONFIG,
    OPERATING_SYMBOLS,
    TrendSource,
    get_strategy,
    run_strategy,
    run_strategy_with_trend_reference,
    trend_filter_enabled_for,
    trend_source_for,
)
from wisdom_engine.des.src.indicator import timeframe_minutes
from wisdom_engine.des.src.redis_io.candle_reader import OUTPUT_SOURCES, STAMP_FORMAT
from wisdom_engine.des.src.rules.trend_tf import build_trend_frame, get_method

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8518
DEFAULT_SYMBOL = "US30"
DEFAULT_BARS = 300
# Nguồn của trang chính ("raw" = không lọc trend) và các nguồn lọc trend của trang /trend (mỗi nguồn một khối, một db đầu ra).
RAW_SOURCE = "raw"
TREND_SOURCES: tuple[str, ...] = tuple(source for source in OUTPUT_SOURCES if source != RAW_SOURCE)
DEFAULT_TREND_SOURCE = TREND_SOURCES[0]  # knn: nguồn đầu tiên của trang /trend
BARS_MIN = 50
BARS_MAX = READ_BARS
# Quá ngần này giây thật mà des vẫn chưa ghi tín hiệu của nến mới nhất -> trình
# duyệt đổi "chờ" thành "LỠ". p99 độ trễ publish đo được ~2,1 s, tối đa ~2,5 s.
MISS_AFTER_SECONDS = 5
# Trang multi-timeframe: tối đa 4 khung xếp dọc; mặc định hiện sẵn 4 khung phổ biến;
# khoảng nhìn mặc định = số nến này của khung nhỏ nhất.
MAX_MTF_TIMEFRAMES = 4
MTF_DEFAULT_TIMEFRAMES = ("H4", "H1", "M30", "M10")
MTF_VISIBLE_BARS = 120

STRATEGY_LABELS = {"combo": "Combo", "ma_cross": "MA Cross", "macd_break": "MACD Break"}
_PARAMS_LABEL = {
    "combo": "MA {MA_PERIOD} · MACD {MACD_FAST}/{MACD_SLOW}/{MACD_SIGNAL} · ATR {ATR_PERIOD}",
    "ma_cross": (
        "Fast {FAST_MA} · Slow {SLOW_MA} · MACD {MACD_FAST}/{MACD_SLOW}/{MACD_SIGNAL}"
        " · ATR {ATR_PERIOD}"
    ),
    "macd_break": (
        "Lookback {LOOKBACK_BARS} · MACD {MACD_FAST}/{MACD_SLOW}/{MACD_SIGNAL}"
        " · ATR {ATR_PERIOD}"
    ),
}
# Trạng thái mà chart có "field kỳ vọng" để so/hiện (nến có tín hiệu theo chart).
_HAS_EXPECTED = (STATUS_OK, STATUS_PENDING, STATUS_MISSED, STATUS_WRONG_SIDE)


@dataclass(frozen=True)
class ViewRequest:
    """Một yêu cầu xem: chiến lược × symbol × timeframe × số nến × nguồn tín hiệu (raw hoặc một khối lọc trend)."""

    strategy: str
    symbol: str
    tf: str
    bars: int
    source: str = RAW_SOURCE


@dataclass(frozen=True)
class Token:
    """Dấu vân tay khung nhìn trình duyệt đang giữ (xem build_view)."""

    head: str
    nsig: int
    pending: bool
    run: str
    thead: str = ""  # chỉ trang trend dùng: stamp nến khung trend mới nhất (nến trend mới đóng thì đường trend đổi)


@dataclass(frozen=True)
class MtfRequest:
    """Một yêu cầu xem multi-timeframe: symbol × các timeframe (lớn -> nhỏ) × số nến."""

    symbol: str
    tfs: tuple[str, ...]
    bars: int


@dataclass(frozen=True)
class MtfToken:
    """Dấu vân tay trình duyệt giữ cho từng khung (stamp nến mới nhất) + run_id của DFS."""

    heads: dict[str, str]
    run: str


@dataclass(frozen=True)
class Context:
    """Những thứ dựng một lần lúc khởi động server."""

    source: RedisSource
    catalog: dict[str, Any]
    pairs: list[Pair]


# ------------------------------------------------------------------ danh mục
def build_catalog(config: dict[str, Any]) -> dict[str, Any]:
    """Các tổ hợp mà des thực sự xử lý, lấy từ des_config.yaml (không gõ cứng).

    Chiến lược = live.enabled_strategies; timeframe của từng chiến lược = đúng
    danh sách mà live_worker dùng để định tuyến sự kiện; symbol = strategies.combo.symbols.
    """
    strategies: dict[str, Any] = {}
    for key in [str(name).lower() for name in config["live"]["enabled_strategies"]]:
        spec = get_strategy(key)
        timeframes = sorted(
            spec.recommended_timeframes or spec.supported_timeframes, key=timeframe_minutes
        )
        default_tf = str(config["strategies"][key]["default_timeframe"]).upper()
        strategies[key] = {
            "label": STRATEGY_LABELS.get(key, key),
            "timeframes": list(timeframes),
            "defaultTimeframe": default_tf if default_tf in timeframes else timeframes[0],
        }
    return {"strategies": strategies, "symbols": sorted(OPERATING_SYMBOLS)}


def all_pairs(catalog: dict[str, Any]) -> list[Pair]:
    """Mọi (strategy, symbol, timeframe) mà des xử lý (176 tổ hợp với cấu hình hiện tại)."""
    return [
        (strategy, symbol, tf)
        for strategy, info in catalog["strategies"].items()
        for symbol in catalog["symbols"]
        for tf in info["timeframes"]
    ]


# ------------------------------------------------------------ đọc tham số HTTP
def _parse_bars(raw: str | None) -> int:
    if raw in (None, ""):
        return DEFAULT_BARS
    try:
        bars = int(str(raw))
    except ValueError as exc:
        raise ValueError(f"bars must be an integer, got '{raw}'") from exc
    return min(BARS_MAX, max(BARS_MIN, bars))


def parse_request(
    args: Mapping[str, str], catalog: dict[str, Any], *, sources: tuple[str, ...] = (RAW_SOURCE,)
) -> ViewRequest:
    """Kiểm tra strategy / symbol / tf nằm trong danh mục của des và source nằm trong `sources` (mục đầu là mặc định); sai thì ValueError."""
    strategies = catalog["strategies"]
    strategy = str(args.get("strategy", "")).lower()
    if strategy not in strategies:
        raise ValueError(f"strategy '{strategy}' is not one of {sorted(strategies)}")
    symbol = str(args.get("symbol", "")).upper()
    if symbol not in catalog["symbols"]:
        raise ValueError(f"symbol '{symbol}' is not one of {catalog['symbols']}")
    tf = str(args.get("tf", "")).upper()
    if tf not in strategies[strategy]["timeframes"]:
        raise ValueError(
            f"timeframe '{tf}' is not valid for {strategy}: {strategies[strategy]['timeframes']}"
        )
    source = str(args.get("source", "") or sources[0]).lower()
    if source not in sources:
        raise ValueError(f"source '{source}' is not one of {list(sources)}")
    return ViewRequest(strategy, symbol, tf, _parse_bars(args.get("bars")), source)


def parse_token(args: Mapping[str, str]) -> Token | None:
    """Dấu vân tay trình duyệt gửi kèm; thiếu hoặc hỏng thì coi như chưa có (nạp đủ)."""
    if "head" not in args:
        return None
    try:
        nsig = int(args.get("nsig", "-1"))
    except ValueError:
        return None
    return Token(
        head=str(args.get("head", "")),
        nsig=nsig,
        pending=args.get("pending") == "1",
        run=str(args.get("run", "")),
        thead=str(args.get("thead", "")),
    )


def parse_mtf_request(args: Mapping[str, str], catalog: dict[str, Any]) -> MtfRequest:
    """Kiểm tra symbol thuộc danh mục des, tfs thuộc 15 khung DFS phát, 1..4 khung khác nhau."""
    symbol = str(args.get("symbol", "")).upper()
    if symbol not in catalog["symbols"]:
        raise ValueError(f"symbol '{symbol}' is not one of {catalog['symbols']}")
    requested = [part.strip().upper() for part in str(args.get("tfs", "")).split(",") if part.strip()]
    for tf in requested:
        if tf not in DFS_TIMEFRAMES:
            raise ValueError(f"timeframe '{tf}' is not one of {list(DFS_TIMEFRAMES)}")
    tfs = sorted(dict.fromkeys(requested), key=timeframe_minutes, reverse=True)
    if not tfs:
        raise ValueError(f"choose 1 to {MAX_MTF_TIMEFRAMES} timeframes")
    if len(tfs) > MAX_MTF_TIMEFRAMES:
        raise ValueError(f"at most {MAX_MTF_TIMEFRAMES} timeframes, got {len(tfs)}")
    return MtfRequest(symbol, tuple(tfs), _parse_bars(args.get("bars")))


def parse_mtf_token(args: Mapping[str, str]) -> MtfToken | None:
    """`heads` = "H4=<stamp>|H1=<stamp>"; thiếu thì coi như chưa có gì (nạp đủ mọi khung)."""
    if "heads" not in args:
        return None
    heads: dict[str, str] = {}
    for item in str(args["heads"]).split("|"):
        tf, separator, stamp = item.partition("=")
        if separator:
            heads[tf.strip().upper()] = stamp
    return MtfToken(heads=heads, run=str(args.get("run", "")))


# ----------------------------------------------------------------- dựng khung nhìn
def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _reject_trend(strategy: str) -> None:
    """Cờ trend_filter_enabled phải tắt (live_worker cũng từ chối khởi động nếu bật); bật thì báo rõ, không đoán."""
    if trend_filter_enabled_for(strategy):
        raise ValueError(
            f"des_config.yaml has trend_filter_enabled on for {strategy}; it must be false "
            "(the filtered sources knn / breakout / zigzag / tstat do not depend on it), so chart_simul cannot check against des"
        )


def _unchanged(source: RedisSource, pair: Pair, clock: dict[str, Any], token: Token) -> bool:
    """Khung nhìn trình duyệt đang giữ còn đúng không (nến mới nhất, số tín hiệu, lần chạy DFS)."""
    return (
        not token.pending
        and (clock["runId"] or "") == token.run
        and (source.head_stamp(pair[1], pair[2]) or "") == token.head
        and source.signal_count(pair, source=RAW_SOURCE) == token.nsig
    )


def _check_from(
    candles: pd.DataFrame, snapshot_bars: int, des_from: str | None
) -> pd.Timestamp | None:
    """Nến sớm nhất được đối chiếu: đã đủ `snapshot_bars` nến lịch sử VÀ des đã chạy."""
    if des_from is None:
        return None
    enough_history = pd.Timestamp(candles["bartime"].iloc[min(snapshot_bars, len(candles)) - 1])
    return max(enough_history, pd.Timestamp(des_from))


def _exact_signal_fn(
    candles: pd.DataFrame, req: ViewRequest, snapshot_bars: int
) -> Callable[[pd.Timestamp], int]:
    """Tín hiệu tại nến `time` theo đúng cửa sổ mà des thấy (snapshot_bars nến kết thúc ở đó)."""

    def exact_signal(time: pd.Timestamp) -> int:
        window = candles[candles["bartime"] <= time].tail(snapshot_bars).reset_index(drop=True)
        result = run_strategy(req.strategy, symbol=req.symbol, tf=req.tf, bars=window)
        return int(pd.to_numeric(result["signal"].iloc[-1], errors="coerce") or 0)

    return exact_signal


def _expected_by_time(
    req: ViewRequest, frame: pd.DataFrame, checks: list[Check], tf_minutes: int
) -> dict[pd.Timestamp, dict[str, str]]:
    """Field HASH mà des SẼ ghi cho từng nến có tín hiệu theo chart."""
    index = {pd.Timestamp(time): i for i, time in enumerate(frame["bartime"])}
    expected: dict[pd.Timestamp, dict[str, str]] = {}
    for check in checks:
        if check.status not in _HAS_EXPECTED or check.time not in index:
            continue
        fields = expected_fields(
            req.strategy,
            req.symbol,
            req.tf,
            frame.iloc[index[check.time]],
            tf_minutes=tf_minutes,
        )
        if fields is not None:
            expected[check.time] = fields
    return expected


def _base_meta(req: ViewRequest, clock: dict[str, Any], signal_count: int) -> dict[str, Any]:
    return {
        "strategy": req.strategy,
        "strategyLabel": STRATEGY_LABELS.get(req.strategy, req.strategy),
        "symbol": req.symbol,
        "tf": req.tf,
        "source": req.source,
        "clock": clock,
        "serverTime": _now(),
        "signalCount": signal_count,
        "missAfterSeconds": MISS_AFTER_SECONDS,
    }


def _empty_view(
    meta: dict[str, Any],
    message: str = "No candles for this pair in Redis yet (DFS has not played that far).",
) -> dict[str, Any]:
    """Khung nhìn khi db đầu vào chưa có nến nào cho cặp này."""
    return {
        "candles": [],
        "overlays": [],
        "panels": [],
        "marks": [],
        "signals": [],
        "stats": {**count_by_status([]), "fieldDiff": 0},
        "meta": {
            **meta,
            "head": "",
            "lastCandle": "",
            "lastSignal": "-",
            "checkFrom": None,
            "paramsLabel": "",
            "message": message,
        },
    }


def _full_view(
    ctx: Context, req: ViewRequest, pair: Pair, clock: dict[str, Any]
) -> dict[str, Any]:
    source = ctx.source
    # Đọc số tín hiệu TRƯỚC nến và tín hiệu: dấu vân tay gửi về trình duyệt luôn
    # <= thực tế, nên lỡ có tín hiệu mới giữa chừng thì nhịp sau tự nạp lại.
    signal_count = source.signal_count(pair, source=req.source)
    candles = source.read_candles(req.symbol, req.tf, READ_BARS)
    meta = _base_meta(req, clock, signal_count)
    if candles.empty:
        return _empty_view(meta)

    params = get_strategy(req.strategy).normalize_params(None, req.symbol, req.tf)
    enriched = run_strategy(req.strategy, symbol=req.symbol, tf=req.tf, bars=candles)
    frame = enriched.tail(req.bars).reset_index(drop=True)
    # Tín hiệu đọc SAU nến: nếu des vừa ghi tín hiệu cho nến mới hơn nến chart đã
    # đọc, tín hiệu đó rơi ngoài vùng đối chiếu (unchecked) và nhịp sau sẽ khớp.
    published = source.read_signals(pair, since=pd.Timestamp(frame["bartime"].iloc[0]), source=req.source)
    des_from = source.first_signal_stamp(ctx.pairs)
    check_from = _check_from(candles, source.snapshot_bars, des_from)
    checks = classify(
        frame,
        published,
        check_from=check_from,
        exact_signal=_exact_signal_fn(candles, req, source.snapshot_bars),
    )
    tf_minutes = timeframe_minutes(req.tf)
    expected = _expected_by_time(req, frame, checks, tf_minutes)
    data = ViewData(
        strategy=req.strategy,
        symbol=req.symbol,
        tf=req.tf,
        tf_minutes=tf_minutes,
        frame=frame,
        params=params,
        checks=checks,
        published=published,
        expected=expected,
        diffs=field_diffs(checks, expected, published),
    )
    last_candle = pd.Timestamp(frame["bartime"].iloc[-1])
    return {
        **build_payload(data),
        "meta": {
            **meta,
            "head": last_candle.strftime(STAMP_FORMAT),
            "lastCandle": last_candle.strftime("%Y-%m-%d %H:%M"),
            "lastSignal": max(published) if published else "-",
            "bars": len(frame),
            "paramsLabel": _PARAMS_LABEL[req.strategy].format(**params),
            "checkFrom": check_from.strftime("%Y-%m-%d %H:%M") if check_from is not None else None,
            "desFrom": des_from,
        },
    }


def build_view(ctx: Context, req: ViewRequest, token: Token | None) -> dict[str, Any]:
    """Khung nhìn cho một yêu cầu; hoặc {"changed": false} nếu trình duyệt đã có bản đúng."""
    _reject_trend(req.strategy)
    pair = (req.strategy, req.symbol, req.tf)
    clock = ctx.source.sim_clock()
    if token is not None and _unchanged(ctx.source, pair, clock, token):
        return {"changed": False, "meta": {"clock": clock, "serverTime": _now()}}
    return {"changed": True, **_full_view(ctx, req, pair, clock)}


# ------------------------------------------------------------------ trang trend
def _trend_for(req: ViewRequest) -> TrendSource:
    """Khung trend + chính sách của khối `req.source` cho ô (chiến lược, symbol, khung vào lệnh).

    knn, zigzag và tstat tra bảng trend_tf_by_symbol của des_config.yaml; breakout tính trên chính khung vào lệnh.
    """
    trend = trend_source_for(req.strategy, req.symbol, req.tf, req.source)
    if not trend.trend_tf:
        raise ValueError(
            f"no trend timeframe is configured for {req.strategy} {req.symbol} {req.tf} "
            "in des_config.yaml (trend_tf_by_symbol)"
        )
    return trend


def _trend_unchanged(
    store: RedisSource, pair: Pair, req: ViewRequest, trend_tf: str, clock: dict[str, Any], token: Token
) -> bool:
    """Khung nhìn trend trình duyệt đang giữ còn đúng không (nến vào lệnh, nến trend, số tín hiệu của nguồn, lần chạy DFS)."""
    return (
        not token.pending
        and (clock["runId"] or "") == token.run
        and (store.head_stamp(pair[1], pair[2]) or "") == token.head
        and (store.head_stamp(pair[1], trend_tf) or "") == token.thead
        and store.signal_count(pair, source=req.source) == token.nsig
    )


def _exact_trend_signal_fn(
    candles: pd.DataFrame,
    trend_candles: pd.DataFrame,
    req: ViewRequest,
    trend: TrendSource,
    snapshot_bars: int,
) -> Callable[[pd.Timestamp], int]:
    """Tín hiệu SAU lọc trend (khối `req.source`) tại nến `time` theo đúng cái des thấy lúc xử lý nến đó.

    Nến vào lệnh: `snapshot_bars` nến kết thúc ở đó. Nến trend: những nến đã đóng tới lúc nến vào lệnh đóng
    (DFS ghi mọi nến cùng giờ đóng trong một MULTI/EXEC), lấy `max(snapshot_bars, warmup của khối)` nến mới nhất
    (live_worker xin chừng đó nến; DFS chỉ giữ tối đa READ_BARS nên khối cần warm-up dài hơn, vd zigzag, chỉ nhận được ngần ấy).
    """
    trend_tf = trend.trend_tf
    trend_bars = max(snapshot_bars, trend.warmup_bars)
    tf_delta = pd.Timedelta(minutes=timeframe_minutes(req.tf))
    trend_delta = pd.Timedelta(minutes=timeframe_minutes(trend_tf))

    def exact_signal(time: pd.Timestamp) -> int:
        entry_window = candles[candles["bartime"] <= time].tail(snapshot_bars).reset_index(drop=True)
        closed = trend_candles[trend_candles["bartime"] + trend_delta <= time + tf_delta]
        trend_window = closed.tail(trend_bars).reset_index(drop=True)
        if trend_window.empty:
            return 0
        result = run_strategy_with_trend_reference(
            req.strategy,
            symbol=req.symbol,
            entry_tf=req.tf,
            entry_bars=entry_window,
            trend_tf=trend_tf,
            trend_bars=trend_window,
            overrides={"TREND_METHODS": req.source},
        )
        return int(pd.to_numeric(result["signal"].iloc[-1], errors="coerce") or 0)

    return exact_signal


def _trend_specs(methods: tuple[str, ...]) -> list[dict[str, Any]]:
    """Khai báo đường vẽ (OVERLAYS) của các khối xu hướng đang bật, theo thứ tự khối."""
    return [dict(spec) for name in methods for spec in get_method(name).OVERLAYS]


def _empty_trend_view(meta: dict[str, Any], message: str | None = None) -> dict[str, Any]:
    empty = _empty_view(meta) if message is None else _empty_view(meta, message)
    return {**empty, "stats": {**empty["stats"], "filtered": 0}, "trend": None}


def _full_trend_view(
    ctx: Context, req: ViewRequest, pair: Pair, trend: TrendSource, clock: dict[str, Any]
) -> dict[str, Any]:
    source = ctx.source
    trend_tf = trend.trend_tf
    # Đọc số tín hiệu TRƯỚC nến và tín hiệu (cùng lý do với _full_view).
    signal_count = source.signal_count(pair, source=req.source)
    candles = source.read_candles(req.symbol, req.tf, READ_BARS)
    trend_candles = source.read_candles(req.symbol, trend_tf, READ_BARS)
    methods = (req.source,)
    meta = {
        **_base_meta(req, clock, signal_count),
        "trendTf": trend_tf,
        "trendLabel": " + ".join(name.upper() for name in methods),
    }
    if candles.empty:
        return _empty_trend_view(meta)
    if trend_candles.empty:
        return _empty_trend_view(
            meta, f"No {trend_tf} candles for {req.symbol} in Redis yet, so the trend filter cannot be computed."
        )

    params = {
        **get_strategy(req.strategy).normalize_params({"TREND_METHODS": req.source}, req.symbol, req.tf),
        "TREND_TF": trend_tf,
    }
    enriched = run_strategy_with_trend_reference(
        req.strategy,
        symbol=req.symbol,
        entry_tf=req.tf,
        entry_bars=candles,
        trend_tf=trend_tf,
        trend_bars=trend_candles,
        overrides={"TREND_METHODS": req.source},
    )
    frame = enriched.tail(req.bars).reset_index(drop=True)
    published = source.read_signals(pair, since=pd.Timestamp(frame["bartime"].iloc[0]), source=req.source)
    des_from = source.first_signal_stamp(ctx.pairs)
    check_from = _check_from(candles, source.snapshot_bars, des_from)
    checks = classify(
        frame,
        published,
        check_from=check_from,
        exact_signal=_exact_trend_signal_fn(candles, trend_candles, req, trend, source.snapshot_bars),
    )
    tf_minutes = timeframe_minutes(req.tf)
    expected = _expected_by_time(req, frame, checks, tf_minutes)
    view = ViewData(
        strategy=req.strategy,
        symbol=req.symbol,
        tf=req.tf,
        tf_minutes=tf_minutes,
        frame=frame,
        params=params,
        checks=checks,
        published=published,
        expected=expected,
        diffs=field_diffs(checks, expected, published),
    )
    # Pane xu hướng: chỉ phần khung trend phủ vùng đang vẽ (từ một nến trend trước nến vào lệnh đầu tiên).
    trend_frame = build_trend_frame(trend_candles, params)
    first = pd.Timestamp(frame["bartime"].iloc[0]) - pd.Timedelta(minutes=timeframe_minutes(trend_tf))
    shown = trend_frame[trend_frame["bartime"] >= first].reset_index(drop=True)
    data = TrendViewData(
        view=view,
        trend_tf=trend_tf,
        trend_frame=shown,
        trend_specs=_trend_specs(methods),
        trend_label=meta["trendLabel"],
        check_from=check_from,
    )
    last_candle = pd.Timestamp(frame["bartime"].iloc[-1])
    return {
        **build_trend_payload(data),
        "meta": {
            **meta,
            "head": last_candle.strftime(STAMP_FORMAT),
            "thead": pd.Timestamp(trend_candles["bartime"].iloc[-1]).strftime(STAMP_FORMAT),
            "lastCandle": last_candle.strftime("%Y-%m-%d %H:%M"),
            "lastSignal": max(published) if published else "-",
            "bars": len(frame),
            "paramsLabel": _PARAMS_LABEL[req.strategy].format(**params),
            "checkFrom": check_from.strftime("%Y-%m-%d %H:%M") if check_from is not None else None,
            "desFrom": des_from,
        },
    }


def build_trend_view(ctx: Context, req: ViewRequest, token: Token | None) -> dict[str, Any]:
    """Khung nhìn trang trend; hoặc {"changed": false} nếu trình duyệt đã có bản đúng."""
    _reject_trend(req.strategy)
    pair = (req.strategy, req.symbol, req.tf)
    trend = _trend_for(req)
    clock = ctx.source.sim_clock()
    if token is not None and _trend_unchanged(ctx.source, pair, req, trend.trend_tf, clock, token):
        return {"changed": False, "meta": {"clock": clock, "serverTime": _now()}}
    return {"changed": True, **_full_trend_view(ctx, req, pair, trend, clock)}


def build_mtf_view(ctx: Context, req: MtfRequest, token: MtfToken | None) -> dict[str, Any]:
    """Nến các khung đã đổi kể từ dấu vân tay của trình duyệt; không khung nào đổi -> changed=False.

    Hỏi nhẹ trước (đồng hồ DFS + stamp đầu LIST từng khung, 2 vòng Redis); khung
    nào có nến mới hoặc DFS vừa chạy lại (run_id đổi) mới đọc đủ cửa sổ nến. Stamp
    đọc TRƯỚC nến nên dấu vân tay gửi về luôn <= thực tế, nhịp sau tự nạp bù.
    """
    source = ctx.source
    clock = source.sim_clock()
    heads = source.heads(req.symbol, req.tfs)
    same_run = token is not None and (clock["runId"] or "") == token.run
    known = token.heads if token is not None and same_run else {}
    stale = [tf for tf in req.tfs if known.get(tf) != (heads[tf] or "")]
    meta = {"symbol": req.symbol, "tfs": list(req.tfs), "clock": clock, "serverTime": _now()}
    if not stale:
        return {"changed": False, "meta": meta}
    panes = []
    for tf in stale:
        candles = source.read_candles(req.symbol, tf, READ_BARS)
        frame = candles.tail(req.bars).reset_index(drop=True)
        panes.append(mtf_pane(tf, timeframe_minutes(tf), frame))
    return {"changed": True, "panes": panes, "meta": {**meta, "bars": req.bars}}


# --------------------------------------------------------------------- Flask
def _json(payload: dict[str, Any], status: int = 200) -> Response:
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def create_app(source: RedisSource | None = None) -> Flask:
    """Dựng app Flask. `source` truyền vào (test) thay cho kết nối Redis thật."""
    app = Flask(__name__)
    catalog = build_catalog(CONFIG)
    ctx = Context(source or RedisSource(CONFIG), catalog, all_pairs(catalog))

    @app.get("/")
    def index() -> Response:
        symbols = catalog["symbols"]
        html = render_page(
            strategies=catalog["strategies"],
            symbols=symbols,
            default_strategy=next(iter(catalog["strategies"])),
            default_symbol=DEFAULT_SYMBOL if DEFAULT_SYMBOL in symbols else symbols[0],
            default_bars=DEFAULT_BARS,
            bars_min=BARS_MIN,
            bars_max=BARS_MAX,
            miss_after_seconds=MISS_AFTER_SECONDS,
        )
        return Response(html, mimetype="text/html")

    def respond(build: Callable[[], dict[str, Any]]) -> Response:
        """Biên HTTP chung: tham số sai -> 400, Redis lỗi -> 503, lỗi khác -> 500, đều JSON gọn."""
        try:
            return _json(build())
        except (KeyError, ValueError) as exc:
            return _json({"error": str(exc).strip('"')}, 400)
        except redis.RedisError as exc:
            return _json({"error": f"Redis is not responding: {exc}"}, 503)
        except Exception as exc:  # noqa: BLE001 -- Biên HTTP: trả lỗi JSON gọn cho trang.
            return _json({"error": str(exc)}, 500)

    @app.get("/api/view")
    def api_view() -> Response:
        return respond(
            lambda: build_view(ctx, parse_request(request.args, catalog), parse_token(request.args))
        )

    @app.get("/trend")
    def trend_index() -> Response:
        symbols = catalog["symbols"]
        html = render_trend_page(
            strategies=catalog["strategies"],
            symbols=symbols,
            default_strategy=next(iter(catalog["strategies"])),
            default_symbol=DEFAULT_SYMBOL if DEFAULT_SYMBOL in symbols else symbols[0],
            default_bars=DEFAULT_BARS,
            bars_min=BARS_MIN,
            bars_max=BARS_MAX,
            miss_after_seconds=MISS_AFTER_SECONDS,
            timeframe_seconds={tf: timeframe_minutes(tf) * 60 for tf in DFS_TIMEFRAMES},
            sources=list(TREND_SOURCES),
            default_source=DEFAULT_TREND_SOURCE,
        )
        return Response(html, mimetype="text/html")

    @app.get("/api/trend")
    def api_trend() -> Response:
        return respond(
            lambda: build_trend_view(
                ctx,
                parse_request(request.args, catalog, sources=TREND_SOURCES),
                parse_token(request.args),
            )
        )

    @app.get("/mtf")
    def mtf_index() -> Response:
        symbols = catalog["symbols"]
        html = render_mtf_page(
            symbols=symbols,
            timeframes=list(DFS_TIMEFRAMES),
            timeframe_minutes={tf: timeframe_minutes(tf) for tf in DFS_TIMEFRAMES},
            default_symbol=DEFAULT_SYMBOL if DEFAULT_SYMBOL in symbols else symbols[0],
            default_timeframes=list(MTF_DEFAULT_TIMEFRAMES),
            max_timeframes=MAX_MTF_TIMEFRAMES,
            default_bars=DEFAULT_BARS,
            bars_min=BARS_MIN,
            bars_max=BARS_MAX,
            visible_bars=MTF_VISIBLE_BARS,
        )
        return Response(html, mimetype="text/html")

    @app.get("/api/mtf")
    def api_mtf() -> Response:
        return respond(
            lambda: build_mtf_view(
                ctx, parse_mtf_request(request.args, catalog), parse_mtf_token(request.args)
            )
        )

    @app.get("/health")
    def health() -> Response:
        return jsonify({"status": "ok"})

    return app


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="des live chart (read-only Redis viewer).")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    print(f"des chart_simul: http://{args.host}:{args.port}")
    create_app().run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
