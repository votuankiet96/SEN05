"""Presentation conventions shared by every dashboard feature.

Pure Dash helpers only: no persistence, no core_engine import. Each constant
records the guideline it implements so feature views stay consistent.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

from dash import html, no_update
from dash.dash_table.Format import Format, Group, Scheme, Symbol

# Restore values the user typed after a page reload (Dash `persistence`).
# Only apply it to fields that no initial callback overwrites; callback-driven
# fields (timeframes, kSL/kTP, dates) are recomputed from these on load.
PERSIST = {"persistence": True, "persistence_type": "local"}
# The strategy values changed when the Multi bots were replaced by BacktestBot.
# A distinct persistence key prevents stale Multi values from overriding the
# current strategy selection after deployment.
STRATEGY_PERSIST = {
    "persistence": "dashboard-backtestbot-strategies-v1",
    "persistence_type": "local",
}

# Numeric inputs trigger Redis/budget previews. Waiting for a 400 ms pause in
# typing keeps feedback inside Nielsen's 1-second flow limit without querying
# on every keystroke.
INPUT_DEBOUNCE_MS = 400


def strategy_label(strategy: Any, options: dict[str, Any] | None = None) -> str:
    """Display current strategy labels and identify saved removed profiles."""
    value = str(strategy or "")
    for item in (options or {}).get("strategies", []):
        if item.get("value") == value:
            return str(item.get("label") or value)
    removed = {
        "combomulti": "ComboMulti",
        "macrossmulti": "MACrossMulti",
        "macdbreakmulti": "MACDBreakMulti",
        "macross": "MA Cross",
        "macdbreak": "MACD Break",
    }
    if value in removed:
        return f"{removed[value]} (profile removed)"
    return value or "—"


def status_region(component_id: str, children: Any = None) -> html.Div:
    """Live region for action results and progress (WCAG 2.2 SC 4.1.3)."""
    return html.Div(children, id=component_id, role="status", **{"aria-live": "polite"})


# Live status of a background run comes ONLY from the session store, read by a
# 1-second dcc.Interval. A callback must never take a background callback's
# output (the `*-run-message` result) as an Input: dash-renderer does not run a
# callback while any of its Inputs is an output of a callback still in flight,
# and that block propagates to everything downstream (getReadyCallbacks /
# getAllSubsequentOutputsForCallback in dash_renderer). Doing so froze the whole
# status panel - progress, Stop button, results - for the entire run until a
# hard refresh (measured 2026-09-30, headless Edge on the real dashboard: zero
# progress requests during a run). tests/test_dashboard_contracts.py enforces it.
def poll_disabled(keep_polling: bool, is_tick: bool) -> Any:
    """Value for a progress Interval's `disabled` output.

    Any trigger may switch polling on; only a poll tick, which has just read the
    session store, may switch it off. Otherwise a non-tick trigger (such as the
    background job's `running` flag clearing) could stop the ticker before any
    tick has seen the finished state, and the finished result would never be
    drawn."""
    if keep_polling:
        return False
    return True if is_tick else no_update


# DataTable numeric columns: keep numbers numeric so native sorting and
# filtering compare values, not formatted text (dash.plotly.com/datatable/typing),
# and right-align them for comparison (GOV.UK Design System, tables).
# Display rule of this dashboard: values are CUT toward zero, never rounded.
# d3's ",.2f" rounds, so every value is passed through truncated() first; a
# number that already has <= 2 decimals is then shown with exactly its digits.
# Format's builder methods mutate the object: never derive from these shared
# instances; define a new constant instead.
MONEY = Format(precision=2, scheme=Scheme.fixed, group=Group.yes, symbol=Symbol.yes, symbol_suffix=" USD")
PERCENT = Format(precision=2, scheme=Scheme.fixed, group=Group.yes, symbol=Symbol.yes, symbol_suffix="%")
SECONDS = Format(precision=2, scheme=Scheme.fixed, group=Group.yes, symbol=Symbol.yes, symbol_suffix=" s")
DECIMAL = Format(precision=2, scheme=Scheme.fixed, group=Group.yes)
INTEGER = Format(precision=0, scheme=Scheme.fixed, group=Group.yes)


def numeric_column(column_id: str, name: str, number_format: Format) -> dict[str, Any]:
    return {"id": column_id, "name": name, "type": "numeric", "format": number_format}


def right_align(column_ids: list[str]) -> list[dict[str, Any]]:
    return [{"if": {"column_id": column_id}, "textAlign": "right"} for column_id in column_ids]


def as_number(value: Any) -> float | None:
    """Finite float or None, so a missing metric sorts as empty, not as text."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


OBJECTIVE_LABELS = {"net_profit": "net profit", "profit_factor": "profit factor", "win_rate": "win rate"}


def objective_label(value: Any) -> str:
    return OBJECTIVE_LABELS.get(str(value or ""), str(value or "-").replace("_", " "))


def selected_dsr(dsr_result: dict[str, Any]) -> tuple[float, str]:
    """DSR of the combination the research actually selects, with its basis.

    Core results since 2026-09-28 carry `selected` (the top combination by the
    session's selection objective). Older results only have the DSR of the
    highest-Sharpe combination, which is usually a different combination, so
    the basis text says so instead of presenting it as the selected one."""
    chosen = dsr_result.get("selected")
    if isinstance(chosen, dict) and chosen.get("dsr") is not None:
        return float(chosen["dsr"]), f"top combination by {objective_label(dsr_result.get('objective'))}"
    return float(dsr_result.get("dsr") or 0), "highest-Sharpe combination (analysed before selection objectives)"


def truncated(value: Any, places: int = 2) -> float | None:
    """Finite number cut toward zero at `places` decimals (1.239 -> 1.23,
    -1.239 -> -1.23), the same rule as Decimal ROUND_DOWN used across the views."""
    if as_number(value) is None:
        return None
    try:
        cut = Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN)
    except (InvalidOperation, ValueError):
        return None
    return float(cut)
