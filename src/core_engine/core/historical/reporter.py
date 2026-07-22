"""Operator-readable logging helpers for historical pulls."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from core_engine.util.logkit.formatters import clean, operation_line
from core_engine.util.logkit.tables import cell as _cell, kv as _kv


PAIR_HEADER = " | ".join(
    [
        "PROG".ljust(7),
        "SYMBOL".ljust(8),
        "TF".ljust(4),
        "STEP".ljust(8),
        "WHY".ljust(20),
        "ASKED".rjust(7),
        "SAVED".rjust(7),
        "STATUS".ljust(8),
        "NOTE",
    ]
)
PAIR_RULE = "-" * len(PAIR_HEADER)


def fmt_int(value: int | float | None) -> str:
    if value is None:
        return "-"
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


def _duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "-"
    try:
        total = int(float(seconds))
    except Exception:
        return str(seconds)
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {sec}s"
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


def log_historical_block(logger: logging.Logger, level: int, text: str) -> None:
    for raw_line in str(text).split("\n"):
        logger.log(level, "%s", raw_line.rstrip())


def _mode_label(mode: str) -> str:
    mapping = {
        "gap": "Backfill Missing / Gap Repair",
        "full": "Initial / Replay Historical Pull",
        "reset": "Reset OHLCV Data",
        "auto": "Auto",
    }
    return mapping.get(str(mode or "").lower(), clean(mode))


def _scope_text(scope_events: Iterable[Any]) -> str:
    parts = []
    for event in scope_events:
        action = clean(getattr(event, "action", ""))
        amount = clean(getattr(event, "amount", ""))
        status = clean(getattr(event, "status", ""))
        piece = " ".join(part for part in (action, amount) if part and part != "-")
        if status and status != "-":
            piece = f"{piece} ({status})" if piece else status
        if piece:
            parts.append(piece)
    return "; ".join(parts) if parts else "all configured symbols/timeframes"


def historical_start_block(
    *,
    mode: str,
    started: datetime,
    symbols: int,
    timeframes: int,
    replay_enabled: bool,
    replay_tfs: Iterable[str],
    lookback_days: int | None,
    dry_run: bool,
    scope_events: Iterable[Any],
    replay_changes: Iterable[str] | None = None,
) -> str:
    if started.tzinfo is None:
        started_utc = started.replace(tzinfo=timezone.utc)
    else:
        started_utc = started.astimezone(timezone.utc)
    replay_tf_text = ",".join(sorted(str(tf).upper() for tf in replay_tfs)) or "-"
    replay_change_text = ", ".join(replay_changes or []) or "-"
    return "\n".join(
        [
            "",
            "=" * 96,
            "[HISTORICAL START]",
            _kv("Mode", _mode_label(mode)),
            _kv("Started UTC", started_utc.strftime("%Y-%m-%d %H:%M:%S UTC")),
            _kv("Scope", f"{symbols:,} symbol(s) | {timeframes:,} timeframe(s)"),
            _kv("Lookback", f"{lookback_days} day(s)" if lookback_days else "-"),
            _kv("Replay", "on" if replay_enabled else "off"),
            _kv("Replay TFs", replay_tf_text),
            _kv("Dry run", "yes" if dry_run else "no"),
            _kv("Filters", _scope_text(scope_events)),
            _kv("Overrides", replay_change_text),
            "=" * 96,
            "",
        ]
    )


def historical_pair_section(title: str = "PAIR FLOW") -> str:
    return "\n".join(
        [
            "",
            f"[{title}]",
            PAIR_HEADER,
            PAIR_RULE,
        ]
    )


def historical_pair_line(
    *,
    progress: str,
    symbol: str,
    timeframe: str,
    action: str,
    reason: str,
    request: str,
    saved: str | int | None,
    result: str,
    detail: str | None = None,
) -> str:
    return " | ".join(
        [
            _cell(progress, 7),
            _cell(symbol, 8),
            _cell(timeframe, 4),
            _cell(action.upper(), 8),
            _cell(reason, 20),
            _cell(request, 7, align="right"),
            _cell(saved, 7, align="right"),
            _cell(result.upper(), 8),
            clean(detail),
        ]
    )


def historical_scan_summary_block(
    *,
    raw_gap_windows: int,
    non_trading_windows: int = 0,
    verified_skipped: int = 0,
    upgraded_pairs: int = 0,
    new_pairs: int = 0,
    result: str = "clean",
) -> str:
    gap_repair_pairs = int(upgraded_pairs or 0) + int(new_pairs or 0)
    return "\n".join(
        [
            "",
            "[SCAN SUMMARY]",
            _kv("Raw gaps", fmt_int(raw_gap_windows)),
            _kv("Ignored closures", fmt_int(non_trading_windows)),
            _kv("Verified skipped", fmt_int(verified_skipped)),
            _kv("Pairs upgraded", fmt_int(upgraded_pairs)),
            _kv("New pairs", fmt_int(new_pairs)),
            _kv("Gap repair pairs", fmt_int(gap_repair_pairs)),
            _kv("Result", result),
            "-" * 96,
        ]
    )


def historical_run_summary_block(
    *,
    mode: str,
    elapsed_seconds: float,
    stats: dict[str, Any],
    dry_run: bool,
) -> str:
    fail = int(stats.get("fail", 0) or 0)
    status = "completed" if fail == 0 else "completed with warnings"
    block = [
        "",
        "[HISTORICAL SUMMARY]",
        _kv("Status", status),
        _kv("Mode", _mode_label(mode)),
        _kv("Duration", _duration(elapsed_seconds)),
        _kv("Dry run", "yes" if dry_run else "no"),
    ]
    for key, label in (
        ("queued", "Pairs selected"),
        ("ok", "Pairs completed"),
        ("fail", "Pairs failed"),
        ("preview_fact", "Preview fact"),
        ("preview_staging", "Preview staging"),
        ("deleted_fact", "Deleted fact"),
        ("deleted_staging", "Deleted staging"),
    ):
        if key in stats:
            block.append(_kv(label, fmt_int(stats.get(key))))
    if "inserted" in stats:
        block.append(_kv("Rows saved", "not written (dry run)" if dry_run else fmt_int(stats.get("inserted"))))
    block.extend(
        [
            _kv(
                "Next action",
                "review failed pairs and rerun Backfill Missing" if fail else "no action needed unless counts look unexpected",
            ),
            "=" * 96,
            "",
        ]
    )
    return "\n".join(block)


class HistoricalReporter:
    def __init__(self, logger: logging.Logger, *, replay_enabled: bool, replay_tfs: set[str]) -> None:
        self.logger = logger
        self.replay_enabled = bool(replay_enabled)
        self.replay_tfs = {str(tf).upper() for tf in replay_tfs}
        self._pair_context: dict[tuple[str, str, str], dict[str, str]] = {}

    def rule(self, label: str, detail: str = "") -> None:
        self.logger.info("%s", operation_line("HISTORICAL", label, detail))

    @staticmethod
    def _friendly_status(status: str) -> str:
        value = str(status or "").strip()
        mapping = {
            "STALE+HOLE": "old candle + gap",
            "STALE": "old candle",
            "HOLE": "timeline gap",
            "MISS": "no stored data",
            "done": "completed",
        }
        return mapping.get(value, value)

    @staticmethod
    def _request_text(amount: str) -> str:
        text = clean(amount)
        return text.replace("pull ", "", 1) if text.startswith("pull ") else text

    @staticmethod
    def _progress(index: int, total: int) -> str:
        width = max(3, len(str(max(1, int(total)))))
        return f"{index:0{width}d}/{total:0{width}d}"

    def start(
        self,
        *,
        mode: str,
        started: datetime,
        symbols: int,
        timeframes: int,
        lookback_days: int | None,
        dry_run: bool,
        scope_events: Iterable[Any],
        replay_changes: Iterable[str] | None = None,
    ) -> None:
        log_historical_block(
            self.logger,
            logging.INFO,
            historical_start_block(
                mode=mode,
                started=started,
                symbols=symbols,
                timeframes=timeframes,
                replay_enabled=self.replay_enabled,
                replay_tfs=self.replay_tfs,
                lookback_days=lookback_days,
                dry_run=dry_run,
                scope_events=scope_events,
                replay_changes=replay_changes,
            ),
        )

    def pair_flow_header(self, title: str = "PAIR FLOW") -> None:
        log_historical_block(self.logger, logging.INFO, historical_pair_section(title))

    def pair_start(
        self,
        index: int,
        total: int,
        symbol: str,
        tf_code: str,
        amount: str,
        range_: str = "-",
        status: str = "-",
    ) -> None:
        progress = self._progress(index, total)
        reason = self._friendly_status(status)
        request = self._request_text(amount)
        detail = clean(range_)
        self._pair_context[(progress, symbol, tf_code)] = {
            "reason": reason,
            "request": request,
            "detail": detail,
        }
        self.logger.info(
            "%s",
            historical_pair_line(
                progress=progress,
                symbol=symbol,
                timeframe=tf_code,
                action="pull",
                reason=reason,
                request=request,
                saved="-",
                result="running",
                detail=detail,
            ),
        )

    def pair_result(self, index: int, total: int, symbol: str, tf_code: str, result: int) -> None:
        progress = self._progress(index, total)
        context = self._pair_context.get((progress, symbol, tf_code), {})
        reason = context.get("reason", "-")
        request = context.get("request", "-")
        if result > 0:
            saved = fmt_int(result)
            status = "ok"
            detail = "-"
        elif result == 0:
            saved = "0"
            status = "no new rows"
            detail = "-"
        else:
            saved = "-"
            status = "failed"
            detail = "TradingView returned no data" if result == -2 else "write/fetch error"
        self.logger.info(
            "%s",
            historical_pair_line(
                progress=progress,
                symbol=symbol,
                timeframe=tf_code,
                action="done" if result >= 0 else "failed",
                reason=reason,
                request=request,
                saved=saved,
                result=status,
                detail=detail,
            ),
        )

    def pair_dry_run(self, index: int, total: int, symbol: str, tf_code: str) -> None:
        progress = self._progress(index, total)
        context = self._pair_context.get((progress, symbol, tf_code), {})
        self.logger.info(
            "%s",
            historical_pair_line(
                progress=progress,
                symbol=symbol,
                timeframe=tf_code,
                action="preview",
                reason=context.get("reason", "-"),
                request=context.get("request", "-"),
                saved="-",
                result="skipped",
                detail="preview only; no rows written",
            ),
        )

    def tf_header(
        self,
        step_idx: int,
        total_tfs: int,
        tf_code: str,
        n_bars: int,
        pairs: int,
        staging: str,
    ) -> None:
        replay_hint = "on" if self.replay_enabled and tf_code.upper() in self.replay_tfs else "off"
        log_historical_block(
            self.logger,
            logging.INFO,
            "\n".join(
                [
                    "",
                    "[TIMEFRAME]",
                    _kv("Progress", f"{step_idx:02d}/{total_tfs:02d}"),
                    _kv("Timeframe", tf_code),
                    _kv("Pairs", fmt_int(pairs)),
                    _kv("Target bars", fmt_int(n_bars)),
                    _kv("Replay", replay_hint),
                    _kv("Temp table", staging),
                    "-" * 96,
                ]
            ),
        )

    def tf_summary(self, tf_code: str, ok: int, fail: int, inserted: int | None = None) -> None:
        log_historical_block(
            self.logger,
            logging.INFO if fail == 0 else logging.WARNING,
            "\n".join(
                [
                    "",
                    "[TIMEFRAME SUMMARY]",
                    _kv("Timeframe", tf_code),
                    _kv("Completed", fmt_int(ok)),
                    _kv("Failed", fmt_int(fail)),
                    _kv("Rows saved", fmt_int(inserted)),
                    _kv("Result", "completed" if fail == 0 else "completed with errors"),
                    "-" * 96,
                ]
            ),
        )

    def run_summary(self, *, mode: str, elapsed_seconds: float, stats: dict[str, Any], dry_run: bool) -> None:
        level = logging.INFO if int(stats.get("fail", 0) or 0) == 0 else logging.WARNING
        log_historical_block(
            self.logger,
            level,
            historical_run_summary_block(
                mode=mode,
                elapsed_seconds=elapsed_seconds,
                stats=stats,
                dry_run=dry_run,
            ),
        )
