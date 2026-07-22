"""Shared fixed-width table cell/key-value helpers for operator reports.

`core_engine.core.live.telemetry` and `core_engine.core.historical.reporter`
used to each define their own byte-identical `_cell`/`_kv` helpers; this
module is the single source of truth for both.
"""

from __future__ import annotations

from typing import Any

from core_engine.util.logkit.formatters import clean


def cell(value: Any, width: int, *, align: str = "left") -> str:
    """Fixed-width, single-line cell truncated with an ellipsis."""
    text = "-" if value is None or value == "" else clean(value)
    if len(text) > width:
        text = text[: max(1, width - 3)].rstrip() + "..."
    if align == "right":
        return text.rjust(width)
    if align == "center":
        return text.center(width)
    return text.ljust(width)


def kv(label: str, value: Any, *, label_width: int = 17) -> str:
    """One `  label: value` line for operator report blocks."""
    return f"  {label:<{label_width}}: {clean(value)}"
