"""One-shot cTrader jobs: account-list, symbol-sync and historical backfill."""

from __future__ import annotations

import logging
import json
import os
import re
import subprocess
import sys
import socket
from collections import defaultdict
from collections import deque
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from statistics import median
from pathlib import Path
from typing import Any, Callable, Iterable

from tick_engine.env_safety import child_env
from tick_engine.data_storage.spool import TickBatcher, TickSpool
from tick_engine.data_storage.store_sql import TickSqlStore
from tick_engine.data_storage.symbols import RemoteSymbol, TargetSymbol, build_symbol_matches
from tick_engine.data_storage.ticks import (
    DecodedHistoricalTick,
    TickRecord,
    decode_delta_ticks,
    iter_tick_windows,
    millis_from_utc,
    utc_from_millis,
)
from tick_engine.reporting.notifications import flush_notifications, notify_tick_report
from tick_engine.reporting.system_log import write_system_event
from tick_engine.utils_support.runtime import (
    TickRuntimeSettings,
    ensure_fresh_access_token,
    extract_payload,
    load_ctrader_sdk,
    make_application_auth_req,
    make_get_account_list_req,
    make_get_tick_data_req,
    make_symbols_list_req,
    new_client,
    remote_symbol_from_proto,
    send_auth_chain,
    stop_reactor,
)
from tick_engine.utils_support.lock_coord import (
    CANCEL_ENV,
    CancelRequested,
    cancel_file_for,
    cancel_requested,
    clear_cancel_file,
    raise_if_cancelled,
)

logger = logging.getLogger(__name__)

MS_PER_SECOND = 1000
MS_PER_MINUTE = 60 * MS_PER_SECOND
QUOTE_OUTLIER_RATIO = Decimal("5")
QUOTE_MAX_SPREAD_BPS = Decimal("1000")
_MANUAL_AREA_WIDTH = 12
_MANUAL_ITEM_WIDTH = 26
_CHILD_LOG_PREFIX_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\s+\|\s+[A-Z]{4,8}\s+\|\s+"
)


def manual_line(area: str, item: str, detail: str = "") -> str:
    return f"{area:<{_MANUAL_AREA_WIDTH}} | {item:<{_MANUAL_ITEM_WIDTH}} | {detail}"


def _clean_child_output_line(line: str) -> str:
    return _CHILD_LOG_PREFIX_RE.sub("", line.rstrip("\r\n"))


def _utc_naive_iso_from_ms(value: int) -> str:
    return utc_from_millis(value).astimezone(timezone.utc).replace(tzinfo=None).isoformat()


def _failure_summary(failure: Any) -> str:
    value = getattr(failure, "value", None)
    if value is not None:
        return f"{value.__class__.__name__}: {value}"
    if isinstance(failure, BaseException):
        return f"{failure.__class__.__name__}: {failure}"
    return str(failure)


_NON_RETRYABLE_AUTH_MARKERS = (
    "RET_ACCOUNT_DISABLED",
    "RET_ACCOUNT_NOT_FOUND",
    "RET_ACCOUNT_NOT_AUTHORIZED",
    "RET_INVALID_ACCOUNT",
    "cTrader account auth rejected",
)


def _non_retryable_auth_reason(text: str) -> str | None:
    for marker in _NON_RETRYABLE_AUTH_MARKERS:
        if marker in text:
            return marker
    return None


def _backfill_failure_log_level() -> str:
    level = os.environ.get("TICK_ENGINE_BACKFILL_FAILURE_LEVEL", "ERROR").strip().upper()
    return level if level in {"WARNING", "ERROR"} else "ERROR"


def _with_response_timeout(
    settings: TickRuntimeSettings,
    request_timeout_seconds: float | None,
) -> TickRuntimeSettings:
    if request_timeout_seconds is None:
        if settings.response_timeout_seconds <= 0:
            raise ValueError("CTRADER_FTMO_TICK_RESPONSE_TIMEOUT_SECONDS must be greater than 0")
        return settings
    if request_timeout_seconds <= 0:
        raise ValueError("request timeout must be greater than 0")
    return replace(settings, response_timeout_seconds=float(request_timeout_seconds))


def _fmt_history_time(timestamp_ms: int | None) -> str:
    if timestamp_ms is None:
        return "-"
    return utc_from_millis(timestamp_ms).strftime("%Y-%m-%d %H:%M:%SZ")


def _filter_side_outliers(
    ticks: list[DecodedHistoricalTick],
) -> tuple[list[DecodedHistoricalTick], int]:
    if len(ticks) < 5:
        return ticks, 0
    raw_prices = [int(tick.raw_price) for tick in ticks if int(tick.raw_price) > 0]
    if len(raw_prices) < 5:
        return ticks, 0
    median_raw = Decimal(int(median(raw_prices)))
    if median_raw <= 0:
        return ticks, 0
    lower = median_raw / QUOTE_OUTLIER_RATIO
    upper = median_raw * QUOTE_OUTLIER_RATIO
    kept: list[DecodedHistoricalTick] = []
    dropped = 0
    for tick in ticks:
        price = Decimal(int(tick.raw_price))
        if price < lower or price > upper:
            dropped += 1
            continue
        kept.append(tick)
    return kept, dropped


def _quote_spread_bps(record: TickRecord) -> Decimal | None:
    if record.bid is None or record.ask is None:
        return None
    mid = (record.bid + record.ask) / Decimal("2")
    if mid <= 0:
        return None
    return ((record.ask - record.bid) / mid) * Decimal("10000")


def _next_history_page_to_timestamp(
    *,
    symbol: str,
    quote_type: str,
    from_timestamp_ms: int,
    current_to_timestamp_ms: int,
    page_ticks: list[DecodedHistoricalTick],
    unique_page_tick_count: int,
    has_more: bool,
) -> int | None:
    if not has_more:
        return None
    if not page_ticks:
        raise RuntimeError(
            f"cTrader returned hasMore=true with an empty {quote_type} page for {symbol}"
        )
    oldest_ms = min(tick.timestamp_ms for tick in page_ticks)
    if oldest_ms <= from_timestamp_ms:
        raise RuntimeError(
            f"cTrader {quote_type} pagination is capped at the lower boundary "
            f"for {symbol}; refusing partial history"
        )
    if oldest_ms >= current_to_timestamp_ms or unique_page_tick_count <= 0:
        raise RuntimeError(
            f"cTrader {quote_type} pagination made no backward progress "
            f"for {symbol}; refusing partial history"
        )
    return oldest_ms


def merge_historical_quote_ticks(
    target: TargetSymbol,
    remote: RemoteSymbol,
    bid_ticks: list[DecodedHistoricalTick],
    ask_ticks: list[DecodedHistoricalTick],
    *,
    ingest_run_id: str,
    max_side_age_seconds: int = 900,
) -> tuple[list[TickRecord], QuoteMergeStats]:
    """Merge cTrader BID/ASK history streams into full quote rows."""
    original_bid_count = len(bid_ticks)
    original_ask_count = len(ask_ticks)
    bid_ticks, dropped_bid_outliers = _filter_side_outliers(bid_ticks)
    ask_ticks, dropped_ask_outliers = _filter_side_outliers(ask_ticks)

    by_timestamp: dict[int, dict[str, list[int]]] = defaultdict(
        lambda: {"bid_raw": [], "ask_raw": []}
    )
    for tick in sorted(reversed(bid_ticks), key=lambda item: item.timestamp_ms):
        by_timestamp[int(tick.timestamp_ms)]["bid_raw"].append(int(tick.raw_price))
    for tick in sorted(reversed(ask_ticks), key=lambda item: item.timestamp_ms):
        by_timestamp[int(tick.timestamp_ms)]["ask_raw"].append(int(tick.raw_price))

    records: list[TickRecord] = []
    last_bid_raw: int | None = None
    last_ask_raw: int | None = None
    last_bid_timestamp_ms: int | None = None
    last_ask_timestamp_ms: int | None = None
    dropped_unseeded = 0
    dropped_stale_side = 0
    dropped_crossed = 0
    dropped_wide_spread = 0
    dropped_duplicate_quote = 0
    seen_event_hashes: set[bytes] = set()
    max_side_age_ms = max(0, int(max_side_age_seconds)) * 1000

    for timestamp_ms in sorted(by_timestamp):
        sides = by_timestamp[timestamp_ms]
        bid_values = sides["bid_raw"]
        ask_values = sides["ask_raw"]
        for event_index in range(max(len(bid_values), len(ask_values))):
            bid_updated = event_index < len(bid_values)
            ask_updated = event_index < len(ask_values)
            if bid_updated:
                last_bid_raw = bid_values[event_index]
                last_bid_timestamp_ms = timestamp_ms
            if ask_updated:
                last_ask_raw = ask_values[event_index]
                last_ask_timestamp_ms = timestamp_ms
            if last_bid_raw is None or last_ask_raw is None:
                dropped_unseeded += 1
                continue
            if (
                last_bid_timestamp_ms is None
                or last_ask_timestamp_ms is None
                or timestamp_ms - last_bid_timestamp_ms > max_side_age_ms
                or timestamp_ms - last_ask_timestamp_ms > max_side_age_ms
            ):
                dropped_stale_side += 1
                continue

            record = TickRecord.from_historical_quote(
                target,
                remote,
                timestamp_ms,
                last_bid_raw,
                last_ask_raw,
                bid_updated=bid_updated,
                ask_updated=ask_updated,
                ingest_run_id=ingest_run_id,
            )
            if record.bid is None or record.ask is None:
                dropped_unseeded += 1
                continue
            if record.ask < record.bid:
                dropped_crossed += 1
                continue
            spread_bps = _quote_spread_bps(record)
            if spread_bps is not None and spread_bps > QUOTE_MAX_SPREAD_BPS:
                dropped_wide_spread += 1
                continue
            event_hash = bytes(record.event_hash or b"")
            if event_hash in seen_event_hashes:
                dropped_duplicate_quote += 1
                continue
            seen_event_hashes.add(event_hash)
            records.append(record)

    return records, QuoteMergeStats(
        bid_ticks=original_bid_count,
        ask_ticks=original_ask_count,
        dropped_bid_outliers=dropped_bid_outliers,
        dropped_ask_outliers=dropped_ask_outliers,
        dropped_unseeded=dropped_unseeded,
        dropped_stale_side=dropped_stale_side,
        dropped_crossed=dropped_crossed,
        dropped_wide_spread=dropped_wide_spread,
        dropped_duplicate_quote=dropped_duplicate_quote,
    )


@dataclass(frozen=True)
class HistoryRequest:
    target: TargetSymbol
    remote: RemoteSymbol
    quote_type: str
    from_timestamp_ms: int
    to_timestamp_ms: int


@dataclass(frozen=True)
class HistoryWindowRequest:
    target: TargetSymbol
    remote: RemoteSymbol
    from_timestamp_ms: int
    to_timestamp_ms: int


@dataclass(frozen=True)
class QuoteMergeStats:
    bid_ticks: int
    ask_ticks: int
    dropped_bid_outliers: int
    dropped_ask_outliers: int
    dropped_unseeded: int
    dropped_stale_side: int
    dropped_crossed: int
    dropped_wide_spread: int
    dropped_duplicate_quote: int

    @property
    def dropped_total(self) -> int:
        return (
            self.dropped_bid_outliers
            + self.dropped_ask_outliers
            + self.dropped_unseeded
            + self.dropped_stale_side
            + self.dropped_crossed
            + self.dropped_wide_spread
            + self.dropped_duplicate_quote
        )


@dataclass(frozen=True)
class BackfillBatch:
    index: int
    total: int
    cursor_from_ms: int
    request_from_ms: int
    to_ms: int

    @property
    def cursor_from_utc(self) -> str:
        return _fmt_ms(self.cursor_from_ms)

    @property
    def request_from_utc(self) -> str:
        return _fmt_ms(self.request_from_ms)

    @property
    def to_utc(self) -> str:
        return _fmt_ms(self.to_ms)


def _fmt_ms(value: int) -> str:
    dt = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def iter_backfill_batches(
    from_ms: int,
    to_ms: int,
    *,
    batch_minutes: int,
    overlap_seconds: int,
) -> list[BackfillBatch]:
    """Split a requested range into short overlapping windows."""
    from_ms = int(from_ms)
    to_ms = int(to_ms)
    if from_ms > to_ms:
        raise ValueError("from_ms must be <= to_ms")
    if int(batch_minutes) <= 0:
        raise ValueError("batch_minutes must be greater than 0")
    if int(overlap_seconds) < 0:
        raise ValueError("overlap_seconds must be >= 0")

    batch_ms = int(batch_minutes) * MS_PER_MINUTE
    overlap_ms = int(overlap_seconds) * MS_PER_SECOND
    raw: list[tuple[int, int, int]] = []
    cursor = from_ms
    while True:
        end_ms = min(to_ms, cursor + batch_ms)
        request_from = max(from_ms, cursor - overlap_ms) if raw else from_ms
        raw.append((cursor, request_from, end_ms))
        if end_ms >= to_ms:
            break
        cursor = end_ms

    total = len(raw)
    return [
        BackfillBatch(
            index=index,
            total=total,
            cursor_from_ms=cursor_from,
            request_from_ms=request_from,
            to_ms=end_ms,
        )
        for index, (cursor_from, request_from, end_ms) in enumerate(raw, start=1)
    ]


def default_progress_path(from_ms: int, to_ms: int) -> Path:
    from tick_engine.settings import RUN_DIR

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return RUN_DIR / "backfill_batches" / f"manual_backfill_{stamp}_{from_ms}_{to_ms}.json"


def _write_progress(progress_path: Path, state: dict[str, object]) -> None:
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at_utc"] = _utc_now_text()
    progress_path.write_text(
        json.dumps(state, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _command_for_batch(
    batch: BackfillBatch,
    *,
    symbols: list[str] | None,
    request_timeout: float | None,
    timeout_per_batch: int | None,
    wait_lock_seconds: int,
    notify_per_batch: bool,
) -> list[str]:
    cmd = [
        sys.executable,
        "-u",
        "-B",
        "-m",
        "tick_engine",
        "backfill",
        "--from",
        str(batch.request_from_ms),
        "--to",
        str(batch.to_ms),
        "--wait-lock-seconds",
        str(max(0, int(wait_lock_seconds))),
    ]
    if symbols:
        cmd.append("--symbols")
        cmd.extend(symbols)
    if request_timeout is not None:
        cmd.extend(["--request-timeout", str(float(request_timeout))])
    if timeout_per_batch is not None:
        cmd.extend(["--timeout", str(int(timeout_per_batch))])
    if not notify_per_batch:
        cmd.append("--no-notify")
    return cmd


def _run_batch_command(cmd: list[str]) -> tuple[int, str | None]:
    """Run one child backfill and stream its output through the parent process."""
    proc = subprocess.Popen(
        cmd,
        cwd=str(Path(__file__).resolve().parents[1]),
        env=child_env({
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "TICK_ENGINE_BACKFILL_FAILURE_LEVEL": "WARNING",
        }),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    fatal_auth_reason: str | None = None
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            clean_line = _clean_child_output_line(line)
            if clean_line:
                fatal_auth_reason = fatal_auth_reason or _non_retryable_auth_reason(clean_line)
                print(clean_line, flush=True)
        return int(proc.wait()), fatal_auth_reason
    except BaseException:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        raise


def run_batched_backfill(
    *,
    from_ms: int,
    to_ms: int,
    symbols: list[str] | None = None,
    batch_minutes: int = 60,
    overlap_seconds: int = 60,
    wait_lock_seconds: int = 300,
    request_timeout: float | None = None,
    timeout_per_batch: int | None = None,
    sleep_seconds: float = 0.0,
    max_attempts: int = 3,
    retry_sleep_seconds: float = 10.0,
    retry_sleep_max_seconds: float = 90.0,
    notify_per_batch: bool = False,
    notify_summary: bool = False,
    notify_success_summary: bool = True,
    progress_path: Path | None = None,
    dry_run: bool = False,
) -> int:
    """Run a manual backfill as short subprocess-backed batches."""
    if wait_lock_seconds < 0:
        raise ValueError("wait_lock_seconds must be >= 0")
    if timeout_per_batch is not None and timeout_per_batch <= 0:
        raise ValueError("timeout_per_batch must be greater than 0")
    if sleep_seconds < 0:
        raise ValueError("sleep_seconds must be >= 0")
    if max_attempts <= 0:
        raise ValueError("max_attempts must be greater than 0")
    if retry_sleep_seconds < 0:
        raise ValueError("retry_sleep_seconds must be >= 0")
    if retry_sleep_max_seconds < 0:
        raise ValueError("retry_sleep_max_seconds must be >= 0")

    batches = iter_backfill_batches(
        from_ms,
        to_ms,
        batch_minutes=batch_minutes,
        overlap_seconds=overlap_seconds,
    )
    if not os.environ.get(CANCEL_ENV):
        cancel_file = cancel_file_for("manual-backfill")
        clear_cancel_file(cancel_file)
        os.environ[CANCEL_ENV] = str(cancel_file)
    else:
        cancel_file = Path(os.environ[CANCEL_ENV])
    progress_path = progress_path or default_progress_path(from_ms, to_ms)
    state: dict[str, object] = {
        "status": "DRY_RUN" if dry_run else "RUNNING",
        "created_at_utc": _utc_now_text(),
        "host": socket.gethostname(),
        "process_id": os.getpid(),
        "cancel_file": str(cancel_file),
        "from_ms": int(from_ms),
        "from_utc": _fmt_ms(from_ms),
        "to_ms": int(to_ms),
        "to_utc": _fmt_ms(to_ms),
        "symbols": sorted(symbols or []),
        "batch_minutes": int(batch_minutes),
        "overlap_seconds": int(overlap_seconds),
        "wait_lock_seconds": int(wait_lock_seconds),
        "max_attempts": int(max_attempts),
        "retry_sleep_seconds": float(retry_sleep_seconds),
        "retry_sleep_max_seconds": float(retry_sleep_max_seconds),
        "notify_summary": bool(notify_summary),
        "notify_success_summary": bool(notify_success_summary),
        "progress_path": str(progress_path),
        "batches": [],
    }

    print("")
    print(manual_line("Manual", "progress file", str(progress_path)))
    print(
        manual_line(
            "Manual",
            "plan",
            f"batches={len(batches)} | window={_fmt_ms(from_ms)} -> {_fmt_ms(to_ms)}",
        )
    )
    print(
        manual_line(
            "Manual",
            "batch settings",
            f"batch_minutes={batch_minutes} | overlap_seconds={overlap_seconds}",
        )
    )
    print(manual_line("Manual", "symbols", ",".join(sorted(symbols or [])) or "all"))

    if dry_run:
        for batch in batches:
            print(
                manual_line(
                    "Batch",
                    f"{batch.index}/{batch.total}",
                    f"request={batch.request_from_utc} -> {batch.to_utc}",
                )
            )
        state["batches"] = [
            {
                **asdict(batch),
                "cursor_from_utc": batch.cursor_from_utc,
                "request_from_utc": batch.request_from_utc,
                "to_utc": batch.to_utc,
                "status": "PLANNED",
            }
            for batch in batches
        ]
        return 0

    _write_progress(progress_path, state)
    try:
        import time

        for batch in batches:
            raise_if_cancelled()
            cmd = _command_for_batch(
                batch,
                symbols=symbols,
                request_timeout=request_timeout,
                timeout_per_batch=timeout_per_batch,
                wait_lock_seconds=wait_lock_seconds,
                notify_per_batch=notify_per_batch,
            )
            record: dict[str, object] = {
                **asdict(batch),
                "cursor_from_utc": batch.cursor_from_utc,
                "request_from_utc": batch.request_from_utc,
                "to_utc": batch.to_utc,
                "status": "RUNNING",
                "started_at_utc": _utc_now_text(),
                "finished_at_utc": None,
                "exit_code": None,
                "attempts": [],
            }
            state_batches = state["batches"]
            assert isinstance(state_batches, list)
            state_batches.append(record)
            _write_progress(progress_path, state)
            print("")
            print("-" * 88)
            print(
                manual_line(
                    "Batch",
                    f"{batch.index}/{batch.total}",
                    f"window={batch.request_from_utc} -> {batch.to_utc}",
                )
            )
            rc = 1
            attempts = record["attempts"]
            assert isinstance(attempts, list)
            for attempt in range(1, int(max_attempts) + 1):
                raise_if_cancelled()
                attempt_record = {
                    "attempt": attempt,
                    "started_at_utc": _utc_now_text(),
                    "finished_at_utc": None,
                    "exit_code": None,
                }
                attempts.append(attempt_record)
                _write_progress(progress_path, state)
                if attempt > 1:
                    print(
                        manual_line(
                            "Batch",
                            f"{batch.index}/{batch.total}",
                            f"retry | attempt={attempt}/{max_attempts}",
                        ),
                        flush=True,
                    )
                rc, fatal_auth_reason = _run_batch_command(cmd)
                attempt_record["exit_code"] = int(rc)
                attempt_record["finished_at_utc"] = _utc_now_text()
                if fatal_auth_reason:
                    attempt_record["non_retryable"] = True
                    attempt_record["non_retryable_reason"] = fatal_auth_reason
                _write_progress(progress_path, state)
                if rc == 0:
                    break
                if fatal_auth_reason:
                    write_system_event(
                        "Retry Decision",
                        "stop",
                        f"batch={batch.index}/{batch.total} | attempt={attempt}/{max_attempts} | "
                        f"exit_code={rc} | reason=non-retryable cTrader account auth error: {fatal_auth_reason}",
                        level="ERROR",
                    )
                    print(
                        manual_line(
                            "Batch",
                            f"{batch.index}/{batch.total}",
                            f"stop retry | reason=non-retryable account auth error ({fatal_auth_reason})",
                        ),
                        flush=True,
                    )
                    break
                if attempt < int(max_attempts) and retry_sleep_seconds:
                    delay = min(
                        float(retry_sleep_max_seconds) if retry_sleep_max_seconds else float(retry_sleep_seconds),
                        float(retry_sleep_seconds) * (3 ** (attempt - 1)),
                    )
                    write_system_event(
                        "Retry Decision",
                        "retry",
                        f"batch={batch.index}/{batch.total} | attempt={attempt}/{max_attempts} | "
                        f"exit_code={rc} | sleep={delay:.0f}s",
                    )
                    time.sleep(delay)
            record["exit_code"] = int(rc)
            record["finished_at_utc"] = _utc_now_text()
            record["status"] = "DONE" if rc == 0 else "FAILED"
            _write_progress(progress_path, state)
            if rc != 0:
                fatal_reasons = [
                    str(attempt.get("non_retryable_reason"))
                    for attempt in attempts
                    if isinstance(attempt, dict) and attempt.get("non_retryable_reason")
                ]
                if fatal_reasons:
                    record["non_retryable"] = True
                    record["non_retryable_reason"] = fatal_reasons[-1]
                write_system_event(
                    "Retry Decision",
                    "give up",
                    f"batch={batch.index}/{batch.total} | attempts={len(attempts)} | exit_code={rc}"
                    + (
                        f" | reason=non-retryable cTrader account auth error: {fatal_reasons[-1]}"
                        if fatal_reasons
                        else ""
                    ),
                    level="ERROR",
                )
                state["status"] = "FAILED"
                state["failed_batch"] = batch.index
                state["exit_code"] = int(rc)
                if fatal_reasons:
                    state["non_retryable"] = True
                    state["non_retryable_reason"] = fatal_reasons[-1]
                _write_progress(progress_path, state)
                if notify_summary:
                    notify_tick_report(
                        "ERROR",
                        "Tick batched backfill failed",
                        conclusion="A scheduled batched backfill failed after all retry attempts.",
                        action="Review system.log for the CTrader phase and operation.log for the failed batch.",
                        details=[
                            ("Batch", f"{batch.index}/{batch.total}"),
                            ("Window", f"{batch.request_from_utc} -> {batch.to_utc}"),
                            ("Exit code", rc),
                            ("Progress", progress_path),
                        ],
                        throttle_key=f"tick-batched-backfill-failed-{progress_path.name}",
                        throttle_seconds=900,
                    )
                    flush_notifications()
                print(
                    manual_line("Batch", f"{batch.index}/{batch.total}", f"failed | exit_code={rc}"),
                    flush=True,
                )
                return int(rc)
            print(
                manual_line("Batch", f"{batch.index}/{batch.total}", "finished | result=OK"),
                flush=True,
            )
            if sleep_seconds and batch.index < batch.total:
                time.sleep(float(sleep_seconds))
    except KeyboardInterrupt:
        state["status"] = "CANCELLED"
        state["exit_code"] = 130
        _write_progress(progress_path, state)
        print(manual_line("Manual", "cancelled", "operator interrupted the run"), flush=True)
        return 130
    except CancelRequested as exc:
        state["status"] = "CANCELLED"
        state["exit_code"] = 130
        state["error"] = str(exc)
        _write_progress(progress_path, state)
        print(manual_line("Manual", "cancelled", str(exc)), flush=True)
        return 130
    except Exception as exc:
        state["status"] = "FAILED"
        state["exit_code"] = 1
        state["error"] = str(exc)
        _write_progress(progress_path, state)
        logger.exception(manual_line("Manual", "failed", "unexpected error"))
        return 1

    state["status"] = "DONE"
    state["exit_code"] = 0
    _write_progress(progress_path, state)
    if notify_summary and notify_success_summary:
        notify_tick_report(
            "INFO",
            "Tick batched backfill completed",
            conclusion="The scheduled batched backfill completed all batches.",
            details=[("Batches", len(batches)), ("Progress", progress_path)],
            throttle_key=f"tick-batched-backfill-completed-{progress_path.name}",
            throttle_seconds=3600,
        )
        flush_notifications()
    print("")
    print(manual_line("Manual", "done", f"batches={len(batches)} | progress={progress_path}"))
    return 0


@dataclass(frozen=True)
class HistoryDepthRequest:
    target: TargetSymbol
    remote: RemoteSymbol
    lookback_days: int
    from_timestamp_ms: int
    to_timestamp_ms: int


@dataclass(frozen=True)
class HistoryDepthResult:
    symbol: str
    ctrader_symbol_id: int
    ctrader_symbol_name: str
    deepest_available_days: int | None
    earliest_probe_from_ms: int | None
    earliest_probe_from_utc: str | None
    latest_probe_to_ms: int
    latest_probe_to_utc: str
    probes: list[dict[str, object]]


def fetch_remote_symbols(
    settings: TickRuntimeSettings, timeout_seconds: int = 45
) -> list[RemoteSymbol]:
    """Authenticate and fetch the account symbol list from cTrader."""
    settings = ensure_fresh_access_token(settings, "symbol sync")
    missing = settings.missing_api_fields
    if missing:
        raise ValueError(f"missing cTrader environment fields: {', '.join(missing)}")

    sdk = load_ctrader_sdk()
    client = new_client(settings, sdk)
    result: list[RemoteSymbol] = []
    errors: list[Exception] = []

    def on_error(failure: Any) -> None:
        if errors or result:
            return
        errors.append(RuntimeError(_failure_summary(failure)))
        stop_reactor(sdk, client)

    def on_authed() -> None:
        req = make_symbols_list_req(sdk, int(settings.account_id))

        def on_symbols(message: Any) -> None:
            payload = extract_payload(sdk, message)
            symbols = getattr(payload, "symbol", None) or getattr(payload, "symbols", [])
            for proto_symbol in symbols:
                data = remote_symbol_from_proto(proto_symbol)
                result.append(RemoteSymbol(**data, raw=data))
            stop_reactor(sdk, client)

        client.send(
            req,
            responseTimeoutInSeconds=settings.response_timeout_seconds,
        ).addCallbacks(on_symbols, on_error)

    client.setConnectedCallback(
        lambda _client: send_auth_chain(settings, sdk, client, on_authed, on_error)
    )
    client.setDisconnectedCallback(
        lambda _client, reason: None if result else on_error(reason)
    )
    client.startService()
    sdk.reactor.callLater(timeout_seconds, lambda: on_error(TimeoutError("symbol sync timed out")))
    sdk.reactor.run()
    if errors:
        raise errors[0]
    return result


def fetch_account_list(
    settings: TickRuntimeSettings, timeout_seconds: int = 45
) -> list[dict[str, object]]:
    """Authenticate the app and list accounts granted by the access token."""
    settings = ensure_fresh_access_token(settings, "account list")
    required = {
        "CTRADER_CLIENT_ID": settings.client_id,
        "CTRADER_CLIENT_SECRET": settings.client_secret,
        "CTRADER_ACCESS_TOKEN": settings.access_token,
    }
    missing = tuple(name for name, value in required.items() if not value)
    if missing:
        raise ValueError(f"missing cTrader environment fields: {', '.join(missing)}")

    sdk = load_ctrader_sdk()
    client = new_client(settings, sdk)
    result: list[dict[str, object]] = []
    errors: list[Exception] = []

    def on_error(failure: Any) -> None:
        if errors or result:
            return
        errors.append(RuntimeError(_failure_summary(failure)))
        stop_reactor(sdk, client)

    def on_app_auth(_response: Any) -> None:
        req = make_get_account_list_req(sdk, settings.access_token)

        def on_accounts(message: Any) -> None:
            payload = extract_payload(sdk, message)
            accounts = getattr(payload, "ctidTraderAccount", [])
            for account in accounts:
                result.append(
                    {
                        "ctidTraderAccountId": int(getattr(account, "ctidTraderAccountId")),
                        "isLive": bool(getattr(account, "isLive", False)),
                        "brokerName": str(getattr(account, "brokerName", "")),
                        "traderLogin": str(getattr(account, "traderLogin", "")),
                    }
                )
            stop_reactor(sdk, client)

        client.send(
            req,
            responseTimeoutInSeconds=settings.response_timeout_seconds,
        ).addCallbacks(on_accounts, on_error)

    app_req = make_application_auth_req(sdk, settings.client_id, settings.client_secret)
    client.setConnectedCallback(
        lambda _client: client.send(
            app_req,
            responseTimeoutInSeconds=settings.response_timeout_seconds,
        ).addCallbacks(on_app_auth, on_error)
    )
    client.setDisconnectedCallback(
        lambda _client, reason: None if result else on_error(reason)
    )
    client.startService()
    sdk.reactor.callLater(timeout_seconds, lambda: on_error(TimeoutError("account-list timed out")))
    sdk.reactor.run()
    if errors:
        raise errors[0]
    return result


def verify_account_auth(
    settings: TickRuntimeSettings, timeout_seconds: int = 45
) -> dict[str, object]:
    """Verify cTrader application and account auth without touching SQL Server."""
    settings = ensure_fresh_access_token(settings, "account auth check")
    missing = settings.missing_api_fields
    if missing:
        raise ValueError(f"missing cTrader environment fields: {', '.join(missing)}")

    sdk = load_ctrader_sdk()
    client = new_client(settings, sdk)
    result: dict[str, object] = {}
    errors: list[Exception] = []
    label = f"auth-check | env={settings.env} | endpoint={settings.endpoint_label}"

    def on_error(failure: Any) -> None:
        if errors or result:
            return
        errors.append(RuntimeError(_failure_summary(failure)))
        stop_reactor(sdk, client)

    def on_authed() -> None:
        result.update(
            {
                "account_auth_ok": True,
                "account_id": int(settings.account_id or 0),
                "trader_login": settings.trader_login,
                "env": settings.env,
                "endpoint": settings.endpoint_label,
            }
        )
        stop_reactor(sdk, client)

    client.setConnectedCallback(
        lambda _client: send_auth_chain(settings, sdk, client, on_authed, on_error, context=label)
    )
    client.setDisconnectedCallback(
        lambda _client, reason: None if result else on_error(reason)
    )
    client.startService()
    sdk.reactor.callLater(timeout_seconds, lambda: on_error(TimeoutError("account auth check timed out")))
    sdk.reactor.run()
    if errors:
        raise errors[0]
    return result


def sync_symbols(
    settings: TickRuntimeSettings, store: TickSqlStore | None, apply: bool
) -> list[str]:
    """Fetch remote symbols, match them, optionally persist to tick.SymbolMap."""
    remotes = fetch_remote_symbols(settings)
    matches = build_symbol_matches(settings.symbols, remotes)
    if apply and store is not None:
        store.upsert_symbol_matches(matches)

    lines = []
    for match in matches:
        remote_name = match.remote.symbol_name if match.remote else "NONE"
        remote_id = match.remote.ctrader_symbol_id if match.remote else "NONE"
        lines.append(
            f"{match.target.local_symbol}: {match.status} "
            f"score={match.score} remote={remote_name} id={remote_id} reason={match.reason}"
        )
    return lines


def build_activity_profile(
    settings: TickRuntimeSettings,
    store: TickSqlStore,
    lookback_days: int = 30,
    bucket_minutes: int = 15,
    active_min_ratio: float = 0.25,
    min_active_ticks: int = 1,
) -> dict[str, Any]:
    """Build the learned market activity profile used by tick health checks."""
    from tick_engine.utils_support.health import build_tick_activity_profile

    return build_tick_activity_profile(
        settings,
        store,
        lookback_days=lookback_days,
        bucket_minutes=bucket_minutes,
        active_min_ratio=active_min_ratio,
        min_active_ticks=min_active_ticks,
    )


def run_history_backfill(
    settings: TickRuntimeSettings,
    store: TickSqlStore,
    from_timestamp_ms: int,
    to_timestamp_ms: int,
    symbols: list[str] | None = None,
    request_timeout_seconds: float | None = None,
    timeout_seconds: int | None = None,
    notify: bool = True,
    run_note: str | None = None,
) -> None:
    """Backfill historical BID/ASK ticks for matched symbols."""
    raise_if_cancelled()
    if int(from_timestamp_ms) > int(to_timestamp_ms):
        raise ValueError("from_timestamp_ms must be <= to_timestamp_ms")
    settings = ensure_fresh_access_token(settings, "history backfill")
    settings = _with_response_timeout(settings, request_timeout_seconds)
    missing = settings.missing_api_fields
    if missing:
        raise ValueError(f"missing cTrader environment fields: {', '.join(missing)}")

    matched = store.fetch_matched_symbols()
    if symbols:
        requested = {symbol.upper() for symbol in symbols}
        matched = {key: value for key, value in matched.items() if key in requested}
    if not matched:
        raise RuntimeError("no matched symbols selected for backfill")
    if timeout_seconds is not None and int(timeout_seconds) <= 0:
        raise ValueError("timeout_seconds must be greater than 0")

    sdk = load_ctrader_sdk()
    client = new_client(settings, sdk)
    spool = TickSpool(settings.spool_path)

    def on_spooled(records: list[TickRecord], spooled: int, exc: Exception) -> None:
        if not notify:
            return
        symbols_in_batch = sorted({record.local_symbol.upper() for record in records})
        notify_tick_report(
            "WARNING",
            "Tick backfill SQL write failed; ticks were spooled",
            conclusion="The failed backfill batch was saved to the local spool. No data is lost yet.",
            action="Check SQL Server or the database connection. You can rerun backfill after the database is stable.",
            details=[
                ("Spooled ticks", spooled),
                (
                    "Symbol",
                    f"{','.join(symbols_in_batch[:8])}{'...' if len(symbols_in_batch) > 8 else ''}",
                ),
                ("Spool file", settings.spool_path),
            ],
            technical=[("SQL error", str(exc)[:300])],
            throttle_key="tick-backfill-spool-write-failed",
            throttle_seconds=300,
        )

    batcher = TickBatcher(
        store=store,
        spool=spool,
        batch_size=settings.batch_size,
        flush_seconds=settings.flush_seconds,
        on_spooled=on_spooled,
    )
    ingest_run_id = store.start_ingest_run(
        "BACKFILL",
        note=run_note or f"{from_timestamp_ms}->{to_timestamp_ms}",
    )
    if notify:
        notify_tick_report(
            "INFO",
            "Tick backfill started",
            conclusion="The service started downloading historical ticks from cTrader.",
            action="Monitor the dashboard or log viewer. Large multi-year backfills can take a long time.",
            details=[
                ("Run ID", ingest_run_id),
                ("Symbol", ",".join(sorted(matched))),
                ("From", _utc_naive_iso_from_ms(from_timestamp_ms)),
                ("To", _utc_naive_iso_from_ms(to_timestamp_ms)),
            ],
            throttle_key="tick-backfill-started",
            throttle_seconds=30,
        )
    queue: deque[HistoryWindowRequest] = deque()
    state: dict[str, object] = {
        "finished": False,
        "error": None,
        "cancelled": False,
        "phase": "starting",
        "symbol": "",
        "side": "",
        "request_from": None,
        "request_to": None,
    }
    for target, remote in matched.values():
        for start_ms, end_ms in iter_tick_windows(from_timestamp_ms, to_timestamp_ms):
            queue.append(HistoryWindowRequest(target, remote, start_ms, end_ms))
    planned_api_requests = len(queue) * 2
    total_timeout_seconds = (
        int(timeout_seconds)
        if timeout_seconds is not None
        else max(120, int(planned_api_requests * settings.response_timeout_seconds + 60))
    )
    logger.info(
        manual_line(
            "Backfill",
            "start",
            f"run={ingest_run_id[:8]} | symbols={','.join(sorted(matched))} | "
            f"window={_fmt_history_time(from_timestamp_ms)} -> {_fmt_history_time(to_timestamp_ms)} | "
            f"windows={len(queue)} | min_requests={planned_api_requests} | "
            f"request_timeout={settings.response_timeout_seconds:.0f}s | total_timeout={total_timeout_seconds}s",
        )
    )
    session_label = f"backfill run={ingest_run_id[:8]} | env={settings.env} | endpoint={settings.endpoint_label}"
    write_system_event(
        "CTrader Session",
        "start",
        f"{session_label} | symbols={len(matched)} | window={_fmt_history_time(from_timestamp_ms)} -> "
        f"{_fmt_history_time(to_timestamp_ms)} | request_timeout={settings.response_timeout_seconds:.0f}s",
    )

    def on_error(failure: Any) -> None:
        if state["finished"]:
            return
        state["finished"] = True
        summary = _failure_summary(failure)
        state["error"] = summary
        phase = str(state.get("phase") or "-")
        failure_level = _backfill_failure_log_level()
        failure_area = "Backfill Failure" if failure_level == "ERROR" else "Backfill Attempt"
        failure_item = phase if failure_level == "ERROR" else f"{phase} retryable"
        symbol = str(state.get("symbol") or "-")
        side = str(state.get("side") or "-")
        request_from = state.get("request_from")
        request_to = state.get("request_to")
        write_system_event(
            failure_area,
            failure_item,
            f"run={ingest_run_id[:8]} | symbol={symbol} | side={side} | "
            f"request={_fmt_history_time(request_from if isinstance(request_from, int) else None)} -> "
            f"{_fmt_history_time(request_to if isinstance(request_to, int) else None)} | "
            f"inserted={batcher.rows_inserted:,} | spooled={batcher.rows_spooled:,} | error={summary[:500]}",
            level=failure_level,
        )
        log_fn = logger.error if failure_level == "ERROR" else logger.warning
        log_fn(
            manual_line(
                "Backfill",
                "failed" if failure_level == "ERROR" else "attempt failed",
                f"run={ingest_run_id[:8]} | inserted={batcher.rows_inserted:,} | "
                f"spooled={batcher.rows_spooled:,} | symbols={','.join(sorted(matched))} | error={summary}",
            )
        )
        stop_reactor(sdk, client)

    def cancel_backfill() -> bool:
        if not cancel_requested():
            return False
        if state["finished"]:
            return True
        state["cancelled"] = True
        logger.warning(
            manual_line(
                "Backfill",
                "cancelled",
                f"run={ingest_run_id[:8]} | inserted={batcher.rows_inserted:,} | "
                f"spooled={batcher.rows_spooled:,} | symbols={','.join(sorted(matched))}",
            )
        )
        try:
            batcher.flush()
        except Exception as exc:
            state["error"] = exc
            state["cancelled"] = False
            logger.exception(manual_line("Backfill", "cancel flush", f"failed | run={ingest_run_id[:8]}"))
        state["finished"] = True
        stop_reactor(sdk, client)
        return True

    def fetch_side(
        item: HistoryWindowRequest,
        quote_type: str,
        to_timestamp_ms: int,
        decoded_ticks: list[DecodedHistoricalTick],
        seen_tick_counts: dict[tuple[int, int, str], int],
        on_complete: Callable[[list[DecodedHistoricalTick]], None],
    ) -> None:
        if cancel_backfill():
            return
        logger.debug(
            "BACKFILL REQUEST | %s %-3s | API window=%s -> %s | run=%s",
            item.target.local_symbol,
            quote_type,
            _fmt_history_time(item.from_timestamp_ms),
            _fmt_history_time(to_timestamp_ms),
            ingest_run_id[:8],
        )
        req = make_get_tick_data_req(
            sdk,
            int(settings.account_id),
            item.remote.ctrader_symbol_id,
            quote_type,
            item.from_timestamp_ms,
            to_timestamp_ms,
        )
        state["phase"] = "history request"
        state["symbol"] = item.target.local_symbol
        state["side"] = quote_type
        state["request_from"] = item.from_timestamp_ms
        state["request_to"] = to_timestamp_ms
        write_system_event(
            "History Request",
            "sent",
            f"run={ingest_run_id[:8]} | symbol={item.target.local_symbol} | side={quote_type} | "
            f"window={_fmt_history_time(item.from_timestamp_ms)} -> {_fmt_history_time(to_timestamp_ms)} | "
            f"timeout={settings.response_timeout_seconds:.0f}s",
        )

        def on_ticks(message: Any) -> None:
            try:
                payload = extract_payload(sdk, message)
                raw_ticks = getattr(payload, "tickData", [])
                page_ticks = decode_delta_ticks(raw_ticks, quote_type)
                unique_page_ticks: list[DecodedHistoricalTick] = []
                page_tick_counts: dict[tuple[int, int, str], int] = defaultdict(int)
                for tick in page_ticks:
                    key = (int(tick.timestamp_ms), int(tick.raw_price), tick.quote_type)
                    page_tick_counts[key] += 1
                    if page_tick_counts[key] <= seen_tick_counts.get(key, 0):
                        continue
                    unique_page_ticks.append(tick)
                for key, count in page_tick_counts.items():
                    seen_tick_counts[key] = max(seen_tick_counts.get(key, 0), count)
                decoded_ticks.extend(unique_page_ticks)
                earliest_ms = min((tick.timestamp_ms for tick in page_ticks), default=None)
                latest_ms = max((tick.timestamp_ms for tick in page_ticks), default=None)
                has_more = bool(getattr(payload, "hasMore", False))
                write_system_event(
                    "History Request",
                    "response",
                    f"run={ingest_run_id[:8]} | symbol={item.target.local_symbol} | side={quote_type} | "
                    f"ticks={len(page_ticks):,} | unique={len(unique_page_ticks):,} | "
                    f"more={'yes' if has_more else 'no'} | "
                    f"data={_fmt_history_time(earliest_ms)} -> {_fmt_history_time(latest_ms)}",
                )
                logger.info(
                    manual_line(
                        "Ticks",
                        f"{item.target.local_symbol} {quote_type}",
                        f"run={ingest_run_id[:8]} | ticks={len(page_ticks):,} | "
                        f"data={_fmt_history_time(earliest_ms)} -> {_fmt_history_time(latest_ms)} | "
                        f"more={'yes' if has_more else 'no'} | pages_total={len(decoded_ticks):,}",
                    )
                )
                if cancel_backfill():
                    return
                next_to_timestamp_ms = _next_history_page_to_timestamp(
                    symbol=item.target.local_symbol,
                    quote_type=quote_type,
                    from_timestamp_ms=item.from_timestamp_ms,
                    current_to_timestamp_ms=to_timestamp_ms,
                    page_ticks=page_ticks,
                    unique_page_tick_count=len(unique_page_ticks),
                    has_more=has_more,
                )
                if next_to_timestamp_ms is not None:
                    fetch_side(
                        item,
                        quote_type,
                        next_to_timestamp_ms,
                        decoded_ticks,
                        seen_tick_counts,
                        on_complete,
                    )
                    return
                on_complete(decoded_ticks)
            except Exception as exc:
                on_error(exc)

        client.send(
            req,
            responseTimeoutInSeconds=settings.response_timeout_seconds,
        ).addCallbacks(on_ticks, on_error)

    def send_next() -> None:
        if cancel_backfill():
            return
        if not queue:
            logger.info(
                manual_line(
                    "Backfill",
                    "finalize",
                    f"run={ingest_run_id[:8]} | inserted={batcher.rows_inserted:,} | spooled={batcher.rows_spooled:,}",
                )
            )
            batcher.flush()
            state["finished"] = True
            logger.info(manual_line("Backfill", "flush", f"done | run={ingest_run_id[:8]}"))
            stop_reactor(sdk, client)
            return

        item = queue.popleft()
        logger.debug(
            "BACKFILL WINDOW | %s | window=%s -> %s | remaining=%d | run=%s",
            item.target.local_symbol,
            _fmt_history_time(item.from_timestamp_ms),
            _fmt_history_time(item.to_timestamp_ms),
            len(queue),
            ingest_run_id[:8],
        )

        def on_ask_complete(ask_ticks: list[DecodedHistoricalTick], bid_ticks: list[DecodedHistoricalTick]) -> None:
            try:
                records, merge_stats = merge_historical_quote_ticks(
                    item.target,
                    item.remote,
                    bid_ticks,
                    ask_ticks,
                    ingest_run_id=ingest_run_id,
                    max_side_age_seconds=settings.max_quote_side_age_seconds,
                )
                for record in records:
                    batcher.add(record)
                batcher.flush()
                logger.info(
                    manual_line(
                        "Quote",
                        item.target.local_symbol,
                        f"run={ingest_run_id[:8]} | quotes={len(records):,} | "
                        f"bid_ticks={merge_stats.bid_ticks:,} | ask_ticks={merge_stats.ask_ticks:,} | "
                        f"dropped={merge_stats.dropped_total:,} | inserted_total={batcher.rows_inserted:,} | "
                        f"spool={batcher.rows_spooled:,}",
                    )
                )
                if merge_stats.dropped_total:
                    logger.warning(
                        manual_line(
                            "Quote",
                            item.target.local_symbol,
                            f"dropped detail | run={ingest_run_id[:8]} | "
                            f"bid_outliers={merge_stats.dropped_bid_outliers} | "
                            f"ask_outliers={merge_stats.dropped_ask_outliers} | "
                            f"unseeded={merge_stats.dropped_unseeded} | "
                            f"stale_side={merge_stats.dropped_stale_side} | "
                            f"crossed={merge_stats.dropped_crossed} | "
                            f"wide_spread={merge_stats.dropped_wide_spread} | "
                            f"duplicate_quote={merge_stats.dropped_duplicate_quote}",
                        )
                    )
                send_next()
            except Exception as exc:
                on_error(exc)

        def on_bid_complete(bid_ticks: list[DecodedHistoricalTick]) -> None:
            fetch_side(
                item,
                "ASK",
                item.to_timestamp_ms,
                [],
                {},
                lambda ask_ticks: on_ask_complete(ask_ticks, bid_ticks),
            )

        fetch_side(item, "BID", item.to_timestamp_ms, [], {}, on_bid_complete)

    def on_connected(_client: Any) -> None:
        state["phase"] = "auth"
        write_system_event("CTrader Session", "connected", session_label)
        send_auth_chain(settings, sdk, client, send_next, on_error, context=session_label)

    def on_disconnected(_client: Any, reason: Any) -> None:
        reason_text = "completed" if state["finished"] else str(reason)[:300]
        write_system_event(
            "CTrader Session",
            "disconnected",
            f"{session_label} | finished={bool(state['finished'])} | reason={reason_text}",
            level="WARNING" if not state["finished"] else "INFO",
        )
        if not state["finished"]:
            on_error(Exception(str(reason)))

    client.setConnectedCallback(on_connected)
    client.setDisconnectedCallback(on_disconnected)
    client.startService()
    sdk.reactor.callLater(
        total_timeout_seconds,
        lambda: on_error(TimeoutError("history backfill timed out")),
    )
    sdk.reactor.run()
    write_system_event(
        "CTrader Session",
        "stopped",
        f"{session_label} | finished={bool(state['finished'])} | cancelled={bool(state['cancelled'])}",
    )
    if state["cancelled"]:
        logger.info(manual_line("Backfill", "cancelled", f"metadata update | run={ingest_run_id[:8]}"))
        try:
            store.finish_ingest_run(
                ingest_run_id,
                status="INTERRUPTED",
                rows_inserted=batcher.rows_inserted,
                rows_spooled=batcher.rows_spooled,
                note="cancel requested",
            )
        except Exception:
            logger.exception(manual_line("Backfill", "metadata", f"cancel update failed | run={ingest_run_id[:8]}"))
        if notify:
            notify_tick_report(
                "WARNING",
                "Tick backfill cancelled",
                conclusion="The backfill stopped after the current batch and flushed pending ticks.",
                action="Rerun the same window later if you still need the remaining history.",
                details=[
                    ("Run ID", ingest_run_id),
                    ("Inserted ticks", batcher.rows_inserted),
                    ("Spooled ticks", batcher.rows_spooled),
                ],
                throttle_key="tick-backfill-cancelled",
                throttle_seconds=30,
            )
            flush_notifications()
        raise CancelRequested("history backfill cancelled")
    if state["error"]:
        summary = str(state["error"])
        try:
            store.finish_ingest_run(
                ingest_run_id,
                status="FAILED",
                rows_inserted=batcher.rows_inserted,
                rows_spooled=batcher.rows_spooled,
                note=summary,
            )
        except Exception:
            logger.exception(manual_line("Backfill", "metadata", f"failed update failed | run={ingest_run_id[:8]}"))
        if notify:
            notify_tick_report(
                "ERROR",
                "Tick backfill failed",
                conclusion="Backfill failed before completion.",
                action="Check logs, cTrader token, network, and database connectivity. After fixing the issue, rerun the same time window.",
                details=[
                    ("Run ID", ingest_run_id),
                    ("Inserted ticks", batcher.rows_inserted),
                    ("Spooled ticks", batcher.rows_spooled),
                ],
                technical=[("Error", summary[:600])],
                throttle_key="tick-backfill-failed",
                throttle_seconds=60,
            )
            flush_notifications()
        raise RuntimeError(f"history backfill failed: {state['error']}")
    if state["finished"]:
        logger.info(manual_line("Backfill", "metadata", f"finishing | run={ingest_run_id[:8]}"))
        store.finish_ingest_run(
            ingest_run_id,
            status="DONE",
            rows_inserted=batcher.rows_inserted,
            rows_spooled=batcher.rows_spooled,
        )
        logger.info(manual_line("Backfill", "metadata", f"finished | run={ingest_run_id[:8]}"))
        logger.info(
            manual_line(
                "Backfill",
                "done",
                f"run={ingest_run_id[:8]} | inserted={batcher.rows_inserted:,} | spooled={batcher.rows_spooled:,}",
            )
        )
        if notify:
            notify_tick_report(
                "INFO",
                "Tick backfill completed",
                conclusion="Historical tick backfill completed.",
                action="Open the dashboard or run tick_status.ps1 to review the new data.",
                details=[
                    ("Run ID", ingest_run_id),
                    ("Inserted ticks", batcher.rows_inserted),
                    ("Spooled ticks", batcher.rows_spooled),
                ],
                throttle_key="tick-backfill-completed",
                throttle_seconds=30,
            )
            flush_notifications()


def _default_probe_days(max_days: int) -> list[int]:
    candidates = [
        1, 7, 30, 90, 180, 365, 730, 1095, 1460, 1825,
        2555, 3650, 5475, 7300, 9125, 10950, 14600, 18250, 20000,
    ]
    max_days = max(1, int(max_days))
    days = [day for day in candidates if day <= max_days]
    if max_days not in days:
        days.append(max_days)
    return sorted(set(days))


def _raise_if_history_depth_missing_probes(
    probes_by_symbol: dict[str, list[dict[str, object]]],
) -> None:
    missing = sorted(symbol for symbol, probes in probes_by_symbol.items() if not probes)
    if missing:
        raise RuntimeError(
            "history-depth completed without any probe response for: "
            + ",".join(missing)
        )


def probe_history_depth_for_matches(
    settings: TickRuntimeSettings,
    matched: dict[str, tuple[TargetSymbol, RemoteSymbol]],
    max_days: int = 20000,
    to_timestamp_ms: int | None = None,
    probe_window_days: int = 7,
    timeout_seconds: int = 300,
    request_timeout_seconds: float | None = None,
) -> list[HistoryDepthResult]:
    """Probe cTrader historical BID ticks for an already matched symbol map."""
    settings = ensure_fresh_access_token(settings, "history depth probe")
    settings = _with_response_timeout(settings, request_timeout_seconds)
    missing = settings.missing_api_fields
    if missing:
        raise ValueError(f"missing cTrader environment fields: {', '.join(missing)}")
    if not matched:
        raise RuntimeError("no matched symbols selected for history depth probe")

    to_ms = int(to_timestamp_ms or millis_from_utc(datetime.now(timezone.utc)))
    day_ms = 24 * 60 * 60 * 1000
    window_ms = max(1, min(7, int(probe_window_days))) * day_ms

    def build_depth_request(
        target: TargetSymbol, remote: RemoteSymbol, lookback_days: int
    ) -> HistoryDepthRequest:
        start_ms = max(0, to_ms - int(lookback_days) * day_ms)
        end_ms = min(to_ms, start_ms + window_ms - 1)
        return HistoryDepthRequest(
            target=target,
            remote=remote,
            lookback_days=int(lookback_days),
            from_timestamp_ms=start_ms,
            to_timestamp_ms=end_ms,
        )

    queue: deque[HistoryDepthRequest] = deque()
    for target, remote in matched.values():
        for lookback_days in _default_probe_days(max_days):
            queue.append(build_depth_request(target, remote, lookback_days))

    sdk = load_ctrader_sdk()
    client = new_client(settings, sdk)
    probes_by_symbol: dict[str, list[dict[str, object]]] = {symbol: [] for symbol in matched}
    errors: list[Exception] = []
    refine_rounds = {"count": 0}
    state: dict[str, object] = {"finished": False, "in_flight": False}

    def enqueue_refinement_requests() -> int:
        if refine_rounds["count"] >= 12:
            return 0
        added = 0
        for symbol, probes in probes_by_symbol.items():
            tested = {int(probe["lookback_days"]) for probe in probes}
            successes = sorted(
                int(probe["lookback_days"]) for probe in probes if int(probe["tick_count"]) > 0
            )
            failures = sorted(
                int(probe["lookback_days"]) for probe in probes if int(probe["tick_count"]) == 0
            )
            if not successes:
                continue
            low = successes[-1]
            higher_failures = [day for day in failures if day > low]
            high = higher_failures[0] if higher_failures else int(max_days)
            if high - low <= 1:
                continue
            mid = low + ((high - low) // 2)
            if mid in tested:
                continue
            target, remote = matched[symbol]
            queue.append(build_depth_request(target, remote, mid))
            added += 1
        if added:
            refine_rounds["count"] += 1
        return added

    def on_error(failure: Any) -> None:
        if state["finished"]:
            return
        state["finished"] = True
        state["in_flight"] = False
        errors.append(Exception(str(failure)))
        stop_reactor(sdk, client)

    def send_next() -> None:
        if not queue:
            if enqueue_refinement_requests():
                send_next()
                return
            state["finished"] = True
            state["in_flight"] = False
            stop_reactor(sdk, client)
            return
        item = queue.popleft()
        state["in_flight"] = True
        req = make_get_tick_data_req(
            sdk,
            int(settings.account_id),
            item.remote.ctrader_symbol_id,
            "BID",
            item.from_timestamp_ms,
            item.to_timestamp_ms,
        )

        def on_ticks(message: Any) -> None:
            state["in_flight"] = False
            payload = extract_payload(sdk, message)
            raw_ticks = getattr(payload, "tickData", [])
            decoded = decode_delta_ticks(raw_ticks, "BID") if raw_ticks else []
            earliest_tick_ms = min((tick.timestamp_ms for tick in decoded), default=None)
            latest_tick_ms = max((tick.timestamp_ms for tick in decoded), default=None)
            probes_by_symbol[item.target.local_symbol.upper()].append(
                {
                    "lookback_days": item.lookback_days,
                    "from_ms": item.from_timestamp_ms,
                    "from_utc": _utc_naive_iso_from_ms(item.from_timestamp_ms),
                    "to_ms": item.to_timestamp_ms,
                    "to_utc": _utc_naive_iso_from_ms(item.to_timestamp_ms),
                    "tick_count": len(decoded),
                    "has_more": bool(getattr(payload, "hasMore", False)),
                    "earliest_tick_ms": earliest_tick_ms,
                    "earliest_tick_utc": _utc_naive_iso_from_ms(earliest_tick_ms)
                    if earliest_tick_ms is not None
                    else None,
                    "latest_tick_ms": latest_tick_ms,
                    "latest_tick_utc": _utc_naive_iso_from_ms(latest_tick_ms)
                    if latest_tick_ms is not None
                    else None,
                }
            )
            send_next()

        client.send(
            req,
            responseTimeoutInSeconds=settings.response_timeout_seconds,
        ).addCallbacks(on_ticks, on_error)

    client.setConnectedCallback(
        lambda _client: send_auth_chain(settings, sdk, client, send_next, on_error)
    )
    client.setDisconnectedCallback(
        lambda _client, reason: None
        if state["finished"]
        else on_error(Exception(str(reason)))
    )
    client.startService()
    sdk.reactor.callLater(
        timeout_seconds, lambda: on_error(TimeoutError("history-depth probe timed out"))
    )
    sdk.reactor.run()
    if errors:
        raise errors[0]
    _raise_if_history_depth_missing_probes(probes_by_symbol)

    results: list[HistoryDepthResult] = []
    for symbol, probes in sorted(probes_by_symbol.items()):
        target, remote = matched[symbol]
        found = [probe for probe in probes if int(probe["tick_count"]) > 0]
        deepest = max(found, key=lambda probe: int(probe["lookback_days"])) if found else None
        results.append(
            HistoryDepthResult(
                symbol=symbol,
                ctrader_symbol_id=remote.ctrader_symbol_id,
                ctrader_symbol_name=remote.symbol_name,
                deepest_available_days=int(deepest["lookback_days"]) if deepest else None,
                earliest_probe_from_ms=int(deepest["from_ms"]) if deepest else None,
                earliest_probe_from_utc=str(deepest["from_utc"]) if deepest else None,
                latest_probe_to_ms=to_ms,
                latest_probe_to_utc=_utc_naive_iso_from_ms(to_ms),
                probes=probes,
            )
        )
    return results


def probe_history_depth(
    settings: TickRuntimeSettings,
    store: TickSqlStore,
    symbols: list[str] | None = None,
    max_days: int = 20000,
    to_timestamp_ms: int | None = None,
    probe_window_days: int = 7,
    timeout_seconds: int = 300,
    request_timeout_seconds: float | None = None,
) -> list[HistoryDepthResult]:
    """Probe how far back cTrader has BID ticks for matched symbols."""
    matched = store.fetch_matched_symbols()
    if symbols:
        requested = {symbol.upper() for symbol in symbols}
        matched = {key: value for key, value in matched.items() if key in requested}
    return probe_history_depth_for_matches(
        settings,
        matched,
        max_days=max_days,
        to_timestamp_ms=to_timestamp_ms,
        probe_window_days=probe_window_days,
        timeout_seconds=timeout_seconds,
        request_timeout_seconds=request_timeout_seconds,
    )
