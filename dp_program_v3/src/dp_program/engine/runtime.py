"""Per-mode production runtime, scheduling, state, and locking."""
from __future__ import annotations
import json, logging, os, signal, sys, time
from collections import deque
from contextlib import contextmanager
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from ..log import log_event
from ..util.discord_report import DiscordReporter
from .sql_connector import Pair, pair_key, select_pairs
from .auth import auth_status, ensure_authenticated
from .backfill import next_backfill_group, prioritize_backfill_pairs, run_backfill_pairs
from .live import run_live_pairs
from .spool import InterprocessLockTimeout, atomic_write_text, drain, interprocess_lock, pending_status
from .sql_connector import check_connection
LOGGER = logging.getLogger(__name__)
_SIGNAL_STOP = False
@contextmanager
def instance_lock(config: dict[str, Any], name: str) -> Iterator[None]:
    """Hold one non-blocking OS lock per mode."""
    try:
        with interprocess_lock(config, f"engine_{name}", timeout_seconds=0):
            yield
    except InterprocessLockTimeout as exc:
        raise RuntimeError(f"Another DP Program V3 {name} writer is already running") from exc
def _run_dir(config: dict[str, Any]) -> Path:
    path = Path(config["app"]["runtime_dir"]) / "run"; path.mkdir(parents=True, exist_ok=True); return path
def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
def _read_state(config: dict[str, Any], name: str) -> dict[str, Any]:
    try:
        value = json.loads((_run_dir(config) / f"state_{name}.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}
def _write_state(config: dict[str, Any], name: str, state: dict[str, Any]) -> None:
    payload = {**state, "heartbeat_at": _now().isoformat()}
    path = _run_dir(config) / f"state_{name}.json"; text = json.dumps(payload, ensure_ascii=True, default=str)
    atomic_write_text(path, text)
def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle); return True
    try:
        os.kill(pid, 0); return True
    except OSError:
        return False
def _parse_utc(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
def _auth_info(config: dict[str, Any], source: str) -> dict[str, Any]:
    return {**auth_status(config), "source": source}
def service_status(config: dict[str, Any], name: str) -> dict[str, Any]:
    state = _read_state(config, name)
    heartbeat = _parse_utc(state.get("heartbeat_at"))
    age = None if heartbeat is None else (_now() - heartbeat).total_seconds()
    pid = int(state.get("pid") or 0); alive = _pid_alive(pid)
    return {**state, "ok": bool(state.get("status") == "running" and alive and age is not None and age < 600),
            "process_alive": alive, "heartbeat_age_seconds": None if age is None else round(age, 1)}
def request_stop(config: dict[str, Any], name: str, *, wait_seconds: int = 300) -> dict[str, Any]:
    (_run_dir(config) / f"stop_{name}.request").write_text(_now().isoformat(), encoding="ascii")
    pid = int(_read_state(config, name).get("pid") or 0); deadline = time.monotonic() + max(0, wait_seconds)
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(1)
    return {"ok": not _pid_alive(pid), "pid": pid, "stop_requested": True}

def record_service_failure(config: dict[str, Any], name: str, error: Exception) -> None:
    state = _read_state(config, name); recorded_pid = int(state.get("pid") or 0)
    if recorded_pid != os.getpid() and _pid_alive(recorded_pid):
        return
    state.update(status="failed", pid=os.getpid(), failed_at=_now().isoformat(), error_type=type(error).__name__)
    _write_state(config, name, state)

def _signal_stop(_number: int, _frame: Any) -> None:
    global _SIGNAL_STOP
    _SIGNAL_STOP = True

def _install_signal_handlers() -> None:
    signal.signal(signal.SIGINT, _signal_stop); signal.signal(signal.SIGTERM, _signal_stop)

def _stop_requested(config: dict[str, Any], name: str) -> bool:
    return _SIGNAL_STOP or (_run_dir(config) / f"stop_{name}.request").exists()

def _slot_datetime(day: date, slot: str) -> datetime:
    hour, minute = (int(part) for part in slot.split(":", 1))
    return datetime.combine(day, dtime(hour, minute), tzinfo=timezone.utc)

def _due_slot(slots: list[str], previous: str, *, now: datetime | None = None) -> str:
    current = (now or _now()).astimezone(timezone.utc)
    candidates = [_slot_datetime(current.date() + timedelta(days=offset), str(slot)) for offset in (-1, 0) for slot in slots]
    due = [item for item in candidates if item <= current]
    selected = "" if not due else max(due).isoformat()
    return "" if not selected or selected == previous else selected

def _workflow_pairs(config: dict[str, Any], *, live: bool) -> list[Pair]:
    section = "live" if live else "backfill"
    if not config[section]["enabled"]:
        raise RuntimeError(f"{section} workflow is disabled in Config.yaml")
    return select_pairs(config, live=live)

def _wait_for_database(config: dict[str, Any]) -> dict[str, Any]:
    deadline = time.monotonic() + int(config["service"]["startup_grace_seconds"]); last_error: Exception | None = None
    while True:
        try:
            result = check_connection(config)
            if result["ok"]:
                return result
            last_error = RuntimeError("SQL contract is not ready")
        except Exception as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            raise RuntimeError("SQL startup grace expired") from last_error
        log_event(LOGGER, logging.WARNING, "STARTUP_DEPENDENCY_WAIT", "MEDIUM", component="runtime",
                  dependency="sql_server", error_type=type(last_error).__name__, action="bounded readiness retry")
        time.sleep(float(config["sql_server"].get("retry_delay_seconds", 5)))

def _start_generation(state: dict[str, Any], pairs: list[Pair], *, reason: str) -> deque[Pair]:
    started = _now().isoformat()
    state.update(backfill_generation_reason=reason, backfill_generation_started_at=started,
                 last_backfill_progress_at=started, backfill_generation_total=len(pairs),
                 backfill_generation_processed=0, backfill_queue_remaining=len(pairs),
                 backfill_failed_pairs=[], backfill_deferred_pairs=[],
                 backfill_generation_deferred=0, backfill_circuit_open=False)
    return deque(pairs)

def _set_below_normal_priority() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
    except Exception:
        pass

def _live_yield_active(config: dict[str, Any]) -> bool:
    state = _read_state(config, "live")
    if state.get("status") != "running":
        return False
    pid = int(state.get("pid") or 0)
    if not _pid_alive(pid):
        return False
    heartbeat = _parse_utc(state.get("heartbeat_at"))
    if heartbeat is None or state.get("cycle_active"):
        return True
    if (_now() - heartbeat).total_seconds() > int(config["service"]["heartbeat_seconds"]) * 4:
        return True
    next_due = _parse_utc(state.get("next_live_due_at"))
    return True if next_due is None else (next_due - _now()).total_seconds() <= int(config["service"]["backfill_guard_seconds"])

def _finish(config: dict[str, Any], name: str, state: dict[str, Any], reporter: DiscordReporter) -> dict[str, Any]:
    state.update(status="stopped", cycle_active=False, stopped_at=_now().isoformat())
    _write_state(config, name, state); reporter.publish("stopped", state)
    log_event(LOGGER, logging.INFO, "SERVICE_STOPPED", "NONE", component="runtime", mode=name)
    return {"ok": True, "status": "stopped", "pid": os.getpid()}

def run_live_service(config: dict[str, Any]) -> dict[str, Any]:
    """Run authenticated live cycles continuously; never blocked by backfill."""
    global _SIGNAL_STOP
    _SIGNAL_STOP = False; _install_signal_handlers()
    state: dict[str, Any] = {"mode": "live", "status": "starting", "pid": os.getpid(), "started_at": _now().isoformat()}
    with instance_lock(config, "live"), DiscordReporter(config) as reporter:
        _write_state(config, "live", state)
        if _stop_requested(config, "live"):
            return _finish(config, "live", state, reporter)
        live_pairs = _workflow_pairs(config, live=True)
        credentials = ensure_authenticated(config); database = _wait_for_database(config); replay = drain(config)
        pending_live = {pair_key(pair) for pair in live_pairs}
        state.update(status="running", cycle_active=False, auth=_auth_info(config, credentials["source"]),
                     database={"ok": database["ok"], "database": database["database"]},
                     startup_replay=replay, spool=pending_status(config),
                     bootstrap_remaining_pairs=database["bootstrap_remaining_pairs"],
                     pending_live_pairs=sorted(pending_live))
        _write_state(config, "live", state); reporter.publish("started", state)
        log_event(LOGGER, logging.INFO, "SERVICE_STARTED", "NONE", component="runtime",
                  mode="live", live_pairs=len(live_pairs), auth_source=credentials["source"])
        interval = int(config["live"]["interval_minutes"]) * 60; next_heartbeat = time.monotonic()
        while not _stop_requested(config, "live"):
            started_mono = time.monotonic(); started_at = _now(); due_at = started_at + timedelta(seconds=interval)
            state.update(cycle_active=True, cycle_started_at=started_at.isoformat(), next_live_due_at=due_at.isoformat())
            _write_state(config, "live", state)
            replay = drain(config); credentials = ensure_authenticated(config)
            summary = run_live_pairs(config, live_pairs, pending_pairs=list(state.get("pending_live_pairs") or []),
                                     stop_requested=lambda: _stop_requested(config, "live"))
            if started_mono + interval <= time.monotonic():
                due_at = _now()
                log_event(LOGGER, logging.WARNING, "LIVE_CYCLE_OVERRUN", "MEDIUM", component="runtime",
                          overrun_seconds=round(time.monotonic() - (started_mono + interval), 3),
                          action="next live cycle starts immediately")
            spool = pending_status(config)
            state.update(cycle_active=False, next_live_due_at=due_at.isoformat(), auth=_auth_info(config, credentials["source"]),
                         last_live={"ended_at": _now().isoformat(), **summary}, spool=spool,
                         last_replay=replay, pending_live_pairs=summary["pending_pairs"])
            log_event(LOGGER, logging.INFO, "LIVE_CYCLE_COMPLETED",
                      "HIGH" if summary["failed"] else ("MEDIUM" if summary["deferred"] else "NONE"),
                      component="runtime", pairs=summary["pairs"], ok=summary["ok"], failed=summary["failed"],
                      deferred=summary["deferred"], affected=summary["affected"], spool_pending=spool["pending"],
                      pending_pairs=len(summary["pending_pairs"]), recovered_pairs=len(summary["recovered_pairs"]),
                      replay_delivered=replay["delivered"], duration_seconds=round(time.monotonic() - started_mono, 3),
                      **summary.get("timings", {}))
            _write_state(config, "live", state); reporter.publish("live", state)
            while not _stop_requested(config, "live") and time.monotonic() < started_mono + interval:
                if time.monotonic() >= next_heartbeat:
                    state["spool"] = pending_status(config); _write_state(config, "live", state)
                    reporter.publish("health", state); next_heartbeat = time.monotonic() + int(config["service"]["heartbeat_seconds"])
                time.sleep(1)
        return _finish(config, "live", state, reporter)

def run_backfill_service(config: dict[str, Any]) -> dict[str, Any]:
    """Run scheduled backfill generations continuously; yields to live on overlap."""
    global _SIGNAL_STOP
    _SIGNAL_STOP = False; _install_signal_handlers(); _set_below_normal_priority()
    previous = _read_state(config, "backfill")
    state: dict[str, Any] = {"mode": "backfill", "status": "starting", "pid": os.getpid(),
                             "started_at": _now().isoformat(), "last_backfill_slot": previous.get("last_backfill_slot", "")}
    with instance_lock(config, "backfill"), DiscordReporter(config) as reporter:
        _write_state(config, "backfill", state)
        if _stop_requested(config, "backfill"):
            return _finish(config, "backfill", state, reporter)
        backfill_pairs = _workflow_pairs(config, live=False)
        credentials = ensure_authenticated(config); database = _wait_for_database(config); replay = drain(config)
        startup_pairs = prioritize_backfill_pairs(config, backfill_pairs) if config["backfill"]["run_on_start"] else []
        queue = _start_generation(state, startup_pairs, reason="service_start"); slots = list(config["backfill"]["schedule_utc"])
        startup_slot = _due_slot(slots, str(previous.get("last_backfill_slot") or "")) if startup_pairs else ""
        state.update(status="running", auth=_auth_info(config, credentials["source"]),
                     database={"ok": database["ok"], "database": database["database"]},
                     startup_replay=replay, spool=pending_status(config),
                     bootstrap_remaining_pairs=database["bootstrap_remaining_pairs"],
                     last_backfill_slot=startup_slot or previous.get("last_backfill_slot", ""))
        _write_state(config, "backfill", state); reporter.publish("started", state)
        log_event(LOGGER, logging.INFO, "SERVICE_STARTED", "NONE", component="runtime",
                  mode="backfill", backfill_queued=len(queue), auth_source=credentials["source"])
        failures_consecutive = failures_total = 0; next_heartbeat = time.monotonic()
        while not _stop_requested(config, "backfill"):
            slot = _due_slot(slots, str(state.get("last_backfill_slot") or ""))
            if slot:
                state["last_backfill_slot"] = slot
                if queue:
                    log_event(LOGGER, logging.INFO, "BACKFILL_SCHEDULE_COALESCED", "NONE", component="runtime",
                              slot=slot, queue_remaining=len(queue))
                else:
                    queue = _start_generation(state, prioritize_backfill_pairs(config, backfill_pairs), reason=f"schedule:{slot}")
                    failures_consecutive = failures_total = 0
                    log_event(LOGGER, logging.INFO, "BACKFILL_SCHEDULED", "NONE", component="runtime", slot=slot, pairs=len(queue))
                _write_state(config, "backfill", state)
            if queue and not _live_yield_active(config):
                state["last_replay"] = drain(config); credentials = ensure_authenticated(config)
                group = next_backfill_group(config, list(queue)[:15])
                for _pair in group:
                    queue.popleft()
                summary = run_backfill_pairs(config, group)
                failed_pairs = set(state.get("backfill_failed_pairs") or []) | set(summary["failed_pairs"])
                started = _parse_utc(state["backfill_generation_started_at"]) or _now()
                state.update(last_backfill={"ended_at": _now().isoformat(), **summary},
                             auth=_auth_info(config, credentials["source"]), backfill_failed_pairs=sorted(failed_pairs),
                             backfill_generation_processed=int(state.get("backfill_generation_processed") or 0) + len(group),
                             last_backfill_progress_at=_now().isoformat(),
                             bootstrap_remaining_pairs=max(0, int(state.get("bootstrap_remaining_pairs") or 0) - int(summary["completed_bootstraps"])))
                failures_consecutive = failures_consecutive + 1 if summary.get("group_failures") else 0
                failures_total += 1 if summary.get("group_failures") else 0
                if failures_consecutive >= 2 or failures_total >= 3:
                    deferred_pairs = [pair_key(pair) for pair in queue]; queue.clear()
                    state.update(backfill_circuit_open=True, backfill_deferred_pairs=deferred_pairs,
                                 backfill_generation_deferred=len(deferred_pairs))
                    log_event(LOGGER, logging.ERROR, "BACKFILL_CIRCUIT_OPEN", "HIGH", component="runtime",
                              deferred_pairs=len(deferred_pairs), action="generation deferred until the next schedule")
                state["backfill_queue_remaining"] = len(queue)
                if not queue:
                    state["last_backfill_generation"] = (
                        f"{state.get('backfill_generation_reason', '')}: {state['backfill_generation_processed']}/"
                        f"{state['backfill_generation_total']} processed, {len(failed_pairs)} failed, "
                        f"{state.get('backfill_generation_deferred', 0)} deferred, elapsed {int((_now() - started).total_seconds())}s"
                    )
                    reporter.publish("backfill_completed" if not summary["failed"] else "backfill_failed", state)
                elif summary["failed"]:
                    reporter.publish("backfill_failed", state)
                _write_state(config, "backfill", state); continue
            if time.monotonic() >= next_heartbeat:
                state.update(backfill_queue_remaining=len(queue), spool=pending_status(config))
                _write_state(config, "backfill", state); reporter.publish("health", state)
                next_heartbeat = time.monotonic() + int(config["service"]["heartbeat_seconds"])
            time.sleep(1)
        return _finish(config, "backfill", state, reporter)
