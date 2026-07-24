"""Read-only health queries for the tick data check viewer."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


def _get_connection():
    from tick_engine.data_storage.db_connector import get_connection
    return get_connection()


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    return value


def _json_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _json_value(v) for k, v in row.items()}


_TICK_TABLES = {
    2: "FR40",
    3: "DE40",
    4: "HK50",
    5: "J225",
    6: "SP35",
    7: "UK100",
    8: "US500",
    9: "US100",
    10: "US30",
    56: "GOLD",
    81: "BTCUSD",
}


def _tick_table_stats_cte() -> str:
    parts: list[str] = []
    for symbol_id, symbol in _TICK_TABLES.items():
        parts.append(
            f"""
            SELECT
                {symbol_id} AS SymbolID,
                '{symbol}' AS SenSymbol,
                CAST(COALESCE(ps.RowsTotal, 0) AS BIGINT) AS TotalTicksInserted,
                last_tick.TickTimeUtc AS LastHistoricalTickTimeUtc,
                last_tick.ReceivedAtUtc AS LastWriteAtUtc,
                last_tick.Bid AS LastBid,
                last_tick.Ask AS LastAsk
            FROM (SELECT 1 AS x) seed
            OUTER APPLY (
                SELECT COUNT_BIG(*) AS RowsTotal
                FROM tick.[{symbol}] WITH (NOLOCK)
                WHERE Bid IS NOT NULL AND Ask IS NOT NULL
            ) ps
            OUTER APPLY (
                SELECT TOP (1) TickTimeUtc, ReceivedAtUtc, Bid, Ask
                FROM tick.[{symbol}] WITH (NOLOCK)
                WHERE Bid IS NOT NULL AND Ask IS NOT NULL
                ORDER BY TickTimeUtc DESC, TickID DESC
            ) last_tick
            """
        )
    return "\nUNION ALL\n".join(parts)


def _read_sql(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
        cursor.close()
        return [dict(zip(columns, row)) for row in rows]
    finally:
        conn.close()


def load_summary() -> dict[str, Any]:
    rows = _read_sql(
        f"""
        WITH table_stats AS (
            {_tick_table_stats_cte()}
        )
        SELECT
            SYSUTCDATETIME()                                                 AS GeneratedAtUtc,
            COUNT(m.SymbolID)                                                AS TotalSymbols,
            COALESCE(SUM(table_stats.TotalTicksInserted), 0)                 AS TotalTicksInserted,
            SUM(CASE WHEN table_stats.LastHistoricalTickTimeUtc IS NOT NULL THEN 1 ELSE 0 END)
                                                                            AS ActiveSymbols,
            MAX(table_stats.LastHistoricalTickTimeUtc)                       AS LatestHistoricalTickUtc,
            MAX(table_stats.LastWriteAtUtc)                                  AS LatestWriteAtUtc,
            DATEDIFF(second, MAX(table_stats.LastHistoricalTickTimeUtc), SYSUTCDATETIME())
                                                                            AS LagSeconds
        FROM tick.SymbolMap m WITH (NOLOCK)
        LEFT JOIN table_stats ON table_stats.SymbolID = m.SymbolID
        WHERE m.Enabled = 1
        """
    )
    running = _read_sql(
        """
        SELECT COUNT(*) AS RunningCount
        FROM tick.IngestRun WITH (NOLOCK)
        WHERE Status = 'RUNNING'
        """
    )
    result = _json_row(rows[0] if rows else {})
    result.update(_json_row(running[0] if running else {}))
    return result


def load_symbol_health() -> dict[str, Any]:
    rows = _read_sql(
        f"""
        WITH table_stats AS (
            {_tick_table_stats_cte()}
        )
        SELECT
            h.SymbolID,
            h.SenSymbol,
            h.AssetType,
            h.MappingStatus,
            h.Status,
            h.LastLiveTickTimeUtc,
            table_stats.LastHistoricalTickTimeUtc,
            table_stats.LastBid,
            table_stats.LastAsk,
            CAST(
                CASE WHEN table_stats.LastBid IS NOT NULL AND table_stats.LastAsk IS NOT NULL
                THEN table_stats.LastAsk - table_stats.LastBid ELSE NULL END
            AS DECIMAL(19,8))                                               AS Spread,
            COALESCE(table_stats.TotalTicksInserted, 0)                      AS TotalTicksInserted,
            h.ConsecutiveErrors,
            h.LastError,
            table_stats.LastWriteAtUtc,
            DATEDIFF(second, table_stats.LastHistoricalTickTimeUtc, SYSUTCDATETIME())
                                                                            AS LagSeconds
        FROM tick.v_IngestHealth h WITH (NOLOCK)
        LEFT JOIN table_stats ON table_stats.SymbolID = h.SymbolID
        ORDER BY h.SenSymbol
        """
    )
    return {"symbols": [_json_row(r) for r in rows]}


def load_ingest_runs(limit: int = 25) -> dict[str, Any]:
    rows = _read_sql(
        """
        SELECT TOP (?)
            CAST(IngestRunID AS NVARCHAR(36)) AS IngestRunID,
            AppName,
            Environment,
            StartedAtUtc,
            StoppedAtUtc,
            Status,
            StopReason,
            COALESCE(RowsInserted, 0)   AS RowsInserted,
            COALESCE(RowsSpooled, 0)    AS RowsSpooled,
            HostName,
            ProcessID,
            DATEDIFF(second, StartedAtUtc, COALESCE(StoppedAtUtc, SYSUTCDATETIME())) AS DurationSeconds
        FROM tick.IngestRun WITH (NOLOCK)
        ORDER BY StartedAtUtc DESC
        """,
        (limit,),
    )
    return {"runs": [_json_row(r) for r in rows]}


def load_locks() -> dict[str, Any]:
    rows = _read_sql(
        """
        SELECT
            TaskName,
            StartedAt,
            ExpiresAt,
            Payload,
            CASE WHEN ExpiresAt > SYSUTCDATETIME() THEN 1 ELSE 0 END AS IsActive,
            DATEDIFF(second, SYSUTCDATETIME(), ExpiresAt)             AS SecondsToExpiry
        FROM SEN.ActiveTask WITH (NOLOCK)
        ORDER BY
            CASE WHEN ExpiresAt > SYSUTCDATETIME() THEN 0 ELSE 1 END,
            ExpiresAt DESC
        """
    )
    return {"items": [_json_row(r) for r in rows]}


def load_spool() -> dict[str, Any]:
    import sqlite3

    try:
        from tick_engine.settings import WS_OVERFLOW_SPOOL
        path = WS_OVERFLOW_SPOOL
        if not path.exists():
            return {"count": 0, "path": str(path), "exists": False}
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3) as conn:
            cur = conn.execute("SELECT COUNT(*) FROM tick_spool")
            count = cur.fetchone()[0]
        return {"count": count, "path": str(path), "exists": True}
    except Exception as exc:
        return {"count": None, "error": str(exc), "exists": False}
