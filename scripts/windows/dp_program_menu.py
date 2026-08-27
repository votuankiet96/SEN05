"""Interactive console menu shown when dp_program.exe is launched from a
real terminal (see dp_program_entry.py's TTY check).

Every action here calls straight into the existing dp_program CLI
(dp_program.__main__.main) or engine/runtime.py functions the CLI already
uses -- this file adds a friendlier front door, no new engine behaviour.
"""
from __future__ import annotations

import multiprocessing
from pathlib import Path
from typing import Callable

_TITLE = "DP Program -- SEN05"
_LOG_FILENAMES = {"live": "dp_program_live.log", "backfill": "dp_program_backfill.log"}


def _pause() -> None:
    input("\nNhan Enter de quay lai menu...")


def _run_cli(argv: list[str]) -> None:
    from dp_program.__main__ import main as cli_main

    cli_main(argv)


def _view_config_and_status() -> None:
    print("\n=== Cau hinh hien tai (settings) ===")
    _run_cli(["settings"])
    print("\n=== Kiem tra san sang van hanh (doctor) ===")
    _run_cli(["doctor"])
    _pause()


def _view_running_status() -> None:
    for mode in ("live", "backfill"):
        print(f"\n=== Trang thai {mode} ===")
        _run_cli(["status", "--mode", mode])
    _pause()


def _run_foreground(mode: str) -> None:
    # Chay trong 1 tien trinh con rieng (khong goi _run_cli truc tiep):
    # configure_logging() chi cau hinh 1 lan cho ca vong doi tien trinh,
    # nen neu goi live roi backfill trong CUNG 1 tien trinh menu, lan sau
    # se bi ghi nham vao log cua lan truoc. Tien trinh con moi lan luon
    # cau hinh log sach, dung file, giong het cach run_live.bat dang lam.
    from dp_program_entry import _run_role

    print(f"\nDang chay {mode} o che do foreground. Nhan Ctrl+C de dung an toan.\n")
    process = multiprocessing.Process(target=_run_role, args=(mode,), name=f"dp_program_{mode}")
    process.start()
    try:
        process.join()
    except KeyboardInterrupt:
        process.join()
    _pause()


def _run_both_foreground() -> None:
    # Live va Backfill chay trong 2 tien trinh con rieng (giong
    # dp_program_entry.main_entry): moi service tu cai signal handler
    # rieng cho tien trinh chinh cua no, nen khong the chung 1 tien trinh.
    from dp_program_entry import _run_role

    processes = [
        multiprocessing.Process(target=_run_role, args=(role,), name=f"dp_program_{role}")
        for role in ("live", "backfill")
    ]
    print("\nDang chay ca Live + Backfill o che do foreground. Nhan Ctrl+C de dung an toan.\n")
    for process in processes:
        process.start()
    try:
        for process in processes:
            process.join()
    except KeyboardInterrupt:
        for process in processes:
            process.join()
    _pause()


def _view_logs() -> None:
    from dp_program.configuration import load_config

    config = load_config()
    runtime_dir = Path(config["app"]["runtime_dir"])
    for mode, filename in _LOG_FILENAMES.items():
        path = runtime_dir / "logs" / filename
        print(f"\n=== {mode}: 30 dong log gan nhat ({path}) ===")
        if not path.exists():
            print("(chua co log)")
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-30:]:
            print(line)
    _pause()


def _request_stop() -> None:
    for mode in ("live", "backfill"):
        answer = input(f"Gui yeu cau dung {mode}? (y/N): ").strip().lower()
        if answer == "y":
            _run_cli(["stop", "--mode", mode])
    _pause()


def _install_startup_task() -> None:
    from dp_program_task_setup import install_engine_task

    install_engine_task()
    _pause()


def _install_watchdog_task() -> None:
    from dp_program_task_setup import install_watchdog_task

    install_watchdog_task()
    _pause()


def _uninstall_tasks() -> None:
    from dp_program_task_setup import uninstall_tasks

    uninstall_tasks()
    _pause()


_ACTIONS: dict[str, Callable[[], None]] = {
    "1": _view_config_and_status,
    "2": _view_running_status,
    "3": lambda: _run_foreground("live"),
    "4": lambda: _run_foreground("backfill"),
    "5": _run_both_foreground,
    "6": _view_logs,
    "7": _request_stop,
    "8": _install_startup_task,
    "9": _install_watchdog_task,
    "10": _uninstall_tasks,
}


def _print_menu() -> None:
    print(f"\n{_TITLE}\n{'=' * len(_TITLE)}")
    print("  Van hanh thu cong")
    print("  1. Xem cau hinh & tinh trang he thong (settings + doctor)")
    print("  2. Xem trang thai dang chay")
    print("  3. Chay Live (foreground)")
    print("  4. Chay Backfill (foreground)")
    print("  5. Chay ca Live + Backfill (foreground)")
    print("  6. Xem log gan nhat")
    print("  7. Gui yeu cau dung an toan")
    print("\n  Cai dat van hanh nen (can quyen Administrator)")
    print("  8. Cai dat khoi dong cung Windows + tu restart khi crash")
    print("  9. Cai dat Watchdog (giam sat dinh ky, canh bao khi treo)")
    print(" 10. Go cai dat (huy Task Scheduler da dang ky)")
    print("\n  0. Thoat")


def run_menu() -> int:
    while True:
        _print_menu()
        try:
            choice = input("\nChon [0-10]: ").strip()
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
