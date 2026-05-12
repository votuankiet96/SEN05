"""Pending signal queue with JSON file persistence for cBot dispatch.

Two processes share this queue:
  signal_watcher.py  — writer (push)
  signal_server.py   — reader + acker (get_pending / ack)

Persistence: core_python/runtime/pending_signals.json
Concurrency: threading.Lock for in-process safety, atomic file replace for
             cross-process safety.

Signal lifecycle:
  push()        → signal enters queue (dedup by state_key hash)
  get_pending() → returns unacked, non-expired signals
  ack(id)       → signal consumed by cBot, will not be returned again
  cleanup()     → removes old acked signals to keep file small
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

_DEFAULT_QUEUE_PATH = Path(__file__).parent.parent / "runtime" / "pending_signals.json"
_DEFAULT_TTL_SECONDS = 2 * 3600       # signals expire after 2 hours
_CLEANUP_AGE_SECONDS = 24 * 3600      # remove acked signals after 24 hours


def _state_key_to_id(state_key: str) -> str:
    """Derive a short, URL-safe, deterministic ID from the state key."""
    return hashlib.sha256(state_key.encode("utf-8")).hexdigest()[:16]


class PendingSignalQueue:
    def __init__(
        self,
        path: Path | str | None = None,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        self.path = Path(path) if path else _DEFAULT_QUEUE_PATH
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push(
        self,
        *,
        state_key: str,
        symbol: str,
        side: str,
        entry_ref: float | None,
        sl_price: float | None,
        tp_price: float | None,
        bar_time: str,
    ) -> str | None:
        """Add a signal to the queue.

        Returns the signal id if added, None if already present (duplicate).
        state_key is the AI Trend dedup key used in state.json.
        """
        signal_id = _state_key_to_id(state_key)
        with self._lock:
            signals = self._load()
            existing = {s["id"] for s in signals}
            if signal_id in existing:
                logger.debug("signal_queue: duplicate skipped %s", signal_id)
                return None
            now = _utc_now_iso()
            signals.append(
                {
                    "id": signal_id,
                    "state_key": state_key,
                    "symbol": symbol.upper(),
                    "side": side.upper(),
                    "entry_ref": entry_ref,
                    "sl_price": sl_price,
                    "tp_price": tp_price,
                    "bar_time": bar_time,
                    "created_at": now,
                    "acked": False,
                    "acked_at": None,
                }
            )
            self._save(signals)
        logger.info("signal_queue: pushed %s %s %s id=%s", symbol, side, bar_time, signal_id)
        return signal_id

    def get_pending(self) -> list[dict]:
        """Return unacked, non-expired signals ordered by created_at."""
        with self._lock:
            signals = self._load()
        cutoff = _utc_now() - pd.Timedelta(seconds=self.ttl_seconds)
        result = []
        for s in signals:
            if s.get("acked"):
                continue
            created = _parse_ts(s.get("created_at"))
            if created is not None and created < cutoff:
                continue
            result.append(dict(s))
        result.sort(key=lambda x: x.get("created_at", ""))
        return result

    def ack(self, signal_id: str) -> bool:
        """Mark a signal as consumed by the cBot.

        Returns True if found and acked, False if not found or already acked.
        """
        with self._lock:
            signals = self._load()
            found = False
            for s in signals:
                if s["id"] == signal_id and not s.get("acked"):
                    s["acked"] = True
                    s["acked_at"] = _utc_now_iso()
                    found = True
                    break
            if found:
                self._save(signals)
        if found:
            logger.info("signal_queue: acked %s", signal_id)
        return found

    def cleanup(self) -> int:
        """Remove stale entries to keep the file small.

        Removes two categories:
          - Acked signals older than _CLEANUP_AGE_SECONDS (24 h)
          - Unacked signals that have already expired (older than ttl_seconds)
            These will never be served to the cBot again, so keeping them is wasteful.

        Returns count of removed entries.
        """
        now = _utc_now()
        cutoff_acked   = now - pd.Timedelta(seconds=_CLEANUP_AGE_SECONDS)
        cutoff_expired = now - pd.Timedelta(seconds=self.ttl_seconds)
        with self._lock:
            signals = self._load()
            before = len(signals)
            kept = []
            for s in signals:
                if s.get("acked"):
                    acked_at = _parse_ts(s.get("acked_at"))
                    if acked_at is not None and acked_at >= cutoff_acked:
                        kept.append(s)
                    # else: old acked → drop
                else:
                    created = _parse_ts(s.get("created_at"))
                    if created is None or created >= cutoff_expired:
                        kept.append(s)
                    # else: unacked but expired → will never be served → drop
            removed = before - len(kept)
            if removed > 0:
                self._save(kept)
        if removed:
            logger.info("signal_queue: cleaned %d stale entries", removed)
        return removed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            return data.get("signals", [])
        except Exception as exc:
            logger.warning("signal_queue: load error %s", exc)
            return []

    def _save(self, signals: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"signals": signals}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self.path)


# ------------------------------------------------------------------
# Timestamp helpers
# ------------------------------------------------------------------

def _utc_now() -> pd.Timestamp:
    return pd.Timestamp.now("UTC")


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _parse_ts(value: Any) -> pd.Timestamp | None:
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts
    except Exception:
        return None
