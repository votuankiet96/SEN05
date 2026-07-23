"""Atomic JSON state-file primitives shared by long-running processes."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core_engine.shared.time import utc_iso


@dataclass(frozen=True)
class JsonReadResult:
    """Result of a bounded attempt sequence to read an atomic JSON file."""

    data: dict[str, Any] | None
    error: Exception | None
    attempts: int

    @property
    def ok(self) -> bool:
        return self.data is not None


def read_json_snapshot(
    path: Path,
    *,
    attempts: int = 5,
    base_delay_sec: float = 0.01,
) -> JsonReadResult:
    """Read one JSON object with bounded retries for atomic-replace races.

    ``data is None`` is intentionally different from an empty, valid JSON
    object.  Lifecycle decisions must not mistake a transient Windows sharing
    error for an absent semantic field.
    """

    total_attempts = max(1, int(attempts))
    last_error: Exception | None = None
    for attempt in range(total_attempts):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"Expected a JSON object in {path}")
            return JsonReadResult(data=data, error=None, attempts=attempt + 1)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            if attempt + 1 < total_attempts:
                time.sleep(min(max(0.0, base_delay_sec) * (2**attempt), 0.1))
    return JsonReadResult(data=None, error=last_error, attempts=total_attempts)


def load_json(path: Path) -> dict[str, Any]:
    """Compatibility reader for callers that do not make lifecycle decisions."""

    return read_json_snapshot(path).data or {}


def atomic_write_json(
    path: Path,
    payload: dict[str, Any],
    *,
    indent: int | None = 2,
    attempts: int = 6,
) -> None:
    """Atomically replace a JSON file with bounded retries for Windows shares."""

    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=indent)
    last_error: OSError | None = None
    for attempt in range(attempts):
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            tmp.write_text(data, encoding="utf-8")
            tmp.replace(path)
            return
        except OSError as exc:
            last_error = exc
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt + 1 < attempts:
                time.sleep(min(0.05 * (2**attempt), 0.8))
    if last_error is not None:
        raise last_error


class RuntimeStateWriter:
    """Merge and atomically persist one process-owned runtime state document."""

    ACTIVE_STATUSES = {
        "starting",
        "running",
        "waiting",
        "batch_running",
        "batch_stale_released",
        "network_blocked",
    }
    TERMINAL_FIELDS = (
        "stopped_at",
        "stop_reason",
        "previous_pid",
        "lock_error",
        "active_pid",
        "active_started",
        "handoff_wait_seconds",
        "handoff_elapsed_seconds",
    )

    def __init__(self, path: Path, logger) -> None:
        self.path = path
        self.logger = logger
        self._lock = threading.Lock()
        self._last_warning_at = 0.0
        self._state: dict[str, Any] = {}

    def write(self, **updates: Any) -> None:
        current_pid = os.getpid()
        with self._lock:
            payload: dict[str, Any] = {
                "pid": current_pid,
                "updated_at": utc_iso(),
                "status": "running",
            }
            payload.update(self._state)
            payload.update(updates)
            status_text = str(payload.get("status") or "")
            if status_text == "stopped":
                payload["previous_pid"] = current_pid
                payload["pid"] = None
            else:
                payload["pid"] = current_pid
            payload["updated_at"] = utc_iso()
            if status_text in self.ACTIVE_STATUSES:
                for key in self.TERMINAL_FIELDS:
                    payload.pop(key, None)

            # The process owns this document.  Preserve the complete semantic
            # snapshot in memory even when one filesystem write exhausts its
            # retries; the next heartbeat will persist it without rebuilding a
            # partial state from disk.
            self._state = payload.copy()

            try:
                atomic_write_json(self.path, payload, indent=None)
                return
            except OSError as exc:
                now = time.time()
                if now - self._last_warning_at >= 60:
                    self._last_warning_at = now
                    self.logger.warning(
                        "Could not write runtime state heartbeat after retries: %s",
                        exc,
                        exc_info=True,
                    )

    def heartbeat_loop(self, stop_event: threading.Event, interval_sec: int) -> None:
        while not stop_event.wait(interval_sec):
            self.write(heartbeat="alive")
