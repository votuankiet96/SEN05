"""Candidate ranking and filtering cho MA Cross grid search results."""

from __future__ import annotations

import pandas as pd


def rank_grid(df: pd.DataFrame, score_column: str = "sharpe") -> pd.DataFrame:
    """Sắp xếp grid result DataFrame theo score_column giảm dần.

    Nếu score_column không tồn tại, trả về df nguyên bản.
    Tie-break: profit_factor → total_trades.
    """
    if df.empty:
        return df
    sort_cols = [c for c in [score_column, "profit_factor", "total_trades"] if c in df.columns]
    return df.sort_values(sort_cols, ascending=[False] * len(sort_cols)).reset_index(drop=True)


def select_top_candidates(
    df: pd.DataFrame,
    *,
    top_n: int = 10,
    min_trades: int = 30,
    min_profit_factor: float = 1.0,
    max_drawdown: float | None = None,
    score_column: str = "sharpe",
) -> pd.DataFrame:
    """Lọc và xếp hạng candidates từ grid search results.

    Args:
        df: Output của run_symbol_grid_search hoặc metrics_frame.
        top_n: Số candidates trả về tối đa.
        min_trades: Loại bỏ candidates có total_trades < min_trades.
        min_profit_factor: Loại bỏ candidates có profit_factor < threshold.
        max_drawdown: Nếu set, loại bỏ candidates có |max_drawdown| > threshold.
        score_column: Cột dùng để xếp hạng chính.

    Returns:
        DataFrame đã lọc và xếp hạng, tối đa top_n hàng.
    """
    if df.empty:
        return df
    out = df.copy()
    if "total_trades" in out.columns:
        out = out[out["total_trades"].fillna(0) >= min_trades]
    if "profit_factor" in out.columns:
        out = out[out["profit_factor"].fillna(0) >= min_profit_factor]
    if max_drawdown is not None and "max_drawdown" in out.columns:
        out = out[out["max_drawdown"].abs().fillna(999) <= max_drawdown]
    return rank_grid(out, score_column=score_column).head(top_n).reset_index(drop=True)


__all__ = ["rank_grid", "select_top_candidates"]
