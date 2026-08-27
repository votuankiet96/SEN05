"""The only DP Program V3 module that talks to SQL Server."""
from __future__ import annotations
import logging, re, time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable
import pyodbc
from ..log import log_event
LOGGER = logging.getLogger(__name__)
_SQL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")
Pair = tuple[dict[str, Any], dict[str, Any]]
_LIVE_ASSET_TYPES = {"CRYPTO", "INDICE", "METAL"}

# Đây là file duy nhất trong engine được nói chuyện với SQL Server.
# File khác muốn đọc symbol/timeframe, đọc mốc nến, hoặc ghi SQL đều phải gọi qua đây.
# Làm vậy để toàn bộ quy ước SQL nằm một chỗ, dễ kiểm tra.

# Một pair nghĩa là một cặp symbol + timeframe.
# Danh sách gốc lấy từ bảng dimension trong SQL.


def warehouse_timestamp(value: datetime) -> datetime:
    """Normalize an aware UTC timestamp for SQL DATETIME2(0)."""
    # SQL lưu thời gian UTC đến giây, không lưu microsecond.
    normalized = value if value.tzinfo is None else value.astimezone(timezone.utc).replace(
        tzinfo=None
    )
    return normalized.replace(microsecond=0)


def _decimal_text(value: Any, *, precision: int, scale: int) -> str:
    # Ép số về đúng độ chính xác mà SQL đang lưu.
    try:
        normalized = Decimal(value).quantize(
            Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid SQL decimal value: {value!r}") from exc
    if not normalized.is_finite() or abs(normalized) >= Decimal(10) ** (precision - scale):
        raise ValueError(f"SQL decimal value exceeds DECIMAL({precision},{scale})")
    return f"{normalized:.{scale}f}"


def warehouse_value_signature(
    open_: Any, high: Any, low: Any, close: Any, volume: Any
) -> tuple[str | None, ...]:
    """Normalize OHLCV values exactly to the warehouse DECIMAL contract."""
    # Tạo dấu so sánh cho OHLCV.
    # Dùng để biết nến mới có khác nến trong SQL không.
    prices = tuple(
        _decimal_text(value, precision=18, scale=8)
        for value in (open_, high, low, close)
    )
    normalized_volume = (
        None if volume is None else _decimal_text(volume, precision=20, scale=4)
    )
    return (*prices, normalized_volume)


def candle_signature(candle: dict[str, Any]) -> tuple[str | None, ...]:
    """Normalize one candle exactly to the warehouse DECIMAL contract."""
    # Đưa một nến về dạng so sánh được với SQL.
    return warehouse_value_signature(
        candle["open"], candle["high"], candle["low"], candle["close"], candle.get("volume")
    )


def prepare_warehouse_rows(candles: Iterable[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """Build SQL rows while reusing signatures computed during comparison."""
    # Chuẩn bị dữ liệu để đưa vào bảng tạm SQL.
    return [
        (
            int(candle["symbol_id"]),
            warehouse_timestamp(candle["timestamp"]),
            *(candle.get("_signature") or candle_signature(candle)),
        )
        for candle in candles
    ]


def _quoted_name(name: str) -> str:
    # Chỉ chấp nhận tên bảng/procedure dạng Schema.Object.
    # Sau đó quote lại để tránh tên SQL không an toàn.
    if not _SQL_NAME.fullmatch(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return ".".join(f"[{part}]" for part in name.split("."))
def build_connection_string(sql: dict[str, Any]) -> str:
    """Build a pyodbc connection string from resolved configuration."""
    # Tạo connection string từ config.
    # Không log chuỗi này vì có thể chứa password.
    server = sql["server"]
    if sql.get("port"):
        server = f"{server},{sql['port']}"
    parts = [f"DRIVER={{{sql['driver']}}}", f"SERVER={server}",
             f"DATABASE={sql['database']}", f"Encrypt={sql.get('encrypt', 'no')}",
             f"TrustServerCertificate={'yes' if sql.get('trust_server_certificate') else 'no'}"]
    if sql.get("username") and sql.get("password"):
        parts.extend((f"UID={sql['username']}", f"PWD={sql['password']}"))
    elif sql.get("trusted_connection", True):
        parts.append("Trusted_Connection=yes")
    else:
        raise ValueError("SQL credentials are missing and trusted_connection is disabled")
    return ";".join(parts) + ";"
def get_connection(config: dict[str, Any]) -> pyodbc.Connection:
    """Open a SQL connection with one bounded retry policy."""
    # Mở kết nối SQL với số lần retry có giới hạn.
    sql, last_error = config["sql_server"], None
    attempts, started = int(sql["retry_count"]), time.monotonic()
    for attempt in range(1, attempts + 1):
        attempt_started = time.monotonic()
        try:
            connection = pyodbc.connect(
                build_connection_string(sql), timeout=int(sql["timeout_seconds"]))
            connection.timeout = int(sql["command_timeout_seconds"])
            if attempt > 1:
                log_event(
                    LOGGER, logging.INFO, "SQL_CONNECTION_RECOVERED", "NONE",
                    component="sql", attempts=attempt, duration_seconds=round(
                        time.monotonic() - started, 3))
            return connection
        except pyodbc.Error as exc:
            last_error = exc
            if attempt < attempts:
                log_event(
                    LOGGER, logging.WARNING, "SQL_CONNECTION_RETRY", "LOW",
                    component="sql", attempt=attempt, max_attempts=attempts,
                    duration_seconds=round(time.monotonic() - attempt_started, 3),
                    error_type=type(exc).__name__, action="bounded SQL connection retry")
                time.sleep(float(sql.get("retry_delay_seconds", 1)))
    raise ConnectionError(f"SQL Server connection failed: {last_error}") from last_error
def fetch_universe(
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read symbol and timeframe definitions from the canonical DWH dimensions."""
    # Đọc danh sách symbol/timeframe từ SQL.
    # `BrokerChannel` là phần đứng trước symbol TradingView, ví dụ CAPITALCOM:GOLD.
    connection = get_connection(config)
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT SymbolID,Symbol,BrokerChannel,AssetType,IsActive FROM DWH.Dim_Symbol ORDER BY SymbolID"
        )
        symbols = []
        for row in cursor.fetchall():
            # Symbol đang bật phải có BrokerChannel.
            # Nếu thiếu thì báo lỗi ngay để tránh gọi sai mã TradingView.
            enabled = bool(row[4])
            exchange = str(row[2] or "").strip()
            if enabled and not exchange:
                raise ValueError(
                    f"DWH.Dim_Symbol.BrokerChannel is null/empty for active "
                    f"symbol {row[1]!r} (SymbolID={row[0]})"
                )
            symbols.append({"symbol_id": int(row[0]), "exchange": exchange,
                             "symbol": str(row[1]), "asset_type": str(row[3]), "enabled": enabled})
        cursor.execute(
            "SELECT Code,Minutes,SourceTable FROM DWH.Dim_Timeframe ORDER BY Minutes"
        )
        timeframes = [
            # TradingView dùng phút cho intraday, nhưng D1/W cần chuỗi riêng.
            {"code": str(row[0]),
             "interval": "1D" if int(row[1]) == 1440 else "1W" if int(row[1]) == 10080 else str(int(row[1])),
             "minutes": int(row[1]), "staging_table": f"SEN.{row[2]}"}
            for row in cursor.fetchall()
        ]
        return symbols, timeframes
    finally:
        connection.close()


def _selection(values: Any, name: str) -> set[str]:
    # Kiểm danh sách operator nhập trong Config.yaml.
    # Không được rỗng hoặc trùng.
    if not isinstance(values, list) or not values:
        raise ValueError(f"{name} must be a non-empty list")
    normalized = [str(value).strip().upper() for value in values]
    if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
        raise ValueError(f"{name} contains an empty or duplicate value")
    return set(normalized)


def _index(rows: list[dict[str, Any]], key: str, name: str) -> dict[str, dict[str, Any]]:
    # Tạo index theo symbol hoặc timeframe code.
    # Nếu SQL có trùng thì báo lỗi ngay.
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row.get(key) or "").strip().upper()
        if not value or value in indexed:
            raise ValueError(f"SQL {name} contains an empty or duplicate {key}")
        indexed[value] = row
    return indexed


def select_pairs(
    config: dict[str, Any],
    *,
    live: bool,
    symbol_filter: str | None = None,
    timeframe_filter: str | None = None,
) -> list[Pair]:
    """Resolve SQL definitions, operator live selection, and optional CLI filters."""
    # Chọn danh sách cặp cần chạy.
    # Backfill chạy toàn bộ symbol/timeframe đang bật.
    # Live chỉ chạy danh sách operator chọn trong Config.yaml.
    sql_symbols, sql_timeframes = fetch_universe(config)
    symbols_by_name = _index(sql_symbols, "symbol", "symbol universe")
    timeframes_by_code = _index(sql_timeframes, "code", "timeframe universe")
    active = {
        name: symbol for name, symbol in symbols_by_name.items() if symbol["enabled"]
    }
    if live:
        # Live chỉ nhận symbol/timeframe có trong SQL và đang active.
        selected_symbols = _selection(config["live"].get("symbols"), "live.symbols")
        selected_timeframes = _selection(
            config["live"].get("timeframes"), "live.timeframes"
        )
        unknown_symbols = selected_symbols - set(active)
        unknown_timeframes = selected_timeframes - set(timeframes_by_code)
        if unknown_symbols or unknown_timeframes:
            invalid = sorted(unknown_symbols | unknown_timeframes)
            raise ValueError(f"unknown or inactive live selection: {', '.join(invalid)}")
        forbidden = sorted(
            name
            for name in selected_symbols
            if str(active[name]["asset_type"]).upper() not in _LIVE_ASSET_TYPES
        )
        if forbidden:
            # FOREX hiện chỉ dùng cho backfill, không chạy live.
            raise ValueError(f"FOREX is historical-only: {', '.join(forbidden)}")
    else:
        selected_symbols, selected_timeframes = set(active), set(timeframes_by_code)
    requested_symbol = (symbol_filter or "").strip().upper()
    requested_timeframe = (timeframe_filter or "").strip().upper()
    symbols = [
        symbol
        for name, symbol in active.items()
        if name in selected_symbols
        and (
            not requested_symbol
            or requested_symbol
            in {name, f"{symbol['exchange']}:{symbol['symbol']}".upper()}
        )
    ]
    timeframes = [
        timeframe
        for code, timeframe in timeframes_by_code.items()
        if code in selected_timeframes and (not requested_timeframe or code == requested_timeframe)
    ]
    if requested_symbol and not symbols:
        raise ValueError(f"unknown or disabled symbol: {symbol_filter}")
    if requested_timeframe and not timeframes:
        raise ValueError(f"unknown timeframe: {timeframe_filter}")
    pairs = [(symbol, timeframe) for symbol in symbols for timeframe in timeframes]
    if not pairs:
        raise ValueError("workflow selection resolved to zero pairs")
    return pairs


def pair_key(pair: Pair) -> str:
    """Return the stable, secret-free runtime key for one pair."""
    # Key này dùng trong log/state.
    # Không chứa secret.
    symbol, timeframe = pair
    return f"{symbol['exchange']}:{symbol['symbol']}/{timeframe['code']}"

def read_chart_rows(config: dict[str, Any], symbol: str, timeframe: str, bars: int) -> list[tuple[Any, ...]]:
    """Read recent committed Fact candles for the offline operator chart."""
    # Chart offline chỉ đọc dữ liệu đã ghi trong SQL.
    # Không gọi TradingView và không ghi gì.
    fact_table = _quoted_name(config["tables"]["fact_table"])
    connection = get_connection(config)
    try:
        cursor = connection.cursor()
        cursor.execute(
            f"""SELECT TOP (?) f.BarTime,f.[Open],f.High,f.Low,f.[Close],f.Volume
            FROM {fact_table} f JOIN DWH.Dim_Symbol s ON s.SymbolID=f.SymbolID JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID=f.TimeframeID
            WHERE s.Symbol=? AND tf.Code=? ORDER BY f.BarTime DESC""", int(bars), symbol, timeframe)
        return list(cursor.fetchall())
    finally:
        connection.close()
def check_connection(config: dict[str, Any]) -> dict[str, Any]:
    """Return read-only schema and stored-procedure contract evidence."""
    # Doctor/check-sql dùng hàm này để kiểm SQL có đúng quy ước không.
    fact_table = _quoted_name(config["tables"]["fact_table"])
    procedure = config["tables"]["load_procedure"]
    connection = get_connection(config)
    try:
        cursor = connection.cursor()
        cursor.execute(f"SELECT COUNT_BIG(*), MAX(BarTime) FROM {fact_table}"); fact_row = cursor.fetchone()
        fact_rows, fact_watermark = int(fact_row[0]), fact_row[1]
        cursor.execute("""SELECT CAST(value AS NVARCHAR(50)) FROM sys.extended_properties
            WHERE major_id=OBJECT_ID(?) AND minor_id=0 AND class=1 AND name='DPContractVersion'""", procedure)
        row = cursor.fetchone()
        version = str(row[0]) if row and row[0] is not None else None
        threshold = warehouse_timestamp(
            datetime.now(timezone.utc) - timedelta(days=int(config["backfill"]["lookback_days"])))
        # Đếm cặp nào chưa có dữ liệu chạm tới mốc lookback.
        # Số này cho biết còn thiếu dữ liệu nền hay không.
        cursor.execute(f"""SELECT COUNT(*) FROM DWH.Dim_Symbol s CROSS JOIN DWH.Dim_Timeframe tf
            WHERE s.IsActive=1 AND NOT EXISTS (SELECT 1 FROM {fact_table} f
            WHERE f.SymbolID=s.SymbolID AND f.TimeframeID=tf.TimeframeID AND f.BarTime<=?)""", threshold)
        bootstrap_remaining_pairs = int(cursor.fetchone()[0])
        expected = str(config["sql_server"]["contract_version"])
        return {
            "ok": version == expected,
            "database": config["sql_server"]["database"], "fact_rows": fact_rows,
            "fact_watermark_utc": fact_watermark,
            "bootstrap_remaining_pairs": bootstrap_remaining_pairs,
            "procedure": procedure, "contract_version": version,
            "expected_contract_version": expected,
        }
    finally:
        connection.close()
def get_pair_states(
    config: dict[str, Any],
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[tuple[int, str], dict[str, Any]]:
    """Batch-read earliest and latest Fact watermarks for each pair."""
    # Đọc mốc nến cũ nhất và mới nhất cho nhiều cặp cùng lúc.
    # Backfill dùng mốc cũ nhất; live dùng mốc mới nhất.
    if not pairs:
        return {}
    fact_table = _quoted_name(config["tables"]["fact_table"])
    requested = sorted({(int(s["symbol_id"]), str(tf["code"])) for s, tf in pairs})
    connection = get_connection(config)
    try:
        cursor = connection.cursor()
        cursor.execute("CREATE TABLE #Pairs (SymbolID INT NOT NULL,TFCode VARCHAR(5) COLLATE DATABASE_DEFAULT NOT NULL,"
                       "PRIMARY KEY (SymbolID,TFCode))")
        cursor.fast_executemany = True
        cursor.executemany("INSERT INTO #Pairs (SymbolID, TFCode) VALUES (?, ?)", requested)
        cursor.execute(f"""SELECT p.SymbolID,p.TFCode,earliest.BarTime,latest.BarTime
            FROM #Pairs p JOIN DWH.Dim_Timeframe tf ON tf.Code=p.TFCode
            OUTER APPLY (SELECT TOP (1) f.BarTime FROM {fact_table} f WHERE f.SymbolID=p.SymbolID
            AND f.TimeframeID=tf.TimeframeID ORDER BY f.BarTime ASC) earliest
            OUTER APPLY (SELECT TOP (1) f.BarTime FROM {fact_table} f WHERE f.SymbolID=p.SymbolID
            AND f.TimeframeID=tf.TimeframeID ORDER BY f.BarTime DESC) latest""")
        return {(int(row[0]), str(row[1])): {
            "earliest": row[2], "latest": row[3],
        } for row in cursor.fetchall()}
    finally:
        connection.close()
def fetch_existing_candles(config: dict[str, Any], symbol_id: int, timeframe_code: str,
                           start_time: datetime, end_time: datetime, connection: pyodbc.Connection | None = None) -> dict[datetime, tuple[str | None, ...]]:
    """Read committed Fact values in an inclusive provider-observed window."""
    # Pipeline dùng hàm này để biết SQL đang có nến nào trong cùng cửa sổ.
    fact_table = _quoted_name(config["tables"]["fact_table"])
    active = connection or get_connection(config)
    try:
        cursor = active.cursor()
        cursor.execute(f"""SELECT f.BarTime,f.[Open],f.High,f.Low,f.[Close],f.Volume
            FROM {fact_table} f JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID=f.TimeframeID
            WHERE f.SymbolID=? AND tf.Code=? AND f.BarTime>=? AND f.BarTime<=?""",
            int(symbol_id), timeframe_code, warehouse_timestamp(start_time), warehouse_timestamp(end_time))
        return {
            row[0]: warehouse_value_signature(row[1], row[2], row[3], row[4], row[5])
            for row in cursor.fetchall()
        }
    finally:
        if connection is None: active.close()
def read_latest_candles(
    config: dict[str, Any], symbol_id: int, timeframe_code: str, limit: int,
) -> list[tuple[Any, ...]]:
    """Read the most recent N committed Fact candles for one pair, oldest first."""
    # Redis publisher dùng hàm này để lấy cửa sổ nến mới nhất cho một pair.
    fact_table = _quoted_name(config["tables"]["fact_table"])
    connection = get_connection(config)
    try:
        cursor = connection.cursor()
        cursor.execute(f"""SELECT TOP (?) f.BarTime,f.[Open],f.High,f.Low,f.[Close],f.Volume
            FROM {fact_table} f JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID=f.TimeframeID
            WHERE f.SymbolID=? AND tf.Code=? ORDER BY f.BarTime DESC""",
            int(limit), int(symbol_id), timeframe_code)
        return list(reversed(cursor.fetchall()))
    finally:
        connection.close()
def _require_contract(cursor: pyodbc.Cursor, procedure: str, expected: str) -> None:
    # Trước khi ghi SQL, kiểm stored procedure đúng version.
    cursor.execute("""SELECT CAST(value AS NVARCHAR(50)) FROM sys.extended_properties
        WHERE major_id=OBJECT_ID(?) AND minor_id=0 AND class=1 AND name='DPContractVersion'""",
        procedure)
    row = cursor.fetchone()
    actual = str(row[0]) if row and row[0] is not None else None
    if actual != expected:
        raise RuntimeError(
            f"{procedure} contract mismatch: expected {expected}, found {actual or 'missing'}"
        )
def _fetch_result_row(cursor: pyodbc.Cursor, operation: str) -> Any:
    """Return the first query row after any preceding DML row-count sets."""
    # Một lệnh SQL có thể trả nhiều result set.
    # Hàm này đi tới dòng kết quả thật cần đọc.
    while True:
        if cursor.description is not None:
            row = cursor.fetchone()
            if row is not None:
                return row
        if not cursor.nextset():
            raise RuntimeError(f"{operation} did not return row counts")
def bulk_upsert_candles(
    config: dict[str, Any], timeframe: dict[str, Any],
    candles: list[dict[str, Any]], *,
    symbol_id: int | None = None, connection: pyodbc.Connection | None = None,
) -> dict[str, int]:
    """Load one complete provider window into staging and Fact."""
    # Hàm ghi SQL chính.
    # Nến đã kiểm xong được đưa vào staging, rồi loader đẩy sang Fact.
    # Có lỗi ở bước nào thì rollback.
    empty = {"input": 0, "staged_inserted": 0, "staged_updated": 0,
             "fact_inserted": 0, "fact_updated": 0, "affected": 0, "skipped": 0}
    symbol_ids = {int(candle["symbol_id"]) for candle in candles}
    if len(symbol_ids) > 1:
        # Một lần ghi chỉ nhận một symbol.
        raise ValueError("bulk_upsert_candles accepts one symbol per batch")
    if symbol_ids:
        resolved_symbol_id = next(iter(symbol_ids))
        if symbol_id is not None and int(symbol_id) != resolved_symbol_id:
            raise ValueError("symbol_id does not match candle batch")
    elif symbol_id is not None:
        resolved_symbol_id = int(symbol_id)
    else:
        return empty
    rows = prepare_warehouse_rows(candles)
    staging_name = timeframe["staging_table"]
    staging_table = _quoted_name(staging_name)
    fact_table = _quoted_name(config["tables"]["fact_table"])
    procedure_name = config["tables"]["load_procedure"]
    procedure = _quoted_name(procedure_name)
    active = connection or get_connection(config)
    try:
        cursor = active.cursor()
        _require_contract(cursor, procedure_name, str(config["sql_server"]["contract_version"]))
        staged_inserted = staged_updated = fact_inserted = fact_updated = affected = 0
        if rows:
            # Bảng tạm giữ toàn bộ nến cần ghi trong lần này.
            cursor.execute("""IF OBJECT_ID('tempdb..#V3Candles') IS NOT NULL DROP TABLE #V3Candles;
                CREATE TABLE #V3Candles (SymbolID INT NOT NULL,BarTime DATETIME2(0) NOT NULL,[Open] DECIMAL(18,8) NOT NULL,High DECIMAL(18,8) NOT NULL,Low DECIMAL(18,8) NOT NULL,
                [Close] DECIMAL(18,8) NOT NULL,Volume DECIMAL(20,4) NULL,PRIMARY KEY(SymbolID,BarTime))""")
            cursor.fast_executemany = True
            insert = """INSERT INTO #V3Candles(SymbolID,BarTime,[Open],High,Low,[Close],Volume)
                VALUES(?,?,?,?,?,?,?)"""
            size = int(config["sql_server"]["batch_size"])
            for offset in range(0, len(rows), size):
                cursor.executemany(insert, rows[offset : offset + size])
            # MERGE staging: thêm nến mới, cập nhật nến đổi giá trị.
            cursor.execute(f"""
                DECLARE @Actions TABLE (ActionName NVARCHAR(10));
                MERGE {staging_table} WITH (HOLDLOCK) AS target
                USING #V3Candles AS source
                  ON target.SymbolID=source.SymbolID AND target.BarTime=source.BarTime
                WHEN NOT MATCHED THEN INSERT
                  (SymbolID, BarTime, [Open], High, Low, [Close], Volume, IsProcessed)
                  VALUES (source.SymbolID, source.BarTime, source.[Open], source.High,
                          source.Low, source.[Close], source.Volume, 1)
                WHEN MATCHED AND (
                  target.[Open]<>source.[Open] OR target.High<>source.High
                  OR target.Low<>source.Low OR target.[Close]<>source.[Close]
                  OR ISNULL(target.Volume,-1)<>ISNULL(source.Volume,-1)
                  OR target.IsProcessed<>1)
                THEN UPDATE SET [Open]=source.[Open], High=source.High, Low=source.Low,
                  [Close]=source.[Close], Volume=source.Volume, IsProcessed=1
                OUTPUT $action INTO @Actions;
                SELECT SUM(CASE WHEN ActionName='INSERT' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN ActionName='UPDATE' THEN 1 ELSE 0 END) FROM @Actions;""")
            stage_row = _fetch_result_row(cursor, "staging merge")
            staged_inserted, staged_updated = (int(value or 0) for value in stage_row)
            from_time = min(row[1] for row in rows)
            # Loader đẩy dữ liệu từ staging sang Fact.
            cursor.execute(f"EXEC {procedure} ?,?,?,?", resolved_symbol_id,
                           timeframe["code"], staging_name, from_time)
            fact_row = _fetch_result_row(cursor, procedure_name)
            fact_updated, fact_inserted, affected = (
                max(0, int(value or 0)) for value in fact_row)
            # Sau khi loader chạy, kiểm lại Fact đã có đủ nến đúng giá trị.
            cursor.execute(f"""IF EXISTS(SELECT 1 FROM #V3Candles source
                JOIN DWH.Dim_Timeframe tf ON tf.Code=? LEFT JOIN {fact_table} fact
                ON fact.SymbolID=source.SymbolID AND fact.TimeframeID=tf.TimeframeID
                AND fact.BarTime=source.BarTime WHERE fact.SymbolID IS NULL
                OR fact.[Open]<>source.[Open] OR fact.High<>source.High OR fact.Low<>source.Low
                OR fact.[Close]<>source.[Close] OR ISNULL(fact.Volume,-1)<>ISNULL(source.Volume,-1))
                THROW 51021,'Fact verification failed for provider batch.',1;""", timeframe["code"])
        active.commit()
        return {"input": len(rows), "staged_inserted": staged_inserted, "staged_updated": staged_updated,
                "fact_inserted": fact_inserted, "fact_updated": fact_updated, "affected": affected,
                "skipped": max(0, len(rows) - affected)}
    except Exception:
        active.rollback()
        raise
    finally:
        if connection is None: active.close()
