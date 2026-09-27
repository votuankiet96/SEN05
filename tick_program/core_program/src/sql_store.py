"""All SQL Server access: connection, tick insert/dedup, IngestRun/IngestState,
SymbolMap read/write, and administrative queries (reset, stats, stale-run repair).

The tick tables only ever use the "slim" 5-column insert shape created by
scripts/sql/tickdata_setup.sql (SymbolID, TickTimeUtc, Bid, Ask, ReceivedAtUtc)
and tick_program only ever writes HISTORICAL data (no live mode), so this
module does not carry the legacy multi-shape / LIVE-vs-HISTORICAL branching
the previous implementation needed for older, already-migrated schemas.

Dedup on insert is enforced by a unique index on (TickTimeUtc, Bid, Ask) --
no separate EventHash column (2026-09, see tickdata_setup.sql header): a
symbol's own table already fixes SymbolID, and cTrader's raw tick feed is
just (timestamp, price) per side (ProtoOATickData), so the two-sided quote
after BID/ASK merge is fully identified by its own time+bid+ask -- a
derived hash of exactly that information added nothing a direct index
couldn't enforce, at a real, measured storage cost (EventHash was ~66% of
a tick table's on-disk size, being high-entropy SHA-256 that resists
columnstore compression, versus ~0% for a genuinely redundant column like
SymbolID).
"""

from __future__ import annotations

import json
import os
import re
import socket
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timezone
from typing import Any

import pyodbc

from src.pipeline import TickRecord

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
VALID_INGEST_RUN_STATUSES = {"RUNNING", "STOPPED", "FAILED", "DONE"}
MAX_STOP_REASON_CHARS = 400
INSERT_COLUMNS = ("SymbolID", "TickTimeUtc", "Bid", "Ask", "ReceivedAtUtc")
_TEMP_COLUMN_DEFS = {
    "SymbolID": "INT NOT NULL",
    "TickTimeUtc": "DATETIME2(3) NOT NULL",
    "Bid": "DECIMAL(19,8) NULL",
    "Ask": "DECIMAL(19,8) NULL",
    "ReceivedAtUtc": "DATETIME2(3) NOT NULL",
}
_DEDUP_COLUMNS = ("TickTimeUtc", "Bid", "Ask")

_DB_RETRY_COUNT = int(os.environ.get("DB_RETRY_COUNT", "3") or 3)
_DB_RETRY_DELAY = int(os.environ.get("DB_RETRY_DELAY_SEC", "5") or 5)


def get_connection() -> pyodbc.Connection:
    """Return an active pyodbc connection with bounded retry."""
    from src.configuration import build_conn_str, redact_operator_secrets
    from src.notify import write_system_event

    conn_str = build_conn_str()
    last_err: Exception = RuntimeError("unreachable")
    for attempt in range(1, _DB_RETRY_COUNT + 1):
        try:
            return pyodbc.connect(conn_str, timeout=30)
        except pyodbc.Error as exc:
            last_err = exc
            write_system_event(
                "sql_store", "DB_CONNECT_RETRY", redact_operator_secrets(exc), level="WARNING",
                attempt=attempt, max_attempts=_DB_RETRY_COUNT,
            )
            if attempt < _DB_RETRY_COUNT:
                time.sleep(_DB_RETRY_DELAY)
    write_system_event(
        "sql_store", "DB_CONNECT_FAILED", redact_operator_secrets(last_err), level="ERROR", attempts=_DB_RETRY_COUNT,
    )
    raise last_err


def resolve_target_symbols(requested: Sequence[str]) -> tuple[Any, ...]:
    """Cross-reference requested symbol names against DWH.Dim_Symbol.

    Config.yaml's `symbols:` list is only the operator's *selection* of
    which names to backfill — never a source of SymbolID/AssetType. Those
    are looked up here, from the warehouse dimension table, every time.
    Fail-closed: a name that isn't in DWH.Dim_Symbol (or is IsActive=0) is
    a hard error, not a silently skipped symbol.
    """
    from src.configuration import TargetSymbol

    names = [str(n).strip().upper() for n in requested if str(n).strip()]
    if not names:
        raise ValueError("no symbols requested (Config.yaml `symbols:` is empty)")

    conn = get_connection()
    cursor = conn.cursor()
    try:
        placeholders = ", ".join("?" for _ in names)
        cursor.execute(
            f"SELECT SymbolID, Symbol, AssetType, IsActive FROM DWH.Dim_Symbol WHERE Symbol IN ({placeholders})",
            tuple(names),
        )
        found = {str(row.Symbol).upper(): row for row in cursor.fetchall()}
    finally:
        conn.close()

    missing = [n for n in names if n not in found]
    inactive = [n for n in names if n in found and not bool(found[n].IsActive)]
    if missing or inactive:
        problems = []
        if missing:
            problems.append(f"not in DWH.Dim_Symbol: {', '.join(missing)}")
        if inactive:
            problems.append(f"IsActive=0 in DWH.Dim_Symbol: {', '.join(inactive)}")
        raise RuntimeError(
            "cannot resolve requested symbols against the warehouse — " + "; ".join(problems)
        )

    return tuple(
        TargetSymbol(symbol_id=int(found[n].SymbolID), local_symbol=n, asset_type=str(found[n].AssetType))
        for n in names
    )


def quote_ident(identifier: str) -> str:
    if not IDENTIFIER_RE.match(identifier):
        raise ValueError(f"unsafe SQL identifier: {identifier!r}")
    return f"[{identifier}]"


def qualified_tick_table(schema: str, local_symbol: str, allowed_symbols: set[str] | None = None) -> str:
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


def _default_connection_factory() -> object:
    return get_connection()


class TickSqlStore:
    """Write matched historical ticks into per-symbol tables under the SQL ``tick`` schema."""

    def __init__(
        self,
        schema: str,
        targets: Sequence[Any],
        connection_factory: Callable[[], object] | None = None,
        environment: str = "demo",
        account_id: int | None = None,
    ) -> None:
        self.schema = schema
        self.targets = {t.local_symbol.upper(): t for t in targets}
        self.allowed_symbols = set(self.targets)
        self.connection_factory = connection_factory or _default_connection_factory
        self.environment = environment
        self.account_id = account_id
        self.last_inserted_keys: set[tuple[Any, ...]] = set()

    # -- tick insert ---------------------------------------------------

    def insert_ticks(self, records: Iterable[TickRecord]) -> int:
        """Dedup strategy (2026-09): no full-history index backs this anymore
        (see UX_*_RecentDedup / _DEDUP_WINDOW_RETAIN_DAYS in configuration.py
        and refresh_dedup_window() below) -- for EVERY batch, regardless of
        how old or new its ticks are, we first ask the table directly
        "which of these (TickTimeUtc, Bid, Ask) keys already exist between
        this batch's own min and max timestamp" and only attempt to insert
        the ones that come back missing. That range query is cheap without
        any index, because a Clustered Columnstore Index keeps per-segment
        min/max metadata and skips whole segments outside the range (a
        historical batch from January only ever touches January's segments).
        The one case this in-memory check can't fully protect against on its
        own is two *concurrent* processes racing to insert overlapping data
        (spool-drain retrying a batch a live backfill is also re-fetching,
        after a transient SQL failure -- they use separate job locks, see
        __main__.py). That's what the small filtered recent-window unique
        index is for: a real, enforced-by-SQL backstop, but only sized for
        the last _DEDUP_WINDOW_RETAIN_DAYS days, since that's the entire
        realistic span such a race could ever land in (routine jobs never
        reach further back than gap_fill_max_lookback_days, and the longest
        observed SQL-outage-driven staleness in this project's history was
        under 24h). SQL Server refuses IGNORE_DUP_KEY on a filtered index
        (confirmed against the real server), so a genuine collision there
        raises instead of being silently dropped -- that's fine, it just
        fails this INSERT the normal way, and the caller's existing
        spool-and-retry path (TickBatcher.flush()) already handles any SQL
        failure, including this one. A duplicate that manages to land
        outside the filtered window -- which would require a human
        deliberately re-running two overlapping ad-hoc historical backfills
        into the same old range at the same moment -- is not silently
        corrupted or lost, just a rare extra row, recoverable with a
        one-off cleanup query if it's ever actually seen.
        """
        self.last_inserted_keys = set()
        grouped: dict[str, list[TickRecord]] = defaultdict(list)
        for record in records:
            grouped[record.local_symbol.upper()].append(record)
        if not grouped:
            return 0

        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            total_inserted = 0
            columns = ", ".join(quote_ident(c) for c in INSERT_COLUMNS)
            source_columns = ", ".join(f"src.{quote_ident(c)}" for c in INSERT_COLUMNS)
            placeholders = ", ".join("?" for _ in INSERT_COLUMNS)
            temp_defs = ", ".join(f"{quote_ident(c)} {_TEMP_COLUMN_DEFS[c]}" for c in INSERT_COLUMNS)
            dedup_cols = ", ".join(quote_ident(c) for c in _DEDUP_COLUMNS)
            dedup_output = ", ".join(f"inserted.{quote_ident(c)}" for c in _DEDUP_COLUMNS)
            inserted_records: list[TickRecord] = []
            for symbol, rows in grouped.items():
                table_name = qualified_tick_table(self.schema, symbol, allowed_symbols=self.allowed_symbols)

                range_min = min(r.tick_time_utc for r in rows).astimezone(timezone.utc).replace(tzinfo=None)
                range_max = max(r.tick_time_utc for r in rows).astimezone(timezone.utc).replace(tzinfo=None)
                cursor.execute(
                    f"SELECT {dedup_cols} FROM {table_name} WHERE [TickTimeUtc] BETWEEN ? AND ?",
                    (range_min, range_max),
                )
                existing_keys = {tuple(row) for row in cursor.fetchall()}

                seen: set[tuple[Any, ...]] = set()
                new_rows: list[TickRecord] = []
                for row in rows:
                    key = row.sql_dedup_key()
                    if key in existing_keys or key in seen:
                        continue
                    seen.add(key)
                    new_rows.append(row)
                if not new_rows:
                    continue
                params = [row.to_db_params() for row in new_rows]

                cursor.execute("IF OBJECT_ID('tempdb..#TickInsert') IS NOT NULL DROP TABLE #TickInsert;")
                cursor.execute(f"CREATE TABLE #TickInsert ({temp_defs});")
                cursor.executemany(f"INSERT INTO #TickInsert ({columns}) VALUES ({placeholders})", params)
                cursor.execute(
                    f"""
                    INSERT INTO {table_name} ({columns})
                    OUTPUT {dedup_output}
                    SELECT {source_columns} FROM #TickInsert AS src;
                    """
                )
                # If a row still collides with the recent-window filtered
                # index (see refresh_dedup_window) -- a race the in-memory
                # check above can't see coming, per this method's own
                # docstring -- this statement raises and the whole batch
                # falls back to the spool via TickBatcher.flush()'s except
                # clause, same as any other SQL failure.
                inserted_keys = {tuple(row) for row in cursor.fetchall()}
                self.last_inserted_keys.update(inserted_keys)
                total_inserted += len(inserted_keys)
                # new_rows is already unique-by-key (built via `seen` above),
                # so each key below matches at most one row -- no need to
                # track "already appended" separately.
                inserted_records.extend(row for row in new_rows if row.sql_dedup_key() in inserted_keys)
                cursor.execute("DROP TABLE #TickInsert;")

            for symbol in {r.local_symbol.upper() for r in inserted_records}:
                rows = [r for r in inserted_records if r.local_symbol.upper() == symbol]
                latest = max(rows, key=lambda item: item.source_timestamp_ms)
                bid_rows = [r for r in rows if r.bid is not None]
                ask_rows = [r for r in rows if r.ask is not None]
                last_bid = max(bid_rows, key=lambda r: r.source_timestamp_ms).bid if bid_rows else None
                last_ask = max(ask_rows, key=lambda r: r.source_timestamp_ms).ask if ask_rows else None
                self._update_ingest_state_cursor(
                    cursor, self.targets[symbol], latest.ctrader_symbol_id,
                    latest.tick_time_utc, latest.source_timestamp_ms,
                    status="SYNCED", last_bid=last_bid, last_ask=last_ask, ticks_inserted=len(rows),
                )
            conn.commit()
            return total_inserted
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -- IngestRun -------------------------------------------------------

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
                (ingest_run_id, f"SEN05 cTrader FTMO Tick {mode.upper()}", self.environment,
                 self.account_id, truncate_stop_reason(note), socket.gethostname(), os.getpid()),
            )
            conn.commit()
            return ingest_run_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def finish_ingest_run(
        self, ingest_run_id: str, status: str, rows_inserted: int, rows_spooled: int = 0, note: str | None = None,
    ) -> None:
        status = status.upper()
        if status not in VALID_INGEST_RUN_STATUSES:
            raise ValueError(f"invalid tick ingest run status: {status!r}")
        stop_reason = truncate_stop_reason(note or f"rows_inserted={rows_inserted}; rows_spooled={rows_spooled}")
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                UPDATE {quote_ident(self.schema)}.[IngestRun]
                   SET StoppedAtUtc = SYSUTCDATETIME(), Status = ?, RowsInserted = ?,
                       RowsSpooled = ?, StopReason = COALESCE(?, StopReason)
                 WHERE IngestRunID = ?
                """,
                (status, int(rows_inserted), int(rows_spooled), stop_reason, ingest_run_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def mark_stale_runs_stopped(self, lookback_days: int = 30, min_age_seconds: int = 0) -> int:
        from src.spool import is_pid_alive

        hostname = socket.gethostname()
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                SELECT IngestRunID, ProcessID FROM {quote_ident(self.schema)}.[IngestRun]
                WHERE Status = 'RUNNING' AND HostName = ?
                  AND StartedAtUtc >= DATEADD(day, ?, SYSUTCDATETIME())
                  AND StartedAtUtc <= DATEADD(second, ?, SYSUTCDATETIME())
                """,
                (hostname, -abs(lookback_days), -abs(int(min_age_seconds))),
            )
            stale = [(str(row[0]), row[1]) for row in cursor.fetchall()]
        finally:
            conn.close()

        updated = 0
        for run_id, pid in stale:
            if pid is not None and is_pid_alive(int(pid)):
                continue
            conn2 = self.connection_factory()
            cur2 = conn2.cursor()
            try:
                cur2.execute(
                    f"""
                    UPDATE {quote_ident(self.schema)}.[IngestRun]
                       SET StoppedAtUtc = SYSUTCDATETIME(), Status = 'STOPPED',
                           StopReason = LEFT(COALESCE(StopReason + ' | ', '') + ?, ?)
                     WHERE IngestRunID = ? AND Status = 'RUNNING'
                    """,
                    ("auto-marked STOPPED by tick health repair (process dead)", MAX_STOP_REASON_CHARS, run_id),
                )
                conn2.commit()
                updated += cur2.rowcount if cur2.rowcount >= 0 else 0
            except Exception as exc:
                conn2.rollback()
                from src.notify import write_system_event

                write_system_event("sql_store", "STALE_RUN_REPAIR_FAILED", str(exc), level="ERROR", run_id=run_id)
            finally:
                conn2.close()
        return updated

    def has_successful_first_run_backfill(self) -> bool:
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                SELECT TOP 1 1 FROM {quote_ident(self.schema)}.[IngestRun]
                WHERE Status = 'DONE' AND AppName LIKE '%BACKFILL%'
                ORDER BY StoppedAtUtc DESC
                """
            )
            return cursor.fetchone() is not None
        finally:
            conn.close()

    def min_ingest_watermark(self) -> datetime | None:
        """Earliest per-symbol LastHistoricalTickTimeUtc across tracked symbols.

        SQL's MIN() ignores NULLs, so a symbol with no IngestState row yet
        simply doesn't affect the result. None means *no* tracked symbol has
        ever ingested a tick — callers should treat that as "unknown, use
        the bounded fallback", not as "already caught up".
        """
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            symbol_ids = [t.symbol_id for t in self.targets.values()]
            if not symbol_ids:
                return None
            placeholders = ", ".join("?" for _ in symbol_ids)
            cursor.execute(
                f"""
                SELECT MIN(LastHistoricalTickTimeUtc)
                FROM {quote_ident(self.schema)}.[IngestState]
                WHERE SymbolID IN ({placeholders})
                """,
                tuple(symbol_ids),
            )
            row = cursor.fetchone()
            value = row[0] if row else None
            if value is None:
                return None
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        finally:
            conn.close()

    # -- SymbolMap ---------------------------------------------------------

    def upsert_symbol_matches(self, matches: Sequence[Any]) -> int:
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
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
                        CTraderSymbolId = src.CTraderSymbolId, CTraderSymbolName = src.CTraderSymbolName,
                        CTraderDescription = src.CTraderDescription, CTraderEnabled = src.CTraderEnabled,
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
                    (match.target.symbol_id, match.target.local_symbol, match.target.asset_type,
                     remote.ctrader_symbol_id if remote else None, remote.symbol_name if remote else None,
                     remote.description if remote else None, remote.enabled if remote else None,
                     remote.digits if remote else None, remote.pip_position if remote else None,
                     mapping_status, match.score, match.reason),
                )
            conn.commit()
            return len(matches)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def update_symbol_schedules(self, schedules_by_ctrader_id: dict[int, dict[str, Any]]) -> int:
        """Persist real trading-schedule + holiday data fetched from cTrader.

        Only overwrites rows we actually got fresh data for this call -- a
        symbol missing from `schedules_by_ctrader_id` (e.g. it dropped out of
        a partial fetch) keeps its last-known-good schedule rather than being
        blanked out, so a transient sync problem never regresses coverage.
        """
        if not schedules_by_ctrader_id:
            return 0
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            updated = 0
            for ctrader_id, sched in schedules_by_ctrader_id.items():
                cursor.execute(
                    f"""
                    UPDATE {quote_ident(self.schema)}.[SymbolMap]
                       SET ScheduleTimeZone = ?, ScheduleJson = ?, HolidayJson = ?
                     WHERE CTraderSymbolId = ?
                    """,
                    (
                        sched.get("tz"),
                        json.dumps(sched.get("intervals", [])),
                        json.dumps(sched.get("holidays", [])),
                        int(ctrader_id),
                    ),
                )
                updated += cursor.rowcount if cursor.rowcount >= 0 else 0
            conn.commit()
            return updated
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def load_symbol_schedules(self) -> dict[str, dict[str, Any]]:
        """SenSymbol -> {tz, intervals, holidays} for market-hours checks.

        A symbol with no synced schedule yet (ScheduleJson still NULL, or a
        malformed row) is simply absent from the result -- callers must treat
        that as "unknown", never as "closed".
        """
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                SELECT SenSymbol, ScheduleTimeZone, ScheduleJson, HolidayJson
                FROM {quote_ident(self.schema)}.[SymbolMap]
                WHERE ScheduleJson IS NOT NULL AND ScheduleTimeZone IS NOT NULL
                """
            )
            result: dict[str, dict[str, Any]] = {}
            for row in cursor.fetchall():
                try:
                    result[str(row.SenSymbol).upper()] = {
                        "tz": str(row.ScheduleTimeZone),
                        "intervals": json.loads(row.ScheduleJson),
                        "holidays": json.loads(row.HolidayJson) if row.HolidayJson else [],
                    }
                except (ValueError, TypeError):
                    continue
            return result
        finally:
            conn.close()

    def fetch_matched_symbols(self) -> dict[str, tuple[Any, Any]]:
        from src.configuration import RemoteSymbol

        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                SELECT SymbolID, SenSymbol, AssetType, CTraderSymbolId,
                       CTraderSymbolName, Digits, PipPosition
                FROM {quote_ident(self.schema)}.[SymbolMap]
                WHERE MappingStatus = 'MATCHED' AND Enabled = 1 AND CTraderSymbolId IS NOT NULL
                """
            )
            results: dict[str, tuple[Any, Any]] = {}
            for row in cursor.fetchall():
                local_symbol = str(row.SenSymbol).upper()
                if local_symbol not in self.targets:
                    continue
                remote = RemoteSymbol(
                    ctrader_symbol_id=int(row.CTraderSymbolId), symbol_name=str(row.CTraderSymbolName),
                    digits=int(row.Digits) if row.Digits is not None else None,
                    pip_position=int(row.PipPosition) if row.PipPosition is not None else None,
                )
                results[local_symbol] = (self.targets[local_symbol], remote)
            return results
        finally:
            conn.close()

    # -- stats / reset -------------------------------------------------

    def tick_row_stats_by_symbol(self) -> dict[str, dict[str, Any]]:
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            stats: dict[str, dict[str, Any]] = {}
            for symbol in sorted(self.allowed_symbols):
                table_name = qualified_tick_table(self.schema, symbol, allowed_symbols=self.allowed_symbols)
                cursor.execute(f"SELECT COUNT_BIG(*), MIN(TickTimeUtc), MAX(TickTimeUtc) FROM {table_name}")
                count, first_tick, last_tick = cursor.fetchone()
                stats[symbol] = {"rows": int(count or 0), "first_tick_utc": first_tick, "last_tick_utc": last_tick}
            return stats
        finally:
            conn.close()

    def read_chart_ticks(self, local_symbol: str, since_utc: datetime, limit: int) -> list[tuple[Any, ...]]:
        """Most recent `limit` ticks at/after `since_utc`, oldest first -- for
        src/chart/server.py. Read-only, no dedup/insert logic touched.

        Every tick ever ingested is kept (see scripts/sql/tickdata_setup.sql
        header for why), so this always returns full-fidelity raw ticks
        regardless of how old `since_utc` is -- no thinned/pivot-only data
        to reason about.
        """
        table_name = qualified_tick_table(self.schema, local_symbol, allowed_symbols=self.allowed_symbols)
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            since_value = since_utc.astimezone(timezone.utc).replace(tzinfo=None)
            cursor.execute(
                f"""
                SELECT TOP (?) TickTimeUtc, Bid, Ask FROM {table_name}
                WHERE TickTimeUtc >= ?
                ORDER BY TickTimeUtc DESC, TickID DESC
                """,
                (int(limit), since_value),
            )
            rows = cursor.fetchall()
            return list(reversed(rows))
        finally:
            conn.close()

    def reset_tick_data(self) -> dict[str, Any]:
        """Clear per-symbol tick rows and reset ingest state for a fresh pull."""
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            stats_before = self.tick_row_stats_by_symbol()
            truncate_used = True
            try:
                for symbol in sorted(self.allowed_symbols):
                    table_name = qualified_tick_table(self.schema, symbol, allowed_symbols=self.allowed_symbols)
                    cursor.execute(f"TRUNCATE TABLE {table_name};")
            except Exception:
                conn.rollback()
                truncate_used = False
                for symbol in sorted(self.allowed_symbols):
                    table_name = qualified_tick_table(self.schema, symbol, allowed_symbols=self.allowed_symbols)
                    cursor.execute(f"DELETE FROM {table_name};")
                    cursor.execute(f"DBCC CHECKIDENT ('{self.schema}.{symbol}', RESEED, 0) WITH NO_INFOMSGS;")

            symbol_ids = [self.targets[s].symbol_id for s in sorted(self.allowed_symbols)]
            placeholders = ", ".join("?" for _ in symbol_ids)
            cursor.execute(
                f"""
                UPDATE {quote_ident(self.schema)}.[IngestState]
                   SET LastHistoricalTickTimeUtc = NULL, LastSourceTimestampMs = NULL,
                       LastBid = NULL, LastAsk = NULL, LastWriteAtUtc = NULL,
                       TotalTicksInserted = 0, Status = 'INIT',
                       UpdatedAtUtc = SYSUTCDATETIME()
                 WHERE SymbolID IN ({placeholders})
                """,
                tuple(symbol_ids),
            )
            ingest_state_rows = cursor.rowcount if cursor.rowcount >= 0 else 0
            conn.commit()
            return {
                "symbols": stats_before,
                "total_rows": sum(int(v["rows"]) for v in stats_before.values()),
                "ingest_state_rows_reset": int(ingest_state_rows),
                "truncate_used": truncate_used,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -- columnstore maintenance -----------------------------------------

    def compress_tick_table(self, local_symbol: str) -> None:
        """Force-compress a tick table's Clustered Columnstore Index.

        Trickle inserts (every 30 min-1h, a few thousand to tens of
        thousands of rows) land far below the ~102,400-row threshold SQL
        Server uses to write directly into compressed columnstore segments,
        so they otherwise sit in an uncompressed row-mode "delta store"
        until enough of them accumulate naturally (up to 1,048,576 rows per
        rowgroup) or something forces the tuple mover to act. This is that
        force -- a plain, built-in REORGANIZE, not a custom job -- run
        periodically (see `compress-ticks` in runtime.py) so compression
        savings are realized promptly instead of waiting on natural
        rowgroup closure. Online/low-blocking; safe to run against a live,
        continuously-inserting table.
        """
        table_name = qualified_tick_table(self.schema, local_symbol, allowed_symbols=self.allowed_symbols)
        conn = self.connection_factory()
        conn.autocommit = True
        cursor = conn.cursor()
        try:
            cursor.execute(f"ALTER INDEX ALL ON {table_name} REORGANIZE WITH (COMPRESS_ALL_ROW_GROUPS = ON);")
        finally:
            conn.close()

    def refresh_dedup_window(self, local_symbol: str, retain_days: int) -> None:
        """Roll the small filtered unique-index safety net forward (2026-09).

        A filtered index's WHERE clause is a fixed value baked in at
        creation time -- SQL Server never moves it on its own, and won't
        accept a non-deterministic function (SYSUTCDATETIME(), GETDATE())
        directly in the predicate anyway, since the filter has to mean the
        same thing until the index is rebuilt. Microsoft's own filtered-
        index docs: changing the filter expression requires
        `CREATE INDEX ... WITH DROP_EXISTING`, which is exactly what this
        does, computing the new cutoff literal in Python instead. Run daily
        (see `compress-ticks`) so the index keeps covering "the last
        retain_days days" instead of just growing from whatever date it was
        first created on -- see insert_ticks()'s docstring for why this
        window only needs to be a few days wide.

        No IGNORE_DUP_KEY: SQL Server rejects that option on a filtered
        index outright (Msg 10618, confirmed against the real server before
        writing this). A genuine collision here is rare enough (it needs a
        prior transient SQL failure plus a second process re-fetching the
        exact same rows before the spool drains) that letting the whole
        INSERT fail and fall back to the existing spool-and-retry path
        (TickBatcher.flush()'s except clause) is simpler and safe -- the
        next retry's own pre-check in insert_ticks() sees the now-committed
        row and skips it, same as any other transient SQL failure.
        """
        from datetime import timedelta

        raw_index_name = f"UX_tick_{local_symbol.upper()}_RecentDedup"
        table_name = qualified_tick_table(self.schema, local_symbol, allowed_symbols=self.allowed_symbols)
        index_name = quote_ident(raw_index_name)
        dedup_cols = ", ".join(quote_ident(c) for c in _DEDUP_COLUMNS)
        cutoff_literal = (datetime.now(timezone.utc) - timedelta(days=int(retain_days))).strftime("%Y-%m-%d")
        conn = self.connection_factory()
        conn.autocommit = True
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(?) AND name = ?",
                (f"{self.schema}.{local_symbol.upper()}", raw_index_name),
            )
            drop_existing = " DROP_EXISTING = ON," if cursor.fetchone() is not None else ""
            cursor.execute(
                f"""
                CREATE UNIQUE NONCLUSTERED INDEX {index_name} ON {table_name} ({dedup_cols})
                    WHERE [TickTimeUtc] >= '{cutoff_literal}'
                    WITH ({drop_existing} DATA_COMPRESSION = PAGE);
                """
            )
        finally:
            conn.close()

    # -- IngestState ---------------------------------------------------

    def _update_ingest_state_cursor(
        self, cursor: object, target: Any, ctrader_symbol_id: int,
        last_tick_time_utc: datetime, last_source_timestamp_ms: int, status: str,
        last_bid: object | None = None, last_ask: object | None = None, ticks_inserted: int = 0,
    ) -> None:
        tick_time_value = last_tick_time_utc.astimezone(timezone.utc).replace(tzinfo=None)
        cursor.execute(
            """
                MERGE tick.[IngestState] AS tgt
                USING (SELECT ? AS SymbolID, ? AS CTraderSymbolId) AS src
                ON tgt.SymbolID = src.SymbolID
                WHEN MATCHED THEN UPDATE SET
                    CTraderSymbolId = ?,
                    LastHistoricalTickTimeUtc =
                        CASE WHEN tgt.LastHistoricalTickTimeUtc IS NULL OR ? >= tgt.LastHistoricalTickTimeUtc
                             THEN ? ELSE tgt.LastHistoricalTickTimeUtc END,
                    LastSourceTimestampMs =
                        CASE WHEN tgt.LastHistoricalTickTimeUtc IS NULL OR ? >= tgt.LastHistoricalTickTimeUtc
                             THEN ? ELSE LastSourceTimestampMs END,
                    LastBid =
                        CASE WHEN tgt.LastHistoricalTickTimeUtc IS NULL OR ? >= tgt.LastHistoricalTickTimeUtc
                             THEN COALESCE(?, LastBid) ELSE LastBid END,
                    LastAsk =
                        CASE WHEN tgt.LastHistoricalTickTimeUtc IS NULL OR ? >= tgt.LastHistoricalTickTimeUtc
                             THEN COALESCE(?, LastAsk) ELSE LastAsk END,
                    LastWriteAtUtc = SYSUTCDATETIME(),
                    TotalTicksInserted = COALESCE(TotalTicksInserted, 0) + ?,
                    Status = ?, UpdatedAtUtc = SYSUTCDATETIME()
                WHEN NOT MATCHED THEN INSERT
                    (SymbolID, CTraderSymbolId, LastHistoricalTickTimeUtc,
                     LastSourceTimestampMs, LastBid, LastAsk, LastWriteAtUtc,
                     TotalTicksInserted, Status)
                VALUES (?, ?, ?, ?, ?, ?, SYSUTCDATETIME(), ?, ?);
            """.replace("tick.[IngestState]", f"{quote_ident(self.schema)}.[IngestState]"),
            (
                target.symbol_id, int(ctrader_symbol_id),
                int(ctrader_symbol_id),
                tick_time_value, tick_time_value,
                tick_time_value, int(last_source_timestamp_ms),
                tick_time_value, last_bid,
                tick_time_value, last_ask,
                int(ticks_inserted), status,
                target.symbol_id, int(ctrader_symbol_id), tick_time_value,
                int(last_source_timestamp_ms), last_bid, last_ask,
                int(ticks_inserted), status,
            ),
        )

    def record_fetch_attempt(self, local_symbol: str, when_utc: datetime) -> None:
        """Mark that a fetch for this symbol just completed without error,
        whether or not it found any new ticks. See notify.py::run_tick_check
        for why this is tracked separately from LastHistoricalTickTimeUtc."""
        target = self.targets.get(local_symbol.upper())
        if target is None:
            return
        when_value = when_utc.astimezone(timezone.utc).replace(tzinfo=None)
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                MERGE {quote_ident(self.schema)}.[IngestState] AS tgt
                USING (SELECT ? AS SymbolID) AS src
                ON tgt.SymbolID = src.SymbolID
                WHEN MATCHED THEN UPDATE SET LastAttemptAtUtc = ?, UpdatedAtUtc = SYSUTCDATETIME()
                WHEN NOT MATCHED THEN INSERT (SymbolID, LastAttemptAtUtc)
                    VALUES (?, ?);
                """,
                (target.symbol_id, when_value, target.symbol_id, when_value),
            )
            conn.commit()
        finally:
            conn.close()

    def load_last_attempt_times(self) -> dict[str, datetime]:
        """Per-symbol LastAttemptAtUtc (last error-free fetch), keyed by local symbol."""
        symbol_by_id = {t.symbol_id: local for local, t in self.targets.items()}
        conn = self.connection_factory()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                SELECT SymbolID, LastAttemptAtUtc FROM {quote_ident(self.schema)}.[IngestState]
                WHERE LastAttemptAtUtc IS NOT NULL
                """
            )
            result: dict[str, datetime] = {}
            for symbol_id, when_value in cursor.fetchall():
                local = symbol_by_id.get(int(symbol_id))
                if local is not None:
                    result[local] = when_value.replace(tzinfo=timezone.utc) if when_value.tzinfo is None else when_value
            return result
        finally:
            conn.close()
