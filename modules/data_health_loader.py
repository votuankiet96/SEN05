# =============================================================================
# modules/data_health_loader.py - Read-only data health queries
# =============================================================================
# This module is intentionally read-only. It is used by data_provider/03_chart.py
# to show database freshness, coverage, runtime locks, and staging backlog.

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from config import TF_DISPLAY_ORDER, TF_MINUTES, TF_STAGING

from .db_connector import get_connection


def _read_sql(query: str, conn, params=None) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    if params:
        cursor.execute(query, params)
    else:
        cursor.execute(query)
    columns = [col[0] for col in cursor.description]
    rows = cursor.fetchall()
    cursor.close()
    return [dict(zip(columns, row)) for row in rows]


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    return value


def _json_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _json_value(value) for key, value in row.items()}


def _to_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    return int(value)


def _freshness_thresholds(tf_code: str) -> tuple[int, int]:
    minutes = TF_MINUTES.get(tf_code, 60)
    if tf_code == "D1":
        return 3 * 1440, 7 * 1440
    if tf_code == "W":
        return 21 * 1440, 45 * 1440
    return max(minutes * 6, 120), max(minutes * 18, 360)


def _freshness_state(tf_code: str, row_count: int, lag_minutes: int | None) -> str:
    if row_count <= 0:
        return "empty"
    if lag_minutes is None:
        return "unknown"
    if lag_minutes <= 0:
        return "ok"
    warn_after, stale_after = _freshness_thresholds(tf_code)
    if lag_minutes > stale_after:
        return "stale"
    if lag_minutes > warn_after:
        return "warn"
    return "ok"


def load_health_summary() -> dict[str, Any]:
    conn = get_connection()
    try:
        rows = _read_sql(
            """
            SELECT
                SYSUTCDATETIME() AS GeneratedAtUtc,
                COUNT_BIG(*) AS TotalRows,
                COUNT(DISTINCT SymbolID) AS SymbolsWithData,
                COUNT(DISTINCT TimeframeID) AS TimeframesWithData,
                MIN(BarTime) AS OldestBarTime,
                MAX(BarTime) AS LatestBarTime,
                MIN(CreatedAt) AS OldestCreatedAt,
                MAX(CreatedAt) AS LatestCreatedAt,
                SUM(CASE WHEN CreatedAt >= DATEADD(hour, -24, SYSUTCDATETIME())
                         THEN 1 ELSE 0 END) AS RowsCreatedLast24h,
                SUM(CASE WHEN CAST(CreatedAt AS date) = CAST(SYSUTCDATETIME() AS date)
                         THEN 1 ELSE 0 END) AS RowsCreatedTodayUtc
            FROM DWH.Fact_OHLCV WITH (NOLOCK)
            """,
            conn,
        )
        dims = _read_sql(
            """
            SELECT
                (SELECT COUNT(*) FROM DWH.Dim_Symbol WITH (NOLOCK)) AS TotalSymbols,
                (SELECT COUNT(*) FROM DWH.Dim_Timeframe WITH (NOLOCK)) AS TotalTimeframes
            """,
            conn,
        )[0]
    finally:
        conn.close()

    summary = _json_row(rows[0] if rows else {})
    summary.update(_json_row(dims))
    summary["ExpectedPairs"] = _to_int(dims.get("TotalSymbols")) * _to_int(dims.get("TotalTimeframes"))
    return summary


def load_health_matrix() -> dict[str, Any]:
    conn = get_connection()
    try:
        rows = _read_sql(
            """
            WITH pairs AS (
                SELECT
                    s.SymbolID,
                    s.Symbol,
                    s.AssetType,
                    tf.TimeframeID,
                    tf.Code AS TFCode,
                    tf.Minutes
                FROM DWH.Dim_Symbol s WITH (NOLOCK)
                CROSS JOIN DWH.Dim_Timeframe tf WITH (NOLOCK)
            ),
            agg AS (
                SELECT
                    SymbolID,
                    TimeframeID,
                    COUNT_BIG(*) AS [RowCount],
                    MIN(BarTime) AS OldestBarTime,
                    MAX(BarTime) AS LatestBarTime,
                    MAX(CreatedAt) AS LatestCreatedAt
                FROM DWH.Fact_OHLCV WITH (NOLOCK)
                GROUP BY SymbolID, TimeframeID
            )
            SELECT
                p.SymbolID,
                p.Symbol,
                p.AssetType,
                p.TimeframeID,
                p.TFCode,
                p.Minutes,
                ISNULL(a.[RowCount], 0) AS [RowCount],
                a.OldestBarTime,
                a.LatestBarTime,
                a.LatestCreatedAt,
                CASE
                    WHEN a.LatestBarTime IS NULL THEN NULL
                    ELSE DATEDIFF(minute, a.LatestBarTime, SYSUTCDATETIME())
                END AS LatestLagMinutes
            FROM pairs p
            LEFT JOIN agg a
              ON a.SymbolID = p.SymbolID
             AND a.TimeframeID = p.TimeframeID
            ORDER BY p.Symbol, p.TimeframeID
            """,
            conn,
        )
    finally:
        conn.close()

    symbols: dict[str, dict[str, Any]] = {}
    totals = {"ok": 0, "warn": 0, "stale": 0, "empty": 0, "unknown": 0}

    for raw in rows:
        tf_code = str(raw["TFCode"])
        row_count = _to_int(raw.get("RowCount"))
        lag = raw.get("LatestLagMinutes")
        lag_minutes = int(lag) if lag is not None else None
        state = _freshness_state(tf_code, row_count, lag_minutes)
        totals[state] = totals.get(state, 0) + 1

        symbol = str(raw["Symbol"])
        if symbol not in symbols:
            symbols[symbol] = {
                "symbol_id": raw["SymbolID"],
                "symbol": symbol,
                "asset_type": raw.get("AssetType"),
                "cells": {},
            }

        cell = _json_row(raw)
        cell["state"] = state
        cell["warnAfterMinutes"], cell["staleAfterMinutes"] = _freshness_thresholds(tf_code)
        symbols[symbol]["cells"][tf_code] = cell

    ordered_symbols = [symbols[name] for name in sorted(symbols)]
    return {
        "timeframes": [tf for tf in TF_DISPLAY_ORDER],
        "symbols": ordered_symbols,
        "totals": totals,
    }


def _validate_staging_table(table_name: str) -> None:
    parts = table_name.split(".")
    if len(parts) != 2 or parts[0] != "SEN" or not parts[1].startswith("TF_"):
        raise ValueError(f"Unsafe staging table name: {table_name!r}")
    for part in parts:
        if not part.replace("_", "").isalnum():
            raise ValueError(f"Unsafe staging table name: {table_name!r}")


def load_staging_health() -> dict[str, Any]:
    conn = get_connection()
    items = []
    try:
        for tf_code in TF_DISPLAY_ORDER:
            table_name = TF_STAGING.get(tf_code)
            if not table_name:
                continue
            _validate_staging_table(table_name)
            try:
                row = _read_sql(
                    f"""
                    SELECT
                        ? AS TFCode,
                        ? AS StagingTable,
                        COUNT_BIG(*) AS TotalRows,
                        COALESCE(SUM(CASE WHEN fk.BarTime IS NULL THEN 1 ELSE 0 END), 0) AS NotLoadedRows,
                        COALESCE(SUM(CASE WHEN fk.BarTime IS NOT NULL THEN 1 ELSE 0 END), 0) AS LoadedRows,
                        MIN(s.BarTime) AS OldestBarTime,
                        MAX(s.BarTime) AS LatestBarTime,
                        MAX(s.ReceivedAt) AS LatestReceivedAt
                    FROM {table_name} s WITH (NOLOCK)
                    LEFT JOIN (
                        SELECT f.SymbolID, f.BarTime
                        FROM DWH.Fact_OHLCV f WITH (NOLOCK)
                        WHERE f.TimeframeID = (
                            SELECT TimeframeID
                            FROM DWH.Dim_Timeframe WITH (NOLOCK)
                            WHERE Code = ?
                        )
                    ) fk
                      ON fk.SymbolID = s.SymbolID
                     AND fk.BarTime = s.BarTime
                    """,
                    conn,
                    (tf_code, table_name, tf_code),
                )[0]
                item = _json_row(row)
                item["PendingRows"] = item.get("NotLoadedRows")
                item["ProcessedRows"] = item.get("LoadedRows")
                item["error"] = None
            except Exception as exc:
                item = {
                    "TFCode": tf_code,
                    "StagingTable": table_name,
                    "TotalRows": None,
                    "LoadedRows": None,
                    "NotLoadedRows": None,
                    "PendingRows": None,
                    "ProcessedRows": None,
                    "OldestBarTime": None,
                    "LatestBarTime": None,
                    "LatestReceivedAt": None,
                    "error": str(exc),
                }
            items.append(item)
    finally:
        conn.close()
    return {"items": items}


def load_active_locks() -> dict[str, Any]:
    conn = get_connection()
    try:
        rows = _read_sql(
            """
            SELECT
                TaskName,
                StartedAt,
                ExpiresAt,
                Payload,
                CASE WHEN ExpiresAt > SYSUTCDATETIME() THEN 1 ELSE 0 END AS IsActive,
                DATEDIFF(second, SYSUTCDATETIME(), ExpiresAt) AS SecondsToExpiry
            FROM SEN.ActiveTask WITH (NOLOCK)
            ORDER BY
                CASE WHEN ExpiresAt > SYSUTCDATETIME() THEN 0 ELSE 1 END,
                ExpiresAt DESC
            """,
            conn,
        )
    finally:
        conn.close()
    return {"items": [_json_row(row) for row in rows]}
