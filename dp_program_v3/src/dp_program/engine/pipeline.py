"""Shared validation and durable delivery for one provider candle window."""

from __future__ import annotations

import logging
import time
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .auth import AuthError
from ..log import log_event
from .spool import ack, enqueue, interprocess_lock
from .sql_connector import (
    bulk_upsert_candles,
    candle_signature,
    fetch_existing_candles,
)
from .websocket import fetch_candles

LOGGER = logging.getLogger(__name__)
_FAILURE_ACTIONS = {
    "fetch": "pair remains pending and will retry from the Fact watermark",
    "validation": "pair stopped before durable or SQL write",
    "coverage": "incomplete window discarded before durable or SQL write",
    "sql_compare": "pair stopped before durable or SQL write",
    "spool_enqueue": "pair stopped before SQL write",
    "sql_delivery": "transaction rolled back; candles retained in spool",
    "spool_ack": "SQL committed; spool cleanup will retry",
    "planning": "window was not requested; Fact watermark did not advance",
}


class CoverageError(RuntimeError):
    """Raised when a completed response does not reach the planned cursor."""


class CandleValidationError(RuntimeError):
    """Raised instead of silently advancing past an invalid provider candle."""


class PipelineError(RuntimeError):
    """Attach a stable pipeline stage to an underlying failure."""

    def __init__(self, stage: str, cause: Exception) -> None:
        super().__init__(str(cause))
        self.stage = stage
        self.cause = cause


def utc(value: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC."""
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def validate_candles(
    candles: list[dict[str, Any]],
    timeframe: dict[str, Any],
    *,
    closed_only: bool,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Validate, normalize, deduplicate, and sort the canonical candle shape."""
    current = utc(now or datetime.now(timezone.utc))
    unique: dict[tuple[int, str, datetime], dict[str, Any]] = {}
    for index, candle in enumerate(candles):
        try:
            timestamp = utc(candle["timestamp"]).replace(microsecond=0)
            prices = [Decimal(candle[key]) for key in ("open", "high", "low", "close")]
            volume = None if candle.get("volume") is None else Decimal(candle["volume"])
        except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
            raise CandleValidationError(
                f"provider candle {index} cannot be normalized"
            ) from exc
        if not all(value.is_finite() for value in prices):
            raise CandleValidationError(f"provider candle {index} has non-finite price")
        if volume is not None and (not volume.is_finite() or volume < 0):
            raise CandleValidationError(f"provider candle {index} has invalid volume")
        open_, high, low, close = prices
        if low > min(open_, close) or high < max(open_, close) or high < low:
            raise CandleValidationError(f"provider candle {index} violates OHLC bounds")
        if closed_only and timestamp + timedelta(minutes=int(timeframe["minutes"])) > current:
            continue
        normalized = {
            **candle,
            "timestamp": timestamp,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
        unique[(int(candle["symbol_id"]), timeframe["code"], timestamp)] = normalized
    return [unique[key] for key in sorted(unique, key=lambda item: item[2])]


def _empty_sql_result() -> dict[str, int]:
    return {
        "input": 0,
        "staged_inserted": 0,
        "staged_updated": 0,
        "fact_inserted": 0,
        "fact_updated": 0,
        "affected": 0,
        "skipped": 0,
    }


def _window(
    candles: list[dict[str, Any]],
    start: datetime | None,
    end: datetime,
) -> list[dict[str, Any]]:
    lower = None if start is None else utc(start)
    upper = utc(end)
    return [
        candle
        for candle in candles
        if (lower is None or candle["timestamp"] >= lower)
        and candle["timestamp"] <= upper
    ]


def fetch_and_store(
    config: dict[str, Any],
    symbol: dict[str, Any],
    timeframe: dict[str, Any],
    *,
    workflow: str,
    bars: int,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    require_coverage: bool = False,
    required_cursor: datetime | None = None,
    provider_candles: list[dict[str, Any]] | None = None,
    connection: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch one complete window and converge its provider-observed candles."""
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    current = utc(now or window_end or started_at)
    upper = utc(window_end or current)
    stage = "fetch"
    try:
        received = (
            fetch_candles(config, symbol, timeframe, int(bars))
            if provider_candles is None
            else provider_candles
        )
        stage = "validation"
        valid = validate_candles(
            received,
            timeframe,
            closed_only=bool(config["live"]["closed_candles_only"]),
            now=current,
        )
        stage = "coverage"
        if require_coverage and window_start is not None:
            lower = utc(window_start)
            if not valid or valid[0]["timestamp"] > lower:
                earliest = None if not valid else valid[0]["timestamp"].isoformat()
                raise CoverageError(
                    f"coverage did not reach {lower.isoformat()}; earliest={earliest}"
                )
        if required_cursor is not None:
            cursor = utc(required_cursor)
            if not valid or valid[-1]["timestamp"] < cursor:
                latest = None if not valid else valid[-1]["timestamp"].isoformat()
                raise CoverageError(
                    f"coverage did not retain cursor {cursor.isoformat()}; latest={latest}"
                )
        observed = _window(valid, window_start, upper)
        stage = "sql_compare"
        lock_timeout = float(config["sql_server"]["command_timeout_seconds"]) + 5.0
        guard = (
            interprocess_lock(config, "delivery", timeout_seconds=lock_timeout)
            if observed else nullcontext()
        )
        with guard:
            existing: dict[datetime, tuple[str | None, ...]] = {}
            if observed:
                existing = fetch_existing_candles(
                    config,
                    int(symbol["symbol_id"]),
                    timeframe["code"],
                    observed[0]["timestamp"],
                    observed[-1]["timestamp"],
                    connection=connection,
                )
            missing = changed = unchanged = 0
            for candle in observed:
                key = candle["timestamp"].replace(tzinfo=None)
                candle["_signature"] = signature = candle_signature(candle)
                if key not in existing:
                    missing += 1
                elif existing[key] != signature:
                    changed += 1
                else:
                    unchanged += 1
            needs_delivery = missing + changed > 0
            sql_result = _empty_sql_result()
            delivery = observed if needs_delivery else []
            if delivery:
                stage = "spool_enqueue"
                enqueue(config, delivery)
            if delivery:
                stage = "sql_delivery"
                sql_result = bulk_upsert_candles(
                    config,
                    timeframe,
                    delivery,
                    symbol_id=int(symbol["symbol_id"]),
                    connection=connection,
                )
            if delivery:
                stage = "spool_ack"
                ack(config, delivery)
    except AuthError:
        raise
    except Exception as exc:
        raise PipelineError(stage, exc) from exc
    result = {
        "workflow": workflow,
        "symbol": f"{symbol['exchange']}:{symbol['symbol']}",
        "timeframe": timeframe["code"],
        "started_at": started_at.isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "requested_bars": int(bars),
        "received": len(received),
        "valid": len(valid),
        "provider_observed": len(observed),
        "missing_before": missing,
        "changed_before": changed,
        "unchanged_before": unchanged,
        "delivery_input": len(delivery),
        "gap_basis": "provider_observed",
        "calendar_closures_ignored": True,
        **sql_result,
        "duration_seconds": round(time.monotonic() - started, 3),
    }
    log_event(
        LOGGER,
        logging.DEBUG if workflow == "live" else logging.INFO,
        "PAIR_COMPLETED",
        "NONE",
        component="pipeline",
        workflow=workflow,
        symbol=result["symbol"],
        timeframe=timeframe["code"],
        requested_bars=result["requested_bars"],
        provider_observed=result["provider_observed"],
        missing_before=missing,
        changed_before=changed,
        affected=result["affected"],
        gap_basis="provider_observed",
        calendar_closures_ignored=True,
        duration_seconds=result["duration_seconds"],
    )
    return result


def log_pair_failure(
    workflow: str,
    symbol: dict[str, Any],
    timeframe: dict[str, Any],
    error: Exception,
    *,
    stage: str = "planning",
) -> None:
    """Emit one secret-safe failure record with the durable recovery action."""
    failure_stage = error.stage if isinstance(error, PipelineError) else stage
    cause = error.cause if isinstance(error, PipelineError) else error
    log_event(
        LOGGER,
        logging.ERROR,
        "PAIR_FAILED",
        "HIGH",
        component="pipeline",
        workflow=workflow,
        symbol=f"{symbol['exchange']}:{symbol['symbol']}",
        timeframe=timeframe["code"],
        stage=failure_stage,
        error_type=type(cause).__name__,
        error=cause,
        action=_FAILURE_ACTIONS.get(failure_stage, "operator review required"),
    )
