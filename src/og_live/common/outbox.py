"""Durable local outbox for signals that could not be published to Redis."""

from __future__ import annotations

from collections.abc import Callable
import json
import logging
import threading
from pathlib import Path
from typing import Any

from og_live.common import settings as common_settings

logger = logging.getLogger(__name__)

PublishFn = Callable[[str, dict[str, Any]], str | None]


class DeliveryOutbox:
    """Thread-safe, best-effort durable queue keyed by signal_id."""

    def __init__(self, path: Path | None = None, *, runtime_dir: Path | None = None) -> None:
        base_dir = runtime_dir or common_settings.runtime_dir("common")
        self._path = path or (base_dir / "delivery_outbox.json")
        self._lock = threading.Lock()
        self._pending: list[dict[str, Any]] = self._load()

    def has(self, signal_id: str) -> bool:
        """Return True if the signal is already queued for retry."""
        with self._lock:
            return any(item.get("payload", {}).get("signal_id") == signal_id for item in self._pending)

    def add_pending(self, strategy: str, payload: dict[str, Any]) -> bool:
        """Add one payload if it is not already queued. Never raises."""
        signal_id = str(payload.get("signal_id", ""))
        with self._lock:
            if signal_id and any(item.get("payload", {}).get("signal_id") == signal_id for item in self._pending):
                return False
            self._pending.append({"strategy": strategy, "payload": payload})
            saved = self._save_locked()
        if saved:
            logger.warning("outbox: queued signal_id=%s", signal_id)
        return saved

    def retry_all(self, publish_fn: PublishFn) -> list[dict[str, Any]]:
        """Try publishing all pending payloads and return payloads delivered in this call."""
        with self._lock:
            pending_snapshot = list(self._pending)
        if not pending_snapshot:
            return []

        delivered_items: list[dict[str, Any]] = []
        for item in pending_snapshot:
            try:
                result = publish_fn(str(item["strategy"]), item["payload"])
            except Exception:
                logger.exception("outbox: publish_fn raised for signal_id=%s", item.get("payload", {}).get("signal_id"))
                result = None
            if result is not None:
                delivered_items.append(item)

        if not delivered_items:
            return []

        with self._lock:
            for item in delivered_items:
                try:
                    self._pending.remove(item)
                except ValueError:
                    pass
            self._save_locked()
        return [item["payload"] for item in delivered_items]

    def _load(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("outbox: failed to load %s, starting empty: %s", self._path, exc)
            return []
        if not isinstance(raw, list):
            logger.warning("outbox: invalid content in %s, starting empty", self._path)
            return []
        return [item for item in raw if isinstance(item, dict) and isinstance(item.get("payload"), dict)]

    def _save_locked(self) -> bool:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._pending), encoding="utf-8")
            tmp.replace(self._path)
            return True
        except OSError as exc:
            logger.error("outbox: failed to persist %s: %s", self._path, exc)
            return False

