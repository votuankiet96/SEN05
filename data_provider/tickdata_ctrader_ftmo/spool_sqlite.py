"""Local SQLite spool for ticks that could not be written to SQL Server."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .models import TickRecord


class TickSpool:
    """Durable local overflow queue for 24/7 tick ingest."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tick_spool (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_hash BLOB NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def append_many(self, records: list[TickRecord]) -> int:
        if not records:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                sqlite3.Binary(record.event_hash or b""),
                json.dumps(record.to_json_dict(), separators=(",", ":"), sort_keys=True),
                now,
            )
            for record in records
        ]
        with closing(self._connect()) as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT OR IGNORE INTO tick_spool
                    (event_hash, payload_json, created_at_utc)
                VALUES (?, ?, ?)
                """,
                rows,
            )
            conn.commit()
            return cursor.rowcount

    def read_batch(self, limit: int) -> list[tuple[int, TickRecord]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT seq, payload_json
                FROM tick_spool
                ORDER BY seq
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [(int(seq), TickRecord.from_json_dict(json.loads(payload))) for seq, payload in rows]

    def delete_through(self, seq: int) -> int:
        with closing(self._connect()) as conn:
            cursor = conn.execute("DELETE FROM tick_spool WHERE seq <= ?", (int(seq),))
            conn.commit()
            return cursor.rowcount

    def count(self) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COUNT(*) FROM tick_spool").fetchone()
        return int(row[0] if row else 0)
