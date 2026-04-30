"""Research display utilities for Combo notebooks."""

from __future__ import annotations

from .dashboard import *  # noqa: F401,F403
from .symbol import *  # noqa: F401,F403
from .export import *  # noqa: F401,F403
from ._shared import (
    annotate_optimizer_plateau,
    filter_optimizer_candidates,
    rank_optimizer_candidates,
    summarize_candidate_validation,
    summarize_optimizer_plateau,
    validate_symbol_candidates,
)
from .symbol import _count_signal_window_rows

from .dashboard import __all__ as _dashboard_all
from .symbol import __all__ as _symbol_all
from .export import __all__ as _export_all

_optimizer_all = {
    "annotate_optimizer_plateau",
    "filter_optimizer_candidates",
    "rank_optimizer_candidates",
    "summarize_candidate_validation",
    "summarize_optimizer_plateau",
    "validate_symbol_candidates",
}

__all__ = sorted(set(_dashboard_all) | set(_symbol_all) | set(_export_all) | _optimizer_all | {"_count_signal_window_rows"})
