"""Live-domain telemetry, human-readable reports, and bounded log output."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path

from core_engine.core.live import runtime as _runtime
from core_engine.core.live.runtime import (
    _CANDLE_HEADER_REPEAT_ROWS,
    _candle_table_lock,
    _live_table_file_lock,
)
from core_engine.util.logkit.formatters import clean, operation_line
from core_engine.util.logkit.factory import get_logger
from core_engine.util.logkit.handlers import ResilientRotatingFileHandler
from core_engine.util.logkit.jsonl import append_jsonl_capped
from core_engine.util.logkit.tables import cell as _cell
from core_engine.settings import (
    LIVE_SUMMARY_LOG,
    SYMBOLS,
    TF_DISPLAY_ORDER,
    WS_LIVE_LOG,
    WS_LIVE_REPORT_LOG,
)

TF_ORDER = tuple(TF_DISPLAY_ORDER)
WS_LOG_FILE = str(WS_LIVE_LOG)

logger = get_logger(
    "live_fetching",
    WS_LOG_FILE,
    rotating=True,
    utc=True,
    pipe_format=True,
    normalize_prefixes=True,
)

report_logger = get_logger(
    "live_reports",
    str(WS_LIVE_REPORT_LOG),
    rotating=True,
    console=False,
    utc=True,
    pipe_format=True,
)


def _inline_fields(*details: str, **fields) -> str:
    parts: list[str] = []
    for detail in details:
        text = clean(detail)
        if text and text != "-":
            parts.append(text)
    for key, value in fields.items():
        if value is None or value == "":
            continue
        safe_key = re.sub(r"[^A-Za-z0-9_]+", "_", str(key).strip().lower()).strip("_")
        if safe_key:
            parts.append(f"{safe_key}={clean(value)}")
    return " | ".join(parts) if parts else "-"


def log_live_block(logger: logging.Logger, level: int, text: str) -> None:
    block = str(text).strip("\n")
    if block:
        logger.log(level, "%s", block)


def live_operation_line(event: str, *details: str, **fields) -> str:
    result = fields.pop("result", None)
    result_text = clean(result).upper() if result else "-"
    detail_text = _inline_fields(*details, **fields)
    return " | ".join(
        [
            "[EVENT]",
            _cell(event, 34),
            _cell(result_text, 10),
            detail_text,
        ]
    )


def live_start_block(
    *,
    pid: int,
    utc_started: str,
    local_started: str,
    interval_min: int,
    symbols: int,
    timeframes: int,
    groups: int,
) -> str:
    return "\n".join(
        [
            "",
            "=" * 96,
            "LIVE FETCHING START",
            "=" * 96,
            f"UTC Start       : {utc_started}",
            f"Local Start     : {local_started}",
            f"Process ID      : {pid}",
            "Mode            : live",
            f"Batch Interval  : {interval_min} minute(s)",
            f"Symbols         : {symbols}",
            f"Timeframes      : {timeframes}",
            f"Connection Grps : {groups}",
            "=" * 96,
        ]
    )


def _candle_text(latest_utc: str, first_utc: str | None = None) -> str:
    latest = clean(latest_utc)
    first = clean(first_utc)
    if first and first != "-" and first != latest:
        first_clock = first.replace(" UTC", "")
        return f"{first_clock} -> {latest}"
    return latest


def live_progress_header() -> str:
    return " | ".join(
        [
            _cell("TIME", 8),
            _cell("TYPE", 6),
            _cell("SRC", 3),
            _cell("SYMBOL", 7),
            _cell("TF", 4),
            _cell("ACTION", 8),
            _cell("BARS", 5, align="right"),
            _cell("CANDLE UTC", 20),
            _cell("QUEUE", 5, align="right"),
            _cell("TEMP", 5, align="right"),
            _cell("FACT", 5, align="right"),
            _cell("RESULT", 7),
            "DETAIL",
        ]
    )


def live_progress_rule() -> str:
    return "-" * len(live_progress_header())


def live_candle_section(batch_id: int) -> str:
    return "\n".join(
        [
            "",
            "=" * 96,
            f"CANDLE FLOW - BATCH #{batch_id}",
            "=" * 96,
            live_progress_header(),
            live_progress_rule(),
        ]
    )


def live_candle_header_refresh() -> str:
    return "\n".join(
        [
            "",
            "CANDLE FLOW - CONTINUED",
            live_progress_rule(),
            live_progress_header(),
            live_progress_rule(),
        ]
    )


def live_next_batch_block(*, wait_seconds: int, interval_minutes: int) -> str:
    return "\n".join(
        [
            "",
            "-" * 96,
            "NEXT LIVE BATCH",
            "-" * 96,
            "Status         : waiting",
            f"Wait Time      : {wait_seconds:,} second(s)",
            f"Batch Interval : {interval_minutes:,} minute(s)",
            "-" * 96,
        ]
    )


def _live_progress_row(
    *,
    logged_at: str | None = None,
    source: str,
    symbol: str,
    timeframe: str,
    action: str,
    candles: int,
    latest_utc: str,
    first_utc: str | None = None,
    queue_depth: int | None = None,
    temporary_rows: int | None = None,
    saved_rows: int | None = None,
    result: str = "OK",
    detail: str | None = None,
) -> str:
    detail_text = clean(detail)
    return " | ".join(
        [
            _cell(logged_at, 8),
            _cell("CANDLE", 6),
            _cell(source, 3),
            _cell(symbol, 7),
            _cell(timeframe, 4),
            _cell(action.upper(), 8),
            _cell(f"{int(candles):,}", 5, align="right"),
            _cell(_candle_text(latest_utc, first_utc), 20),
            _cell(None if queue_depth is None else f"{int(queue_depth):,}", 5, align="right"),
            _cell(None if temporary_rows is None else f"{int(temporary_rows):,}", 5, align="right"),
            _cell(None if saved_rows is None else f"{int(saved_rows):,}", 5, align="right"),
            _cell(result.upper(), 7),
            detail_text,
        ]
    )


def live_tv_line(
    *,
    logged_at: str | None = None,
    symbol: str,
    timeframe: str,
    candles: int,
    latest_utc: str,
    first_utc: str | None = None,
    queue_depth: int | None = None,
) -> str:
    return _live_progress_row(
        logged_at=logged_at,
        source="TV",
        symbol=symbol,
        timeframe=timeframe,
        action="received",
        candles=candles,
        first_utc=first_utc,
        latest_utc=latest_utc,
        queue_depth=queue_depth,
    )


def live_db_line(
    *,
    logged_at: str | None = None,
    symbol: str,
    timeframe: str,
    action: str,
    candles: int,
    latest_utc: str,
    first_utc: str | None = None,
    temporary_rows: int | None = None,
    saved_rows: int | None = None,
    result: str = "OK",
    detail: str | None = None,
) -> str:
    return _live_progress_row(
        logged_at=logged_at,
        source="DB",
        symbol=symbol,
        timeframe=timeframe,
        action=action,
        candles=candles,
        first_utc=first_utc,
        latest_utc=latest_utc,
        temporary_rows=temporary_rows,
        saved_rows=saved_rows,
        result=result,
        detail=detail,
    )


class LiveReporter:
    def __init__(
        self,
        logger: logging.Logger,
        symbol_names: Mapping[int, str],
        *,
        width: int = 96,
    ) -> None:
        self.logger = logger
        self.symbol_names = symbol_names
        self.width = width

    def log_block(self, title: str, lines: list[str], level: int = logging.INFO) -> None:
        friendly = self._friendly_title(title)
        log_live_block(self.logger, level, self._summary_block(friendly, lines, level=level))
        self.logger.debug("%s", operation_line("LIVE", f"{friendly} detail", *self._line_details(lines)))

    def format_block(self, title: str, lines: list[str], level: int = logging.INFO) -> str:
        friendly = self._friendly_title(title)
        return self._summary_block(friendly, lines, level=level)

    @staticmethod
    def _friendly_title(title: str) -> str:
        text = clean(title)
        match = re.match(r"WS LIVE GROUP G(\d+) REPORT", text, re.IGNORECASE)
        if match:
            return f"Connection group {match.group(1)} report"
        match = re.match(r"WS LIVE BATCH REPORT #(\d+)", text, re.IGNORECASE)
        if match:
            return f"Batch {match.group(1)} report"
        if text.upper() == "WS LIVE HEALTH REPORT":
            return "Hourly health report"
        return text

    @staticmethod
    def _line_details(lines: list[str]) -> list[str]:
        details: list[str] = []
        used: dict[str, int] = {}
        for raw in lines:
            text = clean(raw)
            if not text or text == "-":
                continue
            if ":" in text:
                key, value = text.split(":", 1)
                safe_key = re.sub(r"[^A-Za-z0-9_]+", "_", key.strip().lower()).strip("_") or "detail"
                used[safe_key] = used.get(safe_key, 0) + 1
                if used[safe_key] > 1:
                    safe_key = f"{safe_key}_{used[safe_key]}"
                value_text = value.strip().replace("|", ";")
                value_text = re.sub(r"\s*;\s*-\s*$", "", value_text).strip()
                details.append(f"{safe_key}={value_text}")
            else:
                details.append(text.replace("|", ";"))
        return details

    @staticmethod
    def _detail_map(lines: list[str]) -> dict[str, str]:
        items: dict[str, str] = {}
        for detail in LiveReporter._line_details(lines):
            if "=" not in detail:
                continue
            key, value = detail.split("=", 1)
            items[key.strip()] = value.strip()
        return items

    @staticmethod
    def _short(value: str | None, *, limit: int = 96) -> str | None:
        text = clean(value)
        if not text or text == "-":
            return None
        if len(text) <= limit:
            return text
        return text[: max(1, limit - 4)].rstrip() + " ..."

    @staticmethod
    def _count_list(value: str | None) -> int:
        text = clean(value)
        if not text or text in {"-", "none"}:
            return 0
        return len([part for part in text.split(";") if part.strip()])

    @staticmethod
    def _match(pattern: str, value: str | None) -> str | None:
        match = re.search(pattern, clean(value), re.IGNORECASE)
        if not match:
            return None
        return match.group(1).replace(",", "")

    @staticmethod
    def _match_int(pattern: str, value: str | None) -> int | None:
        matched = LiveReporter._match(pattern, value)
        if matched is None:
            return None
        try:
            return int(matched)
        except ValueError:
            return None

    @staticmethod
    def _has_issue(value: str | None) -> bool:
        text = clean(value).lower()
        if not text or text in {"-", "none"}:
            return False
        if text in {"everything looks normal.", "everything looks normal", "no action needed"}:
            return False
        if re.match(r"^0\s+(pair\(s\)|pair|pairs|session|sessions|item|items|bar|bars)", text):
            return False
        return True

    @staticmethod
    def _report_result(level: int, missing: str | None = None) -> str:
        if level >= logging.ERROR:
            return "failed"
        if level >= logging.WARNING or LiveReporter._has_issue(missing):
            return "warning"
        return "healthy"

    @staticmethod
    def _kv(label: str, value: str | int | None) -> str:
        text = "-" if value is None or value == "" else str(value)
        return f"{label:<14}: {text}"

    @staticmethod
    def _clean_count_line(value: str | None, *, fallback: str = "-") -> str:
        text = clean(value)
        return fallback if not text or text == "-" else text

    def _summary_block(self, friendly_title: str, lines: list[str], *, level: int) -> str:
        data = self._detail_map(lines)
        if friendly_title.startswith("Connection group "):
            group = self._match(r"Connection group\s+(\d+)", friendly_title) or "?"
            accepted_text = data.get("accepted")
            accepted_bars = self._match_int(r"(\d[\d,]*)\s+bars?", accepted_text) or 0
            missing = self._clean_count_line(data.get("missing"), fallback="none")
            result = self._report_result(level, data.get("missing"))
            changed_count = self._count_list(data.get("changed"))
            missing_count = "0 pairs" if missing.lower() == "none" else missing
            block = [
                "",
                "-" * 96,
                f"GROUP G{group} SUMMARY",
                "-" * 96,
                self._kv("Status", result),
                self._kv("Sessions", data.get("sessions")),
                self._kv("Accepted", f"{accepted_bars:,} closed candles"),
                self._kv("Changed", f"{changed_count:,} symbol/timeframe pairs"),
                self._kv("Missing", missing_count),
                self._kv("By symbol", self._short(data.get("symbols"), limit=130)),
                self._kv("By TF", self._short(data.get("tfs"), limit=130)),
            ]
            backlog = self._short(data.get("backlog"), limit=130)
            if backlog:
                block.append(self._kv("Backlog", backlog))
            block.append(self._kv("Details", self._short(data.get("analysis"), limit=140)))
            block.append("-" * 96)
            return "\n".join(block)

        if friendly_title.startswith("Batch "):
            batch = self._match(r"Batch\s+(\d+)", friendly_title) or "?"
            window = data.get("runtime") or data.get("window")
            accepted = data.get("accepted")
            database = data.get("database")
            buffers = data.get("buffers")
            retry = data.get("retry")
            result = self._report_result(level, retry)
            block = [
                "",
                "=" * 96,
                f"BATCH SUMMARY #{batch}",
                "=" * 96,
                self._kv("Status", result),
                self._kv("Runtime", self._short(window, limit=130)),
                self._kv("Accepted", self._short(accepted, limit=130)),
                self._kv("Database", self._short(database, limit=130)),
                self._kv("Queue", self._short(buffers, limit=130)),
                self._kv("Retry", self._short(retry, limit=130)),
                self._kv("By symbol", self._short(data.get("by_symbol"), limit=130)),
                self._kv("By TF", self._short(data.get("by_tf"), limit=130)),
                self._kv("Top pairs", self._short(data.get("top_pairs"), limit=130)),
            ]
            block.append(self._kv("Details", self._short(data.get("analysis"), limit=140)))
            block.append("=" * 96)
            return "\n".join(block)

        if friendly_title == "Hourly health report":
            issue_text = data.get("issues") or data.get("analysis")
            status_text = clean(data.get("status")).upper()
            if "CRITICAL" in status_text:
                result = "failed"
            elif "WARNING" in status_text:
                result = "warning"
            elif "HEALTHY" in status_text:
                result = "healthy"
            else:
                result = self._report_result(level, issue_text)
            return "\n".join(
                [
                    "",
                    "-" * 96,
                    "LIVE HEALTH REPORT",
                    "-" * 96,
                    self._kv("Result", result),
                    self._kv("Status", self._short(data.get("status"), limit=130)),
                    self._kv("Issues", self._short(issue_text, limit=160)),
                ]
            )

        fields = self._summary_fields(friendly_title, lines, level=level)
        return operation_line("LIVE", friendly_title, **fields)

    def _summary_fields(self, friendly_title: str, lines: list[str], *, level: int) -> dict[str, str | int | None]:
        data = self._detail_map(lines)
        if friendly_title.startswith("Connection group "):
            changed_count = self._count_list(data.get("changed"))
            result = self._report_result(level, data.get("missing"))
            accepted_text = data.get("accepted")
            fields: dict[str, str | int | None] = {
                "sessions": data.get("sessions"),
                "closed_candles": self._match_int(r"(\d[\d,]*)\s+bars?", accepted_text),
                "pairs": self._match_int(r"(\d[\d,]*)\s+pair", accepted_text),
                "changed_pairs": changed_count,
                "missing": self._short(data.get("missing"), limit=70),
                "result": result,
            }
            analysis = self._short(data.get("analysis"), limit=90)
            if analysis and result != "healthy":
                fields["note"] = analysis
            return fields

        if friendly_title.startswith("Batch "):
            window = data.get("window")
            accepted = data.get("accepted")
            database = data.get("database")
            buffers = data.get("buffers")
            retry = data.get("retry")
            result = self._report_result(level, retry)
            fields = {
                "started": self._match(r"started\s+([^;]+)", window),
                "groups": self._match_int(r"groups=(\d+)", window),
                "sessions": self._match(r"sessions=([0-9,]+/[0-9,]+)", window),
                "closed_candles": self._match_int(r"(\d[\d,]*)\s+closed", accepted),
                "db_processed": self._match_int(r"DB processed=(\d[\d,]*)", accepted),
                "pending_db": self._match_int(r"pending=(\d[\d,]*)", accepted),
                "temporary_rows": self._match_int(r"temporary rows=(\d[\d,]*)", database),
                "saved_rows": self._match_int(r"saved rows=(\d[\d,]*)", database),
                "deferred": self._match_int(r"deferred=(\d[\d,]*)", database),
                "write_queue": self._match_int(r"write queue=(\d[\d,]*)", buffers),
                "disk_buffer": self._match(r"disk safety=([^;]+)", buffers),
                "retry_pairs": self._match_int(r"(\d[\d,]*)\s+pair", retry),
                "result": result,
            }
            note = self._short(data.get("analysis"), limit=90)
            if note and result != "healthy":
                fields["note"] = note
            return {
                key: value
                for key, value in fields.items()
                if value is not None and value != ""
            }

        if friendly_title == "Hourly health report":
            issue_text = data.get("issues") or data.get("analysis")
            return {
                "status": self._short(data.get("status"), limit=50),
                "issues": self._short(issue_text, limit=120),
                "result": self._report_result(level, issue_text),
            }

        return {
            f"detail_{idx}": self._short(value, limit=100)
            for idx, value in enumerate(self._line_details(lines)[:6], start=1)
        }

    def pair_label(self, key: tuple[int, str]) -> str:
        sid, tf_code = key
        return f"{self.symbol_names.get(sid, str(sid))}/{tf_code}"

    @staticmethod
    def count_items(items: list[tuple[str, int]], *, limit: int = 12) -> str:
        if not items:
            return "-"
        trimmed = items[:limit]
        text = "  ".join(f"{name}:{value:,}" for name, value in trimmed)
        if len(items) > limit:
            text += f"  ... +{len(items) - limit} more"
        return text

    def pair_counts(self, pair_counts: dict[tuple[int, str], int], *, limit: int = 12) -> str:
        items = [
            (self.pair_label(key), int(value))
            for key, value in pair_counts.items()
            if int(value or 0) > 0
        ]
        items.sort(key=lambda item: (-item[1], item[0]))
        return self.count_items(items, limit=limit)

    def counts_by_symbol(
        self,
        pair_counts: dict[tuple[int, str], int],
        *,
        limit: int = 12,
    ) -> str:
        totals: dict[str, int] = {}
        for (sid, _tf), value in pair_counts.items():
            if int(value or 0) <= 0:
                continue
            name = self.symbol_names.get(sid, str(sid))
            totals[name] = totals.get(name, 0) + int(value)
        items = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
        return self.count_items(items, limit=limit)

    def counts_by_tf(
        self,
        pair_counts: dict[tuple[int, str], int],
        *,
        limit: int = 15,
    ) -> str:
        totals: dict[str, int] = {}
        for (_sid, tf_code), value in pair_counts.items():
            if int(value or 0) <= 0:
                continue
            totals[tf_code] = totals.get(tf_code, 0) + int(value)
        items = [(tf, totals[tf]) for tf in TF_ORDER if tf in totals]
        items.extend(sorted((tf, cnt) for tf, cnt in totals.items() if tf not in TF_ORDER))
        return self.count_items(items, limit=limit)

    def backlog(self, backlog: dict[tuple[int, str], int], *, limit: int = 12) -> str:
        items = [(self.pair_label(key), int(count)) for key, count in backlog.items()]
        items.sort(key=lambda item: (-item[1], item[0]))
        return self.count_items(items, limit=limit)

    def classify_health(
        self,
        *,
        recent_errors: int,
        total_errors: int,
        last_hour_accepted: int,
        last_hour_saved: int,
        stale_count: int,
        source_lag_count: int,
        source_lag_entries: list[tuple[float, int, str, float]],
        spool_count: int,
        recent_ws_errors: int,
        total_ws_errors: int,
        n_miss_active: int,
        is_guest: bool,
        consecutive_guest_batches: int,
    ) -> tuple[str, str, list[str]]:
        has_recent_data_flow = last_hour_accepted > 0 and last_hour_saved > 0
        source_lag_is_actionable = source_lag_count > 0 and not has_recent_data_flow

        critical_stale_without_flow = stale_count > 15 and not has_recent_data_flow
        if (
            recent_errors > 0
            or critical_stale_without_flow
            or (source_lag_count > 15 and not has_recent_data_flow)
            or spool_count > 0
            or recent_ws_errors > 5
        ):
            health_status = "CRITICAL"
            notify_level = "ERROR"
        elif n_miss_active > 0 or stale_count > 0 or source_lag_is_actionable or is_guest or recent_ws_errors > 0:
            health_status = "WARNING"
            notify_level = "WARNING"
        else:
            health_status = "HEALTHY"
            notify_level = "SUCCESS"

        issues: list[str] = []
        if recent_errors:
            issues.append(f"{recent_errors} database write errors happened in the last status window")
        if recent_ws_errors:
            issues.append(f"{recent_ws_errors} TradingView WebSocket errors happened in the last status window")
        if spool_count:
            issues.append(
                f"Temporary buffer has {spool_count} bars waiting - database writes are slow"
            )
        if stale_count > 3:
            issues.append(f"{stale_count} pairs have outdated data")
        if source_lag_is_actionable:
            worst_src = source_lag_entries[0]
            issues.append(
                f"{source_lag_count} live feeds are behind "
                f"(worst: {self.symbol_names.get(worst_src[1], worst_src[1])}/{worst_src[2]} {worst_src[0]:.1f}h)"
            )
        elif source_lag_count:
            worst_src = source_lag_entries[0]
            issues.append(
                f"{source_lag_count} feed timing note(s); data still flowed in the last hour "
                f"(worst: {self.symbol_names.get(worst_src[1], worst_src[1])}/{worst_src[2]} {worst_src[0]:.1f}h)"
            )
        if n_miss_active:
            issues.append(f"{n_miss_active} pairs are missing data")
        if is_guest:
            issues.append(
                f"Using a limited TradingView session for {consecutive_guest_batches} batches in a row"
            )
        elif stale_count:
            issues.append(f"{stale_count} pairs are running behind")
        if total_errors and not recent_errors:
            issues.append(
                f"Previous database write errors exist earlier in this run ({total_errors}); "
                "no new database write error in this window"
            )
        if total_ws_errors and not recent_ws_errors:
            issues.append(
                f"Previous TradingView WebSocket errors exist earlier in this run ({total_ws_errors}); "
                "no new WebSocket error in this window"
            )
        return health_status, notify_level, issues


_symbol_name_by_id = {symbol["symbol_id"]: symbol["tv_symbol"] for symbol in SYMBOLS}
reporter = LiveReporter(logger, _symbol_name_by_id)
_report_file_reporter = LiveReporter(report_logger, _symbol_name_by_id)


def operation_line(event: str, *details: str, **fields) -> str:
    return live_operation_line(event, *details, **fields)


def log_report_block(title: str, lines: list[str], level: int = logging.INFO) -> None:
    append_live_table_text(reporter.format_block(title, lines, level=level))
    _report_file_reporter.log_block(title, lines, level)


def live_log_rotating_handler() -> logging.Handler | None:
    for handler in logger.handlers:
        if isinstance(handler, ResilientRotatingFileHandler):
            return handler
    return None


def maybe_rotate_live_log() -> None:
    """Rotate after raw table appends that bypass the logging handler."""

    handler = live_log_rotating_handler()
    if handler is None:
        return
    try:
        if handler.maxBytes > 0 and os.path.getsize(WS_LOG_FILE) >= handler.maxBytes:
            handler.acquire()
            try:
                handler.doRollover()
            finally:
                handler.release()
    except Exception:
        pass


def append_live_table_text(text: str) -> None:
    block = str(text).strip("\n")
    if not block:
        return
    try:
        print(block, flush=True)
    except Exception:
        pass
    try:
        path = Path(WS_LOG_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _live_table_file_lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(block + "\n")
            maybe_rotate_live_log()
    except Exception as exc:
        logger.warning("Could not write live table log: %s", exc)


def log_block(level: int, text: str) -> None:
    log_live_block(logger, level, text)


def log_report_text(level: int, text: str) -> None:
    log_live_block(report_logger, level, text)


def start_candle_table(batch_id: int) -> None:
    with _candle_table_lock:
        _runtime._candle_table_rows = 0
    append_live_table_text(live_candle_section(batch_id))


def log_candle_row(line: str) -> None:
    with _candle_table_lock:
        if (
            _runtime._candle_table_rows > 0
            and _runtime._candle_table_rows % _CANDLE_HEADER_REPEAT_ROWS == 0
        ):
            append_live_table_text(live_candle_header_refresh())
        _runtime._candle_table_rows += 1
    append_live_table_text(line)


def write_live_summary(row: dict) -> None:
    try:
        append_jsonl_capped(LIVE_SUMMARY_LOG, row)
    except Exception as exc:
        logger.warning("Could not write live batch summary: %s", exc)


format_pair_label = reporter.pair_label
summarize_pair_counts = reporter.pair_counts
summarize_counts_by_symbol = reporter.counts_by_symbol
summarize_counts_by_tf = reporter.counts_by_tf
summarize_backlog = reporter.backlog
