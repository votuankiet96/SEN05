"""Interactive console menu shown when dp_program.exe is launched from a
real terminal (see dp_program_entry.py's TTY check).

Every action calls straight into the existing dp_program CLI
(dp_program.__main__.main), engine/runtime.py functions the CLI already
uses, or the operational modes in dp_program_entry.py -- this file adds a
friendlier front door, no new engine behaviour.

Menu is grouped by intent, because the actions differ in blast radius:
production actions go through the Scheduled Task (what actually runs in
production), diagnostics change nothing, and the foreground runners are
debug-only -- they start the engine in THIS console's process tree, not
under the task, so they are not how production should ever be started.
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


# --- Van hanh (qua Scheduled Task) ------------------------------------------


def _engine_status() -> None:
    for mode in ("live", "backfill"):
        print(f"\n=== Trang thai {mode} ===")
        _run_cli(["status", "--mode", mode])
    _pause()


def _start_engine() -> None:
    from dp_program_task_setup import engine_task_ready, start_engine_task

    if not engine_task_ready():
        print("\nChua dang ky Scheduled Task engine. Dung muc 'Cai/cap nhat Task Scheduler' truoc.")
        _pause()
        return
    start_engine_task()
    print("Da yeu cau Task Scheduler khoi dong engine.")
    _pause()


def _stop_engine() -> None:
    from dp_program.configuration import load_config
    from dp_program.engine.runtime import request_stop

    config = load_config()
    for mode in ("live", "backfill"):
        if not config[mode]["enabled"]:
            continue
        print(f"Dang gui yeu cau dung {mode}...")
        result = request_stop(config, mode)
        print(f"  {mode}: {'da dung' if result['ok'] else 'chua thoat het trong thoi gian cho'}")
    _pause()


def _restart_engine() -> None:
    from dp_program_entry import restart_engine

    restart_engine()
    _pause()


# --- Chan doan ---------------------------------------------------------------


def _view_config_and_status() -> None:
    print("\n=== Cau hinh hien tai (settings) ===")
    _run_cli(["settings"])
    print("\n=== Kiem tra san sang van hanh (doctor) ===")
    _run_cli(["doctor"])
    _pause()


def _ops_doctor() -> None:
    from dp_program_ops_doctor import report

    report()
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


def _open_chart() -> None:
    from dp_program.util.chart.server import run_server

    print("\nDang mo chart read-only tai http://127.0.0.1:8050 ... Nhan Ctrl+C de dong va quay lai menu.\n")
    try:
        run_server(open_browser=True)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"\nERROR: {exc}")
    _pause()


# --- Chay tam khong qua Task (debug) ----------------------------------------


def _run_foreground(*roles: str) -> None:
    # Chay trong tien trinh con rieng (khong goi _run_cli truc tiep):
    # configure_logging() chi cau hinh 1 lan cho ca vong doi tien trinh,
    # nen neu goi live roi backfill trong CUNG 1 tien trinh menu, lan sau
    # se bi ghi nham vao log cua lan truoc. Moi service cung tu cai signal
    # handler rieng, chi lam duoc tu main thread cua chinh tien trinh no.
    from dp_program_entry import _run_role

    processes = [
        multiprocessing.Process(target=_run_role, args=(role,), name=f"dp_program_{role}")
        for role in roles
    ]
    print(f"\nDang chay {' + '.join(roles)} o che do foreground. Nhan Ctrl+C de dung an toan.\n")
    for process in processes:
        process.start()
    try:
        for process in processes:
            process.join()
    except KeyboardInterrupt:
        for process in processes:
            process.join()
    _pause()


# --- Cai dat may -------------------------------------------------------------


def _install_tasks() -> None:
    from dp_program_task_setup import install_tasks

    install_tasks()
    _pause()


def _uninstall_tasks() -> None:
    from dp_program_task_setup import uninstall_tasks

    uninstall_tasks()
    _pause()


_ACTIONS: dict[str, Callable[[], None]] = {
    "1": _engine_status,
    "2": _start_engine,
    "3": _stop_engine,
    "4": _restart_engine,
    "5": _view_config_and_status,
    "6": _ops_doctor,
    "7": _view_logs,
    "8": _open_chart,
    "9": lambda: _run_foreground("live"),
    "10": lambda: _run_foreground("backfill"),
    "11": lambda: _run_foreground("live", "backfill"),
    "12": _install_tasks,
    "13": _uninstall_tasks,
}


def _print_menu() -> None:
    print(f"\n{_TITLE}\n{'=' * len(_TITLE)}")
    print("  Van hanh (qua Scheduled Task)")
    print("  1. Trang thai engine")
    print("  2. Start engine")
    print("  3. Stop engine (dung an toan)")
    print("  4. Restart engine (ap config.yaml moi)")
    print("\n  Chan doan (khong thay doi gi)")
    print("  5. Cau hinh & san sang van hanh (settings + doctor)")
    print("  6. Suc khoe lop van hanh (task, tien trinh, chu ky)")
    print("  7. Xem log gan nhat")
    print("  8. Mo chart (read-only, http://127.0.0.1:8050)")
    print("\n  Chay tam KHONG qua Task (debug)")
    print("  9. Chay Live foreground")
    print(" 10. Chay Backfill foreground")
    print(" 11. Chay ca hai foreground")
    print("\n  Cai dat may (can quyen Administrator)")
    print(" 12. Cai/cap nhat Task Scheduler (engine + watchdog)")
    print(" 13. Go toan bo Task")
    print("\n  0. Thoat")


def run_menu() -> int:
    while True:
        _print_menu()
        try:
            choice = input("\nChon [0-13]: ").strip()
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
