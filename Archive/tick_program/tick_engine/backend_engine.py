"""24/7 backfill-only supervisor for the cTrader FTMO tick service.

The service keeps no realtime WebSocket connection. It owns the singleton
process, token refresh, health checks, spool drain, and scheduled overlap
historical backfills. Each operational unit still runs as a subprocess so a
slow or failed cTrader history request cannot poison the supervisor loop.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import threading
import time
from datetime import datetime, timezone

from tick_engine.scheduler import (
    ServiceConfig,
    TickScheduler,
    load_service_config,
    op_line,
)
from tick_engine.utils_support.proc_utils import is_pid_alive, is_tick_engine_process

logger = logging.getLogger("tick_engine.service")

_LOOP_SECONDS = 1.0
_SERVICE_HANDOFF_TIMEOUT = 60.0


# ---------------------------------------------------------------------------
# Cross-process stop sentinel helpers
# ---------------------------------------------------------------------------


def request_supervisor_stop(reason: str = "") -> bool:
    """Write the cross-process stop sentinel for a running supervisor.

    Returns ``True`` if the sentinel was written. A running
    :class:`BackendSupervisor` polls for this file every loop tick and shuts
    down gracefully, then removes it.
    """
    from tick_engine.settings import SUPERVISOR_STOP, ensure_runtime_dirs

    try:
        ensure_runtime_dirs()
        stamp = datetime.now(timezone.utc).isoformat()
        SUPERVISOR_STOP.write_text(reason or f"stop requested {stamp}", encoding="utf-8")
        return True
    except OSError:
        logger.exception("could not write supervisor stop sentinel")
        return False


def supervisor_stop_requested() -> bool:
    """Return True if a cross-process stop has been requested."""
    from tick_engine.settings import SUPERVISOR_STOP

    return SUPERVISOR_STOP.exists()


def _clear_supervisor_stop_sentinel() -> None:
    from tick_engine.settings import SUPERVISOR_STOP

    try:
        SUPERVISOR_STOP.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("could not remove supervisor stop sentinel")


# ---------------------------------------------------------------------------
# BackendSupervisor
# ---------------------------------------------------------------------------


class BackendSupervisor:
    def __init__(
        self,
        cfg: ServiceConfig,
        *,
        dry_run: bool = False,
    ) -> None:
        self.cfg = cfg
        self.dry_run = dry_run
        self.scheduler = TickScheduler(cfg)
        self._stop = threading.Event()
        self._last_heartbeat_mono = 0.0
        self._last_progress_repair_mono = 0.0

    # -- lifecycle ---------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        def _handler(signum, _frame):
            logger.info("Stop signal received (signal %s). Shutting down...", signum)
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

    def _shutdown(self) -> None:
        logger.info("")
        logger.info(op_line("Service", "stop", "stopping backfill service"))
        self.scheduler.shutdown()
        logger.info(op_line("Service", "stop", "backfill service stopped"))

    def _notify_lifecycle(
        self,
        level: str,
        title: str,
        conclusion: str,
        *,
        error: Exception | None = None,
    ) -> None:
        try:
            from tick_engine.reporting.notifications import flush_notifications, notify_tick_report

            notify_tick_report(
                level,
                title,
                conclusion=conclusion,
                details=[("Host", os.environ.get("COMPUTERNAME") or "unknown"), ("PID", os.getpid())],
                technical=[("Error", str(error)[:600])] if error is not None else None,
                throttle_key=f"tick-service-{title.lower().replace(' ', '-')}",
                throttle_seconds=60,
            )
            flush_notifications()
        except Exception:
            logger.exception("service lifecycle notification failed (non-fatal)")

    def _check_prerequisites(self) -> int:
        from tick_engine.utils_support.runtime import load_settings

        settings = load_settings()
        missing = settings.missing_api_fields
        if missing:
            logger.error(
                "Cannot start — required fields are missing: %s. "
                "Run 'oauth-login' and then 'account-list' to set them up.",
                ", ".join(missing),
            )
            return 2
        ttl = settings.access_token_seconds_remaining
        logger.info("")
        logger.info(op_line("Preflight", "status", "OK"))
        logger.info(op_line("Preflight", "environment", f"{settings.env} | account={settings.account_id}"))
        logger.info(op_line("Preflight", "token", "valid_for=unknown" if ttl is None else f"valid_for={ttl}s"))
        return 0

    def _repair_stale_ingest_runs(self) -> None:
        try:
            from tick_engine.data_storage.db_connector import get_connection
            from tick_engine.data_storage.store_sql import TickSqlStore
            from tick_engine.utils_support.runtime import load_settings

            settings = load_settings()
            store = TickSqlStore(
                schema=settings.schema,
                targets=list(settings.symbols),
                connection_factory=get_connection,
                environment=settings.env,
            )
            n = store.mark_stale_runs_interrupted()
            if n:
                logger.warning(
                    "Cleaned up %d session record(s) left over from the previous run "
                    "(marked as INTERRUPTED).",
                    n,
                )
        except Exception:
            logger.exception("mark_stale_runs_interrupted failed (non-fatal)")

    def _repair_stale_progress_files(self) -> None:
        try:
            from tick_engine.settings import TICK_SCHEDULED_PROGRESS_STALE_SECONDS
            from tick_engine.utils_support.service_state import mark_stale_backfill_progress

            n = mark_stale_backfill_progress(TICK_SCHEDULED_PROGRESS_STALE_SECONDS)
            if n:
                logger.warning(
                    op_line(
                        "Startup",
                        "progress repair",
                        f"marked {n} stale batch progress file(s)",
                    )
                )
        except Exception:
            logger.exception("batch progress repair failed (non-fatal)")

    def _repair_stale_progress_files_periodic(self, now_mono: float) -> None:
        from tick_engine.settings import TICK_SCHEDULED_PROGRESS_STALE_SECONDS

        interval = max(60.0, min(300.0, float(TICK_SCHEDULED_PROGRESS_STALE_SECONDS) / 2.0))
        if now_mono - self._last_progress_repair_mono < interval:
            return
        self._last_progress_repair_mono = now_mono
        self._repair_stale_progress_files()

    def _write_heartbeat(self, *, status: str = "RUNNING", force: bool = False) -> None:
        from tick_engine.settings import TICK_SERVICE_HEARTBEAT_INTERVAL_SECONDS
        from tick_engine.utils_support.service_state import write_service_heartbeat

        now_mono = time.monotonic()
        interval = max(5, int(TICK_SERVICE_HEARTBEAT_INTERVAL_SECONDS))
        if not force and now_mono - self._last_heartbeat_mono < interval:
            return
        write_service_heartbeat(
            {
                "status": status,
                "service_pid": os.getpid(),
                "data_mode": "historical overlap backfill only",
                "scheduler": self.scheduler.runtime_snapshot(),
            }
        )
        self._last_heartbeat_mono = now_mono

    def _log_plan(self) -> None:
        logger.info("")
        logger.info("=" * 88)
        logger.info(op_line("Service", "plan", f"scheduled_jobs={len(self.scheduler.jobs)}"))
        logger.info(op_line("Service", "data mode", "historical overlap backfill only"))
        logger.info("-" * 88)
        for job in self.scheduler.jobs:
            logger.info(op_line("Schedule", job.name, job.describe()))
        logger.info("=" * 88)

    # -- singleton pid file ------------------------------------------------

    def _acquire_supervisor_pid(self) -> bool:
        """Claim the supervisor singleton (new-wins handoff).

        If another supervisor already owns the pid file, signal it to stop
        and wait up to ``_SERVICE_HANDOFF_TIMEOUT`` for it to exit, then take
        over. A stale file with no running owner is reclaimed at once.
        """
        from tick_engine.settings import SUPERVISOR_PID, ensure_runtime_dirs

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
                    _clear_supervisor_stop_sentinel()
                    return True
                if other > 0 and is_pid_alive(other) and is_tick_engine_process(other):
                    logger.info(
                        "Another supervisor is already running (PID %s). "
                        "Requesting it to stop gracefully (waiting up to %.0f seconds)...",
                        other,
                        _SERVICE_HANDOFF_TIMEOUT,
                    )
                    request_supervisor_stop(f"handoff to pid {my_pid}")
                    deadline = time.monotonic() + _SERVICE_HANDOFF_TIMEOUT
                    while time.monotonic() < deadline and is_pid_alive(other):
                        time.sleep(0.5)
                    if is_pid_alive(other):
                        logger.error(
                            "Previous supervisor (PID %s) did not stop within %.0f seconds. "
                            "Not starting a new instance.",
                            other,
                            _SERVICE_HANDOFF_TIMEOUT,
                        )
                        _clear_supervisor_stop_sentinel()
                        return False
                    logger.info("Previous supervisor (PID %s) stopped. Starting new instance.", other)
                elif other > 0:
                    logger.info(
                        "Found a leftover session file from a previous run (PID %s). Reclaiming.",
                        other,
                    )
                try:
                    current = SUPERVISOR_PID.read_text(encoding="utf-8").strip()
                    if current == existing:
                        SUPERVISOR_PID.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    logger.exception("could not reclaim stale supervisor pid file")
                    return False
                continue
            except OSError:
                logger.exception("could not atomically create supervisor pid file")
                return False

            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(str(my_pid))
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                try:
                    SUPERVISOR_PID.unlink()
                except OSError:
                    pass
                logger.exception("could not initialize supervisor pid file")
                return False
            _clear_supervisor_stop_sentinel()
            return True

    def _release_supervisor_pid(self) -> None:
        from tick_engine.settings import SUPERVISOR_PID

        try:
            owner = SUPERVISOR_PID.read_text(encoding="utf-8").strip()
        except OSError:
            return
        if owner == str(os.getpid()):
            try:
                SUPERVISOR_PID.unlink()
            except OSError:
                logger.warning("could not remove supervisor pid file")

    def _poll_stop_sentinel(self) -> None:
        if self._stop.is_set():
            return
        if supervisor_stop_requested():
            logger.info("")
            logger.info(op_line("Stop", "request", "graceful stop requested"))
            _clear_supervisor_stop_sentinel()
            self._stop.set()

    def run(self) -> int:
        from tick_engine.settings import ensure_runtime_dirs

        ensure_runtime_dirs()
        self._log_plan()
        if self.dry_run:
            logger.info(op_line("Dry run", "service", "jobs were not started"))
            return 0

        rc = self._check_prerequisites()
        if rc != 0:
            return rc

        self._repair_stale_ingest_runs()
        self._repair_stale_progress_files()

        if not self._acquire_supervisor_pid():
            return 3

        self._install_signal_handlers()
        now_mono = time.monotonic()
        self.scheduler.init_timers(now_mono)
        logger.info("")
        logger.info(op_line("Service", "start", f"running | pid={os.getpid()}"))
        self._write_heartbeat(status="RUNNING", force=True)
        self._notify_lifecycle(
            "INFO",
            "Tick service started",
            "The 24/7 scheduled tick backfill service is running.",
        )
        exit_code = 0
        unexpected_error: Exception | None = None
        try:
            while not self._stop.is_set():
                now_mono = time.monotonic()
                now_utc = datetime.now(timezone.utc)
                self._poll_stop_sentinel()
                if self._stop.is_set():
                    break
                self.scheduler.tick(now_mono, now_utc)
                self._repair_stale_progress_files_periodic(now_mono)
                self._write_heartbeat(status="RUNNING")
                self._stop.wait(_LOOP_SECONDS)
        except KeyboardInterrupt:
            logger.info("")
            logger.info(op_line("Stop", "request", "interrupted by operator"))
        except Exception as exc:
            exit_code = 1
            unexpected_error = exc
            logger.exception("tick service supervisor failed unexpectedly")
        finally:
            try:
                self._shutdown()
            except Exception as exc:
                exit_code = 1
                if unexpected_error is None:
                    unexpected_error = exc
                logger.exception("tick service shutdown failed")
            finally:
                self._write_heartbeat(status="STOPPED", force=True)
                self._release_supervisor_pid()
            if unexpected_error is None:
                self._notify_lifecycle(
                    "INFO",
                    "Tick service stopped",
                    "The tick backfill service completed a graceful shutdown.",
                )
            else:
                self._notify_lifecycle(
                    "CRITICAL",
                    "Tick service stopped unexpectedly",
                    "The tick backfill supervisor stopped because of an unhandled error.",
                    error=unexpected_error,
                )
        return exit_code


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def _setup_logging() -> None:
    from tick_engine.settings import SUPERVISOR_LOG, ensure_runtime_dirs

    ensure_runtime_dirs()
    class ReadableFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            if record.getMessage() == "":
                return ""
            return super().format(record)

    formatter = ReadableFormatter(
        "%(asctime)sZ | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(
            SUPERVISOR_LOG,
            maxBytes=10_000_000,
            backupCount=5,
            encoding="utf-8",
        ),
    ]
    if os.environ.get("TICK_ENGINE_SERVICE_CONSOLE_LOG", "").strip().lower() in {"1", "true", "yes"}:
        handlers.insert(0, logging.StreamHandler())
    for handler in handlers:
        handler.setFormatter(formatter)
    logger.handlers.clear()
    for handler in handlers:
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tick_engine service",
        description="24/7 scheduler for cTrader FTMO historical tick backfill",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the schedule and exit without spawning jobs",
    )
    args = parser.parse_args(argv)
    _setup_logging()
    return BackendSupervisor(
        load_service_config(),
        dry_run=args.dry_run,
    ).run()


if __name__ == "__main__":
    raise SystemExit(main())
