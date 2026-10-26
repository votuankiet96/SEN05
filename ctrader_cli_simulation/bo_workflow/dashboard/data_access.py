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
import statistics
import sys
from contextlib import closing
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    from . import channel_selection, signal_sources
except ImportError:  # app.py imports dashboard modules without a package prefix.
    import channel_selection
    import signal_sources

_BO_WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
if str(_BO_WORKFLOW_ROOT) not in sys.path:
    sys.path.insert(0, str(_BO_WORKFLOW_ROOT))

from core_engine import master_db  # noqa: E402
from core_engine.configuration import (  # noqa: E402
    load_backtest_settings,
    load_engine_profile,
    load_master_db_profile,
    load_portfolio_risk_settings,
    load_redis_profile,
    load_strategy_profile,
)
from core_engine import signal_trans  # noqa: E402
from core_engine.output_util import evidence, pipeline_lock, selection, walkforward_readout  # noqa: E402
from core_engine.signal_trans import SYMBOL_GROUPS  # noqa: E402
from core_engine.store import DAILY_CLI_LAUNCH_LIMIT, RUNS_ROOT, ExperimentStore, ProtocolStore  # noqa: E402

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
    r"pending\s+<?(?P<code>cb|mc|mb)\s+(?P<timeframe>[A-Z0-9]+)\s+"
    r"(?P<side>Buy|Sell)>?\s+placed at\s+(?P<price>[\d.]+).*?"
    r"valid until\s+(?P<valid_until>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}).*?"
    r"leg\s+(?P<leg>\d+)/(?P<legs>\d+)",
    re.IGNORECASE,
)
_MARKET_PLACE_RE = re.compile(
    r"market\s+<?(?P<code>cb|mc|mb)\s+(?P<timeframe>[A-Z0-9]+)\s+"
    r"(?P<side>Buy|Sell)>?\s+placed;.*?SL=(?P<sl>[\d.]+)\s+pips,\s+"
    r"TP=(?P<tp>[\d.]+)\s+pips;.*?leg\s+(?P<leg>\d+)/(?P<legs>\d+)",
    re.IGNORECASE,
)
_ORDER_REJECT_RE = re.compile(
    r"(?P<kind>pending|market)\s+<?(?P<code>cb|mc|mb)\s+(?P<timeframe>[A-Z0-9]+)\s+"
    r"(?P<side>Buy|Sell)>?(?:\s+at\s+[\d.]+)?\s+was rejected:\s+(?P<reason>.+?)\s+"
    r"\(leg\s+(?P<leg>\d+)/(?P<legs>\d+),",
    re.IGNORECASE,
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
SESSION_STRATEGY_PROFILES = {
    "combo": "backtestbot-v1",
    "ma_cross": "backtestbot-v1",
    "macd_break": "backtestbot-v1",
}


def _fib_label(level: str) -> str:
    """Render a core Fib enum value for people while retaining its exact value."""
    digits = str(level).removeprefix("Fib")
    if len(digits) == 4 and digits.isdigit():
        return f"Fib {digits[0]}.{digits[1:]}"
    return str(level)


def _profile_timeframes(profile: Any, strategy: str) -> list[str]:
    """Expose the BacktestBot signal frames for one selected strategy token."""
    return [item.lower() for item in profile.strategy_frames[strategy]]


def session_form_options() -> dict[str, Any]:
    """Đọc profile hiện hữu để UI không tự duy trì một bản parameter khác."""
    strategies = []
    for strategy, label in (
        ("combo", "Combo"),
        ("ma_cross", "MA Cross"),
        ("macd_break", "MACD Break"),
    ):
        profile_id = SESSION_STRATEGY_PROFILES[strategy]
        profile = load_strategy_profile(profile_id)
        ksl_values = profile.params["KslLevel"].enum_values
        ktp_values = profile.params["KtpLevel"].enum_values
        strategies.append({
            "value": strategy,
            "label": label,
            "timeframes": _profile_timeframes(profile, strategy),
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
    backtest_defaults = load_backtest_settings().get("defaults") or {}
    canonical_fixed = load_strategy_profile("backtestbot-v1").resolve_params(
        backtest_defaults.get("fixed_params") or {}
    )
    portfolio_risk = load_portfolio_risk_settings()
    engine_id = str(backtest_defaults["engine_profile"])
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
        "backtest_defaults": {
            "balance": float(backtest_defaults.get("balance", 100000)),
            "fixed_params": canonical_fixed,
        },
        "portfolio_risk": {
            "total_risk_percent": portfolio_risk.total_risk_percent,
            "risk_decimals": portfolio_risk.risk_decimals,
        },
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

    if normalized_strategy not in signal_trans.STRATEGY_COLUMNS:
        return {"available": False, "error": "The selected strategy has no Redis signal token."}
    redis_strategy = normalized_strategy
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


def multi_channel_signal_date_bounds(
    strategies: list[str] | tuple[str, ...],
    symbols: list[str] | tuple[str, ...],
    timeframes: list[str] | tuple[str, ...],
    *,
    redis_profile: str = signal_sources.ORIGINAL,
) -> dict[str, Any]:
    """Return common Redis coverage for the exact multi-channel selection."""
    try:
        profile = load_strategy_profile("backtestbot-v1")
        selection = channel_selection.canonical_selection(
            profile, strategies, symbols, timeframes, allowed_symbols=SYMBOL_GROUPS,
        )
    except (KeyError, ValueError) as exc:
        return {"available": False, "error": str(exc)}

    channel_bounds: list[dict[str, str]] = []
    unavailable: list[str] = []
    for channel in selection["channels"]:
        bounds = signal_date_bounds(
            channel["symbol"], channel["timeframe"], channel["strategy"],
            redis_profile=redis_profile,
        )
        label = (
            f"{channel['strategy']} / {channel['symbol']} / "
            f"{str(channel['timeframe']).upper()}"
        )
        if not bounds.get("available"):
            unavailable.append(f"{label}: {bounds.get('error', 'unavailable')}")
            continue
        channel_bounds.append({
            **channel,
            "earliest_date": str(bounds["earliest_date"]),
            "latest_date": str(bounds["latest_date"]),
        })
    if unavailable:
        return {
            "available": False,
            "error": "Signal history is unavailable for: " + "; ".join(unavailable),
        }

    earliest = max(item["earliest_date"] for item in channel_bounds)
    latest = min(item["latest_date"] for item in channel_bounds)
    return {
        "available": True,
        "earliest_date": earliest,
        "latest_date": latest,
        "channel_count": selection["channel_count"],
        "channels": channel_bounds,
        "signal_strategies": list(selection["strategies"]),
        "signal_symbols": list(selection["symbols"]),
        "signal_timeframes": list(selection["timeframes"]),
        "strategy_frames": {
            token: list(frames) for token, frames in selection["strategy_frames"].items()
        },
    }


def multi_channel_rows_date_bounds(
    channels: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    redis_profile: str = signal_sources.ORIGINAL,
) -> dict[str, Any]:
    """Return common Redis coverage for explicit v2 channel rows.

    A row's own ``source`` (if any) feeds that channel; otherwise ``redis_profile`` (the plan's source) does."""
    try:
        profile = load_strategy_profile("backtestbot-v1")
        selected = channel_selection.canonical_channels(
            profile, channels, allowed_symbols=SYMBOL_GROUPS,
            allowed_sources=signal_sources.LABELS, default_source=redis_profile,
        )
    except (KeyError, ValueError) as exc:
        return {"available": False, "error": str(exc)}
    rows: list[dict[str, Any]] = []
    unavailable: list[str] = []
    for channel in selected:
        bounds = signal_date_bounds(
            channel["symbol"], channel["timeframe"], channel["strategy"],
            redis_profile=channel.get("source") or redis_profile,
        )
        label = f"{channel['strategy']} / {channel['symbol']} / {channel['timeframe'].upper()}"
        if not bounds.get("available"):
            unavailable.append(f"{label}: {bounds.get('error', 'unavailable')}")
        else:
            rows.append({
                **channel,
                "earliest_date": str(bounds["earliest_date"]),
                "latest_date": str(bounds["latest_date"]),
            })
    if unavailable:
        return {
            "available": False,
            "error": "Signal history is unavailable for: " + "; ".join(unavailable),
        }
    return {
        "available": True,
        "earliest_date": max(row["earliest_date"] for row in rows),
        "latest_date": min(row["latest_date"] for row in rows),
        "channel_count": len(rows),
        "channels": rows,
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
    normalized_strategy = str(strategy).strip().lower().replace(" ", "")
    if normalized_strategy not in signal_trans.STRATEGY_COLUMNS:
        raise ValueError("The selected strategy has no Redis signal token")
    redis_strategy = normalized_strategy
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
    # Window.end is the last traded day (cTrader runs through the whole --end day).
    return [sum(1 for day in dates if train.start <= day <= train.end) for train, _oos in windows]


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


def read_standard_single_backtest(plan: dict[str, Any]) -> dict[str, Any]:
    """Read every v2 logical cell from local artifacts and the SQL master copy."""
    preflight = plan.get("preflight") or {}
    planned_cells = list(preflight.get("cells") or [])
    experiment = str(plan.get("experiment") or plan.get("pipeline") or "")
    local_rows: dict[str, dict[str, Any]] = {}
    store = ExperimentStore("grid", experiment)
    if store.db_path.is_file():
        try:
            local_rows = {
                str(row.get("param_hash")): row
                for row in store.flat_rows()
                if row.get("param_hash")
            }
        except Exception as exc:
            return {
                "exists": True,
                "cells": [],
                "read_error": f"Could not read local result index: {type(exc).__name__}: {exc}",
            }

    sql_rows: dict[str, dict[str, Any]] = {}
    sql_error = ""
    run_keys = [str(cell.get("run_key")) for cell in planned_cells if cell.get("run_key")]
    if run_keys:
        profile = load_master_db_profile()
        if profile.enabled:
            try:
                marks = ",".join("?" for _ in run_keys)
                query = f"""
                    SELECT r.run_key, r.batch_name, r.margin_rejections,
                           r.net_profit, r.profit_factor, r.total_trades, r.win_rate,
                           r.max_equity_drawdown_percent, s.channel
                    FROM dbo.backtest_run AS r
                    LEFT JOIN dbo.signal_snapshot AS s
                      ON s.signal_snapshot_id = r.signal_snapshot_id
                    WHERE r.run_key IN ({marks})
                """
                with closing(master_db.connect(profile)) as connection:
                    cursor = connection.execute(query, *run_keys)
                    columns = [str(item[0]) for item in cursor.description]
                    for row in cursor.fetchall():
                        item = dict(zip(columns, row))
                        sql_rows[str(item["run_key"])] = item
            except Exception as exc:
                sql_error = f"Could not read SQL result copy: {type(exc).__name__}: {exc}"

    requested_source = signal_sources.profile_from_input(plan.get("input"))
    cells: list[dict[str, Any]] = []
    for planned in planned_cells:
        item = dict(planned)
        run_key = str(item.get("run_key") or "")
        local = local_rows.get(run_key)
        sql = sql_rows.get(run_key)
        if local is not None:
            flags = local.get("validity_flags") or {}
            item.update({
                "status": str(local.get("status") or "unknown"),
                "net_profit": local.get("net_profit"),
                "profit_factor": local.get("profit_factor"),
                "total_trades": local.get("total_trades"),
                "win_rate": local.get("win_rate"),
                "max_equity_drawdown_pct": local.get("max_equity_drawdown_pct"),
                "margin_rejections": flags.get("margin_rejections"),
                "result_source": "local",
                "batch_name": experiment,
            })
        elif sql is not None:
            sql_channel = str(sql.get("channel") or "")
            fallback_db0 = (
                requested_source != signal_sources.ORIGINAL
                and sql_channel == "original"
            )
            item.update({
                "status": "ok",
                "net_profit": sql.get("net_profit"),
                "profit_factor": sql.get("profit_factor"),
                "total_trades": sql.get("total_trades"),
                "win_rate": sql.get("win_rate"),
                "max_equity_drawdown_pct": sql.get("max_equity_drawdown_percent"),
                "margin_rejections": sql.get("margin_rejections"),
                "result_source": "sql_db0_fallback" if fallback_db0 else "sql",
                "fallback_db0": fallback_db0,
                "batch_name": sql.get("batch_name"),
            })
        else:
            item.update({"status": "missing", "result_source": "missing"})
        cells.append(item)

    available = sum(cell.get("status") in {"ok", "skipped"} for cell in cells)
    return {
        "exists": bool(local_rows or sql_rows),
        "cells": cells,
        "total": len(cells),
        "available": available,
        "missing": len(cells) - available,
        "counts": {
            "local": sum(cell.get("result_source") == "local" for cell in cells),
            "sql": sum(cell.get("result_source") == "sql" for cell in cells),
            "db0_fallback": sum(cell.get("result_source") == "sql_db0_fallback" for cell in cells),
            "missing": sum(cell.get("result_source") == "missing" for cell in cells),
        },
        **({"read_warning": sql_error} if sql_error else {}),
    }


_CHANNEL_LABEL_CODES = {"combo": "cb", "ma_cross": "mc", "macd_break": "mb"}


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _channel_label_map(channels: list[dict[str, Any]], leg_count: int) -> dict[str, int]:
    labels: dict[str, int] = {}
    for index, channel in enumerate(channels):
        code = _CHANNEL_LABEL_CODES.get(str(channel.get("strategy") or ""))
        if not code:
            continue
        short_symbol = str(channel.get("symbol") or "").split(".", 1)[0]
        timeframe = str(channel.get("timeframe") or "").upper()
        for leg in range(1, leg_count + 1):
            labels[f"{code}_{short_symbol}_{timeframe}_L{leg}".casefold()] = index
    return labels


def multi_channel_contributions(
    channels: list[dict[str, Any]], trades: list[dict[str, Any]], leg_count: int,
) -> list[dict[str, Any]]:
    """Attribute net trade P&L to exact channel rows; unknown labels stay explicit."""
    label_map = _channel_label_map(channels, leg_count)
    buckets = [
        {
            "strategy": channel["strategy"], "symbol": channel["symbol"],
            "timeframe": str(channel["timeframe"]).upper(),
            "risk_percent": channel["risk_percent"], "total_profit": 0.0,
            "total_loss": 0.0, "net_contribution": 0.0,
            "wins": 0, "losses": 0, "trades": 0,
        }
        for channel in channels
    ]
    unknown: dict[str, Any] | None = None
    for trade in trades:
        label = str(trade.get("leg_label") or trade.get("label") or "").casefold()
        index = label_map.get(label)
        if index is None:
            if unknown is None:
                unknown = {
                    "strategy": "Unknown", "symbol": "Unknown",
                    "timeframe": "Unknown", "risk_percent": None,
                    "total_profit": 0.0, "total_loss": 0.0,
                    "net_contribution": 0.0, "wins": 0, "losses": 0, "trades": 0,
                }
            bucket = unknown
        else:
            bucket = buckets[index]
        net = _number(trade.get("net_profit", trade.get("net")))
        bucket["trades"] += 1
        bucket["net_contribution"] += net
        if net > 0:
            bucket["total_profit"] += net
            bucket["wins"] += 1
        elif net < 0:
            bucket["total_loss"] += abs(net)
            bucket["losses"] += 1
    if unknown is not None:
        buckets.append(unknown)
    all_profit = sum(row["total_profit"] for row in buckets)
    all_loss = sum(row["total_loss"] for row in buckets)
    for row in buckets:
        row["profit_share_pct"] = 100.0 * row["total_profit"] / all_profit if all_profit else 0.0
        row["loss_share_pct"] = 100.0 * row["total_loss"] / all_loss if all_loss else 0.0
        row["warning"] = (
            "No orders placed — risk per leg may be below the broker minimum volume"
            if row["strategy"] != "Unknown" and row["trades"] == 0 else ""
        )
    return buckets


def read_multi_channel_backtest(plan: dict[str, Any]) -> dict[str, Any]:
    """Read every v2 period plus trade-level contribution from local/SQL copies."""
    if plan.get("schema") == "bo-dashboard-multi-channel-backtest/v1":
        return read_single_backtest(str(plan.get("experiment") or plan.get("pipeline") or ""))
    planned_cells = list((plan.get("preflight") or {}).get("cells") or [])
    experiment = str(plan.get("experiment") or plan.get("pipeline") or "")
    channels = list((plan.get("input") or {}).get("channels") or [])
    fixed = (plan.get("input") or {}).get("fixed_params") or {}
    leg_count = int(float(fixed.get("LegCount") or 3))
    store = ExperimentStore("grid", experiment)
    local_rows: dict[str, dict[str, Any]] = {}
    local_trades: dict[str, list[dict[str, Any]]] = {}
    if store.db_path.is_file():
        try:
            for row in store.flat_rows():
                run_key = str(row.get("param_hash") or "")
                if not run_key:
                    continue
                local_rows[run_key] = row
                report_path = store.run_dir(str(row.get("run_id")), str(row.get("label"))) / "report.json.gz"
                if report_path.is_file():
                    local_trades[run_key] = evidence.trades(evidence.read_report(report_path))
        except Exception as exc:
            return {
                "exists": True, "periods": [],
                "read_error": f"Could not read local result index: {type(exc).__name__}: {exc}",
            }

    run_keys = [str(cell["run_key"]) for cell in planned_cells if cell.get("run_key")]
    sql_rows: dict[str, dict[str, Any]] = {}
    sql_trades: dict[str, list[dict[str, Any]]] = {}
    sql_error = ""
    if run_keys and load_master_db_profile().enabled:
        try:
            marks = ",".join("?" for _ in run_keys)
            with closing(master_db.connect(load_master_db_profile())) as connection:
                cursor = connection.execute(f"""
                    SELECT r.backtest_run_id, r.run_key, r.batch_name, r.margin_rejections,
                           r.net_profit, r.total_trades, r.max_equity_drawdown_percent,
                           s.channel
                    FROM dbo.backtest_run AS r
                    LEFT JOIN dbo.signal_snapshot AS s
                      ON s.signal_snapshot_id = r.signal_snapshot_id
                    WHERE r.run_key IN ({marks})
                """, *run_keys)
                columns = [str(item[0]) for item in cursor.description]
                ids: dict[int, str] = {}
                for raw in cursor.fetchall():
                    row = dict(zip(columns, raw))
                    key = str(row["run_key"])
                    sql_rows[key] = row
                    ids[int(row["backtest_run_id"])] = key
                if ids:
                    id_marks = ",".join("?" for _ in ids)
                    trade_cursor = connection.execute(f"""
                        SELECT backtest_run_id, leg_label, net_profit
                        FROM dbo.trade WHERE backtest_run_id IN ({id_marks})
                    """, *ids)
                    for run_id, label, net_profit in trade_cursor.fetchall():
                        sql_trades.setdefault(ids[int(run_id)], []).append({
                            "leg_label": label, "net_profit": net_profit,
                        })
        except Exception as exc:
            sql_error = f"Could not read SQL result copy: {type(exc).__name__}: {exc}"

    requested_source = signal_sources.profile_from_input(plan.get("input"))
    periods: list[dict[str, Any]] = []
    for planned in planned_cells:
        item = dict(planned)
        run_key = str(item.get("run_key") or "")
        local = local_rows.get(run_key)
        sql = sql_rows.get(run_key)
        trades = local_trades.get(run_key) or sql_trades.get(run_key) or []
        if local is not None:
            flags = local.get("validity_flags") or {}
            item.update({
                "status": str(local.get("status") or "unknown"),
                "net_profit": local.get("net_profit"),
                "total_trades": local.get("total_trades"),
                "max_equity_drawdown_pct": local.get("max_equity_drawdown_pct"),
                "margin_rejections": flags.get("margin_rejections"),
                "result_source": "local",
            })
        elif sql is not None:
            fallback = requested_source != signal_sources.ORIGINAL and str(sql.get("channel")) == "original"
            item.update({
                "status": "ok", "net_profit": sql.get("net_profit"),
                "total_trades": sql.get("total_trades"),
                "max_equity_drawdown_pct": sql.get("max_equity_drawdown_percent"),
                "margin_rejections": sql.get("margin_rejections"),
                "result_source": "sql_db0_fallback" if fallback else "sql",
                "fallback_db0": fallback,
            })
        else:
            item.update({"status": "missing", "result_source": "missing"})
        has_result = local is not None or sql is not None
        trade_count = item.get("total_trades")
        contribution_available = has_result and (
            bool(trades) or (trade_count is not None and int(trade_count) == 0)
        )
        if contribution_available:
            contribution = multi_channel_contributions(channels, trades, leg_count)
            item.update({
                "total_profit": sum(row["total_profit"] for row in contribution),
                "total_loss": -sum(row["total_loss"] for row in contribution),
                "contributions": contribution,
                "contribution_available": True,
            })
        else:
            item.update({
                "total_profit": None,
                "total_loss": None,
                "contributions": [],
                "contribution_available": False,
            })
        periods.append(item)
    available = sum(row.get("status") in {"ok", "skipped"} for row in periods)
    return {
        "exists": bool(local_rows or sql_rows), "periods": periods,
        "total": len(periods), "available": available,
        "missing": len(periods) - available,
        **({"read_warning": sql_error} if sql_error else {}),
    }


def single_bot_events(experiment: str) -> tuple[dict[str, str], ...]:
    """Every persisted cBot log line of one Single Backtest, for server-side paging.

    The detail view sends only one page to the browser; later pages are read
    here. Parsing is cached per bot.log version (path, size, mtime), so paging
    does not re-run the regex projection for every page. Callers must not
    mutate the returned rows.
    """
    store = ExperimentStore("grid", experiment)
    if not store.db_path.is_file():
        return ()
    rows = store.flat_rows()
    if not rows:
        return ()
    row = rows[-1]
    path = store.run_dir(str(row["run_id"]), str(row["label"])) / "bot.log"
    if not path.is_file():
        return ()
    stat = path.stat()
    return _parsed_bot_events(str(path), stat.st_size, stat.st_mtime_ns)


@lru_cache(maxsize=16)
def _parsed_bot_events(path: str, size: int, mtime_ns: int) -> tuple[dict[str, str], ...]:
    del size, mtime_ns  # cache key only
    return tuple(_bot_events(_read_text(Path(path))))


def historical_seconds_per_day(
    *, strategy: str, symbol: str, timeframe: str, data_mode: str,
) -> dict[str, Any]:
    """Median observed CLI wall time per calendar day of backtest period.

    Reads the persisted ``runs`` table of every Grid and Walk-forward store
    through read-only SQLite URIs. Only successful runs of this exact
    strategy, symbol, timeframe and data mode count; without such history no
    estimate is returned, so the dashboard never presents an invented figure.
    """
    rates: list[float] = []
    for method in ("grid", "walkforward"):
        for db_path in sorted((RUNS_ROOT / method).glob("*/index.sqlite")):
            try:
                db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
                try:
                    rows = db.execute(
                        """SELECT wall_seconds,start_date,end_date FROM runs
                           WHERE status='ok' AND strategy=? AND symbol=? AND timeframe=?
                             AND data_mode=? AND wall_seconds > 0""",
                        (str(strategy), str(symbol), str(timeframe).lower(), str(data_mode)),
                    ).fetchall()
                finally:
                    db.close()
            except sqlite3.Error:
                continue
            for wall_seconds, start, end in rows:
                try:
                    days = (date.fromisoformat(str(end)) - date.fromisoformat(str(start))).days
                except ValueError:
                    continue
                if days > 0:
                    rates.append(float(wall_seconds) / days)
    if not rates:
        return {"available": False, "samples": 0}
    return {
        "available": True,
        "samples": len(rates),
        "median_seconds_per_day": statistics.median(rates),
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
            "detail": (
                f"{placed.group('code').lower()} {placed.group('timeframe').upper()} · "
                f"entry {placed.group('price')} · valid until {placed.group('valid_until')} · "
                f"leg {placed.group('leg')}/{placed.group('legs')}"
            ),
            "tone": "info",
        }
    market = _MARKET_PLACE_RE.search(bot_message)
    if market:
        return {
            "event": f"Market order placed — {market.group('side').title()}",
            "detail": (
                f"{market.group('code').lower()} {market.group('timeframe').upper()} · "
                f"SL {market.group('sl')} pips · TP {market.group('tp')} pips · "
                f"leg {market.group('leg')}/{market.group('legs')}"
            ),
            "tone": "info",
        }
    rejected = _ORDER_REJECT_RE.search(bot_message)
    if rejected:
        kind = rejected.group("kind").title()
        return {
            "event": f"{kind} order rejected — {rejected.group('side').title()}",
            "detail": (
                f"{rejected.group('code').lower()} {rejected.group('timeframe').upper()} · "
                f"{rejected.group('reason').strip()} · "
                f"leg {rejected.group('leg')}/{rejected.group('legs')}"
            ),
            "tone": "neutral",
        }
    if "pending order" in bot_message and "cancelled after" in bot_message:
        return {"event": "Pending order expired", "detail": "Unfilled order cancelled by its expiry rule", "tone": "neutral"}
    if "signal skipped: already have same-direction exposure" in bot_message:
        return {"event": "Signal skipped", "detail": "Same-direction exposure was already open", "tone": "neutral"}
    if "signal skipped: validity window ended" in bot_message:
        return {"event": "Signal skipped", "detail": "Validity window ended before placement", "tone": "neutral"}
    if "reversal - closing existing" in bot_message:
        return {"event": "Reversal", "detail": "Existing opposite position closed before reversal", "tone": "neutral"}
    return None
