"""Structured audit logging for OG Live mechanisms."""

from __future__ import annotations

from collections import OrderedDict, defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
import json
import logging
import threading
from pathlib import Path
from typing import Any

from og_live.common import settings as common_settings

logger = logging.getLogger(__name__)

DEFAULT_AUDIT_MAX_BYTES = 20 * 1024 * 1024
DEFAULT_AUDIT_BACKUP_COUNT = 5

TABLE_COLUMNS = (
    ("time", 19),
    ("mechanism", 8),
    ("stage", 18),
    ("status", 12),
    ("pair", 12),
    ("strategy", 10),
    ("signals", 7),
    ("snapshot", 34),
    ("detail", 42),
)


class AuditLogger:
    """Best-effort JSONL audit writer that never interrupts live processing."""

    def __init__(
        self,
        *,
        mechanism: str,
        path: Path,
        enabled: bool = True,
        max_bytes: int = DEFAULT_AUDIT_MAX_BYTES,
        backup_count: int = DEFAULT_AUDIT_BACKUP_COUNT,
    ) -> None:
        self.mechanism = mechanism
        self.path = path
        self.enabled = enabled
        self.max_bytes = max(1024 * 1024, int(max_bytes))
        self.backup_count = max(1, int(backup_count))
        self._lock = threading.Lock()

    def write(self, stage: str, *, status: str = "ok", **fields: Any) -> None:
        """Append one structured audit event."""
        if not self.enabled:
            return

        event: OrderedDict[str, Any] = OrderedDict()
        event["ts_utc"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        event["mechanism"] = self.mechanism
        event["stage"] = str(stage)
        event["status"] = str(status)
        for key, value in fields.items():
            if value is not None:
                event[str(key)] = _json_safe(value)

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
            with self._lock:
                self._rotate_if_needed(len(line) + 1)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
                    handle.write("\n")
        except OSError as exc:
            logger.warning("audit: failed to write %s: %s", self.path, exc)

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        if not self.path.exists():
            return
        try:
            if self.path.stat().st_size + incoming_bytes <= self.max_bytes:
                return
        except OSError:
            return

        for idx in range(self.backup_count - 1, 0, -1):
            older = self.path.with_name(f"{self.path.name}.{idx}")
            newer = self.path.with_name(f"{self.path.name}.{idx + 1}")
            if older.exists():
                try:
                    if newer.exists():
                        newer.unlink()
                    older.rename(newer)
                except OSError:
                    logger.warning("audit: failed rotating %s", older)
        first_backup = self.path.with_name(f"{self.path.name}.1")
        try:
            if first_backup.exists():
                first_backup.unlink()
            self.path.rename(first_backup)
        except OSError as exc:
            logger.warning("audit: failed rotating %s: %s", self.path, exc)


def audit_log_path(filename: str | Path) -> Path:
    """Return an absolute audit log path under runtime/logs unless already absolute."""
    path = Path(filename)
    if path.is_absolute():
        return path
    return common_settings.REPO_ROOT / "runtime" / "logs" / path


def elapsed_ms(start_monotonic: float, end_monotonic: float) -> int:
    """Return elapsed milliseconds from two monotonic readings."""
    return max(0, int(round((end_monotonic - start_monotonic) * 1000)))


def read_events(
    path: Path,
    *,
    limit: int = 50,
    strategy: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    snapshot_version: str | None = None,
    stages: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Read matching audit events from one JSONL file, newest first."""
    if not path.exists():
        return []

    wanted_stages = {stage.strip() for stage in stages or () if str(stage).strip()}
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        logger.warning("audit: failed reading %s: %s", path, exc)
        return []

    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if strategy and str(event.get("strategy", "")).lower() != strategy.lower():
            continue
        if symbol and str(event.get("symbol", "")).upper() != symbol.upper():
            continue
        if timeframe and str(event.get("timeframe", "")).upper() != timeframe.upper():
            continue
        if snapshot_version and str(event.get("snapshot_version", "")) != snapshot_version:
            continue
        if wanted_stages and str(event.get("stage", "")) not in wanted_stages:
            continue
        rows.append(event)
        if len(rows) >= limit:
            break
    return rows


def print_audit_table(
    *,
    title: str,
    path: Path,
    events: list[dict[str, Any]],
    filters: dict[str, str | None],
) -> None:
    """Print a compact operator-friendly audit table."""
    print(title)
    print(f"File: {path}")
    _print_filter_line(filters)
    print()
    if not events:
        print("No audit events matched the current filter.")
        return

    _print_row([name.upper() for name, _width in TABLE_COLUMNS])
    _print_separator()
    for event in events:
        _print_row(_event_cells(event))


def print_comparison_table(
    *,
    stream_path: Path,
    pubsub_path: Path,
    stream_events: list[dict[str, Any]],
    pubsub_events: list[dict[str, Any]],
    filters: dict[str, str | None],
    limit: int,
) -> None:
    """Print a side-by-side Stream/PubSub comparison by snapshot."""
    stream_rows = _group_by_snapshot(stream_events)
    pubsub_rows = _group_by_snapshot(pubsub_events)
    keys = sorted(set(stream_rows) | set(pubsub_rows), key=lambda value: value[-1], reverse=True)[:limit]

    print("OG Live Audit Compare - Stream vs Pub/Sub")
    print(f"Stream log: {stream_path}")
    print(f"Pub/Sub log: {pubsub_path}")
    _print_filter_line(filters)
    print()
    if not keys:
        print("No matching audit events found in either mechanism.")
        return

    headers = ("PAIR", "SNAPSHOT", "STREAM", "PUB/SUB", "RESULT")
    widths = (13, 34, 30, 30, 10)
    print("  ".join(_clip(header, width).ljust(width) for header, width in zip(headers, widths, strict=True)))
    print("  ".join("-" * width for width in widths))
    for key in keys:
        stream_summary = _snapshot_summary(stream_rows.get(key, []))
        pubsub_summary = _snapshot_summary(pubsub_rows.get(key, []))
        result = "MATCH" if stream_summary == pubsub_summary else "CHECK"
        cells = (
            f"{key[1]} {key[2]}",
            key[3],
            stream_summary,
            pubsub_summary,
            result,
        )
        print("  ".join(_clip(cell, width).ljust(width) for cell, width in zip(cells, widths, strict=True)))


def _group_by_snapshot(events: list[dict[str, Any]]) -> dict[tuple[str, str, str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        strategy = str(event.get("strategy") or "")
        symbol = str(event.get("symbol") or "")
        timeframe = str(event.get("timeframe") or "")
        snapshot_version = str(event.get("snapshot_version") or "")
        if strategy and symbol and timeframe and snapshot_version:
            grouped[(strategy, symbol, timeframe, snapshot_version)].append(event)
    return grouped


def _snapshot_summary(events: list[dict[str, Any]]) -> str:
    if not events:
        return "missing"
    published = sum(1 for event in events if event.get("stage") == "signal_published")
    queued = sum(1 for event in events if event.get("stage") == "signal_queued")
    generated = _latest_int(events, "signals_generated")
    processed = any(event.get("stage") == "snapshot_processed" for event in events)
    skipped = next((event for event in events if str(event.get("stage", "")).endswith("skipped")), None)
    if published:
        return f"published={published}"
    if queued:
        return f"queued={queued}"
    if processed:
        return f"processed signals={generated}"
    if skipped:
        return f"skipped {skipped.get('status', '')}".strip()
    return str(events[0].get("stage", "seen"))


def _latest_int(events: list[dict[str, Any]], field: str) -> int:
    for event in events:
        value = event.get(field)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _event_cells(event: dict[str, Any]) -> list[str]:
    return [
        _short_time(event.get("ts_utc")),
        str(event.get("mechanism", "")),
        str(event.get("stage", "")),
        str(event.get("status", "")),
        _pair(event),
        str(event.get("strategy", "")),
        _signals(event),
        str(event.get("snapshot_version", "")),
        _detail(event),
    ]


def _print_filter_line(filters: dict[str, str | None]) -> None:
    active = [f"{key}={value}" for key, value in filters.items() if value]
    print("Filter: " + (", ".join(active) if active else "none"))


def _print_row(values: list[str]) -> None:
    cells = []
    for value, (_name, width) in zip(values, TABLE_COLUMNS, strict=True):
        cells.append(_clip(value, width).ljust(width))
    print("  ".join(cells))


def _print_separator() -> None:
    print("  ".join("-" * width for _name, width in TABLE_COLUMNS))


def _short_time(value: object) -> str:
    text = str(value or "")
    return text.replace("T", " ")[:19]


def _pair(event: dict[str, Any]) -> str:
    symbol = str(event.get("symbol") or "")
    timeframe = str(event.get("timeframe") or "")
    return f"{symbol} {timeframe}".strip()


def _signals(event: dict[str, Any]) -> str:
    if "signals_published" in event or "signals_generated" in event:
        generated = event.get("signals_generated", 0)
        published = event.get("signals_published", event.get("signals_delivered", 0))
        queued = event.get("signals_queued", 0)
        return f"g{generated}/p{published}/q{queued}"
    side = event.get("side")
    return str(side or "")


def _detail(event: dict[str, Any]) -> str:
    for key in ("message", "reason", "output_key", "source", "output_entry_id", "state_key"):
        value = event.get(key)
        if value:
            return str(value)
    return ""


def _clip(value: object, width: int) -> str:
    text = str(value or "")
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(item) for item in value]
    return str(value)
