"""PyInstaller entry point for the standalone, config-driven engine .exe.

No command-line argument needed: double-click dp_program.exe and it reads
Config.yaml's live.enabled / backfill.enabled and starts whichever
workflow(s) are turned on -- both, concurrently, if both are enabled.
Config.yaml is the single source of truth for what runs; nothing about
which workflow to run is hardcoded here.

Each enabled workflow runs in its own child OS process (multiprocessing,
not threading): engine/runtime.py's run_live_service()/run_backfill_service()
each call signal.signal(), which Python only allows from a thread's own
process main thread -- running both in threads of one process would crash
the second one. A separate child process is also exactly how today's two
independent `python -m dp_program run-live` / `run-backfill` invocations
already work, so this preserves the existing per-workflow instance lock
and state-file behaviour unchanged; this script only decides whether to
launch each one, not how each one runs.
"""
from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

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


if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        raise SystemExit(main_entry())
    except Exception as exc:  # operator-facing message instead of a raw traceback
        print(f"ERROR: {exc}")
        print(
            "Copy Config.example.yaml to Config.yaml next to dp_program.exe, "
            "fill in your settings, and try again."
        )
        raise SystemExit(1) from None
