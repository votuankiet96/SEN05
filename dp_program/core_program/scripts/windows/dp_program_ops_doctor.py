"""Operational-layer health report for dp_program.exe. Reports, never fixes.

`dp_program doctor` answers "is the ENGINE ready to run" (SQL, auth,
config). This answers the other half: "is the engine being OPERATED
correctly" -- are the right Scheduled Tasks registered, do they point at
files that still exist, is anything conflicting, is the running engine
actually the one the task started, and is it still producing cycles.

Deliberately read-only: diagnosis is separated from mutation so that
running it can never surprise an operator. Fixing what it finds is
--setup's job (for the two tasks this program owns) or the operator's
(for anything else under \\SEN05\\).
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dp_program_task_setup import ENGINE_TASK, LEGACY_TASKS, TASK_FOLDER, WATCHDOG_TASK

_ROLES = ("live", "backfill")
_LOG_FILENAMES = {"live": "dp_program_live.log", "backfill": "dp_program_backfill.log"}

# Windows PowerShell 5.1 khong co -AsArray, va tu bung mang 1 phan tu thanh
# object don -- nen bao mang bang @() roi chuan hoa lai o phia Python.
_TASK_QUERY = """
$ErrorActionPreference = "SilentlyContinue"
$rows = @(Get-ScheduledTask -TaskPath "{folder}" -ErrorAction SilentlyContinue | ForEach-Object {{
    $info = $_ | Get-ScheduledTaskInfo
    $a = $_.Actions[0]
    [pscustomobject]@{{
        Name    = [string]$_.TaskName
        State   = [string]$_.State
        Execute = [string]$a.Execute
        Args    = [string]$a.Arguments
        Trigger = [string](($_.Triggers | ForEach-Object {{ $_.CimClass.CimClassName }}) -join ",")
        LastRun = [string]$info.LastRunTime
        LastResult = [string]$info.LastTaskResult
    }}
}})
ConvertTo-Json -InputObject $rows -Compress -Depth 4
"""


def _query_tasks() -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", _TASK_QUERY.format(folder=TASK_FOLDER)],
        capture_output=True, text=True,
    )
    try:
        parsed = json.loads(completed.stdout or "[]")
    except ValueError:
        return []
    return [parsed] if isinstance(parsed, dict) else list(parsed)


def _missing_paths(task: dict[str, Any]) -> list[str]:
    """Đường dẫn file trong Execute + Args mà không còn tồn tại.

    Chỉ kiểm Execute là chưa đủ: một task chạy `python.exe -B "<script>.py"`
    hay `powershell -File "<script>.ps1"` có Execute hoàn toàn hợp lệ trong
    khi script thật đã bị dời hoặc xoá -- task vẫn "Ready" nhưng chạy là hỏng.
    """
    candidates = [task.get("Execute") or ""]
    candidates += re.findall(r'"([^"]+\.(?:py|ps1|bat|exe))"', task.get("Args") or "", re.IGNORECASE)
    candidates += re.findall(r'(?<!")(\S+\.(?:py|ps1|bat))(?!")', task.get("Args") or "", re.IGNORECASE)
    return [path for path in candidates if path and not Path(path).is_file()]


def _is_dp_task(task: dict[str, Any]) -> bool:
    """Task này có thuộc dp_program không (thư mục \\SEN05\\ còn chứa chương trình khác)."""
    blob = f"{task.get('Execute') or ''} {task.get('Args') or ''}".lower()
    return "dp_program" in blob


def _check_tasks(findings: list[str]) -> None:
    tasks = {task["Name"]: task for task in _query_tasks()}
    print(f"\n=== Scheduled Task trong {TASK_FOLDER} ===")
    if not tasks:
        findings.append("Khong tim thay task nao -- engine se khong tu chay khi may khoi dong. Chay --setup.")
        print("  (khong co task nao)")
        return
    for name, task in sorted(tasks.items()):
        missing = _missing_paths(task)
        own = "dp" if _is_dp_task(task) else "  "
        mark = "MAT" if missing else "ok "
        print(f"  [{mark}][{own}] {name}: {task.get('Execute')} {task.get('Args') or ''}".rstrip())
        print(f"        state={task.get('State')} trigger={task.get('Trigger')} "
              f"last={task.get('LastRun')} result={task.get('LastResult')}")
        if missing and _is_dp_task(task):
            findings.append(f"Task '{name}' tro vao file khong ton tai: {', '.join(missing)}")
    for name in LEGACY_TASKS:
        if name in tasks:
            findings.append(
                f"Task the he cu '{name}' van con -- se crash-loop khi may khoi dong. Chay --setup de go."
            )
    for name in (ENGINE_TASK, WATCHDOG_TASK):
        if name not in tasks:
            findings.append(f"Thieu task bat buoc '{name}'. Chay --setup.")
    # Chỉ tính task của dp_program: thư mục \SEN05\ còn chứa chương trình
    # khác (tick_program) cũng chạy AtStartup một cách hợp lệ.
    boot = [
        name for name, task in tasks.items()
        if "Boot" in (task.get("Trigger") or "") and _is_dp_task(task)
    ]
    if len(boot) > 1:
        findings.append(
            f"Co {len(boot)} task dp_program cung chay luc khoi dong "
            f"({', '.join(sorted(boot))}) -- tranh nhau cung vai tro."
        )


def _check_engine(config: dict[str, Any], findings: list[str]) -> None:
    from dp_program.engine.runtime import service_status

    print("\n=== Tien trinh engine ===")
    for role in _ROLES:
        status = service_status(config, role)
        enabled = bool(config[role]["enabled"])
        age = status.get("heartbeat_age_seconds")
        print(f"  {role}: enabled={enabled} status={status.get('status')} "
              f"pid={status.get('pid')} alive={status.get('process_alive')} heartbeat_age={age}s")
        if not enabled:
            continue
        if status.get("ok"):
            continue
        if status.get("status") == "stopped":
            findings.append(f"{role}: dang dung chu dong (stopped_at={status.get('stopped_at')}).")
        elif status.get("process_alive"):
            findings.append(f"{role}: tien trinh song nhung heartbeat da {age}s -- co dau hieu treo.")
        else:
            findings.append(f"{role}: khong chay va khong phai dung chu dong -- da chet ngoai y muon.")


def _check_cycles(config: dict[str, Any], findings: list[str]) -> None:
    print("\n=== Chu ky gan nhat trong log ===")
    logs_dir = Path(config["app"]["runtime_dir"]) / "logs"
    for role, filename in _LOG_FILENAMES.items():
        path = logs_dir / filename
        if not path.is_file():
            print(f"  {role}: (chua co log)")
            continue
        marker = "LIVE_CYCLE_COMPLETED" if role == "live" else "BACKFILL_SCHEDULED"
        last = ""
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if marker in line:
                    last = line[:19]
        if not last:
            print(f"  {role}: chua thay {marker}")
            continue
        try:
            age = (datetime.now(timezone.utc)
                   - datetime.strptime(last, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)).total_seconds()
        except ValueError:
            print(f"  {role}: {last} (khong doc duoc moc thoi gian)")
            continue
        print(f"  {role}: {marker} gan nhat luc {last}Z ({round(age / 60)} phut truoc)")
        if role == "live" and age > 3600:
            findings.append(f"live: da {round(age / 60)} phut khong hoan tat chu ky nao.")


def report(config: dict[str, Any] | None = None) -> int:
    """In báo cáo vận hành. Trả 0 nếu không phát hiện vấn đề, 1 nếu có."""
    from dp_program.configuration import load_config

    if config is None:
        config = load_config()
    findings: list[str] = []
    _check_tasks(findings)
    _check_engine(config, findings)
    _check_cycles(config, findings)
    print("\n=== Ket luan ===")
    if not findings:
        print("  Khong phat hien van de o lop van hanh.")
        return 0
    for item in findings:
        print(f"  - {item}")
    return 1
