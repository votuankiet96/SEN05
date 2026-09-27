from __future__ import annotations

import json
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# tests/ nằm ngang hàng core_engine/api_engine dưới bo_workflow/ -> parents[1]
# (1 cấp lên từ tests/) đúng là bo_workflow/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core_engine import facilitator
from core_engine.configuration import (
    SL_FIB_LEVELS, TP_FIB_LEVELS, load_engine_profile, load_ftmo_profile,
    load_redis_profile, load_strategy_profile,
)
from core_engine.models import EngineProfile, FailureCode, RunResult, RunSpec, RunStatus
from core_engine import cli_runner, signal_trans, store
from core_engine.optimize import dsr, montecarlo, pbo, walkforward
from core_engine.output_util import evidence, pipeline_lock, readout, selection
from core_engine.planner import build_experiment, expand_grid

FAKE_FINGERPRINT = {
    "algo_sha256": "a" * 64, "cli_sha256": "c" * 64, "signals": {"US30.cash|h1": "s" * 64},
}


def metadata_for(profile_id: str) -> dict:
    profile = load_strategy_profile(profile_id)
    type_map = {"enum": "Enum", "float": "Double", "int": "Integer", "bool": "Boolean"}
    rows = [{"PropertyName": "SignalFilePath", "Type": "String", "DefaultValue": ""}]
    for pdef in profile.params.values():
        row = {
            "PropertyName": pdef.name,
            "Type": type_map[pdef.kind],
            "DefaultValue": pdef.encode(pdef.default),
        }
        if pdef.kind == "enum":
            row["EnumValues"] = {name: i for i, name in enumerate(pdef.enum_values)}
        rows.append(row)
    return {"Name": profile.cbot_name, "Type": "Robot", "AccessRights": "FullAccess", "Parameters": rows}


class ConfigurationPlannerTests(unittest.TestCase):
    def test_load_engine_and_ftmo_profile_from_config_yaml(self) -> None:
        """load_ftmo_profile() không hề tồn tại trước 2026-09-12 (không ai load
        profiles/ftmo/*.json) — test này khoá lại hành vi mới, không phải hồi quy."""
        engine = load_engine_profile("ctrader-5.9.16-ticks-approxfx-v1")
        self.assertEqual(engine.data_mode, "ticks")
        self.assertFalse(engine.precise_conversion)
        self.assertEqual(engine.backend, "cli")
        ftmo = load_ftmo_profile("ftmo-2step-v1")
        self.assertEqual(ftmo.timezone, "Europe/Prague")
        self.assertEqual(ftmo.daily_loss_pct, 5.0)
        self.assertEqual(ftmo.initial_balance, 100000)

    def test_load_api_engine_profile_has_api_backend(self) -> None:
        """[2026-09-17] Engine thứ 2 (Plugin qua bridge) — khoá lại field mới
        `backend` đọc đúng từ config.yaml, không âm thầm mặc định về "cli"."""
        engine = load_engine_profile("ctrader-api-plugin-v1")
        self.assertEqual(engine.backend, "api")

    def test_engine_profile_rejects_unknown_backend(self) -> None:
        with self.assertRaisesRegex(ValueError, "backend"):
            EngineProfile("e", "5.9.16", backend="grpc")

    def test_metadata_fail_closed_on_extra_param(self) -> None:
        profile = load_strategy_profile("combo-v1")
        meta = metadata_for("combo-v1")
        profile.validate_metadata(meta)
        meta["Parameters"].append({"PropertyName": "NewParam", "Type": "Double", "DefaultValue": 1})
        with self.assertRaisesRegex(ValueError, "parameter drift"):
            profile.validate_metadata(meta)

    def test_engine_profile_rejects_bad_commission_combo(self) -> None:
        with self.assertRaisesRegex(ValueError, "commission_auto"):
            EngineProfile("e", "5.9.16", commission_auto=True, commission=1.0)
        with self.assertRaisesRegex(ValueError, "commission_type"):
            EngineProfile("e", "5.9.16", commission_type="NotARealType", commission=1.0)
        with self.assertRaisesRegex(ValueError, "cần commission"):
            EngineProfile("e", "5.9.16", commission_type="UsdPerOneLot")

    def test_grid_is_deterministic_and_uses_enum_indexes(self) -> None:
        profile = load_strategy_profile("combo-v1")
        engine = EngineProfile("engine", "test")
        config = {
            "experiment": "x",
            "strategy": "combo",
            "strategy_profile": "combo-v1",
            "engine_profile": "engine",
            "symbols": ["US30.cash"],
            "timeframe": "h1",
            "start": "2026-01-01",
            "end": "2026-01-31",
            "parameter_space": {"KslLevel": ["Fib0618", "Fib1000"], "KtpLevel": ["Fib2618"]},
        }
        specs = expand_grid(config, profile, engine)
        self.assertEqual(len(specs), 2)
        self.assertEqual(specs[0].params["KslLevel"], "0")
        self.assertEqual(specs[1].params["KslLevel"], str(SL_FIB_LEVELS.index("Fib1000")))
        self.assertEqual(specs[0].params["KtpLevel"], str(TP_FIB_LEVELS.index("Fib2618")))


class EvidenceSelectionTests(unittest.TestCase):
    def test_bot_summary_and_validity_signal_gate(self) -> None:
        text = (
            "Info | Combo: loaded=10, processed=8, before-start=1, not-processed=1, "
            "placed=3, failed=0, guard-skipped=0, pending-expired=1, "
            "same-direction-skipped=2, reversed=1.\n"
            "Info | FILLED something\n"
        )
        summary = evidence.bot_summary(text)
        self.assertEqual(summary["loaded"], 10)
        self.assertEqual(summary["filled"], 1)
        # end = 2026-02-01 la moc DUNG (exclusive) -> phai trung y het endDate
        # trong report (1769904000000 = 2026-02-01). Xem test_end_date_is_exclusive.
        spec = RunSpec(
            "combo", "combo-v1", "engine", "US30.cash", "h1",
            date(2026, 1, 1), date(2026, 2, 1), 100000, "ticks", {},
        )
        report = {"main": {"testingPeriod": {"startDate": 1767225600000, "endDate": 1769904000000}}}
        flags = evidence.validity_flags(spec, report, summary)
        self.assertTrue(flags["signal_ok"])
        self.assertTrue(flags["period_ok"])

    def test_margin_rejections_count_events_not_log_lines(self) -> None:
        """Mot lenh bi choi sinh 1 HOAC 2 dong log, tuy kieu lenh.

        Lenh cho (Combo dat Stop Order) -> engine ghi dung 1 dong. Lenh thi
        truong (MA Cross) -> engine ghi 1 dong VA cBot ghi them 1 dong nua,
        trung moc thoi gian toi mili-giay. Dem so DONG thi cung 1 su kien ra
        hai con so khac nhau (do that 2026-09-12: bao 14 khi chi co 7 lenh),
        nen phai gom theo moc thoi gian de dem SU KIEN.
        """
        market_order = (
            '10/04/2026 07:30:00.044 | Trade |  Executing Market Order to Sell '
            '31.67 US30.cash (SL: 30.63, TP: 80.18) FAILED with error '
            '"NOT_ENOUGH_MARGIN_BALANCE"' "\n"
            '10/04/2026 07:30:00.044 | Info | MA Cross: bartime=2026-04-10 '
            '07:00, market Sell was rejected: NoMoney.' "\n"
        )
        pending_order = (
            '23/01/2025 07:09:10.623 | Trade | Stop Order OID78 to Sell 28.84 '
            'US30.cash got rejected with error "NOT_ENOUGH_MARGIN_BALANCE"' "\n"
        )
        self.assertEqual(evidence.bot_summary(market_order)["margin_rejections"], 1)
        self.assertEqual(evidence.bot_summary(pending_order)["margin_rejections"], 1)
        self.assertEqual(
            evidence.bot_summary(market_order + pending_order)["margin_rejections"], 2
        )

    def test_end_date_is_exclusive_and_matches_gui(self) -> None:
        """`end` phai di thang vao --end, khong cong them ngay nao (nghia tu
        chung, ap dung cho cả 2 engine — bang chung dung cli_runner de dung
        build_argv co san, khong phai vi test nay chi noi ve CLI)."""
        from core_engine import cli_runner

        spec = RunSpec(
            "combo", "combo-v1", "engine", "US30.cash", "h1",
            date(2026, 1, 1), date(2026, 9, 1), 100000, "ticks", {},
        )
        argv = cli_runner.build_argv(spec, cli_path=Path("cli.exe"), algo_path=Path("a.algo"),
                                     cbotset=Path("p.cbotset"), report_json=Path("r.json"))
        self.assertIn("--start=01/01/2026 00:00", argv)
        self.assertIn("--end=01/09/2026 00:00", argv)

        # va period_ok chi True khi endDate cua report trung dung spec.end
        summary = {"loaded": 1, "processed": 1, "filled": 1}
        same = {"main": {"testingPeriod": {"startDate": 1767225600000,
                                           "endDate": 1788220800000}}}   # 2026-09-01
        self.assertTrue(evidence.validity_flags(spec, same, summary)["period_ok"])
        later = {"main": {"testingPeriod": {"startDate": 1767225600000,
                                            "endDate": 1788307200000}}}  # 2026-09-02
        self.assertFalse(evidence.validity_flags(spec, later, summary)["period_ok"])

    def test_selection_rejects_margin_rejections(self) -> None:
        rows = [
            {"status": "ok", "total_trades": 10, "profit_factor": 2, "net_profit": 1,
             "validity_flags": {"period_ok": True, "signal_ok": True, "margin_rejections": 0}},
            {"status": "ok", "total_trades": 10, "profit_factor": 3, "net_profit": 2,
             "validity_flags": {"period_ok": True, "signal_ok": True, "margin_rejections": 1}},
        ]
        ranked = selection.rank(rows)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["net_profit"], 1)


class SelectionClassifyTests(unittest.TestCase):
    """[2026-09-25] selection.classify() phân loại có cấu trúc, thay cho 1
    boolean gộp — khoá lại: (a) mỗi câu hỏi tách riêng đúng ý, (b) 2 policy
    dựng từ đó, (c) eligible() cũ = alias strict (không đổi hành vi), (d)
    policy lạ luôn raise, không âm thầm bỏ qua."""

    OK_FLAGS = {"period_ok": True, "signal_ok": True, "margin_rejections": 0}

    def _row(self, *, status="ok", total_trades=10, **flag_overrides) -> dict:
        return {
            "status": status, "total_trades": total_trades,
            "validity_flags": {**self.OK_FLAGS, **flag_overrides},
        }

    def test_fully_clean_trial_is_eligible_under_both_policies(self) -> None:
        c = selection.classify(self._row())
        self.assertTrue(c["execution_completed"])
        self.assertTrue(c["report_valid"])
        self.assertTrue(c["strict_research_eligible"])
        self.assertTrue(c["completed_execution_eligible"])
        self.assertEqual(c["reasons"], [])

    def test_failed_or_cancelled_status_excluded_under_both_policies(self) -> None:
        for status in ("cli_error", "cancelled", "timeout", "no_report"):
            c = selection.classify(self._row(status=status))
            self.assertFalse(c["execution_completed"], status)
            self.assertFalse(c["strict_research_eligible"], status)
            self.assertFalse(c["completed_execution_eligible"], status)
            self.assertIn("execution_not_completed", c["reasons"])

    def test_legacy_parity_flag_in_stored_rows_is_ignored(self) -> None:
        """Dòng cũ trong SQLite còn `parity_certified: False` (vd Single
        Backtest) — cờ đã bỏ, không được làm trial bị loại."""
        c = selection.classify(self._row(parity_certified=False))
        self.assertTrue(c["strict_research_eligible"])
        self.assertTrue(c["completed_execution_eligible"])
        self.assertNotIn("parity_certified", c)

    def test_period_or_signal_failure_excluded_under_both_policies(self) -> None:
        for override in ({"period_ok": False}, {"signal_ok": False}):
            c = selection.classify(self._row(**override))
            self.assertFalse(c["report_valid"], override)
            self.assertFalse(c["strict_research_eligible"], override)
            self.assertFalse(c["completed_execution_eligible"], override)

    def test_margin_rejection_excluded_strict_but_included_completed_execution(self) -> None:
        """Đúng kịch bản báo cáo: report đầy đủ, chạy hết kỳ, 1 lệnh bị từ
        chối margin — KHÔNG phải run hỏng."""
        c = selection.classify(self._row(margin_rejections=1))
        self.assertTrue(c["execution_completed"])
        self.assertTrue(c["report_valid"])
        self.assertEqual(c["margin_rejections"], 1)
        self.assertFalse(c["strict_research_eligible"])
        self.assertTrue(c["completed_execution_eligible"])
        self.assertIn("margin_rejections", c["reasons"])

    def test_unknown_margin_rejections_excluded_under_both_policies(self) -> None:
        """margin_rejections=None (chưa parse được log) khác margin=0 — không
        được coi là "sạch" dưới bất kỳ policy nào, kể cả completed_execution."""
        c = selection.classify(self._row(margin_rejections=None))
        self.assertIsNone(c["margin_rejections"])
        self.assertFalse(c["strict_research_eligible"])
        self.assertFalse(c["completed_execution_eligible"])
        self.assertIn("margin_rejections_unknown", c["reasons"])

    def test_below_min_trades_excluded_under_both_policies(self) -> None:
        c = selection.classify(self._row(total_trades=2), min_trades=5)
        self.assertFalse(c["meets_min_trades"])
        self.assertFalse(c["strict_research_eligible"])
        self.assertFalse(c["completed_execution_eligible"])
        self.assertIn("below_min_trades", c["reasons"])

    def test_eligible_is_strict_policy_alias(self) -> None:
        clean = self._row()
        margin = self._row(margin_rejections=1)
        self.assertEqual(selection.eligible(clean), selection.classify(clean)["strict_research_eligible"])
        self.assertEqual(selection.eligible(margin), selection.classify(margin)["strict_research_eligible"])
        self.assertFalse(selection.eligible(margin))

    def test_eligible_under_policy_raises_on_unknown_policy(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown trial policy"):
            selection.eligible_under_policy(self._row(), "made_up_policy")

    def test_exclusion_reasons_drops_margin_only_for_completed_execution(self) -> None:
        c = selection.classify(self._row(margin_rejections=1, period_ok=False))
        strict_reasons = selection.exclusion_reasons(c, selection.STRICT_RESEARCH_POLICY)
        completed_reasons = selection.exclusion_reasons(c, selection.COMPLETED_EXECUTION_POLICY)
        self.assertIn("margin_rejections", strict_reasons)
        self.assertNotIn("margin_rejections", completed_reasons)
        self.assertIn("period_mismatch", completed_reasons)  # non-margin reason stays

    def test_exclusion_reasons_raises_on_unknown_policy(self) -> None:
        c = selection.classify(self._row())
        with self.assertRaisesRegex(ValueError, "unknown trial policy"):
            selection.exclusion_reasons(c, "made_up_policy")

    def test_rank_default_policy_matches_old_eligible_behavior(self) -> None:
        """rank() không truyền policy phải xử sự y hệt trước khi có classify()
        — không âm thầm đổi hành vi Grid ranking/Walk-forward hiện có."""
        rows = [self._row(), self._row(margin_rejections=1)]
        self.assertEqual(len(selection.rank(rows)), 1)

    def test_rank_completed_execution_policy_includes_margin_rejected_rows(self) -> None:
        rows = [
            {**self._row(), "net_profit": 1, "profit_factor": 1.2},
            {**self._row(margin_rejections=1), "net_profit": 2, "profit_factor": 1.1},
        ]
        ranked = selection.rank(rows, policy=selection.COMPLETED_EXECUTION_POLICY)
        self.assertEqual(len(ranked), 2)


class FacilitatorTests(unittest.TestCase):
    """facilitator.py — bộ thực thi lõi (run_experiment) + phương pháp grid (chọn cli_engine
    hay api_engine, chạy song song, claim/retry). Trần max_parallel kiểm tra
    được mà không cần CLI/API/auth thật vì nó raise NGAY sau khi tính workers,
    trước khi chạm tới engine nào."""

    def test_max_parallel_default_is_twelve_and_capped(self) -> None:
        self.assertEqual(facilitator.DEFAULT_MAX_PARALLEL, 12)
        self.assertLessEqual(facilitator.DEFAULT_MAX_PARALLEL, facilitator.MAX_PARALLEL_CAP)

    def test_run_experiment_rejects_max_parallel_above_cap(self) -> None:
        with self.assertRaisesRegex(ValueError, "vượt trần an toàn"):
            facilitator.run_experiment(
                {"max_parallel": facilitator.MAX_PARALLEL_CAP + 1},
                {"method": "grid", "name": "x"},
                profile=None,
                specs=[object()],
                engine=EngineProfile("e", "5.9.16"),
            )

    def test_run_experiment_requires_engine(self) -> None:
        """[2026-09-17] engine giờ bắt buộc — thiếu nó thì không biết rẽ nhánh
        cli_engine hay api_engine, phải báo lỗi rõ thay vì AttributeError mù mờ
        sau này khi code chạm tới `engine.backend`."""
        with self.assertRaisesRegex(ValueError, "engine"):
            facilitator.run_experiment(
                {}, {"method": "grid", "name": "x"}, profile=None, specs=[object()],
            )


class PipelineLockTests(unittest.TestCase):
    """output_util/pipeline_lock.py — sổ ghi content-addressable dùng chung
    cho facilitator.run_grid()/walkforward.run_walkforward()/readout.py."""

    def test_stage_hash_is_deterministic_and_shape_agnostic(self) -> None:
        a = pipeline_lock.stage_hash("grid", {"KslLevel": True}, ["US30.cash"], 0.5)
        b = pipeline_lock.stage_hash("grid", {"KslLevel": True}, ["US30.cash"], 0.5)
        c = pipeline_lock.stage_hash("grid", {"KslLevel": True}, ["US30.cash"], 0.6)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_check_record_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.object(pipeline_lock, "LOCKS_ROOT", Path(td)):
            self.assertIsNone(pipeline_lock.check("us30_h1_combo", "grid", "hash1"))
            pipeline_lock.record("us30_h1_combo", "grid", "hash1", {"ref": "grid/x"})
            self.assertEqual(pipeline_lock.check("us30_h1_combo", "grid", "hash1"), {"ref": "grid/x"})
            # hash khac -> khong duoc coi la cache hit, du cung ten pipeline/stage
            self.assertIsNone(pipeline_lock.check("us30_h1_combo", "grid", "hash2"))

    def test_record_overwrites_only_named_stage(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.object(pipeline_lock, "LOCKS_ROOT", Path(td)):
            pipeline_lock.record("p", "grid", "h1", {"a": 1})
            pipeline_lock.record("p", "walkforward", "h2", {"b": 2})
            pipeline_lock.record("p", "grid", "h3", {"a": 3})  # ghi de dung "grid"
            doc = pipeline_lock.read_all("p")
            self.assertEqual(doc["stages"]["grid"], {"deps_hash": "h3", "outs": {"a": 3}})
            self.assertEqual(doc["stages"]["walkforward"], {"deps_hash": "h2", "outs": {"b": 2}})

    def test_list_pipelines_empty_when_no_locks_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.object(pipeline_lock, "LOCKS_ROOT", Path(td) / "nope"):
            self.assertEqual(pipeline_lock.list_pipelines(), [])

    def test_list_pipelines_finds_saved_locks(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.object(pipeline_lock, "LOCKS_ROOT", Path(td)):
            pipeline_lock.record("us30_h1_combo", "grid", "h", {})
            pipeline_lock.record("hk50_h4_macross", "grid", "h", {})
            self.assertEqual(pipeline_lock.list_pipelines(), ["hk50_h4_macross", "us30_h1_combo"])


class PipelineCachingIntegrationTests(unittest.TestCase):
    """Xác nhận facilitator.run_grid()/walkforward.run_walkforward()/
    readout.run_dsr()/run_pbo()/run_montecarlo() THẬT SỰ bỏ qua công việc thật
    (CLI/tính toán) khi cache trùng — không mock toàn bộ CLI, mà để phần việc
    thật ở trạng thái CHẮC CHẮN LỖI nếu bị chạm tới (thiếu config.yaml/report
    giả), rồi xác nhận không có lỗi nào xảy ra (tức cache đã chặn trước khi
    tới đó)."""

    def test_run_grid_skips_real_work_on_cache_hit(self) -> None:
        config = {
            "pipeline": "us30_h1_combo_test", "pipeline_stage": "grid",
            "symbols": ["US30.cash"], "timeframe": ["h1"], "strategy": "combo",
            "strategy_profile": "combo-v1", "engine_profile": "ctrader-5.9.16-ticks-approxfx-v1",
            "start": "2025-01-01", "end": "2026-09-01", "balance": 100000,
            "parameter_space": {"KslLevel": True}, "fixed_params": {"RiskPercent": 0.5},
        }
        expected_hash = pipeline_lock.stage_hash(
            "grid", config["symbols"], config["timeframe"], config["strategy"],
            config["strategy_profile"], config["engine_profile"],
            config["start"], config["end"], config["balance"],
            config["parameter_space"], config["fixed_params"], FAKE_FINGERPRINT,
        )
        with tempfile.TemporaryDirectory() as td, patch.object(pipeline_lock, "LOCKS_ROOT", Path(td)), \
                patch.object(facilitator, "input_fingerprint", return_value=FAKE_FINGERPRINT):
            pipeline_lock.record("us30_h1_combo_test", "grid", expected_hash, {"total": 100, "tally": {"ok": 100}})
            # Chi mock phan doc input (Redis/.algo/CLI) — neu cache khong chan
            # truoc run_experiment, ham se raise (thieu ha tang that).
            result = facilitator.run_grid(config)
            self.assertEqual(result, {"total": 100, "tally": {"ok": 100}, "cached": True})

    def test_run_walkforward_skips_real_work_on_cache_hit(self) -> None:
        config = {
            "pipeline": "us30_h1_combo_test",
            "symbols": ["US30.cash"], "timeframe": ["h1"], "strategy": "combo",
            "strategy_profile": "combo-v1", "engine_profile": "ctrader-5.9.16-ticks-approxfx-v1",
            "start": "2025-01-01", "end": "2026-09-01", "balance": 100000,
            "parameter_space": {"KslLevel": True}, "fixed_params": {"RiskPercent": 0.5},
        }
        expected_hash = pipeline_lock.stage_hash(
            "walkforward", config["symbols"], config["timeframe"], config["strategy"],
            config["strategy_profile"], config["engine_profile"],
            config["start"], config["end"], config["balance"],
            config["parameter_space"], config["fixed_params"],
            # walkforward, selection_rules, plateau_dimensions, min_trades, objective, zones
            None, None, None, None, None, None, FAKE_FINGERPRINT,
        )
        with tempfile.TemporaryDirectory() as td, patch.object(pipeline_lock, "LOCKS_ROOT", Path(td)), \
                patch.object(facilitator, "input_fingerprint", return_value=FAKE_FINGERPRINT):
            pipeline_lock.record("us30_h1_combo_test", "walkforward", expected_hash, {"windows": 3})
            result = walkforward.run_walkforward(config)
            self.assertEqual(result, {"windows": 3, "cached": True})

    def test_run_dsr_and_run_pbo_skip_real_work_on_cache_hit(self) -> None:
        with tempfile.TemporaryDirectory() as td, \
                patch.object(store, "RUNS_ROOT", Path(td) / "runs"), \
                patch.object(pipeline_lock, "LOCKS_ROOT", Path(td) / "locks"):
            st = store.ExperimentStore("grid", "exp")
            st.open({"schema": "bo-research-experiment/v1", "name": "exp"})
            db_sha = store.sha256_file(st.db_path)
            for fn, stage in ((readout.run_dsr, "dsr"), (readout.run_pbo, "pbo")):
                config = {"min_trades": 5}
                # deps_hash goi qua trial_policy da CHUAN HOA (mac dinh strict)
                # - config goc khong co key nay, nhung ham tu them truoc khi hash.
                expected_hash = pipeline_lock.stage_hash(
                    stage, db_sha, {**config, "trial_policy": selection.STRICT_RESEARCH_POLICY},
                )
                pipeline_lock.record("pipe", stage, expected_hash, {"value": 42})
                result = fn("grid", "exp", config, pipeline="pipe")
                self.assertEqual(result, {"value": 42, "cached": True})

    def test_run_montecarlo_skips_real_work_on_cache_hit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report_path = Path(td) / "report.json.gz"
            report_path.write_bytes(b"not a real gzip, must never be opened")
            report_sha = store.sha256_file(report_path)
            with patch.object(pipeline_lock, "LOCKS_ROOT", Path(td) / "locks"):
                config = {"paths": 10}
                expected_hash = pipeline_lock.stage_hash("monte_carlo", report_sha, config)
                pipeline_lock.record("pipe", "monte_carlo", expected_hash, {"schema": "cached-mc"})
                result = readout.run_montecarlo(report_path, config, pipeline="pipe")
                self.assertEqual(result, {"schema": "cached-mc", "cached": True})


class StoreMonteCarloTests(unittest.TestCase):
    def test_store_snapshot_and_claim_resume(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.object(store, "RUNS_ROOT", Path(td) / "runs"):
            src = Path(td) / "a.algo"
            src.write_bytes(b"algo")
            st = store.ExperimentStore("grid", "exp")
            st.open({"schema": "bo-research-experiment/v1", "name": "exp"})
            snap, digest = st.snapshot(src)
            self.assertTrue(snap.is_file())
            spec = RunSpec("combo", "combo-v1", "engine", "US30.cash", "h1",
                           date(2026, 1, 1), date(2026, 1, 2), 100000, "ticks", {})
            claim = st.claim(spec, st.param_hash(spec, digest))
            self.assertEqual(claim.disposition, "claimed")
            busy = st.claim(spec, st.param_hash(spec, digest))
            self.assertEqual(busy.disposition, "busy")

    def test_montecarlo_is_reproducible(self) -> None:
        points = [
            {"time": "2026-01-01T12:00:00+00:00", "balance": 100000},
            {"time": "2026-01-02T12:00:00+00:00", "balance": 101000},
            {"time": "2026-01-03T12:00:00+00:00", "balance": 100500},
        ]
        cfg = montecarlo.MonteCarloConfig(paths=100, block_days=1, seed=7)
        self.assertEqual(montecarlo.run(points, cfg), montecarlo.run(points, cfg))

    def test_montecarlo_run_dual_compares_balance_and_min_equity(self) -> None:
        # balance đổi <=1%/ngày (không bao giờ chạm ngưỡng daily_loss_pct=5%);
        # minEquity có 1 ngày sụt ~11% (lỗ nổi giữa ngày) — đủ để 2 nhánh cho
        # kết quả breach khác nhau rõ rệt, xác nhận field nào ảnh hưởng gì.
        points = [
            {"time": "2026-01-01T12:00:00+00:00", "balance": 100000, "minEquity": 100000},
            {"time": "2026-01-02T12:00:00+00:00", "balance": 101000, "minEquity": 89000},
            {"time": "2026-01-03T12:00:00+00:00", "balance": 100500, "minEquity": 99500},
        ]
        cfg = montecarlo.MonteCarloConfig(paths=200, block_days=1, seed=7)
        result = montecarlo.run_dual(points, cfg)
        self.assertEqual(result["schema"], "bo-research-montecarlo-dual/v1")
        self.assertEqual(result["balance_based"]["return_field"], "balance")
        self.assertEqual(result["min_equity_based"]["return_field"], "minEquity")
        self.assertEqual(result["balance_based"]["p_daily_breach"], 0.0)
        self.assertGreater(result["min_equity_based"]["p_daily_breach"], 0.0)

    def test_montecarlo_each_block_method_is_reproducible(self) -> None:
        points = [
            {"time": f"2026-01-{day:02d}T12:00:00+00:00", "balance": 100000 + day * 37}
            for day in range(1, 11)
        ]
        cfg = montecarlo.MonteCarloConfig(paths=50, block_days=3, seed=11)
        for method in montecarlo.BLOCK_METHODS:
            with self.subTest(method=method):
                first = montecarlo.run(points, cfg, method=method)
                second = montecarlo.run(points, cfg, method=method)
                self.assertEqual(first, second)
                self.assertEqual(first["block_method"], method)

    def test_montecarlo_moving_blocks_cover_every_start_position(self) -> None:
        # Kunsch (1989): khoi chong lap phai co N = n-L+1 vi tri bat dau, khong
        # chi boi so cua L nhu non_overlapping (n/L).
        days = [float(i) for i in range(10)]
        non_overlapping = montecarlo._cut_blocks(days, 3, "non_overlapping")
        moving = montecarlo._cut_blocks(days, 3, "moving")
        self.assertEqual(len(non_overlapping), 4)   # ceil(10/3)
        self.assertEqual(len(moving), 8)             # 10-3+1

    def test_montecarlo_run_methods_compare_returns_three_named_branches(self) -> None:
        points = [
            {"time": f"2026-01-{day:02d}T12:00:00+00:00", "balance": 100000 + day * 37}
            for day in range(1, 11)
        ]
        cfg = montecarlo.MonteCarloConfig(paths=50, block_days=3, seed=11)
        result = montecarlo.run_methods_compare(points, cfg)
        self.assertEqual(result["schema"], "bo-research-montecarlo-methods/v1")
        self.assertEqual(set(montecarlo.BLOCK_METHODS), {"non_overlapping", "moving", "stationary"})
        for method in montecarlo.BLOCK_METHODS:
            self.assertEqual(result[method]["block_method"], method)


class DsrTests(unittest.TestCase):
    """optimize/dsr.py — Deflated Sharpe Ratio, thuần hàm/không I/O."""

    def test_sharpe_ratio_and_skewness_hand_verifiable(self) -> None:
        self.assertEqual(dsr.sharpe_ratio([1.0, 1.0, 1.0]), 0.0)  # phuong sai=0, tranh chia 0
        self.assertAlmostEqual(dsr.skewness([-1.0, 0.0, 1.0]), 0.0)  # doi xung hoan hao

    def test_expected_max_sharpe_matches_cited_literature_value(self) -> None:
        # Bailey & Lopez de Prado (2014): voi sigma_SR=1, N=1000 -> E[max SR]
        # ~3.26 (so da duoc trich trong research_notes truoc khi code file
        # nay). Dung dataset 500 gia tri +1 / 500 gia tri -1 -> pstdev=1.0
        # DUNG (khong lam tron), kiem tra cong thuc khop dung nguon da dan.
        sharpe_values = [1.0] * 500 + [-1.0] * 500
        e_max = dsr.expected_max_sharpe(sharpe_values)
        self.assertAlmostEqual(e_max, 3.26, delta=0.01)

    def test_expected_max_sharpe_grows_with_n(self) -> None:
        small = dsr.expected_max_sharpe([1.0, -1.0] * 5)
        large = dsr.expected_max_sharpe([1.0, -1.0] * 500)
        self.assertGreater(large, small)

    def test_deflated_sharpe_ratio_is_half_when_observed_equals_benchmark(self) -> None:
        # SR quan sat == nguong -> tu so = 0 -> Phi(0) = 0.5 dung, bat ke T/skew
        # (skew=0, kurt=3 la truong hop chuan, mau so > 0).
        value = dsr.deflated_sharpe_ratio(
            0.1, benchmark_sharpe=0.1, sample_length=200, skew=0.0, kurt=3.0,
        )
        self.assertAlmostEqual(value, 0.5)

    def test_run_picks_highest_sharpe_trial_and_returns_probability(self) -> None:
        good = [0.02, 0.018, 0.021, 0.019, 0.02, 0.017, 0.022, 0.02] * 5
        bad = [0.001, -0.002, 0.0, 0.001, -0.001, 0.0, 0.002, -0.001] * 5
        trials = {f"noise_{i}": bad for i in range(9)}
        trials["good"] = good
        result = dsr.run(trials)
        self.assertEqual(result["winner_label"], "good")
        self.assertEqual(result["n_trials"], 10)
        self.assertGreaterEqual(result["dsr"], 0.0)
        self.assertLessEqual(result["dsr"], 1.0)

    def test_run_requires_at_least_two_trials(self) -> None:
        with self.assertRaises(ValueError):
            dsr.run({"only_one": [0.01, 0.02, -0.01]})


class PboTests(unittest.TestCase):
    """optimize/pbo.py — PBO/CSCV, thuần hàm/không I/O."""

    def test_always_dominant_trial_never_flagged_as_overfit(self) -> None:
        # 1 trial thang TUYET DOI moi ngay (khong chi trung binh) -> thang IS
        # THI thang luon OOS o MOI cach chia -> PBO phai dung bang 0.
        trials = {
            "best": [0.05] * 8,
            "middle": [0.02] * 8,
            "worst": [-0.01] * 8,
        }
        result = pbo.run(trials, blocks=4)
        self.assertEqual(result["pbo"], 0.0)
        self.assertEqual(result["splits_tested"], 6)  # C(4,2)
        self.assertEqual(result["n_trials"], 3)
        self.assertEqual(result["sample_length"], 8)

    def test_split_dependent_luck_is_detected(self) -> None:
        # Tai dung dung vi du tay da giai thich cho nguoi dung: A thang o
        # {Q1,Q2} nhung roi xuong TE NHAT o {Q3,Q4} - dung la 1 lan overfit.
        # Moi "quy" = 1 khoi 2 ngay (blocks=4, chunk_len=2).
        trials = {
            "A": [2.5, 2.5, 1.5, 1.5, -1.0, -1.0, 0.5, 0.5],
            "B": [0.5, 0.5, 0.5, 0.5, 2.0, 2.0, 1.5, 1.5],
            "C": [1.0, 1.0, 1.0, 1.0, 0.5, 0.5, 1.0, 1.0],
        }
        result = pbo.run(trials, blocks=4)
        self.assertGreater(result["pbo"], 0.0)

    def test_rejects_mismatched_trial_lengths(self) -> None:
        with self.assertRaises(ValueError):
            pbo.run({"a": [0.01, 0.02], "b": [0.01, 0.02, 0.03]}, blocks=2)

    def test_rejects_odd_blocks(self) -> None:
        with self.assertRaises(ValueError):
            pbo.run({"a": [0.01] * 8, "b": [0.02] * 8}, blocks=3)

    def test_rejects_blocks_exceeding_sample_length(self) -> None:
        with self.assertRaises(ValueError):
            pbo.run({"a": [0.01] * 4, "b": [0.02] * 4}, blocks=8)


class SignalTransTests(unittest.TestCase):
    """signal_trans.py — chuyển Redis (List+Hash) sang CSV đúng khuôn
    LoadSignalFile(). Chỉ test logic thuần qua mock Redis client, không cần
    server Redis thật."""

    def test_signal_list_key_format(self) -> None:
        self.assertEqual(
            signal_trans.signal_list_key("US30", "h1", "combo"),
            "L_PastSignal_US30_H1_COMBO",
        )

    def test_signal_list_key_accepts_custom_key_prefix(self) -> None:
        # DB3 "L_PastSignal_Trend" (2026-09-26) -- cung khuon key, chi khac
        # tien to, xem RedisProfile.key_prefix.
        self.assertEqual(
            signal_trans.signal_list_key("US30", "h1", "combo", key_prefix="L_PastSignal_Trend"),
            "L_PastSignal_Trend_US30_H1_COMBO",
        )

    def test_fetch_signal_rows_returns_empty_when_list_missing(self) -> None:
        client = MagicMock()
        client.lrange.return_value = []
        rows = signal_trans.fetch_signal_rows(client, "US30", "H1", "combo")
        self.assertEqual(rows, [])
        client.pipeline.assert_not_called()

    def test_fetch_signal_rows_uses_pipeline_and_skips_missing_hashes(self) -> None:
        client = MagicMock()
        client.lrange.return_value = ["2024-01-01 23:00", "2024-01-02 00:00"]
        pipe = MagicMock()
        client.pipeline.return_value = pipe
        pipe.execute.return_value = [
            {"bartime": "2024-01-01 23:00", "atr": "1.0", "entry": "100", "signal": "1"},
            {},  # HASH mất/race-condition -> phải bị bỏ qua, không đoán giá trị
        ]
        rows = signal_trans.fetch_signal_rows(client, "US30", "H1", "combo")
        self.assertEqual(len(rows), 1)
        client.lrange.assert_called_once_with("L_PastSignal_US30_H1_COMBO", 0, -1)

    def test_fetch_signal_rows_uses_custom_key_prefix_for_lrange(self) -> None:
        client = MagicMock()
        client.lrange.return_value = []
        signal_trans.fetch_signal_rows(client, "US30", "H1", "combo", key_prefix="L_PastSignal_Trend")
        client.lrange.assert_called_once_with("L_PastSignal_Trend_US30_H1_COMBO", 0, -1)

    def test_materialize_signal_csv_reads_from_profile_key_prefix(self) -> None:
        # profile.key_prefix (khong phai hang so cung "L_PastSignal") phai
        # quyet dinh key LIST thuc su duoc doc -- day la co che duy nhat de
        # doi tu DB2 sang DB3 (xem docstring module).
        profile = MagicMock()
        profile.key_prefix = "L_PastSignal_Trend"
        fake_client = MagicMock()
        with tempfile.TemporaryDirectory() as td, \
                patch.object(signal_trans, "redis_client", return_value=fake_client), \
                patch.object(
                    signal_trans, "fetch_signal_rows",
                    return_value=[{"bartime": "2024-01-01 23:00", "atr": "1.0", "signal": "1"}],
                ) as fetch:
            dest = Path(td) / "out.csv"
            written = signal_trans.materialize_signal_csv(profile, "US30", "H1", "ma_cross", dest)
        self.assertEqual(written, 1)
        fetch.assert_called_once_with(fake_client, "US30", "H1", "ma_cross", key_prefix="L_PastSignal_Trend")

    def test_materialize_signal_csv_error_message_uses_profile_key_prefix(self) -> None:
        profile = MagicMock()
        profile.key_prefix = "L_PastSignal_Trend"
        with tempfile.TemporaryDirectory() as td, \
                patch.object(signal_trans, "redis_client", return_value=MagicMock()), \
                patch.object(signal_trans, "fetch_signal_rows", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "L_PastSignal_Trend_US30_H1_MA_CROSS"):
                signal_trans.materialize_signal_csv(profile, "US30", "H1", "ma_cross", Path(td) / "out.csv")

    def test_load_redis_profile_defaults_key_prefix_to_past_signal(self) -> None:
        profile = load_redis_profile("sen05-signal-v1")
        self.assertEqual(profile.db, 2)
        self.assertEqual(profile.key_prefix, "L_PastSignal")

    def test_load_redis_profile_reads_trend_key_prefix_for_db3(self) -> None:
        profile = load_redis_profile("sen05-signal-trend-v1")
        self.assertEqual(profile.db, 3)
        self.assertEqual(profile.key_prefix, "L_PastSignal_Trend")
        # Cung server/mat khau voi DB2 -- chi khac db + key_prefix.
        self.assertEqual(profile.host, load_redis_profile("sen05-signal-v1").host)
        self.assertEqual(profile.pwd_file, load_redis_profile("sen05-signal-v1").pwd_file)

    def test_write_signal_csv_matches_combo_header_and_keeps_raw_signal(self) -> None:
        rows = [
            {"bartime": "2024-01-01 23:00", "atr": "71.71", "entry": "37753.2", "signal": "1"},
            {"bartime": "2024-01-02 00:00", "atr": "70.0", "entry": "37700.0", "signal": "2"},
        ]
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "out.csv"
            written = signal_trans.write_signal_csv(rows, dest, "combo")
            self.assertEqual(written, 2)
            lines = dest.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(lines[0], "bartime,atr,entry,signal")
            # signal="2" (SELL theo ProtoOATradeSide) giữ NGUYÊN, không tự
            # chuẩn hoá -> LoadSignalFile() bên cBot mới là nơi chuẩn hoá.
            self.assertEqual(lines[2], "2024-01-02 00:00,70.0,37700.0,2")

    def test_write_signal_csv_skips_rows_missing_required_column(self) -> None:
        rows = [{"bartime": "2024-01-01 23:00", "atr": "1.0", "signal": "1"}]  # thiếu entry
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "out.csv"
            written = signal_trans.write_signal_csv(rows, dest, "combo")
            self.assertEqual(written, 0)

    def test_write_signal_csv_ma_cross_has_no_entry_column(self) -> None:
        rows = [{"bartime": "2024-01-01 23:00", "atr": "1.0", "signal": "1"}]
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "out.csv"
            written = signal_trans.write_signal_csv(rows, dest, "ma_cross")
            self.assertEqual(written, 1)
            self.assertEqual(dest.read_text(encoding="utf-8").splitlines()[0], "bartime,atr,signal")


class ReadoutTests(unittest.TestCase):
    """readout.py — tầng đọc kết quả TỔNG QUÁT. Khoá lại: module import sạch
    (không vòng import ngược) và mặt API còn đúng phần dùng chung được cho mọi
    phương pháp (đọc kết quả riêng walk-forward nằm ở walkforward_readout.py)."""

    def test_public_api_surface_is_method_agnostic(self) -> None:
        self.assertTrue(callable(readout.screen))
        self.assertTrue(callable(readout.run_montecarlo))
        self.assertTrue(callable(readout.run_dsr))
        self.assertTrue(callable(readout.run_pbo))
        self.assertIs(readout.export_release, store.export_release)
        self.assertFalse(hasattr(readout, "walkforward_table"))


class WalkforwardTests(unittest.TestCase):
    """walkforward.py — TOÀN BỘ phương pháp walk-forward gom về 1 file
    (2026-09-22). Khoá lại 2 điều: (a) mặt API đầy đủ nằm đúng ở đây, (b) luật
    chia cửa sổ vẫn giữ nguyên hành vi sau khi dời khỏi planner.py."""

    def test_public_api_surface_lives_in_one_module(self) -> None:
        for name in ("walkforward_windows", "plateau", "pick", "run_walkforward"):
            self.assertTrue(callable(getattr(walkforward, name)), name)
        # Doc ket qua da doi sang output_util/walkforward_readout.py (thuan doc).
        self.assertFalse(hasattr(walkforward, "walkforward_table"))
        # Khong con sot lai o file cu — tranh 2 ban song song lech nhau.
        self.assertFalse(hasattr(selection, "plateau"))
        self.assertFalse(hasattr(selection, "pick"))
        self.assertFalse(hasattr(selection, "WF_MIN_TRADES"))

    def test_walkforward_windows_join_without_gap_or_overlap(self) -> None:
        """`end` la moc LOAI TRU, nen doan hoc va doan kiem phai GIAP KHIT.

        Ban cu tru 1 ngay o ca hai moc (viet theo quy uoc cu "end bao gom"),
        nen sau khi doi quy uoc 2026-09-13 thi moi cua so am tham mat ngay cuoi.
        Test nay ghim ca hai tinh chat: khong ho ngay nao, va khong chong lan.
        """
        pairs = walkforward.walkforward_windows({"start": "2024-01-01", "end": "2026-09-13"})
        self.assertEqual(len(pairs), 6)
        first_is, first_oos = pairs[0]
        self.assertEqual((first_is.start, first_is.end), (date(2024, 1, 1), date(2025, 1, 1)))
        self.assertEqual((first_oos.start, first_oos.end), (date(2025, 1, 1), date(2025, 4, 1)))
        for is_window, oos_window in pairs:
            self.assertEqual(is_window.end, oos_window.start)
            self.assertEqual(is_window.zone, "train")
            self.assertEqual(oos_window.zone, "oos")
        # Cua so cuoi khong duoc vuot qua moc `end`.
        self.assertLessEqual(pairs[-1][1].end, date(2026, 9, 13))
        # Truot dung 3 thang moi lan.
        self.assertEqual(pairs[1][0].start, date(2024, 4, 1))


class ResolveSignalPathTests(unittest.TestCase):
    """resolve_signal_path() — nối dây Redis-only 2026-09-21 lần 3. Mock thẳng
    materialize_signal_csv() (đã có test riêng ở lớp SignalTransTests cho phần
    dưới nó: fetch_signal_rows/write_signal_csv) — lớp này chỉ khoá lại phần
    LOGIC MỚI: map symbol->group, map strategy nội bộ->token Redis, và dedup
    qua `materialized` trong 1 batch.

    [2026-09-22] dest_dir giờ do CALLER cấp (không còn hằng số REDIS_CACHE_DIR
    cố định — bỏ vì không mang lại giá trị thật, xem docstring signal_trans.py)
    — mỗi test tự tạo 1 thư mục tạm riêng."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dest_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _spec(self, symbol: str, timeframe: str) -> RunSpec:
        return RunSpec("combo", "combo-v1", "engine", symbol, timeframe,
                       date(2026, 1, 1), date(2026, 2, 1), 100000, "ticks", {})

    def test_combo_maps_symbol_group_and_keeps_combo_token(self) -> None:
        profile = load_strategy_profile("combo-v1")
        redis_profile = MagicMock()
        spec = self._spec("US30.cash", "h1")
        with patch.object(signal_trans, "materialize_signal_csv") as materialize:
            path = signal_trans.resolve_signal_path(redis_profile, profile, spec, self.dest_dir)
        expected = self.dest_dir / "US30_H1_combo.csv"
        self.assertEqual(path, expected)
        materialize.assert_called_once_with(redis_profile, "US30", "h1", "combo", expected)

    def test_macross_internal_name_maps_to_ma_cross_redis_token(self) -> None:
        """profile.strategy nội bộ = "macross" (liền, config.yaml) nhưng key
        Redis thật của strategy_lab dùng "ma_cross" (gạch dưới) — đây chính là
        lỗi tôi (Claude) suýt để lọt khi thiết kế, đã sửa lại đúng theo memory
        cbot-cli-simulation-redis-vision.md trước khi code."""
        profile = load_strategy_profile("macross-v1")
        redis_profile = MagicMock()
        spec = self._spec("US30.cash", "m30")
        with patch.object(signal_trans, "materialize_signal_csv") as materialize:
            path = signal_trans.resolve_signal_path(redis_profile, profile, spec, self.dest_dir)
        expected = self.dest_dir / "US30_M30_ma_cross.csv"
        self.assertEqual(path, expected)
        materialize.assert_called_once_with(redis_profile, "US30", "m30", "ma_cross", expected)

    def test_dedups_within_a_shared_materialized_set(self) -> None:
        """Quét lưới nhiều KslLevel/KtpLevel trên CÙNG 1 symbol/timeframe chỉ
        được đọc Redis ĐÚNG 1 LẦN trong 1 batch — không phải 1 lần/spec."""
        profile = load_strategy_profile("combo-v1")
        redis_profile = MagicMock()
        materialized: set[Path] = set()
        with patch.object(signal_trans, "materialize_signal_csv") as materialize:
            signal_trans.resolve_signal_path(redis_profile, profile, self._spec("US30.cash", "h1"),
                                             self.dest_dir, materialized=materialized)
            signal_trans.resolve_signal_path(redis_profile, profile, self._spec("US30.cash", "h1"),
                                             self.dest_dir, materialized=materialized)
        materialize.assert_called_once()

    def test_rejects_symbol_without_redis_mapping(self) -> None:
        profile = load_strategy_profile("combo-v1")
        spec = self._spec("NOTASYMBOL", "h1")
        with patch.object(signal_trans, "materialize_signal_csv") as materialize:
            with self.assertRaisesRegex(KeyError, "NOTASYMBOL"):
                signal_trans.resolve_signal_path(MagicMock(), profile, spec, self.dest_dir)
        materialize.assert_not_called()


IN_WINDOW_CSV = "bartime,atr,entry,signal\n2026-01-05 10:00,50,100,1\n"


def _fake_result(work_dir: Path, status: RunStatus = RunStatus.OK) -> RunResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "bot.log").write_text("", encoding="utf-8")
    (work_dir / "cli.log").write_text("", encoding="utf-8")
    return RunResult(
        status=status,
        failure_code=FailureCode.NONE if status is RunStatus.OK else FailureCode.NO_REPORT,
        reason="", exit_code=0, timed_out=False, wall_seconds=0.1,
        started_utc="2026-01-01T00:00:00+00:00", ended_utc="2026-01-01T00:00:01+00:00",
        cli_path="fake", cli_version="fake",
        report_json=work_dir / "report.json", cli_log=work_dir / "cli.log",
        bot_log=work_dir / "bot.log", cbotset=work_dir / "params.cbotset",
    )


class RunExperimentLifecycleTests(unittest.TestCase):
    """[2026-09-24] Cancel không lên lịch thêm (C1), fingerprint input + 1
    experiment = 1 bộ input (C3), cờ complete/failed (C4). Chạy run_experiment
    THẬT, chỉ thay phần chạm hạ tầng ngoài (CLI, Redis, protocol DB)."""

    def _run(self, td: str, *, csv_text: str, run_backtest, should_cancel=None, name: str = "exp",
             protocol: Any = None):
        profile = load_strategy_profile("combo-v1")
        engine = load_engine_profile("ctrader-5.9.16-ticks-approxfx-v1")
        config = {
            "experiment": name, "strategy": "combo", "strategy_profile": "combo-v1",
            "engine_profile": engine.id, "symbols": ["US30.cash"], "timeframe": "h1",
            "start": "2026-01-01", "end": "2026-02-01", "balance": 100000,
            "parameter_space": {"KslLevel": ["0", "1", "2", "3"]}, "max_parallel": 1,
        }
        experiment = build_experiment(config)
        specs = expand_grid(config, profile, engine)
        cli = Path(td) / "ctrader-cli.exe"
        cli.write_bytes(b"fake cli")

        def resolve(_redis, _profile, _spec, dest_dir, *, materialized=None):
            path = Path(dest_dir) / "US30_H1_combo.csv"
            path.write_text(csv_text, encoding="utf-8")
            return path

        with patch.object(store, "RUNS_ROOT", Path(td) / "runs"), \
                patch.object(facilitator, "ProtocolStore", MagicMock(return_value=protocol or MagicMock())), \
                patch.object(facilitator, "load_redis_profile", MagicMock()), \
                patch.object(facilitator.signal_trans, "resolve_signal_path", side_effect=resolve), \
                patch.object(facilitator.cli_runner, "select_cli", return_value=cli), \
                patch.object(facilitator.cli_runner, "read_metadata", return_value=metadata_for("combo-v1")), \
                patch.object(facilitator.cli_runner, "validate_capabilities"), \
                patch.object(facilitator.cli_runner, "auth_options", return_value={}), \
                patch.object(facilitator.cli_runner, "child_environment", return_value={}), \
                patch.object(facilitator.cli_runner, "run_backtest", side_effect=run_backtest):
            result = facilitator.run_experiment(
                config, experiment.canonical(), profile, specs, engine=engine, should_cancel=should_cancel,
            )
            rows = store.ExperimentStore("grid", name).rows()
        return result, rows

    def test_stop_prevents_new_claims_and_spawns(self) -> None:
        calls = {"n": 0}

        def run_backtest(spec, **kwargs):
            calls["n"] += 1
            return _fake_result(kwargs["work_dir"])

        with tempfile.TemporaryDirectory() as td:
            result, rows = self._run(
                td, csv_text=IN_WINDOW_CSV, run_backtest=run_backtest,
                should_cancel=lambda: calls["n"] >= 1,
            )
        self.assertEqual(calls["n"], 1)
        self.assertEqual(result["tally"], {"ok": 1, facilitator.NOT_STARTED: 3})
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["complete"])
        self.assertEqual(len(rows), 1)

    def test_complete_and_failed_counts(self) -> None:
        def run_backtest(spec, **kwargs):
            status = RunStatus.CLI_ERROR if spec.params["KslLevel"] == "3" else RunStatus.OK
            return _fake_result(kwargs["work_dir"], status)

        with tempfile.TemporaryDirectory() as td:
            result, _ = self._run(td, csv_text=IN_WINDOW_CSV, run_backtest=run_backtest)
        self.assertFalse(result["complete"])
        self.assertEqual(result["failed"], 1)
        self.assertFalse(result["cancelled"])

        with tempfile.TemporaryDirectory() as td:
            result, _ = self._run(td, csv_text=IN_WINDOW_CSV, run_backtest=lambda spec, **kw: _fake_result(kw["work_dir"]))
        self.assertTrue(result["complete"])
        self.assertEqual(result["failed"], 0)
        self.assertEqual(set(result["inputs"]), {"algo_sha256", "cli_sha256", "signals"})

    def test_signals_appended_after_end_do_not_rerun_finished_specs(self) -> None:
        ran = {"n": 0}

        def run_backtest(spec, **kwargs):
            ran["n"] += 1
            return _fake_result(kwargs["work_dir"])

        with tempfile.TemporaryDirectory() as td:
            first, _ = self._run(td, csv_text=IN_WINDOW_CSV, run_backtest=run_backtest)
            later = IN_WINDOW_CSV + "2026-03-01 10:00,55,101,2\n"
            second, rows = self._run(td, csv_text=later, run_backtest=run_backtest)
        self.assertEqual(ran["n"], 4)
        self.assertEqual(second["tally"], {"skipped": 4})
        self.assertTrue(second["complete"])
        self.assertEqual(len(rows), 4)
        self.assertEqual(first["inputs"], second["inputs"])

    def test_changed_signals_inside_window_refuse_to_mix_into_experiment(self) -> None:
        ok = lambda spec, **kw: _fake_result(kw["work_dir"])  # noqa: E731
        with tempfile.TemporaryDirectory() as td:
            self._run(td, csv_text=IN_WINDOW_CSV, run_backtest=ok)
            changed = IN_WINDOW_CSV + "2026-01-20 10:00,55,101,2\n"
            with self.assertRaises(facilitator.InputFingerprintMismatch):
                self._run(td, csv_text=changed, run_backtest=ok)

    def test_experiment_without_fingerprint_sidecar_is_not_extended(self) -> None:
        ok = lambda spec, **kw: _fake_result(kw["work_dir"])  # noqa: E731
        with tempfile.TemporaryDirectory() as td:
            self._run(td, csv_text=IN_WINDOW_CSV, run_backtest=ok)
            (Path(td) / "runs" / "grid" / "exp" / "input_fingerprint.json").unlink()
            with self.assertRaisesRegex(facilitator.InputFingerprintMismatch, "predates"):
                self._run(td, csv_text=IN_WINDOW_CSV, run_backtest=ok)


class CancellationHookTests(unittest.TestCase):
    def test_cancel_requested_ignores_broken_observers(self) -> None:
        self.assertFalse(cli_runner.cancel_requested(None))
        self.assertTrue(cli_runner.cancel_requested(lambda: True))

        def broken() -> bool:
            raise RuntimeError("dashboard observer lost")

        self.assertFalse(cli_runner.cancel_requested(broken))

    def test_run_backtest_does_not_spawn_when_already_cancelled(self) -> None:
        spec = RunSpec("combo", "combo-v1", "engine", "US30.cash", "h1",
                       date(2026, 1, 1), date(2026, 2, 1), 100000, "ticks", {})
        with tempfile.TemporaryDirectory() as td, \
                patch.object(cli_runner, "run_process", side_effect=AssertionError("must not spawn")):
            result = cli_runner.run_backtest(
                spec, algo_path=Path(td) / "a.algo", signal_file=Path(td) / "s.csv",
                work_dir=Path(td) / "work", cli_path=Path(td) / "ctrader-cli.exe",
                should_cancel=lambda: True,
            )
            self.assertTrue(result.bot_log.is_file())
        self.assertIs(result.status, RunStatus.CANCELLED)
        self.assertIs(result.failure_code, FailureCode.CANCELLED)


class StaleClaimTests(unittest.TestCase):
    """[2026-09-24] Dòng 'running' của tiến trình đã chết được claim lại (C2);
    owner còn sống hoặc không kiểm chứng được thì vẫn 'busy'."""

    def _claimed_store(self, td: str):
        st = store.ExperimentStore("grid", "exp")
        st.open({"schema": "bo-research-experiment/v1", "name": "exp"})
        spec = RunSpec("combo", "combo-v1", "engine", "US30.cash", "h1",
                       date(2026, 1, 1), date(2026, 1, 2), 100000, "ticks", {})
        param_hash = st.param_hash(spec, "x")
        return st, spec, param_hash

    def _set_owner(self, st, param_hash: str, owner: str) -> None:
        db = sqlite3.connect(st.db_path)
        try:
            db.execute("UPDATE runs SET claim_owner=? WHERE param_hash=?", (owner, param_hash))
            db.commit()
        finally:
            db.close()

    def test_dead_owner_is_reclaimed_live_or_foreign_owner_stays_busy(self) -> None:
        live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        try:
            with tempfile.TemporaryDirectory() as td, patch.object(store, "RUNS_ROOT", Path(td) / "runs"):
                st, spec, param_hash = self._claimed_store(td)
                self.assertEqual(st.claim(spec, param_hash).disposition, "claimed")
                host = socket.gethostname()

                self._set_owner(st, param_hash, f"{host}:{live.pid}")
                self.assertEqual(st.claim(spec, param_hash).disposition, "busy")

                self._set_owner(st, param_hash, f"other-host:{dead.pid}")
                self.assertEqual(st.claim(spec, param_hash).disposition, "busy")

                self._set_owner(st, param_hash, f"{host}:{dead.pid}")
                self.assertEqual(st.claim(spec, param_hash).disposition, "claimed")
        finally:
            live.kill()
            live.wait()


class RunGridLockPolicyTests(unittest.TestCase):
    CONFIG = {
        "pipeline": "p", "symbols": ["US30.cash"], "timeframe": ["h1"], "strategy": "combo",
        "strategy_profile": "combo-v1", "engine_profile": "ctrader-5.9.16-ticks-approxfx-v1",
        "start": "2026-01-01", "end": "2026-02-01", "balance": 100000,
        "parameter_space": {"KslLevel": ["0", "1"]}, "fixed_params": {"RiskPercent": 0.5},
        "experiment": "exp",
    }

    def test_lock_recorded_only_for_complete_runs_and_input_is_pinned(self) -> None:
        for complete in (False, True):
            outcome = {"total": 2, "tally": {"ok": 2}, "cancelled": False, "complete": complete}
            with tempfile.TemporaryDirectory() as td, patch.object(pipeline_lock, "LOCKS_ROOT", Path(td)), \
                    patch.object(facilitator, "input_fingerprint", return_value=FAKE_FINGERPRINT), \
                    patch.object(facilitator, "run_experiment", return_value=outcome) as run:
                facilitator.run_grid(self.CONFIG)
                stages = pipeline_lock.read_all("p").get("stages", {})
            self.assertEqual("grid" in stages, complete)
            self.assertEqual(run.call_args.args[0]["expected_input_fingerprint"], FAKE_FINGERPRINT)

    def test_frozen_fingerprint_mismatch_stops_before_running(self) -> None:
        config = {**self.CONFIG, "expected_input_fingerprint": {**FAKE_FINGERPRINT, "algo_sha256": "b" * 64}}
        with tempfile.TemporaryDirectory() as td, patch.object(pipeline_lock, "LOCKS_ROOT", Path(td)), \
                patch.object(facilitator, "input_fingerprint", return_value=FAKE_FINGERPRINT), \
                patch.object(facilitator, "run_experiment") as run:
            with self.assertRaises(facilitator.InputFingerprintMismatch):
                facilitator.run_grid(config)
        run.assert_not_called()


class WalkforwardHookTests(unittest.TestCase):
    CONFIG = {
        "experiment": "wf", "strategy": "combo", "strategy_profile": "combo-v1",
        "engine_profile": "ctrader-5.9.16-ticks-approxfx-v1",
        "symbols": ["US30.cash"], "timeframe": "h1",
        "start": "2024-01-01", "end": "2026-09-13",
        "parameter_space": {"KslLevel": ["0", "1"]},
    }

    def test_hooks_reach_train_and_incomplete_train_stops_without_lock(self) -> None:
        calls = []

        def fake_run(config, doc, profile, specs, *, engine, on_progress=None, should_cancel=None):
            calls.append((on_progress, should_cancel))
            return {"total": len(specs), "tally": {"ok": 1, facilitator.NOT_STARTED: len(specs) - 1},
                    "cancelled": True, "complete": False, "failed": 0, "inputs": None, "wall_seconds": 1.0}

        progress, cancel = MagicMock(), MagicMock(return_value=False)
        config = {**self.CONFIG, "pipeline": "p"}
        with tempfile.TemporaryDirectory() as td, patch.object(store, "RUNS_ROOT", Path(td) / "runs"), \
                patch.object(pipeline_lock, "LOCKS_ROOT", Path(td) / "locks"), \
                patch.object(facilitator, "input_fingerprint", return_value=FAKE_FINGERPRINT), \
                patch.object(walkforward, "run_experiment", side_effect=fake_run):
            result = walkforward.run_walkforward(config, on_progress=progress, should_cancel=cancel)
            stages = pipeline_lock.read_all("p").get("stages", {})
        self.assertEqual(calls, [(progress, cancel)])
        self.assertEqual(result["phase_stopped"], "train")
        self.assertFalse(result["complete"])
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["test"]["total"], 0)
        self.assertNotIn("walkforward", stages)


class SignalWindowHashTests(unittest.TestCase):
    def _sha(self, td: str, text: str) -> str:
        path = Path(td) / "s.csv"
        path.write_text(text, encoding="utf-8")
        return signal_trans.signal_window_sha256(path, date(2026, 2, 1))

    def test_only_rows_before_end_define_the_hash(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = self._sha(td, IN_WINDOW_CSV)
            self.assertEqual(base, self._sha(td, IN_WINDOW_CSV + "2026-02-01 00:00,1,1,1\n"))
            self.assertNotEqual(base, self._sha(td, IN_WINDOW_CSV + "2026-01-31 23:00,1,1,1\n"))
            self.assertNotEqual(base, self._sha(td, IN_WINDOW_CSV + "garbage,1,1,1\n"))


class TrialPolicyReadoutTests(unittest.TestCase):
    """[2026-09-25] readout.run_dsr()/run_pbo() nhận trial_policy — khoá lại
    đúng kịch bản báo cáo: report đầy đủ + margin rejection KHÔNG phải run
    hỏng, chỉ bị strict loại. Dùng ExperimentStore/report.json.gz THẬT (không
    mock selection/evidence) để tránh lặp lại lỗi "test qua nhưng thực tế
    sai" — chỉ mock hạ tầng CLI vì đây là hậu kỳ, không chạy backtest."""

    def _write_row(
        self, st: "store.ExperimentStore", tmp: Path, label: str, *,
        status: str = "ok", total_trades: int = 50,
        period_ok: bool = True, signal_ok: bool = True,
        margin_rejections: Any = 0, days: int = 3, start_balance: float = 100000.0,
    ) -> None:
        # RunSpec.label() chi doc KslLevel/KtpLevel/RiskPercent trong params —
        # dung KslLevel de moi row co label() rieng biet (khac "label" tu do,
        # bi label() bo qua hoan toan, khien moi row trung 1 chuoi va ghi de
        # nhau trong dict `trials`).
        spec = RunSpec(
            "combo", "combo-v1", "engine", "US30.cash", "h1",
            date(2026, 1, 1), date(2026, 1, 1 + days), start_balance, "ticks", {"KslLevel": label},
        )
        param_hash = st.param_hash(spec, label)
        claim = st.claim(spec, param_hash)
        report_path = tmp / f"{label}.json"
        balance = start_balance
        points = []
        for day in range(days):
            balance *= 1.01
            points.append({"time": f"2026-01-{day + 1:02d} 23:00:00", "balance": round(balance, 2)})
        report_path.write_text(json.dumps({"equity": {"points": points}}), encoding="utf-8")
        run_result = RunResult(
            status=RunStatus(status), failure_code=FailureCode.NONE if status == "ok" else FailureCode.NO_REPORT,
            reason="", exit_code=0, timed_out=False, wall_seconds=1.0,
            started_utc="2026-01-01T00:00:00+00:00", ended_utc="2026-01-01T00:00:01+00:00",
            cli_path="fake", cli_version="fake",
            report_json=report_path, cli_log=report_path, bot_log=report_path,
            cbotset=report_path,
        )
        flags = {
            "period_ok": period_ok, "signal_ok": signal_ok,
            "margin_rejections": margin_rejections,
        }
        st.record(
            claim=claim, spec=spec, param_hash=param_hash, run_result=run_result,
            metrics={"total_trades": total_trades}, flags=flags, bot_summary={}, environment={},
        )

    def _store(self, td: str) -> "store.ExperimentStore":
        st = store.ExperimentStore("grid", "exp")
        st.open({"schema": "bo-research-experiment/v1", "name": "exp"})
        return st

    def test_dsr_completed_execution_includes_margin_rejected_trial_dsr_report_case(self) -> None:
        """Tái hiện đúng ca dashboard_grid_5db0256b041ad863: 1 trial 'ok', report
        đủ, 1 lệnh bị NOT_ENOUGH_MARGIN_BALANCE — completed_execution phải
        dùng được, strict phải loại kèm lý do rõ."""
        with tempfile.TemporaryDirectory() as td, patch.object(store, "RUNS_ROOT", Path(td) / "runs"):
            st = self._store(td)
            self._write_row(st, Path(td), "clean_a")
            self._write_row(st, Path(td), "clean_b")
            self._write_row(st, Path(td), "margin", margin_rejections=1)
            self._write_row(st, Path(td), "failed", status="cli_error")

            strict = readout.run_dsr("grid", "exp", {"trial_policy": selection.STRICT_RESEARCH_POLICY})
            self.assertEqual(strict["included_trials"], 2)
            self.assertEqual(strict["excluded_trials"], 2)
            self.assertEqual(strict["total_rows"], 4)
            self.assertEqual(strict["margin_rejection_trials"], 1)
            self.assertEqual(strict["margin_rejection_events"], 1)
            margin_excl = next(item for item in strict["excluded"] if "margin" in item["label"])
            self.assertIn("margin_rejections", margin_excl["reasons"])
            failed_excl = next(item for item in strict["excluded"] if "failed" in item["label"])
            self.assertIn("execution_not_completed", failed_excl["reasons"])

            completed = readout.run_dsr("grid", "exp", {"trial_policy": selection.COMPLETED_EXECUTION_POLICY})
            self.assertEqual(completed["included_trials"], 3)
            self.assertEqual(completed["excluded_trials"], 1)
            self.assertEqual(completed["margin_rejection_trials"], 1)
            margin_excl_again = next(item for item in completed["excluded"] if "failed" in item["label"])
            # margin KHONG con la ly do bi loai duoi completed_execution — hang
            # duy nhat con bi loai la 'failed', khong phai 'margin'.
            self.assertFalse(any("margin" in item["label"] for item in completed["excluded"]))
            self.assertIn("execution_not_completed", margin_excl_again["reasons"])

    def test_pbo_reports_same_diagnostics_shape(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.object(store, "RUNS_ROOT", Path(td) / "runs"):
            st = self._store(td)
            self._write_row(st, Path(td), "a")
            self._write_row(st, Path(td), "b")
            self._write_row(st, Path(td), "c", margin_rejections=2)
            result = readout.run_pbo(
                "grid", "exp",
                {"trial_policy": selection.COMPLETED_EXECUTION_POLICY, "blocks": 2},
            )
            self.assertEqual(result["trial_policy"], selection.COMPLETED_EXECUTION_POLICY)
            self.assertEqual(result["included_trials"], 3)
            self.assertEqual(result["margin_rejection_events"], 2)
            self.assertEqual(result["excluded"], [])

    def test_unknown_trial_policy_raises_before_any_read(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.object(store, "RUNS_ROOT", Path(td) / "runs"):
            st = self._store(td)
            self._write_row(st, Path(td), "a")
            with self.assertRaisesRegex(ValueError, "unknown trial_policy"):
                readout.run_dsr("grid", "exp", {"trial_policy": "made_up"})
            with self.assertRaisesRegex(ValueError, "unknown trial_policy"):
                readout.run_pbo("grid", "exp", {"trial_policy": "made_up"})

    def test_cache_hash_differs_by_policy(self) -> None:
        with tempfile.TemporaryDirectory() as td, \
                patch.object(store, "RUNS_ROOT", Path(td) / "runs"), \
                patch.object(pipeline_lock, "LOCKS_ROOT", Path(td) / "locks"):
            st = self._store(td)
            self._write_row(st, Path(td), "a")
            self._write_row(st, Path(td), "b")
            self._write_row(st, Path(td), "c", margin_rejections=1)

            readout.run_dsr("grid", "exp", {"trial_policy": selection.STRICT_RESEARCH_POLICY}, pipeline="p")
            strict_hash = pipeline_lock.get_stage("p", "dsr")["deps_hash"]
            readout.run_dsr("grid", "exp", {"trial_policy": selection.COMPLETED_EXECUTION_POLICY}, pipeline="p")
            completed_hash = pipeline_lock.get_stage("p", "dsr")["deps_hash"]
            self.assertNotEqual(strict_hash, completed_hash)

            # Re-running strict now MISSES cache again (last recorded hash is
            # completed_execution's) but MUST recompute correctly, not reuse
            # completed_execution's cached numbers.
            rerun_strict = readout.run_dsr(
                "grid", "exp", {"trial_policy": selection.STRICT_RESEARCH_POLICY}, pipeline="p",
            )
            self.assertNotIn("cached", rerun_strict)
            self.assertEqual(rerun_strict["included_trials"], 2)

    def test_missing_trial_policy_defaults_to_strict_and_matches_bare_config_hash(self) -> None:
        with tempfile.TemporaryDirectory() as td, \
                patch.object(store, "RUNS_ROOT", Path(td) / "runs"), \
                patch.object(pipeline_lock, "LOCKS_ROOT", Path(td) / "locks"):
            st = self._store(td)
            self._write_row(st, Path(td), "a")
            self._write_row(st, Path(td), "b")
            readout.run_dsr("grid", "exp", {}, pipeline="p")
            implicit_hash = pipeline_lock.get_stage("p", "dsr")["deps_hash"]
            readout.run_dsr("grid", "exp", {"trial_policy": selection.STRICT_RESEARCH_POLICY}, pipeline="p")
            explicit_hash = pipeline_lock.get_stage("p", "dsr")["deps_hash"]
            self.assertEqual(implicit_hash, explicit_hash)


class DailyCliBudgetTests(unittest.TestCase):
    """[2026-09-25] Trần số lần spawn `ctrader-cli backtest`/ngày (người dùng
    chốt 1.500) — mỗi spawn là 1 lần đăng nhập server (tiền lệ hyperactivity
    FTMO 2026-09-15)."""

    def test_reserve_stops_at_limit_and_counts_today(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            protocol = store.ProtocolStore(Path(td) / "protocol.sqlite")
            self.assertEqual(protocol.cli_launches_today(), 0)
            self.assertTrue(protocol.reserve_cli_launch(experiment="e", label="a", limit=2))
            self.assertTrue(protocol.reserve_cli_launch(experiment="e", label="b", limit=2))
            self.assertFalse(protocol.reserve_cli_launch(experiment="e", label="c", limit=2))
            self.assertEqual(protocol.cli_launches_today(), 2)

    def test_launches_today_is_read_only_when_db_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "protocol.sqlite"
            self.assertEqual(store.ProtocolStore(path).cli_launches_today(), 0)
            self.assertFalse(path.exists())

    def test_default_limit_is_user_chosen_value(self) -> None:
        self.assertEqual(store.DAILY_CLI_LAUNCH_LIMIT, 1500)

    def test_exhausted_budget_stops_spawning_without_rows(self) -> None:
        protocol = MagicMock()
        protocol.reserve_cli_launch.side_effect = [True, False, False, False]
        ran = {"n": 0}

        def run_backtest(spec, **kwargs):
            ran["n"] += 1
            return _fake_result(kwargs["work_dir"])

        harness = RunExperimentLifecycleTests()
        with tempfile.TemporaryDirectory() as td:
            result, rows = harness._run(td, csv_text=IN_WINDOW_CSV, run_backtest=run_backtest, protocol=protocol)
        self.assertEqual(ran["n"], 1)
        self.assertEqual(result["tally"], {"ok": 1, facilitator.BUDGET_EXHAUSTED: 3})
        self.assertTrue(result["daily_cli_budget_exhausted"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["failed"], 0)
        self.assertEqual(len(rows), 1)

    def test_already_finished_specs_do_not_spend_budget(self) -> None:
        ok = lambda spec, **kw: _fake_result(kw["work_dir"])  # noqa: E731
        harness = RunExperimentLifecycleTests()
        with tempfile.TemporaryDirectory() as td:
            harness._run(td, csv_text=IN_WINDOW_CSV, run_backtest=ok)
            protocol = MagicMock()
            result, _ = harness._run(td, csv_text=IN_WINDOW_CSV, run_backtest=ok, protocol=protocol)
        self.assertEqual(result["tally"], {"skipped": 4})
        protocol.reserve_cli_launch.assert_not_called()


class ExperimentLockTests(unittest.TestCase):
    """[2026-09-25] experiment.json chỉ khoá theo field quyết định kết quả."""

    DOC = {
        "schema": "bo-research-experiment/v1", "name": "exp", "locked": True,
        "config": {"start": "2026-01-01", "end": "2026-02-01", "max_parallel": 10, "keep_logs": False},
    }

    def test_execution_only_fields_do_not_trip_the_lock(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.object(store, "RUNS_ROOT", Path(td) / "runs"):
            store.ExperimentStore("grid", "exp").open(self.DOC)
            resumed = {**self.DOC, "config": {**self.DOC["config"], "max_parallel": 6, "keep_logs": True,
                                              "expected_input_fingerprint": FAKE_FINGERPRINT}}
            store.ExperimentStore("grid", "exp").open(resumed)

    def test_result_changing_fields_still_trip_the_lock(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.object(store, "RUNS_ROOT", Path(td) / "runs"):
            store.ExperimentStore("grid", "exp").open(self.DOC)
            changed = {**self.DOC, "config": {**self.DOC["config"], "start": "2025-01-01"}}
            with self.assertRaisesRegex(RuntimeError, "locked"):
                store.ExperimentStore("grid", "exp").open(changed)


class WalkforwardReadoutTests(unittest.TestCase):
    """[2026-09-25] output_util/walkforward_readout.py — đọc kết quả walk-forward
    THUẦN ĐỌC, không kéo facilitator vào tầng hiển thị."""

    def test_module_does_not_import_the_executor(self) -> None:
        code = (
            "import sys; sys.path.insert(0, '.'); "
            "import core_engine.output_util.walkforward_readout; "
            "print('core_engine.facilitator' in sys.modules, 'core_engine.optimize.walkforward' in sys.modules)"
        )
        out = subprocess.run(
            [sys.executable, "-B", "-c", code], capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parents[1]), check=True,
        ).stdout.split()
        self.assertEqual(out, ["False", "False"])

    def test_walkforward_writes_with_the_readout_constants(self) -> None:
        from core_engine.output_util import walkforward_readout as wr
        pairs = walkforward.walkforward_windows({"start": "2024-01-01", "end": "2026-09-13"})
        self.assertEqual({pairs[0][0].zone, pairs[0][1].zone}, {wr.TRAIN_ZONE, wr.OOS_ZONE})

    def _row(self, st, *, window: str, zone: str, ksl: str, status: str = "ok", tags=None) -> str:
        spec = RunSpec("combo", "combo-v1", "engine", "US30.cash", "h1", date(2026, 1, 1), date(2026, 2, 1),
                       100000, "ticks", {"KslLevel": ksl}, zone=zone, window=window, tags=tags or {})
        param_hash = st.param_hash(spec, window, ksl, zone)
        claim = st.claim(spec, param_hash)
        if status != "running":
            db = sqlite3.connect(st.db_path)
            db.execute("UPDATE runs SET status=?, net_profit=? WHERE run_id=?", (status, 10.0, claim.run_id))
            db.commit()
            db.close()
        return claim.run_id

    def test_table_and_progress_from_a_real_store(self) -> None:
        from core_engine.output_util import walkforward_readout as wr
        with tempfile.TemporaryDirectory() as td, patch.object(store, "RUNS_ROOT", Path(td) / "runs"):
            self.assertEqual(wr.walkforward_table("wf"), [])
            self.assertFalse(wr.walkforward_progress("wf")["exists"])
            st = store.ExperimentStore(wr.METHOD, "wf")
            st.open({"schema": "bo-research-experiment/v1", "name": "wf"})
            train_run = self._row(st, window="wf01_is", zone=wr.TRAIN_ZONE, ksl="0")
            self._row(st, window="wf01_is", zone=wr.TRAIN_ZONE, ksl="1", status="running")
            progress = wr.walkforward_progress("wf")
            self.assertEqual(progress["train"]["rows"], 2)
            self.assertEqual(progress["train"]["running"], 1)
            self.assertIsNone(progress["selection"])

            st.write_sidecar(wr.SELECTION_SIDECAR, {"decisions": [
                {"symbol": "US30.cash", "timeframe": "h1", "rule": "best", "train_window": "wf01_is",
                 "test_window": "wf01_oos", "selected_params": {"KslLevel": "0"}, "train_run_id": train_run},
                {"symbol": "US30.cash", "timeframe": "h1", "rule": "plateau", "train_window": "wf01_is",
                 "test_window": "wf01_oos", "selected_params": None, "train_run_id": None},
            ]})
            self._row(st, window="wf01_oos", zone=wr.OOS_ZONE, ksl="0",
                      tags={"rule": "best", "train_window": "wf01_is"})
            progress = wr.walkforward_progress("wf")
            self.assertEqual(progress["oos"]["finished"], 1)
            self.assertEqual(progress["selection"], {"decisions": 2, "selected": 1, "without_pick": 1})
            table = wr.walkforward_table("wf")
            best = next(item for item in table if item["rule"] == "best")
            self.assertEqual(best["train_net_profit"], 10.0)
            self.assertEqual(best["test_status"], "ok")


class WalkforwardCacheKeyTests(unittest.TestCase):
    """[2026-09-25] objective và zones nằm trong khoá cache của walk-forward —
    đổi 1 trong 2 phải chạy lại, không được trả kết quả cũ."""

    CONFIG = {**WalkforwardHookTests.CONFIG, "pipeline": "p"}

    def _cache_missed(self, recorded_extra: dict, requested_extra: dict) -> bool:
        def fake_run(config, doc, profile, specs, *, engine, on_progress=None, should_cancel=None):
            return {"total": len(specs), "tally": {}, "cancelled": True, "complete": False,
                    "failed": 0, "inputs": None, "wall_seconds": 0.0}

        base = self.CONFIG
        recorded = {**base, **recorded_extra}
        requested = {**base, **requested_extra}
        with tempfile.TemporaryDirectory() as td, patch.object(store, "RUNS_ROOT", Path(td) / "runs"), \
                patch.object(pipeline_lock, "LOCKS_ROOT", Path(td) / "locks"), \
                patch.object(facilitator, "input_fingerprint", return_value=FAKE_FINGERPRINT), \
                patch.object(walkforward, "run_experiment", side_effect=fake_run) as run:
            deps = pipeline_lock.stage_hash(
                "walkforward", recorded.get("symbols"), recorded.get("timeframe"), recorded.get("strategy"),
                recorded.get("strategy_profile"), recorded.get("engine_profile"),
                recorded.get("start"), recorded.get("end"), recorded.get("balance"),
                recorded.get("parameter_space"), recorded.get("fixed_params"), recorded.get("walkforward"),
                recorded.get("selection_rules"), recorded.get("plateau_dimensions"),
                recorded.get("min_trades"), recorded.get("objective"), recorded.get("zones"), FAKE_FINGERPRINT,
            )
            pipeline_lock.record("p", "walkforward", deps, {"windows": 1})
            walkforward.run_walkforward(requested)
            return run.called

    def test_same_config_hits_cache(self) -> None:
        self.assertFalse(self._cache_missed({"objective": "net_profit"}, {"objective": "net_profit"}))

    def test_changed_objective_misses_cache(self) -> None:
        self.assertTrue(self._cache_missed({"objective": "net_profit"}, {"objective": "profit_factor"}))

    def test_changed_zones_misses_cache(self) -> None:
        zones = {"walkforward_range": ["2024-01-01", "2026-06-01"]}
        self.assertTrue(self._cache_missed({}, {"zones": zones}))


if __name__ == "__main__":
    unittest.main()
