"""Process/lock coordination utilities for the DP Program supervisor.

Everything here is a free function or small helper class the
BackendSupervisor class builds on:
same-host process identification, graceful/forced stop coordination
across the three runtime locks (supervisor/live/historical), the queued-
historical-job file, operator-decision logging, and the small JSON
read/write helpers the on-disk supervisor state file uses.
Nothing here imports BackendSupervisor - engine.py imports from this
module, never the other way around.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core_engine.shared.time import utc_iso
from core_engine.util.runtime_state import atomic_write_json as _atomic_write_json
from core_engine.util.runtime_state import load_json as _load_json
from core_engine.util.logkit.activity import log_activity
from core_engine.util.logkit.factory import get_logger
from core_engine.util.logkit.formatters import operation_line
from core_engine.util.notify.discord import notify_backend_event, notify_historical_event, notify_live_event, flush_pending
from core_engine.settings import (
    BACKEND_LOG,
    BACKEND_STATE,
    BACKEND_STOP_FILE,
    HISTORICAL_CANCEL_FILE,
    HISTORICAL_QUEUE_FILE,
    WS_LIVE_STATE,
    ensure_runtime_dirs,
)
from core_engine.util.coordination.locks import (
    DP_PROGRAM_LOCK,
    HISTORICAL_JOB_LOCK,
    LIVE_RUNTIME_LOCK,
    cleanup_stale_lock,
    fetch_lock,
    local_pid_alive,
    request_ws_live_shutdown,
)


logger = get_logger("system", str(BACKEND_LOG), rotating=True, utc=True, pipe_format=True)


def _slog(event: str, *details: str, **fields: Any) -> str:
    return operation_line("SYSTEM", event, *details, **fields)


STOP_TARGETS = (
    ("supervisor", "DP Program supervisor", DP_PROGRAM_LOCK),
    ("live", "Live fetching", LIVE_RUNTIME_LOCK),
    ("historical", "Historical pulling", HISTORICAL_JOB_LOCK),
)


def local_host_names() -> set[str]:
    names = {
        str(os.environ.get("COMPUTERNAME") or "").strip().lower(),
        str(socket.gethostname() or "").strip().lower(),
    }
    return {name for name in names if name}


def same_local_host(host: str | None) -> bool:
    host_name = str(host or "").strip().lower()
    return bool(host_name and host_name in local_host_names())


def _safe_pid(value: Any) -> int | None:
    try:
        pid = int(str(value or "").strip())
    except Exception:
        return None
    return pid if pid > 0 else None


def _active_lock_detail(task_name: str) -> dict[str, Any]:
    record = fetch_lock(task_name, active_only=True)
    if not record:
        return {"active": False, "task_name": task_name}
    meta = record.meta
    return {
        "active": True,
        "task_name": task_name,
        "pid": meta.get("pid"),
        "host": meta.get("host"),
        "owner": meta.get("owner") or meta.get("kind"),
        "started": meta.get("started"),
        "heartbeat": meta.get("heartbeat"),
        "expires_at": str(record.expires_at or ""),
    }


def _stop_target_snapshot() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for kind, name, task_name in STOP_TARGETS:
        record = fetch_lock(task_name, active_only=True)
        if not record:
            rows.append(
                {
                    "kind": kind,
                    "name": name,
                    "task_name": task_name,
                    "active": False,
                    "status": "idle",
                    "pid": None,
                    "note": "not running",
                }
            )
            continue
        meta = record.meta
        pid = _safe_pid(meta.get("pid"))
        rows.append(
            {
                "kind": kind,
                "name": name,
                "task_name": task_name,
                "active": True,
                "status": "running",
                "pid": pid,
                "host": meta.get("host"),
                "owner": meta.get("owner") or meta.get("kind"),
                "started": meta.get("started"),
                "heartbeat": meta.get("heartbeat"),
                "expires_at": str(record.expires_at or ""),
                "same_host": same_local_host(meta.get("host")),
                "note": "active runtime lock",
            }
        )
    return rows


def _wait_for_stop(deadline: float) -> list[dict[str, Any]]:
    rows = _stop_target_snapshot()
    while time.time() < deadline and any(row.get("active") for row in rows):
        time.sleep(1)
        rows = _stop_target_snapshot()
    return rows


def _terminate_local_process(pid: int, *, name: str, reason: str) -> bool:
    if not local_pid_alive(pid):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception as exc:
        logger.error("%s", _slog("Process terminate failed", process=name, pid=pid, reason=exc, result="failed"))
        return False
    logger.warning("%s", _slog("Process terminated after graceful timeout", process=name, pid=pid, reason=reason, result="terminated"))
    for _ in range(20):
        if not local_pid_alive(pid):
            return True
        time.sleep(0.5)
    return not local_pid_alive(pid)


def _normalize_historical_args(args: list[str]) -> list[str]:
    result = list(args)
    if result[:3] == ["-m", "core_engine", "historical"]:
        return result[3:]
    if result[:2] == ["core_engine", "historical"]:
        return result[2:]
    if result[:1] == ["historical"]:
        return result[1:]
    return result


def queue_historical_job(
    args: list[str],
    *,
    title: str = "Historical job",
    requested_by: str = "operator",
) -> dict[str, Any]:
    ensure_runtime_dirs()
    job = {
        "id": uuid.uuid4().hex[:12],
        "queued_at": utc_iso(),
        "title": title,
        "requested_by": requested_by,
        "args": _normalize_historical_args(args),
    }
    HISTORICAL_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with HISTORICAL_QUEUE_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(job, ensure_ascii=True, sort_keys=True) + "\n")
    logger.warning("%s", _slog("Historical job queued", job_id=job["id"], title=title, args=" ".join(job["args"]), result="queued"))
    log_activity(
        "historical_queued",
        component="historical_pulling",
        status="queued",
        message="Historical job was queued for later execution.",
        job_id=job["id"],
        title=title,
        requested_by=requested_by,
    )
    notify_historical_event(
        severity="WARNING",
        title="Historical job queued",
        summary="A historical pulling request was added to the waiting list because another historical job is already running.",
        current_state={"queued_job": title, "job_id": job["id"], "requested_by": requested_by},
        data_result="No data was changed by the queued request yet. It will run after the active historical job finishes.",
        health_risk="Low. The system avoided running two historical jobs at the same time.",
        recommended_action="Let the active historical job finish, then watch historical_pulling.log for the queued job start.",
        trace={"queue_file": str(HISTORICAL_QUEUE_FILE)},
        result="queued",
    )
    flush_pending()
    return job


def record_operator_decision(
    *,
    kind: str,
    decision: str,
    requested_title: str,
    detail: dict[str, Any] | None = None,
) -> None:
    detail = detail or {}
    component = {
        "supervisor": "system",
        "live": "live_fetching",
        "historical": "historical_pulling",
    }.get(kind, "system")
    logger.warning(
        "%s",
        _slog(
            "Operator conflict decision",
            process_group=kind,
            decision=decision,
            requested_action=requested_title,
            active_pid=detail.get("pid"),
            active_owner=detail.get("owner"),
            task=detail.get("task_name"),
            result="handled",
        ),
    )
    log_activity(
        "operator_conflict_decision",
        component=component,
        status="skipped" if decision == "skip" else "queued" if decision == "queue" else "stopping",
        message="Operator handled a duplicate or conflicting request.",
        kind=kind,
        decision=decision,
        requested=requested_title,
        active_pid=detail.get("pid"),
        active_owner=detail.get("owner"),
    )
    notify = {
        "supervisor": notify_backend_event,
        "live": notify_live_event,
        "historical": notify_historical_event,
    }.get(kind, notify_backend_event)
    notify(
        severity="WARNING",
        title="Duplicate request handled",
        summary=f"Operator requested '{requested_title}' while the related {kind} process was already active.",
        current_state={
            "decision": decision,
            "active_pid": detail.get("pid") or "-",
            "active_owner": detail.get("owner") or "-",
        },
        data_result=(
            "The new request was not started."
            if decision == "skip"
            else "The old process will be stopped before the new request starts."
            if decision == "replace"
            else "The new request was placed in the historical queue."
        ),
        health_risk="Low. The system avoided an accidental duplicate process.",
        recommended_action="No action needed unless this was not the intended choice.",
        trace={"task_name": detail.get("task_name", "-")},
        result=decision,
    )
    flush_pending()


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen | None = None
    started_at: float = 0.0
    command: list[str] | None = None
    stdout_handle: Any | None = None
    last_exit_code: int | None = None

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process else None

    @property
    def running(self) -> bool:
        return bool(self.process and self.process.poll() is None)

    def poll(self) -> int | None:
        if not self.process:
            return self.last_exit_code
        code = self.process.poll()
        if code is not None:
            self.last_exit_code = int(code)
            self.close_log()
        return code

    def close_log(self) -> None:
        if self.stdout_handle is not None:
            try:
                self.stdout_handle.close()
            except Exception:
                pass
            self.stdout_handle = None


def _mark_runtime_state_stopped(*, reason: str) -> None:
    """Close stale state files when an external Graceful Stop owns cleanup."""
    stopped_at = utc_iso()
    if not fetch_lock(DP_PROGRAM_LOCK, active_only=True):
        state = _load_json(BACKEND_STATE)
        previous_pid = state.get("pid")
        state.update(
            {
                "status": "stopped",
                "updated_at": stopped_at,
                "stopped_at": stopped_at,
                "stop_reason": reason,
                "previous_pid": previous_pid,
                "pid": None,
                "live_pid": None,
                "live_running": False,
                "historical_pid": None,
                "historical_running": False,
            }
        )
        _atomic_write_json(BACKEND_STATE, state)

    if not fetch_lock(LIVE_RUNTIME_LOCK, active_only=True):
        state = _load_json(WS_LIVE_STATE)
        previous_pid = state.get("pid")
        state.update(
            {
                "status": "stopped",
                "updated_at": stopped_at,
                "stopped_at": stopped_at,
                "stop_reason": reason,
                "previous_pid": previous_pid,
                "pid": None,
                "heartbeat": "stopped",
                "lock_name": LIVE_RUNTIME_LOCK,
            }
        )
        _atomic_write_json(WS_LIVE_STATE, state)


def request_stop(
    reason: str = "operator",
    *,
    wait_sec: int | float = 0,
    force_after_grace: bool = False,
) -> dict[str, Any]:
    ensure_runtime_dirs()
    requested_at = utc_iso()
    BACKEND_STOP_FILE.write_text(
        json.dumps({"requested_at": requested_at, "reason": reason}, sort_keys=True),
        encoding="utf-8",
    )
    HISTORICAL_CANCEL_FILE.write_text(
        json.dumps({"requested_at": requested_at, "reason": reason}, sort_keys=True),
        encoding="utf-8",
    )
    live_signal_sent = request_ws_live_shutdown(logger)
    initial_targets = _stop_target_snapshot()
    active_count = sum(1 for row in initial_targets if row.get("active"))

    logger.warning(
        "%s",
        _slog(
            "Graceful stop requested",
            reason=reason,
            active_processes=active_count,
            live_stop_signal=live_signal_sent,
            result="stopping",
        ),
    )
    log_activity(
        "stop_requested",
        component="system",
        status="stopping",
        message="Graceful Stop requested for every running DP Program process.",
        reason=reason,
        active_processes=active_count,
        live_signal_sent=live_signal_sent,
        historical_cancel_file=HISTORICAL_CANCEL_FILE,
    )

    report: dict[str, Any] = {
        "reason": reason,
        "requested_at": requested_at,
        "wait_sec": int(wait_sec or 0),
        "force_after_grace": bool(force_after_grace),
        "live_signal_sent": bool(live_signal_sent),
        "targets": initial_targets,
        "forced": [],
        "still_running": False,
    }

    if wait_sec and wait_sec > 0:
        logger.info("%s", _slog("Waiting for processes to stop", max_wait_seconds=wait_sec, result="waiting"))
        report["targets"] = _wait_for_stop(time.time() + float(wait_sec))

    if force_after_grace and any(row.get("active") for row in report["targets"]):
        forced: list[dict[str, Any]] = []
        for row in report["targets"]:
            if not row.get("active"):
                continue
            pid = _safe_pid(row.get("pid"))
            if not pid:
                row["status"] = "still_running"
                row["note"] = "active lock has no local PID to terminate"
                continue
            if not row.get("same_host"):
                row["status"] = "still_running"
                row["note"] = "active process is not on this host; not forcing"
                continue
            stopped = _terminate_local_process(pid, name=str(row.get("name") or row.get("kind")), reason=reason)
            forced.append({"name": row.get("name"), "pid": pid, "stopped": stopped})
            if stopped:
                cleanup_stale_lock(str(row["task_name"]), force=True)
        report["forced"] = forced
        report["targets"] = _wait_for_stop(time.time() + 10)

    initial_active = {row["task_name"] for row in initial_targets if row.get("active")}
    final_targets = []
    for row in report["targets"]:
        if row.get("active"):
            row["status"] = "still_running"
        elif row.get("task_name") in initial_active:
            row["status"] = "stopped"
            row["note"] = "stopped after Graceful Stop request"
        final_targets.append(row)
    report["targets"] = final_targets
    report["still_running"] = any(row.get("active") for row in final_targets)

    if report["still_running"]:
        logger.warning(
            "%s",
            _slog(
                "Graceful stop incomplete",
                active_processes=sum(1 for row in final_targets if row.get("active")),
                result="warning",
            ),
        )
        log_activity(
            "stop_incomplete",
            component="system",
            status="warning",
            message="Graceful Stop finished, but at least one process is still running.",
            reason=reason,
            active_processes=sum(1 for row in final_targets if row.get("active")),
        )
    else:
        _mark_runtime_state_stopped(reason=reason)
        logger.info("%s", _slog("Graceful stop completed", active_processes=0, result="stopped"))
        log_activity(
            "stop_completed",
            component="system",
            status="stopped",
            message="Graceful Stop completed. No active DP Program process remains.",
            reason=reason,
            forced_processes=len(report.get("forced") or []),
        )
    return report


def clear_stop_request() -> None:
    for path in (BACKEND_STOP_FILE, HISTORICAL_CANCEL_FILE):
        try:
            path.unlink(missing_ok=True)
        except Exception:
            logger.debug("Could not remove stop flag %s", path, exc_info=True)


def stop_requested() -> bool:
    return BACKEND_STOP_FILE.exists()
