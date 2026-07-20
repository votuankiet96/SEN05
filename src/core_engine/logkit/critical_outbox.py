"""Durable outbox for CRITICAL-level alerts.

Round-2 audit finding (both reviews, independently): CriticalDiscordHandler
used to call reporting.discord.send_alert() - which is fire-and-forget (it
starts a background thread and returns None, with no way for the caller to
know whether the HTTP POST ever actually succeeded) - inside a bare
`except Exception: pass`. If the webhook was down, misconfigured, or the
delivery thread died, the CRITICAL alert vanished with no trace and no
fallback channel at all: Discord is the only outward-facing alert channel
this system has.

This module gives CRITICAL alerts their own small, independently
verifiable delivery path: persist to SQLite BEFORE attempting delivery,
send synchronously so a real HTTP status code can be checked, ack (delete)
only on 200/204, and otherwise leave the row pending for the next drain
pass. critical_undelivered.log is updated on every attempt so `doctor`/
health can see the pending backlog age and the last confirmed delivery
time even if nothing is watching Discord itself.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from core_engine.tls import ensure_system_truststore

DEFAULT_TIMEOUT_CONNECT_SEC = 5.0
DEFAULT_TIMEOUT_READ_SEC = 10.0


class CriticalAlertOutbox:
    def __init__(self, db_path: Path, status_log_path: Path) -> None:
        self.db_path = db_path
        self.status_log_path = status_log_path
        self._lock = threading.Lock()
        self._last_success_at: str | None = None

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with closing(sqlite3.connect(self.db_path)) as con:
                con.execute(
                    """
                    CREATE TABLE IF NOT EXISTS critical_alerts (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        message    TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT (datetime('now')),
                        attempts   INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT
                    )
                    """
                )
                con.commit()

    def persist(self, message: str) -> int | None:
        try:
            with self._lock:
                with closing(sqlite3.connect(self.db_path)) as con:
                    cur = con.execute(
                        "INSERT INTO critical_alerts (message) VALUES (?)", (message,)
                    )
                    con.commit()
                    return int(cur.lastrowid)
        except Exception:
            return None

    def ack(self, row_id: int) -> None:
        try:
            with self._lock:
                with closing(sqlite3.connect(self.db_path)) as con:
                    con.execute("DELETE FROM critical_alerts WHERE id=?", (row_id,))
                    con.commit()
        except Exception:
            pass

    def mark_failed(self, row_id: int, error: str) -> None:
        try:
            with self._lock:
                with closing(sqlite3.connect(self.db_path)) as con:
                    con.execute(
                        "UPDATE critical_alerts SET attempts = attempts + 1, last_error = ? WHERE id = ?",
                        (error[:500], row_id),
                    )
                    con.commit()
        except Exception:
            pass

    def pending(self) -> list[tuple[int, str, str, int]]:
        try:
            with self._lock:
                with closing(sqlite3.connect(self.db_path)) as con:
                    return con.execute(
                        "SELECT id, message, created_at, attempts FROM critical_alerts ORDER BY id"
                    ).fetchall()
        except Exception:
            return []

    def send_one(self, message: str) -> bool:
        """Synchronous webhook POST so the caller gets a real success/
        failure signal - deliberately NOT routed through
        reporting.discord.send_alert(), which is fire-and-forget by
        design for the normal notification paths."""
        try:
            from core_engine.settings import NOTIFICATION
        except Exception:
            return False
        webhook_url = getattr(NOTIFICATION, "discord_webhook_url", "")
        if not webhook_url:
            # No webhook configured at all - nothing to retry toward, but
            # this is a configuration fact, not a transient failure; the
            # caller still records it as undelivered so doctor can surface
            # "critical alerts have nowhere to go" rather than pretending
            # delivery succeeded.
            return False
        # Do not rely on TradingView auth being imported first to activate
        # Windows' certificate store. CRITICAL startup alerts can be the
        # first outbound HTTP request made by the process.
        ensure_system_truststore()
        try:
            import requests

            resp = requests.post(
                webhook_url,
                json={"content": message[:1900]},
                timeout=(DEFAULT_TIMEOUT_CONNECT_SEC, DEFAULT_TIMEOUT_READ_SEC),
                verify=True,
            )
            return resp.status_code in (200, 204)
        except Exception:
            return False

    def record_and_send(self, message: str) -> bool:
        row_id = self.persist(message)
        ok = self.send_one(message)
        if ok and row_id is not None:
            self.ack(row_id)
        elif row_id is not None:
            self.mark_failed(row_id, "delivery failed")
        if ok:
            self._last_success_at = _utc_now_iso()
        self._write_status()
        return ok

    def drain(self, *, limit: int = 20) -> int:
        """Retry every pending row once. Intended to be called
        periodically (e.g. from the supervisor loop) so a Discord outage
        self-heals once it recovers instead of requiring a new CRITICAL
        log record to trigger the next attempt."""
        sent = 0
        for row_id, message, _created_at, _attempts in self.pending()[:limit]:
            if self.send_one(message):
                self.ack(row_id)
                sent += 1
                self._last_success_at = _utc_now_iso()
            else:
                self.mark_failed(row_id, "retry failed")
        self._write_status()
        return sent

    def status(self) -> dict[str, Any]:
        rows = self.pending()
        oldest_age = None
        if rows:
            oldest_age = _age_seconds(rows[0][2])
        return {
            "pending_count": len(rows),
            "oldest_pending_age_seconds": oldest_age,
            "last_success_at": self._last_success_at,
        }

    def _write_status(self) -> None:
        try:
            self.status_log_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"updated_at": _utc_now_iso(), **self.status()}
            with self.status_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True) + "\n")
        except Exception:
            pass


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _age_seconds(sqlite_datetime: str) -> float | None:
    try:
        parsed = time.strptime(sqlite_datetime, "%Y-%m-%d %H:%M:%S")
        return max(0.0, time.mktime(time.gmtime()) - time.mktime(parsed))
    except Exception:
        return None


_OUTBOX: CriticalAlertOutbox | None = None


def critical_alert_outbox() -> CriticalAlertOutbox:
    global _OUTBOX
    if _OUTBOX is None:
        from core_engine.settings import SYSTEM_LOG_DIR, CACHE_DIR

        outbox = CriticalAlertOutbox(
            db_path=CACHE_DIR / "critical_alerts_outbox.db",
            status_log_path=SYSTEM_LOG_DIR / "critical_undelivered.log",
        )
        outbox.init()
        _OUTBOX = outbox
    return _OUTBOX
