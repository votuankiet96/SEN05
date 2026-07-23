"""Crash capture that works before ordinary component loggers exist."""

from __future__ import annotations

import atexit
import faulthandler
import os
import re
import sys
import threading
import traceback
from pathlib import Path
from typing import TextIO

_INSTALLED = False
_CRASH_HANDLE: TextIO | None = None
_CRASH_PATH: Path | None = None


def infer_process_role() -> str:
    configured = str(os.environ.get("DP_PROCESS_ROLE") or "").strip().lower()
    if configured:
        return configured
    argv = " ".join(str(item).lower() for item in sys.argv)
    if "pytest" in argv:
        return "test"
    if "core_engine.core.live.engine" in argv or re.search(r"(?:^|\s)live(?:\s|$)", argv):
        return "live"
    if "core_engine.core.historical.engine" in argv or re.search(
        r"(?:^|\s)historical(?:\s|$)", argv
    ):
        return "historical"
    if re.search(r"(?:^|\s)run(?:\s|$)", argv):
        return "supervisor"
    return "cli"


def _app_root() -> Path:
    override = str(os.environ.get("DP_APP_ROOT") or "").strip()
    return Path(override) if override else Path(__file__).resolve().parents[4]


def _emergency_dir() -> Path:
    return _app_root() / "runtime" / "run" / "log_emergency"


def _close_current_capture() -> None:
    global _CRASH_HANDLE
    handle = _CRASH_HANDLE
    _CRASH_HANDLE = None
    if handle is not None:
        try:
            faulthandler.disable()
        except Exception:
            pass
        try:
            handle.flush()
            handle.close()
        except Exception:
            pass
    path = _CRASH_PATH
    if path is not None:
        try:
            if path.exists() and path.stat().st_size == 0:
                path.unlink()
        except Exception:
            pass


def install_crash_capture() -> str:
    """Install one idempotent early-crash sink and return the process role."""
    global _INSTALLED, _CRASH_HANDLE, _CRASH_PATH
    role = infer_process_role()
    os.environ.setdefault("DP_PROCESS_ROLE", role)
    if _INSTALLED or role not in {"supervisor", "live", "historical"}:
        return role
    _INSTALLED = True
    try:
        target_dir = _emergency_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"crash.{role}.{os.getpid()}.log"
        handle = target.open("a", encoding="utf-8", buffering=1)
        _CRASH_HANDLE = handle
        _CRASH_PATH = target
        faulthandler.enable(file=handle, all_threads=True)

        def _write_exception(exc_type, exc_value, exc_traceback) -> None:
            try:
                traceback.print_exception(exc_type, exc_value, exc_traceback, file=handle)
                handle.flush()
                os.fsync(handle.fileno())
            except Exception:
                pass

        def _unhandled(exc_type, exc_value, exc_traceback) -> None:
            _write_exception(exc_type, exc_value, exc_traceback)

        def _thread_unhandled(args) -> None:
            _write_exception(args.exc_type, args.exc_value, args.exc_traceback)

        sys.excepthook = _unhandled
        if hasattr(threading, "excepthook"):
            threading.excepthook = _thread_unhandled
        atexit.register(_close_current_capture)
    except Exception:
        _CRASH_HANDLE = None
        _CRASH_PATH = None
    return role


def ingest_emergency_crashes(*, include_current: bool = False) -> int:
    """Move completed crash artifacts into ``system.log`` and delete them."""
    directory = _emergency_dir()
    if not directory.exists():
        return 0
    current = _CRASH_PATH.resolve() if _CRASH_PATH is not None else None
    ingested = 0
    from core_engine.util.logkit.core import get_logger, log_event

    logger = get_logger("crash_capture", stream="system", console=False)
    for path in sorted(directory.glob("crash.*.log")):
        try:
            if not include_current and current is not None and path.resolve() == current:
                continue
            parts = path.stem.split(".")
            role = parts[1] if len(parts) > 1 else "unknown"
            pid = parts[2] if len(parts) > 2 else "unknown"
            if not include_current:
                try:
                    from core_engine.util.coordination.locks import local_pid_alive

                    if local_pid_alive(int(pid)):
                        continue
                except (TypeError, ValueError):
                    pass
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                path.unlink(missing_ok=True)
                continue
            log_event(
                logger,
                50,
                "system.process.crashed",
                f"An unhandled {role} process crash was recovered after restart",
                area="SYSTEM",
                stage="CRITICAL",
                result="ACTION REQUIRED",
                crashed_role=role,
                crashed_pid=pid,
                traceback=text,
            )
            path.unlink(missing_ok=True)
            ingested += 1
        except Exception:
            continue
    return ingested


__all__ = ["infer_process_role", "ingest_emergency_crashes", "install_crash_capture"]
