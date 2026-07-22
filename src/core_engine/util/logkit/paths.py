"""Process-aware paths for log files written by more than one process.

Windows cannot reliably rotate a file while another process still has it
open.  DP Program therefore gives every long-lived process its own physical
file for cross-cutting streams such as ``activity`` and ``errors``.
"""

from __future__ import annotations

import os
import re
import json
import threading
from datetime import datetime, timezone
from pathlib import Path


_SCOPED_ROLES = {"supervisor", "live", "historical"}
_REGISTRY_LOCK = threading.Lock()


def process_role() -> str:
    """Return the stable role assigned before component modules are imported."""
    raw = str(os.environ.get("DP_PROCESS_ROLE") or "").strip().lower()
    return re.sub(r"[^a-z0-9_-]+", "_", raw).strip("_") or "unknown"


def process_scoped_log_path(path: str | os.PathLike[str]) -> Path:
    """Add the production process role before the final suffix.

    CLI and test processes retain the canonical path so diagnostics and unit
    tests do not create a new file for every short-lived invocation.
    """
    original = Path(path)
    role = process_role()
    if role not in _SCOPED_ROLES:
        return original
    suffix = original.suffix or ".log"
    return original.with_name(f"{original.stem}.{role}.{os.getpid()}{suffix}")


def _app_root() -> Path:
    override = str(os.environ.get("DP_APP_ROOT") or "").strip()
    return Path(override) if override else Path(__file__).resolve().parents[4]


def log_sink_registry_path(*, role: str | None = None, pid: int | None = None) -> Path:
    actual_role = role or process_role()
    actual_pid = os.getpid() if pid is None else int(pid)
    return _app_root() / "runtime" / "run" / "log_sinks" / f"{actual_role}.{actual_pid}.json"


def register_log_sink(
    physical_path: str | os.PathLike[str],
    *,
    logical_path: str | os.PathLike[str] | None = None,
    kind: str = "rotating",
) -> bool:
    """Publish one process-owned sink for doctor/operator discovery."""
    role = process_role()
    if role not in _SCOPED_ROLES:
        return True
    target = log_sink_registry_path(role=role, pid=os.getpid())
    physical = str(Path(physical_path).resolve())
    logical = str(Path(logical_path or physical_path).resolve())
    try:
        with _REGISTRY_LOCK:
            payload: dict = {}
            if target.exists():
                try:
                    loaded = json.loads(target.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        payload = loaded
                except Exception:
                    payload = {}
            sinks = payload.get("sinks") if isinstance(payload.get("sinks"), list) else []
            sinks = [item for item in sinks if str(item.get("physical_path")) != physical]
            sinks.append(
                {
                    "logical_path": logical,
                    "physical_path": physical,
                    "kind": kind,
                    "registered_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            payload = {
                "role": role,
                "pid": os.getpid(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "sinks": sinks,
            }
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(
                f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            temp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
            os.replace(temp, target)
        return True
    except Exception:
        return False


def unregister_log_sink(physical_path: str | os.PathLike[str]) -> bool:
    """Remove a short-lived sink (for example, a finished child stderr pump)."""
    role = process_role()
    if role not in _SCOPED_ROLES:
        return True
    target = log_sink_registry_path(role=role, pid=os.getpid())
    physical = str(Path(physical_path).resolve())
    try:
        with _REGISTRY_LOCK:
            if not target.exists():
                return True
            payload = json.loads(target.read_text(encoding="utf-8"))
            sinks = payload.get("sinks") if isinstance(payload, dict) else []
            payload["sinks"] = [
                item for item in sinks if str(item.get("physical_path")) != physical
            ]
            payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            temp = target.with_name(
                f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            temp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
            os.replace(temp, target)
        return True
    except Exception:
        return False


__all__ = [
    "log_sink_registry_path",
    "process_role",
    "process_scoped_log_path",
    "register_log_sink",
    "unregister_log_sink",
]
