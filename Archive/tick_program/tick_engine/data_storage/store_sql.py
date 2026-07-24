"""SQL Server persistence for the cTrader/FTMO tick provider."""

from __future__ import annotations

import logging
import os
import re
import socket
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timezone
from typing import Any

from tick_engine.data_storage.symbols import RemoteSymbol, SymbolMatch, TargetSymbol
from tick_engine.data_storage.ticks import TickRecord

_logger = logging.getLogger(__name__)

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
VALID_INGEST_RUN_STATUSES = {"RUNNING", "STOPPED", "FAILED", "DONE", "INTERRUPTED"}
MAX_STOP_REASON_CHARS = 400

INSERT_COLUMNS = (
    "SymbolID",
    "TickTimeUtc",
    "Bid",
    "Ask",
    "ReceivedAtUtc",
    "EventHash",
)

SOURCE_MODE_INSERT_COLUMNS = (
    "SymbolID",
    "TickTimeUtc",
    "Bid",
    "Ask",
    "ReceivedAtUtc",
    "SourceMode",
    "EventHash",
)

LEGACY_INSERT_COLUMNS = (
    "SymbolID",
    "CTraderSymbolId",
    "TickTimeUtc",
    "Bid",
    "Ask",
    "ReceivedAtUtc",
    "SourceMode",
    "EventHash",
)

FULL_LEGACY_INSERT_COLUMNS = (
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

TEMP_COLUMN_DEFS = {
    "SymbolID": "INT NOT NULL",
    "CTraderSymbolId": "BIGINT NULL",
    "CTraderSymbolName": "NVARCHAR(80) NULL",
    "TickTimeUtc": "DATETIME2(3) NOT NULL",
    "SourceTimestampMs": "BIGINT NULL",
    "BidRaw": "BIGINT NULL",
    "AskRaw": "BIGINT NULL",
    "Bid": "DECIMAL(19,8) NULL",
    "Ask": "DECIMAL(19,8) NULL",
    "BidUpdated": "BIT NULL",
    "AskUpdated": "BIT NULL",
    "QuoteType": "VARCHAR(16) NULL",
    "SourceMode": "VARCHAR(16) NOT NULL",
    "SessionCloseRaw": "BIGINT NULL",
    "SessionClose": "DECIMAL(19,8) NULL",
    "IsTechnicalEvent": "BIT NULL",
    "ReceivedAtUtc": "DATETIME2(3) NOT NULL",
    "IngestRunID": "UNIQUEIDENTIFIER NULL",
    "EventHash": "BINARY(32) NOT NULL",
}


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


def truncate_stop_reason(note: str | None) -> str | None:
    if note is None:
        return None
    text = str(note)
    if len(text) <= MAX_STOP_REASON_CHARS:
        return text
    suffix = "... [truncated]"
    return text[: MAX_STOP_REASON_CHARS - len(suffix)] + suffix


def _hash_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    return bytes(value or b"")


def inserted_records_from_last_insert(
    store: object,
    records: Iterable[TickRecord],
) -> list[TickRecord]:
    """Return records whose EventHash was inserted by the most recent store insert.

    Fake stores used in tests may not expose the tracking attribute; in that
    case keep the historic behavior and let callers use the original records.
    """
    hashes = getattr(store, "last_inserted_event_hashes", None)
    if hashes is None:
        return list(records)
    normalized = {_hash_bytes(item) for item in hashes}
    inserted: list[TickRecord] = []
    seen: set[bytes] = set()
    for record in records:
        if record.event_hash is None:
            continue
        digest = _hash_bytes(record.event_hash)
        if digest in normalized and digest not in seen:
            inserted.append(record)
            seen.add(digest)
    return inserted


def _is_ingest_run_status_constraint_error(exc: Exception) -> bool:
    text = str(exc)
    return "CK_tick_IngestRun_Status" in text and "CHECK constraint" in text


def _is_legacy_done_status_constraint_error(exc: Exception) -> bool:
    text = str(exc)
    return _is_ingest_run_status_constraint_error(exc) and "'DONE'" not in text


def _legacy_done_stop_reason(note: str | None) -> str:
    suffix = note or "rows_inserted=0; rows_spooled=0"
    return truncate_stop_reason(f"DONE; {suffix}") or "DONE"


def _legacy_interrupted_stop_reason(note: str) -> str:
    return truncate_stop_reason(
        f"INTERRUPTED; {note}; stored as STOPPED because DB status constraint is legacy"
    ) or "INTERRUPTED"


def _legacy_status_stop_reason(original_status: str, note: str | None) -> str:
    suffix = note or "rows_inserted=0; rows_spooled=0"
    return truncate_stop_reason(
        f"{original_status}; {suffix}; stored as FAILED because DB status constraint is legacy"
    ) or original_status


def update_ingest_state_after_insert(
    store: "TickSqlStore",
    matched: dict[str, tuple[TargetSymbol, RemoteSymbol]],
    records: list[TickRecord],
) -> None:
    grouped: dict[str, dict] = defaultdict(
        lambda: {
            "count": 0,
            "latest": None,
            "last_bid": None,
            "last_bid_ts": -1,
            "last_ask": None,
            "last_ask_ts": -1,
            "source_mode": "HISTORICAL",
        }
    )
    for record in records:
        symbol = record.local_symbol.upper()
        if symbol not in matched or record.is_technical_event:
            continue
        item = grouped[symbol]
        item["count"] = int(item["count"]) + 1
        item["source_mode"] = record.source_mode
        latest = item["latest"]
        if latest is None or record.source_timestamp_ms >= latest.source_timestamp_ms:
            item["latest"] = record
        if record.bid is not None and record.source_timestamp_ms >= int(item["last_bid_ts"]):
            item["last_bid"] = record.bid
            item["last_bid_ts"] = record.source_timestamp_ms
        if record.ask is not None and record.source_timestamp_ms >= int(item["last_ask_ts"]):
            item["last_ask"] = record.ask
            item["last_ask_ts"] = record.source_timestamp_ms

    for symbol, item in grouped.items():
        latest = item["latest"]
        if latest is None:
            continue
        target, remote = matched[symbol]
        source_mode = str(item["source_mode"])
        store.update_ingest_state(
            target,
            remote,
            latest.tick_time_utc,
            latest.source_timestamp_ms,
            status="LIVE" if source_mode == "LIVE" else "SYNCED",
            last_bid=item["last_bid"],
            last_ask=item["last_ask"],
            source_mode=source_mode,
            ticks_inserted=int(item["count"]),
        )


def _default_connection_factory() -> object:
    from tick_engine.data_storage.db_connector import get_connection

    return get_connection()


class TickSqlStore:
    """Write matched ticks into per-symbol tables under the SQL ``tick`` schema."""

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
        self._insert_shape_cache: dict[str, tuple[tuple[str, ...], str]] = {}
        self.last_inserted_event_hashes: set[bytes] = set()

    def _insert_shape_for_table(self, cursor: object, symbol: str) -> tuple[tuple[str, ...], str]:
        key = symbol.upper()
        if key in self._insert_shape_cache:
            return self._insert_shape_cache[key]
        cursor.execute(
            "SELECT name FROM sys.columns WHERE object_id = OBJECT_ID(?) AND is_computed = 0;",
            (f"{self.schema}.{key}",),
        )
        columns = {str(row[0]) for row in cursor.fetchall()}
        if "CTraderSymbolName" in columns:
            shape = (FULL_LEGACY_INSERT_COLUMNS, "full_legacy")
        elif "CTraderSymbolId" in columns:
            shape = (LEGACY_INSERT_COLUMNS, "legacy_id")
        elif "SourceMode" in columns:
            shape = (SOURCE_MODE_INSERT_COLUMNS, "source_mode")
        else:
            shape = (INSERT_COLUMNS, "slim")
        self._insert_shape_cache[key] = shape
        return shape

    def insert_ticks(self, records: Iterable[TickRecord]) -> int:
        self.last_inserted_event_hashes = set()
        grouped: dict[str, list[TickRecord]] = defaultdict(list)
        for record in records:
            if record.is_technical_event:
                continue
            grouped[record.local_symbol.upper()].append(record)
        if not grouped:
            return 0

        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            total_inserted = 0
            inserted_records: list[TickRecord] = []
            for symbol, rows in grouped.items():
                table_name = qualified_tick_table(
                    self.schema, symbol, allowed_symbols=self.allowed_symbols
                )
                insert_columns, insert_shape = self._insert_shape_for_table(cursor, symbol)
                columns = ", ".join(quote_ident(col) for col in insert_columns)
                source_columns = ", ".join(f"src.{quote_ident(col)}" for col in insert_columns)
                placeholders = ", ".join("?" for _ in insert_columns)
                temp_defs = ", ".join(
                    f"{quote_ident(col)} {TEMP_COLUMN_DEFS[col]}" for col in insert_columns
                )
                params = [row.to_db_params(insert_shape=insert_shape) for row in rows]

                cursor.execute("IF OBJECT_ID('tempdb..#TickInsert') IS NOT NULL DROP TABLE #TickInsert;")
                cursor.execute(f"CREATE TABLE #TickInsert ({temp_defs});")
                cursor.executemany(
                    f"INSERT INTO #TickInsert ({columns}) VALUES ({placeholders})",
                    params,
                )
                cursor.execute(
                    f"""
                    ;WITH src AS (
                        SELECT {columns},
                               ROW_NUMBER() OVER (PARTITION BY [EventHash] ORDER BY [EventHash]) AS _rn
                        FROM #TickInsert
                    )
                    INSERT INTO {table_name} ({columns})
                    OUTPUT inserted.[EventHash]
                    SELECT {source_columns}
                    FROM src
                    WHERE src._rn = 1
                      AND NOT EXISTS (
                          SELECT 1
                          FROM {table_name} AS tgt WITH (UPDLOCK, HOLDLOCK)
                          WHERE tgt.[EventHash] = src.[EventHash]
                      );
                    """
                )
                inserted_hashes = {_hash_bytes(row[0]) for row in cursor.fetchall()}
                self.last_inserted_event_hashes.update(inserted_hashes)
                total_inserted += len(inserted_hashes)
                seen_hashes: set[bytes] = set()
                for row in rows:
                    digest = _hash_bytes(row.event_hash)
                    if digest in inserted_hashes and digest not in seen_hashes:
                        inserted_records.append(row)
                        seen_hashes.add(digest)
                cursor.execute("DROP TABLE #TickInsert;")

            state_groups: dict[tuple[str, str], list[TickRecord]] = defaultdict(list)
            for record in inserted_records:
                state_groups[(record.local_symbol.upper(), record.source_mode.upper())].append(record)
            for (symbol, source_mode), rows in state_groups.items():
                target = self.targets[symbol]
                latest = max(rows, key=lambda item: item.source_timestamp_ms)
                bid_rows = [row for row in rows if row.bid is not None]
                ask_rows = [row for row in rows if row.ask is not None]
                last_bid = (
                    max(bid_rows, key=lambda item: item.source_timestamp_ms).bid
                    if bid_rows
                    else None
                )
                last_ask = (
                    max(ask_rows, key=lambda item: item.source_timestamp_ms).ask
                    if ask_rows
                    else None
                )
                self._update_ingest_state_cursor(
                    cursor,
                    target,
                    latest.ctrader_symbol_id,
                    latest.tick_time_utc,
                    latest.source_timestamp_ms,
                    status="LIVE" if source_mode == "LIVE" else "SYNCED",
                    last_bid=last_bid,
                    last_ask=last_ask,
                    source_mode=source_mode,
                    ticks_inserted=len(rows),
                )
            conn.commit()
            return total_inserted
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
                    truncate_stop_reason(note),
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
        status = status.upper()
        if status not in VALID_INGEST_RUN_STATUSES:
            raise ValueError(f"invalid tick ingest run status: {status!r}")
        stop_reason = truncate_stop_reason(
            note or f"rows_inserted={rows_inserted}; rows_spooled={rows_spooled}"
        )
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
                (status, int(rows_inserted), int(rows_spooled), stop_reason, ingest_run_id),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            if status == "DONE" and _is_legacy_done_status_constraint_error(exc):
                legacy_note = _legacy_done_stop_reason(stop_reason)
                for fallback_status in ("STOPPED", "FAILED"):
                    try:
                        cursor.execute(
                            f"""
                            UPDATE {quote_ident(self.schema)}.[IngestRun]
                               SET StoppedAtUtc = SYSUTCDATETIME(),
                                   Status = ?,
                                   RowsInserted = ?,
                                   RowsSpooled = ?,
                                   StopReason = ?
                             WHERE IngestRunID = ?
                            """,
                            (
                                fallback_status,
                                int(rows_inserted),
                                int(rows_spooled),
                                legacy_note,
                                ingest_run_id,
                            ),
                        )
                        conn.commit()
                        return
                    except Exception:
                        conn.rollback()
            if status in {"STOPPED", "INTERRUPTED"} and _is_ingest_run_status_constraint_error(exc):
                legacy_note = _legacy_status_stop_reason(status, stop_reason)
                try:
                    cursor.execute(
                        f"""
                        UPDATE {quote_ident(self.schema)}.[IngestRun]
                           SET StoppedAtUtc = SYSUTCDATETIME(),
                               Status = 'FAILED',
                               RowsInserted = ?,
                               RowsSpooled = ?,
                               StopReason = ?
                         WHERE IngestRunID = ?
                        """,
                        (int(rows_inserted), int(rows_spooled), legacy_note, ingest_run_id),
                    )
                    conn.commit()
                    _logger.warning(
                        "finish_ingest_run: DB constraint rejected %s; stored run as FAILED run_id=%s",
                        status,
                        ingest_run_id,
                    )
                    return
                except Exception:
                    conn.rollback()
            raise
        finally:
            conn.close()

    def mark_stale_runs_interrupted(
        self,
        lookback_days: int = 30,
        min_age_seconds: int = 0,
    ) -> int:
        from tick_engine.utils_support.proc_utils import is_pid_alive

        hostname = socket.gethostname()
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                SELECT IngestRunID, ProcessID
                FROM {quote_ident(self.schema)}.[IngestRun]
                WHERE Status = 'RUNNING'
                  AND HostName = ?
                  AND StartedAtUtc >= DATEADD(day, ?, SYSUTCDATETIME())
                  AND StartedAtUtc <= DATEADD(second, ?, SYSUTCDATETIME())
                """,
                (hostname, -abs(lookback_days), -abs(int(min_age_seconds))),
            )
            stale: list[tuple[str, int | None]] = [
                (str(row[0]), row[1]) for row in cursor.fetchall()
            ]
        finally:
            conn.close()

        updated = 0
        for run_id, pid in stale:
            if pid is not None and is_pid_alive(int(pid)):
                continue
            conn2 = self.connection_factory()
            cur2 = conn2.cursor()
            repair_note = "auto-marked INTERRUPTED by tick health repair (process dead)"
            try:
                cur2.execute(
                    f"""
                    UPDATE {quote_ident(self.schema)}.[IngestRun]
                       SET StoppedAtUtc = SYSUTCDATETIME(),
                           Status = 'INTERRUPTED',
                           StopReason = LEFT(COALESCE(StopReason + ' | ', '') + ?, ?)
                     WHERE IngestRunID = ?
                       AND Status = 'RUNNING'
                    """,
                    (repair_note, MAX_STOP_REASON_CHARS, run_id),
                )
                conn2.commit()
                updated += cur2.rowcount if cur2.rowcount >= 0 else 0
            except Exception as exc:
                conn2.rollback()
                if _is_ingest_run_status_constraint_error(exc):
                    legacy_note = _legacy_interrupted_stop_reason(repair_note)
                    try:
                        cur2.execute(
                            f"""
                            UPDATE {quote_ident(self.schema)}.[IngestRun]
                               SET StoppedAtUtc = SYSUTCDATETIME(),
                                   Status = 'STOPPED',
                                   StopReason = LEFT(COALESCE(StopReason + ' | ', '') + ?, ?)
                             WHERE IngestRunID = ?
                               AND Status = 'RUNNING'
                            """,
                            (legacy_note, MAX_STOP_REASON_CHARS, run_id),
                        )
                        conn2.commit()
                        updated += cur2.rowcount if cur2.rowcount >= 0 else 0
                        _logger.warning(
                            "mark_stale_runs_interrupted: DB constraint rejected "
                            "INTERRUPTED; stored stale run as STOPPED run_id=%s",
                            run_id,
                        )
                        continue
                    except Exception:
                        conn2.rollback()
                _logger.exception(
                    "mark_stale_runs_interrupted: failed to update run_id=%s", run_id
                )
            finally:
                conn2.close()
        return updated

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
                        SELECT ? AS SymbolID, ? AS SenSymbol, ? AS AssetType,
                               ? AS CTraderSymbolId, ? AS CTraderSymbolName, ? AS CTraderDescription,
                               ? AS CTraderEnabled, ? AS Digits, ? AS PipPosition,
                               ? AS MappingStatus, ? AS MappingScore, ? AS Notes
                    ) AS src
                    ON tgt.SymbolID = src.SymbolID
                    WHEN MATCHED THEN UPDATE SET
                        SenSymbol = src.SenSymbol, AssetType = src.AssetType,
                        CTraderSymbolId = src.CTraderSymbolId,
                        CTraderSymbolName = src.CTraderSymbolName,
                        CTraderDescription = src.CTraderDescription,
                        CTraderEnabled = src.CTraderEnabled,
                        Digits = src.Digits, PipPosition = src.PipPosition,
                        MappingStatus = src.MappingStatus, MappingScore = src.MappingScore,
                        Notes = src.Notes, LastSyncedAtUtc = SYSUTCDATETIME()
                    WHEN NOT MATCHED THEN INSERT
                        (SymbolID, SenSymbol, AssetType, CTraderSymbolId, CTraderSymbolName,
                         CTraderDescription, CTraderEnabled, Digits, PipPosition,
                         MappingStatus, MappingScore, Notes, LastSyncedAtUtc)
                    VALUES
                        (src.SymbolID, src.SenSymbol, src.AssetType, src.CTraderSymbolId,
                         src.CTraderSymbolName, src.CTraderDescription, src.CTraderEnabled,
                         src.Digits, src.PipPosition, src.MappingStatus, src.MappingScore,
                         src.Notes, SYSUTCDATETIME());
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

    def count_tick_rows_by_symbol(self) -> dict[str, int]:
        return {
            symbol: int(stats["rows"])
            for symbol, stats in self.tick_row_stats_by_symbol().items()
        }

    def tick_row_stats_by_symbol(self) -> dict[str, dict[str, Any]]:
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            stats: dict[str, dict[str, Any]] = {}
            for symbol in sorted(self.allowed_symbols):
                table_name = qualified_tick_table(
                    self.schema, symbol, allowed_symbols=self.allowed_symbols
                )
                cursor.execute(
                    f"SELECT COUNT_BIG(*), MIN(TickTimeUtc), MAX(TickTimeUtc) FROM {table_name}"
                )
                count, first_tick, last_tick = cursor.fetchone()
                stats[symbol] = {
                    "rows": int(count or 0),
                    "first_tick_utc": first_tick,
                    "last_tick_utc": last_tick,
                }
            return stats
        finally:
            conn.close()

    def reset_tick_data(self) -> dict[str, Any]:
        """Clear per-symbol tick rows and reset ingest state for a fresh pull."""
        conn = self.connection_factory()
        cursor = conn.cursor()
        stats_before: dict[str, dict[str, Any]] = {}
        truncate_used = True
        try:
            for symbol in sorted(self.allowed_symbols):
                table_name = qualified_tick_table(
                    self.schema, symbol, allowed_symbols=self.allowed_symbols
                )
                cursor.execute(
                    f"SELECT COUNT_BIG(*), MIN(TickTimeUtc), MAX(TickTimeUtc) FROM {table_name}"
                )
                count, first_tick, last_tick = cursor.fetchone()
                stats_before[symbol] = {
                    "rows": int(count or 0),
                    "first_tick_utc": first_tick,
                    "last_tick_utc": last_tick,
                }

            try:
                for symbol in sorted(self.allowed_symbols):
                    table_name = qualified_tick_table(
                        self.schema, symbol, allowed_symbols=self.allowed_symbols
                    )
                    cursor.execute(f"TRUNCATE TABLE {table_name};")
            except Exception:
                conn.rollback()
                truncate_used = False
                for symbol in sorted(self.allowed_symbols):
                    table_name = qualified_tick_table(
                        self.schema, symbol, allowed_symbols=self.allowed_symbols
                    )
                    cursor.execute(f"DELETE FROM {table_name};")
                    cursor.execute(
                        f"DBCC CHECKIDENT ('{self.schema}.{symbol}', RESEED, 0) WITH NO_INFOMSGS;"
                    )

            symbol_ids = [self.targets[symbol].symbol_id for symbol in sorted(self.allowed_symbols)]
            placeholders = ", ".join("?" for _ in symbol_ids)
            cursor.execute(
                f"""
                UPDATE {quote_ident(self.schema)}.[IngestState]
                   SET LastLiveTickTimeUtc = NULL,
                       LastHistoricalTickTimeUtc = NULL,
                       LastSourceTimestampMs = NULL,
                       LastBid = NULL,
                       LastAsk = NULL,
                       LastWriteAtUtc = NULL,
                       LastHeartbeatAtUtc = NULL,
                       TotalTicksInserted = 0,
                       ConsecutiveErrors = 0,
                       Status = 'INIT',
                       UpdatedAtUtc = SYSUTCDATETIME(),
                       LastError = NULL
                 WHERE SymbolID IN ({placeholders})
                """,
                tuple(symbol_ids),
            )
            ingest_state_rows = cursor.rowcount if cursor.rowcount >= 0 else 0
            conn.commit()
            total_rows = sum(int(item["rows"]) for item in stats_before.values())
            return {
                "symbols": stats_before,
                "total_rows": total_rows,
                "ingest_state_rows_reset": int(ingest_state_rows),
                "truncate_used": truncate_used,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def has_successful_first_run_backfill(self) -> bool:
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                SELECT TOP 1 1
                FROM {quote_ident(self.schema)}.[IngestRun]
                WHERE (Status = 'DONE' OR (Status = 'STOPPED' AND StopReason LIKE 'DONE;%'))
                  AND AppName LIKE '%BACKFILL%'
                  AND StopReason LIKE '%first-run%'
                ORDER BY StoppedAtUtc DESC
                """
            )
            return cursor.fetchone() is not None
        finally:
            conn.close()

    def update_ingest_state(
        self,
        target: TargetSymbol,
        remote: RemoteSymbol,
        last_tick_time_utc: datetime,
        last_source_timestamp_ms: int,
        status: str,
        last_bid: object | None = None,
        last_ask: object | None = None,
        last_error: str | None = None,
        source_mode: str = "HISTORICAL",
        ticks_inserted: int = 0,
    ) -> None:
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            self._update_ingest_state_cursor(
                cursor,
                target,
                remote.ctrader_symbol_id,
                last_tick_time_utc,
                last_source_timestamp_ms,
                status,
                last_bid=last_bid,
                last_ask=last_ask,
                last_error=last_error,
                source_mode=source_mode,
                ticks_inserted=ticks_inserted,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _update_ingest_state_cursor(
        self,
        cursor: object,
        target: TargetSymbol,
        ctrader_symbol_id: int,
        last_tick_time_utc: datetime,
        last_source_timestamp_ms: int,
        status: str,
        last_bid: object | None = None,
        last_ask: object | None = None,
        last_error: str | None = None,
        source_mode: str = "HISTORICAL",
        ticks_inserted: int = 0,
    ) -> None:
        source_mode = source_mode.upper()
        if source_mode not in {"LIVE", "HISTORICAL"}:
            raise ValueError(f"invalid tick source_mode for ingest state: {source_mode!r}")
        tick_time_column = (
            "LastHistoricalTickTimeUtc" if source_mode == "HISTORICAL" else "LastLiveTickTimeUtc"
        )
        tick_time_sql = quote_ident(tick_time_column)
        tick_time_value = last_tick_time_utc.astimezone(timezone.utc).replace(tzinfo=None)
        cursor.execute(
            f"""
                MERGE {quote_ident(self.schema)}.[IngestState] AS tgt
                USING (SELECT ? AS SymbolID, ? AS CTraderSymbolId) AS src
                ON tgt.SymbolID = src.SymbolID
                WHEN MATCHED THEN UPDATE SET
                    CTraderSymbolId = ?,
                    {tick_time_sql} =
                        CASE WHEN tgt.{tick_time_sql} IS NULL OR ? >= tgt.{tick_time_sql}
                             THEN ? ELSE tgt.{tick_time_sql} END,
                    LastSourceTimestampMs =
                        CASE WHEN tgt.{tick_time_sql} IS NULL OR ? >= tgt.{tick_time_sql}
                             THEN ? ELSE LastSourceTimestampMs END,
                    LastBid =
                        CASE WHEN tgt.{tick_time_sql} IS NULL OR ? >= tgt.{tick_time_sql}
                             THEN COALESCE(?, LastBid) ELSE LastBid END,
                    LastAsk =
                        CASE WHEN tgt.{tick_time_sql} IS NULL OR ? >= tgt.{tick_time_sql}
                             THEN COALESCE(?, LastAsk) ELSE LastAsk END,
                    LastWriteAtUtc = SYSUTCDATETIME(),
                    LastHeartbeatAtUtc = SYSUTCDATETIME(),
                    TotalTicksInserted = COALESCE(TotalTicksInserted, 0) + ?,
                    Status = ?, LastError = ?, UpdatedAtUtc = SYSUTCDATETIME()
                WHEN NOT MATCHED THEN INSERT
                    (SymbolID, CTraderSymbolId, {tick_time_sql},
                     LastSourceTimestampMs, LastBid, LastAsk, LastWriteAtUtc, LastHeartbeatAtUtc,
                     TotalTicksInserted, Status, LastError)
                VALUES (?, ?, ?, ?, ?, ?, SYSUTCDATETIME(), SYSUTCDATETIME(), ?, ?, ?);
            """,
            (
                target.symbol_id, int(ctrader_symbol_id),
                int(ctrader_symbol_id),
                tick_time_value, tick_time_value,
                tick_time_value, int(last_source_timestamp_ms),
                tick_time_value, last_bid,
                tick_time_value, last_ask,
                int(ticks_inserted), status, last_error,
                target.symbol_id, int(ctrader_symbol_id), tick_time_value,
                int(last_source_timestamp_ms), last_bid, last_ask,
                int(ticks_inserted), status, last_error,
            ),
        )
