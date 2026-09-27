"""Windows Scheduled Task setup/teardown for tick_program.exe, callable from
the interactive menu (tick_program_menu.py).

The "engine" task registers the same task name already in production
(`\\SEN05\\SEN05 Tick Program Engine`) so re-running this against an
already-deployed install is an in-place update, not a confusing duplicate.
The "watchdog" task is new: a periodic (every 5 minutes) one-shot health
check calling this same .exe with --watchdog, registered as its own
separate task so a fully-dead engine process still gets alerted on even
though the engine task's own restart-on-failure only reacts to an actual
process exit (a clean-but-nonzero exit is never restarted by Task
Scheduler's policy -- see tick_program_entry.py::watchdog_once's docstring
for the incident this closes).

Registering/removing a Scheduled Task needs Administrator rights. If the
current process isn't elevated, these functions relaunch this same .exe
elevated (one UAC prompt) with an internal action flag, let that instance
perform the one requested action, and exit.
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path

_TASK_FOLDER = "\\SEN05\\"
_ENGINE_TASK = "SEN05 Tick Program Engine"
_WATCHDOG_TASK = "SEN05 Tick Program Watchdog"

ACTION_FLAGS = {
    "--setup-engine-task": "install_engine_task",
    "--setup-watchdog-task": "install_watchdog_task",
    "--remove-tasks": "uninstall_tasks",
}


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _exe_path() -> Path:
    if not getattr(sys, "frozen", False):
        raise RuntimeError(
            "Chi dang ky Task Scheduler tu tick_program.exe da build, "
            "khong the thuc hien khi chay truc tiep tu source."
        )
    return Path(sys.executable).resolve()


def _relaunch_elevated(action_flag: str) -> None:
    exe = _exe_path()
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", str(exe), action_flag, str(exe.parent), 1
    )
    if int(result) <= 32:
        raise RuntimeError("Khong xin duoc quyen Administrator (bi tu choi hoac UAC that bai).")
    print("Da mo 1 cua so moi voi quyen Administrator de thuc hien thao tac nay.")


def _run_powershell(script: str) -> None:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
    )
    if completed.stdout.strip():
        print(completed.stdout.strip())
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"powershell exited with code {completed.returncode}")


_ENGINE_SCRIPT = """
$ErrorActionPreference = "Stop"
$exePath = "{exe}"
$installDir = "{install_dir}"
$name = "{name}"
$taskFolder = "{folder}"
$existing = Get-ScheduledTask -TaskPath $taskFolder -TaskName $name -ErrorAction SilentlyContinue
if ($existing -and $existing.State -eq "Running") {{
    throw "$name dang chay. Dung an toan (tao file runtime\\run\\supervisor.stop hoac Stop-ScheduledTask) truoc khi dang ky lai."
}}
$action = New-ScheduledTaskAction -Execute $exePath -WorkingDirectory $installDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -Priority 5
$settings.ExecutionTimeLimit = "PT0S"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\\$env:USERNAME" -LogonType S4U -RunLevel Highest
$definition = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal
Register-ScheduledTask -TaskPath $taskFolder -TaskName $name -InputObject $definition -Force | Out-Null
Write-Host "[ok] Da dang ky Scheduled Task '$taskFolder$name' (AtStartup, tu restart khi crash)."
"""

_WATCHDOG_SCRIPT = """
$ErrorActionPreference = "Stop"
$exePath = "{exe}"
$installDir = "{install_dir}"
$name = "{name}"
$taskFolder = "{folder}"
$action = New-ScheduledTaskAction -Execute $exePath -Argument "--watchdog" -WorkingDirectory $installDir
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -Priority 7
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\\$env:USERNAME" -LogonType S4U -RunLevel Highest
$definition = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal
Register-ScheduledTask -TaskPath $taskFolder -TaskName $name -InputObject $definition -Force | Out-Null
Write-Host "[ok] Da dang ky Scheduled Task '$taskFolder$name' (kiem tra moi 5 phut)."
"""

_UNINSTALL_SCRIPT = """
$ErrorActionPreference = "SilentlyContinue"
foreach ($name in @("{engine}", "{watchdog}")) {{
    $existing = Get-ScheduledTask -TaskPath "{folder}" -TaskName $name -ErrorAction SilentlyContinue
    if ($existing) {{
        Unregister-ScheduledTask -TaskPath "{folder}" -TaskName $name -Confirm:$false
        Write-Host "[ok] Da go Scheduled Task '{folder}$name'."
    }} else {{
        Write-Host "(khong tim thay task '$name', bo qua)"
    }}
}}
"""


def install_engine_task() -> None:
    if not _is_admin():
        _relaunch_elevated("--setup-engine-task")
        return
    exe = _exe_path()
    script = _ENGINE_SCRIPT.format(exe=exe, install_dir=exe.parent, name=_ENGINE_TASK, folder=_TASK_FOLDER)
    _run_powershell(script)


def install_watchdog_task() -> None:
    if not _is_admin():
        _relaunch_elevated("--setup-watchdog-task")
        return
    exe = _exe_path()
    script = _WATCHDOG_SCRIPT.format(exe=exe, install_dir=exe.parent, name=_WATCHDOG_TASK, folder=_TASK_FOLDER)
    _run_powershell(script)


def uninstall_tasks() -> None:
    if not _is_admin():
        _relaunch_elevated("--remove-tasks")
        return
    script = _UNINSTALL_SCRIPT.format(engine=_ENGINE_TASK, watchdog=_WATCHDOG_TASK, folder=_TASK_FOLDER)
    _run_powershell(script)
