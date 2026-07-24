"""Read-only health checks for the isolated cTrader tick provider."""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from data_provider.paths import CACHE_DIR, ensure_runtime_dirs

from .runtime import TickRuntimeSettings
from .runtime_lock import TICK_LIVE_RUNTIME_LOCK, stale_pid_status
from .spool import TickSpool
from .store_sql import TickSqlStore, qualified_tick_table, quote_ident

ACTIVITY_PROFILE_PATH = CACHE_DIR / "tick_activity_profile.json"
ACTIVITY_STATE_ACTIVE = "EXPECTED_ACTIVE"
ACTIVITY_STATE_QUIET = "EXPECTED_QUIET"
ACTIVITY_STATE_TRANSITION = "TRANSITION_GRACE"
ACTIVITY_STATE_UNKNOWN = "UNKNOWN"
DEFAULT_ACTIVITY_LOOKBACK_DAYS = 30
DEFAULT_ACTIVITY_BUCKET_MINUTES = 15
DEFAULT_ACTIVITY_ACTIVE_MIN_RATIO = 0.25
DEFAULT_ACTIVITY_MIN_ACTIVE_TICKS = 1
DEFAULT_ACTIVITY_MAX_AGE_DAYS = 7


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
        lines = [
            f"status={self.status}",
            f"generated_at_utc={self.generated_at_utc}",
        ]
        for finding in self.findings:
            lines.append(f"[{finding.severity}] {finding.code}: {finding.message}")
        if not self.findings:
            lines.append("[OK] tick_data: no findings")
        spool = self.data.get("spool", {})
        pid = self.data.get("pid", {})
        activity_profile = self.data.get("activity_profile", {})
        activity_summary = self.data.get("activity_summary", {})
        active = ",".join(activity_summary.get(ACTIVITY_STATE_ACTIVE, [])) or "-"
        quiet = ",".join(activity_summary.get(ACTIVITY_STATE_QUIET, [])) or "-"
        transition = ",".join(activity_summary.get(ACTIVITY_STATE_TRANSITION, [])) or "-"
        unknown = ",".join(activity_summary.get(ACTIVITY_STATE_UNKNOWN, [])) or "-"
        lines.append(
            "activity_profile="
            f"{'available' if activity_profile.get('available') else 'fallback'} "
            f"generated_at_utc={activity_profile.get('generated_at_utc') or '-'} "
            f"bucket_minutes={activity_profile.get('bucket_minutes') or '-'}"
        )
        lines.append(f"expected_active={active}")
        lines.append(f"expected_quiet={quiet}")
        lines.append(f"transition_grace={transition}")
        lines.append(f"activity_unknown={unknown}")
        lines.append(f"spool_count={spool.get('count')} spool_path={spool.get('path')}")
        lines.append(f"pid_file={pid.get('path')} pid={pid.get('pid')} alive={pid.get('alive')}")
        return "\n".join(lines)


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
        day_start = datetime(cursor_date.year, cursor_date.month, cursor_date.day, tzinfo=timezone.utc)
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


def build_tick_activity_profile(
    settings: TickRuntimeSettings,
    store: TickSqlStore,
    lookback_days: int = DEFAULT_ACTIVITY_LOOKBACK_DAYS,
    bucket_minutes: int = DEFAULT_ACTIVITY_BUCKET_MINUTES,
    active_min_ratio: float = DEFAULT_ACTIVITY_ACTIVE_MIN_RATIO,
    min_active_ticks: int = DEFAULT_ACTIVITY_MIN_ACTIVE_TICKS,
    output_path=ACTIVITY_PROFILE_PATH,
    to_utc: datetime | None = None,
) -> dict[str, Any]:
    """Build a learned tick activity profile from recent SQL tick history."""
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
                if latest_tick is not None and (latest_tick_utc is None or latest_tick > latest_tick_utc):
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

    path = ACTIVITY_PROFILE_PATH if output_path is None else Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(profile, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp_path.replace(path)
    profile["path"] = str(path)
    return profile


def load_tick_activity_profile(path=ACTIVITY_PROFILE_PATH) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
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
    table = qualified_tick_table(store.schema, symbol, store.allowed_symbols)
    conn = store.connection_factory()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            SELECT COALESCE(SUM(row_count), 0) AS RowsTotal
            FROM sys.dm_db_partition_stats
            WHERE object_id = OBJECT_ID(?)
              AND index_id IN (0, 1)
            """,
            (f"{store.schema}.{symbol}",),
        )
        row = cursor.fetchone()
        cursor.execute(
            f"""
            SELECT TOP 1 TickTimeUtc AS LatestTickTimeUtc
            FROM {table}
            ORDER BY TickTimeUtc DESC, TickID DESC
            """
        )
        latest = cursor.fetchone()
        cursor.execute(
            f"""
            SELECT TOP 1 1 AS HasNegativeSpread
            FROM {table}
            WHERE Bid IS NOT NULL AND Ask IS NOT NULL AND Ask < Bid
            """
        )
        neg = cursor.fetchone()
        return {
            "rows_total": int(_row_get(row, "RowsTotal", 0) or 0) if row else 0,
            "latest_tick_time_utc": str(_row_get(latest, "LatestTickTimeUtc", 0)) if latest else None,
            "has_negative_spread": bool(neg),
        }
    finally:
        conn.close()


def run_tick_check(
    settings: TickRuntimeSettings,
    store: TickSqlStore,
    stale_seconds: int | None = None,
) -> TickCheckReport:
    """Run read-only checks and return a structured operator report."""
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
        "path": str(ACTIVITY_PROFILE_PATH),
        "available": activity_profile_available,
        "generated_at_utc": generated_at,
        "bucket_minutes": activity_profile.get("bucket_minutes") if activity_profile else None,
    }

    if settings.missing_api_fields:
        findings.append(
            TickCheckFinding(
                "ERROR",
                "missing_api_fields",
                "Missing cTrader fields: " + ",".join(settings.missing_api_fields),
            )
        )

    spool = TickSpool(settings.spool_path)
    spool_count = spool.count()
    data["spool"] = {"count": spool_count, "path": str(settings.spool_path)}
    if spool_count > 0:
        findings.append(
            TickCheckFinding("WARNING", "spool_backlog", f"{spool_count} tick rows are waiting in SQLite spool")
        )

    pid_status = stale_pid_status()
    data["pid"] = pid_status
    if pid_status["exists"] and not pid_status["alive"]:
        findings.append(TickCheckFinding("WARNING", "stale_pid_file", f"Stale pid file: {pid_status['path']}"))

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
                    TickCheckFinding("ERROR", "symbol_not_matched", f"{symbol} mapping_status={mapping_status}")
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
        for row in health_rows:
            symbol = str(_row_get(row, "SenSymbol", 0)).upper()
            heartbeat_age = _seconds_since(_row_get(row, "LastHeartbeatAtUtc", 5), now)
            activity = classify_activity_expectation(activity_profile, symbol, now)
            activity_state = str(activity.get("state") or ACTIVITY_STATE_UNKNOWN)
            activity_summary.setdefault(activity_state, []).append(symbol)
            threshold = stale_seconds
            if threshold is None:
                threshold = (
                    settings.stale_seconds_btc
                    if symbol == "BTCUSD"
                    else settings.stale_seconds_market
                )
            health_item = {
                "symbol": symbol,
                "mapping_status": str(_row_get(row, "MappingStatus", 1)),
                "status": str(_row_get(row, "Status", 2)) if _row_get(row, "Status", 2) is not None else None,
                "last_live_tick_time_utc": str(_row_get(row, "LastLiveTickTimeUtc", 3)),
                "last_historical_tick_time_utc": str(_row_get(row, "LastHistoricalTickTimeUtc", 4)),
                "last_heartbeat_at_utc": str(_row_get(row, "LastHeartbeatAtUtc", 5)),
                "heartbeat_age_seconds": heartbeat_age,
                "total_ticks_inserted": int(_row_get(row, "TotalTicksInserted", 6) or 0),
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
            expected_quiet = activity_state in {ACTIVITY_STATE_QUIET, ACTIVITY_STATE_TRANSITION}
            if health_item["mapping_status"] == "MATCHED" and heartbeat_age is None and not expected_quiet:
                findings.append(TickCheckFinding("WARNING", "missing_heartbeat", f"{symbol} has no live heartbeat yet"))
            elif (
                health_item["mapping_status"] == "MATCHED"
                and heartbeat_age is not None
                and heartbeat_age > int(threshold)
                and not expected_quiet
            ):
                findings.append(
                    TickCheckFinding(
                        "WARNING",
                        "stale_heartbeat",
                        (
                            f"{symbol} heartbeat age {heartbeat_age}s exceeds {threshold}s "
                            f"while activity_state={activity_state}"
                        ),
                    )
                )
            if health_item["last_error"]:
                findings.append(
                    TickCheckFinding("WARNING", "symbol_last_error", f"{symbol}: {health_item['last_error'][:300]}")
                )
        data["ingest_health"] = health
        data["activity_summary"] = {key: sorted(value) for key, value in activity_summary.items()}

        cursor.execute(
            f"""
            SELECT TOP 5 IngestRunID, AppName, StartedAtUtc, StoppedAtUtc, Status,
                   RowsInserted, RowsSpooled, StopReason, HostName, ProcessID
            FROM {quote_ident(store.schema)}.[IngestRun]
            ORDER BY StartedAtUtc DESC
            """
        )
        data["recent_runs"] = [
            {
                "ingest_run_id": str(_row_get(row, "IngestRunID", 0)),
                "app_name": str(_row_get(row, "AppName", 1)),
                "started_at_utc": str(_row_get(row, "StartedAtUtc", 2)),
                "stopped_at_utc": str(_row_get(row, "StoppedAtUtc", 3)),
                "status": str(_row_get(row, "Status", 4)),
                "rows_inserted": int(_row_get(row, "RowsInserted", 5) or 0),
                "rows_spooled": int(_row_get(row, "RowsSpooled", 6) or 0),
                "stop_reason": str(_row_get(row, "StopReason", 7) or ""),
                "host_name": str(_row_get(row, "HostName", 8) or ""),
                "process_id": _row_get(row, "ProcessID", 9),
            }
            for row in cursor.fetchall()
        ]

        cursor.execute(
            """
            SELECT ExpiresAt, Payload
            FROM SEN.ActiveTask
            WHERE TaskName = ?
            """,
            (TICK_LIVE_RUNTIME_LOCK,),
        )
        lock_row = cursor.fetchone()
        data["runtime_lock"] = {
            "exists": lock_row is not None,
            "expires_at": str(_row_get(lock_row, "ExpiresAt", 0)) if lock_row else None,
            "payload": str(_row_get(lock_row, "Payload", 1)) if lock_row else None,
        }
    except Exception as exc:
        findings.append(TickCheckFinding("ERROR", "sql_check_failed", str(exc)[:800]))
    finally:
        conn.close()

    table_stats: dict[str, Any] = {}
    for target in settings.symbols:
        symbol = target.local_symbol.upper()
        try:
            stats = _table_stats(store, symbol)
            table_stats[symbol] = stats
            if stats["has_negative_spread"]:
                findings.append(
                    TickCheckFinding(
                        "WARNING",
                        "negative_spread",
                        f"{symbol} has at least one row with Ask < Bid",
                    )
                )
        except Exception as exc:
            table_stats[symbol] = {"error": str(exc)}
            findings.append(TickCheckFinding("ERROR", "tick_table_check_failed", f"{symbol}: {str(exc)[:500]}"))
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
