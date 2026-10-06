"""
Tầng duy nhất kết nối SQL Server (DP6) và cung cấp dữ liệu cho core_python.

Mô tả:
    File này có đúng 2 trách nhiệm:
    1. Kết nối: nạp config.yaml (mục `sql:`), build connection string, mở
       kết nối pyodbc có retry (get_connection()).
    2. Cung cấp dữ liệu cho hệ thống:
       - OHLCV lịch sử: SELECT DWH.Fact_OHLCV -> DataFrame sạch.
         load(symbol, tf, n_bars, before=None): N bar mới nhất, hoặc N bar
         ngay trước một cursor thời gian (dùng cho chart/dashboard).
         load_range(symbol, tf, date_from, date_to): theo khoảng thời gian,
         không giới hạn số bar (dùng cho export_cli.py) — để trống cả 2 mốc
         = toàn bộ lịch sử có trong DP6.
       - Dữ liệu tham chiếu symbol/timeframe: query trực tiếp
         DWH.Dim_Symbol/Dim_Timeframe, KHÔNG hardcode trong code
         (symbols(), tf_minutes(), get_symbol()) — cache lại 1 lần trong
         tiến trình, không query lại cho mỗi lần tra cứu.

    Tham số chiến lược (X, MA_PERIOD...) KHÔNG nằm ở đây — đó là
    core_python/configuration.py.

Hợp đồng UTC (UTC Contract):
    BarTime được lưu dạng UTC-naive trong DB theo chuẩn Capital.com / MT5.
    Caller cần tự localize về UTC nếu cần so sánh với timestamp có timezone.
    Bar cuối trong kết quả load() có thể là bar đang mở (chưa đóng) —
    caller tự lọc nếu cần chỉ dùng bar đã đóng.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pyodbc
import yaml

logger = logging.getLogger(__name__)

OHLCV_COLUMNS = ["bartime", "open", "high", "low", "close", "volume"]

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "cp_config.yaml"


def _load_config() -> dict[str, Any]:
    """Đọc cp_config.yaml; trả về dict rỗng nếu file chưa tồn tại."""
    if not _CONFIG_PATH.exists():
        return {}
    with _CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


_SQL: dict[str, Any] = _load_config().get("sql") or {}


# =============================================================================
# 1. Kết nối SQL Server — operator xác định trong config.yaml (mục `sql:`).
# =============================================================================

SQL_SERVER = str(_SQL.get("server") or "localhost")
SQL_DATABASE = str(_SQL.get("database") or "")
SQL_DRIVER = str(_SQL.get("driver") or "freetds")
SQL_PORT = str(_SQL.get("port") or "1433")
SQL_TDS_VERSION = str(_SQL.get("tds_version") or "7.4")
SQL_UID = str(_SQL.get("uid") or "")
SQL_PWD = str(_SQL.get("pwd") or "")
SQL_ENCRYPT = "yes" if _SQL.get("encrypt") else "no"
SQL_TRUST_SERVER_CERT = "no" if _SQL.get("trust_server_cert") is False else "yes"
SQL_RETRY_COUNT = int(_SQL.get("retry_count") or 3)
SQL_RETRY_DELAY = int(_SQL.get("retry_delay_seconds") or 5)
SQL_TIMEOUT = int(_SQL.get("timeout_seconds") or 30)


def sql_connection_string() -> str:
    """
    Ghép các thông tin SQL trong config.yaml thành connection string ODBC.

    Hàm này chỉ tạo chuỗi kết nối. Việc thật sự mở kết nối nằm ở
    get_connection().
    """
    if SQL_DRIVER.lower() == "freetds":
        server_part = f"SERVER={SQL_SERVER};"
        if SQL_PORT:
            server_part += f"PORT={SQL_PORT};"
        base = (
            f"DRIVER={{{SQL_DRIVER}}};"
            f"{server_part}"
            f"DATABASE={SQL_DATABASE};"
            f"TDS_Version={SQL_TDS_VERSION};"
        )
    else:
        base = (
            f"DRIVER={{{SQL_DRIVER}}};"
            f"SERVER={SQL_SERVER};"
            f"DATABASE={SQL_DATABASE};"
            f"Encrypt={SQL_ENCRYPT};"
            f"TrustServerCertificate={SQL_TRUST_SERVER_CERT};"
        )
    if SQL_UID and SQL_PWD:
        return base + f"UID={SQL_UID};PWD={SQL_PWD};"
    return base + "Trusted_Connection=yes;"


def get_connection() -> pyodbc.Connection:
    """
    Trả về kết nối pyodbc đang sống, có retry khi lỗi kết nối tạm thời.

    Returns:
        pyodbc.Connection đang mở.

    Raises:
        pyodbc.Error nếu hết số lần retry vẫn không kết nối được.
    """
    last_err: Exception = RuntimeError("unreachable")
    for attempt in range(1, SQL_RETRY_COUNT + 1):
        try:
            return pyodbc.connect(sql_connection_string(), timeout=SQL_TIMEOUT)
        except pyodbc.Error as e:
            last_err = e
            logger.warning("DB connect attempt %d/%d failed: %s", attempt, SQL_RETRY_COUNT, e)
            if attempt < SQL_RETRY_COUNT:
                time.sleep(SQL_RETRY_DELAY)
    logger.error("Cannot connect to SQL Server after %d attempts: %s", SQL_RETRY_COUNT, last_err)
    raise last_err


# =============================================================================
# 2a. Dữ liệu tham chiếu symbol/timeframe — nguồn sự thật là DWH.Dim_Symbol /
#    DWH.Dim_Timeframe trên DP6, KHÔNG hardcode trong code. Query 1 lần khi
#    có chỗ cần rồi cache lại trong tiến trình.
# =============================================================================

_symbols_cache: dict[str, dict[str, Any]] | None = None
_tf_cache: dict[str, int] | None = None


def _fetch_symbols() -> dict[str, dict[str, Any]]:
    """
    Đọc danh sách symbol đang active từ DWH.Dim_Symbol.

    Kết quả trả về theo dạng:
        {symbol: {symbol_id, label, asset_type}}
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT SymbolID, Symbol, AssetType FROM DWH.Dim_Symbol WHERE IsActive = 1")
        rows = cursor.fetchall()
    finally:
        conn.close()
    return {
        str(symbol).strip().upper(): {
            "symbol_id": int(symbol_id),
            "label": str(symbol).strip().upper(),
            "asset_type": asset_type or "",
        }
        for symbol_id, symbol, asset_type in rows
    }


def _fetch_tf_minutes() -> dict[str, int]:
    """
    Đọc danh sách timeframe từ DWH.Dim_Timeframe.

    Kết quả trả về theo dạng:
        {"M30": 30, "H1": 60, ...}
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT Code, Minutes FROM DWH.Dim_Timeframe")
        rows = cursor.fetchall()
    finally:
        conn.close()
    return {str(code).strip().upper(): int(minutes) for code, minutes in rows}


def symbols() -> dict[str, dict[str, Any]]:
    """Toàn bộ symbol đang active trên DP6 — query lần đầu, cache trong tiến trình."""
    global _symbols_cache
    if _symbols_cache is None:
        _symbols_cache = _fetch_symbols()
    return _symbols_cache


def tf_minutes() -> dict[str, int]:
    """Map mã timeframe -> số phút — query lần đầu, cache trong tiến trình."""
    global _tf_cache
    if _tf_cache is None:
        _tf_cache = _fetch_tf_minutes()
    return _tf_cache


def get_symbol(symbol: str) -> dict[str, Any]:
    """
    Tra cứu metadata cho một symbol.

    Args:
        symbol: Mã TradingView (không phân biệt hoa/thường).

    Returns:
        Bản sao dict metadata gồm symbol_id, label, asset_type.

    Raises:
        KeyError: Nếu symbol không có trên DP6 (hoặc IsActive=0).
    """
    key = str(symbol).strip().upper()
    table = symbols()
    if key not in table:
        raise KeyError(f"Unknown symbol '{symbol}'. Available: {', '.join(sorted(table))}")
    return dict(table[key])


# =============================================================================
# 2b. OHLCV lịch sử — SELECT DWH.Fact_OHLCV -> DataFrame sạch.
# =============================================================================


def _read_sql(query: str, conn: pyodbc.Connection, params: tuple) -> pd.DataFrame:
    """Thực thi câu SQL qua pyodbc cursor và trả về DataFrame."""
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
    """
    n_in = len(df)

    df = df.dropna(subset=["bartime"])
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df.drop_duplicates(subset=["bartime"], keep="last")
    df = df.sort_values("bartime").reset_index(drop=True)

    n_dropped = n_in - len(df)
    if n_dropped:
        logger.warning("data: dropped %d bad rows for %s %s", n_dropped, symbol, tf)

    return df


def _run_ohlcv_query(query: str, params: tuple, *, symbol: str, tf_code: str, needs_sort: bool) -> pd.DataFrame:
    """Chạy 1 câu SELECT OHLCV, chuẩn hoá kiểu dữ liệu + validate.

    Dùng chung cho load() (TOP N mới nhất, cần đảo lại tăng dần) và
    load_range() (đã ORDER BY tăng dần sẵn trong SQL, không cần đảo).
    """
    conn = get_connection()
    try:
        df = _read_sql(query, conn, params=params)
    finally:
        conn.close()

    if df.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    df["bartime"] = pd.to_datetime(df["bartime"], errors="coerce")
    if needs_sort:
        df = df.sort_values("bartime").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = _validate(df, symbol, tf_code)
    return df[OHLCV_COLUMNS]


def load(
    symbol: str,
    tf: str,
    n_bars: int,
    *,
    before: object | None = None,
) -> pd.DataFrame:
    """
    Tải N bar OHLCV gần nhất từ DWH.Fact_OHLCV và trả về DataFrame sạch.

    Args:
        symbol: Mã TradingView (ví dụ: "US30"). Phải có trong symbols().
        tf: Mã khung thời gian (ví dụ: "H1", "M5"). Phải tồn tại trong
            tf_minutes().
        n_bars: Số bar tối đa. Query dùng TOP (n_bars) ORDER BY DESC.
        before: Cursor UTC tùy chọn. Khi có giá trị, chỉ lấy bar có BarTime
            nhỏ hơn cursor; None giữ nguyên hành vi lấy bar mới nhất.

    Returns:
        DataFrame với cột [bartime, open, high, low, close, volume], sắp xếp
        tăng dần. Trả về DataFrame rỗng (cùng schema) nếu không có dữ liệu.

    Giả định giao dịch:
        BarTime trong DB là thời điểm bar tương ứng — UTC-naive.
        Bar cuối trong kết quả có thể là bar đang mở (chưa đóng).
    """
    symbol_cfg = get_symbol(symbol)
    tf_code = str(tf).strip().upper()
    if tf_code not in tf_minutes():
        raise ValueError(f"Unsupported timeframe '{tf_code}'.")
    limit = max(1, int(n_bars))

    cutoff = None
    if before is not None:
        try:
            cutoff = pd.Timestamp(before)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Invalid OHLCV before cursor") from exc
        if pd.isna(cutoff):
            raise ValueError("Invalid OHLCV before cursor")
        if cutoff.tzinfo is not None:
            cutoff = cutoff.tz_convert("UTC").tz_localize(None)

    # Lấy TOP N bars mới nhất bằng ORDER BY DESC, sau đó đảo lại thành tăng dần.
    # JOIN với Dim_Timeframe để lọc theo mã TF thay vì TimeframeID trực tiếp.
    before_clause = "AND f.BarTime < ?" if cutoff is not None else ""
    query = f"""
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
          {before_clause}
        ORDER BY f.BarTime DESC
    """
    query_params: tuple[object, ...] = (limit, symbol_cfg["symbol_id"], tf_code)
    if cutoff is not None:
        query_params += (cutoff.to_pydatetime(),)
    return _run_ohlcv_query(
        query,
        query_params,
        symbol=symbol,
        tf_code=tf_code,
        needs_sort=True,
    )


def load_range(symbol: str, tf: str, date_from: str | None = None, date_to: str | None = None) -> pd.DataFrame:
    """
    Tải OHLCV theo khoảng thời gian từ DWH.Fact_OHLCV — không giới hạn số bar.

    Args:
        symbol: Mã TradingView. Phải có trong symbols().
        tf: Mã khung thời gian. Phải tồn tại trong tf_minutes().
        date_from: Mốc bắt đầu (vd "2020-01-01"). None/rỗng = lấy từ bar
            đầu tiên có trong DP6 (không giới hạn dưới).
        date_to: Mốc kết thúc. None/rỗng = tới bar gần nhất (không giới
            hạn trên).

    Returns:
        DataFrame [bartime, open, high, low, close, volume], sắp xếp tăng
        dần. Trả về DataFrame rỗng (cùng schema) nếu không có dữ liệu.
    """
    symbol_cfg = get_symbol(symbol)
    tf_code = str(tf).strip().upper()
    if tf_code not in tf_minutes():
        raise ValueError(f"Unsupported timeframe '{tf_code}'.")

    where = ["f.SymbolID = ?", "tf.Code = ?"]
    params: list[object] = [symbol_cfg["symbol_id"], tf_code]
    if date_from:
        where.append("f.BarTime >= ?")
        params.append(date_from)
    if date_to:
        where.append("f.BarTime <= ?")
        params.append(date_to)

    query = f"""
        SELECT f.BarTime AS bartime,
               f.[Open] AS [open],
               f.High AS [high],
               f.Low AS [low],
               f.[Close] AS [close],
               f.Volume AS [volume]
        FROM DWH.Fact_OHLCV f
        JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
        WHERE {" AND ".join(where)}
        ORDER BY f.BarTime ASC
    """
    return _run_ohlcv_query(
        query,
        tuple(params),
        symbol=symbol,
        tf_code=tf_code,
        needs_sort=False,
    )


def load_range_with_warmup(
    symbol: str,
    tf: str,
    date_from: object,
    date_to: object,
    warmup_bars: int,
) -> pd.DataFrame:
    """
    Như `load_range()`, nhưng tải thêm `warmup_bars` bar NGAY TRƯỚC
    `date_from` để chỉ báo (SMA/MACD/ATR/KNN trend) kịp "ấm" trước khi vào
    đúng khoảng yêu cầu — không có bước này, vài bar đầu của khoảng sẽ NaN/
    thiếu tín hiệu thật dù dữ liệu đó thực sự tồn tại trước đó trong DP6.

    `date_from`/`date_to` chấp nhận chuỗi (vd "2024-01-01", dùng bởi
    export_cli.py) hoặc `pd.Timestamp` (dùng bởi signal_display/server.py)
    — tự chuẩn hoá sang `datetime` an toàn cho pyodbc trước khi query,
    giống hệt cách `load()` đã làm cho tham số `before`.

    `date_from` bắt buộc (không như `load_range`, không có nghĩa "warmup
    cho toàn bộ lịch sử" — full-history export không cần bước này, không
    có gì đứng "trước" bar đầu tiên).

    Caller (export_cli.py, signal_display/server.py) tự chịu trách nhiệm
    cắt kết quả về lại đúng `bartime >= date_from` sau khi chạy strategy —
    hàm này chỉ lo phần load, không biết gì về pipeline chiến lược.
    """
    from_sql = pd.Timestamp(date_from).to_pydatetime()
    to_sql = pd.Timestamp(date_to).to_pydatetime() if date_to else None
    warmup = load(symbol, tf, warmup_bars, before=date_from)
    window = load_range(symbol, tf, from_sql, to_sql)
    non_empty = [frame for frame in (warmup, window) if not frame.empty]
    if not non_empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    return (
        pd.concat(non_empty, ignore_index=True)
        .drop_duplicates(subset=["bartime"], keep="last")
        .sort_values("bartime")
        .reset_index(drop=True)
    )
