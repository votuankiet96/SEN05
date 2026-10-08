"""Mọi truy cập SQL của DPS — CHỈ SELECT (test kiến trúc kiểm điều này).

Đọc ba thứ: danh sách pair (Dim_Symbol x Dim_Timeframe), nến theo cửa sổ giờ phát, và nến seed trước T0.
Truy vấn dùng index covering IX_Fact_Sym_TF_Time (SymbolID, TimeframeID, BarTime) nên khoảng thời gian được
viết dạng sargable: giờ phát trong (a, b] tương đương BarTime trong (a - Minutes, b - Minutes]. Dữ liệu SQL
được coi là đúng tuyệt đối: DPS không sửa, không bù, không suy diễn nến thiếu.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Sequence

import pyodbc

from configuration import SqlConfig
from runtime import Candle, DpsError, Pair, log_event

LOGGER = logging.getLogger("dps.sql_source")

_SQL_SYMBOLS = "SELECT SymbolID, Symbol FROM DWH.Dim_Symbol WHERE IsActive = 1"
_SQL_TIMEFRAMES = "SELECT TimeframeID, Code, Minutes FROM DWH.Dim_Timeframe"
_SQL_WINDOW = (
    "SELECT BarTime, [Open], High, Low, [Close] FROM DWH.Fact_OHLCV "
    "WHERE SymbolID = ? AND TimeframeID = ? AND BarTime > ? AND BarTime <= ? ORDER BY BarTime"
)
_SQL_SEED = (
    "SELECT TOP (?) BarTime, [Open], High, Low, [Close] FROM DWH.Fact_OHLCV "
    "WHERE SymbolID = ? AND TimeframeID = ? AND BarTime <= ? ORDER BY BarTime DESC"
)
STATEMENTS = (_SQL_SYMBOLS, _SQL_TIMEFRAMES, _SQL_WINDOW, _SQL_SEED)


class SourceError(DpsError):
    """Không kết nối được SQL hoặc symbol/khung yêu cầu không tồn tại."""


def _braced(value: str) -> str:
    # ODBC cho phép bọc giá trị trong {} để chứa dấu ';'; ký tự '}' được nhân đôi.
    return "{" + value.replace("}", "}}") + "}"


def _connection_string(config: SqlConfig) -> str:
    server = f"{config.server},{config.port}" if config.port else config.server
    parts = [
        f"DRIVER={{{config.driver}}}", f"SERVER={server}", f"DATABASE={config.database}", "APP=dps",
        f"Encrypt={config.encrypt}", f"TrustServerCertificate={'yes' if config.trust_server_certificate else 'no'}",
    ]
    if config.username and config.password:
        parts += [f"UID={_braced(config.username)}", f"PWD={_braced(config.password)}"]
    elif config.trusted_connection:
        parts.append("Trusted_Connection=yes")
    else:
        raise SourceError("SQL credentials are missing and trusted_connection is disabled")
    return ";".join(parts) + ";"


def _connect(config: SqlConfig) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, config.retry_count + 1):
        try:
            connection = pyodbc.connect(_connection_string(config), timeout=config.login_timeout_seconds)
            connection.autocommit = True
            connection.timeout = config.command_timeout_seconds
            return connection
        except pyodbc.Error as exc:
            last_error = exc
            log_event(LOGGER, logging.WARNING, "SQL_CONNECT_RETRY", attempt=attempt, max_attempts=config.retry_count,
                      error_type=type(exc).__name__)
            if attempt < config.retry_count:
                time.sleep(config.retry_delay_seconds)
    raise SourceError(f"SQL Server connection failed ({type(last_error).__name__})") from last_error


class SqlSource:
    def __init__(self, config: SqlConfig, *, connection: Any = None) -> None:
        self._connection = connection if connection is not None else _connect(config)

    def __enter__(self) -> "SqlSource":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._connection.close()
        except Exception:  # noqa: BLE001 - đóng kết nối không được làm hỏng luồng xử lý lỗi chính
            pass

    def pairs(self, symbols: Sequence[str], timeframes: Sequence[str]) -> list[Pair]:
        """Các pair (symbol x khung) được chọn, sắp theo (SymbolID, độ dài khung); tên lạ hoặc symbol tắt -> lỗi."""
        cursor = self._connection.cursor()
        cursor.execute(_SQL_SYMBOLS)
        known_symbols = {str(name).upper(): (int(symbol_id), str(name)) for symbol_id, name in cursor.fetchall()}
        cursor.execute(_SQL_TIMEFRAMES)
        known_frames = {str(code).upper(): (int(frame_id), str(code), int(minutes)) for frame_id, code, minutes in cursor.fetchall()}
        unknown = [name for name in symbols if name not in known_symbols] + [name for name in timeframes if name not in known_frames]
        if unknown:
            raise SourceError(f"unknown or inactive symbol/timeframe: {', '.join(unknown)}")
        pairs = [
            Pair(known_symbols[s][1], known_symbols[s][0], known_frames[t][1], known_frames[t][0], known_frames[t][2])
            for s in symbols for t in timeframes
        ]
        return sorted(pairs, key=lambda pair: (pair.symbol_id, pair.minutes))

    def window(self, pairs: Sequence[Pair], after: datetime, upto: datetime) -> list[Candle]:
        """Mọi nến có giờ phát (BarTime + Minutes) trong (after, upto]."""
        cursor = self._connection.cursor()
        candles: list[Candle] = []
        for pair in pairs:
            shift = timedelta(minutes=pair.minutes)
            cursor.execute(_SQL_WINDOW, pair.symbol_id, pair.timeframe_id, after - shift, upto - shift)
            candles.extend(Candle(pair, *row) for row in cursor.fetchall())
        return candles

    def seed(self, pairs: Sequence[Pair], at: datetime, limit: int) -> list[Candle]:
        """Tối đa `limit` nến đã đóng (giờ phát <= at) của mỗi pair; theo thứ tự pair, tăng dần theo thời gian."""
        cursor = self._connection.cursor()
        candles: list[Candle] = []
        for pair in pairs:
            cursor.execute(_SQL_SEED, limit, pair.symbol_id, pair.timeframe_id, at - timedelta(minutes=pair.minutes))
            candles.extend(Candle(pair, *row) for row in reversed(cursor.fetchall()))
        return candles
