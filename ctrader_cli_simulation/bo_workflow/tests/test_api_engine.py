from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

# tests/ nằm ngang hàng core_engine/cli_engine/api_engine dưới bo_workflow/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api_engine import api_runner
from api_engine.bridge_client import BridgeClient
from api_engine.contracts import BacktestApiJob, BacktestApiResult
from core_engine.configuration import load_strategy_profile
from core_engine.models import RunStatus


class BridgeClientTests(unittest.TestCase):
    """Dời từ ctrader_api/tests/test_bridge_client.py 2026-09-17 (dời cùng lúc
    với contracts.py/bridge_client.py vào api_engine/) — nội dung không đổi,
    chỉ đổi import path."""

    def test_submit_writes_camel_case_job_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client = BridgeClient(td)
            job = BacktestApiJob(
                job_id="job-1",
                robot_name="Combo",
                algo_path="C:/Robots/Combo.algo",
                symbol="FRA40.cash",
                timeframe="h1",
                start_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
                end_utc=datetime(2026, 2, 1, tzinfo=timezone.utc),
                balance=100000,
                data_mode="ticks",
                precise_conversion=True,
                commission_auto=True,
                parameters={"KslLevel": "1", "SignalFilePath": "Z:/signals.csv"},
            )

            path = client.submit(job)
            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(payload["schema"], "bo-backtest-api-job/v2")
            self.assertEqual(payload["jobId"], "job-1")
            self.assertEqual(payload["robotName"], "Combo")
            self.assertEqual(payload["startUtc"], "2026-01-01T00:00:00Z")
            self.assertTrue(payload["preciseConversion"])
            self.assertTrue(payload["commissionAuto"])
            self.assertFalse(list(Path(td).glob("jobs/*.tmp")))

    def test_wait_result_reads_results_folder(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client = BridgeClient(td)
            client.ensure_dirs()
            result = Path(td) / "results" / "job-1.json"
            result.write_text(json.dumps({
                "schema": "bo-backtest-api-result/v1",
                "jobId": "job-1",
                "status": "ok",
                "jsonReport": "{\"main\":{}}",
                "wallSeconds": 1.25,
                "preciseConversionRequested": True,
                "preciseConversionApplied": True,
                "commissionAutoRequested": True,
                "commissionAutoApplied": True,
                "capabilities": {"preciseConversion": True},
            }), encoding="utf-8")

            parsed = client.wait_result("job-1", timeout_seconds=0.1, poll_seconds=0.01)

            self.assertEqual(parsed.job_id, "job-1")
            self.assertEqual(parsed.status, "ok")
            self.assertEqual(parsed.wall_seconds, 1.25)
            self.assertTrue(parsed.precise_conversion_requested)
            self.assertTrue(parsed.precise_conversion_applied)
            self.assertTrue(parsed.commission_auto_requested)
            self.assertTrue(parsed.commission_auto_applied)
            self.assertTrue(parsed.capabilities["preciseConversion"])

    def test_wait_result_preserves_unsupported_capability(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            client = BridgeClient(td)
            client.ensure_dirs()
            failed = Path(td) / "failed" / "job-precise.json"
            failed.write_text(json.dumps({
                "schema": "bo-backtest-api-result/v1",
                "jobId": "job-precise",
                "status": "unsupported_capability",
                "preciseConversionRequested": True,
                "preciseConversionApplied": False,
            }), encoding="utf-8")

            parsed = client.wait_result(
                "job-precise", timeout_seconds=0.1, poll_seconds=0.01
            )

            self.assertEqual(parsed.status, "unsupported_capability")
            self.assertTrue(parsed.precise_conversion_requested)
            self.assertFalse(parsed.precise_conversion_applied)


class ApiRunnerTests(unittest.TestCase):
    """api_runner.py — engine API, đối xứng với cli_runner.py. Chỉ test logic
    thuần (validate/classify), không cần Plugin/cTrader Desktop thật đang chạy."""

    def test_validate_capabilities_requires_live_heartbeat(self) -> None:
        profile = load_strategy_profile("combo-v1")
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(RuntimeError, "chưa từng chạy"):
                api_runner.validate_capabilities([], profile=profile, bridge_root=Path(td))

    def test_validate_capabilities_rejects_unknown_param_name(self) -> None:
        profile = load_strategy_profile("combo-v1")
        with tempfile.TemporaryDirectory() as td:
            bridge_root = Path(td)
            (bridge_root / "heartbeat").mkdir(parents=True)
            (bridge_root / "heartbeat" / "BoBacktestRunner.json").write_text("{}", encoding="utf-8")
            spec = _fake_spec({"NotARealParam": "1"})
            with self.assertRaisesRegex(RuntimeError, "tham số lạ"):
                api_runner.validate_capabilities([spec], profile=profile, bridge_root=bridge_root)

    def test_validate_capabilities_rejects_enum_index_out_of_range(self) -> None:
        profile = load_strategy_profile("combo-v1")
        with tempfile.TemporaryDirectory() as td:
            bridge_root = Path(td)
            (bridge_root / "heartbeat").mkdir(parents=True)
            (bridge_root / "heartbeat" / "BoBacktestRunner.json").write_text("{}", encoding="utf-8")
            spec = _fake_spec({"KslLevel": "99"})
            with self.assertRaisesRegex(RuntimeError, "ngoài phạm vi"):
                api_runner.validate_capabilities([spec], profile=profile, bridge_root=bridge_root)

    def test_classify_maps_plugin_status_to_run_status(self) -> None:
        ok = BacktestApiResult(job_id="j", status="ok", json_report="{}")
        status, code, _ = api_runner._classify(ok)
        self.assertEqual(status, RunStatus.OK)

        mismatch = BacktestApiResult(job_id="j", status="report_mismatch", json_report=None,
                                      error="parameter KslLevel mismatch")
        status, code, reason = api_runner._classify(mismatch)
        self.assertEqual(status, RunStatus.PERIOD_MISMATCH)
        self.assertIn("mismatch", reason)

    def test_api_parameters_encode_enum_names(self) -> None:
        profile = load_strategy_profile("combo-v1")
        converted = api_runner._api_parameters(
            {"KslLevel": "2", "KtpLevel": "4", "RiskPercent": "1"},
            profile,
        )
        self.assertEqual(converted["KslLevel"], "Fib1000")
        self.assertEqual(converted["KtpLevel"], "Fib2618")
        self.assertEqual(converted["RiskPercent"], "1")


def _fake_spec(params: dict[str, str]):
    from core_engine.models import RunSpec
    from datetime import date
    return RunSpec("combo", "combo-v1", "engine", "US30.cash", "h1",
                    date(2026, 1, 1), date(2026, 1, 2), 100000, "ticks", params)


if __name__ == "__main__":
    unittest.main()
