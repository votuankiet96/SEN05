"""Discord alerting, the system log, service heartbeat/progress state, and
the read-only health check used by the ``check`` CLI command and the
scheduled ``check`` job.

Market-closed suppression primarily uses the real trading schedule + holiday
calendar cTrader itself reports per symbol (synced into
tick.SymbolMap.ScheduleJson/HolidayJson by `symbol-sync --apply`, see
ctrader_client.fetch_remote_schedules). ``SESSION_RULES`` below is now only
a fallback for a symbol with no synced schedule yet — see is_market_closed().
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any

logger_lock = threading.Lock()
_pending_threads: list[threading.Thread] = []
_last_sent: dict[str, float] = {}
_MAX_BYTES, _BACKUP_COUNT = 10_000_000, 5
_COLORS = {"INFO": 3447003, "WARNING": 16776960, "ERROR": 15158332, "CRITICAL": 10038562}

# weekday: Mon=0 .. Sun=6. Minutes are UTC-of-day. Derived from observed
# session boundaries in tick.<SYMBOL> (see docs/ARCHITECTURE.md) -- the
# weekly close for "weekend_gap" symbols lands Friday evening UTC, not
# Saturday: DE40/UK100 stop ~19:49 Friday, J225/US500/US100/US30/GOLD stop
# ~20:49 Friday, all resume ~22:05 Sunday. An earlier version of this table
# checked Saturday for the close boundary, which incorrectly left the whole
# Friday-evening-to-Saturday window flagged as "market should be open",
# firing false stale-tick alerts every weekend.
SESSION_RULES: dict[str, dict[str, Any]] = {
    "FR40": {"kind": "weekday_session", "start_minute": 6 * 60, "end_minute": 20 * 60},
    "HK50": {"kind": "weekday_session", "start_minute": 1 * 60, "end_minute": 19 * 60},
    "SP35": {"kind": "weekday_session", "start_minute": 7 * 60, "end_minute": 18 * 60},
    "DE40": {"kind": "weekend_gap", "fri_close_minute": 19 * 60 + 45, "sun_open_minute": 22 * 60 + 10},
    "UK100": {"kind": "weekend_gap", "fri_close_minute": 19 * 60 + 45, "sun_open_minute": 22 * 60 + 10},
    "J225": {"kind": "weekend_gap", "fri_close_minute": 20 * 60 + 45, "sun_open_minute": 22 * 60 + 10},
    "US500": {"kind": "weekend_gap", "fri_close_minute": 20 * 60 + 45, "sun_open_minute": 22 * 60 + 10},
    "US100": {"kind": "weekend_gap", "fri_close_minute": 20 * 60 + 45, "sun_open_minute": 22 * 60 + 10},
    "US30": {"kind": "weekend_gap", "fri_close_minute": 20 * 60 + 45, "sun_open_minute": 22 * 60 + 10},
    "GOLD": {"kind": "weekend_gap", "fri_close_minute": 20 * 60 + 45, "sun_open_minute": 22 * 60 + 10},
    "BTCUSD": {"kind": "always_open"},
}


def _closed_by_real_schedule(schedule: dict[str, Any], now_utc: datetime) -> bool | None:
    """Evaluate a real cTrader-fetched schedule (see ctrader_client.schedule_from_proto).

    Returns True/False when the schedule can answer, or None when it can't
    (e.g. an unrecognized IANA tz name) -- callers must fall back to
    SESSION_RULES in that case, never assume "open" or "closed" silently.

    cTrader's week seconds start **Sunday 00:00** in the symbol's own
    ``scheduleTimeZone`` (verified against this project's previously
    observed real Friday-close / Sunday-open UTC timestamps -- matched to
    the minute). ``holidayDate`` is a day count since the Unix epoch.
    """
    from datetime import date
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        tz = ZoneInfo(str(schedule["tz"]))
    except (KeyError, ZoneInfoNotFoundError, ValueError):
        return None
    local = now_utc.astimezone(tz)
    sunday_indexed_weekday = (local.weekday() + 1) % 7  # Mon=0..Sun=6 -> Sun=0..Sat=6
    second_of_week = sunday_indexed_weekday * 86400 + local.hour * 3600 + local.minute * 60 + local.second
    in_session = any(start <= second_of_week < end for start, end in schedule.get("intervals", []))
    if not in_session:
        return True

    day_epoch = (local.date() - date(1970, 1, 1)).days
    second_of_day = local.hour * 3600 + local.minute * 60 + local.second
    for h in schedule.get("holidays", []):
        if int(h.get("date_days", -1)) != day_epoch:
            continue
        if int(h.get("start", 0)) <= second_of_day <= int(h.get("end", 86399)):
            return True
    return False


def is_market_closed(symbol: str, now_utc: datetime, schedules: dict[str, dict[str, Any]] | None = None) -> bool:
    """Real cTrader-fetched schedule (synced daily by `symbol-sync --apply`,
    see tick.SymbolMap.ScheduleJson) is the primary source; SESSION_RULES is
    only a fallback for a symbol with no synced schedule yet (first run
    before any sync, or every sync attempt so far failed)."""
    schedule = (schedules or {}).get(symbol.upper())
    if schedule:
        result = _closed_by_real_schedule(schedule, now_utc)
        if result is not None:
            return result

    rule = SESSION_RULES.get(symbol.upper())
    if rule is None or rule["kind"] == "always_open":
        return False
    weekday, minute_of_day = now_utc.weekday(), now_utc.hour * 60 + now_utc.minute
    if rule["kind"] == "weekday_session":
        if weekday >= 5:
            return True
        return not (rule["start_minute"] <= minute_of_day < rule["end_minute"])
    if rule["kind"] == "weekend_gap":
        if weekday == 4 and minute_of_day >= rule["fri_close_minute"]:
            return True
        if weekday == 5:
            return True
        if weekday == 6 and minute_of_day < rule["sun_open_minute"]:
            return True
        return False
    return False


# ---------------------------------------------------------------------------
# system.log
# ---------------------------------------------------------------------------


def _clean(value: Any) -> str:
    return str(value).replace("\r\n", " ").replace("\n", " ").strip()


_RISK_BY_LEVEL = {"INFO": "NONE", "WARNING": "LOW", "ERROR": "MEDIUM", "CRITICAL": "HIGH"}


def _logfmt_value(value: Any) -> str:
    text = _clean(value)
    return f'"{text}"' if not text or " " in text or "=" in text else text


def write_system_event(
    component: str, event: str, detail: str = "", *, level: str = "INFO", pid: int | None = None, **fields: Any
) -> None:
    """Append one structured logfmt line to runtime/logs/tick_engine.log.

    ``{iso8601z} {LEVEL} component={component} event={EVENT} risk={risk} pid={pid} key=value...``
    — the same shape as dp_program_v3's log files, so both are equally
    greppable by component/event/pid and never mixed formats in one file.
    Every subprocess (job) and the supervisor itself write to this same
    file through this one function; there is no second logging path.

    No cross-process file lock, deliberately, matching dp_program_v3: a
    single ``open(path, "a").write(line)`` call is one atomic append at the
    OS level on both NTFS and POSIX, which is all a one-line-per-call writer
    needs — a lock file would be the only extra file this module ever
    created, and every writer here does at most one such write per call.
    """
    try:
        from src.configuration import LOG_FILE, ensure_runtime_dirs

        ensure_runtime_dirs()
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        level_up = str(level or "INFO").upper()
        parts = [
            f"component={_logfmt_value(component)}",
            f"event={_logfmt_value(event)}",
            f"risk={_RISK_BY_LEVEL.get(level_up, 'NONE')}",
            f"pid={pid if pid is not None else os.getpid()}",
        ]
        for key, value in fields.items():
            parts.append(f"{key}={_logfmt_value(value)}")
        if detail:
            parts.append(f"detail={_logfmt_value(detail)}")
        line = f"{stamp} {level_up} " + " ".join(parts)
        with logger_lock:
            try:
                if LOG_FILE.stat().st_size >= _MAX_BYTES:
                    LOG_FILE.with_name(f"{LOG_FILE.name}.{_BACKUP_COUNT}").unlink(missing_ok=True)
                    for i in range(_BACKUP_COUNT - 1, 0, -1):
                        src = LOG_FILE.with_name(f"{LOG_FILE.name}.{i}")
                        if src.exists():
                            os.replace(src, LOG_FILE.with_name(f"{LOG_FILE.name}.{i + 1}"))
                    os.replace(LOG_FILE, LOG_FILE.with_name(f"{LOG_FILE.name}.1"))
            except FileNotFoundError:
                pass
            with LOG_FILE.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:
        return


# ---------------------------------------------------------------------------
# Discord webhook
# ---------------------------------------------------------------------------


def _strip_html(message: str) -> str:
    text = unescape(str(message)).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"</?(b|strong)>", "**", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(i|em)>", "*", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def _detail_block(items: list[tuple[str, object]] | None) -> str:
    if not items:
        return ""
    width = min(18, max((len(k) for k, _v in items if k), default=0))
    lines = [f"{k.ljust(width)} : {v}" if k else str(v) for k, v in items]
    return "```text\n" + "\n".join(lines) + "\n```"


def _post_webhook(payload: dict) -> None:
    import requests

    from src.configuration import load_settings

    webhook_url = load_settings().discord_webhook_url
    if not webhook_url:
        return
    for attempt in range(3):
        try:
            resp = requests.post(webhook_url, json=payload, timeout=10, verify=True)
            if resp.status_code in (200, 204):
                write_system_event("discord", "DISCORD_REPORT_SENT", http_status=resp.status_code, attempt=attempt + 1)
                return
            if resp.status_code == 429:
                time.sleep(min(5.0, 30))
            elif attempt < 2:
                time.sleep(3)
        except Exception:
            if attempt < 2:
                time.sleep(3)
    write_system_event("discord", "DISCORD_REPORT_FAILED", "webhook send failed after 3 attempts", level="ERROR")


def notify(
    level: str, title: str, *, conclusion: str, action: str | None = None,
    details: list[tuple[str, object]] | None = None, technical: list[tuple[str, object]] | None = None,
    throttle_key: str | None = None, throttle_seconds: int = 300,
) -> None:
    """Send a throttled operator-facing Discord embed. Never raises into ingest code."""
    from src.configuration import CACHE_DIR

    if throttle_key:
        now = time.monotonic()
        if now - _last_sent.get(throttle_key, 0.0) < throttle_seconds:
            return
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            throttle_path = CACHE_DIR / f"tick_notify_throttle_{hashlib.sha1(throttle_key.encode()).hexdigest()}.txt"
            wall_now = time.time()
            if throttle_path.exists():
                last_wall = float(throttle_path.read_text(encoding="ascii").strip() or "0")
                if wall_now - last_wall < throttle_seconds:
                    _last_sent[throttle_key] = now
                    return
            throttle_path.write_text(str(wall_now), encoding="ascii")
        except Exception:
            pass
        _last_sent[throttle_key] = now

    body = [f"**{title}**", "", "**Summary**", _strip_html(conclusion)]
    if action:
        body += ["", "**Action**", _strip_html(action)]
    if _detail_block(details):
        body += ["", "**Details**", _detail_block(details)]
    if _detail_block(technical):
        body += ["", "**Technical**", _detail_block(technical)]
    text = "\n".join(body).strip()
    label = {"INFO": "Info", "WARNING": "Warning", "ERROR": "Error", "CRITICAL": "Critical"}.get(level, level)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    description = f"{text}\n\n*{timestamp}*"[:4096]
    payload = {"embeds": [{"title": f"Tick Engine — {label}", "description": description, "color": _COLORS.get(level, _COLORS["INFO"])}]}

    def _send() -> None:
        _post_webhook(payload)

    t = threading.Thread(target=_send, daemon=True)
    t.start()
    global _pending_threads
    _pending_threads = [x for x in _pending_threads if x.is_alive()] + [t]


def flush_notifications(timeout: float = 12.0) -> None:
    global _pending_threads
    alive = []
    for t in _pending_threads:
        t.join(timeout=timeout)
        if t.is_alive():
            alive.append(t)
    _pending_threads = alive


# ---------------------------------------------------------------------------
# Service heartbeat + backfill progress state
# ---------------------------------------------------------------------------


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


def write_service_heartbeat(payload: dict[str, Any]) -> None:
    from src.configuration import SERVICE_HEARTBEAT

    data = {**payload, "updated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "process_id": os.getpid()}
    _write_json(SERVICE_HEARTBEAT, data)


def read_service_heartbeat() -> dict[str, Any] | None:
    from src.configuration import SERVICE_HEARTBEAT

    try:
        return json.loads(SERVICE_HEARTBEAT.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        return {"error": str(exc)}


def scan_backfill_progress(*, prefix: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    from src.configuration import RUN_DIR

    progress_dir = RUN_DIR / "backfill_batches"
    if not progress_dir.exists():
        return []
    items: list[dict[str, Any]] = []
    paths = sorted(progress_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths:
        if prefix and not path.name.startswith(prefix):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        items.append({"name": path.name, "status": payload.get("status"), "updated_at_utc": payload.get("updated_at_utc")})
        if len(items) >= limit:
            break
    return items


def mark_stale_backfill_progress(max_age_seconds: int) -> int:
    """Mark local RUNNING batch progress files stale after the owner process died."""
    from src.configuration import RUN_DIR
    from src.spool import is_pid_alive

    progress_dir = RUN_DIR / "backfill_batches"
    if not progress_dir.exists():
        return 0
    now, updated = datetime.now(timezone.utc), 0
    for path in progress_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if payload.get("status") != "RUNNING":
            continue
        updated_at = payload.get("updated_at_utc")
        try:
            age = (now - datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))).total_seconds()
        except Exception:
            continue
        if age < max_age_seconds:
            continue
        pid = int(payload.get("process_id") or 0)
        if pid and is_pid_alive(pid):
            continue
        payload["status"] = "STALE"
        payload["stale_detected_at_utc"] = now.isoformat()
        _write_json(path, payload)
        updated += 1
    return updated


def prune_backfill_progress(max_age_seconds: int) -> int:
    """Delete completed/stale batch progress files older than ``max_age_seconds``.

    These are diagnostic artifacts only -- the durable audit trail is
    tick.IngestRun in SQL. Without this, runtime/run/backfill_batches/
    grows one file per scheduled backfill run forever (never cleaned up).
    """
    from src.configuration import RUN_DIR

    progress_dir = RUN_DIR / "backfill_batches"
    if not progress_dir.exists():
        return 0
    now, deleted = datetime.now(timezone.utc), 0
    for path in progress_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if payload.get("status") == "RUNNING":
            continue
        updated_at = payload.get("updated_at_utc")
        try:
            age = (now - datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))).total_seconds()
        except Exception:
            continue
        if age < max_age_seconds:
            continue
        try:
            path.unlink()
            deleted += 1
        except OSError:
            continue
    return deleted


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@dataclass
class TickCheckFinding:
    severity: str
    code: str
    message: str


@dataclass
class TickCheckReport:
    status: str
    generated_at_utc: str
    findings: list[TickCheckFinding] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def to_text(self) -> str:
        lines = [f"Status                      {self.status}", f"Generated UTC               {self.generated_at_utc}"]
        if not self.findings:
            lines.append("  No issue found.")
        for f_ in self.findings:
            lines.append(f"  {f_.severity:<7} {f_.code:<28} {f_.message}")
        return "\n".join(lines)


def run_tick_check(settings: Any, store: Any, *, stale_seconds: int | None = None) -> TickCheckReport:
    """Read-only freshness + runtime status check. Never writes tick data."""
    from src.spool import TickSpool, is_pid_alive

    now = datetime.now(timezone.utc)
    findings: list[TickCheckFinding] = []
    stats = store.tick_row_stats_by_symbol()
    try:
        schedules = store.load_symbol_schedules()
    except Exception as exc:
        write_system_event("notify", "SCHEDULE_LOAD_FAILED", str(exc), level="WARNING")
        schedules = {}
    try:
        last_attempts = store.load_last_attempt_times()
    except Exception as exc:
        write_system_event("notify", "LAST_ATTEMPT_LOAD_FAILED", str(exc), level="WARNING")
        last_attempts = {}
    data: dict[str, Any] = {"symbols": {}}
    default_stale = int(stale_seconds or settings.check_stale_seconds)

    for target in store.targets.values():
        symbol = target.local_symbol
        item = stats.get(symbol, {})
        last_tick = item.get("last_tick_utc")
        age_seconds = None
        if last_tick is not None:
            ts = last_tick if last_tick.tzinfo else last_tick.replace(tzinfo=timezone.utc)
            age_seconds = int((now - ts).total_seconds())
        last_attempt = last_attempts.get(symbol)
        attempt_age_seconds = int((now - last_attempt).total_seconds()) if last_attempt is not None else None
        data["symbols"][symbol] = {"rows": item.get("rows", 0), "last_tick_utc": str(last_tick) if last_tick else None,
                                    "age_seconds": age_seconds, "last_attempt_age_seconds": attempt_age_seconds}
        if item.get("rows", 0) == 0:
            findings.append(TickCheckFinding("WARNING", "no_data", f"{symbol}: no tick rows yet"))
            continue
        # Prefer "how long since a fetch attempt for this symbol last
        # completed without error" over raw tick age: a symbol can be
        # legitimately tick-idle for a long stretch (evening cTrader
        # materialization lag, just-reopened session, quiet market) while
        # fetches keep succeeding cleanly -- that is not a fault. Only fall
        # back to tick age when no attempt has been recorded yet (e.g. right
        # after this tracking was added, before the first post-deploy fetch).
        stale_basis_seconds = attempt_age_seconds if attempt_age_seconds is not None else age_seconds
        if stale_basis_seconds is not None and stale_basis_seconds > default_stale and not is_market_closed(symbol, now, schedules):
            basis_label = "last successful fetch" if attempt_age_seconds is not None else "last tick"
            findings.append(
                TickCheckFinding("ERROR", "stale_historical_tick", f"{symbol}: {basis_label} {stale_basis_seconds}s old (market should be open)")
            )

    spool = TickSpool(settings.spool_path)
    spool_count, quarantine_count = spool.count(), spool.quarantine_count()
    data["spool"] = {"count": spool_count, "quarantine_count": quarantine_count}
    if spool_count > 0:
        findings.append(TickCheckFinding("WARNING", "spool_backlog", f"{spool_count} tick(s) waiting in local spool"))
    if quarantine_count > 0:
        findings.append(TickCheckFinding("WARNING", "spool_quarantine", f"{quarantine_count} malformed spool row(s) quarantined"))

    heartbeat = read_service_heartbeat()
    if heartbeat:
        try:
            hb_age = (now - datetime.fromisoformat(str(heartbeat["updated_at_utc"]).replace("Z", "+00:00"))).total_seconds()
        except Exception:
            hb_age = None
        pid = heartbeat.get("service_pid")
        data["runtime"] = {"service_pid": pid, "heartbeat_age_seconds": hb_age,
                            "service_alive": bool(pid and is_pid_alive(int(pid)))}
        if pid and not is_pid_alive(int(pid)):
            findings.append(TickCheckFinding("ERROR", "service_down", f"heartbeat file exists but PID {pid} is not running"))
    else:
        data["runtime"] = {"service_pid": None, "service_alive": False}
        findings.append(TickCheckFinding("WARNING", "service_not_running", "no service heartbeat file found"))

    status = "OK"
    if any(f.severity == "ERROR" for f in findings):
        status = "ERROR"
    elif findings:
        status = "WARNING"
    return TickCheckReport(status=status, generated_at_utc=now.isoformat(), findings=findings, data=data)


# ---------------------------------------------------------------------------
# One-shot recovery notification (avoid repeat paging while an incident lasts)
# ---------------------------------------------------------------------------


def update_incident_state(status: str, generated_at_utc: str, active: bool) -> dict[str, Any] | None:
    """Persist actionable health state; return a one-shot recovery payload, or None.

    Pure state transition, kept separate from the Discord call below so it is
    testable without a network mock: returns a payload exactly once, on the
    active -> inactive transition, and None every other call.
    """
    from src.configuration import INCIDENT_STATE

    previous: dict[str, Any] = {}
    try:
        if INCIDENT_STATE.exists():
            previous = json.loads(INCIDENT_STATE.read_text(encoding="utf-8"))
    except Exception:
        previous = {}
    payload = {
        "active": active, "status": status, "updated_at_utc": generated_at_utc,
        "active_since_utc": previous.get("active_since_utc") if active and previous.get("active")
        else generated_at_utc if active else None,
    }
    try:
        INCIDENT_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = INCIDENT_STATE.with_name(f".{INCIDENT_STATE.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, INCIDENT_STATE)
    except Exception:
        pass

    if not previous.get("active") or active:
        return None
    return {"active_since_utc": previous.get("active_since_utc") or "unknown", "recovered_at_utc": generated_at_utc}


def notify_daily_summary(report: TickCheckReport) -> None:
    """Send an unconditional daily status ping to Discord, regardless of findings.

    ``notify_check_result`` below only pages on an active/recovered incident,
    so a healthy service would otherwise never post anything — an operator
    watching Discord alone couldn't distinguish "quiet because healthy" from
    "quiet because the service died". This is the once-a-day heartbeat that
    closes that gap.
    """
    symbols = report.data.get("symbols", {})
    total_rows = sum(int(v.get("rows") or 0) for v in symbols.values())
    errors = sum(1 for f in report.findings if f.severity == "ERROR")
    warnings = sum(1 for f in report.findings if f.severity == "WARNING")
    level = "ERROR" if report.status == "ERROR" else "WARNING" if report.status == "WARNING" else "INFO"
    notify(
        level, "Tick engine daily summary",
        conclusion=f"Daily status: {report.status}.",
        details=[("Symbols tracked", len(symbols)), ("Total rows", f"{total_rows:,}"),
                  ("Warnings", warnings), ("Errors", errors)],
        throttle_key="tick-daily-summary", throttle_seconds=3600,
    )


def notify_check_result(report: TickCheckReport) -> None:
    """Notify Discord for actionable findings only, and once on recovery."""
    actionable = [f for f in report.findings if f.severity in {"WARNING", "ERROR"}]
    active = bool(actionable)
    recovery = update_incident_state(report.status, report.generated_at_utc, active)

    if active:
        level = "ERROR" if report.status == "ERROR" else "WARNING"
        notify(
            level, f"Tick data check {report.status}",
            conclusion="The health check found condition(s) that need operator attention.",
            action="Review runtime/logs/tick_engine.log for details.",
            details=[(f.code, f.message) for f in actionable[:10]],
            throttle_key=f"tick-check-{level.lower()}", throttle_seconds=300 if level == "ERROR" else 3600,
        )
        return
    if recovery is not None:
        notify(
            "INFO", "Tick data recovered",
            conclusion="The latest health check no longer has an actionable incident.",
            details=[("Incident started", recovery["active_since_utc"]), ("Recovered at", recovery["recovered_at_utc"])],
            throttle_key="tick-check-recovered", throttle_seconds=60,
        )
