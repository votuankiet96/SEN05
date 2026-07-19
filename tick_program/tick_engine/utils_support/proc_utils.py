"""Cross-platform process liveness and termination helpers."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Iterable


def is_pid_alive(pid: int) -> bool:
    """Return True if a process with this PID is currently running."""
    if not pid or pid <= 0:
        return False

    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def process_command_line(pid: int) -> str | None:
    """Return the command line for a running process, or None if unavailable."""
    if not is_pid_alive(pid):
        return None

    if os.name == "nt":
        ps = (
            '$p=Get-CimInstance Win32_Process -Filter "ProcessId=%d";'
            "if($p){$p.CommandLine}"
        ) % int(pid)
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=3.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return (completed.stdout or "").strip() or None

    try:
        raw = open(f"/proc/{int(pid)}/cmdline", "rb").read()
    except OSError:
        return None
    return raw.replace(b"\x00", b" ").decode(errors="replace").strip() or None


def pid_command_contains(pid: int, markers: Iterable[str]) -> bool:
    cmd = process_command_line(pid)
    if not cmd:
        return False
    haystack = cmd.lower()
    return any(marker.lower() in haystack for marker in markers)


def is_tick_engine_process(pid: int) -> bool:
    """Return True when pid appears to belong to tick_engine."""
    return pid_command_contains(pid, ("tick_engine", "tick_program"))


def terminate_pid(pid: int, timeout: float = 10.0) -> bool:
    """Terminate a process (and its children) and wait up to timeout seconds."""
    if not is_pid_alive(pid):
        return True

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=max(1.0, timeout),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            return True
        time.sleep(0.3)

    if os.name == "nt" and is_pid_alive(pid):
        _terminate_windows_process(pid, timeout=3.0)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if not is_pid_alive(pid):
                return True
            time.sleep(0.2)

    if os.name != "nt" and is_pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        time.sleep(0.5)

    return not is_pid_alive(pid)


def _terminate_windows_process(pid: int, timeout: float = 3.0) -> bool:
    if os.name != "nt" or not pid or pid <= 0:
        return False
    import ctypes

    PROCESS_TERMINATE = 0x0001
    SYNCHRONIZE = 0x00100000
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    WAIT_OBJECT_0 = 0

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(
        PROCESS_TERMINATE | SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        int(pid),
    )
    if not handle:
        return False
    try:
        kernel32.TerminateProcess(handle, 1)
        wait_ms = max(0, int(timeout * 1000))
        kernel32.WaitForSingleObject(handle, wait_ms)
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)
