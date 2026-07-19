"""Read-only health checks for the cTrader tick provider."""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tick_engine.data_storage.spool import TickSpool
from tick_engine.data_storage.store_sql import TickSqlStore, qualified_tick_table, quote_ident
from tick_engine.utils_support.service_state import scan_backfill_progress, service_heartbeat_status
from tick_engine.utils_support.runtime import TickRuntimeSettings

ACTIVITY_STATE_ACTIVE = "EXPECTED_ACTIVE"
ACTIVITY_STATE_QUIET = "EXPECTED_QUIET"
ACTIVITY_STATE_TRANSITION = "TRANSITION_GRACE"
ACTIVITY_STATE_UNKNOWN = "UNKNOWN"
DEFAULT_ACTIVITY_LOOKBACK_DAYS = 30
DEFAULT_ACTIVITY_BUCKET_MINUTES = 15
DEFAULT_ACTIVITY_ACTIVE_MIN_RATIO = 0.25
DEFAULT_ACTIVITY_MIN_ACTIVE_TICKS = 1
DEFAULT_ACTIVITY_MAX_AGE_DAYS = 7


def _activity_profile_path() -> Path:
    from tick_engine.settings import CACHE_DIR
    return CACHE_DIR / "tick_activity_profile.json"


def _pid_file_status(path: Path) -> dict[str, object]:
    from tick_engine.utils_support.proc_utils import is_pid_alive

    try:
        exists = path.exists()
    except OSError as exc:
        return {"exists": None, "pid": None, "alive": False, "path": str(path), "error": str(exc)}
    if not exists:
        return {"exists": False, "pid": None, "alive": False, "path": str(path)}
    try:
        raw = path.read_text(encoding="utf-8").strip()
        pid = int(raw) if raw.isdigit() else None
    except OSError as exc:
        return {"exists": True, "pid": None, "alive": False, "path": str(path), "error": str(exc)}
    except Exception:
        pid = None
    return {"exists": True, "pid": pid, "alive": is_pid_alive(pid or 0), "path": str(path)}


def _supervisor_pid_status() -> dict[str, object]:
    from tick_engine.settings import SUPERVISOR_PID

    return _pid_file_status(SUPERVISOR_PID)


def _runtime_cancel_files() -> list[dict[str, object]]:
    from tick_engine.settings import RUN_DIR

    cancel_dir = RUN_DIR / "cancel"
    if not cancel_dir.exists():
        return []
    results: list[dict[str, object]] = []
    for path in sorted(cancel_dir.glob("*.cancel")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            payload = {}
        results.append(
            {
                "name": path.name,
                "path": str(path),
                "requested_at_utc": payload.get("requested_at_utc"),
                "reason": payload.get("reason"),
            }
        )
    return results


def _manual_backfill_progress(limit: int = 5) -> list[dict[str, object]]:
    return scan_backfill_progress(prefix="manual_backfill_", limit=limit)


def _apply_heartbeat_runtime_status(supervisor: dict[str, object], heartbeat: dict[str, object]) -> None:
    if supervisor.get("alive"):
        return
    if heartbeat.get("status") != "RUNNING" or heartbeat.get("stale"):
        return
    hb_host = str(heartbeat.get("host") or "")
    if not hb_host:
        return
    import socket

    local_host = socket.gethostname()
    if hb_host.lower() == local_host.lower():
        return
    supervisor["alive"] = True
    supervisor["remote"] = True
    supervisor["host"] = hb_host
    supervisor["pid"] = heartbeat.get("service_pid") or supervisor.get("pid")
    supervisor["process_state"] = "remote-heartbeat"


@dataclass(frozen=True)
class TickCheckFinding:
    severity: str
    code: str
    message: str


@dataclass(frozen=True)
class TickCheckReport:
    status: str
    generated_at_utc: str
    findings: list[TickCheckFinding]
    data: dict[str, Any]

    def to_text(self) -> str:
        W = 26
        lines = [
            f"{'Status':<{W}} {self.status}",
            f"{'Generated UTC':<{W}} {self.generated_at_utc}",
            "",
        ]
        if self.findings:
            lines.append("Findings:")
            for f in self.findings:
                lines.append(f"  [{f.severity}]  {f.message}")
        else:
            lines.append("  No issue found.")
        lines.append("")
        spool = self.data.get("spool", {})
        supervisor = self.data.get("supervisor", {})
        heartbeat = self.data.get("service_heartbeat", {})
        history_lock = self.data.get("history_lock", {})
        manual_jobs = self.data.get("manual_backfill_progress", [])
        scheduled_jobs = self.data.get("scheduled_backfill_progress", [])
        cancel_files = self.data.get("cancel_files", [])
        recent_runs = self.data.get("recent_runs", [])
        service_alive = bool(supervisor.get("alive"))
        heartbeat_stale = bool(heartbeat.get("stale"))
        service_running = bool(service_alive and not heartbeat_stale)
        service_hung = bool(service_alive and heartbeat_stale)
        history_running = bool(history_lock.get("active"))
        active_manual = next(
            (
                item for item in manual_jobs
                if item.get("status") == "RUNNING" and not item.get("stale_progress")
            ),
            None,
        )
        active_scheduled = next(
            (
                item for item in scheduled_jobs
                if item.get("status") == "RUNNING" and not item.get("stale_progress")
            ),
            None,
        )
        running_session = next(
            (
                item for item in recent_runs
                if item.get("status") == "RUNNING"
            ),
            None,
        )

        lines.append("Runtime status:")
        if service_running:
            host_note = f"  host={supervisor.get('host')}" if supervisor.get("remote") else ""
            lines.append(
                f"  Backfill service       Running  PID={supervisor.get('pid') or '-'}{host_note}"
            )
            lines.append(
                f"  Service heartbeat      OK       age={heartbeat.get('age_seconds') if heartbeat.get('age_seconds') is not None else '-'}s"
            )
        elif service_hung:
            lines.append(
                f"  Backfill service       Hung     PID={supervisor.get('pid') or '-'}"
            )
            lines.append(
                f"  Service heartbeat      Stale    age={heartbeat.get('age_seconds') if heartbeat.get('age_seconds') is not None else '-'}s"
            )
        else:
            lines.append("  Backfill service       Stopped")

        if history_running:
            process_state = (running_session or {}).get("process_state") or "-"
            lines.append(
                f"  Data job               Running  PID={history_lock.get('pid') or '-'}  "
                f"host={history_lock.get('host') or '-'}  process={process_state}"
            )
            if active_manual:
                current = active_manual.get("current_batch") if isinstance(active_manual.get("current_batch"), dict) else {}
                if current:
                    lines.append(
                        f"  Current batch          {current.get('index')}/{current.get('total')}  "
                        f"{current.get('request_from_utc')} -> {current.get('to_utc')}"
                    )
                    lines.append(
                            f"  Progress updated       {active_manual.get('updated_at_utc') or '-'}"
                    )
            elif active_scheduled:
                current = active_scheduled.get("current_batch") if isinstance(active_scheduled.get("current_batch"), dict) else {}
                if current:
                    lines.append(
                        f"  Current batch          {current.get('index')}/{current.get('total')}  "
                        f"{current.get('request_from_utc')} -> {current.get('to_utc')}"
                    )
                    lines.append(
                        f"  Progress updated       {active_scheduled.get('updated_at_utc') or '-'}"
                    )
            if active_manual:
                stop_signal = next(
                    (
                        item for item in cancel_files
                        if item.get("name") == "manual-backfill.cancel"
                    ),
                    None,
                )
                if stop_signal:
                    lines.append(
                        f"  Stop requested         {stop_signal.get('requested_at_utc') or '-'}"
                    )
            lines.append("  Action                 choose mode 11 to stop safely")
        else:
            suffix = " (stale lock file present)" if history_lock.get("stale") else ""
            lines.append(f"  Data job               Stopped{suffix}")

        stale_scheduled = [
            item for item in scheduled_jobs
            if item.get("status") == "RUNNING" and item.get("stale_progress")
        ]
        if stale_scheduled:
            item = stale_scheduled[0]
            current = item.get("current_batch") if isinstance(item.get("current_batch"), dict) else {}
            lines.append(
                f"  Stale scheduled job    {item.get('name')}  "
                f"batch={current.get('index') or '-'}/{current.get('total') or '-'}"
            )

        if not service_alive and not history_running:
            lines.append("")
            lines.append("No tick_program runtime process is running.")

        if spool.get("count"):
            lines.append("")
            lines.append(f"Spool buffer             {spool.get('count')} row(s) queued")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_get(row: Any, name: str, index: int) -> Any:
    if hasattr(row, name):
        return getattr(row, name)
    return row[index]


def _as_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _seconds_since(value: Any, now: datetime) -> int | None:
    dt = _as_utc(value)
    if dt is None:
        return None
    return max(0, int((now - dt).total_seconds()))


def _utc_naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _bucket_count(bucket_minutes: int) -> int:
    bucket_minutes = int(bucket_minutes)
    if bucket_minutes <= 0 or 1440 % bucket_minutes != 0:
        raise ValueError("bucket_minutes must be a positive divisor of 1440")
    return 1440 // bucket_minutes


def _bucket_key(weekday: int, bucket_of_day: int) -> str:
    return f"{int(weekday)}:{int(bucket_of_day):03d}"


def _bucket_for_time(value: datetime, bucket_minutes: int) -> tuple[int, int]:
    dt = value.astimezone(timezone.utc)
    minute_of_day = dt.hour * 60 + dt.minute
    return dt.weekday(), minute_of_day // int(bucket_minutes)


def _observed_bucket_counts(
    start_utc: datetime,
    to_utc: datetime,
    bucket_minutes: int,
) -> dict[tuple[int, int], int]:
    counts: dict[tuple[int, int], int] = {}
    buckets_per_day = _bucket_count(bucket_minutes)
    cursor_date = start_utc.astimezone(timezone.utc).date()
    end_date = to_utc.astimezone(timezone.utc).date()
    while cursor_date <= end_date:
        day_start = datetime(
            cursor_date.year, cursor_date.month, cursor_date.day, tzinfo=timezone.utc
        )
        for bucket in range(buckets_per_day):
            bucket_start = day_start + timedelta(minutes=bucket * bucket_minutes)
            bucket_end = bucket_start + timedelta(minutes=bucket_minutes)
            if bucket_end <= start_utc or bucket_start >= to_utc:
                continue
            key = (bucket_start.weekday(), bucket)
            counts[key] = counts.get(key, 0) + 1
        cursor_date += timedelta(days=1)
    return counts


def _date_from_sql(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def _profile_entry(
    observed_days: int,
    tick_counts: list[int],
    active_min_ratio: float,
    min_active_ticks: int,
) -> dict[str, Any]:
    active_days = len(tick_counts)
    ratio = (active_days / observed_days) if observed_days else 0.0
    median_ticks = int(statistics.median(tick_counts)) if tick_counts else 0
    state = (
        ACTIVITY_STATE_ACTIVE
        if active_days > 0
        and ratio >= float(active_min_ratio)
        and median_ticks >= int(min_active_ticks)
        else ACTIVITY_STATE_QUIET
    )
    return {
        "state": state,
        "observed_days": int(observed_days),
        "active_days": int(active_days),
        "active_day_ratio": round(ratio, 4),
        "median_ticks": median_ticks,
        "max_ticks": int(max(tick_counts)) if tick_counts else 0,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_tick_activity_profile(
    settings: TickRuntimeSettings,
    store: TickSqlStore,
    lookback_days: int = DEFAULT_ACTIVITY_LOOKBACK_DAYS,
    bucket_minutes: int = DEFAULT_ACTIVITY_BUCKET_MINUTES,
    active_min_ratio: float = DEFAULT_ACTIVITY_ACTIVE_MIN_RATIO,
    min_active_ticks: int = DEFAULT_ACTIVITY_MIN_ACTIVE_TICKS,
    output_path: Path | None = None,
    to_utc: datetime | None = None,
) -> dict[str, Any]:
    """Build a learned tick activity profile from recent SQL tick history."""
    from tick_engine.settings import ensure_runtime_dirs

    _bucket_count(bucket_minutes)
    ensure_runtime_dirs()
    lookback_days = max(1, int(lookback_days))
    bucket_minutes = int(bucket_minutes)
    to_utc = (to_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start_utc = to_utc - timedelta(days=lookback_days)
    observed_counts = _observed_bucket_counts(start_utc, to_utc, bucket_minutes)

    profile: dict[str, Any] = {
        "generated_at_utc": to_utc.isoformat().replace("+00:00", "Z"),
        "lookback_days": lookback_days,
        "bucket_minutes": bucket_minutes,
        "active_min_ratio": float(active_min_ratio),
        "min_active_ticks": int(min_active_ticks),
        "from_utc": start_utc.isoformat().replace("+00:00", "Z"),
        "to_utc": to_utc.isoformat().replace("+00:00", "Z"),
        "symbols": {},
    }

    conn = store.connection_factory()
    cursor = conn.cursor()
    try:
        for target in settings.symbols:
            symbol = target.local_symbol.upper()
            table = qualified_tick_table(store.schema, symbol, store.allowed_symbols)
            cursor.execute(
                f"""
                SELECT
                    TickDateUtc,
                    BucketOfDay,
                    COUNT_BIG(*) AS TickCount,
                    MAX(TickTimeUtc) AS LatestTickTimeUtc
                FROM (
                    SELECT
                        TickTimeUtc,
                        CAST(TickTimeUtc AS date) AS TickDateUtc,
                        CAST(((DATEPART(hour, TickTimeUtc) * 60) + DATEPART(minute, TickTimeUtc)) / ? AS int) AS BucketOfDay
                    FROM {table}
                    WHERE TickTimeUtc >= ? AND TickTimeUtc < ?
                      AND Bid IS NOT NULL AND Ask IS NOT NULL
                ) q
                GROUP BY TickDateUtc, BucketOfDay
                """,
                (
                    bucket_minutes,
                    _utc_naive(start_utc),
                    _utc_naive(to_utc),
                ),
            )
            counts_by_bucket: dict[tuple[int, int], list[int]] = {}
            latest_tick_utc: datetime | None = None
            rows = 0
            for row in cursor.fetchall():
                tick_date = _date_from_sql(_row_get(row, "TickDateUtc", 0))
                if tick_date is None:
                    continue
                bucket = int(_row_get(row, "BucketOfDay", 1))
                tick_count = int(_row_get(row, "TickCount", 2) or 0)
                latest_tick = _as_utc(_row_get(row, "LatestTickTimeUtc", 3))
                if latest_tick is not None and (
                    latest_tick_utc is None or latest_tick > latest_tick_utc
                ):
                    latest_tick_utc = latest_tick
                key = (tick_date.weekday(), bucket)
                counts_by_bucket.setdefault(key, []).append(tick_count)
                rows += tick_count

            entries: dict[str, dict[str, Any]] = {}
            active_bucket_count = 0
            for key, observed_days in sorted(observed_counts.items()):
                entry = _profile_entry(
                    observed_days,
                    counts_by_bucket.get(key, []),
                    active_min_ratio,
                    min_active_ticks,
                )
                if entry["state"] == ACTIVITY_STATE_ACTIVE:
                    active_bucket_count += 1
                entries[_bucket_key(*key)] = entry

            profile["symbols"][symbol] = {
                "entries": entries,
                "rows_observed": int(rows),
                "active_buckets": active_bucket_count,
                "total_buckets": len(entries),
                "latest_tick_utc": latest_tick_utc.isoformat().replace("+00:00", "Z")
                if latest_tick_utc is not None
                else None,
            }
    finally:
        conn.close()

    path = _activity_profile_path() if output_path is None else Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(profile, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    tmp_path.replace(path)
    profile["path"] = str(path)
    return profile


def load_tick_activity_profile(path: Path | None = None) -> dict[str, Any] | None:
    target = path if path is not None else _activity_profile_path()
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _profile_is_fresh(profile: dict[str, Any] | None, now: datetime) -> bool:
    if not profile:
        return False
    generated_at = _as_utc(profile.get("generated_at_utc"))
    if generated_at is None:
        return False
    return (now - generated_at).total_seconds() <= DEFAULT_ACTIVITY_MAX_AGE_DAYS * 24 * 60 * 60


def _profile_entry_at(
    profile: dict[str, Any],
    symbol: str,
    weekday: int,
    bucket: int,
) -> dict[str, Any] | None:
    symbols = profile.get("symbols") or {}
    symbol_profile = symbols.get(symbol.upper()) or {}
    entries = symbol_profile.get("entries") or {}
    return entries.get(_bucket_key(weekday, bucket))


def classify_activity_expectation(
    profile: dict[str, Any] | None,
    symbol: str,
    now: datetime,
) -> dict[str, Any]:
    """Classify whether a symbol is expected to be active in the current UTC bucket."""
    if not _profile_is_fresh(profile, now):
        return {"state": ACTIVITY_STATE_UNKNOWN, "reason": "profile_missing_or_stale"}
    try:
        bucket_minutes = int(profile.get("bucket_minutes") or DEFAULT_ACTIVITY_BUCKET_MINUTES)
        buckets_per_day = _bucket_count(bucket_minutes)
    except Exception:
        return {"state": ACTIVITY_STATE_UNKNOWN, "reason": "profile_invalid_bucket"}

    symbols = profile.get("symbols") or {}
    symbol_profile = symbols.get(symbol.upper()) or {}
    if (
        int(symbol_profile.get("rows_observed") or 0) > 0
        and int(symbol_profile.get("active_buckets") or 0) == 0
    ):
        return {
            "state": ACTIVITY_STATE_UNKNOWN,
            "reason": "profile_insufficient_training",
            "bucket_minutes": bucket_minutes,
        }

    weekday, bucket = _bucket_for_time(now, bucket_minutes)
    entry = _profile_entry_at(profile, symbol, weekday, bucket)
    if not entry:
        return {"state": ACTIVITY_STATE_UNKNOWN, "reason": "profile_entry_missing"}

    state = str(entry.get("state") or ACTIVITY_STATE_UNKNOWN)
    reason = "learned_profile"
    if state == ACTIVITY_STATE_QUIET:
        prev_bucket = bucket - 1
        prev_weekday = weekday
        if prev_bucket < 0:
            prev_bucket = buckets_per_day - 1
            prev_weekday = (weekday - 1) % 7
        next_bucket = bucket + 1
        next_weekday = weekday
        if next_bucket >= buckets_per_day:
            next_bucket = 0
            next_weekday = (weekday + 1) % 7
        prev_entry = _profile_entry_at(profile, symbol, prev_weekday, prev_bucket)
        next_entry = _profile_entry_at(profile, symbol, next_weekday, next_bucket)
        prev_active = bool(prev_entry and prev_entry.get("state") == ACTIVITY_STATE_ACTIVE)
        next_active = bool(next_entry and next_entry.get("state") == ACTIVITY_STATE_ACTIVE)
        if prev_active or next_active:
            state = ACTIVITY_STATE_TRANSITION
            reason = "near_learned_active_bucket"

    return {
        "state": state,
        "reason": reason,
        "bucket_minutes": bucket_minutes,
        "weekday": weekday,
        "bucket_of_day": bucket,
        "observed_days": int(entry.get("observed_days") or 0),
        "active_days": int(entry.get("active_days") or 0),
        "active_day_ratio": float(entry.get("active_day_ratio") or 0),
        "median_ticks": int(entry.get("median_ticks") or 0),
    }


def _table_stats(store: TickSqlStore, symbol: str) -> dict[str, Any]:
    from tick_engine.settings import SQL_HEALTH_TIMEOUT_SECONDS

    table = qualified_tick_table(store.schema, symbol, store.allowed_symbols)
    conn = store.connection_factory()
    cursor = conn.cursor()
    try:
        try:
            cursor.timeout = max(1, int(SQL_HEALTH_TIMEOUT_SECONDS))
        except (AttributeError, TypeError):
            pass
        cursor.execute(
            """
            SELECT COALESCE(SUM(row_count), 0) AS RowsTotal
            FROM sys.dm_db_partition_stats
            WHERE object_id = OBJECT_ID(?) AND index_id IN (0, 1)
            """,
            (f"{store.schema}.{symbol.upper()}",),
        )
        row = cursor.fetchone()
        cursor.execute(
            f"""
            SELECT TOP 1 TickTimeUtc AS LatestTickTimeUtc
            FROM {table}
            WHERE Bid IS NOT NULL AND Ask IS NOT NULL
            ORDER BY TickTimeUtc DESC, TickID DESC
            """
        )
        latest = cursor.fetchone()
        cursor.execute(
            f"""
            SELECT TOP 1 1 AS HasNegativeSpread
            FROM {table}
            WHERE TickTimeUtc >= DATEADD(day, -7, SYSUTCDATETIME())
              AND Bid IS NOT NULL AND Ask IS NOT NULL AND Ask < Bid
            """
        )
        neg = cursor.fetchone()
        return {
            "rows_total": int(_row_get(row, "RowsTotal", 0) or 0) if row else 0,
            "latest_tick_time_utc": str(_row_get(latest, "LatestTickTimeUtc", 0))
            if latest
            else None,
            "latest_tick_age_seconds": _seconds_since(
                _row_get(latest, "LatestTickTimeUtc", 0), datetime.now(timezone.utc)
            )
            if latest
            else None,
            "has_negative_spread": bool(neg),
        }
    finally:
        conn.close()


def _add_runtime_findings(
    findings: list[TickCheckFinding],
    data: dict[str, Any],
    expected_active_symbols: list[str],
) -> None:
    if not expected_active_symbols:
        return

    active_label = ",".join(sorted(expected_active_symbols))
    supervisor = data.get("supervisor", {})
    supervisor_alive = bool(supervisor.get("alive"))

    if not supervisor_alive:
        findings.append(
            TickCheckFinding(
                "WARNING",
                "backfill_service_stopped",
                "Tick backfill service is stopped while these symbols are expected "
                f"to be active: {active_label}. Start mode 10 when you want automatic backfill running.",
            )
        )


def run_tick_check(
    settings: TickRuntimeSettings,
    store: TickSqlStore,
    stale_seconds: int | None = None,
) -> TickCheckReport:
    """Run read-only checks and return a structured operator report."""
    from tick_engine.utils_support.proc_utils import is_pid_alive

    now = datetime.now(timezone.utc)
    activity_profile = load_tick_activity_profile()
    activity_profile_available = _profile_is_fresh(activity_profile, now)
    generated_at = activity_profile.get("generated_at_utc") if activity_profile else None
    findings: list[TickCheckFinding] = []
    data: dict[str, Any] = {
        "settings": {
            "env": settings.env,
            "schema": settings.schema,
            "symbols": [symbol.local_symbol for symbol in settings.symbols],
            "missing_api_fields": list(settings.missing_api_fields),
        }
    }
    activity_summary: dict[str, list[str]] = {
        ACTIVITY_STATE_ACTIVE: [],
        ACTIVITY_STATE_QUIET: [],
        ACTIVITY_STATE_TRANSITION: [],
        ACTIVITY_STATE_UNKNOWN: [],
    }
    data["activity_profile"] = {
        "path": str(_activity_profile_path()),
        "available": activity_profile_available,
        "generated_at_utc": generated_at,
        "bucket_minutes": activity_profile.get("bucket_minutes") if activity_profile else None,
    }

    if settings.missing_api_fields:
        findings.append(
            TickCheckFinding(
                "ERROR",
                "missing_api_fields",
                f"Required API fields are not set: {', '.join(settings.missing_api_fields)}. "
                "Run 'oauth-login' and then 'account-list' to set them up.",
            )
        )

    spool = TickSpool(settings.spool_path)
    spool_count = spool.count()
    spool_quarantine_count = spool.quarantine_count()
    spool_oldest_age = spool.oldest_age_seconds()
    data["spool"] = {
        "count": spool_count,
        "quarantine_count": spool_quarantine_count,
        "oldest_age_seconds": int(spool_oldest_age) if spool_oldest_age is not None else None,
        "path": str(settings.spool_path),
    }
    if spool_count > 0:
        findings.append(
            TickCheckFinding(
                "WARNING",
                "spool_backlog",
                f"{spool_count:,} ticks are queued in the local buffer. "
                "They will be replayed to the database automatically when the connection recovers. "
                f"Oldest queued age: {int(spool_oldest_age or 0)}s.",
            )
        )
    if spool_quarantine_count > 0:
        findings.append(
            TickCheckFinding(
                "ERROR",
                "spool_quarantine",
                f"{spool_quarantine_count:,} malformed spool row(s) were quarantined. "
                "They no longer block replay, but require an operator data review.",
            )
        )

    data["supervisor"] = _supervisor_pid_status()
    data["service_heartbeat"] = service_heartbeat_status()
    _apply_heartbeat_runtime_status(data["supervisor"], data["service_heartbeat"])
    try:
        from tick_engine.utils_support.lock_coord import job_lock_status

        data["history_lock"] = job_lock_status("ctrader-history")
    except Exception as exc:
        data["history_lock"] = {"active": None, "owner": None, "pid": None, "error": str(exc)}
    data["manual_backfill_progress"] = _manual_backfill_progress()
    data["scheduled_backfill_progress"] = scan_backfill_progress(prefix="scheduled_", limit=5)
    data["cancel_files"] = _runtime_cancel_files()
    active_manual = [
        item for item in data["manual_backfill_progress"]
        if item.get("status") == "RUNNING" and not item.get("stale_progress")
    ]
    manual_pull_active = bool(active_manual)
    data["manual_backfill_active"] = manual_pull_active
    if len(active_manual) > 1:
        findings.append(
            TickCheckFinding(
                "WARNING",
                "multiple_manual_backfills",
                f"{len(active_manual)} current manual backfill progress file(s) are marked RUNNING. "
                "Use Graceful Stop before starting a new manual backfill.",
            )
        )
    if data["supervisor"].get("exists") and not data["supervisor"].get("alive"):
        findings.append(
            TickCheckFinding(
                "WARNING",
                "stale_supervisor_pid_file",
                "A leftover supervisor pid file was found but that process is no longer running.",
            )
        )
    if data["supervisor"].get("alive") and data["service_heartbeat"].get("stale"):
        findings.append(
            TickCheckFinding(
                "ERROR",
                "stale_service_heartbeat",
                "The backfill service PID exists, but its heartbeat is stale. The service is likely hung.",
            )
        )
    stale_scheduled = [
        item for item in data["scheduled_backfill_progress"]
        if item.get("status") == "RUNNING" and item.get("stale_progress")
    ]
    if stale_scheduled:
        findings.append(
            TickCheckFinding(
                "WARNING",
                "stale_scheduled_progress",
                f"{len(stale_scheduled)} scheduled backfill progress file(s) are stale. "
                "The checker will clean local stale metadata automatically when the owner process is gone.",
            )
        )
    table_stats: dict[str, Any] = {}
    conn = store.connection_factory()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            SELECT SenSymbol, MappingStatus, Enabled, CTraderSymbolId
            FROM {quote_ident(store.schema)}.[SymbolMap]
            ORDER BY SenSymbol
            """
        )
        symbol_rows = cursor.fetchall()
        data["symbol_map"] = [
            {
                "symbol": str(_row_get(row, "SenSymbol", 0)),
                "mapping_status": str(_row_get(row, "MappingStatus", 1)),
                "enabled": bool(_row_get(row, "Enabled", 2)),
                "ctrader_symbol_id": _row_get(row, "CTraderSymbolId", 3),
            }
            for row in symbol_rows
        ]

        for row in symbol_rows:
            symbol = str(_row_get(row, "SenSymbol", 0)).upper()
            mapping_status = str(_row_get(row, "MappingStatus", 1))
            enabled = bool(_row_get(row, "Enabled", 2))
            if enabled and mapping_status != "MATCHED":
                findings.append(
                    TickCheckFinding(
                        "ERROR",
                        "symbol_not_matched",
                        f"{symbol} is enabled but has no cTrader match (status: {mapping_status}). "
                        "Run 'symbol-sync' to fix this.",
                    )
                )

        cursor.execute(
            f"""
            SELECT SenSymbol, MappingStatus, Status, LastLiveTickTimeUtc,
                   LastHistoricalTickTimeUtc, LastHeartbeatAtUtc, TotalTicksInserted, LastError
            FROM {quote_ident(store.schema)}.[v_IngestHealth]
            ORDER BY SenSymbol
            """
        )
        health_rows = cursor.fetchall()
        health: list[dict[str, Any]] = []
        expected_active_symbols: list[str] = []
        for row in health_rows:
            symbol = str(_row_get(row, "SenSymbol", 0)).upper()
            heartbeat_age = _seconds_since(_row_get(row, "LastHeartbeatAtUtc", 5), now)
            try:
                stats = _table_stats(store, symbol)
                table_stats[symbol] = stats
            except Exception:
                stats = {}
            latest_tick_time = stats.get("latest_tick_time_utc") or _row_get(
                row, "LastHistoricalTickTimeUtc", 4
            )
            historical_tick_age = _seconds_since(latest_tick_time, now)
            activity = classify_activity_expectation(activity_profile, symbol, now)
            activity_state = str(activity.get("state") or ACTIVITY_STATE_UNKNOWN)
            activity_summary.setdefault(activity_state, []).append(symbol)
            if activity_state == ACTIVITY_STATE_ACTIVE:
                expected_active_symbols.append(symbol)
            threshold = stale_seconds
            if threshold is None:
                threshold = 1800
            health_item = {
                "symbol": symbol,
                "mapping_status": str(_row_get(row, "MappingStatus", 1)),
                "status": str(_row_get(row, "Status", 2))
                if _row_get(row, "Status", 2) is not None
                else None,
                "last_live_tick_time_utc": str(_row_get(row, "LastLiveTickTimeUtc", 3)),
                "last_historical_tick_time_utc": str(latest_tick_time),
                "state_last_historical_tick_time_utc": str(_row_get(row, "LastHistoricalTickTimeUtc", 4)),
                "last_heartbeat_at_utc": str(_row_get(row, "LastHeartbeatAtUtc", 5)),
                "heartbeat_age_seconds": heartbeat_age,
                "live_tick_age_seconds": None,
                "historical_tick_age_seconds": historical_tick_age,
                "total_ticks_inserted": int(
                    stats.get("rows_total") or _row_get(row, "TotalTicksInserted", 6) or 0
                ),
                "last_error": str(_row_get(row, "LastError", 7) or ""),
                "stale_threshold_seconds": int(threshold),
                "activity_state": activity_state,
                "activity_reason": str(activity.get("reason") or ""),
                "activity_bucket_minutes": activity.get("bucket_minutes"),
                "activity_weekday": activity.get("weekday"),
                "activity_bucket_of_day": activity.get("bucket_of_day"),
                "activity_active_day_ratio": activity.get("active_day_ratio"),
                "activity_median_ticks": activity.get("median_ticks"),
            }
            health.append(health_item)
            expected_active = activity_state == ACTIVITY_STATE_ACTIVE
            if (
                health_item["mapping_status"] == "MATCHED"
                and historical_tick_age is None
                and expected_active
                and not manual_pull_active
            ):
                findings.append(
                    TickCheckFinding(
                        "WARNING",
                        "missing_historical_tick",
                        f"{symbol}: no historical ticks have been recorded yet. "
                        "The market is expected to be active right now.",
                    )
                )
            elif (
                health_item["mapping_status"] == "MATCHED"
                and historical_tick_age is not None
                and historical_tick_age > int(threshold)
                and expected_active
                and not manual_pull_active
            ):
                mins = historical_tick_age // 60
                age_str = f"{mins} minute(s)" if mins >= 1 else f"{historical_tick_age} seconds"
                findings.append(
                    TickCheckFinding(
                        "WARNING",
                        "stale_historical_tick",
                        f"{symbol}: no fresh historical tick for {age_str} "
                        f"(limit is {threshold}s). The market is expected to be active.",
                    )
                )
            if health_item["last_error"]:
                findings.append(
                    TickCheckFinding(
                        "WARNING",
                        "symbol_last_error",
                        f"{symbol} reported an error: {health_item['last_error'][:300]}",
                    )
                )
        data["ingest_health"] = health
        data["activity_summary"] = {key: sorted(value) for key, value in activity_summary.items()}
        data["expected_active_symbols"] = sorted(expected_active_symbols)

        cursor.execute(
            f"""
            SELECT TOP 10 IngestRunID, AppName, StartedAtUtc, StoppedAtUtc, Status,
                   RowsInserted, RowsSpooled, StopReason, HostName, ProcessID
            FROM {quote_ident(store.schema)}.[IngestRun]
            ORDER BY StartedAtUtc DESC
            """
        )
        import socket as _socket

        _local_host = _socket.gethostname()
        recent_run_rows = cursor.fetchall()
        recent_runs: list[dict] = []
        for row in recent_run_rows:
            run_status = str(_row_get(row, "Status", 4))
            pid_raw = _row_get(row, "ProcessID", 9)
            host_name = str(_row_get(row, "HostName", 8) or "")
            run_dict: dict = {
                "ingest_run_id": str(_row_get(row, "IngestRunID", 0)),
                "app_name": str(_row_get(row, "AppName", 1)),
                "started_at_utc": str(_row_get(row, "StartedAtUtc", 2)),
                "stopped_at_utc": str(_row_get(row, "StoppedAtUtc", 3)),
                "status": run_status,
                "rows_inserted": int(_row_get(row, "RowsInserted", 5) or 0),
                "rows_spooled": int(_row_get(row, "RowsSpooled", 6) or 0),
                "stop_reason": str(_row_get(row, "StopReason", 7) or ""),
                "host_name": host_name,
                "process_id": pid_raw,
                "process_state": "-",
            }
            if run_status == "RUNNING":
                try:
                    pid_int = int(pid_raw) if pid_raw is not None else None
                    is_local = host_name.lower() == _local_host.lower()
                    if pid_int is not None and is_local:
                        alive = is_pid_alive(pid_int)
                        run_dict["process_state"] = "alive" if alive else "dead"
                    elif pid_int is not None and host_name:
                        run_dict["process_state"] = "remote"
                    else:
                        run_dict["process_state"] = "unknown"
                except Exception:
                    pass
            recent_runs.append(run_dict)
        data["recent_runs"] = recent_runs

        _add_runtime_findings(findings, data, expected_active_symbols)
    except Exception as exc:
        findings.append(TickCheckFinding("ERROR", "sql_check_failed", str(exc)[:800]))
    finally:
        conn.close()

    for target in settings.symbols:
        symbol = target.local_symbol.upper()
        try:
            stats = table_stats.get(symbol)
            if stats is None:
                stats = _table_stats(store, symbol)
            table_stats[symbol] = stats
            if stats["has_negative_spread"]:
                findings.append(
                    TickCheckFinding(
                        "WARNING",
                        "negative_spread",
                        f"{symbol}: data quality issue - some ticks have Ask price lower than Bid price. "
                        "This is abnormal and may indicate a bad data feed.",
                    )
                )
        except Exception as exc:
            table_stats[symbol] = {"error": str(exc)}
            findings.append(
                TickCheckFinding("ERROR", "tick_table_check_failed", f"{symbol}: {str(exc)[:500]}")
            )
    data["table_stats"] = table_stats

    severity_rank = {"ERROR": 2, "WARNING": 1, "INFO": 0}
    max_rank = max((severity_rank.get(item.severity, 0) for item in findings), default=0)
    status = "ERROR" if max_rank >= 2 else "WARNING" if max_rank == 1 else "OK"
    return TickCheckReport(
        status=status,
        generated_at_utc=now.isoformat(),
        findings=findings,
        data=data,
    )
