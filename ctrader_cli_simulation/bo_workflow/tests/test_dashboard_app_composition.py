"""No-CLI release gates for the Dashboard composition root.

These tests start neither cTrader CLI nor a Dash background job.  A fresh
Python subprocess is intentional: Dash transfers global callbacks during its
first WSGI setup, so inspecting ``callback_map`` immediately after import is
not a valid application-level check.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"


class DashboardCompositionTests(unittest.TestCase):
    def test_fresh_app_serves_every_feature_and_registers_callbacks(self) -> None:
        script = r'''
import json
import re
import sys
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root / "dashboard"))
import app

client = app.app.server.test_client()
responses = {path: client.get(path) for path in ("/", "/_dash-layout", "/_dash-dependencies")}
assert all(response.status_code == 200 for response in responses.values()), {
    path: response.status_code for path, response in responses.items()
}
layout = responses["/_dash-layout"].get_data(as_text=True)
for label in ("Single Backtest", "Multi-channel", "Grid Search", "Walk-forward", "DSR + PBO"):
    assert label in layout, label
for label in ("global-signal-profile", "RAW (db0)", "KNN (db1)", "Breakout (db2)"):
    assert label in layout, label
assert "session-active-run" in layout

def count(pattern):
    return sum(bool(re.search(pattern, key)) for key in app.app.callback_map)

actual = {
    "single": count(r"(^|\.)session-"),
    "multi": count(r"(^|\.)multi-"),
    "grid": count(r"(^|\.)grid-"),
    "dsr_pbo": count(r"(^|\.)diagnostics-"),
    "walkforward": count(r"(^|\.)wf-"),
}
expected = {"single": 9, "multi": 15, "grid": 11, "dsr_pbo": 11, "walkforward": 14}
assert actual == expected, {"actual": actual, "keys": list(app.app.callback_map)}
assert "global-signal-message.children" in app.app.callback_map
single_status = next(meta for key, meta in app.app.callback_map.items() if "session-progress-bar.value" in key)
assert "refresh-session-view-btn" in {item["id"] for item in single_status["inputs"]}
assert "run-session-btn" in {item["id"] for item in single_status["inputs"]}
assert "session-active-run.children" in next(key for key in app.app.callback_map if "session-progress-bar.value" in key)
diagnostics_status = next(meta for key, meta in app.app.callback_map.items() if "diagnostics-progress-bar.value" in key)
assert "diagnostics-refresh-btn" in {item["id"] for item in diagnostics_status["inputs"]}
for button, selector, detail, status in (
    ("refresh-session-view-btn", "session-select.data", "session-detail.children", "session-progress-bar.value"),
    ("multi-refresh-btn", "multi-session-select.data", "multi-session-detail.children", "multi-progress-bar.value"),
    ("grid-refresh-btn", "grid-session-select.data", "grid-session-detail.children", "grid-progress-bar.value"),
    ("diagnostics-refresh-btn", "diagnostics-session-select.data", "diagnostics-session-detail.children", "diagnostics-progress-bar.value"),
    ("wf-refresh-btn", "wf-session-select.data", "wf-session-detail.children", "wf-live-progress.children"),
):
    for output in (selector, detail, status):
        meta = next(meta for meta in app.app.callback_map.values()
                    if output in [str(item) for item in (meta["output"] if isinstance(meta["output"], list) else [meta["output"]])])
        assert button in {item["id"] for item in meta["inputs"]}, (button, output)
for prefix in ("multi", "grid", "diagnostics", "wf"):
    assert any(f"{prefix}-create-message.children" in key and f"{prefix}-delete-message.children" in key
               for key in app.app.callback_map), prefix
for prefix, button in (("session", "refresh-session-view-btn"), ("grid", "grid-refresh-btn"),
                       ("multi", "multi-refresh-btn"), ("diagnostics", "diagnostics-refresh-btn"),
                       ("wf", "wf-refresh-btn")):
    key, meta = next((key, meta) for key, meta in app.app.callback_map.items()
                     if f"{prefix}-create-message.children" in key and f"{prefix}-delete-message.children" in key)
    response = client.post("/_dash-update-component", json={
        "output": key,
        "outputs": [{"id": item.component_id, "property": item.component_property} for item in meta["output"]],
        "inputs": [{"id": button, "property": "n_clicks", "value": 1}],
        "state": [],
        "changedPropIds": [f"{button}.n_clicks"],
    })
    assert response.status_code == 200, (prefix, response.status_code, response.get_data(as_text=True)[:300])
    assert all(item["children"] is None for item in response.get_json()["response"].values()), prefix
print(json.dumps(actual))
'''
        completed = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)

    def test_no_callback_waits_on_a_background_callback_output(self) -> None:
        """[2026-09-30] dash-renderer does not run a callback while one of its
        Inputs is the output of a callback still in flight, and the block spreads
        to everything downstream (getReadyCallbacks /
        getAllSubsequentOutputsForCallback). A background job runs for minutes,
        so any callback listening to its output froze the live status panel for
        the whole run until a hard refresh. Its output may only be displayed."""
        script = r'''
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "dashboard"))
import app

app.app.server.test_client().get("/_dash-dependencies")  # transfers the global callbacks

def outputs_of(key):
    return key.strip(".").split("...") if key.startswith("..") else [key]

background = {out: key for key, meta in app.app.callback_map.items() if meta.get("background")
              for out in outputs_of(key)}
assert len(background) == 5, background
offenders = []
for key, meta in app.app.callback_map.items():
    for item in meta["inputs"]:
        prop = f"{item['id']}.{item['property']}"
        if prop in background:
            offenders.append((key, prop))
assert not offenders, offenders
print(json.dumps(sorted(background)))
'''
        completed = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)


if __name__ == "__main__":
    unittest.main()
