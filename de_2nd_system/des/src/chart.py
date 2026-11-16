"""
Chart của DES: module HIỂN THỊ chỉ đọc. Vẽ nến mà DFS phát (db10) cùng những gì DES đã ghi (db11), theo đúng config DES đang chạy.

Một trang duy nhất. Chọn chiến lược (mục ``strategies:``) rồi symbol của nó; khung trigger và các yếu tố trend (mỗi yếu tố một khung) do config quy định nên không chọn tự do. Trang gồm:
    * pane giá (khung trigger): nến, dấu tín hiệu CUỐI (mũi tên BUY/SELL, ở ``L_DE_*``), dấu tín hiệu THÔ của chiến lược (chấm nhỏ, ở ``L_MODSTR_*``: thô mà không có mũi tên cuối = bị trend
      chặn hoặc hết số nến dời), các đường chỉ báo mà tham số của chiến lược khai (``fast_ma``/``slow_ma``/``ma_period`` -> SMA);
    * pane MACD histogram (khung trigger) khi chiến lược khai ``macd_fast``/``macd_slow``/``macd_signal``;
    * mỗi yếu tố trend của chiến lược một pane ở khung của nó: nến, các đường của yếu tố (sma: SMA; break: đỉnh/đáy của N nến trước; macd: histogram) và dải màu bias đã lưu ở ``L_MODTRE_*``
      (xanh +1, đỏ -1, xám 0);
    * giờ ảo của DFS (``clock_key`` của nguồn nến), phiên bản config đang dùng, bảng tín hiệu (cuối và thô) và chi tiết khi bấm vào một dấu.
Mọi lớp vẽ (đường, dải bias, dấu thô) có công tắc, và chỉ những lớp config quy định mới có. Yếu tố trend thuộc loại chart chưa biết vẽ thì vẫn có nến và dải bias, chỉ thiếu đường.

Dữ liệu: nến đọc bằng ``data_engine.read_candles`` (db của nguồn nến, tên symbol theo nguồn), kết quả chiến lược/yếu tố bằng ``data_engine.read_module``, tín hiệu cuối (List–Hash ``L_DE_*``) và giờ ảo
bằng lệnh đọc của chính file này. Config lấy từ Redis (phiên bản MỚI NHẤT mà các tiến trình DES đang dùng, ``configuration.fetch_plan``), nạp lại mỗi ``CONFIG_REFRESH_SECONDS`` giây nên đổi config
rồi ``--load-config`` thì chart tự theo. Trình duyệt hỏi ``/api/view`` mỗi vài giây kèm "dấu vân tay" của khung nhìn đang giữ; không đổi thì server chỉ trả giờ ảo (``changed: false``).

File này KHÔNG làm các việc sau:
    - Không ghi, xoá hay đặt hạn gì ở Redis: chỉ lệnh đọc, trên kết nối riêng của chart (``redis_connector.make_client``), không dùng chung với tiến trình DES nào.
    - Không chạy chiến lược, không quyết định tín hiệu, không đối chiếu "kỳ vọng so với đã ghi": việc đó là của engine (và của script kiểm). Đường chỉ báo chỉ để NHÌN, tính từ nến theo tham số trong config.
    - Không giữ tham số nào: chu kỳ, khung, symbol, tiền tố key, db, giờ ảo đều lấy từ config.

Chạy (từ gốc repo): ``./.venv/bin/python -m des.src.chart --port 8518``.
Đầu ra: trang HTML tự chứa (thư viện Lightweight Charts nhúng sẵn, không cần internet) và JSON ở ``/api/catalog``, ``/api/view``.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import redis
from flask import Flask, Response, jsonify, request

from des.src import configuration
from des.src.data_engine import STAMP_FORMAT, pair_list_key, read_candles, read_module
from des.src.indicator import macd_hist, sma
from des.src.redis_connector import make_client
from des.src.util import module_token

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8518
CONFIG_REFRESH_SECONDS = 10
# Sức chứa LIST nến của nguồn (DFS seed 1200): đọc nhiều hơn cũng không có. 300 nến ĐẦU chỉ để chỉ báo kịp ấm, không hiển thị.
READ_BARS = 1200
WARMUP_BARS = 300
BARS_MIN = 50
BARS_MAX = READ_BARS - WARMUP_BARS
DEFAULT_BARS = 300
SIGNAL_ROWS_MAX = 300
DFS_STATE_KEY = "dfs:state"  # checkpoint của DFS (hợp đồng DFS); giờ ảo thì lấy theo ``clock_key`` của config

BUY_COLOR = "#22c55e"
SELL_COLOR = "#ef4444"
NEUTRAL_COLOR = "#94a3b8"
LINE_COLORS = ("#38bdf8", "#f59e0b", "#a855f7", "#22c55e", "#ef4444", "#eab308")
_EPOCH = pd.Timestamp("1970-01-01")
_ONE_SECOND = pd.Timedelta(seconds=1)
_PRICE_LINE_PARAMS = ("fast_ma", "slow_ma", "ma_period")  # tham số chiến lược khai chu kỳ một đường SMA trên giá
_PRICE_LINE_LABELS = {"fast_ma": "SMA {n} (fast)", "slow_ma": "SMA {n} (slow)", "ma_period": "SMA {n}"}
_MACD_PARAMS = ("macd_fast", "macd_slow", "macd_signal")
_ACRONYMS = {"ma", "ema", "sma", "macd", "atr", "rsi", "knn"}  # chữ viết tắt giữ in hoa khi đổi tên khai ở config thành tên hiển thị


# -- ngữ cảnh: config đang chạy + kết nối ------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Context:
    """Config đang dùng (số phiên bản và ``Plan`` đã kiểm) và cách mở kết nối chỉ-đọc tới một db của một Redis (``connect(endpoint, db)``, có nhớ kết nối)."""

    version: int
    plan: configuration.Plan
    connect: Callable[[dict[str, Any], int], redis.Redis]


class Provider:
    """Giữ ``Context`` hiện hành và nạp lại config từ Redis tối đa mỗi ``CONFIG_REFRESH_SECONDS`` giây. Nạp lỗi mà đã có config cũ thì giữ bản cũ; chưa có bản nào thì để lỗi nổi lên cho route trả 503."""

    def __init__(
        self,
        local: dict[str, Any],
        *,
        loader: Callable[[], tuple[int, configuration.Plan]] | None = None,
        connect: Callable[[dict[str, Any], int], redis.Redis] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._loader = loader or (lambda: configuration.fetch_plan(local))
        self._connect = connect or self._cached_connect
        self._clock = clock
        self._clients: dict[tuple[Any, ...], redis.Redis] = {}
        self._context: Context | None = None
        self._loaded_at = 0.0

    def _cached_connect(self, endpoint: dict[str, Any], db: int) -> redis.Redis:
        timeouts = self._context.plan.timeouts if self._context is not None else {}
        key = (endpoint["host"], endpoint["port"], db)
        if key not in self._clients:
            self._clients[key] = make_client(host=endpoint["host"], port=endpoint["port"], password=endpoint["password"], db=db, **timeouts)
        return self._clients[key]

    def get(self) -> Context:
        if self._context is None or self._clock() - self._loaded_at >= CONFIG_REFRESH_SECONDS:
            try:
                version, plan = self._loader()
            except (redis.RedisError, RuntimeError, ValueError, KeyError, TypeError):
                if self._context is None:
                    raise
            else:
                self._context = Context(version, plan, self._connect)
            self._loaded_at = self._clock()
        assert self._context is not None
        return self._context


# -- danh mục (đọc từ config) --------------------------------------------------------------------------------------------------------------------


def display_name(name: str) -> str:
    """Tên hiển thị của một chiến lược hoặc yếu tố khai ở config: bỏ gạch dưới, viết hoa chữ đầu, giữ chữ viết tắt in hoa (``ma_cross`` thành ``MA Cross``, ``combo`` thành ``Combo``)."""
    return " ".join(part.upper() if part.lower() in _ACRONYMS else part.capitalize() for part in name.split("_"))


def trend_label(name: str, kind: str, params: Mapping[str, Any]) -> str:
    """Tên hiển thị của một yếu tố trend theo loại và tham số (``MACD Histogram 5/25/5``, ``SMA 20``, ``Breakout 20 (High/Low)``); loại chưa biết thì dùng tên khai ở config."""
    if kind == "macd" and all(key in params for key in ("fast", "slow", "signal")):
        return f"MACD Histogram {int(params['fast'])}/{int(params['slow'])}/{int(params['signal'])}"
    if kind == "sma" and "period" in params:
        return f"SMA {int(params['period'])}"
    if kind == "break" and "lookback" in params:
        return f"Breakout {int(params['lookback'])} (High/Low)"
    return display_name(name)


def trend_kind(factor: configuration.TrendFactor) -> str:
    """Loại của một yếu tố trend: phần sau ``bias_`` của tên hàm đăng ký (``bias_macd`` -> ``macd``)."""
    return factor.function.__name__.removeprefix("bias_")


def build_catalog(ctx: Context) -> dict[str, Any]:
    """Những gì người xem được chọn: mỗi chiến lược đã đăng ký với tên hiển thị, khung trigger, symbol, loại lệnh và các yếu tố trend tham chiếu (tên, tên hiển thị, khung, loại, tham số)."""
    plan = ctx.plan
    strategies = {
        name: {
            "label": display_name(name),
            "trigger": strategy.trigger,
            "symbols": list(strategy.symbols),
            "orderType": strategy.order_type,
            "trends": [
                {"name": factor, "frame": frame, "type": trend_kind(plan.trends[factor]), "params": plan.trends[factor].params, "label": trend_label(factor, trend_kind(plan.trends[factor]), plan.trends[factor].params)}
                for factor, frame in strategy.trends
            ],
        }
        for name, strategy in plan.strategies.items()
    }
    return {"configVersion": ctx.version, "strategies": strategies, "defaultBars": DEFAULT_BARS, "barsMin": BARS_MIN, "barsMax": BARS_MAX}


@dataclass(frozen=True)
class ViewRequest:
    """Một yêu cầu xem: chiến lược × symbol × số nến trigger hiển thị."""

    strategy: str
    symbol: str
    bars: int


def parse_request(args: Mapping[str, str], plan: configuration.Plan) -> ViewRequest:
    """Kiểm chiến lược và symbol nằm trong config, đưa số nến về khoảng cho phép; sai thì ValueError nêu lựa chọn hợp lệ."""
    strategy = str(args.get("strategy", "")).strip().lower()
    if strategy not in plan.strategies:
        raise ValueError(f"strategy '{strategy}' is not one of {sorted(plan.strategies)}")
    symbol = str(args.get("symbol", "")).strip().upper()
    if symbol not in plan.strategies[strategy].symbols:
        raise ValueError(f"symbol '{symbol}' is not one of {list(plan.strategies[strategy].symbols)} for {strategy}")
    raw = args.get("bars")
    try:
        bars = DEFAULT_BARS if raw in (None, "") else int(str(raw))
    except ValueError as exc:
        raise ValueError(f"bars must be an integer, got '{raw}'") from exc
    return ViewRequest(strategy, symbol, min(BARS_MAX, max(BARS_MIN, bars)))


def _source_of(plan: configuration.Plan, symbol: str) -> configuration.Source:
    """Nguồn nến nuôi ``symbol`` (mỗi symbol chỉ thuộc một nguồn)."""
    for source in plan.sources.values():
        if symbol in source.symbols:
            return source
    raise ValueError(f"symbol '{symbol}' is fed by no candle source")


# -- đọc Redis (chỉ lệnh đọc) --------------------------------------------------------------------------------------------------------------------


def read_clock(client: redis.Redis, clock_key: str) -> dict[str, Any]:
    """Giờ ảo của DFS (``clock_key``) và checkpoint của nó; khoá thiếu thì giá trị None."""
    pipe = client.pipeline(transaction=False)
    pipe.get(clock_key)
    pipe.hgetall(DFS_STATE_KEY)
    clock, state = pipe.execute()
    state = state or {}
    tick = state.get("tick_seq")
    return {"simTime": clock or state.get("sim_time"), "tick": int(tick) if tick not in (None, "") else None, "status": state.get("status"), "runId": state.get("run_id")}


def read_signals(client: redis.Redis, *, key_prefix: str, symbol: str, timeframe: str, strategy: str) -> list[tuple[str, dict[str, str]]]:
    """Tín hiệu cuối DES đã ghi cho (symbol, khung trigger): [(mốc, field HASH)] tăng dần, chỉ của ``strategy``. HASH đã hết hạn thì bỏ."""
    list_key = pair_list_key(key_prefix, symbol, timeframe)
    stamps = client.lrange(list_key, 0, -1)
    if not stamps:
        return []
    pipe = client.pipeline(transaction=False)
    for stamp in stamps:
        pipe.hgetall(f"{list_key}:{stamp}")
    rows = [(stamp, fields) for stamp, fields in zip(stamps, pipe.execute(), strict=True) if fields and str(fields.get("strategy", "")).lower() == strategy]
    return sorted(rows, key=lambda row: row[0])


def fingerprint(ctx: Context, req: ViewRequest) -> str:
    """"Dấu vân tay" của khung nhìn: phiên bản config, mốc nến mới nhất của từng khung, mốc kết quả mới nhất của chiến lược và từng yếu tố, số tín hiệu cuối, lần chạy DFS. Đổi một trong số đó là khung nhìn đổi."""
    plan = ctx.plan
    strategy = plan.strategies[req.strategy]
    source = _source_of(plan, req.symbol)
    candles, output = ctx.connect(source.endpoint, source.db), ctx.connect(plan.output, plan.output["db"])
    frames = sorted({strategy.trigger, *(frame for _, frame in strategy.trends)})
    pipe = candles.pipeline(transaction=False)
    for frame in frames:
        pipe.lindex(pair_list_key(source.key_prefix, req.symbol, frame), 0)
    pipe.hget(DFS_STATE_KEY, "run_id")
    heads = pipe.execute()
    pipe = output.pipeline(transaction=False)
    pipe.lindex(pair_list_key(plan.output["strategy_prefix"], req.symbol, strategy.trigger, module_token(req.strategy)), -1)
    for factor, frame in strategy.trends:
        pipe.lindex(pair_list_key(plan.output["trend_prefix"], req.symbol, frame, module_token(factor)), -1)
    pipe.llen(pair_list_key(plan.output["signal_prefix"], req.symbol, strategy.trigger))
    return "|".join(str(item) for item in (ctx.version, req.bars, *heads, *pipe.execute()))


# -- dựng dữ liệu vẽ (hàm thuần) -----------------------------------------------------------------------------------------------------------------


def unix_times(times: pd.Series) -> list[int]:
    """Cột giờ UTC không múi giờ thành Unix giây (định dạng Lightweight Charts)."""
    return [int(value) for value in (pd.to_datetime(times) - _EPOCH) // _ONE_SECOND]


def _number(value: Any) -> float | None:
    """Số thực làm tròn 8 chữ số; thiếu hoặc không hữu hạn thì None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 8) if np.isfinite(number) else None


def candle_points(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """[{time, open, high, low, close}] cho series nến."""
    return [
        {"time": at, "open": _number(o), "high": _number(h), "low": _number(low), "close": _number(c)}
        for at, o, h, low, c in zip(unix_times(frame["bartime"]), frame["open"], frame["high"], frame["low"], frame["close"], strict=True)
    ]


def line_points(times: pd.Series, values: Any) -> list[dict[str, Any]]:
    """Điểm của một đường: mỗi nến một điểm; chưa đủ dữ liệu (NaN) thì chỉ có ``time`` (điểm trống) để mọi đường có đúng số điểm như nến."""
    points: list[dict[str, Any]] = []
    for at, value in zip(unix_times(times), np.asarray(values, dtype=float), strict=True):
        number = _number(value)
        points.append({"time": at} if number is None else {"time": at, "value": number})
    return points


def histogram_points(times: pd.Series, values: Any) -> list[dict[str, Any]]:
    """Điểm của một histogram: cột xanh khi dương, đỏ khi âm, xám khi bằng 0; NaN thì điểm trống."""
    points: list[dict[str, Any]] = []
    for at, value in zip(unix_times(times), np.asarray(values, dtype=float), strict=True):
        number = _number(value)
        if number is None:
            points.append({"time": at})
        else:
            points.append({"time": at, "value": number, "color": BUY_COLOR if number > 0 else SELL_COLOR if number < 0 else NEUTRAL_COLOR})
    return points


def _bias_color(value: int | None) -> str:
    """Màu của một hướng đã lưu: xanh +1, đỏ -1, xám 0 hoặc chưa có kết quả."""
    return BUY_COLOR if value is not None and value > 0 else SELL_COLOR if value is not None and value < 0 else NEUTRAL_COLOR


def colored_points(times: pd.Series, values: Any, bias: Mapping[pd.Timestamp, int]) -> list[dict[str, Any]]:
    """Điểm của một đường hoặc histogram mà MÀU từng điểm là hướng (bias) đã lưu ở db11 của nến đó (nến chưa có kết quả thì xám); giá trị NaN là điểm trống."""
    points: list[dict[str, Any]] = []
    for at, stamp, value in zip(unix_times(times), times, np.asarray(values, dtype=float), strict=True):
        number = _number(value)
        points.append({"time": at} if number is None else {"time": at, "value": number, "color": _bias_color(bias.get(pd.Timestamp(stamp)))})
    return points


def _series(key: str, label: str, kind: str, color: str, data: list[dict[str, Any]], *, scale: str = "right") -> dict[str, Any]:
    return {"key": key, "label": label, "kind": kind, "color": color, "scale": scale, "data": data}


def _int_params(params: Mapping[str, Any], names: tuple[str, ...]) -> list[tuple[str, int]]:
    return [(name, int(params[name])) for name in names if name in params]


def strategy_series(params: Mapping[str, Any], frame: pd.DataFrame, window: pd.Series) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Đường chỉ báo của chiến lược theo tham số nó khai: (các đường SMA trên giá, các series ở pane MACD). ``frame`` là toàn bộ nến đã đọc (để chỉ báo ấm), ``window`` là mặt nạ phần hiển thị."""
    close = pd.to_numeric(frame["close"], errors="coerce")
    times = frame.loc[window, "bartime"]
    lines = [
        _series(name, _PRICE_LINE_LABELS[name].format(n=period), "line", LINE_COLORS[index % len(LINE_COLORS)], line_points(times, sma(close, period)[window]))
        for index, (name, period) in enumerate(_int_params(params, _PRICE_LINE_PARAMS))
    ]
    panel: list[dict[str, Any]] = []
    if all(name in params for name in _MACD_PARAMS):
        fast, slow, signal = (int(params[name]) for name in _MACD_PARAMS)
        panel.append(_series("macd_hist", f"MACD Histogram {fast}/{slow}/{signal}", "histogram", NEUTRAL_COLOR, histogram_points(times, macd_hist(close, fast=fast, slow=slow, signal=signal)[window])))
    return lines, panel


def factor_series(
    name: str, kind: str, params: Mapping[str, Any], frame: pd.DataFrame, window: pd.Series, bias: Mapping[pd.Timestamp, int] | None = None
) -> list[dict[str, Any]]:
    """Đường của một yếu tố trend theo loại: sma -> SMA(period); break -> đỉnh (High cao nhất) và đáy (Low thấp nhất) của ``lookback`` nến TRƯỚC; macd -> histogram. Loại chưa biết vẽ thì không có đường.
    ``bias`` (hướng đã lưu ở db11 theo giờ mở nến) tô màu từng điểm của histogram (macd) và của đường SMA (sma): xanh +1, đỏ -1, xám 0 hoặc chưa có kết quả; không truyền thì macd tô theo dấu của histogram."""
    times = frame.loc[window, "bartime"]
    if kind == "sma":
        period = int(params["period"])
        average = sma(pd.to_numeric(frame["close"], errors="coerce"), period)[window]
        data = colored_points(times, average, bias) if bias is not None else line_points(times, average)
        return [_series(f"{name}.sma", f"SMA {period}", "line", LINE_COLORS[0], data)]
    if kind == "break":
        lookback = int(params["lookback"])
        top = pd.to_numeric(frame["high"], errors="coerce").shift(1).rolling(lookback).max()
        bottom = pd.to_numeric(frame["low"], errors="coerce").shift(1).rolling(lookback).min()
        return [
            _series(f"{name}.high", f"Breakout {lookback} high", "line", BUY_COLOR, line_points(times, top[window])),
            _series(f"{name}.low", f"Breakout {lookback} low", "line", SELL_COLOR, line_points(times, bottom[window])),
        ]
    if kind == "macd":
        fast, slow, signal = int(params["fast"]), int(params["slow"]), int(params["signal"])
        hist = macd_hist(pd.to_numeric(frame["close"], errors="coerce"), fast=fast, slow=slow, signal=signal)
        data = colored_points(times, hist[window], bias) if bias is not None else histogram_points(times, hist[window])
        return [_series(f"{name}.hist", f"MACD Histogram {fast}/{slow}/{signal}", "histogram", NEUTRAL_COLOR, data)]
    return []


def stored_bias(results: pd.DataFrame) -> dict[pd.Timestamp, int]:
    """Bias đã lưu (+1/-1/0) theo giờ mở nến, lấy từ kết quả của yếu tố ở db11 (rỗng nếu chưa có kết quả)."""
    if results.empty or "trend_bias" not in results.columns:
        return {}
    return {pd.Timestamp(stamp): int(value) for stamp, value in zip(results["bartime"], results["trend_bias"], strict=True) if np.isfinite(value)}


def bias_points(window_frame: pd.DataFrame, results: pd.DataFrame) -> list[dict[str, Any]]:
    """Bias đã lưu (+1/-1/0) của từng nến trong cửa sổ, lấy từ kết quả yếu tố ở db11; nến chưa có kết quả thì bỏ qua."""
    stored = stored_bias(results)
    return [{"time": at, "value": stored[pd.Timestamp(stamp)]} for at, stamp in zip(unix_times(window_frame["bartime"]), window_frame["bartime"], strict=True) if pd.Timestamp(stamp) in stored]


def _side(value: Any) -> str:
    return "BUY" if float(value) > 0 else "SELL"


def _unix(stamp: pd.Timestamp) -> int:
    return int((stamp - _EPOCH) // _ONE_SECOND)


def _raw_events(raw: pd.DataFrame, position: Mapping[pd.Timestamp, int]) -> list[tuple[int, pd.Timestamp, int]]:
    """Tín hiệu thô nằm trong bảng nến trigger đang đọc, tăng dần theo vị trí nến: ``(vị trí, giờ mở nến, hướng +1/-1)``. Vị trí là chỉ số của nến liên tiếp trong bảng (không phải giờ), đúng cách
    engine đếm số nến dời nên nghỉ phiên không làm sai số nến."""
    events = []
    for opened, value in zip(raw.get("bartime", []), raw.get("signal", []), strict=False):
        stamp = pd.Timestamp(opened)
        if np.isfinite(value) and value != 0 and stamp in position:
            events.append((position[stamp], stamp, 1 if value > 0 else -1))
    return sorted(events)


def final_rows(
    signals: list[tuple[str, dict[str, str]]], first: pd.Timestamp, length: pd.Timedelta, raw: pd.DataFrame, max_shift_bars: int, position: Mapping[pd.Timestamp, int]
) -> list[dict[str, Any]]:
    """Tín hiệu cuối (DES đã quyết định) từ ``first`` trở đi thành dòng bảng/dấu: giờ mở nến phát, giờ đóng, hướng, loại lệnh, giá chờ, atr, hạn chót, lý do, và LIÊN KẾT với tín hiệu thô gốc.
    DES không ghi gốc của tín hiệu cuối nên chart tìm: gốc = tín hiệu thô cùng chiều gần nhất ở nến phát hoặc trước nó không quá ``max_shift_bars`` NẾN (đếm theo ``position``, vị trí các nến trigger
    liên tiếp): ``origin`` và ``shift`` (số nến bị dời). Không có gốc mà nến phát nằm trong vùng đã có kết quả thô thì ``orphan`` (dữ liệu ghi bởi luật hoặc config cũ, chart chỉ báo chứ không vẽ
    thành một loại dấu riêng); nến phát cũ hơn kết quả thô còn giữ thì không biết gốc và không có cờ nào."""
    events = _raw_events(raw, position)
    covered_from = events[0][1] if events else None
    rows: list[dict[str, Any]] = []
    for stamp, fields in signals:
        opened = pd.Timestamp(stamp)
        if opened < first:
            continue
        side = 1 if str(fields.get("tradeSide", "")).upper() == "BUY" else -1
        fire = position.get(opened)
        origin = None if fire is None else max(((index, at) for index, at, direction in events if direction == side and fire - max_shift_bars <= index <= fire), default=None)
        expires = _number(fields.get("expirationTimestamp"))
        rows.append(
            {
                "time": _unix(opened),
                "bar": opened.strftime(STAMP_FORMAT),
                "close": (opened + length).strftime(STAMP_FORMAT),
                "side": str(fields.get("tradeSide", "")).upper(),
                "kind": "final",
                "orderType": fields.get("orderType", ""),
                "stop": _number(fields.get("stopPrice")),
                "atr": _number(fields.get("atr")),
                "until": None if expires is None else (_EPOCH + pd.Timedelta(seconds=expires)).strftime(STAMP_FORMAT),
                "reason": fields.get("comment", ""),
                "origin": None if origin is None else origin[1].strftime(STAMP_FORMAT),
                "shift": None if origin is None else fire - origin[0],
                "orphan": origin is None and fire is not None and covered_from is not None and opened >= covered_from,
            }
        )
    return rows


def raw_rows(
    results: pd.DataFrame, first: pd.Timestamp, length: pd.Timedelta, finals: list[dict[str, Any]], last_bar: pd.Timestamp, max_shift_bars: int, position: Mapping[pd.Timestamp, int]
) -> list[dict[str, Any]]:
    """Tín hiệu thô của chiến lược (``L_MODSTR_*``) từ ``first`` trở đi thành dòng bảng/dấu. ``decidedAt`` = nến phát của tín hiệu cuối lấy nó làm gốc (None nếu chưa có); ``pending`` = nến mới nhất
    còn cách nến tín hiệu không quá ``max_shift_bars`` nến (đếm theo ``position``) nên DES có thể còn đang chờ trend."""
    rows: list[dict[str, Any]] = []
    if results.empty or "signal" not in results.columns:
        return rows
    decided = {row["origin"]: row["bar"] for row in finals if row["origin"] is not None}
    last = position.get(last_bar)
    for opened, signal, reason in zip(results["bartime"], results["signal"], results.get("reason", pd.Series("", index=results.index)), strict=True):
        if pd.Timestamp(opened) < first or not np.isfinite(signal) or signal == 0:
            continue
        opened = pd.Timestamp(opened)
        index = position.get(opened)
        rows.append(
            {
                "time": _unix(opened),
                "bar": opened.strftime(STAMP_FORMAT),
                "close": (opened + length).strftime(STAMP_FORMAT),
                "side": _side(signal),
                "kind": "raw",
                "orderType": "",
                "stop": None,
                "atr": None,
                "until": None,
                "reason": "" if pd.isna(reason) else str(reason),
                "origin": None,
                "shift": None,
                "orphan": False,
                "decidedAt": decided.get(opened.strftime(STAMP_FORMAT)),
                "pending": last is not None and index is not None and last - index <= max_shift_bars,
            }
        )
    return rows


# -- nến trend mà engine tham chiếu ----------------------------------------------------------------------------------------------------------------


def reference_at(results: pd.DataFrame, close_time: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp, int | None] | None:
    """Nến của một yếu tố trend mà engine tham chiếu cho nến trigger đóng lúc ``close_time`` (luật reality epoch, giống ``decision_engine.merge_closed_reference``): nến trend đã lưu có giờ đóng KHÔNG
    MUỘN HƠN ``close_time`` và gần nhất (bằng nhau vẫn tính). Trả ``(giờ mở, giờ đóng, bias)`` (bias None nếu nến đó chưa có giá trị) hoặc None khi chưa có nến nào."""
    if results.empty or "close_time" not in results.columns:
        return None
    index = int(results["close_time"].searchsorted(close_time, side="right")) - 1
    if index < 0:
        return None
    value = results["trend_bias"].iloc[index] if "trend_bias" in results.columns else np.nan
    return pd.Timestamp(results["bartime"].iloc[index]), pd.Timestamp(results["close_time"].iloc[index]), int(value) if np.isfinite(value) else None


def evaluate_bar(references: list[tuple[str, pd.DataFrame]], direction: int, opened: pd.Timestamp, length: pd.Timedelta) -> dict[str, Any]:
    """Việc engine làm ở MỘT nến trigger mở lúc ``opened``: với từng yếu tố trend (``references``: tên và kết quả đã lưu), nến trend nó nhìn (``reference_at`` ở giờ đóng của nến trigger) và hướng của nến đó
    có cùng chiều ``direction`` không. ``ok`` = MỌI yếu tố cùng chiều (điều kiện để phát)."""
    closes = opened + length
    items = []
    for name, results in references:
        found = reference_at(results, closes)
        bias = None if found is None else found[2]
        items.append(
            {
                "name": name,
                "time": None if found is None else _unix(found[0]),
                "bar": None if found is None else found[0].strftime(STAMP_FORMAT),
                "close": None if found is None else found[1].strftime(STAMP_FORMAT),
                "bias": bias,
                "agree": bias == direction,
            }
        )
    return {"time": _unix(opened), "bar": opened.strftime(STAMP_FORMAT), "close": closes.strftime(STAMP_FORMAT), "ok": all(item["agree"] for item in items), "refs": items}


def attach_chains(
    finals: list[dict[str, Any]],
    raws: list[dict[str, Any]],
    events: list[tuple[int, pd.Timestamp, int]],
    references: list[tuple[str, pd.DataFrame]],
    bar_times: list[pd.Timestamp],
    position: Mapping[pd.Timestamp, int],
    length: pd.Timedelta,
    max_shift_bars: int,
) -> list[dict[str, Any]]:
    """Gom các dòng thành CHUỖI kiểm trend và ghi ``chain`` (chỉ số trong danh sách trả về) vào từng dòng. Một chuỗi = một tín hiệu thô cùng tín hiệu cuối sinh ra từ nó (nếu có) và các nến trigger engine đã xét,
    từ nến gốc tới nến phát: ``raw`` và ``fired`` (giờ mở nến, None nếu không có), ``evals`` (``evaluate_bar`` cho từng nến). Tín hiệu thô không được phát thì các nến xét là từ nến gốc tới
    tối đa ``max_shift_bars`` nến sau, dừng trước nến có tín hiệu thô NGƯỢC chiều (hủy), đúng như ``decision_engine.decide``. Tín hiệu cuối không có gốc chỉ có nến phát của nó."""
    chains: list[dict[str, Any]] = []
    index_of: dict[tuple[str | None, str | None], int] = {}
    last = len(bar_times) - 1

    def chain(raw_bar: str | None, fired_bar: str | None, side: str, start: int, end: int) -> int:
        key = (raw_bar, fired_bar)
        if key not in index_of:
            direction = 1 if side == "BUY" else -1
            index_of[key] = len(chains)
            chains.append({"raw": raw_bar, "fired": fired_bar, "side": side, "evals": [evaluate_bar(references, direction, bar_times[at], length) for at in range(start, end + 1)]})
        return index_of[key]

    for row in finals:
        fire = position.get(pd.Timestamp(row["bar"]))
        if fire is None:
            row["chain"] = None
            continue
        start = fire if row["origin"] is None else position[pd.Timestamp(row["origin"])]
        row["chain"] = chain(row["origin"], row["bar"], row["side"], start, fire)
    for row in raws:
        start = position.get(pd.Timestamp(row["bar"]))
        if start is None:
            row["chain"] = None
        elif row["decidedAt"] is not None:
            row["chain"] = index_of[(row["bar"], row["decidedAt"])]
        else:
            direction = 1 if row["side"] == "BUY" else -1
            end = min(start + max_shift_bars, last)
            cancelled = next((index for index, _, other in events if index > start and other != direction), None)
            row["chain"] = chain(row["bar"], None, row["side"], start, end if cancelled is None else min(end, cancelled - 1))
    return chains


def _duration_label(seconds: int) -> str:
    if seconds % 3600 == 0:
        return f"{seconds // 3600} h"
    if seconds % 60 == 0:
        return f"{seconds // 60} min"
    return f"{seconds} s"


def _params_label(strategy: configuration.Strategy) -> str:
    """Dòng tham số ở đầu trang bằng chữ chuẩn: SMA, MACD Histogram theo tham số chiến lược khai ở config (tham số lạ in nguyên ``tên giá trị``) + các số quyết định (ATR, số nến dời, hiệu lực, loại lệnh)."""
    params, used, parts = strategy.params, set(), []
    if "fast_ma" in params and "slow_ma" in params:
        parts.append(f"SMA {int(params['fast_ma'])}/{int(params['slow_ma'])}")
        used |= {"fast_ma", "slow_ma"}
    elif "ma_period" in params:
        parts.append(f"SMA {int(params['ma_period'])}")
        used.add("ma_period")
    if all(name in params for name in _MACD_PARAMS):
        parts.append("MACD Histogram " + "/".join(str(int(params[name])) for name in _MACD_PARAMS))
        used |= set(_MACD_PARAMS)
    parts += [f"{key} {value}" for key, value in params.items() if key not in used]
    shift = f"shift up to {strategy.max_shift_bars} bars" if strategy.max_shift_bars else "no shift"
    parts += [f"ATR {strategy.atr_period}", shift, f"valid {_duration_label(strategy.validity_seconds)}", f"{strategy.order_type} order"]
    return " · ".join(parts)


def _read_window(frame: pd.DataFrame, bars: int) -> pd.Series:
    """Mặt nạ ``bars`` nến cuối của bảng (bảng ngắn hơn thì lấy cả bảng)."""
    mask = pd.Series(False, index=frame.index)
    mask.iloc[max(0, len(frame) - bars) :] = True
    return mask


def build_view(ctx: Context, req: ViewRequest, token: str | None) -> dict[str, Any]:
    """Khung nhìn của một yêu cầu: pane giá (+ MACD Histogram của chiến lược), một pane cho mỗi yếu tố trend tham chiếu, bảng tín hiệu, các chuỗi kiểm trend, giờ ảo. Dấu vân tay không đổi so với
    ``token`` thì chỉ trả giờ ảo (``changed: false``)."""
    plan = ctx.plan
    strategy = plan.strategies[req.strategy]
    source = _source_of(plan, req.symbol)
    candle_client, output = ctx.connect(source.endpoint, source.db), ctx.connect(plan.output, plan.output["db"])
    clock = read_clock(candle_client, source.clock_key)
    meta: dict[str, Any] = {
        "configVersion": ctx.version, "clock": clock, "strategy": req.strategy, "strategyLabel": display_name(req.strategy), "symbol": req.symbol, "trigger": strategy.trigger, "warnings": [],
    }
    current = fingerprint(ctx, req)
    if token is not None and token == current:
        return {"changed": False, "meta": meta}

    length = configuration.timeframe_duration(strategy.trigger)
    trigger = read_candles(candle_client, symbol=req.symbol, timeframe=strategy.trigger, key_prefix=source.key_prefix, snapshot_bars=READ_BARS)
    meta.update({"paramsLabel": _params_label(strategy), "orderType": strategy.order_type, "keyPrefix": source.key_prefix, "maxShiftBars": strategy.max_shift_bars, "triggerSeconds": int(length.total_seconds())})
    if trigger.empty:
        meta["warnings"].append(f"No {strategy.trigger} candles for {req.symbol} in db{source.db} yet (has DFS played that far?).")
        return {"changed": True, "token": current, "meta": {**meta, "lastCandle": "", "lastSignal": "-"}, "panes": [], "signals": [], "chains": []}

    if req.bars > plan.snapshot_bars:
        meta["warnings"].append(f"DES keeps results for the last {plan.snapshot_bars} bars of each pair, so signals and bias older than that are not drawn.")
    window = _read_window(trigger, req.bars)
    shown = trigger.loc[window].reset_index(drop=True)
    first, last_bar = pd.Timestamp(shown["bartime"].iloc[0]), pd.Timestamp(shown["bartime"].iloc[-1])
    bar_times = [pd.Timestamp(stamp) for stamp in trigger["bartime"]]
    position = {stamp: index for index, stamp in enumerate(bar_times)}
    lines, macd_panel = strategy_series(strategy.params, trigger, window)
    final = read_signals(output, key_prefix=plan.output["signal_prefix"], symbol=req.symbol, timeframe=strategy.trigger, strategy=req.strategy)
    raw = read_module(output, symbol=req.symbol, timeframe=strategy.trigger, module=module_token(req.strategy), key_prefix=plan.output["strategy_prefix"], snapshot_bars=plan.snapshot_bars)
    finals = final_rows(final, first, length, raw, strategy.max_shift_bars, position)
    raws = raw_rows(raw, first, length, finals, last_bar, strategy.max_shift_bars, position)
    markers = [
        {"time": row["time"], "side": row["side"], "kind": row["kind"], "shift": row["shift"]}
        for row in sorted(raws + finals, key=lambda row: (row["time"], row["kind"] == "final"))
    ]
    price = {
        "id": "price",
        "title": f"{display_name(req.strategy)} · {req.symbol} · {strategy.trigger}",
        "group": "Price",
        "factorLabel": "",
        "frame": strategy.trigger,
        "frameSeconds": int(length.total_seconds()),
        "height": 420,
        "hasCandles": True,
        "candles": candle_points(shown),
        "series": lines,
        "bias": [],
        "biasStrip": False,
        "markers": markers,
    }
    panes = [price]
    if macd_panel:
        panes.append(
            {
                "id": "macd", "title": f"{macd_panel[0]['label']} · {strategy.trigger}", "group": f"Strategy · {strategy.trigger}", "factorLabel": "", "frame": strategy.trigger, "frameSeconds": int(length.total_seconds()), "height": 150,
                "hasCandles": False, "candles": [], "series": macd_panel, "bias": [], "biasStrip": False, "markers": [],
            }
        )
    references: list[tuple[str, pd.DataFrame]] = []
    for factor, frame_name in strategy.trends:
        pane, results = _factor_pane(ctx, plan.trends[factor], factor, frame_name, req, source, candle_client, output, first, meta["warnings"])
        panes.append(pane)
        references.append((factor, results))
    chains = attach_chains(finals, raws, _raw_events(raw, position), references, bar_times, position, length, strategy.max_shift_bars)
    orphans, shifted = sum(1 for row in finals if row["orphan"]), sum(1 for row in finals if row["shift"])
    if orphans:
        meta["warnings"].append(f"{orphans} decided signal(s) in view have no matching raw signal: they were written under an older rule or config version.")
    meta.update(
        {"lastCandle": last_bar.strftime(STAMP_FORMAT), "lastSignal": finals[-1]["bar"] if finals else "-", "finalCount": len(finals), "rawCount": len(raws), "orphanCount": orphans, "shiftedCount": shifted}
    )
    rows = sorted(finals + raws, key=lambda row: (row["time"], row["kind"] != "final"), reverse=True)[:SIGNAL_ROWS_MAX]
    return {"changed": True, "token": current, "meta": meta, "panes": panes, "signals": rows, "chains": chains}


def _factor_pane(
    ctx: Context,
    factor: configuration.TrendFactor,
    name: str,
    frame_name: str,
    req: ViewRequest,
    source: configuration.Source,
    candle_client: redis.Redis,
    output: redis.Redis,
    first: pd.Timestamp,
    warnings: list[str],
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Pane của MỘT yếu tố trend ở khung của nó, chỉ chứa những gì yếu tố đó dùng: macd -> chỉ histogram (không nến), mỗi cột tô theo bias đã lưu ở db11; sma -> nến và đường SMA tô theo bias đã lưu;
    break -> nến, đường đỉnh/đáy và một dải bias; loại chưa biết -> nến và dải bias. Nến lấy từ ``first`` lùi hai độ dài khung (nến trend engine tham chiếu cho nến trigger đầu tiên có thể mở sớm
    gần hai khung trước nó). Trả thêm kết quả đã lưu của yếu tố (rỗng nếu chưa có) để kiểm nến trend mà từng tín hiệu đã dùng."""
    plan = ctx.plan
    kind = trend_kind(factor)
    label = trend_label(name, kind, factor.params)
    frame = read_candles(candle_client, symbol=req.symbol, timeframe=frame_name, key_prefix=source.key_prefix, snapshot_bars=READ_BARS)
    pane: dict[str, Any] = {
        "id": f"trend.{name}", "title": f"Trend reference · {label} · {frame_name}", "group": f"Trend · {frame_name}", "factorLabel": label, "frame": frame_name, "frameSeconds": int(configuration.timeframe_duration(frame_name).total_seconds()), "height": 170 if kind == "macd" else 230,
        "hasCandles": kind != "macd", "candles": [], "series": [], "bias": [], "biasStrip": False, "markers": [],
    }
    if frame.empty:
        warnings.append(f"No {frame_name} candles for {req.symbol} yet, so the {label} pane is empty.")
        return pane, pd.DataFrame()
    window = frame["bartime"] >= first - 2 * configuration.timeframe_duration(frame_name)
    shown = frame.loc[window].reset_index(drop=True)
    results = read_module(output, symbol=req.symbol, timeframe=frame_name, module=module_token(name), key_prefix=plan.output["trend_prefix"], snapshot_bars=plan.snapshot_bars)
    if kind != "macd":
        pane["candles"] = candle_points(shown)
    pane["series"] = factor_series(name, kind, factor.params, frame, window, stored_bias(results))
    pane["bias"] = bias_points(shown, results)
    pane["biasStrip"] = kind not in ("macd", "sma")
    if results.empty:
        warnings.append(f"No stored results for {label} on {req.symbol} {frame_name} in db{plan.output['db']} yet.")
    return pane, results


# -- trang HTML ----------------------------------------------------------------------------------------------------------------------------------

_VENDOR_JS_PATH = Path(__file__).resolve().parent / "vendor" / "lightweight-charts.js"

_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>des chart</title>
<style>
  html, body { margin: 0; padding: 0; min-height: 100%; }
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #e2e8f0; }
  #controls { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 10px 16px; border-bottom: 1px solid #1e293b; }
  #controls label { font-size: 13px; }
  #controls select, #controls input { background: #1e293b; color: #e2e8f0; border: 1px solid #334155; border-radius: 4px; padding: 4px 6px; font-size: 13px; }
  #controls input[type=number] { width: 70px; }
  #controls button { background: #2563eb; color: #fff; border: none; border-radius: 4px; padding: 5px 12px; font-size: 13px; cursor: pointer; }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #64748b; }
  .dot.ok { background: #22c55e; }
  .dot.warn { background: #eab308; }
  .dot.bad { background: #ef4444; }
  #statusbar { display: flex; flex-wrap: wrap; gap: 6px 18px; padding: 6px 16px; font-size: 12px; color: #94a3b8; border-bottom: 1px solid #1e293b; }
  #refs { padding: 6px 16px; font-size: 12px; color: #cbd5e1; border-bottom: 1px solid #1e293b; }
  #refs b { color: #e2e8f0; font-weight: 600; }
  #layers { display: flex; flex-wrap: wrap; gap: 4px 14px; padding: 6px 16px; font-size: 12px; color: #cbd5e1; border-bottom: 1px solid #1e293b; min-height: 18px; }
  #layers label { cursor: pointer; max-width: 100%; }
  #error { display: none; padding: 8px 16px; background: #7f1d1d; color: #fecaca; font-size: 13px; }
  #warnings { padding: 4px 16px; font-size: 12px; color: #fbbf24; }
  .pane { border-bottom: 1px solid #1e293b; }
  .pane-head { display: flex; flex-wrap: wrap; gap: 4px 14px; padding: 4px 16px; font-size: 12px; color: #94a3b8; }
  .pane-head b { color: #e2e8f0; font-weight: 600; }
  .pane-legend { color: #cbd5e1; }
  .pane-chart { width: 100%; position: relative; }
  .pane-key { margin-left: auto; color: #94a3b8; }
  .pane-key i { display: inline-block; width: 9px; height: 9px; margin: 0 4px 0 9px; border-radius: 2px; vertical-align: -1px; }
  .chart-legend { position: absolute; top: 6px; right: 74px; z-index: 5; max-width: 250px; padding: 4px 8px; font-size: 11px; line-height: 1.55; color: #cbd5e1; background: rgba(15,23,42,0.9); border: 1px solid #334155; border-radius: 4px; }
  .chart-legend .legend-title { cursor: pointer; font-weight: 600; color: #e2e8f0; user-select: none; }
  .chart-legend .legend-row { display: flex; gap: 8px; align-items: baseline; }
  .chart-legend .legend-sym { flex: none; min-width: 34px; white-space: nowrap; }
  .chart-legend .legend-hint { margin-top: 3px; color: #94a3b8; }
  #summary { padding: 8px 16px; font-size: 13px; color: #cbd5e1; border-top: 1px solid #1e293b; }
  #signal-detail { display: none; position: fixed; left: 0; right: 0; bottom: 0; z-index: 20; max-height: 32vh; overflow: auto; margin: 0; padding: 8px 16px; font-size: 12px; line-height: 1.6; background: #111c33; border-top: 3px solid #64748b; box-shadow: 0 -6px 14px rgba(0,0,0,0.45); overflow-wrap: anywhere; }
  #signal-detail[data-side="BUY"] { border-top-color: #22c55e; }
  #signal-detail[data-side="SELL"] { border-top-color: #ef4444; }
  #signal-detail .detail-close { float: right; background: none; border: none; color: #94a3b8; font-size: 18px; line-height: 1; cursor: pointer; }
  #signal-detail .head { font-weight: 600; }
  #signal-detail .note { color: #94a3b8; }
  #signals-wrap { padding: 0 16px 24px; overflow-x: auto; }
  #signals { border-collapse: collapse; width: 100%; font-size: 12px; }
  #signals th { text-align: left; color: #94a3b8; font-weight: 500; padding: 5px 8px; border-bottom: 1px solid #334155; }
  #signals td { padding: 4px 8px; border-bottom: 1px solid #1e293b; vertical-align: top; }
  #signals tr.kind-raw td { color: #94a3b8; }
  .side-BUY { color: #22c55e; font-weight: 600; }
  .side-SELL { color: #ef4444; font-weight: 600; }
</style>
</head>
<body>
<div id="controls">
  <label>Strategy <select id="strategy"></select></label>
  <label>Symbol <select id="symbol"></select></label>
  <label>Bars <input id="bars" type="number" step="50"></label>
  <label>Auto
    <select id="auto">
      <option value="1000">1s</option>
      <option value="2000" selected>2s</option>
      <option value="5000">5s</option>
      <option value="0">Off</option>
    </select>
  </label>
  <button id="refresh">Refresh</button>
  <span id="live-dot" class="dot"></span>
</div>
<div id="statusbar"><span id="clock"></span><span id="config"></span><span id="pair"></span><span id="updated"></span></div>
<div id="refs"></div>
<div id="layers"></div>
<div id="error"></div>
<div id="warnings"></div>
<div id="panes"></div>
<div id="summary"></div>
<div id="signal-detail"></div>
<div id="signals-wrap">
  <table id="signals">
    <thead><tr><th>Bar open (UTC)</th><th>Bar close (UTC)</th><th>Kind</th><th>Side</th><th>Order</th><th>Stop</th><th>ATR</th><th>Valid until</th><th>Link raw &harr; decided</th><th>Reason</th></tr></thead>
    <tbody></tbody>
  </table>
</div>
<script>
__VENDOR_JS__
</script>
<script>
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const el = {
    strategy: $("strategy"), symbol: $("symbol"), bars: $("bars"), auto: $("auto"), refresh: $("refresh"), dot: $("live-dot"),
    clock: $("clock"), config: $("config"), pair: $("pair"), updated: $("updated"), layers: $("layers"), error: $("error"), warnings: $("warnings"),
    panes: $("panes"), summary: $("summary"), detail: $("signal-detail"), refs: $("refs"), tbody: document.querySelector("#signals tbody"),
  };
  const BUY = "#16a34a", SELL = "#dc2626", NEUTRAL = "#94a3b8";
  const DOT_BUY = "rgba(34,197,94,0.9)", DOT_SELL = "rgba(239,68,68,0.9)";   // chấm tròn = tín hiệu thô
  const initialQuery = Object.fromEntries(new URLSearchParams(window.location.search));   // đọc trước khi updateUrl ghi đè

  let catalog = null;          // danh mục từ /api/catalog (đọc từ config DES)
  let view = null;             // khung nhìn đang vẽ
  let token = null;            // dấu vân tay của khung nhìn đang vẽ
  let viewKey = "";
  let layoutKey = "";
  let timer = null, loading = false, requestId = 0, syncing = false, crossSyncing = false;
  const hidden = new Set();    // khoá các lớp người xem đã tắt
  const panes = new Map();     // id pane -> { chart, candle, lines: Map(key -> series), bias, legend, byTime }

  // ---------------------------------------------------------------- tiện ích
  function showError(message) { el.error.textContent = message; el.error.style.display = "block"; }
  function clearError() { el.error.style.display = "none"; el.error.textContent = ""; }
  function setDot(kind) { el.dot.className = "dot " + kind; }
  function fmt(value) {
    if (value === undefined || value === null || !Number.isFinite(Number(value))) return "-";
    return Number(value).toLocaleString("en-US", { maximumFractionDigits: 6 });
  }
  function fmtTime(unix) { return new Date(unix * 1000).toISOString().slice(0, 16).replace("T", " "); }
  function clampBars(value) {
    const n = Number.isFinite(value) ? Math.round(value) : catalog.defaultBars;
    return Math.min(catalog.barsMax, Math.max(catalog.barsMin, n));
  }
  // items: giá trị thuần hoặc cặp [giá trị, nhãn hiển thị].
  function setOptions(select, items, selected) {
    const pairs = items.map((item) => (Array.isArray(item) ? item : [item, item]));
    select.replaceChildren(...pairs.map(([value, text]) => {
      const option = document.createElement("option");
      option.value = value; option.textContent = text;
      return option;
    }));
    if (pairs.some(([value]) => value === selected)) select.value = selected;
  }
  function cell(text, className) {
    const td = document.createElement("td");
    td.textContent = text;
    if (className) td.className = className;
    return td;
  }

  // ----------------------------------------------------------- bộ chọn (từ config)
  function populate(initial) {
    const names = Object.keys(catalog.strategies);
    const strategy = names.includes(initial.strategy) ? initial.strategy : names[0];
    setOptions(el.strategy, names.map((name) => [name, catalog.strategies[name].label]), strategy);
    populateSymbols(initial.symbol);
    renderRefs();
    el.bars.value = String(clampBars(parseInt(initial.bars || "", 10)));
    el.bars.min = catalog.barsMin; el.bars.max = catalog.barsMax;
  }
  function populateSymbols(selected) {
    setOptions(el.symbol, catalog.strategies[el.strategy.value].symbols, selected);
  }
  // Dòng "chiến lược này tham chiếu trend nào": khung trigger, loại lệnh và mọi yếu tố trend (tên chuẩn + khung); tín hiệu chỉ được phát khi TẤT CẢ cùng chiều.
  function renderRefs() {
    const info = catalog && catalog.strategies[el.strategy.value];
    if (!info) { el.refs.textContent = ""; return; }
    const refs = info.trends.map((t) => t.label + " @ " + t.frame).join("  ·  ");
    el.refs.replaceChildren();
    const head = document.createElement("b"); head.textContent = info.label + ": ";
    el.refs.append(head, "trigger " + info.trigger + " · " + info.orderType + " order · trend reference (all must agree with the signal): " + refs);
  }
  function currentSelection() { return { strategy: el.strategy.value, symbol: el.symbol.value, bars: el.bars.value }; }
  function updateUrl() { if (!el.strategy.value) return; window.history.replaceState(null, "", "?" + new URLSearchParams(currentSelection()).toString()); }

  // ------------------------------------------------------------------- chart
  function chartOptions(height) {
    return {
      height: height,
      layout: { background: { color: "#0f172a" }, textColor: "#e2e8f0" },
      grid: { vertLines: { color: "#1e293b" }, horzLines: { color: "#1e293b" } },
      timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 6 },
    };
  }

  // Đồng bộ khoảng nhìn theo THỜI GIAN giữa mọi pane (các pane khác khung nên số nến khác nhau). Cờ ``syncing`` chống vòng lặp sự kiện.
  function syncRanges(source) {
    return (range) => {
      if (!range || syncing) return;
      syncing = true;
      try {
        panes.forEach((pane) => {
          if (pane.chart === source) return;
          try { pane.chart.timeScale().setVisibleRange(range); } catch (err) { /* khoảng nằm ngoài dữ liệu của pane này */ }
        });
      } finally { syncing = false; }
    };
  }

  function destroyPanes() {
    panes.forEach((pane) => pane.chart.remove());
    panes.clear();
    el.panes.replaceChildren();
  }

  function buildPanes(defs) {
    destroyPanes();
    defs.forEach((def) => {
      const wrap = document.createElement("div");
      wrap.className = "pane";
      const head = document.createElement("div");
      head.className = "pane-head";
      const title = document.createElement("b");
      title.textContent = def.title;
      const legend = document.createElement("span");
      legend.className = "pane-legend";
      const key = document.createElement("span");
      key.className = "pane-key";
      key.append(...paneKey(def));
      head.append(title, legend, key);
      const holder = document.createElement("div");
      holder.className = "pane-chart";
      wrap.append(head, holder);
      el.panes.appendChild(wrap);
      const chart = LightweightCharts.createChart(holder, chartOptions(def.height));
      const pane = { def: def, chart: chart, holder: holder, legend: legend, box: null, titleEl: title, candle: null, lines: new Map(), bias: null, byTime: new Map() };
      // Đường (SMA, breakout) tạo TRƯỚC nến để nằm DƯỚI nến: nến luôn đọc được, đường không che nến.
      def.series.filter((s) => s.kind !== "histogram").forEach((s) => seriesFor(pane, s));
      if (def.hasCandles) {
        pane.candle = chart.addCandlestickSeries({ upColor: BUY, downColor: SELL, borderVisible: false, wickUpColor: BUY, wickDownColor: SELL });
      }
      if (def.id === "price") { pane.box = document.createElement("div"); pane.box.className = "chart-legend"; holder.appendChild(pane.box); }
      panes.set(def.id, pane);
      chart.timeScale().subscribeVisibleTimeRangeChange(syncRanges(chart));
      chart.subscribeCrosshairMove((param) => onCrosshair(pane, param));
      if (def.id === "price") chart.subscribeClick(showDetail);
    });
    resize();
  }

  function resize() { panes.forEach((pane) => pane.chart.resize(pane.holder.clientWidth, pane.def.height)); }

  function seriesFor(pane, def) {
    let series = pane.lines.get(def.key);
    if (!series) {
      if (def.kind === "histogram") {
        series = pane.chart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
      } else {
        series = pane.chart.addLineSeries({ color: def.color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      }
      pane.lines.set(def.key, series);
    }
    return series;
  }

  // Tên lớp trong danh sách công tắc: nhóm của pane (Price, Strategy · M10, Trend · M30) + tên lớp, nên hai lớp cùng tên ở hai pane không lẫn nhau.
  function layerKeys(def) {
    const keys = def.series.map((s) => ({ key: s.key, label: def.group + ": " + s.label }));
    if (def.biasStrip && def.bias.length) keys.push({ key: def.id + ".bias", label: def.group + ": " + def.factorLabel + " bias strip" });
    return keys;
  }

  function applyPane(def) {
    const pane = panes.get(def.id);
    pane.def = def;
    pane.titleEl.textContent = def.title;   // đổi symbol mà bố cục pane giữ nguyên thì tiêu đề vẫn phải theo
    pane.byTime = new Map();
    if (pane.candle) {
      pane.candle.setData(def.candles);
      def.candles.forEach((c) => pane.byTime.set(c.time, { candle: c, price: c.close }));
    }
    const wanted = new Set(def.series.map((s) => s.key));
    pane.lines.forEach((series, key) => { if (!wanted.has(key)) { pane.chart.removeSeries(series); pane.lines.delete(key); } });
    def.series.forEach((s) => {
      const series = seriesFor(pane, s);
      series.setData(s.data);
      series.applyOptions({ visible: !hidden.has(s.key) });
      s.data.forEach((p) => { if (p.value !== undefined) { const slot = pane.byTime.get(p.time) || {}; (slot.values = slot.values || []).push(s.label + " " + fmt(p.value)); if (slot.price === undefined) slot.price = p.value; pane.byTime.set(p.time, slot); } });
    });
    if (def.biasStrip && def.bias.length) {
      if (!pane.bias) {
        pane.bias = pane.chart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false, priceScaleId: "bias" });
        pane.bias.priceScale().applyOptions({ scaleMargins: { top: 0.93, bottom: 0 } });
      }
      pane.bias.setData(def.bias.map((b) => ({ time: b.time, value: 1, color: b.value > 0 ? BUY : b.value < 0 ? SELL : NEUTRAL })));
      pane.bias.applyOptions({ visible: !hidden.has(def.id + ".bias") });
    } else if (pane.bias) {
      pane.chart.removeSeries(pane.bias); pane.bias = null;
    }
    def.bias.forEach((b) => { const slot = pane.byTime.get(b.time) || {}; slot.bias = b.value; pane.byTime.set(b.time, slot); });   // bias đã lưu luôn hiện ở legend, dù vẽ bằng dải hay bằng màu
    pane.times = [...pane.byTime.keys()].sort((a, b) => a - b);   // giờ mở các nến của pane, để tìm nến trend tham chiếu
    if (def.id === "price") { applyMarkers(pane); renderLegend(pane); }
  }

  // Chú thích tĩnh ở góc phải đầu pane: màu của pane (hướng trend, dấu histogram) và dấu "ref" khi bấm vào một tín hiệu.
  function swatch(color) { const i = document.createElement("i"); i.style.background = color; return i; }
  function paneKey(def) {
    if (def.id.startsWith("trend.")) return [swatch(BUY), "+1 BUY", swatch(SELL), "-1 SELL", swatch(NEUTRAL), "0 / no result", "  ·  ref marker = trend bar used by the clicked signal"];
    if (def.id === "macd") return [swatch(BUY), "> 0", swatch(SELL), "< 0"];
    return [];
  }

  // Chú thích góc trên bên phải của pane giá (thu gọn được): ý nghĩa của chấm, mũi tên, "+n", các đường và nến. Chỉ nêu những gì chiến lược đang xem có thật.
  let legendOpen = true;
  function colored(text, color) { const s = document.createElement("span"); s.textContent = text; s.style.color = color; return s; }
  function legendRow(symbols, text) {
    const row = document.createElement("div"); row.className = "legend-row";
    const sym = document.createElement("span"); sym.className = "legend-sym"; sym.append(...symbols);
    const label = document.createElement("span"); label.textContent = text;
    row.append(sym, label);
    return row;
  }
  function renderLegend(pane) {
    if (!pane.box) return;
    const title = document.createElement("div");
    title.className = "legend-title"; title.textContent = (legendOpen ? "▾" : "▸") + " Legend";
    title.addEventListener("click", () => { legendOpen = !legendOpen; renderLegend(pane); });
    const items = [title];
    if (legendOpen) {
      items.push(legendRow([colored("●", DOT_BUY), colored("●", DOT_SELL)], "Raw signal (strategy output)"));
      items.push(legendRow([colored("▲", BUY), colored("▼", SELL)], "Decided signal (after trend)"));
      if (view.meta.maxShiftBars > 0) items.push(legendRow([colored("BUY +n", BUY)], "fired n bars late (max " + view.meta.maxShiftBars + ")"));
      else items.push(legendRow([], "No delay: ▲▼ is on the bar of its ●"));
      items.push(legendRow([colored("●", DOT_BUY)], "alone: filtered out by the trend"));
      pane.def.series.filter((s) => s.kind !== "histogram").forEach((s) => items.push(legendRow([colored("━━", s.color)], s.label)));
      items.push(legendRow([colored("▮", BUY), colored("▮", SELL)], "Candle up / down"));
      const hint = document.createElement("div"); hint.className = "legend-hint"; hint.textContent = "Click a signal: marks the trend bars it used";
      items.push(hint);
    }
    pane.box.replaceChildren(...items);
  }

  // Hai lớp dấu tách bạch: CHẤM TRÒN = tín hiệu thô của chiến lược, đặt ở nến tín hiệu gốc; MŨI TÊN = tín hiệu DES đã quyết định sau khi tham chiếu trend, đặt ở nến phát.
  // Tín hiệu bị dời thì mũi tên nằm sau chấm gốc và ghi "+n" (n nến). Không có dấu xám: mọi tín hiệu cuối đều phải từ một tín hiệu thô; chỗ nào không nối được thì chỉ báo ở cảnh báo và bảng.
  function markerFor(m) {
    const buy = m.side === "BUY";
    if (m.kind === "raw") return { time: m.time, position: buy ? "belowBar" : "aboveBar", shape: "circle", color: buy ? DOT_BUY : DOT_SELL, size: 1 };
    return { time: m.time, position: buy ? "belowBar" : "aboveBar", shape: buy ? "arrowUp" : "arrowDown", color: buy ? BUY : SELL, text: m.side + (m.shift ? " +" + m.shift : "") };
  }
  function applyMarkers(pane) {
    const showRaw = !hidden.has("raw"), showFinal = !hidden.has("final");
    pane.candle.setMarkers(pane.def.markers.filter((m) => (m.kind === "raw" ? showRaw : showFinal)).map(markerFor));
  }

  // ---------------------------------------------------------- công tắc lớp vẽ
  function renderLayers(defs) {
    const items = [];
    defs.forEach((def) => layerKeys(def).forEach((k) => items.push(k)));
    if (defs.some((d) => d.markers.length)) {
      items.push({ key: "raw", label: "● Raw signals (strategy output)" });
      items.push({ key: "final", label: "▲▼ Decided signals (DE, after trend reference)" });
    }
    el.layers.replaceChildren(...items.map((item) => {
      const label = document.createElement("label");
      const box = document.createElement("input");
      box.type = "checkbox"; box.checked = !hidden.has(item.key);
      box.addEventListener("change", () => { if (box.checked) hidden.delete(item.key); else hidden.add(item.key); applyLayerVisibility(); });
      label.append(box, " " + item.label);
      return label;
    }));
  }
  function applyLayerVisibility() {
    panes.forEach((pane) => {
      pane.lines.forEach((series, key) => series.applyOptions({ visible: !hidden.has(key) }));
      if (pane.bias) pane.bias.applyOptions({ visible: !hidden.has(pane.def.id + ".bias") });
      if (pane.def.id === "price") applyMarkers(pane);
    });
  }

  // ------------------------------------------------------- legend / chi tiết
  function showLegend(pane, time) {
    const slot = time === undefined || time === null ? null : pane.byTime.get(time);
    if (!slot) { pane.legend.textContent = ""; return; }
    const parts = [fmtTime(time) + " UTC"];
    if (slot.candle) parts.push("O " + fmt(slot.candle.open), "H " + fmt(slot.candle.high), "L " + fmt(slot.candle.low), "C " + fmt(slot.candle.close));
    if (slot.values) parts.push(...slot.values);
    if (slot.bias !== undefined) parts.push("bias " + (slot.bias > 0 ? "+1 (BUY only)" : slot.bias < 0 ? "-1 (SELL only)" : "0 (neutral)"));
    pane.legend.textContent = parts.join("  ·  ");
  }

  // Crosshair đồng bộ: rê hoặc bấm trên một pane CÙNG KHUNG TRIGGER (giá, MACD của chiến lược) thì mọi pane khác hiện crosshair và số liệu ở đúng nến đó. Pane cùng khung lấy cùng giờ; pane trend lấy nến
  // trend mà engine đã tham chiếu (luật reality epoch: nến trend ĐÃ ĐÓNG gần nhất có giờ đóng không muộn hơn giờ đóng của nến trigger), cùng nến với dấu "ref". Rê trên pane trend không đồng bộ ngược lại.
  function referencedTime(pane, triggerTime) {
    const closeAt = triggerTime + view.meta.triggerSeconds, length = pane.def.frameSeconds, times = pane.times;
    let low = 0, high = times.length - 1, found = null;
    while (low <= high) {
      const mid = (low + high) >> 1;
      if (times[mid] + length <= closeAt) { found = times[mid]; low = mid + 1; } else { high = mid - 1; }
    }
    return found;
  }
  function crosshairTarget(pane, triggerTime) {
    if (pane.def.frame === view.meta.trigger) return pane.byTime.has(triggerTime) ? triggerTime : null;
    return referencedTime(pane, triggerTime);
  }
  function syncCrosshair(source, triggerTime) {
    crossSyncing = true;   // setCrosshairPosition làm các pane kia phát sự kiện crosshair: cờ này chặn vòng lặp
    try {
      panes.forEach((pane) => {
        if (pane === source) return;
        const target = triggerTime === null ? null : crosshairTarget(pane, triggerTime);
        const slot = target === null ? null : pane.byTime.get(target);
        const series = markerSeries(pane);
        if (!slot || !series) { pane.chart.clearCrosshairPosition(); showLegend(pane, null); return; }
        pane.chart.setCrosshairPosition(slot.price === undefined ? 0 : slot.price, target, series);
        showLegend(pane, target);
      });
    } finally { crossSyncing = false; }
  }
  function onCrosshair(pane, param) {
    if (crossSyncing) return;
    showLegend(pane, param.time);
    if (view && pane.def.frame === view.meta.trigger) syncCrosshair(pane, param.time === undefined ? null : param.time);
  }
  // Cột liên kết giữa tín hiệu thô và tín hiệu đã quyết định.
  function linkText(r) {
    const noShift = view && view.meta && view.meta.maxShiftBars === 0;   // chiến lược không dời tín hiệu (Combo)
    if (r.kind === "final") {
      if (r.orphan) return "No matching raw signal (written under an older rule or config version)";
      if (r.origin === null || r.origin === undefined) return "Raw signal is older than the stored results";
      return "from raw signal " + r.origin + (r.shift ? " (delayed " + r.shift + " bar" + (r.shift > 1 ? "s" : "") + " until the trend reference agreed)" : " (decided on the same bar)");
    }
    if (r.decidedAt) return "decided on bar " + r.decidedAt;
    if (noShift) return r.pending ? "waiting for the trend reference" : "not published (the trend reference did not agree on this bar)";
    return r.pending ? "waiting (still inside the shift window)" : "not published (the trend reference did not agree within the shift window, or an opposite signal cancelled it)";
  }
  // Bấm vào một nến trigger có tín hiệu: hiện chi tiết và, với MỖI yếu tố trend, đánh dấu "ref" đúng nến trend mà engine tham chiếu (luật reality epoch: nến trend đã đóng gần nhất, giờ đóng không muộn
  // hơn giờ đóng của nến trigger). Tín hiệu bị dời thì có nhiều nến trigger được xét (từ nến gốc tới nến phát), mỗi nến một dòng và một dấu "ref n" (n = số nến sau nến gốc).
  let selected = null;   // giờ (unix) của nến trigger đang được chọn
  function markerSeries(pane) { return pane.candle || pane.lines.values().next().value || null; }
  function clearReferenceMarks() {
    panes.forEach((pane, id) => { if (id.startsWith("trend.")) { const series = markerSeries(pane); if (series) series.setMarkers([]); } });
  }
  function hideDetail() {
    selected = null;
    clearReferenceMarks();
    el.detail.style.display = "none"; el.detail.replaceChildren(); delete el.detail.dataset.side;
  }
  function line(text, className) { const d = document.createElement("div"); d.textContent = text; if (className) d.className = className; return d; }
  function biasColor(bias) { return bias > 0 ? BUY : bias < 0 ? SELL : NEUTRAL; }
  function showDetail(param) { select(param.time === undefined ? null : param.time); }
  function select(time) {
    const rows = time === null || !view ? [] : view.signals.filter((r) => r.time === time);
    if (!rows.length) { hideDetail(); return; }
    selected = time;
    const close = document.createElement("button");
    close.className = "detail-close"; close.textContent = "×"; close.title = "Close"; close.addEventListener("click", hideDetail);
    const lines = [close];
    rows.forEach((r) => {
      lines.push(line((r.kind === "final" ? "DECIDED " : "RAW ") + r.side + " · bar open " + r.bar + " UTC · close " + r.close + " UTC", "head"));
      if (r.kind === "final") {
        lines.push(line("Order " + (r.orderType || "-") + (r.orderType === "STOP" ? " · stop " + fmt(r.stop) : "") + " · ATR " + fmt(r.atr) + " · valid until " + (r.until || "-") + " UTC"));
      }
      lines.push(line(linkText(r)));
      if (r.reason) lines.push(line("Reason: " + r.reason));
    });
    const seen = new Set();
    const marks = new Map();   // id pane trend -> Map(giờ nến trend -> dấu "ref")
    rows.forEach((r) => {
      if (r.chain === null || r.chain === undefined || seen.has(r.chain)) return;
      seen.add(r.chain);
      const chain = view.chains[r.chain];
      chain.evals.forEach((ev, step) => {
        const fired = ev.bar === chain.fired;
        ev.refs.forEach((x) => {
          if (x.time === null) return;
          const id = "trend." + x.name;
          const byTime = marks.get(id) || new Map();
          const mark = byTime.get(x.time) || { time: x.time, bias: x.bias, steps: [], decisive: false, total: chain.evals.length };
          mark.steps.push(step);
          mark.decisive = mark.decisive || (ev.ok && fired);
          byTime.set(x.time, mark);
          marks.set(id, byTime);
        });
      });
    });
    lines.push(line("Green / red / grey mark on a trend pane = the trend bar used (bias +1 / -1 / 0), square = the bar where the signal fired. Evaluated with config v" + view.meta.configVersion + ": a signal written under an older config may have used other trend factors.", "note"));
    el.detail.replaceChildren(...lines);
    el.detail.dataset.side = rows[0].side;
    el.detail.style.display = "block";
    panes.forEach((pane, id) => {
      if (!id.startsWith("trend.")) return;
      const series = markerSeries(pane);
      if (!series) return;
      const list = [...(marks.get(id) || new Map()).values()].sort((a, b) => a.time - b.time).map((m) => ({
        time: m.time, position: "inBar", shape: m.decisive ? "square" : "circle", color: biasColor(m.bias), size: m.decisive ? 2 : 1, text: m.total > 1 ? "ref " + m.steps.join(",") : "ref",
      }));
      series.setMarkers(list);
    });
  }

  // ------------------------------------------------------- bảng + trạng thái
  function renderTable() {
    el.tbody.replaceChildren(...view.signals.map((r) => {
      const tr = document.createElement("tr");
      tr.className = "kind-" + r.kind;
      [cell(r.bar), cell(r.close), cell(r.kind === "final" ? "decided" : "raw"), cell(r.side, "side-" + r.side), cell(r.orderType || "-"), cell(r.kind === "final" && r.orderType === "STOP" ? fmt(r.stop) : "-"),
        cell(fmt(r.atr)), cell(r.until || "-"), cell(linkText(r)), cell(r.reason || "")].forEach((td) => tr.appendChild(td));
      return tr;
    }));
  }
  function renderStatus(meta) {
    const clock = meta.clock || {};
    el.clock.textContent = "DFS " + (clock.status || "?") + " · tick " + (clock.tick === null || clock.tick === undefined ? "?" : clock.tick) +
      " · sim time " + (clock.simTime || "?") + " UTC" + (clock.runId ? " · run " + clock.runId : "");
    el.config.textContent = "config v" + meta.configVersion;
    el.updated.textContent = "updated " + new Date().toLocaleTimeString("en-GB") + " (local)";
    setDot(clock.status === "running" ? "ok" : "warn");
  }
  function renderMeta(meta) {
    if (!meta.lastCandle) { el.pair.textContent = ""; return; }
    el.pair.textContent = meta.strategyLabel + " " + meta.symbol + " " + meta.trigger + " · " + meta.paramsLabel + " · last bar " + meta.lastCandle + " · last decided signal " + meta.lastSignal;
    const delayed = meta.maxShiftBars > 0 ? "; " + meta.shiftedCount + " of them delayed: +n = n bars after the raw signal" : "; this strategy never delays a signal";
    el.summary.textContent = "In view: " + meta.rawCount + " raw signals (●, strategy output) and " + meta.finalCount + " decided signals (▲▼, written by DES after the trend reference" + delayed + ").";
  }

  // ------------------------------------------------------------ nạp & vẽ
  function clearView() {
    view = null; token = null; viewKey = ""; layoutKey = "";
    destroyPanes();
    el.tbody.replaceChildren(); el.summary.textContent = ""; el.pair.textContent = ""; el.layers.replaceChildren(); el.warnings.textContent = "";
    hideDetail();
  }

  function applyView(payload, key) {
    const reset = key !== viewKey;
    const layout = payload.panes.map((p) => p.id + ":" + p.height + ":" + p.hasCandles + ":" + p.biasStrip).join("|") + "#" + payload.meta.configVersion;
    if (layout !== layoutKey) { buildPanes(payload.panes); layoutKey = layout; renderLayers(payload.panes); }
    const price = panes.get("price");
    const range = reset || !price ? null : price.chart.timeScale().getVisibleLogicalRange();
    const atRight = reset || !range || !view || !view.panes.length || range.to >= view.panes[0].candles.length - 2;
    const keep = reset || atRight || !price ? null : price.chart.timeScale().getVisibleRange();
    if (reset) hideDetail();
    view = payload; viewKey = key; token = payload.token;
    payload.panes.forEach(applyPane);
    renderTable(); renderMeta(payload.meta);
    if (selected !== null) select(selected);   // dữ liệu tự cập nhật: giữ lựa chọn và vẽ lại dấu ref
    el.warnings.textContent = payload.meta.warnings.join(" · ");
    if (reset) panes.forEach((pane) => pane.chart.timeScale().fitContent());
    else if (atRight) panes.forEach((pane) => pane.chart.timeScale().scrollToRealTime());
    else if (keep) { try { price.chart.timeScale().setVisibleRange(keep); } catch (err) { /* khoảng cũ không còn */ } }
  }

  async function ensureCatalog(version) {
    if (catalog && catalog.configVersion === version) return false;
    const response = await fetch("/api/catalog", { cache: "no-store" });
    const next = await response.json();
    if (!response.ok) throw new Error(next.error || ("HTTP " + response.status));
    const initial = catalog ? currentSelection() : initialQuery;
    catalog = next;
    populate(initial);
    return true;
  }

  async function load(force) {
    const id = ++requestId;
    loading = true;
    try {
      if (!catalog) await ensureCatalog(null);
      const key = [el.strategy.value, el.symbol.value, el.bars.value].join("|");
      const params = new URLSearchParams(currentSelection());
      if (!force && view && token && key === viewKey) params.set("token", token);
      const response = await fetch("/api/view?" + params.toString(), { cache: "no-store" });
      const payload = await response.json();
      if (id !== requestId) return;
      if (!response.ok) { clearView(); showError(payload.error || ("HTTP " + response.status)); setDot("bad"); return; }
      clearError();
      renderStatus(payload.meta);
      if (await ensureCatalog(payload.meta.configVersion)) { reload(); return; }   // config đổi: nạp lại bộ chọn rồi vẽ lại
      if (payload.changed === false) return;
      applyView(payload, key);
    } catch (err) {
      if (id === requestId) { showError("Lost connection to the chart server: " + err); setDot("bad"); }
    } finally {
      if (id === requestId) loading = false;
    }
  }
  function reload() { updateUrl(); load(true); }
  function setAuto() {
    if (timer) { clearInterval(timer); timer = null; }
    const ms = parseInt(el.auto.value, 10);
    if (ms > 0) timer = setInterval(() => { if (!loading) load(false); }, ms);
  }

  // ------------------------------------------------------------------- khởi động
  window.addEventListener("error", (event) => showError("JS error: " + event.message));
  window.addEventListener("resize", resize);
  el.strategy.addEventListener("change", () => { populateSymbols(el.symbol.value); renderRefs(); reload(); });
  el.symbol.addEventListener("change", reload);
  el.bars.addEventListener("change", () => { el.bars.value = String(clampBars(parseInt(el.bars.value, 10))); reload(); });
  el.refresh.addEventListener("click", reload);
  el.auto.addEventListener("change", setAuto);
  reload();
  setAuto();
})();
</script>
</body>
</html>
"""


@lru_cache(maxsize=1)
def _read_vendor_js() -> str:
    """Thư viện Lightweight Charts đã vendor sẵn (nhúng vào trang, không cần internet)."""
    return _VENDOR_JS_PATH.read_text(encoding="utf-8")


def render_page() -> str:
    """Trang chart: HTML tự chứa; bộ chọn và mọi dữ liệu do JS lấy từ /api/catalog và /api/view."""
    return _TEMPLATE.replace("__VENDOR_JS__", _read_vendor_js())


# -- server --------------------------------------------------------------------------------------------------------------------------------------


def create_app(provider: Provider | None = None) -> Flask:
    """Dựng app Flask. ``provider`` truyền vào (test) thay cho việc đọc config và mở kết nối Redis thật."""
    app = Flask(__name__)
    source = provider or Provider(configuration.load_bootstrap())

    def respond(build: Callable[[Context], dict[str, Any]]) -> Response:
        """Biên HTTP chung: tham số sai -> 400, Redis lỗi hoặc chưa có config -> 503, lỗi khác -> 500, đều JSON gọn."""
        try:
            return _json(build(source.get()))
        except (KeyError, ValueError) as exc:
            return _json({"error": str(exc).strip('"')}, 400)
        except redis.RedisError as exc:
            return _json({"error": f"Redis is not responding: {exc}"}, 503)
        except RuntimeError as exc:
            return _json({"error": str(exc)}, 503)
        except Exception as exc:  # noqa: BLE001 -- Biên HTTP: trả lỗi JSON gọn cho trang.
            return _json({"error": str(exc)}, 500)

    @app.get("/")
    def index() -> Response:
        return Response(render_page(), mimetype="text/html")

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status=204)

    @app.get("/api/catalog")
    def api_catalog() -> Response:
        return respond(build_catalog)

    @app.get("/api/view")
    def api_view() -> Response:
        return respond(lambda ctx: build_view(ctx, parse_request(request.args, ctx.plan), request.args.get("token")))

    @app.get("/health")
    def health() -> Response:
        return jsonify({"status": "ok"})

    return app


def _json(payload: dict[str, Any], status: int = 200) -> Response:
    """JSON gọn, không cache (trang hỏi lại liên tục)."""
    response = Response(json.dumps(payload, separators=(",", ":")), status=status, mimetype="application/json")
    response.headers["Cache-Control"] = "no-store"
    return response


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DES chart (read-only viewer of DFS candles in db10 and DES results in db11).")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    print(f"des chart: http://{args.host}:{args.port}")
    create_app().run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
