"""Read-only query boundary between the Dash UI and core_engine artifacts.

This module never imports facilitator or walkforward, never claims/records an
ExperimentStore run, and never calls a calculation module. Dashboard session
metadata lives in session_store.py; this file only reads core-owned profiles,
reports, SQLite rows, locks, and persisted logs.
"""
from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any

try:
    from . import signal_sources
except ImportError:  # app.py imports dashboard modules without a package prefix.
    import signal_sources

_BO_WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
if str(_BO_WORKFLOW_ROOT) not in sys.path:
    sys.path.insert(0, str(_BO_WORKFLOW_ROOT))

from core_engine.configuration import (  # noqa: E402
    load_engine_profile,
    load_redis_profile,
    load_strategy_profile,
)
from core_engine import signal_trans  # noqa: E402
from core_engine.output_util import evidence, pipeline_lock, selection, walkforward_readout  # noqa: E402
from core_engine.signal_trans import SYMBOL_GROUPS  # noqa: E402
from core_engine.store import DAILY_CLI_LAUNCH_LIMIT, ExperimentStore, ProtocolStore  # noqa: E402

_CLOSE_REASON_RE = re.compile(r"\bCLOSED position\b.*?\breason=(?P<reason>[^:]+):", re.IGNORECASE)
_BOT_LINE_RE = re.compile(
    r"^(?P<time>[^|]+?)\s*\|\s*(?P<level>[^|]+?)\s*\|\s*(?P<message>.+)$"
)
_SYSTEM_LOG_LINE_RE = re.compile(r"^(?P<level>Info|Trade|Warning|Error)\s*\|\s*(?P<message>.+)$", re.IGNORECASE)
_POSITION_CLOSE_RE = re.compile(
    r"CLOSED position\s+(?P<position>\d+).*?reason=(?P<reason>[^:]+):.*?net=\$(?P<net>-?[\d.,]+)",
    re.IGNORECASE,
)
_POSITION_FILL_RE = re.compile(
    r"FILLED pending\s+\d+\s+=>\s+position\s+(?P<position>\d+)\s+(?P<side>Buy|Sell)\s+.*?\sat\s+(?P<price>[\d.]+)",
    re.IGNORECASE,
)
_PENDING_PLACE_RE = re.compile(
    r"pending\s+(?P<side>Buy|Sell)\s+placed.*?valid for the next\s+(?P<bars>\d+)\s+chart bar", re.IGNORECASE
)
_SIDE_RE = re.compile(r"\b(?P<side>Buy|Sell)\b", re.IGNORECASE)
_POSITION_RE = re.compile(r"\bposition\s+(?P<id>\d+)\b", re.IGNORECASE)
_PENDING_RE = re.compile(r"\bpending\s+(?P<id>\d+)\b", re.IGNORECASE)
_ORDER_RE = re.compile(r"\b(?P<kind>OID|PID)(?P<id>\d+)\b", re.IGNORECASE)

STAGE_ORDER = ("grid", "walkforward", "dsr", "pbo", "final", "monte_carlo")
STAGE_LABELS = {
    "grid": "1. Independent grid",
    "walkforward": "2. Walk-forward",
    "dsr": "3a. DSR",
    "pbo": "3b. PBO",
    "final": "4. Final backtest",
    "monte_carlo": "5. Monte Carlo",
}
SESSION_STRATEGY_PROFILES = {"combo": "combo-v1", "macross": "macross-v1"}


def _fib_label(level: str) -> str:
    """Render a core Fib enum value for people while retaining its exact value."""
    digits = str(level).removeprefix("Fib")
    if len(digits) == 4 and digits.isdigit():
        return f"Fib {digits[0]}.{digits[1:]}"
    return str(level)


def session_form_options() -> dict[str, Any]:
    """Đọc profile hiện hữu để UI không tự duy trì một bản parameter khác."""
    strategies = []
    for strategy, profile_id, label in (
        ("combo", "combo-v1", "Combo"),
        ("macross", "macross-v1", "MA Cross"),
    ):
        profile = load_strategy_profile(profile_id)
        ksl_values = profile.params["KslLevel"].enum_values
        ktp_values = profile.params["KtpLevel"].enum_values
        strategies.append({
            "value": strategy,
            "label": label,
            "timeframes": list(profile.default_timeframes),
            "ksl_options": [
                {"value": str(index), "label": _fib_label(level)}
                for index, level in enumerate(ksl_values)
            ],
            "ktp_options": [
                {"value": str(index), "label": _fib_label(level)}
                for index, level in enumerate(ktp_values)
            ],
            "default_ksl": str(ksl_values.index(profile.params["KslLevel"].default)),
            "default_ktp": str(ktp_values.index(profile.params["KtpLevel"].default)),
            "defaults": profile.defaults(),
        })
    engine_id = "ctrader-5.9.16-ticks-approxfx-v1"
    engine = load_engine_profile(engine_id)
    # This is a read-only import of the canonical symbol-to-Redis mapping. It
    # does not connect to Redis; a run still validates signal availability in
    # the core execution path.
    symbols = [
        {"value": symbol, "label": symbol}
        for symbol in sorted(SYMBOL_GROUPS)
    ]
    return {
        "strategies": strategies,
        "symbols": symbols,
        "engines": [{
            "value": engine_id,
            "label": f"{engine_id} ({engine.data_mode}, {engine.backend})",
            "data_mode": engine.data_mode,
        }],
    }


def signal_date_bounds(
    symbol: str, timeframe: str, strategy: str, *, redis_profile: str = signal_sources.ORIGINAL,
) -> dict[str, Any]:
    """Read the available signal-date range for one dashboard form selection.

    This is a query-only Redis operation: it reads the signal List timestamps,
    never materializes CSV, writes Redis, or starts a core-engine run.
    """
    normalized_symbol = str(symbol).strip()
    normalized_timeframe = str(timeframe).strip().lower()
    normalized_strategy = str(strategy).strip().lower().replace(" ", "")
    if normalized_symbol not in SYMBOL_GROUPS:
        return {"available": False, "error": "The selected symbol has no signal mapping."}
    profile_id = SESSION_STRATEGY_PROFILES.get(normalized_strategy)
    if not profile_id:
        return {"available": False, "error": "The selected strategy has no signal mapping."}

    profile = load_strategy_profile(profile_id)
    redis_strategy = signal_trans.REDIS_STRATEGY_TOKENS.get(profile.strategy)
    if not redis_strategy:
        return {"available": False, "error": "The selected strategy has no Redis signal token."}
    client = None
    try:
        profile = load_redis_profile(signal_sources.require_profile(redis_profile))
        key = signal_trans.signal_list_key(
            SYMBOL_GROUPS[normalized_symbol], normalized_timeframe, redis_strategy,
            key_prefix=profile.key_prefix,
        )
        client = signal_trans.redis_client(profile)
        stamps = client.lrange(key, 0, -1)
        dates = []
        for stamp in stamps:
            try:
                dates.append(date.fromisoformat(str(stamp).split(" ", 1)[0]))
            except ValueError:
                continue
        if not dates:
            return {
                "available": False,
                "error": "No usable Redis signal history exists for this selection.",
            }
        return {
            "available": True,
            "earliest_date": min(dates).isoformat(),
            "latest_date": max(dates).isoformat(),
        }
    except Exception as exc:
        return {
            "available": False,
            "error": f"Could not read Redis signal history: {type(exc).__name__}.",
        }
    finally:
        if client is not None:
            client.close()


def signal_date_bounds_for_selection(
    symbols: list[str] | tuple[str, ...],
    timeframes: list[str] | tuple[str, ...],
    strategy: str,
    *,
    redis_profile: str = signal_sources.ORIGINAL,
) -> dict[str, Any]:
    """Return the safe common start cutoff for a form selection.

    Each individual draft still has one symbol and one timeframe.  This helper
    only reads Redis so the UI can validate every prospective pair before it
    persists *any* dashboard drafts.  The common cutoff is the latest of the
    individual first-signal dates; this prevents a group from silently
    containing a draft with an unavailable requested start date.
    """
    normalized_symbols = _unique_choices(symbols)
    normalized_timeframes = _unique_choices(timeframes, lower=True)
    if not normalized_symbols:
        return {"available": False, "error": "Select at least one symbol."}
    if not normalized_timeframes:
        return {"available": False, "error": "Select at least one timeframe."}

    pair_bounds: list[dict[str, str]] = []
    unavailable: list[str] = []
    for symbol in normalized_symbols:
        for timeframe in normalized_timeframes:
            bounds = signal_date_bounds(symbol, timeframe, strategy, redis_profile=redis_profile)
            if not bounds.get("available"):
                unavailable.append(f"{symbol} {timeframe.upper()}: {bounds.get('error', 'unavailable')}")
                continue
            pair_bounds.append({
                "symbol": symbol,
                "timeframe": timeframe,
                "earliest_date": str(bounds["earliest_date"]),
                "latest_date": str(bounds["latest_date"]),
            })
    if unavailable:
        return {
            "available": False,
            "error": "Signal history is unavailable for: " + "; ".join(unavailable),
        }

    earliest = max(item["earliest_date"] for item in pair_bounds)
    latest = min(item["latest_date"] for item in pair_bounds)
    return {
        "available": True,
        "earliest_date": earliest,
        "latest_date": latest,
        "pair_count": len(pair_bounds),
        "pairs": pair_bounds,
    }


def _unique_choices(values: Any, *, lower: bool = False) -> list[str]:
    """Normalise Dash single/multi select values while preserving their order."""
    raw_values = [values] if isinstance(values, str) else list(values or [])
    seen: set[str] = set()
    result: list[str] = []
    for raw in raw_values:
        value = str(raw).strip()
        if lower:
            value = value.lower()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def list_pipelines() -> list[str]:
    """Mọi pipeline đã từng ghi sổ; không tạo lock khi chỉ xem."""
    return pipeline_lock.list_pipelines()


def load_pipeline(pipeline: str) -> dict[str, Any]:
    """Đọc toàn bộ trạng thái pipeline đã tồn tại."""
    lock = pipeline_lock.read_all(pipeline)
    stages_raw = lock.get("stages", {})
    stages: dict[str, Any] = {}
    for name in STAGE_ORDER:
        entry = stages_raw.get(name)
        stages[name] = {"status": "pending", "label": STAGE_LABELS[name]} if entry is None else {
            "status": "done", "label": STAGE_LABELS[name], **entry,
        }
    return {"pipeline": pipeline, "stages": stages}


def grid_rows(experiment: str, *, method: str = "grid", min_trades: int = 0) -> list[dict[str, Any]]:
    """Các run eligible đã tồn tại, xếp hạng thuần để trình bày."""
    store = ExperimentStore(method, experiment)
    if not store.db_path.is_file():
        return []
    return selection.rank(store.flat_rows(), min_trades=min_trades)


def grid_execution_status(experiment: str) -> dict[str, Any]:
    """Read lightweight per-run status counts for a live Grid Search."""
    store = ExperimentStore("grid", experiment)
    if not store.db_path.is_file():
        return {"exists": False, "started": 0, "finished": 0, "running": 0, "counts": {}}
    rows = store.rows()
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "exists": True,
        "started": len(rows),
        "finished": sum(count for status, count in counts.items() if status != "running"),
        "running": counts.get("running", 0),
        "counts": counts,
    }


def walkforward_execution_status(experiment: str) -> dict[str, Any]:
    """Read the persisted train/OOS state without importing the executor."""
    progress = walkforward_readout.walkforward_progress(str(experiment))
    progress["last_ok_utc"] = None
    store = ExperimentStore("walkforward", str(experiment))
    if progress.get("exists") and store.db_path.is_file():
        try:
            with sqlite3.connect(store.db_path.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
                row = db.execute(
                    "SELECT MAX(ended_utc) FROM runs WHERE zone IN ('train','oos') AND status='ok'"
                ).fetchone()
            progress["last_ok_utc"] = row[0] if row else None
        except sqlite3.Error:
            # Progress counts remain useful during a concurrent SQLite write.
            pass
    return progress


def walkforward_results(experiment: str) -> list[dict[str, Any]]:
    """Read the window table and attach persisted OOS quality flags by run ID."""
    rows = walkforward_readout.walkforward_table(str(experiment))
    if not rows:
        return rows
    stored = {str(row.get("run_id")): row for row in ExperimentStore("walkforward", str(experiment)).flat_rows()}
    enriched = []
    for row in rows:
        item = dict(row)
        test = stored.get(str(row.get("test_run_id"))) if row.get("test_run_id") else None
        if test is None:
            item.update(test_quality="Not run", test_strict_eligible=False,
                        test_completed_execution_eligible=False, test_quality_reasons=[])
        else:
            quality = selection.classify(test)
            reasons = list(quality["reasons"])
            if not quality["execution_completed"]:
                label = "Execution incomplete"
            elif not quality["report_valid"]:
                label = "Report invalid"
            elif quality["margin_rejections"] is None:
                label = "Margin unknown"
            elif quality["margin_rejections"] > 0:
                label = "Margin rejection"
            else:
                label = "Research-valid"
            item.update(test_quality=label,
                        test_strict_eligible=bool(quality["strict_research_eligible"]),
                        test_completed_execution_eligible=bool(quality["completed_execution_eligible"]),
                        test_quality_reasons=reasons)
        enriched.append(item)
    return enriched


def walkforward_train_signal_counts(
    symbol: str, timeframe: str, strategy: str, windows: list[Any],
    *, redis_profile: str = signal_sources.ORIGINAL,
) -> list[int]:
    """Count Redis List timestamps inside each IS window; never write Redis or core data."""
    normalized_symbol = str(symbol).strip()
    if normalized_symbol not in SYMBOL_GROUPS:
        raise ValueError("The selected symbol has no signal mapping")
    profile_id = SESSION_STRATEGY_PROFILES.get(str(strategy).strip().lower().replace(" ", ""))
    if not profile_id:
        raise ValueError("The selected strategy has no signal mapping")
    profile = load_strategy_profile(profile_id)
    redis_strategy = signal_trans.REDIS_STRATEGY_TOKENS.get(profile.strategy)
    if not redis_strategy:
        raise ValueError("The selected strategy has no Redis signal token")
    selected_profile = load_redis_profile(signal_sources.require_profile(redis_profile))
    key = signal_trans.signal_list_key(
        SYMBOL_GROUPS[normalized_symbol], str(timeframe).lower(), redis_strategy,
        key_prefix=selected_profile.key_prefix,
    )
    client = signal_trans.redis_client(selected_profile)
    try:
        stamps = client.lrange(key, 0, -1)
    finally:
        client.close()
    dates = []
    for stamp in stamps:
        try:
            dates.append(date.fromisoformat(str(stamp).split(" ", 1)[0]))
        except ValueError:
            continue
    return [sum(1 for day in dates if train.start <= day < train.end) for train, _oos in windows]


def cli_launch_budget() -> dict[str, int]:
    """Read today's Prague-day CLI launch allowance; never reserve a launch."""
    used = int(ProtocolStore().cli_launches_today())
    return {"limit": DAILY_CLI_LAUNCH_LIMIT, "used": used, "remaining": max(0, DAILY_CLI_LAUNCH_LIMIT - used)}


def commission_warning(symbol: str) -> str | None:
    """Known FTMO commission omissions in the fixed cTrader engine profile."""
    notices = {
        "XAUUSD": "Commission is not modelled (FTMO charges 0.0007% of volume); results are optimistic.",
        "BTCUSD": "Commission is not modelled (FTMO charges 0.0325% of volume); results are optimistic.",
    }
    return notices.get(str(symbol).upper())


def grid_snapshot(experiment: str, *, min_trades: int = 0) -> dict[str, Any]:
    """Read a Grid Search result for presentation without mutating its store."""
    store = ExperimentStore("grid", experiment)
    if not store.db_path.is_file():
        return {"exists": False, "rows": [], "ranked": [], "counts": {}}
    rows = store.flat_rows()
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "exists": True,
        "rows": rows,
        "ranked": selection.rank(rows, min_trades=min_trades),
        "counts": counts,
    }


def grid_diagnostics_source_snapshot(
    experiment: str, *, min_trades: int = 0
) -> dict[str, Any]:
    """Read and fingerprint a terminal Grid source for DSR/PBO preflight."""
    store = ExperimentStore("grid", experiment)
    if not store.db_path.is_file():
        return {
            "exists": False,
            "row_count": 0,
            "completed_execution_count": 0,
            "eligible_count": 0,
            "margin_rejection_trial_count": 0,
            "margin_rejection_event_count": 0,
            "artifact_sha256": "",
            "counts": {},
        }
    rows = store.flat_rows()
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    # Classification belongs to core. The dashboard only presents the two
    # named policies that core DSR/PBO understands; it must not reproduce the
    # decision rules locally.
    completed_execution_count = 0
    strict_eligible_count = 0
    margin_rejection_trial_count = 0
    margin_rejection_event_count = 0
    ineligible_reasons: dict[str, int] = {}
    for row in rows:
        classification = selection.classify(row, min_trades=int(min_trades))
        margin_rejections = classification["margin_rejections"]
        if margin_rejections:
            margin_rejection_event_count += int(margin_rejections)
            if int(margin_rejections) > 0:
                margin_rejection_trial_count += 1
        if selection.trial_included(classification, selection.COMPLETED_EXECUTION_POLICY):
            completed_execution_count += 1
        if selection.trial_included(classification, selection.STRICT_RESEARCH_POLICY):
            strict_eligible_count += 1
            continue
        reasons = selection.exclusion_reasons(
            classification, selection.STRICT_RESEARCH_POLICY,
        )
        for reason in reasons or ["unknown eligibility failure"]:
            ineligible_reasons[reason] = ineligible_reasons.get(reason, 0) + 1
    digest = hashlib.sha256()
    with store.db_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "exists": True,
        "row_count": len(rows),
        "completed_execution_count": completed_execution_count,
        "eligible_count": strict_eligible_count,
        "margin_rejection_trial_count": margin_rejection_trial_count,
        "margin_rejection_event_count": margin_rejection_event_count,
        "artifact_sha256": digest.hexdigest(),
        "counts": counts,
        "ineligible_reasons": ineligible_reasons,
    }


def read_single_backtest(experiment: str) -> dict[str, Any]:
    """Đọc một execution session mà không tạo/mở/claim ExperimentStore.

    A successful core run always persists report.json.gz. bot.log only exists
    when ``keep_logs=True`` (the dashboard command sets that flag); cli.log is
    necessarily absent for successful runs under the current core contract.
    """
    store = ExperimentStore("grid", experiment)
    if not store.db_path.is_file():
        return {"exists": False, "rows": []}
    try:
        rows = store.flat_rows()
    except Exception as exc:
        return {"exists": True, "rows": [], "read_error": f"{type(exc).__name__}: {exc}"}
    if not rows:
        return {"exists": True, "rows": []}
    # A Single Backtest execution produces exactly one RunSpec. If an artifact
    # is malformed and has more, return all row summaries but show the latest.
    row = rows[-1]
    run_dir = store.run_dir(str(row["run_id"]), str(row["label"]))
    report_path = run_dir / "report.json.gz"
    report: dict[str, Any] = {}
    if report_path.is_file():
        try:
            report = evidence.read_report(report_path)
        except Exception as exc:
            return {
                "exists": True, "rows": rows, "row": row,
                "read_error": f"Could not read report: {type(exc).__name__}: {exc}",
            }
    execution = _read_json(run_dir / "execution.json")
    points = ((report.get("equity") or {}).get("points") or [])
    history = ((report.get("history") or {}).get("items") or [])
    bot_log = _read_text(run_dir / "bot.log")
    return {
        "exists": True,
        "rows": rows,
        "row": row,
        "run_dir": str(run_dir),
        "metrics": evidence.metrics(report) if report else {},
        "execution": execution,
        "equity_points": points,
        "history": history,
        "bot_log": bot_log,
        "bot_events": _bot_events(bot_log),
        "cli_log": _read_text(run_dir / "cli.log"),
        "execution_summary": _execution_summary(execution, bot_log, history),
        "has_report": bool(report),
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _execution_summary(
    execution: dict[str, Any], bot_log: str, history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Extract UI-facing run evidence without treating a P&L sign as an exit reason."""
    raw_summary = execution.get("bot_summary") or {}
    summary = {
        key: _nonnegative_int(raw_summary.get(key))
        for key in (
            "loaded", "before_start", "processed", "placed", "filled",
            "pending_expired", "same_direction_skipped", "reversal_cancels",
            "margin_rejections", "guard_skipped", "not_processed", "failed",
        )
    }
    reasons: dict[str, int] = {}
    for match in _CLOSE_REASON_RE.finditer(bot_log):
        reason = match.group("reason").strip().casefold().replace(" ", "")
        reasons[reason] = reasons.get(reason, 0) + 1
    take_profit = reasons.get("takeprofit", 0)
    stop_loss = reasons.get("stoploss", 0)
    known_exits = take_profit + stop_loss
    total_exits = sum(reasons.values())
    summary.update({
        "take_profit_exits": take_profit,
        "stop_loss_exits": stop_loss,
        "other_exits": total_exits - known_exits,
        "exit_events": total_exits,
        "unattributed_exits": max(0, len(history) - total_exits),
        "exit_attribution_complete": bool(history) and total_exits == len(history),
    })
    return summary


def _nonnegative_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _bot_events(bot_log: str) -> list[dict[str, str]]:
    """Project every persisted cBot log line into structured display columns."""
    events: list[dict[str, str]] = []
    for line in bot_log.splitlines():
        cleaned_line = _clean_log_text(line)
        if not cleaned_line:
            continue
        parsed = _BOT_LINE_RE.match(cleaned_line)
        if parsed:
            timestamp = parsed.group("time").strip()
            level = parsed.group("level").strip()
            message = parsed.group("message").strip()
        else:
            system_line = _SYSTEM_LOG_LINE_RE.match(cleaned_line)
            timestamp = ""
            level = system_line.group("level").strip() if system_line else "System"
            message = system_line.group("message").strip() if system_line else cleaned_line
        normalised = _normalise_bot_event(message)
        fields = _log_fields(message)
        events.append({
            "time": timestamp,
            "level": level,
            "event": normalised.get("event") if normalised else _log_activity(level, message),
            "detail": message,
            "net": normalised.get("net", "") if normalised else "",
            "tone": normalised.get("tone", "neutral") if normalised else _log_tone(level),
            **fields,
        })
    return events


def _clean_log_text(value: str) -> str:
    cleaned = "".join(character if character.isprintable() else " " for character in value)
    return " ".join(cleaned.split())


def _log_tone(level: str) -> str:
    lowered = level.casefold()
    if "error" in lowered or "fail" in lowered:
        return "loss"
    if "trade" in lowered:
        return "info"
    return "neutral"


def _log_activity(level: str, message: str) -> str:
    lowered = message.casefold()
    if "risk_detail" in lowered:
        return "Risk calculation"
    if "fx bartime" in lowered:
        return "FX conversion"
    if "loaded" in lowered and "signal row" in lowered:
        return "Signal file loaded"
    if "signal scheduling active" in lowered:
        return "Signal scheduling"
    if "placing stop order" in lowered:
        return "Order request"
    if "succeeded, pendingorder" in lowered:
        return "Pending order accepted"
    if "cancelling pending order" in lowered and "succeeded" in lowered:
        return "Pending order cancelled"
    if "cancelling pending order" in lowered:
        return "Pending order cancellation"
    return level.title()


def _log_fields(message: str) -> dict[str, str]:
    """Extract optional columns while retaining the full message separately."""
    side_match = _SIDE_RE.search(message)
    position_match = _POSITION_RE.search(message)
    pending_match = _PENDING_RE.search(message)
    order_match = _ORDER_RE.search(message)
    reference = ""
    if position_match:
        reference = f"Position #{position_match.group('id')}"
    elif pending_match:
        reference = f"Pending #{pending_match.group('id')}"
    elif order_match:
        reference = f"{order_match.group('kind').upper()} {order_match.group('id')}"
    price_parts = _labelled_values(message, (("Price", "Price"), ("entry", "Entry"), ("close", "Close"), ("trigger", "Trigger")))
    protection_parts = _labelled_values(message, (("SL", "SL"), ("TP", "TP")))
    account_parts = _labelled_values(message, (("balance", "Balance"), ("risk", "Risk"), ("budget", "Budget"), ("ATR", "ATR")))
    return {
        "side": side_match.group("side").title() if side_match else "",
        "reference": reference,
        "price_context": " · ".join(price_parts),
        "protection": " · ".join(protection_parts),
        "account_context": " · ".join(account_parts),
    }


def _labelled_values(message: str, labels: tuple[tuple[str, str], ...]) -> list[str]:
    parts: list[str] = []
    for source, display in labels:
        match = re.search(
            rf"\b{re.escape(source)}\s*(?::|=|\s)\s*\$?(?P<value>-?[\d.]+%?)",
            message,
            re.IGNORECASE,
        )
        if match:
            parts.append(f"{display} {match.group('value')}")
    return parts


def _normalise_bot_event(message: str) -> dict[str, str] | None:
    if "CBot instance" in message and message.endswith("started."):
        return {"event": "Backtest started", "detail": "cBot instance started", "tone": "info"}
    if "CBot instance" in message and message.endswith("stopped."):
        return {"event": "Backtest finished", "detail": "cBot instance stopped", "tone": "info"}
    if ": " not in message:
        return None
    bot_message = message.split(": ", 1)[1]
    closed = _POSITION_CLOSE_RE.search(bot_message)
    if closed:
        reason = closed.group("reason").strip()
        label = {
            "takeprofit": "Take profit",
            "stoploss": "Stop loss",
        }.get(reason.casefold().replace(" ", ""), reason)
        return {
            "event": f"Position closed — {label}",
            "detail": f"Position #{closed.group('position')}",
            "net": closed.group("net"),
            "tone": "profit" if label == "Take profit" else "loss" if label == "Stop loss" else "neutral",
        }
    filled = _POSITION_FILL_RE.search(bot_message)
    if filled:
        return {
            "event": f"Position filled — {filled.group('side').title()}",
            "detail": f"Position #{filled.group('position')} · entry {filled.group('price')}",
            "tone": "info",
        }
    placed = _PENDING_PLACE_RE.search(bot_message)
    if placed:
        return {
            "event": f"Pending order placed — {placed.group('side').title()}",
            "detail": f"Expires after {placed.group('bars')} chart bars if unfilled",
            "tone": "info",
        }
    if "pending order" in bot_message and "cancelled after" in bot_message:
        return {"event": "Pending order expired", "detail": "Unfilled order cancelled by its expiry rule", "tone": "neutral"}
    if "signal skipped: already have same-direction exposure" in bot_message:
        return {"event": "Signal skipped", "detail": "Same-direction exposure was already open", "tone": "neutral"}
    if "reversal - closing existing" in bot_message:
        return {"event": "Reversal", "detail": "Existing opposite position closed before reversal", "tone": "neutral"}
    return None
