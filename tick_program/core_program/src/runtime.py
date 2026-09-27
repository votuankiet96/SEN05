"""24/7 supervisor: singleton process, signal handling, heartbeat, and the
job schedule (interval/daily-time jobs spawned as CLI subprocesses).

The service itself never talks to cTrader or SQL directly — every unit of
work is a ``python -m src <subcommand>`` subprocess, so a stuck or
crashed job can never take the supervisor down with it.

Every event here — supervisor lifecycle, job spawn/reap, and each job
subprocess's own captured output — goes through
``src.notify.write_system_event`` into the single shared log file.
There is no second logging path: no Python ``logging`` handler writes to
that file, so the whole file stays one consistent, greppable stream.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

_LOOP_SECONDS = 1.0
_SERVICE_HANDOFF_TIMEOUT = 60.0
_JOB_STOP_GRACE = 20.0
_JOB_FAILURE_RETRY_SECONDS = 300.0
# Widest-window job wins when more than one backfill tier is due at once;
# the others are marked "covered" once it succeeds instead of stacking up.
_BACKFILL_JOB_PRIORITY = {"gap-fill": 100, "daily-backfill": 60, "recent-backfill": 40}
_CREATE_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0


def _log(event: str, detail: str = "", *, level: str = "INFO", **fields: object) -> None:
    from src.notify import write_system_event

    write_system_event("runtime", event, detail, level=level, **fields)


def _is_backfill_job(name: str) -> bool:
    return name in _BACKFILL_JOB_PRIORITY


# ---------------------------------------------------------------------------
# Job process spawning
# ---------------------------------------------------------------------------


def job_command(args: list[str]) -> list[str]:
    # Frozen (PyInstaller) build: sys.executable is the app itself, which
    # the frozen entry point dispatches straight to src.__main__.main() for
    # any recognized CLI subcommand -- no `-B -m src` interpreter flags to
    # hand it (see src/backfill.py::_run_batch_subprocess for the same fix).
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-B", "-m", "src", *args]


class JobProcess:
    def __init__(self, label: str, proc: subprocess.Popen, *, cancel_file: Path | None = None) -> None:
        self.label = label
        self.proc = proc
        self.cancel_file = cancel_file
        self.started_mono = time.monotonic()
        self.last_output_mono = self.started_mono
        self._tee: threading.Thread | None = None

    @property
    def pid(self) -> int:
        return self.proc.pid

    def poll(self) -> int | None:
        return self.proc.poll()

    @property
    def returncode(self) -> int | None:
        return self.proc.returncode

    def idle_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.last_output_mono)

    def terminate(self, timeout: float = 10.0) -> None:
        from src.spool import terminate_pid

        if self.proc.poll() is None:
            terminate_pid(self.proc.pid, timeout=timeout)
        self.close()

    def request_cancel(self, reason: str = "cancel requested") -> None:
        from src.spool import write_cancel_file

        write_cancel_file(self.cancel_file, reason)

    def close(self) -> None:
        if self._tee is not None and self._tee.is_alive():
            self._tee.join(timeout=2.0)


def spawn_job(args: list[str], *, label: str | None = None, cancel_file: Path | None = None) -> JobProcess:
    from src.spool import CANCEL_ENV, clear_cancel_file

    label = label or (args[0] if args else "job")
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["TICK_ENGINE_JOB"] = "1"
    if cancel_file is not None:
        clear_cancel_file(cancel_file)
        env[CANCEL_ENV] = str(cancel_file)
    proc = subprocess.Popen(
        job_command(args), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, creationflags=_CREATE_FLAGS,
    )
    _log("JOB_SPAWNED", job=label, pid=proc.pid, command=" ".join(args))
    handle = JobProcess(label, proc, cancel_file=cancel_file)

    def _tee() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            handle.last_output_mono = time.monotonic()
            clean = line.rstrip("\r\n")
            if clean:
                _log("JOB_OUTPUT", clean, job=label, pid=proc.pid)

    handle._tee = threading.Thread(target=_tee, name=f"job-tee-{label}", daemon=True)
    handle._tee.start()
    return handle


# ---------------------------------------------------------------------------
# Job schedule
# ---------------------------------------------------------------------------


@dataclass
class Job:
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
        return self.arg_builder() if self.arg_builder is not None else list(self.args or [])

    def _cooldown_active(self, now_utc: datetime) -> bool:
        if not self.startup_cooldown_hours or self.cooldown_path is None:
            return False
        try:
            last_run = datetime.fromisoformat(self.cooldown_path.read_text(encoding="utf-8").strip())
            if last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=timezone.utc)
            return (now_utc - last_run).total_seconds() / 3600.0 < self.startup_cooldown_hours
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
            hour, minute = (int(p) for p in self.daily_time_utc.split(":"))
            target = now_utc.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if self._last_daily_date == "":
                if self._cooldown_active(now_utc):
                    self._last_daily_date = today
                    return False
                return self.run_at_startup or now_utc >= target
            return now_utc >= target
        return self.interval_seconds is not None and now_mono >= self._next_run

    def mark_ran(self, now_mono: float, now_utc: datetime) -> None:
        self._retry_not_before = 0.0
        if self.startup_only:
            self._startup_ran = True
        if self.daily_time_utc:
            self._last_daily_date = now_utc.strftime("%Y-%m-%d")
        if self.interval_seconds is not None:
            self._next_run = now_mono + self.interval_seconds

    def mark_failed(self, now_mono: float, retry_seconds: float = _JOB_FAILURE_RETRY_SECONDS) -> None:
        self._retry_not_before = now_mono + max(1.0, retry_seconds)

    def mark_success(self, now_utc: datetime) -> None:
        if self.cooldown_path is not None:
            self.cooldown_path.parent.mkdir(parents=True, exist_ok=True)
            self.cooldown_path.write_text(now_utc.isoformat(), encoding="utf-8")

    def describe(self) -> str:
        cadence = "startup-only" if self.startup_only else f"daily@{self.daily_time_utc}Z" if self.daily_time_utc else f"every {self.interval_seconds:.0f}s"
        return f"cadence={cadence}" + (" | startup=yes" if self.run_at_startup else "")


def _batched_backfill_args(job_name: str, lookback_minutes: int, batch_minutes: int, delay_seconds: int, s) -> Callable[[], list[str]]:
    def _build() -> list[str]:
        from src.configuration import RUN_DIR

        to_dt = datetime.now(timezone.utc) - timedelta(seconds=delay_seconds)
        from_dt = to_dt - timedelta(minutes=lookback_minutes)
        from_ms, to_ms = int(from_dt.timestamp() * 1000), int(to_dt.timestamp() * 1000)
        return [
            "backfill-batched", "--from", str(from_ms), "--to", str(to_ms),
            "--batch-minutes", str(batch_minutes), "--overlap-seconds", "60", "--wait-lock-seconds", "0",
            "--request-timeout", str(s.scheduled_request_timeout_seconds),
            "--timeout-per-batch", str(s.scheduled_batch_timeout_seconds),
            "--max-attempts", str(s.scheduled_backfill_max_attempts),
            "--retry-sleep-seconds", str(s.scheduled_backfill_retry_sleep_seconds),
            "--retry-sleep-max-seconds", str(s.scheduled_backfill_retry_sleep_max_seconds),
            "--progress-file", str(RUN_DIR / "backfill_batches" / f"scheduled_{job_name}_{from_ms}_{to_ms}.json"),
            "--notify-summary",
        ]

    return _build


def _gap_fill_args(s) -> Callable[[], list[str]]:
    def _build() -> list[str]:
        return [
            "gap-fill-backfill",
            "--max-lookback-days", str(s.gap_fill_max_lookback_days),
            "--batch-minutes", str(s.gap_fill_batch_minutes),
            "--delay-seconds", str(s.backfill_delay_seconds),
            "--wait-lock-seconds", "0",
            "--request-timeout", str(s.scheduled_request_timeout_seconds),
            "--timeout-per-batch", str(s.scheduled_batch_timeout_seconds),
            "--max-attempts", str(s.scheduled_backfill_max_attempts),
            "--retry-sleep-seconds", str(s.scheduled_backfill_retry_sleep_seconds),
            "--retry-sleep-max-seconds", str(s.scheduled_backfill_retry_sleep_max_seconds),
            "--notify-summary",
        ]

    return _build


def build_jobs(s) -> list[Job]:
    """Exactly 3 backfill jobs, widest-window-wins when more than one is due:
    recent-backfill (30 min interval, small window), daily-backfill (hourly,
    1-day window), gap-fill (startup + hourly, real watermark -> now, so it
    self-heals after any length of downtime instead of guessing a lookback)."""
    return [
        Job("refresh-token", args=["refresh-token", "--save"], interval_seconds=s.token_refresh_interval_seconds, run_at_startup=True),
        Job("check", args=["check", "--notify", "--auto-repair-stale-runs", "--stale-seconds", str(s.check_stale_seconds)], interval_seconds=s.check_interval_seconds),
        Job("spool-drain", args=["spool-drain"], interval_seconds=s.spool_drain_interval_seconds),
        Job("daily-health-summary", args=["check", "--notify", "--notify-summary", "--stale-seconds", str(s.check_stale_seconds)], daily_time_utc=s.daily_health_summary_utc),
        Job("recent-backfill",
            arg_builder=_batched_backfill_args("recent-backfill", s.recent_backfill_lookback_minutes, s.recent_backfill_batch_minutes, s.backfill_delay_seconds, s),
            interval_seconds=s.recent_backfill_interval_seconds, run_at_startup=True),
        Job("daily-backfill",
            arg_builder=_batched_backfill_args("daily-backfill", s.daily_backfill_lookback_minutes, s.daily_backfill_batch_minutes, s.backfill_delay_seconds, s),
            interval_seconds=s.daily_backfill_interval_seconds),
        Job("gap-fill", arg_builder=_gap_fill_args(s), interval_seconds=s.gap_fill_interval_seconds, run_at_startup=True),
        Job("compress-ticks", args=["compress-ticks"], interval_seconds=s.compress_ticks_interval_seconds),
        Job("refresh-dedup-window",
            args=["refresh-dedup-window", "--retain-days", str(s.dedup_window_retain_days)],
            interval_seconds=s.refresh_dedup_window_interval_seconds),
        Job("symbol-sync", args=["symbol-sync", "--apply"], daily_time_utc=s.symbol_sync_daily_utc, run_at_startup=True),
    ]


class TickScheduler:
    """Owns the job list and dispatches due jobs as subprocesses, one backfill tier at a time."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.jobs = build_jobs(settings)
        self._active: dict[str, tuple[Job, JobProcess]] = {}
        self._covered_by_active: dict[str, list[Job]] = {}

    def init_timers(self, now_mono: float) -> None:
        for job in self.jobs:
            job.init_timer(now_mono)

    def _backfill_active(self) -> bool:
        return any(_is_backfill_job(name) for name in self._active)

    def _spawn(self, job: Job, now_mono: float) -> bool:
        from src.spool import cancel_file_for

        try:
            handle = spawn_job(job.build_args(), label=job.name, cancel_file=cancel_file_for(f"scheduled-{job.name}"))
            self._active[job.name] = (job, handle)
            _log("JOB_STARTED", job=job.name, pid=handle.pid)
            return True
        except Exception as exc:
            _log("JOB_SPAWN_FAILED", str(exc), level="ERROR", job=job.name)
            job.mark_failed(now_mono)
            return False

    def _reap(self) -> None:
        now_utc, now_mono = datetime.now(timezone.utc), time.monotonic()
        for name, (job, handle) in list(self._active.items()):
            if handle.poll() is None:
                limit = max(60, int(self.settings.child_idle_timeout_seconds))
                if handle.idle_seconds() > limit:
                    _log("JOB_IDLE_TIMEOUT", level="WARNING", job=name, pid=handle.pid, idle_seconds=int(handle.idle_seconds()))
                    handle.request_cancel(f"job idle timeout after {handle.idle_seconds():.0f}s")
                    handle.terminate(timeout=10.0)
            if handle.poll() is not None:
                rc = handle.returncode
                handle.close()
                if rc:
                    job.mark_failed(now_mono, 60.0 if rc == 75 else _JOB_FAILURE_RETRY_SECONDS)
                    _log("JOB_FAILED", level="ERROR", job=name, pid=handle.pid, exit_code=rc)
                else:
                    _log("JOB_FINISHED", job=name, pid=handle.pid, exit_code=0)
                    job.mark_ran(now_mono, now_utc)
                    job.mark_success(now_utc)
                    for covered in self._covered_by_active.get(name, []):
                        covered.mark_ran(now_mono, now_utc)
                self._covered_by_active.pop(name, None)
                del self._active[name]

    def runtime_snapshot(self) -> dict[str, object]:
        return {"active_jobs": list(self._active), "active_job_count": len(self._active), "backfill_active": self._backfill_active()}

    def tick(self, now_mono: float, now_utc: datetime) -> None:
        self._reap()
        due = [j for j in self.jobs if j.name not in self._active and j.due(now_mono, now_utc)]
        due_backfills = [j for j in due if _is_backfill_job(j.name)]
        for job in due:
            if not _is_backfill_job(job.name):
                self._spawn(job, now_mono)
        if not due_backfills or self._backfill_active():
            return
        selected = max(due_backfills, key=lambda j: _BACKFILL_JOB_PRIORITY[j.name])
        covered = [j for j in due_backfills if j is not selected and _BACKFILL_JOB_PRIORITY[j.name] < _BACKFILL_JOB_PRIORITY[selected.name]]
        if self._spawn(selected, now_mono):
            self._covered_by_active[selected.name] = covered

    def shutdown(self) -> None:
        if not self._active:
            return
        for _name, (_job, handle) in list(self._active.items()):
            handle.request_cancel("service shutdown")
        deadline = time.monotonic() + _JOB_STOP_GRACE
        while self._active and time.monotonic() < deadline:
            self._reap()
            if self._active:
                time.sleep(0.25)
        for _name, (_job, handle) in list(self._active.items()):
            handle.terminate(timeout=10)
        self._active.clear()


# ---------------------------------------------------------------------------
# Supervisor (singleton process, heartbeat, signal handling)
# ---------------------------------------------------------------------------


def request_supervisor_stop(reason: str = "") -> bool:
    from src.configuration import SUPERVISOR_STOP, ensure_runtime_dirs

    try:
        ensure_runtime_dirs()
        SUPERVISOR_STOP.write_text(reason or "stop requested", encoding="utf-8")
        return True
    except OSError:
        return False


class BackendSupervisor:
    def __init__(self, settings, *, dry_run: bool = False) -> None:
        self.settings = settings
        self.dry_run = dry_run
        self.scheduler = TickScheduler(settings)
        self._stop = threading.Event()
        self._last_heartbeat_mono = 0.0

    def _install_signal_handlers(self) -> None:
        def _handler(signum, _frame):
            _log("SERVICE_STOP_SIGNAL", signal=signum)
            self._stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):
                pass
        if os.name == "nt":
            try:
                signal.signal(signal.SIGBREAK, _handler)  # type: ignore[attr-defined]
            except (ValueError, OSError, AttributeError):
                pass

    def _write_heartbeat(self, *, status: str = "RUNNING", force: bool = False) -> None:
        from src.notify import write_service_heartbeat

        now_mono = time.monotonic()
        interval = max(5, int(self.settings.heartbeat_interval_seconds))
        if not force and now_mono - self._last_heartbeat_mono < interval:
            return
        self._last_heartbeat_mono = now_mono
        try:
            write_service_heartbeat({"status": status, "service_pid": os.getpid(),
                                      "data_mode": "historical overlap backfill only",
                                      "scheduler": self.scheduler.runtime_snapshot()})
        except OSError as exc:
            # Heartbeat is a monitoring convenience, not correctness-critical — a
            # transient file-lock (antivirus/backup scan) writing it must not take
            # down the whole supervisor loop. See 2026-08-14 SERVICE_CRASHED incident.
            _log("HEARTBEAT_WRITE_FAILED", str(exc), level="ERROR")

    def _acquire_supervisor_pid(self) -> bool:
        from src.configuration import SUPERVISOR_PID, ensure_runtime_dirs
        from src.spool import is_pid_alive

        ensure_runtime_dirs()
        my_pid = os.getpid()
        while True:
            try:
                fd = os.open(SUPERVISOR_PID, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                try:
                    existing = SUPERVISOR_PID.read_text(encoding="utf-8").strip()
                except OSError:
                    time.sleep(0.1)
                    continue
                other = int(existing) if existing.isdigit() else 0
                if other == my_pid:
                    SUPERVISOR_PID.unlink(missing_ok=True)
                    continue
                if other > 0 and is_pid_alive(other):
                    _log("SERVICE_HANDOFF_REQUESTED", level="WARNING", previous_pid=other, new_pid=my_pid)
                    request_supervisor_stop(f"handoff to pid {my_pid}")
                    deadline = time.monotonic() + _SERVICE_HANDOFF_TIMEOUT
                    while time.monotonic() < deadline and is_pid_alive(other):
                        time.sleep(0.5)
                    if is_pid_alive(other):
                        _log("SERVICE_HANDOFF_TIMED_OUT", level="ERROR", previous_pid=other)
                        return False
                try:
                    SUPERVISOR_PID.unlink(missing_ok=True)
                except OSError:
                    return False
                continue
            except OSError as exc:
                _log("SERVICE_PID_FILE_ERROR", str(exc), level="ERROR")
                return False
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(str(my_pid))
                handle.flush()
                os.fsync(handle.fileno())
            return True

    def _release_supervisor_pid(self) -> None:
        from src.configuration import SUPERVISOR_PID

        try:
            if SUPERVISOR_PID.read_text(encoding="utf-8").strip() == str(os.getpid()):
                SUPERVISOR_PID.unlink()
        except OSError:
            pass

    def _repair_stale_state(self) -> None:
        from src.notify import mark_stale_backfill_progress
        from src.sql_store import TickSqlStore, resolve_target_symbols

        try:
            targets = resolve_target_symbols(self.settings.symbols)
            store = TickSqlStore(self.settings.schema, targets, environment=self.settings.env)
            n = store.mark_stale_runs_stopped()
            if n:
                _log("STALE_INGEST_RUNS_REPAIRED", level="WARNING", count=n)
        except Exception as exc:
            _log("STALE_STATE_REPAIR_FAILED", str(exc), level="ERROR", stage="ingest_runs")
        try:
            mark_stale_backfill_progress(self.settings.scheduled_progress_stale_seconds)
        except Exception as exc:
            _log("STALE_STATE_REPAIR_FAILED", str(exc), level="ERROR", stage="batch_progress")

    def run(self) -> int:
        from src.configuration import SUPERVISOR_STOP, ensure_runtime_dirs

        ensure_runtime_dirs()
        _log(
            "SERVICE_PLAN",
            scheduled_jobs=len(self.scheduler.jobs),
            **{f"job_{i}": f"{job.name}({job.describe()})" for i, job in enumerate(self.scheduler.jobs)},
        )
        if self.dry_run:
            return 0

        missing = self.settings.missing_api_fields
        if missing:
            _log("SERVICE_START_BLOCKED", "missing required config fields — run oauth-login then account-list", level="ERROR", missing=",".join(missing))
            return 2

        self._repair_stale_state()
        if not self._acquire_supervisor_pid():
            return 3

        self._install_signal_handlers()
        now_mono = time.monotonic()
        self.scheduler.init_timers(now_mono)
        _log("SERVICE_STARTED", pid=os.getpid())
        self._write_heartbeat(status="RUNNING", force=True)

        exit_code = 0
        try:
            while not self._stop.is_set():
                now_mono, now_utc = time.monotonic(), datetime.now(timezone.utc)
                if SUPERVISOR_STOP.exists():
                    SUPERVISOR_STOP.unlink(missing_ok=True)
                    self._stop.set()
                    break
                self.scheduler.tick(now_mono, now_utc)
                self._write_heartbeat(status="RUNNING")
                self._stop.wait(_LOOP_SECONDS)
        except KeyboardInterrupt:
            _log("SERVICE_STOP_INTERRUPTED")
        except Exception as exc:
            exit_code = 1
            _log("SERVICE_CRASHED", str(exc), level="CRITICAL")
        finally:
            try:
                self.scheduler.shutdown()
            except Exception as exc:
                exit_code = 1
                _log("SERVICE_SHUTDOWN_FAILED", str(exc), level="ERROR")
            self._write_heartbeat(status="STOPPED", force=True)
            self._release_supervisor_pid()
            _log("SERVICE_STOPPED", exit_code=exit_code)
        return exit_code


def main(argv: list[str] | None = None) -> int:
    import argparse

    from src.configuration import load_settings

    parser = argparse.ArgumentParser(prog="python -m src service")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return BackendSupervisor(load_settings(), dry_run=args.dry_run).run()
