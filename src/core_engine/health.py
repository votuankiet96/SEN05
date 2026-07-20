"""Operator-facing health and status checks for the DP Program terminal app.

This module is deliberately read-oriented. It must never expose credentials and
should avoid changing runtime state except for creating required runtime
directories during a readiness check.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core_engine.tradingview import auth as tv_auth
from core_engine.settings import (
    APP_ROOT,
    BACKEND,
    BACKEND_STATE,
    CACHE_DIR,
    ENV_FILE,
    HISTORICAL,
    HISTORICAL_SUMMARY_LOG,
    LOG_DIR,
    RUN_DIR,
    SPOOL_DIR,
    SYMBOLS,
    TF_DISPLAY_ORDER,
    WS_LIVE_LOG,
    WS_LIVE_STATE,
    ensure_runtime_dirs,
)
from core_engine.live.runtime_support import playwright_browser_status
from core_engine.coordination.locks import (
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


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _age_seconds(value: Any) -> float | None:
    parsed = _parse_time(value)
    if parsed is None:
        return None
    return max(0.0, (_utc_now() - parsed).total_seconds())


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


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


def _safe_path_status(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "is_dir": path.is_dir(),
    }


def _runtime_check() -> Check:
    try:
        ensure_runtime_dirs()
        writable: dict[str, bool] = {}
        for path in (LOG_DIR, CACHE_DIR, RUN_DIR, SPOOL_DIR):
            probe = path / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            writable[path.name] = True
        return Check(
            "runtime",
            "ok",
            "Runtime folders are present and writable.",
            {
                "app_root": str(APP_ROOT),
                "env_file": str(ENV_FILE),
                "writable": writable,
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
    return Check(
        "config",
        "ok",
        "Config file and instrument list are available.",
        {"symbols": len(SYMBOLS), "env_file": str(ENV_FILE)},
    )


def _database_check() -> Check:
    try:
        from core_engine.warehouse.connection import get_connection

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


def _discord_check() -> Check:
    from core_engine.settings import NOTIFICATION

    if NOTIFICATION.discord_webhook_url:
        return Check("discord", "ok", "Discord webhook is configured.", {"configured": True})
    return Check("discord", "warn", "Discord webhook is not configured.", {"configured": False})


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
                host = str(meta.get("host") or "").strip().lower()
                local_hosts = {
                    str(os.environ.get("COMPUTERNAME") or "").strip().lower(),
                    str(os.uname().nodename if hasattr(os, "uname") else "").strip().lower(),
                }
                local_hosts.discard("")
                same_host = bool(host and host in local_hosts)
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
    stale_after = BACKEND.live_stale_minutes * 60
    detail = {
        "path": str(WS_LIVE_STATE),
        "status": state.get("status"),
        "pid": state.get("pid"),
        "updated_at": state.get("updated_at"),
        "age_seconds": round(age, 1) if age is not None else None,
        "batches_run": state.get("batches_run"),
        "accepted_bars": state.get("accepted_bars"),
        "fact_inserted": state.get("fact_inserted"),
        "errors": state.get("errors"),
    }
    active_status = str(state.get("status") or "").lower() in {"starting", "running", "waiting"}
    pid, alive = _pid_state(state.get("pid"))
    detail["pid_alive"] = alive
    if active_status and pid and alive is False:
        return Check(
            "live_state",
            "warn",
            "Live state is stale: it says live is active, but that PID is not running.",
            detail,
        )
    if age is None:
        return Check("live_state", "warn", "Live state timestamp is not readable.", detail)
    if active_status and age > stale_after:
        return Check("live_state", "fail", "Live state heartbeat is stale.", detail)
    return Check("live_state", "ok", "Live state is readable.", detail)


def _historical_state_check() -> Check:
    summary_path = HISTORICAL_SUMMARY_LOG
    summary = _read_last_jsonl(summary_path)
    if not summary:
        return Check(
            "historical_state",
            "warn",
            "No historical run summary has been written yet.",
            {"path": str(summary_path)},
        )
    age = _age_seconds(summary.get("ts"))
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


def collect_health(*, deep_auth: bool = False, include_database: bool = True) -> dict[str, Any]:
    checks = [
        _runtime_check(),
        _config_check(),
        _process_inventory_check(),
        _chromium_check(),
        _auth_check(deep=deep_auth),
        _discord_check(),
        _backend_state_check(),
        _live_state_check(),
        _historical_state_check(),
    ]
    if include_database:
        checks.insert(2, _database_check())
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


def collect_data_health(*, lookback_days: int | None = None) -> dict[str, Any]:
    """Read-only warehouse coverage summary for operators.

    This is intentionally separate from `doctor`: it answers whether the OHLCV
    data itself looks current enough to run production, not whether the machine
    dependencies are installed.
    """
    from core_engine.warehouse.repository import get_internal_gaps, get_latest_bars
    from core_engine.warehouse.connection import get_connection
    from core_engine.historical.runtime_support import find_hole_pairs, find_stale_pairs

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
    repair_items = list(stale)
    repair_items.extend(
        find_hole_pairs(
            repair_items,
            logging.getLogger("data_health"),
            lookback_days=lookback,
            symbols=SYMBOLS,
            tf_filter=set(TF_DISPLAY_ORDER),
        )
    )

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

    worst_stale = []
    repair_items.sort(key=lambda item: (item.get("reason") != "MISS", -float(item.get("gap_hours") or 0)))
    for item in repair_items[:15]:
        sym = item.get("sym") or {}
        last_bar = item.get("last_bar")
        worst_stale.append(
            {
                "symbol": sym.get("tv_symbol", "-"),
                "timeframe": item.get("tf_code", "-"),
                "reason": item.get("reason", "-"),
                "last_bar": _format_dt(last_bar),
                "gap": _format_age(float(item.get("gap_hours") or 0) * 3600),
                "suggested_pull_bars": item.get("n_bars"),
            }
        )

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
            "raw_internal_gap_pairs": len(raw_gaps),
            "raw_internal_gap_windows": gap_windows,
        },
        "newest_bar": pair_row(newest_key, latest_configured.get(newest_key)) if newest_key else None,
        "oldest_latest_bar": pair_row(oldest_key, latest_configured.get(oldest_key)) if oldest_key else None,
        "missing_examples": [pair_row(key) for key in missing_keys[:15]],
        "repair_examples": worst_stale,
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
    retention_days = BACKEND.log_retention_days if days is None else max(0, int(days))
    cutoff = time.time() - retention_days * 86400
    candidates = []
    for root in (LOG_DIR, RUN_DIR, SPOOL_DIR):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() in {".log", ".jsonl", ".tmp", ".old"} or root == RUN_DIR:
                candidates.append(path)
    deleted: list[str] = []
    kept: list[str] = []
    for path in candidates:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                deleted.append(str(path))
            else:
                kept.append(str(path))
        except Exception:
            kept.append(str(path))
    return {"retention_days": retention_days, "deleted": deleted, "kept_count": len(kept)}
