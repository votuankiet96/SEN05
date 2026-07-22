"""Backend engine package for the refactored SEN05 data provider.

The few lines of runtime bootstrap below deliberately execute before the CLI
or worker modules are imported.  They give production processes a stable role
and capture import-time/unhandled/native Python crashes even when Task
Scheduler and the supervisor redirect their standard streams.
"""

from __future__ import annotations

import faulthandler
import os
import re
import sys
import threading
import traceback
from pathlib import Path
from typing import TextIO


def _infer_process_role() -> str:
    configured = str(os.environ.get("DP_PROCESS_ROLE") or "").strip().lower()
    if configured:
        return configured
    argv = " ".join(str(item).lower() for item in sys.argv)
    if "pytest" in argv:
        return "test"
    if "core_engine.core.live.engine" in argv or re.search(r"(?:^|\s)live(?:\s|$)", argv):
        return "live"
    if "core_engine.core.historical.engine" in argv or re.search(r"(?:^|\s)historical(?:\s|$)", argv):
        return "historical"
    if re.search(r"(?:^|\s)run(?:\s|$)", argv):
        return "supervisor"
    return "cli"


PROCESS_ROLE = _infer_process_role()
os.environ.setdefault("DP_PROCESS_ROLE", PROCESS_ROLE)


class _CrashTee:
    """Mirror Python stderr into the durable crash file."""

    def __init__(self, primary: TextIO, crash: TextIO) -> None:
        self._primary = primary
        self._crash = crash

    def write(self, value: str) -> int:
        try:
            self._crash.write(value)
            self._crash.flush()
        except Exception:
            pass
        try:
            self._primary.write(value)
            self._primary.flush()
        except Exception:
            pass
        return len(value)

    def flush(self) -> None:
        for stream in (self._crash, self._primary):
            try:
                stream.flush()
            except Exception:
                pass

    def isatty(self) -> bool:
        try:
            return bool(self._primary.isatty())
        except Exception:
            return False

    def fileno(self) -> int:
        return self._primary.fileno()

    def __getattr__(self, name: str):
        return getattr(self._primary, name)

    @property
    def encoding(self) -> str:
        return "utf-8"


_CRASH_HANDLE: TextIO | None = None


def _rotate_startup_crash_log(path: Path, *, max_bytes: int = 10 * 1024 * 1024, backups: int = 3) -> None:
    try:
        if not path.exists() or path.stat().st_size < max_bytes:
            return
        oldest = path.with_name(f"{path.name}.{backups}")
        oldest.unlink(missing_ok=True)
        for number in range(backups - 1, 0, -1):
            source = path.with_name(f"{path.name}.{number}")
            if source.exists():
                source.replace(path.with_name(f"{path.name}.{number + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))
    except OSError:
        # Opening the current file below is still worth attempting.  A crash
        # capture failure must never prevent the collector itself from starting.
        pass


def _install_crash_capture() -> None:
    global _CRASH_HANDLE
    if PROCESS_ROLE not in {"supervisor", "live", "historical"}:
        return
    try:
        override = str(os.environ.get("DP_APP_ROOT") or "").strip()
        app_root = Path(override) if override else Path(__file__).resolve().parents[2]
        target = app_root / "runtime" / "logs" / "system" / f"crash.{PROCESS_ROLE}.{os.getpid()}.log"
        target.parent.mkdir(parents=True, exist_ok=True)
        _rotate_startup_crash_log(target)
        _CRASH_HANDLE = target.open("a", encoding="utf-8", buffering=1)
        if PROCESS_ROLE == "supervisor":
            # Task Scheduler does not preserve stderr. Child stderr is instead
            # pumped by the supervisor into a bounded per-child file.
            sys.stderr = _CrashTee(sys.stderr, _CRASH_HANDLE)  # type: ignore[assignment]
        faulthandler.enable(file=_CRASH_HANDLE, all_threads=True)

        # The supervisor's original hooks already write through _CrashTee.
        # Child stderr is piped separately, so give child processes explicit
        # hooks that also persist normal unhandled Python exceptions here.
        if PROCESS_ROLE != "supervisor":
            original_excepthook = sys.excepthook

            def _unhandled(exc_type, exc_value, exc_traceback) -> None:
                try:
                    traceback.print_exception(exc_type, exc_value, exc_traceback, file=_CRASH_HANDLE)
                    _CRASH_HANDLE.flush()
                except Exception:
                    pass
                original_excepthook(exc_type, exc_value, exc_traceback)

            sys.excepthook = _unhandled
            if hasattr(threading, "excepthook"):
                original_threading_hook = threading.excepthook

                def _thread_unhandled(args) -> None:
                    try:
                        traceback.print_exception(
                            args.exc_type,
                            args.exc_value,
                            args.exc_traceback,
                            file=_CRASH_HANDLE,
                        )
                        _CRASH_HANDLE.flush()
                    except Exception:
                        pass
                    original_threading_hook(args)

                threading.excepthook = _thread_unhandled
    except Exception:
        _CRASH_HANDLE = None


_install_crash_capture()


__all__ = ["PROCESS_ROLE"]
