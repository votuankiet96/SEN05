"""SQL Server persistence for cTrader FTMO ticks."""

from __future__ import annotations

import os
import re
import socket
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timezone

from .models import RemoteSymbol, SymbolMatch, TargetSymbol, TickRecord

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

INSERT_COLUMNS = (
    "SymbolID",
    "CTraderSymbolId",
    "CTraderSymbolName",
    "TickTimeUtc",
    "SourceTimestampMs",
    "BidRaw",
    "AskRaw",
    "Bid",
    "Ask",
    "BidUpdated",
    "AskUpdated",
    "QuoteType",
    "SourceMode",
    "SessionCloseRaw",
    "SessionClose",
    "IsTechnicalEvent",
    "ReceivedAtUtc",
    "IngestRunID",
    "EventHash",
)


def quote_ident(identifier: str) -> str:
    if not IDENTIFIER_RE.match(identifier):
        raise ValueError(f"unsafe SQL identifier: {identifier!r}")
    return f"[{identifier}]"


def qualified_tick_table(
    schema: str,
    local_symbol: str,
    allowed_symbols: set[str] | None = None,
) -> str:
    symbol = local_symbol.upper()
    if allowed_symbols is not None and symbol not in allowed_symbols:
        raise ValueError(f"symbol is not in configured tick universe: {local_symbol!r}")
    return f"{quote_ident(schema)}.{quote_ident(symbol)}"


def _default_connection_factory():
    from modules.db_connector import get_connection

    return get_connection()


class TickSqlStore:
    """Write matched ticks into the per-symbol tick schema."""

    def __init__(
        self,
        schema: str,
        targets: Sequence[TargetSymbol],
        connection_factory: Callable[[], object] | None = None,
        environment: str = "demo",
        account_id: int | None = None,
    ) -> None:
        self.schema = schema
        self.targets = {target.local_symbol.upper(): target for target in targets}
        self.allowed_symbols = set(self.targets)
        self.connection_factory = connection_factory or _default_connection_factory
        self.environment = environment
        self.account_id = account_id

    def insert_ticks(self, records: Iterable[TickRecord]) -> int:
        grouped: dict[str, list[TickRecord]] = defaultdict(list)
        for record in records:
            grouped[record.local_symbol.upper()].append(record)
        if not grouped:
            return 0

        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            if hasattr(cursor, "fast_executemany"):
                cursor.fast_executemany = True

            total = 0
            columns = ", ".join(quote_ident(column) for column in INSERT_COLUMNS)
            placeholders = ", ".join("?" for _ in INSERT_COLUMNS)
            for symbol, rows in grouped.items():
                table_name = qualified_tick_table(
                    self.schema,
                    symbol,
                    allowed_symbols=self.allowed_symbols,
                )
                cursor.executemany(
                    f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})",
                    [row.to_db_params() for row in rows],
                )
                total += len(rows)
            conn.commit()
            return total
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def start_ingest_run(self, mode: str, note: str | None = None) -> str:
        conn = self.connection_factory()
        cursor = conn.cursor()
        ingest_run_id = str(uuid.uuid4())
        try:
            cursor.execute(
                f"""
                INSERT INTO {quote_ident(self.schema)}.[IngestRun]
                    (IngestRunID, AppName, Environment, CtidTraderAccountId,
                     StartedAtUtc, Status, StopReason, HostName, ProcessID)
                VALUES (?, ?, ?, ?, SYSUTCDATETIME(), 'RUNNING', ?, ?, ?);
                """,
                (
                    ingest_run_id,
                    f"SEN05 cTrader FTMO Tick {mode.upper()}",
                    self.environment,
                    self.account_id,
                    note,
                    socket.gethostname(),
                    os.getpid(),
                ),
            )
            conn.commit()
            return ingest_run_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def finish_ingest_run(
        self,
        ingest_run_id: str,
        status: str,
        rows_inserted: int,
        rows_spooled: int = 0,
        note: str | None = None,
    ) -> None:
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                UPDATE {quote_ident(self.schema)}.[IngestRun]
                   SET StoppedAtUtc = SYSUTCDATETIME(),
                       Status = ?,
                       RowsInserted = ?,
                       RowsSpooled = ?,
                       StopReason = COALESCE(?, StopReason)
                 WHERE IngestRunID = ?
                """,
                (
                    status.upper(),
                    int(rows_inserted),
                    int(rows_spooled),
                    note or f"rows_inserted={rows_inserted}; rows_spooled={rows_spooled}",
                    ingest_run_id,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def upsert_symbol_matches(self, matches: Sequence[SymbolMatch]) -> int:
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            rows = 0
            for match in matches:
                remote = match.remote
                mapping_status = "NOT_FOUND" if match.status == "UNMATCHED" else match.status
                cursor.execute(
                    f"""
                    MERGE {quote_ident(self.schema)}.[SymbolMap] AS tgt
                    USING (
                        SELECT
                            ? AS SymbolID,
                            ? AS SenSymbol,
                            ? AS AssetType,
                            ? AS CTraderSymbolId,
                            ? AS CTraderSymbolName,
                            ? AS CTraderDescription,
                            ? AS CTraderEnabled,
                            ? AS Digits,
                            ? AS PipPosition,
                            ? AS MappingStatus,
                            ? AS MappingScore,
                            ? AS Notes
                    ) AS src
                    ON tgt.SymbolID = src.SymbolID
                    WHEN MATCHED THEN UPDATE SET
                        SenSymbol = src.SenSymbol,
                        AssetType = src.AssetType,
                        CTraderSymbolId = src.CTraderSymbolId,
                        CTraderSymbolName = src.CTraderSymbolName,
                        CTraderDescription = src.CTraderDescription,
                        CTraderEnabled = src.CTraderEnabled,
                        Digits = src.Digits,
                        PipPosition = src.PipPosition,
                        MappingStatus = src.MappingStatus,
                        MappingScore = src.MappingScore,
                        Notes = src.Notes,
                        LastSyncedAtUtc = SYSUTCDATETIME()
                    WHEN NOT MATCHED THEN INSERT
                        (SymbolID, SenSymbol, AssetType, CTraderSymbolId,
                         CTraderSymbolName, CTraderDescription, CTraderEnabled,
                         Digits, PipPosition, MappingStatus, MappingScore, Notes, LastSyncedAtUtc)
                    VALUES
                        (src.SymbolID, src.SenSymbol, src.AssetType, src.CTraderSymbolId,
                         src.CTraderSymbolName, src.CTraderDescription, src.CTraderEnabled,
                         src.Digits, src.PipPosition, src.MappingStatus, src.MappingScore, src.Notes,
                         SYSUTCDATETIME());
                    """,
                    (
                        match.target.symbol_id,
                        match.target.local_symbol,
                        match.target.asset_type,
                        remote.ctrader_symbol_id if remote else None,
                        remote.symbol_name if remote else None,
                        remote.description if remote else None,
                        remote.enabled if remote else None,
                        remote.digits if remote else None,
                        remote.pip_position if remote else None,
                        mapping_status,
                        match.score,
                        match.reason,
                    ),
                )
                rows += 1
            conn.commit()
            return rows
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def fetch_matched_symbols(self) -> dict[str, tuple[TargetSymbol, RemoteSymbol]]:
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                SELECT SymbolID, SenSymbol, AssetType, CTraderSymbolId,
                       CTraderSymbolName, Digits, PipPosition
                FROM {quote_ident(self.schema)}.[SymbolMap]
                WHERE MappingStatus = 'MATCHED'
                  AND Enabled = 1
                  AND CTraderSymbolId IS NOT NULL
                """
            )
            results: dict[str, tuple[TargetSymbol, RemoteSymbol]] = {}
            for row in cursor.fetchall():
                local_symbol = str(row.SenSymbol).upper()
                if local_symbol not in self.targets:
                    continue
                target = self.targets[local_symbol]
                remote = RemoteSymbol(
                    ctrader_symbol_id=int(row.CTraderSymbolId),
                    symbol_name=str(row.CTraderSymbolName),
                    digits=int(row.Digits) if row.Digits is not None else None,
                    pip_position=int(row.PipPosition) if row.PipPosition is not None else None,
                )
                results[local_symbol] = (target, remote)
            return results
        finally:
            conn.close()

    def update_ingest_state(
        self,
        target: TargetSymbol,
        remote: RemoteSymbol,
        last_tick_time_utc: datetime,
        last_source_timestamp_ms: int,
        status: str,
        last_error: str | None = None,
    ) -> None:
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                MERGE {quote_ident(self.schema)}.[IngestState] AS tgt
                USING (
                    SELECT ? AS SymbolID, ? AS CTraderSymbolId
                ) AS src
                ON tgt.SymbolID = src.SymbolID
                WHEN MATCHED THEN UPDATE SET
                    CTraderSymbolId = ?,
                    LastLiveTickTimeUtc = ?,
                    LastSourceTimestampMs = ?,
                    LastWriteAtUtc = SYSUTCDATETIME(),
                    LastHeartbeatAtUtc = SYSUTCDATETIME(),
                    Status = ?,
                    LastError = ?,
                    UpdatedAtUtc = SYSUTCDATETIME()
                WHEN NOT MATCHED THEN INSERT
                    (SymbolID, CTraderSymbolId, LastLiveTickTimeUtc,
                     LastSourceTimestampMs, LastWriteAtUtc, LastHeartbeatAtUtc,
                     Status, LastError)
                VALUES
                    (?, ?, ?, ?, SYSUTCDATETIME(), SYSUTCDATETIME(), ?, ?);
                """,
                (
                    target.symbol_id,
                    remote.ctrader_symbol_id,
                    remote.ctrader_symbol_id,
                    last_tick_time_utc.astimezone(timezone.utc).replace(tzinfo=None),
                    int(last_source_timestamp_ms),
                    status,
                    last_error,
                    target.symbol_id,
                    remote.ctrader_symbol_id,
                    last_tick_time_utc.astimezone(timezone.utc).replace(tzinfo=None),
                    int(last_source_timestamp_ms),
                    status,
                    last_error,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
