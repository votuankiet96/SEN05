from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

# tests/ nằm ngang hàng core_engine/cli_engine/api_engine dưới bo_workflow/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core_engine import cli_runner
from core_engine.models import FailureCode, RunSpec, RunStatus


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

    def test_select_cli_probes_versions_once_until_a_binary_changes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            local = Path(td)
            bundled = local / "Spotware" / "cTrader" / "abc" / "ctrader-cli.exe"
            standalone = local / "Programs" / "cTrader CLI" / "ctrader-cli.exe"
            for path in (bundled, standalone):
                path.parent.mkdir(parents=True)
                path.write_bytes(b"x")
            versions = {str(bundled): (5, 9, 16, 0), str(standalone): (5, 9, 0, 38)}
            probes: list[str] = []

            def fake_version(path: Path) -> tuple[int, ...]:
                probes.append(str(path))
                return versions[str(path)]

            cli_runner._newest_cli.cache_clear()
            with patch.dict(cli_runner.os.environ, {"LOCALAPPDATA": td, "CTRADER_CLI": ""}), \
                    patch.object(cli_runner, "_version_key", side_effect=fake_version):
                self.assertEqual(cli_runner.select_cli(), bundled)
                self.assertEqual(cli_runner.select_cli(), bundled)
                self.assertEqual(len(probes), 2)
                standalone.write_bytes(b"updated")  # a new build must be re-probed
                versions[str(standalone)] = (5, 10, 0, 0)
                self.assertEqual(cli_runner.select_cli(), standalone)
                self.assertEqual(len(probes), 4)
            cli_runner._newest_cli.cache_clear()

    def test_poll_uses_longer_threshold_while_still_at_zero_percent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report = Path(td) / "report.json"
            clock = {"t": 0.0}
            with patch.object(cli_runner.time, "monotonic", lambda: clock["t"]):
                poll = cli_runner._make_poll(report, stall_s=10, startup_stall_s=100)
                self.assertEqual(poll(["Progress | Backtesting | 0.0 %"], []), "")
                clock["t"] = 50.0  # 50s ở 0% vẫn dưới startup_stall_s=100 -> chưa stalled
                self.assertEqual(poll(["Progress | Backtesting | 0.0 %"], []), "")


class SelectCliTests(unittest.TestCase):
    """Chọn CLI chỉ tin dòng `Version: x.y.z.w` của `ctrader-cli --version` (help.ctrader.com/ctrader-cli/setup)."""

    def setUp(self) -> None:
        cli_runner._cli_version.cache_clear()
        cli_runner._newest_cli.cache_clear()
        self.addCleanup(cli_runner._cli_version.cache_clear)
        self.addCleanup(cli_runner._newest_cli.cache_clear)

    def probe(self, stdout: str, stderr: str = ""):
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "ctrader-cli.exe"
            exe.write_bytes(b"x")
            run = type("Proc", (), {"stdout": stdout, "stderr": stderr})()
            with patch.object(cli_runner.subprocess, "run", return_value=run):
                return cli_runner._version_key(exe)

    def test_version_comes_from_the_version_line(self) -> None:
        self.assertEqual(self.probe("ctrader-cli.exe --version\nVersion: 5.10.18.54260\n"), (5, 10, 18, 54260))

    def test_numbers_in_an_error_message_are_not_a_version(self) -> None:
        """Bản cài riêng cần .NET 6 in lỗi nhắc '6.0.0' khi runtime 6 bị gỡ: không được coi đó là phiên bản 6.0.0."""
        error = "The framework 'Microsoft.NETCore.App', version '6.0.0' was not found."
        self.assertEqual(self.probe("", error), (-1,))

    def test_unusable_candidate_is_skipped_and_none_usable_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            launcher = Path(td) / "Spotware" / "cTrader" / "abc" / "ctrader-cli.exe"
            standalone = Path(td) / "Programs" / "cTrader CLI" / "ctrader-cli.exe"
            for path in (launcher, standalone):
                path.parent.mkdir(parents=True)
                path.write_bytes(b"x")
            keys = {str(launcher): (5, 10, 18, 54260), str(standalone): (-1,)}
            with patch.dict(cli_runner.os.environ, {"LOCALAPPDATA": td, "CTRADER_CLI": ""}), \
                    patch.object(cli_runner, "_version_key", side_effect=lambda p: keys[str(p)]):
                self.assertEqual(cli_runner.select_cli(), launcher)
                cli_runner._newest_cli.cache_clear()
                keys[str(launcher)] = (-1,)
                with self.assertRaisesRegex(RuntimeError, "chạy được"):
                    cli_runner.select_cli()


class ChildEnvironmentTests(unittest.TestCase):
    def test_child_environment_strips_option_names_added_by_cli_5_10(self) -> None:
        """[2026-10-08] `-e` đọc mọi option từ biến môi trường theo tên option viết hoa. CLI 5.10 thêm
        option mới (MC_*, REDOWNLOAD_DATA, ...): thiếu tên trong _OPTION_ENV thì biến shell cha trùng
        tên sẽ lọt xuống tiến trình con và âm thầm đổi hành vi backtest."""
        polluted = (
            "MC_METHOD", "MC-OUTPUT", "MC_SEED", "MC_ITERATIONS", "MC_BLOCK_SIZE", "MC_RUIN_FLOOR",
            "MC_STARTING_EQUITY", "REDOWNLOAD_DATA", "EQUITY_X_AXIS", "ALGO_FILE", "CBOT_SET", "RECONNECT_TIMEOUT",
        )
        with patch.dict(cli_runner.os.environ, {**{name: "x" for name in polluted}, "KEEP_ME": "1"}):
            env = cli_runner.child_environment({"CTID": "user"})
        remaining = {key.upper() for key in env}
        for name in polluted:
            self.assertNotIn(name.upper(), remaining, name)
        self.assertEqual(env["KEEP_ME"], "1")
        self.assertEqual(env["CTID"], "user")


class ClassifyAuthFailureTests(unittest.TestCase):
    """_classify() phân loại lần chạy KHÔNG có report hợp lệ. Mã thoát 81/82/85 là mã chính thức của CLI
    khi không đăng nhập được (tài liệu Spotware)."""

    @staticmethod
    def _classify(stdout: str = "", stderr: str = "", returncode: int | None = 1, stop: str = ""):
        spec = RunSpec(
            "combo", "combo-v1", "engine", "US30.cash", "h1",
            date(2026, 1, 1), date(2026, 2, 1), 100000, "ticks", {},
        )
        with tempfile.TemporaryDirectory() as td:
            result = cli_runner.ProcResult(stdout, stderr, returncode, stop)
            return cli_runner._classify(result, Path(td) / "report.json", spec)

    def test_invalid_ctid_or_password_is_an_auth_failure_not_a_missing_report(self) -> None:
        status, reason, code = self._classify("Error: Invalid ctid or password", returncode=81)
        self.assertEqual((status, code), (RunStatus.CLI_ERROR, FailureCode.AUTH_FAILED))
        self.assertIn("exit=81", reason)

    def test_exit_codes_82_and_85_are_recognised_even_without_a_known_message(self) -> None:
        for exit_code in (82, 85):
            status, reason, code = self._classify("", "", exit_code)
            self.assertEqual((status, code), (RunStatus.CLI_ERROR, FailureCode.AUTH_FAILED), exit_code)
            self.assertIn(f"exit={exit_code}", reason)

    def test_message_alone_still_counts_and_other_failures_stay_no_report(self) -> None:
        self.assertEqual(self._classify("Invalid ctid or password", returncode=1)[2], FailureCode.AUTH_FAILED)
        self.assertEqual(self._classify("Account cannot be found. Use accounts command", returncode=1)[2],
                         FailureCode.AUTH_FAILED)
        self.assertEqual(self._classify("something unexpected", returncode=1)[2], FailureCode.NO_REPORT)
        # Tiến trình bị watchdog giết (kill => mã 1) không được nhầm thành lỗi đăng nhập.
        self.assertEqual(self._classify("", "", 1, stop="timeout")[2], FailureCode.HARD_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
