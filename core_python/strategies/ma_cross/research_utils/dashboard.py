"""Notebook display helpers: cấu hình, strategy summary, run config."""

from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from ._shared import display, show_note
from ..config import summary


def configure_notebook() -> None:
    """Cấu hình pandas display options chuẩn cho research notebook MA Cross."""
    pd.set_option("display.max_columns", 80)
    pd.set_option("display.width", 160)
    pd.set_option("display.float_format", lambda x: f"{x:,.4f}")


def show_strategy_summary() -> None:
    """Hiển thị thông tin strategy MA Cross dưới dạng code block."""
    show_note("```text\n" + summary() + "\n```")


def show_run_config(title: str, config: Mapping[str, Any]) -> None:
    """Hiển thị một dict config dưới dạng bảng. Fallback sang print ngoài notebook."""
    show_note(f"### {title}")
    display(pd.Series(dict(config), name="value").to_frame())


__all__ = ["configure_notebook", "show_strategy_summary", "show_run_config"]
