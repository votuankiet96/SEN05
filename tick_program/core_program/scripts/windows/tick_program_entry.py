"""PyInstaller entry point for the standalone, config-driven tick_program.exe.

Launch modes (decided from argv / how it's invoked):
  - Known CLI subcommand as the first arg (e.g. "backfill", "check",
    "service", "symbol-sync", ...): dispatched straight to
    src.__main__.main(). This is what lets the frozen exe re-invoke ITSELF
    for tick_program's existing subprocess-per-batch/per-job architecture
    (src/runtime.py::job_command, src/backfill.py::_run_batch_subprocess)
    with zero change to that already-proven design -- see those two
    functions for the frozen-vs-dev command-line shape difference (a
    frozen exe IS the program, not a python.exe to hand `-B -m src` to).
  - No arguments, non-interactive (Task Scheduler, no attached console):
    same as `tick_program.exe service` -- runs the 24/7 supervisor.
  - No arguments, real interactive console (double-click, or an open
    terminal): shows the operator menu (tick_program_menu.py).
  - "--watchdog": one-shot check of the supervisor's OWN liveness
    (heartbeat file freshness + PID alive) -- deliberately independent of
    the `check` CLI command, which verifies DATA freshness via SQL and is
    itself a job the supervisor's own scheduler spawns. `check` is useless
    as a canary for "is the supervisor process itself dead", because
    nothing spawns `check` anymore once the supervisor is gone. Exits 0 if
    healthy, 1 otherwise; sends one de-duplicated Discord alert when it
    turns unhealthy. Meant for a SECOND, independent Scheduled Task that
    keeps running every few minutes even when the main service task is
    completely dead -- closes a known gap where a clean-but-nonzero
    service exit is never restarted by Task Scheduler's own
    restart-on-failure policy (see the 2026-08-14 incident in project
    memory / docs history: a crashed service silently stayed down ~10h
    because nothing else was watching).
  - "--setup-engine-task" / "--setup-watchdog-task" / "--remove-tasks":
    one Scheduled-Task action, run elevated (spawned via a UAC prompt by
    the menu's install/uninstall options; see tick_program_task_setup.py).

No functionality is duplicated here: every mode calls straight into
existing src/ modules or tick_program_task_setup.py.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Non-frozen (dev/test) runs: make `import src` and sibling-script imports
# work without needing PYTHONPATH set. Frozen builds already have these on
# sys.path via PyInstaller's own bootstrap, so this is a harmless no-op there.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_KNOWN_SUBCOMMANDS = {
    "service", "start", "show-config", "token-status", "auth-url", "exchange-code",
    "oauth-login", "refresh-token", "account-list", "auth-check", "symbol-sync",
    "backfill", "backfill-batched", "gap-fill-backfill", "check", "repair-stale-runs",
    "spool-status", "spool-drain", "compress-ticks", "refresh-dedup-window", "reset-tick-data", "chart",
}

_WATCHDOG_STALE_SECONDS = 180  # ~6x the 30s heartbeat-write interval (see runtime.py::_HEARTBEAT_INTERVAL_SECONDS)


def _interactive() -> bool:
    """True only when launched from a real console the operator can type into.

    Checks for an attached console window first (a fast, non-blocking Win32
    call) before ever touching sys.stdin/stdout.isatty(). Confirmed live
    (2026-09-02): when Task Scheduler launches this --console-subsystem exe
    under an S4U logon with no interactive session and no I/O redirected,
    the process gets no real console, and sys.stdin.isatty() then hangs
    indefinitely (process alive, "Responding", zero log output, reproduced
    twice with 60s+ waits) instead of returning False or raising. Bare
    Engine-task launches (no argv) go through this function, so that hang
    took the whole service down with it. GetConsoleWindow() returning NULL
    is the reliable signal for "no console attached" and answers the same
    question without ever calling the isatty() that hangs.
    """
    import ctypes

    try:
        if ctypes.windll.kernel32.GetConsoleWindow() == 0:
            return False
    except Exception:
        pass
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def watchdog_once() -> int:
    """Independent liveness check: is the supervisor process itself alive
    and still writing its heartbeat? Deliberately does NOT touch SQL Server
    or cTrader -- it must keep working even when those are what's down."""
    from src.configuration import RUN_DIR
    from src.notify import flush_notifications, notify, read_service_heartbeat
    from src.spool import is_pid_alive

    marker = RUN_DIR / "watchdog_alerted"
    heartbeat = read_service_heartbeat()
    now = datetime.now(timezone.utc)
    problem: str | None = None

    if not heartbeat or "error" in heartbeat:
        problem = "no service heartbeat file found (or unreadable)"
    else:
        pid = heartbeat.get("service_pid")
        if not pid or not is_pid_alive(int(pid)):
            problem = f"heartbeat file exists but PID {pid} is not running"
        else:
            try:
                updated = datetime.fromisoformat(str(heartbeat["updated_at_utc"]).replace("Z", "+00:00"))
                age = (now - updated).total_seconds()
            except Exception:
                age = None
            if age is None or age > _WATCHDOG_STALE_SECONDS:
                problem = f"heartbeat stale ({age if age is not None else 'unknown'}s old), PID {pid} still running"

    if problem is None:
        marker.unlink(missing_ok=True)
        return 0

    if marker.exists():
        return 1  # already alerted for this outage; don't spam every watchdog cycle

    notify(
        "CRITICAL", "tick_program watchdog: service looks down",
        conclusion=problem,
        action="Check runtime/logs/tick_engine.log; restart the Scheduled Task if the service is really dead.",
        throttle_key="tick-program-watchdog-down", throttle_seconds=0,
    )
    flush_notifications()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(now.isoformat(), encoding="ascii")
    return 1


def _run_elevated_task_action(flag: str) -> int:
    # Reached only in the *elevated* relaunch spawned by
    # tick_program_task_setup.py's UAC prompt -- run the one requested
    # Task Scheduler action and exit, pausing so the new console window
    # doesn't flash-close before the operator can read the result.
    import tick_program_task_setup as task_setup
    from tick_program_task_setup import ACTION_FLAGS

    action = getattr(task_setup, ACTION_FLAGS[flag])
    try:
        action()
        code = 0
    except Exception as exc:
        print(f"ERROR: {exc}")
        code = 1
    input("\nNhan Enter de dong cua so nay...")
    return code


def main() -> int:
    argv = sys.argv[1:]

    if argv and argv[0] in _KNOWN_SUBCOMMANDS:
        from src.__main__ import main as cli_main

        return cli_main(argv)

    if argv and argv[0] == "--watchdog":
        return watchdog_once()

    task_flags = [a for a in argv if a in ("--setup-engine-task", "--setup-watchdog-task", "--remove-tasks")]
    if task_flags:
        return _run_elevated_task_action(task_flags[0])

    if _interactive():
        from tick_program_menu import run_menu

        return run_menu()

    from src.__main__ import main as cli_main

    return cli_main(["service"])


if __name__ == "__main__":
    # Only a truly bare launch (no argv at all -- Task Scheduler, or a
    # double-click) is plausibly a "Config.yaml missing/wrong" problem;
    # a known-subcommand/--watchdog/--setup-*-task failure (argv non-empty)
    # is whatever that specific command actually failed on (e.g. the
    # already-documented transient cTrader "Connection lost" pattern) and
    # printing this same setup hint under it was misleading noise --
    # confirmed happening in production job logs before this fix (see
    # project memory, 2026-08-28/31).
    _bare_launch = not sys.argv[1:]
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # operator-facing message instead of a raw traceback
        print(f"ERROR: {exc}")
        if _bare_launch:
            print(
                "Copy Config.example.yaml to Config.yaml next to tick_program.exe, "
                "fill in your settings, and try again."
            )
        raise SystemExit(1) from None
