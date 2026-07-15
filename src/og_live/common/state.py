"""Local delivered-signal and processed-snapshot state for OG Live mechanisms."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from og_live.common import settings as common_settings

logger = logging.getLogger(__name__)

TTL_SECONDS = 14 * 24 * 3600


class SignalState:
    """Thread-safe, best-effort durable set of delivered signal IDs."""

    def __init__(self, path: Path | None = None, *, runtime_dir: Path | None = None) -> None:
        base_dir = runtime_dir or common_settings.runtime_dir("common")
        self._path = path or (base_dir / "state.json")
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
            raw = json.loads(self._path.read_text(encoding="utf-8"))
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


class ProcessedSnapshotState(SignalState):
    """Local durable set of DP snapshot versions already handled by one mechanism."""

    def __init__(self, path: Path | None = None, *, runtime_dir: Path | None = None) -> None:
        base_dir = runtime_dir or common_settings.runtime_dir("common")
        super().__init__(path or (base_dir / "processed_snapshots.json"))

    def processed(self, snapshot_key: str) -> bool:
        """Return True when this strategy/snapshot combination was already handled."""
        return self.seen(snapshot_key)

    def mark_processed(self, snapshot_key: str) -> None:
        """Mark one strategy/snapshot combination as handled."""
        self.mark_delivered(snapshot_key)

