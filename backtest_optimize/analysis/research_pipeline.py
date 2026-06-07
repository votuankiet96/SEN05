"""Shared research flow for stability maps and walk-forward validation."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pandas as pd

from backtest_optimize.analysis.metrics import clusters_to_frame, summarize
from backtest_optimize.analysis.optimize import (
    add_stability_scores,
    build_refined_grid,
    grid_size,
    run_grid,
)
from backtest_optimize.analysis.versioning import stable_hash
from backtest_optimize.analysis.walkforward import WalkForwardWindow, split_by_window
from backtest_optimize.contracts import BacktestResult, MarketSpec
from backtest_optimize.execution.engine import run_configured_backtest

BacktestRunner = Callable[..., BacktestResult]

PARAM_SECTIONS = {
    "entry": "entry_params",
    "stoploss": "sl_params",
    "takeprofit": "tp_params",
    "orders": "order_params",
    "exits": "exit_params",
}
TOP_LEVEL_PARAM_SECTIONS = {"sizing", "risk"}


@dataclass(frozen=True)
class WalkForwardResearchResult:
    """Tables produced by a rolling train-select-test research run."""

    windows: pd.DataFrame
    selected_params: pd.DataFrame
    oos_clusters: pd.DataFrame


def apply_parameter_overrides(
    base_config: Mapping[str, Any],
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply dotted grid parameters to a reusable execution config."""
    config = deepcopy(dict(base_config))
    for raw_key, value in params.items():
        key = str(raw_key)
        if "." not in key:
            config[key] = value
            continue
        section, param_name = key.split(".", 1)
        if section in TOP_LEVEL_PARAM_SECTIONS:
            config[param_name] = value
            continue
        target = PARAM_SECTIONS.get(section)
        if target is None:
            raise ValueError(f"Unsupported parameter section: {section!r}")
        config.setdefault(target, {})[param_name] = value

    exit_mode = config.get("tp_params", {}).get("exit_mode")
    if exit_mode is not None:
        config["exit_model"] = "two_legs_sma20" if str(exit_mode) == "two_legs" else "fixed_only"
    return config


def evaluate_stability_map(
    *,
    signals: pd.DataFrame,
    bars: pd.DataFrame,
    symbol: str,
    timeframe: str,
    market_spec: MarketSpec | dict[str, Any],
    base_config: Mapping[str, Any],
    param_grid: Mapping[str, Sequence[Any] | Any],
    metric_col: str = "expectancy_r",
    neighborhood_pct: float = 0.15,
    neighborhood_steps: int | None = None,
    progress_callback: Callable[[int, int, dict[str, Any]], None] | None = None,
    runner: BacktestRunner = run_configured_backtest,
) -> pd.DataFrame:
    """Backtest every grid point and add local stability statistics."""
    param_cols = list(param_grid)

    def evaluate(params: dict[str, Any]) -> dict[str, Any]:
        run_config = apply_parameter_overrides(base_config, params)
        result = runner(
            signals=signals,
            bars=bars,
            symbol=symbol,
            timeframe=timeframe,
            market_spec=market_spec,
            run_config=run_config,
        )
        metrics = summarize(result)
        metrics["config_hash"] = stable_hash(run_config)
        return metrics

    results = run_grid(evaluate, param_grid, progress_callback=progress_callback)
    return add_stability_scores(
        results,
        metric_col=metric_col,
        param_cols=param_cols,
        neighborhood_pct=neighborhood_pct,
        neighborhood_steps=neighborhood_steps,
    )


def rank_stability_candidates(
    results: pd.DataFrame,
    *,
    param_cols: Sequence[str],
    metric_col: str = "expectancy_r",
    min_filled: int = 1,
    min_fill_rate: float = 0.0,
    max_pending_expired_rate: float = 1.0,
    max_ambiguity_rate: float = 1.0,
    min_local_count: int = 1,
    top_n: int | None = None,
) -> pd.DataFrame:
    """Filter and rank stable parameter candidates using explicit constraints."""
    if results.empty:
        return results.copy()
    score_col = f"{metric_col}_stability_score"
    count_col = f"{metric_col}_local_count"
    required = [*param_cols, metric_col, score_col, count_col]
    missing = [col for col in required if col not in results.columns]
    if missing:
        raise ValueError(f"Missing candidate columns: {missing}")

    out = results.copy()
    def numeric_column(name: str, default: float) -> pd.Series:
        if name not in out.columns:
            return pd.Series(default, index=out.index, dtype=float)
        return pd.to_numeric(out[name], errors="coerce").fillna(default)

    fill_rate = numeric_column("fill_rate", 0.0)
    filled_count = numeric_column("filled_count", 0.0)
    pending_rate = numeric_column("pending_expired_rate", 0.0)
    ambiguity_rate = numeric_column("ambiguity_rate", 0.0)
    local_count = pd.to_numeric(out[count_col], errors="coerce").fillna(0)
    score = pd.to_numeric(out[score_col], errors="coerce")
    metric = pd.to_numeric(out[metric_col], errors="coerce")

    out["candidate_eligible"] = (
        score.notna()
        & metric.notna()
        & (filled_count >= min_filled)
        & (fill_rate >= min_fill_rate)
        & (pending_rate <= max_pending_expired_rate)
        & (ambiguity_rate <= max_ambiguity_rate)
        & (local_count >= min_local_count)
    )
    out["candidate_id"] = [
        stable_hash({col: row[col] for col in param_cols})[:12]
        for _, row in out.iterrows()
    ]
    out = out.sort_values(
        ["candidate_eligible", score_col, metric_col],
        ascending=[False, False, False],
        na_position="last",
    ).reset_index(drop=True)
    out["candidate_rank"] = range(1, len(out) + 1)
    if top_n is not None:
        out = out[out["candidate_eligible"]].head(int(top_n)).copy()
    return out


def run_walkforward_research(
    *,
    signals: pd.DataFrame,
    bars: pd.DataFrame,
    windows: Sequence[WalkForwardWindow],
    symbol: str,
    timeframe: str,
    market_spec: MarketSpec | dict[str, Any],
    base_config: Mapping[str, Any],
    param_grid: Mapping[str, Sequence[Any] | Any],
    metric_col: str = "expectancy_r",
    neighborhood_pct: float = 0.15,
    neighborhood_steps: int | None = None,
    candidate_rules: Mapping[str, Any] | None = None,
    refinement_values: Mapping[str, Sequence[Any] | Any] | None = None,
    fine_top_n: int = 3,
    fine_neighbor_steps: int = 1,
    fine_max_combinations: int = 250,
    min_train_signals: int = 1,
    min_test_signals: int = 1,
    runner: BacktestRunner = run_configured_backtest,
) -> WalkForwardResearchResult:
    """Optimize on each train window, then test only the selected parameters."""
    rules = dict(candidate_rules or {})
    param_cols = list(param_grid)
    window_rows: list[dict[str, Any]] = []
    selected_rows: list[dict[str, Any]] = []
    cluster_frames: list[pd.DataFrame] = []
    bar_times = pd.to_datetime(bars["bartime"])

    for window in windows:
        train_signals, test_signals = split_by_window(signals, window, time_col="bartime")
        train_bars = bars[bar_times < window.train_end].copy()
        test_bars = bars[bar_times < window.test_end].copy()
        train_signals = _signals_with_future_bar(train_signals, train_bars)
        test_signals = _signals_with_future_bar(test_signals, test_bars)
        base_row = {
            "window": window.index,
            "train_start": window.train_start,
            "train_end": window.train_end,
            "test_start": window.test_start,
            "test_end": window.test_end,
            "train_signal_count": len(train_signals),
            "oos_signal_count": len(test_signals),
        }
        if len(train_signals) < min_train_signals or len(test_signals) < min_test_signals:
            window_rows.append({**base_row, "status": "insufficient_signals"})
            continue

        train_map = evaluate_stability_map(
            signals=train_signals,
            bars=train_bars,
            symbol=symbol,
            timeframe=timeframe,
            market_spec=market_spec,
            base_config=base_config,
            param_grid=param_grid,
            metric_col=metric_col,
            neighborhood_pct=neighborhood_pct,
            neighborhood_steps=neighborhood_steps,
            runner=runner,
        )
        coarse_ranked = rank_stability_candidates(
            train_map,
            param_cols=param_cols,
            metric_col=metric_col,
            **rules,
        )
        ranked = coarse_ranked
        fine_grid: dict[str, list[Any]] | None = None
        if refinement_values is not None and not coarse_ranked.empty:
            fine_grid = build_refined_grid(
                coarse_ranked,
                refinement_values,
                param_cols=param_cols,
                top_n=fine_top_n,
                neighbor_steps=fine_neighbor_steps,
                max_combinations=fine_max_combinations,
            )
            fine_map = evaluate_stability_map(
                signals=train_signals,
                bars=train_bars,
                symbol=symbol,
                timeframe=timeframe,
                market_spec=market_spec,
                base_config=base_config,
                param_grid=fine_grid,
                metric_col=metric_col,
                neighborhood_pct=neighborhood_pct,
                neighborhood_steps=neighborhood_steps,
                runner=runner,
            )
            ranked = rank_stability_candidates(
                fine_map,
                param_cols=param_cols,
                metric_col=metric_col,
                **rules,
            )
        ranked = ranked[ranked.get("candidate_eligible", False)].head(1).copy()
        if ranked.empty:
            window_rows.append({**base_row, "status": "no_eligible_candidate"})
            continue

        selected = ranked.iloc[0]
        params = {col: selected[col] for col in param_cols}
        selected_config = apply_parameter_overrides(base_config, params)
        oos_result = runner(
            signals=test_signals,
            bars=test_bars,
            symbol=symbol,
            timeframe=timeframe,
            market_spec=market_spec,
            run_config=selected_config,
        )
        oos_summary = summarize(oos_result)
        candidate_id = str(selected["candidate_id"])
        selected_rows.append(
            {
                **base_row,
                "candidate_id": candidate_id,
                "train_search_stage": "fine" if fine_grid is not None else "coarse",
                "train_coarse_grid_runs": grid_size(param_grid),
                "train_fine_grid_runs": grid_size(fine_grid) if fine_grid is not None else 0,
                **params,
                **{f"train_{key}": selected.get(key) for key in (
                    metric_col,
                    f"{metric_col}_stability_score",
                    f"{metric_col}_local_count",
                    "fill_rate",
                    "filled_count",
                )},
            }
        )
        window_rows.append(
            {
                **base_row,
                "status": "tested",
                "candidate_id": candidate_id,
                **{f"oos_{key}": value for key, value in oos_summary.items()},
            }
        )
        frame = clusters_to_frame(oos_result)
        if not frame.empty:
            frame.insert(0, "candidate_id", candidate_id)
            frame.insert(0, "window", window.index)
            cluster_frames.append(frame)

    return WalkForwardResearchResult(
        windows=pd.DataFrame(window_rows),
        selected_params=pd.DataFrame(selected_rows),
        oos_clusters=pd.concat(cluster_frames, ignore_index=True) if cluster_frames else pd.DataFrame(),
    )


def _signals_with_future_bar(signals: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    """Keep signals that still have at least one later bar inside the window."""
    if signals.empty or bars.empty:
        return signals.iloc[0:0].copy()
    last_bar_time = pd.to_datetime(bars["bartime"]).max()
    signal_times = pd.to_datetime(signals["bartime"])
    return signals[signal_times < last_bar_time].copy()
