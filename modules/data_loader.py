# =============================================================================
# modules/data_loader.py - Lop tai du lieu tu SQL Server vao DataFrame
# =============================================================================
# Muc tieu file:
# - Lay danh muc symbol/timeframe va du lieu nen tu DWH.
# - Tra ve schema on dinh de chart/scanner/backtest su dung lai.
#
# Anh huong khi chinh sua:
# - Doi ten cot output (Open/open, BarTime, x_label...) co the lam vo downstream.
# - Doi TOP/query filter anh huong khoi luong du lieu va toc do xu ly.

import sys
from pathlib import Path

import pandas as pd

from .db_connector import get_connection

TF_FREQ_MAP: dict[str, str] = {
    'M5': '5min', 'M10': '10min', 'M15': '15min', 'M20': '20min',
    'M30': '30min', 'M45': '45min', 'H1': '1h', 'M90': '90min',
    'H2': '2h', 'H3': '3h', 'H4': '4h',
    'H6': '6h', 'H8': '8h', 'D1': '1D', 'W': '1W-MON',
}


def _read_sql(query: str, conn, params=None) -> pd.DataFrame:
    """
    Chay query bang pyodbc cursor va tra ve DataFrame.

    Tai sao dung ham rieng:
    - Dong nhat cach doc SQL trong toan bo file.
    - Tranh warning khong can thiet khi thong qua SQLAlchemy.
    """
    cursor = conn.cursor()
    if params:
        cursor.execute(query, params)
    else:
        cursor.execute(query)
    columns = [col[0] for col in cursor.description]
    rows = cursor.fetchall()
    cursor.close()
    return pd.DataFrame.from_records(rows, columns=columns)


def detect_missing_bars(df: pd.DataFrame, tf_code: str) -> pd.DataFrame:
    """
    Phat hien nen bi thieu trong chuoi OHLCV.
    Tra ve DataFrame day du voi cot is_missing_bar=True cho nen thieu.
    """
    if tf_code not in TF_FREQ_MAP:
        out = df.copy()
        out['is_missing_bar'] = False
        return out

    freq = TF_FREQ_MAP[tf_code]
    full_idx = pd.date_range(
        start=df['BarTime'].min(),
        end=df['BarTime'].max(),
        freq=freq,
    )
    result = (
        df.set_index('BarTime')
          .reindex(full_idx)
          .rename_axis('BarTime')
          .reset_index()
    )
    result['is_missing_bar'] = result['open'].isna()
    missing_count = int(result['is_missing_bar'].sum())
    if missing_count > 0:
        print(f"[data_loader] ⚠️  {tf_code}: phat hien {missing_count} nen bi thieu")
    return result


def fill_missing_bars(df: pd.DataFrame, method: str = 'ffill') -> pd.DataFrame:
    """
    Xu ly nen bi thieu.
    method: 'ffill' = forward fill, 'drop' = xoa dong thieu.
    Giu nguyen cot is_missing_bar de audit.
    """
    if 'is_missing_bar' not in df.columns:
        return df

    out = df.copy()
    ohlcv_cols = ['open', 'high', 'low', 'close', 'volume']
    existing = [c for c in ohlcv_cols if c in out.columns]
    if method == 'ffill':
        out[existing] = out[existing].ffill()
    elif method == 'drop':
        out = out[~out['is_missing_bar']].reset_index(drop=True)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# load_symbols  —  query Dim_Symbol, group by AssetType
# ─────────────────────────────────────────────────────────────────────────────

# AssetType values in DB → display group name, plus desired display order
_ASSET_GROUP_MAP = {
    "Indice": "Indices",
    "FOREX":  "FOREX",
    "Metal":  "Metal & Crypto",
    "Crypto": "Metal & Crypto",
}
_GROUP_ORDER = ["Indices", "FOREX", "Metal & Crypto"]


def load_symbols() -> dict:
    """
    Tai danh sach symbol va gom nhom cho sidebar chart.

    Dau vao:
    - Khong co tham so (lay toan bo Dim_Symbol).

    Dau ra:
    - dict theo nhom hien thi, vi du: Indices/FOREX/Metal & Crypto.

    Anh huong he thong:
    - Neu mapping AssetType sai, symbol se roi vao nhom sai hoac bien mat tren UI.
    - Loi DB se fallback {} de UI khong crash, nhung danh sach se trong.

    Returns
    -------
    dict[group_name → list[str]]  ordered by _GROUP_ORDER, e.g.
        {"Indices": ["CAC40", ...], "FOREX": ["EURUSD", ...], "Metal & Crypto": [...]}
    Falls back to empty dict on any DB error.
    """
    conn = None
    try:
        conn = get_connection()
        df   = _read_sql(
            "SELECT Symbol, AssetType FROM DWH.Dim_Symbol ORDER BY Symbol",
            conn)
    except Exception as e:
        print(f"[WARN] load_symbols: {e}")
        return {}
    finally:
        if conn is not None:
            conn.close()

    groups: dict = {g: [] for g in _GROUP_ORDER}
    for _, row in df.iterrows():
        group = _ASSET_GROUP_MAP.get(row["AssetType"])
        if group and group in groups:
            groups[group].append(row["Symbol"])

    # Remove empty groups (in case DB has no data for a category yet)
    return {g: syms for g, syms in groups.items() if syms}


def load_timeframes() -> list:
    """
    Tai danh sach timeframe code theo thu tu TimeframeID.

    Dau ra:
    - list[str] dung de populate dropdown/chon tf.

    Anh huong he thong:
    - Thu tu danh sach anh huong trai nghiem chon tf tren dashboard.
    - Loi DB se fallback [] de tranh vo man hinh.

    Returns
    -------
    list[str]  e.g. ['M5','M10','M15', ..., 'D1','W']
    Falls back to empty list on any DB error.
    """
    conn = None
    try:
        conn = get_connection()
        df   = _read_sql(
            "SELECT Code FROM DWH.Dim_Timeframe ORDER BY TimeframeID",
            conn)
        return df["Code"].tolist()
    except Exception as e:
        print(f"[WARN] load_timeframes: {e}")
        return []
    finally:
        if conn is not None:
            conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# load_ohlcv   —  by SymbolID  (09_signal_scanner, 10_SAM_dashboard)
# ─────────────────────────────────────────────────────────────────────────────

def load_ohlcv(symbol_id: int, n_bars: int, tf_code: str = 'H4',
               warmup: int = 200, handle_missing: str = 'flag') -> pd.DataFrame:
    """
    Tai du lieu OHLC theo SymbolID cho scanner/signal dashboard.

    Tai sao co warmup:
    - Indicator (MA/MACD/ATR...) can du lieu truoc do de tinh on dinh.
    - warmup giup bo phan scanner tranh bias o dau chuoi du lieu.

    Dau ra chuan hoa:
    - Thu tu oldest -> newest.
    - Cot lowercase: open/high/low/close.
    - Them x_label de ve chart lien mach, khong phu thuoc truc date mac dinh.

    Anh huong he thong:
    - Neu bo sap xep tang theo BarTime, nhieu tinh toan indicator/sequential rule se sai.

    Returns DataFrame (oldest→newest) with:
      - BarTime as plain column
      - lowercase OHLCV columns: open, high, low, close
      - x_label (categorical string for gap-free chart rendering)

    Parameters
    ----------
    symbol_id : DWH.Dim_Symbol SymbolID
    n_bars    : number of bars needed after warmup is stripped
    tf_code   : timeframe code, e.g. 'H4', 'D1'
    warmup    : extra rows loaded before n_bars so indicators can warm up
    handle_missing : 'flag' | 'ffill' | 'drop' | 'none'
    """
    total = n_bars + warmup
    conn  = get_connection()
    try:
        query = f'''
            SELECT TOP ({total})
                   f.BarTime,
                   f.OpenPrice  AS [open],
                   f.HighPrice  AS [high],
                   f.LowPrice   AS [low],
                   f.ClosePrice AS [close]
            FROM   DWH.Fact_OHLCV f
            JOIN   DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
            WHERE  f.SymbolID = ?
              AND  tf.Code    = ?
            ORDER  BY f.BarTime DESC
        '''
        df = _read_sql(query, conn, params=(symbol_id, tf_code))
    finally:
        conn.close()
    if df.empty:
        return df
    df['BarTime'] = pd.to_datetime(df['BarTime'])
    for col in ('open', 'high', 'low', 'close'):
        if col in df.columns:
            df[col] = df[col].astype(float)
    df = df.sort_values('BarTime').reset_index(drop=True)
    if handle_missing != 'none':
        df = detect_missing_bars(df, tf_code)
        if handle_missing == 'ffill':
            df = fill_missing_bars(df, method='ffill')
        elif handle_missing == 'drop':
            df = fill_missing_bars(df, method='drop')
    df['x_label'] = df['BarTime'].dt.strftime('%Y-%m-%d %H:%M')
    return df


# ─────────────────────────────────────────────────────────────────────────────
# load_candles  —  by Symbol name + Volume  (06_chart_v3)
# ─────────────────────────────────────────────────────────────────────────────

def load_candles(symbol: str, tf: str, n_bars: int) -> pd.DataFrame:
    """
    Tai du lieu OHLCV theo ten symbol cho chart day du indicator.

    Khac voi load_ohlcv:
    - Dung Title-Case (Open/High/Low/Close/Volume) de hop voi build_full_chart.
    - Query theo ten symbol (thuan tien cho UI) thay vi SymbolID.

    Anh huong he thong:
    - Sai convention hoa-thuong ten cot se gay loi ngay khi ve chart.
    - Loi DB se fallback DataFrame rong de man hinh thong bao "no data".

    Returns DataFrame (oldest→newest) with:
      - BarTime as plain column
      - Title-Case OHLCV columns: Open, High, Low, Close, Volume
      - x_label (categorical string for gap-free chart rendering)
    """
    try:
        conn = get_connection()
        df   = _read_sql("""
            SELECT TOP (?) f.BarTime,
                   f.OpenPrice  AS [Open],  f.HighPrice  AS [High],
                   f.LowPrice   AS [Low],   f.ClosePrice AS [Close],
                   f.Volume
            FROM DWH.Fact_OHLCV f
            JOIN DWH.Dim_Symbol     s  ON s.SymbolID     = f.SymbolID
            JOIN DWH.Dim_Timeframe  tf ON tf.TimeframeID = f.TimeframeID
            WHERE s.Symbol = ? AND tf.Code = ?
            ORDER BY f.BarTime DESC
        """, conn, params=(n_bars, symbol, tf))
        conn.close()
        if df.empty:
            return df
        df["BarTime"] = pd.to_datetime(df["BarTime"])
        for col in ('Open', 'High', 'Low', 'Close', 'Volume'):
            if col in df.columns:
                df[col] = df[col].astype(float)
        df = df.sort_values("BarTime").reset_index(drop=True)
        df["x_label"] = df["BarTime"].dt.strftime("%Y-%m-%d %H:%M")
        return df
    except Exception as e:
        print(f"[ERROR] load_candles: {e}")
        return pd.DataFrame()
