from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from backtest_optimize.analysis.notebook_dashboard import (
    build_execution_config,
    build_run_config,
    cluster_review_frame,
    cluster_review_html,
    dashboard_cards_html,
    dashboard_interpretation_frame,
    dashboard_metric_frame,
    discover_signal_catalog,
    select_signal,
    status_breakdown_frame,
    warning_notes_frame,
)
from backtest_optimize.contracts import AmbiguityPolicy, MarketSpec


class NotebookDashboardTests(unittest.TestCase):
    def test_build_execution_config_exposes_component_controls(self) -> None:
        config = build_execution_config(
            engine="component",
            profile_name="combo_ctrader_v0",
            account_size=10_000,
            risk_per_cluster=0.01,
            ambiguity_policy=AmbiguityPolicy.CONSERVATIVE,
            entry_model="stop_breakout_signal_bar",
            entry_params={"x_offset": 10.0},
            sl_method="signal_bar_atr_buffer",
            sl_params={"ksl_level": 2},
            tp_method="fib_atr_ctrader_v0",
            tp_params={"ktp_level": 6, "exit_mode": "fixed_only"},
            order_model="stop_order",
            order_params={"cancel_after_bars": 3},
            exit_model="fixed_only",
        )

        self.assertEqual(config["engine"], "component")
        self.assertEqual(config["config"]["x_offset"], 10.0)
        self.assertEqual(config["config"]["ksl_level"], 2)
        self.assertEqual(config["config"]["ktp"], 2.618)
        self.assertEqual(config["config"]["cancel_after_bars"], 3)

    def test_signal_catalog_and_auto_selection(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            combo = root / "combo"
            combo.mkdir()
            (combo / "combo_US30_H4_20170430_20260602_signals.csv").touch()
            (combo / "combo_HK50_H4_20200628_20260602_signals.csv").touch()

            catalog = discover_signal_catalog(root, "combo")
            selected = select_signal(catalog, choice="auto", symbol="US30", timeframe="H4")

            self.assertEqual(len(catalog), 2)
            self.assertEqual(selected.symbol, "US30")
            self.assertEqual(selected.timeframe, "H4")
            self.assertEqual(selected.start_date, "2017-04-30")

    def test_build_run_config_supports_combo_and_r_multiple_profiles(self) -> None:
        combo = build_run_config(
            account_size=10_000,
            risk_per_cluster=0.01,
            x_buffer=10,
            tp_profile="combo_fib_atr",
            ktp_level=4,
            ambiguity_policy=AmbiguityPolicy.CONSERVATIVE,
        )
        current = build_run_config(
            account_size=10_000,
            risk_per_cluster=0.01,
            x_buffer=10,
            tp_profile="risk_multiple",
            ktp_level=4,
            r_multiples=[1, 2, 3],
            ambiguity_policy=AmbiguityPolicy.CONSERVATIVE,
        )

        self.assertEqual(combo["sl_method"], "combo_signal_bar")
        self.assertEqual(combo["tp_method"], "combo_fib_atr")
        self.assertEqual(combo["config"]["ktp"], 2.272)
        self.assertEqual(current["tp_method"], "risk_multiple")
        self.assertEqual(current["tp_params"]["r_multiples"], [1, 2, 3])

    def test_build_run_config_supports_component_combo_ctrader_v0_profile(self) -> None:
        config = build_run_config(
            account_size=10_000,
            risk_per_cluster=0.01,
            x_buffer=10,
            engine_profile="combo_ctrader_v0",
            tp_profile="fixed_only",
            ktp_level=12,
            ksl_level=2,
            cancel_after_bars=3,
            ambiguity_policy=AmbiguityPolicy.CONSERVATIVE,
        )

        self.assertEqual(config["engine"], "component")
        self.assertEqual(config["entry_model"], "stop_breakout_signal_bar")
        self.assertEqual(config["sl_method"], "signal_bar_atr_buffer")
        self.assertEqual(config["tp_method"], "fib_atr_ctrader_v0")
        self.assertEqual(config["order_model"], "stop_order")
        self.assertEqual(config["config"]["ktp"], 4.236)

    def test_warning_notes_flag_zero_cost_and_tp_profile(self) -> None:
        run_config = build_run_config(
            account_size=10_000,
            risk_per_cluster=0.01,
            x_buffer=10,
            tp_profile="combo_fib_atr",
            ktp_level=4,
            ambiguity_policy=AmbiguityPolicy.CONSERVATIVE,
        )
        notes = warning_notes_frame(
            {"total_cost_r": 0.0, "skip_rate": 0.35, "open_at_end_count": 1},
            run_config,
            MarketSpec(symbol="US30", pip_size=1.0, pip_value_per_lot=1.0),
        )

        text = " ".join(notes["note"].tolist())
        self.assertIn("Costs are zero", text)
        self.assertIn("Skip rate is high", text)
        self.assertIn("Combo Fibonacci ATR", text)

    def test_dashboard_frames_and_cards_are_readable(self) -> None:
        summary = {
            "signal_count": 10,
            "accepted_count": 8,
            "skipped_count": 2,
            "skip_rate": 0.2,
            "reversed_count": 3,
            "reversed_rate": 0.375,
            "reversed_profit_count": 2,
            "reversed_loss_count": 1,
            "reversed_flat_count": 0,
            "reversed_win_rate": 2 / 3,
            "reversed_avg_r": 0.18,
            "open_at_end_count": 1,
            "expectancy_r": 0.25,
            "std_r": 1.1,
            "sqn": None,
            "sl_hit_rate": 0.4,
            "tp1_hit_rate": 0.55,
            "mean_bar_mae_r": 0.7,
            "mean_bar_mfe_r": 1.4,
            "mean_holding_bars": 6.2,
            "ambiguity_rate": 0.0,
            "total_cost_r": 0.0,
        }
        run_config = build_run_config(
            account_size=10_000,
            risk_per_cluster=0.01,
            x_buffer=10,
            tp_profile="combo_fib_atr",
            ktp_level=4,
            ambiguity_policy=AmbiguityPolicy.CONSERVATIVE,
        )
        market_spec = MarketSpec(symbol="US30", pip_size=1.0, pip_value_per_lot=1.0)

        cards = dashboard_cards_html(summary, run_config, market_spec)
        interpretation = dashboard_interpretation_frame(summary, run_config, market_spec)
        metrics = dashboard_metric_frame(summary)

        self.assertIn("bt-run-cards", cards)
        self.assertIn("+0.250R", cards)
        self.assertIn("+0.180R", cards)
        self.assertIn("Positive expectancy", " ".join(interpretation["reading"].tolist()))
        self.assertIn("Expectancy R", metrics["metric"].tolist())
        self.assertIn("Reversed avg R", metrics["metric"].tolist())

    def test_cluster_review_frame_formats_core_fields(self) -> None:
        clusters = pd.DataFrame(
            [
                {
                    "signal_bartime": pd.Timestamp("2024-01-01 00:00"),
                    "direction": "BUY",
                    "status": "closed",
                    "skip_reason": None,
                    "entry_time": pd.Timestamp("2024-01-01 04:00"),
                    "entry_price": 100.0,
                    "sl_price": 95.0,
                    "tp_hits": [1],
                    "exit_time": pd.Timestamp("2024-01-01 08:00"),
                    "r_result": 1.5,
                    "bar_mae_r": 0.2,
                    "bar_mfe_r": 1.7,
                    "holding_bars": 2,
                    "ambiguous_bars": 0,
                },
                {
                    "signal_bartime": pd.Timestamp("2024-01-02 00:00"),
                    "direction": "SELL",
                    "status": "skipped",
                    "skip_reason": "cluster_open",
                    "entry_time": None,
                    "entry_price": None,
                    "sl_price": None,
                    "tp_hits": [],
                    "exit_time": None,
                    "r_result": 0.0,
                    "bar_mae_r": None,
                    "bar_mfe_r": None,
                    "holding_bars": 0,
                    "ambiguous_bars": 0,
                },
            ]
        )

        review = cluster_review_frame(clusters, limit=10)
        html = cluster_review_html(review)
        status = status_breakdown_frame(clusters)

        self.assertEqual(review.loc[0, "Signal Time"], "2024-01-01 00:00")
        self.assertEqual(review.loc[0, "TP Hit"], "TP1")
        self.assertEqual(review.loc[1, "Status"], "SKIPPED")
        self.assertIn("Skipped: cluster_open", review.loc[1, "Note"])
        self.assertIn("bt-cluster-review-wrap", html)
        self.assertIn("CLOSED", status["status"].tolist())


if __name__ == "__main__":
    unittest.main()
