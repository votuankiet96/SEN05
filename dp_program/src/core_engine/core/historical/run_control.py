"""CLI parsing, run scope, cooperative cancellation, and TradingView preflight
for the historical OHLCV pull engine.

Split out of the old runtime_support.py: this half owns everything about
*whether and how* a historical run starts and stops (argument parsing, which
symbols/timeframes are in scope, the cooperative stop/lease-loss signal, and
the pre-run TradingView connectivity probe). Gap-detection algorithm code
(deciding *what* needs backfilling) lives in gap_detection.py instead.
"""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core_engine.shared.tradingview import history_client as tv_history
from core_engine.settings import DIRECT_TFS, HISTORICAL_CANCEL_FILE

EXIT_TV_UNAVAILABLE = 3
PREFLIGHT_PROBE_BARS = 5
PREFLIGHT_TIMEOUT_SEC = 30
PREFLIGHT_SYMBOL_LIMIT = 4
PREFLIGHT_RETRIES = 1
PREFLIGHT_PREFERRED_SYMBOLS = ("US500", "EURUSD", "BTCUSD", "FR40")


class HistoricalPullCancelled(Exception):
    """Raised when the operator requested a cooperative historical stop."""


def cancel_file_path() -> str:
    configured = os.environ.get("DP_HISTORICAL_CANCEL_FILE", "").strip()
    return configured or str(HISTORICAL_CANCEL_FILE)


def cancel_requested() -> bool:
    path = cancel_file_path()
    return bool(path and os.path.exists(path))


def raise_if_cancelled(logger: logging.Logger, where: str = "") -> None:
    # Lease loss is equivalent to an operator cancellation from the data
    # path's perspective: finish no more symbol/timeframe units and unwind
    # through the normal finally blocks. Import lazily to keep this helper
    # independent of coordination module initialization order.
    from core_engine.util.coordination.locks import historical_lease_lost

    if historical_lease_lost():
        suffix = f" ({where})" if where else ""
        logger.critical(
            "[LOCK LOST] Historical lease was lost%s; stopping at safe checkpoint.",
            suffix,
        )
        raise HistoricalPullCancelled("historical database lock lease lost")
    if not cancel_requested():
        return
    suffix = f" ({where})" if where else ""
    logger.warning("[CANCEL] Historical pull cancellation requested%s; stopping at safe checkpoint.", suffix)
    raise HistoricalPullCancelled("historical pull cancelled by operator")


def _csv_set(value: str | None) -> set[str]:
    if value is None:
        return set()
    if value.strip().lower() in {"", "-", "none", "off", "no"}:
        return set()
    return {item.strip().upper() for item in value.split(",") if item.strip()}


def apply_replay_cli_options(
    args: Any,
    *,
    valid_tfs: set[str],
    set_runtime: Callable[[str, Any], None],
) -> list[str]:
    changes: list[str] = []
    if args.replay != "config":
        enabled = args.replay == "on"
        set_runtime("TV_WS_REPLAY_ENABLED", enabled)
        changes.append(f"enabled={'yes' if enabled else 'no'}")

    if args.replay_tfs is not None:
        replay_tfs = _csv_set(args.replay_tfs)
        invalid = sorted(replay_tfs - valid_tfs)
        if invalid:
            raise ValueError("Invalid --replay-tfs value(s): " + ",".join(invalid))
        set_runtime("TV_WS_REPLAY_TFS", replay_tfs)
        changes.append("tfs=" + (",".join(sorted(replay_tfs)) if replay_tfs else "-"))

    for arg_name, runtime_name, label in (
        ("replay_endpoint", "TV_WS_REPLAY_ENDPOINT", "endpoint"),
        ("replay_start_date", "TV_WS_REPLAY_START_DATE", "start"),
    ):
        value = getattr(args, arg_name, None)
        if value:
            set_runtime(runtime_name, str(value).lower() if "endpoint" in arg_name else value)
            changes.append(f"{label}={value}")

    for arg_name, runtime_name, label in (
        ("replay_max_windows", "TV_WS_REPLAY_MAX_WINDOWS_PER_PAIR", "max_windows"),
        ("replay_window_bars", "TV_WS_REPLAY_WINDOW_BARS", "window_bars"),
        ("replay_step_bars", "TV_WS_REPLAY_STEP_BARS", "step_bars"),
        ("replay_timeout_sec", "TV_WS_REPLAY_TIMEOUT_SEC", "timeout_sec"),
    ):
        value = getattr(args, arg_name, None)
        if value is not None:
            if value <= 0:
                raise ValueError(f"--{arg_name.replace('_', '-')} must be greater than 0")
            set_runtime(runtime_name, value)
            changes.append(f"{label}={value:g}" if isinstance(value, float) else f"{label}={value}")
    return changes


def build_parser(default_hole_lookback_days: int) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEN05 historical OHLCV pull engine")
    parser.add_argument("--mode", choices=["auto", "full", "gap", "reset"], default="auto")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--symbols", type=str, default=None)
    parser.add_argument("--reset", action="store_true", default=False)
    parser.add_argument("--yes", action="store_true", default=False)
    parser.add_argument("--timeframes", type=str, default=None)
    parser.add_argument("--asset-type", type=str, default=None)
    parser.add_argument("--force-unlock", action="store_true", default=False)
    parser.add_argument("--on-conflict", choices=["skip", "wait", "replace"], default="skip")
    parser.add_argument("--replay", choices=["config", "on", "off"], default="config")
    parser.add_argument("--replay-tfs", type=str, default=None)
    parser.add_argument("--replay-endpoint", type=str, default=None)
    parser.add_argument("--replay-start-date", type=str, default=None)
    parser.add_argument("--replay-max-windows", type=int, default=None)
    parser.add_argument("--replay-window-bars", type=int, default=None)
    parser.add_argument("--replay-step-bars", type=int, default=None)
    parser.add_argument("--replay-timeout-sec", type=float, default=None)
    parser.add_argument("--hole-lookback-days", type=int, default=default_hole_lookback_days)
    return parser


@dataclass(frozen=True)
class ScopeEvent:
    action: str
    amount: str = ""
    status: str = ""


@dataclass(frozen=True)
class ScopeResult:
    symbols: list[dict[str, Any]]
    timeframe_filter: set[str]
    events: list[ScopeEvent] = field(default_factory=list)
    error_action: str | None = None
    error_amount: str = ""
    error_status: str = ""

    @property
    def ok(self) -> bool:
        return self.error_action is None


def _csv_upper(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip().upper() for item in value.split(",") if item.strip()}


def _csv_exact(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


def resolve_scope(
    symbols: list[dict[str, Any]],
    *,
    asset_type_csv: str | None = None,
    symbols_csv: str | None = None,
    timeframes_csv: str | None = None,
) -> ScopeResult:
    selected = list(symbols)
    events: list[ScopeEvent] = []

    if asset_type_csv:
        asset_filter = _csv_exact(asset_type_csv)
        selected = [s for s in selected if s.get("asset_type") in asset_filter]
        if not selected:
            return ScopeResult(selected, set(), error_action="asset_filter", error_amount=asset_type_csv, error_status="no_matching_symbols")
        events.append(ScopeEvent("asset_type", asset_type_csv, f"symbols {len(selected):,}"))

    if symbols_csv:
        symbol_filter = _csv_upper(symbols_csv)
        selected = [s for s in selected if str(s.get("tv_symbol", "")).upper() in symbol_filter]
        if not selected:
            return ScopeResult(selected, set(), error_action="symbol_filter", error_amount=symbols_csv, error_status="no_matching_symbols")
        events.append(ScopeEvent("symbols", f"count {len(selected):,}", ",".join(str(s.get("tv_symbol", "")) for s in selected)))

    timeframe_filter = _csv_upper(timeframes_csv)
    if timeframe_filter:
        invalid_tfs = sorted(timeframe_filter - set(DIRECT_TFS))
        if invalid_tfs:
            return ScopeResult(
                selected,
                set(),
                error_action="timeframe_filter",
                error_amount=",".join(invalid_tfs),
                error_status="invalid_timeframe",
            )
        events.append(ScopeEvent("timeframes", status=",".join(sorted(timeframe_filter))))
    return ScopeResult(selected, timeframe_filter, events)


def symbol_candidates(symbols: list[dict]) -> list[dict]:
    if not symbols:
        return []
    by_symbol = {str(sym.get("tv_symbol", "")).strip().upper(): sym for sym in symbols if str(sym.get("tv_symbol", "")).strip()}
    chosen: list[dict] = []
    seen: set[str] = set()

    def add(sym: dict | None) -> None:
        if not sym:
            return
        key = str(sym.get("tv_symbol", "")).strip().upper()
        if key and key not in seen:
            chosen.append(sym)
            seen.add(key)

    for preferred in PREFLIGHT_PREFERRED_SYMBOLS:
        add(by_symbol.get(preferred))
    for sym in symbols:
        add(sym)
        if len(chosen) >= PREFLIGHT_SYMBOL_LIMIT:
            break
    return chosen[:PREFLIGHT_SYMBOL_LIMIT]


def tv_probe(logger: logging.Logger, *, symbols: list[dict], direct_tfs: set[str]) -> tuple[bool, str]:
    candidates = symbol_candidates(symbols)
    if not candidates:
        return True, "no symbols configured"
    probe_tf = "H1" if "H1" in direct_tfs else next(iter(direct_tfs), "H1")
    attempts: list[str] = []
    for sym in candidates:
        for attempt in range(1, PREFLIGHT_RETRIES + 1):
            try:
                res = tv_history.fetch_history(
                    symbol=sym["tv_symbol"],
                    exchange=sym["tv_exchange"],
                    tf_code=probe_tf,
                    n_bars=PREFLIGHT_PROBE_BARS,
                    logger=logger,
                    timeout_sec=float(PREFLIGHT_TIMEOUT_SEC),
                )
            except Exception as exc:
                attempts.append(f"{sym['tv_symbol']} {probe_tf}: probe error={exc} attempt={attempt}/{PREFLIGHT_RETRIES}")
                continue
            status = str(getattr(res, "status", "") or "unknown")
            returned = int(getattr(res, "returned", 0) or 0)
            endpoint = str(getattr(res, "endpoint", "") or "-")
            error = str(getattr(res, "error", "") or "").strip()
            detail = f"{sym['tv_symbol']} {probe_tf}: status={status}, returned={returned}, endpoint={endpoint}"
            if error:
                detail += f", error={error[:180]}"
            if returned > 0:
                return True, detail
            attempts.append(detail)
    return False, "; ".join(attempts[: PREFLIGHT_SYMBOL_LIMIT * PREFLIGHT_RETRIES]) or "no TradingView probe returned bars"
