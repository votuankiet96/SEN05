"""Read-only health checks for the isolated cTrader tick provider."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .runtime import TickRuntimeSettings
from .runtime_lock import TICK_LIVE_RUNTIME_LOCK, stale_pid_status
from .spool import TickSpool
from .store_sql import TickSqlStore, qualified_tick_table, quote_ident


@dataclass(frozen=True)
class TickCheckFinding:
    severity: str
    code: str
    message: str


@dataclass(frozen=True)
class TickCheckReport:
    status: str
    generated_at_utc: str
    findings: list[TickCheckFinding]
    data: dict[str, Any]

    def to_text(self) -> str:
        lines = [
            f"status={self.status}",
            f"generated_at_utc={self.generated_at_utc}",
        ]
        for finding in self.findings:
            lines.append(f"[{finding.severity}] {finding.code}: {finding.message}")
        if not self.findings:
            lines.append("[OK] tick_data: no findings")
        spool = self.data.get("spool", {})
        pid = self.data.get("pid", {})
        lines.append(f"spool_count={spool.get('count')} spool_path={spool.get('path')}")
        lines.append(f"pid_file={pid.get('path')} pid={pid.get('pid')} alive={pid.get('alive')}")
        return "\n".join(lines)


def _row_get(row: Any, name: str, index: int) -> Any:
    if hasattr(row, name):
        return getattr(row, name)
    return row[index]


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _seconds_since(value: Any, now: datetime) -> int | None:
    dt = _as_utc(value)
    if dt is None:
        return None
    return max(0, int((now - dt).total_seconds()))


def _table_stats(store: TickSqlStore, symbol: str) -> dict[str, Any]:
    table = qualified_tick_table(store.schema, symbol, store.allowed_symbols)
    conn = store.connection_factory()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            SELECT COALESCE(SUM(row_count), 0) AS RowsTotal
            FROM sys.dm_db_partition_stats
            WHERE object_id = OBJECT_ID(?)
              AND index_id IN (0, 1)
            """,
            (f"{store.schema}.{symbol}",),
        )
        row = cursor.fetchone()
        cursor.execute(
            f"""
            SELECT TOP 1 TickTimeUtc AS LatestTickTimeUtc
            FROM {table}
            ORDER BY TickTimeUtc DESC, TickID DESC
            """
        )
        latest = cursor.fetchone()
        cursor.execute(
            f"""
            SELECT TOP 1 1 AS HasNegativeSpread
            FROM {table}
            WHERE Bid IS NOT NULL AND Ask IS NOT NULL AND Ask < Bid
            """
        )
        neg = cursor.fetchone()
        return {
            "rows_total": int(_row_get(row, "RowsTotal", 0) or 0) if row else 0,
            "latest_tick_time_utc": str(_row_get(latest, "LatestTickTimeUtc", 0)) if latest else None,
            "has_negative_spread": bool(neg),
        }
    finally:
        conn.close()


def run_tick_check(
    settings: TickRuntimeSettings,
    store: TickSqlStore,
    stale_seconds: int | None = None,
) -> TickCheckReport:
    """Run read-only checks and return a structured operator report."""
    now = datetime.now(timezone.utc)
    findings: list[TickCheckFinding] = []
    data: dict[str, Any] = {
        "settings": {
            "env": settings.env,
            "schema": settings.schema,
            "symbols": [symbol.local_symbol for symbol in settings.symbols],
            "missing_api_fields": list(settings.missing_api_fields),
        }
    }

    if settings.missing_api_fields:
        findings.append(
            TickCheckFinding(
                "ERROR",
                "missing_api_fields",
                "Missing cTrader fields: " + ",".join(settings.missing_api_fields),
            )
        )

    spool = TickSpool(settings.spool_path)
    spool_count = spool.count()
    data["spool"] = {"count": spool_count, "path": str(settings.spool_path)}
    if spool_count > 0:
        findings.append(
            TickCheckFinding("WARNING", "spool_backlog", f"{spool_count} tick rows are waiting in SQLite spool")
        )

    pid_status = stale_pid_status()
    data["pid"] = pid_status
    if pid_status["exists"] and not pid_status["alive"]:
        findings.append(TickCheckFinding("WARNING", "stale_pid_file", f"Stale pid file: {pid_status['path']}"))

    conn = store.connection_factory()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            SELECT SenSymbol, MappingStatus, Enabled, CTraderSymbolId
            FROM {quote_ident(store.schema)}.[SymbolMap]
            ORDER BY SenSymbol
            """
        )
        symbol_rows = cursor.fetchall()
        data["symbol_map"] = [
            {
                "symbol": str(_row_get(row, "SenSymbol", 0)),
                "mapping_status": str(_row_get(row, "MappingStatus", 1)),
                "enabled": bool(_row_get(row, "Enabled", 2)),
                "ctrader_symbol_id": _row_get(row, "CTraderSymbolId", 3),
            }
            for row in symbol_rows
        ]

        for row in symbol_rows:
            symbol = str(_row_get(row, "SenSymbol", 0)).upper()
            mapping_status = str(_row_get(row, "MappingStatus", 1))
            enabled = bool(_row_get(row, "Enabled", 2))
            if enabled and mapping_status != "MATCHED":
                findings.append(
                    TickCheckFinding("ERROR", "symbol_not_matched", f"{symbol} mapping_status={mapping_status}")
                )

        cursor.execute(
            f"""
            SELECT SenSymbol, MappingStatus, Status, LastLiveTickTimeUtc,
                   LastHistoricalTickTimeUtc, LastHeartbeatAtUtc, TotalTicksInserted, LastError
            FROM {quote_ident(store.schema)}.[v_IngestHealth]
            ORDER BY SenSymbol
            """
        )
        health_rows = cursor.fetchall()
        health: list[dict[str, Any]] = []
        for row in health_rows:
            symbol = str(_row_get(row, "SenSymbol", 0)).upper()
            heartbeat_age = _seconds_since(_row_get(row, "LastHeartbeatAtUtc", 5), now)
            threshold = stale_seconds
            if threshold is None:
                threshold = (
                    settings.stale_seconds_btc
                    if symbol == "BTCUSD"
                    else settings.stale_seconds_market
                )
            health_item = {
                "symbol": symbol,
                "mapping_status": str(_row_get(row, "MappingStatus", 1)),
                "status": str(_row_get(row, "Status", 2)) if _row_get(row, "Status", 2) is not None else None,
                "last_live_tick_time_utc": str(_row_get(row, "LastLiveTickTimeUtc", 3)),
                "last_historical_tick_time_utc": str(_row_get(row, "LastHistoricalTickTimeUtc", 4)),
                "last_heartbeat_at_utc": str(_row_get(row, "LastHeartbeatAtUtc", 5)),
                "heartbeat_age_seconds": heartbeat_age,
                "total_ticks_inserted": int(_row_get(row, "TotalTicksInserted", 6) or 0),
                "last_error": str(_row_get(row, "LastError", 7) or ""),
                "stale_threshold_seconds": int(threshold),
            }
            health.append(health_item)
            if health_item["mapping_status"] == "MATCHED" and heartbeat_age is None:
                findings.append(TickCheckFinding("WARNING", "missing_heartbeat", f"{symbol} has no live heartbeat yet"))
            elif (
                health_item["mapping_status"] == "MATCHED"
                and heartbeat_age is not None
                and heartbeat_age > int(threshold)
            ):
                findings.append(
                    TickCheckFinding(
                        "WARNING",
                        "stale_heartbeat",
                        f"{symbol} heartbeat age {heartbeat_age}s exceeds {threshold}s",
                    )
                )
            if health_item["last_error"]:
                findings.append(
                    TickCheckFinding("WARNING", "symbol_last_error", f"{symbol}: {health_item['last_error'][:300]}")
                )
        data["ingest_health"] = health

        cursor.execute(
            f"""
            SELECT TOP 5 IngestRunID, AppName, StartedAtUtc, StoppedAtUtc, Status,
                   RowsInserted, RowsSpooled, StopReason, HostName, ProcessID
            FROM {quote_ident(store.schema)}.[IngestRun]
            ORDER BY StartedAtUtc DESC
            """
        )
        data["recent_runs"] = [
            {
                "ingest_run_id": str(_row_get(row, "IngestRunID", 0)),
                "app_name": str(_row_get(row, "AppName", 1)),
                "started_at_utc": str(_row_get(row, "StartedAtUtc", 2)),
                "stopped_at_utc": str(_row_get(row, "StoppedAtUtc", 3)),
                "status": str(_row_get(row, "Status", 4)),
                "rows_inserted": int(_row_get(row, "RowsInserted", 5) or 0),
                "rows_spooled": int(_row_get(row, "RowsSpooled", 6) or 0),
                "stop_reason": str(_row_get(row, "StopReason", 7) or ""),
                "host_name": str(_row_get(row, "HostName", 8) or ""),
                "process_id": _row_get(row, "ProcessID", 9),
            }
            for row in cursor.fetchall()
        ]

        cursor.execute(
            """
            SELECT ExpiresAt, Payload
            FROM SEN.ActiveTask
            WHERE TaskName = ?
            """,
            (TICK_LIVE_RUNTIME_LOCK,),
        )
        lock_row = cursor.fetchone()
        data["runtime_lock"] = {
            "exists": lock_row is not None,
            "expires_at": str(_row_get(lock_row, "ExpiresAt", 0)) if lock_row else None,
            "payload": str(_row_get(lock_row, "Payload", 1)) if lock_row else None,
        }
    except Exception as exc:
        findings.append(TickCheckFinding("ERROR", "sql_check_failed", str(exc)[:800]))
    finally:
        conn.close()

    table_stats: dict[str, Any] = {}
    for target in settings.symbols:
        symbol = target.local_symbol.upper()
        try:
            stats = _table_stats(store, symbol)
            table_stats[symbol] = stats
            if stats["has_negative_spread"]:
                findings.append(
                    TickCheckFinding(
                        "WARNING",
                        "negative_spread",
                        f"{symbol} has at least one row with Ask < Bid",
                    )
                )
        except Exception as exc:
            table_stats[symbol] = {"error": str(exc)}
            findings.append(TickCheckFinding("ERROR", "tick_table_check_failed", f"{symbol}: {str(exc)[:500]}"))
    data["table_stats"] = table_stats

    severity_rank = {"ERROR": 2, "WARNING": 1, "INFO": 0}
    max_rank = max((severity_rank.get(item.severity, 0) for item in findings), default=0)
    status = "ERROR" if max_rank >= 2 else "WARNING" if max_rank == 1 else "OK"
    return TickCheckReport(
        status=status,
        generated_at_utc=now.isoformat(),
        findings=findings,
        data=data,
    )
