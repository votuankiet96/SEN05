"""Operator-facing Discord notification policy for tick health reports.

Health checks should stay strict on the terminal. Discord should be quieter:
only conditions that need operator attention should leave the machine.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from tick_engine.reporting.system_log import write_system_event

_STALE_CODES = {"missing_historical_tick", "stale_historical_tick"}
_INFO_CODES = {"stale_progress_repaired", "stale_runs_repaired"}
_BTC_SYMBOL = "BTCUSD"


@dataclass(frozen=True)
class TickCheckNotificationDecision:
    notify: bool
    level: str
    title: str
    conclusion: str
    action: str | None
    details: list[tuple[str, object]]
    technical: list[tuple[str, object]]
    throttle_key: str
    throttle_seconds: int
    suppressed: list[tuple[str, str]]


def build_tick_check_notification(report: Any) -> TickCheckNotificationDecision:
    """Return the Discord decision for a TickCheckReport-like object."""
    findings = list(getattr(report, "findings", []) or [])
    status = str(getattr(report, "status", "OK") or "OK").upper()
    data = getattr(report, "data", {}) or {}
    now = _report_generated_at(report)

    if not findings or status == "OK":
        return _decision(False, "INFO", status, [], [])

    actionable: list[Any] = []
    suppressed: list[tuple[str, str]] = []
    for finding in findings:
        code = str(getattr(finding, "code", "") or "")
        severity = str(getattr(finding, "severity", "") or "").upper()
        if severity == "INFO" or code in _INFO_CODES:
            suppressed.append((code or severity, "informational health maintenance"))
            continue
        if severity == "ERROR":
            actionable.append(finding)
            continue
        if code in _STALE_CODES:
            symbol = _symbol_from_finding(finding)
            reason = _stale_suppression_reason(symbol, data, now)
            if reason:
                suppressed.append((symbol or code, reason))
            else:
                actionable.append(finding)
            continue
        actionable.append(finding)

    if not actionable:
        return _decision(False, "WARNING", status, [], suppressed)

    level = "ERROR" if any(str(getattr(item, "severity", "")).upper() == "ERROR" for item in actionable) else "WARNING"
    return _decision(True, level, status, actionable, suppressed)


def write_tick_check_notification_policy_event(decision: TickCheckNotificationDecision) -> None:
    """Record why Discord was quiet or partially filtered."""
    if not decision.suppressed:
        return
    detail = _suppressed_detail(decision.suppressed)
    item = "suppressed" if not decision.notify else "filtered"
    write_system_event("Discord Policy", item, detail)


def update_tick_check_incident_state(
    report: Any,
    decision: TickCheckNotificationDecision,
) -> TickCheckNotificationDecision | None:
    """Persist actionable health state and return a one-shot recovery notification."""
    from tick_engine.settings import CACHE_DIR

    path = CACHE_DIR / "tick_health_incident_state.json"
    previous: dict[str, Any] = {}
    try:
        if path.exists():
            previous = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        previous = {}

    now = _report_generated_at(report)
    active = bool(decision.notify)
    payload = {
        "active": active,
        "status": str(getattr(report, "status", "OK") or "OK").upper(),
        "updated_at_utc": now.isoformat(),
        "active_since_utc": (
            previous.get("active_since_utc")
            if active and previous.get("active")
            else now.isoformat() if active else None
        ),
    }
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    except Exception:
        write_system_event("Discord Policy", "state write failed", str(path), level="WARNING")

    if not previous.get("active") or active:
        return None
    return TickCheckNotificationDecision(
        notify=True,
        level="INFO",
        title="Tick data recovered",
        conclusion="The latest health check no longer has an actionable incident.",
        action=None,
        details=[
            ("Current status", payload["status"]),
            ("Incident started", previous.get("active_since_utc") or "unknown"),
            ("Recovered at", now.isoformat()),
        ],
        technical=[],
        throttle_key="tick-check-recovered",
        throttle_seconds=60,
        suppressed=[],
    )


def build_tick_check_summary_notification(report: Any) -> TickCheckNotificationDecision:
    """Build a compact daily OK summary without sending per-check success noise."""
    data = getattr(report, "data", {}) or {}
    health = list(data.get("ingest_health") or [])
    ages = [
        int(item["historical_tick_age_seconds"])
        for item in health
        if item.get("historical_tick_age_seconds") is not None
    ]
    spool = data.get("spool") or {}
    status = str(getattr(report, "status", "OK") or "OK").upper()
    return TickCheckNotificationDecision(
        notify=True,
        level="INFO",
        title="Tick daily health summary",
        conclusion="The scheduled daily health review completed.",
        action=None,
        details=[
            ("Status", status),
            ("Symbols checked", len(health)),
            ("Worst tick age", f"{max(ages)}s" if ages else "no data"),
            ("Spool backlog", int(spool.get("count") or 0)),
            ("Spool quarantine", int(spool.get("quarantine_count") or 0)),
        ],
        technical=[],
        throttle_key=f"tick-daily-health-summary-{_report_generated_at(report).date().isoformat()}",
        throttle_seconds=20 * 60 * 60,
        suppressed=[],
    )


def _decision(
    notify: bool,
    level: str,
    status: str,
    actionable: list[Any],
    suppressed: list[tuple[str, str]],
) -> TickCheckNotificationDecision:
    if level == "ERROR":
        conclusion = "The checker found a problem that needs operator attention."
        action = "Run mode 1 in DP6 and review system.log / operation.log."
        throttle_seconds = 300
    elif notify:
        conclusion = "The checker found warning(s) that are not explained by normal market closure."
        action = "Monitor the next checks. If it repeats, inspect mode 1 and the tick logs."
        throttle_seconds = 3600
    else:
        conclusion = "Health warnings were explained by market session timing or active maintenance."
        action = None
        throttle_seconds = 3600

    details: list[tuple[str, object]] = [
        ("Status", status),
        ("Actionable findings", len(actionable)),
    ]
    if suppressed:
        details.append(("Suppressed findings", len(suppressed)))

    technical = [
        (
            f"{str(getattr(item, 'severity', '')).upper()} {str(getattr(item, 'code', ''))}",
            str(getattr(item, "message", "")),
        )
        for item in actionable[:10]
    ]
    return TickCheckNotificationDecision(
        notify=notify,
        level=level,
        title=f"Tick data check {status}",
        conclusion=conclusion,
        action=action,
        details=details,
        technical=technical,
        throttle_key=f"tick-check-{level.lower()}-actionable",
        throttle_seconds=throttle_seconds,
        suppressed=suppressed,
    )


def _symbol_from_finding(finding: Any) -> str | None:
    message = str(getattr(finding, "message", "") or "")
    match = re.match(r"\s*([A-Z0-9]+)\s*:", message)
    return match.group(1).upper() if match else None


def _health_item(data: dict[str, Any], symbol: str | None) -> dict[str, Any]:
    if not symbol:
        return {}
    for item in data.get("ingest_health", []) or []:
        if str(item.get("symbol") or "").upper() == symbol:
            return item
    return {}


def _stale_suppression_reason(
    symbol: str | None,
    data: dict[str, Any],
    now: datetime,
) -> str | None:
    item = _health_item(data, symbol)
    age = _int_or_none(item.get("historical_tick_age_seconds"))

    if symbol and _is_weekend_closed(symbol, now):
        return "outside weekend trading session"
    state = str(item.get("activity_state") or "").upper()
    if state and state != "EXPECTED_ACTIVE":
        return "outside learned trading session"
    if symbol and _near_session_boundary(symbol, now, item):
        return "near learned session open/close"
    if _data_job_running(data) and _suppress_while_data_job_running(age):
        return "scheduled data job is running"
    if age is not None and age < _discord_stale_min_seconds():
        return "freshness lag is below Discord alert threshold"
    return None


def _report_generated_at(report: Any) -> datetime:
    raw = getattr(report, "generated_at_utc", None)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _is_weekend_closed(symbol: str, now: datetime) -> bool:
    return symbol.upper() != _BTC_SYMBOL and now.weekday() >= 5


def _near_session_boundary(symbol: str, now: datetime, item: dict[str, Any]) -> bool:
    try:
        from tick_engine.settings import TICK_DISCORD_SESSION_GRACE_BUCKETS
        from tick_engine.utils_support.health import (
            ACTIVITY_STATE_ACTIVE,
            classify_activity_expectation,
            load_tick_activity_profile,
        )

        grace_buckets = max(0, int(TICK_DISCORD_SESSION_GRACE_BUCKETS))
        if grace_buckets <= 0:
            return False
        bucket_minutes = int(item.get("activity_bucket_minutes") or 15)
        profile = load_tick_activity_profile()
        now_state = classify_activity_expectation(profile, symbol, now).get("state")
        if now_state != ACTIVITY_STATE_ACTIVE:
            return True
        for step in range(1, grace_buckets + 1):
            delta = timedelta(minutes=bucket_minutes * step)
            before = classify_activity_expectation(profile, symbol, now - delta).get("state")
            after = classify_activity_expectation(profile, symbol, now + delta).get("state")
            if before != ACTIVITY_STATE_ACTIVE or after != ACTIVITY_STATE_ACTIVE:
                return True
    except Exception:
        return False
    return False


def _data_job_running(data: dict[str, Any]) -> bool:
    lock = data.get("history_lock", {}) or {}
    if bool(lock.get("active")):
        return True
    for item in data.get("scheduled_backfill_progress", []) or []:
        if item.get("status") == "RUNNING" and not item.get("stale_progress"):
            return True
    return False


def _suppress_while_data_job_running(age_seconds: int | None) -> bool:
    try:
        from tick_engine.settings import TICK_DISCORD_SUPPRESS_WHILE_DATA_JOB_RUNNING

        if not bool(TICK_DISCORD_SUPPRESS_WHILE_DATA_JOB_RUNNING):
            return False
    except Exception:
        pass
    if age_seconds is None:
        return True
    return age_seconds < max(7200, _discord_stale_min_seconds() * 2)


def _discord_stale_min_seconds() -> int:
    try:
        from tick_engine.settings import TICK_DISCORD_STALE_MIN_SECONDS

        return max(0, int(TICK_DISCORD_STALE_MIN_SECONDS))
    except Exception:
        return 3600


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _suppressed_detail(items: list[tuple[str, str]]) -> str:
    counts: dict[str, int] = {}
    symbols: list[str] = []
    for symbol, reason in items:
        counts[reason] = counts.get(reason, 0) + 1
        if symbol:
            symbols.append(str(symbol))
    reason_text = ", ".join(f"{reason}={count}" for reason, count in sorted(counts.items()))
    symbol_text = ",".join(sorted(set(symbols))[:12])
    if len(set(symbols)) > 12:
        symbol_text += ",..."
    return f"reasons: {reason_text} | symbols={symbol_text or '-'}"
