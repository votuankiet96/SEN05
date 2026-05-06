"""
Tải dữ liệu OHLCV từ SQL Server cho dashboard và watcher.

Mô tả:
    Kết nối SQL Server qua modules.db_connector, truy vấn bảng DWH.Fact_OHLCV
    lấy N bar gần nhất cho một symbol và khung thời gian, rồi trả về DataFrame
    đã được làm sạch và sắp xếp tăng dần theo thời gian.

Đầu ra:
    pd.DataFrame với các cột: [bartime, open, high, low, close, volume].
    bartime là UTC-naive (không có timezone info).

Hợp đồng UTC (UTC Contract):
    BarTime được lưu dạng UTC-naive trong DB theo chuẩn Capital.com / MT5.
    Caller cần tự localize về UTC nếu cần so sánh với timestamp có timezone.
    Xem _drop_open_bar() trong signal_watcher.py để biết cách xử lý đúng.

Phụ thuộc ngoài:
    modules.db_connector.get_connection() — kết nối pyodbc đến SQL Server.
"""

from __future__ import annotations

import logging

import pandas as pd

from modules.db_connector import get_connection
from core_python.config import get_symbol

logger = logging.getLogger(__name__)

OHLCV_COLUMNS = ["bartime", "open", "high", "low", "close", "volume"]


def _read_sql(query: str, conn, params: tuple) -> pd.DataFrame:
    """
    Thực thi câu SQL qua pyodbc cursor và trả về DataFrame.

    Args:
        query: Câu SQL có placeholder '?'.
        conn: Kết nối pyodbc đang mở.
        params: Tuple tham số truyền vào câu SQL theo thứ tự.

    Returns:
        DataFrame với tên cột lấy từ cursor.description.

    Side Effects:
        Mở và đóng cursor. Không đóng connection (caller chịu trách nhiệm).
    """
    cursor = conn.cursor()
    try:
        cursor.execute(query, params)
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
        return pd.DataFrame.from_records(rows, columns=columns)
    finally:
        cursor.close()


def _validate(df: pd.DataFrame, symbol: str, tf: str) -> pd.DataFrame:
    """
    Loại bỏ các dòng dữ liệu rõ ràng là lỗi; không phân tích gap thời gian.

    Các bước lọc (theo thứ tự):
        1. Loại dòng có bartime không parse được (NaT).
        2. Loại dòng có bất kỳ giá OHLC nào là NaN (volume được phép NaN).
        3. Loại dòng trùng bartime — giữ lại dòng cuối (lần ghi mới nhất).
        4. Sắp xếp tăng dần theo bartime.

    Args:
        df: DataFrame sau khi parse kiểu dữ liệu.
        symbol: Dùng cho warning log.
        tf: Dùng cho warning log.

    Returns:
        DataFrame đã làm sạch, reset index.

    Side Effects:
        Ghi warning log nếu có dòng bị loại.
    """
    n_in = len(df)

    # Bước 1: Loại dòng bartime NaT (lỗi parse từ DB)
    df = df.dropna(subset=["bartime"])

    # Bước 2: OHLC phải đầy đủ; volume có thể null (một số broker không cung cấp)
    df = df.dropna(subset=["open", "high", "low", "close"])

    # Bước 3: Giữ lần ghi cuối nếu có nhiều dòng cùng bartime
    df = df.drop_duplicates(subset=["bartime"], keep="last")

    # Bước 4: Đảm bảo thứ tự tăng dần (DB trả về DESC, đã reverse ở load())
    df = df.sort_values("bartime").reset_index(drop=True)

    n_dropped = n_in - len(df)
    if n_dropped:
        logger.warning("loader: dropped %d bad rows for %s %s", n_dropped, symbol, tf)

    return df


def load(symbol: str, tf: str, n_bars: int) -> pd.DataFrame:
    """
    Tải N bar OHLCV gần nhất từ DWH.Fact_OHLCV và trả về DataFrame sạch.

    Hợp đồng UTC: BarTime lưu UTC-naive trong DB (Capital.com / MT5).
    Caller cần tự localize về UTC nếu cần so sánh timezone-aware.

    Args:
        symbol: Mã TradingView (ví dụ: "US30"). Phải có trong config.SYMBOLS.
        tf: Mã khung thời gian (ví dụ: "H1", "M5"). Phải tồn tại trong DWH.Dim_Timeframe.
        n_bars: Số bar tối đa. Query dùng TOP (n_bars) ORDER BY DESC.

    Returns:
        DataFrame với cột [bartime, open, high, low, close, volume], sắp xếp tăng dần.
        Trả về DataFrame rỗng (cùng schema) nếu không có dữ liệu.

    Side Effects:
        Mở và đóng kết nối DB sau mỗi lần gọi.

    Giả định giao dịch:
        BarTime trong DB là thời điểm bar tương ứng — UTC-naive.
        Bar cuối trong kết quả có thể là bar đang mở (chưa đóng).
        Caller cần tự lọc bar đang mở nếu cần — xem _drop_open_bar() trong signal_watcher.py.
    """
    symbol_cfg = get_symbol(symbol)
    tf_code = str(tf).strip().upper()
    limit = max(1, int(n_bars))

    # Lấy TOP N bars mới nhất bằng ORDER BY DESC, sau đó đảo lại thành tăng dần ở client.
    # JOIN với Dim_Timeframe để lọc theo mã TF thay vì TimeframeID trực tiếp.
    query = """
        SELECT TOP (?)
               f.BarTime AS bartime,
               f.[Open] AS [open],
               f.High AS [high],
               f.Low AS [low],
               f.[Close] AS [close],
               f.Volume AS [volume]
        FROM DWH.Fact_OHLCV f
        JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
        WHERE f.SymbolID = ?
          AND tf.Code = ?
        ORDER BY f.BarTime DESC
    """

    conn = get_connection()
    try:
        df = _read_sql(query, conn, params=(limit, symbol_cfg["symbol_id"], tf_code))
    finally:
        conn.close()

    if df.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    # Parse kiểu dữ liệu: bartime → datetime UTC-naive, OHLCV → float
    df["bartime"] = pd.to_datetime(df["bartime"], errors="coerce")
    df = df.sort_values("bartime").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = _validate(df, symbol, tf_code)
    return df[OHLCV_COLUMNS]
