"""BO Workflow dashboard composition root.

Feature packages own their views and callbacks. Shared command, query and
session-state boundaries remain at dashboard root so every feature follows the
same CQRS and persistence rules.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_DASHBOARD_ROOT = Path(__file__).resolve().parent
_BO_WORKFLOW_ROOT = _DASHBOARD_ROOT.parent
if str(_BO_WORKFLOW_ROOT) not in sys.path:
    sys.path.insert(0, str(_BO_WORKFLOW_ROOT))

import diskcache
from dash import Dash, DiskcacheManager, Input, Output, html
import dash_mantine_components as dmc

import data_access
import session_store
import signal_sources
from dsr_pbo import callbacks as dsr_pbo_callbacks
from dsr_pbo import views as dsr_pbo_views
from grid import callbacks as grid_callbacks
from grid import views as grid_views
from single import callbacks as single_callbacks  # noqa: F401 - registers Dash callbacks
from single import views as single_views
from walkforward import callbacks as walkforward_callbacks
from walkforward import views as walkforward_views

CACHE = diskcache.Cache(str(_DASHBOARD_ROOT / ".dashboard_cache"))
BACKGROUND_MANAGER = DiskcacheManager(CACHE)
FORM_OPTIONS = data_access.session_form_options()
session_store.reconcile_abandoned_sessions()

app = Dash(
    __name__,
    background_callback_manager=BACKGROUND_MANAGER,
    title="BO Workflow Dashboard",
)

def serve_layout() -> dmc.MantineProvider:
    """Read the persisted signal choice on every page load, including F5."""
    return dmc.MantineProvider(
        dmc.Container(size="xl", py="lg", children=[
        dmc.Title("BO Workflow Dashboard", order=2),
        dmc.Text(
            "Run and inspect core_engine workflows from one local dashboard.",
            c="dimmed",
        ),
        dmc.Paper(withBorder=True, radius="md", p="md", my="md", children=dmc.Group([
            dmc.Select(
                id="global-signal-profile", label="Signal source",
                data=list(signal_sources.OPTIONS),
                value=session_store.get_signal_profile(), clearable=False,
                w=260,
            ),
            dmc.Stack([
                dmc.Text("Applies to new drafts in Single Backtest, Grid Search and Walk-forward.", size="sm"),
                dmc.Text("Saved drafts and running jobs keep their original signal source.", size="xs", c="dimmed"),
                html.Div(id="global-signal-message"),
            ], gap=2),
        ], align="center")),
        dmc.Tabs(value="single-backtest", children=[
            dmc.TabsList([
                dmc.TabsTab("Single Backtest", value="single-backtest"),
                dmc.TabsTab("Grid Search", value="grid-search"),
                dmc.TabsTab("Walk-forward", value="walkforward"),
                dmc.TabsTab("DSR + PBO", value="grid-diagnostics"),
            ]),
            dmc.TabsPanel(
                single_views.layout(FORM_OPTIONS), value="single-backtest", pt="md",
            ),
            dmc.TabsPanel(
                grid_views.layout(FORM_OPTIONS), value="grid-search", pt="md",
            ),
            dmc.TabsPanel(
                walkforward_views.layout(FORM_OPTIONS), value="walkforward", pt="md",
            ),
            dmc.TabsPanel(
                dsr_pbo_views.layout(), value="grid-diagnostics", pt="md",
            ),
        ]),
    ]))


app.layout = serve_layout


@app.callback(
    Output("global-signal-message", "children"),
    Input("global-signal-profile", "value"),
    prevent_initial_call=True,
)
def save_signal_profile(value: str):
    try:
        profile = session_store.set_signal_profile(value)
        return dmc.Text(f"Active for new drafts: {signal_sources.LABELS[profile]}", size="xs", c="teal")
    except Exception as exc:
        return dmc.Text(f"Signal source was not saved: {exc}", size="xs", c="red")

grid_callbacks.register(app, FORM_OPTIONS)
walkforward_callbacks.register(app, FORM_OPTIONS)
dsr_pbo_callbacks.register(app)


if __name__ == "__main__":
    # Stable by default. Developers may opt in with BO_DASH_DEBUG=1.
    debug = os.environ.get("BO_DASH_DEBUG", "0").strip().lower() in {"1", "true", "yes"}
    app.run(debug=debug, host="127.0.0.1", port=8050)
