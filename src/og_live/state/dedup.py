"""Local delivered-signal state for OG live deduplication."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from og_live.settings import runtime_dir

logger = logging.getLogger(__name__)

TTL_SECONDS = 14 * 24 * 3600


class SignalState:
    """Thread-safe, best-effort durable set of delivered signal IDs."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (runtime_dir() / "state.json")
        self._lock = threading.Lock()
        self._seen: dict[str, float] = self._load()

    def seen(self, signal_id: str) -> bool:
        """Return True when a signal ID has already been delivered."""
        with self._lock:
            return signal_id in self._seen

    def mark_delivered(self, signal_id: str) -> None:
        """Mark a signal as delivered. Disk persistence failures are logged, not raised."""
        with self._lock:
            self._seen[signal_id] = time.time()
            self._prune_locked()
            self._save_locked()

    def _load(self) -> dict[str, float]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("state: failed to load %s, starting empty: %s", self._path, exc)
            return {}
        if not isinstance(raw, dict):
            logger.warning("state: invalid content in %s, starting empty", self._path)
            return {}
        cutoff = time.time() - TTL_SECONDS
        seen: dict[str, float] = {}
        for signal_id, seen_at in raw.items():
            try:
                seen_at_float = float(seen_at)
            except (TypeError, ValueError):
                logger.warning("state: invalid seen_at for signal_id=%s, dropping entry", signal_id)
                continue
            if seen_at_float >= cutoff:
                seen[str(signal_id)] = seen_at_float
        return seen

    def _prune_locked(self) -> None:
        cutoff = time.time() - TTL_SECONDS
        self._seen = {signal_id: seen_at for signal_id, seen_at in self._seen.items() if seen_at >= cutoff}

    def _save_locked(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._seen), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:
            logger.error("state: failed to persist %s: %s", self._path, exc)
