"""Job process spawning and time-based scheduler for tick maintenance jobs.

Merged from:
  tick_program/service/job_runner.py          — JobProcess, spawn_job, job_command
  tick_program/service/tasks/scheduler.py     — TickScheduler, Job, ServiceConfig, build_jobs

Each job runs as its own ``python -m tick_engine <subcommand>`` subprocess.
Backfill jobs are priority-gated inside the scheduler and all tick rows dedup
on ``EventHash``. Job output is routed to the dedicated tick log (not the
supervisor log) via ``mirror_output_to_logger=False``.
"""

from __future__ import annotations

import logging
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO

from tick_engine.env_safety import child_env
from tick_engine.env_utils import env_flag
from tick_engine.utils_support.lock_coord import (
    CANCEL_ENV,
    cancel_file_for,
    clear_cancel_file,
    write_cancel_file,
)
from tick_engine.utils_support.proc_utils import is_pid_alive, terminate_pid

logger = logging.getLogger("tick_engine.service")

_CREATE_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
_JOB_STOP_GRACE = 20.0
_AUTH_JOBS = {"refresh-token"}
_HISTORY_JOBS = {
    "startup-catchup-backfill",
    "frequent-backfill",
    "hourly-repair",
    "daily-deep-repair",
    "first-run-seed",
    "build-activity-profile",
    "spool-drain",
}
_BACKFILL_JOB_PRIORITY = {
    "first-run-seed": 120,
    "daily-deep-repair": 100,
    "hourly-repair": 80,
    "startup-catchup-backfill": 60,
    "frequent-backfill": 40,
}
_BACKFILL_WAIT_LOG_SECONDS = 60.0
_JOB_FAILURE_RETRY_SECONDS = 300.0
_CHILD_LOG_PREFIX_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\s+\|\s+[A-Z]{4,8}\s+\|\s+"
)
_LOG_AREA_WIDTH = 12
_LOG_ITEM_WIDTH = 26


def _is_backfill_job(job_name: str) -> bool:
    return job_name in _BACKFILL_JOB_PRIORITY


def op_line(area: str, item: str, detail: str = "") -> str:
    return f"{area:<{_LOG_AREA_WIDTH}} | {item:<{_LOG_ITEM_WIDTH}} | {detail}"


def _clean_child_output_line(line: str) -> str:
    return _CHILD_LOG_PREFIX_RE.sub("", line.rstrip("\r\n"))


def _parse_utc_text(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _manual_backfill_active(max_age_seconds: int = 900) -> dict[str, object] | None:
    """Return the current manual backfill progress, ignoring stale metadata."""
    from tick_engine.settings import RUN_DIR

    progress_dir = RUN_DIR / "backfill_batches"
    try:
        if not progress_dir.exists():
            return None
        paths = sorted(
            progress_dir.glob("manual_backfill_*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None

    now = datetime.now(timezone.utc)
    for path in paths[:20]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if payload.get("status") != "RUNNING":
            continue

        host = str(payload.get("host") or "")
        pid_raw = payload.get("process_id")
        pid_alive = False
        try:
            pid = int(pid_raw) if pid_raw is not None else 0
        except (TypeError, ValueError):
            pid = 0
        if pid > 0 and host.lower() == socket.gethostname().lower():
            pid_alive = is_pid_alive(pid)

        updated_at = payload.get("updated_at_utc")
        updated_dt = _parse_utc_text(updated_at)
        if updated_dt is None:
            try:
                updated_dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                updated_dt = None
        if updated_dt is not None:
            age_seconds = int((now - updated_dt).total_seconds())
            if age_seconds > max_age_seconds and not pid_alive:
                continue
        else:
            age_seconds = None
            if not pid_alive:
                continue

        batches = payload.get("batches") or []
        current: dict[str, object] | None = None
        if isinstance(batches, list) and batches:
            running = [
                batch
                for batch in batches
                if isinstance(batch, dict) and batch.get("status") == "RUNNING"
            ]
            candidate = running[-1] if running else batches[-1]
            if isinstance(candidate, dict):
                current = candidate

        return {
            "path": str(path),
            "updated_at_utc": updated_at,
            "updated_age_seconds": age_seconds,
            "host": payload.get("host"),
            "process_id": payload.get("process_id"),
            "current_batch": current,
        }
    return None


# ---------------------------------------------------------------------------
# Job process infrastructure (from job_runner.py)
# ---------------------------------------------------------------------------


def job_command(args: list[str]) -> list[str]:
    """Build the argv for a child invocation of the tick_engine CLI.

    A PyInstaller build has no ``python -m`` to call, so the frozen executable
    re-dispatches on its own argv. In source mode we invoke the module normally.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-B", "-m", "tick_engine", *args]


class JobProcess:
    """Non-blocking handle around a spawned job subprocess."""

    def __init__(
        self,
        label: str,
        proc: subprocess.Popen,
        *,
        file_handle: IO[str] | None = None,
        tee_thread: threading.Thread | None = None,
        cancel_file: Path | None = None,
    ) -> None:
        self.label = label
        self.proc = proc
        self._file = file_handle
        self._tee = tee_thread
        self.cancel_file = cancel_file
        self._closed = False
        self.started_mono = time.monotonic()
        self.last_output_mono = self.started_mono

    @property
    def pid(self) -> int:
        return self.proc.pid

    def poll(self) -> int | None:
        return self.proc.poll()

    @property
    def returncode(self) -> int | None:
        return self.proc.returncode

    def mark_output(self) -> None:
        self.last_output_mono = time.monotonic()

    def idle_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.last_output_mono)

    def terminate(self, timeout: float = 10.0) -> None:
        if self.proc.poll() is None:
            terminate_pid(self.proc.pid, timeout=timeout)
        self.close()

    def request_cancel(self, reason: str = "cancel requested") -> None:
        write_cancel_file(self.cancel_file, reason)

    def close(self) -> None:
        if self._closed:
            return
        if self._tee is not None and self._tee.is_alive():
            self._tee.join(timeout=2.0)
        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            except Exception:
                pass
        self._closed = True


def _open_log(output_log_path: Path, header: str) -> IO[str]:
    output_log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(output_log_path, "a", encoding="utf-8")
    handle.write(header)
    handle.flush()
    return handle


def spawn_job(
    args: list[str],
    *,
    output_log_path: Path | None = None,
    mirror_output_to_logger: bool = True,
    label: str | None = None,
    extra_env: dict[str, str] | None = None,
    cancel_file: Path | None = None,
) -> JobProcess:
    """Start a tick CLI subcommand and return a non-blocking handle.

    ``output_log_path`` receives the child's stdout/stderr (appended). When
    ``mirror_output_to_logger`` is true the output is also emitted on the
    service logger; tick jobs set it false so verbose per-tick detail stays out
    of the supervisor/console log.
    """
    label = label or (args[0] if args else "job")
    cmd = job_command(args)
    env = child_env()
    if cancel_file is not None:
        clear_cancel_file(cancel_file)
        env[CANCEL_ENV] = str(cancel_file)
    if extra_env:
        env.update({str(key): str(value) for key, value in extra_env.items()})
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    header = f"\n==== {stamp}Z {label}: {' '.join(args)} ====\n"

    if mirror_output_to_logger:
        logger.info("")
        logger.info(op_line("Job", label, f"command={' '.join(args)}"))
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=_CREATE_FLAGS,
        )
        handle = _open_log(Path(output_log_path), header) if output_log_path else None
        job_handle = JobProcess(label, proc, file_handle=handle, cancel_file=cancel_file)

        def _tee() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                job_handle.mark_output()
                clean_line = _clean_child_output_line(line)
                if handle is not None:
                    handle.write(line)
                    handle.flush()
                if clean_line:
                    logger.info(op_line("Output", label, clean_line))

        tee = threading.Thread(target=_tee, name=f"job-tee-{label}", daemon=True)
        job_handle._tee = tee
        tee.start()
        return job_handle

    handle = _open_log(Path(output_log_path), header) if output_log_path else None
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=handle if handle is not None else subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        creationflags=_CREATE_FLAGS,
    )
    return JobProcess(label, proc, file_handle=handle, cancel_file=cancel_file)


# ---------------------------------------------------------------------------
# Scheduler (from scheduler.py)
# ---------------------------------------------------------------------------


def _to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


@dataclass
class ServiceConfig:
    token_refresh_interval: float
    check_interval: float
    spool_drain_interval: float
    auto_repair_stale_runs: bool
    stale_run_min_age_seconds: int
    backfill_delay_sec: int
    startup_catchup_enabled: bool
    startup_catchup_lookback_min: int
    frequent_backfill_interval: float
    frequent_backfill_lookback_min: int
    frequent_backfill_batch_min: int
    hourly_repair_enabled: bool
    hourly_repair_interval: float
    hourly_repair_lookback_min: int
    hourly_repair_batch_min: int
    daily_deep_repair_utc: str
    daily_deep_repair_lookback_min: int
    daily_deep_repair_batch_min: int
    daily_deep_repair_timeout_seconds: int
    daily_deep_repair_cooldown_hours: float
    daily_maintain_utc: str
    daily_health_summary_utc: str
    first_run_backfill_days: int
    scheduled_request_timeout_seconds: float
    scheduled_batch_timeout_seconds: int
    scheduled_backfill_max_attempts: int
    scheduled_backfill_retry_sleep_seconds: float
    scheduled_backfill_retry_sleep_max_seconds: float
    child_idle_timeout_seconds: int


def load_service_config() -> ServiceConfig:
    import tick_engine.settings as c

    return ServiceConfig(
        token_refresh_interval=float(c.TICK_TOKEN_REFRESH_INTERVAL_SECONDS),
        check_interval=float(c.TICK_CHECK_INTERVAL_SECONDS),
        spool_drain_interval=float(c.TICK_SPOOL_DRAIN_INTERVAL_SECONDS),
        auto_repair_stale_runs=bool(c.TICK_AUTO_REPAIR_STALE_RUNS_ENABLED),
        stale_run_min_age_seconds=int(c.TICK_STALE_RUN_MIN_AGE_SECONDS),
        backfill_delay_sec=int(c.TICK_BACKFILL_DELAY_SECONDS),
        startup_catchup_enabled=bool(c.TICK_STARTUP_CATCHUP_ENABLED),
        startup_catchup_lookback_min=int(c.TICK_STARTUP_CATCHUP_LOOKBACK_MINUTES),
        frequent_backfill_interval=float(c.TICK_FREQUENT_BACKFILL_INTERVAL_SECONDS),
        frequent_backfill_lookback_min=int(c.TICK_FREQUENT_BACKFILL_LOOKBACK_MINUTES),
        frequent_backfill_batch_min=int(c.TICK_FREQUENT_BACKFILL_BATCH_MINUTES),
        hourly_repair_enabled=bool(c.TICK_HOURLY_REPAIR_ENABLED),
        hourly_repair_interval=float(c.TICK_HOURLY_REPAIR_INTERVAL_SECONDS),
        hourly_repair_lookback_min=int(c.TICK_HOURLY_REPAIR_LOOKBACK_MINUTES),
        hourly_repair_batch_min=int(c.TICK_HOURLY_REPAIR_BATCH_MINUTES),
        daily_deep_repair_utc=str(c.TICK_DAILY_DEEP_REPAIR_UTC),
        daily_deep_repair_lookback_min=int(c.TICK_DAILY_DEEP_REPAIR_LOOKBACK_MINUTES),
        daily_deep_repair_batch_min=int(c.TICK_DAILY_DEEP_REPAIR_BATCH_MINUTES),
        daily_deep_repair_timeout_seconds=int(c.TICK_DAILY_DEEP_REPAIR_TIMEOUT_SECONDS),
        daily_deep_repair_cooldown_hours=float(c.TICK_DAILY_DEEP_REPAIR_COOLDOWN_HOURS),
        daily_maintain_utc=str(c.TICK_DAILY_MAINTAIN_UTC),
        daily_health_summary_utc=str(c.TICK_DAILY_HEALTH_SUMMARY_UTC),
        first_run_backfill_days=int(c.TICK_FIRST_RUN_BACKFILL_DAYS),
        scheduled_request_timeout_seconds=float(c.TICK_SCHEDULED_REQUEST_TIMEOUT_SECONDS),
        scheduled_batch_timeout_seconds=int(c.TICK_SCHEDULED_BATCH_TIMEOUT_SECONDS),
        scheduled_backfill_max_attempts=int(c.TICK_SCHEDULED_BACKFILL_MAX_ATTEMPTS),
        scheduled_backfill_retry_sleep_seconds=float(c.TICK_SCHEDULED_BACKFILL_RETRY_SLEEP_SECONDS),
        scheduled_backfill_retry_sleep_max_seconds=float(c.TICK_SCHEDULED_BACKFILL_RETRY_SLEEP_MAX_SECONDS),
        child_idle_timeout_seconds=int(c.TICK_CHILD_IDLE_TIMEOUT_SECONDS),
    )


@dataclass
class Job:
    """One scheduled job. Cadence is either interval-based or daily-at-time.

    ``arg_builder`` lets time-window jobs (backfill) compute fresh ``--from/--to``
    at spawn time; otherwise ``args`` is used verbatim.

    ``startup_cooldown_hours``: when set and ``run_at_startup=True``, the job is
    skipped at startup if a persisted ``cooldown_path`` records a successful run
    within the last N hours. This prevents a repeat service restart from
    triggering another gap fill when one just ran in the current day.
    ``cooldown_path``: the file that holds the ISO timestamp of the last run.
    """

    name: str
    args: list[str] | None = None
    arg_builder: Callable[[], list[str]] | None = None
    interval_seconds: float | None = None
    daily_time_utc: str | None = None
    run_at_startup: bool = False
    startup_only: bool = False
    startup_cooldown_hours: float = 0.0
    cooldown_path: Path | None = None
    _next_run: float = field(default=0.0, repr=False)
    _last_daily_date: str = field(default="", repr=False)
    _startup_ran: bool = field(default=False, repr=False)
    _retry_not_before: float = field(default=0.0, repr=False)

    def build_args(self) -> list[str]:
        if self.arg_builder is not None:
            return self.arg_builder()
        return list(self.args or [])

    def _cooldown_active(self, now_utc: datetime) -> bool:
        """Return True if the startup cooldown is still in effect."""
        if not self.startup_cooldown_hours or self.cooldown_path is None:
            return False
        try:
            ts_text = self.cooldown_path.read_text(encoding="utf-8").strip()
            last_run = datetime.fromisoformat(ts_text)
            if last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=timezone.utc)
            elapsed_hours = (now_utc - last_run).total_seconds() / 3600.0
            return elapsed_hours < self.startup_cooldown_hours
        except Exception:
            return False

    def init_timer(self, now_mono: float) -> None:
        if self.interval_seconds is not None:
            self._next_run = now_mono if self.run_at_startup else now_mono + self.interval_seconds

    def due(self, now_mono: float, now_utc: datetime) -> bool:
        if now_mono < self._retry_not_before:
            return False
        if self.startup_only:
            return bool(self.run_at_startup and not self._startup_ran)
        if self.daily_time_utc:
            today = now_utc.strftime("%Y-%m-%d")
            if self._last_daily_date == today:
                return False
            hour, minute = (int(part) for part in self.daily_time_utc.split(":"))
            target = now_utc.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if self._last_daily_date == "":
                if self._cooldown_active(now_utc):
                    logger.info(
                        op_line(
                            "Job",
                            self.name,
                            f"skipped | reason=startup cooldown active | cooldown={self.startup_cooldown_hours:.0f}h",
                        )
                    )
                    self._last_daily_date = today
                    return False
                return self.run_at_startup or now_utc >= target
            return now_utc >= target
        if self.interval_seconds is not None:
            return now_mono >= self._next_run
        return False

    def mark_ran(self, now_mono: float, now_utc: datetime) -> None:
        self._retry_not_before = 0.0
        if self.startup_only:
            self._startup_ran = True
        if self.daily_time_utc:
            self._last_daily_date = now_utc.strftime("%Y-%m-%d")
        if self.interval_seconds is not None:
            self._next_run = now_mono + self.interval_seconds

    def mark_failed(self, now_mono: float, retry_seconds: float = _JOB_FAILURE_RETRY_SECONDS) -> None:
        self._retry_not_before = now_mono + max(1.0, float(retry_seconds))

    def mark_success(self, now_utc: datetime) -> None:
        if self.cooldown_path is None:
            return
        try:
            self.cooldown_path.parent.mkdir(parents=True, exist_ok=True)
            self.cooldown_path.write_text(now_utc.isoformat(), encoding="utf-8")
        except Exception:
            logger.warning(op_line("Job", self.name, "warning | could not write cooldown file"))

    def describe(self) -> str:
        if self.startup_only:
            cadence = "startup-only"
        elif self.daily_time_utc:
            cadence = f"daily@{self.daily_time_utc}Z"
        else:
            cadence = f"every {self.interval_seconds:.0f}s"
        parts = [f"cadence={cadence}"]
        if self.run_at_startup:
            parts.append("startup=yes")
        if self.startup_cooldown_hours:
            parts.append(f"cooldown={self.startup_cooldown_hours:.0f}h")
        return " | ".join(parts)


def _batched_backfill_arg_builder(
    job_name: str,
    lookback_minutes: int,
    delay_seconds: int,
    *,
    batch_minutes: int,
    request_timeout_seconds: float,
    timeout_per_batch_seconds: int,
    max_attempts: int,
    retry_sleep_seconds: float,
    retry_sleep_max_seconds: float,
    notify_summary: bool,
    notify_success_summary: bool = True,
) -> Callable[[], list[str]]:
    def _build() -> list[str]:
        from tick_engine.settings import RUN_DIR

        to_dt = datetime.now(timezone.utc) - timedelta(seconds=delay_seconds)
        from_dt = to_dt - timedelta(minutes=lookback_minutes)
        from_ms = _to_ms(from_dt)
        to_ms = _to_ms(to_dt)
        progress_path = (
            RUN_DIR
            / "backfill_batches"
            / f"scheduled_{job_name}_{from_ms}_{to_ms}.json"
        )
        args = [
            "backfill-batched",
            "--from",
            str(from_ms),
            "--to",
            str(to_ms),
            "--batch-minutes",
            str(max(1, int(batch_minutes))),
            "--overlap-seconds",
            "60",
            "--wait-lock-seconds",
            "0",
            "--request-timeout",
            str(float(request_timeout_seconds)),
            "--timeout-per-batch",
            str(max(1, int(timeout_per_batch_seconds))),
            "--max-attempts",
            str(max(1, int(max_attempts))),
            "--retry-sleep-seconds",
            str(max(0.0, float(retry_sleep_seconds))),
            "--retry-sleep-max-seconds",
            str(max(0.0, float(retry_sleep_max_seconds))),
            "--progress-file",
            str(progress_path),
        ]
        if notify_summary:
            args.append("--notify-summary")
            if not notify_success_summary:
                args.append("--no-notify-success-summary")
        return args

    return _build


def build_jobs(cfg: ServiceConfig) -> list[Job]:
    from tick_engine.settings import DEEP_REPAIR_LAST_RUN

    check_args = ["check", "--notify"]
    if cfg.auto_repair_stale_runs:
        check_args += [
            "--auto-repair-stale-runs",
            "--stale-run-min-age-seconds",
            str(max(0, int(cfg.stale_run_min_age_seconds))),
        ]
    jobs: list[Job] = [
        Job(
            "refresh-token",
            args=["refresh-token", "--save"],
            interval_seconds=cfg.token_refresh_interval,
            run_at_startup=True,
        ),
        Job(
            "check",
            args=check_args,
            interval_seconds=cfg.check_interval,
        ),
        Job(
            "spool-drain",
            args=["spool-drain"],
            interval_seconds=cfg.spool_drain_interval,
        ),
        Job(
            "build-activity-profile",
            args=["build-activity-profile"],
            daily_time_utc=cfg.daily_maintain_utc,
        ),
        Job(
            "daily-health-summary",
            args=["check", "--notify", "--notify-summary"],
            daily_time_utc=cfg.daily_health_summary_utc,
        ),
    ]
    if cfg.startup_catchup_enabled:
        jobs.append(
            Job(
                "startup-catchup-backfill",
                arg_builder=_batched_backfill_arg_builder(
                    "startup-catchup-backfill",
                    cfg.startup_catchup_lookback_min,
                    cfg.backfill_delay_sec,
                    batch_minutes=cfg.frequent_backfill_batch_min,
                    request_timeout_seconds=cfg.scheduled_request_timeout_seconds,
                    timeout_per_batch_seconds=cfg.scheduled_batch_timeout_seconds,
                    max_attempts=cfg.scheduled_backfill_max_attempts,
                    retry_sleep_seconds=cfg.scheduled_backfill_retry_sleep_seconds,
                    retry_sleep_max_seconds=cfg.scheduled_backfill_retry_sleep_max_seconds,
                    notify_summary=True,
                ),
                run_at_startup=True,
                startup_only=True,
            )
        )
    jobs.append(
        Job(
            "frequent-backfill",
            arg_builder=_batched_backfill_arg_builder(
                "frequent-backfill",
                cfg.frequent_backfill_lookback_min,
                cfg.backfill_delay_sec,
                batch_minutes=cfg.frequent_backfill_batch_min,
                request_timeout_seconds=cfg.scheduled_request_timeout_seconds,
                timeout_per_batch_seconds=cfg.scheduled_batch_timeout_seconds,
                max_attempts=cfg.scheduled_backfill_max_attempts,
                retry_sleep_seconds=cfg.scheduled_backfill_retry_sleep_seconds,
                retry_sleep_max_seconds=cfg.scheduled_backfill_retry_sleep_max_seconds,
                notify_summary=False,
            ),
            interval_seconds=cfg.frequent_backfill_interval,
        )
    )
    if cfg.hourly_repair_enabled:
        jobs.append(
            Job(
                "hourly-repair",
                arg_builder=_batched_backfill_arg_builder(
                    "hourly-repair",
                    cfg.hourly_repair_lookback_min,
                    cfg.backfill_delay_sec,
                    batch_minutes=cfg.hourly_repair_batch_min,
                    request_timeout_seconds=cfg.scheduled_request_timeout_seconds,
                    timeout_per_batch_seconds=cfg.scheduled_batch_timeout_seconds,
                    max_attempts=cfg.scheduled_backfill_max_attempts,
                    retry_sleep_seconds=cfg.scheduled_backfill_retry_sleep_seconds,
                    retry_sleep_max_seconds=cfg.scheduled_backfill_retry_sleep_max_seconds,
                    notify_summary=False,
                ),
                interval_seconds=cfg.hourly_repair_interval,
            )
        )
    jobs.append(
        Job(
            "daily-deep-repair",
            arg_builder=_batched_backfill_arg_builder(
                "daily-deep-repair",
                cfg.daily_deep_repair_lookback_min,
                cfg.backfill_delay_sec,
                batch_minutes=cfg.daily_deep_repair_batch_min,
                request_timeout_seconds=cfg.scheduled_request_timeout_seconds,
                timeout_per_batch_seconds=cfg.scheduled_batch_timeout_seconds,
                max_attempts=cfg.scheduled_backfill_max_attempts,
                    retry_sleep_seconds=cfg.scheduled_backfill_retry_sleep_seconds,
                    retry_sleep_max_seconds=cfg.scheduled_backfill_retry_sleep_max_seconds,
                    notify_summary=True,
                    notify_success_summary=True,
                ),
                daily_time_utc=cfg.daily_deep_repair_utc,
            run_at_startup=False,
            startup_cooldown_hours=cfg.daily_deep_repair_cooldown_hours,
            cooldown_path=DEEP_REPAIR_LAST_RUN,
        )
    )
    if env_flag("CTRADER_FTMO_TICK_FIRST_RUN_AUTO_START", False):
        jobs.append(
            Job(
                "first-run-seed",
                arg_builder=_batched_backfill_arg_builder(
                    "first-run-seed",
                    cfg.first_run_backfill_days * 24 * 60,
                    cfg.backfill_delay_sec,
                    batch_minutes=cfg.daily_deep_repair_batch_min,
                    request_timeout_seconds=cfg.scheduled_request_timeout_seconds,
                    timeout_per_batch_seconds=cfg.scheduled_batch_timeout_seconds,
                    max_attempts=cfg.scheduled_backfill_max_attempts,
                    retry_sleep_seconds=cfg.scheduled_backfill_retry_sleep_seconds,
                    retry_sleep_max_seconds=cfg.scheduled_backfill_retry_sleep_max_seconds,
                    notify_summary=True,
                ),
                daily_time_utc="00:00",
                run_at_startup=True,
            )
        )
    return jobs


class TickScheduler:
    """Owns the job list and dispatches due jobs as subprocesses."""

    def __init__(self, cfg: ServiceConfig) -> None:
        self.cfg = cfg
        self.jobs = build_jobs(cfg)
        self._active: dict[str, tuple[Job, JobProcess]] = {}
        self._covered_by_active: dict[str, list[Job]] = {}
        self._last_backfill_wait_log = 0.0

    def init_timers(self, now_mono: float) -> None:
        for job in self.jobs:
            job.init_timer(now_mono)

    def describe(self) -> list[str]:
        return [op_line("Schedule", job.name, job.describe()) for job in self.jobs]

    def _backfill_active(self) -> bool:
        return any(_is_backfill_job(name) for name in self._active)

    def _spawn_due_job(self, job: Job, now_mono: float, now_utc: datetime) -> bool:
        try:
            handle = spawn_job(
                job.build_args(),
                output_log_path=None,
                mirror_output_to_logger=True,
                label=job.name,
                extra_env={
                    "TICK_ENGINE_DISABLE_FILE_LOG": "1",
                },
                cancel_file=cancel_file_for(f"scheduled-{job.name}"),
            )
            self._active[job.name] = (job, handle)
            logger.info(op_line("Job", job.name, f"started | pid={handle.pid}"))
            return True
        except Exception:
            logger.exception("failed to spawn job %s", job.name)
            job.mark_failed(now_mono)
            return False

    def _select_backfill_job(self, jobs: list[Job]) -> Job:
        return max(jobs, key=lambda job: _BACKFILL_JOB_PRIORITY[job.name])

    def _covered_backfills(
        self,
        selected: Job,
        due_backfills: list[Job],
    ) -> list[Job]:
        selected_priority = _BACKFILL_JOB_PRIORITY[selected.name]
        covered: list[Job] = []
        for job in due_backfills:
            if job is selected:
                continue
            priority = _BACKFILL_JOB_PRIORITY[job.name]
            if priority >= selected_priority:
                continue
            covered.append(job)
        return covered

    def _reap(self) -> None:
        now_utc = datetime.now(timezone.utc)
        now_mono = time.monotonic()
        for name, (job, handle) in list(self._active.items()):
            if handle.poll() is None:
                idle_seconds = handle.idle_seconds()
                idle_limit = max(60, int(self.cfg.child_idle_timeout_seconds))
                if idle_seconds > idle_limit:
                    logger.warning(
                        op_line(
                            "Job",
                            name,
                            f"stalled | idle_seconds={idle_seconds:.0f} | limit={idle_limit}s | action=terminate",
                        )
                    )
                    handle.request_cancel(f"job idle timeout after {idle_seconds:.0f}s")
                    handle.terminate(timeout=10.0)
            if handle.poll() is not None:
                rc = handle.returncode
                handle.close()
                if rc:
                    job.mark_failed(now_mono, 60.0 if int(rc) == 75 else _JOB_FAILURE_RETRY_SECONDS)
                    if int(rc) == 75 and _is_backfill_job(name):
                        logger.info(
                            op_line(
                                "Job",
                                name,
                                "finished | result=skipped | reason=another data job is running | exit_code=75",
                            )
                        )
                    else:
                        logger.warning(
                            op_line("Job", name, f"finished | result=failed | exit_code={rc}")
                        )
                else:
                    logger.info(op_line("Job", name, "finished | result=OK | exit_code=0"))
                    job.mark_ran(now_mono, now_utc)
                    job.mark_success(now_utc)
                    for covered_job in self._covered_by_active.get(name, []):
                        logger.info(
                            op_line(
                                "Job",
                                covered_job.name,
                                f"covered | successful_job={name}",
                            )
                        )
                        covered_job.mark_ran(now_mono, now_utc)
                logger.info("")
                self._covered_by_active.pop(name, None)
                del self._active[name]

    def runtime_snapshot(self) -> dict[str, object]:
        active: list[dict[str, object]] = []
        now = time.monotonic()
        for name, (_job, handle) in self._active.items():
            active.append(
                {
                    "name": name,
                    "pid": handle.pid,
                    "returncode": handle.returncode,
                    "started_age_seconds": int(now - handle.started_mono),
                    "idle_seconds": int(now - handle.last_output_mono),
                    "cancel_file": str(handle.cancel_file) if handle.cancel_file else None,
                }
            )
        return {
            "active_jobs": active,
            "active_job_count": len(active),
            "backfill_active": self._backfill_active(),
        }

    def tick(self, now_mono: float, now_utc: datetime) -> None:
        self._reap()
        due_jobs = [
            job
            for job in self.jobs
            if job.name not in self._active and job.due(now_mono, now_utc)
        ]
        due_backfills = [job for job in due_jobs if _is_backfill_job(job.name)]
        for job in due_jobs:
            if _is_backfill_job(job.name):
                continue
            self._spawn_due_job(job, now_mono, now_utc)

        if not due_backfills:
            return
        active_manual = _manual_backfill_active()
        if active_manual:
            if now_mono - self._last_backfill_wait_log >= _BACKFILL_WAIT_LOG_SECONDS:
                pending = ", ".join(
                    job.name
                    for job in sorted(
                        due_backfills,
                        key=lambda item: _BACKFILL_JOB_PRIORITY[item.name],
                        reverse=True,
                    )
                )
                current = active_manual.get("current_batch")
                current = current if isinstance(current, dict) else {}
                logger.info(
                    op_line(
                        "Job",
                        "scheduled backfill",
                        "waiting | reason=manual backfill is running | "
                        f"pending={pending} | batch={current.get('index') or '-'}/{current.get('total') or '-'} | "
                        f"window={current.get('request_from_utc') or '-'} -> {current.get('to_utc') or '-'} | "
                        f"progress_updated={active_manual.get('updated_at_utc') or '-'}",
                    )
                )
                self._last_backfill_wait_log = now_mono
            return
        if self._backfill_active():
            if now_mono - self._last_backfill_wait_log >= _BACKFILL_WAIT_LOG_SECONDS:
                pending = ", ".join(
                    job.name
                    for job in sorted(
                        due_backfills,
                        key=lambda item: _BACKFILL_JOB_PRIORITY[item.name],
                        reverse=True,
                    )
                )
                active = ", ".join(name for name in self._active if _is_backfill_job(name))
                logger.info(
                    op_line("Job", "data job", f"waiting | active={active} | pending={pending}")
                )
                self._last_backfill_wait_log = now_mono
            return

        selected = self._select_backfill_job(due_backfills)
        if len(due_backfills) > 1:
            logger.info(
                op_line(
                    "Job",
                    selected.name,
                    "selected | pending="
                    + ", ".join(
                        job.name
                        for job in sorted(
                            due_backfills,
                            key=lambda item: _BACKFILL_JOB_PRIORITY[item.name],
                            reverse=True,
                        )
                    ),
                )
            )
        covered = self._covered_backfills(selected, due_backfills)
        if self._spawn_due_job(selected, now_mono, now_utc):
            self._covered_by_active[selected.name] = covered

    def shutdown(self) -> None:
        """Stop all active job subprocesses.

        Logs which jobs are interrupted so the operator can review IngestRun
        records after a restart. The supervisor calls
        ``store.mark_stale_runs_interrupted()`` on next startup to repair the
        audit trail for any jobs that were still running at this point.
        """
        if not self._active:
            return
        names = list(self._active)
        logger.warning(
            "scheduler shutting down with %d active job(s): %s — these IngestRun rows "
            "will be repaired to INTERRUPTED on next service startup",
            len(names),
            ", ".join(names),
        )
        for name, (_job, handle) in list(self._active.items()):
            logger.info("requesting graceful cancel for job %s (pid=%s)", name, handle.pid)
            handle.request_cancel("service shutdown")

        _grace_deadline = time.monotonic() + _JOB_STOP_GRACE
        while self._active and time.monotonic() < _grace_deadline:
            self._reap()
            if self._active:
                time.sleep(0.25)

        for name, (_job, handle) in list(self._active.items()):
            logger.warning("terminating job %s (pid=%s)", name, handle.pid)
            handle.terminate(timeout=10)
        self._active.clear()
