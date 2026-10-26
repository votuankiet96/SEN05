"""Chạy Grid / chặng HỌC walk-forward bằng `ctrader-cli optimize` (config `optimize_chunk_size`): planner chia hộp,
file `--params` theo định dạng help.ctrader.com/ctrader-cli/optimisation, đọc thư mục pass thành RunResult, và facilitator.
Không chạm ctrader-cli/Redis thật: tiến trình CLI và hạ tầng ngoài đều là bản giả."""
from __future__ import annotations

import dataclasses
import itertools
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# tests/ nằm ngang hàng core_engine/ dưới bo_workflow/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core_engine import cli_runner, facilitator, store
from core_engine.configuration import MasterDbProfile, load_engine_profile, load_strategy_profile
from core_engine.models import FailureCode, OptimizeBox, RunResult, RunSpec, RunStatus
from core_engine.planner import build_experiment, expand_grid, plan_optimize_boxes

# Test không phụ thuộc cờ `master_db.enabled` trong config.yaml của máy (bật thì facilitator sẽ gọi SQL Server thật).
_master_db_off = patch.object(
    facilitator, "load_master_db_profile", return_value=MasterDbProfile(enabled=False, server="x", database="y"),
)


def setUpModule() -> None:
    _master_db_off.start()


def tearDownModule() -> None:
    _master_db_off.stop()


ENGINE = "ctrader-ticks-precisefx-v1"
IN_WINDOW_CSV = "bartime,atr,entry,signal\n2026-01-05 10:00,50,100,1\n"
LEVELS = [str(i) for i in range(10)]
STRATEGY = load_strategy_profile("backtestbot-v1")


def _config(parameter_space: dict[str, Any], name: str = "exp", **extra: Any) -> dict[str, Any]:
    return {
        "experiment": name, "strategy": "backtestbot", "strategy_profile": "backtestbot-v1", "engine_profile": ENGINE,
        "symbols": ["US30.cash"], "timeframe": "h1", "start": "2026-01-01", "end": "2026-02-01", "balance": 100000,
        "parameter_space": parameter_space, "max_parallel": 1, **extra,
    }


def _specs(parameter_space: dict[str, Any], **extra: Any) -> list[RunSpec]:
    return expand_grid(_config(parameter_space, **extra), STRATEGY, load_engine_profile(ENGINE))


def _with(spec: RunSpec, **params: str) -> RunSpec:
    return dataclasses.replace(spec, params={**spec.params, **params})


def _epoch_ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def _box(parameter_space: dict[str, Any], chunk: int = 100) -> OptimizeBox:
    (box,) = plan_optimize_boxes(_specs(parameter_space), STRATEGY, chunk)
    return box


class PlanOptimizeBoxesTests(unittest.TestCase):
    def plan(self, specs: list[RunSpec], chunk: int = 20) -> list[OptimizeBox]:
        return plan_optimize_boxes(specs, STRATEGY, chunk)

    def assert_covers(self, specs: list[RunSpec], boxes: list[OptimizeBox], chunk: int) -> None:
        covered = sorted(s.canonical_json() for box in boxes for s in box.specs)
        self.assertEqual(covered, sorted(s.canonical_json() for s in specs))
        for box in boxes:
            self.assertLessEqual(len(box.specs), chunk)
            names = [name for name, _ in box.dims]
            self.assertEqual([tuple(s.params[n] for n in names) for s in box.specs],
                             list(itertools.product(*(values for _, values in box.dims))))

    def test_box_size_must_match_dims(self) -> None:
        spec = _specs({"KslLevel": ["0", "1"]})[0]
        with self.assertRaises(ValueError):
            OptimizeBox((spec,), (("KslLevel", ("0", "1")),))
        with self.assertRaises(ValueError):
            OptimizeBox(())

    def test_full_grid_that_fits_is_one_box(self) -> None:
        specs = _specs({"KslLevel": ["0", "1", "2"], "KtpLevel": ["3", "4", "5"]})
        boxes = self.plan(specs)
        self.assertEqual([box.dims for box in boxes], [(("KslLevel", ("0", "1", "2")), ("KtpLevel", ("3", "4", "5")))])
        self.assert_covers(specs, boxes, 20)

    def test_ten_by_ten_with_chunk_twenty_is_five_boxes(self) -> None:
        specs = _specs({"KslLevel": LEVELS, "KtpLevel": LEVELS})
        boxes = self.plan(specs)
        self.assertEqual([len(box.specs) for box in boxes], [20] * 5)
        self.assertTrue(all(box.dims[1] == ("KtpLevel", tuple(LEVELS)) for box in boxes))
        self.assert_covers(specs, boxes, 20)

    def test_chunk_smaller_than_a_row_cuts_the_last_dimension(self) -> None:
        specs = _specs({"KslLevel": LEVELS, "KtpLevel": LEVELS})
        self.assert_covers(specs, self.plan(specs, chunk=4), 4)

    def test_chunk_one_is_all_singles(self) -> None:
        boxes = self.plan(_specs({"KslLevel": ["0", "1", "2"]}), chunk=1)
        self.assertEqual([(len(box.specs), box.dims) for box in boxes], [(1, ())] * 3)

    def test_arithmetic_float_dimension_stays_in_the_box(self) -> None:
        specs = _specs({"KslLevel": ["0", "1"], "RiskPercent": [0.5, 1.0, 1.5, 2.0]})
        boxes = self.plan(specs)
        self.assertEqual([box.dims[1] for box in boxes], [("RiskPercent", ("0.5", "1", "1.5", "2"))])
        self.assert_covers(specs, boxes, 20)

    def test_irregular_float_dimension_is_one_box_per_value(self) -> None:
        """Min/Max/Step không diễn tả được dãy 0.5, 1, 3."""
        specs = _specs({"KslLevel": ["0", "1"], "RiskPercent": [0.5, 1.0, 3.0]})
        boxes = self.plan(specs)
        self.assertEqual([len(box.specs) for box in boxes], [2, 2, 2])
        self.assertTrue(all(box.dims == (("KslLevel", ("0", "1")),) for box in boxes))
        self.assert_covers(specs, boxes, 20)

    def test_integer_dimension_needs_an_integer_step(self) -> None:
        base = _specs({"KslLevel": ["0"]})[0]
        regular = [_with(base, MaxConsecutiveLosses=str(n)) for n in (3, 5, 7)]
        irregular = [_with(base, MaxConsecutiveLosses=str(n)) for n in (3, 4, 9)]
        self.assertEqual([len(box.specs) for box in self.plan(regular)], [3])
        self.assertEqual([len(box.specs) for box in self.plan(irregular)], [1, 1, 1])

    def test_different_contexts_never_share_a_box(self) -> None:
        specs = _specs({"KslLevel": ["0", "1", "2"]}) + _specs({"KslLevel": ["0", "1", "2"]}, end="2026-03-01")
        boxes = self.plan(specs)
        self.assertEqual(sorted(len(box.specs) for box in boxes), [3, 3])
        self.assertTrue(all(len({s.end for s in box.specs}) == 1 for box in boxes))

    def test_input_that_is_not_a_product_falls_back_to_singles(self) -> None:
        specs = _specs({"KslLevel": ["0", "1", "2"], "KtpLevel": ["0", "1", "2"]})[:-1]      # thiếu 1 ô
        boxes = self.plan(specs)
        self.assertEqual([(len(box.specs), box.dims) for box in boxes], [(1, ())] * 8)

    def test_duplicate_params_are_not_merged(self) -> None:
        spec = _specs({"KslLevel": ["0", "1"]})[0]
        boxes = self.plan([spec, dataclasses.replace(spec, tags={"copy": "2"})])
        self.assertEqual([len(box.specs) for box in boxes], [1, 1])

    def test_invalid_chunk_size_is_rejected(self) -> None:
        for bad in (0, -3, 2.5, True):
            with self.assertRaises(ValueError):
                self.plan(_specs({"KslLevel": ["0", "1"]}), chunk=bad)


class OptimizeArgumentTests(unittest.TestCase):
    def entries(self, box: OptimizeBox) -> dict[str, dict[str, Any]]:
        document = cli_runner.optimize_parameters(box, STRATEGY, Path("C:/signals/us30.csv"))
        return {entry["Name"]: entry for entry in document["parameters"]}

    def test_every_parameter_is_listed_once_with_string_values(self) -> None:
        entries = self.entries(_box({"KslLevel": ["0", "1"], "KtpLevel": ["2", "3"]}))
        self.assertEqual(set(entries), {*STRATEGY.params, "SignalFilePath"})
        self.assertEqual(entries["KslLevel"], {"Name": "KslLevel", "Optimize": True, "Values": ["0", "1"]})
        self.assertEqual(entries["KtpLevel"], {"Name": "KtpLevel", "Optimize": True, "Values": ["2", "3"]})
        self.assertEqual(entries["RiskPercent"], {"Name": "RiskPercent", "Value": "0.2", "Optimize": False})
        self.assertEqual(entries["EnableDailyLossLimit"]["Value"], "False")
        self.assertEqual(entries["MaxConsecutiveLosses"]["Value"], "5")
        self.assertEqual(entries["SignalFilePath"], {"Name": "SignalFilePath", "Value": str(Path("C:/signals/us30.csv")),
                                                     "Optimize": False})

    def test_fixed_enum_is_its_index_string(self) -> None:
        entries = self.entries(_box({"KtpLevel": ["2", "3"]}))
        default = STRATEGY.params["KslLevel"].encode(STRATEGY.params["KslLevel"].default)
        self.assertEqual(entries["KslLevel"], {"Name": "KslLevel", "Value": default, "Optimize": False})

    def test_numeric_dimension_becomes_min_max_step(self) -> None:
        entries = self.entries(_box({"KslLevel": ["0", "1"], "RiskPercent": [0.5, 1.0, 1.5, 2.0]}))
        self.assertEqual(entries["RiskPercent"],
                         {"Name": "RiskPercent", "Optimize": True, "Min": "0.5", "Max": "2", "Step": "0.5"})
        base = _specs({"KslLevel": ["0"]})[0]
        (ints,) = plan_optimize_boxes([_with(base, MaxConsecutiveLosses=str(n)) for n in (3, 5, 7)], STRATEGY, 100)
        self.assertEqual(self.entries(ints)["MaxConsecutiveLosses"],
                         {"Name": "MaxConsecutiveLosses", "Optimize": True, "Min": "3", "Max": "7", "Step": "2"})

    def test_optimize_argv_has_timeframe_and_the_shared_engine_flags(self) -> None:
        spec = dataclasses.replace(_specs({"KslLevel": ["0", "1"]})[0], precise_conversion=True, commission_auto=True, spread=2.0)
        box = OptimizeBox((spec, _with(spec, KslLevel="1")), (("KslLevel", ("0", "1")),))
        argv = cli_runner.build_optimize_argv(
            box, cli_path=Path("cli.exe"), algo_path=Path("BacktestBot.algo"), params_path=Path("p.json"),
            optres=Path("r.optres"), passes_dir=Path("passes"), cores=7)
        self.assertEqual(argv[:3], ["cli.exe", "optimize", "BacktestBot.algo"])
        for expected in ("--params=p.json", "--start=01/01/2026 00:00", "--end=01/02/2026 00:00", "--data-mode=ticks",
                         "--balance=100000", "--symbol=US30.cash", "--timeframe=h1", "--method=grid", "--cores=7",
                         f"--criteria={cli_runner.OPTIMIZE_CRITERIA}", "--optres=r.optres", "--passes-dir=passes",
                         "-e", "--full-access", "--exit-on-stop", "--precise-conversion", "--commission-auto", "--spread=2"):
            self.assertIn(expected, argv)
        self.assertFalse([arg for arg in argv if arg.startswith(("--period", "--report-json"))])

    def test_backtest_argv_keeps_the_shared_engine_flags(self) -> None:
        spec = dataclasses.replace(_specs({"KslLevel": ["0"]})[0], precise_conversion=True,
                                   commission=30.0, commission_type="UsdPerMillionUsdVolume", spread=1.5)
        argv = cli_runner.build_argv(spec, cli_path=Path("cli.exe"), algo_path=Path("BacktestBot.algo"),
                                     cbotset=Path("p.cbotset"), report_json=Path("r.json"))
        self.assertEqual(argv[-5:], ["--precise-conversion", "--commission=30", "--commission-type=UsdPerMillionUsdVolume",
                                     "--spread=1.5", "--report-json=r.json"])
        self.assertIn("--period=h1", argv)

    def test_capability_check_covers_the_optimize_command_only_when_asked(self) -> None:
        spec = _specs({"KslLevel": ["0"]})[0]
        queried: list[str] = []

        def validate(*, optimize: bool, no_command: str | None = None, missing: str | None = None) -> None:
            def has_command(name: str, **_: Any) -> bool:
                queried.append(name)
                return name != no_command

            with patch.object(cli_runner, "cli_has_command", side_effect=has_command), \
                    patch.object(cli_runner, "advertised_data_modes", return_value=frozenset({"ticks"})), \
                    patch.object(cli_runner, "cli_option_supported", return_value=True), \
                    patch.object(cli_runner, "cli_has_option", side_effect=lambda name, **_: name != missing):
                cli_runner.validate_capabilities([spec], algo_path=Path("x.algo"), cli_path=Path("cli"), optimize=optimize)

        validate(optimize=False)
        self.assertEqual(queried, ["backtest"])
        queried.clear()
        validate(optimize=True)
        self.assertEqual(queried, ["backtest", "optimize"])
        with self.assertRaisesRegex(RuntimeError, "optimize"):
            validate(optimize=True, no_command="optimize")
        for option in cli_runner._OPTIMIZE_OPTIONS:
            with self.subTest(option=option), self.assertRaisesRegex(RuntimeError, option):
                validate(optimize=True, missing=option)

    def test_option_names_of_optimize_are_removed_from_the_child_environment(self) -> None:
        names = ("PARAMS", "METHOD", "CORES", "CRITERIA", "FITNESS", "AUTO-SELECT-BEST", "SKIP_EMPTY_PASSES", "OPTRES", "PASSES_DIR")
        with patch.dict(cli_runner.os.environ, {**{name: "x" for name in names}, "KEEP_ME": "1"}):
            env = cli_runner.child_environment({"CTID": "user"})
        self.assertFalse({key.upper() for key in env} & set(names))
        self.assertEqual(env["KEEP_ME"], "1")

    def test_redaction_and_period_check(self) -> None:
        data = {"main": {"authorNickName": "Joe", "keep": 1}, "items": [{"accountNumber": 7, "ok": "yes"}]}
        cleaned = cli_runner.redact_report_data(data)
        self.assertEqual(cleaned, {"main": {"authorNickName": "<redacted>", "keep": 1}, "items": [{"accountNumber": "<redacted>", "ok": "yes"}]})
        self.assertEqual(data["main"]["authorNickName"], "Joe")
        spec = _specs({"KslLevel": ["0"]})[0]
        period = lambda end: {"main": {"testingPeriod": {"startDate": _epoch_ms(spec.start), "endDate": _epoch_ms(end)}}}  # noqa: E731
        self.assertEqual(cli_runner._check_period(period(spec.end), spec), (RunStatus.OK, "", FailureCode.NONE))
        self.assertEqual(cli_runner._check_period(period(date(2026, 2, 2)), spec)[0], RunStatus.PERIOD_MISMATCH)
        self.assertEqual(cli_runner._check_period({}, spec)[0], RunStatus.NO_REPORT)


class FakeOptimizeCli:
    """Thay `cli_runner.run_process`: đọc --params/--passes-dir/--optres rồi ghi thư mục pass như CLI thật
    (id ngược thứ tự tích Descartes để mã nguồn phải ghép pass theo THAM SỐ, không theo thứ tự)."""

    def __init__(self, spec: RunSpec, *, enum_as_name: bool = False, float_noise: bool = False, omit: tuple[int, ...] = (),
                 statuses: dict[int, str] | None = None, end_shift_days: dict[int, int] | None = None,
                 write_optres: bool = True, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.spec, self.enum_as_name, self.float_noise, self.omit = spec, enum_as_name, float_noise, omit
        self.statuses, self.end_shift_days = statuses or {}, end_shift_days or {}
        self.write_optres, self.returncode, self.stdout, self.stderr = write_optres, returncode, stdout, stderr
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def _arg(argv: list[str], name: str) -> str:
        return next(arg.split("=", 1)[1] for arg in argv if arg.startswith(f"--{name}="))

    @staticmethod
    def _values(entry: dict[str, Any]) -> list[str]:
        if "Values" in entry:
            return list(entry["Values"])
        if "Min" in entry:
            low, high, step = float(entry["Min"]), float(entry["Max"]), float(entry["Step"])
            return [format(round(low + i * step, 9), "g") for i in range(int(round((high - low) / step)) + 1)]
        return [entry["Value"]]

    def _cbotset_value(self, name: str, value: str) -> str:
        pdef = STRATEGY.params.get(name)
        if pdef and pdef.kind == "enum" and self.enum_as_name:
            return pdef.enum_values[int(value)]
        if pdef and pdef.kind == "float" and self.float_noise:
            return str(float(value) + 1e-13)
        return value

    def __call__(self, argv: list[str], *, timeout_s: int, cwd: Path, env: Any, poll: Any = None,
                 interval: float = 0.5) -> cli_runner.ProcResult:
        self.calls.append({"argv": argv, "timeout_s": timeout_s})
        entries = json.loads(Path(self._arg(argv, "params")).read_text(encoding="utf-8"))["parameters"]
        combos = list(itertools.product(*(self._values(entry) for entry in entries)))
        passes_dir, optres = Path(self._arg(argv, "passes-dir")), Path(self._arg(argv, "optres"))
        records = []
        for index, combo in reversed(list(enumerate(combos))):
            if index in self.omit:
                continue
            pass_id = len(combos) - 1 - index
            pass_dir = passes_dir / str(pass_id)
            pass_dir.mkdir(parents=True)
            parameters = {e["Name"]: self._cbotset_value(e["Name"], v) for e, v in zip(entries, combo)}
            (pass_dir / "parameters.cbotset").write_text(
                json.dumps({"Chart": {"Symbol": self.spec.symbol, "Period": self.spec.timeframe}, "Parameters": parameters}),
                encoding="utf-8")
            end = self.spec.end + timedelta(days=self.end_shift_days.get(index, 0))
            report = {"main": {"testingPeriod": {"startDate": _epoch_ms(self.spec.start), "endDate": _epoch_ms(end)},
                               "authorNickName": "Secret Nick", "netProfit": 12.5}, "history": {"items": []}}
            (pass_dir / "report.html").write_text(
                '<html><script id="backtesting-report" type="application/json">' + json.dumps(report) + "</script></html>",
                encoding="utf-8")
            (pass_dir / "log.txt").write_text(f"log of pass {pass_id}\n", encoding="utf-8")
            records.append({"passId": pass_id, "status": self.statuses.get(index, "Completed")})
        if self.write_optres:
            optres.write_text(json.dumps({"results": {"passes": records}}), encoding="utf-8")
        return cli_runner.ProcResult(self.stdout, self.stderr, self.returncode, "")


class RunOptimizeBoxTests(unittest.TestCase):
    def run_box(self, fake: FakeOptimizeCli, box: OptimizeBox, td: str, *, cores: int = 4, timeout_s: int = 100,
                should_cancel: Any = None) -> list[RunResult]:
        with patch.object(cli_runner, "run_process", fake), patch.object(cli_runner, "_auth_env_available", return_value=False):
            return cli_runner.run_optimize_box(
                box, STRATEGY, algo_path=Path("BacktestBot.algo"), signal_file=Path("C:/signals/us30.csv"),
                work_dir=Path(td) / "work", cores=cores, timeout_s=timeout_s, cli_path=Path("fake-cli.exe"),
                env={}, should_cancel=should_cancel)

    def test_passes_are_matched_to_specs_by_parameters_not_by_order(self) -> None:
        box = _box({"KslLevel": ["0", "1", "2"], "KtpLevel": ["3", "4"]})
        for as_name in (False, True):
            with self.subTest(enum_as_name=as_name), tempfile.TemporaryDirectory() as td:
                results = self.run_box(FakeOptimizeCli(box.specs[0], enum_as_name=as_name), box, td)
                self.assertEqual({(r.status, r.failure_code) for r in results}, {(RunStatus.OK, FailureCode.NONE)})
                for spec, result in zip(box.specs, results):
                    saved = json.loads(result.cbotset.read_text(encoding="utf-8"))["Parameters"]
                    for name in ("KslLevel", "KtpLevel"):
                        self.assertEqual(STRATEGY.params[name].encode(saved[name]), spec.params[name])
                    self.assertTrue(result.bot_log.read_text(encoding="utf-8").startswith("log of pass"))

    def test_each_spec_gets_a_redacted_report_and_its_own_files(self) -> None:
        box = _box({"KslLevel": ["0", "1", "2"]})
        with tempfile.TemporaryDirectory() as td:
            results = self.run_box(FakeOptimizeCli(box.specs[0]), box, td)
            self.assertEqual(len({r.report_json for r in results}), 3)
            for result in results:
                text = result.report_json.read_text(encoding="utf-8")
                self.assertNotIn("Secret Nick", text)
                self.assertEqual(json.loads(text)["main"]["authorNickName"], "<redacted>")
                self.assertEqual((result.cli_path, result.exit_code, result.timed_out), ("fake-cli.exe", 0, False))

    def test_float_values_match_despite_representation_noise(self) -> None:
        box = _box({"KslLevel": ["0"], "RiskPercent": [0.3, 0.6]})
        with tempfile.TemporaryDirectory() as td:
            results = self.run_box(FakeOptimizeCli(box.specs[0], float_noise=True), box, td)
        self.assertEqual([r.status for r in results], [RunStatus.OK, RunStatus.OK])

    def test_a_pass_problem_fails_only_that_spec(self) -> None:
        box = _box({"KslLevel": ["0", "1", "2", "3"]})
        fake = FakeOptimizeCli(box.specs[0], omit=(0,), statuses={1: "Failed"}, end_shift_days={2: 1})
        with tempfile.TemporaryDirectory() as td:
            results = self.run_box(fake, box, td)
            self.assertEqual([r.status for r in results],
                             [RunStatus.NO_REPORT, RunStatus.CLI_ERROR, RunStatus.PERIOD_MISMATCH, RunStatus.OK])
            self.assertIn("no matching pass", results[0].reason)
            self.assertIn("Failed", results[1].reason)
            self.assertTrue(results[0].cli_log.read_text(encoding="utf-8"))        # lỗi -> có log cấp hộp

    def test_box_level_failure_is_applied_to_every_spec(self) -> None:
        box = _box({"KslLevel": ["0", "1"]})
        for stdout, stderr, code, expected in (
            ("", "Invalid ctid or password", 81, FailureCode.AUTH_FAILED),
            ("Message expected\n", "", 1, FailureCode.MESSAGE_EXPECTED),
        ):
            fake = FakeOptimizeCli(box.specs[0], write_optres=False, returncode=code, stdout=stdout, stderr=stderr)
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as td:
                results = self.run_box(fake, box, td)
                self.assertEqual({(r.status, r.failure_code, r.exit_code) for r in results}, {(RunStatus.CLI_ERROR, expected, code)})
                self.assertFalse(results[0].report_json.exists())

    def test_cancel_before_start_spawns_nothing(self) -> None:
        box = _box({"KslLevel": ["0", "1"]})
        fake = FakeOptimizeCli(box.specs[0])
        with tempfile.TemporaryDirectory() as td:
            results = self.run_box(fake, box, td, should_cancel=lambda: True)
        self.assertEqual(fake.calls, [])
        self.assertEqual({(r.status, r.failure_code) for r in results}, {(RunStatus.CANCELLED, FailureCode.CANCELLED)})

    def test_cores_are_clamped_and_timeout_scales_with_the_waves(self) -> None:
        for box, cores, expected_cores, expected_timeout in (
            (_box({"KslLevel": LEVELS, "KtpLevel": ["0", "1"]}), 12, 12, 200),      # 20 pass / 12 lõi = 2 đợt
            (_box({"KslLevel": ["0", "1", "2"]}), 12, 3, 100),
        ):
            fake = FakeOptimizeCli(box.specs[0])
            with self.subTest(cores=expected_cores), tempfile.TemporaryDirectory() as td:
                self.run_box(fake, box, td, cores=cores)
            self.assertIn(f"--cores={expected_cores}", fake.calls[0]["argv"])
            self.assertEqual(fake.calls[0]["timeout_s"], expected_timeout)

    def test_stale_passes_and_the_params_file(self) -> None:
        box = _box({"KslLevel": ["0", "1"], "KtpLevel": ["2", "3"]})
        with tempfile.TemporaryDirectory() as td:
            stale = Path(td) / "work" / "passes" / "99"
            stale.mkdir(parents=True)
            self.run_box(FakeOptimizeCli(box.specs[0]), box, td)
            self.assertFalse(stale.exists())
            names = [e["Name"] for e in json.loads((Path(td) / "work" / "params.json").read_text(encoding="utf-8"))["parameters"]]
        self.assertEqual(names, [*STRATEGY.params, "SignalFilePath"])

    def test_poll_waits_for_a_valid_optres_then_the_grace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            optres = Path(td) / "result.optres"
            clock = {"t": 0.0}
            seen: list[tuple[float, str]] = []
            with patch.object(cli_runner.time, "monotonic", lambda: clock["t"]):
                poll = cli_runner._make_poll(
                    optres, stall_s=1000, startup_stall_s=1000, on_progress=lambda p, d: seen.append((p, d)),
                    phase="Optimization", ready=lambda path: cli_runner._read_optres(path) is not None)
                self.assertEqual(poll(["Progress | Optimization | 40.0 %"], []), "")
                self.assertEqual(seen, [(40.0, "Optimization")])
                optres.write_text('{"results": {"passes"', encoding="utf-8")                   # ghi dở: chưa hợp lệ
                clock["t"] = 20.0
                self.assertEqual(poll([], []), "")
                optres.write_text(json.dumps({"results": {"passes": []}}), encoding="utf-8")
                self.assertEqual(poll([], []), "")                                              # vừa thấy: bắt đầu đếm grace
                clock["t"] = 20.0 + cli_runner.REPORT_GRACE_SECONDS + 0.5
                self.assertEqual(poll([], []), "report_ready")


def _fake_result(work_dir: Path, status: RunStatus = RunStatus.OK, code: FailureCode | None = None) -> RunResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "bot.log").write_text("", encoding="utf-8")
    (work_dir / "cli.log").write_text("", encoding="utf-8")
    if code is None:
        code = FailureCode.NONE if status is RunStatus.OK else FailureCode.NO_REPORT
    return RunResult(
        status=status, failure_code=code, reason="", exit_code=0, timed_out=False, wall_seconds=0.1,
        started_utc="2026-01-01T00:00:00+00:00", ended_utc="2026-01-01T00:00:01+00:00", cli_path="fake", cli_version="fake",
        report_json=work_dir / "report.json", cli_log=work_dir / "cli.log", bot_log=work_dir / "bot.log",
        cbotset=work_dir / "params.cbotset")


def _metadata() -> dict:
    kinds = {"enum": "Enum", "float": "Double", "int": "Integer", "bool": "Boolean"}
    rows = [{"PropertyName": "SignalFilePath", "Type": "String", "DefaultValue": ""}]
    for pdef in STRATEGY.params.values():
        row = {"PropertyName": pdef.name, "Type": kinds[pdef.kind], "DefaultValue": pdef.encode(pdef.default)}
        if pdef.kind == "enum":
            row["EnumValues"] = {name: i for i, name in enumerate(pdef.enum_values)}
        rows.append(row)
    return {"Name": STRATEGY.cbot_name, "Type": "Robot", "AccessRights": "FullAccess", "Parameters": rows}


class FacilitatorBoxTests(unittest.TestCase):
    """run_experiment THẬT; chỉ thay phần chạm hạ tầng ngoài (CLI, Redis, protocol DB)."""
    GRID_3X3 = {"KslLevel": ["0", "1", "2"], "KtpLevel": ["0", "1", "2"]}

    def setUp(self) -> None:
        self.box_calls: list[tuple[int, int]] = []        # (số spec, cores) mỗi lần gọi run_optimize_box
        self.backtest_calls: list[RunSpec] = []
        self.validate = MagicMock()

    def fake_box(self, verdict=None):
        """verdict(call_index, spec_index, spec) -> RunStatus | (RunStatus, FailureCode)."""
        def run_optimize_box(box, strategy, **kwargs):
            call = len(self.box_calls)
            self.box_calls.append((len(box.specs), kwargs["cores"]))
            results = []
            for i, spec in enumerate(box.specs):
                answer = verdict(call, i, spec) if verdict else RunStatus.OK
                status, code = answer if isinstance(answer, tuple) else (answer, None)
                results.append(_fake_result(kwargs["work_dir"] / f"s{i}", status, code))
            return results
        return run_optimize_box

    def run_backtest(self, spec, **kwargs):
        self.backtest_calls.append(spec)
        return _fake_result(kwargs["work_dir"])

    def run_exp(self, td: str, parameter_space: dict[str, Any], *, run_optimize_box=None, should_cancel=None,
                protocol: Any = None, name: str = "exp", **extra: Any):
        config = _config(parameter_space, name=name, **extra)
        engine = load_engine_profile(ENGINE)
        specs = expand_grid(config, STRATEGY, engine)
        cli = Path(td) / "ctrader-cli.exe"
        cli.write_bytes(b"fake cli")

        def resolve(_redis, _profile, _spec, dest_dir, *, materialized=None):
            path = Path(dest_dir) / "US30_H1_combo.csv"
            path.write_text(IN_WINDOW_CSV, encoding="utf-8")
            return path

        def unexpected(*_args, **_kwargs):
            raise AssertionError("run_optimize_box không được gọi")

        with patch.object(store, "RUNS_ROOT", Path(td) / "runs"), \
                patch.object(facilitator, "ProtocolStore", MagicMock(return_value=protocol or MagicMock())), \
                patch.object(facilitator, "load_redis_profile", MagicMock()), \
                patch.object(facilitator.signal_trans, "resolve_signal_path", side_effect=resolve), \
                patch.object(facilitator.cli_runner, "select_cli", return_value=cli), \
                patch.object(facilitator.cli_runner, "read_metadata", return_value=_metadata()), \
                patch.object(facilitator.cli_runner, "validate_capabilities", self.validate), \
                patch.object(facilitator.cli_runner, "auth_options", return_value={}), \
                patch.object(facilitator.cli_runner, "child_environment", return_value={}), \
                patch.object(facilitator.cli_runner, "cli_version", return_value="fake"), \
                patch.object(facilitator.cli_runner, "run_optimize_box", side_effect=run_optimize_box or unexpected), \
                patch.object(facilitator.cli_runner, "run_backtest", side_effect=self.run_backtest):
            result = facilitator.run_experiment(
                config, build_experiment(config).canonical(), STRATEGY, specs, engine=engine, should_cancel=should_cancel)
            rows = store.ExperimentStore("grid", name).rows()
        return result, rows

    def test_default_is_off_and_every_spec_runs_through_backtest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result, _ = self.run_exp(td, self.GRID_3X3)
        self.assertEqual((len(self.backtest_calls), result["tally"]), (9, {"ok": 9}))
        self.assertFalse(self.validate.call_args.kwargs["optimize"])

    def test_chunk_size_runs_the_grid_as_one_box(self) -> None:
        protocol = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            result, rows = self.run_exp(td, self.GRID_3X3, run_optimize_box=self.fake_box(), protocol=protocol,
                                        optimize_chunk_size=20, max_parallel=3)
        self.assertEqual((self.box_calls, self.backtest_calls), ([(9, 3)], []))
        self.assertEqual((result["tally"], result["complete"], result["cli_versions"]), ({"ok": 9}, True, ["fake"]))
        self.assertEqual({row["status"] for row in rows}, {"ok"})
        self.assertEqual(protocol.reserve_cli_launch.call_count, 1)                 # 1 lần đăng nhập cho cả hộp
        self.assertTrue(self.validate.call_args.kwargs["optimize"])

    def test_chunk_size_splits_the_grid_and_one_spec_stays_a_backtest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.run_exp(td, self.GRID_3X3, run_optimize_box=self.fake_box(), optimize_chunk_size=3)
            self.run_exp(td, {"KslLevel": ["0"]}, run_optimize_box=self.fake_box(), optimize_chunk_size=3, name="one")
        self.assertEqual([size for size, _ in self.box_calls], [3, 3, 3])
        self.assertEqual(len(self.backtest_calls), 1)

    def test_negative_chunk_size_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td, self.assertRaises(ValueError):
            self.run_exp(td, self.GRID_3X3, run_optimize_box=self.fake_box(), optimize_chunk_size=-1)

    def test_rerun_after_a_clean_stop_keeps_using_boxes_for_the_rest(self) -> None:
        stop_after_first = lambda: len(self.box_calls) >= 1  # noqa: E731
        with tempfile.TemporaryDirectory() as td:
            first, _ = self.run_exp(td, self.GRID_3X3, run_optimize_box=self.fake_box(), optimize_chunk_size=3,
                                    should_cancel=stop_after_first)
            self.box_calls.clear()
            second, rows = self.run_exp(td, self.GRID_3X3, run_optimize_box=self.fake_box(), optimize_chunk_size=3)
        self.assertEqual(first["tally"], {"ok": 3, facilitator.NOT_STARTED: 6})
        self.assertEqual((second["tally"], [s for s, _ in self.box_calls], self.backtest_calls), ({"skipped": 3, "ok": 6}, [3, 3], []))
        self.assertEqual(len(rows), 9)

    def test_failed_specs_make_a_hole_that_reruns_as_single_backtests(self) -> None:
        fail_two = lambda call, i, spec: RunStatus.CLI_ERROR if i in (4, 8) else RunStatus.OK  # noqa: E731
        with tempfile.TemporaryDirectory() as td:
            first, _ = self.run_exp(td, self.GRID_3X3, run_optimize_box=self.fake_box(fail_two), optimize_chunk_size=20)
            self.box_calls.clear()
            second, _ = self.run_exp(td, self.GRID_3X3, run_optimize_box=self.fake_box(), optimize_chunk_size=20)
        self.assertEqual(first["tally"], {"ok": 7, "cli_error": 2})
        self.assertEqual((second["tally"], self.box_calls, len(self.backtest_calls)), ({"skipped": 7, "ok": 2}, [], 2))

    def test_finished_specs_do_not_spend_launch_budget(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.run_exp(td, self.GRID_3X3, run_optimize_box=self.fake_box(), optimize_chunk_size=20)
            protocol = MagicMock()
            result, _ = self.run_exp(td, self.GRID_3X3, protocol=protocol, optimize_chunk_size=20)
        self.assertEqual(result["tally"], {"skipped": 9})
        protocol.reserve_cli_launch.assert_not_called()

    def test_cancel_and_budget_stop_the_box_without_rows(self) -> None:
        protocol = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            cancelled, rows = self.run_exp(td, self.GRID_3X3, run_optimize_box=self.fake_box(), protocol=protocol,
                                           optimize_chunk_size=20, should_cancel=lambda: True)
        self.assertEqual((cancelled["tally"], rows, self.box_calls), ({facilitator.NOT_STARTED: 9}, [], []))
        protocol.reserve_cli_launch.assert_not_called()
        protocol.reserve_cli_launch.return_value = False
        with tempfile.TemporaryDirectory() as td:
            result, rows = self.run_exp(td, self.GRID_3X3, run_optimize_box=self.fake_box(), protocol=protocol, optimize_chunk_size=20)
        self.assertEqual((result["tally"], result["daily_cli_budget_exhausted"], rows, self.box_calls),
                         ({facilitator.BUDGET_EXHAUSTED: 9}, True, [], []))

    def test_transient_failure_retries_the_whole_box_but_others_do_not(self) -> None:
        flaky = lambda call, i, spec: (RunStatus.CLI_ERROR, FailureCode.MESSAGE_EXPECTED) if call == 0 else RunStatus.OK  # noqa: E731
        protocol = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            result, _ = self.run_exp(td, self.GRID_3X3, run_optimize_box=self.fake_box(flaky), protocol=protocol, optimize_chunk_size=20)
        self.assertEqual((len(self.box_calls), protocol.reserve_cli_launch.call_count, result["tally"]), (2, 2, {"ok": 9}))
        self.box_calls.clear()
        auth = lambda call, i, spec: (RunStatus.CLI_ERROR, FailureCode.AUTH_FAILED)  # noqa: E731
        with tempfile.TemporaryDirectory() as td:
            result, _ = self.run_exp(td, self.GRID_3X3, run_optimize_box=self.fake_box(auth), optimize_chunk_size=20)
        self.assertEqual((len(self.box_calls), result["tally"], result["failed"]), (1, {"cli_error": 9}, 9))

    def test_pipeline_errors_are_recorded_for_every_spec(self) -> None:
        def boom(box, strategy, **kwargs):
            raise RuntimeError("boom")

        def short(box, strategy, **kwargs):
            return [_fake_result(kwargs["work_dir"] / "s0")]

        for run_optimize_box in (boom, short):
            with self.subTest(run_optimize_box.__name__), tempfile.TemporaryDirectory() as td:
                result, rows = self.run_exp(td, self.GRID_3X3, run_optimize_box=run_optimize_box, optimize_chunk_size=20)
                self.assertEqual(result["tally"], {"cli_error": 9})
                self.assertEqual({row["status"] for row in rows}, {"cli_error"})          # không còn dòng 'running' treo

    def test_different_symbols_run_as_separate_boxes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result, rows = self.run_exp(td, self.GRID_3X3, run_optimize_box=self.fake_box(), optimize_chunk_size=20,
                                        symbols=["US30.cash", "JP225.cash"])
        self.assertEqual(([size for size, _ in self.box_calls], result["tally"]), ([9, 9], {"ok": 18}))
        self.assertEqual({row["symbol"] for row in rows}, {"US30.cash", "JP225.cash"})

    def test_chunk_size_never_changes_the_experiment_lock(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.run_exp(td, self.GRID_3X3, run_optimize_box=self.fake_box(), optimize_chunk_size=3)
            result, _ = self.run_exp(td, self.GRID_3X3, run_optimize_box=self.fake_box(), optimize_chunk_size=20)
        self.assertEqual(result["tally"], {"skipped": 9})
        self.assertIn("optimize_chunk_size", store.EXECUTION_ONLY_CONFIG_KEYS)


if __name__ == "__main__":
    unittest.main()
