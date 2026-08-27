"""PyInstaller entry point for the standalone, config-driven engine .exe.

Three ways this can be launched:
  - No arguments, non-interactive (Task Scheduler, no attached console):
    unchanged from before -- reads Config.yaml's live.enabled /
    backfill.enabled and starts whichever workflow(s) are turned on, both
    concurrently if both are enabled. This is what the already-deployed
    "SEN05 DP Program Engine" Scheduled Task relies on; it must keep
    working exactly as today.
  - No arguments, from a real interactive console (double-click, or run
    from an open terminal): shows the operator menu (dp_program_menu.py)
    instead of starting anything automatically.
  - "--watchdog": run one health-check pass and exit (0 if live/backfill
    both look healthy, 1 otherwise) -- meant to be invoked periodically by
    a separate, repeating Scheduled Task (see dp_program_task_setup.py).
    Mirrors scripts/windows/watchdog.py's check, duplicated here in a
    dozen lines instead of imported, so the frozen exe never depends on a
    second .py file being resolvable at runtime.

Each enabled live/backfill workflow runs in its own child OS process
(multiprocessing, not threading): engine/runtime.py's
run_live_service()/run_backfill_service() each call signal.signal(),
which Python only allows from a thread's own process main thread --
running both in threads of one process would crash the second one. A
separate child process is also exactly how today's two independent
`python -m dp_program run-live` / `run-backfill` invocations already
work, so this preserves the existing per-workflow instance lock and
state-file behaviour unchanged; this script only decides whether/how to
launch each one, not how each one runs.

Playwright browser path: when frozen by PyInstaller, Playwright's driver
resolves its own bundled temp-extraction folder as the browser cache
location (a "_MEI.../playwright/driver/package/.local-browsers/..." path
that never has a browser in it) instead of the real, already-populated
%LOCALAPPDATA%\\ms-playwright cache that run_dp/install.ps1 sets up (or
that an existing `playwright install` already created on this machine).
Pointing PLAYWRIGHT_BROWSERS_PATH at that real cache, before anything
imports playwright, makes every launch path (headless, menu, watchdog,
and the multiprocessing children below, which inherit this process's
environment) resolve Chromium correctly without needing this env var set
anywhere outside the frozen exe.
"""
from __future__ import annotations

import multiprocessing
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault(
    "PLAYWRIGHT_BROWSERS_PATH",
    str(Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "ms-playwright"),
)

_ROLES = ("live", "backfill")


def _run_role(role: str) -> None:
    from dp_program.__main__ import main

    raise SystemExit(main([f"run-{role}"]))


def main_entry(
    *,
    config: dict[str, Any] | None = None,
    process_factory: Callable[..., Any] = multiprocessing.Process,
) -> int:
    """Start every workflow Config.yaml has enabled; wait for all of them."""
    if config is None:
        from dp_program.configuration import load_config

        config = load_config()
    roles = [role for role in _ROLES if config[role]["enabled"]]
    if not roles:
        print("live.enabled and backfill.enabled are both false in Config.yaml; nothing to run.")
        return 1
    processes = [
        process_factory(target=_run_role, args=(role,), name=f"dp_program_{role}")
        for role in roles
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join()
    return 0 if all(process.exitcode == 0 for process in processes) else 1


def watchdog_once(config: dict[str, Any] | None = None) -> int:
    """One-shot health check: alert on Discord if live/backfill looks unhealthy.

    Never restarts or signals the engine -- Task Scheduler's own
    restart-on-failure on the Engine task is what brings a crashed service
    back. This only reads durable on-disk state and de-dupes repeat alerts
    with a marker file, same as scripts/windows/watchdog.py does for the
    source deployment.
    """
    from dp_program.configuration import load_config
    from dp_program.engine.runtime import service_status
    from dp_program.util.discord_report import send_watchdog_alert

    if config is None:
        config = load_config()
    run_dir = Path(config["app"]["runtime_dir"]) / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    unhealthy = False
    for role in _ROLES:
        marker = run_dir / f"watchdog_alerted_{role}"
        status = service_status(config, role)
        if status.get("ok"):
            marker.unlink(missing_ok=True)
            continue
        unhealthy = True
        if marker.exists():
            continue
        snapshot = {**status, "risk": "CRITICAL", "component": role}
        send_watchdog_alert(config, f"{role}_service_down", snapshot)
        marker.write_text(datetime.now(timezone.utc).isoformat(), encoding="ascii")
    return 1 if unhealthy else 0


def _interactive() -> bool:
    """True only when launched from a real console the operator can type into."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def _run_elevated_task_action(flag: str) -> int:
    # Reached only in the *elevated* relaunch spawned by
    # dp_program_task_setup.py's UAC prompt -- run the one requested
    # Task Scheduler action and exit, pausing so the new console window
    # doesn't flash-close before the operator can read the result.
    from dp_program_task_setup import ACTION_FLAGS

    import dp_program_task_setup as task_setup

    action = getattr(task_setup, ACTION_FLAGS[flag])
    try:
        action()
        code = 0
    except Exception as exc:
        print(f"ERROR: {exc}")
        code = 1
    input("\nNhan Enter de dong cua so nay...")
    return code


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if "--watchdog" in sys.argv[1:]:
        raise SystemExit(watchdog_once())
    _task_flags = [arg for arg in sys.argv[1:] if arg in ("--setup-engine-task", "--setup-watchdog-task", "--remove-tasks")]
    if _task_flags:
        raise SystemExit(_run_elevated_task_action(_task_flags[0]))
    if _interactive():
        from dp_program_menu import run_menu

        raise SystemExit(run_menu())
    try:
        raise SystemExit(main_entry())
    except Exception as exc:  # operator-facing message instead of a raw traceback
        print(f"ERROR: {exc}")
        print(
            "Copy Config.example.yaml to Config.yaml next to dp_program.exe, "
            "fill in your settings, and try again."
        )
        raise SystemExit(1) from None
