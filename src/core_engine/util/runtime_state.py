"""Atomic JSON state-file primitives shared by long-running processes."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from core_engine.shared.time import utc_iso


def load_json(path: Path) -> dict[str, Any]:
    """Read a JSON object, returning an empty mapping for absent/corrupt data."""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


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

    def write(self, **updates: Any) -> None:
        current_pid = os.getpid()
        with self._lock:
            payload: dict[str, Any] = {
                "pid": current_pid,
                "updated_at": utc_iso(),
                "status": "running",
            }
            existing = load_json(self.path)
            if int(existing.get("pid") or 0) == current_pid:
                payload.update(existing)

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
