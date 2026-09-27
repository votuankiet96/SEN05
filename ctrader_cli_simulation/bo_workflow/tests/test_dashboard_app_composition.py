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
for label in ("Single Backtest", "Grid Search", "Walk-forward", "DSR + PBO"):
    assert label in layout, label
for label in ("global-signal-profile", "Original (DB2)", "Trend-filtered (DB3)"):
    assert label in layout, label

def count(pattern):
    return sum(bool(re.search(pattern, key)) for key in app.app.callback_map)

actual = {
    "single": count(r"(^|\.)session-"),
    "grid": count(r"(^|\.)grid-"),
    "dsr_pbo": count(r"(^|\.)diagnostics-"),
    "walkforward": count(r"(^|\.)wf-"),
}
expected = {"single": 9, "grid": 10, "dsr_pbo": 10, "walkforward": 13}
assert actual == expected, {"actual": actual, "keys": list(app.app.callback_map)}
assert "global-signal-message.children" in app.app.callback_map
print(json.dumps(actual))
'''
        completed = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)


if __name__ == "__main__":
    unittest.main()
