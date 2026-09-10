"""Windows Scheduled Task setup/teardown for dp_program.exe, callable from
the interactive menu (dp_program_menu.py) and from run_dp/install.ps1.

This module is the SINGLE source of truth for what the two canonical
tasks look like:

  - "SEN05 DP Program Engine"   -- AtStartup, runs dp_program.exe --run,
    restart-on-failure. Task Scheduler reacts only to a non-zero exit, so
    a graceful `dp_program stop` (exit 0) is never fought.
  - "SEN05 DP Program Watchdog" -- every 5 minutes, runs
    dp_program.exe --watchdog. Catches the failure mode the engine task
    cannot see: a process that is alive but stuck.

install.ps1 calls into this (via dp_program_entry --setup) rather than
carrying its own Register-ScheduledTask blocks, so the two can no longer
drift apart.

Setup also removes the retired first-generation tasks ("SEN05 DP Program
Live"/"Backfill", which launched .bat wrappers that no longer exist and
would crash-loop against the engine's instance lock). Anything else under
\\SEN05\\ is reported, never deleted -- removing a task the operator did
not ask about is worse than leaving it.

Registering/removing a Scheduled Task needs Administrator rights. If the
current process isn't elevated, these functions relaunch this same .exe
elevated (one UAC prompt) with an internal action flag, let that instance
perform the one requested action and exit.
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path

TASK_FOLDER = "\\SEN05\\"
ENGINE_TASK = "SEN05 DP Program Engine"
WATCHDOG_TASK = "SEN05 DP Program Watchdog"
# Thế hệ đầu, chạy qua run_live.bat / run_backfill.bat -- các .bat đó đã bị
# xoá, task còn sót lại chỉ gây crash-loop mỗi phút khi máy khởi động.
LEGACY_TASKS = ("SEN05 DP Program Live", "SEN05 DP Program Backfill")

ACTION_FLAGS = {"--setup": "install_tasks", "--teardown": "uninstall_tasks"}


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _exe_path() -> Path:
    if not getattr(sys, "frozen", False):
        raise RuntimeError(
            "Chi dang ky Task Scheduler tu dp_program.exe da build, "
            "khong the thuc hien khi chay truc tiep tu source."
        )
    return Path(sys.executable).resolve()


def _relaunch_elevated(action_flag: str) -> None:
    exe = _exe_path()
    # --pause-after: this relaunch opens a brand-new console just for this
    # one action, so it needs to wait for the operator before closing.
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", str(exe), f"{action_flag} --pause-after", str(exe.parent), 1
    )
    if int(result) <= 32:
        raise RuntimeError("Khong xin duoc quyen Administrator (bi tu choi hoac UAC that bai).")
    print("Da mo 1 cua so moi voi quyen Administrator de thuc hien thao tac nay.")


def _run_powershell(script: str, *, check: bool = True) -> str:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
    )
    if completed.stdout.strip():
        print(completed.stdout.strip())
    if check and completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"powershell exited with code {completed.returncode}")
    return completed.stdout


_ENGINE_SCRIPT = """
$ErrorActionPreference = "Stop"
$exePath = "{exe}"
$installDir = "{install_dir}"
$name = "{name}"
$taskFolder = "{folder}"
$action = New-ScheduledTaskAction -Execute $exePath -Argument "--run" -WorkingDirectory $installDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -Priority 5
$settings.ExecutionTimeLimit = "PT0S"
# $env:USERDOMAIN tra ve "WORKGROUP" tren may khong join domain, va
# "WORKGROUP\\administrator" khong phan giai duoc thanh SID. Identity that
# (WindowsIdentity.Name) luon ra dang MACHINE\\User phan giai duoc.
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType S4U -RunLevel Highest
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
# [TimeSpan]::MaxValue fails Task Scheduler's XML "value out of range"
# validation; Task Scheduler has no true "forever" repetition duration,
# so 10 years is the conventional stand-in.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -Priority 7
# $env:USERDOMAIN tra ve "WORKGROUP" tren may khong join domain, va
# "WORKGROUP\\administrator" khong phan giai duoc thanh SID. Identity that
# (WindowsIdentity.Name) luon ra dang MACHINE\\User phan giai duoc.
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType S4U -RunLevel Highest
$definition = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal
Register-ScheduledTask -TaskPath $taskFolder -TaskName $name -InputObject $definition -Force | Out-Null
Write-Host "[ok] Da dang ky Scheduled Task '$taskFolder$name' (kiem tra moi 5 phut)."
"""

_REMOVE_SCRIPT = """
$ErrorActionPreference = "SilentlyContinue"
foreach ($name in @({names})) {{
    $existing = Get-ScheduledTask -TaskPath "{folder}" -TaskName $name -ErrorAction SilentlyContinue
    if ($existing) {{
        Unregister-ScheduledTask -TaskPath "{folder}" -TaskName $name -Confirm:$false
        Write-Host "[ok] Da go Scheduled Task '{folder}$name'."
    }}
}}
"""

_REPORT_OTHERS_SCRIPT = """
$ErrorActionPreference = "SilentlyContinue"
$known = @({known})
Get-ScheduledTask -TaskPath "{folder}" -ErrorAction SilentlyContinue | ForEach-Object {{
    if ($known -notcontains $_.TaskName) {{
        Write-Host ("[chu y] Task la trong {folder}: '" + $_.TaskName + "' -- khong tu dong dung toi.")
    }}
}}
"""


def _quoted(names: tuple[str, ...]) -> str:
    return ",".join(f'"{name}"' for name in names)


def install_tasks() -> None:
    """Đăng ký 2 task chuẩn, gỡ task thế hệ cũ, báo cáo task lạ."""
    if not _is_admin():
        _relaunch_elevated("--setup")
        return
    exe = _exe_path()
    _run_powershell(_REMOVE_SCRIPT.format(names=_quoted(LEGACY_TASKS), folder=TASK_FOLDER), check=False)
    _run_powershell(_ENGINE_SCRIPT.format(exe=exe, install_dir=exe.parent, name=ENGINE_TASK, folder=TASK_FOLDER))
    _run_powershell(_WATCHDOG_SCRIPT.format(exe=exe, install_dir=exe.parent, name=WATCHDOG_TASK, folder=TASK_FOLDER))
    _run_powershell(
        _REPORT_OTHERS_SCRIPT.format(known=_quoted((ENGINE_TASK, WATCHDOG_TASK)), folder=TASK_FOLDER),
        check=False,
    )


def uninstall_tasks() -> None:
    """Gỡ 2 task chuẩn và cả task thế hệ cũ nếu còn sót."""
    if not _is_admin():
        _relaunch_elevated("--teardown")
        return
    _run_powershell(
        _REMOVE_SCRIPT.format(names=_quoted((ENGINE_TASK, WATCHDOG_TASK) + LEGACY_TASKS), folder=TASK_FOLDER),
        check=False,
    )


def engine_task_ready() -> bool:
    """Task engine đã đăng ký và chưa bị disable chưa (--restart kiểm trước)."""
    script = (
        f'$t = Get-ScheduledTask -TaskPath "{TASK_FOLDER}" -TaskName "{ENGINE_TASK}" '
        f'-ErrorAction SilentlyContinue; if ($t -and $t.State -ne "Disabled") {{ Write-Host "READY" }}'
    )
    return "READY" in _run_powershell(script, check=False)


def start_engine_task() -> None:
    """Bật task engine ngay (không cần quyền Administrator cho task của chính mình)."""
    _run_powershell(f'Start-ScheduledTask -TaskPath "{TASK_FOLDER}" -TaskName "{ENGINE_TASK}"')
