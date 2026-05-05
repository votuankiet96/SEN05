"""CSV export helper for cTrader backtest imports."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd


EXPORT_COLUMNS = ["bartime", "atr", "signal"]


def export_signals(
    df: pd.DataFrame,
    *,
    symbol: str,
    strategy: str,
    output_dir: str | Path | None = None,
) -> Path:
    """Export non-zero signal rows as bartime, atr and signal."""
    base_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parents[1] / "exports"
    base_dir.mkdir(parents=True, exist_ok=True)

    missing = [col for col in EXPORT_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing export columns: {missing}")

    out = df.loc[df["signal"].fillna(0).astype(int).ne(0), EXPORT_COLUMNS].copy()
    out["bartime"] = pd.to_datetime(out["bartime"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
    out["atr"] = pd.to_numeric(out["atr"], errors="coerce")
    out["signal"] = out["signal"].astype(int)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"{symbol.upper()}_{strategy}_{timestamp}.csv"
    path = base_dir / filename
    out.to_csv(path, index=False, encoding="utf-8-sig")
    return path

