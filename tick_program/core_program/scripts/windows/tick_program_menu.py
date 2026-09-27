"""Interactive console menu shown when tick_program.exe is launched from a
real terminal (see tick_program_entry.py's TTY check).

Every action here calls straight into the existing tick_program CLI
(src.__main__.main) or the same primitives the CLI/service already use --
this file adds a friendlier front door, no new engine behaviour.
"""
from __future__ import annotations

from typing import Callable

_TITLE = "tick_program -- SEN05"


def _pause() -> None:
    input("\nNhan Enter de quay lai menu...")


def _run_cli(argv: list[str]) -> None:
    from src.__main__ import main as cli_main

    cli_main(argv)


def _view_config_and_status() -> None:
    print("\n=== Cau hinh hien tai (show-config) ===")
    _run_cli(["show-config"])
    print("\n=== Kiem tra hien trang (check, khong gui Discord) ===")
    _run_cli(["check"])
    _pause()


def _view_running_status() -> None:
    from src.configuration import SERVICE_HEARTBEAT
    from src.spool import is_pid_alive

    print("\n=== Trang thai service ===")
    if not SERVICE_HEARTBEAT.exists():
        print("Khong tim thay file heartbeat -- service co the chua tung chay.")
    else:
        print(SERVICE_HEARTBEAT.read_text(encoding="utf-8-sig"))
        import json

        try:
            data = json.loads(SERVICE_HEARTBEAT.read_text(encoding="utf-8-sig"))
            pid = data.get("service_pid")
            if pid:
                alive = is_pid_alive(int(pid))
                print(f"\nPID {pid}: {'DANG CHAY' if alive else 'KHONG CHAY (heartbeat cu)'}")
        except Exception as exc:
            print(f"(khong doc duoc chi tiet PID: {exc})")
    _pause()


def _run_foreground() -> None:
    print("\nDang chay service o che do foreground. Nhan Ctrl+C de dung an toan.\n")
    try:
        _run_cli(["service"])
    except KeyboardInterrupt:
        pass
    _pause()


def _view_logs() -> None:
    from src.configuration import LOG_FILE

    print(f"\n=== 60 dong log gan nhat ({LOG_FILE}) ===")
    if not LOG_FILE.exists():
        print("Chua co file log.")
    else:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-60:]:
            print(line)
    _pause()


def _request_stop() -> None:
    from src.configuration import SUPERVISOR_STOP

    answer = input("Gui yeu cau dung service an toan? (y/N): ").strip().lower()
    if answer == "y":
        SUPERVISOR_STOP.parent.mkdir(parents=True, exist_ok=True)
        SUPERVISOR_STOP.touch()
        print(f"[ok] Da tao {SUPERVISOR_STOP} -- service se tu dung o vong lap ke tiep (trong vai giay).")
    _pause()


def _open_chart() -> None:
    from src.chart.server import run_server

    print("\nDang mo chart tick (read-only) tai http://127.0.0.1:8060 ... Nhan Ctrl+C de dong va quay lai menu.\n")
    try:
        run_server(open_browser=True)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"\nERROR: {exc}")
    _pause()


def _install_startup_task() -> None:
    from tick_program_task_setup import install_engine_task

    install_engine_task()
    _pause()


def _install_watchdog_task() -> None:
    from tick_program_task_setup import install_watchdog_task

    install_watchdog_task()
    _pause()


def _uninstall_tasks() -> None:
    from tick_program_task_setup import uninstall_tasks

    uninstall_tasks()
    _pause()


_ACTIONS: dict[str, Callable[[], None]] = {
    "1": _view_config_and_status,
    "2": _view_running_status,
    "3": _run_foreground,
    "4": _view_logs,
    "5": _request_stop,
    "6": _open_chart,
    "7": _install_startup_task,
    "8": _install_watchdog_task,
    "9": _uninstall_tasks,
}


def _print_menu() -> None:
    print(f"\n{_TITLE}\n{'=' * len(_TITLE)}")
    print("  Van hanh thu cong")
    print("  1. Xem cau hinh & tinh trang he thong (show-config + check)")
    print("  2. Xem trang thai dang chay (heartbeat/PID)")
    print("  3. Chay service (foreground)")
    print("  4. Xem log gan nhat")
    print("  5. Gui yeu cau dung an toan")
    print("  6. Mo chart tick (read-only, http://127.0.0.1:8060)")
    print("\n  Cai dat van hanh nen (can quyen Administrator)")
    print("  7. Cai dat khoi dong cung Windows + tu restart khi crash")
    print("  8. Cai dat Watchdog (kiem tra moi 5 phut, canh bao khi treo)")
    print("  9. Go cai dat (huy Task Scheduler da dang ky)")
    print("\n  0. Thoat")


def run_menu() -> int:
    while True:
        _print_menu()
        try:
            choice = input("\nChon [0-9]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if choice == "0":
            return 0
        action = _ACTIONS.get(choice)
        if action is None:
            print("Lua chon khong hop le.")
            continue
        try:
            action()
        except Exception as exc:  # keep the menu alive on any single-action failure
            print(f"\nERROR: {exc}")
            _pause()
