"""Standalone Discord watchdog for run-live/run-backfill.

Registered as its own recurring Scheduled Task (see install_task.ps1),
separate from the 24/7 engine processes. It only reads durable state on
disk (service_status) and, when unhealthy, sends one Discord alert
through discord_report.send_watchdog_alert() — it never restarts,
signals, or otherwise touches the engine processes themselves; Task
Scheduler's own restart-on-failure setting on the live/backfill tasks is
what brings a crashed service back.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from dp_program.configuration import load_config
from dp_program.engine.runtime import service_status
from dp_program.util.discord_report import send_watchdog_alert

_ROLES = ("live", "backfill")


def main() -> int:
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


if __name__ == "__main__":
    raise SystemExit(main())
