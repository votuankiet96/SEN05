"""The dashboard's only command boundary into ``core_engine``.

UI callbacks persist immutable plans in ``session_store`` and execute them only
through functions in this module. Query-only presentation remains in
``data_access``. Implemented research stages always supply their pipeline name
so the core content-addressable lock remains authoritative.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import tempfile
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any, Callable

try:
    from . import channel_selection, signal_sources
except ImportError:  # app.py imports dashboard modules without a package prefix.
    import channel_selection
    import signal_sources

_BO_WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
if str(_BO_WORKFLOW_ROOT) not in sys.path:
    sys.path.insert(0, str(_BO_WORKFLOW_ROOT))

from core_engine import cli_runner, facilitator, master_db, signal_trans  # noqa: E402
from core_engine.configuration import (  # noqa: E402
    load_backtest_settings,
    load_engine_profile,
    load_master_db_profile,
    load_portfolio_risk_settings,
    load_redis_profile,
    load_strategy_profile,
    resolve_backtest_config,
    with_signal_overrides,
)
from core_engine.optimize import walkforward  # noqa: E402
from core_engine.output_util import pipeline_lock, readout  # noqa: E402
from core_engine.planner import expand_grid, research_windows, run_name  # noqa: E402
from core_engine.risk_allocation import allocate_equal_risk, validate_manual_risks  # noqa: E402
from core_engine.signal_trans import SYMBOL_GROUPS  # noqa: E402
from core_engine.store import (  # noqa: E402
    DAILY_CLI_LAUNCH_LIMIT,
    ExperimentStore,
    ProtocolStore,
    sha256_file,
)

# Một nguồn duy nhất: bộ engine mặc định khai trong config.yaml (backtest.defaults.engine_profile).
DEFAULT_ENGINE_PROFILE = str(load_backtest_settings()["defaults"]["engine_profile"])
STRATEGY_PROFILES = {
    "combo": "backtestbot-v1",
    "ma_cross": "backtestbot-v1",
    "macd_break": "backtestbot-v1",
}
STRATEGY_LABELS = {
    "combo": "Combo",
    "ma_cross": "MA Cross",
    "macd_break": "MACD Break",
}
SINGLE_BACKTEST_SCHEMA = "bo-dashboard-single-backtest/v1"
STANDARD_SINGLE_BACKTEST_SCHEMA = "bo-dashboard-single-backtest/v2"
LEGACY_MULTI_CHANNEL_SCHEMA = "bo-dashboard-multi-channel-backtest/v1"
MULTI_CHANNEL_SCHEMA = "bo-dashboard-multi-channel-backtest/v2"
GRID_SEARCH_SCHEMA = "bo-dashboard-grid-search/v1"
GRID_DIAGNOSTICS_SCHEMA = "bo-dashboard-grid-diagnostics/v1"
WALKFORWARD_SCHEMA = "bo-dashboard-walkforward/v1"
WALKFORWARD_OBJECTIVES = ("net_profit", "profit_factor", "win_rate")
# DSR/PBO must test the same selection criterion the research uses to pick a
# combination, so they offer exactly the Walk-forward objectives.
DIAGNOSTICS_OBJECTIVES = WALKFORWARD_OBJECTIVES
COMPLETED_EXECUTION_TRIAL_POLICY = "completed_execution_v1"
InputFingerprintMismatch = facilitator.InputFingerprintMismatch

STANDARD_SINGLE_CHANNELS = {
    "combo": ("H1", "H2", "H3", "H4"),
    "ma_cross": ("M10", "M20", "M30", "M45"),
    "macd_break": ("H1", "H2", "H3", "H4", "M10", "M20", "M30", "M45"),
}
STANDARD_SINGLE_PERIOD_GROUP = "from2022"
STANDARD_SINGLE_ACCOUNT_MODE = "isolated"
STANDARD_SINGLE_WARMUP_WINDOW = "c22"
MULTI_CHANNEL_PERIOD_GROUP = "from2022"
MULTI_CHANNEL_ACCOUNT_MODE = "shared"
DEFAULT_MULTI_CHANNEL_RISK_PERCENT = 0.6
MULTI_CHANNEL_RISK_EQUAL = "equal"
MULTI_CHANNEL_RISK_MANUAL = "manual"


def allocate_multi_channel_risk(
    channels: list[dict[str, Any]], *, mode: str, total_percent: Any,
) -> dict[str, Any]:
    """Apply the core portfolio-risk policy to explicit dashboard rows."""
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in {MULTI_CHANNEL_RISK_EQUAL, MULTI_CHANNEL_RISK_MANUAL}:
        raise ValueError("Risk allocation mode must be Equal split or Manual")
    rows = [dict(channel) for channel in channels]
    settings = load_portfolio_risk_settings()
    if normalized_mode == MULTI_CHANNEL_RISK_EQUAL:
        each = allocate_equal_risk(
            total_percent, len(rows), decimals=settings.risk_decimals,
        )
        for row in rows:
            row["risk_percent"] = each
        used = validate_manual_risks([each for _row in rows], total_percent)
        per_channel = each
    else:
        used = validate_manual_risks(
            [row.get("risk_percent") for row in rows], total_percent,
        )
        per_channel = None
    return {
        "channels": rows,
        "risk_allocation": {
            "mode": normalized_mode,
            "total_percent": float(total_percent),
            "channel_count": len(rows),
        },
        "risk_per_channel_percent": per_channel,
        "risk_used_percent": used,
        "risk_decimals": settings.risk_decimals,
    }


def _load_dashboard_profile(profile_id: str) -> Any:
    """Load a current profile and make removed saved plans fail readably."""
    try:
        return load_strategy_profile(profile_id)
    except KeyError as exc:
        raise ValueError(
            f"Strategy profile {profile_id!r} has been removed. Its saved results remain "
            "viewable, but the session cannot be rerun or used for new diagnostics."
        ) from exc


def _profile_timeframes(profile: Any, strategy: str) -> tuple[str, ...]:
    """Return the BacktestBot signal frames for one selected strategy token."""
    return tuple(item.lower() for item in profile.strategy_frames[strategy])


def _single_channel_overrides(strategy: str, symbol: str, timeframe: str) -> dict[str, list[str]]:
    """Restrict BacktestBot to the dashboard's one selected signal channel."""
    return {
        "signal_strategies": [strategy],
        "signal_symbols": [symbol],
        "signal_timeframes": [timeframe.upper()],
    }


def _execution_key(identity: dict[str, Any]) -> str:
    """Return the stable identity hash for one immutable dashboard plan."""
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _standard_channels(symbol: str, risk_percent: float) -> list[dict[str, Any]]:
    return [
        {
            "strategy": strategy,
            "symbol": symbol,
            "timeframe": timeframe,
            "risk_percent": risk_percent,
        }
        for strategy, timeframes in STANDARD_SINGLE_CHANNELS.items()
        for timeframe in timeframes
    ]


def _standard_signal_key(spec: Any) -> str:
    """Return the content key for one isolated public ``SignalChannel``."""
    if len(spec.channels) != 1:
        raise ValueError("Standard Single Backtest requires one channel per RunSpec")
    channel = spec.channels[0]
    return (
        f"{channel.strategy}|{channel.symbol}|{channel.timeframe}|"
        f"{channel.risk_percent:g}"
    )


def _standard_single_preflight(config: dict[str, Any]) -> dict[str, Any]:
    """Build an exact, read-only run card for the canonical 16 x 17 job.

    Redis is materialised only into a temporary directory. No ExperimentStore
    is opened or claimed, no SQL row is written, and no cTrader command runs.
    """
    profile = with_signal_overrides(
        _load_dashboard_profile(str(config["strategy_profile"])), config,
    )
    engine = load_engine_profile(str(config["engine_profile"]))
    windows = research_windows(config)
    specs = expand_grid(config, profile, engine, windows=windows)
    channel_count = sum(len(frames) for frames in STANDARD_SINGLE_CHANNELS.values())
    expected_total = channel_count * len(windows)
    if len(windows) != 17 or len(specs) != expected_total:
        raise ValueError(
            "Canonical Single Backtest must resolve to 16 channels x 17 periods "
            f"(272 cells), got {channel_count} x {len(windows)} = {len(specs)}"
        )

    redis_profile = load_redis_profile(str(config["redis_profile"]))
    algo_sha = sha256_file(profile.algo_path)
    signal_paths: dict[str, Path] = {}
    window_hashes: dict[tuple[Path, date], str] = {}
    materialized: set[Path] = set()
    with tempfile.TemporaryDirectory(prefix="bo-dashboard-single-preflight-") as scratch:
        scratch_path = Path(scratch)
        for spec in specs:
            canonical = spec.canonical_json()
            path = signal_trans.resolve_signal_path(
                redis_profile, profile, spec, scratch_path, materialized=materialized,
            )
            signal_paths[canonical] = path
            key = (path, spec.end)
            if key not in window_hashes:
                window_hashes[key] = signal_trans.signal_window_sha256(path, spec.end)

        store = ExperimentStore("grid", str(config["name"]))
        cells: list[dict[str, Any]] = []
        warmup_signal_sha = ""
        max_end = max(spec.end for spec in specs)
        input_signals: dict[str, str] = {}
        for spec in specs:
            canonical = spec.canonical_json()
            path = signal_paths[canonical]
            signal_key = _standard_signal_key(spec)
            if signal_key not in input_signals:
                input_signals[signal_key] = signal_trans.signal_window_sha256(path, max_end)
            run_key = store.param_hash(
                spec,
                "bo-research-execution/v1",
                algo_sha,
                window_hashes[(path, spec.end)],
                spec.engine_profile,
            )
            channel = spec.channels[0]
            if (
                channel.strategy == "combo"
                and channel.timeframe == "H1"
                and spec.window == STANDARD_SINGLE_WARMUP_WINDOW
            ):
                warmup_signal_sha = window_hashes[(path, spec.end)]
            cells.append({
                "run_key": run_key,
                "label": spec.label(),
                "strategy": channel.strategy,
                "symbol": channel.symbol,
                "timeframe": channel.timeframe,
                "risk_percent": channel.risk_percent,
                "window": spec.window,
                "zone": spec.zone,
                "start": spec.start.isoformat(),
                "end": spec.end.isoformat(),
            })

    local_keys: set[str] = set()
    local_store = ExperimentStore("grid", str(config["name"]))
    if local_store.db_path.is_file():
        local_keys = {
            str(row.get("param_hash"))
            for row in local_store.rows()
            if str(row.get("status")) in {"ok", "skipped"}
        }

    master_profile = load_master_db_profile()
    if not master_profile.enabled:
        raise ValueError("Canonical Single Backtest preflight requires master_db.enabled=true")
    sql_keys: set[str] = set()
    with closing(master_db.connect(master_profile)) as connection:
        for cell in cells:
            if master_db.describe_run(connection, cell["run_key"]) is not None:
                sql_keys.add(cell["run_key"])

    known_keys = local_keys | sql_keys
    known_count = sum(cell["run_key"] in known_keys for cell in cells)
    new_count = len(cells) - known_count
    budget_used = int(ProtocolStore().cli_launches_today())
    budget_remaining = max(0, DAILY_CLI_LAUNCH_LIMIT - budget_used)
    if not warmup_signal_sha:
        raise ValueError("Canonical Single Backtest could not identify the Combo H1 c22 signal hash")
    return {
        "channel_count": channel_count,
        "period_count": len(windows),
        "total_count": len(cells),
        "existing_count": known_count,
        "existing_local_count": sum(cell["run_key"] in local_keys for cell in cells),
        "existing_sql_count": sum(cell["run_key"] in sql_keys for cell in cells),
        "existing_sql_only_count": sum(
            cell["run_key"] in sql_keys and cell["run_key"] not in local_keys for cell in cells
        ),
        "new_count": new_count,
        "cli_required": new_count,
        "cli_used": budget_used,
        "cli_limit": DAILY_CLI_LAUNCH_LIMIT,
        "cli_remaining": budget_remaining,
        "budget_ok": new_count <= budget_remaining,
        "warmup_signal_sha256": warmup_signal_sha,
        "cells": cells,
        "input_fingerprint": {
            "algo_sha256": algo_sha,
            "signals": dict(sorted(input_signals.items())),
        },
    }


def prepare_standard_single_backtest_session(
    *, symbol: str, redis_profile: str = signal_sources.ORIGINAL,
) -> dict[str, Any]:
    """Freeze one canonical Single session: one symbol, 16 channels, 17 periods."""
    normalized_symbol = str(symbol).strip()
    if normalized_symbol not in SYMBOL_GROUPS:
        raise ValueError(f"Symbol is not supported by the signal mapping: {normalized_symbol!r}")
    selected_source = signal_sources.require_profile(redis_profile)
    settings = load_backtest_settings()
    defaults = settings.get("defaults") or {}
    fixed_defaults = defaults.get("fixed_params") or {}
    risk_percent = float(fixed_defaults.get("RiskPercent", 1.0))
    channels = _standard_channels(normalized_symbol, risk_percent)
    config = resolve_backtest_config({
        "channels": channels,
        "redis_profile": selected_source,
        "account_mode": STANDARD_SINGLE_ACCOUNT_MODE,
        "period_group": STANDARD_SINGLE_PERIOD_GROUP,
        "skip_known_in_sql": True,
        "keep_logs": False,
        "locked": True,
        "dashboard_session_kind": "single_backtest_standard",
    })
    config["name"] = run_name(config)
    preflight = _standard_single_preflight(config)
    config["expected_input_fingerprint"] = preflight["input_fingerprint"]

    warmup_config = resolve_backtest_config({
        **config,
        "name": None,
        "channels": [channels[0]],
        "period_group": None,
        "start": "2022-01-01",
        "end": "2026-06-30",
        "max_parallel": 1,
    })
    warmup_config["name"] = run_name(warmup_config)
    warmup_spec = expand_grid(
        warmup_config,
        with_signal_overrides(
            _load_dashboard_profile(str(warmup_config["strategy_profile"])), warmup_config,
        ),
        load_engine_profile(str(warmup_config["engine_profile"])),
    )[0]
    warmup_key = _standard_signal_key(warmup_spec)
    warmup_config["expected_input_fingerprint"] = {
        "algo_sha256": preflight["input_fingerprint"]["algo_sha256"],
        "signals": {
            warmup_key: preflight["input_fingerprint"]["signals"][warmup_key],
        },
    }
    warmup_store = ExperimentStore("grid", str(warmup_config["name"]))
    warmup_run_key = warmup_store.param_hash(
        warmup_spec,
        "bo-research-execution/v1",
        preflight["input_fingerprint"]["algo_sha256"],
        preflight["warmup_signal_sha256"],
        warmup_spec.engine_profile,
    )
    warmup_local = False
    if warmup_store.db_path.is_file():
        warmup_local = any(
            str(row.get("param_hash")) == warmup_run_key
            and str(row.get("status")) in {"ok", "skipped"}
            for row in warmup_store.rows()
        )
    with closing(master_db.connect(load_master_db_profile())) as connection:
        warmup_sql = master_db.describe_run(connection, warmup_run_key) is not None
    warmup_required = not (warmup_local or warmup_sql)
    preflight["warmup_run_key"] = warmup_run_key
    preflight["warmup_required"] = warmup_required
    preflight["warmup_existing_local"] = warmup_local
    preflight["warmup_existing_sql"] = warmup_sql
    preflight["cli_required"] = int(preflight["new_count"]) + int(warmup_required)
    preflight["budget_ok"] = int(preflight["cli_required"]) <= int(preflight["cli_remaining"])

    identity = {
        "schema": STANDARD_SINGLE_BACKTEST_SCHEMA,
        "symbol": normalized_symbol,
        "redis_profile": selected_source,
        "config": config,
        "input_fingerprint": preflight["input_fingerprint"],
    }
    execution_key = _execution_key(identity)
    source_label = signal_sources.LABELS[selected_source]
    return {
        "schema": STANDARD_SINGLE_BACKTEST_SCHEMA,
        "title": f"{normalized_symbol} · 16 channels × 17 periods · {source_label}",
        "note": "Canonical isolated Single Backtest",
        "execution_key": execution_key,
        "pipeline": str(config["name"]),
        "experiment": str(config["name"]),
        "input": {
            "symbol": normalized_symbol,
            "redis_profile": selected_source,
            "account_mode": STANDARD_SINGLE_ACCOUNT_MODE,
            "period_group": STANDARD_SINGLE_PERIOD_GROUP,
            "risk_percent": risk_percent,
            "channel_count": preflight["channel_count"],
            "period_count": preflight["period_count"],
            "cell_count": preflight["total_count"],
        },
        "command_config": config,
        "warmup_config": warmup_config,
        "preflight": {
            **preflight,
            "engine_profile": config["engine_profile"],
            "balance": config["balance"],
            "fixed_params": config["fixed_params"],
            "account_mode": config["account_mode"],
            "period_group": config["period_group"],
            "max_parallel": config["max_parallel"],
        },
    }


def validate_standard_single_backtest_runtime(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate the frozen v2 plan and current CLI allowance without running it."""
    if plan.get("schema") != STANDARD_SINGLE_BACKTEST_SCHEMA:
        raise ValueError("Single Backtest plan does not match schema v2")
    config = dict(plan.get("command_config") or {})
    source = signal_sources.profile_from_input(plan.get("input"))
    if signal_sources.require_profile(config.get("redis_profile")) != source:
        raise ValueError("Single Backtest signal source does not match its frozen plan")
    if config.get("account_mode") != STANDARD_SINGLE_ACCOUNT_MODE:
        raise ValueError("Standard Single Backtest requires account_mode=isolated")
    if config.get("period_group") != STANDARD_SINGLE_PERIOD_GROUP:
        raise ValueError("Standard Single Backtest requires period_group=from2022")
    if len(config.get("channels") or []) != 16:
        raise ValueError("Standard Single Backtest requires exactly 16 channels")
    if not config.get("expected_input_fingerprint"):
        raise ValueError("Standard Single Backtest is missing its frozen input fingerprint")
    cli_path = cli_runner.select_cli()
    if not cli_path.is_file():
        raise FileNotFoundError(f"ctrader-cli.exe was not found: {cli_path}")
    cli_runner.auth_options()
    budget_used = int(ProtocolStore().cli_launches_today())
    budget_remaining = max(0, DAILY_CLI_LAUNCH_LIMIT - budget_used)
    required = int((plan.get("preflight") or {}).get("cli_required") or 0)
    if required > budget_remaining:
        raise ValueError(
            f"This frozen plan needs at most {required} CLI launches, but only "
            f"{budget_remaining}/{DAILY_CLI_LAUNCH_LIMIT} remain today (Europe/Prague). "
            "Create a new session to refresh the exact preflight."
        )
    return {
        "cli_path": str(cli_path),
        "cli_used": budget_used,
        "cli_remaining": budget_remaining,
        "cli_required": required,
    }


def run_standard_single_backtest_session(
    plan: dict[str, Any],
    *,
    on_progress: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run warm-up when needed, then the canonical full backtest flow."""
    validate_standard_single_backtest_runtime(plan)
    preflight = plan.get("preflight") or {}
    warmup_result: dict[str, Any] | None = None
    if bool(preflight.get("warmup_required")):
        if on_progress:
            on_progress(0.0, "Warm-up Combo H1 · c22 · max_parallel=1")

        def warmup_progress(percent: float, detail: str) -> None:
            if on_progress:
                on_progress(float(percent) / 272.0, f"Warm-up · {detail}")

        warmup_result = facilitator.run_backtest(
            dict(plan.get("warmup_config") or {}),
            on_progress=warmup_progress,
            should_cancel=should_cancel,
        )
        warmup_tally = warmup_result.get("tally") or {}
        warmup_logically_complete = bool(warmup_result.get("complete")) or (
            not bool(warmup_result.get("cancelled"))
            and int(warmup_result.get("failed") or 0) == 0
            and sum(
                int(count) for status, count in warmup_tally.items()
                if status in {"ok", "skipped", "known_in_sql"}
            ) == int(warmup_result.get("total") or 0)
        )
        if not warmup_logically_complete:
            return {**warmup_result, "warmup": warmup_result, "phase": "warmup"}
        if int((warmup_result.get("master_db") or {}).get("error") or 0):
            return {
                **warmup_result,
                "complete": False,
                "failed": 1,
                "warmup": warmup_result,
                "phase": "warmup_sql_copy",
            }

    full_result = facilitator.run_backtest(
        dict(plan.get("command_config") or {}),
        on_progress=on_progress,
        should_cancel=should_cancel,
    )
    tally = full_result.get("tally") or {}
    logical_done = sum(
        int(count) for status, count in tally.items()
        if status in {"ok", "skipped", "known_in_sql"}
    )
    logical_complete = (
        not bool(full_result.get("cancelled"))
        and int(full_result.get("failed") or 0) == 0
        and logical_done == int(full_result.get("total") or 0)
    )
    return {
        **full_result,
        "complete": logical_complete,
        "cached": (
            logical_complete
            and not bool(tally.get("ok"))
            and not bool((warmup_result or {}).get("tally", {}).get("ok"))
        ),
        "warmup": warmup_result,
        "phase": "full",
    }


def prepare_single_backtest_session(
    *,
    symbol: str,
    timeframe: str,
    strategy: str,
    start: str,
    end: str,
    balance: float,
    risk_percent: float,
    ksl_level: int,
    ktp_level: int,
    redis_profile: str = signal_sources.ORIGINAL,
) -> dict[str, Any]:
    """Tạo command snapshot cho ĐÚNG MỘT backtest.

    Hàm này không gọi CLI, không mở ExperimentStore và không ghi ra đĩa. Nó
    chỉ kiểm input dashboard, resolve các parameter mặc định của strategy rồi
    tạo plan bất biến để session_store lưu trước khi người dùng bấm chạy.

    Single Backtest là session chẩn đoán độc lập; nó không phải Stage ``final``
    của pipeline nghiên cứu, dù bên dưới tái dùng executor ``run_grid`` với
    một RunSpec cố định.
    """
    normalized_strategy = str(strategy).strip().lower().replace(" ", "")
    if normalized_strategy not in STRATEGY_PROFILES:
        raise ValueError(f"Strategy is not supported by the dashboard: {strategy!r}")
    normalized_symbol = str(symbol).strip()
    normalized_timeframe = str(timeframe).strip().lower()
    if not normalized_symbol:
        raise ValueError("Symbol is required")
    if normalized_symbol not in SYMBOL_GROUPS:
        raise ValueError(
            f"Symbol is not supported by the dashboard signal mapping: {normalized_symbol!r}"
        )

    profile = _load_dashboard_profile(STRATEGY_PROFILES[normalized_strategy])
    engine = load_engine_profile(DEFAULT_ENGINE_PROFILE)
    if engine.backend != "cli":
        raise ValueError("Single Backtest Session v1 supports only the CLI engine")
    allowed_timeframes = _profile_timeframes(profile, normalized_strategy)
    if normalized_timeframe not in allowed_timeframes:
        raise ValueError(
            f"{normalized_strategy} does not support timeframe {normalized_timeframe!r}; "
            f"use one of: {', '.join(allowed_timeframes)}"
        )
    if not profile.algo_path.is_file():
        raise FileNotFoundError(f"Strategy algo was not found: {profile.algo_path}")

    start_date = date.fromisoformat(str(start))
    end_date = date.fromisoformat(str(end))
    if start_date >= end_date:
        raise ValueError("End date must be after start date (end is inclusive)")
    numeric_balance = float(balance)
    numeric_risk = float(risk_percent)
    if not math.isfinite(numeric_balance) or numeric_balance <= 0:
        raise ValueError("Starting balance (USD) must be greater than 0")
    if not math.isfinite(numeric_risk) or numeric_risk <= 0:
        raise ValueError("RiskPercent must be greater than 0")
    normalized_ksl = _enum_index(ksl_level, "KslLevel")
    normalized_ktp = _enum_index(ktp_level, "KtpLevel")
    selected_signal_profile = signal_sources.require_profile(redis_profile)

    fixed_params = profile.resolve_params({
        "KslLevel": normalized_ksl,
        "KtpLevel": normalized_ktp,
        "RiskPercent": numeric_risk,
    })
    command_base = {
        "method": "grid",
        "symbols": [normalized_symbol],
        "timeframe": [normalized_timeframe],
        **_single_channel_overrides(normalized_strategy, normalized_symbol, normalized_timeframe),
        "strategy": normalized_strategy,
        "strategy_profile": profile.id,
        "engine_profile": engine.id,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "balance": numeric_balance,
        "fixed_params": fixed_params,
        "max_parallel": 1,
        "keep_logs": True,
        "locked": True,
        "dashboard_session_kind": "single_backtest",
        **signal_sources.command_field(selected_signal_profile),
    }
    real_inputs = facilitator.input_fingerprint({"name": "dashboard_input_fingerprint", **command_base})
    # Fingerprint deliberately excludes dashboard metadata. Two sessions with
    # identical technical and real inputs share one execution cache.
    fingerprint_input = {
        "strategy": normalized_strategy,
        "strategy_profile": profile.id,
        "engine_profile": engine.id,
        "symbol": normalized_symbol,
        "timeframe": normalized_timeframe,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "balance": numeric_balance,
        "fixed_params": fixed_params,
        "input_fingerprint": real_inputs,
        **signal_sources.identity_field(selected_signal_profile),
    }
    execution_key = hashlib.sha256(
        json.dumps(fingerprint_input, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    stem = f"dashboard_backtest_{execution_key[:16]}"
    command_config = {
        "pipeline": stem,
        "pipeline_stage": "single_backtest",
        "name": stem,
        **command_base,
        "expected_input_fingerprint": real_inputs,
        # Không có parameter_space: planner sẽ tạo đúng một RunSpec.
    }
    return {
        "schema": SINGLE_BACKTEST_SCHEMA,
        "title": (
            f"{normalized_symbol} · "
            f"{STRATEGY_LABELS[normalized_strategy]} · "
            f"{normalized_timeframe.upper()} · {start_date.isoformat()} to {end_date.isoformat()}"
        ),
        "note": "",
        "execution_key": execution_key,
        "pipeline": stem,
        "experiment": stem,
        "input": {**fingerprint_input, "redis_profile": selected_signal_profile},
        "command_config": command_config,
        "preflight": {
            "engine_backend": engine.backend,
            "data_mode": engine.data_mode,
            "algo_path": str(profile.algo_path),
            "timeframe_allowed": True,
            "single_process": True,
            "input_fingerprint": real_inputs,
        },
    }


def run_single_backtest_session(
    plan: dict[str, Any],
    *,
    on_progress: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Command duy nhất thực thi một Single Backtest Session đã được snapshot."""
    if plan.get("schema") != SINGLE_BACKTEST_SCHEMA:
        raise ValueError("Single Backtest plan does not match the dashboard schema")
    config = dict(plan.get("command_config") or {})
    if signal_sources.profile_from_input(plan.get("input")) != signal_sources.require_profile(
        config.get("redis_profile") or signal_sources.ORIGINAL
    ):
        raise ValueError("Single Backtest signal source does not match its frozen plan")
    if config.get("dashboard_session_kind") != "single_backtest":
        raise ValueError("Plan is not a Single Backtest Session")
    if config.get("parameter_space"):
        raise ValueError("Single Backtest Session must not contain parameter_space")
    if config.get("max_parallel") != 1:
        raise ValueError("Single Backtest Session requires max_parallel=1")
    if config.get("pipeline_stage") != "single_backtest":
        raise ValueError("Single Backtest Session uses an invalid pipeline stage")
    if config.get("method") != "grid" or config.get("force"):
        raise ValueError("Single Backtest Session cannot change method or force a rerun")
    if not config.get("expected_input_fingerprint"):
        raise ValueError("This session predates input fingerprints; create a new session")
    if len(config.get("symbols") or []) != 1 or len(config.get("timeframe") or []) != 1:
        raise ValueError("Single Backtest Session accepts exactly one symbol and one timeframe")
    if config.get("zones") or config.get("walkforward"):
        raise ValueError("Single Backtest Session does not accept research windows or walk-forward settings")
    return facilitator.run_grid(config, on_progress=on_progress, should_cancel=should_cancel)


def validate_single_backtest_runtime(plan: dict[str, Any]) -> dict[str, str]:
    """Preflight không ghi state, không spawn backtest.

    Dashboard có thể bị khởi động trực tiếp bằng ``python app.py`` thay vì
    start_dashboard.bat. Trường hợp đó thiếu auth environment sẽ bị phát hiện
    trước khi session lấy slot chạy, thay vì biến thành một execution thất bại.
    """
    if plan.get("schema") != SINGLE_BACKTEST_SCHEMA:
        raise ValueError("Single Backtest plan does not match the dashboard schema")
    auth = cli_runner.auth_options()
    cli_path = cli_runner.select_cli()
    if not cli_path.is_file():
        raise FileNotFoundError(f"ctrader-cli.exe was not found: {cli_path}")
    return {"cli_path": str(cli_path), "account": auth["ACCOUNT"]}


def _multi_signal_key(spec: Any) -> str:
    return ";".join(
        f"{channel.strategy}|{channel.symbol}|{channel.timeframe}|{channel.risk_percent:g}"
        for channel in spec.channels
    )


def _multi_channel_preflight(config: dict[str, Any]) -> dict[str, Any]:
    """Build the exact shared-account run card without launching cTrader."""
    profile = with_signal_overrides(
        _load_dashboard_profile(str(config["strategy_profile"])), config,
    )
    engine = load_engine_profile(str(config["engine_profile"]))
    windows = research_windows(config)
    specs = expand_grid(config, profile, engine, windows=windows)
    if len(specs) != len(windows) or any(
        len(spec.channels) != len(profile.signal_channels) for spec in specs
    ):
        raise ValueError("Shared Multi-channel must produce exactly one RunSpec per period")

    redis_profile = load_redis_profile(str(config["redis_profile"]))
    algo_sha = sha256_file(profile.algo_path)
    signal_paths: dict[str, Path] = {}
    window_hashes: dict[tuple[Path, date], str] = {}
    materialized: set[Path] = set()
    with tempfile.TemporaryDirectory(prefix="bo-dashboard-multi-preflight-") as scratch:
        scratch_path = Path(scratch)
        for spec in specs:
            canonical = spec.canonical_json()
            path = signal_trans.resolve_signal_path(
                redis_profile, profile, spec, scratch_path, materialized=materialized,
            )
            signal_paths[canonical] = path
            key = (path, spec.end)
            if key not in window_hashes:
                window_hashes[key] = signal_trans.signal_window_sha256(path, spec.end)

        store = ExperimentStore("grid", str(config["name"]))
        max_end = max(spec.end for spec in specs)
        input_signals: dict[str, str] = {}
        cells: list[dict[str, Any]] = []
        for spec in specs:
            path = signal_paths[spec.canonical_json()]
            signal_key = _multi_signal_key(spec)
            if signal_key not in input_signals:
                input_signals[signal_key] = signal_trans.signal_window_sha256(path, max_end)
            run_key = store.param_hash(
                spec, "bo-research-execution/v1", algo_sha,
                window_hashes[(path, spec.end)], spec.engine_profile,
            )
            cells.append({
                "run_key": run_key,
                "label": spec.label(),
                "window": spec.window,
                "zone": spec.zone,
                "start": spec.start.isoformat(),
                "end": spec.end.isoformat(),
            })

    local_store = ExperimentStore("grid", str(config["name"]))
    local_keys = {
        str(row.get("param_hash"))
        for row in (local_store.rows() if local_store.db_path.is_file() else [])
        if str(row.get("status")) in {"ok", "skipped"}
    }
    master_profile = load_master_db_profile()
    if not master_profile.enabled:
        raise ValueError("Multi-channel preflight requires master_db.enabled=true")
    sql_keys: set[str] = set()
    with closing(master_db.connect(master_profile)) as connection:
        for cell in cells:
            if master_db.describe_run(connection, cell["run_key"]) is not None:
                sql_keys.add(cell["run_key"])

    known_keys = local_keys | sql_keys
    new_count = sum(cell["run_key"] not in known_keys for cell in cells)
    used = int(ProtocolStore().cli_launches_today())
    remaining = max(0, DAILY_CLI_LAUNCH_LIMIT - used)
    return {
        "channel_count": len(profile.signal_channels),
        "period_count": len(windows),
        "total_count": len(cells),
        "existing_count": len(cells) - new_count,
        "existing_local_count": sum(cell["run_key"] in local_keys for cell in cells),
        "existing_sql_count": sum(cell["run_key"] in sql_keys for cell in cells),
        "new_count": new_count,
        "cli_required": new_count,
        "cli_used": used,
        "cli_limit": DAILY_CLI_LAUNCH_LIMIT,
        "cli_remaining": remaining,
        "budget_ok": new_count <= remaining,
        "cells": cells,
        "input_fingerprint": {
            "algo_sha256": algo_sha,
            "signals": dict(sorted(input_signals.items())),
        },
    }


def prepare_multi_channel_session(
    *,
    channels: list[dict[str, Any]],
    custom_period: bool,
    start: str | None,
    end: str | None,
    balance: float,
    ksl_level: int,
    ktp_level: int,
    leg_count: int,
    leg2_ktp: int,
    leg3_ktp: int,
    enable_daily_loss_limit: bool,
    max_daily_loss_percent: float,
    enable_max_drawdown: bool,
    max_total_drawdown_percent: float,
    enable_max_consecutive_losses: bool,
    max_consecutive_losses: int,
    redis_profile: str = signal_sources.ORIGINAL,
    risk_allocation_mode: str = MULTI_CHANNEL_RISK_EQUAL,
    total_risk_percent: Any | None = None,
) -> dict[str, Any]:
    """Freeze explicit channel rows for 17 default periods or one custom period."""
    profile = _load_dashboard_profile("backtestbot-v1")
    if total_risk_percent is None:
        total_risk_percent = load_portfolio_risk_settings().total_risk_percent
    allocation = allocate_multi_channel_risk(
        channels, mode=risk_allocation_mode, total_percent=total_risk_percent,
    )
    # Plan source = default for every row; a row may override it with its own `source` (multi-source in one run).
    selected_source = signal_sources.require_profile(redis_profile)
    normalized_channels = list(channel_selection.canonical_channels(
        profile, allocation["channels"], allowed_symbols=SYMBOL_GROUPS,
        allowed_sources=signal_sources.LABELS, default_source=selected_source,
    ))
    numeric_balance = float(balance)
    if not math.isfinite(numeric_balance) or numeric_balance <= 0:
        raise ValueError("Starting balance (USD) must be greater than 0")
    legs = _enum_index(leg_count, "LegCount")
    if legs not in {1, 2, 3}:
        raise ValueError("LegCount must be between 1 and 3")
    daily_loss = float(max_daily_loss_percent)
    total_drawdown = float(max_total_drawdown_percent)
    consecutive_losses = _enum_index(max_consecutive_losses, "MaxConsecutiveLosses")
    if not math.isfinite(daily_loss) or daily_loss <= 0:
        raise ValueError("MaxDailyLossPercent must be greater than 0")
    if not math.isfinite(total_drawdown) or total_drawdown <= 0:
        raise ValueError("MaxTotalDrawdownPercent must be greater than 0")
    if consecutive_losses < 1:
        raise ValueError("MaxConsecutiveLosses must be at least 1")

    fixed_params = profile.resolve_params({
        "KslLevel": _enum_index(ksl_level, "KslLevel"),
        "KtpLevel": _enum_index(ktp_level, "KtpLevel"),
        "LegCount": legs,
        "Leg2Ktp": _enum_index(leg2_ktp, "Leg2Ktp"),
        "Leg3Ktp": _enum_index(leg3_ktp, "Leg3Ktp"),
        "EnableDailyLossLimit": bool(enable_daily_loss_limit),
        "MaxDailyLossPercent": daily_loss,
        "EnableMaxDrawdown": bool(enable_max_drawdown),
        "MaxTotalDrawdownPercent": total_drawdown,
        "EnableMaxConsecutiveLosses": bool(enable_max_consecutive_losses),
        "MaxConsecutiveLosses": consecutive_losses,
    })
    period_overrides: dict[str, Any] = {"period_group": MULTI_CHANNEL_PERIOD_GROUP}
    if custom_period:
        if not start or not end:
            raise ValueError("Custom period requires both start and end dates")
        start_date = date.fromisoformat(str(start))
        end_date = date.fromisoformat(str(end))
        if start_date > end_date:
            raise ValueError("Custom period end must be on or after its start")
        period_overrides = {
            "period_group": None,
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "zone": "custom",
        }

    config = resolve_backtest_config({
        "channels": normalized_channels,
        "redis_profile": selected_source,
        "account_mode": MULTI_CHANNEL_ACCOUNT_MODE,
        "balance": numeric_balance,
        "fixed_params": fixed_params,
        "skip_known_in_sql": True,
        "keep_logs": True,
        "locked": True,
        "dashboard_session_kind": "multi_channel_backtest_v2",
        **period_overrides,
    })
    # Long shared-account labels can make the core's atomic temp-file path
    # exceed MAX_PATH on Windows.  The short name is deterministic; each
    # run_key remains content-addressed by the unchanged RunSpec and inputs.
    config["name"] = f"dashboard_mc_{_execution_key({'config': config})[:12]}"
    preflight = _multi_channel_preflight(config)
    config["expected_input_fingerprint"] = preflight["input_fingerprint"]
    identity = {
        "schema": MULTI_CHANNEL_SCHEMA,
        "channels": normalized_channels,
        "custom_period": bool(custom_period),
        "balance": numeric_balance,
        "fixed_params": fixed_params,
        "config": config,
        "input_fingerprint": preflight["input_fingerprint"],
        **signal_sources.identity_field(selected_source),
    }
    execution_key = _execution_key(identity)
    risk_allocation = allocation["risk_allocation"]
    config["risk_allocation"] = risk_allocation
    period_text = (
        f"{start} to {end}" if custom_period
        else f"{MULTI_CHANNEL_PERIOD_GROUP} ({preflight['period_count']} periods)"
    )
    return {
        "schema": MULTI_CHANNEL_SCHEMA,
        "title": f"Multi-channel · {len(normalized_channels)} channels · {period_text}",
        "note": "Shared account; one independent portfolio run per period",
        "execution_key": execution_key,
        "pipeline": str(config["name"]),
        "experiment": str(config["name"]),
        "input": {
            **identity,
            "redis_profile": selected_source,
            "account_mode": MULTI_CHANNEL_ACCOUNT_MODE,
            "period_group": None if custom_period else MULTI_CHANNEL_PERIOD_GROUP,
            "start": start if custom_period else None,
            "end": end if custom_period else None,
            "channel_count": len(normalized_channels),
            "risk_allocation": risk_allocation,
        },
        "command_config": config,
        "preflight": {
            **preflight,
            "engine_profile": config["engine_profile"],
            "balance": config["balance"],
            "fixed_params": config["fixed_params"],
            "account_mode": config["account_mode"],
            "period_group": config.get("period_group"),
            "max_parallel": config["max_parallel"],
            "risk_allocation": risk_allocation,
            "risk_per_channel_percent": allocation["risk_per_channel_percent"],
            "risk_used_percent": allocation["risk_used_percent"],
            "risk_decimals": allocation["risk_decimals"],
        },
    }


def _prepare_legacy_multi_channel_session(
    *,
    strategies: list[str],
    symbols: list[str],
    timeframes: list[str],
    start: str,
    end: str,
    balance: float,
    risk_percent: float,
    ksl_level: int,
    ktp_level: int,
    leg_count: int,
    leg2_ktp: int,
    leg3_ktp: int,
    enable_daily_loss_limit: bool,
    max_daily_loss_percent: float,
    enable_max_drawdown: bool,
    max_total_drawdown_percent: float,
    enable_max_consecutive_losses: bool,
    max_consecutive_losses: int,
    redis_profile: str = signal_sources.ORIGINAL,
) -> dict[str, Any]:
    """Freeze one BacktestBot portfolio run containing many signal channels."""
    profile = _load_dashboard_profile("backtestbot-v1")
    engine = load_engine_profile(DEFAULT_ENGINE_PROFILE)
    if engine.backend != "cli":
        raise ValueError("Multi-channel Session v1 supports only the CLI engine")
    if not profile.algo_path.is_file():
        raise FileNotFoundError(f"Strategy algo was not found: {profile.algo_path}")

    selection = channel_selection.canonical_selection(
        profile, strategies, symbols, timeframes, allowed_symbols=SYMBOL_GROUPS,
    )
    start_date = date.fromisoformat(str(start))
    end_date = date.fromisoformat(str(end))
    if start_date >= end_date:
        raise ValueError("End date must be after start date (end is inclusive)")
    numeric_balance = float(balance)
    numeric_risk = float(risk_percent)
    if not math.isfinite(numeric_balance) or numeric_balance <= 0:
        raise ValueError("Starting balance (USD) must be greater than 0")
    if not math.isfinite(numeric_risk) or numeric_risk <= 0:
        raise ValueError("RiskPercent must be greater than 0")

    legs = _enum_index(leg_count, "LegCount")
    if legs < 1 or legs > 3:
        raise ValueError("LegCount must be between 1 and 3")
    daily_loss = float(max_daily_loss_percent)
    total_drawdown = float(max_total_drawdown_percent)
    if not math.isfinite(daily_loss) or daily_loss <= 0:
        raise ValueError("MaxDailyLossPercent must be greater than 0")
    if not math.isfinite(total_drawdown) or total_drawdown <= 0:
        raise ValueError("MaxTotalDrawdownPercent must be greater than 0")
    consecutive_losses = _enum_index(max_consecutive_losses, "MaxConsecutiveLosses")
    if consecutive_losses < 1:
        raise ValueError("MaxConsecutiveLosses must be at least 1")

    fixed_params = profile.resolve_params({
        "KslLevel": _enum_index(ksl_level, "KslLevel"),
        "KtpLevel": _enum_index(ktp_level, "KtpLevel"),
        "LegCount": legs,
        "Leg2Ktp": _enum_index(leg2_ktp, "Leg2Ktp"),
        "Leg3Ktp": _enum_index(leg3_ktp, "Leg3Ktp"),
        "RiskPercent": numeric_risk,
        "EnableDailyLossLimit": bool(enable_daily_loss_limit),
        "MaxDailyLossPercent": daily_loss,
        "EnableMaxDrawdown": bool(enable_max_drawdown),
        "MaxTotalDrawdownPercent": total_drawdown,
        "EnableMaxConsecutiveLosses": bool(enable_max_consecutive_losses),
        "MaxConsecutiveLosses": consecutive_losses,
    })
    selected_signal_profile = signal_sources.require_profile(redis_profile)
    chart_symbol = selection["symbols"][0]
    chart_timeframe = str(profile.default_timeframes[0]).lower()
    command_base = {
        "method": "grid",
        # These two fields drive planner expansion. Keeping one chart anchor in
        # each guarantees exactly one RunSpec and therefore one CLI launch.
        "symbols": [chart_symbol],
        "timeframe": [chart_timeframe],
        "signal_strategies": list(selection["strategies"]),
        "signal_symbols": list(selection["symbols"]),
        "signal_timeframes": list(selection["timeframes"]),
        "strategy": profile.strategy,
        "strategy_profile": profile.id,
        "engine_profile": engine.id,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "balance": numeric_balance,
        "fixed_params": fixed_params,
        "max_parallel": 1,
        "keep_logs": True,
        "locked": True,
        "dashboard_session_kind": "multi_channel_backtest",
        **signal_sources.command_field(selected_signal_profile),
    }
    real_inputs = facilitator.input_fingerprint({"name": "dashboard_input_fingerprint", **command_base})
    identity = {
        "schema": LEGACY_MULTI_CHANNEL_SCHEMA,
        "strategy": profile.strategy,
        "strategy_profile": profile.id,
        "engine_profile": engine.id,
        "signal_strategies": list(selection["strategies"]),
        "signal_symbols": list(selection["symbols"]),
        "signal_timeframes": list(selection["timeframes"]),
        "strategy_frames": {
            token: list(frames) for token, frames in selection["strategy_frames"].items()
        },
        "channel_count": selection["channel_count"],
        "chart_symbol": chart_symbol,
        "chart_timeframe": chart_timeframe,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "balance": numeric_balance,
        "fixed_params": fixed_params,
        "input_fingerprint": real_inputs,
        **signal_sources.identity_field(selected_signal_profile),
    }
    execution_key = _execution_key(identity)
    stem = f"dashboard_multichannel_{execution_key[:16]}"
    command_config = {
        "pipeline": stem,
        "pipeline_stage": "multi_channel_backtest",
        "name": stem,
        **command_base,
        "expected_input_fingerprint": real_inputs,
    }
    return {
        "schema": LEGACY_MULTI_CHANNEL_SCHEMA,
        "title": (
            f"Multi-channel · {len(selection['symbols'])} symbols · "
            f"{len(selection['strategies'])} strategies · {selection['channel_count']} channels · "
            f"{start_date.isoformat()} to {end_date.isoformat()}"
        ),
        "note": "",
        "execution_key": execution_key,
        "pipeline": stem,
        "experiment": stem,
        "input": {**identity, "redis_profile": selected_signal_profile},
        "command_config": command_config,
        "preflight": {
            "engine_backend": engine.backend,
            "data_mode": engine.data_mode,
            "algo_path": str(profile.algo_path),
            "single_process": True,
            "run_spec_count": 1,
            "channel_count": selection["channel_count"],
            "input_fingerprint": real_inputs,
        },
    }


def run_multi_channel_session(
    plan: dict[str, Any],
    *,
    on_progress: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Execute a frozen v2 shared-account plan through the canonical flow."""
    if plan.get("schema") != MULTI_CHANNEL_SCHEMA:
        raise ValueError("Legacy Multi-channel sessions are view-only; create a v2 session to run")
    config = dict(plan.get("command_config") or {})
    input_doc = plan.get("input") or {}
    if signal_sources.profile_from_input(input_doc) != signal_sources.require_profile(
        config.get("redis_profile") or signal_sources.ORIGINAL
    ):
        raise ValueError("Multi-channel signal source does not match its frozen plan")
    if config.get("dashboard_session_kind") != "multi_channel_backtest_v2":
        raise ValueError("Plan is not a Multi-channel v2 Session")
    if config.get("account_mode") != MULTI_CHANNEL_ACCOUNT_MODE:
        raise ValueError("Multi-channel Session requires account_mode=shared")
    if config.get("parameter_space") or config.get("params") or config.get("walkforward"):
        raise ValueError("Multi-channel Session accepts one fixed parameter set")
    if not config.get("expected_input_fingerprint"):
        raise ValueError("Multi-channel Session is missing its frozen input fingerprint")
    profile = _load_dashboard_profile(str(config.get("strategy_profile") or ""))
    channels = list(channel_selection.canonical_channels(
        profile, config.get("channels"), allowed_symbols=SYMBOL_GROUPS,
        allowed_sources=signal_sources.LABELS, default_source=config.get("redis_profile"),
    ))
    if channels != list(input_doc.get("channels") or []):
        raise ValueError("Multi-channel rows do not match the frozen plan")
    if len(channels) != int(input_doc.get("channel_count") or 0):
        raise ValueError("Multi-channel count does not match its frozen plan")
    result = facilitator.run_backtest(
        config, on_progress=on_progress, should_cancel=should_cancel,
    )
    tally = result.get("tally") or {}
    logical_done = sum(
        int(count) for status, count in tally.items()
        if status in {"ok", "skipped", "known_in_sql"}
    )
    logical_complete = (
        not bool(result.get("cancelled"))
        and int(result.get("failed") or 0) == 0
        and logical_done == int(result.get("total") or 0)
    )
    return {
        **result,
        "complete": logical_complete,
        "cached": logical_complete and not bool(tally.get("ok")),
    }


def _run_legacy_multi_channel_session(
    plan: dict[str, Any],
    *,
    on_progress: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Execute one frozen multi-channel plan through one core RunSpec."""
    if plan.get("schema") != LEGACY_MULTI_CHANNEL_SCHEMA:
        raise ValueError("Multi-channel plan does not match the dashboard schema")
    config = dict(plan.get("command_config") or {})
    input_doc = plan.get("input") or {}
    if signal_sources.profile_from_input(input_doc) != signal_sources.require_profile(
        config.get("redis_profile") or signal_sources.ORIGINAL
    ):
        raise ValueError("Multi-channel signal source does not match its frozen plan")
    if config.get("dashboard_session_kind") != "multi_channel_backtest":
        raise ValueError("Plan is not a Multi-channel Session")
    if config.get("method") != "grid" or config.get("force"):
        raise ValueError("Multi-channel Session cannot change method or force a rerun")
    if config.get("parameter_space") or config.get("zones") or config.get("walkforward"):
        raise ValueError("Multi-channel Session accepts one fixed parameter set and no research windows")
    if config.get("max_parallel") != 1:
        raise ValueError("Multi-channel Session requires max_parallel=1")
    if config.get("pipeline_stage") != "multi_channel_backtest":
        raise ValueError("Multi-channel Session uses an invalid pipeline stage")
    if len(config.get("symbols") or []) != 1 or len(config.get("timeframe") or []) != 1:
        raise ValueError("Multi-channel Session requires exactly one chart anchor")
    if config.get("symbols", [None])[0] not in (config.get("signal_symbols") or []):
        raise ValueError("Multi-channel chart symbol must be one of signal_symbols")
    if not config.get("expected_input_fingerprint"):
        raise ValueError("This session predates input fingerprints; create a new session")

    profile = _load_dashboard_profile(str(config.get("strategy_profile") or ""))
    selection = channel_selection.canonical_selection(
        profile,
        config.get("signal_strategies"),
        config.get("signal_symbols"),
        config.get("signal_timeframes"),
        allowed_symbols=SYMBOL_GROUPS,
    )
    if list(selection["strategies"]) != list(input_doc.get("signal_strategies") or []):
        raise ValueError("Multi-channel strategy selection does not match its frozen plan")
    if list(selection["symbols"]) != list(input_doc.get("signal_symbols") or []):
        raise ValueError("Multi-channel symbol selection does not match its frozen plan")
    if list(selection["timeframes"]) != list(input_doc.get("signal_timeframes") or []):
        raise ValueError("Multi-channel timeframe selection does not match its frozen plan")
    if selection["channel_count"] != int(input_doc.get("channel_count") or 0):
        raise ValueError("Multi-channel count does not match its frozen plan")
    return facilitator.run_grid(config, on_progress=on_progress, should_cancel=should_cancel)


def validate_multi_channel_runtime(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate the frozen v2 plan and current CLI allowance without running."""
    if plan.get("schema") != MULTI_CHANNEL_SCHEMA:
        raise ValueError("Legacy Multi-channel sessions are view-only; create a v2 session to run")
    config = dict(plan.get("command_config") or {})
    if config.get("account_mode") != MULTI_CHANNEL_ACCOUNT_MODE:
        raise ValueError("Multi-channel Session requires account_mode=shared")
    if not config.get("expected_input_fingerprint"):
        raise ValueError("Multi-channel Session is missing its frozen input fingerprint")
    cli_path = cli_runner.select_cli()
    if not cli_path.is_file():
        raise FileNotFoundError(f"ctrader-cli.exe was not found: {cli_path}")
    cli_runner.auth_options()
    used = int(ProtocolStore().cli_launches_today())
    remaining = max(0, DAILY_CLI_LAUNCH_LIMIT - used)
    required = int((plan.get("preflight") or {}).get("cli_required") or 0)
    if required > remaining:
        raise ValueError(
            f"This frozen plan needs at most {required} CLI launches, but only "
            f"{remaining}/{DAILY_CLI_LAUNCH_LIMIT} remain today (Europe/Prague)."
        )
    return {
        "cli_path": str(cli_path), "cli_used": used,
        "cli_remaining": remaining, "cli_required": required,
    }


def _validate_legacy_multi_channel_runtime(plan: dict[str, Any]) -> dict[str, str]:
    """Validate CLI/auth availability without starting a backtest."""
    if plan.get("schema") != LEGACY_MULTI_CHANNEL_SCHEMA:
        raise ValueError("Multi-channel plan does not match the dashboard schema")
    auth = cli_runner.auth_options()
    cli_path = cli_runner.select_cli()
    if not cli_path.is_file():
        raise FileNotFoundError(f"ctrader-cli.exe was not found: {cli_path}")
    return {"cli_path": str(cli_path), "account": auth["ACCOUNT"]}


def prepare_grid_search_session(
    *,
    symbol: str,
    timeframe: str,
    strategy: str,
    start: str,
    end: str,
    balance: float,
    risk_percent: float,
    max_parallel: int,
    ksl_levels: list[Any],
    ktp_levels: list[Any],
    redis_profile: str = signal_sources.ORIGINAL,
) -> dict[str, Any]:
    """Build an immutable selected kSL x kTP Grid Search command snapshot.

    A Grid Search session represents one research tuple: one symbol, one
    timeframe and one strategy.  This function validates and freezes the
    command only; it never starts cTrader or writes a core artifact.
    """
    normalized_strategy = str(strategy).strip().lower().replace(" ", "")
    if normalized_strategy not in STRATEGY_PROFILES:
        raise ValueError(f"Strategy is not supported by the dashboard: {strategy!r}")
    normalized_symbol = str(symbol).strip()
    normalized_timeframe = str(timeframe).strip().lower()
    if normalized_symbol not in SYMBOL_GROUPS:
        raise ValueError(
            f"Symbol is not supported by the dashboard signal mapping: {normalized_symbol!r}"
        )

    profile = _load_dashboard_profile(STRATEGY_PROFILES[normalized_strategy])
    engine = load_engine_profile(DEFAULT_ENGINE_PROFILE)
    if engine.backend != "cli":
        raise ValueError("Grid Search Session supports only the CLI engine")
    allowed_timeframes = _profile_timeframes(profile, normalized_strategy)
    if normalized_timeframe not in allowed_timeframes:
        raise ValueError(
            f"{normalized_strategy} does not support timeframe {normalized_timeframe!r}; "
            f"use one of: {', '.join(allowed_timeframes)}"
        )
    if not profile.algo_path.is_file():
        raise FileNotFoundError(f"Strategy algo was not found: {profile.algo_path}")

    start_date = date.fromisoformat(str(start))
    end_date = date.fromisoformat(str(end))
    if start_date >= end_date:
        raise ValueError("End date must be after start date")
    numeric_balance = float(balance)
    numeric_risk = float(risk_percent)
    if not math.isfinite(numeric_balance) or numeric_balance <= 0:
        raise ValueError("Starting balance (USD) must be greater than 0")
    if not math.isfinite(numeric_risk) or numeric_risk <= 0:
        raise ValueError("Risk per trade must be greater than 0")
    selected_signal_profile = signal_sources.require_profile(redis_profile)
    workers = _enum_index(max_parallel, "Parallel CLI workers")
    worker_cap = int(getattr(facilitator, "MAX_PARALLEL_CAP", 16))
    if workers < 1 or workers > worker_cap:
        raise ValueError(f"Parallel CLI workers must be between 1 and {worker_cap}")

    normalized_ksl = _enum_selection(profile, "KslLevel", ksl_levels)
    normalized_ktp = _enum_selection(profile, "KtpLevel", ktp_levels)
    ksl_count = len(normalized_ksl)
    ktp_count = len(normalized_ktp)
    pass_count = ksl_count * ktp_count
    fixed_params = {"RiskPercent": numeric_risk}
    parameter_space = {"KslLevel": normalized_ksl, "KtpLevel": normalized_ktp}
    command_base = {
        "method": "grid",
        "symbols": [normalized_symbol],
        "timeframe": [normalized_timeframe],
        **_single_channel_overrides(normalized_strategy, normalized_symbol, normalized_timeframe),
        "strategy": normalized_strategy,
        "strategy_profile": profile.id,
        "engine_profile": engine.id,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "balance": numeric_balance,
        "parameter_space": parameter_space,
        "fixed_params": fixed_params,
        "max_parallel": workers,
        "keep_logs": False,
        "locked": True,
        "dashboard_session_kind": "grid_search",
        **signal_sources.command_field(selected_signal_profile),
    }
    real_inputs = facilitator.input_fingerprint({"name": "dashboard_input_fingerprint", **command_base})
    fingerprint_input = {
        "strategy": normalized_strategy,
        "strategy_profile": profile.id,
        "engine_profile": engine.id,
        "symbol": normalized_symbol,
        "timeframe": normalized_timeframe,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "balance": numeric_balance,
        "fixed_params": fixed_params,
        "parameter_space": parameter_space,
        "input_fingerprint": real_inputs,
        **signal_sources.identity_field(selected_signal_profile),
    }
    execution_key = _execution_key(fingerprint_input)
    stem = f"dashboard_grid_{execution_key[:16]}"
    command_config = {
        "pipeline": stem,
        "pipeline_stage": "grid",
        "name": stem,
        **command_base,
        "expected_input_fingerprint": real_inputs,
    }
    strategy_label = STRATEGY_LABELS[normalized_strategy]
    return {
        "schema": GRID_SEARCH_SCHEMA,
        "title": (
            f"{normalized_symbol} - {strategy_label} - {normalized_timeframe.upper()} - "
            f"{pass_count} combinations - {start_date.isoformat()} to {end_date.isoformat()}"
        ),
        "note": "",
        "execution_key": execution_key,
        "pipeline": stem,
        "experiment": stem,
        "input": {**fingerprint_input, "redis_profile": selected_signal_profile,
                  "max_parallel": workers, "pass_count": pass_count},
        "command_config": command_config,
        "preflight": {
            "engine_backend": engine.backend,
            "data_mode": engine.data_mode,
            "algo_path": str(profile.algo_path),
            "timeframe_allowed": True,
            "ksl_count": ksl_count,
            "ktp_count": ktp_count,
            "ksl_available": len(profile.params["KslLevel"].enum_values),
            "ktp_available": len(profile.params["KtpLevel"].enum_values),
            "pass_count": pass_count,
            "max_parallel_cap": worker_cap,
            "input_fingerprint": real_inputs,
        },
    }


def run_grid_search_session(
    plan: dict[str, Any],
    *,
    on_progress: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Execute one validated dashboard Grid Search through pipeline_lock."""
    if plan.get("schema") != GRID_SEARCH_SCHEMA:
        raise ValueError("Grid Search plan does not match the dashboard schema")
    config = dict(plan.get("command_config") or {})
    if signal_sources.profile_from_input(plan.get("input")) != signal_sources.require_profile(
        config.get("redis_profile") or signal_sources.ORIGINAL
    ):
        raise ValueError("Grid Search signal source does not match its frozen plan")
    if config.get("dashboard_session_kind") != "grid_search":
        raise ValueError("Plan is not a Grid Search Session")
    if config.get("pipeline_stage") != "grid" or config.get("method") != "grid":
        raise ValueError("Grid Search uses an invalid method or pipeline stage")
    if not config.get("pipeline") or config.get("force"):
        raise ValueError("Grid Search must use its pipeline cache and cannot force a rerun")
    if not config.get("expected_input_fingerprint"):
        raise ValueError("This Grid predates input fingerprints; create a new Grid session")
    if len(config.get("symbols") or []) != 1 or len(config.get("timeframe") or []) != 1:
        raise ValueError("Grid Search accepts one symbol and one timeframe per session")
    parameter_space = config.get("parameter_space") or {}
    profile = _load_dashboard_profile(str(config.get("strategy_profile") or ""))
    normalized_ksl = profile.param_levels("KslLevel", parameter_space.get("KslLevel"))
    normalized_ktp = profile.param_levels("KtpLevel", parameter_space.get("KtpLevel"))
    expected_count = len(normalized_ksl) * len(normalized_ktp)
    if expected_count != int((plan.get("input") or {}).get("pass_count") or 0):
        raise ValueError("Grid Search parameter-space size does not match its frozen input snapshot")
    return facilitator.run_grid(config, on_progress=on_progress, should_cancel=should_cancel)


def preview_walkforward_windows(
    *, start: str, end: str, is_months: int, oos_months: int, step_months: int,
) -> list[Any]:
    """Read-only Walk-forward window preview.  It never invokes cTrader CLI."""
    return walkforward.walkforward_windows({
        "start": str(start), "end": str(end),
        "walkforward": {
            "is_months": _enum_index(is_months, "In-sample months"),
            "oos_months": _enum_index(oos_months, "Out-of-sample months"),
            "step_months": _enum_index(step_months, "Step months"),
        },
    })


def prepare_walkforward_session(
    *,
    symbol: str,
    timeframe: str,
    strategy: str,
    start: str,
    end: str,
    balance: float,
    risk_percent: float,
    max_parallel: int,
    is_months: int,
    oos_months: int,
    step_months: int,
    objective: str = "net_profit",
    frozen_input_fingerprint: dict[str, Any] | None = None,
    redis_profile: str = signal_sources.ORIGINAL,
) -> dict[str, Any]:
    """Freeze an independent full-range Walk-forward protocol-v1 plan."""
    normalized_strategy = str(strategy).strip().lower().replace(" ", "")
    if normalized_strategy not in STRATEGY_PROFILES:
        raise ValueError(f"Strategy is not supported by the dashboard: {strategy!r}")
    normalized_symbol = str(symbol).strip()
    normalized_timeframe = str(timeframe).strip().lower()
    if normalized_symbol not in SYMBOL_GROUPS:
        raise ValueError(f"Symbol is not supported by the dashboard signal mapping: {normalized_symbol!r}")
    profile = _load_dashboard_profile(STRATEGY_PROFILES[normalized_strategy])
    engine = load_engine_profile(DEFAULT_ENGINE_PROFILE)
    if engine.backend != "cli":
        raise ValueError("Walk-forward Session supports only the CLI engine")
    allowed_timeframes = _profile_timeframes(profile, normalized_strategy)
    if normalized_timeframe not in allowed_timeframes:
        raise ValueError(
            f"{normalized_strategy} does not support timeframe {normalized_timeframe!r}; "
            f"use one of: {', '.join(allowed_timeframes)}"
        )
    if not profile.algo_path.is_file():
        raise FileNotFoundError(f"Strategy algo was not found: {profile.algo_path}")
    start_date = date.fromisoformat(str(start))
    end_date = date.fromisoformat(str(end))
    if start_date >= end_date:
        raise ValueError("End date must be after start date")
    if start_date.day > 28:
        raise ValueError("Walk-forward start date must be on day 1-28 to keep OOS windows contiguous")
    numeric_balance = float(balance)
    numeric_risk = float(risk_percent)
    if not math.isfinite(numeric_balance) or numeric_balance <= 0:
        raise ValueError("Starting balance (USD) must be greater than 0")
    if not math.isfinite(numeric_risk) or numeric_risk <= 0:
        raise ValueError("Risk per trade must be greater than 0")
    selected_signal_profile = signal_sources.require_profile(redis_profile)
    workers = _enum_index(max_parallel, "Parallel CLI workers")
    worker_cap = int(getattr(facilitator, "MAX_PARALLEL_CAP", 16))
    if workers < 1 or workers > worker_cap:
        raise ValueError(f"Parallel CLI workers must be between 1 and {worker_cap}")
    is_value = _enum_index(is_months, "In-sample months")
    oos_value = _enum_index(oos_months, "Out-of-sample months")
    step_value = _enum_index(step_months, "Step months")
    if step_value != oos_value:
        raise ValueError("Protocol v1 requires Step months to equal Out-of-sample months")
    parameter_space = {
        "KslLevel": profile.param_levels("KslLevel", True),
        "KtpLevel": profile.param_levels("KtpLevel", True),
    }
    normalized_objective = str(objective).strip().lower()
    if normalized_objective not in WALKFORWARD_OBJECTIVES:
        raise ValueError(f"Walk-forward objective must be one of: {', '.join(WALKFORWARD_OBJECTIVES)}")
    protocol = {
        # [2026-09-29] Không còn ngưỡng số-lệnh-tối-thiểu — xem walkforward.WF_MIN_TRADES.
        "min_trades": 0,
        "objective": normalized_objective,
        "selection_rules": ["plateau", "best"],
        "plateau_dimensions": ["KslLevel", "KtpLevel"],
    }
    wf = {"is_months": is_value, "oos_months": oos_value, "step_months": step_value}
    windows = walkforward.walkforward_windows({
        "start": start_date.isoformat(), "end": end_date.isoformat(), "walkforward": wf,
    })
    if not windows:
        raise ValueError("This date range does not produce a complete Walk-forward window")
    fixed_params = {"RiskPercent": numeric_risk}
    command_base = {
        "symbols": [normalized_symbol], "timeframe": [normalized_timeframe],
        **_single_channel_overrides(normalized_strategy, normalized_symbol, normalized_timeframe),
        "strategy": normalized_strategy, "strategy_profile": profile.id,
        "engine_profile": engine.id, "start": start_date.isoformat(), "end": end_date.isoformat(),
        "balance": numeric_balance, "parameter_space": parameter_space, "fixed_params": fixed_params,
        "walkforward": wf, **protocol, "max_parallel": workers, "keep_logs": False,
        "locked": True, "dashboard_session_kind": "walkforward",
        **signal_sources.command_field(selected_signal_profile),
    }
    real_inputs = frozen_input_fingerprint or facilitator.input_fingerprint({"name": "dashboard_input_fingerprint", **command_base})
    identity = {
        "schema": WALKFORWARD_SCHEMA, "strategy": normalized_strategy,
        "strategy_profile": profile.id, "engine_profile": engine.id, "symbol": normalized_symbol,
        "timeframe": normalized_timeframe, "start": start_date.isoformat(), "end": end_date.isoformat(),
        "balance": numeric_balance, "fixed_params": fixed_params, "parameter_space": parameter_space,
        "walkforward": wf, "protocol": protocol, "input_fingerprint": real_inputs,
        # New window dates get a new experiment: the core refuses to reopen one
        # recorded under the old (one-day-overlap) convention.
        "window_convention": walkforward.WINDOW_CONVENTION,
        **signal_sources.identity_field(selected_signal_profile),
    }
    execution_key = _execution_key(identity)
    stem = f"dashboard_walkforward_{execution_key[:16]}"
    command_config = {"name": stem, "pipeline": stem, **command_base,
                      "expected_input_fingerprint": real_inputs}
    strategy_label = STRATEGY_LABELS[normalized_strategy]
    grid_size = len(parameter_space["KslLevel"]) * len(parameter_space["KtpLevel"])
    return {
        "schema": WALKFORWARD_SCHEMA,
        "title": (f"Walk-forward - {normalized_symbol} - {strategy_label} - {normalized_timeframe.upper()} - {normalized_objective} - "
                  f"{len(windows)} windows - {start_date.isoformat()} to {end_date.isoformat()}"),
        "note": "", "execution_key": execution_key, "pipeline": stem, "experiment": stem,
        "input": {**identity, "redis_profile": selected_signal_profile,
                  "max_parallel": workers, "window_count": len(windows),
                  "train_backtests": len(windows) * grid_size,
                  "max_oos_backtests": len(windows) * len(protocol["selection_rules"])},
        "command_config": command_config,
        "preflight": {"engine_backend": engine.backend, "data_mode": engine.data_mode,
                      "algo_path": str(profile.algo_path), "max_parallel_cap": worker_cap,
                      "full_ksl_count": len(parameter_space["KslLevel"]),
                      "full_ktp_count": len(parameter_space["KtpLevel"]),
                      "input_fingerprint": real_inputs},
    }


def run_walkforward_session(
    plan: dict[str, Any], *, on_progress: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Execute a frozen Walk-forward plan through its dedicated pipeline lock."""
    if plan.get("schema") != WALKFORWARD_SCHEMA:
        raise ValueError("Walk-forward plan does not match the dashboard schema")
    config = dict(plan.get("command_config") or {})
    if signal_sources.profile_from_input(plan.get("input")) != signal_sources.require_profile(
        config.get("redis_profile") or signal_sources.ORIGINAL
    ):
        raise ValueError("Walk-forward signal source does not match its frozen plan")
    if config.get("dashboard_session_kind") != "walkforward":
        raise ValueError("Plan is not a Walk-forward Session")
    forbidden = {"zones", "force", "pipeline_stage"}.intersection(config)
    if forbidden:
        raise ValueError(f"Walk-forward plan contains forbidden fields: {', '.join(sorted(forbidden))}")
    if not config.get("pipeline") or not config.get("expected_input_fingerprint"):
        raise ValueError("This Walk-forward plan is missing its frozen pipeline or input fingerprint")
    profile = _load_dashboard_profile(str(config.get("strategy_profile") or ""))
    space = config.get("parameter_space") or {}
    if space.get("KslLevel") != profile.param_levels("KslLevel", True) or space.get("KtpLevel") != profile.param_levels("KtpLevel", True):
        raise ValueError("Walk-forward must use the full kSL and kTP enum range")
    wf = config.get("walkforward") or {}
    if wf.get("step_months") != wf.get("oos_months"):
        raise ValueError("Protocol v1 requires Step months to equal Out-of-sample months")
    if date.fromisoformat(str(config.get("start"))).day > 28:
        raise ValueError("Walk-forward start date must be on day 1-28 to keep OOS windows contiguous")
    if config.get("objective") not in WALKFORWARD_OBJECTIVES:
        raise ValueError("Walk-forward objective is not supported")
    if config.get("min_trades") != 0 or config.get("selection_rules") != ["plateau", "best"] or config.get("plateau_dimensions") != ["KslLevel", "KtpLevel"]:
        raise ValueError("Walk-forward protocol v1 has changed; create a new session")
    if config.get("pipeline") != plan.get("pipeline") or config.get("name") != plan.get("experiment"):
        raise ValueError("Walk-forward pipeline does not match its frozen plan")
    if config.get("expected_input_fingerprint") != (plan.get("input") or {}).get("input_fingerprint"):
        raise ValueError("Walk-forward input fingerprint does not match its frozen plan")
    return walkforward.run_walkforward(config, on_progress=on_progress, should_cancel=should_cancel)


def validate_walkforward_runtime(plan: dict[str, Any]) -> dict[str, str]:
    if plan.get("schema") != WALKFORWARD_SCHEMA:
        raise ValueError("Walk-forward plan does not match the dashboard schema")
    config = plan.get("command_config") or {}
    workers = _enum_index(config.get("max_parallel"), "Parallel CLI workers")
    if workers < 1 or workers > int(getattr(facilitator, "MAX_PARALLEL_CAP", 16)):
        raise ValueError("Parallel CLI workers are outside the allowed range")
    auth = cli_runner.auth_options()
    cli_path = cli_runner.select_cli()
    if not cli_path.is_file():
        raise FileNotFoundError(f"ctrader-cli.exe was not found: {cli_path}")
    return {"cli_path": str(cli_path), "account": auth["ACCOUNT"]}


def validate_grid_search_runtime(plan: dict[str, Any]) -> dict[str, str]:
    """Validate CLI availability and authentication without starting a backtest."""
    if plan.get("schema") != GRID_SEARCH_SCHEMA:
        raise ValueError("Grid Search plan does not match the dashboard schema")
    config = plan.get("command_config") or {}
    workers = _enum_index(config.get("max_parallel"), "Parallel CLI workers")
    worker_cap = int(getattr(facilitator, "MAX_PARALLEL_CAP", 16))
    if workers < 1 or workers > worker_cap:
        raise ValueError(f"Parallel CLI workers must be between 1 and {worker_cap}")
    auth = cli_runner.auth_options()
    cli_path = cli_runner.select_cli()
    if not cli_path.is_file():
        raise FileNotFoundError(f"ctrader-cli.exe was not found: {cli_path}")
    return {"cli_path": str(cli_path), "account": auth["ACCOUNT"]}


def _enum_index(value: Any, field: str) -> int:
    """Không âm thầm biến 3.7 thành enum index 3 ở ranh giới dashboard."""
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    number = float(value)
    if not math.isfinite(number) or not number.is_integer():
        raise ValueError(f"{field} must be an integer")
    return int(number)


def _enum_selection(profile: Any, field: str, values: list[Any]) -> list[str]:
    """Validate and canonicalise a selection using the profile's enum order."""
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError(f"Select at least one {field} Fibonacci level")
    selected = set(profile.param_levels(field, list(values)))
    return [value for value in profile.param_levels(field, True) if value in selected]


def completed_execution_policy_available() -> bool:
    """Whether the installed core explicitly supports the diagnostics policy.

    Older core versions silently ignore unknown readout config keys. Requiring a
    published capability prevents the dashboard from accidentally showing a
    90-trial strict result as though it analysed all completed executions.
    """
    policies = getattr(readout, "SUPPORTED_TRIAL_POLICIES", ())
    return COMPLETED_EXECUTION_TRIAL_POLICY in policies


def _require_completed_execution_policy() -> None:
    if not completed_execution_policy_available():
        raise RuntimeError(
            "The installed core does not yet support "
            f"trial_policy={COMPLETED_EXECUTION_TRIAL_POLICY!r}. "
            "Update core readout before running these diagnostics."
        )


def prepare_grid_diagnostics_session(
    *,
    source_session: dict[str, Any],
    source_snapshot: dict[str, Any],
    min_trades: int = 0,
    blocks: int = 16,
    objective: str = "net_profit",
) -> dict[str, Any]:
    """Freeze a DSR/PBO plan without running analysis or mutating core artifacts."""
    if source_session.get("kind") != "grid_search":
        raise ValueError("DSR/PBO requires a Grid Search source session")
    normalized_objective = str(objective).strip().lower()
    if normalized_objective not in DIAGNOSTICS_OBJECTIVES:
        raise ValueError(f"Selection objective must be one of: {', '.join(DIAGNOSTICS_OBJECTIVES)}")
    if normalized_objective not in getattr(readout, "SUPPORTED_OBJECTIVES", ()):
        raise RuntimeError(
            f"The installed core cannot evaluate the {normalized_objective!r} selection objective. "
            "No diagnostics draft was created."
        )
    if source_session.get("state") not in {"completed", "cached"}:
        raise ValueError("The source Grid must be completed or cached")
    source_plan = source_session.get("plan") or {}
    if source_plan.get("schema") != GRID_SEARCH_SCHEMA:
        raise ValueError("The source Grid plan does not match the dashboard schema")

    source_input = source_plan.get("input") or {}
    profile = _load_dashboard_profile(str(source_input.get("strategy_profile") or ""))
    parameter_space = source_input.get("parameter_space") or {}
    for field in ("KslLevel", "KtpLevel"):
        selected = profile.param_levels(field, parameter_space.get(field))
        full_range = profile.param_levels(field, True)
        if selected != full_range:
            raise ValueError(
                f"The source Grid must use the full {field} range. "
                "Subset Grids require an explicit research provenance contract."
            )

    minimum = _enum_index(min_trades, "Minimum trades")
    if minimum < 0:
        raise ValueError("Minimum trades cannot be negative")
    block_count = _enum_index(blocks, "PBO blocks")
    if block_count < 2 or block_count % 2:
        raise ValueError("PBO blocks must be an even integer greater than or equal to 2")

    pass_count = int(source_input.get("pass_count") or 0)
    completed_execution_count = int(source_snapshot.get("completed_execution_count") or 0)
    strict_eligible_count = int(source_snapshot.get("eligible_count") or 0)
    margin_trial_count = int(source_snapshot.get("margin_rejection_trial_count") or 0)
    margin_event_count = int(source_snapshot.get("margin_rejection_event_count") or 0)
    row_count = int(source_snapshot.get("row_count") or 0)
    artifact_sha256 = str(source_snapshot.get("artifact_sha256") or "")
    if not artifact_sha256:
        raise ValueError("The source Grid index fingerprint is unavailable")
    if row_count != pass_count or completed_execution_count != pass_count:
        raise ValueError(
            f"The source Grid is incomplete for completed-execution diagnostics: "
            f"{row_count:,}/{pass_count:,} rows exist and "
            f"{completed_execution_count:,}/{pass_count:,} completed valid executions."
        )

    source_session_id = str(source_session.get("session_id") or "")
    source_experiment = str(source_session.get("experiment") or "")
    source_pipeline = str(source_session.get("pipeline") or "")
    source_execution_key = str(source_session.get("execution_key") or "")
    if not all((source_session_id, source_experiment, source_pipeline, source_execution_key)):
        raise ValueError("The source Grid lineage is incomplete")

    diagnostic_input = {
        "source_session_id": source_session_id,
        "source_experiment": source_experiment,
        "source_pipeline": source_pipeline,
        "source_execution_key": source_execution_key,
        "source_artifact_sha256": artifact_sha256,
        "source_pass_count": pass_count,
        "trial_policy": COMPLETED_EXECUTION_TRIAL_POLICY,
        "completed_execution_trials": completed_execution_count,
        "strict_eligible_trials": strict_eligible_count,
        "margin_rejection_trials": margin_trial_count,
        "margin_rejection_events": margin_event_count,
        "field": "balance",
        "timezone": "Europe/Prague",
        "initial_balance": float(source_input.get("balance")),
        "min_trades": minimum,
        "blocks": block_count,
        "objective": normalized_objective,
    }
    execution_key = hashlib.sha256(
        json.dumps(diagnostic_input, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    experiment = f"dashboard_diagnostics_{execution_key[:16]}"
    return {
        "schema": GRID_DIAGNOSTICS_SCHEMA,
        "title": f"DSR + PBO - {source_session.get('title')}",
        "note": "",
        "execution_key": execution_key,
        "pipeline": source_pipeline,
        "experiment": experiment,
        "input": {**diagnostic_input,
                  "source_redis_profile": signal_sources.profile_from_input(source_input)},
        "command_config": {
            "method": "grid",
            "source_experiment": source_experiment,
            "pipeline": source_pipeline,
            "trial_policy": COMPLETED_EXECUTION_TRIAL_POLICY,
            "field": "balance",
            "timezone": "Europe/Prague",
            "initial_balance": float(source_input.get("balance")),
            "min_trades": minimum,
            "blocks": block_count,
            "objective": normalized_objective,
        },
        "preflight": {
            "source_state": source_session.get("state"),
            "source_rows": row_count,
            "completed_execution_trials": completed_execution_count,
            "strict_eligible_trials": strict_eligible_count,
            "margin_rejection_trials": margin_trial_count,
            "full_parameter_range": True,
            "calls_ctrader_cli": False,
        },
    }


def validate_grid_diagnostics_source(
    plan: dict[str, Any], source_session: dict[str, Any], source_snapshot: dict[str, Any]
) -> None:
    """Revalidate frozen source lineage immediately before analysis starts."""
    if plan.get("schema") != GRID_DIAGNOSTICS_SCHEMA:
        raise ValueError("Grid diagnostics plan does not match the dashboard schema")
    frozen = plan.get("input") or {}
    if frozen.get("trial_policy") != COMPLETED_EXECUTION_TRIAL_POLICY:
        raise ValueError("Grid diagnostics uses an unsupported trial policy")
    _require_completed_execution_policy()
    if source_session.get("state") not in {"completed", "cached"}:
        raise ValueError("The source Grid is no longer terminal")
    if source_session.get("session_id") != frozen.get("source_session_id"):
        raise ValueError("The source Grid session does not match the frozen plan")
    if source_session.get("execution_key") != frozen.get("source_execution_key"):
        raise ValueError("The source Grid execution key changed")
    if source_snapshot.get("artifact_sha256") != frozen.get("source_artifact_sha256"):
        raise ValueError("The source Grid artifact changed after this diagnostics draft was created")
    expected = int(frozen.get("source_pass_count") or 0)
    if int(source_snapshot.get("row_count") or 0) != expected:
        raise ValueError("The source Grid row count changed")
    if int(source_snapshot.get("completed_execution_count") or 0) != expected:
        raise ValueError("The source Grid no longer has the required completed executions")


def run_grid_diagnostics_session(
    plan: dict[str, Any],
    *,
    on_progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Run DSR then PBO over persisted Grid reports; never invokes cTrader CLI."""
    if plan.get("schema") != GRID_DIAGNOSTICS_SCHEMA:
        raise ValueError("Grid diagnostics plan does not match the dashboard schema")
    _require_completed_execution_policy()
    config = dict(plan.get("command_config") or {})
    source_experiment = str(config.pop("source_experiment", ""))
    pipeline = str(config.pop("pipeline", ""))
    method = str(config.pop("method", ""))
    if method != "grid" or not source_experiment or not pipeline:
        raise ValueError("Grid diagnostics command lineage is incomplete")

    if on_progress:
        on_progress(5, "Calculating Deflated Sharpe Ratio")
    dsr_result = readout.run_dsr(method, source_experiment, config, pipeline=pipeline)
    trials = int(dsr_result.get("included_trials") or dsr_result.get("n_trials") or 0)
    if on_progress:
        blocks = int(config.get("blocks") or 16)
        split_count = math.comb(blocks, blocks // 2)
        on_progress(
            35,
            "DSR complete; PBO is evaluating "
            f"{split_count:,} chronological splits across {trials:,} trials",
        )

    def pbo_progress(done: int, total: int) -> None:
        # PBO is the long phase (minutes at 100 trials), so it owns 35-100%.
        on_progress(
            35 + 65 * done / total,
            f"PBO: {done:,} / {total:,} chronological splits evaluated across {trials:,} trials",
        )

    pbo_result = readout.run_pbo(
        method, source_experiment, config, pipeline=pipeline,
        on_progress=pbo_progress if on_progress else None,
    )
    if on_progress:
        on_progress(100, "DSR and PBO complete")
    return {
        "dsr": dsr_result,
        "pbo": pbo_result,
        "pipeline": pipeline,
        "source_experiment": source_experiment,
        "lock_stages": {
            "dsr": pipeline_lock.get_stage(pipeline, "dsr"),
            "pbo": pipeline_lock.get_stage(pipeline, "pbo"),
        },
        "cached": bool(dsr_result.get("cached")) and bool(pbo_result.get("cached")),
    }
