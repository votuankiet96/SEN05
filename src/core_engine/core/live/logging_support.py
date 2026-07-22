"""Live-engine logging, report formatting and capped summary output."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from core_engine.core.live import state as _state
from core_engine.core.live.reporter import (
    LiveReporter,
    live_candle_header_refresh,
    live_candle_section,
    live_operation_line,
    log_live_block,
)
from core_engine.core.live.state import (
    _CANDLE_HEADER_REPEAT_ROWS,
    _candle_table_lock,
    _live_table_file_lock,
)
from core_engine.settings import LIVE_SUMMARY_LOG, SYMBOLS, WS_LIVE_LOG, WS_LIVE_REPORT_LOG
from core_engine.util.logkit.factory import get_logger
from core_engine.util.logkit.handlers import ResilientRotatingFileHandler
from core_engine.util.logkit.jsonl import append_jsonl_capped


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
        _state._candle_table_rows = 0
    append_live_table_text(live_candle_section(batch_id))


def log_candle_row(line: str) -> None:
    with _candle_table_lock:
        if _state._candle_table_rows > 0 and _state._candle_table_rows % _CANDLE_HEADER_REPEAT_ROWS == 0:
            append_live_table_text(live_candle_header_refresh())
        _state._candle_table_rows += 1
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
