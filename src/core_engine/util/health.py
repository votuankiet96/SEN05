"""Operator-facing health and status checks for the DP Program terminal app.

This module is deliberately read-oriented. It must never expose credentials and
should avoid changing runtime state except for creating required runtime
directories during a readiness check.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core_engine.core.live.outbox import OUTBOX_STALE_ALERT_SECONDS
from core_engine.shared.tradingview import auth as tv_auth
from core_engine.shared.tradingview.diagnostics import playwright_browser_status
from core_engine.shared.time import parse_utc_time as _parse_time
from core_engine.util.runtime_state import load_json
from core_engine.util.logkit import get_logger
from core_engine.util.supervisor.process_control import same_local_host
from core_engine.settings import (
    APP_ROOT,
    BACKEND,
    BACKEND_STATE,
    CACHE_DIR,
    CANDLE_SNAPSHOT,
    ENV_FILE,
    HISTORICAL,
    HISTORICAL_LOG,
    LIVE,
    LIVE_LOG,
    LOG_EMERGENCY_DIR,
    LOG_DIR,
    LOGGING,
    ALERTS_LOG,
    RUN_DIR,
    SPOOL_DIR,
    STORAGE,
    SYMBOLS,
    TF_DISPLAY_ORDER,
    WS_LIVE_STATE,
    WS_OVERFLOW_SPOOL,
    SYSTEM_LOG,
    ensure_runtime_dirs,
    inspect_operator_config,
)
from core_engine.util.coordination.locks import (
    DP_PROGRAM_LOCK,
    HISTORICAL_JOB_LOCK,
    LIVE_BATCH_LOCK,
    LIVE_RUNTIME_LOCK,
    WAREHOUSE_MAINTENANCE_LOCK,
    LockCoordinator,
    local_pid_alive,
)


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    message: str
    detail: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data["detail"] is None:
            data.pop("detail")
        return data


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


LOG_FAIL_TOTAL_BYTES = int(LOGGING.disk_budget_mb) * 1024 * 1024
LOG_WARN_TOTAL_BYTES = int(LOG_FAIL_TOTAL_BYTES * 0.9)
LOG_ACTIVE_FILE_FAIL_BYTES = max(5, int(LOGGING.max_file_mb) * 2) * 1024 * 1024


def _active_runtime_roles() -> dict[str, int]:
    """Return active production role/PID pairs from state plus OS liveness."""
    result: dict[str, int] = {}
    backend = _read_json(BACKEND_STATE)
    live = _read_json(WS_LIVE_STATE)
    candidates = {
        "supervisor": backend.get("pid"),
        "live": live.get("pid") or backend.get("live_pid"),
        "historical": backend.get("historical_pid"),
    }
    for role, raw_pid in candidates.items():
        pid, alive = _pid_state(raw_pid)
        if pid and alive:
            result[role] = pid
    return result


def _age_seconds(value: Any) -> float | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return max(0.0, (_utc_now() - parsed).total_seconds())


def _read_json(path: Path) -> dict[str, Any]:
    return load_json(path)


def _read_last_jsonl(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        last = ""
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.strip():
                    last = line.strip()
        if not last:
            return {}
        data = json.loads(last)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _runtime_check() -> Check:
    try:
        ensure_runtime_dirs()
        writable: dict[str, bool] = {}
        for path in (LOG_DIR, CACHE_DIR, RUN_DIR, SPOOL_DIR):
            probe = path / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            writable[path.name] = True
        usage = shutil.disk_usage(APP_ROOT)
        free_gb = usage.free / (1024**3)
        total_gb = usage.total / (1024**3)
        free_percent = (usage.free / usage.total * 100.0) if usage.total else 0.0
        warn_gb = float(getattr(BACKEND, "disk_warn_free_gb", 5.0))
        fail_gb = float(getattr(BACKEND, "disk_fail_free_gb", 1.0))
        status = "ok"
        message = "Runtime folders are present and writable."
        if free_gb <= fail_gb:
            status = "fail"
            message = "Runtime disk space is critically low; durable spool/log/outbox writes are at risk."
        elif free_gb <= warn_gb:
            status = "warn"
            message = "Runtime disk space is low; free space should be reclaimed before it becomes critical."
        return Check(
            "runtime",
            status,
            message,
            {
                "app_root": str(APP_ROOT),
                "env_file": str(ENV_FILE),
                "writable": writable,
                "disk_total_gb": round(total_gb, 2),
                "disk_free_gb": round(free_gb, 2),
                "disk_free_percent": round(free_percent, 2),
                "disk_warn_free_gb": warn_gb,
                "disk_fail_free_gb": fail_gb,
            },
        )
    except Exception as exc:
        return Check("runtime", "fail", f"Runtime folder check failed: {exc}")


def _config_check() -> Check:
    missing = []
    if not ENV_FILE.exists():
        missing.append(str(ENV_FILE))
    if not SYMBOLS:
        missing.append("config.instruments.SYMBOLS")
    if missing:
        return Check(
            "config",
            "fail",
            "Required DP Program configuration is missing.",
            {"missing": missing},
        )
    report = inspect_operator_config()
    if not report.get("ok"):
        return Check(
            "config_contract",
            "fail",
            "Operator config contains unsupported or invalid settings.",
            {
                "env_file": str(ENV_FILE),
                "issues": report.get("issues", []),
            },
        )
    return Check(
        "config_contract",
        "ok",
        "Operator config is valid and contains only supported settings.",
        {
            "symbols": len(SYMBOLS),
            "env_file": str(ENV_FILE),
            "operator_settings": report.get("key_count", 0),
        },
    )


def _database_check() -> Check:
    try:
        from core_engine.shared.warehouse.connection import get_connection

        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA IN ('SEN','DWH','MART')
            """
        )
        table_count = int(cur.fetchone()[0])
        detail: dict[str, Any] = {"warehouse_tables": table_count}
        try:
            cur.execute("SELECT COUNT_BIG(*) FROM DWH.Fact_OHLCV")
            detail["fact_ohlcv_rows"] = int(cur.fetchone()[0])
            cur.execute(
                """
                SELECT TOP 1 s.Symbol, tf.Code, f.BarTime
                FROM DWH.Fact_OHLCV f
                JOIN DWH.Dim_Symbol s ON s.SymbolID = f.SymbolID
                JOIN DWH.Dim_Timeframe tf ON tf.TimeframeID = f.TimeframeID
                ORDER BY f.BarTime DESC
                """
            )
            row = cur.fetchone()
            if row:
                detail["latest_bar"] = {
                    "symbol": str(row[0]),
                    "timeframe": str(row[1]),
                    "bar_time": str(row[2]),
                }
        except Exception as exc:
            detail["fact_status"] = f"not checked: {exc}"
        conn.close()
        status = "ok" if table_count > 0 else "warn"
        message = "SQL Server is reachable." if status == "ok" else "SQL Server is reachable but warehouse tables were not found."
        return Check("database", status, message, detail)
    except Exception as exc:
        return Check("database", "fail", f"SQL Server check failed: {exc}")


def _db_contract_check() -> Check:
    """Fail loudly if DWH.usp_LoadDirect is not the shape the ETL caller expects.

    This is the check that would have caught the round-2 audit's stale
    usp_LoadDirect finding automatically instead of via a 12+ day silent
    Fact_OHLCV staleness. Unlike the general database check, a contract
    mismatch is always reported as "fail", never "warn" - the caller
    (supervisor/live/historical startup) must refuse to run writes against
    it rather than continue_and_report.
    """
    try:
        from core_engine.shared.warehouse.connection import verify_database_contract

        result = verify_database_contract()
        if result["ok"]:
            return Check(
                "db_contract",
                "ok",
                "DWH.usp_LoadDirect contract version matches.",
                {"object": result["object"], "version": result["version"]},
            )
        return Check("db_contract", "fail", result["reason"] or "contract mismatch", result)
    except Exception as exc:
        return Check("db_contract", "fail", f"db contract check failed: {exc}")


def _critical_outbox_check() -> Check:
    """Surface a stuck CRITICAL-alert backlog (see logkit.critical_outbox)
    even when nobody is watching Discord itself - this is the fallback
    channel for the case that handler exists to cover in the first place,
    so it must not itself be silent."""
    try:
        from core_engine.util.notify.critical_outbox import critical_alert_outbox

        status = critical_alert_outbox().status()
    except Exception as exc:
        return Check("critical_alerts", "warn", f"Could not read critical alert outbox: {exc}")

    if not status.get("healthy") or status.get("pending_count") is None:
        return Check(
            "critical_alerts",
            "fail",
            "CRITICAL alert outbox storage is unhealthy; pending alerts cannot be trusted.",
            status,
        )
    pending = int(status["pending_count"])
    if pending == 0:
        return Check("critical_alerts", "ok", "No undelivered CRITICAL alerts.", status)
    oldest_age = status.get("oldest_pending_age_seconds")
    if oldest_age is not None and oldest_age > 900:
        return Check(
            "critical_alerts",
            "fail",
            f"{pending} CRITICAL alert(s) have been undelivered for over 15 minutes - Discord webhook may be down.",
            status,
        )
    return Check("critical_alerts", "warn", f"{pending} CRITICAL alert(s) waiting for delivery.", status)


def _live_spool_check() -> Check:
    """Detect a live process that receives candles but cannot reach Fact."""
    path = Path(WS_OVERFLOW_SPOOL)
    if not path.exists():
        return Check(
            "live_spool",
            "ok",
            "Live durable spool has not been created yet.",
            {"path": str(path), "pending_count": 0},
        )
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*), MIN(created_at) FROM spool GROUP BY status"
            ).fetchall()
            quarantine = int(
                conn.execute("SELECT COUNT(*) FROM spool_quarantine").fetchone()[0]
            )
    except (OSError, sqlite3.Error) as exc:
        return Check("live_spool", "warn", f"Could not read live durable spool: {exc}")

    by_status = {str(status): int(count) for status, count, _oldest in rows}
    oldest_values = [oldest for _status, _count, oldest in rows if oldest]
    oldest = min(oldest_values) if oldest_values else None
    oldest_age = _age_seconds(oldest)
    pending = sum(by_status.values())
    detail = {
        "path": str(path),
        "pending_count": pending,
        "by_status": by_status,
        "oldest_created_at": oldest,
        "oldest_age_seconds": oldest_age,
        "quarantine_count": quarantine,
    }
    if quarantine:
        return Check(
            "live_spool",
            "fail",
            f"{quarantine} live spool row(s) are quarantined and require operator review.",
            detail,
        )
    if pending == 0:
        return Check("live_spool", "ok", "Live durable spool is empty.", detail)
    if (
        oldest_age is not None
        and oldest_age >= OUTBOX_STALE_ALERT_SECONDS
    ):
        return Check(
            "live_spool",
            "fail",
            f"{pending} live row(s) have waited at least 15 minutes for Fact commit.",
            detail,
        )
    if oldest_age is not None:
        return Check(
            "live_spool",
            "ok",
            f"{pending} live row(s) are in-flight within the normal delivery window.",
            detail,
        )
    return Check(
        "live_spool",
        "warn",
        f"{pending} live row(s) are waiting for Fact commit, but oldest age is unavailable.",
        detail,
    )


def _auth_check(*, deep: bool = False) -> Check:
    try:
        status = tv_auth.browser_profile_status()
        token = status.get("token") or {}
        token_state = str(token.get("state") or "missing")
        usable = token_state in {"valid", "expiring_soon"}
        detail = {
            "token_state": token_state,
            "token_source": status.get("token_source") or "",
            "token_seconds_remaining": token.get("seconds_remaining"),
            "cookie_present": bool(status.get("cookie_present")),
            "username_present": bool(status.get("username_present")),
            "password_present": bool(status.get("password_present")),
            "runtime_cache_present": bool(status.get("cache_present")),
            "browser_profile_present": bool(status.get("profile_present")),
            "auth_lock": tv_auth.auth_refresh_lock_status(),
        }
        if deep:
            diagnosis = tv_auth.diagnose_connectivity()
            detail["connectivity_status"] = diagnosis.get("status")
            detail["connectivity_message"] = diagnosis.get("message")
            if diagnosis.get("status") == "blocker":
                return Check("tradingview_auth", "fail", str(diagnosis.get("message")), detail)
        if usable:
            state = "warn" if token_state == "expiring_soon" else "ok"
            return Check("tradingview_auth", state, "TradingView token is usable.", detail)
        if detail["cookie_present"] or detail["browser_profile_present"] or detail["username_present"]:
            return Check(
                "tradingview_auth",
                "warn",
                "TradingView token is not currently usable, but refresh material is present.",
                detail,
            )
        return Check(
            "tradingview_auth",
            "fail",
            "TradingView credentials are missing or unusable.",
            detail,
        )
    except Exception as exc:
        return Check("tradingview_auth", "fail", f"TradingView auth check failed: {exc}")


def _chromium_check() -> Check:
    ok, detail = playwright_browser_status()
    if ok:
        return Check("chromium", "ok", "Playwright Chromium is installed.", {"executable": detail})
    return Check("chromium", "warn", "Playwright Chromium is not ready for headless auth refresh.", {"reason": detail})


def _log_sinks_check() -> Check:
    active = _active_runtime_roles()
    total_bytes = 0
    log_files: list[Path] = []
    try:
        if LOG_DIR.exists():
            log_files = [path for path in LOG_DIR.rglob("*") if path.is_file()]
            total_bytes = sum(path.stat().st_size for path in log_files)
    except OSError as exc:
        return Check("log_sinks", "fail", f"Runtime logs cannot be inventoried: {exc}")

    detail: dict[str, Any] = {
        "active_roles": active,
        "log_file_count": len(log_files),
        "total_bytes": total_bytes,
        "warn_budget_bytes": LOG_WARN_TOTAL_BYTES,
        "fail_budget_bytes": LOG_FAIL_TOTAL_BYTES,
        "registries": {},
        "missing": [],
        "unwritable": [],
        "oversized_active": [],
        "emergency_fallbacks": [],
    }
    failures: list[str] = []
    warnings: list[str] = []
    for role, pid in active.items():
        registry = RUN_DIR / "log_sinks" / f"{role}.{pid}.json"
        payload = _read_json(registry)
        detail["registries"][role] = str(registry)
        if not payload:
            failures.append(f"missing sink registry for {role} PID {pid}")
            detail["missing"].append(str(registry))
            continue
        if payload.get("error"):
            failures.append(f"{role} sink reported an error: {payload['error']}")
        paths = payload.get("paths") if isinstance(payload.get("paths"), list) else []
        if not paths:
            failures.append(f"empty sink registry for {role} PID {pid}")
            continue
        expected = {
            "supervisor": {str(SYSTEM_LOG.resolve()), str(ALERTS_LOG.resolve())},
            "live": {str(LIVE_LOG.resolve()), str(ALERTS_LOG.resolve())},
            "historical": {str(HISTORICAL_LOG.resolve()), str(ALERTS_LOG.resolve())},
        }.get(role, set())
        missing_expected = expected - {str(Path(item).resolve()) for item in paths}
        if missing_expected:
            failures.append(f"{role} sink registry is missing canonical streams")
            detail["missing"].extend(sorted(missing_expected))
        for item in paths:
            physical = Path(str(item or ""))
            if not physical.is_file():
                detail["missing"].append(str(physical))
                failures.append(f"registered sink is missing: {physical.name}")
                continue
            try:
                size = physical.stat().st_size
                # Opening without writing validates ACL/share compatibility and
                # does not perturb file age or content.
                descriptor = os.open(str(physical), os.O_WRONLY | os.O_APPEND)
                os.close(descriptor)
            except OSError as exc:
                detail["unwritable"].append({"path": str(physical), "reason": str(exc)})
                failures.append(f"registered sink is not appendable: {physical.name}")
                continue
            if size > LOG_ACTIVE_FILE_FAIL_BYTES:
                detail["oversized_active"].append({"path": str(physical), "bytes": size})
                failures.append(f"active sink exceeds 50 MiB: {physical.name}")

        crash_path = RUN_DIR / "log_emergency" / f"crash.{role}.{pid}.log"
        if not crash_path.exists():
            failures.append(f"early crash capture is missing for {role} PID {pid}")
            detail["missing"].append(str(crash_path))

        emergency_path = LOG_EMERGENCY_DIR / f"{role}.{pid}.log"
        try:
            if emergency_path.exists() and emergency_path.stat().st_size:
                detail["emergency_fallbacks"].append(str(emergency_path))
                failures.append(f"{role} PID {pid} used the emergency logging fallback")
        except OSError as exc:
            failures.append(f"cannot inspect {role} emergency logging fallback: {exc}")

    retention_state = _read_json(RUN_DIR / "log_retention_state.json")
    detail["retention"] = retention_state
    if retention_state.get("failed"):
        failures.append("the last runtime retention pass had deletion errors")
    if total_bytes >= LOG_FAIL_TOTAL_BYTES:
        failures.append("runtime logs exceed the configured hard budget")
    elif total_bytes >= LOG_WARN_TOTAL_BYTES:
        warnings.append("runtime logs exceed 90% of the configured hard budget")

    if failures:
        return Check("log_sinks", "fail", "; ".join(dict.fromkeys(failures)), detail)
    if warnings:
        return Check("log_sinks", "warn", "; ".join(warnings), detail)
    message = (
        "All active process log sinks are registered, appendable, and within budget."
        if active
        else "No active DP process; retained log files are within budget."
    )
    return Check("log_sinks", "ok", message, detail)


def _discord_check() -> Check:
    from core_engine.settings import NOTIFICATION

    if not NOTIFICATION.discord_webhook_url:
        return Check("discord", "warn", "Discord webhook is not configured.", {"configured": False})

    active = _active_runtime_roles()
    detail: dict[str, Any] = {
        "configured": True,
        "active_roles": active,
        "senders": {},
        "unexercised_roles": [],
    }
    if not active:
        return Check(
            "discord",
            "warn",
            "Discord webhook is configured, but no active process can prove delivery health.",
            detail,
        )

    failures: list[str] = []
    warnings: list[str] = []
    any_success = False
    for role, pid in active.items():
        path = RUN_DIR / "notification_status" / f"{role}.{pid}.json"
        status = _read_json(path)
        detail["senders"][role] = status or {"path": str(path), "missing": True}
        if not status:
            # A worker that has not emitted an event yet has not demonstrated a
            # failure. The supervisor owns lifecycle notifications for its
            # children, so fail the channel only when no active sender has a
            # confirmed delivery (checked below), not merely because every
            # child role has not independently exercised it yet.
            detail["unexercised_roles"].append(role)
            continue
        if status.get("logger_error"):
            failures.append(f"Discord log sink failed for {role} PID {pid}")
        pending = int(status.get("queue_pending") or 0)
        maximum = max(1, int(status.get("queue_maxsize") or 1))
        if pending >= maximum:
            failures.append(f"Discord queue is full for {role} PID {pid}")
        if pending and status.get("worker_started") and not status.get("worker_alive"):
            failures.append(f"Discord worker is dead with pending messages for {role} PID {pid}")
        if float(status.get("circuit_open_seconds") or 0) > 0:
            failures.append(f"Discord circuit is open for {role} PID {pid}")
        success_at = status.get("last_success_at")
        failure_at = status.get("last_failure_at")
        if success_at:
            any_success = True
        if failure_at and (not success_at or str(failure_at) > str(success_at)):
            failure_age = _age_seconds(failure_at)
            text = f"latest Discord attempt failed for {role} PID {pid}"
            (failures if failure_age is not None and failure_age > 900 else warnings).append(text)
    if not any_success:
        failures.append("no active process has a confirmed Discord HTTP 200/204 delivery")

    if failures:
        return Check("discord", "fail", "; ".join(dict.fromkeys(failures)), detail)
    if warnings:
        return Check("discord", "warn", "; ".join(dict.fromkeys(warnings)), detail)
    return Check(
        "discord",
        "ok",
        "Discord is configured and active senders have confirmed delivery.",
        detail,
    )


def _locks_check() -> Check:
    try:
        coord = LockCoordinator()
        records = {}
        active_names: list[str] = []
        stale_same_host: list[str] = []
        for name in (
            DP_PROGRAM_LOCK,
            LIVE_RUNTIME_LOCK,
            LIVE_BATCH_LOCK,
            HISTORICAL_JOB_LOCK,
            WAREHOUSE_MAINTENANCE_LOCK,
        ):
            active_record = coord.fetch(name, active_only=True)
            record = active_record or coord.fetch(name, active_only=False)
            if record:
                meta = record.meta
                pid_alive: bool | None = None
                same_host_dead = False
                pid_text = meta.get("pid")
                same_host = same_local_host(meta.get("host"))
                if pid_text:
                    try:
                        pid_alive = local_pid_alive(int(pid_text))
                        same_host_dead = bool(active_record and same_host and not pid_alive)
                    except Exception:
                        pid_alive = None
                if active_record:
                    active_names.append(name)
                if same_host_dead:
                    stale_same_host.append(name)
                records[name] = {
                    "active": bool(active_record),
                    "started_at": str(record.started_at or ""),
                    "expires_at": str(record.expires_at or ""),
                    "pid_alive": pid_alive,
                    "same_host_dead": same_host_dead,
                    "meta": meta,
                }
            else:
                records[name] = None
        status = "warn" if stale_same_host else "ok"
        message = (
            "Runtime lock table is readable, but stale same-host lock(s) were found."
            if stale_same_host
            else "Runtime lock table is readable."
        )
        return Check(
            "locks",
            status,
            message,
            {
                "active": active_names,
                "stale_same_host": stale_same_host,
                "records": records,
            },
        )
    except Exception as exc:
        return Check("locks", "warn", f"Could not inspect runtime locks: {exc}")


def _pid_state(value: Any) -> tuple[int | None, bool | None]:
    try:
        pid = int(str(value or "").strip())
    except Exception:
        return None, None
    if pid <= 0:
        return None, None
    try:
        return pid, local_pid_alive(pid)
    except Exception:
        return pid, None


def _live_state_check() -> Check:
    state = _read_json(WS_LIVE_STATE)
    if not state:
        return Check(
            "live_state",
            "warn",
            "Live state file is missing or empty.",
            {"path": str(WS_LIVE_STATE)},
        )
    age = _age_seconds(state.get("updated_at"))
    # batch_completed_at is a *business-progress* signal, distinct from
    # updated_at (a process-liveness heartbeat written by its own thread -
    # see LiveStateWriter.heartbeat_loop). A deadlocked/hung main loop can
    # still tick the heartbeat thread while never finishing another batch,
    # which the heartbeat-only staleness check below cannot detect.
    batch_age = _age_seconds(state.get("batch_completed_at"))
    child_age = _age_seconds(state.get("child_started_at"))
    batch_started_age = _age_seconds(state.get("batch_started_at"))
    stale_after = BACKEND.live_stale_minutes * 60
    detail = {
        "path": str(WS_LIVE_STATE),
        "status": state.get("status"),
        "pid": state.get("pid"),
        "updated_at": state.get("updated_at"),
        "age_seconds": round(age, 1) if age is not None else None,
        "batch_completed_at": state.get("batch_completed_at"),
        "batch_age_seconds": round(batch_age, 1) if batch_age is not None else None,
        "child_age_seconds": round(child_age, 1) if child_age is not None else None,
        "batch_started_age_seconds": round(batch_started_age, 1) if batch_started_age is not None else None,
        "batches_run": state.get("batches_run"),
        "accepted_bars": state.get("accepted_bars"),
        "fact_inserted": state.get("fact_inserted"),
        "errors": state.get("errors"),
        "ws_forced_socket_closes": state.get("ws_forced_socket_closes", 0),
        "ws_orphaned_threads": state.get("ws_orphaned_threads", 0),
        "ws_wedged_group_recycles": state.get("ws_wedged_group_recycles", 0),
    }
    state_status = str(state.get("status") or "").lower()
    active_status = state_status in {
        "starting",
        "running",
        "waiting",
        "batch_running",
        "batch_stale_released",
        "network_blocked",
        "handoff_waiting",
    }
    inactive_status = state_status in {"failed", "stopped"}
    pid, alive = _pid_state(state.get("pid"))
    detail["pid_alive"] = alive
    if inactive_status and BACKEND.live_auto_start:
        # Live is configured to run 24/7 but its own state file says it is
        # not running. Previously this fell through to "ok" simply because
        # "failed"/"stopped" were not in the active_status set at all -
        # meaning a live worker that crashed and was NOT auto-restarted
        # (e.g. mid-backoff, or restart_on_exit disabled) looked healthy to
        # doctor/status until someone happened to read the status field by
        # eye.
        return Check(
            "live_state",
            "fail",
            f"Live is designed to run continuously but its state is '{state.get('status')}'.",
            detail,
        )
    if active_status and pid and alive is False:
        return Check(
            "live_state",
            "fail" if BACKEND.live_auto_start else "warn",
            "Live state is stale: it says live is active, but that PID is not running.",
            detail,
        )
    if age is None:
        return Check("live_state", "warn", "Live state timestamp is not readable.", detail)
    if active_status and age > stale_after:
        return Check("live_state", "fail", "Live state heartbeat is stale.", detail)
    if active_status and batch_age is not None and batch_age > stale_after:
        return Check(
            "live_state",
            "fail",
            "Live process heartbeat is current, but it has not completed a batch in longer than "
            "the stale threshold - the main loop may be stuck even though the process is alive.",
            detail,
        )
    if active_status and batch_age is None:
        # Before the first completed batch there is no batch_completed_at
        # watermark yet. That is legitimate only during the startup grace
        # window; otherwise a first-batch deadlock looks healthy forever
        # because the independent heartbeat thread keeps updated_at fresh.
        first_progress_age = batch_started_age if state_status == "batch_running" else child_age
        if first_progress_age is not None and first_progress_age > stale_after:
            return Check(
                "live_state",
                "fail",
                "Live heartbeat is current, but the first batch has not completed within the startup grace period.",
                detail,
            )
    return Check("live_state", "ok", "Live state is readable.", detail)


def _historical_state_check() -> Check:
    summary_path = RUN_DIR / "historical_last_run.json"
    summary = _read_json(summary_path)
    if not summary:
        return Check(
            "historical_state",
            "warn",
            "No historical run summary has been written yet.",
            {"path": str(summary_path)},
        )
    age = _age_seconds(summary.get("ts") or summary.get("completed_at"))
    return Check(
        "historical_state",
        "ok",
        "Historical run summary is available.",
        {
            "path": str(summary_path),
            "last_mode": summary.get("mode"),
            "last_ts": summary.get("ts"),
            "age_seconds": round(age, 1) if age is not None else None,
            "stats": summary.get("stats"),
        },
    )


def _backend_state_check() -> Check:
    state = _read_json(BACKEND_STATE)
    if not state:
        return Check(
            "program_state",
            "warn",
            "DP Program supervisor state file is missing or empty.",
            {"path": str(BACKEND_STATE)},
        )
    age = _age_seconds(state.get("updated_at"))
    detail = {
        "path": str(BACKEND_STATE),
        "status": state.get("status"),
        "pid": state.get("pid"),
        "updated_at": state.get("updated_at"),
        "age_seconds": round(age, 1) if age is not None else None,
        "live_pid": state.get("live_pid"),
        "historical_pid": state.get("historical_pid"),
    }
    active_status = str(state.get("status") or "").lower() in {"starting", "running", "stopping"}
    _, alive = _pid_state(state.get("pid"))
    detail["pid_alive"] = alive
    if active_status and alive is False:
        return Check(
            "program_state",
            "warn",
            "DP Program supervisor state is stale: the recorded PID is not running.",
            detail,
        )
    stale_after = max(90, BACKEND.health_interval_sec * 4)
    if active_status and alive and age is not None and age > stale_after:
        detail["stale_after_seconds"] = stale_after
        return Check(
            "program_state",
            "fail",
            "DP Program supervisor state is not updating. The process exists, but its main control loop may be stuck.",
            detail,
        )
    if active_status and alive:
        try:
            supervisor_lock = LockCoordinator().fetch(DP_PROGRAM_LOCK, active_only=True)
            detail["supervisor_lock_active"] = bool(supervisor_lock)
            if not supervisor_lock:
                return Check(
                    "program_state",
                    "warn",
                    "DP Program supervisor is running, but its runtime coordination lock is missing.",
                    detail,
                )
        except Exception as exc:
            detail["supervisor_lock_check_error"] = str(exc)
            return Check(
                "program_state",
                "warn",
                "DP Program supervisor is running, but its runtime coordination lock could not be checked.",
                detail,
            )
    return Check(
        "program_state",
        "ok",
        "DP Program supervisor state is readable.",
        detail,
    )


def _short_command(command: str, *, max_len: int = 180) -> str:
    text = " ".join(str(command or "").split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _classify_dp_process(command: str) -> str:
    lower = command.lower()
    if " chart-datacheck" in lower or "chart_datacheck" in lower:
        return "Chart & Data Health"
    if " auth login" in lower:
        return "TradingView Login"
    if " historical" in lower or "historical_pulling" in lower:
        return "Historical Pulling"
    if " live" in lower or "live_fetching" in lower:
        return "Live Fetching"
    if " run" in lower or "backend_engine" in lower:
        return "DP Program Supervisor"
    return "DP Program Utility"


def _is_transient_status_command(command: str) -> bool:
    lower = command.lower()
    transient = (
        " status",
        " doctor",
        " settings",
        " data-health",
        " auth status",
        " auth diagnose",
        " conflict-status",
        " operator-decision",
        " clean-runtime",
        " queue-historical",
    )
    return any(item in lower for item in transient)


def _process_inventory_check() -> Check:
    """List real DP Program Python processes, excluding log tail windows."""
    if os.name != "nt":
        return Check(
            "process_inventory",
            "warn",
            "Process inventory is currently implemented for Windows hosts only.",
            {"processes": []},
        )

    script = r"""
$rows = Get-CimInstance Win32_Process |
  Where-Object {
    ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and
    $_.CommandLine -and
    ($_.CommandLine -like '*core_engine*' -or $_.CommandLine -like '*dp_program*')
  } |
  Select-Object `
    @{n='pid';e={[int]$_.ProcessId}},
    @{n='parent_pid';e={[int]$_.ParentProcessId}},
    @{n='name';e={$_.Name}},
    @{n='created_at';e={ if ($_.CreationDate) { $_.CreationDate.ToUniversalTime().ToString('o') } else { '' } }},
    @{n='command';e={$_.CommandLine}}
$rows | ConvertTo-Json -Compress -Depth 4
"""
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as exc:
        return Check("process_inventory", "warn", f"Could not inspect Windows processes: {exc}", {"processes": []})

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return Check(
            "process_inventory",
            "warn",
            "Could not inspect Windows processes.",
            {"error": detail[:500], "processes": []},
        )

    raw = (completed.stdout or "").strip()
    rows: list[dict[str, Any]]
    if not raw:
        rows = []
    else:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                rows = [parsed]
            elif isinstance(parsed, list):
                rows = [row for row in parsed if isinstance(row, dict)]
            else:
                rows = []
        except json.JSONDecodeError:
            rows = []

    current_pid = os.getpid()
    processes: list[dict[str, Any]] = []
    for row in rows:
        try:
            pid = int(row.get("pid") or 0)
        except Exception:
            pid = 0
        command = str(row.get("command") or "")
        if not pid or pid == current_pid:
            continue
        if _is_transient_status_command(command):
            continue
        # Log tails are PowerShell Get-Content windows and are intentionally not in this Python-only inventory.
        role = _classify_dp_process(command)
        processes.append(
            {
                "pid": pid,
                "parent_pid": row.get("parent_pid"),
                "role": role,
                "created_at": row.get("created_at") or "",
                "command": _short_command(command),
            }
        )

    processes.sort(key=lambda item: (str(item.get("role")), int(item.get("pid") or 0)))
    if not processes:
        return Check(
            "process_inventory",
            "ok",
            "No DP Program runtime process is currently running.",
            {"count": 0, "processes": [], "note": "Log tail windows are excluded."},
        )
    return Check(
        "process_inventory",
        "ok",
        f"{len(processes)} DP Program runtime process(es) are running.",
        {"count": len(processes), "processes": processes, "note": "Log tail windows are excluded."},
    )


def _redis_snapshot_check() -> Check:
    """Probe the optional OG snapshot lane without weakening SQL health.

    ``both`` keeps SQL Server authoritative, so a Redis outage is a warning
    rather than a service-wide failure. The check belongs to the slower
    DB-inclusive cycle to avoid adding network I/O to the 30-second fast
    heartbeat loop.
    """
    if STORAGE.mode == "sql" or not CANDLE_SNAPSHOT.enabled:
        return Check(
            "redis_snapshot",
            "ok",
            "Redis candle snapshot handoff is disabled.",
            {"enabled": False, "storage_mode": STORAGE.mode},
        )
    if not CANDLE_SNAPSHOT.redis_host:
        return Check(
            "redis_snapshot",
            "warn",
            "Redis candle snapshot handoff is enabled but no host is configured.",
            {"enabled": True, "storage_mode": STORAGE.mode, "configured": False},
        )

    client = None
    try:
        import redis

        timeout = max(0.05, min(float(CANDLE_SNAPSHOT.timeout_sec), 5.0))
        client = redis.Redis(
            host=CANDLE_SNAPSHOT.redis_host,
            port=CANDLE_SNAPSHOT.redis_port,
            username=CANDLE_SNAPSHOT.redis_username or None,
            password=CANDLE_SNAPSHOT.redis_password or None,
            db=CANDLE_SNAPSHOT.redis_db,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
        )
        if not client.ping():
            raise RuntimeError("PING returned a false response")
        return Check(
            "redis_snapshot",
            "ok",
            "Redis candle snapshot handoff is reachable.",
            {"enabled": True, "storage_mode": STORAGE.mode, "configured": True},
        )
    except Exception as exc:
        return Check(
            "redis_snapshot",
            "warn",
            "Redis candle snapshot handoff is not reachable; SQL remains authoritative.",
            {
                "enabled": True,
                "storage_mode": STORAGE.mode,
                "configured": True,
                "reason": f"{type(exc).__name__}: {exc}",
            },
        )
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def collect_health(*, deep_auth: bool = False, include_database: bool = True) -> dict[str, Any]:
    checks = [
        _runtime_check(),
        _config_check(),
        _process_inventory_check(),
        _chromium_check(),
        _auth_check(deep=deep_auth),
        _log_sinks_check(),
        _discord_check(),
        _critical_outbox_check(),
        _backend_state_check(),
        _live_state_check(),
        _live_spool_check(),
        _historical_state_check(),
    ]
    if include_database:
        checks.insert(2, _database_check())
        checks.insert(3, _db_contract_check())
        checks.insert(4, _redis_snapshot_check())
        checks.append(_locks_check())

    statuses = [check.status for check in checks]
    overall = "fail" if "fail" in statuses else ("warn" if "warn" in statuses else "ok")
    return {
        "status": overall,
        "generated_at": _utc_now().isoformat(),
        "python": sys.version.split()[0],
        "app_root": str(APP_ROOT),
        "checks": [check.to_dict() for check in checks],
    }


def _format_dt(value: Any) -> str:
    if value is None:
        return "-"
    try:
        parsed = value
        if isinstance(value, str):
            parsed = _parse_time(value) or value
        if isinstance(parsed, datetime):
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        pass
    return str(value)


def _format_age(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.0f}m"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def _historical_schedule_context() -> dict[str, Any]:
    """Describe whether historical-only freshness is inside its run schedule."""
    enabled = bool(getattr(BACKEND, "historical_backfill_enabled", False))
    schedule_text = str(getattr(BACKEND, "historical_backfill_utc", "") or "")
    max_runtime_minutes = max(
        1,
        int(getattr(BACKEND, "historical_max_runtime_minutes", 0) or 0),
    )
    context: dict[str, Any] = {
        "enabled": enabled,
        "schedule_utc": schedule_text,
        "max_runtime_minutes": max_runtime_minutes,
        "current": False,
    }
    if not enabled:
        context["reason"] = "scheduled historical backfill is disabled"
        return context

    schedule: list[tuple[int, int]] = []
    try:
        for raw in schedule_text.split(","):
            token = raw.strip()
            if not token:
                continue
            hour_text, minute_text = token.split(":", 1)
            hour = int(hour_text)
            minute = int(minute_text)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError(token)
            schedule.append((hour, minute))
    except (TypeError, ValueError):
        context["reason"] = "historical schedule is invalid"
        return context
    schedule = sorted(set(schedule))
    if not schedule:
        context["reason"] = "historical schedule is empty"
        return context

    now = _utc_now().astimezone(timezone.utc)
    due_slots: list[datetime] = []
    for day_offset in range(-3, 1):
        day = (now + timedelta(days=day_offset)).date()
        for hour, minute in schedule:
            slot = datetime(
                day.year,
                day.month,
                day.day,
                hour,
                minute,
                tzinfo=timezone.utc,
            )
            if slot <= now:
                due_slots.append(slot)
    due_slots.sort()
    if not due_slots:
        context["reason"] = "no historical schedule slot could be resolved"
        return context

    current_due = due_slots[-1]
    previous_due = due_slots[-2] if len(due_slots) > 1 else current_due
    grace_deadline = current_due + timedelta(minutes=max_runtime_minutes)
    required_success_after = (
        previous_due if now <= grace_deadline else current_due
    )

    state_path = RUN_DIR / "historical_last_run.json"
    state = _read_json(state_path)
    completed_at = _parse_time(state.get("completed_at") or state.get("ts"))
    stats = state.get("stats") if isinstance(state.get("stats"), dict) else {}
    try:
        failures = int(stats.get("fail", 0) or 0)
        succeeded = int(stats.get("ok", 0) or 0)
        queued_raw = stats.get("queued")
        queued = int(queued_raw) if queued_raw is not None else None
    except (TypeError, ValueError):
        failures = 1
        succeeded = 0
        queued = None
    last_run_successful = (
        completed_at is not None
        and failures == 0
        and (queued is None or succeeded >= queued)
    )
    current = bool(
        last_run_successful
        and completed_at is not None
        and completed_at >= required_success_after
    )

    context.update(
        {
            "state_path": str(state_path),
            "current_due_at": current_due.isoformat(),
            "grace_deadline": grace_deadline.isoformat(),
            "required_success_after": required_success_after.isoformat(),
            "last_completed_at": completed_at.isoformat() if completed_at else None,
            "last_run_successful": last_run_successful,
            "last_run_age_seconds": (
                max(0.0, (now - completed_at).total_seconds())
                if completed_at is not None
                else None
            ),
            "current": current,
        }
    )
    if not current:
        context["reason"] = (
            "no successful historical run completed within the required schedule window"
        )
    return context


def _partition_data_health_repairs(
    items: list[dict[str, Any]],
    *,
    live_asset_types: set[str],
    historical_schedule_current: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Separate expected historical-only latency from actionable repair work."""
    live_types = {str(value).upper() for value in live_asset_types}
    actionable: list[dict[str, Any]] = []
    scheduled_latency: list[dict[str, Any]] = []
    for item in items:
        sym = item.get("sym") if isinstance(item.get("sym"), dict) else {}
        asset_type = str(sym.get("asset_type") or "").upper()
        expected_latency = (
            historical_schedule_current
            and str(item.get("reason") or "") == "STALE"
            and bool(asset_type)
            and asset_type not in live_types
        )
        if expected_latency:
            scheduled_latency.append(item)
        else:
            actionable.append(item)
    return actionable, scheduled_latency


def collect_data_health(*, lookback_days: int | None = None) -> dict[str, Any]:
    """Read-only warehouse coverage summary for operators.

    This is intentionally separate from `doctor`: it answers whether the OHLCV
    data itself looks current enough to run production, not whether the machine
    dependencies are installed.
    """
    from core_engine.shared.warehouse.reader import get_internal_gaps, get_latest_bars
    from core_engine.shared.warehouse.connection import get_connection
    from core_engine.core.historical.runtime_support import (
        find_hole_pairs,
        find_stale_pairs,
        load_verified_gaps,
    )

    lookback = int(lookback_days or HISTORICAL.hole_lookback_days)
    expected_pairs = len(SYMBOLS) * len(TF_DISPLAY_ORDER)
    latest = get_latest_bars()
    symbol_by_id = {int(sym["symbol_id"]): sym for sym in SYMBOLS}
    configured_keys = {
        (int(sym["symbol_id"]), str(tf_code))
        for sym in SYMBOLS
        for tf_code in TF_DISPLAY_ORDER
    }
    latest_configured = {
        key: value
        for key, value in latest.items()
        if key in configured_keys
    }
    missing_keys = sorted(configured_keys - set(latest_configured.keys()))
    stale = find_stale_pairs(latest, symbols=SYMBOLS)
    raw_stale_keys = {
        (int(item["sym"]["symbol_id"]), str(item["tf_code"]))
        for item in stale
    }
    repair_items = list(stale)
    verified_gaps = load_verified_gaps()
    new_holes = find_hole_pairs(
        repair_items,
        get_logger("data_health", stream="system", console=False),
        verified_gaps=verified_gaps,
        lookback_days=lookback,
        symbols=SYMBOLS,
        tf_filter=set(TF_DISPLAY_ORDER),
    )
    repair_items.extend(new_holes)
    historical_schedule = _historical_schedule_context()
    repair_items, scheduled_latency = _partition_data_health_repairs(
        repair_items,
        live_asset_types=set(LIVE.asset_types),
        historical_schedule_current=bool(historical_schedule.get("current")),
    )
    scheduled_latency_keys = {
        (int(item["sym"]["symbol_id"]), str(item["tf_code"]))
        for item in scheduled_latency
    }
    actionable_stale_keys = raw_stale_keys - scheduled_latency_keys
    market_open_gap_keys = {
        (int(item["sym"]["symbol_id"]), str(item["tf_code"]))
        for item in repair_items
        if "HOLE" in str(item.get("reason", ""))
    }

    newest_key = None
    oldest_key = None
    if latest_configured:
        newest_key = max(latest_configured, key=lambda key: latest_configured[key])
        oldest_key = min(latest_configured, key=lambda key: latest_configured[key])

    now = _utc_now().replace(tzinfo=None)

    def pair_row(key: tuple[int, str], value: Any = None) -> dict[str, Any]:
        sym = symbol_by_id.get(int(key[0]), {})
        age = None
        if isinstance(value, datetime):
            dt = value.replace(tzinfo=None) if value.tzinfo else value
            age = max(0.0, (now - dt).total_seconds())
        return {
            "symbol": sym.get("tv_symbol", str(key[0])),
            "timeframe": key[1],
            "last_bar": _format_dt(value),
            "age": _format_age(age),
        }

    def repair_example(item: dict[str, Any]) -> dict[str, Any]:
        sym = item.get("sym") or {}
        last_bar = item.get("last_bar")
        return {
            "symbol": sym.get("tv_symbol", "-"),
            "timeframe": item.get("tf_code", "-"),
            "reason": item.get("reason", "-"),
            "last_bar": _format_dt(last_bar),
            "gap": _format_age(float(item.get("gap_hours") or 0) * 3600),
            "suggested_pull_bars": item.get("n_bars"),
        }

    repair_items.sort(key=lambda item: (item.get("reason") != "MISS", -float(item.get("gap_hours") or 0)))
    scheduled_latency.sort(
        key=lambda item: -float(item.get("gap_hours") or 0)
    )
    worst_stale = [repair_example(item) for item in repair_items[:15]]
    scheduled_latency_examples = [
        repair_example(item) for item in scheduled_latency[:15]
    ]

    raw_gaps = get_internal_gaps(list(TF_DISPLAY_ORDER), lookback_days=lookback)
    gap_windows = sum(len(rows) for rows in raw_gaps.values())
    gap_examples = []
    for (symbol_id, tf_code), rows in sorted(
        raw_gaps.items(),
        key=lambda pair: max((int(row[2] or 0) for row in pair[1]), default=0),
        reverse=True,
    )[:15]:
        sym = symbol_by_id.get(int(symbol_id), {})
        worst = max(rows, key=lambda row: int(row[2] or 0))
        gap_examples.append(
            {
                "symbol": sym.get("tv_symbol", str(symbol_id)),
                "timeframe": tf_code,
                "from": _format_dt(worst[0]),
                "to": _format_dt(worst[1]),
                "gap_minutes": int(worst[2] or 0),
            }
        )

    fact_rows = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT_BIG(*) FROM DWH.Fact_OHLCV")
        fact_rows = int(cur.fetchone()[0])
        conn.close()
    except Exception:
        fact_rows = None

    recommendation = "OK to start DP Program 24/7."
    status = "ok"
    if len(latest_configured) == 0:
        status = "fail"
        recommendation = "No OHLCV data found. Run Initial Full Pull before production live operation."
    elif repair_items or missing_keys:
        status = "warn"
        recommendation = "Run Backfill Missing / Gap Repair, then review Data Health again."
    elif scheduled_latency:
        recommendation = (
            "No manual repair needed. Historical-only latency is within "
            "the configured schedule grace."
        )

    return {
        "status": status,
        "generated_at": _utc_now().isoformat(),
        "lookback_days": lookback,
        "fact_rows": fact_rows,
        "coverage": {
            "symbols": len(SYMBOLS),
            "timeframes": len(TF_DISPLAY_ORDER),
            "expected_symbol_timeframe_pairs": expected_pairs,
            "pairs_with_data": len(latest_configured),
            "pairs_missing_all_data": len(missing_keys),
            "pairs_needing_repair": len(repair_items),
            # GO-gate metric: unlike the raw LEAD() scan, this excludes
            # weekend time, configured overnight allowance, verified
            # windows, and exact recurring market-closure signatures.
            "market_open_gap_pairs": len(market_open_gap_keys),
            "verified_upstream_gap_pairs": len(verified_gaps),
            "verified_upstream_gap_windows": sum(len(rows) for rows in verified_gaps.values()),
            "stale_pairs": len(actionable_stale_keys),
            "raw_stale_pairs": len(raw_stale_keys),
            "scheduled_historical_latency_pairs": len(scheduled_latency_keys),
            "raw_internal_gap_pairs": len(raw_gaps),
            "raw_internal_gap_windows": gap_windows,
        },
        "historical_schedule": historical_schedule,
        "newest_bar": pair_row(newest_key, latest_configured.get(newest_key)) if newest_key else None,
        "oldest_latest_bar": pair_row(oldest_key, latest_configured.get(oldest_key)) if oldest_key else None,
        "missing_examples": [pair_row(key) for key in missing_keys[:15]],
        "repair_examples": worst_stale,
        "scheduled_historical_latency_examples": scheduled_latency_examples,
        "internal_gap_examples": gap_examples,
        "recommendation": recommendation,
    }


def print_data_health(report: dict[str, Any], *, as_json: bool = False) -> None:
    if as_json:
        print_json(report)
        return
    coverage = report.get("coverage") or {}
    print(f"DP Program data health: {str(report.get('status')).upper()}")
    print(f"Generated at : {report.get('generated_at')}")
    print(f"Lookback     : {report.get('lookback_days')} day(s)")
    print(f"Fact rows    : {report.get('fact_rows') if report.get('fact_rows') is not None else '-'}")
    print("")
    print("[Coverage]")
    print(f"- Symbols/timeframes expected : {coverage.get('expected_symbol_timeframe_pairs')}")
    print(f"- Pairs with data             : {coverage.get('pairs_with_data')}")
    print(f"- Pairs missing all data       : {coverage.get('pairs_missing_all_data')}")
    print(f"- Pairs needing repair         : {coverage.get('pairs_needing_repair')}")
    print(f"- Market-open gap pairs        : {coverage.get('market_open_gap_pairs')}")
    print(f"- Actionable stale pairs       : {coverage.get('stale_pairs')}")
    print(f"- Scheduled historical latency : {coverage.get('scheduled_historical_latency_pairs')}")
    print(f"- Raw stale candidates         : {coverage.get('raw_stale_pairs')}")
    print(f"- Raw timeline gap pairs       : {coverage.get('raw_internal_gap_pairs')}")
    print(f"- Raw timeline gap windows     : {coverage.get('raw_internal_gap_windows')}")
    newest = report.get("newest_bar") or {}
    oldest = report.get("oldest_latest_bar") or {}
    print("")
    print("[Latest Candles]")
    print(f"- Newest candle                : {newest.get('symbol', '-')} {newest.get('timeframe', '-')} @ {newest.get('last_bar', '-')}")
    print(f"- Oldest latest candle         : {oldest.get('symbol', '-')} {oldest.get('timeframe', '-')} @ {oldest.get('last_bar', '-')} ({oldest.get('age', '-')})")
    if report.get("repair_examples"):
        print("")
        print("[Needs Repair: first examples]")
        for row in report["repair_examples"][:10]:
            print(
                f"- {row['symbol']} {row['timeframe']}: {row['reason']} | "
                f"last={row['last_bar']} | gap={row['gap']} | pull~{row['suggested_pull_bars']}"
            )
    if report.get("scheduled_historical_latency_examples"):
        print("")
        print("[Scheduled Historical Latency: informational]")
        for row in report["scheduled_historical_latency_examples"][:10]:
            print(
                f"- {row['symbol']} {row['timeframe']}: "
                f"last={row['last_bar']} | gap={row['gap']}"
            )
    if report.get("missing_examples"):
        print("")
        print("[Missing Data: first examples]")
        for row in report["missing_examples"][:10]:
            print(f"- {row['symbol']} {row['timeframe']}: no candles in Fact_OHLCV")
    if report.get("internal_gap_examples"):
        print("")
        print("[Timeline Gap Examples: raw scan, may include normal market closures]")
        for row in report["internal_gap_examples"][:10]:
            print(f"- {row['symbol']} {row['timeframe']}: {row['from']} -> {row['to']} ({row['gap_minutes']} min)")
    print("")
    print(f"Recommendation: {report.get('recommendation')}")


def print_human(report: dict[str, Any]) -> None:
    print(f"SEN05 DP Program status: {str(report.get('status')).upper()}")
    print(f"Generated at: {report.get('generated_at')}")
    print(f"App root    : {report.get('app_root')}")
    print("")
    for check in report.get("checks", []):
        status = str(check.get("status", "")).upper().ljust(5)
        print(f"[{status}] {check.get('name')}: {check.get('message')}")
        detail = check.get("detail")
        if isinstance(detail, dict):
            if check.get("name") == "process_inventory":
                print(f"        count: {detail.get('count', 0)}")
                note = detail.get("note")
                if note:
                    print(f"        note: {note}")
                for proc in detail.get("processes") or []:
                    print(
                        "        - {role} | pid={pid} | started={started}".format(
                            role=proc.get("role") or "DP Program Process",
                            pid=proc.get("pid") or "-",
                            started=_format_dt(proc.get("created_at")),
                        )
                    )
                    print(f"          command: {proc.get('command') or '-'}")
                continue
            for key in ("status", "pid", "pid_alive", "age_seconds", "token_state", "configured", "live_pid", "historical_pid"):
                if key in detail:
                    print(f"        {key}: {detail.get(key)}")


def print_json(report: dict[str, Any], *, indent: int | None = None) -> None:
    print(json.dumps(report, ensure_ascii=True, indent=BACKEND.status_json_indent if indent is None else indent))


def cleanup_old_runtime_files(*, days: int | None = None) -> dict[str, Any]:
    """Clean closed runtime artifacts without touching active logs or spool data."""
    retention_days = LOGGING.retention_days if days is None else max(0, int(days))
    cutoff = time.time() - retention_days * 86400
    active_pids = set(_active_runtime_roles().values())
    deleted: list[str] = []
    kept: list[str] = []
    failed: list[dict[str, str]] = []

    def _belongs_to_active_pid(path: Path) -> bool:
        return any(re.search(rf"(?:^|\.){pid}(?:\.|$)", path.name) for pid in active_pids)

    def _delete(path: Path, *, reason: str) -> bool:
        try:
            path.unlink()
            deleted.append(str(path))
            return True
        except Exception as exc:
            failed.append({"path": str(path), "reason": f"{reason}: {type(exc).__name__}: {exc}"})
            return False

    # Canonical current logs are never cleanup candidates. The sink owns only
    # archive retention and its configured disk budget.
    try:
        from core_engine.util.logkit.sink import cleanup_archives

        archive_result = cleanup_archives()
        deleted.extend(str(path) for path in archive_result.get("deleted", []))
        failed.extend(
            {
                "path": str(item.get("path", LOG_DIR)),
                "reason": f"archive_retention: {item.get('error', 'unknown error')}",
            }
            for item in archive_result.get("failed", [])
        )
    except Exception as exc:
        archive_result = {}
        failed.append(
            {
                "path": str(LOG_DIR),
                "reason": f"archive_retention: {type(exc).__name__}: {exc}",
            }
        )

    candidates: list[Path] = []
    if RUN_DIR.exists():
        for path in RUN_DIR.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(RUN_DIR).as_posix().lower()
            name = path.name.lower()
            if path.suffix.lower() in {".tmp", ".old", ".out"}:
                candidates.append(path)
            elif relative.startswith(("log_sinks/", "notification_status/")):
                candidates.append(path)
            elif relative.startswith("log_emergency/") and name.endswith(".log"):
                candidates.append(path)

    for path in candidates:
        try:
            if _belongs_to_active_pid(path):
                kept.append(str(path))
            elif path.stat().st_mtime < cutoff:
                _delete(path, reason="age_retention")
            else:
                kept.append(str(path))
        except Exception as exc:
            failed.append({"path": str(path), "reason": f"stat: {type(exc).__name__}: {exc}"})
            kept.append(str(path))

    try:
        remaining_logs = [path for path in LOG_DIR.rglob("*") if path.is_file()]
        total_bytes = sum(path.stat().st_size for path in remaining_logs)
    except Exception as exc:
        failed.append({"path": str(LOG_DIR), "reason": f"budget: {type(exc).__name__}: {exc}"})
        total_bytes = None

    result = {
        "generated_at": _utc_now().isoformat(),
        "retention_days": retention_days,
        "active_pids": sorted(active_pids),
        "deleted": deleted,
        "archive_retention": archive_result,
        "failed": failed,
        "kept_count": len(kept),
        "remaining_log_bytes": total_bytes,
        "hard_budget_bytes": LOG_FAIL_TOTAL_BYTES,
    }
    return result
