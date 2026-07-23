"""Operator and automated-review queries over current and archived log streams."""

from __future__ import annotations

import gzip
import heapq
import json
import time
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from core_engine.settings import ALERTS_LOG, HISTORICAL_LOG, LIVE_LOG, LOG_ARCHIVE_DIR, SYSTEM_LOG
from core_engine.util.logkit.formatter import parse_operator_line

_CURRENT = {
    "live": LIVE_LOG,
    "historical": HISTORICAL_LOG,
    "system": SYSTEM_LOG,
    "alerts": ALERTS_LOG,
}
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().lower()
    now = datetime.now(timezone.utc)
    units = {"m": "minutes", "h": "hours", "d": "days"}
    if len(text) >= 2 and text[-1] in units:
        try:
            amount = float(text[:-1])
            return now - timedelta(**{units[text[-1]]: amount})
        except Exception:
            pass
    parsed = datetime.fromisoformat(text.replace("z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def _event_time(event: dict[str, Any]) -> datetime | None:
    try:
        return datetime.strptime(
            str(event["timestamp"]),
            "%Y-%m-%d %H:%M:%S.%f UTC",
        ).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _candidate_files(streams: set[str]) -> list[tuple[str, Path]]:
    result: list[tuple[str, Path]] = []
    for stream in ("live", "historical", "system", "alerts"):
        if stream not in streams:
            continue
        current = Path(_CURRENT[stream])
        if current.exists():
            result.append((stream, current))
        if LOG_ARCHIVE_DIR.exists():
            for path in LOG_ARCHIVE_DIR.rglob(f"{current.stem}.*.log*"):
                if path.is_file():
                    result.append((stream, path))
    return result


def _read_lines(path: Path) -> Iterator[str]:
    if path.suffix.lower() == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            yield from handle
    else:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            yield from handle


def iter_events(
    *,
    streams: Iterable[str] | None = None,
    since: str | datetime | None = None,
    min_level: str | None = None,
    component: str | None = None,
    event_code: str | None = None,
    correlation_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    selected = {str(item).lower() for item in (streams or _CURRENT)}
    selected &= set(_CURRENT)
    cutoff = (
        since
        if isinstance(since, datetime)
        else _parse_since(str(since) if since is not None else None)
    )
    required_level = _LEVELS.get(str(min_level or "DEBUG").upper(), 10)
    component_filter = str(component or "").lower()
    event_filter = str(event_code or "").lower()
    correlation_filter = str(correlation_id or "")

    seen_event_ids: set[str] = set()
    for stream, path in _candidate_files(selected):
        try:
            for line_number, line in enumerate(_read_lines(path), start=1):
                event = parse_operator_line(line)
                if event is None:
                    continue
                event["source_file"] = str(path)
                event["line_number"] = line_number
                event.setdefault("stream", stream)
                event_id = str(event.get("event_id") or "")
                if event_id and event_id != "-":
                    if event_id in seen_event_ids:
                        continue
                    seen_event_ids.add(event_id)
                stamp = _event_time(event)
                if cutoff is not None and (stamp is None or stamp < cutoff):
                    continue
                if _LEVELS.get(str(event.get("level", "")).upper(), 0) < required_level:
                    continue
                if component_filter and component_filter not in str(event.get("component", "")).lower():
                    continue
                if event_filter and event_filter not in str(event.get("event", "")).lower():
                    continue
                if correlation_filter:
                    reference = str(event.get("correlation_id") or event.get("reference") or "")
                    if reference != correlation_filter:
                        continue
                yield event
        except (OSError, EOFError, gzip.BadGzipFile):
            continue


def find_events(*, limit: int = 200, **filters: Any) -> list[dict[str, Any]]:
    size = max(1, int(limit))
    ranked = heapq.nlargest(
        size,
        enumerate(iter_events(**filters)),
        key=lambda item: (str(item[1].get("timestamp", "")), item[0]),
    )
    events = [item for _, item in ranked]
    events.sort(key=lambda item: str(item.get("timestamp", "")))
    return events


def _human_line(event: dict[str, Any]) -> str:
    stamp = str(event.get("timestamp", "-")).replace(" UTC", "")
    if len(stamp) >= 19:
        stamp = stamp[11:19]
    return " | ".join(
        [
            f"{stamp:<8}",
            f"{str(event.get('level', '-')):<8}",
            f"{str(event.get('area', '-')):<12}",
            f"{str(event.get('stage', '-')):<12}",
            str(event.get("message", "-")),
            f"result={event.get('result', '-')}",
            f"ref={event.get('reference', '-')}",
        ]
    )


def print_events(events: list[dict[str, Any]], *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(events, ensure_ascii=True, indent=2, default=str))
        return
    if not events:
        print("No matching log events.")
        return
    for event in events:
        print(_human_line(event))


def status_report(*, since: str = "24h") -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for stream in _CURRENT:
        events.extend(find_events(streams={stream}, since=since, limit=5000))
    latest: dict[str, dict[str, Any]] = {}
    levels = Counter()
    for event in events:
        stream = str(event.get("stream", "system"))
        latest[stream] = event
        levels[str(event.get("level", "UNKNOWN"))] += 1
    streams: dict[str, Any] = {}
    now = datetime.now(timezone.utc)
    for stream, path in _CURRENT.items():
        last = latest.get(stream)
        stamp = _event_time(last) if last else None
        age = (now - stamp).total_seconds() if stamp else None
        streams[stream] = {
            "path": str(path),
            "exists": Path(path).exists(),
            "last_event_at": last.get("timestamp") if last else None,
            "last_event": last.get("event") if last else None,
            "last_message": last.get("message") if last else None,
            "age_seconds": age,
        }
    return {
        "generated_at": now.isoformat(),
        "since": since,
        "events": len(events),
        "levels": dict(levels),
        "streams": streams,
    }


def print_status(report: dict[str, Any], *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=True, indent=2, default=str))
        return
    print("DP PROGRAM LOG STATUS")
    print()
    print(f"{'STREAM':<12} {'STATE':<12} {'LAST EVENT':<24} MESSAGE")
    print("-" * 100)
    for stream, item in report.get("streams", {}).items():
        age = item.get("age_seconds")
        if not item.get("exists"):
            state = "NO DATA"
        elif age is None:
            state = "UNKNOWN"
        elif stream == "historical":
            state = "OK" if age < 36 * 3600 else "STALE"
        else:
            state = "OK" if age < 20 * 60 else "STALE"
        print(
            f"{stream.upper():<12} {state:<12} "
            f"{str(item.get('last_event') or '-'):<24} "
            f"{item.get('last_message') or '-'}"
        )
    levels = report.get("levels", {})
    print()
    print(
        "Last 24h: "
        f"warnings={levels.get('WARNING', 0)} "
        f"errors={levels.get('ERROR', 0)} "
        f"critical={levels.get('CRITICAL', 0)}"
    )


def risk_report(*, since: str = "24h", max_events: int = 200000) -> dict[str, Any]:
    events_buffer: deque[dict[str, Any]] = deque(maxlen=max(1, int(max_events)))
    scanned = 0
    for event in iter_events(since=since):
        events_buffer.append(event)
        scanned += 1
    events = list(events_buffer)
    truncated = scanned > len(events)
    events.sort(key=lambda item: str(item.get("timestamp", "")))

    issues: list[dict[str, Any]] = []
    open_lifecycle: dict[str, dict[str, Any]] = {}
    warning_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        level = str(event.get("level", "")).upper()
        code = str(event.get("event", "unknown"))
        reference = str(event.get("correlation_id") or event.get("reference") or "")
        stage = str(event.get("stage", "")).upper()
        if level in {"WARNING", "ERROR", "CRITICAL"}:
            warning_groups[code].append(event)
        if reference and reference != "-":
            if stage == "START":
                open_lifecycle[reference] = event
            elif stage in {"COMPLETE", "FAILED", "STOP", "CRITICAL"}:
                open_lifecycle.pop(reference, None)

        count_sets = [
            ("accepted_bars", "staged_rows", "fact_inserted"),
            ("accepted", "staged", "fact"),
        ]
        for keys in count_sets:
            values = [event.get(key) for key in keys]
            if all(value is not None for value in values):
                try:
                    numbers = [int(value) for value in values]
                except Exception:
                    continue
                if len(set(numbers)) > 1:
                    issues.append(
                        {
                            "severity": "high",
                            "kind": "delivery_count_mismatch",
                            "message": f"Delivery counts do not match: {dict(zip(keys, numbers))}",
                            "evidence": _evidence(event),
                        }
                    )

    now = datetime.now(timezone.utc)
    for reference, event in open_lifecycle.items():
        stamp = _event_time(event)
        if stamp and now - stamp >= timedelta(minutes=20):
            issues.append(
                {
                    "severity": "high",
                    "kind": "incomplete_lifecycle",
                    "message": f"Operation {reference} started but has no terminal event.",
                    "evidence": _evidence(event),
                }
            )

    for code, group in warning_groups.items():
        errors = [item for item in group if item.get("level") in {"ERROR", "CRITICAL"}]
        if errors:
            latest = errors[-1]
            issues.append(
                {
                    "severity": "critical" if latest.get("level") == "CRITICAL" else "high",
                    "kind": "logged_failure",
                    "message": f"{code} occurred {len(errors)} time(s).",
                    "evidence": _evidence(latest),
                }
            )
        elif len(group) >= 5:
            issues.append(
                {
                    "severity": "medium",
                    "kind": "repeated_warning",
                    "message": f"{code} occurred {len(group)} time(s).",
                    "evidence": _evidence(group[-1]),
                }
            )

    for stream, path in _CURRENT.items():
        if stream == "alerts":
            continue
        if not Path(path).exists():
            issues.append(
                {
                    "severity": "high",
                    "kind": "missing_log_stream",
                    "message": f"{stream}.log does not exist.",
                    "evidence": {"file": str(path)},
                }
            )
            continue
        age = now.timestamp() - Path(path).stat().st_mtime
        limit = 36 * 3600 if stream == "historical" else 20 * 60
        if age > limit:
            issues.append(
                {
                    "severity": "high" if stream == "live" else "medium",
                    "kind": "silent_log_stream",
                    "message": f"{stream}.log has been silent for {age / 60:.1f} minutes.",
                    "evidence": {"file": str(path), "age_seconds": age},
                }
            )

    issues.sort(
        key=lambda item: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(
            str(item.get("severity")),
            4,
        )
    )
    return {
        "generated_at": now.isoformat(),
        "since": since,
        "events_scanned": len(events),
        "truncated": truncated,
        "status": "ok" if not issues else "risk",
        "issues": issues,
    }


def _evidence(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": event.get("timestamp"),
        "event": event.get("event"),
        "correlation_id": event.get("correlation_id") or event.get("reference"),
        "file": event.get("source_file"),
        "line": event.get("line_number"),
        "message": event.get("message"),
    }


def print_risks(report: dict[str, Any], *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=True, indent=2, default=str))
        return
    print(f"LOG RISK REVIEW - {report.get('since')}")
    print()
    issues = report.get("issues", [])
    if not issues:
        print("No open logging or pipeline risk was detected.")
        return
    print(f"{'SEVERITY':<10} {'TYPE':<28} DESCRIPTION")
    print("-" * 110)
    for item in issues:
        print(
            f"{str(item.get('severity', '-')).upper():<10} "
            f"{str(item.get('kind', '-')):<28} "
            f"{item.get('message', '-')}"
        )
        evidence = item.get("evidence") or {}
        if evidence:
            print(
                f"{'':<10} {'evidence':<28} "
                f"{evidence.get('timestamp') or '-'} | "
                f"{evidence.get('event') or '-'} | "
                f"{evidence.get('file') or '-'}:{evidence.get('line') or '-'}"
            )


def watch_events(*, poll_seconds: float = 0.5) -> None:
    """Follow all four current logs without holding Windows file handles open."""
    offsets: dict[Path, int] = {}
    try:
        while True:
            batch: list[dict[str, Any]] = []
            for path in _CURRENT.values():
                path = Path(path)
                if not path.exists():
                    continue
                try:
                    size = path.stat().st_size
                    offset = offsets.get(path, size)
                    if size < offset:
                        offset = 0
                    with path.open("r", encoding="utf-8", errors="replace") as handle:
                        handle.seek(offset)
                        for line in handle:
                            event = parse_operator_line(line)
                            if event:
                                batch.append(event)
                        offsets[path] = handle.tell()
                except OSError:
                    continue
            batch.sort(key=lambda item: str(item.get("timestamp", "")))
            for event in batch:
                print(_human_line(event), flush=True)
            time.sleep(max(0.1, float(poll_seconds)))
    except KeyboardInterrupt:
        return


__all__ = [
    "find_events",
    "iter_events",
    "print_events",
    "print_risks",
    "print_status",
    "risk_report",
    "status_report",
    "watch_events",
]
