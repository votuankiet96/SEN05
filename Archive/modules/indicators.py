# =============================================================================
# modules/indicators.py - Cong thuc indicator dung chung toan he thong
# =============================================================================
# Muc tieu file:
# - Cung cap cac phep tinh indicator o mot noi duy nhat.
# - Dam bao chart/scanner/backtest dung cung mot logic.
#
# Anh huong khi chinh sua:
# - Moi thay doi cong thuc o day se tac dong dong thoi den tin hieu va backtest.
# - Neu chi muon "nhay hon/cham hon", uu tien sua tham so trong strategy_config.

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Low-level Series / DataFrame calculations
# Column convention: Title-Case  (Open, High, Low, Close, Volume)
# Used by: data_provider chart indicators and legacy helper callers.
# ─────────────────────────────────────────────────────────────────────────────

def calc_sma(s, period):
    """SMA: trung binh dong cua period gan nhat; dung de lam duong xu huong mem."""
    return s.rolling(window=period, min_periods=1).mean()


def calc_ema(s, period):
    """EMA: trung binh co trong so uu tien du lieu moi; phan ung nhanh hon SMA."""
    return s.ewm(span=period, adjust=False).mean()


def calc_bollinger(df, period=20, std=2.0):
    """Bollinger Bands: tra ve mid/up/low de danh gia do bien dong quanh xu huong."""
    mid = calc_sma(df["Close"], period)
    rs  = df["Close"].rolling(window=period, min_periods=1).std()
    return mid, mid + std * rs, mid - std * rs


def calc_vwap(df):
    """VWAP: gia trung binh theo khoi luong; dung de tham chieu muc gia giao dich quan trong."""
    tp  = (df["High"] + df["Low"] + df["Close"]) / 3
    vol = df["Volume"].replace(0, np.nan).fillna(1)
    return (tp * vol).cumsum() / vol.cumsum()


def calc_rsi(s, period=14):
    """RSI (Wilder): do cuong do tang/giam, thuong doc theo nguong 30/70."""
    d  = s.diff()
    g  = d.clip(lower=0)
    losses = (-d).clip(lower=0)
    ag = g.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    al = losses.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_macd(s, fast=12, slow=26, signal=9):
    """MACD: tra ve MACD line, signal line va histogram (chenh lech 2 duong)."""
    ml = calc_ema(s, fast) - calc_ema(s, slow)
    sl = calc_ema(ml, signal)
    return ml, sl, ml - sl


def calc_stochastic(df, k=14, d=3, smooth=3):
    """Stochastic: vi tri close trong bien high-low gan day; tra ve %K va %D."""
    lo = df["Low"].rolling(k,  min_periods=1).min()
    hi = df["High"].rolling(k, min_periods=1).max()
    fk = 100 * (df["Close"] - lo) / (hi - lo).replace(0, np.nan)
    sk = fk.rolling(smooth, min_periods=1).mean()
    sd = sk.rolling(d,      min_periods=1).mean()
    return sk, sd


def calc_atr(df, period=14):
    """ATR (Wilder): do bien dong tuyet doi, hay dung de dat SL/TP theo dong luc gia."""
    hl = df["High"] - df["Low"]
    hc = (df["High"] - df["Close"].shift(1)).abs()
    lc = (df["Low"]  - df["Close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()


def calc_adx(df, period=14):
    """ADX (Wilder): do manh xu huong 0-100; ADX>25 = xu huong ro; tra ve adx, +DI, -DI."""
    high  = df["High"]
    low   = df["Low"]
    close = df["Close"]

    up   = high.diff()
    down = -low.diff()  # prev_low - low

    plus_dm  = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    alpha = 1.0 / period
    atr_s     = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    plus_di   = 100 * plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr_s
    minus_di  = 100 * minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr_s
    denom     = (plus_di + minus_di).replace(0, np.nan)
    dx        = 100 * (plus_di - minus_di).abs() / denom
    adx       = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    return adx, plus_di, minus_di


def calc_obv(df):
    """OBV: dong tich luy volume theo huong gia, ho tro danh gia xac nhan dong tien."""
    sign = np.sign(df["Close"].diff()).fillna(0)
    return (sign * df["Volume"]).cumsum()


def _calc_wma(s: pd.Series, period: int) -> pd.Series:
    """Weighted moving average compatible with Pine ta.wma() directionally."""
    period = max(1, int(period))
    weights = np.arange(1, period + 1, dtype=float)
    denom = weights.sum()
    return s.astype(float).rolling(period, min_periods=period).apply(
        lambda x: float(np.dot(x, weights) / denom),
        raw=True,
    )


def _calc_rma(s: pd.Series, period: int) -> pd.Series:
    """Wilder RMA, used by Pine ta.rma()."""
    period = max(1, int(period))
    return s.astype(float).ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def _calc_hma(s: pd.Series, period: int) -> pd.Series:
    """Hull moving average built from WMA."""
    period = max(1, int(period))
    half = max(1, period // 2)
    root = max(1, int(np.sqrt(period)))
    return _calc_wma(2 * _calc_wma(s, half) - _calc_wma(s, period), root)


def _calc_vwap_source(df: pd.DataFrame, source: pd.Series) -> pd.Series:
    """Cumulative VWAP for an arbitrary source series."""
    vol = df["Volume"].replace(0, np.nan).fillna(1.0).astype(float)
    src = source.astype(float)
    return (src * vol).cumsum() / vol.cumsum()


def _ai_trend_source(df: pd.DataFrame, kind: str, period: int) -> pd.Series:
    kind = (kind or "hl2").strip().lower()
    close = df["Close"].astype(float)
    hl2 = (df["High"].astype(float) + df["Low"].astype(float)) / 2.0

    if kind == "hl2":
        return calc_sma(hl2, period)
    if kind == "vwap":
        return _calc_vwap_source(df, close.shift(period))
    if kind == "sma":
        return calc_sma(close, period)
    if kind == "wma":
        return _calc_wma(close, period)
    if kind == "ema":
        return calc_ema(close, period)
    if kind == "hma":
        return _calc_hma(close, period)
    return calc_sma(hl2, period)


def _ai_trend_target(df: pd.DataFrame, kind: str, period: int) -> pd.Series:
    kind = (kind or "price action").strip().lower()
    close = df["Close"].astype(float)

    if kind == "price action":
        return _calc_rma(close, period)
    if kind == "vwap":
        return _calc_vwap_source(df, close.shift(period))
    if kind == "volatility":
        return calc_atr(df, 14)
    if kind == "sma":
        return calc_sma(close, period)
    if kind == "wma":
        return _calc_wma(close, period)
    if kind == "ema":
        return calc_ema(close, period)
    if kind == "hma":
        return _calc_hma(close, period)
    return _calc_rma(close, period)


def calc_ai_trend_navigator(
    df: pd.DataFrame,
    price_value: str = "hl2",
    ma_len: int = 5,
    target_value: str = "Price Action",
    target_len: int = 5,
    number_of_closest_values: int = 3,
    smoothing_period: int = 50,
) -> pd.DataFrame:
    """
    AI Trend Navigator / KNN classifier line ported from Zeiierman's Pine script.

    This is a deterministic nearest-neighbor smoother on recent OHLCV data, not
    an offline-trained ML model. Defaults mirror the TradingView script:
    PriceValue=hl2, TargetValue=Price Action, maLen=5, targetLen=5, k=3,
    smoothingPeriod=50.
    """
    if df.empty:
        return pd.DataFrame(index=df.index)

    work = df.reset_index(drop=True).copy()
    k = max(2, min(int(number_of_closest_values), 200))
    ma_len = max(2, min(int(ma_len), 200))
    target_len = max(2, min(int(target_len), 200))
    smoothing_period = max(2, min(int(smoothing_period), 500))
    window_size = max(k, 30)

    value_in = _ai_trend_source(work, price_value, ma_len)
    target_in = _ai_trend_target(work, target_value, target_len)
    values = value_in.to_numpy(dtype=float)
    targets = target_in.to_numpy(dtype=float)
    close = work["Close"].astype(float).to_numpy()

    n = len(work)
    knn_ma = np.full(n, np.nan, dtype=float)
    for idx in range(n):
        target = targets[idx]
        if np.isnan(target) or idx == 0:
            continue
        start = max(0, idx - window_size)
        hist = values[start:idx]
        valid = hist[~np.isnan(hist)]
        if len(valid) < k:
            continue
        nearest = np.argsort(np.abs(valid - target))[:k]
        knn_ma[idx] = float(valid[nearest].mean())

    knn_ma_s = pd.Series(knn_ma)
    price = (knn_ma + close) / 2.0
    c_line = _calc_rma(knn_ma_s.shift(1), smoothing_period).to_numpy(dtype=float)
    o_line = _calc_rma(knn_ma_s, smoothing_period).to_numpy(dtype=float)

    raw_prediction = np.full(n, np.nan, dtype=float)
    for idx in range(n):
        current_price = price[idx]
        if np.isnan(current_price):
            continue
        pos_count = 0
        neg_count = 0
        min_distance = np.inf
        for j in range(1, 11):
            hist_idx = idx - j
            if hist_idx < 0:
                break
            hist_price = price[hist_idx]
            if np.isnan(hist_price):
                continue
            distance = abs(hist_price - current_price)
            if distance < min_distance:
                min_distance = distance
                if c_line[hist_idx] > o_line[hist_idx]:
                    neg_count += 1
                if c_line[hist_idx] < o_line[hist_idx]:
                    pos_count += 1
        if pos_count or neg_count:
            raw_prediction[idx] = 1.0 if pos_count > neg_count else -1.0

    prediction = _calc_wma(pd.Series(raw_prediction), 3)
    knn_line = _calc_wma(knn_ma_s, 5)
    avg_line = _calc_rma(knn_ma_s, smoothing_period)
    direction = pd.Series(
        np.where(knn_line > knn_line.shift(1), 1, np.where(knn_line < knn_line.shift(1), -1, 0))
    )

    out = pd.DataFrame({
        "knn": knn_line,
        "average": avg_line,
        "prediction": prediction,
        "direction": direction,
    })
    out.index = df.index
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Strategy-level indicator bundle
# Column convention: lowercase  (open, high, low, close)
# BarTime must be a plain column (not index)
# Used by: legacy callers that still expect the combined indicator frame.
# ─────────────────────────────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    """
    Them bo indicator cot loi cho scanner/backtest: MA, MACD histogram, ATR.

    Dau vao:
    - df: du lieu lowercase (open/high/low/close), thu tu thoi gian tang dan.
    - p: tham so chien luoc (MA_PERIOD, MACD_*, ATR_PERIOD).

    Dau ra:
    - DataFrame moi (copy) co them cot ma, macd_h, atr.

    Tai sao copy DataFrame:
    - Tranh tac dong side-effect len du lieu goc khi tai su dung o noi khac.

    Anh huong he thong:
    - Day la ham "xuong song" cua signal rule: sai o day se sai toan bo ket qua scan/backtest.

    Parameters
    ----------
    df : DataFrame with lowercase OHLCV columns and BarTime as a plain column
    p  : indicator params dict with keys:
         MA_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL, ATR_PERIOD
    """
    df       = df.copy()
    df['ma'] = df['close'].rolling(p['MA_PERIOD']).mean()

    ema_f        = df['close'].ewm(span=p['MACD_FAST'],   adjust=False).mean()
    ema_s        = df['close'].ewm(span=p['MACD_SLOW'],   adjust=False).mean()
    macd_line    = ema_f - ema_s
    df['macd_h'] = macd_line - macd_line.ewm(
        span=p['MACD_SIGNAL'], adjust=False).mean()

    prev      = df['close'].shift(1)
    tr        = pd.concat([
        df['high'] - df['low'],
        (df['high'] - prev).abs(),
        (df['low']  - prev).abs(),
    ], axis=1).max(axis=1)
    # Use Wilder EWM smoothing — same method as calc_atr() for consistency
    df['atr'] = tr.ewm(alpha=1/p['ATR_PERIOD'], min_periods=p['ATR_PERIOD'],
                       adjust=False).mean()

    return df
