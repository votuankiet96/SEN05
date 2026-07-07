"""
Kết nối SQL Server cho OG — chỉ đọc (SELECT), không ghi.

Mô tả:
    Bản rút gọn từ modules/db_connector.py gốc của SEN05_Autotrading. File
    gốc còn sở hữu toàn bộ tầng ghi/ETL (staging, MERGE, aggregate timeframe
    phái sinh, purge, gap-check, delete...) phục vụ pipeline lấy dữ liệu trên
    DP6 — không thuộc phạm vi OG và có ghi chú "rủi ro cao" trong chính file
    gốc. OG (data/loader.py) chỉ từng gọi đúng một hàm của file gốc:
    get_connection(). Toàn bộ phần ghi/ETL không được port sang đây.

Đầu ra:
    get_connection() -> pyodbc.Connection, có retry khi lỗi kết nối tạm thời.

Phụ thuộc ngoài:
    pyodbc, core_python.config (SQL_* settings).
"""

from __future__ import annotations

import logging
import time

import pyodbc

from core_python.config import (
    SQL_DATABASE,
    SQL_DRIVER,
    SQL_ENCRYPT,
    SQL_PORT,
    SQL_PWD,
    SQL_SERVER,
    SQL_TDS_VERSION,
    SQL_TRUST_SERVER_CERT,
    SQL_UID,
)

logger = logging.getLogger(__name__)

_DB_RETRY_COUNT = 3
_DB_RETRY_DELAY = 5  # giây giữa các lần thử


def _build_conn_str() -> str:
    """Dựng connection string; ưu tiên SQL auth, fallback Windows Integrated Auth."""
    if str(SQL_DRIVER).lower() == "freetds":
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

    Tại sao cần retry:
        Lỗi mạng/DB ngắn hạn hay xảy ra trong pipeline; retry giảm false-fail
        cho job chạy tự động (giữ nguyên hành vi của modules/db_connector.py gốc).

    Returns:
        pyodbc.Connection đang mở.

    Raises:
        pyodbc.Error nếu hết số lần retry vẫn không kết nối được.
    """
    last_err: Exception = RuntimeError("unreachable")
    for attempt in range(1, _DB_RETRY_COUNT + 1):
        try:
            return pyodbc.connect(_build_conn_str(), timeout=30)
        except pyodbc.Error as e:
            last_err = e
            logger.warning("DB connect attempt %d/%d failed: %s", attempt, _DB_RETRY_COUNT, e)
            if attempt < _DB_RETRY_COUNT:
                time.sleep(_DB_RETRY_DELAY)
    logger.error("Cannot connect to SQL Server after %d attempts: %s", _DB_RETRY_COUNT, last_err)
    raise last_err
