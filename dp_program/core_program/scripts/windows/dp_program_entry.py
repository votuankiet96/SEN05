"""PyInstaller entry point for the standalone, config-driven engine .exe.

This file is the OPERATIONAL layer: it decides whether/how the engine is
launched, supervised, restarted and diagnosed. It never changes how the
engine itself works -- engine resilience (spool/replay, pending retry,
circuit breakers, auth fail-closed) lives in src/dp_program/ and is only
called from here through read-only or already-public APIs.

Modes, chosen explicitly by flag (or by console type when no flag):
  - "--run" (and no arguments + no attached console, kept for the
    already-registered Scheduled Task): read config.yaml's live.enabled /
    backfill.enabled and start whichever workflow(s) are on, each in its
    own child process. Skips any role that is already running instead of
    forking a child that would only die on the instance lock.
  - No arguments, from a real interactive console (double-click, or run
    from an open terminal): shows the operator menu (dp_program_menu.py).
  - "--watchdog": one health-check pass, then exit. Alerts on real
    failure, stays quiet for an intentional stop, and -- for the one
    failure mode Task Scheduler cannot see (process alive but hung) --
    escalates to a bounded auto-restart. See watchdog_once().
  - "--restart": apply a config.yaml change without losing a cycle --
    validate first, stop gracefully, then bring the engine back. See
    restart_engine(). "--restart-live" / "--restart-backfill" do the same
    for exactly one role, leaving the other role running untouched -- live
    and backfill are fully independent workflows now (live pushes straight
    to Redis, backfill is the only SQL writer), so a config change that only
    affects one of them no longer needs to interrupt the other.
  - "--setup" / "--teardown": register or remove the two canonical
    Scheduled Tasks (see dp_program_task_setup.py).
  - "--doctor-ops": report operational-layer health, change nothing (see
    dp_program_ops_doctor.py).

Each enabled live/backfill workflow runs in its own child OS process
(multiprocessing, not threading): engine/runtime.py's
run_live_service()/run_backfill_service() each call signal.signal(),
which Python only allows from a thread's own process main thread --
running both in threads of one process would crash the second one. A
separate child process is also exactly how two independent
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

import json
import multiprocessing
import os
import subprocess
import sys
import time
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
# Dừng chủ động lâu hơn ngưỡng này thì watchdog nhắc nhẹ (không báo động):
# đủ rộng cho một cửa sổ bảo trì bình thường, đủ hẹp để không quên cả ngày.
_STOPPED_REMINDER_SECONDS = 2 * 3600
# Phanh cho auto-restart: quá số lần này trong cửa sổ thời gian thì ngừng
# tự phục hồi và giao lại cho người -- chặn đúng kiểu crash-loop vô hạn.
_MAX_AUTO_RESTARTS = 3
_AUTO_RESTART_WINDOW_SECONDS = 3600


def _run_role(role: str) -> None:
    # A previous graceful stop (menu, or dp_program stop) leaves
    # stop_<role>.request on disk. Nothing else clears it for this entry
    # point, so do it here. Without it, a fresh start sees the stale
    # request immediately and exits right away (SERVICE_STOPPED with no
    # SERVICE_STARTED at all) instead of running.
    from dp_program.configuration import load_config

    config = load_config()
    (Path(config["app"]["runtime_dir"]) / "run" / f"stop_{role}.request").unlink(missing_ok=True)

    from dp_program.__main__ import main

    raise SystemExit(main([f"run-{role}"]))


def _already_running(config: dict[str, Any], role: str) -> bool:
    """True when this role has a live process owning its state file."""
    from dp_program.engine.runtime import service_status

    status = service_status(config, role)
    return bool(status.get("status") == "running" and status.get("process_alive"))


def main_entry(
    *,
    config: dict[str, Any] | None = None,
    process_factory: Callable[..., Any] = multiprocessing.Process,
) -> int:
    """Start every enabled workflow that is not already running; wait for them."""
    if config is None:
        from dp_program.configuration import load_config

        config = load_config()
    enabled = [role for role in _ROLES if config[role]["enabled"]]
    if not enabled:
        print("live.enabled and backfill.enabled are both false in config.yaml; nothing to run.")
        return 1
    # Guard vận hành: role nào đã có tiến trình sống thì bỏ qua. Không có
    # guard này, mỗi lần lỡ chạy chồng sẽ fork ra con để rồi con chết ngay
    # trên instance lock, đẻ log CRITICAL và (dưới Task Scheduler) quay
    # vòng retry mỗi phút.
    roles = [role for role in enabled if not _already_running(config, role)]
    for role in enabled:
        if role not in roles:
            print(f"{role}: da co tien trinh dang chay, bo qua.")
    if not roles:
        print("Tat ca workflow duoc bat deu dang chay san; khong khoi dong them.")
        return 0
    processes = [
        process_factory(target=_run_role, args=(role,), name=f"dp_program_{role}")
        for role in roles
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join()
    return 0 if all(process.exitcode == 0 for process in processes) else 1


# --- Watchdog ---------------------------------------------------------------


def _classify(status: dict[str, Any]) -> str:
    """healthy | stopped | hung | down -- quyết định watchdog làm gì.

    Tách 'stopped' (người chủ động dừng, state ghi status=stopped khi
    thoát sạch) khỏi 'down' (chết ngoài ý muốn) để một lần dừng có chủ
    đích không đẻ cảnh báo CRITICAL giả. 'hung' là trường hợp duy nhất
    Task Scheduler không nhìn thấy: tiến trình còn sống nhưng heartbeat
    đứng, nên không có exit code nào để nó phản ứng.
    """
    if status.get("ok"):
        return "healthy"
    if status.get("status") == "stopped":
        return "stopped"
    if status.get("process_alive"):
        return "hung"
    return "down"


def _stopped_age_seconds(status: dict[str, Any]) -> float | None:
    raw = status.get("stopped_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def _auto_restart_allowed(run_dir: Path) -> bool:
    """Phanh: tối đa _MAX_AUTO_RESTARTS lần trong _AUTO_RESTART_WINDOW_SECONDS."""
    ledger = run_dir / "watchdog_restarts.json"
    now = time.time()
    try:
        recent = [float(item) for item in json.loads(ledger.read_text(encoding="ascii"))]
    except (OSError, ValueError):
        recent = []
    recent = [item for item in recent if now - item < _AUTO_RESTART_WINDOW_SECONDS]
    if len(recent) >= _MAX_AUTO_RESTARTS:
        return False
    recent.append(now)
    ledger.write_text(json.dumps(recent), encoding="ascii")
    return True


def watchdog_once(config: dict[str, Any] | None = None) -> int:
    """One-shot health check; alert on real failure, recover a hung engine.

    Division of labour with Task Scheduler, deliberately non-overlapping:
    Task Scheduler restarts a process that EXITED non-zero; this restarts
    a process that is ALIVE but stuck. Escalation is two-step (alert
    first, restart only if the next pass still sees it hung) and capped,
    so a permanently broken engine ends in one "needs a human" alert
    instead of an endless restart loop.
    """
    from dp_program.configuration import load_config
    from dp_program.engine.runtime import service_status
    from dp_program.util.discord_report import send_watchdog_alert

    if config is None:
        config = load_config()
    run_dir = Path(config["app"]["runtime_dir"]) / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    unhealthy = False
    restart_roles: list[str] = []
    for role in _ROLES:
        marker = run_dir / f"watchdog_alerted_{role}"
        status = service_status(config, role)
        state = _classify(status)
        if state == "healthy":
            marker.unlink(missing_ok=True)
            continue
        if state == "stopped":
            # Dừng có chủ đích: im lặng trong cửa sổ bảo trì, chỉ nhắc nhẹ
            # nếu để quên quá lâu. Không tính là unhealthy.
            age = _stopped_age_seconds(status)
            if age is not None and age > _STOPPED_REMINDER_SECONDS and not marker.exists():
                send_watchdog_alert(
                    config, f"{role}_stopped_reminder",
                    {**status, "risk": "LOW", "component": role, "stopped_hours": round(age / 3600, 1)},
                )
                marker.write_text(datetime.now(timezone.utc).isoformat(), encoding="ascii")
            continue
        unhealthy = True
        if not marker.exists():
            # Lần đầu thấy hỏng: chỉ cảnh báo, có thể chỉ là thoáng qua.
            send_watchdog_alert(
                config, f"{role}_service_down", {**status, "risk": "CRITICAL", "component": role},
            )
            marker.write_text(datetime.now(timezone.utc).isoformat(), encoding="ascii")
            continue
        # Đã cảnh báo ở lượt trước mà vẫn hỏng. Treo là trường hợp duy
        # nhất Task Scheduler không tự xử được -> mới cần can thiệp.
        if state == "hung":
            restart_roles.append(role)
    if restart_roles:
        # Chỉ restart đúng (các) role đang treo -- live/backfill độc lập
        # hoàn toàn, role còn lại đang khoẻ không cần bị gián đoạn theo.
        if _auto_restart_allowed(run_dir):
            print(f"watchdog: {', '.join(restart_roles)} treo, dang tu restart...")
            restart_engine(config=config, roles=tuple(restart_roles), validate=False)
        else:
            send_watchdog_alert(
                config, "auto_recovery_exhausted",
                {"risk": "CRITICAL", "component": "watchdog", "roles": restart_roles,
                 "detail": f"da tu restart {_MAX_AUTO_RESTARTS} lan trong 1 gio, ngung tu phuc hoi"},
            )
    return 1 if unhealthy else 0


# --- Restart ----------------------------------------------------------------


def _any_alive(config: dict[str, Any], roles: tuple[str, ...]) -> bool:
    return any(_already_running(config, role) for role in roles)


def _all_alive(config: dict[str, Any], roles: tuple[str, ...]) -> bool:
    return all(_already_running(config, role) for role in roles)


def _spawn_detached_run() -> None:
    """Fallback cuối: tự chạy lại engine khi Start-ScheduledTask thất bại."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("chi spawn duoc tu dp_program.exe da build")
    creation = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        [sys.executable, "--run"], cwd=str(Path(sys.executable).resolve().parent),
        creationflags=creation, close_fds=True,
    )


def restart_engine(
    config: dict[str, Any] | None = None, *, roles: tuple[str, ...] = _ROLES,
    validate: bool = True, wait_seconds: int = 300,
) -> int:
    """Graceful stop rồi bật lại engine, dùng khi đổi config.yaml.

    `roles` mặc định cả hai (`--restart`); truyền `("live",)` hoặc
    `("backfill",)` (`--restart-live` / `--restart-backfill`) để chỉ dừng và
    bật lại đúng role đó -- live/backfill độc lập hoàn toàn nên role còn lại
    không cần bị gián đoạn theo.

    Thứ tự có chủ đích: mọi thứ có thể fail đều được kiểm TRƯỚC khi động
    vào engine đang chạy, để một config.yaml hỏng không giết mất tiến
    trình tốt đang chạy. Và nếu Start-ScheduledTask fail thì tự chạy lại
    trực tiếp -- restart không bao giờ được kết thúc bằng engine nằm chết.
    """
    from dp_program.configuration import load_config

    if config is None:
        config = load_config()
    active = tuple(role for role in roles if config[role]["enabled"])
    if not active:
        print(f"Khong role nao trong {roles} dang bat (enabled) -- khong co gi de restart.")
        return 0
    if validate:
        from dp_program.__main__ import main as cli_main

        print("Kiem tra config.yaml va tinh san sang truoc khi dung engine...")
        if cli_main(["doctor"]) != 0:
            print("ERROR: doctor khong dat -- KHONG dung engine. Sua config.yaml roi thu lai.")
            return 1
        from dp_program_task_setup import engine_task_ready

        if not engine_task_ready():
            print("ERROR: chua dang ky Scheduled Task engine -- chay --setup truoc.")
            return 1

    from dp_program.engine.runtime import request_stop

    for role in active:
        print(f"Dang gui yeu cau dung {role}...")
        request_stop(config, role, wait_seconds=wait_seconds)
    if _any_alive(config, active):
        print("ERROR: engine chua thoat het sau khi cho -- KHONG khoi dong chong len.")
        return 1

    # Start-ScheduledTask bat lai ca task engine (--run), nhung main_entry()
    # tu bo qua role nao da co tien trinh song -- nen chi (cac) role vua
    # dung o tren duoc khoi dong lai, role kia (neu co) khong bi dong tram.
    from dp_program_task_setup import start_engine_task

    try:
        start_engine_task()
    except Exception as exc:
        print(f"Start-ScheduledTask that bai ({exc}); chuyen sang chay truc tiep.")
        _spawn_detached_run()
    for _ in range(15):
        time.sleep(1)
        if _all_alive(config, active):
            print("Engine da chay lai voi cau hinh moi.")
            return 0
    print("ERROR: engine chua len sau 15 giay -- kiem tra log.")
    return 1


def _interactive() -> bool:
    """True only when launched from a real console the operator can type into."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def _run_elevated_task_action(flag: str, *, pause_after: bool) -> int:
    # Two callers reach this: dp_program_task_setup.py's own UAC relaunch
    # (a brand-new console just for this one action -- pause_after=True so
    # it doesn't flash-close before the operator reads the result), and
    # install.ps1's Register-EngineTask, which is already elevated and
    # calls this directly inline in its own automated, mostly-unattended
    # flow -- pause_after=False there so a stray prompt never blocks it.
    from dp_program_task_setup import ACTION_FLAGS

    import dp_program_task_setup as task_setup

    action = getattr(task_setup, ACTION_FLAGS[flag])
    try:
        action()
        code = 0
    except Exception as exc:
        print(f"ERROR: {exc}")
        code = 1
    if pause_after:
        input("\nNhan Enter de dong cua so nay...")
    return code


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _args = sys.argv[1:]
    if "--watchdog" in _args:
        raise SystemExit(watchdog_once())
    if "--restart-live" in _args:
        raise SystemExit(restart_engine(roles=("live",)))
    if "--restart-backfill" in _args:
        raise SystemExit(restart_engine(roles=("backfill",)))
    if "--restart" in _args:
        raise SystemExit(restart_engine())
    if "--doctor-ops" in _args:
        from dp_program_ops_doctor import report

        raise SystemExit(report())
    _task_flags = [arg for arg in _args if arg in ("--setup", "--teardown")]
    if _task_flags:
        raise SystemExit(_run_elevated_task_action(_task_flags[0], pause_after="--pause-after" in _args))
    if "--run" not in _args and _interactive():
        from dp_program_menu import run_menu

        raise SystemExit(run_menu())
    try:
        raise SystemExit(main_entry())
    except Exception as exc:  # operator-facing message instead of a raw traceback
        print(f"ERROR: {exc}")
        print(
            "Copy config.example.yaml to config.yaml next to dp_program.exe, "
            "fill in your settings, and try again."
        )
        raise SystemExit(1) from None
