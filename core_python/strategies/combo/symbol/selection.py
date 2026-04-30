"""Candidate selection helpers for Combo symbol optimization."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd


OPTIMIZER_PARAM_COLS = ["ktp", "x", "min_rr"]


def filter_optimizer_candidates(
    grid: pd.DataFrame,
    rules: Mapping[str, Any] | None = None,
    *,
    return_all: bool = False,
) -> pd.DataFrame:
    """Apply practical pass/fail gates before ranking optimizer candidates."""
    if grid is None or grid.empty:
        return pd.DataFrame()

    rules = dict(rules or {})
    frame = grid.copy()
    trades = _optimizer_metric_series(frame, ["trades", "total_trades"])
    pf = _optimizer_metric_series(frame, ["pf", "profit_factor"])
    ret = _optimizer_metric_series(frame, ["ret", "total_return"])
    dd = _optimizer_metric_series(frame, ["maxdd", "max_drawdown"]).abs()
    sharpe = _optimizer_metric_series(frame, ["sharpe"])

    frame["filter_trades"] = trades
    frame["filter_pf"] = pf
    frame["filter_return"] = ret
    frame["filter_drawdown_abs"] = dd
    frame["filter_sharpe"] = sharpe
    frame["pass_min_trades"] = trades >= float(rules.get("min_trades", 0))
    frame["pass_min_profit_factor"] = pf >= float(rules.get("min_profit_factor", 0))
    frame["pass_positive_return"] = ret > float(rules.get("min_return", 0))
    frame["pass_max_drawdown"] = dd <= float(rules.get("max_drawdown_pct", np.inf))
    frame["pass_min_sharpe"] = sharpe >= float(rules.get("min_sharpe", -np.inf))
    pass_cols = [
        "pass_min_trades",
        "pass_min_profit_factor",
        "pass_positive_return",
        "pass_max_drawdown",
        "pass_min_sharpe",
    ]
    frame["filter_pass"] = frame[pass_cols].all(axis=1)
    return frame if return_all else frame[frame["filter_pass"]].copy()


def rank_optimizer_candidates(grid: pd.DataFrame, *, mode: str = "robust") -> pd.DataFrame:
    """Rank candidates by robust score or by legacy Sharpe ordering."""
    if grid is None or grid.empty:
        return pd.DataFrame()

    frame = grid.copy()
    trades = _optimizer_metric_series(frame, ["trades", "total_trades"])
    pf = _optimizer_metric_series(frame, ["pf", "profit_factor"]).replace([np.inf, -np.inf], np.nan)
    ret = _optimizer_metric_series(frame, ["ret", "total_return"])
    dd = _optimizer_metric_series(frame, ["maxdd", "max_drawdown"]).abs()
    sharpe = _optimizer_metric_series(frame, ["sharpe"])
    raw_score = _optimizer_metric_series(frame, ["score"])

    pf_score = np.log1p(pf.clip(lower=0, upper=10).fillna(0))
    ret_score = np.sqrt(ret.clip(lower=0))
    trade_confidence = (np.log1p(trades.clip(lower=0)) / np.log1p(100)).clip(upper=1)
    plateau_bonus = pd.Series(0.0, index=frame.index)
    if "stable_ratio" in frame.columns:
        plateau_bonus += pd.to_numeric(frame["stable_ratio"], errors="coerce").fillna(0).clip(0, 1) * 0.75
    if "is_plateau" in frame.columns:
        plateau_bonus += frame["is_plateau"].fillna(False).astype(bool).astype(float) * 0.50

    frame["robust_score"] = (
        sharpe.fillna(0) * 2.0
        + pf_score * 1.5
        + ret_score * 0.25
        + trade_confidence
        + raw_score.fillna(0).clip(lower=0) * 0.10
        + plateau_bonus
        - dd.fillna(0) * 0.08
    ).round(4)

    if mode == "sharpe":
        sort_cols = [c for c in ["sharpe", "score", "pf", "ret"] if c in frame.columns]
    else:
        sort_cols = [c for c in ["robust_score", "sharpe", "score", "pf", "ret"] if c in frame.columns]
    return frame.sort_values(sort_cols, ascending=False, ignore_index=True)


def summarize_optimizer_plateau(
    grid: pd.DataFrame,
    best: Mapping[str, Any] | pd.Series | None = None,
    *,
    score_col: str = "sharpe",
    radius: int = 1,
    threshold_ratio: float = 0.5,
) -> dict[str, Any]:
    """Return plateau stability for one selected optimizer candidate."""
    if grid is None or grid.empty:
        return {
            "is_plateau": False,
            "stable_ratio": 0.0,
            "neighbors_checked": 0,
            "neighbors_stable": 0,
            "warning": "Grid is empty; cannot check plateau stability.",
        }
    if best is None:
        score_col = _first_existing(grid, [score_col, "sharpe", "score", "pf", "ret"])
        best = grid.sort_values(score_col, ascending=False).iloc[0]

    points, resolved_score_col = _optimizer_points(grid, score_col)
    return _summarize_plateau_from_points(
        points,
        best,
        score_col=resolved_score_col,
        radius=radius,
        threshold_ratio=threshold_ratio,
    )


def annotate_optimizer_plateau(
    grid: pd.DataFrame,
    *,
    score_col: str = "sharpe",
    radius: int = 1,
    threshold_ratio: float = 0.5,
) -> pd.DataFrame:
    """Add plateau columns for each candidate in a compact grid."""
    if grid is None or grid.empty:
        return pd.DataFrame()

    frame = grid.copy()
    points, resolved_score_col = _optimizer_points(frame, score_col)
    plateaus = [
        _summarize_plateau_from_points(
            points,
            row,
            score_col=resolved_score_col,
            radius=radius,
            threshold_ratio=threshold_ratio,
        )
        for _, row in frame.iterrows()
    ]
    frame["is_plateau"] = [p.get("is_plateau", False) for p in plateaus]
    frame["stable_ratio"] = [p.get("stable_ratio", 0.0) for p in plateaus]
    frame["neighbors_checked"] = [p.get("neighbors_checked", 0) for p in plateaus]
    frame["neighbors_stable"] = [p.get("neighbors_stable", 0) for p in plateaus]
    return frame


def validate_symbol_candidates(
    symbol: str,
    candidates: pd.DataFrame,
    run_config: Mapping[str, Any],
) -> tuple[pd.DataFrame, list[Any]]:
    """Run full backtest validation for ranked symbol candidates."""
    if candidates is None or candidates.empty:
        return pd.DataFrame(), []

    from core_python.strategies.combo.symbol.backtest import run_symbol_backtest

    rows: list[dict[str, Any]] = []
    results: list[Any] = []
    for rank, (_, candidate) in enumerate(candidates.iterrows(), start=1):
        params = _optimizer_params_from_row(candidate)
        result = run_symbol_backtest(
            symbol,
            init_eq=float(run_config.get("initial_balance", 100_000.0)),
            account_mode=str(run_config.get("account_mode", "standard")),
            date_from=run_config.get("date_from"),
            date_to=run_config.get("date_to"),
            tf=run_config.get("tf"),
            max_bars=int(run_config.get("max_bars", 60_000)),
            indicator_overrides=run_config.get("indicator_overrides"),
            strategy_overrides=run_config.get("strategy_overrides"),
            costs=run_config.get("costs"),
            broker_profile=run_config.get("broker_profile"),
            symbol_overrides=params,
        )
        metrics = {
            k: v
            for k, v in (result.metrics or {}).items()
            if k not in {"monthly_pnl_table", "ftmo_account_check"}
        }
        rows.append({
            "candidate_rank": rank,
            **params,
            **metrics,
        })
        results.append(result)

    frame = pd.DataFrame(rows)
    ranked = rank_optimizer_candidates(frame, mode=str(run_config.get("selection_mode", "robust")))
    if not ranked.empty:
        ranked.insert(0, "full_validation_rank", range(1, len(ranked) + 1))
        ranked["ranking_basis"] = "full_backtest"
    return ranked, results


def summarize_candidate_validation(
    fast_grid: pd.DataFrame,
    validations: pd.DataFrame,
) -> pd.DataFrame:
    """Compare fast optimizer metrics against full validation metrics."""
    if validations is None or validations.empty:
        return pd.DataFrame()
    if fast_grid is None or fast_grid.empty:
        return validations.copy()

    fast = fast_grid.copy()
    full = validations.copy()
    for col in OPTIMIZER_PARAM_COLS:
        if col in fast:
            fast[col] = pd.to_numeric(fast[col], errors="coerce")
        if col in full:
            full[col] = pd.to_numeric(full[col], errors="coerce")

    fast_view = fast[OPTIMIZER_PARAM_COLS].copy()
    fast_view["fast_trades"] = _optimizer_metric_series(fast, ["trades", "total_trades"])
    fast_view["fast_pf"] = _optimizer_metric_series(fast, ["pf", "profit_factor"])
    fast_view["fast_return"] = _optimizer_metric_series(fast, ["ret", "total_return"])
    fast_view["fast_drawdown"] = _optimizer_metric_series(fast, ["maxdd", "max_drawdown"]).abs()
    fast_view["fast_sharpe"] = _optimizer_metric_series(fast, ["sharpe"])

    full_view = full.copy()
    full_view["full_trades"] = _optimizer_metric_series(full, ["total_trades", "trades"])
    full_view["full_pf"] = _optimizer_metric_series(full, ["profit_factor", "pf"])
    full_view["full_return"] = _optimizer_metric_series(full, ["total_return", "ret"])
    full_view["full_drawdown"] = _optimizer_metric_series(full, ["max_drawdown", "maxdd"]).abs()
    full_view["full_sharpe"] = _optimizer_metric_series(full, ["sharpe"])

    merged = full_view.merge(fast_view, on=OPTIMIZER_PARAM_COLS, how="left")
    merged["trades_gap"] = merged["full_trades"] - merged["fast_trades"]
    merged["pf_gap"] = merged["full_pf"] - merged["fast_pf"]
    merged["return_gap"] = merged["full_return"] - merged["fast_return"]
    merged["drawdown_gap"] = merged["full_drawdown"] - merged["fast_drawdown"]
    merged["sharpe_gap"] = merged["full_sharpe"] - merged["fast_sharpe"]
    return merged


def _optimizer_metric_series(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    for col in columns:
        if col in frame.columns:
            return pd.to_numeric(frame[col], errors="coerce").fillna(0.0)
    return pd.Series(0.0, index=frame.index)


def _optimizer_params_from_row(row: Mapping[str, Any] | pd.Series) -> dict[str, Any]:
    return {
        "ktp":    float(row["ktp"]),
        "x":      float(row["x"]),
        "min_rr": float(row.get("min_rr", 0.0)),
    }


def _optimizer_points(
    grid: pd.DataFrame,
    score_col: str,
) -> tuple[dict[tuple[float, float, float], dict[str, float]], str]:
    resolved_score_col = _first_existing(grid, [score_col, "sharpe", "score", "pf", "ret"])
    required_cols = [c for c in OPTIMIZER_PARAM_COLS if c in grid.columns]
    points: dict[tuple[float, float, float], dict[str, float]] = {}
    for _, row in grid.dropna(subset=required_cols).iterrows():
        key = (float(row["ktp"]), float(row["x"]), float(row.get("min_rr", 0.0)))
        points[key] = {"sharpe": float(row.get(resolved_score_col, row.get("sharpe", 0.0)) or 0.0)}
    return points, resolved_score_col


def _summarize_plateau_from_points(
    points: dict[tuple[float, float, float, int], dict[str, float]],
    best: Mapping[str, Any] | pd.Series,
    *,
    score_col: str,
    radius: int,
    threshold_ratio: float,
) -> dict[str, Any]:
    from core_python.strategies.combo.symbol.walkforward import check_plateau_stability

    best_params = _optimizer_params_from_row(best)
    best_score = float(best.get(score_col, best.get("sharpe", 0.0)) or 0.0)
    return check_plateau_stability(
        points,
        best_params,
        best_score,
        radius=radius,
        threshold_ratio=threshold_ratio,
    )


def analyze_x_fill_rate(
    symbol_key: str,
    df: pd.DataFrame,
    *,
    ttl_bars: int = 3,
    x_candidates: list[float] | None = None,
) -> pd.DataFrame:
    """Tính fill rate tại từng mức x cho signal bars của symbol.

    Với mỗi BUY signal bar: lệnh được fill nếu max(high) của ttl_bars bars tiếp theo
    đạt bar_high + x. Tương tự cho SELL signal.

    Parameters
    ----------
    symbol_key   : tên symbol (chỉ dùng để log).
    df           : DataFrame H4 OHLCV đã có cột 'signal' (1=BUY, -1=SELL, 0=none)
                   từ add_combo_indicators + detect_combo_signals.
    ttl_bars     : số bar tối đa chờ fill (mặc định 3 = pending_ttl_bars).
    x_candidates : list giá trị x cần kiểm tra; nếu None tự tạo từ giá trị hợp lý.

    Returns
    -------
    DataFrame với cột: x, n_buy, n_sell, fill_buy, fill_sell, fill_rate
    """
    if "signal" not in df.columns or "high" not in df.columns or "low" not in df.columns:
        return pd.DataFrame()

    highs = df["high"].to_numpy()
    lows  = df["low"].to_numpy()
    sigs  = df["signal"].to_numpy() if "signal" in df.columns else np.zeros(len(df))

    buy_idx  = np.where(sigs == 1)[0]
    sell_idx = np.where(sigs == -1)[0]

    if len(buy_idx) == 0 and len(sell_idx) == 0:
        return pd.DataFrame()

    # Tự tạo x_candidates dựa trên khoảng typical bar range
    if x_candidates is None:
        typical_range = np.median(highs - lows)
        step = max(0.1, round(typical_range / 20, 2))
        x_max = round(typical_range * 1.5, 2)
        cands: list[float] = []
        v = 0.0
        while v <= x_max + step / 2:
            cands.append(round(v, 2))
            v = round(v + step, 2)
        x_candidates = cands

    n = len(df)
    rows = []
    for x in x_candidates:
        filled_buy = filled_sell = 0

        for i in buy_idx:
            end = min(i + ttl_bars + 1, n)
            if end > i + 1 and np.max(highs[i + 1:end]) >= highs[i] + x:
                filled_buy += 1

        for i in sell_idx:
            end = min(i + ttl_bars + 1, n)
            if end > i + 1 and np.min(lows[i + 1:end]) <= lows[i] - x:
                filled_sell += 1

        n_buy  = len(buy_idx)
        n_sell = len(sell_idx)
        n_total = n_buy + n_sell
        fill_buy  = filled_buy  / n_buy  if n_buy  > 0 else np.nan
        fill_sell = filled_sell / n_sell if n_sell > 0 else np.nan
        fill_combined = (filled_buy + filled_sell) / n_total if n_total > 0 else np.nan
        rows.append({
            "x":         x,
            "n_buy":     n_buy,
            "n_sell":    n_sell,
            "fill_buy":  round(fill_buy,  4) if not np.isnan(fill_buy)  else np.nan,
            "fill_sell": round(fill_sell, 4) if not np.isnan(fill_sell) else np.nan,
            "fill_rate": round(fill_combined, 4) if not np.isnan(fill_combined) else np.nan,
        })
    return pd.DataFrame(rows)


def recommend_x_range(
    symbol_key: str,
    df: pd.DataFrame,
    *,
    n_values: int = 5,
    fill_min: float = 0.30,
    fill_max: float = 0.90,
    ttl_bars: int = 3,
) -> list[float]:
    """Trả về list n_values giá trị x trong khoảng fill rate [fill_min, fill_max].

    Dùng kết quả từ analyze_x_fill_rate để tìm khoảng x phù hợp per-symbol,
    rồi chia đều thành n_values giá trị để đưa vào optimizer grid.

    Nếu không tìm được khoảng hợp lệ, trả về fallback dựa trên median bar range.
    """
    fill_df = analyze_x_fill_rate(symbol_key, df, ttl_bars=ttl_bars)
    if fill_df.empty:
        return []

    valid = fill_df.dropna(subset=["fill_rate"])
    in_range = valid[(valid["fill_rate"] >= fill_min) & (valid["fill_rate"] <= fill_max)]

    if in_range.empty:
        # Fallback: lấy khoảng có fill_rate gần nhất với trung điểm [fill_min, fill_max]
        mid = (fill_min + fill_max) / 2
        closest = valid.iloc[(valid["fill_rate"] - mid).abs().argsort()[:n_values]]
        return sorted(round(float(v), 2) for v in closest["x"])

    x_lo = float(in_range["x"].min())
    x_hi = float(in_range["x"].max())
    if x_lo == x_hi:
        return [round(x_lo, 2)]

    step = (x_hi - x_lo) / (n_values - 1) if n_values > 1 else 0.0
    return [round(x_lo + i * step, 2) for i in range(n_values)]


def _first_existing(frame: pd.DataFrame, columns: list[str]) -> str:
    for col in columns:
        if col in frame.columns:
            return col
    return frame.columns[-1]
