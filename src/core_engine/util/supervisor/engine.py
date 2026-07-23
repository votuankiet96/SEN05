"""24/7 process supervisor for DP Program: BackendSupervisor + run_forever.

Owns lifecycle, the historical backfill schedule, the live-freshness
watchdog, and restart/retry policy. It does
not fetch or transform market data; those responsibilities stay inside
historical/engine.py and live/engine.py, spawned as subprocesses.

Lock/stop-file coordination, the historical job queue, and small JSON
state-file helpers live in process_control.py.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime, time as dtime, timezone
from typing import Any

from core_engine.other.exit_codes import EXIT_CANCELLED, EXIT_ERROR, EXIT_LOCK_CONFLICT
from core_engine.util.health import _age_seconds, cleanup_old_runtime_files, collect_health
from core_engine.shared.time import parse_utc_time
from core_engine.util.logkit import log_activity, operation_line
from core_engine.util.logkit.bootstrap import ingest_emergency_crashes
from core_engine.util.notify.discord import notify_backend_event, notify_historical_event, notify_live_event, flush_pending
from core_engine.util.runtime_state import read_json_snapshot
from core_engine.settings import (
    APP_ROOT,
    BACKEND,
    BACKEND_STATE,
    BACKEND_STOP_FILE,
    HISTORICAL_CANCEL_FILE,
    HISTORICAL_LOG,
    HISTORICAL_QUEUE_FILE,
    LIVE_LOG,
    LOGGING,
    SYSTEM_LOG,
    WS_LIVE_STATE,
    ensure_runtime_dirs,
)
from core_engine.util.coordination.locks import (
    DP_PROGRAM_LOCK,
    acquire,
    cleanup_stale_lock,
    fetch_lock,
    format_payload,
    release,
    renew,
    request_ws_live_shutdown,
    utc_stamp,
)
from core_engine.util.supervisor.process_control import (
    ManagedProcess,
    _active_lock_detail,
    _atomic_write_json,
    _load_json,
    _slog,
    clear_stop_request,
    logger,
    request_stop,
    stop_requested,
    utc_iso,
)


# Backoff schedule for live restarts once the per-hour restart budget
# (BACKEND.live_max_restarts_per_hour) is exhausted. Previously the
# supervisor simply gave up and left live dead until an operator manually
# restarted DP Program - these constants replace that with an automatic,
# non-blocking retry-with-cooldown (mirrors the existing historical-retry
# backoff shape: base * 2^failures, capped).
LIVE_RESTART_BACKOFF_BASE_SEC = 30
LIVE_RESTART_BACKOFF_MAX_SEC = 1800
# Exit codes where an immediate retry is unlikely to help (another process
# already holds the lock, or the exit was itself a cooperative cancel) get
# a longer minimum backoff instead of competing for the normal restart
# budget, which exists for genuine crash-loop protection.
LIVE_RESTART_SLOW_EXIT_CODES = (EXIT_LOCK_CONFLICT, EXIT_CANCELLED)
LIVE_RESTART_SLOW_MIN_SEC = 120
# After this much continuous uptime, a prior failure streak is forgiven -
# without this, one bad hour early in a multi-day run would otherwise
# leave a permanently smaller retry budget for the rest of that run.
LIVE_RESTART_STABLE_RESET_SEC = 1800
LIVE_SEMANTIC_STALE_CONFIRMATIONS = 3


def _child_stderr_level(line: str) -> int:
    text = str(line).lower()
    if any(word in text for word in ("traceback", "exception", "error:", "fatal")):
        return logging.ERROR
    if any(word in text for word in ("warning", "deprecated")):
        return logging.WARNING
    return logging.INFO


def _pump_child_stderr(
    managed: ManagedProcess,
) -> None:
    """Drain one child's stderr into the canonical system stream."""
    handle = managed.stderr_handle
    if handle is None:
        return
    try:
        for raw_line in iter(handle.readline, ""):
            line = raw_line.rstrip("\r\n")
            if line:
                logger.log(
                    _child_stderr_level(line),
                    "%s",
                    operation_line(
                        "SYSTEM",
                        "Child process diagnostic",
                        line,
                        process=managed.name,
                        result="captured",
                    ),
                )
    except (OSError, ValueError):
        pass
    finally:
        try:
            handle.close()
        except Exception:
            pass


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
        self.live_failure_count = 0
        self.live_retry_not_before = 0.0
        self._live_backoff_reported_until = 0.0
        self._live_stable_reset_done_for_start: float | None = None
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
        self._last_runtime_health_status: str | None = None
        self._last_db_health_at = 0.0
        self._last_critical_drain_at = 0.0
        self._critical_drain_thread: threading.Thread | None = None
        self._last_retention_at = 0.0
        self._started_at = utc_iso()
        self._lock_stop = threading.Event()
        self._supervisor_lock_acquired = False
        self._main_loop_seen_at = time.time()
        self._main_loop_stale_reported = False
        self._supervisor_lock_issue_reported_at = 0.0
        self._supervisor_lock_issue_kind = ""
        self._live_state_issue_reported_at = 0.0
        self._live_state_issue_kind = ""
        self._live_semantic_stale_key = ""
        self._live_semantic_stale_confirmations = 0

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
            "live_failure_count": self.live_failure_count,
            "live_retry_wait_seconds": self._live_retry_wait_seconds(),
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
            recommended_action="No immediate action needed if live fetching is healthy. If this repeats after several retries, inspect runtime/logs/historical.log and TradingView connectivity.",
            trace={"system_log": str(SYSTEM_LOG), "historical_log": str(HISTORICAL_LOG)},
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
        # A forced Task stop or VM reboot can leave the prior supervisor row
        # alive for the remainder of its 10-minute SQL lease.  The Scheduled
        # Task retries every minute, but every retry used to exit with code 5
        # until that lease expired.  Remove only a lock that the fencing layer
        # can prove belongs to a dead same-host PID (or whose heartbeat has
        # exceeded the lease itself), then acquire normally.
        cleanup_stale_lock(DP_PROGRAM_LOCK, stale_after_sec=10 * 60)
        acquired = acquire(DP_PROGRAM_LOCK, duration_min=10, payload=payload)
        if not acquired:
            # During VM boot, SQL Server can become reachable between the
            # cleanup fetch and the INSERT. In the reproduced VM-DP6 case,
            # cleanup saw no row only because its DB connection failed; the
            # subsequent acquire connected successfully but hit the prior-
            # boot row and was misreported as a genuine lock conflict. Now
            # that connectivity is established, classify/delete the exact
            # fenced row once more and retry the INSERT.
            cleaned_after_connect = cleanup_stale_lock(
                DP_PROGRAM_LOCK,
                stale_after_sec=10 * 60,
            )
            if cleaned_after_connect:
                acquired = acquire(DP_PROGRAM_LOCK, duration_min=10, payload=payload)

        if acquired:
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
            flush_pending()
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
        flush_pending()
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
        self._spawn(self.historical, command)
        self._safe_notify(
            notify_historical_event,
            severity="INFO",
            title="Queued historical job started",
            summary="The active historical job finished, so DP Program started the next queued historical request.",
            current_state={"queued_job": title, "job_id": job.get("id"), "remaining_queue": len(jobs)},
            data_result="The queued job is now running. Results will be reported when it completes.",
            health_risk="Medium while running. Historical repair can temporarily defer live merges.",
            recommended_action="Watch runtime/logs/historical.log until the job completes.",
            trace={"queue_file": str(HISTORICAL_QUEUE_FILE), "log_file": str(HISTORICAL_LOG)},
            result="started",
        )
        return True

    def _child_env(self, role: str) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env["DP_PROCESS_ROLE"] = role
        env.setdefault("PYTHONFAULTHANDLER", "1")
        env["DP_HISTORICAL_CANCEL_FILE"] = str(HISTORICAL_CANCEL_FILE)
        env["DP_DISABLE_CONSOLE_LOG"] = "1"
        env["DP_LIVE_CONFLICT_POLICY"] = "skip"
        return env

    def _spawn(self, managed: ManagedProcess, command: list[str]) -> None:
        logger.info(
            "%s",
            operation_line(
                "SUBPROCESS",
                "Child process starting",
                process=managed.name,
                command=" ".join(command),
                started_at=utc_iso(),
                result="starting",
            ),
        )
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(
            command,
            cwd=str(APP_ROOT),
            env=self._child_env(managed.name),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
            close_fds=True,
        )
        managed.process = process
        managed.command = command
        managed.started_at = time.time()
        managed.stderr_handle = process.stderr
        managed.stderr_thread = threading.Thread(
            target=_pump_child_stderr,
            args=(managed,),
            name=f"stderr-{managed.name}-{process.pid}",
            daemon=True,
        )
        managed.stderr_thread.start()
        managed.last_exit_code = None
        self._write_state(status="starting", spawn_phase=f"{managed.name}_spawned")
        logger.info(
            "%s",
            operation_line(
                "SUBPROCESS",
                "Child process started",
                process=managed.name,
                pid=process.pid,
                command=" ".join(command),
                started_at=utc_iso(),
                result="running",
            ),
        )
        logger.info("%s", _slog("Child process started", process=managed.name, pid=process.pid, command=" ".join(command), result="running"))
        component = "live_fetching" if managed.name == "live" else "historical_pulling"
        log_activity(
            "process_started",
            component=component,
            status="started",
            message=f"{component} worker process started.",
            pid=process.pid,
            output_log=SYSTEM_LOG,
        )

    def start_live(self, *, reason: str = "auto_start") -> None:
        if self.live.running:
            return
        # clear_stop_request() intentionally does NOT happen here (it used
        # to). If an operator's Graceful Stop request lands in the window
        # between a crash being detected and this restart call, this used
        # to silently erase that stop signal, meaning "stop DP Program"
        # sent during a restart cooldown was ignored. The stop flag is now
        # only cleared once, at supervisor startup (see run()).
        self._spawn(
            self.live,
            [sys.executable, "-m", "core_engine.core.live.engine"],
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
            trace={"log_file": str(LIVE_LOG)},
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
            "core_engine.core.historical.engine",
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
        self._spawn(self.historical, command)
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
            trace={"log_file": str(HISTORICAL_LOG)},
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

    def _live_retry_wait_seconds(self) -> int:
        if self.live_retry_not_before <= 0:
            return 0
        return max(0, int(round(self.live_retry_not_before - time.time())))

    def _arm_live_backoff(self, *, exit_code: int | None, reason: str) -> None:
        """Schedule a non-blocking automatic retry instead of leaving live
        dead. Exit codes where an immediate retry is unlikely to help
        (lock conflict, cooperative cancel) get a longer minimum delay so
        they do not spend the fast retry budget on a near-certain repeat
        failure."""
        self.live_failure_count += 1
        base = LIVE_RESTART_BACKOFF_BASE_SEC
        if exit_code in LIVE_RESTART_SLOW_EXIT_CODES:
            base = max(base, LIVE_RESTART_SLOW_MIN_SEC)
        delay = min(LIVE_RESTART_BACKOFF_MAX_SEC, base * (2 ** max(0, self.live_failure_count - 1)))
        self.live_retry_not_before = time.time() + delay
        retry_after = datetime.fromtimestamp(self.live_retry_not_before, tz=timezone.utc).isoformat()
        logger.warning(
            "%s",
            _slog(
                "Live restart backoff armed",
                reason=reason,
                exit_code=exit_code,
                failures=self.live_failure_count,
                retry_delay_seconds=delay,
                retry_after_utc=retry_after,
                result="waiting",
            ),
        )
        self._safe_notify(
            notify_live_event,
            severity="WARNING",
            title="Live restart delayed",
            summary="The live feed exited and immediate restart was skipped (safety budget or a slow-retry exit "
            "code). DP Program will retry automatically after a backoff instead of staying down.",
            current_state={
                "reason": reason,
                "exit_code": exit_code,
                "failure_count": self.live_failure_count,
                "retry_after_utc": retry_after,
            },
            data_result="No live candles will be collected until the automatic retry succeeds.",
            health_risk="Medium - automatic recovery is scheduled, not abandoned, but data collection is paused "
            "until then.",
            recommended_action="No action needed if this resolves on its own. If failures keep repeating, inspect "
            "runtime/logs/live.log.",
            trace={"log_file": str(LIVE_LOG), "state_file": str(WS_LIVE_STATE)},
            result="waiting",
        )

    def _reset_live_backoff_if_stable(self) -> None:
        """Forgive a prior failure streak after enough continuous uptime,
        so one bad hour early in a multi-day run does not permanently
        shrink the retry budget for the rest of that run."""
        if not self.live.running or not self.live.started_at or not self.live_failure_count:
            return
        if self._live_stable_reset_done_for_start == self.live.started_at:
            return
        if time.time() - self.live.started_at < LIVE_RESTART_STABLE_RESET_SEC:
            return
        logger.info(
            "%s",
            _slog("Live restart backoff cleared after stable runtime", previous_failures=self.live_failure_count, result="recovered"),
        )
        self.live_failure_count = 0
        self.live_retry_not_before = 0.0
        self._live_stable_reset_done_for_start = self.live.started_at

    def _live_retry_due(self) -> bool:
        if not self.live_enabled or self.live.running or not BACKEND.live_restart_on_exit:
            return False
        if self.live_retry_not_before <= 0:
            return False
        return time.time() >= self.live_retry_not_before

    def _retry_live_after_backoff(self) -> None:
        self.live_retry_not_before = 0.0
        self.live_restart_times.append(time.time())
        logger.info("%s", _slog("Live automatic retry starting", result="starting"))
        self.start_live(reason="restart:backoff_retry")

    def _restart_live(self, reason: str, *, exit_code: int | None = None) -> None:
        if exit_code in LIVE_RESTART_SLOW_EXIT_CODES or not self._restart_budget_available():
            if exit_code not in LIVE_RESTART_SLOW_EXIT_CODES:
                logger.error(
                    "%s",
                    _slog(
                        "Live restart budget exhausted - scheduling backoff retry instead of giving up",
                        reason=reason,
                        limit_per_hour=BACKEND.live_max_restarts_per_hour,
                        result="deferred",
                    ),
                )
            self._arm_live_backoff(exit_code=exit_code, reason=reason)
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

    def _live_state_age_seconds(
        self,
        state: dict[str, Any],
        field: str = "updated_at",
    ) -> float | None:
        state_pid = state.get("pid")
        if self.live.pid is not None:
            try:
                if int(state_pid or 0) != int(self.live.pid):
                    return None
            except (TypeError, ValueError):
                return None
        value = state.get(field)
        if not value:
            return None
        parsed = parse_utc_time(value)
        if parsed is None:
            return None
        if self.live.started_at and parsed.timestamp() < self.live.started_at - 5:
            return None
        return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())

    def _reset_live_semantic_stale_confirmation(self) -> None:
        self._live_semantic_stale_key = ""
        self._live_semantic_stale_confirmations = 0

    def _confirm_live_semantic_stale(self, key: str) -> bool:
        if key != self._live_semantic_stale_key:
            self._live_semantic_stale_key = key
            self._live_semantic_stale_confirmations = 1
        else:
            self._live_semantic_stale_confirmations += 1
        return self._live_semantic_stale_confirmations >= LIVE_SEMANTIC_STALE_CONFIRMATIONS

    def _live_output_age_seconds(self) -> float | None:
        if not self.live.started_at:
            return None
        ages: list[float] = []
        paths = [LIVE_LOG]
        for path in paths:
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
                self._restart_live(f"exit_code={live_code}", exit_code=live_code)

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
        now = time.time()
        if now - self._last_retention_at < 3600:
            return
        self._last_retention_at = now
        result = cleanup_old_runtime_files(days=LOGGING.retention_days)
        deleted = len(result.get("deleted", []))
        failures = result.get("failed") or []
        if deleted:
            logger.info("%s", _slog("Runtime retention removed old files", deleted_files=deleted, retention_days=LOGGING.retention_days, result="completed"))
        if failures:
            logger.error(
                "%s",
                _slog(
                    "Runtime retention could not remove one or more files",
                    failure_count=len(failures),
                    first_failure=failures[0],
                    result="failed",
                ),
            )
        log_activity(
            "runtime_retention",
            component="system",
            status="failed" if failures else "completed",
            message="Runtime retention check completed.",
            retention_days=LOGGING.retention_days,
            deleted_files=deleted,
            failures=len(failures),
        )

    def _monitor_live_freshness(self) -> None:
        if not self.live_enabled or not self.live.running:
            self._reset_live_semantic_stale_confirmation()
            return
        if not BACKEND.live_restart_on_stale:
            self._reset_live_semantic_stale_confirmation()
            return
        threshold = BACKEND.live_stale_minutes * 60
        snapshot = read_json_snapshot(WS_LIVE_STATE)
        state = snapshot.data
        if state is None:
            self._reset_live_semantic_stale_confirmation()
            output_age = self._live_output_age_seconds()
            if output_age is not None and output_age <= threshold:
                self._log_live_state_issue_once(
                    "Live state snapshot temporarily unreadable but live output is current",
                    attempts=snapshot.attempts,
                    reason=type(snapshot.error).__name__ if snapshot.error else "unknown",
                    output_age_seconds=round(output_age),
                    threshold_seconds=threshold,
                    action="keep_running",
                    result="warning",
                )
                return
            startup_age = time.time() - self.live.started_at if self.live.started_at else 0.0
            if startup_age > threshold:
                logger.error(
                    "%s",
                    _slog(
                        "Live state snapshot unreadable and live output is stale",
                        attempts=snapshot.attempts,
                        reason=type(snapshot.error).__name__ if snapshot.error else "unknown",
                        age_seconds=round(startup_age),
                        threshold_seconds=threshold,
                        result="restart_needed",
                    ),
                )
                self._restart_live(f"unreadable_state_and_output_age={startup_age:.0f}s")
            return

        age = self._live_state_age_seconds(state)
        if age is None:
            self._reset_live_semantic_stale_confirmation()
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
            self._reset_live_semantic_stale_confirmation()
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
            return

        # Business-progress check: the process heartbeat above is written
        # by its own dedicated thread (LiveStateWriter.heartbeat_loop) and
        # can stay fresh even if the main batch loop itself deadlocks or
        # hangs. batch_completed_at only advances when a batch actually
        # finishes, so a live process that is technically "alive" but
        # stuck looks unhealthy here even though the heartbeat check above
        # passed.
        if self.live.started_at and time.time() - self.live.started_at > threshold:
            batch_age = self._live_state_age_seconds(state, "batch_completed_at")
            if batch_age is not None and batch_age > threshold:
                stale_key = f"batch:{state.get('pid')}:{state.get('batch_completed_at')}"
                if not self._confirm_live_semantic_stale(stale_key):
                    return
                logger.error(
                    "%s",
                    _slog(
                        "Live batch loop stale despite fresh heartbeat",
                        batch_age_seconds=round(batch_age),
                        threshold_seconds=threshold,
                        result="restart_needed",
                    ),
                )
                self._reset_live_semantic_stale_confirmation()
                self._restart_live(f"stale_batch_progress_age={batch_age:.0f}s")
                return
            elif batch_age is None:
                # A child can hang before completing its very first batch,
                # so no batch_completed_at field exists to become stale.
                # Use the batch start timestamp (or child start timestamp
                # while waiting for the first scheduled batch) as the
                # initial semantic-progress deadline. Do not restart a
                # known network-blocked child here: reconnect/cooldown is
                # already active and a process recycle cannot restore the
                # external network.
                state_status = str(state.get("status") or "").lower()
                if state_status not in {"network_blocked", "handoff_waiting"}:
                    reference_field = "batch_started_at" if state_status == "batch_running" else "child_started_at"
                    first_batch_age = self._live_state_age_seconds(state, reference_field)
                    if first_batch_age is not None and first_batch_age > threshold:
                        stale_key = (
                            f"first:{state.get('pid')}:{reference_field}:"
                            f"{state.get(reference_field)}"
                        )
                        if not self._confirm_live_semantic_stale(stale_key):
                            return
                        logger.error(
                            "%s",
                            _slog(
                                "Live first batch did not complete",
                                progress_age_seconds=round(first_batch_age),
                                threshold_seconds=threshold,
                                result="restart_needed",
                            ),
                        )
                        self._reset_live_semantic_stale_confirmation()
                        self._restart_live(f"first_batch_stale_age={first_batch_age:.0f}s")
                        return
                # The snapshot is valid but does not yet prove a stale first
                # batch (for example, network handoff is active or the first
                # batch deadline has not elapsed).  Do not carry a previous
                # stale vote into a later supervisor pass.
                self._reset_live_semantic_stale_confirmation()
            else:
                self._reset_live_semantic_stale_confirmation()
        else:
            self._reset_live_semantic_stale_confirmation()

    def _enforce_historical_runtime_limit(self) -> None:
        """HISTORICAL_MAX_RUNTIME_MINUTES was defined in settings but never
        read anywhere - a stuck/hung historical job (e.g. blocked
        indefinitely waiting on a lock, or a network call with no
        effective timeout) could run forever with nothing to stop it."""
        limit_min = BACKEND.historical_max_runtime_minutes
        if limit_min <= 0 or not self.historical.running or not self.active_historical_started_at:
            return
        elapsed_min = (datetime.now(timezone.utc) - self.active_historical_started_at).total_seconds() / 60.0
        if elapsed_min <= limit_min:
            return
        logger.error(
            "%s",
            _slog(
                "Historical job exceeded max runtime - cancelling",
                elapsed_minutes=round(elapsed_min, 1),
                limit_minutes=limit_min,
                result="cancelling",
            ),
        )
        self._safe_notify(
            notify_historical_event,
            severity="ERROR",
            title="Historical job exceeded max runtime",
            summary=f"The active historical job has been running for {elapsed_min:.0f} minutes "
            f"(limit {limit_min}) and is being cancelled.",
            current_state={"elapsed_minutes": round(elapsed_min, 1), "limit_minutes": limit_min},
            data_result="The job will be asked to stop gracefully, then force-terminated after the usual grace period.",
            health_risk="Medium - a stuck job blocks the warehouse-write lock, which can delay live merges.",
            recommended_action="Check runtime/logs/historical.log for what it was stuck on.",
            trace={"log_file": str(HISTORICAL_LOG)},
            result="cancelling",
        )
        self.stop_historical(reason="max_runtime_exceeded", force_after_grace=True)

    def _drain_critical_alert_outbox(self) -> None:
        """Retry any CRITICAL Discord alerts still stuck undelivered (see
        logkit.critical_outbox) so a webhook/network outage self-heals once
        connectivity returns, instead of only retrying the next time
        something happens to log another CRITICAL record."""
        now = time.time()
        if now - self._last_critical_drain_at < 60:
            return
        if self._critical_drain_thread is not None and self._critical_drain_thread.is_alive():
            return
        self._last_critical_drain_at = now

        def _drain() -> None:
            try:
                from core_engine.util.notify.critical_outbox import critical_alert_outbox

                # Three rows keep one pass below ~45 seconds at the configured
                # connect/read timeouts. Most importantly this network work is
                # never allowed to pause child polling or restart decisions.
                critical_alert_outbox().drain(limit=3)
            except Exception as exc:
                # Do not emit CRITICAL here: that would recursively enqueue a
                # new alert into the outbox whose drain just failed.
                logger.warning(
                    "%s",
                    _slog(
                        "Critical alert outbox retry failed",
                        reason=exc,
                        action="leave_unacked_for_next_retry",
                        result="warning",
                    ),
                )

        self._critical_drain_thread = threading.Thread(
            target=_drain,
            name="critical-outbox-drain",
            daemon=True,
        )
        self._critical_drain_thread.start()

    def _run_periodic_db_health_check(self) -> None:
        """DB-inclusive health check on a slow interval (default 15 min),
        separate from the fast heartbeat-only loop below. Round-2 audit
        BLOCKER-3: before this existed, the only include_database=True
        health check ran once at supervisor startup - an ETL/contract
        failure that started hours or days into a run (the exact scenario
        that produced a 12+ day Fact_OHLCV staleness previously) was never
        checked again until an operator happened to run `doctor` by hand.
        """
        now = time.time()
        if now - self._last_db_health_at < BACKEND.db_health_interval_sec:
            return
        self._last_db_health_at = now
        db_health = collect_health(deep_auth=False, include_database=True)
        checks_by_name = {c.get("name"): c for c in db_health.get("checks", [])}

        contract_check = checks_by_name.get("db_contract")
        if contract_check and contract_check.get("status") == "fail":
            reason = str(contract_check.get("message") or "")
            if not (reason.startswith("contract check could not run") or reason.startswith("db contract check failed")):
                logger.critical(
                    "%s",
                    _slog(
                        "Periodic database contract check failed",
                        reason=reason,
                        result="failed",
                    ),
                )
                self._safe_notify(
                    notify_backend_event,
                    severity="CRITICAL",
                    title="Database contract check failed during 24/7 operation",
                    summary="DWH.usp_LoadDirect no longer matches the version the ETL writer expects. This can "
                    "happen if the procedure was redeployed/rolled back without updating the extended property.",
                    current_state={"reason": reason},
                    data_result="Live/historical may be writing staging rows that never reach Fact_OHLCV.",
                    health_risk="Critical - this is the exact drift that previously caused a multi-day Fact_OHLCV staleness.",
                    recommended_action="Run `python -m core_engine doctor` and scripts/sql/08_migration_usp_loaddirect_v2.sql "
                    "through a controlled deploy, then `python -m core_engine reconcile-fact --apply`.",
                    trace={"migration_script": "scripts/sql/08_migration_usp_loaddirect_v2.sql"},
                    result="failed",
                )

        db_check = checks_by_name.get("database")
        if db_check and db_check.get("status") == "ok":
            latest_bar = (db_check.get("detail") or {}).get("latest_bar")
            if latest_bar and latest_bar.get("bar_time"):
                age = _age_seconds(latest_bar.get("bar_time"))
                if age is not None and self.live_enabled and age > BACKEND.live_stale_minutes * 60 * 4:
                    logger.error(
                        "%s",
                        _slog(
                            "Fact_OHLCV freshness check failed",
                            latest_bar=latest_bar,
                            age_seconds=round(age),
                            result="stale",
                        ),
                    )

                    self._safe_notify(
                        notify_backend_event,
                        severity="ERROR",
                        title="Fact_OHLCV is stale",
                        summary="The most recent row in DWH.Fact_OHLCV is older than expected while live fetching "
                        "is enabled. Candles may be stuck in staging - check reconcile-fact.",
                        current_state={"latest_bar": latest_bar, "age_seconds": round(age)},
                        data_result="Strategies reading Fact_OHLCV may be trading on stale data.",
                        health_risk="High for an AutoTrading system.",
                        recommended_action="Run `python -m core_engine reconcile-fact` to check for staging rows "
                        "stuck behind a broken/skipped ETL call.",
                        trace={"tool": "python -m core_engine reconcile-fact"},
                        result="failed",
                    )

    def _report_runtime_health_transition(self, health: dict[str, Any]) -> None:
        """Make disk/readiness degradation visible without alert spam."""
        runtime_check = next(
            (check for check in health.get("checks", []) if check.get("name") == "runtime"),
            None,
        )
        if not runtime_check:
            return
        status = str(runtime_check.get("status") or "").lower()
        if status == self._last_runtime_health_status:
            return
        previous = self._last_runtime_health_status
        self._last_runtime_health_status = status
        detail = runtime_check.get("detail") or {}
        fields = {
            "free_gb": detail.get("disk_free_gb", "unknown"),
            "free_percent": detail.get("disk_free_percent", "unknown"),
            "previous_status": previous or "startup",
        }
        if status == "fail":
            # Component loggers carry the durable CRITICAL outbox handler.
            logger.critical(
                "%s",
                _slog("Runtime disk health critical", **fields, result="failed"),
            )
        elif status == "warn":
            logger.warning(
                "%s",
                _slog("Runtime disk health degraded", **fields, result="warning"),
            )
        elif status == "ok" and previous in {"warn", "fail"}:
            logger.info(
                "%s",
                _slog("Runtime disk health recovered", **fields, result="recovered"),
            )

    def run(self) -> int:
        if not self._acquire_supervisor_lock():
            return EXIT_LOCK_CONFLICT
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
        recovered_crashes = ingest_emergency_crashes()
        if recovered_crashes:
            logger.warning(
                "%s",
                _slog(
                    "Recovered crash evidence from a previous process",
                    recovered_files=recovered_crashes,
                    result="recorded",
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
        self._report_runtime_health_transition(health)
        if health.get("status") == "fail":
            logger.warning("%s", _slog("Startup health check failed", action="continue_and_report", result="warning"))
        contract_check = next(
            (c for c in health.get("checks", []) if c.get("name") == "db_contract"),
            None,
        )
        if contract_check and contract_check.get("status") == "fail":
            reason = str(contract_check.get("message") or "")
            db_unreachable = reason.startswith("contract check could not run") or reason.startswith("db contract check failed")
            if not db_unreachable:
                # Database IS reachable but usp_LoadDirect is the wrong shape
                # (or predates contract tagging entirely) - every write would
                # either fail loudly or silently drop into a broken ETL
                # path. Refuse to start rather than continue_and_report.
                logger.critical(
                    "%s",
                    _slog(
                        "Database contract check failed - refusing to start",
                        reason=reason,
                        action="run scripts/sql/08_migration_usp_loaddirect_v2.sql through a controlled deploy",
                        result="failed",
                    ),
                )
                self._write_state(status="failed", reason="db_contract_mismatch", contract_detail=contract_check.get("detail"))
                notify_backend_event(
                    severity="CRITICAL",
                    title="DP Program refused to start: database contract mismatch",
                    summary="DWH.usp_LoadDirect is not the version the ETL writer expects. Starting anyway would "
                    "either fail every ETL call or silently accumulate staging rows that never reach Fact_OHLCV.",
                    current_state={"reason": reason},
                    data_result="No live or historical worker was started.",
                    health_risk="Critical - this is the exact drift that caused a multi-day Fact_OHLCV staleness previously.",
                    recommended_action="Run scripts/sql/08_migration_usp_loaddirect_v2.sql through a controlled "
                    "deploy, then `python -m core_engine doctor` to confirm, then restart DP Program.",
                    trace={"migration_script": "scripts/sql/08_migration_usp_loaddirect_v2.sql"},
                    result="failed",
                )
                flush_pending()
                self._release_supervisor_lock()
                return EXIT_ERROR

        if self.schedule_enabled and not self._schedule_times():
            # HISTORICAL_BACKFILL_UTC parsed to zero usable slots (e.g. a
            # typo like "1100,2200" with no colons). Previously each bad
            # token only logged an error and the supervisor kept running
            # with historical_backfill_enabled=True but literally no
            # scheduled slot ever due - a silent, easy-to-miss gap where
            # scheduled backfill silently stopped happening at all (the
            # one-time startup historical run, if enabled, would still
            # fire once, masking the problem until the next day).
            logger.critical(
                "%s",
                _slog(
                    "Historical schedule is enabled but resolved to zero usable time slots",
                    raw_value=BACKEND.historical_backfill_utc,
                    expected_format="HH:MM[,HH:MM...]",
                    result="refused_to_start",
                ),
            )
            self._write_state(status="failed", reason="empty_historical_schedule")
            notify_backend_event(
                severity="CRITICAL",
                title="DP Program refused to start: historical schedule is empty",
                summary=f"HISTORICAL_BACKFILL_UTC={BACKEND.historical_backfill_utc!r} did not parse to any usable "
                "HH:MM slot, but historical backfill is enabled.",
                current_state={"raw_value": BACKEND.historical_backfill_utc},
                data_result="No live or historical worker was started.",
                health_risk="High - scheduled gap repair would silently never run.",
                recommended_action="Fix HISTORICAL_BACKFILL_UTC in config/dp_provider.env (format: HH:MM,HH:MM), "
                "or set HISTORICAL_BACKFILL_ENABLED=0 if no schedule is intended.",
                trace={},
                result="failed",
            )
            flush_pending()
            self._release_supervisor_lock()
            return EXIT_ERROR

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
                ingest_emergency_crashes()
                self._monitor_live_freshness()
                self._reset_live_backoff_if_stable()
                if self._live_retry_due():
                    self._retry_live_after_backoff()
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
                self._run_periodic_db_health_check()
                self._drain_critical_alert_outbox()
                self._enforce_historical_runtime_limit()
                now = time.time()
                if now >= next_health_at:
                    self._last_health = collect_health(deep_auth=False, include_database=False)
                    self._report_runtime_health_transition(self._last_health)
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
                recommended_action="Review runtime/logs/system.log, fix the cause, then start DP Program again.",
                trace={"system_log": str(SYSTEM_LOG)},
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
        flush_pending()
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
