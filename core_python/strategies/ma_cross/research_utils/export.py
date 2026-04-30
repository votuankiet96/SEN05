"""Export helpers cho research result bundles."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pandas as pd


def export_research_bundle(
    bundle: Mapping[str, Any],
    *,
    name: str,
    output_dir: str | Path = "output/ma_cross/research",
) -> Path:
    """Ghi tất cả DataFrame/Series trong bundle ra CSV.

    Args:
        bundle: Dict {tên → DataFrame hoặc Series}.
        name: Tên subfolder trong output_dir.
        output_dir: Thư mục gốc output.

    Returns:
        Path đến subfolder đã ghi.
    """
    out = Path(output_dir) / name
    out.mkdir(parents=True, exist_ok=True)
    for key, value in bundle.items():
        if isinstance(value, pd.DataFrame):
            value.to_csv(out / f"{key}.csv", index=True)
        elif isinstance(value, pd.Series):
            value.to_csv(out / f"{key}.csv", header=True)
    return out


__all__ = ["export_research_bundle"]
