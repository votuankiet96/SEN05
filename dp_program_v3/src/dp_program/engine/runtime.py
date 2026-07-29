"""Single-process production runtime, scheduling, state, and locking."""
from __future__ import annotations
import json, logging, os, signal, sys, time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from ..log import log_event
from ..util.discord_report import DiscordReporter
from . import pair_key, select_pairs
from .auth import auth_status, ensure_authenticated
from .backfill import next_backfill_group, prioritize_backfill_pairs, run_backfill_pairs
from .live import run_live_pairs
from .spool import drain, pending_status
from .sql_connector import check_connection
LOGGER = logging.getLogger(__name__)
_SIGNAL_STOP, _STATE_WRITE_ATTEMPTS = False, 100
_Pair = tuple[dict[str, Any], dict[str, Any]]
@contextmanager
def instance_lock(config: dict[str, Any]) -> Iterator[None]:
    """Hold one OS file lock so V3 writers cannot overlap on this host."""
    path = Path(config["app"]["runtime_dir"]) / "run" / "engine.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT), "r+b")
        if handle.seek(0, 2) < 32:
            handle.write(b" " * (32 - handle.tell()))
            handle.flush()
        handle.seek(0)
    except OSError as exc:
        try: handle.close()
        except (OSError, UnboundLocalError): pass
        raise RuntimeError("Another DP Program V3 writer is already running") from exc
    try:
        if sys.platform == "win32":
            import msvcrt; msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl; fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError("Another DP Program V3 writer is already running") from exc
    try:
        handle.seek(1); handle.truncate(32)
        handle.write(f"{os.getpid():<31}".encode("ascii")); handle.flush()
        yield
    finally:
        try:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
def _read_state(config: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads((Path(config["app"]["runtime_dir"]) / "run" / "state.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}
def _write_state(config: dict[str, Any], state: dict[str, Any]) -> None:
    path = Path(config["app"]["runtime_dir"]) / "run" / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    state["heartbeat_at"] = datetime.now(timezone.utc).isoformat(); temporary = path.with_suffix(".tmp")
    for attempt in range(_STATE_WRITE_ATTEMPTS):
        try:
            temporary.write_text(json.dumps(state, ensure_ascii=True, default=str), encoding="utf-8")
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == _STATE_WRITE_ATTEMPTS - 1: raise
            time.sleep(0.05)
def _pid_alive(pid: int) -> bool:
    if pid <= 0: return False
    if sys.platform == "win32":
        import ctypes; handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle: return False
        ctypes.windll.kernel32.CloseHandle(handle); return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
def service_status(config: dict[str, Any]) -> dict[str, Any]:
    state = _read_state(config); pid = int(state.get("pid") or 0)
    heartbeat = state.get("heartbeat_at")
    age = None
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(str(heartbeat))).total_seconds()
    except (TypeError, ValueError):
        pass
    alive = _pid_alive(pid)
    return {**state,
        "ok": bool(state.get("status") == "running" and alive and age is not None and age < 600),
        "process_alive": alive,
        "heartbeat_age_seconds": None if age is None else round(age, 1)}
def request_stop(config: dict[str, Any], *, wait_seconds: int = 300) -> dict[str, Any]:
    path = Path(config["app"]["runtime_dir"]) / "run" / "stop.request"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now(timezone.utc).isoformat(), encoding="ascii")
    pid = int(_read_state(config).get("pid") or 0)
    deadline = time.monotonic() + max(0, wait_seconds)
    while _pid_alive(pid) and time.monotonic() < deadline: time.sleep(1)
    return {"ok": not _pid_alive(pid), "pid": pid, "stop_requested": True}
def record_service_failure(config: dict[str, Any], error: Exception) -> None:
    state = _read_state(config); recorded_pid = int(state.get("pid") or 0)
    if recorded_pid != os.getpid() and _pid_alive(recorded_pid):
        return
    state.update(status="failed", pid=os.getpid(),
        failed_at=datetime.now(timezone.utc).isoformat(),
        error_type=type(error).__name__)
    _write_state(config, state)
def _signal_stop(_number: int, _frame: Any) -> None:
    global _SIGNAL_STOP; _SIGNAL_STOP = True
def _stop_requested(config: dict[str, Any]) -> bool: return _SIGNAL_STOP or (Path(config["app"]["runtime_dir"]) / "run" / "stop.request").exists()
def _due_slot(slots: list[str], previous: str) -> str:
    now = datetime.now(timezone.utc)
    due = [f"{now.date().isoformat()}T{slot}" for slot in slots if now.strftime("%H:%M") >= slot]
    return due[-1] if due and due[-1] != previous else ""
def _service_pairs(config: dict[str, Any]) -> tuple[list[_Pair], list[_Pair]]:
    live_pairs = select_pairs(config, live=True) if config["live"]["enabled"] else []
    backfill_pairs = select_pairs(config, live=False) if config["backfill"]["enabled"] else []
    if not live_pairs and not backfill_pairs: raise RuntimeError("both live and backfill workflows are disabled")
    return live_pairs, backfill_pairs
def _wait_for_database(config: dict[str, Any]) -> dict[str, Any]:
    deadline = time.monotonic() + int(config["service"]["startup_grace_seconds"])
    last_error: Exception | None = None
    while True:
        try:
            result = check_connection(config)
            if result["ok"]: return result
            last_error = RuntimeError("SQL contract is not ready")
        except Exception as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            raise RuntimeError("SQL startup grace expired") from last_error
        log_event(LOGGER, logging.WARNING, "STARTUP_DEPENDENCY_WAIT", "MEDIUM",
            component="runtime", dependency="sql_server", error_type=type(last_error).__name__,
            action="bounded readiness retry")
        time.sleep(float(config["sql_server"].get("retry_delay_seconds", 5)))
def _start_generation(state: dict[str, Any], pairs: list[_Pair], *, reason: str) -> deque[_Pair]:
    started = datetime.now(timezone.utc).isoformat()
    state.update(
        backfill_generation_reason=reason, backfill_generation_started_at=started,
        last_backfill_progress_at=started, backfill_generation_total=len(pairs),
        backfill_generation_processed=0, backfill_queue_remaining=len(pairs),
        backfill_failed_pairs=[], backfill_deferred_pairs=[],
        backfill_generation_deferred=0, backfill_circuit_open=False)
    return deque(pairs)
def run_service(config: dict[str, Any]) -> dict[str, Any]:
    """Run authenticated live cycles and interleaved backfill in one writer."""
    global _SIGNAL_STOP
    _SIGNAL_STOP = False
    signal.signal(signal.SIGINT, _signal_stop)
    signal.signal(signal.SIGTERM, _signal_stop)
    previous = _read_state(config)
    state: dict[str, Any] = {"status": "starting", "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "last_backfill_slot": previous.get("last_backfill_slot", "")}
    with instance_lock(config), DiscordReporter(config) as reporter:
        _write_state(config, state)
        if _stop_requested(config):
            state.update(status="stopped", stopped_at=datetime.now(timezone.utc).isoformat())
            _write_state(config, state)
            log_event(LOGGER, logging.INFO, "SERVICE_STOPPED", "NONE", component="runtime")
            return {"ok": True, "status": "stopped", "pid": os.getpid()}
        live_pairs, backfill_pairs = _service_pairs(config)
        credentials = ensure_authenticated(config)
        database = _wait_for_database(config)
        replay = drain(config)
        startup_pairs = prioritize_backfill_pairs(config, backfill_pairs) if (
            backfill_pairs and config["service"]["backfill_on_start"]) else []
        queue = _start_generation(state, startup_pairs, reason="service_start")
        backfill_consecutive = backfill_total = 0
        slots = list(config["service"]["backfill_schedule_utc"])
        startup_slot = _due_slot(
            slots, str(previous.get("last_backfill_slot") or "")) if startup_pairs else ""
        pending_live = set(previous.get("pending_live_pairs") or ())
        pending_live.update(pair_key(pair) for pair in live_pairs)
        state.update(
            status="running",
            auth={"source": credentials["source"], **auth_status(config)},
            database={"ok": database["ok"], "database": database["database"]},
            startup_replay=replay, spool=pending_status(config),
            bootstrap_remaining_pairs=database["bootstrap_remaining_pairs"],
            pending_live_pairs=sorted(pending_live),
            last_backfill_slot=startup_slot or previous.get("last_backfill_slot", ""),
            workflows={"live": bool(live_pairs), "backfill": bool(backfill_pairs)})
        _write_state(config, state)
        reporter.publish("started", state)
        log_event(
            LOGGER, logging.INFO, "SERVICE_STARTED", "NONE", component="runtime",
            live_pairs=len(live_pairs), backfill_queued=len(queue),
            auth_source=credentials["source"])
        interval = int(config["live"]["interval_minutes"]) * 60
        guard = int(config["service"]["backfill_guard_seconds"])
        next_live = time.monotonic() if live_pairs else float("inf")
        next_heartbeat = time.monotonic()
        while not _stop_requested(config):
            now = time.monotonic()
            slot = _due_slot(slots, str(state.get("last_backfill_slot") or "")
                             ) if backfill_pairs else ""
            if slot:
                state["last_backfill_slot"] = slot
                if queue:
                    log_event(LOGGER, logging.INFO,
                        "BACKFILL_SCHEDULE_COALESCED", "NONE",
                        component="runtime", slot=slot, queue_remaining=len(queue))
                else:
                    planned = prioritize_backfill_pairs(config, backfill_pairs)
                    queue = _start_generation(state, planned, reason=f"schedule:{slot}")
                    backfill_consecutive = backfill_total = 0
                    log_event(LOGGER, logging.INFO, "BACKFILL_SCHEDULED", "NONE",
                        component="runtime", slot=slot, pairs=len(queue))
                _write_state(config, state)
            if live_pairs and now >= next_live:
                cycle_started = time.monotonic()
                replay = drain(config)
                credentials = ensure_authenticated(config)
                summary = run_live_pairs(config, live_pairs,
                    pending_pairs=list(state.get("pending_live_pairs") or []),
                    stop_requested=lambda: _stop_requested(config))
                spool = pending_status(config)
                state.update(
                    last_live={"ended_at": datetime.now(timezone.utc).isoformat(), **summary},
                    auth={"source": credentials["source"], **auth_status(config)},
                    spool=spool, last_replay=replay,
                    pending_live_pairs=summary["pending_pairs"])
                log_event(
                    LOGGER, logging.INFO, "LIVE_CYCLE_COMPLETED",
                    "HIGH" if summary["failed"] else (
                        "MEDIUM" if summary["deferred"] else "NONE"), component="runtime",
                    pairs=summary["pairs"], ok=summary["ok"], failed=summary["failed"],
                    deferred=summary["deferred"], affected=summary["affected"],
                    spool_pending=spool["pending"],
                    pending_pairs=len(summary["pending_pairs"]),
                    recovered_pairs=len(summary["recovered_pairs"]),
                    replay_delivered=replay["delivered"],
                    duration_seconds=round(time.monotonic() - cycle_started, 3))
                next_live = cycle_started + interval
                if next_live <= time.monotonic():
                    log_event(LOGGER, logging.WARNING, "LIVE_CYCLE_OVERRUN", "MEDIUM",
                        component="runtime",
                        overrun_seconds=round(time.monotonic() - next_live, 3),
                        action="next live cycle starts immediately")
                    next_live = time.monotonic()
                _write_state(config, state)
                reporter.publish("live", state)
                continue
            if queue and next_live - now > guard:
                group = next_backfill_group(config, list(queue)[:15])
                for _pair in group:
                    queue.popleft()
                summary = run_backfill_pairs(config, group)
                failed_pairs = set(state.get("backfill_failed_pairs") or []) | set(summary["failed_pairs"])
                state["last_backfill"] = {"ended_at": datetime.now(timezone.utc).isoformat(), **summary}
                state["backfill_failed_pairs"] = sorted(failed_pairs)
                state["backfill_queue_remaining"] = len(queue)
                state["backfill_generation_processed"] = int(
                    state.get("backfill_generation_processed") or 0) + len(group)
                state["last_backfill_progress_at"] = datetime.now(timezone.utc).isoformat()
                state["bootstrap_remaining_pairs"] = max(0, int(
                    state.get("bootstrap_remaining_pairs") or 0)
                    - int(summary["completed_bootstraps"]))
                if summary.get("group_failures"):
                    backfill_consecutive += 1
                    backfill_total += 1
                else:
                    backfill_consecutive = 0
                if backfill_consecutive >= 2 or backfill_total >= 3:
                    deferred_pairs = [pair_key(pair) for pair in queue]; deferred = len(deferred_pairs)
                    queue.clear()
                    state.update(backfill_circuit_open=True, backfill_deferred_pairs=deferred_pairs,
                        backfill_generation_deferred=deferred)
                    log_event(LOGGER, logging.ERROR, "BACKFILL_CIRCUIT_OPEN", "HIGH",
                        component="runtime", deferred_pairs=deferred, action="generation deferred until the next schedule")
                state["backfill_queue_remaining"] = len(queue)
                if not queue:
                    elapsed = int((datetime.now(timezone.utc) - datetime.fromisoformat(state["backfill_generation_started_at"])).total_seconds())
                    state["last_backfill_generation"] = (f"{state.get('backfill_generation_reason','')}: "
                        f"{state['backfill_generation_processed']}/{state['backfill_generation_total']} processed, "
                        f"{len(failed_pairs)} failed, {state.get('backfill_generation_deferred',0)} deferred, elapsed {elapsed}s")
                _write_state(config, state)
                if summary["failed"]:
                    reporter.publish("backfill_failed", state)
                if not queue: reporter.publish("backfill_completed", state)
                continue
            if now >= next_heartbeat:
                state["backfill_queue_remaining"] = len(queue)
                state["spool"] = pending_status(config)
                _write_state(config, state)
                reporter.publish("health", state)
                next_heartbeat = now + int(config["service"]["heartbeat_seconds"])
            time.sleep(1)
        state.update(status="stopped", stopped_at=datetime.now(timezone.utc).isoformat())
        _write_state(config, state)
        reporter.publish("stopped", state)
        log_event(LOGGER, logging.INFO, "SERVICE_STOPPED", "NONE", component="runtime")
        return {"ok": True, "status": "stopped", "pid": os.getpid()}
