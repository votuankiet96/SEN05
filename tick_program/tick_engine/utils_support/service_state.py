"""Runtime heartbeat and batch-progress state helpers."""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc_text(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def seconds_since(value: object, now: datetime | None = None) -> int | None:
    dt = parse_utc_text(value)
    if dt is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0, int((now - dt).total_seconds()))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


def write_service_heartbeat(payload: dict[str, Any]) -> None:
    from tick_engine.settings import SERVICE_HEARTBEAT

    data = dict(payload)
    data["updated_at_utc"] = utc_now_text()
    data.setdefault("host", socket.gethostname())
    data.setdefault("process_id", os.getpid())
    _write_json(SERVICE_HEARTBEAT, data)


def read_service_heartbeat() -> dict[str, Any] | None:
    from tick_engine.settings import SERVICE_HEARTBEAT

    try:
        return json.loads(SERVICE_HEARTBEAT.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        return {"error": str(exc), "path": str(SERVICE_HEARTBEAT)}


def service_heartbeat_status(stale_seconds: int | None = None) -> dict[str, Any]:
    from tick_engine.settings import SERVICE_HEARTBEAT, TICK_SERVICE_HEARTBEAT_STALE_SECONDS

    payload = read_service_heartbeat()
    if not payload:
        return {
            "exists": False,
            "path": str(SERVICE_HEARTBEAT),
            "age_seconds": None,
            "stale": True,
        }
    age = seconds_since(payload.get("updated_at_utc"))
    limit = int(stale_seconds or TICK_SERVICE_HEARTBEAT_STALE_SECONDS)
    return {
        **payload,
        "exists": True,
        "path": str(SERVICE_HEARTBEAT),
        "age_seconds": age,
        "stale": age is None or age > limit,
        "stale_seconds": limit,
    }


def _load_progress(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _progress_owner_alive(path: Path, pid: int) -> bool:
    """Return True only when pid still owns this specific progress file."""
    if pid <= 0:
        return False
    from tick_engine.utils_support.proc_utils import process_command_line

    cmd = process_command_line(pid)
    if not cmd:
        return False
    lower_cmd = cmd.lower()
    if "tick_engine" not in lower_cmd and "tick_program" not in lower_cmd:
        return False
    return path.name.lower() in lower_cmd


def scan_backfill_progress(*, prefix: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    from tick_engine.settings import RUN_DIR, TICK_SCHEDULED_PROGRESS_STALE_SECONDS

    progress_dir = RUN_DIR / "backfill_batches"
    if not progress_dir.exists():
        return []
    now = datetime.now(timezone.utc)
    local_host = socket.gethostname().lower()
    paths = sorted(progress_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    items: list[dict[str, Any]] = []
    for path in paths:
        if prefix and not path.name.startswith(prefix):
            continue
        payload = _load_progress(path)
        if payload is None:
            continue
        batches = payload.get("batches") or []
        running_batches = [
            batch for batch in batches
            if isinstance(batch, dict) and batch.get("status") == "RUNNING"
        ]
        current = running_batches[-1] if running_batches else (batches[-1] if batches else None)
        updated_at = payload.get("updated_at_utc")
        age = seconds_since(updated_at, now)
        host = str(payload.get("host") or "")
        pid_raw = payload.get("process_id")
        try:
            pid = int(pid_raw) if pid_raw is not None else 0
        except (TypeError, ValueError):
            pid = 0
        local = bool(host and host.lower() == local_host)
        pid_alive = bool(local and _progress_owner_alive(path, pid))
        is_running = payload.get("status") == "RUNNING"
        stale = bool(
            is_running
            and age is not None
            and age > int(TICK_SCHEDULED_PROGRESS_STALE_SECONDS)
            and (not local or not pid_alive)
        )
        items.append(
            {
                "name": path.name,
                "path": str(path),
                "status": payload.get("status"),
                "display_status": "STALE_METADATA" if stale else payload.get("status"),
                "stale_progress": stale,
                "updated_at_utc": updated_at,
                "updated_age_seconds": age,
                "created_at_utc": payload.get("created_at_utc"),
                "host": host,
                "process_id": pid or None,
                "local_host": local,
                "process_alive": pid_alive,
                "from_utc": payload.get("from_utc"),
                "to_utc": payload.get("to_utc"),
                "batch_minutes": payload.get("batch_minutes"),
                "total_batches": len(batches) if isinstance(batches, list) else 0,
                "current_batch": current if isinstance(current, dict) else None,
            }
        )
        if len(items) >= limit:
            break
    return items


def mark_stale_backfill_progress(
    max_age_seconds: int,
    *,
    prefixes: tuple[str, ...] = ("scheduled_", "manual_backfill_"),
) -> int:
    """Mark local RUNNING batch progress files stale after the owner died."""
    from tick_engine.settings import RUN_DIR

    progress_dir = RUN_DIR / "backfill_batches"
    if not progress_dir.exists():
        return 0
    now = datetime.now(timezone.utc)
    local_host = socket.gethostname().lower()
    updated = 0
    for path in progress_dir.glob("*.json"):
        if prefixes and not any(path.name.startswith(prefix) for prefix in prefixes):
            continue
        payload = _load_progress(path)
        if not payload or payload.get("status") != "RUNNING":
            continue
        host = str(payload.get("host") or "")
        if host.lower() != local_host:
            continue
        age = seconds_since(payload.get("updated_at_utc"), now)
        if age is None or age < int(max_age_seconds):
            continue
        try:
            pid = int(payload.get("process_id") or 0)
        except (TypeError, ValueError):
            pid = 0
        if _progress_owner_alive(path, pid):
            continue
        payload["status"] = "STALE"
        payload["exit_code"] = 1
        payload["stale_detected_at_utc"] = utc_now_text()
        payload["stale_reason"] = "batch progress was left RUNNING by a dead process"
        for batch in payload.get("batches") or []:
            if isinstance(batch, dict) and batch.get("status") == "RUNNING":
                batch["status"] = "STALE"
                batch["exit_code"] = 1
                batch.setdefault("finished_at_utc", payload["stale_detected_at_utc"])
        _write_json(path, payload)
        updated += 1
    return updated


def mark_stale_scheduled_progress(max_age_seconds: int) -> int:
    """Mark local scheduled RUNNING progress files stale after the owner died."""
    return mark_stale_backfill_progress(max_age_seconds, prefixes=("scheduled_",))
