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
import ui_common  # noqa: E402
from single import callbacks as single_callbacks  # noqa: E402
from single import views as single_views  # noqa: E402
from multichannel import views as multichannel_views  # noqa: E402


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
            "strategy_profile": "backtestbot-v1",
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
    def test_backtestbot_profile_drives_all_three_form_strategies_and_defaults(self) -> None:
        options = data_access.session_form_options()
        strategies = {item["value"]: item for item in options["strategies"]}
        self.assertEqual(set(strategies), {"combo", "ma_cross", "macd_break"})
        self.assertEqual(strategies["combo"]["label"], "Combo")
        self.assertEqual(strategies["combo"]["timeframes"], ["h1", "h2", "h3", "h4"])
        self.assertEqual(strategies["ma_cross"]["label"], "MA Cross")
        self.assertEqual(strategies["ma_cross"]["timeframes"], ["m10", "m20", "m30", "m45"])
        self.assertEqual(strategies["macd_break"]["label"], "MACD Break")
        self.assertEqual(
            strategies["macd_break"]["timeframes"],
            ["h1", "h2", "h3", "h4", "m10", "m20", "m30", "m45"],
        )
        for option in strategies.values():
            self.assertEqual(option["default_ksl"], "4")
            self.assertEqual(option["default_ktp"], "4")
            self.assertEqual(option["defaults"]["RiskPercent"], "0.2")

    @patch.object(actions, "_multi_channel_preflight")
    def test_multi_channel_plan_is_shared_with_one_run_spec_per_default_period(self, preflight) -> None:
        preflight.return_value = {
            "channel_count": 3, "period_count": 17, "total_count": 17,
            "existing_count": 2, "existing_local_count": 1, "existing_sql_count": 1,
            "new_count": 15, "cli_required": 15, "cli_used": 100,
            "cli_limit": 2000, "cli_remaining": 1900, "budget_ok": True,
            "cells": [{"run_key": f"key-{index}", "window": f"p{index}"} for index in range(17)],
            "input_fingerprint": FINGERPRINT,
        }
        channels = [
            {"strategy": "combo", "symbol": "US30.cash", "timeframe": "h1", "risk_percent": 0.6},
            {"strategy": "combo", "symbol": "US30.cash", "timeframe": "h2", "risk_percent": 0.4},
            {"strategy": "macd_break", "symbol": "XAUUSD", "timeframe": "m45", "risk_percent": 0.8},
        ]
        plan = actions.prepare_multi_channel_session(
            channels=channels, custom_period=False, start=None, end=None, balance=100000,
            ksl_level=4, ktp_level=4,
            leg_count=3, leg2_ktp=5, leg3_ktp=6,
            enable_daily_loss_limit=True, max_daily_loss_percent=5,
            enable_max_drawdown=True, max_total_drawdown_percent=10,
            enable_max_consecutive_losses=True, max_consecutive_losses=5,
            risk_allocation_mode="manual", total_risk_percent=2.0,
        )
        command = plan["command_config"]
        self.assertEqual(plan["schema"], actions.MULTI_CHANNEL_SCHEMA)
        self.assertEqual(command["strategy_profile"], "backtestbot-v1")
        self.assertEqual(command["channels"], channels)
        self.assertEqual(command["account_mode"], "shared")
        self.assertEqual(command["period_group"], "from2022")
        self.assertEqual(command["engine_profile"], "ctrader-ticks-precisefx-v1")
        self.assertTrue(command["name"].startswith("dashboard_mc_"))
        self.assertLessEqual(len(command["name"]), 25)
        self.assertEqual(plan["input"]["channel_count"], 3)
        self.assertEqual(plan["input"]["risk_allocation"], {
            "mode": "manual", "total_percent": 2.0, "channel_count": 3,
        })
        self.assertEqual(command["risk_allocation"], plan["input"]["risk_allocation"])
        self.assertEqual(plan["preflight"]["risk_used_percent"], 1.8)
        self.assertEqual(plan["preflight"]["total_count"], 17)
        self.assertNotIn("parameter_space", command)
        self.assertEqual(command["max_parallel"], 16)
        self.assertEqual(command["fixed_params"]["LegCount"], "3")
        self.assertEqual(command["fixed_params"]["EnableMaxDrawdown"], "True")

        from core_engine.configuration import load_engine_profile, load_strategy_profile, with_signal_overrides
        from core_engine.planner import expand_grid

        selected_profile = with_signal_overrides(load_strategy_profile("backtestbot-v1"), command)
        specs = expand_grid(command, selected_profile, load_engine_profile(command["engine_profile"]))
        self.assertEqual(len(specs), 17)
        self.assertTrue(all(len(spec.channels) == 3 for spec in specs))

        rendered = multichannel_views.detail(
            {
                "session_id": "multi", "kind": "multi_channel_backtest", "state": "draft",
                "title": plan["title"], "experiment": plan["experiment"], "plan": plan,
            },
            {}, [], data_access.session_form_options(),
        )
        rendered_text = " | ".join(_texts(rendered))
        self.assertIn("17", rendered_text)
        self.assertIn("ctrader-ticks-precisefx-v1", rendered_text)

        result_payload = {"total": 17, "failed": 0, "tally": {"known_in_sql": 17}}
        with patch.object(actions.facilitator, "run_backtest", return_value=result_payload) as run_backtest:
            result = actions.run_multi_channel_session(plan)
        self.assertTrue(result["complete"])
        self.assertTrue(result["cached"])
        run_backtest.assert_called_once()
        self.assertEqual(run_backtest.call_args.args[0]["account_mode"], "shared")

    def test_equal_risk_acceptance_examples_use_core_floor_allocation(self) -> None:
        five_channels = [
            {"strategy": "combo", "symbol": "HK50.cash", "timeframe": "h3", "risk_percent": 9},
            {"strategy": "macd_break", "symbol": "HK50.cash", "timeframe": "m45", "risk_percent": 9},
            {"strategy": "macd_break", "symbol": "HK50.cash", "timeframe": "h2", "risk_percent": 9},
            {"strategy": "combo", "symbol": "US30.cash", "timeframe": "h4", "risk_percent": 9},
            {"strategy": "macd_break", "symbol": "JP225.cash", "timeframe": "h2", "risk_percent": 9},
        ]
        allocated = actions.allocate_multi_channel_risk(
            five_channels, mode="equal", total_percent=2,
        )
        self.assertEqual([row["risk_percent"] for row in allocated["channels"]], [0.4] * 5)
        self.assertEqual(allocated["risk_used_percent"], 2.0)

        two = actions.allocate_multi_channel_risk(
            five_channels[:2], mode="equal", total_percent=1.5,
        )
        self.assertEqual([row["risk_percent"] for row in two["channels"]], [0.75, 0.75])
        three = actions.allocate_multi_channel_risk(
            five_channels[:3], mode="equal", total_percent=2,
        )
        self.assertEqual([row["risk_percent"] for row in three["channels"]], [0.6666] * 3)
        self.assertLessEqual(three["risk_used_percent"], 2.0)

    def test_manual_risk_above_limit_cannot_create_a_plan(self) -> None:
        with self.assertRaisesRegex(ValueError, "above the total limit"):
            actions.prepare_multi_channel_session(
                channels=[
                    {"strategy": "combo", "symbol": "US30.cash", "timeframe": "h1", "risk_percent": 1.1},
                    {"strategy": "combo", "symbol": "US30.cash", "timeframe": "h2", "risk_percent": 1.0},
                ],
                custom_period=False, start=None, end=None, balance=100000,
                ksl_level=4, ktp_level=4, leg_count=3, leg2_ktp=5, leg3_ktp=6,
                enable_daily_loss_limit=True, max_daily_loss_percent=5,
                enable_max_drawdown=True, max_total_drawdown_percent=10,
                enable_max_consecutive_losses=True, max_consecutive_losses=5,
                risk_allocation_mode="manual", total_risk_percent=2.0,
            )

    def test_risk_allocation_metadata_does_not_enter_preflight_run_identity(self) -> None:
        seen_configs = []

        def preflight(config):
            self.assertNotIn("risk_allocation", config)
            seen_configs.append({
                "name": config["name"], "channels": list(config["channels"]),
                "fixed_params": dict(config["fixed_params"]),
            })
            return {
                "channel_count": 2, "period_count": 1, "total_count": 1,
                "existing_count": 0, "existing_local_count": 0, "existing_sql_count": 0,
                "new_count": 1, "cli_required": 1, "cli_used": 0,
                "cli_limit": 2000, "cli_remaining": 2000, "budget_ok": True,
                "cells": [{"run_key": "same-content-run-key", "window": "custom"}],
                "input_fingerprint": FINGERPRINT,
            }

        common = dict(
            channels=[
                {"strategy": "combo", "symbol": "US30.cash", "timeframe": "h1", "risk_percent": 1.0},
                {"strategy": "combo", "symbol": "US30.cash", "timeframe": "h2", "risk_percent": 1.0},
            ],
            custom_period=True, start="2025-01-01", end="2025-06-30", balance=100000,
            ksl_level=4, ktp_level=4, leg_count=3, leg2_ktp=5, leg3_ktp=6,
            enable_daily_loss_limit=True, max_daily_loss_percent=5,
            enable_max_drawdown=True, max_total_drawdown_percent=10,
            enable_max_consecutive_losses=True, max_consecutive_losses=5,
            total_risk_percent=2.0,
        )
        with patch.object(actions, "_multi_channel_preflight", side_effect=preflight):
            equal = actions.prepare_multi_channel_session(
                **common, risk_allocation_mode="equal",
            )
            manual = actions.prepare_multi_channel_session(
                **common, risk_allocation_mode="manual",
            )
        self.assertEqual(seen_configs[0], seen_configs[1])
        self.assertEqual(equal["preflight"]["cells"][0]["run_key"], manual["preflight"]["cells"][0]["run_key"])
        self.assertEqual(equal["command_config"]["risk_allocation"]["mode"], "equal")
        self.assertEqual(manual["command_config"]["risk_allocation"]["mode"], "manual")

    def test_multi_channel_rows_reject_exact_duplicates(self) -> None:
        with self.assertRaisesRegex(ValueError, "Duplicate channel"):
            actions.prepare_multi_channel_session(
                channels=[
                    {"strategy": "combo", "symbol": "US30.cash", "timeframe": "h1", "risk_percent": 0.6},
                    {"strategy": "combo", "symbol": "US30.cash", "timeframe": "H1", "risk_percent": 0.8},
                ], custom_period=True, start="2025-01-01", end="2026-09-01", balance=100000,
                ksl_level=4, ktp_level=4,
                leg_count=1, leg2_ktp=5, leg3_ktp=6,
                enable_daily_loss_limit=False, max_daily_loss_percent=5,
                enable_max_drawdown=False, max_total_drawdown_percent=10,
                enable_max_consecutive_losses=False, max_consecutive_losses=5,
            )

    @patch.object(actions, "_multi_channel_preflight")
    def test_multi_channel_row_can_pick_its_own_signal_source(self, preflight) -> None:
        preflight.return_value = {
            "channel_count": 3, "period_count": 17, "total_count": 17,
            "existing_count": 0, "existing_local_count": 0, "existing_sql_count": 0,
            "new_count": 17, "cli_required": 17, "cli_used": 0,
            "cli_limit": 2000, "cli_remaining": 2000, "budget_ok": True,
            "cells": [{"run_key": f"key-{index}", "window": f"p{index}"} for index in range(17)],
            "input_fingerprint": FINGERPRINT,
        }
        channels = [
            {"strategy": "combo", "symbol": "US30.cash", "timeframe": "h1", "risk_percent": 0.6},
            {"strategy": "macd_break", "symbol": "HK50.cash", "timeframe": "h2", "risk_percent": 0.6, "source": signal_sources.KNN},
            {"strategy": "ma_cross", "symbol": "XAUUSD", "timeframe": "m45", "risk_percent": 0.6, "source": signal_sources.BREAKOUT},
        ]
        common = dict(
            custom_period=False, start=None, end=None, balance=100000, ksl_level=4, ktp_level=4,
            leg_count=3, leg2_ktp=5, leg3_ktp=6,
            enable_daily_loss_limit=False, max_daily_loss_percent=5,
            enable_max_drawdown=False, max_total_drawdown_percent=10,
            enable_max_consecutive_losses=False, max_consecutive_losses=5,
            risk_allocation_mode="manual", total_risk_percent=2.0,
        )
        plan = actions.prepare_multi_channel_session(channels=channels, **common)
        self.assertEqual([row.get("source") for row in plan["command_config"]["channels"]],
                         [None, signal_sources.KNN, signal_sources.BREAKOUT])
        self.assertEqual(plan["command_config"]["redis_profile"], signal_sources.ORIGINAL)
        # A row that names the page source explicitly is the same plan as one that follows the page.
        explicit = [{**channels[0], "source": signal_sources.ORIGINAL}, *channels[1:]]
        again = actions.prepare_multi_channel_session(channels=explicit, **common)
        self.assertEqual(again["execution_key"], plan["execution_key"])
        # Another source for the same row is another plan.
        other = [channels[0], {**channels[1], "source": signal_sources.BREAKOUT}, channels[2]]
        self.assertNotEqual(actions.prepare_multi_channel_session(channels=other, **common)["execution_key"], plan["execution_key"])

        from core_engine.configuration import load_engine_profile, load_strategy_profile, with_signal_overrides
        from core_engine.planner import expand_grid

        command = plan["command_config"]
        specs = expand_grid(
            command, with_signal_overrides(load_strategy_profile("backtestbot-v1"), command),
            load_engine_profile(command["engine_profile"]),
        )
        self.assertEqual([c.source for c in specs[0].channels],
                         [signal_sources.ORIGINAL, signal_sources.KNN, signal_sources.BREAKOUT])
        rendered = multichannel_views.detail(
            {"session_id": "multi", "kind": "multi_channel_backtest", "state": "draft",
             "title": plan["title"], "experiment": plan["experiment"], "plan": plan},
            {}, [], data_access.session_form_options(),
        )
        from dash.development.base_component import Component

        def tables(node):
            if isinstance(node, (list, tuple)):
                for item in node:
                    yield from tables(item)
            elif isinstance(node, Component):
                if type(node).__name__ == "DataTable":
                    yield node
                yield from tables(getattr(node, "children", None))

        shown = [row["source"] for table in tables(rendered) for row in table.data if "risk_percent" in row]
        self.assertEqual(shown, ["RAW (db0)", "KNN (db1)", "Breakout (db2)"])

    def test_multi_channel_row_with_an_unsupported_source_or_a_duplicate_across_sources_is_rejected(self) -> None:
        common = dict(
            custom_period=False, start=None, end=None, balance=100000, ksl_level=4, ktp_level=4,
            leg_count=3, leg2_ktp=5, leg3_ktp=6,
            enable_daily_loss_limit=False, max_daily_loss_percent=5,
            enable_max_drawdown=False, max_total_drawdown_percent=10,
            enable_max_consecutive_losses=False, max_consecutive_losses=5,
            risk_allocation_mode="manual", total_risk_percent=2.0,
        )
        row = {"strategy": "combo", "symbol": "US30.cash", "timeframe": "h1", "risk_percent": 0.6}
        with self.assertRaisesRegex(ValueError, "unsupported signal source"):
            actions.prepare_multi_channel_session(channels=[{**row, "source": signal_sources.LEGACY_TREND}], **common)
        with self.assertRaisesRegex(ValueError, "Duplicate channel"):
            actions.prepare_multi_channel_session(channels=[row, {**row, "source": signal_sources.KNN}], **common)

    def test_multi_channel_redis_bounds_read_each_row_from_its_own_source(self) -> None:
        rows = [
            {"strategy": "combo", "symbol": "US30.cash", "timeframe": "h1", "risk_percent": 0.6},
            {"strategy": "macd_break", "symbol": "HK50.cash", "timeframe": "h2", "risk_percent": 0.6, "source": signal_sources.KNN},
        ]
        with patch.object(
            data_access, "signal_date_bounds",
            return_value={"available": True, "earliest_date": "2025-01-01", "latest_date": "2026-09-01"},
        ) as bounds:
            result = data_access.multi_channel_rows_date_bounds(rows, redis_profile=signal_sources.BREAKOUT)
        self.assertTrue(result["available"])
        self.assertEqual([call.kwargs["redis_profile"] for call in bounds.call_args_list],
                         [signal_sources.BREAKOUT, signal_sources.KNN])

    def test_multi_channel_redis_preview_uses_only_compatible_channels(self) -> None:
        with patch.object(
            data_access, "signal_date_bounds",
            return_value={"available": True, "earliest_date": "2025-01-01", "latest_date": "2026-09-01"},
        ) as bounds:
            result = data_access.multi_channel_signal_date_bounds(
                ["combo", "ma_cross"], ["US30.cash"], ["h1", "m10"],
            )
        self.assertTrue(result["available"])
        self.assertEqual(result["channel_count"], 2)
        self.assertEqual(
            [(call.args[2], call.args[1]) for call in bounds.call_args_list],
            [("combo", "h1"), ("ma_cross", "m10")],
        )
        self.assertEqual(result["strategy_frames"], {"combo": ["H1"], "ma_cross": ["M10"]})

    def test_multi_channel_contribution_groups_legs_and_keeps_unknown_labels(self) -> None:
        channels = [
            {"strategy": "combo", "symbol": "US30.cash", "timeframe": "h4", "risk_percent": 0.6},
            {"strategy": "macd_break", "symbol": "XAUUSD", "timeframe": "m45", "risk_percent": 0.6},
        ]
        rows = data_access.multi_channel_contributions(channels, [
            {"leg_label": "cb_US30_H4_L1", "net_profit": 120},
            {"leg_label": "cb_US30_H4_L2", "net_profit": -30},
            {"leg_label": "mb_XAUUSD_M45_L3", "net_profit": 80},
            {"leg_label": "legacy-label", "net_profit": -10},
        ], 3)
        self.assertEqual(rows[0]["net_contribution"], 90)
        self.assertEqual(rows[0]["total_profit"], 120)
        self.assertEqual(rows[0]["total_loss"], 30)
        self.assertEqual(rows[1]["net_contribution"], 80)
        self.assertEqual(rows[2]["strategy"], "Unknown")
        self.assertEqual(rows[2]["total_loss"], 10)

    def test_multi_channel_zero_trade_warning_is_visible(self) -> None:
        channels = [
            {"strategy": "combo", "symbol": "US30.cash", "timeframe": "h4", "risk_percent": 0.4},
            {"strategy": "macd_break", "symbol": "XAUUSD", "timeframe": "m45", "risk_percent": 0.4},
        ]
        rows = data_access.multi_channel_contributions(channels, [
            {"leg_label": "cb_US30_H4_L1", "net_profit": 25},
        ], 3)
        warning = "No orders placed — risk per leg may be below the broker minimum volume"
        self.assertEqual(rows[0]["warning"], "")
        self.assertEqual(rows[1]["warning"], warning)
        rendered = multichannel_views.contribution_detail({
            "contribution_available": True, "contributions": rows,
        })
        self.assertIn(warning, " | ".join(_texts(rendered)))

    def test_backtestbot_order_and_expired_signal_logs_are_normalised(self) -> None:
        pending = data_access._normalise_bot_event(
            "BacktestBot: bartime=2026-01-02 03:00, executed=2026-01-02 03:00:01, "
            "pending cb H3 Buy placed at 42123.5; SL=42000, TP=42300; "
            "volume=1.00 units (0.0100 lots); valid until 2026-01-02 12:00:00; "
            "leg 1/3; symbol US30.cash."
        )
        market = data_access._normalise_bot_event(
            "BacktestBot: bartime=2026-01-02 03:00, executed=2026-01-02 03:00:01, "
            "market mc M45 Sell placed; SL=12.5 pips, TP=20.0 pips; "
            "volume=1.00 units (0.0100 lots); leg 2/3; symbol US30.cash."
        )
        rejected = data_access._normalise_bot_event(
            "BacktestBot: bartime=2026-01-02 03:00, market mb H1 Buy was rejected: "
            "NoMoney (leg 1/1, symbol US30.cash)."
        )
        expired = data_access._normalise_bot_event(
            "BacktestBot: bartime=2026-01-02 03:00, mb H1 Buy signal skipped: validity "
            "window ended 2026-01-02 02:00:00 before it could be placed (symbol US30.cash)."
        )

        self.assertEqual(pending["event"], "Pending order placed — Buy")
        self.assertIn("valid until 2026-01-02 12:00:00", pending["detail"])
        self.assertEqual(market["event"], "Market order placed — Sell")
        self.assertIn("mc M45", market["detail"])
        self.assertEqual(rejected["event"], "Market order rejected — Buy")
        self.assertEqual(rejected["tone"], "neutral")
        self.assertEqual(expired["event"], "Signal skipped")
        self.assertIn("Validity window ended", expired["detail"])

    @patch.object(actions.facilitator, "input_fingerprint", return_value=FINGERPRINT)
    def test_single_signal_channel_overrides_allow_strategy_frames(self, _fingerprint) -> None:
        combo = actions.prepare_grid_search_session(
            symbol="US30.cash", timeframe="h4", strategy="combo",
            start="2025-01-01", end="2026-09-01", balance=100000,
            risk_percent=0.2, max_parallel=1, ksl_levels=["4"], ktp_levels=["4"],
        )
        macross = actions.prepare_single_backtest_session(
            symbol="US30.cash", timeframe="m45", strategy="ma_cross",
            start="2025-01-01", end="2026-09-01", balance=100000,
            risk_percent=0.2, ksl_level=4, ktp_level=4,
        )
        for plan, strategy, profile, timeframe in (
            (combo, "combo", "backtestbot-v1", "H4"),
            (macross, "ma_cross", "backtestbot-v1", "M45"),
        ):
            command = plan["command_config"]
            self.assertEqual(command["strategy"], strategy)
            self.assertEqual(command["strategy_profile"], profile)
            self.assertEqual(command["signal_strategies"], [strategy])
            self.assertEqual(command["signal_symbols"], ["US30.cash"])
            self.assertEqual(command["signal_timeframes"], [timeframe])

    @patch.object(actions.facilitator, "input_fingerprint", return_value=FINGERPRINT)
    def test_macd_break_prepares_single_grid_and_walkforward(self, _fingerprint) -> None:
        common = dict(
            symbol="US30.cash", timeframe="m45", strategy="macd_break",
            start="2025-01-01", end="2026-09-01", balance=100000,
            risk_percent=0.2,
        )
        plans = (
            actions.prepare_single_backtest_session(**common, ksl_level=4, ktp_level=4),
            actions.prepare_grid_search_session(
                **common, max_parallel=1, ksl_levels=["4"], ktp_levels=["4"],
            ),
            actions.prepare_walkforward_session(
                **common, max_parallel=1, is_months=12, oos_months=3, step_months=3,
            ),
        )
        for plan in plans:
            command = plan["command_config"]
            self.assertEqual(command["strategy"], "macd_break")
            self.assertEqual(command["strategy_profile"], "backtestbot-v1")
            self.assertEqual(command["signal_strategies"], ["macd_break"])
            self.assertEqual(command["signal_symbols"], ["US30.cash"])
            self.assertEqual(command["signal_timeframes"], ["M45"])

    def test_removed_profile_results_are_labelled_and_new_analysis_is_rejected_readably(self) -> None:
        from grid import views as grid_views
        from walkforward import views as wf_views

        options = data_access.session_form_options()
        self.assertEqual(ui_common.strategy_label("combomulti", options), "ComboMulti (profile removed)")
        legacy_session = {
            "session_id": "legacy", "kind": "grid_search", "state": "completed",
            "title": "Legacy Combo Grid", "experiment": "legacy-experiment",
            "plan": {"input": {"strategy": "combomulti", "strategy_profile": "combomulti-v1",
                               "symbol": "US30.cash", "timeframe": "h1", "fixed_params": {}},
                     "preflight": {}},
        }
        rendered = (
            single_views.detail(legacy_session, {}, [], options),
            grid_views.detail(legacy_session, {}, options),
            wf_views.detail(legacy_session, {}, [], options),
        )
        for detail in rendered:
            self.assertIn("ComboMulti (profile removed)", " | ".join(_texts(detail)))

        legacy = _grid_plan()
        legacy["input"] = {
            **legacy["input"], "strategy": "combomulti", "strategy_profile": "combomulti-v1",
        }
        source = {
            "session_id": "legacy", "kind": "grid_search", "state": "completed",
            "execution_key": "legacy-key", "pipeline": "legacy-pipeline",
            "experiment": "legacy-experiment", "title": "Legacy Combo Grid", "plan": legacy,
        }
        with self.assertRaisesRegex(ValueError, "profile 'combomulti-v1' has been removed"):
            actions.prepare_grid_diagnostics_session(
                source_session=source,
                source_snapshot={"row_count": 100, "completed_execution_count": 100,
                                 "eligible_count": 100, "artifact_sha256": "d" * 64},
            )

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
                trend = prepare(**inputs, redis_profile=signal_sources.KNN)
                self.assertEqual(legacy["execution_key"], original["execution_key"])
                self.assertNotEqual(original["execution_key"], trend["execution_key"])
                self.assertEqual(original["input"]["redis_profile"], signal_sources.ORIGINAL)
                self.assertNotIn("redis_profile", original["command_config"])
                self.assertEqual(trend["input"]["redis_profile"], signal_sources.KNN)
                self.assertEqual(trend["command_config"]["redis_profile"], signal_sources.KNN)
                self.assertEqual(trend["command_config"]["expected_input_fingerprint"], FINGERPRINT)
                self.assertEqual(trend["command_config"]["signal_strategies"], ["combo"])
                self.assertEqual(trend["command_config"]["signal_symbols"], ["US30.cash"])
                self.assertEqual(trend["command_config"]["signal_timeframes"], ["H1"])
        self.assertEqual(fingerprint.call_count, 9)

    @patch.object(actions.facilitator, "input_fingerprint", return_value=FINGERPRINT)
    def test_frozen_db3_profile_reaches_every_command_without_cli(self, _fingerprint) -> None:
        common = dict(symbol="US30.cash", timeframe="h1", strategy="combo",
                      start="2025-01-01", end="2026-09-01", balance=100000,
                      risk_percent=0.5, redis_profile=signal_sources.KNN)
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
            self.assertEqual(call.args[0]["redis_profile"], signal_sources.KNN)
        with patch.object(actions.walkforward, "run_walkforward", return_value={}) as run_wf:
            actions.run_walkforward_session(wf)
        self.assertEqual(run_wf.call_args.args[0]["redis_profile"], signal_sources.KNN)
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
        self.assertEqual(plan["input"]["protocol"]["min_trades"], 0)
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
    def test_multi_channel_draft_has_its_own_kind_and_cli_resource_group(self) -> None:
        plan = {
            "schema": actions.MULTI_CHANNEL_SCHEMA,
            "title": "Multi-channel test",
            "note": "",
            "execution_key": "multi-key",
            "pipeline": "dashboard_multichannel_test",
            "experiment": "dashboard_multichannel_test",
            "input": {"channel_count": 3},
            "command_config": {},
            "preflight": {},
        }
        with isolated_store():
            session = session_store.create_multi_channel_backtest(plan)
            self.assertEqual(session["kind"], "multi_channel_backtest")
            self.assertEqual(session["resource_group"], "ctrader_cli")
            self.assertEqual(
                session_store.list_sessions(kind="multi_channel_backtest")[0]["session_id"],
                session["session_id"],
            )

    def test_signal_setting_persists_and_rejects_stale_form(self) -> None:
        with isolated_store():
            self.assertEqual(session_store.get_signal_profile(), signal_sources.ORIGINAL)
            session_store.set_signal_profile(signal_sources.KNN)
            self.assertEqual(session_store.get_signal_profile(), signal_sources.KNN)
            self.assertEqual(session_store.confirmed_signal_profile(signal_sources.KNN), signal_sources.KNN)
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
            signal_sources.KNN: SimpleNamespace(db=3, key_prefix="L_PastSignal_Knn"),
        }
        with patch.object(data_access, "load_redis_profile", side_effect=profiles.get), patch.object(
            data_access.signal_trans, "redis_client", side_effect=lambda profile: Client(profile.db)
        ):
            original = data_access.signal_date_bounds("US30.cash", "h1", "combo")
            trend = data_access.signal_date_bounds(
                "US30.cash", "h1", "combo", redis_profile=signal_sources.KNN
            )
            windows = [(SimpleNamespace(start=date(2025, 1, 1), end=date(2025, 3, 1)), None)]
            counts = data_access.walkforward_train_signal_counts(
                "US30.cash", "h1", "combo", windows,
                redis_profile=signal_sources.KNN,
            )
        self.assertTrue(original["available"])
        self.assertTrue(trend["available"])
        self.assertEqual(counts, [2])
        self.assertEqual([db for db, _key in observed], [2, 3, 3])
        self.assertTrue(observed[0][1].startswith("L_PastSignal_"))
        self.assertTrue(observed[1][1].startswith("L_PastSignal_Knn_"))

    def test_signal_sources_are_the_three_fixed_ones_and_the_retired_trend_profile_only_keeps_a_label(self) -> None:
        self.assertEqual(list(signal_sources.LABELS), [signal_sources.ORIGINAL, signal_sources.KNN, signal_sources.BREAKOUT])
        for profile in (signal_sources.KNN, signal_sources.BREAKOUT):
            self.assertEqual(signal_sources.command_field(profile), {"redis_profile": profile})
            self.assertEqual(signal_sources.identity_field(profile), {"redis_profile": profile})
        self.assertEqual(signal_sources.command_field(signal_sources.ORIGINAL), {})
        with self.assertRaises(ValueError):
            signal_sources.require_profile(signal_sources.LEGACY_TREND)
        self.assertEqual(
            signal_sources.label_from_input({"redis_profile": signal_sources.LEGACY_TREND}), "Trend (legacy db1)",
        )
        self.assertEqual(signal_sources.label_from_input({}), "RAW (db0)")

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


class SingleBacktestControlsTests(unittest.TestCase):
    def test_refresh_reselects_active_session_when_value_was_lost(self) -> None:
        active = {"session_id": "active", "title": "Running", "state": "running", "created_utc": "2026-09-27T00:00:00Z", "plan": {"input": {}}}
        with patch.object(single_callbacks, "ctx", SimpleNamespace(triggered_id="refresh-session-view-btn")), patch.object(
            session_store, "list_sessions", return_value=[active]
        ):
            _options, selected = single_callbacks.refresh_session_options(1, None, None, 0, [], [])
        self.assertEqual(selected, ["active"])

    def test_status_poll_only_republishes_selector_when_state_changes(self) -> None:
        draft = {"session_id": "active", "title": "Run", "state": "draft", "created_utc": "2026-09-27T00:00:00Z", "plan": {"input": {}}}
        running = {**draft, "state": "running"}
        old_data = single_views.select_data([draft])
        with patch.object(single_callbacks, "ctx", SimpleNamespace(triggered_id="session-progress-poll")), patch.object(
            session_store, "list_sessions", return_value=[draft]
        ):
            data, selected = single_callbacks.refresh_session_options(0, None, None, 1, ["active"], old_data)
        self.assertIs(data, single_callbacks.no_update)
        self.assertIs(selected, single_callbacks.no_update)
        with patch.object(single_callbacks, "ctx", SimpleNamespace(triggered_id="session-progress-poll")), patch.object(
            session_store, "list_sessions", return_value=[running]
        ):
            data, selected = single_callbacks.refresh_session_options(0, None, None, 2, ["active"], old_data)
        self.assertIn("running", data[0]["label"])
        self.assertIs(selected, single_callbacks.no_update)

    def test_active_progress_and_stop_do_not_depend_on_dropdown_selection(self) -> None:
        active = {"session_id": "active", "title": "Running", "state": "running", "progress_pct": 47.5,
                  "progress_text": "Backtesting", "cancel_requested_utc": None}
        with patch.object(single_callbacks, "ctx", SimpleNamespace(triggered_id="refresh-session-view-btn")), patch.object(
            single_callbacks, "_selected_sessions", return_value=[]
        ), patch.object(session_store, "active_session", return_value=active), patch.object(
            session_store, "request_cancel", return_value=active
        ) as cancel:
            progress, _label, stop_disabled, _delete_disabled, poll_disabled, active_card = (
                single_callbacks.refresh_selected_run_status(0, [], None, 1, None, False)
            )
            single_callbacks.stop_selected_session(1)
        self.assertEqual(progress, 47.5)
        self.assertFalse(stop_disabled)
        self.assertFalse(poll_disabled)
        self.assertIsNotNone(active_card)
        cancel.assert_called_once_with("active")

    def test_zero_percent_active_run_explains_wait_and_elapsed(self) -> None:
        active = {"session_id": "active", "title": "UK100", "state": "running", "progress_pct": 0,
                  "progress_text": "Backtesting", "started_utc": "2026-09-27T00:00:00+00:00",
                  "updated_utc": "2026-09-27T00:00:30+00:00"}
        with patch.object(single_callbacks, "ctx", SimpleNamespace(triggered_id="refresh-session-view-btn")), patch.object(
            single_callbacks, "_selected_sessions", return_value=[]
        ), patch.object(session_store, "active_session", return_value=active):
            _progress, label, _stop, _delete, _poll, card = single_callbacks.refresh_selected_run_status(
                0, [], None, 1, None, False,
            )
        self.assertIn("waiting for a CLI percentage", label)
        self.assertIsNotNone(card)

    def test_detail_does_not_duplicate_the_saved_sessions_progress_bar(self) -> None:
        from dash.development.base_component import Component
        import dash_mantine_components as dmc

        active = {"session_id": "active", "title": "Running", "state": "running", "plan": {"input": {}, "preflight": {}}, "progress_pct": 47.5}

        def descendants(node):
            if isinstance(node, Component):
                yield node
                yield from descendants(getattr(node, "children", None))
            elif isinstance(node, (list, tuple)):
                for item in node:
                    yield from descendants(item)

        detail = single_views.detail(active, {}, [])
        self.assertFalse(any(isinstance(item, dmc.Progress) for item in descendants(detail)))


class _FakeApp:
    """Collect callbacks registered through ``register(app, ...)`` for direct calls."""

    def __init__(self) -> None:
        self.functions: dict[str, object] = {}

    def callback(self, *_args, **_kwargs):
        def decorator(function):
            self.functions[function.__name__] = function
            return function
        return decorator


def _grid_row(ksl: str, ktp: str, net: float, status: str = "ok") -> dict:
    return {"params": {"KslLevel": ksl, "KtpLevel": ktp}, "status": status, "net_profit": net,
            "profit_factor_resolved": 1.2, "total_trades": 40, "win_rate": 0.5,
            "max_equity_drawdown_pct": 3.0, "wall_seconds": 12.5}


class AuditRemediationTests(unittest.TestCase):
    """Behaviours fixed after the 2026-09-27 dashboard audit (see research sources in code comments)."""

    options = data_access.session_form_options()
    grid_input = {"strategy": "combo", "parameter_space": {"KslLevel": ["0", "1"], "KtpLevel": ["0", "1"]}}

    def test_heatmap_is_centred_on_zero_colour_blind_safe_and_marks_excluded_cells(self) -> None:
        from grid import views as grid_views
        eligible = [_grid_row("0", "0", -300.0), _grid_row("1", "1", 9_000.0)]
        excluded = _grid_row("0", "1", 50.0, status="ok")
        figure = grid_views.heatmap_figure(eligible, [*eligible, excluded], self.grid_input, self.options)
        heatmap = figure.data[0]
        self.assertEqual(heatmap.zmid, 0)
        self.assertNotIn("RdYlGn", str(heatmap.colorscale))
        hover = [text for row in heatmap.customdata for text in row]
        self.assertTrue(any("excluded by the strict research filter" in text for text in hover))
        self.assertTrue(any("Not run yet" in text for text in hover))
        self.assertEqual(list(figure.data[1].text), ["✕"])

    def test_leaderboard_keeps_numbers_numeric_and_colours_losses_red(self) -> None:
        from grid import views as grid_views
        table = grid_views.leaderboard_table(
            [_grid_row("0", "0", 500.0), _grid_row("1", "1", -250.0)], self.grid_input, self.options,
        )
        columns = {column["id"]: column for column in table.columns}
        self.assertEqual(columns["net_profit"]["type"], "numeric")
        self.assertEqual(table.data[1]["net_profit"], -250.0)
        self.assertEqual(table.sort_action, "native")
        rules = {rule["if"].get("filter_query"): rule["color"] for rule in table.style_data_conditional}
        self.assertEqual(rules["{net_profit} < 0"], "#dc2626")
        self.assertFalse(any("row_index" in rule["if"] for rule in table.style_data_conditional))

    def test_walkforward_result_table_sorts_numbers_not_text(self) -> None:
        from walkforward import views as wf_views
        rows = [
            {"test_window": "w1", "rule": "best", "test_status": "ok", "test_net_profit": 9.0},
            {"test_window": "w2", "rule": "best", "test_status": "ok", "test_net_profit": 100.0},
        ]
        table = wf_views.result_table(rows, self.options, "net_profit")
        columns = {column["id"]: column for column in table.columns}
        self.assertEqual(columns["oos_net_profit"]["type"], "numeric")
        self.assertEqual([row["oos_net_profit"] for row in table.data], [9.0, 100.0])

    def test_m4_aggregation_bounds_points_and_keeps_extremes(self) -> None:
        count = 20_000
        times = [float(i * 60_000) for i in range(count)]
        values = [float((i * 7919) % 1000) for i in range(count)]
        values[12_345] = -5_000.0  # the deepest drawdown must survive
        keep = single_views._m4_indices(times, values, 1_200)
        self.assertLessEqual(len(keep), 4 * 1_200)
        self.assertIn(12_345, keep)
        self.assertEqual(single_views._m4_indices(times[:100], values[:100], 1_200), list(range(100)))

    def test_equity_chart_formats_in_plotly_without_adding_zoom(self) -> None:
        # User requirement: synchronised hover only between the two panels,
        # no zoom/pan surface (this chart is for inspection, not editing).
        points = [{"timestamp": 1_767_225_600_000 + i * 3_600_000, "balance": 10_000.239 + i,
                   "minEquity": 9_990 + i, "maxEquity": 10_010 + i} for i in range(50)]
        figure = single_views._equity_figure(points)
        self.assertIsNone(figure.data[0].customdata)
        self.assertIn("%{y", figure.data[0].hovertemplate)
        self.assertTrue(figure.layout.xaxis.fixedrange)
        self.assertTrue(figure.layout.yaxis.fixedrange)
        self.assertTrue(figure.layout.xaxis2.fixedrange)
        self.assertFalse(figure.layout.xaxis.rangeselector.buttons)
        # Plotted value is cut (10000.23), not rounded (10000.24).
        self.assertEqual(figure.data[0].y[0], 10_000.23)

    def test_display_values_are_cut_toward_zero_not_rounded(self) -> None:
        from grid import views as grid_views
        from walkforward import views as wf_views
        self.assertEqual(ui_common.truncated(1.239), 1.23)
        self.assertEqual(ui_common.truncated(-1.239), -1.23)
        self.assertIsNone(ui_common.truncated(None))
        self.assertEqual(ui_common.truncated(7, 0), 7.0)

        rows = [_grid_row("0", "0", 42_095.678)]
        figure = grid_views.heatmap_figure(rows, rows, self.grid_input, self.options)
        self.assertEqual(figure.data[0].z[0][0], 42_095.0)  # not 42096 (rounded)

        table = grid_views.leaderboard_table(
            [_grid_row("0", "0", 500.239, )], self.grid_input, self.options,
        )
        self.assertEqual(table.data[0]["net_profit"], 500.23)

        wf_rows = [{"test_window": "w1", "rule": "best", "test_status": "ok", "test_net_profit": 9.239}]
        wf_table = wf_views.result_table(wf_rows, self.options, "net_profit")
        self.assertEqual(wf_table.data[0]["oos_net_profit"], 9.23)

    def test_history_table_discloses_display_limit(self) -> None:
        history = [{"id": i, "net": 1.0} for i in range(single_views.HISTORY_DISPLAY_LIMIT + 5)]
        badge = single_views._history_table(history).children.children[0].children[1]
        self.assertIn(f"of {single_views.HISTORY_DISPLAY_LIMIT + 5:,}", badge.children)

    def test_bot_log_is_paged_filtered_and_sorted_on_the_server(self) -> None:
        events = tuple(
            {"time": f"01/01/2026 00:00:{i:02d}", "level": "Info", "detail": f"line {i}", "tone": "neutral"}
            for i in range(45)
        )
        with patch.object(data_access, "single_bot_events", return_value=events), patch.object(
            single_callbacks, "ctx", SimpleNamespace(triggered_prop_ids={"session-bot-activity-table.page_current": 1})
        ):
            page, page_count, current = single_callbacks.page_bot_activity(2, [], "", 20, {"experiment": "x"})
        self.assertEqual((page_count, current, len(page)), (3, 2, 5))
        with patch.object(data_access, "single_bot_events", return_value=events), patch.object(
            single_callbacks, "ctx", SimpleNamespace(triggered_prop_ids={"session-bot-activity-table.sort_by": 1})
        ):
            page, _count, current = single_callbacks.page_bot_activity(
                2, [{"column_id": "time", "direction": "desc"}], "{detail} contains line 1", 20, {"experiment": "x"},
            )
        self.assertEqual(current, 0)  # a new sort or filter restarts at the first page
        self.assertEqual(page[0]["detail"], "line 19")
        self.assertTrue(all("line 1" in row["detail"] for row in page))
        # "ne " inside the value is part of the value, not a "not equal" operator.
        self.assertEqual(single_callbacks._split_filter_part("{detail} contains line 1")[:3],
                         ("detail", "contains", "line 1"))
        self.assertEqual(single_callbacks._split_filter_part('{level} icontains "TRADE"'),
                         ("level", "contains", "TRADE", True))

    def test_grid_status_stops_polling_for_a_finished_grid(self) -> None:
        from grid import callbacks as grid_callbacks
        app = _FakeApp()
        grid_callbacks.register(app, self.options)
        status = app.functions["refresh_grid_status"]
        finished = {"session_id": "g", "kind": "grid_search", "state": "completed", "progress_pct": 100,
                    "progress_text": "Completed", "experiment": "g-exp", "plan": {"input": {"pass_count": 4}}}
        with patch.object(grid_callbacks, "ctx", SimpleNamespace(triggered_id="grid-progress-poll")), \
                patch.object(session_store, "get", return_value=finished):
            self.assertTrue(status(1, None, "g", None, {"active": False})[4])
        running = {**finished, "state": "running", "started_utc": "2026-09-27T00:00:00+00:00"}
        with patch.object(grid_callbacks, "ctx", SimpleNamespace(triggered_id="grid-progress-poll")), \
                patch.object(session_store, "get", return_value=running), \
                patch.object(data_access, "grid_execution_status", return_value={"finished": 1, "running": 1}):
            self.assertFalse(status(2, None, "g", None, {"active": False})[4])

    def test_diagnostics_tick_neither_rebuilds_a_running_detail_nor_takes_a_write_lock(self) -> None:
        from dsr_pbo import callbacks as dsr_callbacks
        app = _FakeApp()
        dsr_callbacks.register(app)
        running = {"session_id": "d", "kind": "dsr_pbo", "state": "running", "progress_pct": 40,
                   "progress_text": "PBO", "plan": {"input": {}}}
        tick = SimpleNamespace(triggered_id="diagnostics-progress-poll")
        with patch.object(dsr_callbacks, "ctx", tick), patch.object(session_store, "get", return_value=running), \
                patch.object(session_store, "reconcile_abandoned_sessions") as reconcile:
            self.assertIs(app.functions["refresh_detail"](None, "d", 3), dsr_callbacks.no_update)
            app.functions["refresh_status"](3, "d", None, None, None)
        reconcile.assert_not_called()

    @patch.object(actions.readout, "SUPPORTED_TRIAL_POLICIES", (actions.COMPLETED_EXECUTION_TRIAL_POLICY,), create=True)
    def test_pbo_progress_is_published_between_35_and_100_percent(self) -> None:
        def fake_pbo(*_args, on_progress=None, **_kwargs):
            for done in (1, 2, 4):
                on_progress(done, 4)
            return {"pbo": 0.4}

        plan = {"schema": actions.GRID_DIAGNOSTICS_SCHEMA, "command_config": {
            "method": "grid", "source_experiment": "src", "pipeline": "pipe", "blocks": 4}}
        seen: list[float] = []
        with patch.object(actions.readout, "run_dsr", return_value={"dsr": 0.5, "included_trials": 3}), \
                patch.object(actions.readout, "run_pbo", side_effect=fake_pbo), \
                patch.object(actions.pipeline_lock, "get_stage", return_value=None):
            actions.run_grid_diagnostics_session(plan, on_progress=lambda pct, _text: seen.append(pct))
        self.assertEqual(seen[:2], [5, 35])
        self.assertEqual(seen[-2:], [100, 100])
        self.assertEqual(seen, sorted(seen))

    def test_history_estimate_uses_only_matching_successful_runs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = root / "grid" / "exp" / "index.sqlite"
            db_path.parent.mkdir(parents=True)
            db = sqlite3.connect(db_path)
            db.execute("""CREATE TABLE runs (status TEXT, strategy TEXT, symbol TEXT, timeframe TEXT,
                          data_mode TEXT, wall_seconds REAL, start_date TEXT, end_date TEXT)""")
            db.executemany("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)", [
                ("ok", "combo", "US30.cash", "h1", "ticks", 100.0, "2025-01-01", "2025-01-11"),
                ("ok", "combo", "US30.cash", "h1", "ticks", 300.0, "2025-01-01", "2025-01-11"),
                ("cli_error", "combo", "US30.cash", "h1", "ticks", 9_999.0, "2025-01-01", "2025-01-11"),
                ("ok", "combo", "HK50.cash", "h1", "ticks", 9_999.0, "2025-01-01", "2025-01-11"),
            ])
            db.commit()
            db.close()
            with patch.object(data_access, "RUNS_ROOT", root):
                found = data_access.historical_seconds_per_day(
                    strategy="combo", symbol="US30.cash", timeframe="h1", data_mode="ticks")
                missing = data_access.historical_seconds_per_day(
                    strategy="combo", symbol="GER40.cash", timeframe="h1", data_mode="ticks")
        self.assertEqual((found["samples"], found["median_seconds_per_day"]), (2, 20.0))
        self.assertFalse(missing["available"])

    def test_session_store_uses_wal_and_prefers_finished_diagnostics(self) -> None:
        with isolated_store():
            grid = session_store.create_grid_search(_grid_plan())
            first = session_store.create_grid_diagnostics(_diagnostics_plan(grid["session_id"]))
            second = session_store.create_grid_diagnostics(
                {**_diagnostics_plan(grid["session_id"]), "execution_key": "diag-key-2"})
            db = sqlite3.connect(session_store.DB_PATH)
            try:
                self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                db.execute("UPDATE sessions SET state='completed' WHERE session_id=?", (first["session_id"],))
                db.commit()
            finally:
                db.close()
            self.assertEqual(session_store.latest_diagnostics(grid["session_id"])["session_id"], first["session_id"])
            self.assertNotEqual(first["session_id"], second["session_id"])

    def test_live_process_created_after_the_claim_is_not_the_owner(self) -> None:
        import subprocess
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertTrue(session_store._process_alive(child.pid, "2999-01-01T00:00:00+00:00"))
            self.assertFalse(session_store._process_alive(child.pid, "2000-01-01T00:00:00+00:00"))
        finally:
            child.kill()
            child.wait()


class LiveStatusDuringRunTests(unittest.TestCase):
    """[2026-09-30] A run's live status must reach the page without a hard refresh.

    Root cause fixed: status/detail callbacks took the background job's output
    (`*-run-message`) as an Input, and dash-renderer does not run a callback
    while one of its Inputs is the output of a callback still in flight, so the
    whole panel froze until the job ended (0 progress requests measured in a
    real browser). The structural rule is enforced app-wide in
    test_dashboard_app_composition; these tests pin the polling rules that
    replace it: any trigger may switch polling on, only a tick may switch it off.
    """

    options = data_access.session_form_options()

    def test_polling_can_only_be_switched_off_by_a_tick(self) -> None:
        self.assertIs(ui_common.poll_disabled(True, False), False)
        self.assertIs(ui_common.poll_disabled(True, True), False)
        self.assertIs(ui_common.poll_disabled(False, True), True)
        self.assertIs(ui_common.poll_disabled(False, False), ui_common.no_update)

    def test_single_job_end_leaves_the_last_tick_to_draw_the_result(self) -> None:
        finished = {"session_id": "s", "title": "Run", "state": "completed", "progress_pct": 100,
                    "progress_text": "Completed"}
        # The background `running` flag clearing (run button re-enabled) is not a tick.
        with patch.object(single_callbacks, "ctx", SimpleNamespace(triggered_id="run-session-btn")), \
                patch.object(single_callbacks, "_selected_sessions", return_value=[finished]), \
                patch.object(session_store, "active_session", return_value=None):
            poll = single_callbacks.refresh_selected_run_status(3, ["s"], 1, None, None, False)[4]
        self.assertIs(poll, single_callbacks.no_update)
        with patch.object(single_callbacks, "ctx", SimpleNamespace(triggered_id="session-progress-poll")), \
                patch.object(single_callbacks, "_selected_sessions", return_value=[finished]), \
                patch.object(session_store, "active_session", return_value=None):
            poll = single_callbacks.refresh_selected_run_status(4, ["s"], 1, None, None, False)[4]
        self.assertIs(poll, True)

    def test_grid_start_flag_clearing_does_not_stop_the_ticker(self) -> None:
        from grid import callbacks as grid_callbacks
        app = _FakeApp()
        grid_callbacks.register(app, self.options)
        finished = {"session_id": "g", "kind": "grid_search", "state": "completed", "progress_pct": 100,
                    "progress_text": "Completed", "experiment": "g-exp", "plan": {"input": {"pass_count": 4}}}
        with patch.object(grid_callbacks, "ctx", SimpleNamespace(triggered_id="grid-run-state")), \
                patch.object(session_store, "get", return_value=finished):
            poll = app.functions["refresh_grid_status"](5, None, "g", 1, {"active": False})[4]
        self.assertIs(poll, grid_callbacks.no_update)

    def test_walkforward_detail_redraws_on_a_tick_only_when_the_state_changed(self) -> None:
        from walkforward import callbacks as wf_callbacks
        app = _FakeApp()
        wf_callbacks.register(app, self.options)
        detail = app.functions["refresh_detail"]
        running = {"session_id": "w", "kind": "walkforward", "state": "running", "experiment": "w-exp"}
        finished = {**running, "state": "completed"}
        tick = SimpleNamespace(triggered_id="wf-progress-poll")
        with patch.object(wf_callbacks, "ctx", tick), patch.object(session_store, "get", return_value=running), \
                patch.object(wf_callbacks.views, "detail", return_value="DETAIL") as draw:
            self.assertEqual(detail(None, "w", 7, ["w", "running"]), (wf_callbacks.no_update, wf_callbacks.no_update))
            draw.assert_not_called()
        with patch.object(wf_callbacks, "ctx", tick), patch.object(session_store, "get", return_value=finished), \
                patch.object(data_access, "walkforward_execution_status", return_value={}), \
                patch.object(data_access, "walkforward_results", return_value=[]), \
                patch.object(wf_callbacks.views, "detail", return_value="DETAIL"):
            self.assertEqual(detail(None, "w", 8, ["w", "running"]), ("DETAIL", ["w", "completed"]))

    def test_walkforward_status_keeps_the_ticker_until_a_tick_sees_the_end(self) -> None:
        from walkforward import callbacks as wf_callbacks
        app = _FakeApp()
        wf_callbacks.register(app, self.options)
        status = app.functions["refresh_status"]
        finished = {"session_id": "w", "kind": "walkforward", "state": "completed", "experiment": "w-exp"}
        with patch.object(session_store, "get", return_value=finished), \
                patch.object(session_store, "active_session", return_value=None):
            with patch.object(wf_callbacks, "ctx", SimpleNamespace(triggered_id="wf-run-state")):
                self.assertIs(status(9, None, "w", {"active": False})[4], wf_callbacks.no_update)
            with patch.object(wf_callbacks, "ctx", SimpleNamespace(triggered_id="wf-progress-poll")):
                self.assertIs(status(10, None, "w", {"active": False})[4], True)

    def test_diagnostics_rerun_of_a_failed_session_keeps_polling_until_claimed(self) -> None:
        from dsr_pbo import callbacks as dsr_callbacks
        app = _FakeApp()
        dsr_callbacks.register(app)
        status = app.functions["refresh_status"]
        failed = {"session_id": "d", "kind": "dsr_pbo", "state": "failed", "progress_pct": 0,
                  "progress_text": "Failed", "plan": {"input": {}}}
        tick = SimpleNamespace(triggered_id="diagnostics-progress-poll")
        with patch.object(dsr_callbacks, "ctx", tick), patch.object(session_store, "get", return_value=failed):
            # The worker has not claimed the session yet; the running flag keeps the ticker on.
            self.assertIs(status(1, "d", 1, None, True)[4], False)
            # The run ended (flag cleared) and the session is terminal: the tick switches it off.
            self.assertIs(status(2, "d", 1, None, False)[4], True)


def _texts(node) -> list[str]:
    """Every string rendered anywhere in a Dash component tree."""
    from dash.development.base_component import Component
    if isinstance(node, str):
        return [node]
    if isinstance(node, (list, tuple)):
        return [text for item in node for text in _texts(item)]
    if isinstance(node, Component):
        found = []
        for name in ("children", "title", "label", "description"):
            found.extend(_texts(getattr(node, name, None)))
        return found
    return []


class MethodAuditRemediationTests(unittest.TestCase):
    """Behaviours fixed after the 2026-09-28 core methods audit."""

    SOURCE = {
        "session_id": "source", "kind": "grid_search", "state": "completed", "execution_key": "grid-key",
        "pipeline": "dashboard_grid_source", "experiment": "dashboard_grid_source", "title": "Full Grid",
        "plan": _grid_plan(),
    }
    SNAPSHOT = {"row_count": 100, "completed_execution_count": 100, "eligible_count": 100,
                "artifact_sha256": "d" * 64}

    def _plan(self, **extra):
        return actions.prepare_grid_diagnostics_session(
            source_session=self.SOURCE, source_snapshot=self.SNAPSHOT, **extra,
        )

    def test_diagnostics_objective_is_frozen_and_separates_sessions(self) -> None:
        default = self._plan()
        by_pf = self._plan(objective="profit_factor")
        self.assertEqual(default["input"]["objective"], "net_profit")
        self.assertEqual(default["command_config"]["objective"], "net_profit")
        self.assertEqual(by_pf["command_config"]["objective"], "profit_factor")
        self.assertNotEqual(default["execution_key"], by_pf["execution_key"])
        self.assertNotEqual(default["experiment"], by_pf["experiment"])
        with self.assertRaisesRegex(ValueError, "Selection objective"):
            self._plan(objective="sharpe")
        with patch.object(actions.readout, "SUPPORTED_OBJECTIVES", (), create=True):
            with self.assertRaisesRegex(RuntimeError, "cannot evaluate"):
                self._plan()

    def test_objective_reaches_the_core_readout_calls(self) -> None:
        plan = self._plan(objective="win_rate")
        with patch.object(actions.readout, "run_dsr", return_value={"n_trials": 100}) as run_dsr, \
                patch.object(actions.readout, "run_pbo", return_value={"pbo": 0.1}) as run_pbo, \
                patch.object(actions.pipeline_lock, "get_stage", return_value=None):
            actions.run_grid_diagnostics_session(plan)
        self.assertEqual(run_dsr.call_args.args[2]["objective"], "win_rate")
        self.assertEqual(run_pbo.call_args.args[2]["objective"], "win_rate")

    def test_dsr_shown_is_the_selected_combination_with_the_reference_kept(self) -> None:
        from dsr_pbo import views as dsr_views
        from grid import views as grid_views
        dsr = {"dsr": 0.9, "winner_label": "combo__US30.cash__h1__single__ksl9__ktp5__r0p5",
               "objective": "profit_factor", "n_trials": 100, "included_trials": 100, "total_rows": 100,
               "selected": {"label": "combo__US30.cash__h1__single__ksl3__ktp5__r0p5", "dsr": 0.3467}}
        pbo = {"pbo": 0.6, "objective": "profit_factor", "splits_tested": 12870, "blocks": 16}
        self.assertEqual(ui_common.selected_dsr(dsr), (0.3467, "top combination by profit factor"))
        value, basis = ui_common.selected_dsr({"dsr": 0.5})
        self.assertEqual(value, 0.5)
        self.assertIn("highest-Sharpe", basis)

        session = {"state": "completed", "title": "DSR + PBO - US30.cash - Combo - H1 - 100",
                   "plan": {"input": {"objective": "profit_factor"}}, "result": {"dsr": dsr, "pbo": pbo}}
        texts = " | ".join(_texts(dsr_views.detail(session)))
        self.assertIn("34.67%", texts)
        self.assertIn("top combination by profit factor", texts)
        self.assertIn("Reference DSR 90.00%", texts)
        self.assertIn("by profit factor landed in the lower half", texts)
        caution = " | ".join(_texts(grid_views._selection_caution({"state": "completed", "result": {"dsr": dsr, "pbo": pbo}})))
        self.assertIn("34.67% for the top combination by profit factor", caution)

    def test_sessions_frozen_before_objectives_say_so(self) -> None:
        from dsr_pbo import views as dsr_views
        old = {"state": "completed", "title": "DSR + PBO - x", "plan": {"input": {}},
               "result": {"dsr": {"dsr": 0.5, "winner_label": "a"}, "pbo": {"pbo": 0.4}}}
        texts = " | ".join(_texts(dsr_views.detail(old)))
        self.assertIn("Not recorded (analysed before selection objectives)", texts)
        self.assertIn("highest-Sharpe combination", texts)
        draft = {"state": "draft", "title": "DSR + PBO - x", "plan": {"input": {}}, "result": {}}
        self.assertIn("Net profit (core default)", " | ".join(_texts(dsr_views.detail(draft))))

    @patch.object(actions.facilitator, "input_fingerprint", return_value=FINGERPRINT)
    def test_walkforward_identity_carries_the_window_convention(self, _fingerprint) -> None:
        common = dict(symbol="US30.cash", timeframe="h1", strategy="combo", start="2025-01-01",
                      end="2026-09-01", balance=100000, risk_percent=0.5, max_parallel=4,
                      is_months=12, oos_months=3, step_months=3)
        plan = actions.prepare_walkforward_session(**common)
        self.assertEqual(plan["input"]["window_convention"], actions.walkforward.WINDOW_CONVENTION)
        with patch.object(actions.walkforward, "WINDOW_CONVENTION", "older"):
            older = actions.prepare_walkforward_session(**common)
        self.assertNotEqual(plan["experiment"], older["experiment"])


if __name__ == "__main__":
    unittest.main()
