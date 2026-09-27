from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# tests/ nằm ngang hàng core_engine/cli_engine/api_engine dưới bo_workflow/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core_engine import cli_runner


class RunnerCapabilityAndStallTests(unittest.TestCase):
    """Phần cổng từ pipeline/core/cli.py + core/runner.py 2026-09-12, đổi tên
    module runner.py -> cli_runner.py 2026-09-17 khi tách engine — chỉ test
    logic thuần (regex match / state machine), không spawn ctrader-cli thật."""

    def test_command_and_option_matching_is_line_anchored(self) -> None:
        text = "Available commands:\n  backtest <algo> ...\n  --data-mode <ticks|m1|open>\n"
        self.assertTrue(cli_runner._match_command(text, "backtest"))
        self.assertFalse(cli_runner._match_command(text, "backtes"))
        self.assertTrue(cli_runner._match_option(text, "data-mode"))
        self.assertFalse(cli_runner._match_option(text, "data-modex"))

    def test_last_progress_pct_reads_most_recent_line(self) -> None:
        lines = ["Progress | Backtesting | 10.0 %", "noise", "Progress | Backtesting | 42.5 %"]
        self.assertEqual(cli_runner.last_progress_pct(lines), 42.5)
        self.assertEqual(cli_runner.last_progress_pct([]), -1.0)

    def test_option_probe_detects_rejection_marker(self) -> None:
        rejected = 'Unknown command line parameters found: precise-conversion'
        accepted = 'Parameter --data-file must be specified with --data-mode=m1-csv!'
        markers = "|".join(map(cli_runner.re.escape, cli_runner._REJECTION_MARKERS))
        pattern = cli_runner.re.compile(rf"(?:{markers})[^\r\n]*(?<![\w-])precise-conversion(?![\w-])")
        self.assertTrue(pattern.search(rejected))
        self.assertFalse(pattern.search(accepted))

    def test_poll_reports_stalled_before_report_appears(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report = Path(td) / "report.json"
            clock = {"t": 0.0}
            with patch.object(cli_runner.time, "monotonic", lambda: clock["t"]):
                poll = cli_runner._make_poll(report, stall_s=10, startup_stall_s=100)
                self.assertEqual(poll(["Progress | Backtesting | 5.0 %"], []), "")
                clock["t"] = 5.0  # vẫn 5%, chưa quá stall_s kể từ mốc 5%
                self.assertEqual(poll(["Progress | Backtesting | 5.0 %"], []), "")
                clock["t"] = 16.0  # 11s không nhích further từ pct=5 -> stalled
                self.assertEqual(poll(["Progress | Backtesting | 5.0 %"], []), "stalled")

    def test_poll_uses_longer_threshold_while_still_at_zero_percent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report = Path(td) / "report.json"
            clock = {"t": 0.0}
            with patch.object(cli_runner.time, "monotonic", lambda: clock["t"]):
                poll = cli_runner._make_poll(report, stall_s=10, startup_stall_s=100)
                self.assertEqual(poll(["Progress | Backtesting | 0.0 %"], []), "")
                clock["t"] = 50.0  # 50s ở 0% vẫn dưới startup_stall_s=100 -> chưa stalled
                self.assertEqual(poll(["Progress | Backtesting | 0.0 %"], []), "")


if __name__ == "__main__":
    unittest.main()
