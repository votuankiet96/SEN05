"""Terminal supervisor for the SEN05 DP Program.

The supervisor is intentionally thin. It owns lifecycle, scheduling, restart
policy, and operator state. It does not fetch or transform market data; those
responsibilities stay inside `historical_pulling.py` and `live_fetching.py`.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path
from typing import Any

from core_engine.health import cleanup_old_runtime_files, collect_health
from core_engine.logkit.activity import log_activity
from core_engine.logkit.factory import setup_logger
from core_engine.logkit.formatters import operation_line
from core_engine.reporting.discord import notify_backend_event, notify_historical_event, notify_live_event, tg_flush
from core_engine.settings import (
    APP_ROOT,
    BACKEND,
    BACKEND_HISTORICAL_STDOUT_LOG,
    BACKEND_LIVE_STDOUT_LOG,
    BACKEND_LOG,
    BACKEND_STATE,
    BACKEND_STOP_FILE,
    HISTORICAL_CANCEL_FILE,
    HISTORICAL_QUEUE_FILE,
    LIVE_SUMMARY_LOG,
    WS_LIVE_LOG,
    WS_LIVE_STATE,
    ensure_runtime_dirs,
)
from core_engine.coordination.locks import (
    DP_PROGRAM_LOCK,
    HISTORICAL_JOB_LOCK,
    LIVE_RUNTIME_LOCK,
    acquire,
    cleanup_stale_lock,
    fetch_lock,
    format_payload,
    local_pid_alive,
    release,
    renew,
    request_ws_live_shutdown,
    utc_stamp,
)


logger = setup_logger("system", str(BACKEND_LOG), rotating=True, utc=True, pipe_format=True)


def _slog(event: str, *details: str, **fields: Any) -> str:
    return operation_line("SYSTEM", event, *details, **fields)


STOP_TARGETS = (
    ("supervisor", "DP Program supervisor", DP_PROGRAM_LOCK),
    ("live", "Live fetching", LIVE_RUNTIME_LOCK),
    ("historical", "Historical pulling", HISTORICAL_JOB_LOCK),
)


def _local_host_names() -> set[str]:
    names = {
        str(os.environ.get("COMPUTERNAME") or "").strip().lower(),
        str(socket.gethostname() or "").strip().lower(),
    }
    return {name for name in names if name}


def _same_local_host(host: str | None) -> bool:
    host_name = str(host or "").strip().lower()
    return bool(host_name and host_name in _local_host_names())


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
                "same_host": _same_local_host(meta.get("host")),
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
    tg_flush()
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
    tg_flush()


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


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2)
    last_error: OSError | None = None
    for attempt in range(6):
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            tmp.write_text(data, encoding="utf-8")
            tmp.replace(path)
            return
        except OSError as exc:
            last_error = exc
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt < 5:
                time.sleep(min(0.05 * (2**attempt), 0.8))
    if last_error is not None:
        raise last_error


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


def backend_status() -> dict[str, Any]:
    return _load_json(BACKEND_STATE)


class BackendSupervisor:
    def __init__(
        self,
        *,
        live_enabled: bool | None = None,
        schedule_enabled: bool | None = None,
        smoke_seconds: int | None = None,
        conflict_policy: str = "skip",
    ) -> None:
        ensure_runtime_dirs()
        self.live_enabled = BACKEND.live_auto_start if live_enabled is None else bool(live_enabled)
        self.schedule_enabled = (
            BACKEND.historical_backfill_enabled
            if schedule_enabled is None
            else bool(schedule_enabled)
        )
        self.smoke_deadline = time.time() + int(smoke_seconds) if smoke_seconds else None
        self.conflict_policy = conflict_policy
        self.live = ManagedProcess("live")
        self.historical = ManagedProcess("historical")
        self.live_restart_times: list[float] = []
        state = _load_json(BACKEND_STATE)
        self.last_backfill_date = str(state.get("last_backfill_date") or "")
        raw_slots = state.get("last_backfill_slots")
        self.last_backfill_slots: dict[str, str] = raw_slots if isinstance(raw_slots, dict) else {}
        self.active_historical_slot: str | None = None
        self.active_historical_reason: str = ""
        self.active_historical_started_at: datetime | None = None
        self.historical_failure_count = int(state.get("historical_failure_count") or 0)
        self.historical_retry_not_before = float(state.get("historical_retry_not_before_epoch") or 0.0)
        self._historical_backoff_reported_until = 0.0
        self.startup_historical_due_at: float | None = (
            time.time() + BACKEND.historical_start_delay_sec
            if self.schedule_enabled and BACKEND.historical_start_on_backend_start
            else None
        )
        self._last_health: dict[str, Any] = {}
        self._last_retention_day = ""
        self._started_at = utc_iso()
        self._lock_stop = threading.Event()
        self._supervisor_lock_acquired = False
        self._main_loop_seen_at = time.time()
        self._main_loop_stale_reported = False
        self._supervisor_lock_issue_reported_at = 0.0
        self._supervisor_lock_issue_kind = ""
        self._live_state_issue_reported_at = 0.0
        self._live_state_issue_kind = ""

    def _state_payload(self, status: str = "running", **extra: Any) -> dict[str, Any]:
        payload = {
            "status": status,
            "pid": os.getpid(),
            "started_at": self._started_at,
            "updated_at": utc_iso(),
            "live_enabled": self.live_enabled,
            "schedule_enabled": self.schedule_enabled,
            "live_pid": self.live.pid,
            "live_running": self.live.running,
            "live_last_exit_code": self.live.last_exit_code,
            "historical_pid": self.historical.pid,
            "historical_running": self.historical.running,
            "historical_last_exit_code": self.historical.last_exit_code,
            "last_backfill_date": self.last_backfill_date,
            "last_backfill_slots": self.last_backfill_slots,
            "historical_schedule_utc": BACKEND.historical_backfill_utc,
            "historical_startup_pending": bool(self.startup_historical_due_at),
            "historical_failure_count": self.historical_failure_count,
            "historical_retry_not_before_epoch": self.historical_retry_not_before,
            "historical_retry_wait_seconds": self._historical_retry_wait_seconds(),
            "live_restart_count_last_hour": self._live_restarts_last_hour(),
        }
        payload.update(extra)
        return payload

    def _write_state(self, status: str = "running", **extra: Any) -> None:
        try:
            _atomic_write_json(BACKEND_STATE, self._state_payload(status=status, **extra))
        except OSError as exc:
            logger.warning(
                "%s",
                _slog(
                    "Supervisor state write skipped",
                    file=BACKEND_STATE,
                    reason=exc,
                    result="warning",
                ),
            )

    def _mark_main_loop_alive(self) -> None:
        self._main_loop_seen_at = time.time()
        self._main_loop_stale_reported = False

    def _handle_supervisor_lock_renew_failed(self, payload: str) -> None:
        active = fetch_lock(DP_PROGRAM_LOCK, active_only=True)
        if active is None:
            if acquire(DP_PROGRAM_LOCK, duration_min=10, payload=payload):
                self._supervisor_lock_acquired = True
                self._supervisor_lock_issue_reported_at = 0.0
                self._supervisor_lock_issue_kind = ""
                logger.warning(
                    "%s",
                    _slog(
                        "Supervisor lock reacquired",
                        lock=DP_PROGRAM_LOCK,
                        reason="lock_row_missing_during_renew",
                        result="recovered",
                    ),
                )
                return
            active = fetch_lock(DP_PROGRAM_LOCK, active_only=True)

        detail = _active_lock_detail(DP_PROGRAM_LOCK) if active else {}
        now = time.time()
        kind = "renew_failed_conflict" if active else "renew_failed_missing"
        if self._supervisor_lock_issue_kind == kind and now - self._supervisor_lock_issue_reported_at < 300:
            return
        self._supervisor_lock_issue_kind = kind
        self._supervisor_lock_issue_reported_at = now
        logger.error(
            "%s",
            _slog(
                "Supervisor lock renewal failed",
                lock=DP_PROGRAM_LOCK,
                active_pid=detail.get("pid") or "-",
                active_started=detail.get("started") or "-",
                action="reacquire_attempted",
                result="warning",
            ),
        )

    def _safe_notify(self, notify_fn: Any, **kwargs: Any) -> None:
        try:
            notify_fn(**kwargs)
        except Exception as exc:
            logger.warning("%s", _slog("Notification dispatch failed", reason=exc, result="warning"))

    def _historical_retry_wait_seconds(self) -> int:
        if self.historical_retry_not_before <= 0:
            return 0
        return max(0, int(round(self.historical_retry_not_before - time.time())))

    def _historical_retry_allowed(self, *, reason: str) -> bool:
        wait_seconds = self._historical_retry_wait_seconds()
        if wait_seconds <= 0:
            return True
        if self._historical_backoff_reported_until != self.historical_retry_not_before:
            self._historical_backoff_reported_until = self.historical_retry_not_before
            logger.warning(
                "%s",
                _slog(
                    "Historical retry delayed",
                    reason=reason,
                    wait_seconds=wait_seconds,
                    failures=self.historical_failure_count,
                    retry_after_utc=datetime.fromtimestamp(
                        self.historical_retry_not_before,
                        tz=timezone.utc,
                    ).isoformat(),
                    result="waiting",
                ),
            )
        return False

    def _record_historical_success(self) -> None:
        if self.historical_failure_count or self.historical_retry_not_before:
            logger.info(
                "%s",
                _slog(
                    "Historical retry backoff cleared",
                    previous_failures=self.historical_failure_count,
                    result="recovered",
                ),
            )
        self.historical_failure_count = 0
        self.historical_retry_not_before = 0.0
        self._historical_backoff_reported_until = 0.0

    def _record_historical_failure(self, exit_code: int) -> None:
        self.historical_failure_count += 1
        base = max(30, BACKEND.historical_retry_base_sec)
        cap = max(base, BACKEND.historical_retry_max_sec)
        delay = min(cap, base * (2 ** max(0, self.historical_failure_count - 1)))
        self.historical_retry_not_before = time.time() + delay
        retry_after = datetime.fromtimestamp(self.historical_retry_not_before, tz=timezone.utc).isoformat()
        logger.warning(
            "%s",
            _slog(
                "Historical retry backoff armed",
                exit_code=exit_code,
                failures=self.historical_failure_count,
                retry_delay_seconds=delay,
                retry_after_utc=retry_after,
                reason=self.active_historical_reason or "-",
                schedule_slot=self.active_historical_slot or "-",
                result="waiting",
            ),
        )
        log_activity(
            "historical_retry_delayed",
            component="historical_pulling",
            status="warning",
            message="Historical pulling failed and the supervisor delayed the next retry.",
            exit_code=exit_code,
            failures=self.historical_failure_count,
            retry_delay_seconds=delay,
            retry_after_utc=retry_after,
        )
        self._safe_notify(
            notify_historical_event,
            severity="WARNING",
            title="Historical retry delayed",
            summary=(
                "Historical pulling ended with an error. DP Program will wait before retrying "
                "so a temporary TradingView/network issue does not create a restart storm."
            ),
            current_state={
                "exit_code": exit_code,
                "failure_count": self.historical_failure_count,
                "retry_after_utc": retry_after,
                "schedule_slot": self.active_historical_slot or "-",
            },
            data_result="No duplicate historical job was started immediately. Live fetching can continue during this waiting period.",
            health_risk="Medium. Missing ranges may remain until the next historical retry succeeds.",
            reason="The historical subprocess exited with a non-zero code, usually caused by a transient TradingView or network failure.",
            recommended_action="No immediate action needed if live fetching is healthy. If this repeats after several retries, inspect historical_pulling.log and TradingView connectivity.",
            trace={"system_log": str(BACKEND_LOG), "historical_log": str(BACKEND_HISTORICAL_STDOUT_LOG)},
            result="warning",
        )

    def _clear_startup_historical_pending(self, *, covered_by: str) -> None:
        if self.startup_historical_due_at is None:
            return
        self.startup_historical_due_at = None
        logger.info(
            "%s",
            _slog(
                "Startup historical request covered",
                covered_by=covered_by,
                result="completed",
            ),
        )

    def _supervisor_payload(self) -> str:
        return format_payload(
            {
                "kind": "supervisor",
                "host": os.environ.get("COMPUTERNAME") or "",
                "pid": os.getpid(),
                "started": self._started_at,
                "heartbeat": utc_stamp(),
                "live_enabled": str(self.live_enabled),
                "schedule_enabled": str(self.schedule_enabled),
            }
        )

    def _start_supervisor_heartbeat(self) -> None:
        def loop() -> None:
            stale_after = max(90, BACKEND.health_interval_sec * 4)
            while not self._lock_stop.wait(30):
                age = time.time() - self._main_loop_seen_at
                if age > stale_after:
                    if not self._main_loop_stale_reported:
                        logger.error(
                            "%s",
                            _slog(
                                "Supervisor main loop stale",
                                age_seconds=round(age),
                                threshold_seconds=stale_after,
                                action="stop_lock_heartbeat",
                                result="failed",
                            ),
                        )
                        try:
                            self._write_state(
                                status="unhealthy",
                                reason="supervisor_main_loop_stale",
                                main_loop_age_seconds=round(age),
                            )
                        except Exception:
                            pass
                        self._main_loop_stale_reported = True
                    return
                payload = self._supervisor_payload()
                if not renew(DP_PROGRAM_LOCK, duration_min=10, payload=payload):
                    self._handle_supervisor_lock_renew_failed(payload)

        threading.Thread(target=loop, name="dp-program-lock-heartbeat", daemon=True).start()

    def _acquire_supervisor_lock(self) -> bool:
        payload = self._supervisor_payload()
        if acquire(DP_PROGRAM_LOCK, duration_min=10, payload=payload):
            self._supervisor_lock_acquired = True
            self._start_supervisor_heartbeat()
            return True

        detail = _active_lock_detail(DP_PROGRAM_LOCK)
        if self.conflict_policy != "replace":
            logger.warning(
                "%s",
                _slog(
                    "Supervisor start skipped",
                    active_pid=detail.get("pid"),
                    active_started=detail.get("started"),
                    policy=self.conflict_policy,
                    result="already_running",
                ),
            )
            log_activity(
                "program_start_skipped",
                component="system",
                status="skipped",
                message="DP Program start was skipped because another supervisor is already running.",
                active_pid=detail.get("pid"),
                active_started=detail.get("started"),
            )
            notify_backend_event(
                severity="WARNING",
                title="DP Program start skipped",
                summary="A DP Program supervisor is already running, so the duplicate start request was not launched.",
                current_state={
                    "active_pid": detail.get("pid") or "-",
                    "active_started": detail.get("started") or "-",
                    "requested_policy": self.conflict_policy,
                },
                data_result="No new supervisor was started. Existing live/historical processes remain under the active supervisor.",
                health_risk="Low. This prevents duplicate 24/7 owners from writing conflicting state.",
                recommended_action="Use Status to inspect the active supervisor, or choose restart/replace intentionally.",
                trace={"task_name": DP_PROGRAM_LOCK},
                result="skipped",
            )
            tg_flush()
            return False

        logger.warning("%s", _slog("Supervisor replacement requested", active_pid=detail.get("pid"), result="stopping_old_process"))
        request_stop(reason="replace_supervisor")
        deadline = time.time() + BACKEND.shutdown_grace_sec + 30
        while time.time() < deadline:
            if not fetch_lock(DP_PROGRAM_LOCK, active_only=True):
                break
            time.sleep(2)
        if acquire(DP_PROGRAM_LOCK, duration_min=10, payload=self._supervisor_payload()):
            self._supervisor_lock_acquired = True
            self._start_supervisor_heartbeat()
            return True
        logger.error("%s", _slog("Supervisor replacement blocked", lock=DP_PROGRAM_LOCK, result="failed"))
        notify_backend_event(
            severity="ERROR",
            title="DP Program restart blocked",
            summary="The new supervisor requested a graceful replacement, but the old supervisor did not release its lock in time.",
            current_state={"active_pid": detail.get("pid") or "-", "timeout_seconds": BACKEND.shutdown_grace_sec + 30},
            data_result="The new supervisor did not start.",
            health_risk="Medium. The old process may still be shutting down or may need manual inspection.",
            recommended_action="Run Status, inspect system.log, then retry after the old process is fully stopped.",
            trace={"task_name": DP_PROGRAM_LOCK},
            result="failed",
        )
        tg_flush()
        return False

    def _release_supervisor_lock(self) -> None:
        self._lock_stop.set()
        if self._supervisor_lock_acquired:
            release(DP_PROGRAM_LOCK)
            self._supervisor_lock_acquired = False

    def _read_historical_queue(self) -> list[dict[str, Any]]:
        if not HISTORICAL_QUEUE_FILE.exists():
            return []
        jobs: list[dict[str, Any]] = []
        for line in HISTORICAL_QUEUE_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("%s", _slog("Invalid historical queue row ignored", preview=line[:200], result="skipped"))
                continue
            if isinstance(row, dict):
                jobs.append(row)
        return jobs

    def _write_historical_queue(self, jobs: list[dict[str, Any]]) -> None:
        HISTORICAL_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not jobs:
            HISTORICAL_QUEUE_FILE.unlink(missing_ok=True)
            return
        with HISTORICAL_QUEUE_FILE.open("w", encoding="utf-8") as handle:
            for job in jobs:
                handle.write(json.dumps(job, ensure_ascii=True, sort_keys=True) + "\n")

    def _start_next_queued_historical(self) -> bool:
        if self.historical.running:
            return False
        if not self._historical_retry_allowed(reason="queued_job"):
            return False
        jobs = self._read_historical_queue()
        if not jobs:
            return False
        job = jobs.pop(0)
        self._write_historical_queue(jobs)
        args = [str(item) for item in job.get("args") or []]
        title = str(job.get("title") or "Historical queued job")
        command = [sys.executable, "-m", "core_engine", "historical", *args]
        self._clear_startup_historical_pending(covered_by=f"queued_job:{title}")
        self._spawn(self.historical, command, BACKEND_HISTORICAL_STDOUT_LOG)
        self._safe_notify(
            notify_historical_event,
            severity="INFO",
            title="Queued historical job started",
            summary="The active historical job finished, so DP Program started the next queued historical request.",
            current_state={"queued_job": title, "job_id": job.get("id"), "remaining_queue": len(jobs)},
            data_result="The queued job is now running. Results will be reported when it completes.",
            health_risk="Medium while running. Historical repair can temporarily defer live merges.",
            recommended_action="Watch historical_pulling.log until the job completes.",
            trace={"queue_file": str(HISTORICAL_QUEUE_FILE), "stdout_log": str(BACKEND_HISTORICAL_STDOUT_LOG)},
            result="started",
        )
        return True

    def _child_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env["DP_HISTORICAL_CANCEL_FILE"] = str(HISTORICAL_CANCEL_FILE)
        env["DP_DISABLE_CONSOLE_LOG"] = "1"
        env["DP_LIVE_CONFLICT_POLICY"] = "skip"
        return env

    def _spawn(self, managed: ManagedProcess, command: list[str], stdout_log: Path) -> None:
        stdout_log.parent.mkdir(parents=True, exist_ok=True)
        with stdout_log.open("a", encoding="utf-8", buffering=1) as handle:
            handle.write(
                operation_line(
                    "SUBPROCESS",
                    "Child process starting",
                    process=managed.name,
                    command=" ".join(command),
                    started_at=utc_iso(),
                    result="starting",
                )
                + "\n"
            )
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(
            command,
            cwd=str(APP_ROOT),
            env=self._child_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            creationflags=creationflags,
            close_fds=True,
        )
        managed.process = process
        managed.command = command
        managed.started_at = time.time()
        managed.stdout_handle = None
        managed.last_exit_code = None
        self._write_state(status="starting", spawn_phase=f"{managed.name}_spawned")
        with stdout_log.open("a", encoding="utf-8", buffering=1) as handle:
            handle.write(
                operation_line(
                    "SUBPROCESS",
                    "Child process started",
                    process=managed.name,
                    pid=process.pid,
                    command=" ".join(command),
                    started_at=utc_iso(),
                    result="running",
                )
                + "\n"
            )
        logger.info("%s", _slog("Child process started", process=managed.name, pid=process.pid, command=" ".join(command), result="running"))
        component = "live_fetching" if managed.name == "live" else "historical_pulling"
        log_activity(
            "process_started",
            component=component,
            status="started",
            message=f"{component} worker process started.",
            pid=process.pid,
            stdout_log=stdout_log,
        )

    def start_live(self, *, reason: str = "auto_start") -> None:
        if self.live.running:
            return
        clear_stop_request()
        self._spawn(
            self.live,
            [sys.executable, "-m", "core_engine.live.engine"],
            BACKEND_LIVE_STDOUT_LOG,
        )
        self._safe_notify(
            notify_live_event,
            severity="INFO",
            title="Live feed started",
            summary="The DP Program supervisor started live OHLCV collection.",
            current_state={
                "pid": self.live.pid,
                "reason": reason,
                "monitoring": "heartbeat and restart policy are active",
            },
            data_result="No candles are expected at startup; wait for the next live batch.",
            health_risk="Low. The worker has started and the supervisor is watching it.",
            recommended_action="No action needed now. Check the next live health report.",
            trace={"stdout_log": str(BACKEND_LIVE_STDOUT_LOG)},
            result="started",
        )

    def start_historical(self, *, reason: str = "schedule", schedule_slot: str | None = None) -> None:
        if self.historical.running:
            logger.info("%s", _slog("Historical start skipped", reason=reason, active_pid=self.historical.pid, result="already_running"))
            return
        try:
            HISTORICAL_CANCEL_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        command = [
            sys.executable,
            "-m",
            "core_engine.historical.engine",
            "--mode",
            BACKEND.historical_backfill_mode,
        ]
        if BACKEND.historical_backfill_args:
            command.extend(shlex.split(BACKEND.historical_backfill_args))
        if not reason.startswith("startup"):
            self._clear_startup_historical_pending(covered_by=reason)
        self.active_historical_slot = schedule_slot
        self.active_historical_reason = reason
        self.active_historical_started_at = datetime.now(timezone.utc)
        self._spawn(self.historical, command, BACKEND_HISTORICAL_STDOUT_LOG)
        self._safe_notify(
            notify_historical_event,
            severity="INFO",
            title="Historical backfill started",
            summary="The DP Program supervisor started the scheduled historical repair job.",
            current_state={
                "pid": self.historical.pid,
                "reason": reason,
                "schedule_slot": schedule_slot or "-",
                "mode": BACKEND.historical_backfill_mode,
            },
            data_result="Rows will be reported by the historical job when it finishes.",
            health_risk="Medium while running. It can write many rows and may defer live merges through the maintenance lock.",
            recommended_action="Let it run unless you intentionally need to stop historical repair.",
            trace={"stdout_log": str(BACKEND_HISTORICAL_STDOUT_LOG)},
            result="started",
        )

    def _live_restarts_last_hour(self) -> int:
        cutoff = time.time() - 3600
        self.live_restart_times = [ts for ts in self.live_restart_times if ts >= cutoff]
        return len(self.live_restart_times)

    def _restart_budget_available(self) -> bool:
        limit = BACKEND.live_max_restarts_per_hour
        if limit <= 0:
            return False
        return self._live_restarts_last_hour() < limit

    def _restart_live(self, reason: str) -> None:
        if not self._restart_budget_available():
            logger.error("%s", _slog("Live restart blocked by safety budget", reason=reason, limit_per_hour=BACKEND.live_max_restarts_per_hour, result="failed"))
            self._safe_notify(
                notify_live_event,
                severity="ERROR",
                title="Live restart was blocked",
                summary="The live feed looked unhealthy, but the supervisor did not restart it because the restart safety limit was reached.",
                current_state={
                    "reason": reason,
                    "restart_limit": f"{BACKEND.live_max_restarts_per_hour} per hour",
                    "current_pid": self.live.pid,
                },
                data_result="Live candles may be delayed until the operator checks the process.",
                health_risk="High. Automatic recovery paused to avoid an endless restart loop.",
                reason="The worker restarted too many times in the last hour.",
                recommended_action="Open runtime/logs/operation/live_fetching.log, fix the repeated cause, then restart DP Program manually.",
                trace={"stdout_log": str(BACKEND_LIVE_STDOUT_LOG), "state_file": str(WS_LIVE_STATE)},
                result="failed",
            )
            return
        self.stop_live(reason=f"restart:{reason}", force_after_grace=True)
        if BACKEND.live_restart_cooldown_sec:
            time.sleep(BACKEND.live_restart_cooldown_sec)
        self.live_restart_times.append(time.time())
        self.start_live(reason=f"restart:{reason}")

    def stop_live(self, *, reason: str = "shutdown", force_after_grace: bool = False) -> None:
        request_ws_live_shutdown(logger)
        if not self.live.process:
            return
        if not self.live.running:
            self.live.poll()
            return
        logger.info("%s", _slog("Live process stop requested", pid=self.live.pid, reason=reason, result="stopping"))
        log_activity(
            "process_stop_requested",
            component="live_fetching",
            status="stopping",
            message="Live fetching worker was asked to stop gracefully.",
            pid=self.live.pid,
            reason=reason,
        )
        deadline = time.time() + BACKEND.shutdown_grace_sec
        while time.time() < deadline:
            if self.live.poll() is not None:
                return
            time.sleep(1)
        if force_after_grace and self.live.running:
            logger.warning("%s", _slog("Live process did not stop gracefully", pid=self.live.pid, action="terminate", result="warning"))
            self.live.process.terminate()
            try:
                self.live.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                logger.error("%s", _slog("Live process kill required", pid=self.live.pid, result="forced"))
                self.live.process.kill()
            self.live.poll()

    def stop_historical(self, *, reason: str = "shutdown", force_after_grace: bool = False) -> None:
        if not self.historical.process:
            try:
                HISTORICAL_CANCEL_FILE.unlink(missing_ok=True)
            except Exception:
                pass
            return
        if not self.historical.running:
            self.historical.poll()
            try:
                HISTORICAL_CANCEL_FILE.unlink(missing_ok=True)
            except Exception:
                pass
            return
        HISTORICAL_CANCEL_FILE.write_text(
            json.dumps({"requested_at": utc_iso(), "reason": reason}, sort_keys=True),
            encoding="utf-8",
        )
        logger.info("%s", _slog("Historical process stop requested", pid=self.historical.pid, reason=reason, result="stopping"))
        log_activity(
            "process_stop_requested",
            component="historical_pulling",
            status="stopping",
            message="Historical pulling worker was asked to stop gracefully.",
            pid=self.historical.pid,
            reason=reason,
        )
        deadline = time.time() + BACKEND.shutdown_grace_sec
        while time.time() < deadline:
            if self.historical.poll() is not None:
                try:
                    HISTORICAL_CANCEL_FILE.unlink(missing_ok=True)
                except Exception:
                    pass
                return
            time.sleep(1)
        if force_after_grace and self.historical.running:
            logger.warning("%s", _slog("Historical process did not stop gracefully", pid=self.historical.pid, action="terminate", result="warning"))
            self.historical.process.terminate()
            try:
                self.historical.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                logger.error("%s", _slog("Historical process kill required", pid=self.historical.pid, result="forced"))
                self.historical.process.kill()
            self.historical.poll()
        try:
            HISTORICAL_CANCEL_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def _live_state_age_seconds(self) -> float | None:
        state = _load_json(WS_LIVE_STATE)
        state_pid = state.get("pid")
        if self.live.pid is not None:
            try:
                if int(state_pid or 0) != int(self.live.pid):
                    return None
            except (TypeError, ValueError):
                return None
        value = state.get("updated_at")
        if not value:
            return None
        try:
            text = str(value)
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if self.live.started_at and parsed.timestamp() < self.live.started_at - 5:
                return None
            return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
        except Exception:
            return None

    def _live_output_age_seconds(self) -> float | None:
        if not self.live.started_at:
            return None
        ages: list[float] = []
        for path in (LIVE_SUMMARY_LOG, WS_LIVE_LOG):
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime < self.live.started_at - 5:
                continue
            ages.append(max(0.0, time.time() - stat.st_mtime))
        if not ages:
            return None
        return min(ages)

    def _log_live_state_issue_once(self, kind: str, **fields: Any) -> None:
        now = time.time()
        if kind == self._live_state_issue_kind and now - self._live_state_issue_reported_at < 300:
            return
        self._live_state_issue_kind = kind
        self._live_state_issue_reported_at = now
        logger.warning("%s", _slog(kind, **fields))

    def _poll_children(self) -> None:
        live_code = self.live.poll()
        if live_code is not None and self.live.process is not None:
            logger.warning("%s", _slog("Live process exited", pid=self.live.pid, exit_code=live_code, result="completed" if live_code == 0 else "failed"))
            log_activity(
                "process_exited",
                component="live_fetching",
                status="completed" if live_code == 0 else "failed",
                message="Live fetching worker exited.",
                pid=self.live.pid,
                exit_code=live_code,
            )
            self.live.process = None
            if self.live_enabled and BACKEND.live_restart_on_exit and not stop_requested():
                self._restart_live(f"exit_code={live_code}")

        hist_code = self.historical.poll()
        if hist_code is not None and self.historical.process is not None:
            logger.info("%s", _slog("Historical process exited", pid=self.historical.pid, exit_code=hist_code, result="completed" if hist_code == 0 else "failed"))
            log_activity(
                "process_exited",
                component="historical_pulling",
                status="completed" if hist_code == 0 else "failed",
                message="Historical pulling worker exited.",
                pid=self.historical.pid,
                exit_code=hist_code,
            )
            if hist_code == 0:
                self._record_historical_success()
                finished_date = (
                    self.active_historical_started_at.date().isoformat()
                    if self.active_historical_started_at
                    else datetime.now(timezone.utc).date().isoformat()
                )
                self.last_backfill_date = finished_date
                if self.active_historical_slot:
                    self.last_backfill_slots[self.active_historical_slot] = finished_date
                elif self.active_historical_reason.startswith("startup"):
                    reference = self.active_historical_started_at or datetime.now(timezone.utc)
                    self._mark_due_schedule_slots_completed(reference)
            elif not stop_requested():
                self._record_historical_failure(hist_code)
            self.historical.process = None
            self.active_historical_slot = None
            self.active_historical_reason = ""
            self.active_historical_started_at = None
            try:
                HISTORICAL_CANCEL_FILE.unlink(missing_ok=True)
            except Exception:
                pass

    @staticmethod
    def _schedule_label(value: dtime) -> str:
        return f"{value.hour:02d}:{value.minute:02d}"

    def _schedule_times(self) -> list[dtime]:
        raw = BACKEND.historical_backfill_utc.replace(";", ",").replace(" ", ",")
        times: dict[str, dtime] = {}
        for item in raw.split(","):
            text = item.strip()
            if not text:
                continue
            try:
                hh, mm = [int(part) for part in text.split(":", 1)]
                value = dtime(hour=hh, minute=mm, tzinfo=timezone.utc)
            except Exception:
                logger.error("%s", _slog("Historical schedule setting invalid", value=text, expected="HH:MM", result="failed"))
                continue
            times[self._schedule_label(value)] = value
        return [times[key] for key in sorted(times)]

    def _mark_due_schedule_slots_completed(self, reference: datetime) -> None:
        today = reference.date().isoformat()
        now_time = reference.timetz()
        marked: list[str] = []
        for value in self._schedule_times():
            slot = self._schedule_label(value)
            if now_time >= value:
                self.last_backfill_slots[slot] = today
                marked.append(slot)
        if marked:
            logger.info("%s", _slog("Startup historical run covered due schedule slots", slots=",".join(marked), date=today, result="completed"))

    def _startup_historical_due(self) -> bool:
        if not self.schedule_enabled:
            return False
        if self.startup_historical_due_at is None:
            return False
        if self.historical.running:
            return False
        if not self._historical_retry_allowed(reason="startup"):
            return False
        return time.time() >= self.startup_historical_due_at

    def _scheduled_backfill_due_slot(self) -> str | None:
        if not self.schedule_enabled:
            return None
        if self.historical.running:
            return None
        if not self._historical_retry_allowed(reason="schedule"):
            return None
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        for value in self._schedule_times():
            slot = self._schedule_label(value)
            if self.last_backfill_slots.get(slot) == today:
                continue
            if now.timetz() >= value:
                return slot
        return None

    def _run_retention_once_per_day(self) -> None:
        today = date.today().isoformat()
        if self._last_retention_day == today:
            return
        self._last_retention_day = today
        result = cleanup_old_runtime_files(days=BACKEND.log_retention_days)
        deleted = len(result.get("deleted", []))
        if deleted:
            logger.info("%s", _slog("Runtime retention removed old files", deleted_files=deleted, retention_days=BACKEND.log_retention_days, result="completed"))
        log_activity(
            "runtime_retention",
            component="system",
            status="completed",
            message="Runtime retention check completed.",
            retention_days=BACKEND.log_retention_days,
            deleted_files=deleted,
        )

    def _monitor_live_freshness(self) -> None:
        if not self.live_enabled or not self.live.running:
            return
        if not BACKEND.live_restart_on_stale:
            return
        threshold = BACKEND.live_stale_minutes * 60
        age = self._live_state_age_seconds()
        if age is None:
            output_age = self._live_output_age_seconds()
            if output_age is not None and output_age <= threshold:
                self._log_live_state_issue_once(
                    "Live state heartbeat unavailable but live output is current",
                    output_age_seconds=round(output_age),
                    threshold_seconds=threshold,
                    action="keep_running",
                    result="warning",
                )
                return
            startup_age = time.time() - self.live.started_at if self.live.started_at else 0.0
            if startup_age > threshold:
                logger.error("%s", _slog("Live state file missing", age_seconds=round(startup_age), threshold_seconds=threshold, result="restart_needed"))
                self._restart_live(f"missing_current_state_age={startup_age:.0f}s")
            return
        if age > threshold:
            output_age = self._live_output_age_seconds()
            if output_age is not None and output_age <= threshold:
                self._log_live_state_issue_once(
                    "Live state heartbeat stale but live output is current",
                    state_age_seconds=round(age),
                    output_age_seconds=round(output_age),
                    threshold_seconds=threshold,
                    action="keep_running",
                    result="warning",
                )
                return
            logger.error("%s", _slog("Live heartbeat stale", age_seconds=round(age), threshold_seconds=threshold, result="restart_needed"))
            self._restart_live(f"stale_heartbeat_age={age:.0f}s")

    def run(self) -> int:
        if not self._acquire_supervisor_lock():
            return 5
        clear_stop_request()
        self._write_state(status="starting")
        logger.info(
            "%s",
            _slog(
                "Supervisor starting",
                live_enabled=self.live_enabled,
                schedule_enabled=self.schedule_enabled,
                health_interval_seconds=BACKEND.health_interval_sec,
                result="starting",
            ),
        )
        log_activity(
            "program_started",
            component="system",
            status="started",
            message="DP Program supervisor started.",
            pid=os.getpid(),
            live_enabled=self.live_enabled,
            schedule_enabled=self.schedule_enabled,
        )
        health = collect_health(deep_auth=False, include_database=True)
        self._last_health = health
        if health.get("status") == "fail":
            logger.warning("%s", _slog("Startup health check failed", action="continue_and_report", result="warning"))
        if self.live_enabled:
            self.start_live(reason="backend_start")
            self._write_state(status="running", startup_phase="live_started")

        next_health_at = 0.0
        try:
            while not stop_requested():
                self._mark_main_loop_alive()
                if self.smoke_deadline and time.time() >= self.smoke_deadline:
                    logger.info("%s", _slog("Smoke test timeout reached", result="stopping"))
                    break
                self._poll_children()
                self._monitor_live_freshness()
                if self._start_next_queued_historical():
                    pass
                elif self._startup_historical_due():
                    self.startup_historical_due_at = None
                    self.start_historical(reason=f"startup_after_{BACKEND.historical_start_delay_sec}s")
                else:
                    due_slot = self._scheduled_backfill_due_slot()
                    if due_slot:
                        self.start_historical(reason=f"schedule_{due_slot}_utc", schedule_slot=due_slot)
                self._run_retention_once_per_day()
                now = time.time()
                if now >= next_health_at:
                    self._last_health = collect_health(deep_auth=False, include_database=False)
                    next_health_at = now + BACKEND.health_interval_sec
                self._write_state(status="running", health_status=self._last_health.get("status"))
                time.sleep(1)
        except KeyboardInterrupt:
            logger.warning("%s", _slog("Keyboard interrupt received", result="stopping"))
        except Exception as exc:
            logger.exception("%s", _slog("Supervisor crashed", reason=exc, result="failed"))
            notify_backend_event(
                severity="ERROR",
                title="DP Program supervisor crashed",
                summary="The main terminal supervisor hit an unexpected error and is exiting.",
                current_state={
                    "live_pid": self.live.pid,
                    "historical_pid": self.historical.pid,
                },
                data_result="Live and historical jobs may stop or be left for shutdown cleanup.",
                health_risk="High. The 24/7 process owner is no longer reliable until restarted.",
                reason=f"{type(exc).__name__}: {exc}",
                recommended_action="Review runtime/logs/system/system.log, fix the cause, then start DP Program again.",
                trace={"backend_log": str(BACKEND_LOG)},
                result="failed",
            )
            return 1
        finally:
            self.shutdown()
        return 0

    def shutdown(self) -> None:
        logger.info("%s", _slog("Supervisor shutdown started", live_pid=self.live.pid, historical_pid=self.historical.pid, result="stopping"))
        log_activity(
            "program_stopping",
            component="system",
            status="stopping",
            message="DP Program supervisor is shutting down workers.",
            live_pid=self.live.pid,
            historical_pid=self.historical.pid,
        )
        self._write_state(status="stopping")
        self.stop_historical(reason="backend_shutdown", force_after_grace=True)
        self.stop_live(reason="backend_shutdown", force_after_grace=True)
        self.live.close_log()
        self.historical.close_log()
        self._write_state(
            status="stopped",
            pid=None,
            previous_pid=os.getpid(),
            stopped_at=utc_iso(),
            live_pid=None,
            live_running=False,
            historical_pid=None,
            historical_running=False,
        )
        self._release_supervisor_lock()
        try:
            BACKEND_STOP_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        notify_backend_event(
            severity="INFO",
            title="DP Program supervisor stopped",
            summary="The DP Program supervisor completed shutdown and flushed Discord notifications.",
            current_state={
                "live_exit_code": self.live.last_exit_code,
                "historical_exit_code": self.historical.last_exit_code,
            },
            data_result="No new data will be collected until DP Program is started again.",
            health_risk="Low if this shutdown was intentional; high if the process stopped unexpectedly.",
            recommended_action="If this was not intentional, restart the DP Program terminal command.",
            trace={"backend_state": str(BACKEND_STATE)},
            result="stopped",
        )
        tg_flush()
        logger.info("%s", _slog("Supervisor stopped", live_exit_code=self.live.last_exit_code, historical_exit_code=self.historical.last_exit_code, result="stopped"))
        log_activity(
            "program_stopped",
            component="system",
            status="stopped",
            message="DP Program supervisor stopped cleanly.",
            live_exit_code=self.live.last_exit_code,
            historical_exit_code=self.historical.last_exit_code,
        )


def run_forever(
    *,
    live_enabled: bool | None = None,
    schedule_enabled: bool | None = None,
    smoke_seconds: int | None = None,
    conflict_policy: str = "skip",
) -> int:
    return BackendSupervisor(
        live_enabled=live_enabled,
        schedule_enabled=schedule_enabled,
        smoke_seconds=smoke_seconds,
        conflict_policy=conflict_policy,
    ).run()
