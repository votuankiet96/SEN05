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
from datetime import date
from pathlib import Path
from typing import Any, Callable

try:
    from . import signal_sources
except ImportError:  # app.py imports dashboard modules without a package prefix.
    import signal_sources

_BO_WORKFLOW_ROOT = Path(__file__).resolve().parents[1]
if str(_BO_WORKFLOW_ROOT) not in sys.path:
    sys.path.insert(0, str(_BO_WORKFLOW_ROOT))

from core_engine import cli_runner, facilitator  # noqa: E402
from core_engine.configuration import load_engine_profile, load_strategy_profile  # noqa: E402
from core_engine.optimize import walkforward  # noqa: E402
from core_engine.output_util import pipeline_lock, readout  # noqa: E402
from core_engine.signal_trans import SYMBOL_GROUPS  # noqa: E402

DEFAULT_ENGINE_PROFILE = "ctrader-5.9.16-ticks-approxfx-v1"
STRATEGY_PROFILES = {"combo": "combo-v1", "macross": "macross-v1"}
SINGLE_BACKTEST_SCHEMA = "bo-dashboard-single-backtest/v1"
GRID_SEARCH_SCHEMA = "bo-dashboard-grid-search/v1"
GRID_DIAGNOSTICS_SCHEMA = "bo-dashboard-grid-diagnostics/v1"
WALKFORWARD_SCHEMA = "bo-dashboard-walkforward/v1"
WALKFORWARD_OBJECTIVES = ("net_profit", "profit_factor", "win_rate")
COMPLETED_EXECUTION_TRIAL_POLICY = "completed_execution_v1"
InputFingerprintMismatch = facilitator.InputFingerprintMismatch


def _execution_key(identity: dict[str, Any]) -> str:
    """Return the stable identity hash for one immutable dashboard plan."""
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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

    profile = load_strategy_profile(STRATEGY_PROFILES[normalized_strategy])
    engine = load_engine_profile(DEFAULT_ENGINE_PROFILE)
    if engine.backend != "cli":
        raise ValueError("Single Backtest Session v1 supports only the CLI engine")
    if normalized_timeframe not in profile.default_timeframes:
        raise ValueError(
            f"{profile.strategy} does not support timeframe {normalized_timeframe!r}; "
            f"use one of: {', '.join(profile.default_timeframes)}"
        )
    if not profile.algo_path.is_file():
        raise FileNotFoundError(f"Strategy algo was not found: {profile.algo_path}")

    start_date = date.fromisoformat(str(start))
    end_date = date.fromisoformat(str(end))
    if start_date >= end_date:
        raise ValueError("End date must be after start date (end is exclusive)")
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
            f"{'MA Cross' if normalized_strategy == 'macross' else 'Combo'} · "
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

    profile = load_strategy_profile(STRATEGY_PROFILES[normalized_strategy])
    engine = load_engine_profile(DEFAULT_ENGINE_PROFILE)
    if engine.backend != "cli":
        raise ValueError("Grid Search Session supports only the CLI engine")
    if normalized_timeframe not in profile.default_timeframes:
        raise ValueError(
            f"{profile.strategy} does not support timeframe {normalized_timeframe!r}; "
            f"use one of: {', '.join(profile.default_timeframes)}"
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
    strategy_label = "MA Cross" if normalized_strategy == "macross" else "Combo"
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
    profile = load_strategy_profile(str(config.get("strategy_profile") or ""))
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
    profile = load_strategy_profile(STRATEGY_PROFILES[normalized_strategy])
    engine = load_engine_profile(DEFAULT_ENGINE_PROFILE)
    if engine.backend != "cli":
        raise ValueError("Walk-forward Session supports only the CLI engine")
    if normalized_timeframe not in profile.default_timeframes:
        raise ValueError(
            f"{profile.strategy} does not support timeframe {normalized_timeframe!r}; "
            f"use one of: {', '.join(profile.default_timeframes)}"
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
        "min_trades": 30,
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
        **signal_sources.identity_field(selected_signal_profile),
    }
    execution_key = _execution_key(identity)
    stem = f"dashboard_walkforward_{execution_key[:16]}"
    command_config = {"name": stem, "pipeline": stem, **command_base,
                      "expected_input_fingerprint": real_inputs}
    strategy_label = "MA Cross" if normalized_strategy == "macross" else "Combo"
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
    profile = load_strategy_profile(str(config.get("strategy_profile") or ""))
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
    if config.get("min_trades") != 30 or config.get("selection_rules") != ["plateau", "best"] or config.get("plateau_dimensions") != ["KslLevel", "KtpLevel"]:
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
) -> dict[str, Any]:
    """Freeze a DSR/PBO plan without running analysis or mutating core artifacts."""
    if source_session.get("kind") != "grid_search":
        raise ValueError("DSR/PBO requires a Grid Search source session")
    if source_session.get("state") not in {"completed", "cached"}:
        raise ValueError("The source Grid must be completed or cached")
    source_plan = source_session.get("plan") or {}
    if source_plan.get("schema") != GRID_SEARCH_SCHEMA:
        raise ValueError("The source Grid plan does not match the dashboard schema")

    source_input = source_plan.get("input") or {}
    profile = load_strategy_profile(str(source_input.get("strategy_profile") or ""))
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
    if on_progress:
        blocks = int(config.get("blocks") or 16)
        split_count = math.comb(blocks, blocks // 2)
        trials = int(dsr_result.get("included_trials") or dsr_result.get("n_trials") or 0)
        on_progress(
            35,
            "DSR complete; PBO is evaluating "
            f"{split_count:,} chronological splits across {trials:,} trials",
        )
    pbo_result = readout.run_pbo(method, source_experiment, config, pipeline=pipeline)
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
