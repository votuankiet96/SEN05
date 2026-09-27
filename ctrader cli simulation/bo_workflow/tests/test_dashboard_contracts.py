from __future__ import annotations

import socket
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

DASHBOARD_ROOT = Path(__file__).resolve().parents[1] / "dashboard"
BO_ROOT = DASHBOARD_ROOT.parent
for path in (DASHBOARD_ROOT, BO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import actions  # noqa: E402
import data_access  # noqa: E402
import session_store  # noqa: E402
import signal_sources  # noqa: E402


FINGERPRINT = {
    "algo_sha256": "a" * 64,
    "cli_sha256": "b" * 64,
    "signals": {"US30.cash|h1": "c" * 64},
}


def _grid_plan(*, key: str = "grid-key") -> dict:
    levels = [str(i) for i in range(10)]
    return {
        "schema": actions.GRID_SEARCH_SCHEMA,
        "title": "US30.cash - Combo - H1 - 100 combinations",
        "note": "",
        "execution_key": key,
        "pipeline": "dashboard_grid_source",
        "experiment": "dashboard_grid_source",
        "input": {
            "strategy": "combo",
            "strategy_profile": "combo-v1",
            "engine_profile": actions.DEFAULT_ENGINE_PROFILE,
            "symbol": "US30.cash",
            "timeframe": "h1",
            "start": "2025-01-01",
            "end": "2026-09-01",
            "balance": 100000.0,
            "fixed_params": {"RiskPercent": 0.5},
            "parameter_space": {"KslLevel": levels, "KtpLevel": levels},
            "pass_count": 100,
        },
        "command_config": {},
        "preflight": {},
    }


def _diagnostics_plan(source_id: str) -> dict:
    return {
        "schema": actions.GRID_DIAGNOSTICS_SCHEMA,
        "title": "DSR + PBO - source",
        "note": "",
        "execution_key": "diag-key",
        "pipeline": "dashboard_grid_source",
        "experiment": "dashboard_diagnostics_diag-key",
        "input": {"source_session_id": source_id},
        "command_config": {},
        "preflight": {},
    }


@contextmanager
def isolated_store():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        with patch.object(session_store, "STATE_ROOT", root), patch.object(
            session_store, "DB_PATH", root / "sessions.sqlite"
        ):
            session_store._SCHEMA_READY = False
            try:
                yield root
            finally:
                session_store._SCHEMA_READY = False


class DashboardPlanTests(unittest.TestCase):
    @patch.object(actions.facilitator, "input_fingerprint", return_value=FINGERPRINT)
    def test_signal_profile_is_frozen_and_separates_all_execution_keys(self, fingerprint) -> None:
        single = dict(symbol="US30.cash", timeframe="h1", strategy="combo",
                      start="2025-01-01", end="2026-09-01", balance=100000,
                      risk_percent=0.5, ksl_level=2, ktp_level=4)
        grid = dict(symbol="US30.cash", timeframe="h1", strategy="combo",
                    start="2025-01-01", end="2026-09-01", balance=100000,
                    risk_percent=0.5, max_parallel=4, ksl_levels=["1", "2"],
                    ktp_levels=["3", "4"])
        wf = dict(symbol="US30.cash", timeframe="h1", strategy="combo",
                  start="2025-01-01", end="2026-09-01", balance=100000,
                  risk_percent=0.5, max_parallel=4, is_months=12,
                  oos_months=3, step_months=3)
        for prepare, inputs in (
            (actions.prepare_single_backtest_session, single),
            (actions.prepare_grid_search_session, grid),
            (actions.prepare_walkforward_session, wf),
        ):
            with self.subTest(prepare=prepare.__name__):
                legacy = prepare(**inputs)
                original = prepare(**inputs, redis_profile=signal_sources.ORIGINAL)
                trend = prepare(**inputs, redis_profile=signal_sources.TREND_FILTERED)
                self.assertEqual(legacy["execution_key"], original["execution_key"])
                self.assertNotEqual(original["execution_key"], trend["execution_key"])
                self.assertEqual(original["input"]["redis_profile"], signal_sources.ORIGINAL)
                self.assertNotIn("redis_profile", original["command_config"])
                self.assertEqual(trend["input"]["redis_profile"], signal_sources.TREND_FILTERED)
                self.assertEqual(trend["command_config"]["redis_profile"], signal_sources.TREND_FILTERED)
                self.assertEqual(trend["command_config"]["expected_input_fingerprint"], FINGERPRINT)
        self.assertEqual(fingerprint.call_count, 9)

    @patch.object(actions.facilitator, "input_fingerprint", return_value=FINGERPRINT)
    def test_frozen_db3_profile_reaches_every_command_without_cli(self, _fingerprint) -> None:
        common = dict(symbol="US30.cash", timeframe="h1", strategy="combo",
                      start="2025-01-01", end="2026-09-01", balance=100000,
                      risk_percent=0.5, redis_profile=signal_sources.TREND_FILTERED)
        single = actions.prepare_single_backtest_session(
            **common, ksl_level=2, ktp_level=4,
        )
        grid = actions.prepare_grid_search_session(
            **common, max_parallel=4, ksl_levels=["1", "2"], ktp_levels=["3", "4"],
        )
        wf = actions.prepare_walkforward_session(
            **common, max_parallel=4, is_months=12, oos_months=3, step_months=3,
        )
        with patch.object(actions.facilitator, "run_grid", return_value={}) as run_grid:
            actions.run_single_backtest_session(single)
            actions.run_grid_search_session(grid)
        self.assertEqual(run_grid.call_count, 2)
        for call in run_grid.call_args_list:
            self.assertEqual(call.args[0]["redis_profile"], signal_sources.TREND_FILTERED)
        with patch.object(actions.walkforward, "run_walkforward", return_value={}) as run_wf:
            actions.run_walkforward_session(wf)
        self.assertEqual(run_wf.call_args.args[0]["redis_profile"], signal_sources.TREND_FILTERED)
        tampered = {**grid, "command_config": {**grid["command_config"], "redis_profile": signal_sources.ORIGINAL}}
        with self.assertRaisesRegex(ValueError, "signal source does not match"):
            actions.run_grid_search_session(tampered)

    def test_completed_execution_policy_is_not_assumed_on_an_older_core(self) -> None:
        with patch.object(actions.readout, "SUPPORTED_TRIAL_POLICIES", (), create=True):
            self.assertFalse(actions.completed_execution_policy_available())

    @patch.object(actions.facilitator, "input_fingerprint", return_value=FINGERPRINT)
    def test_grid_enum_order_is_canonical_and_fingerprint_is_frozen(self, _fingerprint) -> None:
        common = dict(
            symbol="US30.cash", timeframe="h1", strategy="combo",
            start="2025-01-01", end="2026-09-01", balance=100000,
            risk_percent=0.5, max_parallel=4,
        )
        first = actions.prepare_grid_search_session(
            **common, ksl_levels=["3", "1", "2"], ktp_levels=["5", "3", "4"],
        )
        second = actions.prepare_grid_search_session(
            **common, ksl_levels=["1", "2", "3"], ktp_levels=["3", "4", "5"],
        )
        self.assertEqual(first["execution_key"], second["execution_key"])
        self.assertEqual(first["input"]["parameter_space"]["KslLevel"], ["1", "2", "3"])
        self.assertEqual(first["input"]["parameter_space"]["KtpLevel"], ["3", "4", "5"])
        self.assertEqual(first["input"]["input_fingerprint"], FINGERPRINT)
        self.assertEqual(first["command_config"]["expected_input_fingerprint"], FINGERPRINT)
        self.assertNotIn("parity_certified", first["command_config"])

    def test_changed_input_fingerprint_changes_grid_identity(self) -> None:
        changed = {
            **FINGERPRINT,
            "algo_sha256": "d" * 64,
        }
        common = dict(
            symbol="US30.cash", timeframe="h1", strategy="combo",
            start="2025-01-01", end="2026-09-01", balance=100000,
            risk_percent=0.5, max_parallel=4,
            ksl_levels=["1", "2"], ktp_levels=["3", "4"],
        )
        with patch.object(
            actions.facilitator,
            "input_fingerprint",
            side_effect=[FINGERPRINT, changed],
        ):
            first = actions.prepare_grid_search_session(**common)
            second = actions.prepare_grid_search_session(**common)

        self.assertNotEqual(first["execution_key"], second["execution_key"])
        self.assertNotEqual(first["experiment"], second["experiment"])

    def test_diagnostics_requires_every_source_trial_to_complete_execution(self) -> None:
        source = {
            "session_id": "source",
            "kind": "grid_search",
            "state": "completed",
            "execution_key": "grid-key",
            "pipeline": "dashboard_grid_source",
            "experiment": "dashboard_grid_source",
            "title": "Full Grid",
            "plan": _grid_plan(),
        }
        with self.assertRaisesRegex(ValueError, "90/100 completed valid executions"):
            actions.prepare_grid_diagnostics_session(
                source_session=source,
                source_snapshot={
                    "row_count": 100,
                    "completed_execution_count": 90,
                    "eligible_count": 90,
                    "artifact_sha256": "d" * 64,
                },
            )

    def test_diagnostics_plan_preserves_source_balance_and_pipeline(self) -> None:
        source = {
            "session_id": "source",
            "kind": "grid_search",
            "state": "completed",
            "execution_key": "grid-key",
            "pipeline": "dashboard_grid_source",
            "experiment": "dashboard_grid_source",
            "title": "Full Grid",
            "plan": _grid_plan(),
        }
        plan = actions.prepare_grid_diagnostics_session(
            source_session=source,
            source_snapshot={
                "row_count": 100,
                "completed_execution_count": 100,
                "eligible_count": 90,
                "margin_rejection_trial_count": 10,
                "margin_rejection_event_count": 10,
                "artifact_sha256": "d" * 64,
            },
            min_trades=20,
        )
        self.assertEqual(plan["pipeline"], source["pipeline"])
        self.assertEqual(plan["input"]["initial_balance"], 100000.0)
        self.assertEqual(plan["input"]["blocks"], 16)
        self.assertEqual(plan["input"]["min_trades"], 20)
        self.assertEqual(
            plan["input"]["trial_policy"], actions.COMPLETED_EXECUTION_TRIAL_POLICY,
        )
        self.assertEqual(plan["input"]["margin_rejection_trials"], 10)

    @patch.object(actions.facilitator, "input_fingerprint", return_value=FINGERPRINT)
    def test_walkforward_plan_is_full_range_and_independent(self, _fingerprint) -> None:
        plan = actions.prepare_walkforward_session(
            symbol="US30.cash", timeframe="h1", strategy="combo",
            start="2025-01-01", end="2026-09-01", balance=100000,
            risk_percent=0.5, max_parallel=10, is_months=12, oos_months=3, step_months=3,
        )
        self.assertEqual(plan["schema"], actions.WALKFORWARD_SCHEMA)
        self.assertTrue(plan["pipeline"].startswith("dashboard_walkforward_"))
        self.assertEqual(plan["pipeline"], plan["experiment"])
        self.assertEqual(len(plan["input"]["parameter_space"]["KslLevel"]), 10)
        self.assertEqual(len(plan["input"]["parameter_space"]["KtpLevel"]), 10)
        self.assertEqual(plan["input"]["protocol"]["min_trades"], 30)
        self.assertNotIn("source_session_id", plan["input"])
        self.assertNotIn("zones", plan["command_config"])
        self.assertNotIn("pipeline_stage", plan["command_config"])
        self.assertNotIn("parity_certified", plan["command_config"])

    def test_walkforward_fingerprint_changes_identity_and_zero_windows_rejects(self) -> None:
        changed = {**FINGERPRINT, "algo_sha256": "d" * 64}
        common = dict(symbol="US30.cash", timeframe="h1", strategy="combo", start="2025-01-01", end="2026-09-01", balance=100000, risk_percent=0.5, max_parallel=10, is_months=12, oos_months=3, step_months=3)
        with patch.object(actions.facilitator, "input_fingerprint", side_effect=[FINGERPRINT, changed]):
            first = actions.prepare_walkforward_session(**common)
            second = actions.prepare_walkforward_session(**common)
        self.assertNotEqual(first["execution_key"], second["execution_key"])
        with self.assertRaisesRegex(ValueError, "does not produce"):
            with patch.object(actions.facilitator, "input_fingerprint", return_value=FINGERPRINT):
                actions.prepare_walkforward_session(**{**common, "start": "2026-01-01", "end": "2026-03-01"})


class DashboardSessionStoreTests(unittest.TestCase):
    def test_signal_setting_persists_and_rejects_stale_form(self) -> None:
        with isolated_store():
            self.assertEqual(session_store.get_signal_profile(), signal_sources.ORIGINAL)
            session_store.set_signal_profile(signal_sources.TREND_FILTERED)
            self.assertEqual(session_store.get_signal_profile(), signal_sources.TREND_FILTERED)
            self.assertEqual(session_store.confirmed_signal_profile(signal_sources.TREND_FILTERED), signal_sources.TREND_FILTERED)
            with self.assertRaisesRegex(ValueError, "changed in another tab"):
                session_store.confirmed_signal_profile(signal_sources.ORIGINAL)
            with self.assertRaisesRegex(ValueError, "Unsupported signal source"):
                session_store.set_signal_profile("not-a-profile")

    def test_signal_history_and_walkforward_preflight_read_selected_redis_key(self) -> None:
        observed: list[tuple[int, str]] = []

        class Client:
            def __init__(self, db: int):
                self.db = db

            def lrange(self, key: str, _start: int, _end: int):
                observed.append((self.db, key))
                return ["2025-01-02 00:00:00", "2025-02-02 00:00:00"]

            def close(self):
                pass

        profiles = {
            signal_sources.ORIGINAL: SimpleNamespace(db=2, key_prefix="L_PastSignal"),
            signal_sources.TREND_FILTERED: SimpleNamespace(db=3, key_prefix="L_PastSignal_Trend"),
        }
        with patch.object(data_access, "load_redis_profile", side_effect=profiles.get), patch.object(
            data_access.signal_trans, "redis_client", side_effect=lambda profile: Client(profile.db)
        ):
            original = data_access.signal_date_bounds("US30.cash", "h1", "combo")
            trend = data_access.signal_date_bounds(
                "US30.cash", "h1", "combo", redis_profile=signal_sources.TREND_FILTERED
            )
            windows = [(SimpleNamespace(start=date(2025, 1, 1), end=date(2025, 3, 1)), None)]
            counts = data_access.walkforward_train_signal_counts(
                "US30.cash", "h1", "combo", windows,
                redis_profile=signal_sources.TREND_FILTERED,
            )
        self.assertTrue(original["available"])
        self.assertTrue(trend["available"])
        self.assertEqual(counts, [2])
        self.assertEqual([db for db, _key in observed], [2, 3, 3])
        self.assertTrue(observed[0][1].startswith("L_PastSignal_"))
        self.assertTrue(observed[1][1].startswith("L_PastSignal_Trend_"))
    def test_resource_groups_do_not_block_each_other(self) -> None:
        with isolated_store():
            grid = session_store.create_grid_search(_grid_plan())
            diagnostics = session_store.create_grid_diagnostics(
                _diagnostics_plan(grid["session_id"])
            )
            claimed_grid = session_store.claim_run(grid["session_id"])
            claimed_diagnostics = session_store.claim_run(diagnostics["session_id"])
            self.assertEqual(claimed_grid["resource_group"], "ctrader_cli")
            self.assertEqual(claimed_diagnostics["resource_group"], "analysis")

    def test_walkforward_uses_the_cli_resource_group(self) -> None:
        with isolated_store():
            plan = {
                "schema": actions.WALKFORWARD_SCHEMA, "title": "Walk-forward", "note": "",
                "execution_key": "wf-key", "pipeline": "dashboard_walkforward_wf-key",
                "experiment": "dashboard_walkforward_wf-key", "input": {}, "command_config": {}, "preflight": {},
            }
            wf = session_store.create_walkforward(plan)
            grid = session_store.create_grid_search(_grid_plan())
            session_store.claim_run(wf["session_id"])
            with self.assertRaises(session_store.ActiveSessionError):
                session_store.claim_run(grid["session_id"])

    def test_dead_local_owner_is_reconciled_as_abandoned(self) -> None:
        with isolated_store():
            grid = session_store.create_grid_search(_grid_plan())
            db = sqlite3.connect(session_store.DB_PATH)
            try:
                db.execute(
                    """UPDATE sessions SET state='running',owner_host=?,owner_pid=?
                       WHERE session_id=?""",
                    (socket.gethostname(), 2_000_000_000, grid["session_id"]),
                )
                db.commit()
            finally:
                db.close()
            reconciled = session_store.reconcile_abandoned_sessions()
            current = session_store.get(grid["session_id"])
            self.assertEqual(reconciled, [grid["session_id"]])
            self.assertEqual(current["state"], "failed")
            self.assertIn("abandoned", current["error_text"])

    def test_source_delete_is_blocked_while_dependent_session_exists(self) -> None:
        with isolated_store():
            grid = session_store.create_grid_search(_grid_plan())
            session_store.create_grid_diagnostics(_diagnostics_plan(grid["session_id"]))
            with self.assertRaisesRegex(ValueError, "dependent dashboard sessions"):
                session_store.delete_sessions([grid["session_id"]])

    def test_incomplete_core_result_stays_retryable_and_preserves_result(self) -> None:
        with isolated_store():
            grid = session_store.create_grid_search(_grid_plan())
            session_store.claim_run(grid["session_id"])
            finished = session_store.complete(
                grid["session_id"],
                {
                    "complete": False,
                    "cancelled": False,
                    "failed": 1,
                    "tally": {"ok": 99, "cli_error": 1},
                },
            )
            self.assertEqual(finished["state"], "failed")
            self.assertEqual(finished["result"]["failed"], 1)

    def test_fail_does_not_rewrite_an_unclaimed_draft(self) -> None:
        with isolated_store():
            grid = session_store.create_grid_search(_grid_plan())
            current = session_store.fail(grid["session_id"], "pre-claim failure")
            self.assertEqual(current["state"], "draft")


if __name__ == "__main__":
    unittest.main()
