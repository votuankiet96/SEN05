"""
Xây dựng chỉ báo kỹ thuật cho strategy_lab.

Mô tả:
    File này CHỈ tính chỉ báo (indicator) — không có logic phát hiện tín
	    hiệu (signal) và không tính entry/level (levels). Hai phần đó là logic
    riêng của từng chiến lược và sống trong strategies/<tên>.py.

    Gồm 2 nhóm hàm:
    1. Chỉ báo thuần (sma/ema/wma/rma/macd_hist/atr) — nhận Series/
       DataFrame giá, trả về Series, không phụ thuộc chiến lược nào.
    2. add_*_indicators(df, params) — thêm cột chỉ báo (ma/macd_h/atr...)
       vào DataFrame OHLCV cho từng chiến lược, dùng lại nhóm (1). params
       luôn là dict đã resolve đầy đủ từ configuration.py.
       Các default kỹ thuật thuần indicator như Dow structure sống ở đây, vì
       chúng là công thức tính toán chung chứ không phải tham số vận hành mà
       operator cần chỉnh trong config.yaml.

    File này KHÔNG import bất kỳ gì từ strategy_lab.src.strategies — chiều phụ
    thuộc chỉ một chiều: strategies/*.py import từ indicator.py, không
    ngược lại.

Đầu vào:
    pd.Series/pd.DataFrame giá hoặc OHLCV đã load từ db_connector.py.

Đầu ra:
    pd.Series (chỉ báo thuần) hoặc pd.DataFrame đã thêm cột chỉ báo.

Lưu ý:
    Các hàm ở đây không có side effect, không sửa input tại chỗ.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# =============================================================================
# Chỉ báo kỹ thuật thuần — dùng chung cho mọi chiến lược.
# =============================================================================


def sma(series: pd.Series, period: int) -> pd.Series:
    """
    Tính Simple Moving Average (trung bình động đơn giản).

    Dùng rolling window kích thước cố định — cần đủ `period` điểm dữ liệu
    mới bắt đầu cho giá trị đầu tiên (min_periods = period mặc định).

    Args:
        series: Chuỗi giá (thường là close).
        period: Số bar tính trung bình.

    Returns:
        pd.Series SMA cùng index với input. Các vị trí đầu (< period bar)
        sẽ là NaN cho đến khi đủ dữ liệu.
    """
    return series.astype(float).rolling(int(period)).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """
    Tính Exponential Moving Average (trung bình động hàm mũ).

    Dùng pandas ewm với adjust=False — trọng số theo công thức EMA chuẩn,
    không có hiệu chỉnh bias ở đầu chuỗi.

    Args:
        series: Chuỗi giá.
        period: Số bar (span). Alpha = 2 / (period + 1).

    Returns:
        pd.Series EMA cùng index với input.
    """
    return series.astype(float).ewm(span=int(period), adjust=False).mean()


def wma(series: pd.Series, period: int) -> pd.Series:
    """
    Tính Weighted Moving Average theo trọng số tăng dần.

    Hàm này phục vụ AI Trend Navigator KNN. Với window [x1, x2, ..., xn],
    điểm gần nhất có trọng số lớn nhất n.
    """
    period = max(1, int(period))
    weights = np.arange(1, period + 1, dtype=float)
    return series.astype(float).rolling(period, min_periods=period).apply(
        lambda values: float(np.dot(values, weights) / weights.sum()),
        raw=True,
    )


def _wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """
    Làm mượt Wilder đúng seed của TradingView (`ta.rma`): bar hợp lệ đầu
    tiên = SMA(period) của input, các bar sau mới chạy đệ quy
    alpha*x + (1-alpha)*prev với alpha = 1/period.

    Khác với `series.ewm(alpha=1/period, adjust=False)` thuần — cách đó seed
    đệ quy bằng đúng 1 điểm dữ liệu đầu tiên thay vì SMA, nên lệch tới vài %
    so với TradingView ở các bar đầu và hội tụ chậm (đã đo thật: period=5
    lệch 2.5% tại bar đầu, cần +24 bar mới về dưới 0.01%; period=14 lệch
    4.2%, cần +81 bar). `min_periods=period` của cách cũ chỉ CHE NaN, không
    seed lại bằng SMA, nên không đủ để khớp Pine.
    """
    period = max(1, int(period))
    values = series.astype(float)
    n = len(values)
    out = pd.Series(np.nan, index=values.index, dtype=float)
    if n < period:
        return out
    seed = values.iloc[:period].mean()
    tail = values.iloc[period - 1 :].copy()
    tail.iloc[0] = seed
    out.iloc[period - 1 :] = tail.ewm(alpha=1 / period, adjust=False).mean().to_numpy()
    return out


def rma(series: pd.Series, period: int) -> pd.Series:
    """
    Tính Wilder RMA, tương thích với cách làm mượt dùng trong ATR/Pine.
    """
    return _wilder_smooth(series, period)


def macd_hist(
    series: pd.Series,
    *,
    fast: int,
    slow: int,
    signal: int,
) -> pd.Series:
    """
    Tính MACD Histogram = MACD Line − Signal Line.

    Công thức:
        MACD Line   = EMA(fast) − EMA(slow)
        Signal Line = EMA(MACD Line, signal)
        Histogram   = MACD Line − Signal Line

    Args:
        series: Chuỗi giá close.
        fast: Chu kỳ EMA nhanh (ví dụ: 5 hoặc 12).
        slow: Chu kỳ EMA chậm (ví dụ: 25 hoặc 26). Phải > fast.
        signal: Chu kỳ EMA tính đường signal (ví dụ: 5 hoặc 9).

    Returns:
        pd.Series histogram — dương khi momentum tăng, âm khi giảm.
    """
    close = series.astype(float)
    macd_line = ema(close, int(fast)) - ema(close, int(slow))
    signal_line = ema(macd_line, int(signal))
    return macd_line - signal_line


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    """
    Tính Average True Range (ATR) theo phương pháp làm mượt Wilder.

    True Range (TR) = max(high−low, |high−prev_close|, |low−prev_close|)
    ATR = EWM của TR với alpha = 1/period (tương đương Wilder's smoothing).

    Args:
        df: DataFrame chứa ít nhất các cột lowercase: "high", "low", "close".
        period: Số bar cho Wilder's smoothing. min_periods = period.

    Returns:
        pd.Series ATR cùng index với df. Các bar đầu tiên là NaN.
    """
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return _wilder_smooth(tr, int(period))


@dataclass(frozen=True)
class DowStructureParams:
    """
    Tham số kỹ thuật nội bộ cho Dow structure indicator.
    """

    left: int = 3
    right: int = 5
    min_atr_mult: float = 0.5
    atr_period: int = 14


@dataclass(frozen=True)
class _DowPivot:
    index: int
    kind: str
    price: float


def add_dow_structure_indicators(
    df: pd.DataFrame,
    params: DowStructureParams | None = None,
) -> pd.DataFrame:
    """
    Thêm dữ liệu swing Dow đã xác nhận, không repaint.

    Output chính:
        dow_swing_low:  swing low gần nhất đã xác nhận tại thời điểm bar.
        sl_dow_buy:     stop reference cho lệnh BUY (= dow_swing_low).
        sl_dow_sell:    stop reference cho lệnh SELL (= swing high gần nhất).

    Không có cột `dow_swing_high` riêng: nó từng là bản sao y hệt
    `sl_dow_sell` và không nơi nào đọc. `dow_swing_low` được giữ vì test
    characterization đang khoá giá trị của nó (xem CLAUDE.md mục 8).
    """
    out = df.copy()
    out["dow_swing_low"] = np.nan
    out["sl_dow_buy"] = np.nan
    out["sl_dow_sell"] = np.nan

    required = {"high", "low", "close"}
    if out.empty or not required.issubset(out.columns):
        return out

    p = params or DowStructureParams()
    left = max(1, int(p.left))
    right = max(1, int(p.right))
    min_atr_mult = max(0.0, float(p.min_atr_mult))
    atr_period = max(1, int(p.atr_period))

    highs = pd.to_numeric(out["high"], errors="coerce").to_numpy(dtype=float)
    lows = pd.to_numeric(out["low"], errors="coerce").to_numpy(dtype=float)
    atr_values = pd.to_numeric(atr(out, atr_period), errors="coerce")

    swings: list[_DowPivot] = []
    last_high: _DowPivot | None = None
    last_low: _DowPivot | None = None
    for current_pos in range(len(out)):
        pivot_pos = current_pos - right
        if pivot_pos >= left:
            for pivot in _confirmed_dow_pivots(highs, lows, pivot_pos, left, right):
                accepted = _accept_dow_pivot(
                    swings,
                    pivot,
                    atr_values.iloc[pivot.index],
                    min_atr_mult,
                )
                if accepted is None:
                    continue
                if accepted.kind == "high":
                    last_high = accepted
                else:
                    last_low = accepted

        if last_high is not None:
            out.iat[current_pos, out.columns.get_loc("sl_dow_sell")] = last_high.price
        if last_low is not None:
            out.iat[current_pos, out.columns.get_loc("dow_swing_low")] = last_low.price
            out.iat[current_pos, out.columns.get_loc("sl_dow_buy")] = last_low.price

    return out


def _confirmed_dow_pivots(
    highs: np.ndarray,
    lows: np.ndarray,
    pivot_pos: int,
    left: int,
    right: int,
) -> list[_DowPivot]:
    """
    Trả về pivot high/low đã được xác nhận tại pivot_pos.
    """
    high = highs[pivot_pos]
    low = lows[pivot_pos]
    if np.isnan(high) or np.isnan(low):
        return []

    left_high = highs[pivot_pos - left : pivot_pos]
    right_high = highs[pivot_pos + 1 : pivot_pos + right + 1]
    left_low = lows[pivot_pos - left : pivot_pos]
    right_low = lows[pivot_pos + 1 : pivot_pos + right + 1]

    pivots: list[_DowPivot] = []
    if high > np.nanmax(left_high) and high >= np.nanmax(right_high):
        pivots.append(_DowPivot(index=pivot_pos, kind="high", price=float(high)))
    if low < np.nanmin(left_low) and low <= np.nanmin(right_low):
        pivots.append(_DowPivot(index=pivot_pos, kind="low", price=float(low)))
    return pivots


def _accept_dow_pivot(
    swings: list[_DowPivot],
    pivot: _DowPivot,
    atr_value: float,
    min_atr_mult: float,
) -> _DowPivot | None:
    """
    Giữ chuỗi Dow swing luân phiên high/low và lọc nhiễu nhỏ theo ATR.
    """
    if not swings:
        swings.append(pivot)
        return pivot

    last = swings[-1]
    if pivot.kind == last.kind:
        if _is_more_extreme_dow_pivot(pivot, last):
            swings[-1] = pivot
            return pivot
        return None

    min_move = 0.0 if pd.isna(atr_value) else float(atr_value) * min_atr_mult
    if abs(pivot.price - last.price) < min_move:
        return None

    swings.append(pivot)
    return pivot


def _is_more_extreme_dow_pivot(candidate: _DowPivot, current: _DowPivot) -> bool:
    if candidate.kind == "high":
        return candidate.price > current.price
    return candidate.price < current.price


def _price_source(df: pd.DataFrame, kind: str, period: int) -> pd.Series:
    """
    Chọn chuỗi giá đầu vào cho AI Trend Navigator.

    Chỉ hỗ trợ "hl2" — đúng giá trị PRICE_VALUE duy nhất mà hệ thống cấu
    hình (strategies/trend.py: KNN_TREND_DEFAULT_PARAMS). Các nhánh vwap/
    sma/wma/ema/hma của bản port gốc đã bỏ vì không đường nào gọi tới được
    (normalize_*_params không phát ra key PRICE_VALUE). Giá trị lạ raise
    ngay thay vì âm thầm rơi về hl2 — cùng nguyên tắc với config.yaml
    (CLAUDE.md mục 5).
    """
    kind = str(kind or "hl2").strip().lower()
    if kind != "hl2":
        raise ValueError(f"PRICE_VALUE must be 'hl2', got '{kind}'.")
    hl2 = (df["high"].astype(float) + df["low"].astype(float)) / 2.0
    return sma(hl2, period)


def _target_source(df: pd.DataFrame, kind: str, period: int) -> pd.Series:
    """
    Chọn chuỗi target để tìm K hàng xóm gần nhất trong AI Trend Navigator.

    Chỉ hỗ trợ "price action" — xem ghi chú ở _price_source().
    """
    kind = str(kind or "price action").strip().lower()
    if kind != "price action":
        raise ValueError(f"TARGET_VALUE must be 'Price Action', got '{kind}'.")
    return rma(df["close"].astype(float), period)


def calc_ai_trend_navigator(
    df: pd.DataFrame,
    *,
    price_value: str = "hl2",
    ma_len: int = 5,
    target_value: str = "Price Action",
    target_len: int = 5,
    number_of_closest_values: int = 3,
    smoothing_period: int = 50,
) -> pd.DataFrame:
    """
    Tính AI Trend Navigator KNN line.

    Đây là indicator deterministic, không phải model ML train offline.
    Output:
        ai_knn:       đường KNN đã làm mượt WMA(5).
        ai_avg:       RMA của chuỗi KNN để tham chiếu/hiển thị.
        ai_direction: 1 nếu ai_knn tăng, -1 nếu giảm, 0 nếu đi ngang/NaN.
    """
    out = pd.DataFrame(index=df.index)
    if df.empty:
        return out

    work = df.reset_index(drop=True).copy()
    k = max(2, min(int(number_of_closest_values), 200))
    ma_len = max(2, min(int(ma_len), 200))
    target_len = max(2, min(int(target_len), 200))
    smoothing_period = max(2, min(int(smoothing_period), 500))
    window_size = max(k, 30)

    values = _price_source(work, price_value, ma_len).to_numpy(dtype=float)
    targets = _target_source(work, target_value, target_len).to_numpy(dtype=float)
    knn_ma = np.full(len(work), np.nan, dtype=float)
    for idx, target in enumerate(targets):
        if idx == 0 or np.isnan(target):
            continue
        history = values[max(0, idx - window_size) : idx]
        valid = history[~np.isnan(history)]
        if len(valid) < k:
            continue
        nearest = np.argsort(np.abs(valid - target))[:k]
        knn_ma[idx] = float(valid[nearest].mean())

    knn_ma_s = pd.Series(knn_ma)
    ai_knn = wma(knn_ma_s, 5)
    ai_avg = rma(knn_ma_s, smoothing_period)
    ai_direction = pd.Series(
        np.where(ai_knn > ai_knn.shift(1), 1, np.where(ai_knn < ai_knn.shift(1), -1, 0))
    )
    out["ai_knn"] = ai_knn.to_numpy()
    out["ai_avg"] = ai_avg.to_numpy()
    out["ai_direction"] = ai_direction.to_numpy()
    return out


def timeframe_minutes(tf: object) -> int:
    """
    Chuyển mã timeframe phổ biến (M10, M45, H3...) thành số phút.
    """
    code = str(tf or "").strip().upper()
    if not code:
        raise ValueError(f"Unsupported timeframe '{tf}'.")
    amount_text = code[1:] or "1"
    if not amount_text.isdigit():
        raise ValueError(f"Unsupported timeframe '{tf}'.")
    amount = int(amount_text)
    unit = code[0]
    if amount <= 0:
        raise ValueError(f"Unsupported timeframe '{tf}'.")
    if unit == "M":
        return amount
    if unit == "H":
        return amount * 60
    if unit == "D":
        return amount * 60 * 24
    if unit == "W":
        return amount * 60 * 24 * 7
    raise ValueError(f"Unsupported timeframe '{tf}'.")


# =============================================================================
# add_*_indicators — thêm cột chỉ báo vào DataFrame OHLCV cho từng chiến lược.
# params luôn là dict đã resolve đầy đủ từ StrategySpec.normalize_params()
# trong configuration.py — không có default ngầm ở đây.
# =============================================================================


def add_combo_indicators(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Thêm các cột chỉ báo cần thiết cho chiến lược Combo vào DataFrame.

    Các cột được thêm:
        ma:         Simple Moving Average của close (chu kỳ MA_PERIOD).
        macd_h:     MACD Histogram (fast EMA − slow EMA, rồi lấy EMA signal).
        atr:        Average True Range theo Wilder (chu kỳ ATR_PERIOD).
        prev_close: Giá đóng cửa bar trước (close.shift(1)).

    Combo so `prev_close` với `ma` HIỆN TẠI để xác nhận vừa cắt lên/xuống,
    nên không cần cột `prev_ma` — đã bỏ vì không nơi nào đọc.

    Args:
        df: DataFrame OHLCV với cột [bartime, open, high, low, close, volume].
        params: Dict tham số đã resolve đầy đủ (từ
            configuration.normalize_combo_params) — cần MA_PERIOD, MACD_FAST,
            MACD_SLOW, MACD_SIGNAL, ATR_PERIOD.

    Returns:
        Bản sao của df với 4 cột bổ sung. Input không bị thay đổi.
    """
    out = df.copy()
    out["ma"] = sma(out["close"], int(params["MA_PERIOD"]))
    out["macd_h"] = macd_hist(
        out["close"],
        fast=int(params["MACD_FAST"]),
        slow=int(params["MACD_SLOW"]),
        signal=int(params["MACD_SIGNAL"]),
    )
    out["atr"] = atr(out, int(params["ATR_PERIOD"]))
    out["prev_close"] = out["close"].shift(1)
    return out


def add_ma_cross_indicators(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Thêm các cột chỉ báo cần thiết cho chiến lược MA Cross vào DataFrame.

    Các cột được thêm:
        fast_ma, slow_ma:            SMA nhanh/chậm của close.
        macd_h:                      MACD Histogram.
        atr:                         Average True Range theo Wilder.
        prev_fast_ma, prev_slow_ma:  Giá trị SMA bar trước, dùng phát hiện
                                      crossover.

    Args:
        df: DataFrame OHLCV với cột [bartime, open, high, low, close, volume].
        params: Dict tham số đã resolve đầy đủ (từ
            configuration.normalize_ma_cross_params) — cần FAST_MA, SLOW_MA,
            MACD_FAST, MACD_SLOW, MACD_SIGNAL, ATR_PERIOD.

    Returns:
        Bản sao của df với 6 cột bổ sung. Input không bị thay đổi.
    """
    out = df.copy()
    out["fast_ma"] = sma(out["close"], int(params["FAST_MA"]))
    out["slow_ma"] = sma(out["close"], int(params["SLOW_MA"]))
    out["macd_h"] = macd_hist(
        out["close"],
        fast=int(params["MACD_FAST"]),
        slow=int(params["MACD_SLOW"]),
        signal=int(params["MACD_SIGNAL"]),
    )
    out["atr"] = atr(out, int(params["ATR_PERIOD"]))
    out["prev_fast_ma"] = out["fast_ma"].shift(1)
    out["prev_slow_ma"] = out["slow_ma"].shift(1)
    return add_dow_structure_indicators(out)


def add_knn_trend_indicators(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Chuẩn bị frame trend tham chiếu bằng AI Trend Navigator KNN.

    Tên cột dùng tiền tố ``trend_`` để không gắn chết vào H3/M45 và có thể
    dùng chung cho nhiều strategy entry khác nhau.
    """
    out = df.copy()
    ai = calc_ai_trend_navigator(
        out,
        price_value=str(params["PRICE_VALUE"]),
        ma_len=int(params["AI_MA_LEN"]),
        target_value=str(params["TARGET_VALUE"]),
        target_len=int(params["AI_TARGET_LEN"]),
        number_of_closest_values=int(params["AI_K"]),
        smoothing_period=int(params["AI_SMOOTH"]),
    )
    out["trend_ai_knn"] = ai["ai_knn"]
    out["trend_ai_avg"] = ai["ai_avg"]
    out["trend_ai_direction"] = ai["ai_direction"]

    out["trend_bias"] = 0
    out.loc[out["trend_ai_direction"].eq(1), "trend_bias"] = 1
    out.loc[out["trend_ai_direction"].eq(-1), "trend_bias"] = -1

    trend_minutes = timeframe_minutes(params["TREND_TF"])
    out["trend_close_time"] = pd.to_datetime(out["bartime"]) + pd.Timedelta(minutes=trend_minutes)
    return out


def merge_trend_reference(entry_df: pd.DataFrame, trend_df: pd.DataFrame) -> pd.DataFrame:
    """
    Gắn trend bar đã đóng gần nhất vào từng entry bar, không lookahead.

    Điều kiện merge thực tế:
        trend_close_time <= entry bartime
    """
    if entry_df.empty:
        return entry_df.copy()
    if trend_df.empty or "trend_close_time" not in trend_df.columns:
        return entry_df.copy()

    trend_cols = [
        "trend_close_time",
        "trend_ai_knn",
        "trend_ai_avg",
        "trend_ai_direction",
        "trend_bias",
    ]
    available = [col for col in trend_cols if col in trend_df.columns]
    entry = entry_df.sort_values("bartime").copy()
    trend = trend_df[available].sort_values("trend_close_time").copy()
    merged = pd.merge_asof(
        entry,
        trend,
        left_on="bartime",
        right_on="trend_close_time",
        direction="backward",
    )
    return merged.sort_values("bartime").reset_index(drop=True)
