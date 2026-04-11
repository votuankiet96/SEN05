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
# Used by: dashboards/06_chart_v3  (full indicator suite)
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


def calc_obv(df):
    """OBV: dong tich luy volume theo huong gia, ho tro danh gia xac nhan dong tien."""
    sign = np.sign(df["Close"].diff()).fillna(0)
    return (sign * df["Volume"]).cumsum()


# ─────────────────────────────────────────────────────────────────────────────
# Strategy-level indicator bundle
# Column convention: lowercase  (open, high, low, close)
# BarTime must be a plain column (not index)
# Used by: research/09_signal_scanner, dashboards/10_SAM_dashboard
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
