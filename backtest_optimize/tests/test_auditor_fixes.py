from __future__ import annotations

import unittest

import pandas as pd

from backtest_optimize.analysis.metrics import summarize
from backtest_optimize.analysis.optimize import add_stability_scores, build_refined_grid, grid_size
from backtest_optimize.contracts import (
    AmbiguityPolicy,
    ClusterResult,
    ClusterStatus,
    Direction,
    LegSizing,
    MarketSpec,
    SignalRow,
    SLResult,
    TPLevel,
)
from backtest_optimize.execution.engine import run_single
from backtest_optimize.execution.position_manager import simulate_cluster
from backtest_optimize.execution.tp_calculator import calculate_tp_levels
from backtest_optimize.io.signal_loader import normalize_signal_frame


class AuditorFixTests(unittest.TestCase):
    def test_refined_grid_is_bounded_around_top_candidates(self) -> None:
        candidates = pd.DataFrame(
            [
                {
                    "entry.x_offset": 10.0,
                    "stoploss.ksl_level": 2,
                    "takeprofit.ktp_level": 6,
                    "orders.cancel_after_bars": 3,
                    "candidate_eligible": True,
                },
                {
                    "entry.x_offset": 30.0,
                    "stoploss.ksl_level": 4,
                    "takeprofit.ktp_level": 12,
                    "orders.cancel_after_bars": 4,
                    "candidate_eligible": True,
                },
            ]
        )
        values = {
            "entry.x_offset": [1.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0],
            "stoploss.ksl_level": [1, 2, 3, 4],
            "takeprofit.ktp_level": list(range(1, 13)),
            "orders.cancel_after_bars": [2, 3, 4],
        }

        refined = build_refined_grid(
            candidates,
            values,
            top_n=2,
            neighbor_steps=1,
            max_combinations=100,
        )

        self.assertEqual(refined["entry.x_offset"], [5.0, 10.0, 15.0])
        self.assertEqual(refined["takeprofit.ktp_level"], [5, 6, 7])
        self.assertLessEqual(grid_size(refined), 100)

    def test_stability_scores_require_explicit_param_cols(self) -> None:
        results = pd.DataFrame(
            {
                "atr_mult": [1.0, 1.25, 1.5],
                "expectancy_r": [0.2, 0.25, 0.22],
                "mean_bar_mae_r": [0.31, 0.44, 0.52],
            }
        )

        with self.assertRaisesRegex(ValueError, "param_cols is required"):
            add_stability_scores(results)

        scored = add_stability_scores(
            results,
            param_cols=["atr_mult"],
            neighborhood_steps=1,
        )
        self.assertGreater(scored["expectancy_r_local_count"].max(), 1)

    def test_engine_logs_execution_error_and_can_raise(self) -> None:
        raw = pd.DataFrame([{"bartime": "2026-01-01 00:00:00", "side": "BUY", "atr": 1.0}])
        signals = normalize_signal_frame(raw, symbol="TEST", timeframe="H1")
        bars = pd.DataFrame(
            [
                {
                    "bartime": "2026-01-01 00:00:00",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1,
                },
                {
                    "bartime": "2026-01-01 01:00:00",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1,
                },
            ]
        )

        with self.assertLogs("backtest_optimize.execution.engine", level="WARNING") as logs:
            result = run_single(
                signals=signals,
                bars=bars,
                symbol="TEST",
                timeframe="H1",
                market_spec=MarketSpec(symbol="TEST", pip_size=1.0, pip_value_per_lot=1.0),
                sl_method="missing_sl_method",
            )
        self.assertIn("execution_error", result.clusters[0].skip_reason)
        self.assertTrue(any("skipped due to execution error" in line for line in logs.output))

        with self.assertLogs("backtest_optimize.execution.engine", level="WARNING"):
            with self.assertRaises(ValueError):
                run_single(
                    signals=signals,
                    bars=bars,
                    symbol="TEST",
                    timeframe="H1",
                    market_spec=MarketSpec(symbol="TEST", pip_size=1.0, pip_value_per_lot=1.0),
                    sl_method="missing_sl_method",
                    raise_on_error=True,
                )

    def test_open_at_end_is_excluded_from_r_metrics(self) -> None:
        signal = SignalRow(
            bartime=pd.Timestamp("2026-01-01"),
            symbol="TEST",
            timeframe="H1",
            direction=Direction.BUY,
        )
        summary = summarize(
            [
                ClusterResult(signal=signal, status=ClusterStatus.CLOSED, r_result=1.0),
                ClusterResult(signal=signal, status=ClusterStatus.OPEN_AT_END, r_result=99.0),
                ClusterResult(signal=signal, status=ClusterStatus.SKIPPED, skip_reason="x"),
            ]
        )

        self.assertEqual(summary["accepted_count"], 2)
        self.assertEqual(summary["open_at_end_count"], 1)
        self.assertEqual(summary["r_eligible_count"], 1)
        self.assertEqual(summary["expectancy_r"], 1.0)
        self.assertTrue(summary["open_at_end_excluded_from_r"])

    def test_fixed_distance_tp_has_clear_error(self) -> None:
        signal = SignalRow(
            bartime=pd.Timestamp("2026-01-01"),
            symbol="TEST",
            timeframe="H1",
            direction=Direction.BUY,
        )
        with self.assertRaisesRegex(ValueError, "fixed_distance TP requires"):
            calculate_tp_levels("fixed_distance", signal, 100.0, 99.0, pd.DataFrame(), {})

    def test_ambiguity_policy_changes_only_ambiguous_bar_outcome(self) -> None:
        signal = SignalRow(
            bartime=pd.Timestamp("2026-01-01 00:00:00"),
            symbol="TEST",
            timeframe="H1",
            direction=Direction.BUY,
        )
        bars = pd.DataFrame(
            [
                {
                    "bartime": "2026-01-01 01:00:00",
                    "open": 100.0,
                    "high": 101.5,
                    "low": 98.5,
                    "close": 100.0,
                    "volume": 1,
                }
            ]
        )
        common = {
            "signal": signal,
            "entry_time": pd.Timestamp("2026-01-01 01:00:00"),
            "entry_price": 100.0,
            "future_bars": bars,
            "sl_result": SLResult(price=99.0, method="fixed"),
            "tp_levels": [TPLevel(level=1, price=101.0)],
            "sizing": [
                LegSizing(
                    leg_id=1,
                    risk_fraction=1.0,
                    risk_amount=100.0,
                    raw_lot=1.0,
                    lot=1.0,
                )
            ],
            "market_spec": MarketSpec(symbol="TEST", pip_size=1.0, pip_value_per_lot=1.0),
        }

        conservative = simulate_cluster(
            **common,
            ambiguity_policy=AmbiguityPolicy.CONSERVATIVE,
        )
        optimistic = simulate_cluster(
            **common,
            ambiguity_policy=AmbiguityPolicy.OPTIMISTIC,
        )

        self.assertEqual(conservative.ambiguous_bars, 1)
        self.assertEqual(optimistic.ambiguous_bars, 1)
        self.assertLess(conservative.r_result, 0)
        self.assertGreater(optimistic.r_result, 0)


if __name__ == "__main__":
    unittest.main()
